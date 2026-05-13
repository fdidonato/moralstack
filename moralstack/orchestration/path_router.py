"""
Path router: determines which route (refuse, benign, safe_complete, fast_path, deliberative)
the controller should take based on decision, risk, and config.

Extracted from process() to keep routing logic in one place and controller thin.
"""

from __future__ import annotations

from typing import Literal

from moralstack.models.risk import OperationalRisk, RiskPolicyAction, coerce_risk_policy_action
from moralstack.orchestration.types import Decision, OrchestratorConfig, RiskEstimationProtocol

Route = Literal["refuse", "benign", "safe_complete", "fast_path", "deliberative"]

_HARD_SEMANTIC_SIGNALS: frozenset[str] = frozenset(
    {
        "Q4:fraud",
        "Q5:physical_harm",
        "Q8:self_harm_suicide",
        "Q9:cyber_malware",
        "Q10:weapons_explosives_toxins",
        "Q11:privacy_doxxing_stalking",
        "Q12:medical_harmful",
        "Q17:minor_exploitation",
    }
)

_NON_OVERRIDABLE_REFUSE_REASON_CODES: frozenset[str] = frozenset(
    {
        "normative_refuse_non_overridable",
        "operational_risk_high_refuse",
        "requested_instructions_refuse",
        "intent_to_harm_refuse",
        "risk_clearly_harmful",
        "hard_violations",
    }
)


def is_hard_signal_refuse(
    decision: Decision,
    risk_estimation: RiskEstimationProtocol,
    op_risk: OperationalRisk,
) -> bool:
    """
    True when REFUSE is anchored by hard policy evidence, so borderline deliberation
    must be bypassed.
    """
    if decision.final_action != "REFUSE":
        return False
    if bool(getattr(decision, "hard_violations", None)):
        return True

    reason_codes = {
        str(code).strip().lower() for code in (getattr(decision, "reason_codes", None) or []) if str(code).strip()
    }
    if reason_codes & _NON_OVERRIDABLE_REFUSE_REASON_CODES:
        return True

    decision_signals = {str(sig).strip() for sig in (getattr(decision, "risk_signals", None) or []) if str(sig).strip()}
    risk_signals = {
        str(sig).strip() for sig in (getattr(risk_estimation, "semantic_signals", None) or []) if str(sig).strip()
    }
    return bool((decision_signals | risk_signals) & _HARD_SEMANTIC_SIGNALS)


def get_route(
    decision: Decision,
    risk_estimation: RiskEstimationProtocol,
    risk_score: float,
    config: OrchestratorConfig,
    op_risk: OperationalRisk,
) -> tuple[Route, bool, RiskPolicyAction]:
    """
    Return (route, borderline_refuse, risk_policy_action) for the controller to dispatch.
    borderline_refuse is True when REFUSE would apply but risk is in the borderline
    range so we force the deliberative loop instead.
    risk_policy_action is the resolved policy action (for logging when route is fast_path/deliberative).
    """
    final_action_str = decision.final_action
    path = decision.path
    low = config.risk_thresholds.low
    medium = config.risk_thresholds.medium
    borderline_upper = config.borderline_refuse_upper

    hard_signal_refuse = is_hard_signal_refuse(decision, risk_estimation, op_risk)
    borderline_refuse = (
        final_action_str == "REFUSE"
        and path != "DELIBERATIVE_PATH"
        and risk_score >= medium
        and risk_score <= borderline_upper
        and not hard_signal_refuse
    )

    risk_policy_action = coerce_risk_policy_action(getattr(risk_estimation, "risk_policy_action", None))
    if op_risk == OperationalRisk.HIGH:
        risk_policy_action = RiskPolicyAction.DENY
    elif op_risk == OperationalRisk.NONE:
        risk_policy_action = RiskPolicyAction.ALLOW
    elif op_risk == OperationalRisk.LOW and risk_policy_action != RiskPolicyAction.DENY:
        risk_policy_action = RiskPolicyAction.DELIBERATE

    # NOTA ARCHITETTURALE: questo blocco ri-deriva risk_policy_action da op_risk
    # esclusivamente per il ROUTING e per il logging nel controller.
    # NON è la fonte autoritativa della decisione finale (final_action).
    # La fonte autoritativa è decision_service._handle_normative_refuse_guards().
    # Se modifichi la logica op_risk qui, devi aggiornare anche decision_service.py
    # e viceversa. Le due devono rimanere allineate.

    if final_action_str == "REFUSE" and path != "DELIBERATIVE_PATH" and not borderline_refuse:
        return "refuse", borderline_refuse, risk_policy_action

    if (
        final_action_str == "NORMAL_COMPLETE"
        and op_risk == OperationalRisk.NONE
        and risk_score < low
        and path != "DELIBERATIVE_PATH"
    ):
        return ("benign", borderline_refuse, risk_policy_action)

    if final_action_str == "SAFE_COMPLETE" and path != "DELIBERATIVE_PATH":
        return ("safe_complete", borderline_refuse, risk_policy_action)

    if final_action_str == "NORMAL_COMPLETE" and risk_policy_action == RiskPolicyAction.ALLOW and risk_score < low:
        return ("fast_path", borderline_refuse, risk_policy_action)

    return ("deliberative", borderline_refuse, risk_policy_action)
