"""Characterization tests for persist_* / async_persist_* error contracts."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

import moralstack.observability.context as obs_context
from moralstack.observability import emit_helpers as sync_sink


@pytest.fixture(autouse=True)
def _reset_uow_warned():
    sync_sink._uow_warned = False
    obs_context._run_id.set(None)
    obs_context._request_id.set(None)
    yield
    sync_sink._uow_warned = False
    obs_context._run_id.set(None)
    obs_context._request_id.set(None)


def test_persist_llm_call_missing_context_returns_false_without_emit():
    with patch("moralstack.observability.service.get_obs") as get_obs:
        assert sync_sink.persist_llm_call(phase="p", module="m", action="a") is False
        get_obs.assert_not_called()


def test_persist_orchestration_event_returns_none_on_success():
    obs_context.set_current_run_id("r1")
    obs_context.set_current_request_id("req1")
    with patch("moralstack.observability.service.get_obs") as get_obs:
        get_obs.return_value = MagicMock()
        assert (
            sync_sink.persist_orchestration_event(
                stage="s",
                component="c",
                event_type="E",
            )
            is None
        )


def test_uow_parameter_logs_warning_once(caplog):
    obs_context.set_current_run_id("r1")
    obs_context.set_current_request_id("req1")
    with patch("moralstack.observability.service.get_obs") as get_obs:
        get_obs.return_value = MagicMock()
        sync_sink.persist_llm_call(phase="p", module="m", action="a", uow=object())
        sync_sink.persist_llm_call(phase="p", module="m", action="a", uow=object())
    assert caplog.text.count("uow= parameter is deprecated") == 1


def test_sync_emit_failure_returns_false_and_logs(caplog):
    obs_context.set_current_run_id("r1")
    obs_context.set_current_request_id("req1")
    with patch("moralstack.observability.service.get_obs") as get_obs:
        get_obs.return_value.emit.side_effect = RuntimeError("emit failed")
        assert sync_sink.persist_llm_call(phase="p", module="m", action="a") is False
    assert "persist_llm_call failed" in caplog.text


def test_empty_batch_returns_true_without_emit():
    with patch("moralstack.observability.service.get_obs") as get_obs:
        assert sync_sink.persist_llm_calls_batch([]) is True
        assert sync_sink.persist_orchestration_events_batch([]) is True
        get_obs.assert_not_called()


def test_async_persist_propagates_emit_exception():
    obs_context.set_current_run_id("r1")
    obs_context.set_current_request_id("req1")
    with patch("moralstack.observability.emit_helpers.get_obs") as get_obs:
        get_obs.return_value.emit.side_effect = RuntimeError("async emit failed")
        with pytest.raises(RuntimeError, match="async emit failed"):
            sync_sink.async_persist_llm_call(phase="p", module="m", action="a")


def test_async_persist_missing_context_is_noop():
    with patch("moralstack.observability.emit_helpers.get_obs") as get_obs:
        sync_sink.async_persist_llm_call(phase="p", module="m", action="a")
        get_obs.assert_not_called()
