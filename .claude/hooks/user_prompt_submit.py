#!/usr/bin/env python3
"""UserPromptSubmit hook: inject the active plan / context on relevant prompts.

Minimal keyword-gated context injector. When the user's prompt hints they are
resuming or referring to the running work (plan / context / snapshot / resume),
this surfaces two cheap pointers via ``additionalContext``:

- the pre-compaction snapshot (``.claude/.context-snapshot.md``) if present,
- the active plan file(s) under ``ai/plans/`` (name only).

It stays SILENT (emits nothing) when no keyword matches, so it never adds noise
to ordinary prompts. Best-effort: any error exits 0 with no output.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

SNAPSHOT_NAME = ".context-snapshot.md"
_TRIGGERS = (
    "plan",
    "piano",
    "context",
    "contesto",
    "snapshot",
    "resume",
    "riprend",
    "continua",
    "dove eravamo",
    "ripristina",
)
_SNAPSHOT_PREVIEW_CHARS = 1500


def _project_dir() -> Path:
    env = os.environ.get("CLAUDE_PROJECT_DIR")
    if env and Path(env).is_dir():
        return Path(env)
    return Path.cwd()


def _active_plans(project: Path) -> list[str]:
    plans_dir = project / "ai" / "plans"
    if not plans_dir.is_dir():
        return []
    names: list[str] = []
    for path in sorted(plans_dir.glob("*.md")):
        try:
            if path.stat().st_size > 0:
                names.append(path.name)
        except OSError:
            continue
    return names


def _emit(context: str) -> None:
    payload = {
        "hookSpecificOutput": {
            "hookEventName": "UserPromptSubmit",
            "additionalContext": context,
        }
    }
    sys.stdout.write(json.dumps(payload))


def main() -> int:
    try:
        data = json.loads(sys.stdin.read() or "{}")
    except (ValueError, TypeError):
        return 0
    if not isinstance(data, dict):
        return 0

    prompt = data.get("prompt")
    if not isinstance(prompt, str) or not any(t in prompt.lower() for t in _TRIGGERS):
        return 0  # silent on ordinary prompts

    project = _project_dir()
    parts: list[str] = []

    snapshot = project / ".claude" / SNAPSHOT_NAME
    if snapshot.exists():
        try:
            preview = snapshot.read_text(encoding="utf-8")[:_SNAPSHOT_PREVIEW_CHARS]
            parts.append("[context-snapshot available] " + SNAPSHOT_NAME + ":\n" + preview)
        except OSError:
            pass

    plans = _active_plans(project)
    if plans:
        parts.append("[active plans] ai/plans/: " + ", ".join(plans[:12]))

    if parts:
        _emit("\n\n".join(parts))
    return 0


if __name__ == "__main__":
    sys.exit(main())
