"""
Centralized reason codes for decision explainability.
Machine-readable codes mapped from policy reason strings.
"""

from __future__ import annotations

from enum import Enum


class ReasonCode(str, Enum):
    """Structured, machine-readable reason codes for audit and explainability."""

    HIGH_RISK_OPERATIONAL = "HIGH_RISK_OPERATIONAL"
    SENSITIVE_DOMAIN = "SENSITIVE_DOMAIN"
    OVERLAY_HEALTHCARE = "OVERLAY_HEALTHCARE"
    OVERLAY_LEGAL = "OVERLAY_LEGAL"
    OVERLAY_CYBERSECURITY = "OVERLAY_CYBERSECURITY"
    ACADEMIC_LOPHOLE_PATTERN = "ACADEMIC_LOPHOLE_PATTERN"
    FAST_PATH_LOW_RISK = "FAST_PATH_LOW_RISK"
    CYCLES_EXHAUSTED_FALLBACK = "CYCLES_EXHAUSTED_FALLBACK"
    RISK_BELOW_REFUSE_THRESHOLD = "RISK_BELOW_REFUSE_THRESHOLD"
    NO_OPERATIONAL_INTENT = "NO_OPERATIONAL_INTENT"
    DEFAULT_NORMAL_COMPLETE = "DEFAULT_NORMAL_COMPLETE"
    HARD_VIOLATIONS = "HARD_VIOLATIONS"
    RISK_CLEARLY_HARMFUL = "RISK_CLEARLY_HARMFUL"
    RISK_POTENTIALLY_HARMFUL = "RISK_POTENTIALLY_HARMFUL"
    RISK_SENSITIVE = "RISK_SENSITIVE"
    RISK_MORALLY_NUANCED = "RISK_MORALLY_NUANCED"
    RISK_BENIGN = "RISK_BENIGN"
    DOMAIN_REGULATED = "DOMAIN_REGULATED"
    SAFE_COMPLETE_REQUIRED = "SAFE_COMPLETE_REQUIRED"
    SAFE_COMPLETE_ALLOWED = "SAFE_COMPLETE_ALLOWED"
    NORMAL_COMPLETE_REQUIRED = "NORMAL_COMPLETE_REQUIRED"
    REGULATED_BUT_INFORMATIONAL = "REGULATED_BUT_INFORMATIONAL"
    SIM_NEGATIVE_VALENCE = "SIM_NEGATIVE_VALENCE"
    HIGH_ACTIONABILITY = "HIGH_ACTIONABILITY"


# Mapping from policy reason strings (safe_complete_policy, decision_service) to ReasonCode
_POLICY_TO_REASON: dict[str, ReasonCode] = {
    "hard_violations": ReasonCode.HARD_VIOLATIONS,
    "risk_clearly_harmful": ReasonCode.RISK_CLEARLY_HARMFUL,
    "risk_potentially_harmful": ReasonCode.RISK_POTENTIALLY_HARMFUL,
    "risk_sensitive": ReasonCode.RISK_SENSITIVE,
    "risk_morally_nuanced": ReasonCode.RISK_MORALLY_NUANCED,
    "risk_benign": ReasonCode.RISK_BENIGN,
    "domain_regulated": ReasonCode.DOMAIN_REGULATED,
    "safe_complete_required": ReasonCode.SAFE_COMPLETE_REQUIRED,
    "safe_complete_allowed": ReasonCode.SAFE_COMPLETE_ALLOWED,
    "normal_complete_required": ReasonCode.NORMAL_COMPLETE_REQUIRED,
    "risk_sensitive_allowed": ReasonCode.SAFE_COMPLETE_ALLOWED,
    "regulated_but_informational": ReasonCode.REGULATED_BUT_INFORMATIONAL,
    "sim_negative_valence_safe_complete": ReasonCode.SIM_NEGATIVE_VALENCE,
    "hard_violation_downgraded_to_safe_complete": ReasonCode.SAFE_COMPLETE_REQUIRED,
    "safe_complete_required_high_actionability": ReasonCode.HIGH_ACTIONABILITY,
    "policy_bounds_decision": ReasonCode.DEFAULT_NORMAL_COMPLETE,
    "cycles_exhausted_sensitive_fallback": ReasonCode.CYCLES_EXHAUSTED_FALLBACK,
}


def policy_reason_codes_to_reason_codes(reason_codes: list[str] | None) -> list[str]:
    """
    Maps policy reason strings to structured ReasonCode values.
    Returns at least one code; uses DEFAULT_NORMAL_COMPLETE if no mapping.
    """
    if not reason_codes:
        return [ReasonCode.DEFAULT_NORMAL_COMPLETE.value]
    result: list[str] = []
    seen: set[str] = set()
    for rc in reason_codes:
        if not rc or not isinstance(rc, str):
            continue
        key = rc.strip().lower()
        if key in seen:
            continue
        mapped = _POLICY_TO_REASON.get(key)
        if mapped is not None:
            val = mapped.value
            if val not in seen:
                result.append(val)
                seen.add(val)
        else:
            # Unknown code: use uppercase normalized form if looks like enum, else default
            upper = rc.strip().upper().replace("-", "_").replace(" ", "_")
            if upper and upper.isidentifier():
                result.append(upper)
                seen.add(upper)
            elif ReasonCode.DEFAULT_NORMAL_COMPLETE.value not in seen:
                result.append(ReasonCode.DEFAULT_NORMAL_COMPLETE.value)
                seen.add(ReasonCode.DEFAULT_NORMAL_COMPLETE.value)
    if not result:
        return [ReasonCode.DEFAULT_NORMAL_COMPLETE.value]
    return result
