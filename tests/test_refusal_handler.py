"""Tests for refusal handler token accounting."""

from __future__ import annotations

import json
import time
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from moralstack.models.decision_explanation import DecisionExplanation
from moralstack.observability.token_usage import TokenUsage
from moralstack.orchestration.refusal_handler import RefusalHandler
from moralstack.orchestration.safe_refusal_generator import RefusalGenerationResult
from moralstack.orchestration.types import Decision, ProcessedRequest, UserContext


class _CapturingEmitter:
    last_llm_call: dict | None = None

    def emit_llm_call(self, **kwargs):
        self.last_llm_call = dict(kwargs)

    def emit_decision_trace(self, **kwargs):
        return None


def _request() -> ProcessedRequest:
    return ProcessedRequest(
        request_id="r1",
        prompt="bad",
        user_context=UserContext(domain_overlay=None),
    )


def _decision() -> Decision:
    return Decision(
        final_action="REFUSE",
        path="FAST_PATH",
        intent_clarity="HIGH",
        misuse_plausibility="HIGH",
        actionability_risk="HIGH",
        triggered_principles=[],
        hard_violations=[],
        risk_signals=[],
        reason_codes=["risk_clearly_harmful"],
    )


def _explanation() -> DecisionExplanation:
    return DecisionExplanation(
        request_id="r1",
        final_action="REFUSE",
        risk_score=0.9,
        risk_category="clearly_harmful",
        activated_signals=[],
        overlay_applied="",
        winning_rule="x",
        reason_codes=["RISK_CLEARLY_HARMFUL"],
        why_not_refuse="a",
        why_not_safe_complete="b",
        why_not_normal_complete="c",
        timestamp=time.time(),
    )


def _risk() -> SimpleNamespace:
    return SimpleNamespace(
        detected_language="en",
        detected_domain=None,
        rationale="harmful",
        operational_risk="HIGH",
        requested_instructions=False,
        intent_to_harm=False,
        intent_operational=False,
        risk_category=SimpleNamespace(value="clearly_harmful"),
    )


def _trace() -> SimpleNamespace:
    return SimpleNamespace(
        response_type="",
        deliberation_cycles_actual=0,
        modules_called=set(),
        converged=True,
    )


def test_refusal_handler_persists_token_usage_json_and_billable_when_attempts_positive():
    emitter = _CapturingEmitter()
    handler = RefusalHandler(policy=MagicMock(), constitution_store=None, event_emitter=emitter)
    usage = TokenUsage(10, 5, 15, "exact")

    with patch(
        "moralstack.orchestration.refusal_handler.generate_llm_safe_refusal_detailed",
        return_value=RefusalGenerationResult(
            text="I cannot help with that.",
            system_prompt="<sys>",
            user_prompt="<user>",
            attempts=1,
            token_usage=usage,
        ),
    ):
        handler.handle(
            request=_request(),
            decision=_decision(),
            explanation=_explanation(),
            risk_estimation=_risk(),
            risk_score=0.9,
            start_time=time.time(),
            trace=_trace(),
        )

    assert emitter.last_llm_call is not None
    assert emitter.last_llm_call["token_usage_json"] is not None
    assert json.loads(emitter.last_llm_call["token_usage_json"])["total_tokens"] == 15
    assert emitter.last_llm_call["billable_provider_call"] is True


def test_refusal_handler_no_client_marks_non_billable():
    emitter = _CapturingEmitter()
    handler = RefusalHandler(policy=None, constitution_store=None, event_emitter=emitter)

    with patch(
        "moralstack.orchestration.refusal_handler.generate_llm_safe_refusal_detailed",
        return_value=RefusalGenerationResult(
            text="[REFUSAL_FALLBACK]",
            system_prompt="<sys>",
            user_prompt="<user>",
            attempts=0,
            token_usage=TokenUsage(0, 0, 0, "missing"),
        ),
    ):
        handler.handle(
            request=_request(),
            decision=_decision(),
            explanation=_explanation(),
            risk_estimation=_risk(),
            risk_score=0.9,
            start_time=time.time(),
            trace=_trace(),
        )

    assert emitter.last_llm_call is not None
    assert emitter.last_llm_call["billable_provider_call"] is False


def test_refusal_handler_records_generation_model():
    """The refusal LLM call must carry the model that produced it, otherwise it
    surfaces as an unattributed "—" row in the per-model token panel."""
    emitter = _CapturingEmitter()
    handler = RefusalHandler(
        policy=SimpleNamespace(model="gpt-4o-mini"),
        constitution_store=None,
        event_emitter=emitter,
    )

    with patch(
        "moralstack.orchestration.refusal_handler.generate_llm_safe_refusal_detailed",
        return_value=RefusalGenerationResult(
            text="I cannot help with that.",
            system_prompt="<sys>",
            user_prompt="<user>",
            attempts=1,
            token_usage=TokenUsage(10, 5, 15, "exact"),
        ),
    ):
        handler.handle(
            request=_request(),
            decision=_decision(),
            explanation=_explanation(),
            risk_estimation=_risk(),
            risk_score=0.9,
            start_time=time.time(),
            trace=_trace(),
        )

    assert emitter.last_llm_call is not None
    assert emitter.last_llm_call["model"] == "gpt-4o-mini"
