#!/usr/bin/env python3
"""PostToolUse(Edit|Write) auto-formatter + session edit recorder.

Two best-effort jobs, both fail-safe (always exit 0; PostToolUse can never
block):

1. Record every edited path into ``.claude/.session-edits.json`` (keyed by
   session id) so ``stop_gate.py`` knows what changed this session — for the
   non-blocking verify report and the blocking docs gate.
2. If the edited file is a ``.py`` under ``moralstack/`` or ``tests/``, run
   ``ruff check --fix`` then ``black`` on that single file and report the
   outcome back to Claude via ``additionalContext``.

Uses the project venv interpreter when present, else the current one.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

MARKER_NAME = ".session-edits.json"


def _project_dir() -> Path:
    env = os.environ.get("CLAUDE_PROJECT_DIR")
    if env and Path(env).is_dir():
        return Path(env)
    return Path.cwd()


def _venv_python(project: Path) -> str:
    candidates = [
        project / "venv" / "Scripts" / "python.exe",
        project / "venv" / "bin" / "python",
        project / ".venv" / "Scripts" / "python.exe",
        project / ".venv" / "bin" / "python",
    ]
    for candidate in candidates:
        if candidate.exists():
            return str(candidate)
    return sys.executable or "python"


def _record_edit(project: Path, session_id: str, rel: str) -> None:
    marker = project / ".claude" / MARKER_NAME
    state = {"session_id": session_id, "paths": []}
    if marker.exists():
        try:
            loaded = json.loads(marker.read_text(encoding="utf-8"))
            if isinstance(loaded, dict) and loaded.get("session_id") == session_id:
                state = loaded
                state.setdefault("paths", [])
        except (ValueError, OSError):
            pass
    if rel not in state["paths"]:
        state["paths"].append(rel)
    state["session_id"] = session_id
    try:
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text(json.dumps(state), encoding="utf-8")
    except OSError:
        pass


def _emit(context: str) -> None:
    payload = {
        "hookSpecificOutput": {
            "hookEventName": "PostToolUse",
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

    tool_input = data.get("tool_input") or {}
    file_path = tool_input.get("file_path") if isinstance(tool_input, dict) else None
    if not isinstance(file_path, str) or not file_path:
        return 0

    project = _project_dir()
    session_id = str(data.get("session_id") or "unknown")
    try:
        rel = str(Path(file_path).resolve().relative_to(project.resolve()))
    except ValueError:
        return 0  # outside the project tree — ignore
    rel = rel.replace("\\", "/")

    _record_edit(project, session_id, rel)

    formattable = rel.endswith(".py") and (rel.startswith("moralstack/") or rel.startswith("tests/"))
    if not formattable:
        return 0

    py = _venv_python(project)
    results = []
    for label, args in (
        ("ruff", [py, "-m", "ruff", "check", "--fix", rel]),
        ("black", [py, "-m", "black", "-q", rel]),
    ):
        try:
            proc = subprocess.run(
                args,
                cwd=str(project),
                capture_output=True,
                text=True,
                timeout=45,
            )
            results.append(f"{label}: {'ok' if proc.returncode == 0 else 'changed/failed'}")
        except (subprocess.TimeoutExpired, OSError) as exc:
            results.append(f"{label}: skipped ({type(exc).__name__})")

    _emit(f"format_on_edit: {rel} — " + ", ".join(results))
    return 0


if __name__ == "__main__":
    sys.exit(main())
