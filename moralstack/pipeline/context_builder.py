"""
Context Builder - Builds DelibContext and delta evaluation.

Deterministic summarization (no LLM) to reduce costs.
Optional caching keyed by (request_id, draft_hash).
"""

from __future__ import annotations

import difflib
from typing import Any

from moralstack.models.delib_context import DelibContext

DELTA_MAX_BULLETS = 8


def _truncate_to_chars(text: str, max_chars: int) -> str:
    """Truncate text to max_chars, cutting at a word boundary."""
    if not text or len(text) <= max_chars:
        return text or ""
    truncated = text[: max_chars + 1].rsplit(maxsplit=1)[0]
    return truncated if truncated else text[:max_chars]


def compute_delta(prev_text: str, new_text: str) -> list[str]:
    """
    Computes the change_log between prev_text and new_text using difflib.
    Returns a list of bullets describing the changes.

    Args:
        prev_text: Previous draft text
        new_text: New draft text

    Returns:
        List of strings (e.g. "Added: ...", "Removed: ...", "Changed: ...")
    """
    if not prev_text and not new_text:
        return []
    if not prev_text:
        summary = _truncate_to_chars(new_text.strip(), 200)
        return [f"Added: {summary}..."] if len(new_text) > 200 else [f"Added: {new_text.strip()}"]
    if not new_text:
        return ["Removed: entire draft"]

    prev_lines = prev_text.splitlines()
    new_lines = new_text.splitlines()
    differ = difflib.unified_diff(prev_lines, new_lines, lineterm="", n=0)
    diff_lines = list(differ)

    added: list[str] = []
    removed: list[str] = []
    for line in diff_lines:
        if line.startswith("+") and not line.startswith("+++"):
            content = line[1:].strip()
            if content and len(content) < 100:
                added.append(content)
            elif content:
                added.append(content[:97] + "...")
        elif line.startswith("-") and not line.startswith("---"):
            content = line[1:].strip()
            if content and len(content) < 100:
                removed.append(content)
            elif content:
                removed.append(content[:97] + "...")

    bullets: list[str] = []
    for a in added[:3]:
        bullets.append(f"Added: {a}")
    for r in removed[:3]:
        bullets.append(f"Removed: {r}")
    if len(added) > 3 or len(removed) > 3:
        bullets.append(f"... ({len(added)} additions, {len(removed)} removals)")

    return bullets[:DELTA_MAX_BULLETS]


def build_context(
    user_prompt: str,
    risk_result: Any,
    domain: str | None,
    draft_text: str,
    prev_context: DelibContext | None = None,
    cycle: int = 1,
) -> DelibContext:
    """
    Builds the DelibContext for the current cycle.

    Args:
        user_prompt: Original user prompt
        risk_result: RiskEstimation (or an object with score, risk_category, etc.)
        domain: Overlay domain (e.g. "financial")
        draft_text: Current draft text
        prev_context: Previous cycle context (for delta, cycle>1)
        cycle: Cycle number (1-based)

    Returns:
        Populated DelibContext
    """
    ctx = DelibContext()

    ctx.user_prompt = user_prompt or ""
    ctx.draft_text_full = draft_text or ""
    ctx.draft_id = f"cycle_{cycle}"
    ctx.domain_overlay = (domain or "").strip()
    if not ctx.domain_overlay and risk_result is not None:
        detected = getattr(risk_result, "detected_domain", None)
        if detected and isinstance(detected, str) and detected.strip():
            ctx.domain_overlay = detected.strip()

    # Risk signals
    if risk_result is not None:
        ctx.risk_score = getattr(risk_result, "score", 0.5)
        rc = getattr(risk_result, "risk_category", None)
        ctx.risk_category = rc.value if rc and hasattr(rc, "value") else (str(rc or ""))
        op = getattr(risk_result, "operational_risk", None)
        ctx.operational_risk = op.value if op and hasattr(op, "value") else (str(op or ""))
        ctx.intent_operational = getattr(risk_result, "intent_operational", False)
        ar = getattr(risk_result, "actionability_risk", None)
        ctx.actionability_risk = ar.value if ar and hasattr(ar, "value") else (str(ar or ""))
        ctx.detected_language = getattr(risk_result, "detected_language", "") or ""
        ctx.risk_policy_action = getattr(risk_result, "risk_policy_action", "") or ""
        ctx.harm_type = getattr(risk_result, "harm_type", "") or ""
        mp = getattr(risk_result, "misuse_plausibility", None)
        ctx.misuse_plausibility = mp.value if mp and hasattr(mp, "value") else (str(mp or "") if mp is not None else "")
        ctx.intent_to_harm = bool(getattr(risk_result, "intent_to_harm", False))
        ctx.requested_instructions = bool(getattr(risk_result, "requested_instructions", False))

    # Delta per cycle > 1
    if prev_context and cycle > 1 and prev_context.draft_text_full:
        ctx.change_log = compute_delta(prev_context.draft_text_full, draft_text or "")

    # Caveats/disclaimer: left False; the orchestrator may override if it has the info
    ctx.safety_caveats_present = False
    ctx.citations_or_disclaimer_present = False

    return ctx
