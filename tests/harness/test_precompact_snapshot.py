"""PreCompact snapshot hook: writes a context snapshot from the transcript tail."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from conftest import write_session_edits


@pytest.fixture
def precompact(load_hook):
    return load_hook("precompact_snapshot")


def _transcript(project: Path) -> str:
    path = project / "transcript.jsonl"
    lines = [
        {"type": "user", "message": {"role": "user", "content": "implementa il piano X"}},
        {
            "type": "assistant",
            "message": {"role": "assistant", "content": [{"type": "text", "text": "ecco il piano dettagliato"}]},
        },
        {"type": "system", "message": {"role": "system", "content": "ignored"}},
    ]
    path.write_text("\n".join(json.dumps(entry) for entry in lines), encoding="utf-8")
    return str(path)


def test_writes_snapshot_with_tail_and_state(precompact, run_hook, project):
    write_session_edits(project, "s1", ["moralstack/orchestration/foo.py"])
    (project / ".claude" / ".last-verified.json").write_text(
        json.dumps({"session_id": "s1", "fingerprint": "f", "outcome": "passed"}), encoding="utf-8"
    )
    payload = {"session_id": "s1", "trigger": "auto", "transcript_path": _transcript(project)}
    code, _ = run_hook(precompact, payload, project)
    assert code == 0
    text = (project / ".claude" / ".context-snapshot.md").read_text(encoding="utf-8")
    assert "implementa il piano X" in text
    assert "ecco il piano dettagliato" in text
    assert "moralstack/orchestration/foo.py" in text
    assert "passed" in text
    assert "ignored" not in text  # non user/assistant turns are dropped


def test_missing_transcript_still_writes_snapshot(precompact, run_hook, project):
    payload = {"session_id": "s1", "trigger": "manual", "transcript_path": str(project / "nope.jsonl")}
    code, _ = run_hook(precompact, payload, project)
    assert code == 0
    text = (project / ".claude" / ".context-snapshot.md").read_text(encoding="utf-8")
    assert "transcript unavailable" in text
