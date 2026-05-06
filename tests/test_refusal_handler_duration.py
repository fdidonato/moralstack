"""
Tests that RefusalHandler emits the real wall-clock duration of the refusal
LLM call to the observability sink.

Bug being fixed: refusal_handler.py:124-133 used a hardcoded
`duration_ms=0.0` placeholder for emit_llm_call, while the actual LLM call
inside `generate_llm_safe_refusal_detailed()` takes real time. This caused the
markdown report to display "Orchestration / Refusal: 0ms" misleadingly,
and the side-observation in the strong_reject investigation.

Fix: measure wall-clock around the `generate_llm_safe_refusal_detailed` call
and pass that duration to `emit_llm_call`.
"""

from __future__ import annotations

import time
from types import SimpleNamespace
from unittest.mock import patch

from moralstack.models.decision_explanation import DecisionExplanation
from moralstack.orchestration.refusal_handler import RefusalHandler
from moralstack.orchestration.safe_refusal_generator import RefusalGenerationResult
from moralstack.orchestration.types import Decision, ProcessedRequest, UserContext


class _CapturingEmitter:
    """Captures kwargs of emit_llm_call for assertions; ignores other emits."""

    def __init__(self) -> None:
        self.last_llm_call: dict | None = None

    def emit_llm_call(self, **kwargs):
        self.last_llm_call = kwargs

    def emit_decision_trace(self, **kwargs):
        # No-op; the test focuses on emit_llm_call duration only.
        pass


def _build_request() -> ProcessedRequest:
    return ProcessedRequest(
        request_id="req-refusal-dur",
        prompt="Test prompt that has been classified as harmful",
        user_context=UserContext(domain_overlay=None),
    )


def _build_decision() -> Decision:
    return Decision(
        final_action="REFUSE",
        path="FAST_PATH",
        intent_clarity="HIGH",
        misuse_plausibility="HIGH",
        actionability_risk="HIGH",
        triggered_principles=["CORE.NM.1"],
        hard_violations=[],
        risk_signals=["Q5:physical_harm"],
        reason_codes=["risk_clearly_harmful"],
    )


def _build_explanation() -> DecisionExplanation:
    return DecisionExplanation(
        request_id="req-refusal-dur",
        final_action="REFUSE",
        risk_score=0.95,
        risk_category="clearly_harmful",
        activated_signals=["Q5:physical_harm"],
        overlay_applied="",
        winning_rule="normative_refuse_guards",
        reason_codes=["RISK_CLEARLY_HARMFUL"],
        why_not_refuse="x",
        why_not_safe_complete="y",
        why_not_normal_complete="z",
        timestamp=time.time(),
    )


def _build_risk_estimation() -> SimpleNamespace:
    return SimpleNamespace(
        detected_language="en",
        detected_domain=None,
        rationale="harmful",
        operational_risk="HIGH",
        requested_instructions=True,
        intent_to_harm=True,
        intent_operational=True,
        risk_category=SimpleNamespace(value="clearly_harmful"),
    )


def _build_trace() -> SimpleNamespace:
    return SimpleNamespace(
        response_type="",
        deliberation_cycles_actual=0,
        modules_called=set(),
        converged=True,
    )


def test_refusal_handler_emits_real_duration_ms():
    """The duration_ms passed to emit_llm_call must reflect the time spent
    inside generate_llm_safe_refusal, not a hardcoded 0.0 placeholder.
    """
    emitter = _CapturingEmitter()
    handler = RefusalHandler(policy=None, constitution_store=None, event_emitter=emitter)

    def slow_refusal(**kwargs):
        time.sleep(0.05)  # 50ms — easily distinguishable from 0.0 placeholder
        return RefusalGenerationResult(
            text="I'm sorry, but I cannot help with that.",
            system_prompt="<system>",
            user_prompt="<user>",
            attempts=1,
        )

    with patch(
        "moralstack.orchestration.refusal_handler.generate_llm_safe_refusal_detailed",
        side_effect=slow_refusal,
    ):
        handler.handle(
            request=_build_request(),
            decision=_build_decision(),
            explanation=_build_explanation(),
            risk_estimation=_build_risk_estimation(),
            risk_score=0.95,
            start_time=time.time(),
            trace=_build_trace(),
        )

    assert emitter.last_llm_call is not None, "RefusalHandler did not call emit_llm_call"
    captured_duration = emitter.last_llm_call.get("duration_ms")
    assert captured_duration is not None, "duration_ms missing from emit_llm_call kwargs"
    # 50ms sleep should yield ~50-200ms wall time on any reasonable runner;
    # bound generously to avoid flakiness.
    assert captured_duration >= 40.0, (
        f"duration_ms must reflect real LLM latency (>= 40ms with 50ms sleep stub); got {captured_duration}"
    )
    assert captured_duration < 5000.0, (
        f"duration_ms suspiciously high (sanity bound 5s); got {captured_duration}"
    )
