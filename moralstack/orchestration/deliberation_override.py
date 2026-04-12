"""Pure deliberation override: REFUSE -> SAFE_COMPLETE only under strict borderline rules."""

from __future__ import annotations

from moralstack.orchestration.types import (
    ConvergenceOutcome,
    Decision,
    DeliberationState,
    RiskEstimationProtocol,
)


def evaluate_deliberation_override(
    pre_decision: Decision | None,  # noqa: ARG001
    post_decision: Decision,
    state: DeliberationState,
    risk_estimation: RiskEstimationProtocol,
    outcome: ConvergenceOutcome | None,  # noqa: ARG001
) -> Decision | None:
    """
    Evaluate whether deliberative modules unanimously contradict a REFUSE decision.

    IMPORTANT:
    REFUSE -> SAFE_COMPLETE override is allowed ONLY for borderline cases.
    It is NEVER allowed when the refusal is backed by hard harmfulness signals.

    Non-overridable conditions:
    - operational_risk == HIGH
    - risk_category == clearly_harmful
    - requested_instructions == True
    - intent_to_harm == True
    - hard_violations present on the decision
    - reason_codes indicate a normative/non-overridable refuse
    """
    if post_decision.final_action != "REFUSE":
        return None

    op_risk = getattr(risk_estimation, "operational_risk", None)
    op_risk_val = getattr(op_risk, "value", op_risk)
    op_risk_val = str(op_risk_val).strip().upper() if op_risk_val is not None else ""

    risk_category = getattr(risk_estimation, "risk_category", None)
    risk_category_val = getattr(risk_category, "value", risk_category)
    risk_category_val = str(risk_category_val).strip().lower() if risk_category_val is not None else ""

    requested_instructions = bool(getattr(risk_estimation, "requested_instructions", False))
    intent_to_harm = bool(getattr(risk_estimation, "intent_to_harm", False))
    intent_operational = bool(getattr(risk_estimation, "intent_operational", False))

    post_reason_codes = list(getattr(post_decision, "reason_codes", None) or [])
    post_hard_violations = list(getattr(post_decision, "hard_violations", None) or [])

    non_overridable_reason_codes = {
        "normative_refuse_non_overridable",
        "operational_risk_high_refuse",
        "requested_instructions_refuse",
        "intent_to_harm_refuse",
        "risk_clearly_harmful",
        "hard_violations",
    }

    if op_risk_val == "HIGH":
        return None
    if risk_category_val == "clearly_harmful":
        return None
    if requested_instructions:
        return None
    if intent_to_harm:
        return None
    if intent_operational and risk_category_val in ("clearly_harmful", "potentially_harmful"):
        return None
    if post_hard_violations:
        return None
    if any(code in non_overridable_reason_codes for code in post_reason_codes):
        return None

    if state.last_critique is None:
        return None
    if getattr(state.last_critique, "violated_hard", False):
        return None

    violations = getattr(state.last_critique, "violations", None) or []
    if len(violations) > 0:
        return None

    critic_decision = (getattr(state.last_critique, "decision", "") or "").strip().upper()
    if critic_decision not in ("PROCEED", ""):
        return None

    if not state.perspectives:
        return None

    approvals = [getattr(p, "approval_score", 0.0) for p in state.perspectives if hasattr(p, "approval_score")]
    if not approvals or (sum(approvals) / len(approvals)) < 0.7:
        return None

    if state.simulations:
        last_sim = state.simulations[-1]
        exp_val = getattr(last_sim, "expected_valence", -1.0)
        sem_harm = getattr(last_sim, "semantic_expected_harm", 1.0)

        request_type = getattr(risk_estimation, "request_type", "") or ""
        is_educational = request_type in ("factual_query", "sensitive_topic", "ethical_dilemma")
        sem_harm_threshold = 0.5 if is_educational else 0.3

        if exp_val < 0 or sem_harm >= sem_harm_threshold:
            return None

    if state.hindsight is not None:
        agg = getattr(state.hindsight, "aggregated", None)
        rec = getattr(agg, "recommendation", None) if agg else None
        rec = rec or getattr(state.hindsight, "recommendation", None)
        if rec is not None:
            rec_val = str(getattr(rec, "value", rec)).strip().lower()
            if rec_val == "refuse":
                return None

    if state.has_critical_violations:
        return None

    return Decision(
        final_action="SAFE_COMPLETE",
        path="DELIBERATIVE_PATH",
        intent_clarity=post_decision.intent_clarity,
        misuse_plausibility=post_decision.misuse_plausibility,
        actionability_risk=post_decision.actionability_risk,
        triggered_principles=post_decision.triggered_principles,
        hard_violations=post_decision.hard_violations,
        risk_signals=post_decision.risk_signals,
        reason_codes=list(post_decision.reason_codes) + ["deliberation_override_refuse_to_safe_complete"],
    )
