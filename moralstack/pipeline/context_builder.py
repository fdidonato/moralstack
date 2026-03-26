"""
Context Builder - Costruisce DelibContext per thin prompts e delta evaluation.

Summarization deterministica (no LLM) per ridurre costi.
Caching opzionale con chiave (request_id, draft_hash).
"""

from __future__ import annotations

import difflib
import re
from typing import Any

from moralstack.models.delib_context import DelibContext

# Limite token stimato per summary (~4 chars/token)
SUMMARY_MAX_CHARS = 480  # ~120 token
KEY_POINTS_MAX = 5
DELTA_MAX_BULLETS = 8


def _extract_sentences(text: str) -> list[str]:
    """Estrae frasi da testo (split su . ! ?)."""
    if not text or not text.strip():
        return []
    # Split su punteggiatura frase
    parts = re.split(r"[.!?]+", text.strip())
    return [p.strip() for p in parts if p.strip()]


def _truncate_to_chars(text: str, max_chars: int) -> str:
    """Tronca testo a max_chars, tagliando su parola."""
    if not text or len(text) <= max_chars:
        return text or ""
    truncated = text[: max_chars + 1].rsplit(maxsplit=1)[0]
    return truncated if truncated else text[:max_chars]


def _make_summary_compact(draft_text: str) -> str:
    """
    Summary deterministico del draft (~120 token).
    Prime frasi fino a SUMMARY_MAX_CHARS.
    """
    if not draft_text or not draft_text.strip():
        return ""
    sentences = _extract_sentences(draft_text)
    if not sentences:
        return _truncate_to_chars(draft_text.strip(), SUMMARY_MAX_CHARS)
    result: list[str] = []
    total = 0
    for s in sentences:
        if total + len(s) + 2 > SUMMARY_MAX_CHARS:
            break
        result.append(s)
        total += len(s) + 2
    return ". ".join(result) if result else _truncate_to_chars(draft_text.strip(), SUMMARY_MAX_CHARS)


def _extract_key_points(draft_text: str) -> list[str]:
    """
    Estrae key points come bullet (prime frasi significative).
    Deterministico, no LLM.
    """
    sentences = _extract_sentences(draft_text)
    # Filtra frasi troppo corte (< 10 chars) e prendi le prime N
    meaningful = [s for s in sentences if len(s) >= 10][:KEY_POINTS_MAX]
    return meaningful


def compute_delta(prev_text: str, new_text: str) -> list[str]:
    """
    Calcola change_log tra prev_text e new_text usando difflib.
    Ritorna lista di bullet che descrivono le modifiche.

    Args:
        prev_text: Testo draft precedente
        new_text: Testo draft nuovo

    Returns:
        Lista di stringhe (es. "Added: ...", "Removed: ...", "Changed: ...")
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
    Costruisce DelibContext per il ciclo corrente.

    Args:
        user_prompt: Prompt originale utente
        risk_result: RiskEstimation (o oggetto con score, risk_category, ecc.)
        domain: Dominio overlay (es. "financial")
        draft_text: Testo draft corrente
        prev_context: Contesto ciclo precedente (per delta, cycle>1)
        cycle: Numero ciclo (1-based)

    Returns:
        DelibContext popolato
    """
    ctx = DelibContext()

    ctx.user_prompt = user_prompt or ""
    ctx.draft_text_full = draft_text or ""
    ctx.draft_id = f"cycle_{cycle}"
    ctx.draft_summary_compact = _make_summary_compact(draft_text or "")
    ctx.key_points = _extract_key_points(draft_text or "")
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

    # Caveats/disclaimer: lasciati False; orchestrator può sovrascrivere se ha info
    ctx.safety_caveats_present = False
    ctx.citations_or_disclaimer_present = False

    return ctx
