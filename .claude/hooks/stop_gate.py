#!/usr/bin/env python3
"""Stop hook: non-blocking verify report + blocking docs gate.

Fires when Claude tries to finish a turn. Acts only if code/tests were edited
this session (per ``.claude/.session-edits.json`` written by
``format_on_edit.py``). Two jobs:

1. **Verify (non-blocking).** Runs ``pre-commit run --files <edited code>`` and,
   when code/tests changed, **auto-runs pytest scoped to the impacted test
   files** (those edited directly plus ``tests/**/test_<module>*.py`` matching
   the edited modules) so each turn self-verifies fast. The full suite (~3.5 min)
   stays the ``pre-commit-verifier`` agent's job; set ``MSTACK_STOP_RUN_PYTEST=1``
   to force it here instead (raise the hook ``timeout`` in settings.json — pytest
   is slow). Outcome is reported to Claude via ``additionalContext``.
2. **Docs gate (blocking).** If governance *behavior* files were edited without
   touching the matching docs (or the behavior-locking tests), emits
   ``{"decision": "block"}`` so Claude updates docs before finishing
   (PROJECT_SPEC §8). Respects ``stop_hook_active`` to nudge once, never loop.

Best-effort: any unexpected error exits 0 so the hook can never wedge a turn.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

MARKER_NAME = ".session-edits.json"

BEHAVIOR_PREFIXES = (
    "moralstack/runtime/decision/",
    "moralstack/compliance/",
    "moralstack/orchestration/",
    "moralstack/prompts/",
    "moralstack/observability/",
    "moralstack/server/",
    "moralstack/constitution/",
)


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


def _edited_paths(project: Path, session_id: str) -> list[str]:
    marker = project / ".claude" / MARKER_NAME
    if not marker.exists():
        return []
    try:
        state = json.loads(marker.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return []
    if not isinstance(state, dict) or state.get("session_id") != session_id:
        return []
    paths = state.get("paths")
    return [p for p in paths if isinstance(p, str)] if isinstance(paths, list) else []


def _related_tests(project: Path, code: list[str]) -> list[str]:
    """Test files impacted by the edited code: edited tests themselves, plus
    ``tests/**/test_<module-stem>*.py`` for each edited ``moralstack`` module."""
    tests: set[str] = set()
    tests_dir = project / "tests"
    for rel in code:
        if rel.startswith("tests/") and rel.endswith(".py"):
            if (project / rel).exists():
                tests.add(rel)
            continue
        stem = Path(rel).stem
        if not stem or stem == "__init__":
            continue
        for match in tests_dir.glob(f"**/test_{stem}*.py"):
            try:
                tests.add(str(match.relative_to(project)).replace("\\", "/"))
            except ValueError:
                continue
    return sorted(tests)


def _run(args: list[str], project: Path, timeout: int) -> str:
    try:
        proc = subprocess.run(
            args,
            cwd=str(project),
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return "passed" if proc.returncode == 0 else "FAILED/changed"
    except subprocess.TimeoutExpired:
        return "timed out"
    except OSError as exc:
        return f"skipped ({type(exc).__name__})"


def _emit_block(reason: str) -> None:
    sys.stdout.write(json.dumps({"decision": "block", "reason": reason}))


def _emit_context(context: str) -> None:
    payload = {
        "hookSpecificOutput": {
            "hookEventName": "Stop",
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

    project = _project_dir()
    session_id = str(data.get("session_id") or "unknown")
    stop_active = bool(data.get("stop_hook_active"))

    edited = _edited_paths(project, session_id)
    code = [p for p in edited if p.startswith(("moralstack/", "tests/"))]
    if not code:
        return 0

    behavior = [p for p in code if p.startswith(BEHAVIOR_PREFIXES)]
    docs_touched = any(p.startswith("docs/") for p in edited)
    tests_touched = any(p.startswith("tests/") for p in edited)

    py = _venv_python(project)
    report = ["[Stop gate] verify (non-blocking):"]
    report.append("  pre-commit (changed files): " + _run([py, "-m", "pre_commit", "run", "--files", *code], project, 100))
    if os.environ.get("MSTACK_STOP_RUN_PYTEST", "").lower() in ("1", "true", "yes"):
        report.append(
            "  pytest (full suite): "
            + _run([py, "-m", "pytest", "-q", "--no-header", "-p", "no:cacheprovider"], project, 300)
        )
    else:
        related = _related_tests(project, code)
        if related:
            report.append(
                f"  pytest (scoped, {len(related)} file/s): "
                + _run([py, "-m", "pytest", "-q", "--no-header", "-p", "no:cacheprovider", *related], project, 150)
            )
            report.append(
                "  full suite not run here — run the pre-commit-verifier agent "
                "(or MSTACK_STOP_RUN_PYTEST=1) before declaring done."
            )
        else:
            report.append(
                "  pytest: no test file matched the edited modules; full suite "
                "skipped (run the pre-commit-verifier agent before declaring done)."
            )
    report_text = "\n".join(report)

    needs_docs = bool(behavior) and not (docs_touched or tests_touched)
    if needs_docs and not stop_active:
        reason = (
            report_text + "\n\nDOCS GATE (blocking, PROJECT_SPEC §8): you edited governance "
            "behavior files without updating the matching docs:\n  - "
            + "\n  - ".join(behavior)
            + "\nBefore finishing, update the relevant of: "
            "docs/MORALSTACK_CODEBASE_INDEX.md, docs/CODEBASE_FACTS.md, "
            "docs/TRACES/, docs/modules/*.md — or touch the behavior-locking "
            "tests if that is the right place. See .claude/rules/docs-maintenance.md."
        )
        _emit_block(reason)
        return 0

    _emit_context(report_text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
