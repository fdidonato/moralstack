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


# ---------------------------------------------------------------------------
# Cached input tokens (prompt-caching observability).
#
# None and 0 are different answers: None = the provider reported no cache
# details; 0 = it measured a cache miss. Hit-rate analysis needs both.
# ---------------------------------------------------------------------------


_UNSET = object()


def _usage(prompt=70, completion=30, total=100, *, details=_UNSET):
    """Usage double; ``details`` omitted entirely unless explicitly passed."""
    ns = SimpleNamespace(prompt_tokens=prompt, completion_tokens=completion, total_tokens=total)
    if details is not _UNSET:
        ns.prompt_tokens_details = details
    return ns


def test_cached_tokens_read_from_pydantic_like_details():
    usage = _usage(details=SimpleNamespace(cached_tokens=64))
    assert TokenUsage.from_openai_usage(usage).cached_input_tokens == 64


def test_cached_tokens_read_from_mapping_shaped_details():
    """An OpenAI-compatible proxy may return plain dicts instead of models."""
    usage = _usage(details={"cached_tokens": 64})
    assert TokenUsage.from_openai_usage(usage).cached_input_tokens == 64


def test_cached_tokens_zero_is_preserved_not_collapsed_to_unknown():
    usage = _usage(details=SimpleNamespace(cached_tokens=0))
    assert TokenUsage.from_openai_usage(usage).cached_input_tokens == 0


def test_cached_tokens_unknown_when_details_absent():
    assert TokenUsage.from_openai_usage(_usage()).cached_input_tokens is None


def test_cached_tokens_unknown_when_details_is_none():
    """The installed SDK leaves prompt_tokens_details None when not reported."""
    assert TokenUsage.from_openai_usage(_usage(details=None)).cached_input_tokens is None


def test_cached_tokens_unknown_when_cached_tokens_is_none():
    """PromptTokensDetails.cached_tokens is Optional[int]; int(None) would raise."""
    usage = _usage(details=SimpleNamespace(cached_tokens=None))
    assert TokenUsage.from_openai_usage(usage).cached_input_tokens is None


def test_cached_tokens_clamped_to_input_tokens():
    usage = _usage(prompt=10, completion=5, total=15, details=SimpleNamespace(cached_tokens=999))
    assert TokenUsage.from_openai_usage(usage).cached_input_tokens == 10


def test_cached_tokens_rejects_negative():
    usage = _usage(details=SimpleNamespace(cached_tokens=-1))
    assert TokenUsage.from_openai_usage(usage).cached_input_tokens is None


def test_cached_tokens_rejects_non_int_test_doubles():
    """MagicMock coerces to int(1) silently; it must never be read as a count."""
    from unittest.mock import MagicMock

    usage = _usage(details=SimpleNamespace(cached_tokens=MagicMock()))
    assert TokenUsage.from_openai_usage(usage).cached_input_tokens is None
    assert TokenUsage.from_openai_usage(_usage(details=SimpleNamespace(cached_tokens=True))).cached_input_tokens is None


def test_cached_tokens_never_raises_on_hostile_usage_object():
    """Observability must not break the request (PROJECT_SPEC §5.6)."""

    class Hostile:
        prompt_tokens = 10
        completion_tokens = 5
        total_tokens = 15

        @property
        def prompt_tokens_details(self):
            raise RuntimeError("provider blew up")

    assert TokenUsage.from_openai_usage(Hostile()).cached_input_tokens is None


def test_cached_tokens_none_for_embeddings():
    usage = _usage(prompt=42, completion=99, total=42, details=SimpleNamespace(cached_tokens=10))
    assert TokenUsage.from_openai_usage(usage, is_embedding=True).cached_input_tokens is None


def test_cached_tokens_on_estimated_path_is_clamped_to_synthetic_input():
    usage = SimpleNamespace(prompt_tokens=None, completion_tokens=None, total_tokens=100)
    usage.prompt_tokens_details = SimpleNamespace(cached_tokens=90)
    result = TokenUsage.from_openai_usage(usage)
    assert result.source == "estimated"
    assert result.cached_input_tokens == 70  # == synthetic input_tokens


def test_to_json_omits_cached_key_when_unknown():
    data = json.loads(TokenUsage(12, 8, 20, "exact").to_json())
    assert "cached_input_tokens" not in data


def test_to_json_emits_cached_key_when_zero():
    data = json.loads(TokenUsage(12, 8, 20, "exact", 0).to_json())
    assert data["cached_input_tokens"] == 0


def test_from_json_legacy_payload_reads_cached_as_unknown():
    payload = json.dumps({"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15, "source": "exact"})
    assert TokenUsage.from_json(payload).cached_input_tokens is None


def test_from_json_roundtrip_preserves_cached():
    for cached in (None, 0, 64):
        original = TokenUsage(70, 30, 100, "exact", cached)
        assert TokenUsage.from_json(original.to_json()) == original


def test_combine_sums_known_cached_and_ignores_unknown():
    combined = TokenUsage.combine(
        [
            TokenUsage(10, 5, 15, "exact", 8),
            TokenUsage(10, 5, 15, "exact", None),
            TokenUsage(10, 5, 15, "exact", 2),
        ]
    )
    assert combined.cached_input_tokens == 10


def test_combine_all_unknown_stays_unknown():
    combined = TokenUsage.combine([TokenUsage(10, 5, 15, "exact"), TokenUsage(1, 1, 2, "exact")])
    assert combined.cached_input_tokens is None


def test_from_generation_result_reads_and_clamps_cached():
    result = SimpleNamespace(
        tokens_used=100, prompt_tokens=70, completion_tokens=30, cached_prompt_tokens=64, token_usage_source="exact"
    )
    assert TokenUsage.from_generation_result(result).cached_input_tokens == 64
    legacy = SimpleNamespace(tokens_used=100, prompt_tokens=70, completion_tokens=30, token_usage_source="exact")
    assert TokenUsage.from_generation_result(legacy).cached_input_tokens is None
