"""Tests for per-request token usage accumulator."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

import moralstack.observability.request_token_accumulator as acc
from moralstack.observability.request_token_accumulator import (
    RequestTokenTotals,
    finalize_and_persist,
    pop_request_token_usage,
    record_llm_call_usage,
)


def _usage_json(prompt: int, completion: int, total: int, source: str = "exact") -> str:
    import json

    return json.dumps(
        {
            "prompt_tokens": prompt,
            "completion_tokens": completion,
            "total_tokens": total,
            "source": source,
        }
    )


def setup_function() -> None:
    with acc._lock:
        acc._store.clear()


def test_record_llm_call_usage_accumulates_totals():
    record_llm_call_usage("run-1", "req-1", _usage_json(10, 5, 15))
    record_llm_call_usage("run-1", "req-1", _usage_json(20, 10, 30))
    totals = pop_request_token_usage("run-1", "req-1")
    assert totals is not None
    assert totals.input_tokens == 30
    assert totals.output_tokens == 15
    assert totals.total_tokens == 45
    assert totals.llm_call_count == 2


def test_record_llm_call_usage_counts_missing_and_estimated_separately():
    record_llm_call_usage("run-1", "req-1", None)
    record_llm_call_usage("run-1", "req-1", _usage_json(7, 3, 10, "estimated"))
    totals = pop_request_token_usage("run-1", "req-1")
    assert totals is not None
    assert totals.missing_usage_count == 1
    assert totals.estimated_usage_count == 1


def test_pop_request_token_usage_removes_entry():
    record_llm_call_usage("run-1", "req-1", _usage_json(1, 1, 2))
    first = pop_request_token_usage("run-1", "req-1")
    second = pop_request_token_usage("run-1", "req-1")
    assert first is not None
    assert second is None


def test_different_request_ids_do_not_mix():
    record_llm_call_usage("run-1", "req-a", _usage_json(10, 0, 10))
    record_llm_call_usage("run-1", "req-b", _usage_json(5, 0, 5))
    a = pop_request_token_usage("run-1", "req-a")
    b = pop_request_token_usage("run-1", "req-b")
    assert a is not None and a.total_tokens == 10
    assert b is not None and b.total_tokens == 5


def test_concurrent_requests_isolated_under_thread_pool():
    def worker(i: int) -> None:
        rid = f"req-{i}"
        for _ in range(10):
            record_llm_call_usage("run-1", rid, _usage_json(1, 1, 2))

    with ThreadPoolExecutor(max_workers=3) as pool:
        list(pool.map(worker, range(50)))

    for i in range(50):
        totals = pop_request_token_usage("run-1", f"req-{i}")
        assert totals is not None
        assert totals.llm_call_count == 10
        assert totals.total_tokens == 20


def test_fifo_cap_evicts_oldest_entry_deterministically(monkeypatch):
    monkeypatch.setattr(acc, "_max_entries", 3)
    record_llm_call_usage("run-1", "req-1", _usage_json(1, 0, 1))
    record_llm_call_usage("run-1", "req-2", _usage_json(2, 0, 2))
    record_llm_call_usage("run-1", "req-3", _usage_json(3, 0, 3))
    record_llm_call_usage("run-1", "req-4", _usage_json(4, 0, 4))
    assert pop_request_token_usage("run-1", "req-1") is None
    t4 = pop_request_token_usage("run-1", "req-4")
    assert t4 is not None and t4.total_tokens == 4


def test_finalize_and_persist_never_raises_on_persistence_failure():
    record_llm_call_usage("run-1", "req-1", _usage_json(1, 1, 2))
    with patch("moralstack.observability.service.ObservabilityService.emit", side_effect=RuntimeError("boom")):
        result = finalize_and_persist("run-1", "req-1")
    assert result is None


def test_finalize_and_persist_emits_event_with_correct_payload():
    record_llm_call_usage("run-1", "req-1", _usage_json(10, 5, 15))
    with patch("moralstack.observability.service.ObservabilityService.emit") as mock_emit:
        totals = finalize_and_persist("run-1", "req-1")
    assert totals is not None
    assert totals.total_tokens == 15
    mock_emit.assert_called_once()
    env = mock_emit.call_args[0][0]
    assert env.event_type == "request.token_usage_finalized"
    assert env.payload["total_tokens"] == 15
    assert env.payload["llm_call_count"] == 1


def test_finalize_and_persist_pops_even_if_no_calls_recorded():
    with patch("moralstack.observability.service.ObservabilityService.emit") as mock_emit:
        totals = finalize_and_persist("run-1", "req-empty")
    assert totals == RequestTokenTotals()
    mock_emit.assert_called_once()
    assert pop_request_token_usage("run-1", "req-empty") is None
