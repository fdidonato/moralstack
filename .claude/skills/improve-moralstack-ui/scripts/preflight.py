#!/usr/bin/env python3
"""Preflight for one UI-loop iteration.

Checks everything the iteration will assume, and fails loudly *before* the model
starts editing. A blocked preflight is a cheap iteration; a half-applied edit on
a broken environment is not.

Usage
-----
    python .claude/skills/improve-moralstack-ui/scripts/preflight.py
    python .claude/skills/improve-moralstack-ui/scripts/preflight.py --require-clean
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from fnmatch import fnmatch

from _common import ALLOWED_WRITE_GLOBS, REPO_ROOT, db_path, ui_base_url

REQUIRED_PATHS = (
    "pyproject.toml",
    "moralstack/ui/app.py",
    "moralstack/ui/templates/request.html",
    "moralstack/ui/templates/conversation.html",
    "moralstack/ui/static/css/main.css",
    ".claude/skills/mstack-run/SKILL.md",
    ".claude/skills/improve-moralstack-ui/SKILL.md",
    "CHANGELOG.md",
)


def _git(*args: str) -> str:
    """Raw stdout — never .strip(), it would eat the leading status column."""
    result = subprocess.run(["git", *args], cwd=REPO_ROOT, capture_output=True, text=True, check=False)
    return result.stdout


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--require-clean", action="store_true", help="fail on any uncommitted change outside loop scope")
    args = parser.parse_args()

    failures: list[str] = []
    warnings: list[str] = []

    for relative in REQUIRED_PATHS:
        if not (REPO_ROOT / relative).exists():
            failures.append(f"missing required path: {relative}")

    ui_tests = sorted((REPO_ROOT / "tests").glob("test_ui_*.py"))
    if not ui_tests:
        failures.append("no tests/test_ui_*.py found; the loop has no regression net")

    if shutil.which("node") is None:
        failures.append("Node.js not found; the Playwright MCP server cannot start")

    if shutil.which("git") is None:
        failures.append("git not found")
    else:
        dirty = [line[3:].strip() for line in _git("status", "--porcelain").splitlines() if line.strip()]
        out_of_scope = [
            path
            for path in dirty
            if not any(fnmatch(path, glob) or path.startswith(glob.rstrip("*")) for glob in ALLOWED_WRITE_GLOBS)
        ]
        if out_of_scope:
            message = "uncommitted changes outside loop scope: " + ", ".join(out_of_scope[:8])
            (failures if args.require_clean else warnings).append(message)
            if not args.require_clean:
                warnings.append("attribution and rollback are unsafe while these exist — commit or stash them")

    database = db_path()
    if database is None:
        failures.append("MORALSTACK_OBSERVABILITY_DB_PATH is not set (.env or process env)")
    elif not database.is_file():
        failures.append("the observability DB path is set but the file does not exist; record some runs first")

    print("MoralStack UI-loop preflight")
    print(f"  repo:      {REPO_ROOT}")
    print(f"  python:    {sys.executable}")
    print(f"  ui tests:  {len(ui_tests)}")
    print(f"  ui url:    {ui_base_url()}")
    print(f"  obs db:    {database if database else 'MISSING'}")
    print()

    for warning in warnings:
        print(f"WARN: {warning}")
    for failure in failures:
        print(f"FAIL: {failure}", file=sys.stderr)

    if failures:
        return 1
    print("Preflight passed. Next: ui_login.py, then scenarios.py.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
