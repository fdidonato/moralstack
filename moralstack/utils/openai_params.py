"""
OpenAI API parameter helpers. Single source of truth for model-specific params.

Newer models (gpt-5.x, o-series) require max_completion_tokens instead of max_tokens.
Predicted output and strict structured-output support are determined by model
compatibility.
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


# Models that support strict Structured Outputs
# (``response_format={"type": "json_schema", ..., "strict": true}``), where the
# provider itself enforces the schema instead of the client validating it after
# the fact.  Deliberately an allowlist: an unrecognised model (a custom
# deployment behind OPENAI_BASE_URL, say) falls back to plain JSON mode, which
# is the pre-existing behaviour and can never make a call fail.
# Reference: https://platform.openai.com/docs/guides/structured-outputs
MODELS_SUPPORTING_JSON_SCHEMA = (
    "gpt-4o",
    "gpt-4.1",
    "gpt-5",
    "o1",
    "o3",
    "o4",
)

# gpt-4o snapshots older than 2024-08-06 predate Structured Outputs but still
# match the "gpt-4o" prefix above.
MODELS_WITHOUT_JSON_SCHEMA = ("gpt-4o-2024-05-13",)


def uses_max_completion_tokens(model: str | None) -> bool:
    """True if model requires max_completion_tokens instead of max_tokens."""
    m = (model or "").lower()
    return any(m.startswith(p) for p in MODELS_REQUIRING_MAX_COMPLETION_TOKENS)


def supports_predicted_output(model: str | None) -> bool:
    """True if model supports the ``prediction`` parameter (speculative decoding).

    Predicted outputs speed up generation when the expected output is largely
    similar to a known text (e.g. a draft revision).  The feature is only
    available on models that use the legacy ``max_tokens`` parameter.
    """
    m = (model or "").lower()
    return any(m.startswith(p) for p in MODELS_SUPPORTING_PREDICTED_OUTPUT)


def completion_tokens_param(model: str | None, max_tokens: int) -> dict[str, Any]:
    """Returns the correct param dict for chat.completions.create."""
    if uses_max_completion_tokens(model):
        return {"max_completion_tokens": max_tokens}
    return {"max_tokens": max_tokens}


def supports_json_schema(model: str | None) -> bool:
    """True if model supports strict Structured Outputs (schema-enforced JSON).

    With plain JSON mode the provider guarantees only that the reply parses as
    JSON; enum and required-field violations reach the client, which then has to
    reject the whole reply and retry.  A strict schema makes those violations
    impossible at the source.
    """
    m = (model or "").lower()
    if any(m.startswith(p) for p in MODELS_WITHOUT_JSON_SCHEMA):
        return False
    return any(m.startswith(p) for p in MODELS_SUPPORTING_JSON_SCHEMA)
