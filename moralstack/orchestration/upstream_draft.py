"""Upstream-origin speculative draft generator (opt-in `generation="upstream_then_verify"`).

Wraps an OpenAI-compatible client + a caller-supplied ``model`` string so the
speculative draft can be produced by the client's requested model instead of
the governance model (``self.policy``). Exposes exactly the two methods
``OrchestrationController._speculative_generate`` calls on the selected
generator — ``generate(prompt, system, overrides=...)`` and
``generate_messages(messages, overrides=...)`` — each returning a
``GenerationResult``, the identical shape ``OpenAIPolicy`` returns, so the
speculative-generation call site needs no branching on generator type.

Client exceptions propagate (the caller's existing ``except Exception`` falls
back to internal governed regeneration). Empty completion content is
returned as ``GenerationResult(text="")``, never raised — the empty-draft
fallback to internal regeneration is handled by the caller, not here.

Does not import ``OpenAIPolicy`` internals (kept fully independent so the
client model can never leak into the shared governance policy instance);
the small override-application helper below duplicates the ~8-line logic in
``OpenAIPolicy._apply_overrides``.
"""

from __future__ import annotations

import os
from typing import Any

from moralstack.models.base import GenerationOverrides, GenerationResult
from moralstack.observability.token_usage import TokenUsage, TokenUsageSource
from moralstack.utils.openai_params import completion_tokens_param


def _env_sampling_defaults() -> tuple[int, float, float]:
    """Env-derived sampling defaults, mirroring the OPENAI_* env config used elsewhere."""
    max_tokens = int(os.getenv("OPENAI_MAX_TOKENS", "4096"))
    temperature = float(os.getenv("OPENAI_TEMPERATURE", "0.7"))
    top_p = float(os.getenv("OPENAI_TOP_P", "0.9"))
    return max_tokens, temperature, top_p


def _env_timeout_seconds() -> float:
    """Resolve the upstream draft call timeout (seconds) from ``OPENAI_TIMEOUT_MS``.

    Mirrors ``OpenAIPolicy``'s own resolution of the same env var, same
    60000ms default (``moralstack/models/policy.py:49-50``). Bounding the
    call here (not at the speculative join in
    ``moralstack/orchestration/speculative_overlap.py``, which is
    do-not-modify) means a hanging/slow upstream client fails fast; the
    caller's existing ``except Exception`` in
    ``OrchestrationController._speculative_generate`` already falls back to
    internal governed regeneration for any raised exception.
    """
    timeout_ms_env = os.getenv("OPENAI_TIMEOUT_MS")
    timeout_ms = int(timeout_ms_env) if timeout_ms_env else 60000
    return timeout_ms / 1000.0


class UpstreamDraftGenerator:
    """Adapter routing the speculative-draft call to the caller-supplied model.

    ``client`` is an OpenAI-compatible client exposing ``chat.completions.create``
    (the SDK wrapped client / proxy upstream client). ``model`` is the
    requested model string, exposed on ``.model`` for draft-provenance
    attribution by the caller.
    """

    def __init__(self, client: Any, model: str) -> None:
        self.client = client
        self.model = model
        self._timeout = _env_timeout_seconds()

    def _resolve_sampling(
        self,
        overrides: GenerationOverrides | None,
    ) -> tuple[int | None, float | None, float | None]:
        """Apply per-request client overrides on top of resolved sampling defaults.

        ``passthrough_unset`` (proxy) semantics: an unset field stays ``None`` and
        is omitted from the API call so the model uses its own default. Otherwise
        (SDK/CLI) unset fields fall back to the env-derived defaults.
        """
        if overrides is not None and overrides.passthrough_unset:
            return overrides.max_tokens, overrides.temperature, overrides.top_p
        max_tokens, temperature, top_p = _env_sampling_defaults()
        if overrides is not None:
            if overrides.max_tokens is not None:
                max_tokens = overrides.max_tokens
            if overrides.temperature is not None:
                temperature = overrides.temperature
            if overrides.top_p is not None:
                top_p = overrides.top_p
        return max_tokens, temperature, top_p

    def _complete(
        self,
        messages: list[dict[str, str]],
        overrides: GenerationOverrides | None,
    ) -> tuple[str, int, str, int, int, int | None, TokenUsageSource]:
        """Single completions call. Returns (text, tokens_used, finish_reason,
        prompt_tokens, completion_tokens, cached_prompt_tokens, token_usage_source).

        Empty/whitespace content is returned as ``""`` (not raised); client
        exceptions propagate unchanged.
        """
        max_tokens, temperature, top_p = self._resolve_sampling(overrides)
        kwargs: dict[str, Any] = {"model": self.model, "messages": messages, "timeout": self._timeout}
        if temperature is not None:
            kwargs["temperature"] = temperature
        if top_p is not None:
            kwargs["top_p"] = top_p
        if max_tokens is not None:
            kwargs.update(completion_tokens_param(self.model, max_tokens))
        response = self.client.chat.completions.create(**kwargs)
        choice = response.choices[0]
        text = (choice.message.content or "").strip()
        token_usage = TokenUsage.from_openai_usage(response.usage)
        finish_reason = choice.finish_reason or "stop"
        return (
            text,
            token_usage.total_tokens,
            finish_reason,
            token_usage.input_tokens,
            token_usage.output_tokens,
            token_usage.cached_input_tokens,
            token_usage.source,
        )

    def generate(
        self,
        prompt: str,
        system: str = "",
        overrides: GenerationOverrides | None = None,
    ) -> GenerationResult:
        """Generate a response for the prompt using the client-supplied model."""
        messages: list[dict[str, str]] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        text, tokens_used, finish_reason, p_tok, c_tok, cached_tok, source = self._complete(messages, overrides)
        return GenerationResult(
            text=text,
            tokens_used=tokens_used,
            finish_reason=finish_reason,  # type: ignore[arg-type]
            prompt_used=prompt,
            system_used=system if system else None,
            prompt_tokens=p_tok,
            completion_tokens=c_tok,
            cached_prompt_tokens=cached_tok,
            token_usage_source=source,
        )

    def generate_messages(
        self,
        messages: list[dict[str, str]],
        overrides: GenerationOverrides | None = None,
    ) -> GenerationResult:
        """Generate from an already-structured OpenAI chat messages list (multi-turn)."""
        text, tokens_used, finish_reason, p_tok, c_tok, cached_tok, source = self._complete(messages, overrides)
        return GenerationResult(
            text=text,
            tokens_used=tokens_used,
            finish_reason=finish_reason,  # type: ignore[arg-type]
            messages_used=messages,
            prompt_tokens=p_tok,
            completion_tokens=c_tok,
            cached_prompt_tokens=cached_tok,
            token_usage_source=source,
        )
