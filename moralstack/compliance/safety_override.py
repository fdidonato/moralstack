"""
Safety Override system for the DCCL.

Defines the immutable, framework-fixed list of safety-restricted output categories.
A rule whose action_payload classifies as safety-restricted is never authorized
by the DCCL, regardless of what the deployer's contract says.

Reference: dccl_specification_v0.3.md section 5.

Commit 1 (Foundation): only the category constants and a placeholder classifier
that always returns None (no classification, no override) are defined here.
The full classifier (keyword + LLM) is implemented in Commit 2.
"""

from __future__ import annotations

from typing import Final

# =============================================================================
# Safety Override Categories (framework-fixed, NOT deployer-configurable)
# =============================================================================

WEAPONS_SYNTHESIS: Final[str] = "weapons_synthesis"
CBRN_OPERATIONAL: Final[str] = "cbrn_operational"
CSAM: Final[str] = "csam"
SELF_HARM_OPERATIONAL: Final[str] = "self_harm_operational"
FRAUD_MALWARE: Final[str] = "fraud_malware"
DOXXING_STALKING: Final[str] = "doxxing_stalking"
ILLEGAL_PHARMA: Final[str] = "illegal_pharma"

SAFETY_OVERRIDE_CATEGORIES: Final[tuple[str, ...]] = (
    WEAPONS_SYNTHESIS,
    CBRN_OPERATIONAL,
    CSAM,
    SELF_HARM_OPERATIONAL,
    FRAUD_MALWARE,
    DOXXING_STALKING,
    ILLEGAL_PHARMA,
)


# =============================================================================
# Classifier (Commit 1: placeholder; Commit 2: full implementation)
# =============================================================================

def classify_safety_override(action_payload: str) -> str | None:
    """
    Return the matching safety override category for an action_payload,
    or None if the payload is not safety-restricted.

    Commit 1: placeholder. Always returns None.
    Commit 2: will be implemented with keyword check + LLM classifier.

    Args:
        action_payload: the text the deployer wants to emit as response.

    Returns:
        A category string from SAFETY_OVERRIDE_CATEGORIES, or None.
    """
    # Placeholder for Commit 1. The actual classification logic ships in Commit 2.
    return None
