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


# ---- F1: docs/traces casing --------------------------------------------------


def test_docs_gate_satisfied_by_trace_doc(stop_gate, run_hook, project, monkeypatch):
    """Touching a docs/traces/ doc satisfies the docs gate (the F1 root cause:
    MEMORY_DOC_PREFIXES / _DOCS_HINTS used to declare the wrong on-disk casing,
    which never matched the real lowercase directory)."""
    stub_subprocess(monkeypatch, stop_gate, returncode=0)
    write_code_file(project, "moralstack/orchestration/foo.py")
    write_session_edits(
        project,
        "s1",
        ["moralstack/orchestration/foo.py", "docs/traces/governance_decision_flow.md"],
    )
    code, out = run_hook(stop_gate, _payload(), project)
    assert code == 0 and out.get("decision") != "block"


# ---- F2: Stop gate runs pre-commit only, never pytest ------------------------


def test_verify_never_invokes_pytest(stop_gate, run_hook, project, monkeypatch):
    recorder: list = []
    stub_subprocess(monkeypatch, stop_gate, returncode=0, recorder=recorder)
    write_code_file(project, "moralstack/orchestration/foo.py")
    write_code_file(project, "tests/test_foo.py")
    write_code_file(project, "docs/CODEBASE_FACTS.md")
    write_session_edits(
        project,
        "s1",
        ["moralstack/orchestration/foo.py", "tests/test_foo.py", "docs/CODEBASE_FACTS.md"],
    )
    code, out = run_hook(stop_gate, _payload(), project)
    assert code == 0
    assert len(recorder) == 1, "exactly one subprocess call (pre-commit) — no scoped/full pytest"
    assert recorder[0][1:3] == ["-m", "pre_commit"]
    assert not any("pytest" in str(part) for part in recorder[0])


@pytest.mark.parametrize("value", ["1", "true", "yes"])
def test_legacy_pytest_env_var_is_ignored(stop_gate, run_hook, project, monkeypatch, value):
    monkeypatch.setenv("MSTACK_STOP_RUN_PYTEST", value)
    recorder: list = []
    stub_subprocess(monkeypatch, stop_gate, returncode=0, recorder=recorder)
    write_code_file(project, "tests/test_foo.py")
    write_session_edits(project, "s1", ["tests/test_foo.py"])
    code, out = run_hook(stop_gate, _payload(), project)
    assert code == 0
    assert len(recorder) == 1
    assert not any("pytest" in str(part) for part in recorder[0])


def test_related_tests_helper_is_gone(stop_gate):
    assert not hasattr(stop_gate, "_related_tests")


@pytest.mark.parametrize("returncode", [0, 1])
def test_verify_report_states_no_tests_run(stop_gate, run_hook, project, monkeypatch, returncode):
    """No suite_record.py in the project: the hook cannot read a record, so it asks
    for the verifier instead of claiming anything about the suite."""
    stub_subprocess(monkeypatch, stop_gate, returncode=returncode)
    write_code_file(project, "tests/test_foo.py")
    write_session_edits(project, "s1", ["tests/test_foo.py"])
    code, out = run_hook(stop_gate, _payload(), project)
    assert code == 0
    context = out["hookSpecificOutput"]["additionalContext"]
    assert "tests: not run by this hook" in context
    assert "pre-commit-verifier" in context


@pytest.mark.parametrize(
    ("returncode", "expected", "asks_verifier"),
    [
        (0, "full suite PASSED for this exact tree", False),
        (1, "no PASSED full-suite record for this tree", True),
    ],
)
def test_verify_report_reads_the_suite_record(
    stop_gate, run_hook, project, monkeypatch, returncode, expected, asks_verifier
):
    """A suite already run on this tree is reported as such (it used to be reported as
    "no tests were run in this turn" unconditionally); the hook only runs
    `suite_record.py check`, never pytest."""
    calls: list = []
    stub_subprocess(monkeypatch, stop_gate, returncode=returncode, calls=calls)
    write_code_file(project, "scripts/ai/suite_record.py")
    write_code_file(project, "tests/test_foo.py")
    write_session_edits(project, "s1", ["tests/test_foo.py"])
    code, out = run_hook(stop_gate, _payload(), project)
    assert code == 0
    context = out["hookSpecificOutput"]["additionalContext"]
    assert expected in context
    assert ("pre-commit-verifier" in context) is asks_verifier
    assert len(calls) == 2
    check_args, check_kwargs = calls[1]
    assert check_args[1].endswith("suite_record.py") and check_args[2:] == ["check"]
    assert check_kwargs.get("timeout") == stop_gate.SUITE_CHECK_TIMEOUT_SECONDS
    assert stop_gate.PRECOMMIT_TIMEOUT_SECONDS + stop_gate.SUITE_CHECK_TIMEOUT_SECONDS < 300
    if asks_verifier:
        assert "exit 1" in context, "the check's reason (or its exit code) is surfaced"
    # tmp_path itself contains "pytest-of-...", so match whole arguments, not substrings.
    assert not any(part in ("pytest", "-mpytest") for args, _ in calls for part in args)
    assert all(args[1:3] != ["-m", "pytest"] for args, _ in calls)


def test_precommit_timeout_constant(stop_gate, run_hook, project, monkeypatch):
    calls: list = []
    stub_subprocess(monkeypatch, stop_gate, returncode=0, calls=calls)
    write_code_file(project, "tests/test_foo.py")
    write_session_edits(project, "s1", ["tests/test_foo.py"])
    run_hook(stop_gate, _payload(), project)
    assert len(calls) == 1
    _, kwargs = calls[0]
    assert kwargs.get("timeout") == stop_gate.PRECOMMIT_TIMEOUT_SECONDS == 150


@pytest.mark.parametrize(("outcome", "content_match"), [("PASSED", True), ("FAILED", False)])
def test_env_only_mismatch_on_a_passed_record_is_not_reported_as_missing(
    stop_gate, run_hook, project, monkeypatch, outcome, content_match
):
    """The fingerprint includes PATH and the hook's shell has its own PATH, so an
    env-only mismatch on a PASSED record means the same content already passed."""
    stdout = "suite_record: test-relevant environment differs (names: PATH, PYTHONUTF8); run again\n"
    stub_subprocess(monkeypatch, stop_gate, returncode=1, stdout=stdout)
    (project / ".claude" / ".suite-result.json").write_text(
        json.dumps({"outcome": outcome, "reusable": outcome == "PASSED"}), encoding="utf-8"
    )
    write_code_file(project, "scripts/ai/suite_record.py")
    write_code_file(project, "tests/test_foo.py")
    write_session_edits(project, "s1", ["tests/test_foo.py"])
    code, out = run_hook(stop_gate, _payload(), project)
    assert code == 0
    context = out["hookSpecificOutput"]["additionalContext"]
    if content_match:
        assert "a PASSED full-suite record matches this tree's content" in context
        assert "names: PATH, PYTHONUTF8" in context
        assert "pre-commit-verifier" not in context
    else:
        assert "no PASSED full-suite record for this tree (test-relevant environment differs" in context
        assert "pre-commit-verifier" in context
