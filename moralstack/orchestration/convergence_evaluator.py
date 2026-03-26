"""
Convergence evaluation for deliberative cycles.
Determines whether the process has converged and which DecisionType to apply.
Invariants and structured logging remain in convergence.py.
"""

from __future__ import annotations

from moralstack.orchestration.types import (
    DecisionType,
    DeliberationState,
    OrchestratorConfig,
)


def _calculate_weighted_approval(perspectives: list) -> float:
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


class ConvergenceEvaluator:
    """
    Evaluates convergence and decision type for a deliberation state.
    Uses OrchestratorConfig for thresholds (max_deliberation_cycles,
    early_exit_hindsight_threshold, min_hindsight_score).
    """

    def __init__(self, config: OrchestratorConfig) -> None:
        self.config = config

    def determine_decision(self, state: DeliberationState) -> DecisionType:
        """
        Determine the decision type from votes (critic, simulator, perspectives, hindsight).
        Simulator can never produce REFUSE; REFUSE comes only from hard violations,
        op_risk HIGH, misuse HIGH, or explicit policy bounds.
        """
        if state.has_critical_violations or (
            state.last_critique is not None and getattr(state.last_critique, "violated_hard", False)
        ):
            if state.last_critique is not None and getattr(state.last_critique, "decision", None) == "REFUSE":
                return DecisionType.REFUSE
            return DecisionType.REVISE
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
            return DecisionType.CONTINUE
        if votes["refuse"] > total_votes / 2:
            return DecisionType.REFUSE
        if votes["revise"] > votes["proceed"]:
            return DecisionType.REVISE
        # Early exit: critic PROCEED senza violazioni + alta approvazione perspectives
        # Non richiede hindsight (che con enable_hindsight_gating è skip in ciclo 1)
        if not state.has_critical_violations and state.critiques:
            last_crit = state.critiques[-1]
            if not last_crit.violations:
                if state.perspectives:
                    weighted_approval = _calculate_weighted_approval(state.perspectives)
                    if weighted_approval >= self.config.early_exit_perspectives_threshold:
                        if _has_pending_soft_suggestions(state, self.config):
                            return DecisionType.CONVERGED_WITH_SUGGESTIONS
                        return DecisionType.CONVERGED
        if state.hindsight_score >= self.config.min_hindsight_score:
            if _has_pending_soft_suggestions(state, self.config):
                return DecisionType.CONVERGED_WITH_SUGGESTIONS
            return DecisionType.CONVERGED
        return DecisionType.CONTINUE
