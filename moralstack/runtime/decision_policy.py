"""
Decision policy: deterministic rules for SAFE_COMPLETE and NORMAL_COMPLETE.

SAFE_COMPLETE is a first-class action, triggered by risk_level + intent_type + domain_overlay.
No inference from text; structured signals only.
"""

from __future__ import annotations

# -----------------------------------------------------------------------------
# Policy constants (rule-based)
# -----------------------------------------------------------------------------

RISK_LEVELS_SENSITIVE = frozenset({"sensitive", "morally_nuanced"})
INTENT_TYPES_SUPPORT_ADVICE = frozenset({"support", "advice"})
INTENT_TYPES_FACTUAL_EXPLANATION = frozenset({"factual", "explanation"})
DOMAINS_REQUIRING_SAFE = frozenset({"healthcare", "mental_health", "legal", "financial"})


def _norm_risk_level(risk_level: str | None) -> str | None:
    """Normalizes risk_level to lowercase; None if empty/invalid."""
    if risk_level is None:
        return None
    v = (str(risk_level).strip().lower() or "").strip()
    if not v:
        return None
    valid = {"benign", "sensitive", "morally_nuanced", "potentially_harmful", "clearly_harmful"}
    return v if v in valid else None


def _norm_intent_type(intent_type: str | None) -> str | None:
    """Normalizes intent_type to lowercase (SUPPORT -> support)."""
    if intent_type is None:
        return None
    v = (str(intent_type).strip().lower() or "").strip()
    if not v:
        return None
    valid = {"support", "advice", "factual", "explanation"}
    return v if v in valid else None


def _norm_domain(domain_overlay: str | None) -> str | None:
    """Normalizes domain_overlay to lowercase; None if empty."""
    if domain_overlay is None:
        return None
    v = (str(domain_overlay).strip().lower() or "").strip()
    return v if v else None


def safe_complete_required(
    risk_level: str | None,
    intent_type: str | None,
    domain_overlay: str | None,
) -> bool:
    """
    SAFE_COMPLETE is REQUIRED if any of the following conditions holds:

    1) risk_level ∈ {SENSITIVE, MORALLY_NUANCED} AND intent_type ∈ {SUPPORT, ADVICE}

    2) domain_overlay ∈ {healthcare, mental_health, legal, financial} AND risk_level != BENIGN

    Pure function; no side effects.
    """
    r = _norm_risk_level(risk_level)
    i = _norm_intent_type(intent_type)
    d = _norm_domain(domain_overlay)

    # Rule 1: sensitive/morally_nuanced + support/advice → SAFE_COMPLETE required
    if r in RISK_LEVELS_SENSITIVE and i is not None and i in INTENT_TYPES_SUPPORT_ADVICE:
        return True

    # Rule 2: binding domain + non-benign risk → SAFE_COMPLETE required
    if d is not None and d in DOMAINS_REQUIRING_SAFE and r is not None and r != "benign":
        return True

    return False


def intent_type_from_request_type(
    request_type: str | None,
    *,
    intent_operational: bool = False,
    risk_score: float = 0.5,
) -> str | None:
    """
    Derives intent_type from request_type when the LLM provides no explicit intent_type.
    Used for policy and DCF.

    For sensitive_topic: non-operational + low risk_score → factual (informational/descriptive).
    Otherwise conservative → advice.
    """
    if request_type is None:
        return None
    r = (str(request_type).strip().lower() or "").strip()
    if not r:
        return None
    if r in ("crisis_support", "support_request"):
        return "support"
    if r in ("factual_query", "creative_request"):
        return "factual"
    if r == "ethical_dilemma":
        return "advice"
    if r == "sensitive_topic":
        # Non-operational + low risk → informational/descriptive (factual)
        # Otherwise conservative: advice (support-seeking or operational context)
        if not intent_operational and risk_score < 0.5:
            return "factual"
        return "advice"
    if r in ("potential_harm", "clear_harm"):
        return "advice"
    return None


def normal_complete_allowed(
    risk_level: str | None,
    intent_type: str | None,
    domain_overlay: str | None,
) -> bool:
    """
    NORMAL_COMPLETE is allowed ONLY if all of the following hold:

    - risk_level ∈ {SENSITIVE, MORALLY_NUANCED}
    - intent_type ∈ {FACTUAL, EXPLANATION}
    - domain_overlay is None

    In that case the allowed range is [NORMAL_COMPLETE, SAFE_COMPLETE].
    Pure function.
    """
    r = _norm_risk_level(risk_level)
    i = _norm_intent_type(intent_type)
    d = _norm_domain(domain_overlay)

    if r not in RISK_LEVELS_SENSITIVE:
        return False
    if i is None or i not in INTENT_TYPES_FACTUAL_EXPLANATION:
        return False
    if d is not None:
        return False
    return True
