"""Unit tests for PersistencePort / DefaultPersistence / NullPersistence."""

from __future__ import annotations

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
        assert init_db.call_count == 1
    assert dp._db_initialized is True


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
    assert PersistencePort.__protocol_attrs__ == frozenset(
        {"set_request_context", "ensure_run_and_upsert_request", "update_request_domain"}
    )


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
