"""Canonical token usage representation for MoralStack observability."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Literal, Sequence

TokenUsageSource = Literal["exact", "estimated", "missing", "unknown"]


@dataclass(frozen=True)
class TokenUsage:
    input_tokens: int
    output_tokens: int
    total_tokens: int
    source: TokenUsageSource

    def to_json(self) -> str | None:
        """None iff total_tokens == 0 AND source == 'missing'."""
        if self.total_tokens == 0 and self.source == "missing":
            return None
        return json.dumps(
            {
                "prompt_tokens": self.input_tokens,
                "completion_tokens": self.output_tokens,
                "total_tokens": self.total_tokens,
                "source": self.source,
            }
        )

    @classmethod
    def from_openai_usage(cls, usage: Any | None, *, is_embedding: bool = False) -> "TokenUsage":
        if usage is None:
            return cls(0, 0, 0, "missing")

        total = int(getattr(usage, "total_tokens", 0) or 0)
        prompt_tokens = getattr(usage, "prompt_tokens", None)
        completion_tokens = getattr(usage, "completion_tokens", None)

        if is_embedding:
            input_tokens = int(prompt_tokens) if prompt_tokens is not None else total
            output_tokens = 0
            total_tokens = total if total else input_tokens
            source: TokenUsageSource = "exact" if prompt_tokens is not None else "estimated"
            return cls(input_tokens, output_tokens, total_tokens, source)

        if prompt_tokens is not None and completion_tokens is not None:
            input_tokens = int(prompt_tokens)
            output_tokens = int(completion_tokens)
            total_tokens = total if total else input_tokens + output_tokens
            return cls(input_tokens, output_tokens, total_tokens, "exact")

        if total > 0:
            input_tokens = int(total * 0.7)
            output_tokens = total - input_tokens
            return cls(input_tokens, output_tokens, total, "estimated")

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
        return cls(input_tokens, output_tokens, total_tokens, source)

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
        return cls(int(prompt_tokens or 0), int(completion_tokens or 0), tokens_used, source)

    @classmethod
    def combine(cls, usages: Sequence["TokenUsage"]) -> "TokenUsage":
        """Sum counts; combined source is the least certain among inputs."""
        if not usages:
            return cls(0, 0, 0, "missing")
        priority = {"missing": 0, "unknown": 1, "estimated": 2, "exact": 3}
        worst = min(usages, key=lambda u: priority[u.source])
        return cls(
            sum(u.input_tokens for u in usages),
            sum(u.output_tokens for u in usages),
            sum(u.total_tokens for u in usages),
            worst.source,
        )
