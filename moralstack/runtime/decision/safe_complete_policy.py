"""
SAFE_COMPLETE policy: single source of truth for action bounds and final_action.

Mathematical rule:
- Action order: NORMAL_COMPLETE < SAFE_COMPLETE < REFUSE
- min_required_action / max_allowed_action computed from:
  domain (optional), risk_category, op_risk, hard_violations_count, structured signals
- final_action derived from bounds; no inference from response text or disclaimer.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

# RiskCategory/OperationalRisk are used by callers; this module uses normalized strings.

# Reason codes (diagnostics first-class)
REASON_HARD_VIOLATIONS = "hard_violations"
REASON_RISK_CLEARLY_HARMFUL = "risk_clearly_harmful"
REASON_RISK_POTENTIALLY_HARMFUL = "risk_potentially_harmful"
REASON_RISK_SENSITIVE = "risk_sensitive"
REASON_RISK_MORALLY_NUANCED = "risk_morally_nuanced"
REASON_RISK_BENIGN = "risk_benign"
REASON_DOMAIN_REGULATED = "domain_regulated"
REASON_SAFE_COMPLETE_REQUIRED = "safe_complete_required"
REASON_SAFE_COMPLETE_ALLOWED = "safe_complete_allowed"
REASON_NORMAL_COMPLETE_REQUIRED = "normal_complete_required"
REASON_SENSITIVE_ALLOWED = "risk_sensitive_allowed"


# -----------------------------------------------------------------------------
# Action enum (ordine: N < S < R)
# -----------------------------------------------------------------------------


class Action(str, Enum):
    NORMAL_COMPLETE = "NORMAL_COMPLETE"
    SAFE_COMPLETE = "SAFE_COMPLETE"
    REFUSE = "REFUSE"


# -----------------------------------------------------------------------------
# Policy context (inputs)
# -----------------------------------------------------------------------------


@dataclass(frozen=True)
class PolicyContext:
    """
    Inputs per compute_action_bounds / decide_final_action.
    Value object immutabile: ogni variazione richiede una nuova istanza.
    """

    domain: str | None = None
    risk_category: Any = None  # RiskCategory enum or str
    op_risk: Any = None  # OperationalRisk enum or str
    hard_violations_count: int = 0
    # Structured signals (no text inference)
    misuse_plausibility: str = "LOW"  # LOW | MEDIUM | HIGH
    actionability_risk: str = "LOW"  # LOW | MEDIUM | HIGH
    intent_clarity: str = "HIGH"  # LOW | MEDIUM | HIGH
    # Optional: explicit dual-use/ambiguity (if already computed)
    has_ambiguity_or_dual_use: bool = False
    # Intent type for SENSITIVE split: factual|advice|support|explanation
    # (None = unknown -> conservative)
    intent_type: str | None = None
    # Risk-assessment passthrough (used by post-policy decision logic, not by compute_action_bounds)
    risk_score: float = 0.5
    intent_operational: bool = False
    # True if the active YAML overlay for this domain has sensitive=true.
    # Injected by controller.py via is_overlay_sensitive(); drives min_required=SAFE_COMPLETE
    # for POTENTIALLY_HARMFUL queries in sensitive domains (read from constitution, not hardcoded).
    overlay_sensitive: bool = False


# -----------------------------------------------------------------------------
# Policy bounds output
# -----------------------------------------------------------------------------


@dataclass(frozen=True)
class PolicyBounds:
    """
    Output di compute_action_bounds: min_required, max_allowed, reason_codes.
    Value object immutabile: ogni variazione richiede una nuova istanza.
    """

    min_required: Action
    max_allowed: Action
    reason_codes: list[str] = field(default_factory=list)


def _norm_domain(d: str | None) -> str | None:
    if d is None:
        return None
    v = (str(d).strip().lower() or "").strip()
    return v if v else None


def _norm_risk_category(rc: Any) -> str | None:
    if rc is None:
        return None
    if hasattr(rc, "value"):
        v = str(rc.value).strip().lower()
    else:
        v = (str(rc).strip().lower() or "").strip()
    if not v:
        return None
    valid = {"benign", "sensitive", "morally_nuanced", "potentially_harmful", "clearly_harmful"}
    return v if v in valid else None


def _norm_axis(axis: str | None) -> str:
    if axis is None:
        return "LOW"
    v = (str(axis).strip().upper() or "").strip()
    if v in ("LOW", "MEDIUM", "HIGH"):
        return v
    return "LOW"


def _op_risk_actionable_harm(op_risk: Any) -> bool:
    if op_risk is None:
        return False
    if hasattr(op_risk, "value"):
        return str(op_risk.value).upper() == "HIGH"
    return str(op_risk).strip().upper() == "HIGH"


def _has_ambiguity_or_dual_use(ctx: PolicyContext) -> bool:
    """
    Single source of truth: respect the value pre-computed by
    ``_build_policy_context_pre``. That upstream computation already considers
    the full picture (misuse_plausibility, actionability_risk, intent_operational,
    requested_instructions, harm_type) and produces a coherent boolean.

    The fallback "MEDIUM on either axis ⇒ True" was a defensive heuristic for
    legacy paths where the flag was not pre-computed. Today the flag is always
    set by ``_build_policy_context_pre``, so trusting it eliminates the
    inconsistency where a single MEDIUM axis without an operational signal
    triggered an over-governance escalation that the upstream computation
    had explicitly ruled out.

    Note: the safety implications of this change are bounded. When the user's
    intent is genuinely operational or requested_instructions=yes, the upstream
    computation correctly sets the flag to True (via the
    ``(misuse_plausibility in MEDIUM/HIGH and _has_operational_signal)`` clause
    in ``_build_policy_context_pre``). The cases that fall through to this
    relaxed False are exactly those where the intent estimator confirmed
    non-operational, non-harmful intent with no requested instructions —
    cases where forcing SAFE_COMPLETE only on a MEDIUM axis is over-governance.
    """
    return bool(ctx.has_ambiguity_or_dual_use)


def compute_action_bounds(ctx: PolicyContext) -> PolicyBounds:
    """
    Calcola min_required e max_allowed (e reason_codes) dalla policy formale.
    Single source of truth; nessuna inferenza da testo.
    """
    reason_codes: list[str] = []
    rc = _norm_risk_category(ctx.risk_category)
    op_risk_high = _op_risk_actionable_harm(ctx.op_risk)

    # (1) REFUSE REQUIRED
    if ctx.hard_violations_count > 0:
        reason_codes.append(REASON_HARD_VIOLATIONS)
        return PolicyBounds(min_required=Action.REFUSE, max_allowed=Action.REFUSE, reason_codes=reason_codes)
    if rc == "clearly_harmful":
        reason_codes.append(REASON_RISK_CLEARLY_HARMFUL)
        return PolicyBounds(min_required=Action.REFUSE, max_allowed=Action.REFUSE, reason_codes=reason_codes)
    if op_risk_high:
        reason_codes.append(REASON_RISK_CLEARLY_HARMFUL)  # actionable harm
        return PolicyBounds(min_required=Action.REFUSE, max_allowed=Action.REFUSE, reason_codes=reason_codes)

    # (1.5) HIGH actionability_risk → SAFE_COMPLETE (epistemic escalation, domain-agnostic).
    #        This is the sole epistemic escalation signal: if the user asks what to DO,
    #        provides personal resources/constraints/targets, or the output could directly
    #        influence a real-world decision, enforce responsible framing.
    #        Does NOT override REFUSE (hard violations, op_risk HIGH already returned above).
    actionability = _norm_axis(ctx.actionability_risk)
    if actionability == "HIGH" and ctx.hard_violations_count == 0 and not op_risk_high:
        reason_codes.append("safe_complete_required_high_actionability")
        return PolicyBounds(
            min_required=Action.SAFE_COMPLETE,
            max_allowed=Action.SAFE_COMPLETE,
            reason_codes=reason_codes,
        )

    # (2) SENSITIVE or MORALLY_NUANCED
    if rc in ("sensitive", "morally_nuanced"):
        if rc == "sensitive":
            reason_codes.append(REASON_RISK_SENSITIVE)
        else:
            reason_codes.append(REASON_RISK_MORALLY_NUANCED)
        if ctx.overlay_sensitive:
            reason_codes.append(REASON_DOMAIN_REGULATED)
        # Factual intent + no ambiguity/dual-use→allow NORMAL_COMPLETE
        # (reduce over-governance)but only for non-sensitive domains
        intent = (ctx.intent_type or "").strip().lower() if ctx.intent_type else None
        if intent == "factual" and not _has_ambiguity_or_dual_use(ctx) and not ctx.overlay_sensitive:
            reason_codes.append(REASON_SENSITIVE_ALLOWED)
            return PolicyBounds(
                min_required=Action.NORMAL_COMPLETE,
                max_allowed=Action.SAFE_COMPLETE,
                reason_codes=reason_codes,
            )
        reason_codes.append(REASON_SAFE_COMPLETE_REQUIRED)
        return PolicyBounds(
            min_required=Action.SAFE_COMPLETE,
            max_allowed=Action.SAFE_COMPLETE,
            reason_codes=reason_codes,
        )

    # (3) SAFE_COMPLETE ALLOWED / REQUIRED (gray zone): POTENTIALLY_HARMFUL
    if rc == "potentially_harmful":
        reason_codes.append(REASON_RISK_POTENTIALLY_HARMFUL)
        if ctx.overlay_sensitive and not ctx.intent_operational:
            intent = (ctx.intent_type or "").strip().lower() if ctx.intent_type else None
            # Esenzione fattuale: query educative/fattuali non richiedono SAFE_COMPLETE
            # anche in domini regolati — l'over-governance erode utilità.
            if intent == "factual" and not _has_ambiguity_or_dual_use(ctx):
                reason_codes.append(REASON_SAFE_COMPLETE_ALLOWED)
                return PolicyBounds(
                    min_required=Action.NORMAL_COMPLETE,
                    max_allowed=Action.SAFE_COMPLETE,
                    reason_codes=reason_codes,
                )
            reason_codes.append(REASON_DOMAIN_REGULATED)
            reason_codes.append(REASON_SAFE_COMPLETE_REQUIRED)
            return PolicyBounds(
                min_required=Action.SAFE_COMPLETE,
                max_allowed=Action.SAFE_COMPLETE,
                reason_codes=reason_codes,
            )
        reason_codes.append(REASON_SAFE_COMPLETE_ALLOWED)
        return PolicyBounds(
            min_required=Action.NORMAL_COMPLETE,
            max_allowed=Action.SAFE_COMPLETE,
            reason_codes=reason_codes,
        )

    # (4) NORMAL_COMPLETE REQUIRED (benign)
    if rc == "benign":
        reason_codes.append(REASON_RISK_BENIGN)
        reason_codes.append(REASON_NORMAL_COMPLETE_REQUIRED)
        return PolicyBounds(
            min_required=Action.NORMAL_COMPLETE,
            max_allowed=Action.NORMAL_COMPLETE,
            reason_codes=reason_codes,
        )

    # Fallback: insufficient / unknown risk_category -> conservative
    reason_codes.append(REASON_SAFE_COMPLETE_ALLOWED)
    return PolicyBounds(
        min_required=Action.NORMAL_COMPLETE,
        max_allowed=Action.SAFE_COMPLETE,
        reason_codes=reason_codes,
    )


def decide_final_action(ctx: PolicyContext) -> tuple[str, PolicyBounds, list[str]]:
    """
    Deriva final_action da bounds.
    - REFUSE required -> REFUSE
    - SAFE_COMPLETE required -> SAFE_COMPLETE
    - Gray zone (POTENTIALLY_HARMFUL): default NORMAL_COMPLETE to reduce FPs
    - Else NORMAL_COMPLETE

    Returns:
        (final_action_str, bounds, reason_codes)
    """
    bounds = compute_action_bounds(ctx)
    min_r, max_a = bounds.min_required, bounds.max_allowed
    if min_r == Action.REFUSE:
        return "REFUSE", bounds, bounds.reason_codes
    if min_r == Action.SAFE_COMPLETE:
        return "SAFE_COMPLETE", bounds, bounds.reason_codes
    # min_r == NORMAL_COMPLETE
    if max_a == Action.NORMAL_COMPLETE:
        return "NORMAL_COMPLETE", bounds, bounds.reason_codes
    # Gray zone: max_allowed >= SAFE_COMPLETE; default NORMAL_COMPLETE
    return "NORMAL_COMPLETE", bounds, bounds.reason_codes
