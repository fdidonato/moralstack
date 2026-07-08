"""UserPromptSubmit hook: keyword-gated context injection, silent otherwise."""

from __future__ import annotations

import pytest


@pytest.fixture
def ups(load_hook):
    return load_hook("user_prompt_submit")


def test_silent_on_ordinary_prompt(ups, run_hook, project):
    code, out = run_hook(ups, {"prompt": "fixa il bug nel parser"}, project)
    assert code == 0 and out is None


def test_injects_snapshot_and_plans_on_keyword(ups, run_hook, project):
    (project / ".claude" / ".context-snapshot.md").write_text("piano corrente: passo 3", encoding="utf-8")
    plans = project / "ai" / "plans"
    plans.mkdir(parents=True)
    (plans / "my-plan.md").write_text("# Plan", encoding="utf-8")
    (plans / ".gitkeep").write_text("", encoding="utf-8")  # empty → excluded

    code, out = run_hook(ups, {"prompt": "riprendi il piano di prima"}, project)
    assert code == 0
    ctx = out["hookSpecificOutput"]["additionalContext"]
    assert "passo 3" in ctx
    assert "my-plan.md" in ctx
    assert ".gitkeep" not in ctx


def test_keyword_but_nothing_available_is_silent(ups, run_hook, project):
    code, out = run_hook(ups, {"prompt": "dov'è il piano?"}, project)
    assert code == 0 and out is None
