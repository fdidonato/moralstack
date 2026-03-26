"""
SAFE_COMPLETE gating: emettere SAFE_COMPLETE solo quando strettamente necessario.
Domini regolati e/o segnali specifici; in tutti gli altri casi borderline/educativi
-> NORMAL_COMPLETE. Solo metadati e routing; nessuna inferenza dal testo.
"""

from __future__ import annotations

import json
import logging

from moralstack.models.risk import RiskPolicyAction, coerce_risk_policy_action
from moralstack.orchestration.diagnostics import orch_debug_log
from moralstack.orchestration.types import Decision, ProcessedRequest, RiskEstimationProtocol

_LOG = logging.getLogger(__name__)


def _norm_domain(domain: str | None) -> str | None:
    if domain is None:
        return None
    v = (str(domain).strip().lower() or "").strip()
    return v if v else None


def _axis_at_least(s: str | None, level: str) -> bool:
    """True se il livello dell'asse è >= level (LOW < MEDIUM < HIGH)."""
    if s is None:
        return False
    u = (str(s).strip().upper() or "").strip()
    order = ("LOW", "MEDIUM", "HIGH")
    try:
        return order.index(u) >= order.index(level)
    except (ValueError, KeyError):
        return False


def should_use_safe_complete(
    domain: str | None,
    risk_policy_action: str | RiskPolicyAction | None,
    misuse_plausibility: str | None,
    actionability_risk: str | None,
    has_hard_violations: bool = False,
    overlay_sensitive: bool = False,
) -> bool:
    """
    True SOLO se SAFE_COMPLETE è giustificato da dominio regolato o segnali.
    - hard_violations presenti => il caller deve REFUSE, non SAFE_COMPLETE
      (non usare per gating downgrade).
    - domain in REGULATED_DOMAINS => True (anche con segnali LOW).
    - (misuse_plausibility >= MEDIUM AND actionability_risk >= MEDIUM) => True.
    - Se risk_policy_action == ALLOW e domain NON regulated e segnali non attivi
      => False.
    """

    # Removed: hard_violations are fully handled by decision_service._handle_hard_violations.
    # That function already decides REFUSE or SAFE_COMPLETE based on risk context.
    # The gating must not override a deliberate SAFE_COMPLETE from hard_violations handling.
    # if has_hard_violations:
    #    return False
    rpa = coerce_risk_policy_action(risk_policy_action)

    if overlay_sensitive:
        return True
    signals_active = _axis_at_least(misuse_plausibility, "MEDIUM") and _axis_at_least(actionability_risk, "MEDIUM")
    if signals_active:
        return True
    if rpa == RiskPolicyAction.ALLOW and not overlay_sensitive:
        return False
    return False


def apply_safe_complete_gating(
    decision: Decision,
    request: ProcessedRequest | None,
    risk_estimation: RiskEstimationProtocol,
    *,
    request_id: str = "",
    question_id: str | None = None,
    overlay_sensitive: bool = False,
) -> Decision:
    """
    Se decision.final_action è SAFE_COMPLETE ma should_use_safe_complete(...) è False,
    downgrade a NORMAL_COMPLETE. REFUSE invariato. Logga ogni downgrade.
    """
    if decision.final_action != "SAFE_COMPLETE":
        return decision

    # When safe_complete_policy set SAFE_COMPLETE because risk_category is
    # SENSITIVE or MORALLY_NUANCED, the policy decision is authoritative.
    # The gating must not downgrade it based on domain/signal checks alone —
    # those checks are designed for POTENTIALLY_HARMFUL gray-zone cases,
    # not for categories where SAFE_COMPLETE is already min_required.
    risk_category = getattr(risk_estimation, "risk_category", None)
    rc_val = getattr(risk_category, "value", str(risk_category or "")).strip().lower()
    if rc_val in ("sensitive", "morally_nuanced"):
        return decision

    domain = getattr(request, "get_domain", lambda: None)() if request is not None else None
    risk_policy_action = coerce_risk_policy_action(getattr(risk_estimation, "risk_policy_action", None))
    misuse_plausibility = getattr(decision, "misuse_plausibility", "") or ""
    actionability_risk = getattr(decision, "actionability_risk", "") or ""
    has_hard_violations = bool(getattr(decision, "hard_violations", None))

    if should_use_safe_complete(
        domain=domain,
        risk_policy_action=risk_policy_action,
        misuse_plausibility=misuse_plausibility,
        actionability_risk=actionability_risk,
        has_hard_violations=has_hard_violations,
        overlay_sensitive=overlay_sensitive,
    ):
        rpa_val = getattr(risk_policy_action, "value", str(risk_policy_action))
        gating_reason = "overlay_sensitive" if overlay_sensitive else "signals_misuse_and_actionability_medium_plus"
        orch_debug_log(
            "safe_complete_gating.py:apply_safe_complete_gating",
            "SAFE_COMPLETE_GATING_PASSED",
            {
                "overlay_sensitive": overlay_sensitive,
                "misuse_plausibility": misuse_plausibility,
                "actionability_risk": actionability_risk,
                "risk_policy_action": rpa_val,
                "reason": gating_reason,
            },
            "H-safe-complete-gating",
            request_id=request_id,
        )
        return decision

    try:
        rpa_val = getattr(risk_policy_action, "value", str(risk_policy_action))
        payload = {
            "event": "SAFE_COMPLETE_DOWNGRADED",
            "request_id": request_id or "",
            "domain": (domain or "").strip().lower() or None,
            "misuse_plausibility": misuse_plausibility,
            "actionability_risk": actionability_risk,
            "risk_policy_action": rpa_val,
            "overlay_sensitive": overlay_sensitive,
            "reason": "domain_not_regulated_and_signals_low",
        }
        if question_id is not None:
            payload["question_id"] = question_id

        _LOG.info("%s", json.dumps(payload, ensure_ascii=False))
        orch_debug_log(
            "safe_complete_gating.py:apply_safe_complete_gating",
            "SAFE_COMPLETE_DOWNGRADED",
            {k: v for k, v in payload.items() if k != "event"},
            "H-safe-complete-gating",
            request_id=request_id,
        )
    except Exception as e:
        _LOG.warning(
            "_log_fast_path_decision failed request_id=%s question_id=%s error_type=%s error=%s",
            request_id or "",
            question_id,
            type(e).__name__,
            e,
        )

    return Decision(
        final_action="NORMAL_COMPLETE",
        path=decision.path,
        intent_clarity=decision.intent_clarity,
        misuse_plausibility=decision.misuse_plausibility,
        actionability_risk=decision.actionability_risk,
        triggered_principles=decision.triggered_principles,
        hard_violations=decision.hard_violations,
        risk_signals=decision.risk_signals,
    )
