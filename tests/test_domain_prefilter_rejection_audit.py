"""DOMAIN_PREFILTER_DOMAINS_REJECTED: proposed-but-not-applied domain audit (write-only, never routing)."""

from __future__ import annotations

import contextvars
import copy
import hashlib
import inspect
import json
import threading
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

import moralstack.constitution.retriever as retriever_mod
from moralstack.constitution.openai_config import OpenAIClientConfig
from moralstack.constitution.retriever import (
    _PREFILTER_PARSE_STATUS,
    DomainPrefilter,
    _audit_label,
    _audit_value_key,
    _build_prefilter_rejection_record,
    _emit_domain_prefilter_orchestration_event,
    _proposal_items,
    _record_prefilter_parse_status,
)
from moralstack.observability import context as obs_ctx
from moralstack.observability import events as obs_events
from moralstack.observability import router
from moralstack.observability import service as service_module
from moralstack.observability.context import set_current_request_id, set_current_run_id
from moralstack.observability.read_store import SqliteReadStore
from moralstack.observability.service import get_obs
from moralstack.observability.sinks.sqlite_sink import create_run, init_db, upsert_request
from moralstack.orchestration import orchestration_event_taxonomy as taxonomy
from moralstack.orchestration.orchestration_event_taxonomy import (
    ALL_EVENT_TYPES,
    DOMAIN_PREFILTER_CACHE_HIT,
    DOMAIN_PREFILTER_CACHE_INVALIDATED,
    DOMAIN_PREFILTER_CACHE_MISS,
    DOMAIN_PREFILTER_DOMAINS_REJECTED,
    DOMAIN_PREFILTER_QUERY_TOO_SHORT,
)
from moralstack.reports.runtime_decisions import (
    build_retrieval_reuse_summary,
    build_runtime_decision_observability,
    orchestration_event_to_row,
)

REJ = DOMAIN_PREFILTER_DOMAINS_REJECTED
Q = "question about banking and finance regulation"
AVAIL = ["core", "legal", "medical", "cybersecurity", "finance"]
NAN = float("nan")
INF = float("inf")
_PERSIST_PATH = "moralstack.constitution.retriever.persist_orchestration_event"
_LLM_PERSIST_PATH = "moralstack.constitution.retriever.persist_llm_call"
_LLM_ROW_KEYS = {
    "phase",
    "module",
    "action",
    "model",
    "started_at",
    "duration_ms",
    "prompt",
    "system_prompt",
    "raw_response",
    "parsed_summary_json",
    "token_usage_json",
    "attempts",
    "cycle",
    "sequence_in_cycle",
}


def _digest(query: str = Q, avail: list[str] | None = None) -> str:
    return hashlib.md5(f"{query}_{','.join(sorted(avail or AVAIL))}".encode()).hexdigest()


@pytest.fixture(autouse=True)
def _fresh_obs_singleton():
    """Reset obs singletons and the run/request ContextVars around each test (DB tests set them)."""

    def _reset() -> None:
        try:
            get_obs().shutdown(timeout=1.0)
        except Exception:
            pass
        service_module._obs_instance = None
        router._sqlite_sink = None
        router._jsonl_sink = None
        obs_ctx._run_id.set(None)
        obs_ctx._request_id.set(None)
        _PREFILTER_PARSE_STATUS.set(None)

    _reset()
    yield
    _reset()


@pytest.fixture
def events(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    rec: list[dict[str, Any]] = []
    monkeypatch.setattr(_PERSIST_PATH, lambda **kw: rec.append(kw))
    return rec


def _of(evs: list[dict[str, Any]], type_: str) -> list[dict[str, Any]]:
    return [e for e in evs if e["event_type"] == type_]


def _types(evs: list[dict[str, Any]]) -> list[str]:
    return [e["event_type"] for e in evs]


def _pf(max_domains: int = 3) -> DomainPrefilter:
    return DomainPrefilter(domain_keywords={d: [d] for d in AVAIL if d != "core"}, max_domains=max_domains)


def _run(pf: DomainPrefilter, result: Any, query: str = Q, avail: list[str] | None = None, **kw: Any) -> list[str]:
    with patch.object(DomainPrefilter, "_call_openai", return_value=result):
        return pf.filter_domains(query, avail if avail is not None else AVAIL, **kw)


def _one(evs: list[dict[str, Any]]) -> tuple[dict[str, Any], dict[str, Any]]:
    found = _of(evs, REJ)
    assert len(found) == 1, _types(evs)
    return found[0], found[0]["payload"]


def _fake_completion(content: str) -> MagicMock:
    msg = MagicMock()
    msg.content = content
    ch = MagicMock()
    ch.message = msg
    resp = MagicMock()
    resp.choices = [ch]
    resp.usage = None
    return resp


@pytest.fixture
def real_env(monkeypatch: pytest.MonkeyPatch):
    """Factory driving the REAL ``_call_openai`` with a fake OpenAI client; returns (pf, llm_rows, client, ctor)."""

    def make(texts: Any, max_domains: int = 3, api_key: str = "sk-test"):
        rows: list[dict[str, Any]] = []
        client = MagicMock()
        if callable(texts):
            client.chat.completions.create = MagicMock(side_effect=lambda **kw: _fake_completion(texts(kw)))
        elif isinstance(texts, Exception):
            client.chat.completions.create = MagicMock(side_effect=texts)
        else:
            client.chat.completions.create = MagicMock(side_effect=[_fake_completion(t) for t in texts])
        ctor = MagicMock(return_value=client)
        monkeypatch.setattr("openai.OpenAI", ctor)
        monkeypatch.setattr(_LLM_PERSIST_PATH, lambda **kw: rows.append(kw))
        pf = DomainPrefilter(
            openai_config=OpenAIClientConfig(api_key=api_key, model="gpt-4o-mini"),
            domain_keywords={d: [d] for d in AVAIL if d != "core"},
            max_domains=max_domains,
        )
        return pf, rows, client, ctor

    return make


def _common(e: dict[str, Any], p: dict[str, Any], out: list[str]) -> None:
    assert e["stage"] == "retrieval"
    assert e["component"] == "domain_prefilter"
    assert e["status"] == "ok"
    assert e["decision"] == p["decision"]
    assert e["reason_codes"] == p["reason_codes"]
    assert p["decision"] == "rejected"
    assert p["retrieval_phase"] == "risk_routing"
    assert p["confidence_threshold"] == 0.5
    assert p["max_domains"] == 3
    assert p["routing_fallback"] is False
    assert p["cache_key_digest"] == _digest()
    assert p["parse_status"] is None
    assert p["applied_domains"] == out


def _rej(domain: str, reason: str) -> dict[str, str]:
    return {"domain": domain, "reason": reason}


# --------------------------------------------------------------------------------------
# Group 2: one event per reason
# --------------------------------------------------------------------------------------


def test_event_unknown_domain(events):
    out = _run(_pf(), {"domains": ["xyz", "medical"], "confidence": 0.9})
    assert out == ["core", "medical"]
    e, p = _one(events)
    _common(e, p, out)
    assert p["rejected"] == [_rej("xyz", "unknown_domain")]
    assert p["reason_codes"] == ["unknown_domain"]
    assert p["proposed"] == ["xyz", "medical"]
    assert p["proposed_total"] == 2
    assert p["rejected_total"] == 1
    assert p["truncated"] is False
    assert p["confidence"] == 0.9
    assert p["confidence_type"] == "float"


def test_event_low_confidence_lists_all_proposals(events):
    out = _run(_pf(), {"domains": ["medical", "legal"], "confidence": 0.3})
    e, p = _one(events)
    _common(e, p, out)
    assert p["rejected"] == [_rej("medical", "low_confidence"), _rej("legal", "low_confidence")]
    assert p["applied_domains"] == ["core"]


def test_event_low_confidence_missing_confidence(events):
    out = _run(_pf(), {"domains": ["medical"]})
    e, p = _one(events)
    _common(e, p, out)
    assert p["confidence"] is None
    assert p["confidence_type"] == "missing"
    assert p["rejected"] == [_rej("medical", "low_confidence")]


def test_event_over_cap(events):
    out = _run(_pf(), {"domains": ["legal", "medical", "cybersecurity", "finance"], "confidence": 0.9})
    e, p = _one(events)
    _common(e, p, out)
    assert p["rejected"] == [_rej("finance", "over_cap")]


def test_event_over_cap_k1(events):
    out = _run(_pf(1), {"domains": ["legal", "medical"], "confidence": 0.9})
    e, p = _one(events)
    assert out == ["core", "legal"]
    assert p["max_domains"] == 1
    assert p["rejected"] == [_rej("medical", "over_cap")]
    assert e["reason_codes"] == ["over_cap"]


def test_event_over_cap_k0(events):
    out = _run(_pf(0), {"domains": ["legal"], "confidence": 0.9})
    _, p = _one(events)
    assert out == ["core"]
    assert p["max_domains"] == 0
    assert p["rejected"] == [_rej("legal", "over_cap")]


def test_event_dup_pushes_out_next_domain(events):
    out = _run(_pf(), {"domains": ["legal", "legal", "legal", "medical"], "confidence": 0.9})
    e, p = _one(events)
    _common(e, p, out)
    assert out == ["core", "legal"]
    assert p["rejected"] == [_rej("medical", "over_cap")]
    assert p["proposed"] == ["legal", "legal", "legal", "medical"]
    assert p["proposed_total"] == 4


def test_event_core_proposed_never_rejected_but_pushes_out(events):
    out = _run(_pf(), {"domains": ["core", "legal", "medical", "finance"], "confidence": 0.9})
    _, p = _one(events)
    assert out == ["core", "legal", "medical"]
    assert p["rejected"] == [_rej("finance", "over_cap")]
    assert all(r["domain"] != "core" for r in p["rejected"])


def test_event_mixed_reasons_sorted_codes(events):
    _run(_pf(), {"domains": ["xyz", "legal", "medical", "cybersecurity", "finance"], "confidence": 0.9})
    e, p = _one(events)
    assert p["rejected"] == [_rej("xyz", "unknown_domain"), _rej("finance", "over_cap")]
    assert p["reason_codes"] == ["over_cap", "unknown_domain"]
    assert e["reason_codes"] == ["over_cap", "unknown_domain"]


def test_event_one_entry_per_distinct_value(events):
    _run(_pf(), {"domains": ["xyz", "xyz", "Xyz"], "confidence": 0.9})
    _, p = _one(events)
    assert [r["domain"] for r in p["rejected"]] == ["xyz", "Xyz"]
    assert p["proposed_total"] == 3
    assert p["rejected_total"] == 2


def test_event_type_aware_dedup(events):
    _run(_pf(), {"domains": [1, True, 1.0], "confidence": 0.9})
    _, p = _one(events)
    assert [r["domain"] for r in p["rejected"]] == ["1", "true", "1.0"]
    assert all(r["reason"] == "unknown_domain" for r in p["rejected"])


def test_event_non_string_labels(events):
    _run(_pf(), {"domains": [None, 2, ["medical"], {"a": 1}], "confidence": 0.9})
    _, p = _one(events)
    assert [r["domain"] for r in p["rejected"]] == ["null", "2", '["medical"]', '{"a": 1}']
    assert all(r["reason"] == "unknown_domain" for r in p["rejected"])


def test_event_case_and_whitespace_variants_are_unknown(events):
    _run(_pf(), {"domains": ["Medical", " medical", "medical "], "confidence": 0.9})
    _, p = _one(events)
    assert [r["domain"] for r in p["rejected"]] == ["Medical", " medical", "medical "]
    assert all(r["reason"] == "unknown_domain" for r in p["rejected"])


def test_event_empty_string_element_vs_empty_string_domains(events):
    _run(_pf(), {"domains": [""], "confidence": 0.9})
    _, p = _one(events)
    assert p["rejected"] == [_rej("", "unknown_domain")]
    events.clear()
    _run(_pf(), {"domains": "", "confidence": 0.9})
    assert _of(events, REJ) == []


def test_event_string_domains_is_single_item_not_per_char(events):
    out = _run(_pf(), {"domains": "medical", "confidence": 0.9})
    _, p = _one(events)
    assert out == ["core"]
    assert p["proposed"] == ["medical"]
    assert p["rejected"] == [_rej("medical", "parse_failed")]
    assert p["routing_fallback"] is False
    assert p["applied_domains"] == ["core"]


def test_event_dict_domains_uses_keys(events):
    _run(_pf(), {"domains": {"xyz": 1, "medical": 2}, "confidence": 0.9})
    _, p = _one(events)
    assert p["proposed"] == ["xyz", "medical"]
    assert p["rejected"] == [_rej("xyz", "unknown_domain")]


def test_event_int_domains_routing_fallback(events):
    pf = _pf()
    out = _run(pf, {"domains": 3, "confidence": 0.9})
    _, p = _one(events)
    assert out == ["core"]
    assert p["routing_fallback"] is True
    assert p["rejected"] == [_rej("3", "parse_failed")]
    assert pf._cache == {}


@pytest.mark.parametrize(
    ("conf", "ctype"),
    [("0.9", "str"), (None, "NoneType"), ([0.9], "list")],
    ids=["str", "none", "list"],
)
def test_event_wrong_type_confidence_routing_fallback(events, conf, ctype):
    pf = _pf()
    out = _run(pf, {"domains": ["medical"], "confidence": conf})
    _, p = _one(events)
    assert out == ["core"]
    assert p["routing_fallback"] is True
    assert p["rejected"] == [_rej("medical", "parse_failed")]
    assert p["confidence"] is None
    assert p["confidence_type"] == ctype
    assert pf._cache == {}


def test_event_wrong_type_conf_outranks_unknown(events):
    _run(_pf(), {"domains": ["xyz"], "confidence": "0.9"})
    _, p = _one(events)
    assert p["rejected"] == [_rej("xyz", "parse_failed")]


@pytest.mark.parametrize(
    "result",
    [
        {"domains": None, "confidence": 0.9},
        {"domains": [], "confidence": "0.9"},
        {"confidence": [0.9]},
    ],
    ids=["domains_none", "empty_with_str_conf", "no_domains_list_conf"],
)
def test_event_routing_fallback_without_proposals(events, result):
    pf = _pf()
    out = _run(pf, result)
    e, p = _one(events)
    assert out == ["core"]
    assert p["decision"] == "parse_failed"
    assert e["decision"] == "parse_failed"
    assert p["reason_codes"] == ["parse_failed"]
    assert e["reason_codes"] == ["parse_failed"]
    assert p["proposed"] == []
    assert p["rejected"] == []
    assert p["routing_fallback"] is True
    assert p["applied_domains"] == ["core"]
    assert pf._cache == {}


def test_event_routing_fallback_with_only_core_proposed(events):
    """Fallback must stay visible even when every proposed value counts as applied (only ``core``)."""
    pf = _pf()
    out = _run(pf, {"domains": ["core"], "confidence": "0.9"})
    _, p = _one(events)
    assert out == ["core"]
    assert p["decision"] == "parse_failed"
    assert p["routing_fallback"] is True
    assert p["proposed"] == ["core"]
    assert p["rejected"] == []
    assert pf._cache == {}


@pytest.mark.parametrize(
    "result",
    [{"domains": [], "confidence": 0.9}, {"confidence": 0.9}],
    ids=["empty_list", "missing"],
)
def test_no_event_when_nothing_proposed_and_no_fallback(events, result):
    pf = _pf()
    out = _run(pf, result)
    assert out == ["core"]
    assert _of(events, REJ) == []
    assert len(pf._cache) == 1


def test_event_nan_and_inf_confidence_payload_is_strict_json(events):
    _run(_pf(), {"domains": ["legal"], "confidence": NAN})
    _, p = _one(events)
    assert p["confidence"] is None
    assert p["confidence_type"] == "float"
    assert p["rejected"] == [_rej("legal", "low_confidence")]
    json.dumps(p, allow_nan=False)
    events.clear()
    out = _run(_pf(), {"domains": ["legal"], "confidence": INF})
    assert out == ["core", "legal"]
    assert _of(events, REJ) == []


def test_event_bool_confidence(events):
    _run(_pf(), {"domains": ["xyz"], "confidence": True})
    _, p = _one(events)
    assert p["confidence"] is None
    assert p["confidence_type"] == "bool"
    assert p["rejected"] == [_rej("xyz", "unknown_domain")]


def test_event_conf_exactly_threshold_unknown_domain(events):
    _run(_pf(), {"domains": ["xyz"], "confidence": 0.5})
    _, p = _one(events)
    assert p["rejected"] == [_rej("xyz", "unknown_domain")]


def test_event_first_gate_wins_low_conf_over_unknown(events):
    _run(_pf(), {"domains": ["xyz"], "confidence": 0.3})
    _, p = _one(events)
    assert p["rejected"] == [_rej("xyz", "low_confidence")]


def test_event_distinct_available_lists_distinct_digests(events):
    pf = _pf()
    _run(pf, {"domains": ["xyz"], "confidence": 0.9}, avail=AVAIL)
    _run(pf, {"domains": ["xyz"], "confidence": 0.9}, avail=["core", "legal", "medical"])
    found = _of(events, REJ)
    assert len(found) == 2
    assert found[0]["payload"]["cache_key_digest"] != found[1]["payload"]["cache_key_digest"]


# --------------------------------------------------------------------------------------
# Group 3: no event
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("result", "expected"),
    [
        ({"domains": ["medical"], "confidence": 0.9}, ["core", "medical"]),
        ({"domains": [], "confidence": 0.9}, ["core"]),
        ({}, ["core"]),
        ({"confidence": 0.9}, ["core"]),
        ({"domains": "", "confidence": 0.9}, ["core"]),
        ({"domains": ["core"], "confidence": 0.9}, ["core"]),
        ({"domains": "core", "confidence": 0.9}, ["core"]),
        ({"domains": {"core": 1}, "confidence": 0.9}, ["core"]),
        ({"domains": ["legal"], "confidence": INF}, ["core", "legal"]),
    ],
    ids=["clean", "empty", "empty_result", "no_domains", "empty_str", "core_list", "core_str", "core_dict", "inf"],
)
def test_no_rejection_event(events, result, expected):
    pf = _pf()
    out = _run(pf, result)
    assert out == expected
    assert _of(events, REJ) == []
    assert len(_of(events, DOMAIN_PREFILTER_CACHE_MISS)) == 1


def test_no_event_on_short_query(events):
    pf = _pf()
    with patch.object(DomainPrefilter, "_call_openai", return_value={"domains": ["xyz"], "confidence": 0.9}) as m:
        pf.filter_domains("short", AVAIL)
    assert _types(events) == [DOMAIN_PREFILTER_QUERY_TOO_SHORT]
    assert m.call_count == 0


def test_no_event_when_no_candidate_domains(events):
    pf = _pf()
    with patch.object(DomainPrefilter, "_call_openai", return_value={"domains": ["xyz"], "confidence": 0.9}) as m:
        out = pf.filter_domains(Q, ["core"])
    assert out == ["core"]
    assert m.call_count == 0
    assert _of(events, REJ) == []


def test_no_event_on_cache_hit_after_rejected_miss(events):
    pf = _pf()
    with patch.object(DomainPrefilter, "_call_openai", return_value={"domains": ["xyz", "medical"], "confidence": 0.9}) as m:
        pf.filter_domains(Q, AVAIL)
        pf.filter_domains(Q, AVAIL)
    assert m.call_count == 1
    assert _types(events) == [DOMAIN_PREFILTER_CACHE_MISS, REJ, DOMAIN_PREFILTER_CACHE_HIT]
    digests = {e["payload"]["cache_key_digest"] for e in events}
    assert digests == {_digest()}
    assert events[2]["payload"]["matched_domains"] == ["core", "medical"]


def test_rejection_reemitted_after_cache_invalidation(events):
    pf = _pf()
    with patch.object(DomainPrefilter, "_call_openai", return_value={"domains": ["xyz"], "confidence": 0.9}):
        pf.filter_domains(Q, AVAIL)
        assert pf.set_domain_keywords({"legal": ["changed"]}) is True
        pf.filter_domains(Q, AVAIL)
    assert _types(events).count(REJ) == 2
    assert DOMAIN_PREFILTER_CACHE_INVALIDATED in _types(events)


def test_phase_does_not_split_cache_key_so_second_phase_emits_only_hit(events):
    pf = _pf()
    with patch.object(DomainPrefilter, "_call_openai", return_value={"domains": ["xyz"], "confidence": 0.9}) as m:
        pf.filter_domains(Q, AVAIL, retrieval_phase="risk_routing")
        pf.filter_domains(Q, AVAIL, retrieval_phase="deliberation_retrieval")
    assert m.call_count == 1
    assert _types(events) == [DOMAIN_PREFILTER_CACHE_MISS, REJ, DOMAIN_PREFILTER_CACHE_HIT]


def test_no_event_no_api_key(events, real_env):
    pf, rows, client, ctor = real_env(["unused"], api_key="")
    out = pf.filter_domains(Q, AVAIL)
    assert out == ["core"]
    assert _of(events, REJ) == []
    assert ctor.call_count == 0
    assert list(pf._cache.values()) == [["core"]]
    assert rows == []


def test_no_event_on_api_exception(events, real_env):
    pf, rows, _, _ = real_env(RuntimeError("boom"))
    out = pf.filter_domains(Q, AVAIL)
    assert out == ["core"]
    assert _of(events, REJ) == []
    assert rows == []
    assert list(pf._cache.values()) == [["core"]]


def test_no_event_when_patched_call_openai_raises(events):
    pf = _pf()
    with patch.object(DomainPrefilter, "_call_openai", side_effect=RuntimeError("boom")):
        out = pf.filter_domains(Q, AVAIL)
    assert out == ["core"]
    assert _types(events) == [DOMAIN_PREFILTER_CACHE_MISS]
    assert pf._cache == {}


def test_event_order_miss_before_rejected(events):
    _run(_pf(), {"domains": ["xyz"], "confidence": 0.9})
    assert _types(events) == [DOMAIN_PREFILTER_CACHE_MISS, REJ]


def test_event_order_miss_then_rejected_on_routing_fallback(events):
    pf = _pf()
    out = _run(pf, {"domains": ["legal"], "confidence": "0.9"})
    assert out == ["core"]
    assert _types(events) == [DOMAIN_PREFILTER_CACHE_MISS, REJ]
    assert events[1]["payload"]["routing_fallback"] is True


# --------------------------------------------------------------------------------------
# Group 4: parse_failed through the REAL _call_openai
# --------------------------------------------------------------------------------------


def _assert_failed(events, pf, rows, out):
    e, p = _one(events)
    assert out == ["core"]
    assert list(pf._cache.values()) == [["core"]]
    assert p["reason_codes"] == ["parse_failed"]
    assert p["proposed"] == []
    assert p["rejected"] == []
    assert p["rejected_total"] == 0
    assert p["parse_status"] == "failed"
    assert p["confidence"] is None
    assert p["confidence_type"] == "missing"
    assert p["routing_fallback"] is False
    assert p["applied_domains"] == ["core"]
    assert e["decision"] == "parse_failed"
    assert json.loads(rows[0]["parsed_summary_json"])["parse_contract"]["parse_status"] == "failed"


@pytest.mark.parametrize(
    "text",
    [
        "I cannot help with that request.",
        '{"domains": ["medical"], "confidence": 0.9, "w": {"x": 1} oops }',
        '{"domains": ["medical"], "confid',
        "",
        '["medical"]',
    ],
    ids=["prose", "regex_match_invalid_json", "truncated", "empty_text", "top_level_list"],
)
def test_parse_failed_variants(events, real_env, text):
    pf, rows, _, _ = real_env([text])
    out = pf.filter_domains(Q, AVAIL)
    _assert_failed(events, pf, rows, out)
    assert rows[0]["raw_response"] == text


def test_fallback_ok_clean_emits_nothing(events, real_env):
    text = 'Here is the result:\n{"domains": ["legal"], "confidence": 0.9}\nThanks.'
    pf, rows, _, _ = real_env([text])
    out = pf.filter_domains(Q, AVAIL)
    assert out == ["core", "legal"]
    assert _of(events, REJ) == []
    assert json.loads(rows[0]["parsed_summary_json"])["parse_contract"]["parse_status"] == "fallback_ok"


def test_fallback_ok_with_unknown_domain_is_rejected_not_parse_failed(events, real_env):
    text = 'Here is the result:\n{"domains": ["xyz"], "confidence": 0.9}\nThanks.'
    pf, _, _, _ = real_env([text])
    pf.filter_domains(Q, AVAIL)
    _, p = _one(events)
    assert p["decision"] == "rejected"
    assert p["parse_status"] == "fallback_ok"
    assert p["rejected"] == [_rej("xyz", "unknown_domain")]


def test_valid_json_empty_object_no_event(events, real_env):
    pf, _, _, _ = real_env(["{}"])
    assert pf.filter_domains(Q, AVAIL) == ["core"]
    assert _of(events, REJ) == []


def test_ok_status_unknown_domain_reports_parse_status_ok(events, real_env):
    pf, _, _, _ = real_env([json.dumps({"domains": ["sports"], "confidence": 0.9})])
    pf.filter_domains(Q, AVAIL)
    _, p = _one(events)
    assert p["parse_status"] == "ok"
    assert p["rejected"] == [_rej("sports", "unknown_domain")]


def test_deliberation_retrieval_phase_propagates(events, real_env):
    pf, rows, _, _ = real_env([json.dumps({"domains": ["sports"], "confidence": 0.9})])
    pf.filter_domains(Q, AVAIL, retrieval_phase="deliberation_retrieval")
    _, p = _one(events)
    assert p["retrieval_phase"] == "deliberation_retrieval"
    assert rows[0]["cycle"] == 0
    assert rows[0]["sequence_in_cycle"] == -1


def test_second_unparsable_after_clean_is_reported_once_each(events, real_env):
    texts = [json.dumps({"domains": ["sports"], "confidence": 0.9}), "no json here"]
    pf, _, _, _ = real_env(texts)
    pf.filter_domains(Q, AVAIL)
    pf.filter_domains(Q + " second", AVAIL)
    found = _of(events, REJ)
    assert [f["payload"]["decision"] for f in found] == ["rejected", "parse_failed"]
    assert [f["payload"]["parse_status"] for f in found] == ["ok", "failed"]


def test_status_not_stale_when_persist_llm_call_raises(events, real_env, monkeypatch):
    pf, _, _, _ = real_env(["not json at all"])

    def _boom(**kw):
        raise RuntimeError("db down")

    monkeypatch.setattr(_LLM_PERSIST_PATH, _boom)
    out = pf.filter_domains(Q, AVAIL)
    assert out == ["core"]
    _, p = _one(events)
    assert p["decision"] == "parse_failed"
    assert p["parse_status"] == "failed"


# --------------------------------------------------------------------------------------
# Group 5: ContextVar side channel
# --------------------------------------------------------------------------------------


def test_record_prefilter_parse_status_unit():
    def _body() -> None:
        _record_prefilter_parse_status({"parse_status": "failed"})
        assert _PREFILTER_PARSE_STATUS.get() == "failed"
        _record_prefilter_parse_status({"parse_status": 5})
        assert _PREFILTER_PARSE_STATUS.get() is None
        _record_prefilter_parse_status({"parse_status": "ok"})
        _record_prefilter_parse_status({})
        assert _PREFILTER_PARSE_STATUS.get() is None
        _record_prefilter_parse_status({"parse_status": "ok"})
        _record_prefilter_parse_status(None)
        assert _PREFILTER_PARSE_STATUS.get() is None
        _record_prefilter_parse_status({"parse_status": "ok"})
        _record_prefilter_parse_status(42)  # type: ignore[arg-type]
        assert _PREFILTER_PARSE_STATUS.get() is None

    contextvars.copy_context().run(_body)
    assert _PREFILTER_PARSE_STATUS.get() is None


def test_stale_failed_status_is_reset_before_next_call(events, real_env):
    pf, _, _, _ = real_env(["prose only"])
    pf.filter_domains(Q, AVAIL)
    events.clear()
    _run(pf, {"domains": ["xyz"], "confidence": 0.9}, query=Q + " other")
    _, p = _one(events)
    assert p["decision"] == "rejected"
    assert p["parse_status"] is None
    assert _PREFILTER_PARSE_STATUS.get() is None


def test_stale_status_not_leaked_after_api_exception(events, real_env):
    pf, _, client, _ = real_env(["prose only"])
    pf.filter_domains(Q, AVAIL)
    events.clear()
    client.chat.completions.create = MagicMock(side_effect=RuntimeError("boom"))
    out = pf.filter_domains(Q + " other", AVAIL)
    assert out == ["core"]
    assert _of(events, REJ) == []
    assert _PREFILTER_PARSE_STATUS.get() is None


def test_status_reset_for_no_key(events, real_env):
    pf, _, _, _ = real_env(["prose only"])
    pf.filter_domains(Q, AVAIL)
    events.clear()
    no_key = DomainPrefilter(
        openai_config=OpenAIClientConfig(api_key="", model="gpt-4o-mini"),
        domain_keywords={d: [d] for d in AVAIL if d != "core"},
    )
    assert no_key.filter_domains(Q + " other", AVAIL) == ["core"]
    assert _of(events, REJ) == []
    assert _PREFILTER_PARSE_STATUS.get() is None


def test_sequential_same_thread_reset(events, real_env):
    pf, _, _, _ = real_env(["prose only"])
    pf.filter_domains(Q, AVAIL)
    assert _PREFILTER_PARSE_STATUS.get() == "failed"
    events.clear()
    with patch.object(DomainPrefilter, "_call_openai", return_value={}):
        out = pf.filter_domains(Q + " second miss", AVAIL)
    assert out == ["core"]
    assert _of(events, REJ) == []
    assert _PREFILTER_PARSE_STATUS.get() is None


def test_cache_hit_does_not_read_or_set_status(events, real_env):
    pf, _, _, _ = real_env(["prose only"])
    pf.filter_domains(Q, AVAIL)
    events.clear()
    token = _PREFILTER_PARSE_STATUS.set("sentinel")
    try:
        pf.filter_domains(Q, AVAIL)
        assert _PREFILTER_PARSE_STATUS.get() == "sentinel"
    finally:
        _PREFILTER_PARSE_STATUS.reset(token)
    assert _types(events) == [DOMAIN_PREFILTER_CACHE_HIT]


def test_parse_status_isolated_across_two_threads(events, real_env):
    qa, qb = "thread A question about banking", "thread B question about contracts"
    barrier = threading.Barrier(2, timeout=5.0)
    errors: list[BaseException] = []

    def _answer(kw: dict[str, Any]) -> str:
        user = kw["messages"][1]["content"]
        if qa in user:
            return "prose without any json"
        return json.dumps({"domains": ["xyz"], "confidence": 0.9})

    pf, rows, _, _ = real_env(_answer)

    def _sync(**kw: Any) -> None:
        rows.append(kw)
        try:
            barrier.wait()
        except BaseException as exc:  # noqa: BLE001 - collected, persist helper would swallow it
            errors.append(exc)

    retriever_mod_persist = _LLM_PERSIST_PATH
    with patch(retriever_mod_persist, side_effect=_sync):
        threads = [threading.Thread(target=pf.filter_domains, args=(q, AVAIL)) for q in (qa, qb)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10.0)
        assert not any(t.is_alive() for t in threads)

    assert errors == []
    found = {e["payload"]["cache_key_digest"]: e["payload"] for e in _of(events, REJ)}
    assert len(found) == 2
    a, b = found[_digest(qa)], found[_digest(qb)]
    assert (a["decision"], a["parse_status"]) == ("parse_failed", "failed")
    assert (b["decision"], b["parse_status"]) == ("rejected", "ok")
    assert b["rejected"] == [_rej("xyz", "unknown_domain")]
    assert _PREFILTER_PARSE_STATUS.get() is None


def test_status_respects_copy_context_run():
    token = _PREFILTER_PARSE_STATUS.set("failed")
    try:
        contextvars.copy_context().run(lambda: _record_prefilter_parse_status({"parse_status": "ok"}))
        assert _PREFILTER_PARSE_STATUS.get() == "failed"
    finally:
        _PREFILTER_PARSE_STATUS.reset(token)


def test_prefilter_has_no_new_instance_attributes(events, real_env):
    pf, _, _, _ = real_env(["prose only", json.dumps({"domains": ["sports"], "confidence": 0.9})])
    pf.filter_domains(Q, AVAIL)
    before = set(vars(pf))
    pf.filter_domains(Q + " second", AVAIL)
    after = set(vars(pf))
    assert before == after
    assert not any(a.startswith("_last") for a in after)
    # Every attribute already exists on a freshly built prefilter.
    assert after - set(vars(_pf())) == set()


# --------------------------------------------------------------------------------------
# Group 6: fail-safety
# --------------------------------------------------------------------------------------


def _baseline_ok(pf: DomainPrefilter, out: list[str]) -> None:
    assert out == ["core", "medical"]
    assert list(pf._cache.values()) == [["core", "medical"]]


_BASE_INPUT = {"domains": ["xyz", "medical"], "confidence": 0.9}


def test_builder_raises_does_not_change_routing(events, monkeypatch):
    monkeypatch.setattr(retriever_mod, "_build_prefilter_rejection_record", MagicMock(side_effect=RuntimeError("x")))
    pf = _pf()
    out = _run(pf, _BASE_INPUT)
    _baseline_ok(pf, out)
    assert _of(events, REJ) == []
    assert len(_of(events, DOMAIN_PREFILTER_CACHE_MISS)) == 1


def test_emitter_signature_reason_codes_keyword_only():
    p = inspect.signature(_emit_domain_prefilter_orchestration_event).parameters["reason_codes"]
    assert p.kind is inspect.Parameter.KEYWORD_ONLY
    assert p.default is None


def test_builder_raises_on_routing_fallback_path(events, monkeypatch):
    monkeypatch.setattr(retriever_mod, "_build_prefilter_rejection_record", MagicMock(side_effect=RuntimeError("x")))
    pf = _pf()
    out = _run(pf, {"domains": ["legal"], "confidence": "0.9"})
    assert out == ["core"]
    assert pf._cache == {}


def test_emitter_raises_for_rejected_only(events, monkeypatch):
    real = retriever_mod._emit_domain_prefilter_orchestration_event

    def _selective(event_type, payload, **kw):
        if event_type == REJ:
            raise RuntimeError("emit failed")
        return real(event_type, payload, **kw)

    monkeypatch.setattr(retriever_mod, "_emit_domain_prefilter_orchestration_event", _selective)
    pf = _pf()
    out = _run(pf, _BASE_INPUT)
    _baseline_ok(pf, out)
    assert _types(events) == [DOMAIN_PREFILTER_CACHE_MISS]


def test_persist_orchestration_event_raises_for_rejected_only(monkeypatch):
    rec: list[dict[str, Any]] = []

    def _selective(**kw):
        if kw["event_type"] == REJ:
            raise RuntimeError("persist failed")
        rec.append(kw)

    monkeypatch.setattr(_PERSIST_PATH, _selective)
    pf = _pf()
    out = _run(pf, _BASE_INPUT)
    _baseline_ok(pf, out)
    assert _types(rec) == [DOMAIN_PREFILTER_CACHE_MISS]


def test_taxonomy_import_failure_in_audit_is_swallowed(events, monkeypatch):
    monkeypatch.delattr(taxonomy, "DOMAIN_PREFILTER_DOMAINS_REJECTED")
    pf = _pf()
    out = _run(pf, _BASE_INPUT)
    _baseline_ok(pf, out)
    assert _types(events) == [DOMAIN_PREFILTER_CACHE_MISS]


def test_contextvar_get_raises_in_audit(events, monkeypatch):
    def _raise() -> None:
        raise RuntimeError("get failed")

    monkeypatch.setattr(retriever_mod, "_PREFILTER_PARSE_STATUS", SimpleNamespace(get=_raise, set=lambda v: None))
    pf = _pf()
    out = _run(pf, _BASE_INPUT)
    _baseline_ok(pf, out)
    assert _of(events, REJ) == []


def test_contextvar_set_raises_inside_call_openai_still_returns_data_and_writes_row(events, real_env, monkeypatch):
    text = json.dumps({"domains": ["legal"], "confidence": 0.9})
    pf, rows, _, _ = real_env([text, text])

    def _raise(v: Any) -> None:
        raise RuntimeError("set failed")

    monkeypatch.setattr(retriever_mod, "_PREFILTER_PARSE_STATUS", SimpleNamespace(get=lambda: None, set=_raise))
    data = pf._call_openai("USER QUERY:\nq", system_prompt="sys")
    assert data == {"domains": ["legal"], "confidence": 0.9}
    assert len(rows) == 1
    assert set(rows[0]) == _LLM_ROW_KEYS
    assert pf.filter_domains(Q, AVAIL) == ["core", "legal"]


def test_audit_does_not_mutate_cache_entry_or_result(events):
    pf = _pf()
    result = {"domains": ["xyz", "medical"], "confidence": 0.9}
    snapshot = copy.deepcopy(result)
    out = _run(pf, result)
    assert result == snapshot
    entry = list(pf._cache.values())[0]
    assert entry == ["core", "medical"]
    _, p = _one(events)
    assert p["applied_domains"] is not entry
    p["applied_domains"].append("INJECTED")
    assert list(pf._cache.values())[0] == ["core", "medical"]
    assert out == ["core", "medical"]


def test_audit_input_not_mutated(events):
    avail = list(AVAIL)
    result = {"domains": ["xyz", "legal"], "confidence": 0.9}
    snapshot = copy.deepcopy(result)
    _run(_pf(), result, avail=avail)
    assert avail == AVAIL
    assert result == snapshot


def test_no_run_context_is_safe(monkeypatch):
    boom = MagicMock(side_effect=RuntimeError("get_obs must not be invoked"))
    monkeypatch.setattr("moralstack.observability.service.get_obs", boom)
    pf = _pf()
    out = _run(pf, _BASE_INPUT)
    _baseline_ok(pf, out)
    assert boom.call_count == 0


# --------------------------------------------------------------------------------------
# Group 7: llm_calls unchanged
# --------------------------------------------------------------------------------------


def test_llm_calls_row_identical_for_failed_parse(events, real_env):
    text = "I cannot help with that."
    pf, rows, _, _ = real_env([text])
    pf.filter_domains(Q, AVAIL)
    assert len(rows) == 1
    assert set(rows[0]) == _LLM_ROW_KEYS
    assert rows[0]["raw_response"] == text
    summary = json.loads(rows[0]["parsed_summary_json"])
    assert summary["parse_contract"]["parse_status"] == "failed"
    assert not [k for k in rows[0] if "reject" in k or "audit" in k]
    assert "reject" not in rows[0]["parsed_summary_json"]


def test_persist_kwargs_do_not_contain_new_keys(events, real_env):
    pf, rows, _, _ = real_env([json.dumps({"domains": ["sports"], "confidence": 0.9})])
    pf.filter_domains(Q, AVAIL)
    assert set(rows[0]) == _LLM_ROW_KEYS
    assert set(json.loads(rows[0]["parsed_summary_json"])) == {"module", "retrieval_phase", "parse_contract"}


# --------------------------------------------------------------------------------------
# Group 8: taxonomy
# --------------------------------------------------------------------------------------


def test_rejected_constant_value_and_membership():
    assert DOMAIN_PREFILTER_DOMAINS_REJECTED == "DOMAIN_PREFILTER_DOMAINS_REJECTED"
    assert DOMAIN_PREFILTER_DOMAINS_REJECTED in ALL_EVENT_TYPES
    for t in (
        DOMAIN_PREFILTER_CACHE_HIT,
        DOMAIN_PREFILTER_CACHE_MISS,
        DOMAIN_PREFILTER_CACHE_INVALIDATED,
        DOMAIN_PREFILTER_QUERY_TOO_SHORT,
    ):
        assert t in ALL_EVENT_TYPES


def test_all_event_types_values_equal_names():
    names = {n: getattr(taxonomy, n) for n in dir(taxonomy) if n.isupper() and n != "ALL_EVENT_TYPES"}
    for value in ALL_EVENT_TYPES:
        assert names[value] == value


def test_observability_envelope_event_set_unchanged():
    assert len(obs_events.ALL_EVENT_TYPES) == 17
    assert DOMAIN_PREFILTER_DOMAINS_REJECTED not in obs_events.ALL_EVENT_TYPES


# --------------------------------------------------------------------------------------
# Group 9: reports
# --------------------------------------------------------------------------------------


def _db_row(decision: str, codes: list[str]) -> dict[str, Any]:
    return {
        "id": 1,
        "cycle": 0,
        "stage": "retrieval",
        "component": "domain_prefilter",
        "event_type": REJ,
        "decision": decision,
        "status": "ok",
        "reason_codes_json": json.dumps(codes),
    }


def test_rejected_event_row_is_generic_with_reason():
    row = orchestration_event_to_row(_db_row("rejected", ["over_cap", "unknown_domain"]), 0)
    assert row["reason"] == "over_cap, unknown_domain"
    assert row["event"] == REJ
    assert row["decision"] == "rejected"
    assert row["stage"] == "retrieval"
    assert row["badges"] == [REJ]


def test_rejected_event_not_counted_as_reuse_or_invalidation():
    evs = [_db_row("rejected", ["unknown_domain"])]
    summ = build_retrieval_reuse_summary([], evs)
    assert summ["orchestration_reuse_events"] == 0
    assert summ["prefilter_cache_invalidations"] == 0
    hit = dict(_db_row("hit", []), event_type=DOMAIN_PREFILTER_CACHE_HIT)
    assert build_retrieval_reuse_summary([], evs + [hit])["orchestration_reuse_events"] == 1


def test_rejected_event_appears_in_runtime_decisions_table_via_build_runtime_decision_observability():
    vm = build_runtime_decision_observability(traces=[], orchestration_events=[_db_row("rejected", ["over_cap"])])
    assert vm["has_orchestration_events"] is True
    assert len(vm["runtime_decisions"]) == 1
    assert vm["runtime_decisions"][0]["reason"] == "over_cap"


def test_parse_failed_event_row_reason():
    row = orchestration_event_to_row(_db_row("parse_failed", ["parse_failed"]), 3)
    assert row["reason"] == "parse_failed"
    assert row["decision"] == "parse_failed"


# --------------------------------------------------------------------------------------
# Group 10: DB persistence
# --------------------------------------------------------------------------------------


def _db_setup(tmp_path, monkeypatch, rid: str, qid: str) -> None:
    dbp = str(tmp_path / "audit.db")
    monkeypatch.setenv("MORALSTACK_DB_PATH", dbp)
    monkeypatch.setenv("MORALSTACK_PERSIST_MODE", "db_only")
    init_db(dbp)
    assert create_run(rid, run_type="test", meta={})
    assert upsert_request(rid, qid, prompt="p", domain="")
    set_current_run_id(rid)
    set_current_request_id(qid)


def _db_events(rid: str, qid: str) -> list[dict[str, Any]]:
    get_obs().flush(timeout=5.0)
    return SqliteReadStore().get_orchestration_events_for_request(rid, qid)


def test_rejected_event_persisted_with_reason_codes_json(tmp_path, monkeypatch):
    _db_setup(tmp_path, monkeypatch, "r-aud", "q-aud")
    _run(_pf(), {"domains": ["xyz", "legal", "medical", "cybersecurity", "finance"], "confidence": 0.9})
    rows = _db_events("r-aud", "q-aud")
    rej = [r for r in rows if r["event_type"] == REJ]
    miss = [r for r in rows if r["event_type"] == DOMAIN_PREFILTER_CACHE_MISS]
    assert len(rej) == 1
    r = rej[0]
    assert json.loads(r["reason_codes_json"]) == ["over_cap", "unknown_domain"]
    assert (r["stage"], r["component"], r["decision"]) == ("retrieval", "domain_prefilter", "rejected")
    payload = json.loads(r["payload_json"])
    assert payload["rejected"] == [_rej("xyz", "unknown_domain"), _rej("finance", "over_cap")]
    assert payload["applied_domains"] == ["core", "legal", "medical", "cybersecurity"]
    assert json.loads(miss[0]["payload_json"])["cache_key_digest"] == payload["cache_key_digest"]
    assert orchestration_event_to_row(r, 0)["reason"] == "over_cap, unknown_domain"


def test_parse_failed_event_persisted(tmp_path, monkeypatch, real_env):
    _db_setup(tmp_path, monkeypatch, "r-aud2", "q-aud2")
    pf, _, _, _ = real_env(["I cannot help with that request."])
    pf.filter_domains(Q, AVAIL)
    rej = [r for r in _db_events("r-aud2", "q-aud2") if r["event_type"] == REJ]
    assert len(rej) == 1
    assert rej[0]["decision"] == "parse_failed"
    assert json.loads(rej[0]["reason_codes_json"]) == ["parse_failed"]
    assert json.loads(rej[0]["payload_json"])["parse_status"] == "failed"


def test_persisted_payload_is_bounded_and_strict_json(tmp_path, monkeypatch):
    _db_setup(tmp_path, monkeypatch, "r-aud3", "q-aud3")
    domains = [f"u{i}" for i in range(99)] + ["x" * 300]
    # NaN confidence fails the ``>= threshold`` gate, so every proposal is ``low_confidence``.
    _run(_pf(), {"domains": domains, "confidence": float("nan")})
    rej = [r for r in _db_events("r-aud3", "q-aud3") if r["event_type"] == REJ]
    assert len(rej) == 1
    assert json.loads(rej[0]["reason_codes_json"]) == ["low_confidence"]
    raw = rej[0]["payload_json"]
    assert len(raw.encode("utf-8")) <= _payload_bound_bytes()
    assert "NaN" not in raw
    payload = json.loads(raw)
    assert payload["confidence"] is None
    assert payload["truncated"] is True
    assert payload["rejected_total"] == 100
    assert len(payload["rejected"]) == 16
    assert {r["reason"] for r in payload["rejected"]} == {"low_confidence"}


def test_hit_after_rejected_miss_persists_no_second_rejected_row(tmp_path, monkeypatch):
    _db_setup(tmp_path, monkeypatch, "r-aud4", "q-aud4")
    pf = _pf()
    with patch.object(DomainPrefilter, "_call_openai", return_value={"domains": ["xyz"], "confidence": 0.9}):
        pf.filter_domains(Q, AVAIL)
        pf.filter_domains(Q, AVAIL)
    types = [r["event_type"] for r in _db_events("r-aud4", "q-aud4")]
    assert types.count(DOMAIN_PREFILTER_CACHE_MISS) == 1
    assert types.count(REJ) == 1
    assert types.count(DOMAIN_PREFILTER_CACHE_HIT) == 1


# --------------------------------------------------------------------------------------
# Bounds
# --------------------------------------------------------------------------------------


def test_bounds_100_unknown_entries(events):
    _run(_pf(), {"domains": [f"u{i}" for i in range(100)], "confidence": 0.9})
    _, p = _one(events)
    assert len(p["rejected"]) == 16
    assert len(p["proposed"]) == 16
    assert p["rejected_total"] == 100
    assert p["proposed_total"] == 100
    assert p["truncated"] is True
    assert [r["domain"] for r in p["rejected"]] == [f"u{i}" for i in range(16)]


@pytest.mark.parametrize(("n", "truncated"), [(16, False), (17, True)])
def test_bounds_exactly_16_not_truncated(events, n, truncated):
    _run(_pf(), {"domains": [f"u{i}" for i in range(n)], "confidence": 0.9})
    _, p = _one(events)
    assert p["truncated"] is truncated
    assert len(p["rejected"]) == 16


def test_bounds_label_300_chars(events):
    _run(_pf(), {"domains": ["a" * 300], "confidence": 0.9})
    _, p = _one(events)
    assert p["rejected"][0]["domain"] == "a" * 64
    assert p["proposed"][0] == "a" * 64
    assert p["truncated"] is True


@pytest.mark.parametrize(("n", "truncated"), [(64, False), (65, True)])
def test_bounds_label_edge(events, n, truncated):
    _run(_pf(), {"domains": ["a" * n], "confidence": 0.9})
    _, p = _one(events)
    assert p["truncated"] is truncated
    assert len(p["rejected"][0]["domain"]) == 64


def test_bounds_non_ascii_label(events):
    _run(_pf(), {"domains": ["ö" * 100], "confidence": 0.9})
    _, p = _one(events)
    assert p["rejected"][0]["domain"] == "ö" * 64


# Size bound derived from the contract: 16 entries x 64 chars x 4 bytes (max UTF-8) for ``rejected[].domain`` and
# again for ``proposed[]``, plus a fixed overhead.
_LABEL_BOUND_BYTES = 16 * 64 * 4
# Overhead: JSON keys, reason strings, digest, applied_domains and the {domain, reason} wrapper per entry (~2 KiB),
# plus slack for JSON escaping of control characters (6 bytes each instead of 4: 2 * 16 * 64 * 2 = 4 KiB).
_PAYLOAD_OVERHEAD_BYTES = 6144


def _payload_bound_bytes() -> int:
    return 2 * _LABEL_BOUND_BYTES + _PAYLOAD_OVERHEAD_BYTES


def test_bounds_payload_size(events):
    _run(_pf(), {"domains": ["ö" * 100] * 3 + [f"{'z' * 80}{i}" for i in range(40)], "confidence": 0.9})
    _, p = _one(events)
    assert len(json.dumps(p, ensure_ascii=False).encode("utf-8")) <= _payload_bound_bytes()


def test_bounds_payload_size_16_labels_of_4_byte_chars(events):
    labels = [chr(0x1F600 + i) * 64 for i in range(16)]
    _run(_pf(), {"domains": labels, "confidence": 0.9})
    _, p = _one(events)
    assert [r["domain"] for r in p["rejected"]] == labels
    assert all(len(x.encode("utf-8")) == 256 for x in p["proposed"])
    size = len(json.dumps(p, ensure_ascii=False).encode("utf-8"))
    assert 2 * _LABEL_BOUND_BYTES <= size <= _payload_bound_bytes()


def test_bounds_payload_has_no_query_text(events):
    _run(_pf(), {"domains": ["xyz"], "confidence": 0.9})
    _, p = _one(events)
    assert Q not in json.dumps(p)


def test_audit_label_of_arbitrary_object_does_not_raise():
    label, cut = _audit_label(object())
    assert isinstance(label, str)
    assert cut is False or cut is True


# --------------------------------------------------------------------------------------
# Pure-function unit tests
# --------------------------------------------------------------------------------------


def test_proposal_items_table():
    assert _proposal_items(["a", "a"]) == ["a", "a"]
    assert _proposal_items({"a": 1, "b": 2}) == ["a", "b"]
    assert _proposal_items("medical") == ["medical"]
    assert _proposal_items("") == []
    assert _proposal_items(None) == []
    assert _proposal_items(3) == [3]
    assert _proposal_items(True) == [True]


def test_audit_label_table():
    assert _audit_label("abc") == ("abc", False)
    assert _audit_label(None) == ("null", False)
    assert _audit_label(True) == ("true", False)
    assert _audit_label(1.0) == ("1.0", False)
    assert _audit_label(["a"]) == ('["a"]', False)
    assert _audit_label("x" * 65) == ("x" * 64, True)


def test_audit_value_key_distinguishes_types():
    assert len({_audit_value_key(v) for v in (1, True, 1.0, "1")}) == 4


def _build(result: Any, **kw: Any) -> dict[str, Any] | None:
    args: dict[str, Any] = {
        "parse_status": None,
        "available_domains": list(AVAIL),
        "applied_domains": ["core", "medical"],
        "max_domains": 3,
        "threshold": 0.5,
        "routing_fallback": False,
    }
    args.update(kw)
    return _build_prefilter_rejection_record(result, **args)


def test_builder_returns_none_for_clean_accept():
    assert _build({"domains": ["medical"], "confidence": 0.9}) is None
    assert _build({}) is None
    assert _build("not a dict") is None


def test_builder_does_not_mutate_inputs():
    result = {"domains": ["xyz", "medical"], "confidence": 0.9}
    avail, applied = list(AVAIL), ["core", "medical"]
    snap = copy.deepcopy(result)
    rec = _build(result, available_domains=avail, applied_domains=applied)
    assert rec is not None
    assert (result, avail, applied) == (snap, AVAIL, ["core", "medical"])
    rec["applied_domains"].append("INJECTED")
    assert applied == ["core", "medical"]


def test_builder_parse_failed_precedence():
    rec = _build({"domains": ["xyz"], "confidence": 0.9}, parse_status="failed")
    assert rec is not None
    assert rec["decision"] == "parse_failed"
    assert rec["proposed"] == []
    assert rec["rejected"] == []
    assert rec["reason_codes"] == ["parse_failed"]
