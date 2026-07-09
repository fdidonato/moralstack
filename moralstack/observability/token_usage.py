"""Canonical token usage representation for MoralStack observability."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal, Sequence

TokenUsageSource = Literal["exact", "estimated", "missing", "unknown"]


def _coerce_token_count(raw: Any) -> int | None:
    """Accept only a genuine non-negative int. Rejects bool, Mock, str, float."""
    if raw is None or isinstance(raw, bool) or not isinstance(raw, int):
        return None
    return raw if raw >= 0 else None


def extract_cached_input_tokens(usage: Any) -> int | None:
    """
    Cached prompt tokens reported by the provider, or None when not reported.

    None and 0 are different answers: None means the provider said nothing about
    caching, 0 means it measured a cache miss. Hit-rate analysis needs both.

    Never raises: `prompt_tokens_details` may be absent (older SDKs, embeddings,
    non-OpenAI providers), may be an explicit None, may be a Mapping instead of a
    pydantic model (OpenAI-compatible proxies), or may be a test double whose
    attributes auto-materialize. Observability must not break the request (§5.6).
    """
    try:
        details = getattr(usage, "prompt_tokens_details", None)
        if details is None and isinstance(usage, Mapping):
            details = usage.get("prompt_tokens_details")
        if details is None:
            return None
        raw = getattr(details, "cached_tokens", None)
        if raw is None and isinstance(details, Mapping):
            raw = details.get("cached_tokens")
        return _coerce_token_count(raw)
    except Exception:  # noqa: BLE001 - telemetry must never raise into the caller
        return None


def _clamp_cached(cached: int | None, input_tokens: int) -> int | None:
    """Cached tokens are a subset of the input tokens; never report more."""
    if cached is None:
        return None
    return min(cached, input_tokens)


@dataclass(frozen=True)
class TokenUsage:
    input_tokens: int
    output_tokens: int
    total_tokens: int
    source: TokenUsageSource
    cached_input_tokens: int | None = None
    """Subset of ``input_tokens`` served from the provider's prompt cache.

    None = provider reported nothing (unknown). 0 = provider measured no cache hit.
    """

    def to_json(self) -> str | None:
        """None iff total_tokens == 0 AND source == 'missing'."""
        if self.total_tokens == 0 and self.source == "missing":
            return None
        payload: dict[str, Any] = {
            "prompt_tokens": self.input_tokens,
            "completion_tokens": self.output_tokens,
            "total_tokens": self.total_tokens,
            "source": self.source,
        }
        # Omitted when unknown, so payloads from providers that report no cache
        # details stay byte-identical to the pre-caching-observability format.
        if self.cached_input_tokens is not None:
            payload["cached_input_tokens"] = self.cached_input_tokens
        return json.dumps(payload)

    @classmethod
    def from_openai_usage(cls, usage: Any | None, *, is_embedding: bool = False) -> "TokenUsage":
        if usage is None:
            return cls(0, 0, 0, "missing")

        total = int(getattr(usage, "total_tokens", 0) or 0)
        prompt_tokens = getattr(usage, "prompt_tokens", None)
        completion_tokens = getattr(usage, "completion_tokens", None)

        if is_embedding:
            # The embeddings endpoint has no prompt cache and reports no details.
            input_tokens = int(prompt_tokens) if prompt_tokens is not None else total
            output_tokens = 0
            total_tokens = total if total else input_tokens
            source: TokenUsageSource = "exact" if prompt_tokens is not None else "estimated"
            return cls(input_tokens, output_tokens, total_tokens, source)

        cached = extract_cached_input_tokens(usage)

        if prompt_tokens is not None and completion_tokens is not None:
            input_tokens = int(prompt_tokens)
            output_tokens = int(completion_tokens)
            total_tokens = total if total else input_tokens + output_tokens
            return cls(input_tokens, output_tokens, total_tokens, "exact", _clamp_cached(cached, input_tokens))

        if total > 0:
            # input_tokens here is a 70/30 guess, so the clamp bounds cached against
            # a synthetic figure. Kept for consistency: cached must never exceed input.
            input_tokens = int(total * 0.7)
            output_tokens = total - input_tokens
            return cls(input_tokens, output_tokens, total, "estimated", _clamp_cached(cached, input_tokens))

        return cls(0, 0, 0, "estimated")

    @classmethod
    def from_json(cls, s: str | None) -> "TokenUsage":
        if s is None:
            return cls(0, 0, 0, "missing")
        data = json.loads(s)
        source = data.get("source", "unknown")
        if source not in ("exact", "estimated", "missing", "unknown"):
            source = "unknown"
        input_tokens = int(data.get("prompt_tokens", data.get("input_tokens", 0)) or 0)
        output_tokens = int(data.get("completion_tokens", data.get("output_tokens", 0)) or 0)
        total_tokens = int(data.get("total_tokens", 0) or 0)
        # Absent on every row written before cached-token observability: unknown, not zero.
        cached = _coerce_token_count(data.get("cached_input_tokens"))
        return cls(input_tokens, output_tokens, total_tokens, source, cached)

    @classmethod
    def from_generation_result(cls, result: Any) -> "TokenUsage":
        """Build TokenUsage from a GenerationResult-like object via getattr."""
        tokens_used = int(getattr(result, "tokens_used", 0) or 0)
        prompt_tokens = getattr(result, "prompt_tokens", None)
        completion_tokens = getattr(result, "completion_tokens", None)
        source_raw = getattr(result, "token_usage_source", None)
        if source_raw in ("exact", "estimated", "missing", "unknown"):
            source: TokenUsageSource = source_raw
        elif tokens_used == 0 and prompt_tokens is None and completion_tokens is None:
            source = "missing"
        else:
            source = "unknown"
        cached = _coerce_token_count(getattr(result, "cached_prompt_tokens", None))
        return cls(
            int(prompt_tokens or 0),
            int(completion_tokens or 0),
            tokens_used,
            source,
            _clamp_cached(cached, int(prompt_tokens or 0)),
        )

    @classmethod
    def combine(cls, usages: Sequence["TokenUsage"]) -> "TokenUsage":
        """Sum counts; combined source is the least certain among inputs."""
        if not usages:
            return cls(0, 0, 0, "missing")
        priority = {"missing": 0, "unknown": 1, "estimated": 2, "exact": 3}
        worst = min(usages, key=lambda u: priority[u.source])
        # Sum only the measured values; all-unknown stays unknown.
        cached_values = [u.cached_input_tokens for u in usages if u.cached_input_tokens is not None]
        return cls(
            sum(u.input_tokens for u in usages),
            sum(u.output_tokens for u in usages),
            sum(u.total_tokens for u in usages),
            worst.source,
            sum(cached_values) if cached_values else None,
        )
