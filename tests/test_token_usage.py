"""Unit tests for moralstack.observability.token_usage."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from moralstack.observability.token_usage import TokenUsage


def test_from_openai_usage_exact_when_split_present():
    usage = SimpleNamespace(prompt_tokens=70, completion_tokens=30, total_tokens=100)
    result = TokenUsage.from_openai_usage(usage)
    assert result.input_tokens == 70
    assert result.output_tokens == 30
    assert result.total_tokens == 100
    assert result.source == "exact"


def test_from_openai_usage_estimated_when_split_missing_but_total_present():
    usage = SimpleNamespace(prompt_tokens=None, completion_tokens=None, total_tokens=100)
    result = TokenUsage.from_openai_usage(usage)
    assert result.total_tokens == 100
    assert result.input_tokens == 70
    assert result.output_tokens == 30
    assert result.source == "estimated"


def test_from_openai_usage_missing_when_usage_is_none():
    result = TokenUsage.from_openai_usage(None)
    assert result == TokenUsage(0, 0, 0, "missing")


def test_from_openai_usage_embedding_forces_output_zero():
    usage = SimpleNamespace(prompt_tokens=42, completion_tokens=99, total_tokens=42)
    result = TokenUsage.from_openai_usage(usage, is_embedding=True)
    assert result.output_tokens == 0
    assert result.input_tokens == 42
    assert result.source == "exact"


def test_from_openai_usage_zero_total_with_usage_object_present_is_estimated_not_missing():
    usage = SimpleNamespace(prompt_tokens=None, completion_tokens=None, total_tokens=0)
    result = TokenUsage.from_openai_usage(usage)
    assert result.source == "estimated"
    assert result.source != "missing"


def test_from_json_legacy_without_source_defaults_unknown():
    payload = json.dumps({"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15})
    result = TokenUsage.from_json(payload)
    assert result.source == "unknown"
    assert result.total_tokens == 15


def test_from_json_roundtrip_with_source():
    original = TokenUsage(12, 8, 20, "exact")
    restored = TokenUsage.from_json(original.to_json())
    assert restored == original


def test_to_json_missing_returns_none():
    assert TokenUsage(0, 0, 0, "missing").to_json() is None


def test_to_json_zero_but_exact_is_not_none():
    payload = TokenUsage(0, 0, 0, "exact").to_json()
    assert payload is not None
    data = json.loads(payload)
    assert data["source"] == "exact"
    assert data["total_tokens"] == 0


def test_token_usage_is_frozen():
    usage = TokenUsage(1, 2, 3, "exact")
    with pytest.raises(Exception):
        usage.input_tokens = 99  # type: ignore[misc]
