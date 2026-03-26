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
