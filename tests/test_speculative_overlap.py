"""Unit tests for lazy speculative overlap handle (observability + deferred llm_call persist)."""

from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor

import pytest

from moralstack.orchestration.orchestration_event_taxonomy import (
    SPECULATIVE_JOIN_REQUIRED,
    SPECULATIVE_JOIN_SKIPPED,
    SPECULATIVE_RESULT_DISCARDED,
    SPECULATIVE_RESULT_USED,
)
from moralstack.orchestration.speculative_overlap import SpeculativeOverlapHandle


@pytest.fixture
def executor() -> ThreadPoolExecutor:
    ex = ThreadPoolExecutor(max_workers=1)
    yield ex
    ex.shutdown(wait=False)


def test_join_for_consumer_emits_events_and_persists_used(
    monkeypatch: pytest.MonkeyPatch,
    executor: ThreadPoolExecutor,
) -> None:
    events: list[dict] = []
    llm_calls: list[dict] = []

    monkeypatch.setattr(
        "moralstack.orchestration.speculative_overlap.persist_orchestration_event",
        lambda **kw: events.append(kw),
    )
    monkeypatch.setattr(
        "moralstack.orchestration.speculative_overlap.async_persist_llm_call",
        lambda **kw: llm_calls.append(kw),
    )

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
    )
    assert h.join_for_consumer("benign", "benign_fast_path") == "draft"
    assert any(e.get("event_type") == SPECULATIVE_JOIN_REQUIRED for e in events)
    assert any(e.get("event_type") == SPECULATIVE_RESULT_USED for e in events)
    assert not any(e.get("event_type") == SPECULATIVE_RESULT_DISCARDED for e in events)
    assert llm_calls and llm_calls[0].get("call_outcome") == "used"
    h.shutdown_executor()


def test_join_for_consumer_exception_emits_discarded(monkeypatch: pytest.MonkeyPatch, executor: ThreadPoolExecutor) -> None:
    events: list[dict] = []

    monkeypatch.setattr(
        "moralstack.orchestration.speculative_overlap.persist_orchestration_event",
        lambda **kw: events.append(kw),
    )
    monkeypatch.setattr(
        "moralstack.orchestration.speculative_overlap.async_persist_llm_call",
        lambda **kw: None,
    )

    fut: Future[tuple[str | None, dict | None]] = Future()
    fut.set_exception(RuntimeError("boom"))

    h = SpeculativeOverlapHandle(
        risk_estimation=object(),
        spec_future=fut,
        executor=executor,
        spec_started_at_ms=0,
    )
    assert h.join_for_consumer("fast_path", "run_fast_path") is None
    assert any(e.get("event_type") == SPECULATIVE_RESULT_DISCARDED for e in events)
    assert any(
        (e.get("payload") or {}).get("reason") == "speculative_failed"
        for e in events
        if e.get("event_type") == SPECULATIVE_RESULT_DISCARDED
    )
    h.shutdown_executor()


def test_abandon_skips_join_and_emits_events(monkeypatch: pytest.MonkeyPatch, executor: ThreadPoolExecutor) -> None:
    events: list[dict] = []

    monkeypatch.setattr(
        "moralstack.orchestration.speculative_overlap.persist_orchestration_event",
        lambda **kw: events.append(kw),
    )
    monkeypatch.setattr(
        "moralstack.orchestration.speculative_overlap.async_persist_llm_call",
        lambda **kw: None,
    )

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
    )
    h.abandon("refuse_path", "refuse")
    assert any(e.get("event_type") == SPECULATIVE_JOIN_SKIPPED for e in events)
    assert any(e.get("event_type") == SPECULATIVE_RESULT_DISCARDED for e in events)
    h.shutdown_executor()


def test_join_for_consumer_idempotent_second_call_returns_none(
    monkeypatch: pytest.MonkeyPatch,
    executor: ThreadPoolExecutor,
) -> None:
    monkeypatch.setattr(
        "moralstack.orchestration.speculative_overlap.persist_orchestration_event",
        lambda **kw: None,
    )
    monkeypatch.setattr(
        "moralstack.orchestration.speculative_overlap.async_persist_llm_call",
        lambda **kw: None,
    )

    fut: Future[tuple[str | None, dict | None]] = Future()
    fut.set_result(("once", None))

    h = SpeculativeOverlapHandle(
        risk_estimation=object(),
        spec_future=fut,
        executor=executor,
        spec_started_at_ms=0,
    )
    assert h.join_for_consumer("benign", "x") == "once"
    assert h.join_for_consumer("benign", "x") is None
    h.shutdown_executor()
