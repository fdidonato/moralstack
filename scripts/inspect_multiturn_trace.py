"""
CLI utility: inspect a multi-turn conversation trace from the MoralStack
observability store.

Usage:
    python scripts/inspect_multiturn_trace.py <conversation_id>
    python scripts/inspect_multiturn_trace.py --list-run <run_id>
    python scripts/inspect_multiturn_trace.py <conversation_id> --export <output.md>
    python scripts/inspect_multiturn_trace.py <conversation_id> --json

Outputs a compact human-readable summary of every recorded turn for the given
conversation_id, including:
    * governance decision (final_action, risk_score, posture, reason codes)
    * conversation state transitions
    * ledger lookup/store activity
    * session-store get/put activity
    * proxy finalisation summary

Requires the SQLite observability DB to be configured via
``MORALSTACK_OBSERVABILITY_DB_PATH`` (or the legacy ``MORALSTACK_DB_PATH``).
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from typing import Any

from moralstack.observability import obs
from moralstack.observability.config import get_db_path
from moralstack.reports.conversation_export import export_conversation_to_markdown


def _format_ts(value: Any) -> str:
    if value is None:
        return "—"
    try:
        ts = float(value)
        if ts > 1e12:
            ts /= 1000.0
        return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()
    except (ValueError, TypeError, OSError):
        return str(value)


def _safe_json(value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    if isinstance(value, dict):
        return value
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    try:
        parsed = json.loads(text)
    except (ValueError, TypeError):
        return None
    return parsed if isinstance(parsed, dict) else None


def _print_section(title: str) -> None:
    print()
    print(f"== {title} ==")


def _list_run(run_id: str) -> int:
    """Print all conversations recorded in the given run."""
    rs = obs.read_store
    conversations = rs.get_conversation_ids_for_run(run_id) or []
    if not conversations:
        print(f"No conversations found for run_id={run_id}.")
        return 0
    print(f"Conversations in run {run_id}:")
    print()
    header = f"{'conversation_id':<40} {'turns':>5} {'max_risk':>9} {'last_posture':<14} {'cached':>6} {'last_at':<25}"
    print(header)
    print("-" * len(header))
    for c in conversations:
        conv = (c.get("conversation_id") or "")[:38]
        turns = c.get("turn_count") or 0
        risk = c.get("max_risk_score")
        risk_str = f"{risk:.3f}" if isinstance(risk, (int, float)) else "—"
        posture = (c.get("last_posture") or "—")[:14]
        cached = c.get("cached_turn_count") or 0
        last_at = _format_ts(c.get("last_created_at"))
        print(f"{conv:<40} {turns:>5} {risk_str:>9} {posture:<14} {cached:>6} {last_at:<25}")
    return 0


def _inspect(conversation_id: str, as_json: bool) -> int:
    """Print a compact multi-turn summary for the given conversation."""
    rs = obs.read_store
    requests = rs.get_requests_for_conversation(conversation_id) or []
    overview = rs.get_conversation_overview(conversation_id) or {}
    states = {str(r.get("request_id")): r for r in rs.get_conversation_states(conversation_id) or [] if r.get("request_id")}
    ledger = rs.get_ledger_events_for_conversation(conversation_id) or []
    sessions = rs.get_session_store_events_for_conversation(conversation_id) or []
    proxy = {
        str(r.get("request_id")): r
        for r in rs.get_proxy_request_events_for_conversation(conversation_id) or []
        if r.get("request_id")
    }

    if not requests:
        print(f"Conversation {conversation_id!r} not found in the database.")
        return 1

    if as_json:
        payload = {
            "conversation_id": conversation_id,
            "overview": overview,
            "requests": requests,
            "states": list(states.values()),
            "ledger_events": ledger,
            "session_store_events": sessions,
            "proxy_request_events": list(proxy.values()),
        }
        print(json.dumps(payload, indent=2, default=str, ensure_ascii=False))
        return 0

    _print_section(f"Conversation: {conversation_id}")
    print(f"Turns           : {overview.get('turn_count') or len(requests)}")
    print(f"First turn      : {_format_ts(overview.get('first_created_at'))}")
    print(f"Last turn       : {_format_ts(overview.get('last_created_at'))}")
    risk = overview.get("max_risk_score")
    print(f"Max risk score  : {risk:.4f}" if isinstance(risk, (int, float)) else "Max risk score  : —")
    print(f"Last posture    : {overview.get('last_posture') or '—'}")
    fa = overview.get("final_actions") or {}
    print(f"Final actions   : {', '.join(f'{k}={v}' for k, v in fa.items()) or '—'}")
    print("Ledger hits/miss: " f"{overview.get('ledger_hits') or 0} / {overview.get('ledger_misses') or 0}")
    print("SessionStore h/m: " f"{overview.get('session_store_hits') or 0} / {overview.get('session_store_misses') or 0}")

    for req in requests:
        rid = str(req.get("request_id") or "")
        meta = _safe_json(req.get("meta_json")) or {}
        state = states.get(rid)
        ledger_for_turn = [ev for ev in ledger if str(ev.get("request_id")) == rid]
        sessions_for_turn = [ev for ev in sessions if str(ev.get("request_id")) == rid]
        proxy_for_turn = proxy.get(rid)
        _print_section(f"Turn {req.get('turn_index')} · request {rid[:12]}")
        print(f"Created           : {_format_ts(req.get('created_at'))}")
        print(f"Domain            : {req.get('domain') or '—'}")
        print(f"Final action      : {meta.get('final_action') or '—'}")
        rs_val = meta.get("risk_score")
        if isinstance(rs_val, (int, float)):
            print(f"Risk score        : {rs_val:.4f}")
        print(f"Path              : {meta.get('path') or '—'}")
        print(f"Domain overlay    : {meta.get('domain_overlay') or '—'}")
        rc = meta.get("reason_codes") or []
        print(f"Reason codes      : {', '.join(rc) if rc else '—'}")
        tp = meta.get("triggered_principles") or []
        print(f"Triggered princ.  : {', '.join(tp) if tp else '—'}")
        if state:
            print(f"Posture (state)   : {state.get('posture') or '—'}")
            if state.get("was_cached"):
                cft = state.get("cached_from_turn")
                print(f"Cache hit         : yes (from turn {cft})" if cft is not None else "Cache hit         : yes")
            if state.get("refresh_required"):
                print(f"Refresh required  : yes — {state.get('refresh_reason') or 'n/a'}")
        if ledger_for_turn:
            print("Ledger events     :")
            for ev in ledger_for_turn:
                sim = ev.get("similarity")
                sim_str = f" sim={sim:.3f}" if isinstance(sim, (int, float)) else ""
                from_turn = ev.get("from_turn")
                ft_str = f" from_turn={from_turn}" if from_turn is not None else ""
                reason = ev.get("reason")
                reason_str = f" reason={reason}" if reason else ""
                print(f"  · {ev.get('operation'):<6} → {ev.get('outcome'):<8}{sim_str}{ft_str}{reason_str}")
        if sessions_for_turn:
            print("Session store     :")
            for ev in sessions_for_turn:
                payload = _safe_json(ev.get("payload_json")) or {}
                detail_parts: list[str] = []
                ttl = payload.get("ttl_age_seconds") or payload.get("ttl_age_ms")
                if ttl is not None:
                    detail_parts.append(f"ttl_age={ttl}")
                evicted = payload.get("evicted_ids") or []
                if evicted:
                    detail_parts.append(f"evicted={len(evicted)}")
                detail = (" · " + ", ".join(detail_parts)) if detail_parts else ""
                print(f"  · {ev.get('operation'):<5} → {ev.get('outcome'):<8}{detail}")
        if proxy_for_turn:
            print("Proxy finalize    :")
            print(
                "  posture {pi} → {po} | state_provided={sp} | state_updated={su} | was_cached={wc}".format(
                    pi=proxy_for_turn.get("posture_in") or "—",
                    po=proxy_for_turn.get("posture_out") or "—",
                    sp="yes" if proxy_for_turn.get("state_provided") else "no",
                    su="yes" if proxy_for_turn.get("state_updated") else "no",
                    wc="yes" if proxy_for_turn.get("was_cached") else "no",
                )
            )

    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Inspect a multi-turn conversation trace.")
    parser.add_argument("conversation_id", nargs="?", help="Conversation id to inspect.")
    parser.add_argument("--list-run", metavar="RUN_ID", help="List conversations recorded in the given run.")
    parser.add_argument("--export", metavar="PATH", help="Write a full Markdown audit export to PATH and exit.")
    parser.add_argument("--json", action="store_true", help="Emit raw rows as JSON instead of summary.")
    args = parser.parse_args(argv)

    if not get_db_path():
        print(
            "ERROR: MORALSTACK_OBSERVABILITY_DB_PATH is not set; nothing to inspect.",
            file=sys.stderr,
        )
        return 2

    if args.list_run:
        return _list_run(args.list_run)

    if not args.conversation_id:
        parser.error("Provide a conversation_id, or use --list-run RUN_ID.")
        return 2

    if args.export:
        content = export_conversation_to_markdown(args.conversation_id)
        with open(args.export, "w", encoding="utf-8") as f:
            f.write(content)
        print(f"Wrote Markdown export ({len(content)} bytes) to {args.export}.")
        return 0

    return _inspect(args.conversation_id, as_json=args.json)


if __name__ == "__main__":
    sys.exit(main())
