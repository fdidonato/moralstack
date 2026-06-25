#!/usr/bin/env python3
"""PreToolUse(Bash) guard: block destructive git/test shortcuts.

Enforces PROJECT_SPEC.md §9 deterministically (not just as guidance):
no ``--no-verify``, no gpg-sign bypass, no force-push of any kind, and no
deletion of test files. Reads the hook JSON from stdin; blocks with exit
code 2 (stderr is shown to Claude) when a forbidden pattern is found, and
exits 0 (allow) otherwise. Fails open on malformed input so a hook bug can
never wedge the session.

Fallback interpreter: ``py`` if ``python`` is unavailable on PATH.
"""

from __future__ import annotations

import json
import re
import sys


def _command(data: dict) -> str:
    tool_input = data.get("tool_input")
    if not isinstance(tool_input, dict):
        return ""
    command = tool_input.get("command")
    return command if isinstance(command, str) else ""


def _violations(cmd: str) -> list[str]:
    found: list[str] = []
    is_commit = re.search(r"git\s+commit\b", cmd) is not None
    is_push = re.search(r"git\s+push\b", cmd) is not None
    has_rm = re.search(r"(?:^|\s|;|&&|\|)\s*(?:git\s+)?rm\b", cmd) is not None

    if is_commit and re.search(r"(?:^|\s)--no-verify\b", cmd):
        found.append("`git commit --no-verify` bypasses pre-commit hooks (§9).")
    if is_commit and re.search(r"(?:^|\s)-n(?:\s|$)", cmd):
        found.append("`git commit -n` (--no-verify) bypasses pre-commit hooks (§9).")

    if re.search(r"--no-gpg-sign\b", cmd):
        found.append("`--no-gpg-sign` bypasses commit signing (§9).")
    if re.search(r"commit\.gpgsign\s*=\s*false", cmd, re.IGNORECASE):
        found.append("`-c commit.gpgsign=false` bypasses commit signing (§9).")

    if is_push and re.search(r"--force\b", cmd):
        found.append("force-push is forbidden — no exception, not even " "--force-with-lease (§9 invariant).")
    if is_push and re.search(r"(?:^|\s)-f(?:\s|$)", cmd):
        found.append("`git push -f` (force) is forbidden (§9 invariant).")

    if has_rm and (re.search(r"\btests/[^\s]*test[^\s]*\.py", cmd) or re.search(r"(?:^|\s)tests/?(?:\s|$)", cmd)):
        found.append("deleting test files/dirs is forbidden — fix the root " "cause, never delete a failing test (§9).")
    return found


def main() -> int:
    try:
        data = json.loads(sys.stdin.read() or "{}")
    except (ValueError, TypeError):
        return 0
    if not isinstance(data, dict):
        return 0

    cmd = _command(data)
    if not cmd.strip():
        return 0

    violations = _violations(cmd)
    if violations:
        sys.stderr.write(
            "Blocked by guard_dangerous_git (PROJECT_SPEC §9):\n  - "
            + "\n  - ".join(violations)
            + "\nFind the root cause instead of bypassing the guard.\n"
        )
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
