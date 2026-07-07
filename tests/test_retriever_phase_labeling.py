"""
[v4.1 blocker-2 fix] `retrieval_phase` must be threaded through the enhanced AND
legacy domain-agent `_call_openai` calls (and their persistence), not just the
domain-prefilter's own `_call_openai` (which already threaded it). Without this,
every domain-agent `llm_calls` row persists under the default phase regardless of
which wave (risk-owned vs deliberation-fallback) triggered it, making the two
waves indistinguishable in observability.

Offline/deterministic: the OpenAI client is faked (no network); only
`_persist_constitution_llm_call`'s ``retrieval_phase`` argument is asserted.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

from moralstack.constitution.openai_config import OpenAIClientConfig
from moralstack.constitution.retriever import (
    RETRIEVAL_PHASE_DELIBERATION,
    RETRIEVAL_PHASE_RISK_ROUTING,
    DomainAgent,
    EnhancedDomainAgent,
)
from moralstack.constitution.schema import Principle


def _principle(pid: str, level: str = "hard") -> Principle:
    return Principle(id=pid, title="t", rule="r", level=level, domain="test", priority=1)


class _FakeCompletions:
    def __init__(self, content: str) -> None:
        self._content = content

    def create(self, **kwargs: Any) -> Any:
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=self._content))],
            usage=None,
        )


class _FakeChat:
    def __init__(self, content: str) -> None:
        self.completions = _FakeCompletions(content)


class _FakeOpenAIClient:
    def __init__(self, content: str) -> None:
        self.chat = _FakeChat(content)


def _fake_openai_ctor(content: str):
    def _ctor(api_key: str | None = None) -> _FakeOpenAIClient:
        return _FakeOpenAIClient(content)

    return _ctor


def _config() -> OpenAIClientConfig:
    return OpenAIClientConfig(api_key="test-key", model="gpt-4o-mini")


def test_enhanced_agent_labels_risk_routing_by_default():
    agent = EnhancedDomainAgent(domain_name="test", principles=[_principle("P1")], openai_config=_config())
    content = '{"domain_match": true, "confidence": 0.9, "principle_ids": ["P1"], "reasoning": "ok"}'
    calls: list[dict[str, Any]] = []

    def _capture(**kwargs: Any) -> None:
        calls.append(kwargs)

    with (
        patch("openai.OpenAI", _fake_openai_ctor(content)),
        patch("moralstack.constitution.retriever._persist_constitution_llm_call", side_effect=_capture),
    ):
        agent.evaluate("query")

    assert len(calls) == 1
    assert calls[0]["retrieval_phase"] == RETRIEVAL_PHASE_RISK_ROUTING == "risk_routing"


def test_enhanced_agent_labels_deliberation_retrieval_when_specified():
    agent = EnhancedDomainAgent(domain_name="test", principles=[_principle("P1")], openai_config=_config())
    content = '{"domain_match": true, "confidence": 0.9, "principle_ids": ["P1"], "reasoning": "ok"}'
    calls: list[dict[str, Any]] = []

    def _capture(**kwargs: Any) -> None:
        calls.append(kwargs)

    with (
        patch("openai.OpenAI", _fake_openai_ctor(content)),
        patch("moralstack.constitution.retriever._persist_constitution_llm_call", side_effect=_capture),
    ):
        agent.evaluate("query", retrieval_phase=RETRIEVAL_PHASE_DELIBERATION)

    assert len(calls) == 1
    assert calls[0]["retrieval_phase"] == RETRIEVAL_PHASE_DELIBERATION == "deliberation_retrieval"


def test_legacy_agent_labels_risk_routing_by_default():
    agent = DomainAgent(domain_name="test", principles=[_principle("P1")], openai_config=_config())
    content = '{"principle_ids": ["P1"]}'
    calls: list[dict[str, Any]] = []

    def _capture(**kwargs: Any) -> None:
        calls.append(kwargs)

    with (
        patch("openai.OpenAI", _fake_openai_ctor(content)),
        patch("moralstack.constitution.retriever._persist_constitution_llm_call", side_effect=_capture),
    ):
        agent.evaluate("query")

    assert len(calls) == 1
    assert calls[0]["retrieval_phase"] == RETRIEVAL_PHASE_RISK_ROUTING


def test_legacy_agent_labels_deliberation_retrieval_when_specified():
    agent = DomainAgent(domain_name="test", principles=[_principle("P1")], openai_config=_config())
    content = '{"principle_ids": ["P1"]}'
    calls: list[dict[str, Any]] = []

    def _capture(**kwargs: Any) -> None:
        calls.append(kwargs)

    with (
        patch("openai.OpenAI", _fake_openai_ctor(content)),
        patch("moralstack.constitution.retriever._persist_constitution_llm_call", side_effect=_capture),
    ):
        agent.evaluate("query", retrieval_phase=RETRIEVAL_PHASE_DELIBERATION)

    assert len(calls) == 1
    assert calls[0]["retrieval_phase"] == RETRIEVAL_PHASE_DELIBERATION
