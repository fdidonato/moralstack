"""SessionStart hook: re-injects the pre-compaction snapshot on resume/compact."""

from __future__ import annotations

import os
import time

import pytest


@pytest.fixture
def session_start(load_hook):
    return load_hook("session_start")


def _ctx(out) -> str:
    return out["hookSpecificOutput"]["additionalContext"]


def test_restores_snapshot_on_compact(session_start, run_hook, project):
    (project / ".claude" / ".context-snapshot.md").write_text("SNAP: piano al passo 2", encoding="utf-8")
    code, out = run_hook(session_start, {"source": "compact"}, project)
    assert code == 0
    assert "Restored context" in _ctx(out)
    assert "piano al passo 2" in _ctx(out)


def test_no_snapshot_gives_plain_brief(session_start, run_hook, project):
    code, out = run_hook(session_start, {"source": "startup"}, project)
    assert code == 0
    assert "Restored context" not in _ctx(out)


def test_stale_snapshot_not_restored_on_startup(session_start, run_hook, project):
    snap = project / ".claude" / ".context-snapshot.md"
    snap.write_text("SNAP: stale", encoding="utf-8")
    old = time.time() - 7200  # 2h ago, past the 1h freshness window
    os.utime(snap, (old, old))
    code, out = run_hook(session_start, {"source": "startup"}, project)
    assert code == 0
    assert "Restored context" not in _ctx(out)
