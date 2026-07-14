#!/usr/bin/env python3
"""Control plane of the UI loop: read and mutate ``.claude/ui-loop/STATE.json``.

The state file is JSON, not YAML, for one reason: it is written by a model. JSON
round-trips through ``json.loads`` and is either valid or rejected, so a
malformed write can never silently corrupt the loop's stop conditions. All
mutations go through this script, which validates transitions and computes the
terminal states itself rather than trusting the model to compute them.

Usage
-----
    python .claude/skills/improve-moralstack-ui/scripts/state.py show
    python .claude/skills/improve-moralstack-ui/scripts/state.py gate
    python .claude/skills/improve-moralstack-ui/scripts/state.py begin
    python .claude/skills/improve-moralstack-ui/scripts/state.py record \
        --score 74 --active-issue "..." --next-issue "..." \
        --p0 "..." --p1 "..." --report iteration-03.md --commit abc1234 \
        --outcome committed
    python .claude/skills/improve-moralstack-ui/scripts/state.py block --reason UI_UNREACHABLE

``gate`` exits 0 when another iteration may start and 3 when the loop is
terminal. The slash command reads that exit code, so a finished loop cannot be
restarted by a model that misreads its own notes.
"""

from __future__ import annotations

import argparse
import time
from typing import Any

from _common import STATE_PATH, ensure_dirs, fail, read_json, write_json_atomic

TERMINAL = {"COMPLETE", "BLOCKED", "PLATEAU", "MAX_ITERATIONS"}
OUTCOMES = {"committed", "rolled_back", "blocked"}

DEFAULT_STATE: dict[str, Any] = {
    "version": 2,
    "status": "READY",
    "iteration": 0,
    "max_iterations": 12,
    "target_score": 90,
    "required_consecutive_target_passes": 3,
    "consecutive_target_passes": 0,
    "plateau_window": 3,
    "plateau_min_delta": 1.0,
    "current_score": None,
    "score_history": [],
    "active_issue": None,
    "next_issue": None,
    "unresolved_p0": [],
    "unresolved_p1": [],
    "scenario_status": {},
    "last_report": None,
    "last_verified_commit": None,
    "stop_reason": None,
    "updated_at": None,
}


def load() -> dict[str, Any]:
    ensure_dirs()
    if not STATE_PATH.is_file():
        write_json_atomic(STATE_PATH, DEFAULT_STATE)
        return dict(DEFAULT_STATE)
    state = read_json(STATE_PATH)
    if not isinstance(state, dict):
        raise SystemExit("FAIL: STATE.json is not an object")
    merged = dict(DEFAULT_STATE)
    merged.update(state)
    return merged


def save(state: dict[str, Any]) -> None:
    state["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    write_json_atomic(STATE_PATH, state)


def _plateaued(state: dict[str, Any]) -> bool:
    window = int(state["plateau_window"])
    history = [float(s) for s in state["score_history"]]
    if len(history) < window + 1:
        return False
    recent = history[-(window + 1) :]
    if state["unresolved_p0"]:
        return False
    return (max(recent) - recent[0]) < float(state["plateau_min_delta"])


def _recompute_status(state: dict[str, Any]) -> None:
    """Terminal states are derived here, never asserted by the model."""
    if state["status"] == "BLOCKED":
        return

    score = state["current_score"]
    target = float(state["target_score"])
    if score is not None and float(score) >= target:
        state["consecutive_target_passes"] = int(state["consecutive_target_passes"]) + 1
    else:
        state["consecutive_target_passes"] = 0

    required = int(state["required_consecutive_target_passes"])
    scenarios = state["scenario_status"]
    all_scenarios_pass = bool(scenarios) and all(v == "PASS" for v in scenarios.values())
    clean = not state["unresolved_p0"] and not state["unresolved_p1"]

    if state["consecutive_target_passes"] >= required and clean and all_scenarios_pass:
        state["status"] = "COMPLETE"
        state["stop_reason"] = "target score sustained, no P0/P1, all required scenarios PASS"
        return
    if int(state["iteration"]) >= int(state["max_iterations"]):
        state["status"] = "MAX_ITERATIONS"
        state["stop_reason"] = "iteration budget consumed"
        return
    if _plateaued(state):
        state["status"] = "PLATEAU"
        state["stop_reason"] = "score window improved by less than plateau_min_delta"
        return
    state["status"] = "READY"
    state["stop_reason"] = None


def cmd_show(_: argparse.Namespace) -> int:
    state = load()
    print(f"status={state['status']}  iteration={state['iteration']}/{state['max_iterations']}")
    print(f"score={state['current_score']}  target={state['target_score']}  history={state['score_history']}")
    print(f"consecutive_target_passes={state['consecutive_target_passes']}/{state['required_consecutive_target_passes']}")
    print(f"unresolved_p0={state['unresolved_p0']}")
    print(f"unresolved_p1={state['unresolved_p1']}")
    print(f"active_issue={state['active_issue']}")
    print(f"next_issue={state['next_issue']}")
    if state["stop_reason"]:
        print(f"stop_reason={state['stop_reason']}")
    return 0


def cmd_gate(_: argparse.Namespace) -> int:
    state = load()
    if state["status"] in TERMINAL:
        print(f"TERMINAL {state['status']}: {state['stop_reason'] or 'no reason recorded'}")
        return 3
    print(f"READY iteration={int(state['iteration']) + 1}/{state['max_iterations']}")
    return 0


def cmd_begin(_: argparse.Namespace) -> int:
    """Claim the next iteration number. Refuses to start a terminal loop."""
    state = load()
    if state["status"] in TERMINAL:
        print(f"TERMINAL {state['status']}: refusing to begin a new iteration")
        return 3
    state["iteration"] = int(state["iteration"]) + 1
    state["status"] = "RUNNING"
    save(state)
    print(f"iteration={state['iteration']}")
    return 0


def cmd_record(args: argparse.Namespace) -> int:
    state = load()
    if args.outcome not in OUTCOMES:
        fail(f"outcome must be one of {sorted(OUTCOMES)}")

    if args.outcome == "committed":
        if args.score is None:
            fail("--score is required for a committed iteration")
        state["current_score"] = float(args.score)
        state["score_history"] = [*state["score_history"], float(args.score)]
        state["last_verified_commit"] = args.commit
    elif args.outcome == "rolled_back":
        # A rolled-back attempt costs an iteration but never a score: recording a
        # score for code that no longer exists would corrupt the plateau signal.
        state["consecutive_target_passes"] = 0

    state["active_issue"] = args.active_issue
    state["next_issue"] = args.next_issue
    state["unresolved_p0"] = list(args.p0 or [])
    state["unresolved_p1"] = list(args.p1 or [])
    state["last_report"] = args.report
    if args.scenario:
        status = dict(state["scenario_status"])
        for item in args.scenario:
            key, _, value = item.partition("=")
            if value not in {"PASS", "FAIL", "NOT_AVAILABLE"}:
                fail(f"scenario status must be PASS|FAIL|NOT_AVAILABLE, got: {item}")
            status[key] = value
        state["scenario_status"] = status

    _recompute_status(state)
    save(state)
    return cmd_show(args)


def cmd_block(args: argparse.Namespace) -> int:
    state = load()
    state["status"] = "BLOCKED"
    state["stop_reason"] = args.reason
    save(state)
    print(f"BLOCKED: {args.reason}")
    return 0


def cmd_reset(args: argparse.Namespace) -> int:
    state = load()
    fresh = dict(DEFAULT_STATE)
    if args.keep_budget:
        fresh["max_iterations"] = state["max_iterations"]
        fresh["target_score"] = state["target_score"]
    save(fresh)
    print("state reset")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("show").set_defaults(func=cmd_show)
    sub.add_parser("gate").set_defaults(func=cmd_gate)
    sub.add_parser("begin").set_defaults(func=cmd_begin)

    record = sub.add_parser("record")
    record.add_argument("--outcome", required=True, choices=sorted(OUTCOMES))
    record.add_argument("--score", type=float)
    record.add_argument("--active-issue")
    record.add_argument("--next-issue")
    record.add_argument("--p0", action="append")
    record.add_argument("--p1", action="append")
    record.add_argument("--scenario", action="append", help="ID=PASS|FAIL|NOT_AVAILABLE")
    record.add_argument("--report")
    record.add_argument("--commit")
    record.set_defaults(func=cmd_record)

    block = sub.add_parser("block")
    block.add_argument("--reason", required=True)
    block.set_defaults(func=cmd_block)

    reset = sub.add_parser("reset")
    reset.add_argument("--keep-budget", action="store_true")
    reset.set_defaults(func=cmd_reset)

    args = parser.parse_args()
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
