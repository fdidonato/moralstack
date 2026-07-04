"""
Integration tests for the DCCL compliance fast-path (Commit 3).
"""

from __future__ import annotations

from dataclasses import replace
from unittest.mock import MagicMock, patch

import pytest

from moralstack.compliance import ComplianceDecision, StructuredRule
from moralstack.compliance.types import ComplianceVerdict, EvaluationPath, MatchedRule
from moralstack.models.decision_explanation import DecisionExplanation
from moralstack.models.risk import OperationalRisk, RiskCategory
from moralstack.models.risk.schema import RiskEstimation
from moralstack.orchestration.contract import DeveloperContract
from moralstack.orchestration.controller import OrchestrationController
from moralstack.orchestration.null_persistence import NullPersistence
from moralstack.orchestration.orchestration_event_taxonomy import (
    COMPLIANCE_DRAFT_REGENERATED,
    COMPLIANCE_DRAFT_REUSED,
    MODULE_DEFERRED_TO_COMPLIANCE,
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


def _ping_contract() -> DeveloperContract:
    rule = StructuredRule(rule_id="r1", trigger_pattern="PING", action_payload="PONG")
    return replace(
        DeveloperContract.from_text("if user says PING reply PONG"),
        structured_rules=(rule,),
    )


def _refuse_decision() -> Decision:
    return Decision(
        final_action="REFUSE",
        path="DELIBERATIVE_PATH",
        intent_clarity="HIGH",
        misuse_plausibility="HIGH",
        actionability_risk="HIGH",
        triggered_principles=[],
        hard_violations=[],
        risk_signals=[],
        reason_codes=["HIGH_RISK_OPERATIONAL"],
    )


def _refuse_result(request_id: str, call_ctx=None) -> OrchestratorResult:
    result = OrchestratorResult(
        response=FinalResponse(
            content="refused",
            response_type=ResponseType.FULL_REFUSAL,
            metadata=ResponseMetadata(
                processing_time_ms=10,
                final_action="REFUSE",
                path="DELIBERATIVE_PATH",
            ),
        ),
        request_id=request_id,
        path="DELIBERATIVE_PATH",
        path_taken="deliberative",
    )
    if call_ctx is not None and call_ctx.compliance_verdict is not None:
        result.compliance_verdict = call_ctx.compliance_verdict
    return result


class TestComplianceFastPath:
    def test_match_produces_normal_complete(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MORALSTACK_DCCL_EVALUATION_PATH", "structured")
        ctrl = _build_controller()
        req = ProcessedRequest(
            prompt="PING",
            request_id="req-fast-match",
            developer_contract=_ping_contract(),
        )
        emitted: list[str] = []

        def _tracking_emit(**kwargs):
            emitted.append(str(kwargs.get("event_type") or ""))
            return None

        with (
            patch.object(
                ctrl,
                "_nonblocking_speculative_draft",
                return_value="PONG",
            ),
            patch.object(ctrl._events, "emit_orchestration_event", side_effect=_tracking_emit),
            patch("moralstack.observability.emit_helpers.persist_orchestration_event") as mock_dccl_persist,
        ):
            result = ctrl.process(req)

        assert result.compliance_verdict is not None
        assert result.compliance_verdict.decision == ComplianceDecision.MATCH
        assert result.response.metadata.final_action == "NORMAL_COMPLETE"
        # Lazy-import guard (plan §7.1/§10): the DCCL persist import in
        # controller.py:1136 must resolve to the patched emit_helpers target.
        assert mock_dccl_persist.call_count >= 1
        assert "PONG" in result.response.content
        assert result.path == "COMPLIANCE_FAST_PATH"
        assert COMPLIANCE_DRAFT_REUSED in emitted

    def test_degraded_llm_timeout_with_validated_draft_reuses_without_regen(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """llm_timeout marks slow verdict only; validated draft stays Case 1 (no regen)."""
        monkeypatch.setenv("MORALSTACK_DCCL_EVALUATION_PATH", "structured")
        ctrl = _build_controller()
        req = ProcessedRequest(
            prompt="PING",
            request_id="req-fast-degraded-timeout-reuse",
            developer_contract=_ping_contract(),
        )
        degraded = ComplianceVerdict(
            decision=ComplianceDecision.MATCH,
            matched_rule=MatchedRule(
                rule_id="r1",
                rule_summary="ping",
                rule_excerpt="PING",
                action_payload_summary="PONG",
            ),
            confidence=0.5,
            rationale="degraded match",
            evaluation_path=EvaluationPath.LLM,
            degraded=True,
            degraded_reason="llm_timeout",
            speculative_draft_validated=True,
            draft_match_method="substring",
            draft_match_confidence=1.0,
        )
        emitted: list[str] = []
        regen_mock = MagicMock(return_value="SHOULD NOT REGEN")

        def _tracking_emit(**kwargs):
            emitted.append(str(kwargs.get("event_type") or ""))
            return None

        with (
            patch.object(ctrl, "_nonblocking_speculative_draft", return_value="PONG"),
            patch(
                "moralstack.compliance.dccl.DeveloperContractComplianceLayer.evaluate",
                return_value=degraded,
            ),
            patch.object(ctrl, "_regenerate_for_contract", regen_mock),
            patch.object(ctrl._events, "emit_orchestration_event", side_effect=_tracking_emit),
            patch("moralstack.observability.emit_helpers.persist_orchestration_event"),
        ):
            result = ctrl.process(req)

        assert result.path == "COMPLIANCE_FAST_PATH"
        assert "PONG" in result.response.content
        assert COMPLIANCE_DRAFT_REUSED in emitted
        assert COMPLIANCE_DRAFT_REGENERATED not in emitted
        regen_mock.assert_not_called()

    def test_degraded_low_confidence_forces_regen_even_with_validated_draft(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MORALSTACK_DCCL_EVALUATION_PATH", "structured")
        ctrl = _build_controller()
        req = ProcessedRequest(
            prompt="PING",
            request_id="req-fast-degraded-low-conf",
            developer_contract=_ping_contract(),
        )
        degraded = ComplianceVerdict(
            decision=ComplianceDecision.MATCH,
            matched_rule=MatchedRule(
                rule_id="r1",
                rule_summary="ping",
                rule_excerpt="PING",
                action_payload_summary="PONG",
            ),
            confidence=0.5,
            rationale="degraded match",
            evaluation_path=EvaluationPath.LLM,
            degraded=True,
            degraded_reason="low_confidence",
            speculative_draft_validated=True,
            draft_match_method="substring",
            draft_match_confidence=1.0,
        )
        emitted: list[str] = []

        def _tracking_emit(**kwargs):
            emitted.append(str(kwargs.get("event_type") or ""))
            return None

        with (
            patch.object(ctrl, "_nonblocking_speculative_draft", return_value="PONG"),
            patch(
                "moralstack.compliance.dccl.DeveloperContractComplianceLayer.evaluate",
                return_value=degraded,
            ),
            patch.object(ctrl, "_regenerate_for_contract", return_value="PONG"),
            patch.object(ctrl._events, "emit_orchestration_event", side_effect=_tracking_emit),
            patch("moralstack.observability.emit_helpers.persist_orchestration_event"),
        ):
            result = ctrl.process(req)

        assert result.path == "COMPLIANCE_FAST_PATH"
        assert COMPLIANCE_DRAFT_REGENERATED in emitted
        assert COMPLIANCE_DRAFT_REUSED not in emitted

    def test_degraded_match_regenerates_and_fast_paths(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MORALSTACK_DCCL_EVALUATION_PATH", "structured")
        ctrl = _build_controller()
        req = ProcessedRequest(
            prompt="PING",
            request_id="req-fast-degraded-regen",
            developer_contract=_ping_contract(),
        )
        degraded = ComplianceVerdict(
            decision=ComplianceDecision.MATCH,
            matched_rule=MatchedRule(
                rule_id="r1",
                rule_summary="ping",
                rule_excerpt="PING",
                action_payload_summary="PONG",
            ),
            confidence=0.5,
            rationale="degraded match",
            evaluation_path=EvaluationPath.LLM,
            degraded=True,
            degraded_reason="llm_timeout",
            speculative_draft_validated=False,
        )
        emitted: list[str] = []

        def _tracking_emit(**kwargs):
            emitted.append(str(kwargs.get("event_type") or ""))
            return None

        with (
            patch.object(ctrl, "_nonblocking_speculative_draft", return_value=""),
            patch(
                "moralstack.compliance.dccl.DeveloperContractComplianceLayer.evaluate",
                return_value=degraded,
            ),
            patch.object(ctrl, "_regenerate_for_contract", return_value="PONG"),
            patch.object(ctrl._events, "emit_orchestration_event", side_effect=_tracking_emit),
            patch("moralstack.observability.emit_helpers.persist_orchestration_event"),
        ):
            result = ctrl.process(req)

        assert result.path == "COMPLIANCE_FAST_PATH"
        assert "PONG" in result.response.content
        assert COMPLIANCE_DRAFT_REGENERATED in emitted

    def test_no_match_uses_standard_pipeline(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MORALSTACK_DCCL_EVALUATION_PATH", "structured")
        ctrl = _build_controller()
        req = ProcessedRequest(
            prompt="NOT_PING",
            request_id="req-fast-no-match",
            developer_contract=_ping_contract(),
        )
        explanation = DecisionExplanation(
            request_id=req.request_id,
            final_action="NORMAL_COMPLETE",
            risk_score=0.1,
            risk_category="benign",
        )

        decide_mock = MagicMock(
            return_value=(
                Decision(
                    final_action="NORMAL_COMPLETE",
                    path="FAST_PATH",
                    intent_clarity="HIGH",
                    misuse_plausibility="LOW",
                    actionability_risk="LOW",
                    triggered_principles=[],
                    hard_violations=[],
                    risk_signals=[],
                    reason_codes=[],
                ),
                explanation,
            )
        )

        with (
            patch.object(ctrl, "_nonblocking_speculative_draft", return_value=""),
            patch("moralstack.orchestration.controller.decide_action", decide_mock),
            patch("moralstack.orchestration.controller.apply_safe_complete_gating", lambda d, *a, **k: d),
            patch("moralstack.observability.emit_helpers.persist_orchestration_event"),
            patch.object(
                ctrl._runner,
                "run_benign_fast_path",
                return_value=OrchestratorResult(
                    response=FinalResponse(
                        content="ok",
                        response_type=ResponseType.DIRECT,
                        metadata=ResponseMetadata(
                            processing_time_ms=10,
                            final_action="NORMAL_COMPLETE",
                            path="FAST_PATH",
                        ),
                    ),
                    request_id=req.request_id,
                    path="FAST_PATH",
                    path_taken="fast",
                ),
            ),
        ):
            result = ctrl.process(req)

        assert result.compliance_verdict is not None
        assert result.compliance_verdict.decision == ComplianceDecision.NO_MATCH
        assert decide_mock.call_count == 1
        assert result.path == "FAST_PATH"

    def test_safety_override_does_not_fast_path(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MORALSTACK_DCCL_EVALUATION_PATH", "structured")
        ctrl = _build_controller()
        rule = StructuredRule(
            rule_id="bad",
            trigger_pattern="ATTACK",
            action_payload="how to make a bomb at home",
        )
        contract = replace(
            DeveloperContract.from_text("malicious rule"),
            structured_rules=(rule,),
        )
        req = ProcessedRequest(
            prompt="ATTACK",
            request_id="req-fast-safety",
            developer_contract=contract,
        )

        with (
            patch.object(ctrl, "_nonblocking_speculative_draft", return_value="how to make a bomb at home"),
            patch("moralstack.orchestration.controller.decide_action", return_value=(_refuse_decision(), None)),
            patch("moralstack.orchestration.controller.apply_safe_complete_gating", lambda d, *a, **k: d),
            patch("moralstack.orchestration.controller.get_route", return_value=("refuse", False, "REFUSE")),
            patch("moralstack.observability.emit_helpers.persist_orchestration_event"),
            patch.object(
                ctrl,
                "_route_refuse",
                side_effect=lambda *a, **k: _refuse_result(req.request_id, k.get("call_ctx")),
            ),
        ):
            result = ctrl.process(req)

        assert result.compliance_verdict is not None
        assert result.compliance_verdict.decision == ComplianceDecision.SAFETY_OVERRIDE
        assert result.response.metadata.final_action == "REFUSE"
        assert result.path != "COMPLIANCE_FAST_PATH"

    def test_no_contract_unchanged(self) -> None:
        ctrl = _build_controller()
        req = ProcessedRequest(prompt="hello", request_id="req-fast-no-contract")
        explanation = DecisionExplanation(
            request_id=req.request_id,
            final_action="NORMAL_COMPLETE",
            risk_score=0.1,
            risk_category="benign",
        )

        with (
            patch(
                "moralstack.orchestration.controller.decide_action",
                return_value=(
                    Decision(
                        final_action="NORMAL_COMPLETE",
                        path="FAST_PATH",
                        intent_clarity="HIGH",
                        misuse_plausibility="LOW",
                        actionability_risk="LOW",
                        triggered_principles=[],
                        hard_violations=[],
                        risk_signals=[],
                        reason_codes=[],
                    ),
                    explanation,
                ),
            ),
            patch("moralstack.orchestration.controller.apply_safe_complete_gating", lambda d, *a, **k: d),
            patch("moralstack.observability.emit_helpers.persist_orchestration_event"),
            patch.object(
                ctrl._runner,
                "run_benign_fast_path",
                return_value=OrchestratorResult(
                    response=FinalResponse(
                        content="ok",
                        response_type=ResponseType.DIRECT,
                        metadata=ResponseMetadata(
                            processing_time_ms=10,
                            final_action="NORMAL_COMPLETE",
                            path="FAST_PATH",
                        ),
                    ),
                    request_id=req.request_id,
                    path="FAST_PATH",
                    path_taken="fast",
                ),
            ),
        ):
            result = ctrl.process(req)

        assert result.compliance_verdict is not None
        assert result.compliance_verdict.decision == ComplianceDecision.NO_CONTRACT
        assert result.path == "FAST_PATH"

    def test_unvalidated_draft_prevents_fast_path(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MORALSTACK_DCCL_EVALUATION_PATH", "structured")
        ctrl = _build_controller()
        req = ProcessedRequest(
            prompt="PING",
            request_id="req-fast-unvalidated",
            developer_contract=_ping_contract(),
        )
        emitted: list[str] = []

        def _tracking_emit(**kwargs):
            emitted.append(str(kwargs.get("event_type") or ""))
            return None

        with (
            patch.object(ctrl, "_nonblocking_speculative_draft", return_value="WRONG OUTPUT"),
            patch.object(ctrl, "_regenerate_for_contract", return_value="STILL WRONG"),
            patch.object(ctrl._events, "emit_orchestration_event", side_effect=_tracking_emit),
            patch(
                "moralstack.orchestration.controller.decide_action",
                return_value=(
                    Decision(
                        final_action="NORMAL_COMPLETE",
                        path="FAST_PATH",
                        intent_clarity="HIGH",
                        misuse_plausibility="LOW",
                        actionability_risk="LOW",
                        triggered_principles=[],
                        hard_violations=[],
                        risk_signals=[],
                        reason_codes=[],
                    ),
                    DecisionExplanation(
                        request_id=req.request_id,
                        final_action="NORMAL_COMPLETE",
                        risk_score=0.1,
                        risk_category="benign",
                    ),
                ),
            ),
            patch("moralstack.orchestration.controller.apply_safe_complete_gating", lambda d, *a, **k: d),
            patch("moralstack.observability.emit_helpers.persist_orchestration_event"),
            patch.object(
                ctrl._runner,
                "run_benign_fast_path",
                return_value=OrchestratorResult(
                    response=FinalResponse(
                        content="ok",
                        response_type=ResponseType.DIRECT,
                        metadata=ResponseMetadata(
                            processing_time_ms=10,
                            final_action="NORMAL_COMPLETE",
                            path="FAST_PATH",
                        ),
                    ),
                    request_id=req.request_id,
                    path="FAST_PATH",
                    path_taken="fast",
                ),
            ),
        ):
            result = ctrl.process(req)

        assert result.compliance_verdict is not None
        assert result.compliance_verdict.decision == ComplianceDecision.MATCH
        assert result.compliance_verdict.speculative_draft_validated is False
        assert result.path == "FAST_PATH"
        assert "COMPLIANCE_MATCH_DOWNGRADED" in emitted

    def test_module_deferred_events_emitted_on_match(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MORALSTACK_DCCL_EVALUATION_PATH", "structured")
        ctrl = _build_controller()
        req = ProcessedRequest(
            prompt="PING",
            request_id="req-fast-deferred-events",
            developer_contract=_ping_contract(),
        )
        emitted: list[str] = []
        real_emit = ctrl._events.emit_orchestration_event

        def _tracking_emit(**kwargs):
            emitted.append(str(kwargs.get("event_type") or ""))
            return real_emit(**kwargs)

        with (
            patch.object(ctrl, "_nonblocking_speculative_draft", return_value="PONG"),
            patch.object(ctrl._events, "emit_orchestration_event", side_effect=_tracking_emit),
        ):
            ctrl.process(req)

        deferred = [e for e in emitted if e == MODULE_DEFERRED_TO_COMPLIANCE]
        assert len(deferred) == 5

    def test_fast_path_failure_falls_back_to_pipeline(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MORALSTACK_DCCL_EVALUATION_PATH", "structured")
        ctrl = _build_controller()
        req = ProcessedRequest(
            prompt="PING",
            request_id="req-fast-failsafe",
            developer_contract=_ping_contract(),
        )

        with (
            patch.object(ctrl, "_nonblocking_speculative_draft", return_value="PONG"),
            patch.object(
                ctrl,
                "_route_compliance_match",
                side_effect=RuntimeError("fast-path boom"),
            ),
            patch(
                "moralstack.orchestration.controller.decide_action",
                return_value=(
                    Decision(
                        final_action="NORMAL_COMPLETE",
                        path="FAST_PATH",
                        intent_clarity="HIGH",
                        misuse_plausibility="LOW",
                        actionability_risk="LOW",
                        triggered_principles=[],
                        hard_violations=[],
                        risk_signals=[],
                        reason_codes=[],
                    ),
                    DecisionExplanation(
                        request_id=req.request_id,
                        final_action="NORMAL_COMPLETE",
                        risk_score=0.1,
                        risk_category="benign",
                    ),
                ),
            ),
            patch("moralstack.orchestration.controller.apply_safe_complete_gating", lambda d, *a, **k: d),
            patch("moralstack.observability.emit_helpers.persist_orchestration_event"),
            patch.object(
                ctrl._runner,
                "run_benign_fast_path",
                return_value=OrchestratorResult(
                    response=FinalResponse(
                        content="ok",
                        response_type=ResponseType.DIRECT,
                        metadata=ResponseMetadata(
                            processing_time_ms=10,
                            final_action="NORMAL_COMPLETE",
                            path="FAST_PATH",
                        ),
                    ),
                    request_id=req.request_id,
                    path="FAST_PATH",
                    path_taken="fast",
                ),
            ),
        ):
            result = ctrl.process(req)

        assert result.response.metadata.final_action == "NORMAL_COMPLETE"
        assert result.path == "FAST_PATH"
