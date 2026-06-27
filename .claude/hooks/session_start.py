#!/usr/bin/env python3
"""SessionStart hook: orient Claude with repo state, best-effort.

Injects a short situational brief at session start so work can begin without
the user re-explaining state: current branch, working-tree summary, and any
uncommitted review/plan notes sitting in the repo root. Pure context — it never
blocks and always exits 0; any error degrades to no brief.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

_REVIEW_HINTS = ("review", "plan", "analisi", "upgrade")


def _project_dir() -> Path:
    env = os.environ.get("CLAUDE_PROJECT_DIR")
    if env and Path(env).is_dir():
        return Path(env)
    return Path.cwd()


def _git(args: list[str], project: Path) -> str:
    try:
        proc = subprocess.run(
            ["git", *args],
            cwd=str(project),
            capture_output=True,
            text=True,
            timeout=10,
        )
        return proc.stdout.strip() if proc.returncode == 0 else ""
    except (OSError, subprocess.TimeoutExpired):
        return ""


def main() -> int:
    try:
        sys.stdin.read()
    except (ValueError, OSError):
        pass

    project = _project_dir()
    branch = _git(["rev-parse", "--abbrev-ref", "HEAD"], project) or "?"
    lines = [ln for ln in _git(["status", "--porcelain"], project).splitlines() if ln.strip()]

    staged = sum(1 for ln in lines if ln[:1] not in (" ", "?"))
    modified = sum(1 for ln in lines if ln[1:2] == "M")
    untracked = [ln[3:] for ln in lines if ln.startswith("??")]
    review = sorted(
        u for u in untracked if u.endswith(".md") and "/" not in u and any(k in u.lower() for k in _REVIEW_HINTS)
    )

    parts = [
        f"[SessionStart] branch: {branch} | {len(lines)} change(s): "
        f"{staged} staged, {modified} modified, {len(untracked)} untracked."
    ]
    if review:
        parts.append("Uncommitted review/plan notes in root: " + ", ".join(review[:8]) + ".")
    parts.append(
        "Reminders: smallest-diff (PROJECT_SPEC §6); behavior changes need matching "
        "docs (§8 — Stop gate blocks otherwise); run the pre-commit-verifier agent "
        "before declaring done."
    )

    payload = {
        "hookSpecificOutput": {
            "hookEventName": "SessionStart",
            "additionalContext": " ".join(parts),
        }
    }
    sys.stdout.write(json.dumps(payload))
    return 0


if __name__ == "__main__":
    sys.exit(main())
