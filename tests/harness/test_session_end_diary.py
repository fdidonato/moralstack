"""SessionEnd hook: stages an UNVERIFIED digest, never touches verified facts."""

from __future__ import annotations

import json

import pytest
from conftest import write_session_edits


@pytest.fixture
def session_end(load_hook):
    return load_hook("session_end")


def test_appends_unverified_digest(session_end, run_hook, project):
    write_session_edits(project, "s1", ["moralstack/orchestration/foo.py", "tests/test_foo.py"])
    (project / ".claude" / ".last-verified.json").write_text(
        json.dumps({"session_id": "s1", "fingerprint": "f", "outcome": "passed"}), encoding="utf-8"
    )
    code, _ = run_hook(session_end, {"session_id": "s1", "reason": "logout"}, project)
    assert code == 0
    diary = (project / ".claude" / "session-diary.md").read_text(encoding="utf-8")
    assert "UNVERIFIED" in diary
    assert "moralstack/orchestration/foo.py" in diary
    assert "logout" in diary
    assert "passed" in diary


def test_appends_across_sessions(session_end, run_hook, project):
    write_session_edits(project, "s1", ["moralstack/x.py"])
    run_hook(session_end, {"session_id": "s1", "reason": "clear"}, project)
    write_session_edits(project, "s2", ["moralstack/y.py"])
    run_hook(session_end, {"session_id": "s2", "reason": "logout"}, project)
    diary = (project / ".claude" / "session-diary.md").read_text(encoding="utf-8")
    assert "moralstack/x.py" in diary and "moralstack/y.py" in diary
    assert diary.count("session s") >= 2  # append-only, both entries kept


def test_noop_when_nothing_happened(session_end, run_hook, project):
    code, _ = run_hook(session_end, {"session_id": "s1", "reason": "logout"}, project)
    assert code == 0
    assert not (project / ".claude" / "session-diary.md").exists()


def test_never_writes_into_codebase_facts(session_end, run_hook, project):
    # A verified-facts file must remain byte-identical after the hook runs.
    docs = project / "docs"
    docs.mkdir()
    facts = docs / "CODEBASE_FACTS.md"
    original = "# Facts\n\n| verified | ... |\n"
    facts.write_text(original, encoding="utf-8")
    write_session_edits(project, "s1", ["moralstack/x.py"])
    run_hook(session_end, {"session_id": "s1", "reason": "logout"}, project)
    assert facts.read_text(encoding="utf-8") == original
