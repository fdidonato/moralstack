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

    prompt = build_critic_prompt(ctx, principles="P1: Test principle")

    assert "RISK ASSESSMENT:" in prompt
    assert "risk_score=" in prompt
    assert "CLEARLY_HARMFUL" in prompt
    assert "risk_policy_action=DENY" in prompt
    assert "harm_type=physical_harm" in prompt
    assert "misuse_plausibility=HIGH" in prompt
    assert "intent_to_harm=true" in prompt
    assert "requested_instructions=true" in prompt
