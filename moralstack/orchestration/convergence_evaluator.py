"""
Convergence evaluation for deliberative cycles.
Determines whether the process has converged and which DecisionType to apply.
Invariants and structured logging remain in convergence.py.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from moralstack.models.risk.categories import OperationalRisk, RiskCategory, RiskPolicyAction
from moralstack.orchestration.types import (
    DecisionType,
    DeliberationState,
    OrchestratorConfig,
    PerspectiveResultProtocol,
    RiskEstimationProtocol,
)

_LOG = logging.getLogger(__name__)


def _calculate_weighted_approval(perspectives: list[PerspectiveResultProtocol]) -> float:
    if not perspectives:
        return 0.0
    weights_by_id: dict[str, float] = {
        "user": 1.0,
        "vulnerable": 1.2,
        "observer": 1.0,
        "adversary": 0.8,
        "compliance": 1.1,
    }
    total_weight = 0.0
    weighted_sum = 0.0
    for p in perspectives:
        score = getattr(p, "approval_score", 0.5)
        pid = getattr(p, "perspective_id", "") or ""
        weight = weights_by_id.get(pid, 1.0)
        weighted_sum += score * weight
        total_weight += weight
    return weighted_sum / total_weight if total_weight > 0 else 0.0


# Public alias for modules that aggregate perspective signals (e.g. guidance_builder).
calculate_weighted_approval = _calculate_weighted_approval


def _check_simulator_acceptable(state: DeliberationState) -> bool:
    if not state.simulations:
        return True
    last_sim = state.simulations[-1]
    if not last_sim:
        return True
    current_valence = last_sim.expected_valence
    if current_valence >= 0:
        return True
    if len(state.simulations) >= 2:
        prev_valence = state.simulations[-2].expected_valence
        if current_valence > prev_valence + 0.1:
            return True
    if current_valence > -0.3:
        return True
    return False


def _has_pending_soft_suggestions(state: DeliberationState, config: OrchestratorConfig) -> bool:
    """True if there are actionable soft suggestions that haven't been incorporated yet."""
    if not config.enable_soft_revision:
        return False
    # Skip if weighted approval is very high (suggestions are marginal)
    if state.perspectives:
        avg_approval = _calculate_weighted_approval(state.perspectives)
        if avg_approval >= config.soft_revision_max_approval:
            return False
    suggestion_count = 0
    if state.perspectives:
        for p in state.perspectives:
            suggestion_count += len(getattr(p, "suggestions", []) or [])
            suggestion_count += len(getattr(p, "concerns", []) or [])
    if state.hindsight:
        suggestion_count += len(getattr(state.hindsight, "suggestions", []) or [])
        if getattr(state.hindsight, "feedback", None):
            suggestion_count += 1
    return suggestion_count >= config.soft_revision_min_suggestions


def _all_modules_satisfied(state: DeliberationState) -> bool:
    if state.last_critique:
        if state.last_critique and state.last_critique.violations:
            return False
        if state.last_critique and (state.last_critique.revision_guidance or "").strip():
            return False
    if state.perspectives:
        for p in state.perspectives:
            if getattr(p, "approval_score", 1.0) < 0.7:
                return False
            if getattr(p, "concerns", None):
                return False
    if state.hindsight:
        rec = getattr(state.hindsight, "recommendation", None) or (
            getattr(state.hindsight, "aggregated", None) and getattr(state.hindsight.aggregated, "recommendation", None)
        )
        if rec is not None:
            rec_str = str(rec).lower() if not hasattr(rec, "value") else getattr(rec, "value", str(rec)).lower()
            if rec_str in ("refuse", "revise", "reject"):
                return False
        if getattr(state.hindsight, "feedback", None):
            return False
        if hasattr(state.hindsight, "suggestions") and state.hindsight.suggestions:
            return False
    if state.simulations:
        for sim in state.simulations:
            if sim.expected_valence < 0:
                return False
    return True


def _risk_posture_blocks_cycle1_early_stop(
    risk_estimation: RiskEstimationProtocol | None,
    config: OrchestratorConfig,
) -> tuple[bool, str]:
    # When risk is omitted (e.g. isolated unit tests), do not block on posture.
    if risk_estimation is None:
        return False, ""
    if bool(getattr(risk_estimation, "intent_to_harm", False)):
        return True, "HIGH_RISK_REQUIRE_SECOND_CYCLE"
    op = risk_estimation.operational_risk
    op_val = str(getattr(op, "value", op) or "").strip().upper()
    if op_val == OperationalRisk.HIGH.value:
        return True, "HIGH_RISK_REQUIRE_SECOND_CYCLE"
    rpa = risk_estimation.risk_policy_action
    rpa_val = str(getattr(rpa, "value", rpa) or "").strip().upper()
    if rpa_val == RiskPolicyAction.DENY.value:
        return True, "HIGH_RISK_REQUIRE_SECOND_CYCLE"
    rc = risk_estimation.risk_category
    rc_enum = rc if isinstance(rc, RiskCategory) else None
    rc_val = str(getattr(rc, "value", rc) or "").strip().lower()
    if rc_enum in (RiskCategory.POTENTIALLY_HARMFUL, RiskCategory.CLEARLY_HARMFUL):
        return True, "HIGH_RISK_REQUIRE_SECOND_CYCLE"
    if rc_enum is None and rc_val in ("potentially_harmful", "clearly_harmful"):
        return True, "HIGH_RISK_REQUIRE_SECOND_CYCLE"
    if float(risk_estimation.score) >= config.risk_thresholds.medium:
        return True, "HIGH_RISK_REQUIRE_SECOND_CYCLE"
    if bool(getattr(risk_estimation, "requested_instructions", False)) and (
        rc_enum in (RiskCategory.SENSITIVE, RiskCategory.POTENTIALLY_HARMFUL, RiskCategory.CLEARLY_HARMFUL)
        or rc_val in ("sensitive", "potentially_harmful", "clearly_harmful")
    ):
        return True, "HIGH_RISK_REQUIRE_SECOND_CYCLE"
    return False, ""


@dataclass
class Cycle1EarlyConvergenceResult:
    """Conservative cycle-1 early stop evaluation (observability + policy)."""

    accepted: bool
    reason_codes: list[str] = field(default_factory=list)
    evidence_summary: dict[str, object] = field(default_factory=dict)


def _cycle1_early_reject(reason_codes: list[str], evidence: dict[str, object] | None = None) -> Cycle1EarlyConvergenceResult:
    ev: dict[str, object] = dict(evidence or {})
    _LOG.debug("cycle1_early_convergence rejected: %s evidence=%s", reason_codes, ev)
    return Cycle1EarlyConvergenceResult(False, reason_codes, ev)


def _evaluate_cycle1_early_convergence(
    state: DeliberationState,
    config: OrchestratorConfig,
    risk_estimation: RiskEstimationProtocol | None,
) -> Cycle1EarlyConvergenceResult:
    """
    Conservative early convergence for cycle 1 only. Default: reject.
    Uses only signals already present on DeliberationState (no extra LLM work).
    """
    positive: list[str] = []
    if state.cycle != 1:
        return _cycle1_early_reject(["NOT_CYCLE_ONE"], {})

    lc = state.last_critique
    if lc is None:
        return _cycle1_early_reject(["INSUFFICIENT_EVIDENCE"], {"critic": "missing"})
    if bool(getattr(lc, "violated_hard", False)):
        return _cycle1_early_reject(["HARD_VIOLATION_PRESENT"], {})
    if state.has_critical_violations:
        return _cycle1_early_reject(["CRITICAL_VIOLATION_PRESENT"], {})
    viol = getattr(lc, "violations", None) or []
    if len(viol) > 0:
        return _cycle1_early_reject(["VIOLATIONS_PRESENT"], {"violations_count": len(viol)})
    dec = (getattr(lc, "decision", "") or "").strip().upper()
    if dec == "REFUSE":
        return _cycle1_early_reject(["CRITIC_REFUSE_POSTURE"], {})
    # guidance is not blocking when critic decision is PROCEED and no violations
    # (guidance in that context is soft/cosmetic, handled by soft_revision if enabled)
    if dec not in ("PROCEED", "") and (getattr(lc, "revision_guidance", "") or "").strip():
        return _cycle1_early_reject(["CRITIC_REVISION_GUIDANCE_PRESENT"], {})

    block, code = _risk_posture_blocks_cycle1_early_stop(risk_estimation, config)
    if block:
        return _cycle1_early_reject([code], {"risk_block": True})

    if not config.enable_perspectives or not state.perspectives:
        return _cycle1_early_reject(["PERSPECTIVES_NOT_STRONG_ENOUGH"], {"perspectives": "missing"})

    w_ap = _calculate_weighted_approval(state.perspectives)
    min_w = float(config.cycle1_early_convergence_min_weighted_approval)
    if w_ap < min_w:
        return _cycle1_early_reject(
            ["PERSPECTIVES_NOT_STRONG_ENOUGH"],
            {"weighted_approval": w_ap, "min_weighted_approval": min_w},
        )
    min_p = float(config.cycle1_early_convergence_min_per_perspective_approval)
    for p in state.perspectives:
        ap = float(getattr(p, "approval_score", 0.0) or 0.0)
        if ap < min_p:
            return _cycle1_early_reject(
                ["PERSPECTIVES_NOT_STRONG_ENOUGH"],
                {"min_perspective_approval": ap, "min_required": min_p},
            )
        concerns = getattr(p, "concerns", None) or []
        if len(concerns) > 0:
            return _cycle1_early_reject(["PERSPECTIVE_CONCERNS_PRESENT"], {})

    _persp_agg_obj = state._perspectives_aggregation
    if _persp_agg_obj is not None:
        _inner_agg = getattr(_persp_agg_obj, "aggregation", _persp_agg_obj)
        persp_rec = getattr(_inner_agg, "recommendation", None)
        if persp_rec is not None:
            pr = str(getattr(persp_rec, "value", persp_rec)).strip().lower()
            if "refuse" in pr:
                return _cycle1_early_reject(["PERSPECTIVES_REFUSE_RECOMMENDATION"], {})
            if "revise" in pr and "proceed" not in pr:
                return _cycle1_early_reject(["PERSPECTIVES_REVISE_RECOMMENDATION"], {})

    if config.enable_simulation:
        if not state.simulations:
            return _cycle1_early_reject(["INSUFFICIENT_EVIDENCE"], {"simulator": "missing"})
        last_sim = state.simulations[-1]
        sem = float(getattr(last_sim, "semantic_expected_harm", 0.0) or 0.0)
        max_sem = float(config.cycle1_early_convergence_max_semantic_harm)
        if sem > max_sem:
            return _cycle1_early_reject(
                ["SIMULATOR_HARM_TOO_HIGH"],
                {"semantic_expected_harm": sem, "max_semantic_harm": max_sem},
            )
        dom_harms = list(getattr(last_sim, "dominant_harm_types", None) or [])
        if (
            any(
                h in dom_harms
                for h in (
                    "physical_harm",
                    "self_harm",
                    "emotional_manipulation",
                    "financial_deception",
                )
            )
            and sem >= 0.4
        ):
            return _cycle1_early_reject(["SIMULATOR_HARM_TOO_HIGH"], {"dominant_harm_types": dom_harms})
        if not _check_simulator_acceptable(state):
            return _cycle1_early_reject(["SIMULATOR_VALENCE_NOT_ACCEPTABLE"], {})
        positive.append("LOW_SIMULATED_HARM")
    else:
        positive.append("SIMULATOR_NOT_REQUIRED")

    if state.hindsight is not None:
        rec = getattr(
            getattr(state.hindsight, "aggregated", None),
            "recommendation",
            getattr(state.hindsight, "recommendation", None),
        )
        if rec is not None:
            rec = getattr(rec, "value", rec)
            rsl = str(rec).strip().lower()
            if "refuse" in rsl or "revise" in rsl:
                return _cycle1_early_reject(["HINDSIGHT_NEGATIVE_POSTURE"], {})

    positive.extend(
        [
            "CRITIC_CLEAN",
            "NO_HARD_VIOLATIONS",
            "HIGH_PERSPECTIVE_ALIGNMENT",
        ]
    )
    evidence_summary: dict[str, object] = {
        "weighted_approval": w_ap,
        "violations_count": 0,
        "violated_hard": False,
        "semantic_expected_harm": (
            float(getattr(state.simulations[-1], "semantic_expected_harm", 0.0) or 0.0) if state.simulations else None
        ),
    }
    sem_for_log = float(getattr(state.simulations[-1], "semantic_expected_harm", 0.0) or 0.0) if state.simulations else 0.0
    _LOG.info(
        "cycle1_early_convergence ACCEPTED: weighted_approval=%.3f semantic_harm=%.3f reason_codes=%s",
        w_ap,
        sem_for_log,
        positive,
    )
    return Cycle1EarlyConvergenceResult(True, positive, evidence_summary)


class ConvergenceEvaluator:
    """
    Evaluates convergence and decision type for a deliberation state.
    Uses OrchestratorConfig for thresholds (max_deliberation_cycles,
    early_exit_hindsight_threshold, min_hindsight_score).
    """

    def __init__(self, config: OrchestratorConfig) -> None:
        self.config = config

    def determine_decision(
        self,
        state: DeliberationState,
        risk_estimation: RiskEstimationProtocol | None = None,
    ) -> DecisionType:
        """
        Determine the decision type from votes (critic, simulator, perspectives, hindsight).
        Simulator can never produce REFUSE; REFUSE comes only from hard violations,
        op_risk HIGH, misuse HIGH, or explicit policy bounds.
        """
        state._convergence_evaluation_snapshot = None

        if state.has_critical_violations or (
            state.last_critique is not None and getattr(state.last_critique, "violated_hard", False)
        ):
            if state.last_critique is not None and getattr(state.last_critique, "decision", None) == "REFUSE":
                return self._finish_decision(
                    state,
                    DecisionType.REFUSE,
                    early_considered=False,
                    early_accepted=None,
                    reason_codes=["HARD_VIOLATION_GATE"],
                )
            return self._finish_decision(
                state,
                DecisionType.REVISE,
                early_considered=False,
                early_accepted=None,
                reason_codes=["HARD_VIOLATION_GATE"],
            )
        votes = {"proceed": 0, "revise": 0, "refuse": 0}
        if state.critiques:
            last_crit = state.critiques[-1]
            if last_crit.violations:
                votes["revise"] += 1
            else:
                votes["proceed"] += 1
        if state.simulations:
            last_sim = state.simulations[-1]
            if last_sim is not None:
                if last_sim.expected_valence >= 0.3:
                    votes["proceed"] += 1
                else:
                    # Simulator negativity triggers REVISE, never REFUSE.
                    # REFUSE must come only from hard violations, op_risk HIGH,
                    # misuse HIGH, or explicit policy bounds.
                    votes["revise"] += 1
            # Semantic harm influences REVISE votes (never REFUSE)
            sem_harm = last_sim.semantic_expected_harm
            dom_harms = list(last_sim.dominant_harm_types or [])
            if sem_harm >= 0.4:
                votes["revise"] += 1
            if sem_harm >= 0.6:
                votes["revise"] += 2
            # Hard-constraint harm types (CORE.NM.1, MH.CRISIS.1, CORE.MANIPULATION.1, FIN.SCAM.1)
            if (
                any(
                    h in dom_harms
                    for h in (
                        "physical_harm",
                        "self_harm",
                        "emotional_manipulation",
                        "financial_deception",
                    )
                )
                and sem_harm >= 0.4
            ):
                votes["revise"] += 2
        if state.perspectives:
            _persp_agg_obj = state._perspectives_aggregation
            if _persp_agg_obj is not None:
                # EnsembleResult wraps aggregation; PerspectiveAggregation has recommendation directly
                _inner_agg = getattr(_persp_agg_obj, "aggregation", _persp_agg_obj)
                persp_rec = getattr(_inner_agg, "recommendation", None)
            else:
                persp_rec = None
            if persp_rec is None and state.perspectives:
                approvals = [getattr(p, "approval_score", 0.5) for p in state.perspectives]
                if approvals:
                    avg = sum(approvals) / len(approvals)
                    persp_rec = "proceed" if avg >= 0.6 else ("revise" if avg >= 0.3 else "refuse")
            if persp_rec:
                persp_rec = str(persp_rec).strip().lower()
                if "proceed" in persp_rec:
                    votes["proceed"] += 1
                elif "revise" in persp_rec:
                    votes["revise"] += 1
                elif "refuse" in persp_rec:
                    votes["refuse"] += 1
        if state.hindsight is not None:
            rec = getattr(
                getattr(state.hindsight, "aggregated", None),
                "recommendation",
                getattr(state.hindsight, "recommendation", None),
            )
            if rec is not None:
                rec = getattr(rec, "value", rec)
                rec = str(rec).strip().lower()
                if "proceed" in rec:
                    votes["proceed"] += 1
                elif "revise" in rec:
                    votes["revise"] += 1
                elif "refuse" in rec:
                    votes["refuse"] += 1
        total_votes = votes["proceed"] + votes["revise"] + votes["refuse"]
        if total_votes == 0:
            return self._finish_decision(
                state,
                DecisionType.CONTINUE,
                early_considered=False,
                early_accepted=None,
                reason_codes=["NO_VOTES_CONTINUE"],
            )
        if votes["refuse"] > total_votes / 2:
            return self._finish_decision(
                state,
                DecisionType.REFUSE,
                early_considered=False,
                early_accepted=None,
                reason_codes=["VOTE_REFUSE_MAJORITY"],
            )

        c1_early: Cycle1EarlyConvergenceResult | None = None
        if state.cycle == 1:
            c1_early = _evaluate_cycle1_early_convergence(state, self.config, risk_estimation)
            if c1_early.accepted:
                if _has_pending_soft_suggestions(state, self.config):
                    return self._finish_decision(
                        state,
                        DecisionType.CONVERGED_WITH_SUGGESTIONS,
                        early_considered=True,
                        early_accepted=True,
                        reason_codes=c1_early.reason_codes,
                        c1_result=c1_early,
                    )
                return self._finish_decision(
                    state,
                    DecisionType.CONVERGED,
                    early_considered=True,
                    early_accepted=True,
                    reason_codes=c1_early.reason_codes,
                    c1_result=c1_early,
                )

        if votes["revise"] > votes["proceed"]:
            rc = ["VOTE_REVISE_MAJORITY"]
            if c1_early is not None and not c1_early.accepted:
                rc = list(c1_early.reason_codes) + rc
            return self._finish_decision(
                state,
                DecisionType.REVISE,
                early_considered=c1_early is not None,
                early_accepted=False if c1_early is not None else None,
                reason_codes=rc,
                c1_result=c1_early,
            )
        # Early exit (cycle >= 2): critic + perspectives threshold (legacy path)
        if state.cycle >= 2:
            if not state.has_critical_violations and state.critiques:
                last_crit = state.critiques[-1]
                if not last_crit.violations:
                    if state.perspectives:
                        weighted_approval = _calculate_weighted_approval(state.perspectives)
                        if weighted_approval >= self.config.early_exit_perspectives_threshold:
                            if _has_pending_soft_suggestions(state, self.config):
                                return self._finish_decision(
                                    state,
                                    DecisionType.CONVERGED_WITH_SUGGESTIONS,
                                    early_considered=False,
                                    early_accepted=None,
                                    reason_codes=["LEGACY_PERSPECTIVES_EARLY_EXIT"],
                                )
                            return self._finish_decision(
                                state,
                                DecisionType.CONVERGED,
                                early_considered=False,
                                early_accepted=None,
                                reason_codes=["LEGACY_PERSPECTIVES_EARLY_EXIT"],
                            )
        if state.hindsight_score >= self.config.min_hindsight_score:
            if _has_pending_soft_suggestions(state, self.config):
                return self._finish_decision(
                    state,
                    DecisionType.CONVERGED_WITH_SUGGESTIONS,
                    early_considered=False,
                    early_accepted=None,
                    reason_codes=["HINDSIGHT_SCORE_THRESHOLD"],
                )
            return self._finish_decision(
                state,
                DecisionType.CONVERGED,
                early_considered=False,
                early_accepted=None,
                reason_codes=["HINDSIGHT_SCORE_THRESHOLD"],
            )
        rc_cont = ["CONTINUE_DEFAULT"]
        if c1_early is not None and not c1_early.accepted:
            rc_cont = list(c1_early.reason_codes) + rc_cont
        return self._finish_decision(
            state,
            DecisionType.CONTINUE,
            early_considered=c1_early is not None,
            early_accepted=False if c1_early is not None else None,
            reason_codes=rc_cont,
            c1_result=c1_early,
        )

    def _build_snapshot(
        self,
        state: DeliberationState,
        *,
        decision: DecisionType | None,
        early_considered: bool,
        early_accepted: bool | None,
        reason_codes: list[str],
        c1_result: Cycle1EarlyConvergenceResult | None = None,
    ) -> dict[str, object]:
        lc = state.last_critique
        critic_decision = (getattr(lc, "decision", "") or "").strip().upper() if lc is not None else ""
        violations_count = 0
        violated_hard = False
        if lc is not None:
            viol = getattr(lc, "violations", None) or []
            violations_count = len(viol)
            violated_hard = bool(getattr(lc, "violated_hard", False))
        sem_harm: float | None = None
        if state.simulations:
            last_sim = state.simulations[-1]
            sem_harm = float(getattr(last_sim, "semantic_expected_harm", 0.0) or 0.0)
        persp_w: float | None = None
        if state.perspectives:
            persp_w = _calculate_weighted_approval(state.perspectives)
        ev = c1_result.evidence_summary if c1_result else {}
        return {
            "cycle": state.cycle,
            "decision": decision.value if decision is not None else None,
            "early_convergence_considered": early_considered,
            "early_convergence_accepted": early_accepted,
            "convergence_reason_codes": list(reason_codes),
            "critic_decision": critic_decision,
            "violations_count": violations_count,
            "violated_hard": violated_hard,
            "semantic_expected_harm": sem_harm,
            "perspectives_weighted_approval": persp_w,
            "cycle1_evidence_summary": dict(ev) if ev else {},
        }

    def _finish_decision(
        self,
        state: DeliberationState,
        decision: DecisionType,
        *,
        early_considered: bool,
        early_accepted: bool | None,
        reason_codes: list[str],
        c1_result: Cycle1EarlyConvergenceResult | None = None,
    ) -> DecisionType:
        snap = self._build_snapshot(
            state,
            decision=decision,
            early_considered=early_considered,
            early_accepted=early_accepted,
            reason_codes=reason_codes,
            c1_result=c1_result,
        )
        state._convergence_evaluation_snapshot = snap
        return decision
