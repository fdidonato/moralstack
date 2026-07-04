"""Controller integration tests for speculative token accounting."""

from __future__ import annotations

import threading
import types
from unittest.mock import MagicMock, patch

from moralstack.models.decision_explanation import DecisionExplanation
from moralstack.models.risk import OperationalRisk, RiskCategory, RiskPolicyAction
from moralstack.models.risk.schema import RiskEstimation
from moralstack.orchestration.controller import OrchestrationController
from moralstack.orchestration.null_persistence import NullPersistence
from moralstack.orchestration.safe_refusal_generator import RefusalGenerationResult
from moralstack.orchestration.types import (
    ConvergenceOutcome,
    Decision,
    DeliberationState,
    OrchestratorConfig,
    ProcessedRequest,
)
from moralstack.utils.output_protection import OutputProtector


def test_speculative_generate_persist_kwargs_include_token_usage_json():
    ctrl = OrchestrationController(
        config=OrchestratorConfig(),
        policy=MagicMock(model="gpt-test"),
        risk_estimator=MagicMock(),
        critic=None,
        simulator=None,
        hindsight=None,
        perspectives=None,
        constitution_store=None,
        output_protector=OutputProtector(),
        protected_system_prompt="system",
        persistence=NullPersistence(),
    )

    class _Result:
        text = "draft"
        tokens_used = 42
        prompt_tokens = 30
        completion_tokens = 12
        token_usage_source = "exact"
        prompt_used = "p"
        system_used = "system"

    ctrl.policy.generate_messages = MagicMock(return_value=_Result())
    ctrl.policy.generate = MagicMock(return_value=_Result())
    ctrl._output_protector.validate = MagicMock(return_value=MagicMock(cleaned="draft"))

    draft, persist_kwargs = ctrl._speculative_generate(ProcessedRequest(prompt="hello", request_id="r1"))
    assert draft == "draft"
    assert persist_kwargs is not None
    assert persist_kwargs.get("token_usage_json") is not None
    assert '"total_tokens": 42' in persist_kwargs["token_usage_json"]


def test_finalize_token_accounting_synchronous_on_refuse_route_regardless_of_speculative():
    barrier = threading.Event()
    spec_completed = threading.Event()

    def slow_speculative(self, request):
        barrier.wait(timeout=5.0)
        spec_completed.set()
        return None, None

    risk_result = RiskEstimation(
        score=0.99,
        confidence=0.9,
        risk_category=RiskCategory.CLEARLY_HARMFUL,
        operational_risk=OperationalRisk.HIGH,
    )

    class _FastRisk:
        def estimate(self, prompt: str, **kwargs):
            return risk_result

    ctrl = OrchestrationController(
        config=OrchestratorConfig(enable_speculative_generation=True),
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

    refuse_decision = Decision(
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
    explanation = DecisionExplanation(
        request_id="req",
        final_action="REFUSE",
        risk_score=0.99,
        risk_category="clearly_harmful",
    )

    with (
        patch("moralstack.orchestration.controller.decide_action", return_value=(refuse_decision, explanation)),
        patch("moralstack.orchestration.controller.apply_safe_complete_gating", lambda d, *a, **k: d),
        patch(
            "moralstack.orchestration.refusal_handler.generate_llm_safe_refusal_detailed",
            return_value=RefusalGenerationResult(text="no", system_prompt="s", user_prompt="u", attempts=1),
        ),
        patch.object(ctrl, "_finalize_token_accounting") as finalize_mock,
    ):
        from moralstack.observability.context import set_current_request_id, set_current_run_id

        set_current_run_id("run-test")
        set_current_request_id("req")
        result = ctrl.process(ProcessedRequest(prompt="x", request_id="req"))

    assert result.error is None
    finalize_mock.assert_called_once()
    assert not spec_completed.is_set()
    barrier.set()


def test_finalize_token_accounting_synchronous_on_safe_complete_route():
    barrier = threading.Event()
    spec_completed = threading.Event()

    def slow_speculative(self, request):
        barrier.wait(timeout=5.0)
        spec_completed.set()
        return None, None

    risk_result = RiskEstimation(
        score=0.5,
        confidence=0.8,
        risk_category=RiskCategory.SENSITIVE,
        operational_risk=OperationalRisk.LOW,
    )

    class _FastRisk:
        def estimate(self, prompt: str, **kwargs):
            return risk_result

    ctrl = OrchestrationController(
        config=OrchestratorConfig(enable_speculative_generation=True),
        policy=None,
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

    safe_complete_decision = Decision(
        final_action="SAFE_COMPLETE",
        path="FAST_PATH",
        intent_clarity="MEDIUM",
        misuse_plausibility="MEDIUM",
        actionability_risk="MEDIUM",
        triggered_principles=[],
        hard_violations=[],
        risk_signals=[],
        reason_codes=[],
    )
    explanation = DecisionExplanation(
        request_id="req-safe",
        final_action="SAFE_COMPLETE",
        risk_score=0.5,
        risk_category="sensitive",
    )

    with (
        patch("moralstack.orchestration.controller.decide_action", return_value=(safe_complete_decision, explanation)),
        patch("moralstack.orchestration.controller.apply_safe_complete_gating", lambda d, *a, **k: d),
        patch.object(ctrl, "_finalize_token_accounting") as finalize_mock,
    ):
        from moralstack.observability.context import set_current_request_id, set_current_run_id

        set_current_run_id("run-test-safe")
        set_current_request_id("req-safe")
        result = ctrl.process(ProcessedRequest(prompt="x", request_id="req-safe"))

    assert result.error is None
    finalize_mock.assert_called_once()
    assert not spec_completed.is_set()
    barrier.set()


def test_finalize_token_accounting_synchronous_on_deliberative_constrained_generation_route():
    barrier = threading.Event()
    spec_completed = threading.Event()

    def slow_speculative(self, request):
        barrier.wait(timeout=5.0)
        spec_completed.set()
        return None, None

    risk_result = RiskEstimation(
        score=0.85,
        confidence=0.9,
        risk_category=RiskCategory.CLEARLY_HARMFUL,
        operational_risk=OperationalRisk.HIGH,
    )

    class _FastRisk:
        def estimate(self, prompt: str, **kwargs):
            return risk_result

    ctrl = OrchestrationController(
        config=OrchestratorConfig(enable_speculative_generation=True),
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

    decision = Decision(
        final_action="NORMAL_COMPLETE",
        path="FAST_PATH",
        intent_clarity="HIGH",
        misuse_plausibility="LOW",
        actionability_risk="LOW",
        triggered_principles=[],
        hard_violations=[],
        risk_signals=[],
        reason_codes=[],
    )
    explanation = DecisionExplanation(
        request_id="req-delib",
        final_action="NORMAL_COMPLETE",
        risk_score=0.85,
        risk_category="clearly_harmful",
    )
    state = DeliberationState(cycle=1, draft_response="Deliberated response.")
    outcome = ConvergenceOutcome(
        should_continue=False,
        converged=True,
        stop_reason="CONVERGED",
        cycle=1,
        max_cycles=3,
    )

    with (
        patch(
            "moralstack.orchestration.controller.decide_action",
            side_effect=[(decision, explanation), (decision, explanation)],
        ),
        patch("moralstack.orchestration.controller.apply_safe_complete_gating", lambda d, *a, **k: d),
        patch(
            "moralstack.orchestration.controller.get_route",
            return_value=("deliberative", False, RiskPolicyAction.DELIBERATE),
        ),
        patch.object(ctrl._runner, "run_deliberative_path", return_value=(state, 0.85, outcome)),
        patch.object(ctrl, "_finalize_token_accounting") as finalize_mock,
    ):
        from moralstack.observability.context import set_current_request_id, set_current_run_id

        set_current_run_id("run-test-delib")
        set_current_request_id("req-delib")
        result = ctrl.process(ProcessedRequest(prompt="x", request_id="req-delib"))

    assert result.error is None
    finalize_mock.assert_called_once()
    assert not spec_completed.is_set()
    barrier.set()


def test_late_discarded_speculative_call_persisted_but_excluded_from_synchronous_usage():
    barrier = threading.Event()
    persisted = threading.Event()
    captured_calls: list[dict] = []

    def slow_speculative(self, request):
        barrier.wait(timeout=5.0)
        return "draft", {
            "cycle": 0,
            "phase": "speculative_generate",
            "module": "policy",
            "action": "generate (speculative)",
            "model": "gpt-test",
            "started_at": 0,
            "duration_ms": 1.0,
            "prompt": "p",
            "system_prompt": "s",
            "raw_response": "draft",
            "sequence_in_cycle": 0,
            "call_kind": "speculative",
            "token_usage_json": '{"input_tokens": 10, "output_tokens": 5, "total_tokens": 15, "source": "exact"}',
        }

    risk_result = RiskEstimation(
        score=0.99,
        confidence=0.9,
        risk_category=RiskCategory.CLEARLY_HARMFUL,
        operational_risk=OperationalRisk.HIGH,
    )

    class _FastRisk:
        def estimate(self, prompt: str, **kwargs):
            return risk_result

    ctrl = OrchestrationController(
        config=OrchestratorConfig(enable_speculative_generation=True),
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

    refuse_decision = Decision(
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
    explanation = DecisionExplanation(
        request_id="req-late",
        final_action="REFUSE",
        risk_score=0.99,
        risk_category="clearly_harmful",
    )

    def _capture_emit_llm_call(self, **kwargs):
        captured_calls.append(kwargs)
        # The refuse route emits its own (unrelated) llm_call synchronously
        # (refusal generation) — only the discarded speculative's call_outcome
        # signals what this test is waiting for.
        if kwargs.get("call_outcome") == "discarded":
            persisted.set()

    with (
        patch("moralstack.orchestration.controller.decide_action", return_value=(refuse_decision, explanation)),
        patch("moralstack.orchestration.controller.apply_safe_complete_gating", lambda d, *a, **k: d),
        patch(
            "moralstack.orchestration.refusal_handler.generate_llm_safe_refusal_detailed",
            return_value=RefusalGenerationResult(text="no", system_prompt="s", user_prompt="u", attempts=1),
        ),
        patch(
            "moralstack.orchestration.default_event_emitter.DefaultEventEmitter.emit_llm_call",
            _capture_emit_llm_call,
        ),
    ):
        from moralstack.observability.context import set_current_request_id, set_current_run_id

        set_current_run_id("run-test-late")
        set_current_request_id("req-late")
        result = ctrl.process(ProcessedRequest(prompt="x", request_id="req-late"))

        # Synchronous finalize already ran: the response is returned without
        # waiting for the still-in-flight speculative call to resolve.
        assert result.error is None
        assert not persisted.is_set()

        # Now let the discarded speculative resolve in the background — it must
        # still be persisted (call_outcome="discarded"), just too late to affect
        # the already-finalized synchronous usage (R8, accepted limit). Stay
        # inside the patch context: the background thread from abandon() runs
        # after process() has already returned, so the emit_llm_call patch
        # must still be active when it fires.
        barrier.set()
        assert persisted.wait(timeout=5.0)

    discarded_calls = [c for c in captured_calls if c.get("call_outcome") == "discarded"]
    assert discarded_calls
    assert discarded_calls[0].get("token_usage_json")
