"""
Tests for the reputational-harm signal cluster (Q14/Q15/Q16).

Covers:
- parse_risk_dict propagates the new fields from LLM JSON
- merge_mini_estimator_results carries the new keys from signal_data
- _calibrate_harm_signals_present forces harm_type=reputational and emits
  the Q14/Q15/Q16 signal labels
- the Q9 anti-leak path: presence of Q9 alongside Q14/Q15/Q16 yields
  harm_type=reputational (the bug 274 root cause)
"""

from __future__ import annotations

from moralstack.models.risk.calibration import (
    merge_mini_estimator_results,
    parse_risk_dict,
)


def _base_signal_data(**overrides):
    """Helper: minimal signal_data dict with all q* defaulting to 'no'."""
    base = {
        "q1_confidential": "no",
        "q2_unauthorized": "no",
        "q3_breach": "no",
        "q4_fraud": "no",
        "q5_physical": "no",
        "q6_illegal_items": "no",
        "q7_only_emotional": "no",
        "q8_self_harm_suicide": "no",
        "q9_cyber_malware": "no",
        "q10_weapons_explosives_toxins": "no",
        "q11_privacy_doxxing_stalking": "no",
        "q12_medical_harmful": "no",
        "q13_protected_class_targeting": "no",
        "q14_reputational_harm": "no",
        "q15_deceptive_online_campaign": "no",
        "q16_harassment_smear_campaign": "no",
        "domain_sensitivity": "MEDIUM",
    }
    base.update(overrides)
    return base


def _base_intent_data(**overrides):
    base = {
        "detected_language": "en",
        "intent_to_harm": "yes",
        "requested_instructions": "yes",
        "intent_operational": True,
        "request_type": "clear_harm",
        "harm_type": "reputational",
        "intent_clarity": "HIGH",
        "stated_personal_bias": "no",
        "seeks_norm_circumvention": "no",
        "rationale": "Request seeks operational steps to harm a rival's reputation through online maneuvers.",
    }
    base.update(overrides)
    return base


def _base_operational_data(**overrides):
    base = {
        "operational_risk": "HIGH",
        "risk_score": 0.85,
        "confidence": 0.9,
        "misuse_plausibility": "HIGH",
        "actionability_risk": "HIGH",
        "risk_policy_action": "DENY",
        "rationale": "High operational risk: request asks for execution-level guidance.",
    }
    base.update(overrides)
    return base


def test_parse_risk_dict_propagates_q14_q16():
    """parse_risk_dict surfaces q14/q15/q16 to RiskParseResult."""
    data = _base_signal_data(
        q14_reputational_harm="yes",
        q15_deceptive_online_campaign="yes",
        q16_harassment_smear_campaign="no",
    )
    data.update(_base_intent_data())
    data.update(_base_operational_data())

    parsed = parse_risk_dict(data)

    assert parsed.q14_reputational_harm is True
    assert parsed.q15_deceptive_online_campaign is True
    assert parsed.q16_harassment_smear_campaign is False


def test_merge_carries_reputational_cluster_keys():
    """merge_mini_estimator_results includes q14/q15/q16 from signal_data."""
    intent = _base_intent_data()
    signals = _base_signal_data(
        q14_reputational_harm="yes",
        q15_deceptive_online_campaign="yes",
        q16_harassment_smear_campaign="yes",
    )
    operational = _base_operational_data()

    merged = merge_mini_estimator_results(intent, signals, operational)

    assert merged["q14_reputational_harm"] == "yes"
    assert merged["q15_deceptive_online_campaign"] == "yes"
    assert merged["q16_harassment_smear_campaign"] == "yes"


def test_reputational_signals_yield_reputational_harm_type():
    """When the cluster fires, harm_type is 'reputational' even with no other harm signals."""
    data = _base_signal_data(q14_reputational_harm="yes")
    # Pass an intent with harm_type="" so calibration must derive it from signals.
    data.update(_base_intent_data(harm_type=""))
    data.update(_base_operational_data())

    parsed = parse_risk_dict(data)

    assert parsed.harm_type == "reputational"
    assert "Q14:reputational_harm" in parsed.signals


def test_q9_alongside_q14_does_not_become_illegal_harm_type():
    """Bug 274 regression: Q9 over-triggered + Q14 must yield harm_type=reputational, not illegal."""
    data = _base_signal_data(
        q9_cyber_malware="yes",  # over-triggered by signal-detector LLM
        q14_reputational_harm="yes",
        q15_deceptive_online_campaign="yes",
    )
    data.update(_base_intent_data(harm_type=""))
    data.update(_base_operational_data())

    parsed = parse_risk_dict(data)

    # Reputational priority over the generic "illegal" bucket means Q9 alone
    # cannot misclassify the harm_type.
    assert parsed.harm_type == "reputational"
    # All three labels should be present in the signal list for traceability.
    assert "Q9:cyber_malware" in parsed.signals
    assert "Q14:reputational_harm" in parsed.signals
    assert "Q15:deceptive_online_campaign" in parsed.signals


def test_weapons_still_wins_over_reputational():
    """Severity priority: weapons must still beat reputational."""
    data = _base_signal_data(
        q10_weapons_explosives_toxins="yes",
        q14_reputational_harm="yes",
    )
    data.update(_base_intent_data(harm_type=""))
    data.update(_base_operational_data())

    parsed = parse_risk_dict(data)

    assert parsed.harm_type == "weapons"
