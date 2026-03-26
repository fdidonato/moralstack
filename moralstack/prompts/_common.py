"""
Costanti e helper condivisi per i prompt LLM.
Riduce duplicazione tra critic, perspectives, simulator, hindsight.
"""

from __future__ import annotations

from typing import TypedDict

from moralstack.models.delib_context import DelibContext

# Costante standard per chiusura prompt JSON
OUTPUT_JSON_ONLY = "Output ONLY valid JSON."


class ThinContextSections(TypedDict):
    """Sezioni contesto per thin mode (summary, key_points, risk_signals, change_log)."""

    response_summary: str
    key_points: str
    risk_signals: str
    change_log_section: str


def build_thin_context_sections(context: DelibContext) -> ThinContextSections:
    """
    Costruisce le sezioni di contesto condivise per i prompt thin mode.

    Usato da critic, perspectives, simulator, hindsight per evitare
    duplicazione della logica summary/key_points/rischio/change_log.

    Args:
        context: DelibContext con draft, risk signals, change_log

    Returns:
        Dict con response_summary, key_points, risk_signals, change_log_section
    """
    summary = context.draft_summary_compact or "(no summary)"
    key_points_str = "\n".join(f"- {p}" for p in (context.key_points or [])[:5])
    if not key_points_str:
        key_points_str = "- (none extracted)"
    risk_signals = context.get_risk_signals_str() or "none"
    change_log_section = ""
    if context.change_log:
        change_log_section = "CHANGES FROM PREVIOUS DRAFT:\n" + "\n".join(f"- {c}" for c in context.change_log[:5])
    return ThinContextSections(
        response_summary=summary,
        key_points=key_points_str,
        risk_signals=risk_signals,
        change_log_section=change_log_section,
    )
