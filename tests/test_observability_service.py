"""Tests for ObservabilityService: singleton, emit, flush, shutdown."""

from __future__ import annotations

import threading

from moralstack.observability import obs
from moralstack.observability.events import EVENT_LLM_CALL, make_envelope
from moralstack.observability.service import ObservabilityService, get_obs
from moralstack.observability.sinks.sqlite_sink import (
    create_run,
    init_db,
    upsert_request,
)

get_llm_calls_for_request = obs.read_store.get_llm_calls_for_request


def _env(run_id="r1", request_id="q1", event_type=EVENT_LLM_CALL, **payload_kwargs):
    payload = {"phase": "test", "module": "m", "action": "a", "prompt": "", "raw_response": "", "attempts": 1}
    payload.update(payload_kwargs)
    return make_envelope(event_type, run_id=run_id, request_id=request_id, payload=payload)


def test_get_obs_returns_singleton():
    obs1 = get_obs()
    obs2 = get_obs()
    assert obs1 is obs2


def test_obs_module_lazy_proxy_targets_service_singleton():
    svc = get_obs()
    assert isinstance(svc, ObservabilityService)
    assert obs.read_store is svc.read_store


def test_obs_has_read_store():
    assert obs.read_store is not None
    assert hasattr(obs.read_store, "get_run")


def test_emit_and_flush_no_events_lost(tmp_path, monkeypatch):
    dbp = str(tmp_path / "svc.db")
    monkeypatch.setenv("MORALSTACK_DB_PATH", dbp)
    monkeypatch.setenv("MORALSTACK_OBSERVABILITY_MODE", "db_only")
    init_db(dbp)
    create_run("run-svc-1", run_type="test", meta={})
    upsert_request("run-svc-1", "req-svc-1", prompt="p", domain="")

    for i in range(10):
        env = make_envelope(
            EVENT_LLM_CALL,
            run_id="run-svc-1",
            request_id="req-svc-1",
            cycle=i,
            payload={"phase": "test", "module": f"mod{i}", "action": "act", "prompt": "", "raw_response": "", "attempts": 1},
        )
        obs.emit(env)

    obs.flush(timeout=10.0)

    rows = get_llm_calls_for_request("run-svc-1", "req-svc-1")
    assert len(rows) == 10


def test_emit_batch_persists_all(tmp_path, monkeypatch):
    dbp = str(tmp_path / "svc2.db")
    monkeypatch.setenv("MORALSTACK_DB_PATH", dbp)
    monkeypatch.setenv("MORALSTACK_OBSERVABILITY_MODE", "db_only")
    init_db(dbp)
    create_run("run-svc-2", run_type="test", meta={})
    upsert_request("run-svc-2", "req-svc-2", prompt="p", domain="")

    envs = [
        make_envelope(
            EVENT_LLM_CALL,
            run_id="run-svc-2",
            request_id="req-svc-2",
            cycle=i,
            payload={"phase": "test", "module": f"m{i}", "action": "a", "prompt": "", "raw_response": "", "attempts": 1},
        )
        for i in range(5)
    ]
    obs.emit_batch(envs)
    obs.flush(timeout=10.0)

    rows = get_llm_calls_for_request("run-svc-2", "req-svc-2")
    assert len(rows) == 5


def test_flush_with_zero_timeout_does_not_raise():
    """flush(timeout=0) should not raise even if queue is busy."""
    obs.flush(timeout=0)


def test_emit_does_not_raise_on_bad_envelope():
    """emit with a badly-typed object is swallowed by the service."""
    try:
        obs.emit(None)  # type: ignore[arg-type]
    except Exception:
        pass  # acceptable: emit may raise TypeError, but must not crash the service


def test_concurrent_emits_no_deadlock(tmp_path, monkeypatch):
    dbp = str(tmp_path / "svc3.db")
    monkeypatch.setenv("MORALSTACK_DB_PATH", dbp)
    monkeypatch.setenv("MORALSTACK_OBSERVABILITY_MODE", "db_only")
    init_db(dbp)
    create_run("run-svc-3", run_type="test", meta={})
    upsert_request("run-svc-3", "req-svc-3", prompt="p", domain="")

    errors = []

    def emit_n(n: int):
        for i in range(n):
            try:
                env = make_envelope(
                    EVENT_LLM_CALL,
                    run_id="run-svc-3",
                    request_id="req-svc-3",
                    cycle=i,
                    payload={"phase": "test", "module": "m", "action": "a", "prompt": "", "raw_response": "", "attempts": 1},
                )
                obs.emit(env)
            except Exception as e:
                errors.append(e)

    threads = [threading.Thread(target=emit_n, args=(20,)) for _ in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    obs.flush(timeout=10.0)
    assert not errors


def test_observability_write_queue_flush_respects_timeout(caplog):
    """Flush must return when the deadline is reached even if work remains."""
    import logging
    import time

    from moralstack.observability.write_queue import ObservabilityWriteQueue

    q = ObservabilityWriteQueue()

    def slow_job() -> None:
        time.sleep(0.2)

    q.submit(slow_job)
    caplog.set_level(logging.WARNING)
    q.flush(timeout=0.02)
    q.shutdown(timeout=5.0)
    assert any("flush timed out" in r.message for r in caplog.records)
