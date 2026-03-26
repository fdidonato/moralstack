"""
Coercion utilities for RiskPolicyAction.

Centralizes conversion from legacy string input to RiskPolicyAction enum.
"""

from __future__ import annotations

from .categories import RiskPolicyAction

# Case-insensitive mapping from string to enum (for legacy input)
_STR_TO_ENUM = {
    "ALLOW": RiskPolicyAction.ALLOW,
    "ALLOW_WITH_CAVEAT": RiskPolicyAction.ALLOW_WITH_CAVEAT,
    "DELIBERATE": RiskPolicyAction.DELIBERATE,
    "DENY": RiskPolicyAction.DENY,
}


def coerce_risk_policy_action(value: str | RiskPolicyAction | None) -> RiskPolicyAction:
    """
    Coerce str | RiskPolicyAction | None to RiskPolicyAction.

    - Case-insensitive for string input.
    - Returns RiskPolicyAction.DELIBERATE for unknown or None (safe fallback).
    """
    if value is None:
        return RiskPolicyAction.DELIBERATE
    if isinstance(value, RiskPolicyAction):
        return value
    key = (str(value).strip().upper() or "").strip()
    return _STR_TO_ENUM.get(key, RiskPolicyAction.DELIBERATE)
