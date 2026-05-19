"""
Integration-style tests: controller must not block on speculative generation on routes that discard it.

Uses patches for decision/refusal only to pin routing (REFUSE); the property under test is scheduling,
not governance semantics.
"""

from __future__ import annotations

import threading
import types
from unittest.mock import MagicMock, patch

import pytest

from moralstack.models.decision_explanation import DecisionExplanation
from moralstack.models.risk import OperationalRisk, RiskCategory
from moralstack.models.risk.schema import RiskEstimation
from moralstack.orchestration.controller import OrchestrationController
from moralstack.orchestration.types import Decision, OrchestratorConfig, ProcessedRequest
from moralstack.persistence.null import NullPersistence
from moralstack.utils.output_protection import OutputProtector


@pytest.fixture
def refuse_decision() -> Decision:
    """REFUSE with hard violations so borderline deliberation does not override path_router."""
    return Decision(
        final_action="REFUSE",
        path="FAST_PATH",
        intent_clarity="HIGH",
        misuse_plausibility="HIGH",
        actionability_risk="HIGH",
        triggered_principles=[],
        hard_violations=["TEST.HARD"],
        risk_signals=[],
        reason_codes=[],
    )


def test_refuse_route_returns_before_speculative_worker_finishes(
    refuse_decision: Decision,
) -> None:
    """
    Deterministic check: speculative generation is still blocked on a barrier when process() returns.

    If the controller incorrectly awaited the speculative future on REFUSE, process() would not
    return until the barrier is released.
    """
    barrier = threading.Event()
    spec_started = threading.Event()
    spec_completed = threading.Event()

    def slow_speculative(self: OrchestrationController, request: ProcessedRequest) -> tuple[None, None]:
        spec_started.set()
        barrier.wait(timeout=120.0)
        spec_completed.set()
        return None, None

    risk_result = RiskEstimation(
        score=0.99,
        confidence=0.9,
        risk_category=RiskCategory.CLEARLY_HARMFUL,
        operational_risk=OperationalRisk.HIGH,
    )

    class _FastRisk:
        def estimate(self, prompt: str, **kwargs) -> RiskEstimation:
            return risk_result

    config = OrchestratorConfig(enable_speculative_generation=True)
    ctrl = OrchestrationController(
        config=config,
        policy=MagicMock(),
        risk_estimator=_FastRisk(),
        critic=None,
        simulator=None,
        hindsight=None,
        perspectives=None,
        constitution_store=None,
        output_protector=OutputProtector(),
        protected_system_prompt="system",
        persistence=NullPersistence(),
    )
    ctrl._speculative_generate = types.MethodType(slow_speculative, ctrl)

    explanation = DecisionExplanation(
        request_id="req-lazy",
        final_action="REFUSE",
        risk_score=0.99,
        risk_category="clearly_harmful",
    )

    from moralstack.orchestration.safe_refusal_generator import RefusalGenerationResult

    with (
        patch("moralstack.orchestration.controller.decide_action", return_value=(refuse_decision, explanation)),
        patch("moralstack.orchestration.controller.apply_safe_complete_gating", lambda d, *a, **k: d),
        patch(
            "moralstack.orchestration.refusal_handler.generate_llm_safe_refusal_detailed",
            return_value=RefusalGenerationResult(text="[REFUSE]", system_prompt="<sys>", user_prompt="<user>", attempts=1),
        ),
    ):
        req = ProcessedRequest(prompt="blocked speculative draft", request_id="req-lazy-spec")
        result = ctrl.process(req)

    assert result.error is None
    assert result.path == "FAST_PATH"
    assert not spec_completed.is_set(), "process() must return before speculative task completes"

    barrier.set()
    assert spec_completed.wait(timeout=10.0), "speculative worker should finish after barrier release"
    assert spec_started.is_set()
