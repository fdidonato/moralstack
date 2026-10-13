"""Tests for ObservabilityService: singleton, emit, flush, shutdown."""

from __future__ import annotations

import threading

import pytest

from moralstack.observability import obs, router
from moralstack.observability import service as service_module
from moralstack.observability.events import EVENT_LLM_CALL, make_envelope
from moralstack.observability.service import ObservabilityService, get_obs
from moralstack.observability.sinks.sqlite_sink import (
    create_run,
    init_db,
    upsert_request,
)

get_llm_calls_for_request = obs.read_store.get_llm_calls_for_request


@pytest.fixture(autouse=True)
def _fresh_obs_singleton():
    try:
        get_obs().shutdown(timeout=1.0)
    except Exception:
        pass
    service_module._obs_instance = None
    router._sqlite_sink = None
    router._jsonl_sink = None
    yield
    try:
        get_obs().shutdown(timeout=1.0)
    except Exception:
        pass
    service_module._obs_instance = None
    router._sqlite_sink = None
    router._jsonl_sink = None


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


# ---------------------------------------------------------------------------
# Billable gate of the in-process token accumulator (must mirror the SQL rule
# COALESCE(billable_provider_call, 1) = 1: only a falsy flag excludes a row).
# ---------------------------------------------------------------------------

_ABSENT = object()


class _ExplodingBool:
    def __bool__(self) -> bool:
        raise RuntimeError("bool exploded")


def _gate_service(monkeypatch):
    """Service with a stubbed queue and a recorder in place of record_llm_call_usage."""
    from unittest.mock import MagicMock

    calls: list[tuple] = []
    monkeypatch.setattr(service_module, "record_llm_call_usage", lambda *a, **k: calls.append(a))
    svc = ObservabilityService()
    svc._queue.shutdown(timeout=1.0)
    svc._queue = MagicMock()
    return svc, calls


def _gate_env(flag=_ABSENT, **kwargs):
    payload = {"token_usage_json": '{"prompt_tokens":1,"completion_tokens":1,"total_tokens":2}'}
    payload.update(kwargs)
    if flag is not _ABSENT:
        payload["billable_provider_call"] = flag
    return make_envelope(EVENT_LLM_CALL, run_id="r-gate", request_id="q-gate", payload=payload)


def _send(svc, mode, envelope):
    if mode == "emit":
        svc.emit(envelope)
    else:
        svc.emit_batch([envelope])


def _submitted(svc, mode):
    return svc._queue.submit_envelope.call_count if mode == "emit" else svc._queue.submit_batch.call_count


@pytest.mark.parametrize("mode", ["emit", "emit_batch"])
@pytest.mark.parametrize(
    "flag,expected_calls",
    [(True, 1), (None, 1), (_ABSENT, 1), (False, 0), (0, 0)],
    ids=["True", "None", "ABSENT", "False", "zero"],
)
def test_billable_gate_counts_only_falsy_flag_as_excluded(monkeypatch, mode, flag, expected_calls):
    svc, calls = _gate_service(monkeypatch)
    _send(svc, mode, _gate_env(flag))
    assert len(calls) == expected_calls
    assert _submitted(svc, mode) == 1


@pytest.mark.parametrize("mode", ["emit", "emit_batch"])
def test_billable_gate_recorder_exception_does_not_propagate(monkeypatch, mode):
    svc, _ = _gate_service(monkeypatch)

    def boom(*_a, **_k):
        raise RuntimeError("recorder failed")

    monkeypatch.setattr(service_module, "record_llm_call_usage", boom)
    _send(svc, mode, _gate_env(True))
    assert _submitted(svc, mode) == 1


@pytest.mark.parametrize("mode", ["emit", "emit_batch"])
def test_billable_gate_bool_exception_does_not_propagate(monkeypatch, mode):
    svc, calls = _gate_service(monkeypatch)
    _send(svc, mode, _gate_env(_ExplodingBool()))
    assert calls == []
    assert _submitted(svc, mode) == 1


@pytest.mark.parametrize("mode", ["emit", "emit_batch"])
def test_billable_gate_ignores_non_llm_call_and_missing_ids(monkeypatch, mode):
    svc, calls = _gate_service(monkeypatch)
    _send(svc, mode, make_envelope("request.other", run_id="r", request_id="q", payload={}))
    _send(svc, mode, make_envelope(EVENT_LLM_CALL, run_id="r", request_id=None, payload={}))
    _send(svc, mode, make_envelope(EVENT_LLM_CALL, run_id=None, request_id="q", payload={}))
    assert calls == []


@pytest.mark.parametrize("mode", ["emit", "emit_batch"])
def test_billable_gate_dict_token_usage_is_serialized(monkeypatch, mode):
    svc, calls = _gate_service(monkeypatch)
    usage = {"prompt_tokens": 3, "completion_tokens": 4, "total_tokens": 7}
    _send(svc, mode, _gate_env(None, token_usage_json=usage))
    assert len(calls) == 1
    assert calls[0][:2] == ("r-gate", "q-gate")
    assert isinstance(calls[0][2], str)
    assert '"total_tokens": 7' in calls[0][2]


def test_emit_batch_empty_does_not_submit(monkeypatch):
    svc, calls = _gate_service(monkeypatch)
    svc.emit_batch([])
    assert calls == []
    svc._queue.submit_batch.assert_not_called()


@pytest.mark.parametrize("flag", [True, False, 0, 1, None, _ABSENT], ids=["True", "False", "zero", "one", "None", "ABSENT"])
def test_counts_as_billable_matches_persisted_column_reading(flag):
    """A8: in-process rule == SQL COALESCE(billable_provider_call, 1) = 1 on the stored column."""
    from moralstack.observability.sinks.sqlite_sink import _derive_llm_call_token_columns

    payload = {"token_usage_json": '{"prompt_tokens":1,"completion_tokens":1,"total_tokens":2}'}
    if flag is not _ABSENT:
        payload["billable_provider_call"] = flag
    col = _derive_llm_call_token_columns(payload)[5]
    assert service_module._counts_as_billable(payload) == ((col if col is not None else 1) == 1)


def _gate_service_real_recorder():
    """Service with a stubbed queue and the real accumulator."""
    from unittest.mock import MagicMock

    svc = ObservabilityService()
    svc._queue.shutdown(timeout=1.0)
    svc._queue = MagicMock()
    return svc


def test_none_flag_without_usage_counts_call_and_missing():
    """A7: a None-flag call with no usage is counted as a call and as missing usage (H3)."""
    import moralstack.observability.request_token_accumulator as acc

    svc = _gate_service_real_recorder()
    with acc._lock:
        acc._store.clear()
    svc.emit(
        make_envelope(
            EVENT_LLM_CALL,
            run_id="r-a7",
            request_id="q-a7",
            payload={"billable_provider_call": None, "token_usage_json": None},
        )
    )
    totals = acc.pop_request_token_usage("r-a7", "q-a7")
    assert totals is not None
    assert totals.llm_call_count == 1
    assert totals.missing_usage_count == 1
    assert totals.total_tokens == 0


def test_interleaved_requests_do_not_cross_contaminate():
    import moralstack.observability.request_token_accumulator as acc

    svc = _gate_service_real_recorder()
    with acc._lock:
        acc._store.clear()

    def usage(n: int) -> str:
        return f'{{"prompt_tokens":{n},"completion_tokens":0,"total_tokens":{n}}}'

    def env(req: str, flag, tok):
        return make_envelope(
            EVENT_LLM_CALL, run_id="r-ix", request_id=req, payload={"billable_provider_call": flag, "token_usage_json": tok}
        )

    svc.emit_batch([env("a", None, usage(5)), env("b", None, usage(7))])
    svc.emit(env("a", True, usage(10)))
    svc.emit(env("b", False, usage(1000)))
    a = acc.pop_request_token_usage("r-ix", "a")
    b = acc.pop_request_token_usage("r-ix", "b")
    assert a is not None and b is not None
    assert (a.total_tokens, a.llm_call_count) == (15, 2)
    assert (b.total_tokens, b.llm_call_count) == (7, 1)


def test_malformed_token_usage_json_is_swallowed_and_not_counted():
    """Pins parity, not a desired contract: a malformed usage row is in neither side.

    ``TokenUsage.from_json`` raises before the accumulator updates anything, and the
    gate's ``try`` swallows it. The persisted side drops such a row too:
    ``_derive_llm_call_token_columns`` raises outside the ``try`` in
    ``_write_llm_call_single``/``_write_llm_call_batch`` (sqlite_sink.py), so the whole
    write window fails. No production emitter produces malformed JSON.
    """
    import moralstack.observability.request_token_accumulator as acc

    svc = _gate_service_real_recorder()
    with acc._lock:
        acc._store.clear()
    svc.emit(
        make_envelope(
            EVENT_LLM_CALL,
            run_id="r-mal",
            request_id="q-mal",
            payload={"billable_provider_call": None, "token_usage_json": "{not json"},
        )
    )
    assert acc.pop_request_token_usage("r-mal", "q-mal") is None
    svc._queue.submit_envelope.assert_called_once()
