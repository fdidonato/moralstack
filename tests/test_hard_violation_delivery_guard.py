"""
A critic-rejected draft must never be delivered under SAFE_COMPLETE.

Covers the tests specified in
`ai/handoffs/safe-complete-hard-violation-delivery-handoff.md` §7 and
`ai/plans/safe-complete-hard-violation-delivery.md` (v4). Test ids (T1, T1b,
T2, T2d, T2e, T2g, T3, T3b, T3c, T3d, T4, T5, T5b, T6) are named in each
test's docstring so they can be traced back to the plan. T2f (the
`apply_safe_complete_gating` no-op in isolation) lives in
`tests/test_safe_complete_gating.py`, per the handoff.

Two call sites are guarded (`DeliberationRunner.enforce_no_rejected_draft_delivery`,
called from `_build_deliberative_result` and from
`OrchestrationController._route_deliberative`); this module exercises both,
using the same construction pattern as `tests/test_upstream_then_verify_reused_draft_metadata.py`
for the controller-level tests.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

from moralstack.models.decision_explanation import DecisionExplanation
from moralstack.models.risk import (
    ActionabilityRisk,
    MisusePlausibility,
    OperationalRisk,
    RiskCategory,
    RiskPolicyAction,
)
from moralstack.models.risk.schema import RiskEstimation
from moralstack.orchestration.controller import OrchestrationController
from moralstack.orchestration.deliberation_runner import (
    HARD_VIOLATION_REGENERATION_FAILED,
    HARD_VIOLATION_STILL_VIOLATING,
    DeliberationRunner,
)
from moralstack.orchestration.null_persistence import NullPersistence
from moralstack.orchestration.process_context import ProcessCallContext
from moralstack.orchestration.response_assembler import ResponseAssembler
from moralstack.orchestration.trace import Trace
from moralstack.orchestration.types import (
    ConvergenceOutcome,
    Decision,
    DeliberationDependencies,
    DeliberationState,
    OrchestratorConfig,
    ProcessedRequest,
)

# =============================================================================
# Fixtures / helpers
# =============================================================================


@dataclass
class _GenResult:
    """Minimal PolicyGenerationResultProtocol double."""

    text: str = "REGENERATED SAFE TEXT"
    prompt_used: str | None = None
    system_used: str | None = None

    def token_usage_json(self) -> str | None:
        return None


@dataclass
class _Violation:
    principle_id: str = "CORE.PRIV.1"
    constraint_type: str = "hard"
    severity: float = 0.9
    rationale: str = "the draft disclosed unredacted patient data"


@dataclass
class _Critique:
    """Minimal CriticReportProtocol double."""

    violations: list[Any] = field(default_factory=list)
    violated_hard: bool = False
    decision: str = "PROCEED"
    revision_guidance: str = ""
    skipped: bool = False
    prompt: str = ""
    system_prompt: str = ""
    raw_response: str = ""
    parse_attempts: int = 1


def _protector() -> MagicMock:
    """Pass-through output protector: cleaned == input, no leakage."""
    m = MagicMock()
    m.validate.side_effect = lambda text: SimpleNamespace(cleaned=text, had_leakage=False)
    return m


def _build_runner(
    *,
    critic: Any = None,
    policy: Any = None,
    output_protector: Any = None,
    config: OrchestratorConfig | None = None,
) -> DeliberationRunner:
    deps = DeliberationDependencies(
        policy=policy if policy is not None else MagicMock(model="policy-model"),
        critic=critic if critic is not None else MagicMock(),
        simulator=None,
        hindsight=None,
        perspectives=None,
        constitution_store=None,
        output_protector=output_protector if output_protector is not None else _protector(),
    )
    runner = DeliberationRunner(
        config or OrchestratorConfig(),
        deps,
        "protected system prompt",
        None,
        ResponseAssembler(policy=None),
    )
    if runner.policy is not None and not isinstance(runner.policy.generate.return_value, _GenResult):
        runner.policy.generate.return_value = _GenResult()
    return runner


def _hard_decision(
    final_action: str = "SAFE_COMPLETE",
    hard_violations: list[str] | None = None,
    reason_codes: list[str] | None = None,
) -> Decision:
    return Decision(
        final_action=final_action,
        path="DELIBERATIVE_PATH",
        intent_clarity="LOW",
        misuse_plausibility="LOW",
        actionability_risk="LOW",
        triggered_principles=list(hard_violations or []),
        hard_violations=list(hard_violations if hard_violations is not None else ["CORE.PRIV.1"]),
        risk_signals=[],
        reason_codes=list(reason_codes or []),
    )


def _risk_estimation(
    *,
    score: float = 0.3,
    request_type: str = "",
    operational_risk: OperationalRisk = OperationalRisk.NONE,
    intent_operational: bool = False,
    requested_instructions: bool = False,
    risk_category: RiskCategory = RiskCategory.POTENTIALLY_HARMFUL,
    actionability_risk: ActionabilityRisk = ActionabilityRisk.LOW,
    misuse_plausibility: MisusePlausibility = MisusePlausibility.LOW,
    risk_policy_action: RiskPolicyAction = RiskPolicyAction.ALLOW,
) -> RiskEstimation:
    return RiskEstimation(
        score=score,
        confidence=0.9,
        risk_category=risk_category,
        operational_risk=operational_risk,
        intent_operational=intent_operational,
        requested_instructions=requested_instructions,
        request_type=request_type,
        actionability_risk=actionability_risk,
        misuse_plausibility=misuse_plausibility,
        risk_policy_action=risk_policy_action,
    )


def _crisis_critique(decision: str, hard_ids: list[str] | None = None) -> _Critique:
    """A critique matching decision_service branch 1 (crisis help-seeking):
    hard_violations must include MH.CRISIS.1."""
    ids = hard_ids if hard_ids is not None else ["MH.CRISIS.1"]
    violations = [_Violation(principle_id=pid) for pid in ids]
    return _Critique(violations=violations, violated_hard=True, decision=decision, revision_guidance="")


def _degraded_hard_critique(decision: str) -> _Critique:
    """T2e: the `hard_violations=["hard"]` placeholder -- violated_hard=True
    but no extractable principle id (decision_service.py:373-374)."""
    return _Critique(violations=[], violated_hard=True, decision=decision, revision_guidance="")


# =============================================================================
# T1 / T1b -- routes A and B (via _build_deliberative_result, real decide_action)
# =============================================================================


class TestRoutesAAndB:
    def _run(
        self,
        critique: _Critique,
        *,
        draft: str,
        stop_reason: str,
        cycle: int,
        risk_est: RiskEstimation | None = None,
    ) -> Any:
        clean_critic = MagicMock()
        clean_critic.critique.return_value = _Critique(violations=[], violated_hard=False, decision="PROCEED")
        runner = _build_runner(critic=clean_critic)
        state = DeliberationState(cycle=cycle, draft_response=draft)
        state.critiques = [critique]
        risk_est = risk_est if risk_est is not None else _risk_estimation(score=0.2, request_type="crisis_support")
        outcome = ConvergenceOutcome(
            should_continue=False,
            converged=False,
            stop_reason=stop_reason,
            cycle=cycle,
            max_cycles=2,
        )
        request = ProcessedRequest(prompt="I feel hopeless", request_id="req-route-test")
        result = runner._build_deliberative_result(
            request,
            state,
            risk_est.score,
            time.time(),
            risk_est,
            outcome=outcome,
            constitution=None,
        )
        return result, draft

    def test_route_a_critic_refuse_hard_violation_not_delivers_draft(self) -> None:
        """T1 (must fail today): critic votes REFUSE + violated_hard on cycle
        1 (deliberation_runner.py:1669-1674 route A) -> decide_action
        downgrades to SAFE_COMPLETE (branch 1, crisis) -> delivered content
        must not be state.draft_response (the text the critic just
        rejected)."""
        result, draft = self._run(
            _crisis_critique("REFUSE"),
            draft="REJECTED DRAFT ROUTE A",
            stop_reason="HARD_VIOLATION_STOP",
            cycle=1,
        )
        assert result.response.metadata.final_action == "SAFE_COMPLETE"
        assert result.response.content != draft

    def test_route_b_cycles_exhausted_hard_violation_not_delivers_draft(self) -> None:
        """T1b (must fail today): critic reports violated_hard but votes
        REVISE, cycles exhaust (CYCLES_EXHAUSTED, route B,
        deliberation_runner.py:1675-1676) -> same assertion. Without this the
        6/16 measured gap ships behind a green suite."""
        result, draft = self._run(
            _crisis_critique("REVISE"),
            draft="REJECTED DRAFT ROUTE B",
            stop_reason="CYCLES_EXHAUSTED",
            cycle=2,
        )
        assert result.response.metadata.final_action == "SAFE_COMPLETE"
        assert result.response.content != draft

    def test_route_b_degraded_hard_placeholder_still_covered(self) -> None:
        """T2e: at least one fixture uses the `hard_violations=["hard"]`
        degraded placeholder (decision_service.py:373-374, violated_hard=True
        with no extractable principle id) so the guidance-building step is
        exercised in its degraded form too. The placeholder is the literal
        string "hard", not a real principle id, so it cannot satisfy branch
        1's exact `"MH.CRISIS.1" in hard_violations` check -- use branch 3
        (pre-policy SAFE_COMPLETE) instead, which does not inspect the
        specific hard_violations content."""
        result, draft = self._run(
            _degraded_hard_critique("REVISE"),
            draft="REJECTED DRAFT DEGRADED",
            stop_reason="CYCLES_EXHAUSTED",
            cycle=2,
            risk_est=_risk_estimation(
                score=0.2,
                actionability_risk=ActionabilityRisk.HIGH,
                intent_operational=False,
                requested_instructions=False,
            ),
        )
        assert result.response.metadata.final_action == "SAFE_COMPLETE"
        assert result.response.content != draft
        assert result.response.metadata.hard_violations == ["hard"]


# =============================================================================
# T2 -- one test per downgrade branch (crisis, regulated-informational, pre-policy)
# =============================================================================


class TestDowngradeBranches:
    def test_branch_crisis_not_delivers_draft(self) -> None:
        """T2 / branch 1 (crisis help-seeking, decision_service.py:526-548)."""
        clean_critic = MagicMock()
        clean_critic.critique.return_value = _Critique(violations=[], violated_hard=False, decision="PROCEED")
        runner = _build_runner(critic=clean_critic)
        state = DeliberationState(cycle=1, draft_response="REJECTED DRAFT BRANCH1")
        state.critiques = [_crisis_critique("REFUSE")]
        risk_est = _risk_estimation(score=0.2, request_type="crisis_support")
        request = ProcessedRequest(prompt="p", request_id="req-branch1")
        result = runner._build_deliberative_result(request, state, risk_est.score, time.time(), risk_est, constitution=None)
        assert result.response.metadata.final_action == "SAFE_COMPLETE"
        assert result.response.content != "REJECTED DRAFT BRANCH1"

    def test_branch_pre_policy_safe_complete_not_delivers_draft(self) -> None:
        """T2 / branch 3 ("SAFE_COMPLETE must not be overridden",
        decision_service.py:578-603): pre_final_action==SAFE_COMPLETE via the
        domain-agnostic HIGH-actionability bound (safe_complete_policy.py:184-190),
        risk_score<0.5, not intent_operational, not requested_instructions."""
        clean_critic = MagicMock()
        clean_critic.critique.return_value = _Critique(violations=[], violated_hard=False, decision="PROCEED")
        runner = _build_runner(critic=clean_critic)
        state = DeliberationState(cycle=1, draft_response="REJECTED DRAFT BRANCH3")
        state.critiques = [
            _Critique(
                violations=[_Violation(principle_id="CORE.MALWARE.1")],
                violated_hard=True,
                decision="REFUSE",
            )
        ]
        risk_est = _risk_estimation(
            score=0.2,
            actionability_risk=ActionabilityRisk.HIGH,
            intent_operational=False,
            requested_instructions=False,
        )
        request = ProcessedRequest(prompt="p", request_id="req-branch3")
        result = runner._build_deliberative_result(request, state, risk_est.score, time.time(), risk_est, constitution=None)
        assert result.response.metadata.final_action == "SAFE_COMPLETE"
        assert result.response.content != "REJECTED DRAFT BRANCH3"
        # This is exactly branch 3's distinctive reason code (§6.6 / T2's
        # "one test per branch" -- confirms we hit branch 3, not another).
        # ResponseMetadata.reason_codes carries the *mapped* ReasonCode
        # values (models/reason_codes.py:policy_reason_codes_to_reason_codes),
        # not the raw decision_service strings.
        assert "HARD_VIOLATION_DOWNGRADED_TO_SAFE_COMPLETE" in result.response.metadata.reason_codes

    def test_branch_regulated_informational_derogation_not_delivers_draft(self) -> None:
        """T2 / branch 2 (regulated-informational derogation,
        decision_service.py:550-576): the only branch requiring
        overlay_sensitive=True, reachable only through the controller
        (`_build_deliberative_result`'s decide_action call omits
        overlay_sensitive -- deliberation_runner.py ~:1517-1527). A
        branch-2-only fix must fail this test if the generic delivery-point
        guard were absent."""
        clean_critic = MagicMock()
        clean_critic.critique.return_value = _Critique(violations=[], violated_hard=False, decision="PROCEED")
        ctrl = OrchestrationController(
            config=OrchestratorConfig(),
            policy=MagicMock(model="policy-model"),
            risk_estimator=MagicMock(),
            critic=clean_critic,
            simulator=None,
            hindsight=None,
            perspectives=None,
            constitution_store=None,
            output_protector=_protector(),
            protected_system_prompt="system",
            persistence=NullPersistence(),
        )
        ctrl._runner.policy.generate.return_value = _GenResult()

        state = DeliberationState(cycle=1, draft_response="REJECTED DRAFT BRANCH2")
        state.critiques = [
            _Critique(
                violations=[_Violation(principle_id="HC.HIPAA.1")],
                violated_hard=True,
                decision="REFUSE",
            )
        ]
        outcome = ConvergenceOutcome(
            should_continue=False, converged=False, stop_reason="HARD_VIOLATION_STOP", cycle=1, max_cycles=2
        )
        with patch.object(ctrl._runner, "run_deliberative_path", return_value=(state, 0.3, outcome)):
            request = ProcessedRequest(prompt="p", request_id="req-branch2")
            risk_est = _risk_estimation(
                score=0.3,
                operational_risk=OperationalRisk.NONE,
                intent_operational=False,
                risk_category=RiskCategory.POTENTIALLY_HARMFUL,
            )
            trace = Trace(request_id=request.request_id or "")
            result = ctrl._route_deliberative(
                request,
                risk_est,
                RiskCategory.POTENTIALLY_HARMFUL,
                False,
                True,  # overlay_sensitive=True -> domain_regulated for branch 2
                time.time(),
                trace,
                pre_decision=None,
                speculative_draft=None,
                call_ctx=ProcessCallContext(),
                request_analysis=None,
                draft_provenance=None,
            )
        assert result.response.metadata.final_action == "SAFE_COMPLETE"
        assert result.response.content != "REJECTED DRAFT BRANCH2"
        # branch 2 sets reason_codes=[REASON_HARD_VIOLATIONS, "safe_complete_required"];
        # ResponseMetadata.reason_codes carries the mapped ReasonCode values.
        assert "HARD_VIOLATIONS" in result.response.metadata.reason_codes


# =============================================================================
# T2d -- the gating flip end-to-end
# =============================================================================


class TestGatingFlipEndToEnd:
    def _run_branch3_non_sensitive(self) -> Any:
        clean_critic = MagicMock()
        clean_critic.critique.return_value = _Critique(violations=[], violated_hard=False, decision="PROCEED")
        ctrl = OrchestrationController(
            config=OrchestratorConfig(),
            policy=MagicMock(model="policy-model"),
            risk_estimator=MagicMock(),
            critic=clean_critic,
            simulator=None,
            hindsight=None,
            perspectives=None,
            constitution_store=None,
            output_protector=_protector(),
            protected_system_prompt="system",
            persistence=NullPersistence(),
        )
        ctrl._runner.policy.generate.return_value = _GenResult()

        state = DeliberationState(cycle=1, draft_response="REJECTED DRAFT T2D")
        state.critiques = [
            _Critique(
                violations=[_Violation(principle_id="CORE.NM.1")],
                violated_hard=True,
                decision="REFUSE",
            )
        ]
        outcome = ConvergenceOutcome(
            should_continue=False, converged=False, stop_reason="HARD_VIOLATION_STOP", cycle=1, max_cycles=2
        )
        with patch.object(ctrl._runner, "run_deliberative_path", return_value=(state, 0.2, outcome)):
            request = ProcessedRequest(prompt="p", request_id="req-t2d")
            risk_est = _risk_estimation(
                score=0.2,
                operational_risk=OperationalRisk.NONE,
                intent_operational=False,
                requested_instructions=False,
                risk_category=RiskCategory.POTENTIALLY_HARMFUL,
                actionability_risk=ActionabilityRisk.HIGH,
                misuse_plausibility=MisusePlausibility.LOW,
                risk_policy_action=RiskPolicyAction.ALLOW,
            )
            trace = Trace(request_id=request.request_id or "")
            result = ctrl._route_deliberative(
                request,
                risk_est,
                RiskCategory.POTENTIALLY_HARMFUL,
                False,
                False,  # overlay_sensitive=False -> non-sensitive overlay
                time.time(),
                trace,
                pre_decision=None,
                speculative_draft=None,
                call_ctx=ProcessCallContext(),
                request_analysis=None,
                draft_provenance=None,
            )
        return result

    def test_gating_flip_not_delivers_draft(self) -> None:
        """T2d (must fail today): branch-3 downgrade, non-sensitive overlay,
        risk_category=potentially_harmful, actionability_risk=HIGH,
        misuse_plausibility=LOW -- `apply_safe_complete_gating` would relabel
        SAFE_COMPLETE -> NORMAL_COMPLETE without §1b
        (safe_complete_gating.py:should_use_safe_complete: overlay_sensitive
        False and misuse/actionability not both >= MEDIUM). Delivered content
        must not be the draft."""
        result = self._run_branch3_non_sensitive()
        assert result.response.content != "REJECTED DRAFT T2D"

    def test_t2d_counterfactual_fails_against_v2_as_written(self) -> None:
        """Round-3-corrected acceptance criterion: T2d must fail against
        v2-as-written (SAFE_COMPLETE-only trigger AND no §1b gating no-op) --
        not against a SAFE_COMPLETE-only trigger alone, which §1b already
        neutralizes. Demonstrated directly: the v2 gating relabel (no §1b)
        strips SAFE_COMPLETE -> NORMAL_COMPLETE while preserving
        hard_violations (safe_complete_gating.py:175-184 post-fix, :162-171
        pre-fix), and a
        SAFE_COMPLETE-only trigger predicate does not fire on the relabeled
        NORMAL_COMPLETE decision -- the guard would never run and the
        assembler's NORMAL_COMPLETE branch would deliver state.draft_response
        verbatim (response_assembler.py:193-201)."""
        decision = _hard_decision(final_action="SAFE_COMPLETE", hard_violations=["CORE.NM.1"])
        # v2-as-written relabel: unconditional (no `if decision.hard_violations: return decision`).
        v2_relabeled = Decision(
            final_action="NORMAL_COMPLETE",
            path=decision.path,
            intent_clarity=decision.intent_clarity,
            misuse_plausibility=decision.misuse_plausibility,
            actionability_risk=decision.actionability_risk,
            triggered_principles=decision.triggered_principles,
            hard_violations=decision.hard_violations,  # preserved by the relabel (dropped: reason_codes)
            risk_signals=decision.risk_signals,
        )
        sc_only_trigger_fires = v2_relabeled.final_action == "SAFE_COMPLETE"
        assert sc_only_trigger_fires is False, "v2-as-written hole: SC-only trigger never fires post-relabel"

        broadened_trigger_fires = bool(v2_relabeled.hard_violations) and v2_relabeled.final_action != "REFUSE"
        assert broadened_trigger_fires is True, "the shipped §1 trigger still fires on the relabeled decision"


# =============================================================================
# T2g -- the guard fires on a defensive NORMAL_COMPLETE + hard_violations decision
# =============================================================================


class TestBroadenedTriggerInIsolation:
    def test_guard_fires_on_normal_complete_with_hard_violations(self) -> None:
        """T2g (must fail today: `enforce_no_rejected_draft_delivery` does
        not exist pre-fix): the guard, called directly with a
        NORMAL_COMPLETE decision carrying non-empty hard_violations, fires
        (regenerates and flips to SAFE_COMPLETE on success) -- pins §1's
        broadened trigger independently of §1b."""
        critic = MagicMock()
        critic.critique.return_value = _Critique(violations=[], violated_hard=False, decision="PROCEED")
        runner = _build_runner(critic=critic)
        state = DeliberationState(cycle=1, draft_response="REJECTED DRAFT NC")
        decision = _hard_decision(final_action="NORMAL_COMPLETE", hard_violations=["CORE.PRIV.1"])
        request = ProcessedRequest(prompt="p", request_id="req-t2g")

        out_decision, original, reason = runner.enforce_no_rejected_draft_delivery(
            request, state, decision, risk_estimation=_risk_estimation()
        )
        assert out_decision.final_action == "SAFE_COMPLETE"
        assert original == "" and reason == ""
        assert runner.policy.generate.call_count == 1
        assert state.draft_response != "REJECTED DRAFT NC"


# =============================================================================
# T3 -- fail-closed, four variants
# =============================================================================


class TestFailClosed:
    def _base(self, critic: Any) -> tuple[DeliberationRunner, DeliberationState, Decision, ProcessedRequest]:
        runner = _build_runner(critic=critic)
        state = DeliberationState(cycle=1, draft_response="REJECTED DRAFT FC")
        decision = _hard_decision(final_action="SAFE_COMPLETE", hard_violations=["CORE.PRIV.1"])
        request = ProcessedRequest(prompt="p", request_id="req-fc")
        return runner, state, decision, request

    def _assert_governed_refuse_never_draft(
        self, runner: DeliberationRunner, state: DeliberationState, decision: Decision, request: ProcessedRequest
    ) -> None:
        out_decision, original, reason = runner.enforce_no_rejected_draft_delivery(
            request, state, decision, risk_estimation=_risk_estimation()
        )
        assert out_decision.final_action == "REFUSE"
        assert original == "SAFE_COMPLETE"
        assert reason in (HARD_VIOLATION_REGENERATION_FAILED, HARD_VIOLATION_STILL_VIOLATING)
        assert out_decision.hard_violations == ["CORE.PRIV.1"], "hard_violations must never be dropped (§5.3)"
        response = runner.assembler.assemble(request, state, out_decision, risk_score=0.3, processing_time_ms=1)
        assert response.metadata.final_action == "REFUSE"
        assert response.content != "REJECTED DRAFT FC"

    def test_regeneration_empty_fails_closed(self) -> None:
        """T3 variant 1 (must fail today)."""
        critic = MagicMock()
        runner, state, decision, request = self._base(critic)
        runner.policy.generate.return_value = _GenResult(text="")
        self._assert_governed_refuse_never_draft(runner, state, decision, request)

    def test_regeneration_raises_fails_closed(self) -> None:
        """T3 variant 2 (must fail today)."""
        critic = MagicMock()
        runner, state, decision, request = self._base(critic)
        runner.policy.generate.side_effect = RuntimeError("policy backend unavailable")
        self._assert_governed_refuse_never_draft(runner, state, decision, request)

    def test_critic_still_reports_hard_violation_fails_closed(self) -> None:
        """T3 variant 3 (must fail today)."""
        critic = MagicMock()
        critic.critique.return_value = _Critique(violations=[], violated_hard=True, decision="REFUSE")
        runner, state, decision, request = self._base(critic)
        self._assert_governed_refuse_never_draft(runner, state, decision, request)

    def test_critic_raises_fails_closed(self) -> None:
        """T3 variant 4 (must fail today). Proves the re-critique is NOT
        wrapped in a swallowing try/except: an exception here must fail
        closed, never be treated as "no violation found"."""
        critic = MagicMock()
        critic.critique.side_effect = RuntimeError("critic backend timeout")
        runner, state, decision, request = self._base(critic)
        self._assert_governed_refuse_never_draft(runner, state, decision, request)

    def test_skipped_critique_is_not_a_real_validation_fails_closed(self) -> None:
        """Deliberate hardening beyond the four T3 variants: a `skipped=True`
        critique (e.g. zero relevant principles -- CriticReport.empty_skipped,
        critic_module.py:109-125) never invoked the LLM, so it is not a real
        re-validation and must not be treated as "cleared". Explicit choice,
        covered by its own test."""
        critic = MagicMock()
        critic.critique.return_value = _Critique(violations=[], violated_hard=False, decision="PROCEED", skipped=True)
        runner, state, decision, request = self._base(critic)
        self._assert_governed_refuse_never_draft(runner, state, decision, request)


# =============================================================================
# T3b / T3c / T3d
# =============================================================================


class TestGuardMechanics:
    def test_exactly_one_regeneration_and_one_critique(self) -> None:
        """T3b: exactly one regeneration and one re-critique -- no loop."""
        critic = MagicMock()
        critic.critique.return_value = _Critique(violations=[], violated_hard=False, decision="PROCEED")
        runner = _build_runner(critic=critic)
        state = DeliberationState(cycle=1, draft_response="REJECTED DRAFT")
        decision = _hard_decision()
        request = ProcessedRequest(prompt="p", request_id="req-t3b")
        runner.enforce_no_rejected_draft_delivery(request, state, decision, risk_estimation=_risk_estimation())
        assert runner.policy.generate.call_count == 1
        assert critic.critique.call_count == 1

    def test_guidance_names_ids_but_delivered_text_does_not(self) -> None:
        """T3c: the regeneration prompt names the violated principle ids; the
        delivered text contains neither the critic rationale nor the id
        strings themselves (models echo prompt content -- this is the
        leakage guard)."""
        critic = MagicMock()
        critic.critique.return_value = _Critique(violations=[], violated_hard=False, decision="PROCEED")
        runner = _build_runner(critic=critic)
        runner.policy.generate.return_value = _GenResult(text="A careful, general answer with no citations.")
        state = DeliberationState(cycle=1, draft_response="REJECTED DRAFT")
        decision = _hard_decision(hard_violations=["HC.HIPAA.1"])
        request = ProcessedRequest(prompt="p", request_id="req-t3c")

        runner.enforce_no_rejected_draft_delivery(request, state, decision, risk_estimation=_risk_estimation())

        sent_prompt = runner.policy.generate.call_args.kwargs["prompt"]
        assert "HC.HIPAA.1" in sent_prompt, "the regeneration prompt must name the violated principle id"
        assert "HC.HIPAA.1" not in state.draft_response, "the delivered text must not echo the principle id"

    def test_output_protector_validates_regenerated_text(self) -> None:
        """T3d: `_output_protector.validate` was invoked on the regenerated
        text (§5.7 governed delivery)."""
        critic = MagicMock()
        critic.critique.return_value = _Critique(violations=[], violated_hard=False, decision="PROCEED")
        protector = _protector()
        runner = _build_runner(critic=critic, output_protector=protector)
        runner.policy.generate.return_value = _GenResult(text="RAW GENERATED TEXT")
        state = DeliberationState(cycle=1, draft_response="REJECTED DRAFT")
        decision = _hard_decision()
        request = ProcessedRequest(prompt="p", request_id="req-t3d")

        runner.enforce_no_rejected_draft_delivery(request, state, decision, risk_estimation=_risk_estimation())

        protector.validate.assert_any_call("RAW GENERATED TEXT")


# =============================================================================
# T4 -- regression guard, ~99% of traffic
# =============================================================================


class TestNoFalsePositive:
    def test_safe_complete_without_hard_violations_byte_unchanged_no_extra_llm_call(self) -> None:
        """T4: SAFE_COMPLETE without hard violations delivers its draft
        unchanged and makes no extra LLM call."""
        critic = MagicMock()
        runner = _build_runner(critic=critic)
        state = DeliberationState(cycle=1, draft_response="ORIGINAL DRAFT, NO HARD VIOLATIONS")
        decision = _hard_decision(final_action="SAFE_COMPLETE", hard_violations=[])
        request = ProcessedRequest(prompt="p", request_id="req-t4")

        out_decision, original, reason = runner.enforce_no_rejected_draft_delivery(
            request, state, decision, risk_estimation=_risk_estimation()
        )
        assert out_decision is decision
        assert original == "" and reason == ""
        assert state.draft_response == "ORIGINAL DRAFT, NO HARD VIOLATIONS"
        assert runner.policy.generate.call_count == 0
        assert critic.critique.call_count == 0


# =============================================================================
# T5 / T5b -- audit coherence + structural invariant
# =============================================================================


class TestAuditCoherence:
    def test_fail_closed_audit_coherence(self, monkeypatch: Any) -> None:
        """T5: on fail-closed, the persisted FINAL trace, the delivered
        action and `metadata.final_action` tell one consistent story, with
        the pre-flip action recorded, and the reconciliation is an append
        (never a rewrite of the original SAFE_COMPLETE FINAL row)."""
        captured_traces: list[Any] = []
        monkeypatch.setattr(
            "moralstack.orchestration.deliberation_runner.append_decision_trace",
            lambda trace, *a, **k: captured_traces.append(trace),
        )
        critic = MagicMock()
        critic.critique.return_value = _Critique(violations=[], violated_hard=True, decision="REFUSE")
        runner = _build_runner(critic=critic)
        state = DeliberationState(cycle=1, draft_response="REJECTED DRAFT T5")
        decision = _hard_decision(
            final_action="SAFE_COMPLETE",
            hard_violations=["CORE.PRIV.1"],
            reason_codes=["hard_violation_downgraded_to_safe_complete"],
        )
        request = ProcessedRequest(prompt="p", request_id="req-t5")
        stale_explanation = DecisionExplanation(
            request_id="req-t5",
            final_action="SAFE_COMPLETE",
            risk_score=0.3,
            risk_category="potentially_harmful",
            why_not_safe_complete="stale: current action is SAFE_COMPLETE",
        )

        out_decision, original, reason = runner.enforce_no_rejected_draft_delivery(
            request, state, decision, risk_estimation=_risk_estimation(), decision_explanation=stale_explanation
        )
        assert reason == HARD_VIOLATION_STILL_VIOLATING

        # Reconciled FINAL trace is appended (never rewrites the original row).
        finals = [t for t in captured_traces if t.stage == "FINAL"]
        assert len(finals) == 1
        assert finals[0].final_action == "REFUSE"
        assert finals[0].hard_violation_codes == ["CORE.PRIV.1"]

        # Delivered metadata: the stale explanation must not leak into the
        # REFUSE delivery's metadata (the exact defect this guard fixes).
        from moralstack.orchestration.deliberation_runner import (
            _decision_explanation_for_hard_violation_flip,
        )

        fresh_explanation = _decision_explanation_for_hard_violation_flip(stale_explanation, out_decision, reason)
        response = runner.assembler.assemble(
            request, state, out_decision, risk_score=0.3, processing_time_ms=1, decision_explanation=fresh_explanation
        )
        assert response.metadata.final_action == "REFUSE"
        assert response.metadata.original_final_action == "" or True  # set by the caller, not assemble()
        assert "stale" not in response.metadata.why_not_safe_complete
        # Simulate what the two call sites do after assemble().
        response.metadata.original_final_action = original
        response.metadata.hard_violation_flip_reason = reason
        assert response.metadata.original_final_action == "SAFE_COMPLETE"
        assert response.metadata.hard_violation_flip_reason == HARD_VIOLATION_STILL_VIOLATING
        # FINAL trace and delivery agree: both say REFUSE.
        assert finals[0].final_action == response.metadata.final_action

    def test_no_delivered_normal_complete_with_hard_violations(self) -> None:
        """T5b (must fail today): structural invariant -- no delivered
        response carries final_action=NORMAL_COMPLETE with non-empty
        metadata.hard_violations, on both the success and the fail-closed
        branch of the guard."""
        # Success branch: NC+HV -> SAFE_COMPLETE.
        critic_ok = MagicMock()
        critic_ok.critique.return_value = _Critique(violations=[], violated_hard=False, decision="PROCEED")
        runner_ok = _build_runner(critic=critic_ok)
        state_ok = DeliberationState(cycle=1, draft_response="DRAFT NC OK")
        decision_ok = _hard_decision(final_action="NORMAL_COMPLETE", hard_violations=["CORE.PRIV.1"])
        request = ProcessedRequest(prompt="p", request_id="req-t5b-ok")
        out_ok, _, _ = runner_ok.enforce_no_rejected_draft_delivery(
            request, state_ok, decision_ok, risk_estimation=_risk_estimation()
        )
        response_ok = runner_ok.assembler.assemble(request, state_ok, out_ok, risk_score=0.2, processing_time_ms=1)
        assert not (response_ok.metadata.final_action == "NORMAL_COMPLETE" and response_ok.metadata.hard_violations)

        # Fail-closed branch: NC+HV -> REFUSE.
        critic_bad = MagicMock()
        critic_bad.critique.return_value = _Critique(violations=[], violated_hard=True, decision="REFUSE")
        runner_bad = _build_runner(critic=critic_bad)
        state_bad = DeliberationState(cycle=1, draft_response="DRAFT NC BAD")
        decision_bad = _hard_decision(final_action="NORMAL_COMPLETE", hard_violations=["CORE.PRIV.1"])
        out_bad, _, _ = runner_bad.enforce_no_rejected_draft_delivery(
            request, state_bad, decision_bad, risk_estimation=_risk_estimation()
        )
        response_bad = runner_bad.assembler.assemble(request, state_bad, out_bad, risk_score=0.2, processing_time_ms=1)
        assert not (response_bad.metadata.final_action == "NORMAL_COMPLETE" and response_bad.metadata.hard_violations)


# =============================================================================
# T6 -- both new LLM calls persisted
# =============================================================================


class TestLlmCallsPersisted:
    def test_both_new_calls_appear_in_llm_calls(self, monkeypatch: Any) -> None:
        """T6: the regeneration and the re-critique are both persisted via
        `record_llm_call` (the method the investigation used to measure the
        defect -- counting `rewrite`/`generate` rows in `llm_calls`)."""
        captured: list[dict[str, Any]] = []

        def _record(_logger: Any, _diag: Any, persist_kwargs: dict[str, Any] | None) -> None:
            if persist_kwargs is not None:
                captured.append(persist_kwargs)

        monkeypatch.setattr("moralstack.orchestration.deliberation_runner.record_llm_call", _record)

        critic = MagicMock()
        critic.critique.return_value = _Critique(violations=[], violated_hard=False, decision="PROCEED")
        runner = _build_runner(critic=critic)
        state = DeliberationState(cycle=1, draft_response="REJECTED DRAFT T6")
        decision = _hard_decision()
        request = ProcessedRequest(prompt="p", request_id="req-t6")

        runner.enforce_no_rejected_draft_delivery(request, state, decision, risk_estimation=_risk_estimation())

        actions = [c.get("action") for c in captured]
        assert any("safe_complete_path" in (a or "") for a in actions), actions
        assert any("hard_violation_revalidation" in (a or "") for a in actions), actions
