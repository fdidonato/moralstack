"""
Compact principle serialization for LLM prompts.

Reduces token consumption by removing verbose formatting (title, priority,
remediation, examples) while preserving id, level, and rule content.
"""

from __future__ import annotations

from typing import Any, List, Literal

from moralstack.constitution.schema import Principle


def format_principles_for_prompt(
    principles: list[dict[str, Any]],
    include_level: bool = True,
    style: Literal["compact", "verbose"] = "compact",
    max_rule_len: int | None = 180,
) -> str:
    """
    Format principles for inclusion in LLM prompts.

    Supports two styles:
    - compact: `{id} [H|S]: {rule}` (truncated, for domain agents)
    - verbose: numbered list with Title and Rule (for semantic matching)

    Args:
        principles: List of dicts with keys: id, title, rule, level
        include_level: Whether to include level marker (H/S or [HARD]/[SOFT])
        style: "compact" (single line, truncated) or "verbose" (multi-line, full)
        max_rule_len: Max chars for rule in compact style; None = no truncation

    Returns:
        Formatted string for prompt inclusion
    """
    if not principles:
        return ""

    lines: list[str] = []
    for i, p in enumerate(principles, 1):
        level = p.get("level") or "soft"
        rule = (p.get("rule") or "").strip()
        pid = p.get("id") or ""
        title = p.get("title") or ""

        if style == "compact":
            level_marker = "H" if level == "hard" else "S"
            if include_level:
                if max_rule_len is not None and len(rule) > max_rule_len:
                    rule = rule[:max_rule_len].rstrip() + "..."
                lines.append(f"{pid} [{level_marker}]: {rule}")
            else:
                if max_rule_len is not None and len(rule) > max_rule_len:
                    rule = rule[:max_rule_len].rstrip() + "..."
                lines.append(f"{pid}: {rule}")
        else:
            level_marker = "[HARD]" if level == "hard" else "[SOFT]"
            if include_level:
                lines.append(f"{i}. {pid} {level_marker}")
            else:
                lines.append(f"{i}. {pid}")
            lines.append(f"   Title: {title}")
            lines.append(f"   Rule: {rule}")
            lines.append("")

    return "\n".join(lines).rstrip()


def format_principles_compact(
    principles: List[Principle],
    max_rule_len: int = 180,
) -> str:
    """
    Compact serialization for LLM prompts.
    Preserves:
        - principle id
        - hard/soft level
        - rule (truncated)
    Removes:
        - markdown
        - title
        - priority
        - remediation
        - multiple examples
    """
    lines = []
    for p in principles:
        level = "H" if p.level == "hard" else "S"
        rule = (p.rule or "").strip()
        if len(rule) > max_rule_len:
            rule = rule[:max_rule_len].rstrip() + "..."
        lines.append(f"{p.id} [{level}]: {rule}")
    return "\n".join(lines)
