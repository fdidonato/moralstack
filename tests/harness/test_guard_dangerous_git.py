"""F5(a): guard_dangerous_git blocks the exact PROJECT_SPEC §9 shortcuts and
nothing more — in particular it does NOT block a hard `git reset`, and the
README says so (positive wording, not a substring tautology)."""

from __future__ import annotations

import pytest


@pytest.fixture
def guard(load_hook):
    return load_hook("guard_dangerous_git")


def _bash(cmd: str) -> dict:
    return {"tool_name": "Bash", "tool_input": {"command": cmd}}


BLOCK_CASES = [
    "git commit --no-verify -m 'x'",
    "git commit -n -m 'x'",
    "git commit --no-gpg-sign -m 'x'",
    "git -c commit.gpgsign=false commit -m 'x'",
    "git push --force origin main",
    "git push -f origin main",
    "git push --force-with-lease origin main",
    "rm tests/test_foo.py",
    "rm -rf tests/",
]

ALLOW_CASES = [
    "git commit -m 'feat: x'",
    "git push origin feature-branch",
    "rm -rf build/",
    "git status",
]


@pytest.mark.parametrize("cmd", BLOCK_CASES)
def test_dangerous_git_blocked(guard, run_hook, project, cmd):
    code, _ = run_hook(guard, _bash(cmd), project)
    assert code == 2, f"expected block for: {cmd!r}"


@pytest.mark.parametrize("cmd", ALLOW_CASES)
def test_dangerous_git_allowed(guard, run_hook, project, cmd):
    code, _ = run_hook(guard, _bash(cmd), project)
    assert code == 0, f"expected allow for: {cmd!r}"


def test_reset_hard_is_not_blocked_by_design(guard, run_hook, project):
    """Decision (F5(a)): a hard git reset is destructive-but-confirmable, not
    one of the PROJECT_SPEC §9 shortcuts; a PreToolUse hook has no confirmation
    channel, so it stays unblocked and the user's own confirmation rule applies."""
    assert guard._violations("git reset --hard HEAD") == []
    code, _ = run_hook(guard, _bash("git reset --hard HEAD"), project)
    assert code == 0


def test_readme_states_hard_reset_is_not_blocked(repo_root):
    text = (repo_root / ".claude" / "hooks" / "README.md").read_text(encoding="utf-8")
    assert "Does **not** block a hard `git reset`" in text
