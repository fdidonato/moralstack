"""
Conversation audit export for AI Act art. 12 compliance.

Given a conversation_id, produces a complete markdown audit trail of all
turns: user prompts, governance decisions, final responses, decision rationale,
posture evolution, ledger / session-store / proxy finalization activity, and
per-turn low-level evidence counts.

Per design v1.3 §7 (Step 12) and Step 13 multi-turn observability.
"""

from __future__ import annotations

import json
import logging
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

from moralstack import __version__ as _moralstack_version
from moralstack.observability.read_store import ReadStore, SqliteReadStore

logger = logging.getLogger("moralstack.reports.conversation_export")

_MAX_PROMPT_CHARS = 4000
_MAX_FREE_TEXT_CHARS = 2000


def _format_ts(value: Any) -> str:
    """Render an epoch-ms / epoch-s integer as ISO 8601 UTC, or pass through."""
    if value is None:
        return "—"
    try:
        ts = float(value)
        if ts > 1e12:
            ts /= 1000.0
        return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()
    except (ValueError, TypeError, OSError):
        return str(value)


def _safe_json(value: Any) -> dict[str, Any] | list[Any] | None:
    """Parse a JSON string defensively; return ``None`` on any failure."""
    if value is None:
        return None
    if isinstance(value, (dict, list)):
        return value
    if not isinstance(value, str):
        return None
    raw = value.strip()
    if not raw:
        return None
    try:
        parsed = json.loads(raw)
    except (ValueError, TypeError):
        return None
    if isinstance(parsed, (dict, list)):
        return parsed
    return None


def _format_list(items: Any) -> str:
    """Render a list as a comma-separated string, fallback to str()."""
    if not items:
        return "—"
    if isinstance(items, (list, tuple)):
        return ", ".join(str(x) for x in items if str(x).strip())
    return str(items)


def _format_bool(value: Any) -> str:
    if value is None:
        return "—"
    if isinstance(value, bool):
        return "yes" if value else "no"
    try:
        return "yes" if int(value) != 0 else "no"
    except (ValueError, TypeError):
        return str(value)


def _truncate(text: str | None, limit: int) -> str:
    if not text:
        return ""
    if len(text) <= limit:
        return text
    return text[:limit] + "…"


# ---------------------------------------------------------------------------
# Section renderers (best-effort: missing data yields graceful omissions)
# ---------------------------------------------------------------------------


def _render_governance_decision(
    lines: list[str],
    meta: dict[str, Any] | None,
    proxy_event_meta: dict[str, Any] | None,
) -> None:
    """Render the Governance decision section. Falls back to proxy meta."""
    effective: dict[str, Any] = {}
    if isinstance(meta, dict) and meta:
        effective.update(meta)
    if (not effective) and isinstance(proxy_event_meta, dict):
        effective.update(proxy_event_meta)
    if not effective:
        return
    lines.append("### Governance decision")
    lines.append("")
    lines.append(f"- **Final action**: `{effective.get('final_action', 'UNKNOWN')}`")
    risk_score = effective.get("risk_score")
    if isinstance(risk_score, (int, float)):
        lines.append(f"- **Risk score**: {risk_score:.4f}")
    path = effective.get("path") or effective.get("path_taken")
    if path:
        lines.append(f"- **Path**: `{path}`")
    if effective.get("reason_codes"):
        lines.append(f"- **Reason codes**: {_format_list(effective.get('reason_codes'))}")
    if effective.get("triggered_principles"):
        lines.append(f"- **Triggered principles**: {_format_list(effective.get('triggered_principles'))}")
    if effective.get("domain_overlay"):
        lines.append(f"- **Domain overlay**: `{effective['domain_overlay']}`")
    if effective.get("governance_posture"):
        lines.append(f"- **Governance posture**: `{effective['governance_posture']}`")
    if effective.get("was_cached") is not None:
        lines.append(f"- **Was cached**: {_format_bool(effective.get('was_cached'))}")
    if effective.get("cached_from_turn") is not None:
        lines.append(f"- **Cached from turn**: {effective.get('cached_from_turn')}")
    decision_reason = effective.get("decision_reason") or ""
    if decision_reason:
        lines.append("")
        lines.append("**Decision rationale**:")
        lines.append("")
        lines.append(f"> {_truncate(str(decision_reason), _MAX_FREE_TEXT_CHARS)}")
    explanation = effective.get("decision_explanation")
    if explanation:
        lines.append("")
        lines.append("**Decision explanation** (compact):")
        lines.append("")
        lines.append("```json")
        try:
            lines.append(json.dumps(explanation, indent=2, ensure_ascii=False)[:_MAX_FREE_TEXT_CHARS])
        except (TypeError, ValueError):
            lines.append(str(explanation)[:_MAX_FREE_TEXT_CHARS])
        lines.append("```")
    # Always dump the full meta dict so auditors can inspect any field the
    # structured sections above might not yet render explicitly.
    lines.append("")
    lines.append("<details><summary>Raw governance meta (JSON)</summary>")
    lines.append("")
    lines.append("```json")
    try:
        lines.append(json.dumps(effective, indent=2, ensure_ascii=False)[:_MAX_FREE_TEXT_CHARS])
    except (TypeError, ValueError):
        lines.append(str(effective)[:_MAX_FREE_TEXT_CHARS])
    lines.append("```")
    lines.append("")
    lines.append("</details>")
    lines.append("")


def _render_conversation_state(lines: list[str], state_row: dict[str, Any] | None) -> None:
    """Render the Conversation state section from a ``conversation_states`` row."""
    if not state_row:
        return
    lines.append("### Conversation state")
    lines.append("")
    posture = state_row.get("posture")
    if posture:
        lines.append(f"- **Posture (out)**: `{posture}`")
    if state_row.get("was_cached") is not None:
        lines.append(f"- **Was cached**: {_format_bool(state_row.get('was_cached'))}")
    if state_row.get("cached_from_turn") is not None:
        lines.append(f"- **Cached from turn**: {state_row.get('cached_from_turn')}")
    if state_row.get("refresh_required") is not None:
        lines.append(f"- **Refresh required**: {_format_bool(state_row.get('refresh_required'))}")
    if state_row.get("refresh_reason"):
        lines.append(f"- **Refresh reason**: {state_row.get('refresh_reason')}")

    summary = _safe_json(state_row.get("state_summary_json"))
    full_state = _safe_json(state_row.get("state_out_json"))
    if isinstance(summary, dict):
        for key in (
            "active_domain",
            "active_overlay",
            "last_hard_constraints_triggered",
            "principle_shortlist",
            "last_governance_posture",
        ):
            val = summary.get(key)
            if val is None or val == "" or val == []:
                continue
            if isinstance(val, list):
                lines.append(f"- **{key}**: {_format_list(val)}")
            else:
                lines.append(f"- **{key}**: {val}")
        td_summary = summary.get("turn_decisions_summary")
        if isinstance(td_summary, list) and td_summary:
            lines.append("")
            lines.append("**Turn decisions summary**:")
            lines.append("")
            for td in td_summary:
                if not isinstance(td, dict):
                    continue
                lines.append(
                    f"- turn={td.get('turn_index')} "
                    f"action={td.get('final_action')} "
                    f"risk={td.get('risk_score')} "
                    f"rule={td.get('winning_rule') or '—'} "
                    f"cached={_format_bool(td.get('was_cached'))}"
                )
    if isinstance(full_state, dict):
        lines.append("")
        lines.append("<details><summary>Full state (JSON)</summary>")
        lines.append("")
        lines.append("```json")
        try:
            lines.append(json.dumps(full_state, indent=2, ensure_ascii=False)[:_MAX_FREE_TEXT_CHARS])
        except (TypeError, ValueError):
            lines.append(str(full_state)[:_MAX_FREE_TEXT_CHARS])
        lines.append("```")
        lines.append("")
        lines.append("</details>")
    lines.append("")


def _render_ledger_activity(lines: list[str], ledger_rows: list[dict[str, Any]]) -> None:
    """Render the Ledger activity section for the current turn."""
    if not ledger_rows:
        return
    lines.append("### Ledger activity")
    lines.append("")
    for row in ledger_rows:
        op = row.get("operation") or "—"
        outcome = row.get("outcome") or "—"
        bits = [f"`{op}` → **{outcome}**"]
        if row.get("similarity") is not None:
            bits.append(f"sim={row.get('similarity'):.3f}" if isinstance(row.get("similarity"), (int, float)) else "")
        if row.get("reason"):
            bits.append(f"reason={row.get('reason')}")
        if row.get("from_turn") is not None:
            bits.append(f"from_turn={row.get('from_turn')}")
        if row.get("posture"):
            bits.append(f"posture={row.get('posture')}")
        if row.get("domain"):
            bits.append(f"domain={row.get('domain')}")
        if row.get("intent_clarity"):
            bits.append(f"intent_clarity={row.get('intent_clarity')}")
        if row.get("request_type"):
            bits.append(f"request_type={row.get('request_type')}")
        contract_hash = row.get("contract_hash")
        if isinstance(contract_hash, str) and contract_hash:
            bits.append(f"contract_hash={contract_hash[:12]}…")
        if row.get("final_action"):
            bits.append(f"final_action={row.get('final_action')}")
        if row.get("risk_score") is not None and isinstance(row.get("risk_score"), (int, float)):
            bits.append(f"risk={row.get('risk_score'):.3f}")
        lines.append("- " + " · ".join(b for b in bits if b))
    lines.append("")


def _render_session_store_activity(lines: list[str], session_rows: list[dict[str, Any]]) -> None:
    """Render the Session store activity section for the current turn."""
    if not session_rows:
        return
    lines.append("### Session store activity")
    lines.append("")
    for row in session_rows:
        op = row.get("operation") or "—"
        outcome = row.get("outcome") or "—"
        bits = [f"`{op}` → **{outcome}**"]
        summary = _safe_json(row.get("state_summary_json"))
        if isinstance(summary, dict):
            posture = summary.get("last_governance_posture")
            if posture:
                bits.append(f"posture={posture}")
            overlay = summary.get("active_overlay")
            if overlay:
                bits.append(f"overlay={overlay}")
        payload = _safe_json(row.get("payload_json"))
        if isinstance(payload, dict):
            evicted = payload.get("evicted_ids")
            if isinstance(evicted, list) and evicted:
                bits.append(f"evicted={len(evicted)}")
        lines.append("- " + " · ".join(bits))
    lines.append("")


def _render_proxy_finalization(lines: list[str], proxy_event: dict[str, Any] | None) -> None:
    """Render the Proxy finalization section for the current turn."""
    if not proxy_event:
        return
    lines.append("### Proxy finalization")
    lines.append("")
    if proxy_event.get("state_provided") is not None:
        lines.append(f"- **State provided**: {_format_bool(proxy_event.get('state_provided'))}")
    if proxy_event.get("state_updated") is not None:
        lines.append(f"- **State updated**: {_format_bool(proxy_event.get('state_updated'))}")
    if proxy_event.get("posture_in"):
        lines.append(f"- **Posture (in)**: `{proxy_event.get('posture_in')}`")
    if proxy_event.get("posture_out"):
        lines.append(f"- **Posture (out)**: `{proxy_event.get('posture_out')}`")
    if proxy_event.get("was_cached") is not None:
        lines.append(f"- **Was cached**: {_format_bool(proxy_event.get('was_cached'))}")
    if proxy_event.get("cached_from_turn") is not None:
        lines.append(f"- **Cached from turn**: {proxy_event.get('cached_from_turn')}")
    if proxy_event.get("final_response_length") is not None:
        lines.append(f"- **Response length**: {proxy_event.get('final_response_length')} chars")
    headers = _safe_json(proxy_event.get("headers_json"))
    if isinstance(headers, dict) and headers:
        x_headers = {k: v for k, v in headers.items() if str(k).lower().startswith("x-moralstack")}
        if x_headers:
            lines.append("")
            lines.append("**X-MoralStack headers**:")
            lines.append("")
            lines.append("```json")
            lines.append(json.dumps(x_headers, indent=2, ensure_ascii=False)[:_MAX_FREE_TEXT_CHARS])
            lines.append("```")
    lines.append("")


def _render_evidence_links(
    lines: list[str],
    request_id: str,
    store: ReadStore,
    run_id: str | None,
) -> None:
    """Render compact counts of low-level evidence rows for this request."""
    if not request_id or not run_id:
        return
    try:
        llm_count = len(store.get_llm_calls_for_request(run_id, request_id))
        orch_count = len(store.get_orchestration_events_for_request(run_id, request_id))
        trace_count = len(store.get_decision_traces_for_request(run_id, request_id))
        debug_count = len(store.get_debug_events_for_request(run_id, request_id))
    except Exception:
        return
    if not any((llm_count, orch_count, trace_count, debug_count)):
        return
    lines.append("### Evidence")
    lines.append("")
    lines.append(f"- LLM calls: {llm_count}")
    lines.append(f"- Orchestration events: {orch_count}")
    lines.append(f"- Decision traces: {trace_count}")
    lines.append(f"- Debug events: {debug_count}")
    lines.append("")
    lines.append("> For full module I/O, see the per-request export.")
    lines.append("")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def export_conversation_to_markdown(
    conversation_id: str,
    *,
    read_store: ReadStore | None = None,
) -> str:
    """
    Render a markdown audit trail for the given conversation_id.

    The Step 13 export complements per-request prompts/responses with:
    governance decisions, conversation state snapshots, ledger activity,
    session-store activity, and proxy finalization summaries. Missing
    sections are gracefully omitted when the underlying tables are empty
    or absent.
    """
    if not conversation_id:
        return "# Conversation Audit Export\n\n**Error**: empty conversation_id provided.\n"

    store = read_store if read_store is not None else SqliteReadStore()
    requests = store.get_requests_for_conversation(conversation_id)

    states_rows = _safe_call(store, "get_conversation_states", conversation_id) or []
    ledger_rows = _safe_call(store, "get_ledger_events_for_conversation", conversation_id) or []
    session_rows = _safe_call(store, "get_session_store_events_for_conversation", conversation_id) or []
    proxy_rows = _safe_call(store, "get_proxy_request_events_for_conversation", conversation_id) or []
    overview = _safe_call(store, "get_conversation_overview", conversation_id) or {}

    states_by_request = _group_by_request_id(states_rows)
    ledger_by_request = _group_by_request_id_list(ledger_rows)
    session_by_request = _group_by_request_id_list(session_rows)
    proxy_by_request = _group_by_request_id(proxy_rows)

    lines: list[str] = []
    lines.append(f"# Conversation Audit Export — `{conversation_id}`")
    lines.append("")
    lines.append(f"**Framework**: MoralStack v{_moralstack_version}")
    lines.append(f"**Export timestamp**: {datetime.now(timezone.utc).isoformat()}")
    lines.append(f"**Total turns**: {len(requests)}")
    if isinstance(overview, dict) and overview:
        if overview.get("turn_count") is not None:
            lines.append(f"**Recorded turns (overview)**: {overview.get('turn_count')}")
        if overview.get("first_created_at"):
            lines.append(f"**First turn**: {_format_ts(overview.get('first_created_at'))}")
        if overview.get("last_created_at"):
            lines.append(f"**Last turn**: {_format_ts(overview.get('last_created_at'))}")
        if overview.get("max_risk_score") is not None and isinstance(overview.get("max_risk_score"), (int, float)):
            lines.append(f"**Max risk score**: {overview.get('max_risk_score'):.4f}")
        if overview.get("last_posture"):
            lines.append(f"**Last posture**: `{overview.get('last_posture')}`")
        if overview.get("final_actions"):
            fa = overview.get("final_actions")
            if isinstance(fa, dict) and fa:
                summary = ", ".join(f"{k}={v}" for k, v in fa.items())
                lines.append(f"**Final action distribution**: {summary}")
        ledger_hits = overview.get("ledger_hits")
        ledger_misses = overview.get("ledger_misses")
        if ledger_hits is not None or ledger_misses is not None:
            lines.append(f"**Ledger hits / misses**: {ledger_hits or 0} / {ledger_misses or 0}")
        sess_hits = overview.get("session_store_hits")
        sess_misses = overview.get("session_store_misses")
        if sess_hits is not None or sess_misses is not None:
            lines.append(f"**Session store hits / misses**: {sess_hits or 0} / {sess_misses or 0}")
        if overview.get("any_turn_cached") is not None:
            lines.append(f"**Any turn cached**: {_format_bool(overview.get('any_turn_cached'))}")
    lines.append("")

    if not requests:
        lines.append("> No requests found for this conversation_id.")
        return "\n".join(lines) + "\n"

    lines.append("---")
    lines.append("")

    last_run_id: str | None = None
    for idx, req in enumerate(requests):
        turn_index = req.get("turn_index")
        turn_label = f"Turn {turn_index}" if turn_index is not None else f"Turn (unsorted #{idx})"
        lines.append(f"## {turn_label}")
        lines.append("")

        request_id = req.get("request_id", "")
        run_id = req.get("run_id") or last_run_id
        if run_id:
            last_run_id = run_id
        created_at = req.get("created_at")
        if created_at:
            lines.append(f"- **Timestamp**: {_format_ts(created_at)}")
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
        lines.append(_truncate(prompt, _MAX_PROMPT_CHARS))
        lines.append("```")
        lines.append("")

        final_response = req.get("final_response", "") or ""
        lines.append("### Final response")
        lines.append("")
        lines.append("```text")
        lines.append(_truncate(final_response, _MAX_PROMPT_CHARS))
        lines.append("```")
        lines.append("")

        # Step 13: governance / state / ledger / session / proxy sections.
        meta = _safe_json(req.get("meta_json"))
        proxy_event = proxy_by_request.get(request_id)
        proxy_meta = _safe_json(proxy_event.get("metadata_json")) if proxy_event else None
        if isinstance(proxy_meta, dict):
            proxy_meta_dict: dict[str, Any] | None = proxy_meta
        else:
            proxy_meta_dict = None
        _render_governance_decision(
            lines,
            meta if isinstance(meta, dict) else None,
            proxy_meta_dict,
        )
        _render_conversation_state(lines, states_by_request.get(request_id))
        _render_ledger_activity(lines, ledger_by_request.get(request_id, []))
        _render_session_store_activity(lines, session_by_request.get(request_id, []))
        _render_proxy_finalization(lines, proxy_event)
        _render_evidence_links(lines, request_id, store, run_id)

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
    """Convenience wrapper around :func:`export_conversation_to_markdown`."""
    content = export_conversation_to_markdown(conversation_id, read_store=read_store)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(content)
    logger.info(
        "Conversation %s exported to %s (%d bytes)",
        conversation_id,
        output_path,
        len(content),
    )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _safe_call(store: ReadStore, method: str, *args: Any) -> Any:
    """Call ``store.<method>(*args)`` defensively (older stores may lack methods)."""
    fn = getattr(store, method, None)
    if not callable(fn):
        return None
    try:
        return fn(*args)
    except Exception:
        logger.debug("conversation_export: %s call failed", method, exc_info=True)
        return None


def _group_by_request_id(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Index rows by request_id, keeping the last occurrence (for unique snapshots)."""
    out: dict[str, dict[str, Any]] = {}
    for row in rows:
        rid = row.get("request_id")
        if not rid:
            continue
        out[str(rid)] = row
    return out


def _group_by_request_id_list(rows: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """Index rows by request_id, preserving all rows per request."""
    out: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        rid = row.get("request_id")
        if not rid:
            continue
        out[str(rid)].append(row)
    return dict(out)
