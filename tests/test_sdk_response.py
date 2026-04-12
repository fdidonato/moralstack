"""Tests for moralstack.sdk.response — GovernanceMetadata, GovernedResponse."""

from typing import Any
from unittest.mock import MagicMock

import pytest

from moralstack.sdk.response import GovernanceMetadata, GovernedResponse, _SyntheticChoice

# =============================================================================
# Helpers to build mock OrchestratorResult
# =============================================================================


def _make_metadata(**overrides: Any) -> Any:
    defaults = dict(
        final_action="NORMAL_COMPLETE",
        risk_score=0.1,
        risk_category="CLEARLY_BENIGN",
        path="FAST_PATH",
        domain_overlay=None,
        reason_codes=[],
        winning_rule="low_risk",
        decision_reason="Benign request",
        processing_time_ms=150,
        deliberation_cycles=0,
        triggered_principles=[],
        why_not_refuse="Risk too low",
        why_not_safe_complete="No sensitive domain",
    )
    defaults.update(overrides)
    meta = MagicMock()
    for k, v in defaults.items():
        setattr(meta, k, v)
    return meta


def _make_result(final_action: str = "NORMAL_COMPLETE", content: str = "Hello!", **meta_overrides: Any) -> Any:
    result = MagicMock()
    result.response.content = content
    result.response.metadata = _make_metadata(final_action=final_action, **meta_overrides)
    result.conversation_id = "conv-123"
    result.turn_index = 0
    return result


# =============================================================================
# GovernanceMetadata
# =============================================================================


class TestGovernanceMetadata:
    def test_from_result_normal_complete(self):
        result = _make_result("NORMAL_COMPLETE")
        meta = GovernanceMetadata.from_result(result)

        assert meta.final_action == "NORMAL_COMPLETE"
        assert meta.risk_score == 0.1
        assert meta.risk_category == "CLEARLY_BENIGN"
        assert meta.path == "FAST_PATH"
        assert meta.conversation_id == "conv-123"
        assert meta.turn_index == 0

    def test_from_result_refuse(self):
        result = _make_result(
            "REFUSE",
            risk_score=0.95,
            risk_category="CLEARLY_HARMFUL",
            path="FAST_PATH",
        )
        meta = GovernanceMetadata.from_result(result)
        assert meta.final_action == "REFUSE"
        assert meta.risk_score == 0.95

    def test_from_result_safe_complete(self):
        result = _make_result(
            "SAFE_COMPLETE",
            risk_score=0.5,
            risk_category="SENSITIVE",
            domain_overlay="healthcare",
            path="DELIBERATIVE_PATH",
            deliberation_cycles=2,
        )
        meta = GovernanceMetadata.from_result(result)
        assert meta.final_action == "SAFE_COMPLETE"
        assert meta.domain_overlay == "healthcare"
        assert meta.deliberation_cycles == 2

    def test_metadata_is_frozen(self):
        result = _make_result()
        meta = GovernanceMetadata.from_result(result)
        with pytest.raises((AttributeError, TypeError)):
            meta.final_action = "REFUSE"  # type: ignore

    def test_reason_codes_are_copied(self):
        original_codes = ["DUAL_USE", "SENSITIVE_DOMAIN"]
        result = _make_result(reason_codes=original_codes)
        meta = GovernanceMetadata.from_result(result)
        assert meta.reason_codes == original_codes
        # Mutating the original list must not affect metadata
        original_codes.append("NEW")
        assert len(meta.reason_codes) == 2

    def test_triggered_principles_are_copied(self):
        result = _make_result(triggered_principles=["HARM_AVOIDANCE", "PRIVACY"])
        meta = GovernanceMetadata.from_result(result)
        assert meta.triggered_principles == ["HARM_AVOIDANCE", "PRIVACY"]


# =============================================================================
# GovernedResponse
# =============================================================================


class TestGovernedResponse:
    def test_from_normal_has_openai_response(self):
        openai_resp = MagicMock()
        openai_resp.choices[0].message.content = "Generated text"
        result = _make_result("NORMAL_COMPLETE")
        resp = GovernedResponse.from_normal(openai_resp, result)

        assert resp.openai_response is openai_resp
        assert resp.governance_metadata.final_action == "NORMAL_COMPLETE"
        assert resp.is_passthrough is False
        assert resp.governance_content is None

    def test_from_refusal_has_no_openai_response(self):
        result = _make_result("REFUSE", content="I cannot help with that.")
        resp = GovernedResponse.from_refusal(result)

        assert resp.openai_response is None
        assert resp.governance_content == "I cannot help with that."
        assert resp.governance_metadata.final_action == "REFUSE"

    def test_from_safe_wraps_openai_response(self):
        openai_resp = MagicMock()
        result = _make_result("SAFE_COMPLETE")
        resp = GovernedResponse.from_safe(openai_resp, result)

        assert resp.openai_response is openai_resp
        assert resp.governance_metadata.final_action == "SAFE_COMPLETE"

    def test_content_property_normal(self):
        openai_resp = MagicMock()
        openai_resp.choices = [MagicMock()]
        openai_resp.choices[0].message.content = "Normal response"
        result = _make_result("NORMAL_COMPLETE")
        resp = GovernedResponse.from_normal(openai_resp, result)
        assert resp.content == "Normal response"

    def test_content_property_refusal(self):
        result = _make_result("REFUSE", content="Refused.")
        resp = GovernedResponse.from_refusal(result)
        assert resp.content == "Refused."

    def test_choices_property_normal_delegates(self):
        openai_resp = MagicMock()
        choices = [MagicMock()]
        openai_resp.choices = choices
        result = _make_result("NORMAL_COMPLETE")
        resp = GovernedResponse.from_normal(openai_resp, result)
        assert resp.choices is choices

    def test_choices_property_refusal_is_synthetic(self):
        result = _make_result("REFUSE", content="No.")
        resp = GovernedResponse.from_refusal(result)
        choices = resp.choices
        assert len(choices) == 1
        assert isinstance(choices[0], _SyntheticChoice)
        assert choices[0].message.content == "No."

    def test_from_passthrough_is_passthrough(self):
        openai_resp = MagicMock()
        err = RuntimeError("pipeline down")
        resp = GovernedResponse.from_passthrough(openai_resp, err)

        assert resp.is_passthrough is True
        assert resp.openai_response is openai_resp
        assert resp.governance_metadata.final_action == "PASSTHROUGH"
        assert "PIPELINE_ERROR" in resp.governance_metadata.reason_codes

    def test_from_pipeline_error_is_refusal(self):
        err = RuntimeError("critical failure")
        resp = GovernedResponse.from_pipeline_error(err)

        assert resp.openai_response is None
        assert resp.governance_metadata.final_action == "REFUSE"
        assert resp.content == "I'm unable to process this request at the moment."

    def test_model_property_normal(self):
        openai_resp = MagicMock()
        openai_resp.model = "gpt-4o"
        result = _make_result("NORMAL_COMPLETE")
        resp = GovernedResponse.from_normal(openai_resp, result)
        assert resp.model == "gpt-4o"

    def test_model_property_refusal(self):
        result = _make_result("REFUSE")
        resp = GovernedResponse.from_refusal(result)
        assert resp.model is None


class TestSyntheticChoice:
    def test_has_message_content(self):
        choice = _SyntheticChoice("test content")
        assert choice.message.content == "test content"
        assert choice.message.role == "assistant"
        assert choice.finish_reason == "stop"
        assert choice.index == 0
