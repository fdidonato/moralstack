"""Regression: parallel domain agents must inherit the observability context.

Without contextvar propagation the per-domain ``enhanced_domain_agent`` /
``legacy_domain_agent`` llm_calls persist with an empty run_id/request_id
(orphaned rows), so their tokens are never attributed to the request and they
never appear in the request-detail UI. These tests lock the propagation done by
``_run_enhanced_agents_parallel`` / ``_run_agents_parallel``.

Also locks batching (max_parallel_agents default now 4, see
ai/plans/optimize-domain-prefilter-caching-and-parallelism.md Intervention 2):
with N agents, ceil(N / max_parallel_agents) ThreadPoolExecutor batches must run.
"""

from __future__ import annotations

import concurrent.futures
import contextvars
import types
from unittest.mock import patch

from moralstack.constitution.retriever import AgentResult, ConstitutionRetriever
from moralstack.observability.context import (
    get_current_request_id,
    get_current_run_id,
    set_current_request_id,
    set_current_run_id,
)


class _FakeEnhancedAgent:
    def __init__(self, name: str, sink: dict[str, tuple]) -> None:
        self.domain_name = name
        self._sink = sink

    def evaluate(self, query: str, *, retrieval_phase: str = "risk_routing") -> AgentResult:
        # Captured from inside the worker thread.
        self._sink[self.domain_name] = (get_current_run_id(), get_current_request_id())
        return AgentResult(principle_ids=[], confidence=1.0, domain_match=True)


class _FakeLegacyAgent:
    def __init__(self, name: str, sink: dict[str, tuple]) -> None:
        self.domain_name = name
        self._sink = sink

    def evaluate(self, query: str, *, retrieval_phase: str = "risk_routing") -> list[str]:
        self._sink[self.domain_name] = (get_current_run_id(), get_current_request_id())
        return []


def _fake_retriever(max_parallel: int = 4) -> types.SimpleNamespace:
    return types.SimpleNamespace(_config=types.SimpleNamespace(max_parallel_agents=max_parallel))


def test_enhanced_agents_inherit_observability_context():
    def body() -> None:
        set_current_run_id("RUN-1")
        set_current_request_id("REQ-1")
        sink: dict[str, tuple] = {}
        agents = [_FakeEnhancedAgent(f"d{i}", sink) for i in range(3)]
        ConstitutionRetriever._run_enhanced_agents_parallel(_fake_retriever(), agents, "q")  # type: ignore[arg-type]
        assert len(sink) == 3
        assert all(seen == ("RUN-1", "REQ-1") for seen in sink.values()), sink

    # Isolate contextvar mutations to an ephemeral context so nothing leaks.
    contextvars.copy_context().run(body)


def test_legacy_agents_inherit_observability_context():
    def body() -> None:
        set_current_run_id("RUN-2")
        set_current_request_id("REQ-2")
        sink: dict[str, tuple] = {}
        agents = [_FakeLegacyAgent(f"d{i}", sink) for i in range(3)]
        ConstitutionRetriever._run_agents_parallel(_fake_retriever(), agents, "q")  # type: ignore[arg-type]
        assert len(sink) == 3
        assert all(seen == ("RUN-2", "REQ-2") for seen in sink.values()), sink

    contextvars.copy_context().run(body)


class _CountingThreadPoolExecutor(concurrent.futures.ThreadPoolExecutor):
    """Counts constructions without mocking away real execution (contextvars
    propagation still runs through genuine worker threads)."""

    construction_count = 0

    def __init__(self, *args, **kwargs) -> None:
        type(self).construction_count += 1
        super().__init__(*args, **kwargs)


def _run_enhanced_with_counting_executor(agents: list, max_parallel: int) -> int:
    _CountingThreadPoolExecutor.construction_count = 0
    with patch("moralstack.constitution.retriever.concurrent.futures.ThreadPoolExecutor", _CountingThreadPoolExecutor):
        ConstitutionRetriever._run_enhanced_agents_parallel(_fake_retriever(max_parallel), agents, "q")  # type: ignore[arg-type]
    return _CountingThreadPoolExecutor.construction_count


def _run_legacy_with_counting_executor(agents: list, max_parallel: int) -> int:
    _CountingThreadPoolExecutor.construction_count = 0
    with patch("moralstack.constitution.retriever.concurrent.futures.ThreadPoolExecutor", _CountingThreadPoolExecutor):
        ConstitutionRetriever._run_agents_parallel(_fake_retriever(max_parallel), agents, "q")  # type: ignore[arg-type]
    return _CountingThreadPoolExecutor.construction_count


def test_four_agents_run_in_single_threadpool_batch_at_max_parallel_4():
    def body() -> None:
        sink: dict[str, tuple] = {}
        agents = [_FakeEnhancedAgent(f"d{i}", sink) for i in range(4)]
        count = _run_enhanced_with_counting_executor(agents, max_parallel=4)
        assert count == 1
        assert len(sink) == 4

    contextvars.copy_context().run(body)


def test_four_agents_run_in_single_threadpool_batch_at_max_parallel_4_legacy():
    def body() -> None:
        sink: dict[str, tuple] = {}
        agents = [_FakeLegacyAgent(f"d{i}", sink) for i in range(4)]
        count = _run_legacy_with_counting_executor(agents, max_parallel=4)
        assert count == 1
        assert len(sink) == 4

    contextvars.copy_context().run(body)


def test_regression_two_agents_batch_size_2_still_needs_two_batches():
    """Negative control: this is the test that would catch a rejected
    'bump only retriever.py:1088' partial fix (Option A) — with batch_size=2
    and 4 agents, TWO ThreadPoolExecutor batches must still run."""

    def body() -> None:
        sink: dict[str, tuple] = {}
        agents = [_FakeEnhancedAgent(f"d{i}", sink) for i in range(4)]
        count = _run_enhanced_with_counting_executor(agents, max_parallel=2)
        assert count == 2
        assert len(sink) == 4

    contextvars.copy_context().run(body)


def test_batch_count_1_1_2_for_1_4_5_agents():
    """Edge cases at max_parallel_agents=4: 1 agent -> 1 batch, 4 -> 1 batch,
    5 -> 2 batches. Covers the wider blast radius (no-prefilter/legacy paths
    where the wave can include core + every overlay, not just core + 3)."""

    def body() -> None:
        for n_agents, expected_batches in ((1, 1), (4, 1), (5, 2)):
            sink: dict[str, tuple] = {}
            agents = [_FakeEnhancedAgent(f"d{i}", sink) for i in range(n_agents)]
            count = _run_enhanced_with_counting_executor(agents, max_parallel=4)
            assert count == expected_batches, f"{n_agents} agents: expected {expected_batches}, got {count}"
            assert len(sink) == n_agents

    contextvars.copy_context().run(body)
