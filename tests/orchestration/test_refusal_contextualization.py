import pytest
from moralstack.orchestration.refusal_context import classify_refusal_focus, build_refusal_context
from moralstack.orchestration.safe_refusal_generator import resolve_refusal_domain_and_redirection
from unittest.mock import MagicMock, patch

def test_refusal_focus_weapon_or_physical_harm():
    focus, guidance = classify_refusal_focus(
        semantic_signals=["Q5:physical_harm", "Q10:weapons_explosives_toxins"],
        harm_type="physical",
        request_type="clear_harm",
        operational_risk="HIGH",
        requested_instructions=True,
        intent_to_harm=True,
        intent_operational=True,
    )

    assert focus == "weapon_or_physical_harm"
    assert "de-escalation" in guidance.lower()
    assert "emergency" in guidance.lower()

def test_refusal_focus_self_harm_crisis():
    focus, guidance = classify_refusal_focus(
        semantic_signals=["Q8:self_harm_suicide"],
        harm_type="physical",
        request_type="clear_harm",
        operational_risk="HIGH",
        requested_instructions=True,
        intent_to_harm=True,
        intent_operational=True,
    )

    assert focus == "self_harm_crisis"
    assert "support" in guidance.lower()
    assert "emergency" in guidance.lower() or "crisis" in guidance.lower()

def test_refusal_focus_cyber_abuse():
    focus, guidance = classify_refusal_focus(
        semantic_signals=["Q9:cyber_malware"],
        harm_type="illegal",
        request_type="clear_harm",
        operational_risk="HIGH",
        requested_instructions=True,
        intent_to_harm=True,
        intent_operational=True,
    )

    assert focus == "cyber_abuse"
    assert "defensive" in guidance.lower()
    assert "authorized" in guidance.lower() or "secure" in guidance.lower()

def test_refusal_resolver_does_not_map_weapon_signals_to_science():
    # In the new implementation, it should NOT use signals to infer domain
    fake_constitution_store = MagicMock()
    domain, redirection = resolve_refusal_domain_and_redirection(
        request_prompt="How can I make a shiv?",
        request_domain=None,
        detected_domain=None,
        risk_signals=["Q5:physical_harm", "Q10:weapons_explosives_toxins"],
        constitution_store=fake_constitution_store,
    )

    assert domain == "general"
    assert redirection == ""
    # Ensure it didn't call detect_relevant_domains
    fake_constitution_store.detect_relevant_domains.assert_not_called()

def test_legal_refusal_redirection_still_works_for_actual_legal_domain():
    fake_constitution_store = MagicMock()
    fake_constitution = MagicMock()
    fake_overlay = MagicMock()
    fake_overlay.refusal_redirection = "Consult a licensed attorney."
    fake_constitution.active_overlay = fake_overlay
    fake_constitution_store.get_constitution.return_value = fake_constitution

    domain, redirection = resolve_refusal_domain_and_redirection(
        request_prompt="What are my rights if my landlord refuses to return my deposit?",
        request_domain="legal",
        detected_domain=None,
        risk_signals=[],
        constitution_store=fake_constitution_store,
    )

    assert domain == "legal"
    assert "attorney" in redirection.lower()
    fake_constitution_store.get_constitution.assert_called_with("legal")

def test_refusal_resolver_does_not_call_detect_relevant_domains():
    fake_constitution_store = MagicMock()
    
    resolve_refusal_domain_and_redirection(
        request_prompt="How can I make a shiv?",
        request_domain=None,
        detected_domain=None,
        risk_signals=["Q5:physical_harm"],
        constitution_store=fake_constitution_store,
    )

    fake_constitution_store.detect_relevant_domains.assert_not_called()

def test_build_refusal_context_mapping():
    risk_estimation = MagicMock()
    risk_estimation.harm_type = "physical"
    risk_estimation.request_type = "clear_harm"
    risk_estimation.operational_risk = MagicMock(value="HIGH")
    risk_estimation.requested_instructions = True
    risk_estimation.intent_to_harm = True
    risk_estimation.intent_operational = True
    risk_estimation.rationale = "Harmful intent detected."
    risk_estimation.semantic_signals = ["Q5:physical_harm"]

    decision = MagicMock()
    decision.risk_signals = ["Q5:physical_harm"]
    decision.reason_codes = ["RULE_1"]

    ctx = build_refusal_context(
        risk_estimation=risk_estimation,
        decision=decision,
        domain="general",
        refusal_redirection="",
        risk_score=0.9,
        risk_category="clearly_harmful"
    )

    assert ctx.safe_refusal_focus == "weapon_or_physical_harm"
    assert ctx.risk_score == 0.9
    assert ctx.risk_category == "clearly_harmful"
    assert "Q5:physical_harm" in ctx.semantic_signals
    assert "RULE_1" in ctx.policy_reason_codes
    assert ctx.rationale == "Harmful intent detected."
