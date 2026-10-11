"""Routing invariance pins for DomainPrefilter (must pass on the pre-audit base AND after it).

These tests pin the CURRENT applied-domain list, cache content and ``llm_calls`` row for a table of
model outputs. The rejected-domains audit is write-only: none of these expectations may change.
Only symbols that exist on the base commit are imported; event types are string literals.
"""

from __future__ import annotations

import inspect
import json
import logging
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from moralstack.constitution.openai_config import OpenAIClientConfig
from moralstack.constitution.retriever import DomainPrefilter

Q = "question about banking and finance regulation"
AVAIL = ["core", "legal", "medical", "cybersecurity", "finance"]
NAN = float("nan")
INF = float("inf")


def _pf(max_domains: int = 3) -> DomainPrefilter:
    return DomainPrefilter(domain_keywords={d: [d] for d in AVAIL if d != "core"}, max_domains=max_domains)


def _run(pf: DomainPrefilter, result: Any, query: str = Q, avail: list[str] | None = None, **kw: Any) -> list[str]:
    with patch.object(DomainPrefilter, "_call_openai", return_value=result):
        return pf.filter_domains(query, avail if avail is not None else AVAIL, **kw)


def _fake_completion(content: str) -> MagicMock:
    msg = MagicMock()
    msg.content = content
    ch = MagicMock()
    ch.message = msg
    resp = MagicMock()
    resp.choices = [ch]
    resp.usage = None
    return resp


# id, result, expected, cached, max_domains
_ROUTING_TABLE: list[tuple[str, Any, list[str], bool, int]] = [
    ("clean", {"domains": ["medical"], "confidence": 0.9}, ["core", "medical"], True, 3),
    ("order_kept", {"domains": ["medical", "legal"], "confidence": 0.9}, ["core", "medical", "legal"], True, 3),
    ("conf_exact_threshold", {"domains": ["medical"], "confidence": 0.5}, ["core", "medical"], True, 3),
    ("conf_just_below", {"domains": ["medical"], "confidence": 0.49999}, ["core"], True, 3),
    ("conf_zero", {"domains": ["medical"], "confidence": 0.0}, ["core"], True, 3),
    ("conf_negative", {"domains": ["medical"], "confidence": -1}, ["core"], True, 3),
    ("conf_missing", {"domains": ["medical"]}, ["core"], True, 3),
    ("conf_int_one", {"domains": ["legal"], "confidence": 1}, ["core", "legal"], True, 3),
    ("conf_true", {"domains": ["legal"], "confidence": True}, ["core", "legal"], True, 3),
    ("conf_false", {"domains": ["legal"], "confidence": False}, ["core"], True, 3),
    ("conf_nan", {"domains": ["legal"], "confidence": NAN}, ["core"], True, 3),
    ("conf_inf", {"domains": ["legal"], "confidence": INF}, ["core", "legal"], True, 3),
    ("conf_str", {"domains": ["legal"], "confidence": "0.9"}, ["core"], False, 3),
    ("conf_none", {"domains": ["legal"], "confidence": None}, ["core"], False, 3),
    ("conf_list", {"domains": ["legal"], "confidence": [0.9]}, ["core"], False, 3),
    ("domains_none", {"domains": None, "confidence": 0.9}, ["core"], False, 3),
    ("domains_int", {"domains": 3, "confidence": 0.9}, ["core"], False, 3),
    ("domains_missing_hi_conf", {"confidence": 0.9}, ["core"], True, 3),
    ("domains_empty", {"domains": [], "confidence": 0.9}, ["core"], True, 3),
    ("domains_str", {"domains": "medical", "confidence": 0.9}, ["core"], True, 3),
    ("domains_str_empty", {"domains": "", "confidence": 0.9}, ["core"], True, 3),
    ("domains_dict_keys", {"domains": {"medical": 1, "legal": 2}, "confidence": 0.9}, ["core", "medical", "legal"], True, 3),
    ("domains_dict_unknown", {"domains": {"xyz": 1}, "confidence": 0.9}, ["core"], True, 3),
    ("domains_nested_list", {"domains": [["medical"]], "confidence": 0.9}, ["core"], True, 3),
    ("domains_mixed_types", {"domains": [None, 2, "medical"], "confidence": 0.9}, ["core", "medical"], True, 3),
    (
        "unknown_filtered_first",
        {"domains": ["xyz", "legal", "medical", "cybersecurity", "finance"], "confidence": 0.9},
        ["core", "legal", "medical", "cybersecurity"],
        True,
        3,
    ),
    (
        "over_cap_k3",
        {"domains": ["legal", "medical", "cybersecurity", "finance"], "confidence": 0.9},
        ["core", "legal", "medical", "cybersecurity"],
        True,
        3,
    ),
    ("over_cap_k1", {"domains": ["legal", "medical"], "confidence": 0.9}, ["core", "legal"], True, 1),
    ("over_cap_k0", {"domains": ["legal"], "confidence": 0.9}, ["core"], True, 0),
    (
        "dup_takes_cap_slots",
        {"domains": ["legal", "legal", "legal", "medical"], "confidence": 0.9},
        ["core", "legal"],
        True,
        3,
    ),
    (
        "core_takes_cap_slot",
        {"domains": ["core", "legal", "medical", "finance"], "confidence": 0.9},
        ["core", "legal", "medical"],
        True,
        3,
    ),
    ("core_only", {"domains": ["core"], "confidence": 0.9}, ["core"], True, 3),
    ("case_variant", {"domains": ["Medical"], "confidence": 0.9}, ["core"], True, 3),
    ("leading_space", {"domains": [" medical"], "confidence": 0.9}, ["core"], True, 3),
    ("empty_string_domain", {"domains": [""], "confidence": 0.9}, ["core"], True, 3),
    ("empty_result", {}, ["core"], True, 3),
    ("low_conf_with_domains", {"domains": ["medical", "legal"], "confidence": 0.3}, ["core"], True, 3),
]


@pytest.mark.parametrize(
    ("result", "expected", "cached", "k"),
    [pytest.param(r, e, c, k, id=i) for i, r, e, c, k in _ROUTING_TABLE],
)
def test_routing_table(result: Any, expected: list[str], cached: bool, k: int) -> None:
    pf = _pf(max_domains=k)
    out = _run(pf, result)
    assert out == expected
    if cached:
        assert len(pf._cache) == 1
        entry = list(pf._cache.values())[0]
        assert entry == expected
        assert out is not entry
    else:
        assert pf._cache == {}
        assert len(pf._cache) == 0


def test_outer_except_logs_warning_and_core_only(caplog: pytest.LogCaptureFixture) -> None:
    pf = _pf()
    with caplog.at_level(logging.WARNING, logger="moralstack.constitution.retriever"):
        out = _run(pf, {"domains": ["legal"], "confidence": "0.9"})
    assert out == ["core"]
    assert any("DomainPrefilter failed" in rec.getMessage() for rec in caplog.records)


def test_no_candidate_domains_caches_core_without_llm() -> None:
    pf = _pf()
    with patch.object(DomainPrefilter, "_call_openai", return_value={"domains": ["legal"], "confidence": 0.9}) as m:
        out = pf.filter_domains(Q, ["core"])
    assert out == ["core"]
    assert m.call_count == 0
    assert list(pf._cache.values()) == [["core"]]


def test_short_query_returns_empty_not_cached() -> None:
    pf = _pf()
    with patch.object(DomainPrefilter, "_call_openai", return_value={"domains": ["legal"], "confidence": 0.9}) as m:
        out = pf.filter_domains("short", AVAIL)
    assert out == []
    assert m.call_count == 0
    assert pf._cache == {}


def test_cache_hit_returns_copy_and_skips_llm() -> None:
    pf = _pf()
    with patch.object(DomainPrefilter, "_call_openai", return_value={"domains": ["medical"], "confidence": 0.9}) as m:
        first = pf.filter_domains(Q, AVAIL)
        first.append("INJECTED")
        second = pf.filter_domains(Q, AVAIL)
    assert m.call_count == 1
    assert second == ["core", "medical"]
    assert list(pf._cache.values()) == [["core", "medical"]]


def test_signatures_pinned() -> None:
    params = inspect.signature(DomainPrefilter._call_openai).parameters
    assert list(params) == ["self", "prompt", "system_prompt", "response_format", "retrieval_phase"]
    assert params["system_prompt"].kind is inspect.Parameter.KEYWORD_ONLY
    assert params["response_format"].kind is inspect.Parameter.KEYWORD_ONLY
    assert params["response_format"].default is None
    assert params["retrieval_phase"].kind is inspect.Parameter.KEYWORD_ONLY
    assert params["retrieval_phase"].default == "risk_routing"
    for fn in (DomainPrefilter._filter_domains_scoped, DomainPrefilter.filter_domains):
        p = inspect.signature(fn).parameters["retrieval_phase"]
        assert p.kind is inspect.Parameter.KEYWORD_ONLY


def test_real_call_openai_does_not_change_llm_calls_row() -> None:
    pf = DomainPrefilter(
        openai_config=OpenAIClientConfig(api_key="sk-test", model="gpt-4o-mini"),
        domain_keywords={d: [d] for d in AVAIL if d != "core"},
        max_domains=3,
    )
    text = json.dumps({"domains": ["medical"], "confidence": 0.9})
    rec: list[dict[str, Any]] = []
    with (
        patch("openai.OpenAI") as ctor,
        patch("moralstack.constitution.retriever.persist_llm_call", side_effect=lambda **kw: rec.append(kw)),
    ):
        client = MagicMock()
        client.chat.completions.create = MagicMock(side_effect=[_fake_completion(text)])
        ctor.return_value = client
        out = pf.filter_domains(Q, AVAIL)
    assert out == ["core", "medical"]
    assert len(rec) == 1
    row = rec[0]
    assert set(row) == {
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
    assert row["raw_response"] == text
    assert row["cycle"] == 0
    assert row["sequence_in_cycle"] == -10
    assert row["phase"] == "constitution_retrieval"
    summary = json.loads(row["parsed_summary_json"])
    assert set(summary) == {"module", "retrieval_phase", "parse_contract"}
