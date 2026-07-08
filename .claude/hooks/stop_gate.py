#!/usr/bin/env python3
"""Stop hook: non-blocking verify report + blocking docs gate.

Fires when Claude tries to finish a turn. Acts only if code/tests were edited
this session (per ``.claude/.session-edits.json`` written by
``format_on_edit.py``). Three jobs:

1. **Verify (non-blocking).** Runs ``pre-commit run --files <edited code>`` and,
   when code/tests changed, **auto-runs pytest scoped to the impacted test
   files** (those edited directly plus ``tests/**/test_<module>*.py`` matching
   the edited modules) so each turn self-verifies fast. The full suite (~3.5 min)
   stays the ``pre-commit-verifier`` agent's job; set ``MSTACK_STOP_RUN_PYTEST=1``
   to force it here instead (raise the hook ``timeout`` in settings.json — pytest
   is slow). Outcome is reported to Claude via ``additionalContext``.

   The verify is **skipped** (to avoid redundant work) when either
   ``stop_hook_active`` is True (we are inside a nudge chain) or the edit-set
   fingerprint matches the last run that already passed
   (``.claude/.last-verified.json``).
2. **Docs gate (blocking).** If governance *behavior* files were edited without
   touching the matching docs (or the behavior-locking tests), emits
   ``{"decision": "block"}`` so Claude updates docs before finishing
   (PROJECT_SPEC §8). Nudges at most ``MSTACK_DOCS_NUDGE_CAP`` (default 1) times
   per session — a persisted counter (``.claude/.nudge-count.json``) holds the
   cap across separate Stop chains, while ``stop_hook_active`` holds it within a
   chain. When it blocks it also writes a docs-update **stub**
   (``.claude/.docs-stub.md``) listing the touched symbols so the update is
   "review and promote" rather than "write from scratch".

Best-effort: any unexpected error exits 0 so the hook can never wedge a turn.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path

MARKER_NAME = ".session-edits.json"
VERIFIED_NAME = ".last-verified.json"
NUDGE_NAME = ".nudge-count.json"
STUB_NAME = ".docs-stub.md"

BEHAVIOR_PREFIXES = (
    "moralstack/runtime/decision/",
    "moralstack/compliance/",
    "moralstack/orchestration/",
    "moralstack/prompts/",
    "moralstack/observability/",
    "moralstack/server/",
    "moralstack/constitution/",
)

# Map an edited behavior path to the docs most likely to need an update. Kept
# intentionally coarse — the stub is a starting point, not an authority.
_DOCS_HINTS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("moralstack/orchestration/", ("docs/modules/orchestrator.md", "docs/TRACES/governance_decision_flow.md")),
    ("moralstack/observability/", ("docs/modules/observability.md", "docs/TRACES/observability_db_to_ui.md")),
    ("moralstack/server/", ("docs/TRACES/openai_compatible_multiturn.md", "docs/TRACES/observability_db_to_ui.md")),
    ("moralstack/constitution/", ("docs/modules/constitution_store.md", "docs/constitution.md")),
    ("moralstack/compliance/", ("docs/TRACES/complai_llm_rules_flow.md",)),
    ("moralstack/prompts/", ("docs/decision_policy.md",)),
    ("moralstack/runtime/decision/", ("docs/decision_policy.md",)),
)
_DOCS_ALWAYS = ("docs/MORALSTACK_CODEBASE_INDEX.md", "docs/CODEBASE_FACTS.md")

_SYMBOL_RE = re.compile(r"^[+-]\s*(?:async\s+def|def|class)\s+([A-Za-z_]\w*)")


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


def _fingerprint(project: Path, code: list[str]) -> str:
    """Content fingerprint of the edited code set. Two Stop firings with the same
    file contents produce the same fingerprint, so an unchanged edit-set that
    already passed verify can be skipped."""
    digest = hashlib.sha256()
    for rel in sorted(code):
        digest.update(rel.encode("utf-8"))
        try:
            digest.update(hashlib.sha256((project / rel).read_bytes()).digest())
        except OSError:
            digest.update(b"\x00missing")
    return digest.hexdigest()


def _last_verified(project: Path, session_id: str) -> dict:
    marker = project / ".claude" / VERIFIED_NAME
    if not marker.exists():
        return {}
    try:
        state = json.loads(marker.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return {}
    if isinstance(state, dict) and state.get("session_id") == session_id:
        return state
    return {}


def _save_last_verified(project: Path, session_id: str, fingerprint: str, outcome: str) -> None:
    marker = project / ".claude" / VERIFIED_NAME
    try:
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text(
            json.dumps({"session_id": session_id, "fingerprint": fingerprint, "outcome": outcome}),
            encoding="utf-8",
        )
    except OSError:
        pass


def _nudge_count(project: Path, session_id: str) -> int:
    marker = project / ".claude" / NUDGE_NAME
    if not marker.exists():
        return 0
    try:
        state = json.loads(marker.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return 0
    if isinstance(state, dict) and state.get("session_id") == session_id:
        value = state.get("count")
        return value if isinstance(value, int) and value >= 0 else 0
    return 0


def _bump_nudge(project: Path, session_id: str, count: int) -> None:
    marker = project / ".claude" / NUDGE_NAME
    try:
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text(json.dumps({"session_id": session_id, "count": count}), encoding="utf-8")
    except OSError:
        pass


def _symbols_from_diff(diff_text: str) -> list[str]:
    """Names of functions/classes added or removed in a unified diff. Pure so it
    is testable without invoking git."""
    seen: list[str] = []
    for line in diff_text.splitlines():
        if line.startswith(("+++", "---")):
            continue
        match = _SYMBOL_RE.match(line)
        if match:
            name = match.group(1)
            if name not in seen:
                seen.append(name)
    return seen


def _docs_targets(rel: str) -> list[str]:
    targets: list[str] = list(_DOCS_ALWAYS)
    for prefix, hints in _DOCS_HINTS:
        if rel.startswith(prefix):
            targets.extend(h for h in hints if h not in targets)
    return targets


def _write_docs_stub(project: Path, behavior: list[str]) -> str | None:
    """Write a docs-update stub listing the symbols touched in each behavior file
    plus the docs most likely to need an update. Returns the relative stub path,
    or None on failure. Best-effort — a missing git or diff just yields a thinner
    stub."""
    lines = [
        "# Docs update stub (auto-generated — review and promote)",
        "",
        "Generated by the Stop docs gate. This is a STARTING POINT, not an",
        "authority: verify every symbol against the code and update the real docs",
        "(PROJECT_SPEC §8). Delete this file once the docs are updated.",
        "",
    ]
    for rel in behavior:
        diff_text = ""
        try:
            proc = subprocess.run(
                ["git", "diff", "HEAD", "--", rel],
                cwd=str(project),
                capture_output=True,
                text=True,
                timeout=15,
            )
            if proc.returncode == 0:
                diff_text = proc.stdout
        except (OSError, subprocess.TimeoutExpired):
            diff_text = ""
        symbols = _symbols_from_diff(diff_text)
        lines.append(f"## `{rel}`")
        lines.append("- Touched symbols: " + (", ".join(f"`{s}`" for s in symbols) if symbols else "_(none detected)_"))
        lines.append("- Update: " + ", ".join(_docs_targets(rel)))
        lines.append("")
    marker = project / ".claude" / STUB_NAME
    try:
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text("\n".join(lines), encoding="utf-8")
        return f".claude/{STUB_NAME}"
    except OSError:
        return None


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


def _verify(project: Path, code: list[str]) -> tuple[str, bool]:
    """Run the non-blocking verify. Returns (report_text, passed)."""
    py = _venv_python(project)
    report = ["[Stop gate] verify (non-blocking):"]
    outcomes: list[str] = []

    precommit = _run([py, "-m", "pre_commit", "run", "--files", *code], project, 100)
    outcomes.append(precommit)
    report.append("  pre-commit (changed files): " + precommit)

    if os.environ.get("MSTACK_STOP_RUN_PYTEST", "").lower() in ("1", "true", "yes"):
        result = _run([py, "-m", "pytest", "-q", "--no-header", "-p", "no:cacheprovider"], project, 300)
        outcomes.append(result)
        report.append("  pytest (full suite): " + result)
    else:
        related = _related_tests(project, code)
        if related:
            result = _run(
                [py, "-m", "pytest", "-q", "--no-header", "-p", "no:cacheprovider", *related],
                project,
                150,
            )
            outcomes.append(result)
            report.append(f"  pytest (scoped, {len(related)} file/s): " + result)
            report.append(
                "  full suite not run here — run the pre-commit-verifier agent "
                "(or MSTACK_STOP_RUN_PYTEST=1) before declaring done."
            )
        else:
            report.append(
                "  pytest: no test file matched the edited modules; full suite "
                "skipped (run the pre-commit-verifier agent before declaring done)."
            )

    passed = all(o == "passed" for o in outcomes)
    return "\n".join(report), passed


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

    # ---- Verify (skipped when redundant) ------------------------------------
    fingerprint = _fingerprint(project, code)
    last = _last_verified(project, session_id)
    already_ok = last.get("fingerprint") == fingerprint and last.get("outcome") == "passed"
    verify_ran = False
    if stop_active:
        report_text = "[Stop gate] verify skipped (stop_hook_active — inside a nudge chain)."
    elif already_ok:
        report_text = "[Stop gate] verify skipped (edit-set unchanged since last passing verify)."
    else:
        report_text, passed = _verify(project, code)
        _save_last_verified(project, session_id, fingerprint, "passed" if passed else "failed")
        verify_ran = True

    # ---- Docs gate (blocking, capped) ---------------------------------------
    needs_docs = bool(behavior) and not (docs_touched or tests_touched)
    if needs_docs:
        try:
            cap = int(os.environ.get("MSTACK_DOCS_NUDGE_CAP", "1"))
        except ValueError:
            cap = 1
        count = _nudge_count(project, session_id)
        if not stop_active and count < cap:
            stub = _write_docs_stub(project, behavior)
            _bump_nudge(project, session_id, count + 1)
            reason = (
                report_text + "\n\nDOCS GATE (blocking, PROJECT_SPEC §8): you edited governance "
                "behavior files without updating the matching docs:\n  - "
                + "\n  - ".join(behavior)
                + "\nBefore finishing, update the relevant of: "
                "docs/MORALSTACK_CODEBASE_INDEX.md, docs/CODEBASE_FACTS.md, "
                "docs/TRACES/, docs/modules/*.md — or touch the behavior-locking "
                "tests if that is the right place. See .claude/rules/docs-maintenance.md."
            )
            if stub:
                reason += (
                    f"\n\nA docs-update stub (touched symbols + likely doc targets) is staged at "
                    f"{stub} — review and promote it, then delete it."
                )
            _emit_block(reason)
            return 0
        # Cap reached (or intra-chain): remind without blocking so we never nag in a loop.
        report_text += (
            "\n\n[docs reminder] behavior files still lack a matching docs update "
            "(nudge cap reached this session): " + ", ".join(behavior)
        )

    # Emit context ONLY when a fresh verify ran: a Stop-hook additionalContext
    # re-wakes Claude ("the conversation continues"), so re-emitting a "verify
    # skipped" note on every subsequent Stop would loop. When verify was skipped
    # (stop_hook_active or unchanged edit-set) there is nothing new to report.
    if verify_ran:
        _emit_context(report_text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
