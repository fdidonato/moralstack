"""Tests for GenerationResult token usage serialization."""

from __future__ import annotations

import json

from moralstack.models.base import GenerationResult


def test_generation_result_token_usage_json_includes_source_exact():
    result = GenerationResult(
        text="ok",
        tokens_used=20,
        finish_reason="stop",
        prompt_tokens=12,
        completion_tokens=8,
        token_usage_source="exact",
    )
    payload = result.token_usage_json()
    assert payload is not None
    data = json.loads(payload)
    assert data["source"] == "exact"
    assert data["total_tokens"] == 20


def test_generation_result_token_usage_json_none_when_missing_preserves_legacy_null():
    result = GenerationResult(text="", tokens_used=0, finish_reason="stop")
    assert result.token_usage_json() is None


def test_generation_result_default_source_when_field_omitted():
    result = GenerationResult(text="ok", tokens_used=5, finish_reason="stop", prompt_tokens=3, completion_tokens=2)
    assert result.token_usage_source == "unknown"
    payload = result.token_usage_json()
    assert payload is not None
    assert json.loads(payload)["source"] == "unknown"
