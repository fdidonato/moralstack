"""
Test SAFE_COMPLETE gating: should_use_safe_complete e apply_safe_complete_gating.
Downgrade a NORMAL_COMPLETE quando dominio non regolato e segnali bassi.
"""

from moralstack.models.risk import RiskPolicyAction
from moralstack.orchestration.safe_complete_gating import (
    apply_safe_complete_gating,
    should_use_safe_complete,
)
from moralstack.orchestration.types import Decision


def test_cybersecurity_high_signals_keep_safe_complete():
    """domain=cybersecurity + misuse_plausibility=HIGH and actionability_risk=HIGH
    => SAFE_COMPLETE TRUE."""
    use = should_use_safe_complete(
        domain="cybersecurity",
        risk_policy_action=RiskPolicyAction.ALLOW,
        misuse_plausibility="HIGH",
        actionability_risk="HIGH",
        has_hard_violations=False,
    )
    assert use is True
    decision = Decision(
        final_action="SAFE_COMPLETE",
        path="DELIBERATIVE_PATH",
        intent_clarity="LOW",
        misuse_plausibility="HIGH",
        actionability_risk="HIGH",
        triggered_principles=[],
        hard_violations=[],
        risk_signals=[],
    )

    class Req:
        def get_domain(self):
            return "cybersecurity"

    class Risk:
        risk_policy_action = RiskPolicyAction.ALLOW

    out = apply_safe_complete_gating(decision, Req(), Risk())
    assert out.final_action == "SAFE_COMPLETE"


def test_refuse_unchanged():
    """REFUSE non viene mai modificato dal gating."""
    decision = Decision(
        final_action="REFUSE",
        path="FAST_PATH",
        intent_clarity="LOW",
        misuse_plausibility="HIGH",
        actionability_risk="HIGH",
        triggered_principles=[],
        hard_violations=["hard"],
        risk_signals=[],
    )

    class Req:
        def get_domain(self):
            return None

    class Risk:
        risk_policy_action = RiskPolicyAction.DENY

    out = apply_safe_complete_gating(decision, Req(), Risk())
    assert out.final_action == "REFUSE"


def test_hard_violations_gating_no_op_preserves_safe_complete():
    """T2f (must fail today): §1b -- apply_safe_complete_gating with a
    SAFE_COMPLETE decision carrying non-empty hard_violations, AND
    downgrade-qualifying signals (domain not regulated, overlay_sensitive
    False, misuse/actionability below MEDIUM -- i.e. exactly the profile
    that would otherwise trigger the SC->NORMAL_COMPLETE relabel at
    safe_complete_gating.py:175-184), returns the decision UNCHANGED. A
    post-decision gate must never be able to relax an action that carries an
    unresolved hard violation (PROJECT_SPEC §5.3). This is the only test
    pinning "no longer relabels" independently of the delivery-point guard
    (T2d/T5b are end-to-end and would still pass if this mechanism alone
    were broken, per plan §1b / T2f rationale)."""
    decision = Decision(
        final_action="SAFE_COMPLETE",
        path="DELIBERATIVE_PATH",
        intent_clarity="HIGH",
        misuse_plausibility="LOW",
        actionability_risk="LOW",
        triggered_principles=["CORE.PRIV.1"],
        hard_violations=["CORE.PRIV.1"],
        risk_signals=[],
        reason_codes=["hard_violation_downgraded_to_safe_complete"],
    )

    class Req:
        def get_domain(self):
            return None

    class Risk:
        risk_policy_action = RiskPolicyAction.ALLOW
        risk_category = None

    out = apply_safe_complete_gating(decision, Req(), Risk(), overlay_sensitive=False)
    assert out.final_action == "SAFE_COMPLETE"
    assert out.hard_violations == ["CORE.PRIV.1"]
    assert out is decision


def test_downgrade_to_normal_complete_when_signals_low_and_no_hard_violations():
    """Positive gate: with EMPTY hard_violations and non-qualifying signals
    (misuse/actionability below MEDIUM, overlay_sensitive False,
    risk_policy_action ALLOW, risk_category not sensitive/morally_nuanced),
    apply_safe_complete_gating still performs the SC->NORMAL_COMPLETE relabel
    (safe_complete_gating.py:175-184). Pins the branch the gate exists to
    produce, which the §1b no-op must not disturb: §1b returns early only on
    non-empty hard_violations (:99-100), so this path must stay reachable."""
    decision = Decision(
        final_action="SAFE_COMPLETE",
        path="DELIBERATIVE_PATH",
        intent_clarity="HIGH",
        misuse_plausibility="LOW",
        actionability_risk="LOW",
        triggered_principles=["CORE.EDU.1"],
        hard_violations=[],
        risk_signals=[],
    )

    class Req:
        def get_domain(self):
            return "education"

    class Risk:
        risk_policy_action = RiskPolicyAction.ALLOW
        risk_category = None

    out = apply_safe_complete_gating(decision, Req(), Risk(), overlay_sensitive=False)
    assert out.final_action == "NORMAL_COMPLETE"
    assert out is not decision
    assert out.hard_violations == []
    assert out.triggered_principles == ["CORE.EDU.1"]
    assert out.path == "DELIBERATIVE_PATH"


def test_normal_complete_unchanged():
    """NORMAL_COMPLETE non viene modificato."""
    decision = Decision(
        final_action="NORMAL_COMPLETE",
        path="FAST_PATH",
        intent_clarity="HIGH",
        misuse_plausibility="LOW",
        actionability_risk="LOW",
        triggered_principles=[],
        hard_violations=[],
        risk_signals=[],
    )

    class Req:
        def get_domain(self):
            return "education"

    class Risk:
        risk_policy_action = RiskPolicyAction.ALLOW

    out = apply_safe_complete_gating(decision, Req(), Risk())
    assert out.final_action == "NORMAL_COMPLETE"
