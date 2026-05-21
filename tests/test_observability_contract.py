"""
Observability contract tests: full orchestration / llm_call / decision_trace sets per path.

Each test captures every emitted observability record and asserts the expected set so
traceability regressions fail in CI instead of surfacing only in the UI.
"""

from __future__ import annotations

import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Iterator
from unittest.mock import patch

import pytest

from moralstack.compliance.types import ComplianceDecision, ComplianceVerdict, EvaluationPath, MatchedRule
from moralstack.models.decision_explanation import DecisionExplanation
from moralstack.models.risk import OperationalRisk, RiskCategory, RiskPolicyAction
from moralstack.models.risk.schema import RiskEstimation
from moralstack.orchestration.contract import DeveloperContract
from moralstack.orchestration.controller import OrchestrationController
from moralstack.orchestration.orchestration_event_taxonomy import (
    COMPLIANCE_DRAFT_REGENERATED,
    COMPLIANCE_DRAFT_REUSED,
    COMPLIANCE_LAYER_STARTED,
    COMPLIANCE_LAYER_VERDICT_MATCH,
    COMPLIANCE_LAYER_VERDICT_NO_CONTRACT,
    COMPLIANCE_LAYER_VERDICT_NO_MATCH,
    COMPLIANCE_LAYER_VERDICT_SAFETY_OVERRIDE,
    COMPLIANCE_MATCH_DOWNGRADED,
    MODULE_DEFERRED_TO_COMPLIANCE,
    SPECULATIVE_DRAFT_REUSED,
    SPECULATIVE_RESULT_DISCARDED,
    SPECULATIVE_RESULT_USED,
    SPECULATIVE_STARTED,
)
from moralstack.orchestration.types import (
    Decision,
    FinalResponse,
    OrchestratorResult,
    ProcessedRequest,
    ResponseMetadata,
    ResponseType,
    RiskEstimationProtocol,
)
from moralstack.runtime.trace.decision_trace import DecisionTrace
from tests.test_compliance_fast_path import (
    _build_controller,
    _ping_contract,
    _refuse_decision,
    _refuse_result,
)


@dataclass
class _Captured:
    orch_events: list[dict[str, Any]] = field(default_factory=list)
    llm_calls: list[dict[str, Any]] = field(default_factory=list)
    decision_traces: list[DecisionTrace] = field(default_factory=list)


def _event_types(captured: _Captured) -> set[str]:
    return {str(e.get("event_type") or "") for e in captured.orch_events}


def _events_of(captured: _Captured, event_type: str) -> list[dict[str, Any]]:
    return [e for e in captured.orch_events if (e.get("event_type") or "") == event_type]


def _compliance_trace(captured: _Captured) -> DecisionTrace | None:
    for dt in captured.decision_traces:
        if (dt.stage or "").strip().upper() == "COMPLIANCE_LAYER":
            return dt
    return None


def _assert_started_at_sane(captured: _Captured) -> None:
    for call in captured.llm_calls:
        started_at = call.get("started_at")
        if started_at is not None:
            assert int(started_at) > 1_700_000_000_000, f"implausible started_at: {started_at}"


def _assert_no_phantom_llm_calls(captured: _Captured) -> None:
    for call in captured.llm_calls:
        action = str(call.get("action") or "")
        duration_ms = call.get("duration_ms")
        assert not (duration_ms == 0 and "speculative-reuse" in action), call


def _assert_transversal(captured: _Captured) -> None:
    _assert_started_at_sane(captured)
    _assert_no_phantom_llm_calls(captured)


def _assert_risk_estimator_llm_present(captured: _Captured) -> None:
    risk_calls = [c for c in captured.llm_calls if (c.get("module") or "") == "risk_estimator"]
    assert risk_calls, "expected at least one risk_estimator llm_call"


def _assert_speculative_generate(
    captured: _Captured,
    *,
    call_outcome: str,
    count: int = 1,
) -> None:
    spec_calls = [c for c in captured.llm_calls if c.get("phase") == "speculative_generate"]
    assert len(spec_calls) == count, f"expected {count} speculative_generate call(s), got {len(spec_calls)}"
    assert spec_calls[0].get("call_outcome") == call_outcome


def _build_obs_controller(*, enable_speculative: bool = True) -> OrchestrationController:
    """Controller with risk estimator that records a tracked llm_call (no network)."""
    ctrl = _build_controller(enable_speculative=enable_speculative)
    if enable_speculative:
        return ctrl

    inner = ctrl.risk_estimator

    class _TrackedRiskEstimator:
        def estimate(self, prompt: str, **kwargs: Any) -> RiskEstimation:
            from moralstack.orchestration.persistence_helpers import record_llm_call

            result = inner.estimate(prompt, **kwargs)
            record_llm_call(
                None,
                None,
                {
                    "cycle": 0,
                    "phase": "risk_estimate",
                    "module": "risk_estimator",
                    "action": "estimate",
                    "started_at": int(time.time() * 1000),
                    "duration_ms": 2.0,
                    "call_kind": "risk",
                    "sequence_in_cycle": -9,
                },
            )
            return result

    ctrl.risk_estimator = _TrackedRiskEstimator()
    return ctrl


@contextmanager
def _capture(ctrl: OrchestrationController) -> Iterator[_Captured]:
    captured = _Captured()

    def _track_orch(**kwargs: Any) -> None:
        captured.orch_events.append(dict(kwargs))

    def _track_llm_emit(**kwargs: Any) -> None:
        captured.llm_calls.append(dict(kwargs))

    def _track_record(_logger: Any, _diag: Any, persist_kwargs: dict[str, Any] | None) -> None:
        if persist_kwargs:
            captured.llm_calls.append(dict(persist_kwargs))

    def _track_trace(dt: DecisionTrace, path: str | None = None) -> None:
        captured.decision_traces.append(dt)

    with (
        patch.object(ctrl._events, "emit_orchestration_event", side_effect=_track_orch),
        patch.object(ctrl._events, "emit_llm_call", side_effect=_track_llm_emit),
        patch("moralstack.persistence.sink.persist_orchestration_event", side_effect=_track_orch),
        patch("moralstack.orchestration.persistence_helpers.record_llm_call", side_effect=_track_record),
        patch("moralstack.runtime.trace.decision_trace.append_decision_trace", side_effect=_track_trace),
        patch("moralstack.orchestration.controller.append_decision_trace", side_effect=_track_trace),
    ):
        yield captured


class _PolicyGenResult:
    def __init__(self, text: str) -> None:
        self.text = text
        self.prompt_used = None
        self.system_used = None

    def token_usage_json(self) -> None:
        return None


def _speculative_persist_meta() -> dict[str, Any]:
    started_at = int(time.time() * 1000)
    return {
        "cycle": 0,
        "phase": "speculative_generate",
        "module": "policy",
        "action": "generate (speculative)",
        "started_at": started_at,
        "duration_ms": 5.0,
        "call_kind": "speculative",
        "sequence_in_cycle": 0,
    }


@contextmanager
def _deterministic_speculative(ctrl: OrchestrationController, draft: str) -> Iterator[None]:
    """Pre-completed speculative overlap (no policy.generate race with regen)."""
    from concurrent.futures import Future, ThreadPoolExecutor

    from moralstack.orchestration.speculative_overlap import SpeculativeOverlapHandle

    risk = RiskEstimation(
        score=0.1,
        confidence=0.9,
        risk_category=RiskCategory.BENIGN,
        operational_risk=OperationalRisk.NONE,
    )
    meta = _speculative_persist_meta()
    started_at = int(meta["started_at"])

    def _fake_overlap(_request: ProcessedRequest) -> SpeculativeOverlapHandle:
        from moralstack.orchestration.persistence_helpers import record_llm_call

        record_llm_call(
            None,
            None,
            {
                "cycle": 0,
                "phase": "risk_estimate",
                "module": "risk_estimator",
                "action": "estimate (parallel)",
                "started_at": started_at,
                "duration_ms": 2.0,
                "call_kind": "risk",
                "sequence_in_cycle": -9,
            },
        )
        ctrl._events.emit_orchestration_event(
            stage="orchestration",
            component="speculative",
            event_type=SPECULATIVE_STARTED,
            decision="started",
            status="ok",
            started_at=started_at,
            payload={"speculative_mode": True, "model": ""},
        )
        fut: Future[tuple[str | None, dict[str, Any] | None]] = Future()
        fut.set_result((draft, meta))
        executor = ThreadPoolExecutor(max_workers=1)
        return SpeculativeOverlapHandle(
            risk_estimation=risk,
            spec_future=fut,
            executor=executor,
            spec_started_at_ms=started_at,
            event_emitter=ctrl._events,
        )

    with patch.object(ctrl, "_run_speculative_overlap", side_effect=_fake_overlap):
        yield


def _deliberative_result(request_id: str) -> OrchestratorResult:
    return OrchestratorResult(
        response=FinalResponse(
            content="ok",
            response_type=ResponseType.DIRECT,
            metadata=ResponseMetadata(
                processing_time_ms=10,
                final_action="NORMAL_COMPLETE",
                path="DELIBERATIVE_PATH",
            ),
        ),
        request_id=request_id,
        path="DELIBERATIVE_PATH",
        path_taken="deliberative",
    )


def _deliberative_route_side_effect(request_id: str):
    def _route(*_args: Any, **kwargs: Any) -> OrchestratorResult:
        from moralstack.orchestration.persistence_helpers import record_llm_call

        record_llm_call(
            None,
            None,
            {
                "cycle": 1,
                "phase": "critic",
                "module": "critic",
                "action": "critique",
                "started_at": int(time.time() * 1000),
                "duration_ms": 3.0,
                "call_kind": "deliberation",
                "sequence_in_cycle": 1,
            },
        )
        result = _deliberative_result(request_id)
        call_ctx = kwargs.get("call_ctx")
        if call_ctx is not None and call_ctx.compliance_verdict is not None:
            result.compliance_verdict = call_ctx.compliance_verdict
        return result

    return _route


def _wait_background_speculative() -> None:
    time.sleep(0.2)


def _regenerate_pong_side_effect(
    _request: ProcessedRequest,
    _cv: ComplianceVerdict,
    _risk: RiskEstimationProtocol,
) -> str:
    """Mock regen that records compliance_regenerate llm_call (no policy.generate)."""
    from moralstack.orchestration.persistence_helpers import record_llm_call

    record_llm_call(
        None,
        None,
        {
            "cycle": 0,
            "phase": "policy_generate",
            "module": "policy",
            "action": "generate (compliance-regenerate)",
            "started_at": int(time.time() * 1000),
            "duration_ms": 1.0,
            "call_kind": "compliance_regenerate",
            "sequence_in_cycle": 1,
        },
    )
    return "PONG"


class TestObservabilityContract:
    def test_obs_case1_reuse_clean(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MORALSTACK_DCCL_EVALUATION_PATH", "structured")
        ctrl = _build_obs_controller(enable_speculative=True)
        req = ProcessedRequest(
            prompt="PING",
            request_id="obs-case1-clean",
            developer_contract=_ping_contract(),
        )

        with _capture(ctrl) as captured:
            with _deterministic_speculative(ctrl, "PONG"):
                result = ctrl.process(req)
            _wait_background_speculative()

        assert result.path == "COMPLIANCE_FAST_PATH"
        types = _event_types(captured)
        assert SPECULATIVE_STARTED in types
        assert COMPLIANCE_LAYER_STARTED in types
        assert COMPLIANCE_LAYER_VERDICT_MATCH in types
        assert COMPLIANCE_DRAFT_REUSED in types
        assert SPECULATIVE_RESULT_USED in types
        assert len(_events_of(captured, MODULE_DEFERRED_TO_COMPLIANCE)) == 5
        assert COMPLIANCE_DRAFT_REGENERATED not in types
        assert COMPLIANCE_MATCH_DOWNGRADED not in types
        assert SPECULATIVE_DRAFT_REUSED not in types
        assert SPECULATIVE_RESULT_DISCARDED not in types

        _assert_speculative_generate(captured, call_outcome="used")
        _assert_risk_estimator_llm_present(captured)

        dt = _compliance_trace(captured)
        assert dt is not None
        payload = dt.stage_payload or {}
        assert payload.get("risk_estimation_used_for_decision") is False
        assert result.compliance_verdict is not None
        assert result.compliance_verdict.draft_match_method == "substring"
        assert result.compliance_verdict.draft_match_confidence == 1.0
        assert dt.risk_score is not None

        _assert_transversal(captured)

    def test_obs_case1_reuse_degraded_timeout(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MORALSTACK_DCCL_EVALUATION_PATH", "structured")
        ctrl = _build_obs_controller(enable_speculative=True)
        req = ProcessedRequest(
            prompt="PING",
            request_id="obs-case1-timeout",
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

        with _capture(ctrl) as captured:
            with (
                _deterministic_speculative(ctrl, "PONG"),
                patch(
                    "moralstack.compliance.dccl.DeveloperContractComplianceLayer.evaluate",
                    return_value=degraded,
                ),
            ):
                result = ctrl.process(req)
            _wait_background_speculative()

        types = _event_types(captured)
        assert result.path == "COMPLIANCE_FAST_PATH"
        assert COMPLIANCE_LAYER_STARTED in types
        assert COMPLIANCE_LAYER_VERDICT_MATCH in types
        assert COMPLIANCE_DRAFT_REUSED in types
        assert SPECULATIVE_RESULT_USED in types
        assert COMPLIANCE_DRAFT_REGENERATED not in types
        assert SPECULATIVE_RESULT_DISCARDED not in types
        reuse_payload = _events_of(captured, COMPLIANCE_DRAFT_REUSED)[0].get("payload") or {}
        assert reuse_payload.get("degraded") is True
        assert reuse_payload.get("degraded_reason") == "llm_timeout"
        _assert_speculative_generate(captured, call_outcome="used")
        _assert_transversal(captured)

    def test_obs_case2_low_confidence_regen(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MORALSTACK_DCCL_EVALUATION_PATH", "structured")
        ctrl = _build_obs_controller(enable_speculative=True)
        req = ProcessedRequest(
            prompt="PING",
            request_id="obs-case2-low-conf",
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

        with _capture(ctrl) as captured:
            with (
                _deterministic_speculative(ctrl, "PONG"),
                patch(
                    "moralstack.compliance.dccl.DeveloperContractComplianceLayer.evaluate",
                    return_value=degraded,
                ),
                patch.object(ctrl, "_regenerate_for_contract", side_effect=_regenerate_pong_side_effect),
            ):
                result = ctrl.process(req)
            _wait_background_speculative()

        types = _event_types(captured)
        assert result.path == "COMPLIANCE_FAST_PATH"
        assert COMPLIANCE_DRAFT_REGENERATED in types
        assert COMPLIANCE_DRAFT_REUSED not in types
        assert SPECULATIVE_RESULT_DISCARDED in types
        assert SPECULATIVE_RESULT_USED not in types
        _assert_speculative_generate(captured, call_outcome="discarded")
        regen_calls = [c for c in captured.llm_calls if c.get("call_kind") == "compliance_regenerate"]
        assert regen_calls, "expected compliance_regenerate llm_call"
        _assert_transversal(captured)

    def test_obs_case2_unvalidated_regen(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MORALSTACK_DCCL_EVALUATION_PATH", "structured")
        ctrl = _build_obs_controller(enable_speculative=True)
        req = ProcessedRequest(
            prompt="PING",
            request_id="obs-case2-unvalidated",
            developer_contract=_ping_contract(),
        )

        with _capture(ctrl) as captured:
            with (
                _deterministic_speculative(ctrl, "WRONG OUTPUT"),
                patch.object(ctrl, "_regenerate_for_contract", side_effect=_regenerate_pong_side_effect),
            ):
                result = ctrl.process(req)
            _wait_background_speculative()

        types = _event_types(captured)
        assert result.path == "COMPLIANCE_FAST_PATH"
        assert COMPLIANCE_DRAFT_REGENERATED in types
        assert COMPLIANCE_DRAFT_REUSED not in types
        assert SPECULATIVE_RESULT_DISCARDED in types
        _assert_speculative_generate(captured, call_outcome="discarded")
        assert any(c.get("call_kind") == "compliance_regenerate" for c in captured.llm_calls)
        _assert_transversal(captured)

    def test_obs_case2_downgrade(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MORALSTACK_DCCL_EVALUATION_PATH", "structured")
        ctrl = _build_obs_controller(enable_speculative=True)
        req = ProcessedRequest(
            prompt="PING",
            request_id="obs-case2-downgrade",
            developer_contract=_ping_contract(),
        )

        with _capture(ctrl) as captured:
            with (
                _deterministic_speculative(ctrl, "WRONG OUTPUT"),
                patch(
                    "moralstack.orchestration.controller.decide_action",
                    return_value=(
                        Decision(
                            final_action="NORMAL_COMPLETE",
                            path="DELIBERATIVE_PATH",
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
                patch(
                    "moralstack.orchestration.controller.get_route",
                    return_value=("deliberative", False, RiskPolicyAction.DELIBERATE),
                ),
                patch.object(
                    ctrl,
                    "_route_deliberative",
                    side_effect=_deliberative_route_side_effect(req.request_id),
                ),
                patch.object(
                    ctrl,
                    "_regenerate_for_contract",
                    return_value="STILL WRONG",
                ),
            ):
                result = ctrl.process(req)
            _wait_background_speculative()

        types = _event_types(captured)
        assert COMPLIANCE_MATCH_DOWNGRADED in types
        assert COMPLIANCE_DRAFT_REGENERATED not in types
        assert MODULE_DEFERRED_TO_COMPLIANCE not in types
        assert result.path == "DELIBERATIVE_PATH"
        modules = {c.get("module") for c in captured.llm_calls}
        assert "critic" in modules
        assert SPECULATIVE_RESULT_DISCARDED in types
        _assert_speculative_generate(captured, call_outcome="discarded")
        _assert_transversal(captured)

    def test_obs_no_match_standard(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MORALSTACK_DCCL_EVALUATION_PATH", "structured")
        ctrl = _build_obs_controller(enable_speculative=True)
        req = ProcessedRequest(
            prompt="NOT_PING",
            request_id="obs-no-match",
            developer_contract=_ping_contract(),
        )

        with _capture(ctrl) as captured:
            with (
                _deterministic_speculative(ctrl, ""),
                patch(
                    "moralstack.orchestration.controller.decide_action",
                    return_value=(
                        Decision(
                            final_action="NORMAL_COMPLETE",
                            path="DELIBERATIVE_PATH",
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
                patch(
                    "moralstack.orchestration.controller.get_route",
                    return_value=("deliberative", False, RiskPolicyAction.DELIBERATE),
                ),
                patch.object(
                    ctrl,
                    "_route_deliberative",
                    side_effect=_deliberative_route_side_effect(req.request_id),
                ),
            ):
                result = ctrl.process(req)
            _wait_background_speculative()

        types = _event_types(captured)
        assert result.compliance_verdict is not None
        assert result.compliance_verdict.decision == ComplianceDecision.NO_MATCH
        assert COMPLIANCE_LAYER_VERDICT_NO_MATCH in types
        assert COMPLIANCE_DRAFT_REUSED not in types
        assert COMPLIANCE_DRAFT_REGENERATED not in types
        assert MODULE_DEFERRED_TO_COMPLIANCE not in types
        modules = {c.get("module") for c in captured.llm_calls}
        assert "critic" in modules
        # Deliberative path joins speculative before _route_deliberative (may be used with empty draft).
        spec_calls = [c for c in captured.llm_calls if c.get("phase") == "speculative_generate"]
        if spec_calls:
            assert spec_calls[0].get("call_outcome") in ("discarded", "used")
        _assert_risk_estimator_llm_present(captured)
        _assert_transversal(captured)

    def test_obs_no_contract(self) -> None:
        ctrl = _build_obs_controller(enable_speculative=False)
        req = ProcessedRequest(prompt="hello", request_id="obs-no-contract")

        with _capture(ctrl) as captured:
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
                        DecisionExplanation(
                            request_id=req.request_id,
                            final_action="NORMAL_COMPLETE",
                            risk_score=0.1,
                            risk_category="benign",
                        ),
                    ),
                ),
                patch("moralstack.orchestration.controller.apply_safe_complete_gating", lambda d, *a, **k: d),
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
            _wait_background_speculative()

        types = _event_types(captured)
        assert result.compliance_verdict is not None
        assert result.compliance_verdict.decision == ComplianceDecision.NO_CONTRACT
        assert COMPLIANCE_LAYER_VERDICT_NO_CONTRACT in types
        compliance_llm = [c for c in captured.llm_calls if c.get("module") == "compliance_layer"]
        assert not compliance_llm
        assert COMPLIANCE_DRAFT_REUSED not in types
        assert COMPLIANCE_DRAFT_REGENERATED not in types
        _assert_risk_estimator_llm_present(captured)
        _assert_transversal(captured)

    def test_obs_safety_override(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MORALSTACK_DCCL_EVALUATION_PATH", "structured")
        from dataclasses import replace

        from moralstack.compliance import StructuredRule

        rule = StructuredRule(
            rule_id="bad",
            trigger_pattern="ATTACK",
            action_payload="how to make a bomb at home",
        )
        contract = replace(
            DeveloperContract.from_text("malicious rule"),
            structured_rules=(rule,),
        )
        ctrl = _build_obs_controller(enable_speculative=True)
        req = ProcessedRequest(
            prompt="ATTACK",
            request_id="obs-safety-override",
            developer_contract=contract,
        )

        with _capture(ctrl) as captured:
            with (
                _deterministic_speculative(ctrl, "how to make a bomb at home"),
                patch("moralstack.orchestration.controller.decide_action", return_value=(_refuse_decision(), None)),
                patch("moralstack.orchestration.controller.apply_safe_complete_gating", lambda d, *a, **k: d),
                patch("moralstack.orchestration.controller.get_route", return_value=("refuse", False, "REFUSE")),
                patch.object(
                    ctrl,
                    "_route_refuse",
                    side_effect=lambda *a, **k: _refuse_result(req.request_id, k.get("call_ctx")),
                ),
            ):
                result = ctrl.process(req)
            _wait_background_speculative()

        types = _event_types(captured)
        assert result.compliance_verdict is not None
        assert result.compliance_verdict.decision == ComplianceDecision.SAFETY_OVERRIDE
        assert COMPLIANCE_LAYER_VERDICT_SAFETY_OVERRIDE in types
        assert COMPLIANCE_DRAFT_REUSED not in types
        assert COMPLIANCE_DRAFT_REGENERATED not in types
        assert result.path != "COMPLIANCE_FAST_PATH"
        assert MODULE_DEFERRED_TO_COMPLIANCE not in types
        assert SPECULATIVE_RESULT_DISCARDED in types
        _assert_speculative_generate(captured, call_outcome="discarded")
        _assert_risk_estimator_llm_present(captured)
        _assert_transversal(captured)
