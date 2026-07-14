#!/usr/bin/env python3
"""Resolve representative UI scenarios from the observability database.

The loop must never invent evidence. Rather than asking a model to "find a
DCCL-reuse request somewhere in the dashboard" — which ends in either flailing or
fabrication — this script reads the persisted trace, tags only what it can prove,
and writes real URLs to ``.claude/ui-loop/runtime/scenarios.json``. Anything it
cannot prove is ``NOT_AVAILABLE``: that blocks COMPLETE but still allows a useful
iteration on the scenarios that exist. A fabricated PASS is far worse than an
honest gap, because it silently invalidates every later score.

Read-only (``file:...?mode=ro``); it never writes to the database. Every table and
column is probed at runtime, so a schema change degrades the output rather than
crashing the loop.

Tagging matches the *observed* vocabulary — stages, components, event types,
decisions, statuses and reason codes actually present in your DB. Inspect it with
``--vocabulary`` and tune the rules below if a scenario you know exists is
reported missing. Tune the rules; never relax the honesty.

Usage
-----
    python .claude/skills/improve-moralstack-ui/scripts/scenarios.py
    python .claude/skills/improve-moralstack-ui/scripts/scenarios.py --vocabulary
    python .claude/skills/improve-moralstack-ui/scripts/scenarios.py --limit-runs 5
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
from collections import Counter, defaultdict
from typing import Any, Callable

from _common import SCENARIO_PATH, db_path, ensure_dirs, ui_base_url, write_json_atomic

Signals = dict[str, Any]

ACTIONS = ("NORMAL_COMPLETE", "SAFE_COMPLETE", "REFUSE")
SEVERITY = {"NORMAL_COMPLETE": 0, "SAFE_COMPLETE": 1, "REFUSE": 2}


def vocab(signals: Signals, pattern: str) -> bool:
    """True when any observed trace token matches the pattern."""
    regex = re.compile(pattern, re.IGNORECASE)
    return any(regex.search(token) for token in signals["vocabulary"])


# --------------------------------------------------------------------------
# Tagging rules — the tunable surface of this script.
# --------------------------------------------------------------------------

REQUEST_RULES: tuple[tuple[str, str, Callable[[Signals], bool]], ...] = (
    ("S1", "Normal complete on the fast path", lambda s: s["delivered_action"] == "NORMAL_COMPLETE" and s["max_cycle"] <= 1),
    ("S2", "Safe complete with governance bounds", lambda s: s["delivered_action"] == "SAFE_COMPLETE"),
    ("S3", "Refusal", lambda s: s["delivered_action"] == "REFUSE"),
    ("S4", "Multi-cycle deliberation", lambda s: s["max_cycle"] >= 2),
    ("S5", "Parallel module tier", lambda s: vocab(s, r"PARALLEL|CONCURREN|TIER")),
    (
        "S6",
        "Compliance draft reused as delivered output",
        lambda s: vocab(s, r"(DCCL|COMPLIANCE).*(REUSE|HIT)|REUSE.*(DCCL|COMPLIANCE)"),
    ),
    (
        "S7",
        "Compliance match without final reuse",
        lambda s: vocab(s, r"(DCCL|COMPLIANCE).*(MATCH|DOWNGRAD|INVALID)") and not vocab(s, r"REUSE"),
    ),
    ("S8", "Final revalidation blocked or fail-closed", lambda s: vocab(s, r"REVALID.*(BLOCK|FAIL|VIOLAT)|FAIL[_ ]?CLOSED")),
    ("S9", "Skipped or deferred module", lambda s: vocab(s, r"SKIP|DEFER")),
    ("S10", "Calibration-adjusted risk", lambda s: vocab(s, r"CALIBRAT")),
)

CONVERSATION_RULES: tuple[tuple[str, str, Callable[[Signals], bool]], ...] = (
    ("C1", "Stable benign conversation", lambda c: c["turns"] >= 2 and c["actions"] == {"NORMAL_COMPLETE"}),
    ("C2", "Escalation across turns", lambda c: c["turns"] >= 2 and (c["risk_increases"] or c["posture_changes"])),
    ("C3", "Cached state reuse", lambda c: c["was_cached"] or vocab(c, r"CACHE|LEDGER_HIT|REUSE")),
    ("C4", "Refresh required", lambda c: vocab(c, r"REFRESH|RECOMPUT|INVALIDAT")),
    ("C5", "Mixed final actions", lambda c: len(c["actions"]) >= 2),
    ("C6", "Multi-turn navigation", lambda c: c["turns"] >= 3),
)

REQUEST_EVIDENCE_KEYS = (
    "url",
    "run_id",
    "request_id",
    "delivered_action",
    "pre_delivery_action",
    "delivery_differs_from_pre_delivery",
    "max_cycle",
    "risk_category",
    "prompt_head",
)
CONVERSATION_EVIDENCE_KEYS = ("url", "conversation_id", "turns", "actions", "was_cached", "posture_changes")


def _connect(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _has_table(conn: sqlite3.Connection, table: str) -> bool:
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone() is not None


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}


def _obj(raw: Any) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        value = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _tokens(raw: Any) -> list[str]:
    """Flatten a JSON blob into short, comparable trace tokens."""
    if not raw:
        return []
    try:
        value = json.loads(raw)
    except (TypeError, ValueError):
        return [str(raw)[:60]]
    out: list[str] = []

    def walk(node: Any) -> None:
        if isinstance(node, str):
            if 0 < len(node) <= 80:
                out.append(node)
        elif isinstance(node, dict):
            for key, item in node.items():
                if isinstance(item, (str, bool)) and re.search(r"reason|code|status|decision|source|rule|path", key, re.I):
                    out.append(f"{key}={item}")
                else:
                    walk(item)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(value)
    return out


def _request_signals(conn: sqlite3.Connection, row: sqlite3.Row) -> Signals:
    run_id, request_id = row["run_id"], row["request_id"]
    keys = set(row.keys())
    meta = _obj(row["meta_json"]) if "meta_json" in keys else {}

    vocabulary: list[str] = []
    max_cycle = 0

    if _has_table(conn, "orchestration_events"):
        cols = _columns(conn, "orchestration_events")
        wanted = [
            c for c in ("cycle", "stage", "component", "event_type", "decision", "status", "reason_codes_json") if c in cols
        ]
        if wanted:
            query = f"SELECT {', '.join(wanted)} FROM orchestration_events WHERE run_id=? AND request_id=?"
            for event in conn.execute(query, (run_id, request_id)):
                event_keys = set(event.keys())
                for column in ("stage", "component", "event_type", "decision", "status"):
                    if column in event_keys and event[column]:
                        vocabulary.append(str(event[column]))
                if "reason_codes_json" in event_keys:
                    vocabulary.extend(_tokens(event["reason_codes_json"]))
                if "cycle" in event_keys and event["cycle"] is not None:
                    try:
                        max_cycle = max(max_cycle, int(event["cycle"]))
                    except (TypeError, ValueError):
                        pass

    pre_delivery = ""
    risk_category = ""
    if _has_table(conn, "decision_traces"):
        for trace in conn.execute(
            "SELECT stage, trace_json FROM decision_traces WHERE run_id=? AND request_id=? ORDER BY sequence",
            (run_id, request_id),
        ):
            payload = _obj(trace["trace_json"])
            if trace["stage"]:
                vocabulary.append(str(trace["stage"]))
            action = str(payload.get("final_action") or "").upper()
            if action in ACTIONS:
                pre_delivery = action
            category = payload.get("risk_category")
            if isinstance(category, str) and category and not risk_category:
                risk_category = category
            vocabulary.extend(_tokens(trace["trace_json"]))

    delivered = ""
    was_cached = False
    cached_from_turn: Any = None
    posture_in = posture_out = ""
    conversation_id = str(meta.get("conversation_id") or "")
    turn_index = meta.get("turn_index")

    if _has_table(conn, "proxy_request_events"):
        cols = _columns(conn, "proxy_request_events")
        wanted = [
            c
            for c in (
                "final_action",
                "conversation_id",
                "turn_index",
                "was_cached",
                "cached_from_turn",
                "posture_in",
                "posture_out",
            )
            if c in cols
        ]
        if wanted and {"run_id", "request_id"} <= cols:
            query = f"SELECT {', '.join(wanted)} FROM proxy_request_events WHERE run_id=? AND request_id=?"
            for event in conn.execute(query, (run_id, request_id)):
                event_keys = set(event.keys())
                vocabulary.append("PROXY_FINALIZATION")
                if "final_action" in event_keys and event["final_action"]:
                    delivered = str(event["final_action"]).upper()
                if "conversation_id" in event_keys and event["conversation_id"]:
                    conversation_id = str(event["conversation_id"])
                if "turn_index" in event_keys and event["turn_index"] is not None:
                    turn_index = event["turn_index"]
                if "was_cached" in event_keys and event["was_cached"]:
                    was_cached = True
                    vocabulary.append("PROXY_STATE_CACHED")
                if "cached_from_turn" in event_keys and event["cached_from_turn"] is not None:
                    cached_from_turn = event["cached_from_turn"]
                if "posture_in" in event_keys and event["posture_in"]:
                    posture_in = str(event["posture_in"])
                if "posture_out" in event_keys and event["posture_out"]:
                    posture_out = str(event["posture_out"])

    if _has_table(conn, "conversation_states"):
        cols = _columns(conn, "conversation_states")
        if {"run_id", "request_id"} <= cols:
            wanted = [c for c in ("final_action", "conversation_id", "turn_index") if c in cols]
            query = (
                f"SELECT {', '.join(wanted)} FROM conversation_states "
                "WHERE run_id=? AND request_id=? ORDER BY id DESC LIMIT 1"
            )
            state = conn.execute(query, (run_id, request_id)).fetchone()
            if state is not None:
                state_keys = set(state.keys())
                if not delivered and "final_action" in state_keys and state["final_action"]:
                    delivered = str(state["final_action"]).upper()
                if not conversation_id and "conversation_id" in state_keys and state["conversation_id"]:
                    conversation_id = str(state["conversation_id"])
                if turn_index is None and "turn_index" in state_keys:
                    turn_index = state["turn_index"]

    delivered_action = delivered or pre_delivery

    return {
        "run_id": run_id,
        "request_id": request_id,
        "url": f"{ui_base_url()}/runs/{run_id}/requests/{request_id}",
        "prompt_head": (row["prompt"] or "")[:80] if "prompt" in keys else "",
        "conversation_id": conversation_id,
        "turn_index": turn_index,
        "delivered_action": delivered_action,
        "pre_delivery_action": pre_delivery,
        # The single most audit-relevant fact the UI can get wrong.
        "delivery_differs_from_pre_delivery": bool(delivered and pre_delivery and delivered != pre_delivery),
        "risk_category": risk_category,
        "max_cycle": max_cycle,
        "was_cached": was_cached,
        "cached_from_turn": cached_from_turn,
        "posture_in": posture_in,
        "posture_out": posture_out,
        "vocabulary": sorted({token for token in vocabulary if token}),
    }


def _conversation_signals(requests: list[Signals]) -> Signals:
    ordered = sorted(requests, key=lambda r: (r["turn_index"] if r["turn_index"] is not None else 0))
    levels = [SEVERITY.get(r["delivered_action"], 0) for r in ordered]
    postures = [(r["posture_in"], r["posture_out"]) for r in ordered]
    return {
        "conversation_id": ordered[0]["conversation_id"],
        "url": f"{ui_base_url()}/conversations/{ordered[0]['conversation_id']}",
        "turns": len(ordered),
        "actions": {r["delivered_action"] for r in ordered if r["delivered_action"]},
        "risk_increases": any(b > a for a, b in zip(levels, levels[1:])),
        "posture_changes": any(pin and pout and pin != pout for pin, pout in postures),
        "was_cached": any(r["was_cached"] or r["cached_from_turn"] is not None for r in ordered),
        "vocabulary": sorted({token for r in ordered for token in r["vocabulary"]}),
    }


def _entry(scenario_id: str, description: str, match: Signals | None, keys: tuple[str, ...]) -> dict[str, Any]:
    if match is None:
        return {"id": scenario_id, "description": description, "status": "NOT_AVAILABLE", "evidence": None}
    evidence = {k: (sorted(match[k]) if isinstance(match[k], set) else match[k]) for k in keys if k in match}
    return {"id": scenario_id, "description": description, "status": "AVAILABLE", "evidence": evidence}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--limit-runs", type=int, default=25, help="how many recent runs to scan")
    parser.add_argument("--vocabulary", action="store_true", help="print the observed trace vocabulary and exit")
    args = parser.parse_args()

    path = db_path()
    if path is None or not path.is_file():
        print("FAIL: MORALSTACK_OBSERVABILITY_DB_PATH is unset or points at a missing file.")
        print("      The loop cannot evaluate a UI with no persisted runs behind it.")
        return 1

    conn = _connect(str(path))
    if not _has_table(conn, "requests"):
        print("FAIL: no `requests` table — this is not a MoralStack observability DB.")
        return 1

    runs = [
        r["run_id"] for r in conn.execute("SELECT run_id FROM runs ORDER BY started_at DESC LIMIT ?", (args.limit_runs,))
    ]
    signals: list[Signals] = []
    for run_id in runs:
        for row in conn.execute("SELECT * FROM requests WHERE run_id=? ORDER BY created_at", (run_id,)):
            signals.append(_request_signals(conn, row))

    if args.vocabulary:
        counter: Counter[str] = Counter()
        for item in signals:
            counter.update(item["vocabulary"])
        print(f"{len(signals)} requests across {len(runs)} runs. Observed vocabulary:\n")
        for token, count in counter.most_common():
            print(f"  {count:5d}  {token}")
        return 0

    grouped: dict[str, list[Signals]] = defaultdict(list)
    for item in signals:
        if item["conversation_id"]:
            grouped[item["conversation_id"]].append(item)
    conversations = [_conversation_signals(v) for v in grouped.values()]

    # Single-request scenarios prefer a standalone request: a conversation turn
    # would drag conversation context into an audit that is about one request.
    ranked = sorted(signals, key=lambda s: bool(s["conversation_id"]))

    resolved: dict[str, Any] = {}
    for scenario_id, description, predicate in REQUEST_RULES:
        match = next((s for s in ranked if predicate(s)), None)
        resolved[scenario_id] = _entry(scenario_id, description, match, REQUEST_EVIDENCE_KEYS)
    for scenario_id, description, predicate in CONVERSATION_RULES:
        match = next((c for c in conversations if predicate(c)), None)
        resolved[scenario_id] = _entry(scenario_id, description, match, CONVERSATION_EVIDENCE_KEYS)

    divergent = [s for s in signals if s["delivery_differs_from_pre_delivery"]]

    ensure_dirs()
    write_json_atomic(
        SCENARIO_PATH,
        {
            "db_path": str(path),
            "runs_scanned": len(runs),
            "requests_scanned": len(signals),
            "conversations_scanned": len(conversations),
            "delivery_divergence_examples": [s["url"] for s in divergent[:5]],
            "scenarios": resolved,
        },
    )

    available = sum(1 for v in resolved.values() if v["status"] == "AVAILABLE")
    print(f"Scanned {len(signals)} requests / {len(conversations)} conversations from {len(runs)} runs.")
    print(f"Resolved {available}/{len(resolved)} scenarios -> .claude/ui-loop/runtime/scenarios.json\n")
    for scenario_id, entry in resolved.items():
        if entry["status"] == "AVAILABLE":
            print(f"  {scenario_id:3s} AVAILABLE      {entry['evidence']['url']}")
        else:
            print(f"  {scenario_id:3s} NOT_AVAILABLE  {entry['description']}")

    if divergent:
        print(f"\n{len(divergent)} request(s) where the delivered action differs from the pre-delivery decision.")
        print("Audit those first: they are where the UI most easily lies.")
    if available < len(resolved):
        print("\nNOT_AVAILABLE = the DB does not prove that scenario. Record traffic that exercises it,")
        print("or inspect --vocabulary and tune the rules in this file.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
