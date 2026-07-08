#!/usr/bin/env python3
"""PreCompact hook: snapshot the in-flight context before it is compacted.

Auto-compaction silently drops the running plan/task. This hook writes a short
digest to ``.claude/.context-snapshot.md`` **before** compaction so
``session_start.py`` can re-inject it when the session resumes (SessionStart
``source`` in {compact, resume}). PreCompact cannot emit ``additionalContext``,
so the snapshot travels through a file on disk.

Sources (all best-effort, all optional):
- the session transcript tail (``transcript_path`` in the hook input, read from
  disk so it survives compaction),
- the edited-file set (``.claude/.session-edits.json``),
- the last verify outcome (``.claude/.last-verified.json``),
- the current git branch.

Registered ``async: true`` so it never delays compaction. Any error exits 0.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

SNAPSHOT_NAME = ".context-snapshot.md"
MAX_MESSAGES = 30
MAX_CHARS = 16000
MAX_MSG_CHARS = 1200


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


def _read_json(path: Path) -> dict:
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
        return state if isinstance(state, dict) else {}
    except (ValueError, OSError):
        return {}


def _block_text(content) -> str:
    """Extract plain text from a transcript message ``content`` field, which may
    be a string or a list of typed blocks."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, dict) and isinstance(block.get("text"), str):
                parts.append(block["text"])
        return "\n".join(parts)
    return ""


def _transcript_tail(transcript_path: str) -> list[str]:
    """Last few user/assistant text turns from the JSONL transcript. Defensive:
    unknown shapes are skipped, not fatal."""
    path = Path(transcript_path)
    if not path.exists():
        return []
    try:
        raw = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    turns: list[str] = []
    for line in raw:
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if not isinstance(entry, dict):
            continue
        role = entry.get("type") or entry.get("role")
        message = entry.get("message")
        if isinstance(message, dict):
            role = message.get("role", role)
            text = _block_text(message.get("content"))
        else:
            text = _block_text(entry.get("content"))
        text = (text or "").strip()
        if role in ("user", "assistant") and text:
            if len(text) > MAX_MSG_CHARS:
                text = text[:MAX_MSG_CHARS] + " …[troncato]"
            turns.append(f"**{role}:** {text}")
    return turns[-MAX_MESSAGES:]


def main() -> int:
    try:
        data = json.loads(sys.stdin.read() or "{}")
    except (ValueError, TypeError):
        return 0
    if not isinstance(data, dict):
        return 0

    project = _project_dir()
    session_id = str(data.get("session_id") or "unknown")
    trigger = str(data.get("trigger") or "?")
    transcript_path = data.get("transcript_path")

    branch = _git(["rev-parse", "--abbrev-ref", "HEAD"], project) or "?"
    edits = _read_json(project / ".claude" / ".session-edits.json")
    edited = edits.get("paths") if edits.get("session_id") == session_id else None
    edited = [p for p in edited if isinstance(p, str)] if isinstance(edited, list) else []
    verified = _read_json(project / ".claude" / ".last-verified.json")
    verify_outcome = verified.get("outcome") if verified.get("session_id") == session_id else None

    lines = [
        "# Context snapshot (pre-compaction)",
        "",
        f"- Saved: {datetime.now().isoformat(timespec='seconds')} (trigger: {trigger})",
        f"- Branch: {branch}",
        f"- Session: {session_id}",
        f"- Last verify outcome: {verify_outcome or 'n/a'}",
        "",
        "## Files edited this session",
    ]
    if edited:
        lines.extend(f"- {p}" for p in edited[:60])
    else:
        lines.append("- _(none recorded)_")
    lines.append("")
    lines.append("## Recent conversation (tail)")
    tail = _transcript_tail(transcript_path) if isinstance(transcript_path, str) else []
    if tail:
        lines.extend(tail)
    else:
        lines.append("_(transcript unavailable)_")

    text = "\n".join(lines)
    if len(text) > MAX_CHARS:
        text = text[:MAX_CHARS] + "\n\n…[snapshot troncato]"

    snapshot = project / ".claude" / SNAPSHOT_NAME
    try:
        snapshot.parent.mkdir(parents=True, exist_ok=True)
        snapshot.write_text(text, encoding="utf-8")
    except OSError:
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
