"""Load and accounting tests for audit-grade observability persistence."""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from moralstack.observability import obs, router
from moralstack.observability import service as service_module
from moralstack.observability.conversation_events import finalize_audit_sync
from moralstack.observability.events import (
    EVENT_CONVERSATION_STATE_UPDATED,
    EVENT_LEDGER_LOOKUP,
    EVENT_LEDGER_STORE,
    EVENT_LLM_CALL,
    EVENT_PROXY_REQUEST_FINALIZED,
    EVENT_REQUEST_META_UPDATED,
    EVENT_REQUEST_UPSERTED,
    EVENT_SESSION_STORE_GET,
    make_envelope,
)
from moralstack.observability.router import WindowResult, route_window
from moralstack.observability.service import get_obs
from moralstack.observability.sinks.sqlite_sink import (
    SqliteEventSink,
    _get_connection,
    create_run,
    init_db,
    upsert_request,
)
from moralstack.observability.write_queue import ObservabilityWriteQueue
from moralstack.persistence.sink import (
    persist_decision_trace,
    persist_llm_call,
    persist_llm_calls_batch,
    persist_orchestration_event,
)


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


def _configure_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, name: str) -> str:
    db_path = str(tmp_path / name)
    monkeypatch.setenv("MORALSTACK_OBSERVABILITY_DB_PATH", db_path)
    monkeypatch.setenv("MORALSTACK_DB_PATH", db_path)
    monkeypatch.setenv("MORALSTACK_OBSERVABILITY_MODE", "db_only")
    monkeypatch.setenv("MORALSTACK_PERSIST_MODE", "db_only")
    assert init_db(db_path)
    return db_path


def _count_rows(db_path: str, table: str, *, where: str = "", args: tuple[object, ...] = ()) -> int:
    query = f"SELECT COUNT(*) FROM {table}"
    if where:
        query += f" WHERE {where}"
    with sqlite3.connect(db_path) as conn:
        return int(conn.execute(query, args).fetchone()[0])


def _meta_final_action_count(db_path: str, run_id: str) -> int:
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute("SELECT meta_json FROM requests WHERE run_id = ?", (run_id,)).fetchall()
    count = 0
    for (raw,) in rows:
        try:
            if json.loads(raw or "{}").get("final_action"):
                count += 1
        except Exception:
            pass
    return count


def _llm_envelope(run_id: str, request_id: str, index: int):
    return make_envelope(
        EVENT_LLM_CALL,
        run_id=run_id,
        request_id=request_id,
        cycle=0,
        payload={
            "phase": "load",
            "module": "load",
            "action": "call",
            "model": "test",
            "started_at": 1_000_000 + index,
            "duration_ms": 1.0,
            "prompt": f"prompt-{index}",
            "system_prompt": "system",
            "raw_response": "response",
            "sequence_in_cycle": index,
        },
    )


def _percentile(values: list[float], percentile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, int(round((len(ordered) - 1) * percentile))))
    return ordered[index]


def test_concurrency_emitted_equals_persisted(tmp_path, monkeypatch):
    db_path = _configure_db(tmp_path, monkeypatch, "concurrency.db")
    run_id = "run-load-concurrency"
    assert create_run(run_id, run_type="load", meta={})

    request_count = 100
    high_frequency_per_request = 6

    def emit_request(index: int) -> None:
        request_id = f"req-{index:03d}"
        conversation_id = f"conv-{index:03d}"
        assert upsert_request(
            run_id,
            request_id,
            prompt=f"prompt {index}",
            domain="general",
            conversation_id=conversation_id,
            turn_index=index,
        )
        persist_orchestration_event(
            run_id=run_id,
            request_id=request_id,
            cycle=0,
            stage="load",
            component="router",
            event_type="LOAD_EVENT",
            status="ok",
            sequence=0,
        )
        persist_llm_call(
            run_id=run_id,
            request_id=request_id,
            cycle=0,
            phase="load",
            module="load",
            action="primary",
            model="test",
            prompt="p",
            system_prompt="s",
            raw_response="r",
            sequence_in_cycle=0,
        )
        persist_llm_calls_batch(
            [
                {
                    "run_id": run_id,
                    "request_id": request_id,
                    "cycle": 0,
                    "phase": "risk_estimation",
                    "module": "risk_estimator",
                    "action": f"mini_{mini}",
                    "model": "test",
                    "prompt": "p",
                    "system_prompt": "s",
                    "raw_response": "r",
                    "sequence_in_cycle": -9,
                }
                for mini in range(3)
            ]
        )
        persist_decision_trace(
            run_id=run_id,
            request_id=request_id,
            stage="FINAL",
            sequence=1,
            trace_json=json.dumps({"final_action": "NORMAL_COMPLETE"}),
        )
        result = finalize_audit_sync(
            run_id=run_id,
            request_id=request_id,
            final_action="NORMAL_COMPLETE",
            final_response="ok",
            domain="general",
            proxy_summary={
                "conversation_id": conversation_id,
                "turn_index": index,
                "final_action": "NORMAL_COMPLETE",
                "risk_score": 0.1,
                "path": "FAST_PATH",
                "domain": "general",
                "metadata": {"final_action": "NORMAL_COMPLETE", "risk_score": 0.1},
            },
        )
        assert result.failed == 0

    with ThreadPoolExecutor(max_workers=request_count) as pool:
        for future in [pool.submit(emit_request, index) for index in range(request_count)]:
            future.result()

    obs.flush(timeout=30.0)
    stats = obs.stats()
    emitted = request_count * high_frequency_per_request
    persisted = (
        _count_rows(db_path, "orchestration_events", where="run_id = ?", args=(run_id,))
        + _count_rows(db_path, "llm_calls", where="run_id = ?", args=(run_id,))
        + _count_rows(db_path, "decision_traces", where="run_id = ?", args=(run_id,))
    )

    assert _count_rows(db_path, "requests", where="run_id = ?", args=(run_id,)) == request_count
    assert _count_rows(db_path, "orchestration_events", where="run_id = ?", args=(run_id,)) == request_count
    assert _count_rows(db_path, "llm_calls", where="run_id = ?", args=(run_id,)) == request_count * 4
    assert _count_rows(db_path, "decision_traces", where="run_id = ?", args=(run_id,)) == request_count
    assert _count_rows(db_path, "proxy_request_events", where="run_id = ?", args=(run_id,)) == request_count
    assert _meta_final_action_count(db_path, run_id) == request_count
    assert emitted == persisted
    assert stats["submitted_count"] == emitted
    assert stats["submitted_count"] == stats["written_count"] + stats["dropped_count"] + stats["failed_count"]
    assert stats["dropped_count"] == 0
    assert stats["failed_count"] == 0


def test_fk_ordering_children_after_sync_parent(tmp_path, monkeypatch):
    db_path = _configure_db(tmp_path, monkeypatch, "fk-order.db")
    run_id = "run-fk-order"
    request_id = "req-fk-order"
    conversation_id = "conv-fk-order"
    assert create_run(run_id, run_type="load", meta={})
    assert upsert_request(run_id, request_id, prompt="prompt", domain="general", conversation_id=conversation_id)

    children = [
        make_envelope(
            EVENT_PROXY_REQUEST_FINALIZED,
            run_id=run_id,
            request_id=request_id,
            session_id=conversation_id,
            turn_number=0,
            payload={"final_action": "SAFE_COMPLETE", "metadata": {"final_action": "SAFE_COMPLETE"}},
        ),
        make_envelope(
            EVENT_LEDGER_STORE,
            run_id=run_id,
            request_id=request_id,
            session_id=conversation_id,
            turn_number=0,
            payload={"operation": "store", "outcome": "stored", "final_action": "SAFE_COMPLETE"},
        ),
        make_envelope(
            EVENT_REQUEST_META_UPDATED,
            run_id=run_id,
            request_id=request_id,
            payload={"meta": {"final_action": "SAFE_COMPLETE"}, "merge": True},
        ),
        make_envelope(
            EVENT_CONVERSATION_STATE_UPDATED,
            run_id=run_id,
            request_id=request_id,
            session_id=conversation_id,
            turn_number=0,
            payload={
                "state_in": None,
                "state_out": {"posture": "STABLE"},
                "final_action": "SAFE_COMPLETE",
                "posture": "STABLE",
            },
        ),
        make_envelope(
            EVENT_SESSION_STORE_GET,
            run_id=run_id,
            request_id=request_id,
            session_id=conversation_id,
            turn_number=0,
            payload={"operation": "get", "outcome": "hit"},
        ),
        make_envelope(
            EVENT_LEDGER_LOOKUP,
            run_id=run_id,
            request_id=request_id,
            session_id=conversation_id,
            turn_number=0,
            payload={"operation": "lookup", "outcome": "miss"},
        ),
    ]
    conn = _get_connection(db_path)
    try:
        result = route_window(children, conn)
    finally:
        conn.close()

    assert result.written == len(children)
    assert result.failed == 0
    assert _count_rows(db_path, "conversation_states", where="run_id = ?", args=(run_id,)) == 1
    assert _count_rows(db_path, "ledger_events", where="run_id = ?", args=(run_id,)) == 2
    assert _count_rows(db_path, "session_store_events", where="run_id = ?", args=(run_id,)) == 1
    assert _count_rows(db_path, "proxy_request_events", where="run_id = ?", args=(run_id,)) == 1
    assert _meta_final_action_count(db_path, run_id) == 1

    run_id_2 = "run-fk-window-parent"
    request_id_2 = "req-fk-window-parent"
    assert create_run(run_id_2, run_type="load", meta={})
    window = [
        _llm_envelope(run_id_2, request_id_2, 1),
        make_envelope(
            EVENT_REQUEST_UPSERTED,
            run_id=run_id_2,
            request_id=request_id_2,
            session_id="conv-fk-window-parent",
            turn_number=0,
            payload={"prompt": "prompt", "domain": "general"},
        ),
    ]
    conn = _get_connection(db_path)
    try:
        result_2 = route_window(window, conn)
    finally:
        conn.close()
    assert result_2.written == 2
    assert result_2.failed == 0
    assert _count_rows(db_path, "requests", where="run_id = ?", args=(run_id_2,)) == 1
    assert _count_rows(db_path, "llm_calls", where="run_id = ?", args=(run_id_2,)) == 1


def test_backpressure_counts_not_drops(monkeypatch):
    monkeypatch.setenv("MORALSTACK_OBSERVABILITY_MODE", "file_only")
    started = threading.Event()
    release = threading.Event()

    def stalled_route_window(envelopes, conn):
        started.set()
        release.wait(timeout=5.0)
        return WindowResult(written=len(envelopes))

    monkeypatch.setattr(router, "route_window", stalled_route_window)
    queue = ObservabilityWriteQueue(maxsize=8, batch_max_items=500, batch_max_delay_ms=1)
    queue.submit_envelope(_llm_envelope("run-backpressure", "req-backpressure", 0))
    assert started.wait(timeout=2.0)

    for index in range(1, 50):
        queue.submit_envelope(_llm_envelope("run-backpressure", "req-backpressure", index))

    stats_before_release = queue.stats()
    release.set()
    queue.shutdown(timeout=5.0)
    stats = queue.stats()

    assert stats_before_release["dropped_count"] == 41
    assert stats["submitted_count"] == 50
    assert stats["written_count"] == 9
    assert stats["dropped_count"] == 41
    assert stats["failed_count"] == 0


def test_shutdown_drains_all_queued(tmp_path, monkeypatch):
    db_path = _configure_db(tmp_path, monkeypatch, "shutdown-drain.db")
    run_id = "run-shutdown-drain"
    request_id = "req-shutdown-drain"
    assert create_run(run_id, run_type="load", meta={})
    assert upsert_request(run_id, request_id, prompt="prompt", domain="general")

    queue = ObservabilityWriteQueue(maxsize=500, batch_max_items=25, batch_max_delay_ms=1)
    for index in range(100):
        queue.submit_envelope(_llm_envelope(run_id, request_id, index))
    queue.shutdown(timeout=15.0)
    stats = queue.stats()

    assert _count_rows(db_path, "llm_calls", where="run_id = ?", args=(run_id,)) == 100
    assert stats["submitted_count"] == 100
    assert stats["written_count"] == 100
    assert stats["dropped_count"] == 0
    assert stats["failed_count"] == 0
    assert stats["worker_alive"] is False


@pytest.mark.slow
def test_throughput_new_not_slower_than_legacy(tmp_path, monkeypatch):
    # Single-threaded legacy arm pays one FULL fsync per event, so keep N modest to
    # bound the slow benchmark; heavy-concurrency correctness is covered separately
    # by test_concurrency_emitted_equals_persisted.
    total = 4_000
    workers = 100
    per_worker = total // workers

    def run_legacy() -> float:
        db_path = _configure_db(tmp_path, monkeypatch, "throughput-legacy.db")
        run_id = "run-throughput-legacy"
        request_id = "req-throughput-legacy"
        assert create_run(run_id, run_type="load", meta={})
        assert upsert_request(run_id, request_id, prompt="prompt", domain="general")
        sink = SqliteEventSink()

        # Legacy pre-P2 path, measured SINGLE-THREADED. Each event goes through
        # write_envelope, which opens its OWN ephemeral connection
        # (synchronous=FULL), inserts, commits, and closes — the per-event
        # connection churn + per-commit fsync that the windowed worker replaces
        # (NOT write_window on a reused connection, which is the NEW mechanism).
        # Single-threaded on purpose: under concurrency the old per-event path is
        # LOSSY (database-is-locked drops rows) — exactly the defect P2 fixes and
        # proven by test_concurrency_emitted_equals_persisted. Measuring it
        # single-threaded isolates the write-mechanism cost without that confound,
        # so the before/after timing compares like for like over the same N events.
        t0 = time.perf_counter()
        for index in range(total):
            sink.write_envelope(_llm_envelope(run_id, request_id, index))
        wall = time.perf_counter() - t0
        assert _count_rows(db_path, "llm_calls", where="run_id = ?", args=(run_id,)) == total
        return wall

    def run_windowed() -> tuple[float, dict[str, object], list[float]]:
        db_path = _configure_db(tmp_path, monkeypatch, "throughput-windowed.db")
        run_id = "run-throughput-windowed"
        request_id = "req-throughput-windowed"
        assert create_run(run_id, run_type="load", meta={})
        assert upsert_request(run_id, request_id, prompt="prompt", domain="general")
        queue = ObservabilityWriteQueue(maxsize=total + 100, batch_max_items=500, batch_max_delay_ms=5)

        def enqueue_range(worker_index: int) -> None:
            start = worker_index * per_worker
            for offset in range(per_worker):
                queue.submit_envelope(_llm_envelope(run_id, request_id, start + offset))

        t0 = time.perf_counter()
        with ThreadPoolExecutor(max_workers=workers) as pool:
            for future in [pool.submit(enqueue_range, worker) for worker in range(workers)]:
                future.result()

        finalization_latencies_ms: list[float] = []
        for index in range(25):
            final_request_id = f"req-throughput-final-{index}"
            assert upsert_request(run_id, final_request_id, prompt="prompt", domain="general")
            finalize_t0 = time.perf_counter()
            result = finalize_audit_sync(
                run_id=run_id,
                request_id=final_request_id,
                final_action="NORMAL_COMPLETE",
                final_response="ok",
                domain="general",
                proxy_summary={
                    "conversation_id": f"conv-throughput-{index}",
                    "turn_index": index,
                    "final_action": "NORMAL_COMPLETE",
                    "metadata": {"final_action": "NORMAL_COMPLETE"},
                },
            )
            finalization_latencies_ms.append((time.perf_counter() - finalize_t0) * 1000.0)
            assert result.failed == 0

        queue.shutdown(timeout=60.0)
        wall = time.perf_counter() - t0
        stats = queue.stats()
        assert _count_rows(db_path, "llm_calls", where="run_id = ?", args=(run_id,)) == total
        assert stats["dropped_count"] == 0
        return wall, stats, finalization_latencies_ms

    old_wall = run_legacy()
    router._sqlite_sink = None
    router._jsonl_sink = None
    new_wall, stats, finalization_latencies_ms = run_windowed()
    p95 = _percentile(finalization_latencies_ms, 0.95)
    p99 = _percentile(finalization_latencies_ms, 0.99)
    print(
        "persistence throughput old_wall_s="
        f"{old_wall:.4f} new_wall_s={new_wall:.4f} "
        f"finalize_p95_ms={p95:.3f} finalize_p99_ms={p99:.3f}"
    )

    assert stats["submitted_count"] == total
    assert stats["written_count"] == total
    assert stats["failed_count"] == 0
    assert new_wall <= old_wall * 1.10
