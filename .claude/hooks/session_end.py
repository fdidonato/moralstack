#!/usr/bin/env python3
"""SessionEnd hook: append an auto-diary digest to a staging file.

Fires when a session terminates. SessionEnd cannot emit context and its exit
code is ignored — it is side-effect only. It appends a short, timestamped digest
of the session (files touched, verify outcome, termination reason) to
``.claude/session-diary.md``, a **staging** file.

Discipline (PROJECT_SPEC §4, facts-vs-hypotheses): the digest is explicitly
UNVERIFIED and is written ONLY to the local staging file — never into the
verified facts table of ``docs/CODEBASE_FACTS.md``. A human promotes relevant
items into ``docs/CODEBASE_FACTS.md`` (hypotheses section) or
``docs/refactoring_diary.md`` after review.

Best-effort: any error exits 0.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime
from pathlib import Path

DIARY_NAME = "session-diary.md"


def _project_dir() -> Path:
    env = os.environ.get("CLAUDE_PROJECT_DIR")
    if env and Path(env).is_dir():
        return Path(env)
    return Path.cwd()


def _read_json(path: Path) -> dict:
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
        return state if isinstance(state, dict) else {}
    except (ValueError, OSError):
        return {}


def main() -> int:
    try:
        data = json.loads(sys.stdin.read() or "{}")
    except (ValueError, TypeError):
        return 0
    if not isinstance(data, dict):
        return 0

    project = _project_dir()
    session_id = str(data.get("session_id") or "unknown")
    reason = str(data.get("reason") or "?")

    edits = _read_json(project / ".claude" / ".session-edits.json")
    edited = edits.get("paths") if edits.get("session_id") == session_id else None
    edited = [p for p in edited if isinstance(p, str)] if isinstance(edited, list) else []

    verified = _read_json(project / ".claude" / ".last-verified.json")
    verify_outcome = verified.get("outcome") if verified.get("session_id") == session_id else None

    # Nothing worth staging if the session touched no code and never verified.
    if not edited and not verify_outcome:
        return 0

    stamp = datetime.now().isoformat(timespec="seconds")
    entry = [
        "",
        f"## {stamp} — session {session_id} (end: {reason})",
        "",
        "> UNVERIFIED auto-diary — review before promoting into "
        "docs/CODEBASE_FACTS.md (hypotheses) or docs/refactoring_diary.md.",
        "",
        f"- Files edited: {len(edited)}",
    ]
    entry.extend(f"  - {p}" for p in edited[:60])
    entry.append(f"- Last verify outcome: {verify_outcome or 'n/a'}")
    entry.append("- Open hypotheses: _(add by hand after review)_")
    entry.append("")

    diary = project / ".claude" / DIARY_NAME
    header = (
        "# Session diary (auto-staged, UNVERIFIED)\n\n"
        "Append-only digests written by the SessionEnd hook. Nothing here is a "
        "verified fact (PROJECT_SPEC §4). Promote relevant items into the docs by "
        "hand, then trim this file.\n"
    )
    try:
        diary.parent.mkdir(parents=True, exist_ok=True)
        existing = diary.read_text(encoding="utf-8") if diary.exists() else header
        diary.write_text(existing + "\n".join(entry), encoding="utf-8")
    except OSError:
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
