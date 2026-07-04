"""Unit tests for lazy speculative overlap handle (observability + deferred llm_call persist)."""

from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
from typing import Any

import pytest

from moralstack.orchestration.orchestration_event_taxonomy import (
    SPECULATIVE_JOIN_REQUIRED,
    SPECULATIVE_JOIN_SKIPPED,
    SPECULATIVE_RESULT_DISCARDED,
    SPECULATIVE_RESULT_USED,
)
from moralstack.orchestration.speculative_overlap import SpeculativeOverlapHandle


class _RecordingEmitter:
    """Captures orchestration events and LLM call payloads for assertions."""

    def __init__(self) -> None:
        self.orchestration_events: list[dict[str, Any]] = []
        self.llm_calls: list[dict[str, Any]] = []

    def emit_orchestration_event(self, **kwargs: Any) -> None:
        self.orchestration_events.append(dict(kwargs))

    def emit_llm_call(self, **kwargs: Any) -> None:
        self.llm_calls.append(dict(kwargs))

    def emit_decision_trace(self, **kwargs: Any) -> None:
        return None


@pytest.fixture
def executor() -> ThreadPoolExecutor:
    ex = ThreadPoolExecutor(max_workers=1)
    yield ex
    ex.shutdown(wait=False)


def test_join_for_consumer_emits_events_and_persists_used(
    executor: ThreadPoolExecutor,
) -> None:
    emitter = _RecordingEmitter()

    fut: Future[tuple[str | None, dict | None]] = Future()
    fut.set_result(
        (
            "draft",
            {
                "phase": "speculative_generate",
                "module": "policy",
                "call_kind": "speculative",
            },
        )
    )

    h = SpeculativeOverlapHandle(
        risk_estimation=object(),
        spec_future=fut,
        executor=executor,
        spec_started_at_ms=0,
        event_emitter=emitter,
    )
    assert h.join_for_consumer("benign", "benign_fast_path") == "draft"
    events = emitter.orchestration_events
    assert any(e.get("event_type") == SPECULATIVE_JOIN_REQUIRED for e in events)
    assert any(e.get("event_type") == SPECULATIVE_RESULT_USED for e in events)
    assert not any(e.get("event_type") == SPECULATIVE_RESULT_DISCARDED for e in events)
    assert emitter.llm_calls and emitter.llm_calls[0].get("call_outcome") == "used"
    h.shutdown_executor()


def test_join_for_consumer_exception_emits_discarded(executor: ThreadPoolExecutor) -> None:
    emitter = _RecordingEmitter()

    fut: Future[tuple[str | None, dict | None]] = Future()
    fut.set_exception(RuntimeError("boom"))

    h = SpeculativeOverlapHandle(
        risk_estimation=object(),
        spec_future=fut,
        executor=executor,
        spec_started_at_ms=0,
        event_emitter=emitter,
    )
    assert h.join_for_consumer("fast_path", "run_fast_path") is None
    events = emitter.orchestration_events
    assert any(e.get("event_type") == SPECULATIVE_RESULT_DISCARDED for e in events)
    assert any(
        (e.get("payload") or {}).get("reason") == "speculative_failed"
        for e in events
        if e.get("event_type") == SPECULATIVE_RESULT_DISCARDED
    )
    h.shutdown_executor()


def test_abandon_skips_join_and_emits_events(executor: ThreadPoolExecutor) -> None:
    emitter = _RecordingEmitter()

    fut: Future[tuple[str | None, dict | None]] = Future()
    fut.set_result(
        (
            "waste",
            {"phase": "speculative_generate", "call_kind": "speculative"},
        )
    )

    h = SpeculativeOverlapHandle(
        risk_estimation=object(),
        spec_future=fut,
        executor=executor,
        spec_started_at_ms=0,
        event_emitter=emitter,
    )
    h.abandon("refuse_path", "refuse")
    events = emitter.orchestration_events
    assert any(e.get("event_type") == SPECULATIVE_JOIN_SKIPPED for e in events)
    assert any(e.get("event_type") == SPECULATIVE_RESULT_DISCARDED for e in events)
    h.shutdown_executor()


def test_abandon_persists_run_id_request_id_from_captured_context(executor: ThreadPoolExecutor) -> None:
    """abandon() must inject run/request context captured in the calling thread."""
    from moralstack.observability.context import (
        set_current_cycle,
        set_current_request_id,
        set_current_run_id,
        set_current_session_id,
        set_current_turn_number,
    )

    emitter = _RecordingEmitter()
    fut: Future[tuple[str | None, dict | None]] = Future()
    fut.set_result(
        (
            "waste",
            {
                "phase": "speculative_generate",
                "call_kind": "speculative",
                "token_usage_json": '{"total_tokens": 5, "source": "exact"}',
            },
        )
    )

    set_current_run_id("run-abandon")
    set_current_request_id("req-abandon")
    set_current_session_id("sess-1")
    set_current_turn_number(2)
    set_current_cycle(0)
    try:
        h = SpeculativeOverlapHandle(
            risk_estimation=object(),
            spec_future=fut,
            executor=executor,
            spec_started_at_ms=0,
            event_emitter=emitter,
        )
        h.abandon("refuse_path", "refuse")
        import time

        deadline = time.time() + 2.0
        while not emitter.llm_calls and time.time() < deadline:
            time.sleep(0.01)
    finally:
        pass

    assert emitter.llm_calls, "discarded speculative llm_call should be persisted"
    row = emitter.llm_calls[0]
    assert row.get("run_id") == "run-abandon"
    assert row.get("request_id") == "req-abandon"
    assert row.get("call_outcome") == "discarded"
    h.shutdown_executor()


def test_join_for_consumer_idempotent_second_call_returns_none(
    executor: ThreadPoolExecutor,
) -> None:
    emitter = _RecordingEmitter()

    fut: Future[tuple[str | None, dict | None]] = Future()
    fut.set_result(("once", None))

    h = SpeculativeOverlapHandle(
        risk_estimation=object(),
        spec_future=fut,
        executor=executor,
        spec_started_at_ms=0,
        event_emitter=emitter,
    )
    assert h.join_for_consumer("benign", "x") == "once"
    assert h.join_for_consumer("benign", "x") is None
    h.shutdown_executor()
