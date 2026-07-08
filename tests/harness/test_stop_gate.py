"""Stop-gate hook: verify dedup/skip, docs-gate blocking + nudge cap, docs stub."""

from __future__ import annotations

import json

import pytest
from conftest import stub_subprocess, write_code_file, write_session_edits


@pytest.fixture
def stop_gate(load_hook):
    return load_hook("stop_gate")


def _payload(session_id: str = "s1", stop_active: bool = False) -> dict:
    return {"session_id": session_id, "stop_hook_active": stop_active}


def test_no_code_edited_is_noop(stop_gate, run_hook, project):
    write_session_edits(project, "s1", ["docs/only.md"])
    code, out = run_hook(stop_gate, _payload(), project)
    assert code == 0
    assert out is None  # nothing emitted


def test_dedup_skips_second_identical_run(stop_gate, run_hook, project, monkeypatch):
    recorder: list = []
    stub_subprocess(monkeypatch, stop_gate, returncode=0, recorder=recorder)
    write_code_file(project, "tests/test_foo.py", "def test_foo():\n    assert True\n")
    write_session_edits(project, "s1", ["tests/test_foo.py"])

    code, out = run_hook(stop_gate, _payload(), project)
    assert code == 0 and recorder, "first run must execute verify"
    assert out is not None, "a fresh verify emits its report"
    assert (project / ".claude" / ".last-verified.json").exists()

    recorder.clear()
    code, out = run_hook(stop_gate, _payload(), project)
    assert code == 0 and not recorder, "unchanged edit-set must skip verify"
    assert out is None, "a skipped verify emits nothing (no re-wake loop)"


def test_content_change_reruns_verify(stop_gate, run_hook, project, monkeypatch):
    recorder: list = []
    stub_subprocess(monkeypatch, stop_gate, returncode=0, recorder=recorder)
    write_code_file(project, "tests/test_foo.py", "def test_foo():\n    assert True\n")
    write_session_edits(project, "s1", ["tests/test_foo.py"])
    run_hook(stop_gate, _payload(), project)

    recorder.clear()
    write_code_file(project, "tests/test_foo.py", "def test_foo():\n    assert 1 == 1\n")
    code, out = run_hook(stop_gate, _payload(), project)
    assert code == 0 and recorder, "changed content must re-run verify"


def test_failed_verify_is_not_deduped(stop_gate, run_hook, project, monkeypatch):
    recorder: list = []
    stub_subprocess(monkeypatch, stop_gate, returncode=1, recorder=recorder)
    write_code_file(project, "tests/test_foo.py")
    write_session_edits(project, "s1", ["tests/test_foo.py"])
    run_hook(stop_gate, _payload(), project)
    assert json.loads((project / ".claude" / ".last-verified.json").read_text())["outcome"] == "failed"

    recorder.clear()
    run_hook(stop_gate, _payload(), project)
    assert recorder, "a previously failed verify must not be skipped"


def test_stop_active_skips_verify(stop_gate, run_hook, project, monkeypatch):
    recorder: list = []
    stub_subprocess(monkeypatch, stop_gate, returncode=0, recorder=recorder)
    write_code_file(project, "moralstack/orchestration/foo.py")
    write_session_edits(project, "s1", ["moralstack/orchestration/foo.py"])
    code, out = run_hook(stop_gate, _payload(stop_active=True), project)
    assert code == 0 and not recorder
    assert out is None, "stop_hook_active skips verify and emits nothing (no re-wake loop)"


def test_docs_gate_blocks_then_caps(stop_gate, run_hook, project, monkeypatch):
    stub_subprocess(monkeypatch, stop_gate, returncode=0)
    write_code_file(project, "moralstack/orchestration/foo.py")
    write_session_edits(project, "s1", ["moralstack/orchestration/foo.py"])

    # First Stop chain (stop_active False): block + write stub + bump nudge.
    code, out = run_hook(stop_gate, _payload(), project)
    assert code == 0 and out["decision"] == "block"
    assert "DOCS GATE" in out["reason"]
    assert (project / ".claude" / ".docs-stub.md").exists()
    assert json.loads((project / ".claude" / ".nudge-count.json").read_text())["count"] == 1

    # A separate chain (stop_active False again) must NOT block: cap reached.
    # Verify is deduped (unchanged edit-set already passed) so nothing is emitted.
    code, out = run_hook(stop_gate, _payload(), project)
    assert code == 0 and out is None


def test_docs_gate_not_satisfied_by_touching_tests(stop_gate, run_hook, project, monkeypatch):
    """A test must NOT silence the docs gate — the loophole this fix closes.

    The workflow almost always adds a test, so counting a test as a docs substitute
    made the gate inert in exactly the cases that matter.
    """
    stub_subprocess(monkeypatch, stop_gate, returncode=0)
    write_code_file(project, "moralstack/orchestration/foo.py")
    write_code_file(project, "tests/test_foo.py")
    write_session_edits(project, "s1", ["moralstack/orchestration/foo.py", "tests/test_foo.py"])
    code, out = run_hook(stop_gate, _payload(), project)
    assert code == 0 and out["decision"] == "block"


def test_docs_gate_satisfied_by_memory_ledger(stop_gate, run_hook, project, monkeypatch):
    """Touching a verified-memory ledger (CODEBASE_FACTS) satisfies the docs gate."""
    stub_subprocess(monkeypatch, stop_gate, returncode=0)
    write_code_file(project, "moralstack/orchestration/foo.py")
    write_session_edits(project, "s1", ["moralstack/orchestration/foo.py", "docs/CODEBASE_FACTS.md"])
    code, out = run_hook(stop_gate, _payload(), project)
    assert code == 0 and out.get("decision") != "block"


def test_docs_gate_not_satisfied_by_arbitrary_docs(stop_gate, run_hook, project, monkeypatch):
    """An unrelated docs/ file no longer counts (the old any-``docs/`` loophole)."""
    stub_subprocess(monkeypatch, stop_gate, returncode=0)
    write_code_file(project, "moralstack/orchestration/foo.py")
    write_session_edits(project, "s1", ["moralstack/orchestration/foo.py", "docs/refactoring_diary.md"])
    code, out = run_hook(stop_gate, _payload(), project)
    assert code == 0 and out["decision"] == "block"


def test_symbols_from_diff_is_pure(stop_gate):
    diff = (
        "diff --git a/x.py b/x.py\n"
        "--- a/x.py\n+++ b/x.py\n"
        "@@\n"
        "+def new_fn(a):\n"
        "-    async def old_co():\n"
        "+class NewCls:\n"
        "+    x = 1\n"
    )
    assert stop_gate._symbols_from_diff(diff) == ["new_fn", "old_co", "NewCls"]


def test_nudge_cap_env_override(stop_gate, run_hook, project, monkeypatch):
    monkeypatch.setenv("MSTACK_DOCS_NUDGE_CAP", "0")
    stub_subprocess(monkeypatch, stop_gate, returncode=0)
    write_code_file(project, "moralstack/orchestration/foo.py")
    write_session_edits(project, "s1", ["moralstack/orchestration/foo.py"])
    code, out = run_hook(stop_gate, _payload(), project)
    assert code == 0 and out.get("decision") != "block", "cap 0 disables blocking"
