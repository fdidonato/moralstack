"""
Unit tests for SAFE_COMPLETE policy: compute_action_bounds and decide_final_action.
No LLM calls; synthetic contexts only.
"""

import sys
from pathlib import Path

from moralstack.runtime.decision.safe_complete_policy import (
    REASON_DOMAIN_REGULATED,
    REASON_HARD_VIOLATIONS,
    REASON_NORMAL_COMPLETE_REQUIRED,
    REASON_RISK_BENIGN,
    REASON_RISK_CLEARLY_HARMFUL,
    REASON_RISK_MORALLY_NUANCED,
    REASON_RISK_POTENTIALLY_HARMFUL,
    REASON_RISK_SENSITIVE,
    REASON_SAFE_COMPLETE_ALLOWED,
    REASON_SAFE_COMPLETE_REQUIRED,
    REASON_SENSITIVE_ALLOWED,
    Action,
    PolicyContext,
    compute_action_bounds,
    decide_final_action,
)

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def test_benign_regulated_domain_normal_complete():
    """BENIGN + no hard violations + domain regulated -> must be NORMAL_COMPLETE
    (critical FP reducer)."""
    ctx = PolicyContext(
        domain="legal",
        risk_category="benign",
        op_risk=None,
        hard_violations_count=0,
        misuse_plausibility="LOW",
        actionability_risk="LOW",
    )
    bounds = compute_action_bounds(ctx)
    assert bounds.min_required == Action.NORMAL_COMPLETE
    assert bounds.max_allowed == Action.NORMAL_COMPLETE
    assert REASON_RISK_BENIGN in bounds.reason_codes
    assert REASON_NORMAL_COMPLETE_REQUIRED in bounds.reason_codes

    final_action, _, codes = decide_final_action(ctx)
    assert final_action == "NORMAL_COMPLETE"
    assert REASON_RISK_BENIGN in codes


def test_sensitive_regulated_domain_safe_complete_required():
    """SENSITIVE + regulated domain (overlay_sensitive=True) -> must be SAFE_COMPLETE."""
    ctx = PolicyContext(
        domain="healthcare",
        risk_category="sensitive",
        op_risk=None,
        hard_violations_count=0,
        overlay_sensitive=True,
    )
    bounds = compute_action_bounds(ctx)
    assert bounds.min_required == Action.SAFE_COMPLETE
    assert bounds.max_allowed == Action.SAFE_COMPLETE
    assert REASON_RISK_SENSITIVE in bounds.reason_codes
    assert REASON_DOMAIN_REGULATED in bounds.reason_codes
    assert REASON_SAFE_COMPLETE_REQUIRED in bounds.reason_codes

    final_action, _, _ = decide_final_action(ctx)
    assert final_action == "SAFE_COMPLETE"


def test_morally_nuanced_regulated_domain_safe_complete_required():
    """MORALLY_NUANCED + regulated domain -> must be SAFE_COMPLETE."""
    ctx = PolicyContext(
        domain="legal",
        risk_category="morally_nuanced",
        op_risk=None,
        hard_violations_count=0,
    )
    bounds = compute_action_bounds(ctx)
    assert bounds.min_required == Action.SAFE_COMPLETE
    assert bounds.max_allowed == Action.SAFE_COMPLETE
    assert REASON_RISK_MORALLY_NUANCED in bounds.reason_codes
    assert REASON_SAFE_COMPLETE_REQUIRED in bounds.reason_codes

    final_action, _, _ = decide_final_action(ctx)
    assert final_action == "SAFE_COMPLETE"


def test_sensitive_non_regulated_ambiguity_safe_complete_required():
    """SENSITIVE + non-regulated domain but structured ambiguity signal
    -> SAFE_COMPLETE required."""
    ctx = PolicyContext(
        domain="education",
        risk_category="sensitive",
        op_risk=None,
        hard_violations_count=0,
        misuse_plausibility="MEDIUM",
        actionability_risk="LOW",
        has_ambiguity_or_dual_use=True,
    )
    bounds = compute_action_bounds(ctx)
    assert bounds.min_required == Action.SAFE_COMPLETE
    assert bounds.max_allowed == Action.SAFE_COMPLETE
    assert REASON_SAFE_COMPLETE_REQUIRED in bounds.reason_codes

    final_action, _, _ = decide_final_action(ctx)
    assert final_action == "SAFE_COMPLETE"


def test_clearly_harmful_refuse():
    """CLEARLY_HARMFUL -> REFUSE."""
    ctx = PolicyContext(
        domain=None,
        risk_category="clearly_harmful",
        op_risk=None,
        hard_violations_count=0,
    )
    bounds = compute_action_bounds(ctx)
    assert bounds.min_required == Action.REFUSE
    assert bounds.max_allowed == Action.REFUSE
    assert REASON_RISK_CLEARLY_HARMFUL in bounds.reason_codes

    final_action, _, _ = decide_final_action(ctx)
    assert final_action == "REFUSE"


def test_hard_violations_refuse_even_benign():
    """hard_violations > 0 -> REFUSE even if risk would be benign."""
    ctx = PolicyContext(
        domain="legal",
        risk_category="benign",
        op_risk=None,
        hard_violations_count=1,
    )
    bounds = compute_action_bounds(ctx)
    assert bounds.min_required == Action.REFUSE
    assert bounds.max_allowed == Action.REFUSE
    assert REASON_HARD_VIOLATIONS in bounds.reason_codes

    final_action, _, _ = decide_final_action(ctx)
    assert final_action == "REFUSE"


def test_potentially_harmful_max_allowed_safe_final_default_normal():
    """POTENTIALLY_HARMFUL + no hard violations -> max_allowed >= SAFE_COMPLETE,
    final_action defaults NORMAL_COMPLETE."""
    ctx = PolicyContext(
        domain=None,
        risk_category="potentially_harmful",
        op_risk=None,
        hard_violations_count=0,
    )
    bounds = compute_action_bounds(ctx)
    assert bounds.min_required == Action.NORMAL_COMPLETE
    assert bounds.max_allowed == Action.SAFE_COMPLETE
    assert REASON_RISK_POTENTIALLY_HARMFUL in bounds.reason_codes
    assert REASON_SAFE_COMPLETE_ALLOWED in bounds.reason_codes

    final_action, _, _ = decide_final_action(ctx)
    assert final_action == "NORMAL_COMPLETE"


def test_sensitive_no_domain_no_ambiguity_normal_to_safe_range():
    """SENSITIVE (anche senza domain regolato/ambiguity) -> SAFE_COMPLETE REQUIRED (overlay)."""
    ctx = PolicyContext(
        domain="education",
        risk_category="sensitive",
        op_risk=None,
        hard_violations_count=0,
        misuse_plausibility="LOW",
        actionability_risk="LOW",
        has_ambiguity_or_dual_use=False,
    )
    bounds = compute_action_bounds(ctx)
    assert bounds.min_required == Action.SAFE_COMPLETE
    assert bounds.max_allowed == Action.SAFE_COMPLETE

    final_action, _, _ = decide_final_action(ctx)
    assert final_action == "SAFE_COMPLETE"


def test_sensitive_factual_no_ambiguity_allows_normal():
    """SENSITIVE + intent_type=factual + no ambiguity → min=NORMAL, max=SAFE
    (over-governance fix)."""
    from moralstack.runtime.decision.safe_complete_policy import REASON_SENSITIVE_ALLOWED

    ctx = PolicyContext(
        domain="education",
        risk_category="sensitive",
        op_risk=None,
        hard_violations_count=0,
        misuse_plausibility="LOW",
        actionability_risk="LOW",
        has_ambiguity_or_dual_use=False,
        intent_type="factual",
    )
    bounds = compute_action_bounds(ctx)
    assert bounds.min_required == Action.NORMAL_COMPLETE
    assert bounds.max_allowed == Action.SAFE_COMPLETE
    assert REASON_RISK_SENSITIVE in bounds.reason_codes
    assert REASON_SENSITIVE_ALLOWED in bounds.reason_codes

    final_action, _, _ = decide_final_action(ctx)
    assert final_action == "NORMAL_COMPLETE"


def test_sensitive_factual_regulated_domain_safe_complete():
    """SENSITIVE + factual + no ambiguity → NORMAL_COMPLETE (policy allows factual in regulated).
    Current policy: factual intent without ambiguity permits NORMAL_COMPLETE regardless of domain."""
    ctx = PolicyContext(
        domain="healthcare",
        risk_category="sensitive",
        op_risk=None,
        hard_violations_count=0,
        misuse_plausibility="LOW",
        actionability_risk="LOW",
        has_ambiguity_or_dual_use=False,
        intent_type="factual",
    )
    bounds = compute_action_bounds(ctx)
    assert bounds.min_required == Action.NORMAL_COMPLETE
    assert bounds.max_allowed == Action.SAFE_COMPLETE
    assert REASON_RISK_SENSITIVE in bounds.reason_codes
    assert REASON_SENSITIVE_ALLOWED in bounds.reason_codes


def test_sensitive_factual_with_ambiguity_still_safe_complete():
    """SENSITIVE + factual + ambiguity → SAFE_COMPLETE REQUIRED (safety preserved)."""
    ctx = PolicyContext(
        domain="education",
        risk_category="sensitive",
        op_risk=None,
        hard_violations_count=0,
        misuse_plausibility="MEDIUM",
        actionability_risk="LOW",
        has_ambiguity_or_dual_use=True,
        intent_type="factual",
    )
    bounds = compute_action_bounds(ctx)
    assert bounds.min_required == Action.SAFE_COMPLETE
    assert bounds.max_allowed == Action.SAFE_COMPLETE
    assert REASON_SAFE_COMPLETE_REQUIRED in bounds.reason_codes


def test_sensitive_advice_intent_safe_complete_required():
    """SENSITIVE + intent_type=advice → SAFE_COMPLETE REQUIRED (advisory path preserved)."""
    ctx = PolicyContext(
        domain=None,
        risk_category="sensitive",
        op_risk=None,
        hard_violations_count=0,
        misuse_plausibility="LOW",
        actionability_risk="LOW",
        has_ambiguity_or_dual_use=False,
        intent_type="advice",
    )
    bounds = compute_action_bounds(ctx)
    assert bounds.min_required == Action.SAFE_COMPLETE
    assert bounds.max_allowed == Action.SAFE_COMPLETE
    assert REASON_SAFE_COMPLETE_REQUIRED in bounds.reason_codes


def test_morally_nuanced_factual_no_ambiguity_allows_normal():
    """MORALLY_NUANCED + factual + no ambiguity → min=NORMAL, max=SAFE."""
    ctx = PolicyContext(
        domain=None,
        risk_category="morally_nuanced",
        op_risk=None,
        hard_violations_count=0,
        misuse_plausibility="LOW",
        actionability_risk="LOW",
        has_ambiguity_or_dual_use=False,
        intent_type="factual",
    )
    bounds = compute_action_bounds(ctx)
    assert bounds.min_required == Action.NORMAL_COMPLETE
    assert bounds.max_allowed == Action.SAFE_COMPLETE


# -----------------------------------------------------------------------------
# actionability_risk HIGH → SAFE_COMPLETE (epistemic escalation)
# -----------------------------------------------------------------------------


def test_high_actionability_forces_safe_complete_benign():
    """actionability_risk=HIGH + benign + no hard violations → SAFE_COMPLETE
    (epistemic escalation)."""
    ctx = PolicyContext(
        domain=None,
        risk_category="benign",
        op_risk=None,
        hard_violations_count=0,
        misuse_plausibility="LOW",
        actionability_risk="HIGH",
    )
    bounds = compute_action_bounds(ctx)
    assert bounds.min_required == Action.SAFE_COMPLETE
    assert bounds.max_allowed == Action.SAFE_COMPLETE
    assert "safe_complete_required_high_actionability" in bounds.reason_codes

    final_action, _, codes = decide_final_action(ctx)
    assert final_action == "SAFE_COMPLETE"
    assert "safe_complete_required_high_actionability" in codes


def test_high_actionability_forces_safe_complete_sensitive_factual():
    """actionability_risk=HIGH overrides factual intent relaxation for SENSITIVE."""
    ctx = PolicyContext(
        domain=None,
        risk_category="sensitive",
        op_risk=None,
        hard_violations_count=0,
        misuse_plausibility="LOW",
        actionability_risk="HIGH",
        has_ambiguity_or_dual_use=False,
        intent_type="factual",
    )
    bounds = compute_action_bounds(ctx)
    assert bounds.min_required == Action.SAFE_COMPLETE
    assert bounds.max_allowed == Action.SAFE_COMPLETE
    assert "safe_complete_required_high_actionability" in bounds.reason_codes


def test_high_actionability_does_not_override_refuse_hard_violations():
    """actionability_risk=HIGH + hard_violations > 0 → still REFUSE (normative wins)."""
    ctx = PolicyContext(
        domain=None,
        risk_category="benign",
        op_risk=None,
        hard_violations_count=1,
        misuse_plausibility="LOW",
        actionability_risk="HIGH",
    )
    bounds = compute_action_bounds(ctx)
    assert bounds.min_required == Action.REFUSE
    assert bounds.max_allowed == Action.REFUSE
    assert REASON_HARD_VIOLATIONS in bounds.reason_codes


def test_high_actionability_does_not_override_refuse_op_risk_high():
    """actionability_risk=HIGH + op_risk HIGH → still REFUSE (normative wins)."""
    ctx = PolicyContext(
        domain=None,
        risk_category="sensitive",
        op_risk="HIGH",
        hard_violations_count=0,
        misuse_plausibility="LOW",
        actionability_risk="HIGH",
    )
    bounds = compute_action_bounds(ctx)
    assert bounds.min_required == Action.REFUSE
    assert bounds.max_allowed == Action.REFUSE


def test_high_actionability_domain_agnostic():
    """actionability_risk=HIGH works identically with or without regulated domain."""
    for domain in [None, "financial", "education"]:
        ctx = PolicyContext(
            domain=domain,
            risk_category="benign",
            op_risk=None,
            hard_violations_count=0,
            misuse_plausibility="LOW",
            actionability_risk="HIGH",
        )
        bounds = compute_action_bounds(ctx)
        assert bounds.min_required == Action.SAFE_COMPLETE, f"Failed for domain={domain}"
        assert bounds.max_allowed == Action.SAFE_COMPLETE, f"Failed for domain={domain}"
        assert "safe_complete_required_high_actionability" in bounds.reason_codes


def test_medium_actionability_does_not_trigger_escalation():
    """actionability_risk=MEDIUM does NOT trigger epistemic escalation alone."""
    ctx = PolicyContext(
        domain=None,
        risk_category="benign",
        op_risk=None,
        hard_violations_count=0,
        misuse_plausibility="LOW",
        actionability_risk="MEDIUM",
    )
    bounds = compute_action_bounds(ctx)
    assert bounds.min_required == Action.NORMAL_COMPLETE
    assert bounds.max_allowed == Action.NORMAL_COMPLETE
    assert "safe_complete_required_high_actionability" not in bounds.reason_codes
