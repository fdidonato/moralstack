"""Regression: parallel domain agents must inherit the observability context.

Without contextvar propagation the per-domain ``enhanced_domain_agent`` /
``legacy_domain_agent`` llm_calls persist with an empty run_id/request_id
(orphaned rows), so their tokens are never attributed to the request and they
never appear in the request-detail UI. These tests lock the propagation done by
``_run_enhanced_agents_parallel`` / ``_run_agents_parallel``.
"""

from __future__ import annotations

import contextvars
import types

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
