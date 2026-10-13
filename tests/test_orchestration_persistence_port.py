"""Unit tests for PersistencePort / DefaultPersistence / NullPersistence."""

from __future__ import annotations

import sqlite3
import threading
from unittest.mock import patch

import pytest

import moralstack.observability.context as obs_context
from moralstack.observability.context import set_current_request_id, set_current_run_id
from moralstack.orchestration.default_persistence import DefaultPersistence
from moralstack.orchestration.null_persistence import NullPersistence
from moralstack.orchestration.persistence_port import PersistencePort


@pytest.fixture(autouse=True)
def _clear_obs_context():
    obs_context._run_id.set(None)
    obs_context._request_id.set(None)
    yield
    obs_context._run_id.set(None)
    obs_context._request_id.set(None)


def test_null_persistence_all_methods_are_noop():
    n = NullPersistence()
    n.set_request_context("r1")
    n.ensure_run_and_upsert_request("r1", "p", domain="d", conversation_id="c", turn_index=1, parent_request_id="p")
    n.update_request_domain("r1", "d")


def test_default_persistence_lazy_db_init_flag(tmp_path, monkeypatch):
    db_path = str(tmp_path / "lazy.db")
    monkeypatch.setenv("MORALSTACK_DB_PATH", db_path)
    set_current_run_id("run-lazy")

    dp = DefaultPersistence()
    with patch("moralstack.orchestration.default_persistence.init_db", return_value=True) as init_db:
        dp.ensure_run_and_upsert_request("req1", "hello")
        dp.ensure_run_and_upsert_request("req2", "hello2")
        dp.ensure_run_and_upsert_request("req3", "hello3")
        assert init_db.call_count == 1
    assert dp._db_initialized_for == db_path


def test_default_persistence_reinitializes_on_path_change(tmp_path, monkeypatch):
    path_a = str(tmp_path / "a.db")
    path_b = str(tmp_path / "b.db")
    monkeypatch.delenv("MORALSTACK_OBSERVABILITY_DB_PATH", raising=False)
    monkeypatch.setenv("MORALSTACK_DB_PATH", path_a)
    set_current_run_id("run-repoint")

    dp = DefaultPersistence()
    with patch("moralstack.orchestration.default_persistence.init_db", return_value=True) as init_db:
        dp.ensure_run_and_upsert_request("req1", "p1")
        dp.ensure_run_and_upsert_request("req2", "p2")
        monkeypatch.setenv("MORALSTACK_DB_PATH", path_b)
        dp.ensure_run_and_upsert_request("req3", "p3")
        dp.ensure_run_and_upsert_request("req4", "p4")
        assert [c.args[0] for c in init_db.call_args_list] == [path_a, path_b]

        # MORALSTACK_OBSERVABILITY_DB_PATH takes precedence over the legacy variable.
        path_c = str(tmp_path / "c.db")
        monkeypatch.setenv("MORALSTACK_OBSERVABILITY_DB_PATH", path_c)
        dp.ensure_run_and_upsert_request("req5", "p5")
        assert [c.args[0] for c in init_db.call_args_list] == [path_a, path_b, path_c]

        # The second entry point follows the same path-aware cache.
        path_d = str(tmp_path / "d.db")
        monkeypatch.setenv("MORALSTACK_OBSERVABILITY_DB_PATH", path_d)
        dp.update_request_domain("req5", "general")
        dp.update_request_domain("req5", "general")
        assert [c.args[0] for c in init_db.call_args_list] == [path_a, path_b, path_c, path_d]


def test_default_persistence_failed_init_is_not_cached(tmp_path, monkeypatch):
    monkeypatch.delenv("MORALSTACK_OBSERVABILITY_DB_PATH", raising=False)
    monkeypatch.setenv("MORALSTACK_DB_PATH", str(tmp_path / "retry.db"))
    set_current_run_id("run-retry")

    dp = DefaultPersistence()
    with patch("moralstack.orchestration.default_persistence.init_db", side_effect=[False, True]) as init_db:
        dp.ensure_run_and_upsert_request("req1", "p1")
        dp.ensure_run_and_upsert_request("req2", "p2")
        dp.ensure_run_and_upsert_request("req3", "p3")
        assert init_db.call_count == 2


def test_default_persistence_no_path_never_calls_init_db(monkeypatch):
    monkeypatch.delenv("MORALSTACK_DB_PATH", raising=False)
    monkeypatch.delenv("MORALSTACK_OBSERVABILITY_DB_PATH", raising=False)
    set_current_run_id("run-nopath")

    dp = DefaultPersistence()
    with patch("moralstack.orchestration.default_persistence.init_db") as init_db:
        dp.ensure_run_and_upsert_request("req1", "p1")
        dp.update_request_domain("req1", "general")
        assert dp._ensure_db_initialized() is False
        init_db.assert_not_called()


def test_default_persistence_repoint_writes_land_in_new_db(tmp_path, monkeypatch):
    path_a = str(tmp_path / "repoint_a.db")
    path_b = str(tmp_path / "repoint_b.db")
    monkeypatch.delenv("MORALSTACK_DB_PATH", raising=False)
    monkeypatch.setenv("MORALSTACK_OBSERVABILITY_MODE", "db_only")
    set_current_run_id("run-land")

    dp = DefaultPersistence()
    monkeypatch.setenv("MORALSTACK_OBSERVABILITY_DB_PATH", path_a)
    dp.ensure_run_and_upsert_request("req-a", "prompt a")
    monkeypatch.setenv("MORALSTACK_OBSERVABILITY_DB_PATH", path_b)
    dp.ensure_run_and_upsert_request("req-b", "prompt b")

    for path, req in ((path_a, "req-a"), (path_b, "req-b")):
        conn = sqlite3.connect(path)
        try:
            rows = conn.execute("SELECT request_id FROM requests WHERE run_id = ?", ("run-land",)).fetchall()
        finally:
            conn.close()
        assert [r[0] for r in rows] == [req]


def test_default_persistence_noop_when_no_run_id(tmp_path, monkeypatch):
    monkeypatch.setenv("MORALSTACK_DB_PATH", str(tmp_path / "x.db"))
    dp = DefaultPersistence()
    with patch("moralstack.orchestration.default_persistence.get_db_path") as gdb:
        dp.ensure_run_and_upsert_request("req1", "hello")
        gdb.assert_not_called()


def test_default_persistence_noop_when_no_db_path(monkeypatch):
    monkeypatch.delenv("MORALSTACK_DB_PATH", raising=False)
    monkeypatch.delenv("MORALSTACK_OBSERVABILITY_DB_PATH", raising=False)
    set_current_run_id("run-x")
    dp = DefaultPersistence()
    with patch("moralstack.orchestration.default_persistence.create_run") as create_run:
        dp.ensure_run_and_upsert_request("req1", "hello")
        create_run.assert_not_called()


def test_default_persistence_swallows_exception_and_logs_warning(caplog, tmp_path, monkeypatch):
    db_path = str(tmp_path / "swallow.db")
    monkeypatch.setenv("MORALSTACK_DB_PATH", db_path)
    set_current_run_id("run-sw")
    dp = DefaultPersistence()
    with patch("moralstack.orchestration.default_persistence.upsert_request", side_effect=RuntimeError("boom")):
        dp.ensure_run_and_upsert_request("req1", "hello")
    assert "persistence: ensure_run_and_upsert_request failed" in caplog.text

    with patch("moralstack.orchestration.default_persistence.update_request_domain", side_effect=RuntimeError("boom2")):
        dp.update_request_domain("req1", "general")
    assert "persistence: update_request_domain failed" in caplog.text


def test_persistence_port_protocol_shape():
    for impl in (DefaultPersistence(), NullPersistence()):
        assert callable(getattr(impl, "set_request_context", None))
        assert callable(getattr(impl, "ensure_run_and_upsert_request", None))
        assert callable(getattr(impl, "update_request_domain", None))
    # ``__protocol_attrs__`` is a class attribute only on Python >= 3.12; on 3.11
    # the equivalent lives behind ``typing._get_protocol_attrs``. Resolve both so the
    # assertion is stable across the supported interpreter range.
    protocol_attrs = getattr(PersistencePort, "__protocol_attrs__", None)
    if protocol_attrs is None:
        from typing import _get_protocol_attrs

        protocol_attrs = _get_protocol_attrs(PersistencePort)
    assert set(protocol_attrs) == {
        "set_request_context",
        "ensure_run_and_upsert_request",
        "update_request_domain",
    }


def test_concurrent_ensure_run_and_upsert_request(tmp_path, monkeypatch):
    db_path = str(tmp_path / "conc.db")
    monkeypatch.setenv("MORALSTACK_DB_PATH", db_path)
    monkeypatch.setenv("MORALSTACK_PERSIST_MODE", "db_only")
    run_id = "run-conc"
    set_current_run_id(run_id)
    dp = DefaultPersistence()
    errors: list[Exception] = []

    def _worker(idx: int) -> None:
        try:
            # Context vars are not inherited by new threads: without this the call
            # returns early (no run_id) and the test would pass vacuously.
            set_current_run_id(run_id)
            set_current_request_id(f"req-{idx}")
            dp.ensure_run_and_upsert_request(f"req-{idx}", f"prompt-{idx}")
        except Exception as exc:
            errors.append(exc)

    threads = [threading.Thread(target=_worker, args=(i,)) for i in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)
    assert not errors
    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute("SELECT request_id FROM requests WHERE run_id = ?", (run_id,)).fetchall()
    finally:
        conn.close()
    assert sorted(r[0] for r in rows) == sorted(f"req-{i}" for i in range(10))
