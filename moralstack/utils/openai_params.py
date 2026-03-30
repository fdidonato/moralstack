"""
OpenAI API parameter helpers. Single source of truth for model-specific params.

Newer models (gpt-5.x, o-series) require max_completion_tokens instead of max_tokens.
Predicted output support is determined by model compatibility.
"""

from __future__ import annotations

from typing import Any

MODELS_REQUIRING_MAX_COMPLETION_TOKENS = ("o1", "o3", "o4", "gpt-5")

# Models that support the ``prediction`` parameter for speculative decoding.
# Predicted outputs are incompatible with max_completion_tokens, logprobs,
# and n > 1.  Only models using the legacy max_tokens param qualify.
# Reference: https://platform.openai.com/docs/guides/predicted-outputs
MODELS_SUPPORTING_PREDICTED_OUTPUT = (
    "gpt-4o",
    "gpt-4o-mini",
    "gpt-4.1",
    "gpt-4.1-mini",
    "gpt-4.1-nano",
)


def uses_max_completion_tokens(model: str) -> bool:
    """True if model requires max_completion_tokens instead of max_tokens."""
    m = (model or "").lower()
    return any(m.startswith(p) for p in MODELS_REQUIRING_MAX_COMPLETION_TOKENS)


def supports_predicted_output(model: str) -> bool:
    """True if model supports the ``prediction`` parameter (speculative decoding).

    Predicted outputs speed up generation when the expected output is largely
    similar to a known text (e.g. a draft revision).  The feature is only
    available on models that use the legacy ``max_tokens`` parameter.
    """
    m = (model or "").lower()
    return any(m.startswith(p) for p in MODELS_SUPPORTING_PREDICTED_OUTPUT)


def completion_tokens_param(model: str, max_tokens: int) -> dict[str, Any]:
    """Returns the correct param dict for chat.completions.create."""
    if uses_max_completion_tokens(model):
        return {"max_completion_tokens": max_tokens}
    return {"max_tokens": max_tokens}
