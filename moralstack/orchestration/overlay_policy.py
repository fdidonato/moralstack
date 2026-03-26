"""
Overlay policy helpers: constitution lookup and risk-score floor for sensitive overlays.

Centralizes get_constitution + try/except and overlay_sensitive / risk_floor logic
to avoid duplication in the controller.
"""

from __future__ import annotations

import logging
from typing import Any

from moralstack.orchestration.types import ConstitutionStoreProtocol

# Minimum risk_score when a constitutional overlay has sensitive=true.
# Ensures entry into the deliberative path (threshold_low default = 0.3).
OVERLAY_SENSITIVE_RISK_FLOOR = 0.35

_LOG = logging.getLogger(__name__)


def get_constitution_safe(constitution_store: ConstitutionStoreProtocol | None, domain: str | None) -> Any:
    """
    Return constitution for the given domain, or None on error or if store is None.
    Logs a warning on exception instead of swallowing silently.
    """
    if constitution_store is None:
        return None
    try:
        return constitution_store.get_constitution(domain)
    except Exception as e:
        _LOG.warning("get_constitution_safe failed for domain=%s: %s", domain, e)
        return None


def is_overlay_sensitive(constitution_store: ConstitutionStoreProtocol | None, domain: str | None) -> bool:
    """
    Return True if the active overlay for the given domain has sensitive=true.
    Uses get_constitution_safe; returns False on error or if no overlay.
    """
    constitution = get_constitution_safe(constitution_store, domain)
    active_overlay = getattr(constitution, "active_overlay", None) if constitution else None
    if not active_overlay:
        return False
    return bool(getattr(active_overlay, "sensitive", False))


def is_domain_excluded(constitution_store: ConstitutionStoreProtocol | None, domain: str | None) -> bool:
    """
    Return True if the overlay for the given domain has excluded=true.
    [NO LLM] Uses get_constitution_safe; returns False on error or if no overlay.
    """
    constitution = get_constitution_safe(constitution_store, domain)
    active_overlay = getattr(constitution, "active_overlay", None) if constitution else None
    if not active_overlay:
        return False
    return bool(getattr(active_overlay, "excluded", False))


def apply_risk_floor_if_sensitive(
    risk_score: float,
    overlay_sensitive: bool,
    floor: float = OVERLAY_SENSITIVE_RISK_FLOOR,
    overlay_floor_override: float | None = None,
) -> float:
    """
    If overlay_sensitive and risk_score < effective_floor, return effective_floor; else return risk_score.
    overlay_floor_override: per-overlay floor from Overlay.sensitive_risk_floor (None = use global default).
    """
    effective_floor = overlay_floor_override if overlay_floor_override is not None else floor
    if overlay_sensitive and risk_score < effective_floor:
        return effective_floor
    return risk_score
