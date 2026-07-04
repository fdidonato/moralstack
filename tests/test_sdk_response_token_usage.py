"""Tests for GovernanceMetadata token usage fields."""

from __future__ import annotations

from types import SimpleNamespace

from moralstack.sdk.response import GovernanceMetadata


def _make_result(**meta_overrides):
    defaults = dict(
        final_action="NORMAL_COMPLETE",
        risk_score=0.1,
        risk_category="CLEARLY_BENIGN",
        path="FAST_PATH",
        domain_overlay=None,
        reason_codes=[],
        winning_rule="low_risk",
        decision_reason="ok",
        processing_time_ms=10,
        deliberation_cycles=0,
        triggered_principles=[],
        why_not_refuse="",
        why_not_safe_complete="",
    )
    defaults.update(meta_overrides)
    meta = SimpleNamespace(**defaults)
    result = SimpleNamespace()
    result.response = SimpleNamespace(metadata=meta)
    result.conversation_id = None
    result.turn_index = None
    result.compliance_verdict = None
    return result


def test_from_result_copies_token_fields_when_present():
    result = _make_result(
        input_tokens=100,
        output_tokens=50,
        total_tokens=150,
        llm_call_count=3,
        token_usage_missing_count=1,
        token_usage_estimated_count=2,
        usage_may_be_incomplete=True,
        incomplete_reason="speculative",
    )
    meta = GovernanceMetadata.from_result(result)
    assert meta.input_tokens == 100
    assert meta.total_tokens == 150
    assert meta.llm_call_count == 3
    assert meta.token_usage_missing_count == 1
    assert meta.token_usage_estimated_count == 2
    assert meta.usage_may_be_incomplete is True
    assert meta.incomplete_reason == "speculative"


def test_from_result_defaults_token_fields_to_zero_when_metadata_lacks_attrs():
    result = _make_result()
    meta = GovernanceMetadata.from_result(result)
    assert meta.input_tokens == 0
    assert meta.output_tokens == 0
    assert meta.total_tokens == 0
    assert meta.llm_call_count == 0
    assert meta.token_usage_missing_count == 0
    assert meta.token_usage_estimated_count == 0
    assert meta.usage_may_be_incomplete is False
    assert meta.incomplete_reason is None


def test_metadata_is_frozen():
    meta = GovernanceMetadata.from_result(_make_result(input_tokens=1))
    try:
        meta.input_tokens = 99  # type: ignore[misc]
        raised = False
    except AttributeError:
        raised = True
    assert raised
