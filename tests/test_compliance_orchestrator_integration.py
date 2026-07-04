"""
Integration tests verifying that the orchestrator invokes the DCCL
and emits the corresponding events, WITHOUT altering pipeline behavior.

These tests are critical for Commit 2 acceptance: they ensure
isolation between DCCL emission and pipeline decision-making.
"""

from __future__ import annotations

from dataclasses import replace
from unittest.mock import MagicMock, patch

import pytest

from moralstack.compliance import ComplianceDecision, StructuredRule
from moralstack.models.decision_explanation import DecisionExplanation
from moralstack.models.risk import OperationalRisk, RiskCategory
from moralstack.models.risk.schema import RiskEstimation
from moralstack.orchestration.contract import DeveloperContract
from moralstack.orchestration.controller import OrchestrationController
from moralstack.orchestration.null_persistence import NullPersistence
from moralstack.orchestration.orchestration_event_taxonomy import (
    COMPLIANCE_LAYER_STARTED,
    COMPLIANCE_LAYER_VERDICT_MATCH,
    COMPLIANCE_LAYER_VERDICT_NO_CONTRACT,
)
from moralstack.orchestration.types import (
    Decision,
    FinalResponse,
    OrchestratorConfig,
    OrchestratorResult,
    ProcessedRequest,
    ResponseMetadata,
    ResponseType,
)
from moralstack.utils.output_protection import OutputProtector


@pytest.fixture
def benign_decision() -> Decision:
    return Decision(
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


def _benign_orchestrator_result(request_id: str) -> OrchestratorResult:
    return OrchestratorResult(
        response=FinalResponse(
            content="ok",
            response_type=ResponseType.DIRECT,
            metadata=ResponseMetadata(processing_time_ms=10, final_action="NORMAL_COMPLETE"),
        ),
        request_id=request_id,
        path="FAST_PATH",
        path_taken="fast",
    )


def _build_controller(*, enable_speculative: bool = False) -> OrchestrationController:
    risk_result = RiskEstimation(
        score=0.1,
        confidence=0.9,
        risk_category=RiskCategory.BENIGN,
        operational_risk=OperationalRisk.NONE,
    )

    class _FastRisk:
        def estimate(self, prompt: str, **kwargs) -> RiskEstimation:
            return risk_result

    config = OrchestratorConfig(enable_speculative_generation=enable_speculative)
    return OrchestrationController(
        config=config,
        policy=MagicMock(),
        risk_estimator=_FastRisk(),
        critic=MagicMock(),
        simulator=MagicMock(),
        hindsight=MagicMock(),
        perspectives=MagicMock(),
        constitution_store=None,
        output_protector=OutputProtector(),
        protected_system_prompt="system",
        persistence=NullPersistence(),
    )


def test_orchestrator_emits_dccl_events_for_request_with_contract(
    benign_decision: Decision,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MORALSTACK_DCCL_EVALUATION_PATH", "structured")
    ctrl = _build_controller()

    rule = StructuredRule(rule_id="r1", trigger_pattern="PING", action_payload="PONG")
    contract = replace(
        DeveloperContract.from_text("if user says PING reply PONG"),
        structured_rules=(rule,),
    )
    req = ProcessedRequest(prompt="PING", request_id="req-dccl-contract", developer_contract=contract)

    explanation = DecisionExplanation(
        request_id=req.request_id,
        final_action="NORMAL_COMPLETE",
        risk_score=0.1,
        risk_category="benign",
    )

    emitted: list[str] = []

    def _capture(**kwargs):
        emitted.append(kwargs.get("event_type", ""))

    with (
        patch("moralstack.orchestration.controller.decide_action", return_value=(benign_decision, explanation)),
        patch("moralstack.orchestration.controller.apply_safe_complete_gating", lambda d, *a, **k: d),
        patch("moralstack.observability.emit_helpers.persist_orchestration_event", side_effect=_capture),
        patch.object(
            ctrl._runner,
            "run_benign_fast_path",
            return_value=_benign_orchestrator_result(req.request_id),
        ),
    ):
        result = ctrl.process(req)

    assert COMPLIANCE_LAYER_STARTED in emitted
    assert COMPLIANCE_LAYER_VERDICT_MATCH in emitted
    assert result.compliance_verdict is not None
    assert result.compliance_verdict.decision == ComplianceDecision.MATCH


def test_orchestrator_no_dccl_events_without_contract(
    benign_decision: Decision,
) -> None:
    ctrl = _build_controller()
    req = ProcessedRequest(prompt="hello", request_id="req-dccl-no-contract")

    explanation = DecisionExplanation(
        request_id=req.request_id,
        final_action="NORMAL_COMPLETE",
        risk_score=0.1,
        risk_category="benign",
    )

    emitted: list[str] = []

    def _capture(**kwargs):
        emitted.append(kwargs.get("event_type", ""))

    with (
        patch("moralstack.orchestration.controller.decide_action", return_value=(benign_decision, explanation)),
        patch("moralstack.orchestration.controller.apply_safe_complete_gating", lambda d, *a, **k: d),
        patch("moralstack.observability.emit_helpers.persist_orchestration_event", side_effect=_capture),
        patch.object(
            ctrl._runner,
            "run_benign_fast_path",
            return_value=_benign_orchestrator_result(req.request_id),
        ),
    ):
        result = ctrl.process(req)

    assert COMPLIANCE_LAYER_STARTED in emitted
    assert COMPLIANCE_LAYER_VERDICT_NO_CONTRACT in emitted
    assert result.compliance_verdict is not None
    assert result.compliance_verdict.decision == ComplianceDecision.NO_CONTRACT


def test_pipeline_decision_unchanged_without_validated_draft(
    benign_decision: Decision,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """DCCL MATCH without validated draft does not activate the compliance fast-path."""
    monkeypatch.setenv("MORALSTACK_DCCL_EVALUATION_PATH", "structured")
    ctrl = _build_controller()

    rule = StructuredRule(rule_id="r1", trigger_pattern="PING", action_payload="PONG")
    contract = replace(
        DeveloperContract.from_text("if user says PING reply PONG"),
        structured_rules=(rule,),
    )
    req = ProcessedRequest(prompt="PING", request_id="req-dccl-isolation", developer_contract=contract)

    explanation = DecisionExplanation(
        request_id=req.request_id,
        final_action="NORMAL_COMPLETE",
        risk_score=0.1,
        risk_category="benign",
    )

    run_benign_mock = MagicMock(return_value=_benign_orchestrator_result(req.request_id))

    with (
        patch.object(ctrl, "_nonblocking_speculative_draft", return_value=""),
        # With no validated draft the controller attempts a contract regeneration;
        # return an empty string so revalidation deterministically fails and the
        # flow falls through to the benign fast-path. (The bare MagicMock policy
        # would otherwise yield a non-string ``text`` that output protection
        # cannot scrub.)
        patch.object(ctrl, "_regenerate_for_contract", return_value=""),
        patch("moralstack.orchestration.controller.decide_action", return_value=(benign_decision, explanation)),
        patch("moralstack.orchestration.controller.apply_safe_complete_gating", lambda d, *a, **k: d),
        patch("moralstack.observability.emit_helpers.persist_orchestration_event") as mock_dccl_persist,
        patch.object(ctrl._runner, "run_benign_fast_path", run_benign_mock),
    ):
        result = ctrl.process(req)

    assert result.compliance_verdict is not None
    assert result.compliance_verdict.decision == ComplianceDecision.MATCH
    assert result.compliance_verdict.speculative_draft_validated is False
    assert run_benign_mock.call_count == 1
    # Lazy-import guard (plan §7.1/§10): the DCCL persist import in
    # controller.py:1136 must resolve to the patched emit_helpers target.
    assert mock_dccl_persist.call_count >= 1
    assert result.path == "FAST_PATH"
