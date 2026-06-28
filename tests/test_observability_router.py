"""Tests for EventRouter: db_only / file_only / dual mode dispatch."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from moralstack.observability.events import EVENT_LLM_CALL, EVENT_ORCHESTRATION_EVENT, make_envelope
from moralstack.observability.router import route, route_audit_sync, route_batch, route_window


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


class TestRouterWindowAccounting:
    def test_route_window_per_mode(self, monkeypatch):
        envs = [_env()]
        conn = object()

        sqlite_mock = MagicMock()
        jsonl_mock = MagicMock()
        from moralstack.observability.router import WindowResult
        from moralstack.observability.sinks.jsonl_sink import JsonlWindowResult

        sqlite_mock.write_window.return_value = WindowResult(written=1, sqlite_written=1)
        jsonl_mock.write_window.return_value = JsonlWindowResult(written=1)

        monkeypatch.setenv("MORALSTACK_OBSERVABILITY_MODE", "dual")
        with (
            patch("moralstack.observability.router._get_sqlite_sink", return_value=sqlite_mock),
            patch("moralstack.observability.router._get_jsonl_sink", return_value=jsonl_mock),
        ):
            result = route_window(envs, conn)  # type: ignore[arg-type]

        assert result.written == 1
        assert result.failed == 0
        assert result.sqlite_written == 1
        assert result.jsonl_written == 1
        sqlite_mock.write_window.assert_called_once_with(envs, conn)
        jsonl_mock.write_window.assert_called_once_with(envs)

    def test_route_window_file_only_jsonl_drives_persisted(self, monkeypatch):
        envs = [_env()]
        jsonl_mock = MagicMock()
        from moralstack.observability.sinks.jsonl_sink import JsonlWindowResult

        jsonl_mock.write_window.return_value = JsonlWindowResult(written=0, failed=1, error="jsonl fail")
        monkeypatch.setenv("MORALSTACK_OBSERVABILITY_MODE", "file_only")
        with patch("moralstack.observability.router._get_jsonl_sink", return_value=jsonl_mock):
            result = route_window(envs, None)
        assert result.written == 0
        assert result.failed == 1
        assert result.jsonl_failed == 1

    def test_route_audit_sync_per_mode(self, tmp_path, monkeypatch):
        from moralstack.observability.sinks.jsonl_sink import JsonlWindowResult
        from moralstack.observability.sinks.sqlite_sink import create_run, init_db, upsert_request

        dbp = str(tmp_path / "audit-router-per-mode.db")
        monkeypatch.setenv("MORALSTACK_OBSERVABILITY_DB_PATH", dbp)
        monkeypatch.setenv("MORALSTACK_DB_PATH", dbp)
        assert init_db(dbp)
        assert create_run("r-mode", "test", {})
        assert upsert_request("r-mode", "q-mode", "prompt")

        monkeypatch.setenv("MORALSTACK_OBSERVABILITY_MODE", "db_only")
        jsonl_mock = MagicMock()
        with patch("moralstack.observability.router._get_jsonl_sink", return_value=jsonl_mock):
            db_result = route_audit_sync([_env(run_id="r-mode", request_id="q-mode")])
        assert db_result.written == 1
        assert db_result.failed == 0
        assert db_result.sqlite_written == 1
        assert db_result.jsonl_written == 0
        jsonl_mock.write_window.assert_not_called()

        monkeypatch.setenv("MORALSTACK_OBSERVABILITY_MODE", "file_only")
        monkeypatch.delenv("MORALSTACK_OBSERVABILITY_DB_PATH", raising=False)
        monkeypatch.delenv("MORALSTACK_DB_PATH", raising=False)
        jsonl_mock = MagicMock()
        jsonl_mock.write_window.return_value = JsonlWindowResult(written=1)
        with patch("moralstack.observability.router._get_jsonl_sink", return_value=jsonl_mock):
            file_result = route_audit_sync([_env(run_id="r-file", request_id="q-file")])
        assert file_result.written == 1
        assert file_result.failed == 0
        assert file_result.sqlite_written == 0
        assert file_result.jsonl_written == 1

        monkeypatch.setenv("MORALSTACK_OBSERVABILITY_MODE", "dual")
        monkeypatch.setenv("MORALSTACK_OBSERVABILITY_DB_PATH", dbp)
        jsonl_mock = MagicMock()
        jsonl_mock.write_window.return_value = JsonlWindowResult(written=1)
        with patch("moralstack.observability.router._get_jsonl_sink", return_value=jsonl_mock):
            dual_result = route_audit_sync([_env(run_id="r-mode", request_id="q-mode")])
        assert dual_result.written == 1
        assert dual_result.failed == 0
        assert dual_result.sqlite_written == 1
        assert dual_result.jsonl_written == 1

    def test_jsonl_window_result_accounting(self, tmp_path, monkeypatch):
        from moralstack.observability.sinks.jsonl_sink import JsonlEventSink

        sink = JsonlEventSink(str(tmp_path / "jsonl"))
        original_write_line = sink._write_line

        def fail_second(envelope):
            if envelope.request_id == "bad":
                raise OSError("jsonl boom")
            original_write_line(envelope)

        monkeypatch.setattr(sink, "_write_line", fail_second)
        result = sink.write_window([_env(request_id="ok"), _env(request_id="bad")])

        assert result.written == 1
        assert result.failed == 1
        assert result.error == "jsonl boom"

    def test_finalization_failure_counted_not_raised(self, tmp_path, monkeypatch, caplog):
        from moralstack.observability import router as router_module
        from moralstack.observability import service as service_module
        from moralstack.observability.router import WindowResult
        from moralstack.observability.service import get_obs
        from moralstack.observability.sinks.jsonl_sink import JsonlWindowResult
        from moralstack.observability.sinks.sqlite_sink import create_run, init_db, upsert_request

        try:
            get_obs().shutdown(timeout=1.0)
        except Exception:
            pass
        service_module._obs_instance = None
        router_module._sqlite_sink = None
        router_module._jsonl_sink = None

        dbp = str(tmp_path / "audit-router-failures.db")
        monkeypatch.setenv("MORALSTACK_OBSERVABILITY_MODE", "db_only")
        monkeypatch.setenv("MORALSTACK_OBSERVABILITY_DB_PATH", dbp)
        assert init_db(dbp)
        assert create_run("r-fail", "test", {})
        assert upsert_request("r-fail", "q-fail", "prompt")
        sqlite_mock = MagicMock()
        sqlite_mock.write_window.return_value = WindowResult(failed=1, sqlite_failed=1, error="sqlite fail")
        with patch("moralstack.observability.router._get_sqlite_sink", return_value=sqlite_mock):
            sqlite_result = route_audit_sync([_env(run_id="r-fail", request_id="q-fail")])
        assert sqlite_result.failed == 1
        assert get_obs().stats()["finalize_failed"] == 1

        monkeypatch.setenv("MORALSTACK_OBSERVABILITY_MODE", "file_only")
        monkeypatch.delenv("MORALSTACK_OBSERVABILITY_DB_PATH", raising=False)
        monkeypatch.delenv("MORALSTACK_DB_PATH", raising=False)
        jsonl_mock = MagicMock()
        jsonl_mock.write_window.return_value = JsonlWindowResult(failed=1, error="jsonl fail")
        with patch("moralstack.observability.router._get_jsonl_sink", return_value=jsonl_mock):
            jsonl_result = route_audit_sync([_env(run_id="r-jsonl-fail", request_id="q-jsonl-fail")])
        assert jsonl_result.failed == 1
        assert get_obs().stats()["finalize_failed"] == 2
        assert "route_audit_sync counted failure" in caplog.text

    def test_route_audit_sync_dual_jsonl_failure_does_not_flip_persisted(self, tmp_path, monkeypatch):
        from moralstack.observability.sinks.jsonl_sink import JsonlWindowResult
        from moralstack.observability.sinks.sqlite_sink import create_run, init_db, upsert_request

        dbp = str(tmp_path / "audit-router.db")
        monkeypatch.setenv("MORALSTACK_OBSERVABILITY_MODE", "dual")
        monkeypatch.setenv("MORALSTACK_OBSERVABILITY_DB_PATH", dbp)
        assert init_db(dbp)
        assert create_run("r1", "test", {})
        assert upsert_request("r1", "q1", "prompt")
        jsonl_mock = MagicMock()
        jsonl_mock.write_window.return_value = JsonlWindowResult(written=0, failed=1, error="jsonl fail")
        with patch("moralstack.observability.router._get_jsonl_sink", return_value=jsonl_mock):
            result = route_audit_sync([_env(run_id="r1", request_id="q1")])
        assert result.written == 1
        assert result.failed == 0
        assert result.sqlite_written == 1
        assert result.jsonl_failed == 1
