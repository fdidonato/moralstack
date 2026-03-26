"""
Tests for critic_prompt: ensure risk context is injected correctly.
"""

from __future__ import annotations

from moralstack.models.delib_context import DelibContext
from moralstack.prompts.critic_prompt import build_critic_prompt


def test_build_critic_prompt_full_includes_risk_assessment():
    """FULL mode prompt includes RISK ASSESSMENT with risk signals."""
    ctx = DelibContext(
        user_prompt="User question",
        draft_text_full="Draft answer.",
        risk_score=0.9,
        risk_category="CLEARLY_HARMFUL",
        operational_risk="HIGH",
        risk_policy_action="DENY",
        harm_type="physical_harm",
        misuse_plausibility="HIGH",
        intent_operational=True,
        intent_to_harm=True,
        requested_instructions=True,
    )

    prompt = build_critic_prompt(ctx, principles="P1: Test principle", mode="full")

    assert "RISK ASSESSMENT:" in prompt
    assert "risk_score=" in prompt
    assert "CLEARLY_HARMFUL" in prompt
    assert "risk_policy_action=DENY" in prompt
    assert "harm_type=physical_harm" in prompt
    assert "misuse_plausibility=HIGH" in prompt
    assert "intent_to_harm=true" in prompt
    assert "requested_instructions=true" in prompt


def test_build_critic_prompt_thin_uses_risk_signals():
    """THIN mode uses risk_signals from DelibContext."""
    ctx = DelibContext(
        user_prompt="User question",
        draft_text_full="Draft answer.",
        draft_summary_compact="Summary.",
        key_points=["Point A"],
        risk_score=0.7,
        risk_category="SENSITIVE",
        operational_risk="LOW",
        actionability_risk="HIGH",
        intent_operational=True,
        risk_policy_action="DENY",
    )

    prompt = build_critic_prompt(ctx, principles="P1: Test principle", mode="thin")

    # RISK CONTEXT block is present and includes risk_signals
    assert "RISK CONTEXT:" in prompt
    assert "risk_score=" in prompt
    assert "SENSITIVE" in prompt
    assert "risk_policy_action=DENY" in prompt
