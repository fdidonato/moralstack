"""Tests for SqliteEventSink: envelope → SQL row dispatch."""

from __future__ import annotations

import json

from moralstack.observability import obs
from moralstack.observability.context import (
    set_current_request_id,
    set_current_run_id,
)
from moralstack.observability.events import (
    EVENT_DECISION_TRACE,
    EVENT_LLM_CALL,
    EVENT_ORCHESTRATION_EVENT,
    EVENT_RUN_STARTED,
    make_envelope,
)
from moralstack.observability.sinks.sqlite_sink import (
    SqliteEventSink,
    create_run,
    init_db,
    upsert_request,
)

get_llm_calls_for_request = obs.read_store.get_llm_calls_for_request
get_orchestration_events_for_request = obs.read_store.get_orchestration_events_for_request


def _setup(tmp_path, monkeypatch):
    dbp = str(tmp_path / "sink_test.db")
    monkeypatch.setenv("MORALSTACK_DB_PATH", dbp)
    monkeypatch.setenv("MORALSTACK_OBSERVABILITY_MODE", "db_only")
    init_db(dbp)
    return dbp


def test_sqlite_sink_write_llm_call(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    create_run("run-1", run_type="test", meta={})
    upsert_request("run-1", "req-1", prompt="hi", domain="")
    set_current_run_id("run-1")
    set_current_request_id("req-1")

    sink = SqliteEventSink()
    env = make_envelope(
        EVENT_LLM_CALL,
        run_id="run-1",
        request_id="req-1",
        cycle=1,
        payload={
            "phase": "risk_estimation",
            "module": "risk_estimator",
            "action": "estimate",
            "prompt": "test prompt",
            "raw_response": "{}",
            "attempts": 1,
        },
    )
    sink.write_envelope(env)

    rows = get_llm_calls_for_request("run-1", "req-1")
    assert len(rows) == 1
    assert rows[0]["phase"] == "risk_estimation"
    assert rows[0]["module"] == "risk_estimator"


def test_sqlite_sink_write_orchestration_event(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    create_run("run-2", run_type="test", meta={})
    upsert_request("run-2", "req-2", prompt="hi", domain="")
    set_current_run_id("run-2")
    set_current_request_id("req-2")

    sink = SqliteEventSink()
    env = make_envelope(
        EVENT_ORCHESTRATION_EVENT,
        run_id="run-2",
        request_id="req-2",
        cycle=1,
        payload={
            "stage": "deliberation",
            "component": "runner",
            "event_type": "CYCLE_STARTED",
            "decision": "continue",
            "status": "ok",
            "sequence": 1,
        },
    )
    sink.write_envelope(env)

    rows = get_orchestration_events_for_request("run-2", "req-2")
    assert len(rows) == 1
    assert rows[0]["event_type"] == "CYCLE_STARTED"
    assert rows[0]["stage"] == "deliberation"


def test_sqlite_sink_write_decision_trace(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    create_run("run-3", run_type="test", meta={})
    upsert_request("run-3", "req-3", prompt="hi", domain="")

    sink = SqliteEventSink()
    trace_data = {"request_id": "req-3", "stage": "FINAL", "final_action": "NORMAL_COMPLETE"}
    env = make_envelope(
        EVENT_DECISION_TRACE,
        run_id="run-3",
        request_id="req-3",
        payload={
            "stage": "FINAL",
            "sequence": 2,
            "trace_json": json.dumps(trace_data),
        },
    )
    sink.write_envelope(env)

    rs = obs.read_store
    traces = rs.get_decision_traces_for_request("run-3", "req-3")
    assert len(traces) == 1
    assert traces[0]["stage"] == "FINAL"
    td = json.loads(traces[0]["trace_json"])
    assert td["final_action"] == "NORMAL_COMPLETE"


def test_sqlite_sink_write_batch(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    create_run("run-4", run_type="test", meta={})
    upsert_request("run-4", "req-4", prompt="hi", domain="")

    sink = SqliteEventSink()
    envs = [
        make_envelope(
            EVENT_LLM_CALL,
            run_id="run-4",
            request_id="req-4",
            cycle=i,
            payload={"phase": "test", "module": f"mod{i}", "action": "act", "prompt": "", "raw_response": "", "attempts": 1},
        )
        for i in range(3)
    ]
    sink.write_batch(envs)

    rows = get_llm_calls_for_request("run-4", "req-4")
    assert len(rows) == 3


def test_sqlite_sink_lifecycle_run_started(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    sink = SqliteEventSink()
    env = make_envelope(EVENT_RUN_STARTED, run_id="run-5", payload={"run_type": "test", "meta": {}})
    sink.write_envelope(env)

    rs = obs.read_store
    run = rs.get_run("run-5")
    assert run is not None
    assert run["run_id"] == "run-5"


def test_sqlite_sink_does_not_raise_on_missing_db(monkeypatch):
    """When db_path is empty, writes are no-ops and don't raise."""
    monkeypatch.delenv("MORALSTACK_DB_PATH", raising=False)
    monkeypatch.delenv("MORALSTACK_OBSERVABILITY_DB_PATH", raising=False)
    monkeypatch.setenv("MORALSTACK_OBSERVABILITY_MODE", "db_only")
    sink = SqliteEventSink()
    env = make_envelope(EVENT_LLM_CALL, run_id="r1", request_id="q1", payload={"phase": "x"})
    sink.write_envelope(env)  # must not raise
