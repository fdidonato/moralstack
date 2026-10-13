"""Tests for the in-process serialization of the SQLite rollback->WAL switch.

On a brand-new database file SQLite rejects a concurrent ``PRAGMA journal_mode=WAL``
with ``database is locked`` immediately (busy_timeout does not apply). The sink
serializes that single pragma per database file within the process.
"""

from __future__ import annotations

import logging
import os
import sqlite3
import threading
import time

import pytest

import moralstack.observability.sinks.sqlite_sink as sink
from moralstack.observability.sinks.sqlite_sink import _get_connection, init_db

_WAL_PRAGMA = "PRAGMA journal_mode=WAL"


@pytest.fixture(autouse=True)
def _db_only_env(monkeypatch):
    monkeypatch.setenv("MORALSTACK_OBSERVABILITY_MODE", "db_only")
    monkeypatch.delenv("MORALSTACK_OBSERVABILITY_DB_PATH", raising=False)


class _HookConnection(sqlite3.Connection):
    """Connection whose WAL pragma can be intercepted per database path."""

    hook = None  # callable(path) -> None, installed by the tests
    connected: dict = {}  # path -> number of connections created (set by the fixture)
    path: str = ""

    def execute(self, sql, *args, **kwargs):  # type: ignore[override]
        if sql == _WAL_PRAGMA and _HookConnection.hook is not None:
            _HookConnection.hook(self.path)
        return super().execute(sql, *args, **kwargs)


@pytest.fixture
def hooked_connect(monkeypatch):
    """Route sqlite_sink connections through _HookConnection (no C-type patching).

    Patches the global ``sqlite3.connect`` attribute (``sink.sqlite3`` is the shared module),
    so every in-process connect goes through the hook while the test runs; monkeypatch
    restores it. ``_HookConnection.connected`` counts connections created per path.
    """
    real_connect = sqlite3.connect
    _HookConnection.connected = {}

    def _connect(database, *args, **kwargs):
        conn = real_connect(database, *args, factory=_HookConnection, **kwargs)
        conn.path = str(database)
        _HookConnection.connected[conn.path] = _HookConnection.connected.get(conn.path, 0) + 1
        return conn

    monkeypatch.setattr(sink.sqlite3, "connect", _connect)
    _HookConnection.hook = None
    yield
    _HookConnection.hook = None


# ---------------------------------------------------------------------------
# B1: real mechanism (probabilistic, opt-in)
# ---------------------------------------------------------------------------


@pytest.mark.slow
def test_concurrent_fresh_db_openers_all_succeed(tmp_path, caplog):
    n_threads = 8
    for trial in range(5):
        path = str(tmp_path / f"fresh_{trial}.db")
        barrier = threading.Barrier(n_threads)
        init_results: list[bool] = []
        bare_errors: list[Exception] = []

        def _worker(idx: int) -> None:
            try:
                barrier.wait(timeout=10)
                if idx % 2 == 0:
                    init_results.append(init_db(path))
                else:
                    _get_connection(path).close()
            except Exception as exc:  # noqa: BLE001 - collected and asserted below
                bare_errors.append(exc)

        with caplog.at_level(logging.WARNING, logger="moralstack.observability.sinks.sqlite_sink"):
            threads = [threading.Thread(target=_worker, args=(i,)) for i in range(n_threads)]
            for t in threads:
                t.start()
            for t in threads:
                t.join(timeout=30)
        assert not any(t.is_alive() for t in threads)
        assert init_results and all(init_results), f"trial {trial}: init_db returned False"
        assert not bare_errors, f"trial {trial}: bare connections raised {bare_errors!r}"
        assert "init_db failed" not in caplog.text


# ---------------------------------------------------------------------------
# B2: lexical key contract
# ---------------------------------------------------------------------------


def test_wal_switch_lock_key_normalization(tmp_path, monkeypatch):
    lock_for = sink._wal_switch_lock
    target = tmp_path / "a.db"
    abs_path = str(target)

    monkeypatch.chdir(tmp_path)
    assert lock_for("a.db") is lock_for(abs_path)

    (tmp_path / "sub").mkdir()
    assert lock_for(str(tmp_path / "sub" / ".." / "a.db")) is lock_for(abs_path)

    if os.name == "nt":
        assert lock_for(abs_path.upper()) is lock_for(abs_path.lower())

    assert lock_for(str(tmp_path / "b.db")) is not lock_for(abs_path)

    # Must never raise, whatever the input.
    lock_for("")
    lock_for(":memory:")
    lock_for(None)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# B3: serialization per path, no serialization across paths
# ---------------------------------------------------------------------------


def test_wal_switch_serializes_same_path_only(tmp_path, hooked_connect):
    path_p = str(tmp_path / "p.db")
    path_q = str(tmp_path / "q.db")
    counter_lock = threading.Lock()
    entered = {path_p: 0, path_q: 0}
    entered_events = {path_p: threading.Event(), path_q: threading.Event()}
    release_p = threading.Event()

    def _hook(path: str) -> None:
        with counter_lock:
            entered[path] += 1
        entered_events[path].set()
        if path == path_p:
            release_p.wait(timeout=10)

    _HookConnection.hook = _hook

    def _open(path: str) -> None:
        _get_connection(path).close()

    t1 = threading.Thread(target=_open, args=(path_p,))
    t1.start()
    assert entered_events[path_p].wait(5)

    t2 = threading.Thread(target=_open, args=(path_p,))
    t2.start()
    # Bounded wait until thread 2's connect() has returned (poll, no unbounded sleep): the lock
    # must not cover sqlite3.connect, only the WAL pragma.
    deadline = time.monotonic() + 5
    while _HookConnection.connected.get(path_p, 0) < 2 and time.monotonic() < deadline:
        threading.Event().wait(0.01)
    assert _HookConnection.connected.get(path_p, 0) == 2, "connect() must not be serialized by the WAL lock"
    # Negative probe only: the outcome is decided by the entered count, not the sleep.
    threading.Event().wait(0.2)
    with counter_lock:
        assert entered[path_p] == 1, "second opener entered the WAL pragma while the first held the lock"

    t3 = threading.Thread(target=_open, args=(path_q,))
    t3.start()
    assert entered_events[path_q].wait(5), "a different path must not be serialized behind P"

    release_p.set()
    for t in (t1, t2, t3):
        t.join(timeout=10)
    assert not any(t.is_alive() for t in (t1, t2, t3))
    with counter_lock:
        assert entered[path_p] == 2


# ---------------------------------------------------------------------------
# B4: the lock is released when the pragma fails
# ---------------------------------------------------------------------------


def test_get_connection_releases_wal_lock_on_pragma_error(tmp_path, hooked_connect, caplog):
    path_p = str(tmp_path / "err.db")
    failed = {"done": False}

    def _hook(path: str) -> None:
        if path == path_p and not failed["done"]:
            failed["done"] = True
            raise sqlite3.OperationalError("database is locked")

    _HookConnection.hook = _hook

    with pytest.raises(sqlite3.OperationalError):
        _get_connection(path_p)

    errors: list[Exception] = []

    def _second() -> None:
        try:
            _get_connection(path_p).close()
        except Exception as exc:  # noqa: BLE001 - collected and asserted below
            errors.append(exc)

    t = threading.Thread(target=_second)
    t.start()
    t.join(timeout=5)
    assert not t.is_alive(), "WAL lock was not released after the pragma error"
    assert not errors

    # init_db keeps its contract: False + WARNING, never raises.
    path_p2 = str(tmp_path / "err2.db")
    failed["done"] = False

    def _hook2(path: str) -> None:
        if path == path_p2 and not failed["done"]:
            failed["done"] = True
            raise sqlite3.OperationalError("database is locked")

    _HookConnection.hook = _hook2
    with caplog.at_level(logging.WARNING, logger="moralstack.observability.sinks.sqlite_sink"):
        assert init_db(path_p2) is False
    assert "init_db failed" in caplog.text
