"""
Characterization tests for prompts _common module.

Documents current behavior of build_thin_context_sections.
"""

from moralstack.models.delib_context import DelibContext
from moralstack.prompts._common import build_thin_context_sections


def test_build_thin_context_sections_empty():
    """Empty DelibContext produces sections with (no summary), (none extracted)."""
    ctx = DelibContext()
    result = build_thin_context_sections(ctx)
    assert result["response_summary"] == "(no summary)"
    assert result["key_points"] == "- (none extracted)"
    # Default risk_score=0.5 yields risk_signals (characterization of current behavior)
    assert result["risk_signals"].startswith("risk_score=0.50")
    assert result["change_log_section"] == ""


def test_build_thin_context_sections_with_data():
    """DelibContext with draft, risk, change_log produces expected structure."""
    ctx = DelibContext(
        draft_summary_compact="Summary of the response.",
        key_points=["Point A", "Point B"],
        risk_score=0.6,
        risk_category="SENSITIVE",
        operational_risk="LOW",
        actionability_risk="HIGH",
        intent_operational=True,
        change_log=["Added: new section", "Removed: old part"],
    )
    result = build_thin_context_sections(ctx)
    assert result["response_summary"] == "Summary of the response."
    assert "- Point A" in result["key_points"]
    assert "- Point B" in result["key_points"]
    assert "risk_score=" in result["risk_signals"]
    assert "SENSITIVE" in result["risk_signals"]
    assert "CHANGES FROM PREVIOUS DRAFT" in result["change_log_section"]
    assert "Added: new section" in result["change_log_section"]
