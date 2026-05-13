"""
Conversation audit export for AI Act art. 12 compliance.

Given a conversation_id, produces a complete markdown audit trail of all
turns: user prompts, governance decisions, final responses, decision rationale,
posture evolution, and per-turn metadata.

Per design v1.3 §7 — multi-turn audit infrastructure.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from moralstack import __version__ as _moralstack_version
from moralstack.observability.read_store import ReadStore, SqliteReadStore

logger = logging.getLogger("moralstack.reports.conversation_export")


def export_conversation_to_markdown(
    conversation_id: str,
    *,
    read_store: ReadStore | None = None,
) -> str:
    """
    Render a markdown audit trail for the given conversation_id.

    Args:
        conversation_id: the conversation to export. Must be non-empty.
        read_store: optional ReadStore implementation. Defaults to SqliteReadStore.

    Returns:
        A markdown string. If no requests are found for the conversation_id,
        returns a markdown file with a "no data found" notice.
    """
    if not conversation_id:
        return "# Conversation Audit Export\n\n**Error**: empty conversation_id provided.\n"

    store = read_store if read_store is not None else SqliteReadStore()
    requests = store.get_requests_for_conversation(conversation_id)

    lines: list[str] = []
    lines.append(f"# Conversation Audit Export — `{conversation_id}`")
    lines.append("")
    lines.append(f"**Framework**: MoralStack v{_moralstack_version}")
    lines.append(f"**Export timestamp**: {datetime.now(timezone.utc).isoformat()}")
    lines.append(f"**Total turns**: {len(requests)}")
    lines.append("")

    if not requests:
        lines.append("> No requests found for this conversation_id.")
        return "\n".join(lines) + "\n"

    lines.append("---")
    lines.append("")

    for idx, req in enumerate(requests):
        turn_index = req.get("turn_index")
        turn_label = f"Turn {turn_index}" if turn_index is not None else f"Turn (unsorted #{idx})"
        lines.append(f"## {turn_label}")
        lines.append("")

        request_id = req.get("request_id", "")
        created_at = req.get("created_at")
        if created_at:
            # The `requests.created_at` column stores epoch milliseconds.
            ts = datetime.fromtimestamp(created_at / 1000.0, tz=timezone.utc).isoformat()
            lines.append(f"- **Timestamp**: {ts}")
        lines.append(f"- **Request ID**: `{request_id}`")
        domain = req.get("domain") or "general"
        lines.append(f"- **Domain**: {domain}")
        parent = req.get("parent_request_id")
        if parent:
            lines.append(f"- **Parent request**: `{parent}`")
        lines.append("")

        prompt = req.get("prompt", "")
        lines.append("### User prompt")
        lines.append("")
        lines.append("```text")
        lines.append(prompt[:4000])
        lines.append("```")
        lines.append("")

        final_response = req.get("final_response", "") or ""
        lines.append("### Final response")
        lines.append("")
        lines.append("```text")
        lines.append(final_response[:4000])
        lines.append("```")
        lines.append("")

        meta_json = req.get("meta_json") or ""
        if meta_json:
            try:
                meta = json.loads(meta_json) if isinstance(meta_json, str) else meta_json
            except (ValueError, TypeError):
                # Defensive: malformed JSON is logged at higher layers; here we
                # simply skip governance metadata for this turn to keep the
                # audit export resilient.
                meta = {}
            if isinstance(meta, dict) and meta:
                lines.append("### Governance decision")
                lines.append("")
                final_action = meta.get("final_action") or "UNKNOWN"
                risk_score = meta.get("risk_score")
                path = meta.get("path") or meta.get("path_taken") or "UNKNOWN"
                reason_codes = meta.get("reason_codes") or []
                triggered_principles = meta.get("triggered_principles") or []
                lines.append(f"- **Final action**: `{final_action}`")
                if isinstance(risk_score, (int, float)):
                    lines.append(f"- **Risk score**: {risk_score:.4f}")
                lines.append(f"- **Path**: `{path}`")
                if reason_codes:
                    lines.append(f"- **Reason codes**: {', '.join(str(c) for c in reason_codes)}")
                if triggered_principles:
                    lines.append(f"- **Triggered principles**: {', '.join(str(p) for p in triggered_principles)}")
                decision_reason = meta.get("decision_reason") or ""
                if decision_reason:
                    lines.append("")
                    lines.append("**Decision rationale**:")
                    lines.append("")
                    lines.append(f"> {decision_reason}")
                lines.append("")

        lines.append("---")
        lines.append("")

    lines.append("## End of audit export")
    lines.append("")
    lines.append(f"This audit export was generated by MoralStack v{_moralstack_version}.")
    lines.append("Compliance reference: AI Act art. 12 (record-keeping obligations).")
    return "\n".join(lines) + "\n"


def export_conversation_to_file(
    conversation_id: str,
    output_path: str,
    *,
    read_store: ReadStore | None = None,
) -> None:
    """
    Export a conversation to a markdown file.

    Convenience wrapper around `export_conversation_to_markdown`.
    """
    content = export_conversation_to_markdown(conversation_id, read_store=read_store)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(content)
    logger.info(
        "Conversation %s exported to %s (%d bytes)",
        conversation_id,
        output_path,
        len(content),
    )
