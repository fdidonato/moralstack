"""Observability persistence for constitution domain prefilter (risk vs deliberation)."""

from __future__ import annotations

import json
from unittest.mock import patch

from moralstack.constitution.retriever import (
    RETRIEVAL_PHASE_DELIBERATION,
    RETRIEVAL_PHASE_RISK_ROUTING,
    _persist_constitution_llm_call,
)


def test_persist_risk_routing_uses_cycle0_seq_minus10():
    captured: dict[str, object] = {}

    def _cap(**kw):
        captured.update(kw)
        return True

    contract = {"parse_status": "ok", "strict_json_requested": True}
    with patch("moralstack.constitution.retriever.persist_llm_call", side_effect=_cap):
        _persist_constitution_llm_call(
            action="domain_prefilter",
            system_prompt="s",
            prompt="p",
            raw_response="{}",
            duration_ms=1.0,
            started_at=1000,
            parse_contract=contract,
            model="gpt-test",
            retrieval_phase=RETRIEVAL_PHASE_RISK_ROUTING,
        )

    assert captured["cycle"] == 0
    assert captured["sequence_in_cycle"] == -10
    summary = json.loads(str(captured["parsed_summary_json"]))
    assert summary["retrieval_phase"] == RETRIEVAL_PHASE_RISK_ROUTING


def test_persist_deliberation_retrieval_uses_cycle0_seq_minus1():
    captured: dict[str, object] = {}

    def _cap(**kw):
        captured.update(kw)
        return True

    contract = {"parse_status": "ok", "strict_json_requested": True}
    with patch("moralstack.constitution.retriever.persist_llm_call", side_effect=_cap):
        _persist_constitution_llm_call(
            action="domain_prefilter",
            system_prompt="s",
            prompt="p",
            raw_response="{}",
            duration_ms=1.0,
            started_at=2000,
            parse_contract=contract,
            model="gpt-test",
            retrieval_phase=RETRIEVAL_PHASE_DELIBERATION,
        )

    assert captured["cycle"] == 0
    assert captured["sequence_in_cycle"] == -1
    summary = json.loads(str(captured["parsed_summary_json"]))
    assert summary["retrieval_phase"] == RETRIEVAL_PHASE_DELIBERATION


def test_persist_constitution_llm_call_forwards_token_usage_json():
    captured: dict[str, object] = {}

    def _cap(**kw):
        captured.update(kw)
        return True

    usage_json = '{"prompt_tokens": 7, "completion_tokens": 3, "total_tokens": 10, "source": "exact"}'
    with patch("moralstack.constitution.retriever.persist_llm_call", side_effect=_cap):
        _persist_constitution_llm_call(
            action="domain_prefilter",
            system_prompt="s",
            prompt="p",
            raw_response="{}",
            duration_ms=1.0,
            started_at=1000,
            parse_contract={"parse_status": "ok"},
            model="gpt-test",
            token_usage_json=usage_json,
        )

    assert captured["token_usage_json"] == usage_json
