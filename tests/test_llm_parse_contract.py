"""Unit tests for shared LLM parse-contract metadata (Plan 3)."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from moralstack.models.policy import GenerationConfig, GenerationResult
from moralstack.models.risk.estimator import LLMBasedRiskEstimator
from moralstack.reports.runtime_decisions import enrich_llm_call_for_ui
from moralstack.utils.json_utils import JSONParseError
from moralstack.utils.llm_parse_contract import (
    PARSE_STATUS_OK,
    merge_parse_contract_into_summary,
    parse_dict_with_contract,
    parse_principle_id_list_with_contract,
)


def test_parse_dict_direct_ok():
    raw = '{"risk_score": 0.4, "confidence": 0.9}'
    d, c = parse_dict_with_contract(raw, strict_json_requested=True)
    assert d.get("risk_score") == 0.4
    assert c.get("parse_status") == PARSE_STATUS_OK
    assert c.get("fallback_used") is False


def test_parse_dict_fallback_ok():
    raw = 'Some noise {"risk_score": 0.2, "confidence": 0.8} more'
    d, c = parse_dict_with_contract(raw, strict_json_requested=True)
    assert d.get("risk_score") == 0.2
    assert c.get("fallback_used") is True


def test_parse_dict_empty_raises():
    with pytest.raises(JSONParseError):
        parse_dict_with_contract("", strict_json_requested=True)


def test_principle_ids_object_path():
    raw = '{"principle_ids": ["A.1", "B.2"]}'
    ids, c = parse_principle_id_list_with_contract(raw, strict_json_requested=True)
    assert ids == ["A.1", "B.2"]
    assert c.get("parse_status") == PARSE_STATUS_OK


def test_merge_summary_roundtrip():
    s = merge_parse_contract_into_summary(
        {"estimation_mode": "monolithic"},
        {"a": 1, "parse_status": "ok"},
    )
    d = json.loads(s)
    assert d["estimation_mode"] == "monolithic"
    assert d["parse_contract"]["a"] == 1


def test_enrich_llm_badges_from_parse_contract():
    call = {
        "parsed_summary_json": json.dumps(
            {
                "parse_contract": {
                    "strict_json_requested": True,
                    "response_contract": "json_object",
                    "parse_status": "fallback_ok",
                    "retry_count": 2,
                }
            }
        )
    }
    out = enrich_llm_call_for_ui(call)
    b = " ".join(out.get("semantic_badges") or [])
    assert "strict json" in b
    assert "json_object" in b
    assert "fallback parse" in b
    assert "retry x2" in b


def test_generation_config_includes_json_object():
    est = LLMBasedRiskEstimator(policy=None)
    cfg = est._build_generation_config()
    assert cfg is not None
    assert getattr(cfg, "response_format", None) == {"type": "json_object"}


def test_monolithic_retry_persist_parse_contract(monkeypatch):
    """After a clean JSON body, parse_contract records ok with no fallback."""
    clean = {
        "risk_score": 0.1,
        "confidence": 0.9,
        "risk_category": "benign",
        "rationale": "x",
        "request_type": "factual_query",
        "harm_type": "none",
        "domain_sensitivity": "LOW",
        "operational_risk": "NONE",
        "risk_policy_action": "ALLOW",
        "intent_clarity": "HIGH",
        "misuse_plausibility": "LOW",
        "actionability_risk": "LOW",
        "q1_deception_manipulation": "no",
        "q2_harm_instructions": "no",
        "q3_harm_physical": "no",
        "q4_harm_emotional": "no",
        "q5_privacy_violation": "no",
        "q6_illegal_activity": "no",
        "q7_child_safety": "no",
        "q8_self_harm_suicide": "no",
        "q9_cyber_malware": "no",
        "q10_weapons_explosives_toxins": "no",
        "q11_privacy_doxxing_stalking": "no",
        "q12_medical_harmful": "no",
        "detected_language": "en",
        "intent_to_harm": False,
        "requested_instructions": False,
        "intent_operational": False,
    }

    policy = MagicMock()
    policy.model = "gpt-4o-mini"

    def fake_gen(*_a, **kwargs):
        cfg = kwargs.get("config")
        assert isinstance(cfg, GenerationConfig)
        assert getattr(cfg, "response_format", None) == {"type": "json_object"}
        return GenerationResult(text=json.dumps(clean), tokens_used=10, finish_reason="stop")

    policy.generate = fake_gen

    captured: list[dict] = []

    def capture_persist(**kwargs):
        captured.append(dict(kwargs))
        return True

    est = LLMBasedRiskEstimator(policy=policy)
    gen_cfg = est._build_generation_config()
    assert gen_cfg is not None
    with patch("moralstack.models.risk.estimator.persist_llm_call", side_effect=capture_persist):
        _raw, _parsed = est._call_llm_with_retry("prompt", gen_cfg)

    assert captured
    summary = json.loads(captured[-1].get("parsed_summary_json") or "{}")
    pc = summary.get("parse_contract") or {}
    assert pc.get("parse_status") == PARSE_STATUS_OK
    assert pc.get("fallback_used") is False


def test_enrich_old_calls_without_parse_contract():
    out = enrich_llm_call_for_ui({"parsed_summary_json": '{"mini_estimator": "x"}'})
    assert isinstance(out.get("semantic_badges"), list)
