#!/usr/bin/env python3
"""Verification gates for one UI-loop iteration.

Runs exactly what the repository's own pre-commit will run against the touched
surface, plus a scope guard. Passing this is a *necessary* condition for a
commit, never a sufficient one: browser-level and semantic verification is the
verifier subagent's job, and it can veto a green run.

Gates
-----
1. scope     — the working diff touches only moralstack/ui, tests/test_ui_*.py,
               CHANGELOG.md and .claude/ui-loop
2. changelog — CHANGELOG.md is modified whenever moralstack/ui is modified
               (the repo's changelog-guard pre-commit hook blocks otherwise)
3. ruff      — lint on the touched UI surface
4. black     — formatting check
5. mypy      — types on moralstack/ui
6. pytest    — every tests/test_ui_*.py

Usage
-----
    python .claude/skills/improve-moralstack-ui/scripts/verify.py
    python .claude/skills/improve-moralstack-ui/scripts/verify.py --full
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from fnmatch import fnmatch

from _common import ALLOWED_WRITE_GLOBS, REPO_ROOT


def _run(label: str, command: list[str]) -> bool:
    print(f"\n== {label} ==\n{' '.join(command)}", flush=True)
    result = subprocess.run(command, cwd=REPO_ROOT, check=False)
    ok = result.returncode == 0
    print(f"-- {label}: {'PASS' if ok else 'FAIL'}", flush=True)
    return ok


def _changed_files() -> list[str]:
    result = subprocess.run(["git", "status", "--porcelain"], cwd=REPO_ROOT, capture_output=True, text=True, check=False)
    return [line[3:].strip() for line in result.stdout.splitlines() if line.strip()]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--full", action="store_true", help="also run the whole non-slow suite")
    args = parser.parse_args()

    ui_tests = sorted(str(p.relative_to(REPO_ROOT)) for p in (REPO_ROOT / "tests").glob("test_ui_*.py"))
    if not ui_tests:
        print("FAIL: no tests/test_ui_*.py found", file=sys.stderr)
        return 1

    results: dict[str, bool] = {}

    changed = _changed_files()
    out_of_scope = [
        path
        for path in changed
        if not any(fnmatch(path, glob) or path.startswith(glob.rstrip("*")) for glob in ALLOWED_WRITE_GLOBS)
    ]
    results["scope"] = not out_of_scope
    print("== scope ==")
    if out_of_scope:
        print("FAIL: the iteration touched files outside its mandate:")
        for path in out_of_scope:
            print(f"  {path}")
    else:
        print(f"PASS: {len(changed)} changed file(s), all in scope")

    touched_ui = any(path.startswith("moralstack/ui/") for path in changed)
    changelog_updated = "CHANGELOG.md" in changed
    results["changelog"] = (not touched_ui) or changelog_updated
    print("\n== changelog ==")
    if results["changelog"]:
        print("PASS")
    else:
        print("FAIL: moralstack/ui changed but CHANGELOG.md was not updated.")
        print("      The repo's changelog-guard pre-commit hook will reject the commit.")
        print("      Add a bullet under [Unreleased] and stage it.")

    python = sys.executable
    results["ruff"] = _run("ruff", [python, "-m", "ruff", "check", "moralstack/ui", *ui_tests])
    results["black"] = _run("black", [python, "-m", "black", "--check", "moralstack/ui", *ui_tests])
    results["mypy"] = _run("mypy", [python, "-m", "mypy", "moralstack/ui"])
    results["pytest-ui"] = _run("pytest (ui)", [python, "-m", "pytest", "-q", *ui_tests])
    if args.full:
        results["pytest-full"] = _run("pytest (full, not slow)", [python, "-m", "pytest", "-q", "-m", "not slow"])

    print("\n== summary ==")
    for gate, ok in results.items():
        print(f"  {'PASS' if ok else 'FAIL'}  {gate}")
    failed = [gate for gate, ok in results.items() if not ok]
    if failed:
        print(f"\nFAILED GATES: {', '.join(failed)}")
        return 1
    print("\nAll verification gates passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
