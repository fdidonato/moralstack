"""
OpenAI API parameter helpers. Single source of truth for model-specific params.

Newer models (gpt-5.x, o-series) require max_completion_tokens instead of max_tokens.
"""

from __future__ import annotations

from typing import Any

MODELS_REQUIRING_MAX_COMPLETION_TOKENS = ("o1", "o3", "o4", "gpt-5")


def uses_max_completion_tokens(model: str) -> bool:
    """True if model requires max_completion_tokens instead of max_tokens."""
    m = (model or "").lower()
    return any(m.startswith(p) for p in MODELS_REQUIRING_MAX_COMPLETION_TOKENS)


def completion_tokens_param(model: str, max_tokens: int) -> dict[str, Any]:
    """Returns the correct param dict for chat.completions.create."""
    if uses_max_completion_tokens(model):
        return {"max_completion_tokens": max_tokens}
    return {"max_tokens": max_tokens}
