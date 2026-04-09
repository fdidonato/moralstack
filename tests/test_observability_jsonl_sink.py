"""Tests for JsonlEventSink: per-event-type JSONL output."""

from __future__ import annotations

import json
import threading

from moralstack.observability.events import (
    EVENT_DEBUG_EVENT,
    EVENT_DECISION_TRACE,
    EVENT_LLM_CALL,
    EVENT_ORCHESTRATION_EVENT,
    make_envelope,
)
from moralstack.observability.sinks.jsonl_sink import JsonlEventSink


def test_jsonl_sink_creates_file_per_event_type(tmp_path, monkeypatch):
    monkeypatch.setenv("MORALSTACK_OBSERVABILITY_JSONL_DIR", str(tmp_path / "obs"))
    sink = JsonlEventSink()

    env = make_envelope(EVENT_LLM_CALL, run_id="r1", request_id="q1", payload={"module": "risk"})
    sink.write_envelope(env)

    expected = tmp_path / "obs" / f"{EVENT_LLM_CALL}.jsonl"
    assert expected.exists()
    lines = [ln for ln in expected.read_text(encoding="utf-8").strip().split("\n") if ln]
    assert len(lines) == 1
    obj = json.loads(lines[0])
    assert obj["event_type"] == EVENT_LLM_CALL
    assert obj["run_id"] == "r1"


def test_jsonl_sink_separate_files_per_type(tmp_path, monkeypatch):
    monkeypatch.setenv("MORALSTACK_OBSERVABILITY_JSONL_DIR", str(tmp_path / "obs"))
    sink = JsonlEventSink()

    sink.write_envelope(make_envelope(EVENT_LLM_CALL, run_id="r1", request_id="q1"))
    sink.write_envelope(make_envelope(EVENT_ORCHESTRATION_EVENT, run_id="r1", request_id="q1"))
    sink.write_envelope(make_envelope(EVENT_DECISION_TRACE, run_id="r1", request_id="q1"))
    sink.write_envelope(make_envelope(EVENT_DEBUG_EVENT, run_id="r1", request_id="q1"))

    obs_dir = tmp_path / "obs"
    files = {f.name for f in obs_dir.glob("*.jsonl")}
    assert f"{EVENT_LLM_CALL}.jsonl" in files
    assert f"{EVENT_ORCHESTRATION_EVENT}.jsonl" in files
    assert f"{EVENT_DECISION_TRACE}.jsonl" in files
    assert f"{EVENT_DEBUG_EVENT}.jsonl" in files


def test_jsonl_sink_appends_multiple_lines(tmp_path, monkeypatch):
    monkeypatch.setenv("MORALSTACK_OBSERVABILITY_JSONL_DIR", str(tmp_path / "obs"))
    sink = JsonlEventSink()

    for i in range(5):
        sink.write_envelope(make_envelope(EVENT_LLM_CALL, run_id="r1", request_id=f"q{i}", payload={"seq": i}))

    path = tmp_path / "obs" / f"{EVENT_LLM_CALL}.jsonl"
    lines = [ln for ln in path.read_text(encoding="utf-8").strip().split("\n") if ln]
    assert len(lines) == 5
    seqs = [json.loads(ln)["payload"]["seq"] for ln in lines]
    assert sorted(seqs) == list(range(5))


def test_jsonl_sink_batch(tmp_path, monkeypatch):
    monkeypatch.setenv("MORALSTACK_OBSERVABILITY_JSONL_DIR", str(tmp_path / "obs"))
    sink = JsonlEventSink()

    envs = [make_envelope(EVENT_LLM_CALL, run_id="r1", request_id=f"q{i}") for i in range(4)]
    sink.write_batch(envs)

    path = tmp_path / "obs" / f"{EVENT_LLM_CALL}.jsonl"
    lines = [ln for ln in path.read_text(encoding="utf-8").strip().split("\n") if ln]
    assert len(lines) == 4


def test_jsonl_sink_thread_safe(tmp_path, monkeypatch):
    monkeypatch.setenv("MORALSTACK_OBSERVABILITY_JSONL_DIR", str(tmp_path / "obs"))
    sink = JsonlEventSink()
    n_threads, n_per_thread = 5, 20

    def write_n(tid: int):
        for i in range(n_per_thread):
            sink.write_envelope(make_envelope(EVENT_LLM_CALL, run_id="r1", request_id=f"q{tid}-{i}"))

    threads = [threading.Thread(target=write_n, args=(t,)) for t in range(n_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    path = tmp_path / "obs" / f"{EVENT_LLM_CALL}.jsonl"
    lines = [ln for ln in path.read_text(encoding="utf-8").strip().split("\n") if ln]
    assert len(lines) == n_threads * n_per_thread
    # All lines are valid JSON
    objs = [json.loads(ln) for ln in lines]
    request_ids = {obj["request_id"] for obj in objs}
    assert len(request_ids) == n_threads * n_per_thread


def test_jsonl_sink_payload_is_serialized(tmp_path, monkeypatch):
    monkeypatch.setenv("MORALSTACK_OBSERVABILITY_JSONL_DIR", str(tmp_path / "obs"))
    sink = JsonlEventSink()

    env = make_envelope(
        EVENT_DEBUG_EVENT,
        run_id="r1",
        request_id="q1",
        payload={"key": "value", "nested": {"a": 1}},
    )
    sink.write_envelope(env)

    path = tmp_path / "obs" / f"{EVENT_DEBUG_EVENT}.jsonl"
    obj = json.loads(path.read_text(encoding="utf-8").strip())
    assert obj["payload"]["key"] == "value"
    assert obj["payload"]["nested"]["a"] == 1
