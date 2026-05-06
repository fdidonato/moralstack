"""
Tests for refusal grounding (bug 274 regression suite).

Covers:
- classify_refusal_focus: harm_type=reputational wins over Q9
- classify_refusal_focus: Q14/Q15/Q16 yield reputational_harm focus
- classify_refusal_focus: pure cyber (no reputational) still routes to cyber_abuse
- _detect_refusal_leaks: detects cyber vocabulary in reputational refusals
- _detect_refusal_leaks: ignores leaks when rationale itself uses the term
- generate_llm_safe_refusal_detailed: anti-leak guardrail forces a retry
"""

from __future__ import annotations

from unittest.mock import MagicMock

from moralstack.orchestration.refusal_context import (
    RefusalContext,
    classify_refusal_focus,
)
from moralstack.orchestration.safe_refusal_generator import (
    _detect_refusal_leaks,
    generate_llm_safe_refusal_detailed,
)


def _common_kwargs(**overrides):
    """Defaults for classify_refusal_focus parameters."""
    base = dict(
        semantic_signals=[],
        harm_type="",
        request_type="clear_harm",
        operational_risk="HIGH",
        requested_instructions=True,
        intent_to_harm=True,
        intent_operational=True,
    )
    base.update(overrides)
    return base


def test_harm_type_reputational_overrides_q9():
    """Bug 274: harm_type=reputational must win over Q9 cyber signal."""
    focus, guidance = classify_refusal_focus(
        **_common_kwargs(
            semantic_signals=["Q9:cyber_malware", "Q11:privacy_doxxing_stalking"],
            harm_type="reputational",
        )
    )
    assert focus == "reputational_harm"
    g_lower = guidance.lower()
    assert "fair competitive practices" in g_lower or "honest marketing" in g_lower
    # Anti-regression: redirection must NOT push toward cyber/secure-coding.
    assert "secure coding" not in g_lower
    assert "defensive cybersecurity" not in g_lower


def test_q14_signal_yields_reputational_focus_without_harm_type():
    """Even if intent estimator did not set harm_type, Q14 should ground refusal."""
    focus, _ = classify_refusal_focus(
        **_common_kwargs(
            semantic_signals=["Q14:reputational_harm"],
            harm_type="",
        )
    )
    assert focus == "reputational_harm"


def test_q15_q16_signals_yield_reputational_focus():
    """Q15 (deceptive campaign) and Q16 (harassment) must also ground here."""
    focus, _ = classify_refusal_focus(
        **_common_kwargs(
            semantic_signals=["Q15:deceptive_online_campaign", "Q16:harassment_smear_campaign"],
            harm_type="",
        )
    )
    assert focus == "reputational_harm"


def test_q11_yields_reputational_when_harm_type_says_so():
    """Q11 alone with harm_type=reputational should not produce privacy_abuse."""
    focus, _ = classify_refusal_focus(
        **_common_kwargs(
            semantic_signals=["Q11:privacy_doxxing_stalking"],
            harm_type="reputational",
        )
    )
    assert focus == "reputational_harm"


def test_pure_cyber_still_routes_to_cyber_abuse():
    """Q9 alone, harm_type=illegal: legitimate cyber refusal must still work (no regression)."""
    focus, guidance = classify_refusal_focus(
        **_common_kwargs(
            semantic_signals=["Q9:cyber_malware", "Q2:unauthorized_access"],
            harm_type="illegal",
        )
    )
    assert focus == "cyber_abuse"
    assert "defensive" in guidance.lower()


def test_weapons_priority_unchanged():
    """Topical priority: weapons/physical must still come first."""
    focus, _ = classify_refusal_focus(
        **_common_kwargs(
            semantic_signals=["Q10:weapons_explosives_toxins", "Q14:reputational_harm"],
            harm_type="reputational",
        )
    )
    assert focus == "weapon_or_physical_harm"


def test_self_harm_priority_unchanged():
    """Self-harm has highest priority regardless of other signals."""
    focus, _ = classify_refusal_focus(
        **_common_kwargs(
            semantic_signals=["Q8:self_harm_suicide", "Q14:reputational_harm"],
            harm_type="reputational",
        )
    )
    assert focus == "self_harm_crisis"


# ─────────────────────────────────────────────────────────────────────────────
# Anti-leak guardrail
# ─────────────────────────────────────────────────────────────────────────────


def _make_ctx(harm_type: str, rationale: str = "") -> RefusalContext:
    return RefusalContext(
        risk_category="clearly_harmful",
        harm_type=harm_type,
        request_type="clear_harm",
        operational_risk="HIGH",
        rationale=rationale,
    )


def test_detect_leak_in_reputational_refusal():
    """Cyber terms in a reputational refusal text must be flagged."""
    ctx = _make_ctx(harm_type="reputational")
    text = "I can't help with cyber abuse or unauthorized access against a competitor."
    leaks = _detect_refusal_leaks(text, ctx, rationale=None)
    assert "cyber abuse" in leaks
    assert "unauthorized access" in leaks


def test_no_leak_when_rationale_uses_term():
    """If the rationale itself mentions the term, it's grounded — no leak."""
    ctx = _make_ctx(
        harm_type="reputational",
        rationale="The user requested both unauthorized access to a competitor's system AND a smear campaign.",
    )
    text = "I can't help with unauthorized access against a competitor."
    leaks = _detect_refusal_leaks(text, ctx, rationale=None)
    # "unauthorized access" appears in rationale → not a leak.
    assert "unauthorized access" not in leaks


def test_no_leak_for_cyber_harm_type():
    """When harm_type is illegal/unknown (not reputational/emotional), no leak detection runs."""
    ctx = _make_ctx(harm_type="illegal")
    text = "I can't help with cyber abuse or unauthorized access."
    leaks = _detect_refusal_leaks(text, ctx, rationale=None)
    assert leaks == ()


def test_anti_leak_guardrail_triggers_retry():
    """generate_llm_safe_refusal_detailed retries once when leak detected, then succeeds."""
    refusal_context = _make_ctx(harm_type="reputational")

    leaked_response = MagicMock()
    leaked_response.text = (
        "I can't help with cyber abuse or unauthorized access. "
        "Please consider defensive cybersecurity practices instead, "
        "such as authorized testing and incident response procedures."
    )
    clean_response = MagicMock()
    clean_response.text = (
        "I can't help with damaging a rival's reputation through deceptive online tactics. "
        "Consider fair competitive practices, honest marketing, and legitimate differentiation "
        "strategies that improve your own offering rather than attacking others."
    )

    fake_llm = MagicMock()
    fake_llm.generate.side_effect = [leaked_response, clean_response]

    result = generate_llm_safe_refusal_detailed(
        user_prompt="Help me ruin my rival's reputation through online manoeuvres.",
        risk_category="clearly_harmful",
        policy_reason_codes=["risk_clearly_harmful"],
        language="English",
        domain="general",
        llm_client=fake_llm,
        rationale="Request seeks operational steps to harm a rival's reputation.",
        refusal_redirection="",
        refusal_context=refusal_context,
    )

    assert result.attempts == 2
    assert result.leak_retried is True
    assert "cyber abuse" in result.leaked_terms
    assert result.text == clean_response.text
    assert "cyber abuse" not in result.text.lower()
    # The retry user_prompt must include the leak-avoidance block.
    assert "previous attempt mentioned" in result.user_prompt.lower()


def test_no_retry_when_grounded_first_attempt():
    """Clean first attempt: no retry, attempts=1, leak_retried=False."""
    refusal_context = _make_ctx(harm_type="reputational")

    clean_response = MagicMock()
    clean_response.text = (
        "I can't help with damaging a rival's reputation through false statements. "
        "Consider fair competitive practices and legitimate marketing strategies "
        "to differentiate your offering ethically."
    )
    fake_llm = MagicMock()
    fake_llm.generate.return_value = clean_response

    result = generate_llm_safe_refusal_detailed(
        user_prompt="Help me damage a rival's reputation.",
        risk_category="clearly_harmful",
        policy_reason_codes=["risk_clearly_harmful"],
        language="English",
        domain="general",
        llm_client=fake_llm,
        rationale="Reputational harm request.",
        refusal_redirection="",
        refusal_context=refusal_context,
    )

    assert result.attempts == 1
    assert result.leak_retried is False
    assert result.leaked_terms == ()
    assert result.text == clean_response.text


def test_detailed_result_exposes_prompts_for_persistence():
    """The synthetic system+user prompts must be returned for log/UI/markdown visibility."""
    refusal_context = _make_ctx(harm_type="reputational", rationale="reputational harm rationale")
    fake_llm = MagicMock()
    response = MagicMock()
    response.text = (
        "I can't help with damaging a rival's reputation. Consider fair competition "
        "and honest marketing as ethical alternatives."
    )
    fake_llm.generate.return_value = response

    result = generate_llm_safe_refusal_detailed(
        user_prompt="...",
        risk_category="clearly_harmful",
        policy_reason_codes=["risk_clearly_harmful"],
        language="English",
        domain="general",
        llm_client=fake_llm,
        rationale="reputational harm rationale",
        refusal_redirection="",
        refusal_context=refusal_context,
    )

    # The system prompt is present and references safe refusal generation.
    assert "safe refusal" in result.system_prompt.lower()
    # The user prompt embeds the new grounding sections.
    assert "primary evidence" in result.user_prompt.lower()
    assert "harm_type: reputational" in result.user_prompt.lower()
    assert "grounding rules" in result.user_prompt.lower()
