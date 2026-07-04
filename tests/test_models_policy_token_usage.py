"""Tests for OpenAIPolicy token usage source propagation."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

from moralstack.models.policy import OpenAIPolicy


def _policy_with_response(usage: object | None) -> OpenAIPolicy:
    policy = OpenAIPolicy(api_key="sk-test", model="gpt-4o")
    choice = SimpleNamespace(message=SimpleNamespace(content="hello"), finish_reason="stop")
    response = SimpleNamespace(choices=[choice], usage=usage)
    policy.client = MagicMock()
    policy.client.chat.completions.create.return_value = response
    return policy


def test_complete_returns_exact_source_when_usage_has_split():
    usage = SimpleNamespace(prompt_tokens=70, completion_tokens=30, total_tokens=100)
    policy = _policy_with_response(usage)
    _, _, _, pt, ct, source = policy._complete([{"role": "user", "content": "hi"}])
    assert source == "exact"
    assert pt == 70
    assert ct == 30


def test_complete_returns_estimated_source_on_70_30_fallback():
    usage = SimpleNamespace(prompt_tokens=None, completion_tokens=None, total_tokens=100)
    policy = _policy_with_response(usage)
    _, tokens, _, pt, ct, source = policy._complete([{"role": "user", "content": "hi"}])
    assert source == "estimated"
    assert tokens == 100
    assert pt == 70
    assert ct == 30


def test_complete_returns_missing_source_when_usage_is_none():
    policy = _policy_with_response(None)
    _, tokens, _, pt, ct, source = policy._complete([{"role": "user", "content": "hi"}])
    assert source == "missing"
    assert tokens == 0
    assert pt == 0
    assert ct == 0


def test_complete_6_tuple_call_sites_updated():
    usage = SimpleNamespace(prompt_tokens=10, completion_tokens=5, total_tokens=15)
    policy = _policy_with_response(usage)

    gen = policy.generate("hello")
    assert gen.token_usage_source == "exact"

    msgs = policy.generate_messages([{"role": "user", "content": "hello"}])
    assert msgs.token_usage_source == "exact"
