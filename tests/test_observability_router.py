"""Tests for EventRouter: db_only / file_only / dual mode dispatch."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from moralstack.observability.events import EVENT_LLM_CALL, EVENT_ORCHESTRATION_EVENT, make_envelope
from moralstack.observability.router import route, route_batch


def _env(event_type: str = EVENT_LLM_CALL, run_id: str = "r1", request_id: str = "q1"):
    return make_envelope(event_type, run_id=run_id, request_id=request_id, payload={"module": "test"})


class TestRouterDbOnly:
    def test_db_only_calls_sqlite_only(self, monkeypatch):
        sqlite_mock = MagicMock()
        jsonl_mock = MagicMock()
        monkeypatch.setenv("MORALSTACK_OBSERVABILITY_MODE", "db_only")
        with (
            patch("moralstack.observability.router._get_sqlite_sink", return_value=sqlite_mock),
            patch("moralstack.observability.router._get_jsonl_sink", return_value=jsonl_mock),
        ):
            env = _env()
            route(env)
        sqlite_mock.write_envelope.assert_called_once_with(env)
        jsonl_mock.write_envelope.assert_not_called()

    def test_db_only_batch_calls_sqlite_only(self, monkeypatch):
        sqlite_mock = MagicMock()
        jsonl_mock = MagicMock()
        monkeypatch.setenv("MORALSTACK_OBSERVABILITY_MODE", "db_only")
        envs = [_env(), _env(EVENT_ORCHESTRATION_EVENT)]
        with (
            patch("moralstack.observability.router._get_sqlite_sink", return_value=sqlite_mock),
            patch("moralstack.observability.router._get_jsonl_sink", return_value=jsonl_mock),
        ):
            route_batch(envs)
        sqlite_mock.write_batch.assert_called_once_with(envs)
        jsonl_mock.write_batch.assert_not_called()


class TestRouterFileOnly:
    def test_file_only_calls_jsonl_only(self, monkeypatch):
        sqlite_mock = MagicMock()
        jsonl_mock = MagicMock()
        monkeypatch.setenv("MORALSTACK_OBSERVABILITY_MODE", "file_only")
        monkeypatch.delenv("MORALSTACK_OBSERVABILITY_DB_PATH", raising=False)
        monkeypatch.delenv("MORALSTACK_DB_PATH", raising=False)
        with (
            patch("moralstack.observability.router._get_sqlite_sink", return_value=sqlite_mock),
            patch("moralstack.observability.router._get_jsonl_sink", return_value=jsonl_mock),
        ):
            env = _env()
            route(env)
        jsonl_mock.write_envelope.assert_called_once_with(env)
        sqlite_mock.write_envelope.assert_not_called()


class TestRouterDual:
    def test_dual_calls_both_sinks(self, monkeypatch):
        sqlite_mock = MagicMock()
        jsonl_mock = MagicMock()
        monkeypatch.setenv("MORALSTACK_OBSERVABILITY_MODE", "dual")
        with (
            patch("moralstack.observability.router._get_sqlite_sink", return_value=sqlite_mock),
            patch("moralstack.observability.router._get_jsonl_sink", return_value=jsonl_mock),
        ):
            env = _env()
            route(env)
        sqlite_mock.write_envelope.assert_called_once_with(env)
        jsonl_mock.write_envelope.assert_called_once_with(env)


class TestRouterEmptyBatch:
    def test_empty_batch_noop(self, monkeypatch):
        sqlite_mock = MagicMock()
        jsonl_mock = MagicMock()
        monkeypatch.setenv("MORALSTACK_OBSERVABILITY_MODE", "dual")
        with (
            patch("moralstack.observability.router._get_sqlite_sink", return_value=sqlite_mock),
            patch("moralstack.observability.router._get_jsonl_sink", return_value=jsonl_mock),
        ):
            route_batch([])
        sqlite_mock.write_batch.assert_not_called()
        jsonl_mock.write_batch.assert_not_called()
