"""
Characterization tests for constitution prompt formatter.

Documents current behavior of format_principles_compact and format_principles_for_prompt.
"""

from moralstack.constitution.prompt_formatter import (
    format_principles_compact,
    format_principles_for_prompt,
)
from moralstack.constitution.schema import Principle

# -----------------------------------------------------------------------------
# format_principles_for_prompt (shared formatter for DomainAgent, EnhancedDomainAgent,
# ConstitutionStore._format_principles_for_matching)
# -----------------------------------------------------------------------------


def test_format_principles_for_prompt_compact_single():
    """Compact style: single principle produces id [H|S]: rule
    (DomainAgent/EnhancedDomainAgent)."""
    principles = [
        {
            "id": "CORE.BALANCE.1",
            "title": "Balance",
            "rule": "Present limitations.",
            "level": "soft",
        }
    ]
    result = format_principles_for_prompt(principles, include_level=True, style="compact", max_rule_len=233)
    assert result == "CORE.BALANCE.1 [S]: Present limitations."


def test_format_principles_for_prompt_compact_truncates():
    """Compact style: rule truncated at max_rule_len (default 180)."""
    principles = [{"id": "X", "title": "T", "rule": "A" * 200, "level": "hard"}]
    result = format_principles_for_prompt(principles, include_level=True, style="compact", max_rule_len=50)
    assert "X [H]:" in result
    assert result.endswith("...")
    assert len(result.split(": ")[1]) <= 53


def test_format_principles_for_prompt_compact_hard_soft():
    """Compact style: level H for hard, S for soft."""
    hard = [{"id": "H.1", "title": "T", "rule": "R", "level": "hard"}]
    soft = [{"id": "S.1", "title": "T", "rule": "R", "level": "soft"}]
    assert "[H]:" in format_principles_for_prompt(hard, style="compact")
    assert "[S]:" in format_principles_for_prompt(soft, style="compact")


def test_format_principles_for_prompt_verbose_matching():
    """Verbose style: numbered list with Title and Rule (ConstitutionStore matching)."""
    principles = [
        {"id": "CORE.1", "title": "Balance", "rule": "Present limitations.", "level": "hard"},
        {"id": "CORE.2", "title": "Transparency", "rule": "Disclose sources.", "level": "soft"},
    ]
    result = format_principles_for_prompt(principles, include_level=True, style="verbose", max_rule_len=None)
    expected = """1. CORE.1 [HARD]
   Title: Balance
   Rule: Present limitations.

2. CORE.2 [SOFT]
   Title: Transparency
   Rule: Disclose sources."""
    assert result == expected


def test_format_principles_for_prompt_empty():
    """Empty list returns empty string."""
    assert format_principles_for_prompt([]) == ""


def test_format_principles_for_prompt_include_level_false_compact():
    """Compact with include_level=False omits level marker."""
    principles = [{"id": "X", "title": "T", "rule": "R", "level": "hard"}]
    result = format_principles_for_prompt(principles, include_level=False, style="compact")
    assert result == "X: R"
    assert "[H]" not in result


# -----------------------------------------------------------------------------
# format_principles_compact (Principle objects, legacy)
# -----------------------------------------------------------------------------


def test_format_principles_compact_single():
    """Single principle produces expected string format."""
    p = Principle(
        id="CORE.BALANCE.1",
        level="soft",
        priority=78,
        title="Balance",
        rule="Present limitations and counterarguments.",
    )
    result = format_principles_compact([p])
    assert "CORE.BALANCE.1" in result
    assert "[S]:" in result
    assert "Present limitations" in result


def test_format_principles_compact_truncates_long_rule():
    """Rule longer than max_rule_len is truncated with '...'."""
    long_rule = "A" * 200
    p = Principle(
        id="X",
        level="hard",
        priority=50,
        title="T",
        rule=long_rule,
    )
    result = format_principles_compact([p], max_rule_len=50)
    assert "X [H]:" in result
    assert result.endswith("...")
    assert len(result.split(": ")[1]) <= 53  # 50 + "..."


def test_format_principles_compact_hard_soft():
    """Level hard maps to H, soft maps to S."""
    hard_p = Principle(
        id="HARD.1",
        level="hard",
        priority=50,
        title="T",
        rule="Rule",
    )
    soft_p = Principle(
        id="SOFT.1",
        level="soft",
        priority=50,
        title="T",
        rule="Rule",
    )
    hard_result = format_principles_compact([hard_p])
    soft_result = format_principles_compact([soft_p])
    assert "[H]:" in hard_result
    assert "[S]:" in soft_result
