"""DomainPrefilter per-domain ``selections`` shape: catalog rendering, response format, gate, projection, audit.

Drives the REAL ``_call_openai`` with a fake OpenAI client, so the ``selections`` -> ``domains``/``confidence``
projection runs exactly as in production. The legacy ``{domains, confidence}`` shape is pinned separately by
``test_domain_prefilter_routing_invariance.py`` and ``test_domain_prefilter_rejection_audit.py``.
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import MagicMock

import pytest

from moralstack.constitution.openai_config import OpenAIClientConfig
from moralstack.constitution.retriever import (
    _PREFILTER_PARSE_STATUS,
    DomainPrefilter,
    _build_prefilter_rejection_record,
    _parse_selection,
    _selection_confidences,
    _split_scope_notfor,
)
from moralstack.orchestration.orchestration_event_taxonomy import DOMAIN_PREFILTER_DOMAINS_REJECTED

REJ = DOMAIN_PREFILTER_DOMAINS_REJECTED
Q = "question about banking and finance regulation"
AVAIL = ["core", "legal", "medical", "cybersecurity", "finance"]
NAN = float("nan")


@pytest.fixture(autouse=True)
def _reset_parse_status():
    _PREFILTER_PARSE_STATUS.set(None)
    yield
    _PREFILTER_PARSE_STATUS.set(None)


@pytest.fixture
def events(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    rec: list[dict[str, Any]] = []
    monkeypatch.setattr("moralstack.constitution.retriever.persist_orchestration_event", lambda **kw: rec.append(kw))
    return rec


@pytest.fixture
def make_pf(monkeypatch: pytest.MonkeyPatch):
    """Factory: (prefilter, fake client) whose single completion returns ``content``."""

    def make(content: str, *, model: str = "gpt-4o", max_domains: int = 3, descriptions: dict | None = None):
        msg = MagicMock()
        msg.content = content
        choice = MagicMock()
        choice.message = msg
        resp = MagicMock()
        resp.choices = [choice]
        resp.usage = None
        client = MagicMock()
        client.chat.completions.create = MagicMock(return_value=resp)
        monkeypatch.setattr("openai.OpenAI", MagicMock(return_value=client))
        monkeypatch.setattr("moralstack.constitution.retriever.persist_llm_call", lambda **kw: None)
        pf = DomainPrefilter(
            openai_config=OpenAIClientConfig(api_key="sk-test", model=model),
            domain_descriptions=descriptions,
            max_domains=max_domains,
        )
        return pf, client

    return make


def _sels(*pairs: tuple[Any, Any]) -> str:
    return json.dumps(
        {
            "payload": "p",
            "selections": [{"domain": d, "evidence": "e", "confidence": c} for d, c in pairs],
            "wrapper_ignored": [],
        }
    )


def _rejection(evs: list[dict[str, Any]]) -> dict[str, Any] | None:
    found = [e for e in evs if e["event_type"] == REJ]
    assert len(found) <= 1
    return found[0]["payload"] if found else None


# --------------------------------------------------------------------------------------
# Catalog: YAML description split into scope + NOT line, rendered intact
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("desc", "scope", "notfor"),
    [
        ("Legal advice.", "Legal advice", ""),
        ("Legal advice. NOT for: harmful acts.", "Legal advice", "harmful acts."),
        ("A. NOT for: x (use b). NOT for: y.", "A", "x (use b). NOT for: y."),
        ("A. not  For :  x.", "A", "x."),
        ("Scope...", "Scope", ""),
    ],
    ids=["no_marker", "one_marker", "two_markers_second_inline", "case_and_spacing", "trailing_dots"],
)
def test_split_scope_notfor(desc: str, scope: str, notfor: str) -> None:
    assert _split_scope_notfor(desc) == (scope, notfor)


def test_catalog_renders_scope_and_not_lines(make_pf) -> None:
    desc = {"legal": "Law topics. NOT for: crimes (use violent_crime). NOT for: politics.", "medical": "Health."}
    pf, client = make_pf(_sels(), descriptions=desc)
    pf.filter_domains(Q, AVAIL)
    system_prompt = client.chat.completions.create.call_args.kwargs["messages"][0]["content"]
    assert "- legal: Law topics.\n  NOT: crimes (use violent_crime). NOT for: politics.\n" in system_prompt
    assert "- medical: Health.\n" in system_prompt
    # No description -> domain name only.
    assert "- finance\n" in system_prompt


# --------------------------------------------------------------------------------------
# Response format and token budget
# --------------------------------------------------------------------------------------


def test_json_schema_enum_is_request_candidates_without_core(make_pf) -> None:
    pf, client = make_pf(_sels())
    pf.filter_domains(Q, ["core", "medical", "legal"])
    kwargs = client.chat.completions.create.call_args.kwargs
    rf = kwargs["response_format"]
    assert rf["type"] == "json_schema"
    assert rf["json_schema"]["strict"] is True
    schema = rf["json_schema"]["schema"]
    assert schema["additionalProperties"] is False
    assert schema["required"] == ["payload", "selections", "wrapper_ignored"]
    item = schema["properties"]["selections"]["items"]
    assert item["additionalProperties"] is False
    assert item["required"] == ["domain", "evidence", "confidence"]
    assert item["properties"]["domain"]["enum"] == ["legal", "medical"]
    assert kwargs["max_tokens"] == DomainPrefilter.PREFILTER_MAX_OUTPUT_TOKENS == 400


@pytest.mark.parametrize("model", ["gpt-4o-2024-05-13", "my-local-model"])
def test_unsupported_model_falls_back_to_json_object(make_pf, model: str) -> None:
    pf, client = make_pf(_sels(("legal", 0.9)), model=model)
    assert pf.filter_domains(Q, AVAIL) == ["core", "legal"]
    assert client.chat.completions.create.call_args.kwargs["response_format"] == {"type": "json_object"}


def test_reasoning_model_uses_max_completion_tokens(make_pf) -> None:
    pf, client = make_pf(_sels(), model="gpt-5")
    pf.filter_domains(Q, AVAIL)
    kwargs = client.chat.completions.create.call_args.kwargs
    assert kwargs["max_completion_tokens"] == 400
    assert "max_tokens" not in kwargs


# --------------------------------------------------------------------------------------
# Per-domain gate
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("content", "k", "expected"),
    [
        (_sels(("legal", 0.9), ("medical", 0.3)), 3, ["core", "legal"]),
        (_sels(("legal", 0.4)), 3, ["core"]),
        (_sels(("legal", 0.5)), 3, ["core", "legal"]),
        (_sels(("legal", 0.49999)), 3, ["core"]),
        (_sels(("medical", 0.6), ("legal", 0.9)), 3, ["core", "legal", "medical"]),
        (_sels(("medical", 0.9), ("legal", 0.9)), 3, ["core", "medical", "legal"]),
        (
            _sels(("legal", 0.9), ("medical", 0.8), ("cybersecurity", 0.7), ("finance", 0.6)),
            3,
            ["core", "legal", "medical", "cybersecurity"],
        ),
        (_sels(("legal", 0.6), ("legal", 0.9), ("medical", 0.7)), 2, ["core", "legal", "medical"]),
        (_sels(("legal", 0.3), ("legal", 0.9)), 3, ["core", "legal"]),
        (_sels(("xyz", 0.99), ("legal", 0.9)), 3, ["core", "legal"]),
        (_sels(("legal", 0.9), ("medical", 0.8)), 0, ["core"]),
        (_sels(), 3, ["core"]),
        (_sels(("legal", "0.9"), ("medical", NAN), ("finance", True)), 3, ["core", "finance", "legal"]),
        (json.dumps({"selections": ["legal", {"confidence": 0.9}, {"domain": 3, "confidence": 0.9}]}), 3, ["core"]),
    ],
    ids=[
        "weak_secondary_dropped",
        "all_below",
        "exact_threshold",
        "just_below",
        "confidence_order",
        "tie_keeps_model_order",
        "over_cap",
        "dup_dedup_then_cap",
        "dup_any_entry_clears",
        "unknown_dropped",
        "cap_zero",
        "empty_selections",
        "str_nan_bool_confidence",
        "non_dict_and_bad_domain",
    ],
)
def test_per_domain_gate(make_pf, events, content: str, k: int, expected: list[str]) -> None:
    pf, _ = make_pf(content, model="my-local-model" if "xyz" in content else "gpt-4o", max_domains=k)
    out = pf.filter_domains(Q, AVAIL)
    assert out == expected
    assert list(pf._cache.values()) == [expected]


# --------------------------------------------------------------------------------------
# Projection onto the legacy fields read by the audit
# --------------------------------------------------------------------------------------


def test_projection_orders_by_confidence_and_leaves_selections_intact(make_pf) -> None:
    content = _sels(("medical", 0.3), ("legal", 0.9), ("finance", NAN), ("cybersecurity", 0.7))
    pf, _ = make_pf(content)
    data = pf._call_openai("USER QUERY:\nx", system_prompt="s")
    assert data["domains"] == ["legal", "cybersecurity", "medical", "finance"]
    assert data["confidence"] == 0.9
    assert all("_conf" not in s for s in data["selections"])
    assert [s["domain"] for s in data["selections"]] == ["medical", "legal", "finance", "cybersecurity"]


def test_projection_does_not_overwrite_model_domains(make_pf) -> None:
    content = json.dumps(
        {"domains": ["medical"], "confidence": 0.95, "selections": [{"domain": "legal", "confidence": 0.9}]}
    )
    pf, _ = make_pf(content)
    data = pf._call_openai("USER QUERY:\nx", system_prompt="s")
    assert data["domains"] == ["medical"]
    assert data["confidence"] == 0.95


# --------------------------------------------------------------------------------------
# Audit: per-domain attribution for the selections shape
# --------------------------------------------------------------------------------------


def test_audit_weak_secondary_is_low_confidence_not_over_cap(make_pf, events) -> None:
    pf, _ = make_pf(_sels(("legal", 0.9), ("medical", 0.3)))
    out = pf.filter_domains(Q, AVAIL)
    p = _rejection(events)
    assert p is not None
    assert p["applied_domains"] == out == ["core", "legal"]
    assert p["proposed"] == ["legal", "medical"]
    assert p["confidence"] == 0.9
    assert p["rejected"] == [{"domain": "medical", "reason": "low_confidence"}]
    assert p["reason_codes"] == ["low_confidence"]


def test_audit_non_finite_confidence_is_low_confidence(make_pf, events) -> None:
    pf, _ = make_pf(_sels(("legal", 0.9), ("medical", NAN)))
    pf.filter_domains(Q, AVAIL)
    p = _rejection(events)
    assert p is not None
    assert p["rejected"] == [{"domain": "medical", "reason": "low_confidence"}]


def test_audit_all_below_threshold(make_pf, events) -> None:
    pf, _ = make_pf(_sels(("legal", 0.4), ("medical", 0.2)))
    pf.filter_domains(Q, AVAIL)
    p = _rejection(events)
    assert p is not None
    assert p["confidence"] == 0.4
    assert p["rejected"] == [
        {"domain": "legal", "reason": "low_confidence"},
        {"domain": "medical", "reason": "low_confidence"},
    ]


def test_audit_mixed_reasons_follow_routing_gate_order(make_pf, events) -> None:
    content = _sels(("legal", 0.9), ("medical", 0.3), ("xyz", 0.95), ("finance", 0.8), ("cybersecurity", 0.7), ("abc", 0.2))
    pf, _ = make_pf(content, model="my-local-model", max_domains=2)
    out = pf.filter_domains(Q, AVAIL)
    assert out == ["core", "legal", "finance"]
    p = _rejection(events)
    assert p is not None
    assert p["rejected"] == [
        {"domain": "xyz", "reason": "unknown_domain"},
        {"domain": "cybersecurity", "reason": "over_cap"},
        {"domain": "medical", "reason": "low_confidence"},
        {"domain": "abc", "reason": "low_confidence"},
    ]
    assert p["reason_codes"] == ["low_confidence", "over_cap", "unknown_domain"]


def test_audit_duplicate_clearing_once_is_applied_not_rejected(make_pf, events) -> None:
    pf, _ = make_pf(_sels(("legal", 0.3), ("legal", 0.9)))
    assert pf.filter_domains(Q, AVAIL) == ["core", "legal"]
    assert _rejection(events) is None


@pytest.mark.parametrize(
    ("sel", "expected"),
    [
        ({"domain": "legal", "confidence": 0.9}, ("legal", 0.9)),
        ({"domain": "legal", "confidence": "0.7"}, ("legal", 0.7)),
        ({"domain": "legal"}, ("legal", 0.0)),
        ({"domain": "legal", "confidence": NAN}, None),
        ({"domain": "legal", "confidence": None}, None),
        ({"domain": 3, "confidence": 0.9}, None),
        ("legal", None),
    ],
)
def test_parse_selection(sel: Any, expected: Any) -> None:
    assert _parse_selection(sel) == expected


def test_selection_confidences_keeps_best_scorable_entry() -> None:
    sels = [{"domain": "legal", "confidence": 0.3}, {"domain": "legal", "confidence": 0.9}, {"domain": "x"}, "bad"]
    assert _selection_confidences(sels) == {"legal": 0.9, "x": 0.0}


def test_builder_without_domain_confidences_is_legacy_attribution() -> None:
    result = {"domains": ["legal", "medical"], "confidence": 0.9}
    kw: dict[str, Any] = dict(
        parse_status="ok",
        available_domains=AVAIL,
        applied_domains=["core", "legal"],
        max_domains=1,
        threshold=0.5,
        routing_fallback=False,
    )
    legacy = _build_prefilter_rejection_record(result, **kw)
    assert legacy == _build_prefilter_rejection_record(result, domain_confidences=None, **kw)
    assert legacy is not None and legacy["rejected"] == [{"domain": "medical", "reason": "over_cap"}]
    per_domain = _build_prefilter_rejection_record(result, domain_confidences={"legal": 0.9, "medical": 0.3}, **kw)
    assert per_domain is not None and per_domain["rejected"] == [{"domain": "medical", "reason": "low_confidence"}]
