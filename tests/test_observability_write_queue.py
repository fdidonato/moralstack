"""Tests for the windowed observability write queue."""

from __future__ import annotations

import threading
from unittest.mock import MagicMock

import pytest

from moralstack.models.risk.estimator import LLMBasedRiskEstimator
from moralstack.models.risk.schema import RiskEstimatorConfig
from moralstack.observability import router
from moralstack.observability import service as service_module
from moralstack.observability.events import EVENT_DEBUG_EVENT, EVENT_LLM_CALL, make_envelope
from moralstack.observability.service import get_obs
from moralstack.observability.sinks.sqlite_sink import _get_connection, create_run, init_db, upsert_request
from moralstack.observability.write_queue import ObservabilityWriteQueue
from moralstack.persistence.sink import persist_llm_call


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


def _env(i: int = 0):
    return make_envelope(
        EVENT_LLM_CALL,
        run_id="run-q",
        request_id="req-q",
        payload={"phase": "p", "module": "m", "action": f"a-{i}", "prompt": "", "raw_response": ""},
    )


def _configure_db(tmp_path, monkeypatch, name: str) -> str:
    db_path = str(tmp_path / name)
    monkeypatch.setenv("MORALSTACK_OBSERVABILITY_DB_PATH", db_path)
    monkeypatch.setenv("MORALSTACK_DB_PATH", db_path)
    monkeypatch.setenv("MORALSTACK_OBSERVABILITY_MODE", "db_only")
    monkeypatch.setenv("MORALSTACK_PERSIST_MODE", "db_only")
    assert init_db(db_path)
    return db_path


def _row_count(db_path: str, table: str) -> int:
    conn = _get_connection(db_path)
    try:
        return int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
    finally:
        conn.close()


def test_submit_never_raises_on_full():
    release = threading.Event()
    q = ObservabilityWriteQueue(maxsize=1)
    try:
        q.submit(lambda: release.wait(timeout=2.0))
        for i in range(25):
            q.submit_envelope(_env(i))
        release.set()
        q.shutdown(timeout=5.0)
        stats = q.stats()
        assert stats["dropped_count"] > 0
        assert stats["submitted_count"] >= stats["dropped_count"]
    finally:
        release.set()
        q.shutdown(timeout=1.0)


def test_dropped_counter_is_queryable():
    release = threading.Event()
    q = ObservabilityWriteQueue(maxsize=1)
    try:
        q.submit(lambda: release.wait(timeout=2.0))
        for i in range(10):
            q.submit_envelope(_env(i))
        assert q.stats()["dropped_count"] > 0
    finally:
        release.set()
        q.shutdown(timeout=5.0)


def test_submit_swallows_sink_exception(monkeypatch):
    def fail_route_window(envelopes, conn):
        raise RuntimeError("boom")

    monkeypatch.setattr("moralstack.observability.router.route_window", fail_route_window)
    q = ObservabilityWriteQueue(batch_max_items=10, batch_max_delay_ms=1)
    try:
        q.submit_envelope(_env(1))
        q.flush(timeout=5.0)
        assert q.stats()["failed_count"] == 1
        assert "boom" in str(q.stats()["last_error"])
    finally:
        q.shutdown(timeout=5.0)


def test_persist_sink_enqueues_not_routes(tmp_path, monkeypatch):
    db_path = _configure_db(tmp_path, monkeypatch, "persist-sink-enqueues.db")
    assert create_run("run-q", run_type="test", meta={})
    assert upsert_request("run-q", "req-q", prompt="prompt", domain="general")
    monkeypatch.setattr(
        "moralstack.observability.router.route",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("route must not run on request thread")),
    )

    assert persist_llm_call(
        run_id="run-q",
        request_id="req-q",
        phase="p",
        module="m",
        action="a",
        prompt="p",
        system_prompt="s",
        raw_response="r",
    )
    get_obs().flush(timeout=5.0)

    assert _row_count(db_path, "llm_calls") == 1


def test_window_failure_increments_failed_count(monkeypatch):
    def partial(envelopes, conn):
        from moralstack.observability.router import WindowResult

        return WindowResult(written=1, failed=2, error="partial")

    monkeypatch.setattr("moralstack.observability.router.route_window", partial)
    q = ObservabilityWriteQueue(batch_max_items=10, batch_max_delay_ms=1)
    try:
        q.submit_batch([_env(1), _env(2), _env(3)])
        q.flush(timeout=5.0)
        stats = q.stats()
        assert stats["written_count"] == 1
        assert stats["failed_count"] == 2
        assert stats["last_error"] == "partial"
    finally:
        q.shutdown(timeout=5.0)


def test_worker_micro_batches_window(monkeypatch):
    windows: list[int] = []

    def capture(envelopes, conn):
        from moralstack.observability.router import WindowResult

        windows.append(len(envelopes))
        return WindowResult(written=len(envelopes))

    monkeypatch.setattr("moralstack.observability.router.route_window", capture)
    q = ObservabilityWriteQueue(batch_max_items=50, batch_max_delay_ms=10)
    try:
        for i in range(120):
            q.submit_envelope(_env(i))
        q.flush(timeout=5.0)
        assert sum(windows) == 120
        assert max(windows) <= 50
        assert len(windows) < 120
    finally:
        q.shutdown(timeout=5.0)


class _FakeConnection:
    def __init__(self) -> None:
        self.closed = False
        self.execute_calls: list[str] = []
        self.execute_thread_ids: list[int] = []
        self.close_thread_id: int | None = None

    def execute(self, sql: str):
        self.execute_calls.append(sql)
        self.execute_thread_ids.append(threading.get_ident())
        return None

    def close(self) -> None:
        self.closed = True
        self.close_thread_id = threading.get_ident()


def _install_fake_connection(monkeypatch):
    connections: list[_FakeConnection] = []

    def fake_connection(_path: str) -> _FakeConnection:
        conn = _FakeConnection()
        connections.append(conn)
        return conn

    monkeypatch.setenv("MORALSTACK_OBSERVABILITY_MODE", "db_only")
    monkeypatch.setenv("MORALSTACK_OBSERVABILITY_DB_PATH", "fake.db")
    monkeypatch.setattr("moralstack.observability.write_queue._get_connection", fake_connection)
    return connections


def test_worker_reuses_persistent_connection(monkeypatch):
    connections = _install_fake_connection(monkeypatch)
    seen = []

    def capture(envelopes, conn):
        from moralstack.observability.router import WindowResult

        seen.append(conn)
        return WindowResult(written=len(envelopes))

    monkeypatch.setattr("moralstack.observability.router.route_window", capture)
    q = ObservabilityWriteQueue(batch_max_items=1, batch_max_delay_ms=1)
    try:
        for i in range(3):
            q.submit_envelope(_env(i))
        q.flush(timeout=5.0)
        assert len(connections) == 1
        assert seen == [connections[0], connections[0], connections[0]]
    finally:
        q.shutdown(timeout=5.0)


def test_worker_closes_connection_on_shutdown(monkeypatch):
    connections = _install_fake_connection(monkeypatch)
    monkeypatch.setattr(
        "moralstack.observability.router.route_window",
        lambda envelopes, conn: router.WindowResult(written=len(envelopes)),
    )
    q = ObservabilityWriteQueue(batch_max_items=1, batch_max_delay_ms=1)
    q.submit_envelope(_env(1))
    q.shutdown(timeout=5.0)
    assert len(connections) == 1
    assert connections[0].closed is True
    assert q.stats()["worker_connection_closed_thread_id"] == q.stats()["worker_connection_thread_id"]


def test_worker_sets_synchronous_normal(monkeypatch):
    connections = _install_fake_connection(monkeypatch)
    monkeypatch.setattr(
        "moralstack.observability.router.route_window",
        lambda envelopes, conn: router.WindowResult(written=len(envelopes)),
    )
    q = ObservabilityWriteQueue(batch_max_items=1, batch_max_delay_ms=1)
    try:
        q.submit_envelope(_env(1))
        q.flush(timeout=5.0)
        assert any(call.strip().upper() == "PRAGMA SYNCHRONOUS=NORMAL" for call in connections[0].execute_calls)
    finally:
        q.shutdown(timeout=5.0)


def test_worker_connection_thread_ownership(monkeypatch):
    connections = _install_fake_connection(monkeypatch)
    monkeypatch.setattr(
        "moralstack.observability.router.route_window",
        lambda envelopes, conn: router.WindowResult(written=len(envelopes)),
    )
    main_thread_id = threading.get_ident()
    q = ObservabilityWriteQueue(batch_max_items=1, batch_max_delay_ms=1)
    q.submit_envelope(_env(1))
    q.shutdown(timeout=5.0)
    stats = q.stats()
    assert len(connections) == 1
    assert stats["worker_connection_thread_id"] != main_thread_id
    assert connections[0].execute_thread_ids == [stats["worker_connection_thread_id"]]
    assert connections[0].close_thread_id == stats["worker_connection_thread_id"]


def test_second_queue_owns_separate_connection_and_ephemeral_stays_full(tmp_path, monkeypatch):
    """Each ObservabilityWriteQueue worker owns its OWN persistent connection, and an
    ephemeral _get_connection() stays synchronous=FULL — only the worker connection
    relaxes to NORMAL (proven by test_worker_sets_synchronous_normal). Uses a REAL DB
    (the thread-ownership tests above use a fake connection). Codex v4 diff-review
    required: worker-connection scope + FULL ephemeral guarantee."""
    db_path = _configure_db(tmp_path, monkeypatch, "wq_scope.db")
    assert create_run("run-q", run_type="test", meta={})
    assert upsert_request("run-q", "req-q", prompt="p")

    q1 = ObservabilityWriteQueue(batch_max_items=1, batch_max_delay_ms=1)
    q2 = ObservabilityWriteQueue(batch_max_items=1, batch_max_delay_ms=1)
    try:
        q1.submit_envelope(_env(1))
        q2.submit_envelope(_env(2))
        q1.flush(timeout=5.0)
        q2.flush(timeout=5.0)
        s1, s2 = q1.stats(), q2.stats()
        # Two queues => two distinct worker threads => two distinct connections.
        assert s1["worker_connection_thread_id"] is not None
        assert s2["worker_connection_thread_id"] is not None
        assert s1["worker_connection_thread_id"] != s2["worker_connection_thread_id"]
        # Ephemeral (sync/request-thread) connections keep the SQLite default FULL == 2.
        eph = _get_connection(db_path)
        try:
            assert eph.execute("PRAGMA synchronous").fetchone()[0] == 2
        finally:
            eph.close()
    finally:
        q1.shutdown(timeout=5.0)
        q2.shutdown(timeout=5.0)


def test_dual_mode_jsonl_window_failure_counted_separately(monkeypatch):
    """In dual mode a JSONL window failure is counted in jsonl_failed_count WITHOUT
    flipping the SQLite-driven written/failed headline (Codex v4 diff-review #1:
    per-sink async window accounting)."""
    _install_fake_connection(monkeypatch)
    monkeypatch.setenv("MORALSTACK_OBSERVABILITY_MODE", "dual")
    monkeypatch.setattr(
        "moralstack.observability.router.route_window",
        lambda envelopes, conn: router.WindowResult(
            written=len(envelopes),
            sqlite_written=len(envelopes),
            jsonl_failed=len(envelopes),
            error="jsonl boom",
        ),
    )
    q = ObservabilityWriteQueue(batch_max_items=1, batch_max_delay_ms=1)
    try:
        q.submit_envelope(_env(1))
        q.flush(timeout=5.0)
        s = q.stats()
        assert s["jsonl_failed_count"] == 1
        assert s["sqlite_written_count"] == 1
        assert s["written_count"] == 1
        # The JSONL failure does NOT flip the SQLite-driven headline.
        assert s["failed_count"] == 0
    finally:
        q.shutdown(timeout=5.0)


def test_drop_marker_only_attributed_when_run_id_present(monkeypatch):
    """A drop with NO run_id must not feed the PERSISTED count-only marker nor be
    attributed to a later drop's run_id; the total dropped_count still counts it
    (Codex v4 diff-review #2: drop-marker attribution)."""
    from moralstack.observability.context import set_current_request_id, set_current_run_id

    q = ObservabilityWriteQueue(batch_max_items=1, batch_max_delay_ms=1)
    try:
        set_current_run_id("")
        set_current_request_id("")
        q._record_drop(2, "no-run")  # no run_id => not in the persisted marker
        set_current_run_id("r-drop")
        set_current_request_id("req-drop")
        q._record_drop(3, "with-run")  # only these are attributable
        marker = q._take_drop_marker()
        assert marker is not None
        assert marker.run_id == "r-drop"
        assert marker.payload["count"] == 3  # not 5 — the no-run drop is excluded
        assert q.stats()["dropped_count"] == 5  # but the total still counts all drops
    finally:
        set_current_run_id("")
        set_current_request_id("")
        q.shutdown(timeout=5.0)


def test_mixed_legacy_and_envelope_submit_preserves_submission_order(monkeypatch):
    order: list[str] = []

    def capture(envelopes, conn):
        from moralstack.observability.router import WindowResult

        order.extend(str(env.payload["marker"]) for env in envelopes)
        return WindowResult(written=len(envelopes))

    monkeypatch.setattr("moralstack.observability.router.route_window", capture)
    q = ObservabilityWriteQueue(batch_max_items=10, batch_max_delay_ms=50)
    try:
        q.submit_envelope(make_envelope(EVENT_DEBUG_EVENT, run_id="r", payload={"marker": "A"}))
        q.submit(lambda: order.append("B"))
        q.submit_envelope(make_envelope(EVENT_DEBUG_EVENT, run_id="r", payload={"marker": "C"}))
        q.flush(timeout=5.0)
        assert order == ["A", "B", "C"]
    finally:
        q.shutdown(timeout=5.0)


def test_window_per_envelope_isolation(tmp_path, monkeypatch):
    db_path = _configure_db(tmp_path, monkeypatch, "window-isolation.db")
    assert create_run("run-q", run_type="test", meta={})
    assert upsert_request("run-q", "req-q", prompt="prompt", domain="general")
    bad = make_envelope(
        EVENT_LLM_CALL,
        run_id="run-q",
        request_id=None,
        payload={"phase": "p", "module": "m", "action": "bad", "prompt": "", "raw_response": ""},
    )
    q = ObservabilityWriteQueue(batch_max_items=10, batch_max_delay_ms=1)
    try:
        q.submit_batch([_env(1), bad, _env(2)])
        q.flush(timeout=5.0)
        stats = q.stats()
        assert stats["written_count"] == 2
        assert stats["failed_count"] == 1
        assert _row_count(db_path, "llm_calls") == 2
    finally:
        q.shutdown(timeout=5.0)


def test_risk_mini_calls_persist_via_queue(tmp_path, monkeypatch):
    db_path = _configure_db(tmp_path, monkeypatch, "risk-mini-queue.db")
    assert create_run("run-risk-q", run_type="test", meta={})
    assert upsert_request("run-risk-q", "req-risk-q", prompt="prompt", domain="general")
    policy = MagicMock()
    policy.model = "gpt-main"
    policy.tracker = None
    estimator = LLMBasedRiskEstimator(policy=policy, config=RiskEstimatorConfig())
    monkeypatch.setattr(
        "moralstack.observability.router.route_batch",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("route_batch must not run")),
    )

    from moralstack.observability.context import set_current_request_id, set_current_run_id

    set_current_run_id("run-risk-q")
    set_current_request_id("req-risk-q")
    estimator._persist_mini_llm_calls_batch(
        [
            {
                "system_prompt": "s",
                "prompt": "p",
                "raw_response": "r1",
                "action": "a1",
                "duration_ms": 1.0,
                "attempts": 1,
            },
            {
                "system_prompt": "s",
                "prompt": "p",
                "raw_response": "r2",
                "action": "a2",
                "duration_ms": 1.0,
                "attempts": 1,
            },
            {
                "system_prompt": "s",
                "prompt": "p",
                "raw_response": "r3",
                "action": "a3",
                "duration_ms": 1.0,
                "attempts": 1,
            },
        ]
    )
    get_obs().flush(timeout=5.0)

    assert _row_count(db_path, "llm_calls") == 3


def test_no_telemetry_route_on_request_thread(tmp_path, monkeypatch):
    db_path = _configure_db(tmp_path, monkeypatch, "no-request-thread-route.db")
    assert create_run("run-no-route", run_type="test", meta={})
    assert upsert_request("run-no-route", "req-no-route", prompt="prompt", domain="general")
    request_thread_id = threading.get_ident()
    route_thread_ids: list[int] = []

    def capture_route(*_args, **_kwargs):
        route_thread_ids.append(threading.get_ident())

    monkeypatch.setattr("moralstack.observability.router.route", capture_route)
    monkeypatch.setattr("moralstack.observability.router.route_batch", capture_route)

    assert persist_llm_call(
        run_id="run-no-route",
        request_id="req-no-route",
        phase="p",
        module="m",
        action="single",
        prompt="p",
        system_prompt="s",
        raw_response="r",
    )
    from moralstack.persistence.sink import persist_llm_calls_batch

    assert persist_llm_calls_batch(
        [
            {
                "run_id": "run-no-route",
                "request_id": "req-no-route",
                "phase": "p",
                "module": "m",
                "action": "batch",
                "prompt": "p",
                "system_prompt": "s",
                "raw_response": "r",
            }
        ]
    )
    get_obs().flush(timeout=5.0)

    assert route_thread_ids == []
    assert threading.get_ident() == request_thread_id
    assert _row_count(db_path, "llm_calls") == 2


def test_emit_none_does_not_raise():
    q = ObservabilityWriteQueue()
    try:
        q.submit_envelope(None)  # type: ignore[arg-type]
        stats = q.stats()
        assert stats["submitted_count"] == 1
        assert stats["failed_count"] == 1
    finally:
        q.shutdown(timeout=5.0)


def test_shutdown_sentinel_accounting(monkeypatch):
    def capture(envelopes, conn):
        from moralstack.observability.router import WindowResult

        return WindowResult(written=len(envelopes))

    monkeypatch.setattr("moralstack.observability.router.route_window", capture)
    q = ObservabilityWriteQueue(batch_max_items=10, batch_max_delay_ms=1)
    for i in range(5):
        q.submit_envelope(_env(i))
    q.shutdown(timeout=5.0)
    assert q.stats()["unfinished_tasks"] == 0
    assert q.stats()["worker_alive"] is False
