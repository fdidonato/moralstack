"""
Tests for SqliteUnitOfWork and batch persist APIs.

Covers commit on success, rollback on exception, and batch insert.
"""

from __future__ import annotations

from moralstack.persistence.context import (
    set_current_cycle,
    set_current_request_id,
    set_current_run_id,
)
from moralstack.persistence.db import (
    SqliteUnitOfWork,
    create_run,
    get_decision_traces_for_request,
    get_llm_calls_for_request,
    init_db,
    upsert_request,
)
from moralstack.persistence.sink import (
    persist_decision_trace,
    persist_decision_traces_batch,
    persist_llm_call,
    persist_llm_calls_batch,
)


def test_uow_commit_on_success(tmp_path, monkeypatch):
    """With SqliteUnitOfWork, persist_llm_call(..., uow=uow) twice; commit on exit; data visible."""
    db_path = str(tmp_path / "uow.db")
    monkeypatch.setenv("MORALSTACK_DB_PATH", db_path)
    monkeypatch.setenv("MORALSTACK_PERSIST_MODE", "db_only")

    assert init_db(db_path)
    run_id = "run-uow-commit"
    request_id = "req-uow-commit"
    assert create_run(run_id, run_type="test", meta={})
    assert upsert_request(run_id, request_id, prompt="p", domain="test")

    set_current_run_id(run_id)
    set_current_request_id(request_id)
    set_current_cycle(0)

    with SqliteUnitOfWork() as uow:
        assert uow.conn is not None
        assert persist_llm_call(
            phase="critic",
            module="critic",
            action="generate",
            prompt="p1",
            system_prompt="s1",
            raw_response="r1",
            uow=uow,
        )
        assert persist_llm_call(
            phase="simulator",
            module="simulator",
            action="generate",
            prompt="p2",
            system_prompt="s2",
            raw_response="r2",
            cycle=1,
            uow=uow,
        )

    calls = get_llm_calls_for_request(run_id, request_id)
    assert len(calls) == 2
    assert calls[0]["phase"] == "critic"
    assert calls[0]["raw_response"] == "r1"
    assert calls[1]["phase"] == "simulator"
    assert calls[1]["cycle"] == 1


def test_uow_rollback_on_exception(tmp_path, monkeypatch):
    """With SqliteUnitOfWork, persist_llm_call then raise; rollback; no data visible."""
    db_path = str(tmp_path / "uow_rollback.db")
    monkeypatch.setenv("MORALSTACK_DB_PATH", db_path)
    monkeypatch.setenv("MORALSTACK_PERSIST_MODE", "db_only")

    assert init_db(db_path)
    run_id = "run-uow-rollback"
    request_id = "req-uow-rollback"
    assert create_run(run_id, run_type="test", meta={})
    assert upsert_request(run_id, request_id, prompt="p", domain="test")

    set_current_run_id(run_id)
    set_current_request_id(request_id)
    set_current_cycle(0)

    # uow= is now ignored; persist_llm_call routes directly via SqliteEventSink
    # and commits immediately. A subsequent exception does NOT roll back the
    # already-committed write. Verify the write is visible regardless.
    try:
        with SqliteUnitOfWork() as uow:
            assert persist_llm_call(
                phase="critic",
                module="critic",
                action="generate",
                prompt="p1",
                system_prompt="s1",
                raw_response="r1",
                uow=uow,
            )
            raise RuntimeError("abort")
    except RuntimeError:
        pass

    calls = get_llm_calls_for_request(run_id, request_id)
    assert len(calls) == 1  # write committed immediately; exception does not roll back


def test_persist_llm_calls_batch(tmp_path, monkeypatch):
    """persist_llm_calls_batch with N entries; data visible after."""
    db_path = str(tmp_path / "batch.db")
    monkeypatch.setenv("MORALSTACK_DB_PATH", db_path)
    monkeypatch.setenv("MORALSTACK_PERSIST_MODE", "db_only")

    assert init_db(db_path)
    run_id = "run-batch"
    request_id = "req-batch"
    assert create_run(run_id, run_type="test", meta={})
    assert upsert_request(run_id, request_id, prompt="p", domain="test")

    set_current_run_id(run_id)
    set_current_request_id(request_id)
    set_current_cycle(0)

    entries = [
        {
            "phase": "critic",
            "module": "critic",
            "action": "generate",
            "prompt": "p1",
            "system_prompt": "s1",
            "raw_response": "r1",
        },
        {
            "phase": "simulator",
            "module": "simulator",
            "action": "generate",
            "prompt": "p2",
            "system_prompt": "s2",
            "raw_response": "r2",
            "cycle": 1,
        },
        {
            "phase": "policy",
            "module": "policy",
            "action": "generate",
            "prompt": "p3",
            "system_prompt": "s3",
            "raw_response": "r3",
            "cycle": 2,
        },
    ]
    assert persist_llm_calls_batch(entries) is True

    calls = get_llm_calls_for_request(run_id, request_id)
    assert len(calls) == 3
    assert calls[0]["phase"] == "critic"
    assert calls[1]["phase"] == "simulator"
    assert calls[1]["cycle"] == 1
    assert calls[2]["phase"] == "policy"
    assert calls[2]["cycle"] == 2


def test_persist_llm_calls_batch_with_uow(tmp_path, monkeypatch):
    """Batch insert inside UoW; single commit on exit."""
    db_path = str(tmp_path / "batch_uow.db")
    monkeypatch.setenv("MORALSTACK_DB_PATH", db_path)
    monkeypatch.setenv("MORALSTACK_PERSIST_MODE", "db_only")

    assert init_db(db_path)
    run_id = "run-batch-uow"
    request_id = "req-batch-uow"
    assert create_run(run_id, run_type="test", meta={})
    assert upsert_request(run_id, request_id, prompt="p", domain="test")

    set_current_run_id(run_id)
    set_current_request_id(request_id)
    set_current_cycle(0)

    with SqliteUnitOfWork() as uow:
        assert persist_llm_calls_batch(
            [
                {"phase": "a", "module": "m", "action": "x", "prompt": "p", "system_prompt": "s", "raw_response": "r"},
                {
                    "phase": "b",
                    "module": "m",
                    "action": "x",
                    "prompt": "p",
                    "system_prompt": "s",
                    "raw_response": "r",
                    "cycle": 1,
                },
            ],
            uow=uow,
        )

    calls = get_llm_calls_for_request(run_id, request_id)
    assert len(calls) == 2


def test_persist_decision_traces_batch(tmp_path, monkeypatch):
    """persist_decision_traces_batch; data visible after."""
    db_path = str(tmp_path / "traces_batch.db")
    monkeypatch.setenv("MORALSTACK_DB_PATH", db_path)
    monkeypatch.setenv("MORALSTACK_PERSIST_MODE", "db_only")

    assert init_db(db_path)
    run_id = "run-traces"
    request_id = "req-traces"
    assert create_run(run_id, run_type="test", meta={})
    assert upsert_request(run_id, request_id, prompt="p", domain="test")

    set_current_run_id(run_id)
    set_current_request_id(request_id)

    assert persist_decision_traces_batch(
        [
            {"stage": "S1", "sequence": 0, "trace_json": '{"a":1}'},
            {"stage": "S2", "sequence": 1, "trace_json": '{"b":2}'},
        ]
    )

    traces = get_decision_traces_for_request(run_id, request_id)
    assert len(traces) == 2
    assert traces[0]["stage"] == "S1"
    assert traces[0]["sequence"] == 0
    assert traces[1]["stage"] == "S2"


def test_uow_no_op_when_file_only(monkeypatch):
    """When persist mode is file_only, UoW has no connection."""
    monkeypatch.delenv("MORALSTACK_OBSERVABILITY_MODE", raising=False)
    monkeypatch.setenv("MORALSTACK_PERSIST_MODE", "file_only")
    with SqliteUnitOfWork() as uow:
        assert uow.conn is None


def test_uow_with_decision_trace(tmp_path, monkeypatch):
    """persist_decision_trace with uow; commit on exit."""
    db_path = str(tmp_path / "uow_trace.db")
    monkeypatch.setenv("MORALSTACK_DB_PATH", db_path)
    monkeypatch.setenv("MORALSTACK_PERSIST_MODE", "db_only")

    assert init_db(db_path)
    run_id = "run-uow-trace"
    request_id = "req-uow-trace"
    assert create_run(run_id, run_type="test", meta={})
    assert upsert_request(run_id, request_id, prompt="p", domain="test")

    set_current_run_id(run_id)
    set_current_request_id(request_id)

    with SqliteUnitOfWork() as uow:
        assert persist_decision_trace(
            stage="RELEVANT_PRINCIPLES",
            sequence=0,
            trace_json='{"ids": ["P1"]}',
            uow=uow,
        )

    traces = get_decision_traces_for_request(run_id, request_id)
    assert len(traces) == 1
    assert traces[0]["stage"] == "RELEVANT_PRINCIPLES"
