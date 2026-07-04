"""
Base types for MoralStack LLM models.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, List, Literal, Mapping, Optional

from moralstack.observability.token_usage import TokenUsage, TokenUsageSource


@dataclass(frozen=True)
class GenerationConfig:
    """
    Configurazione per la generazione di testo.
    Value object immutabile: ogni variazione richiede una nuova istanza.
    """

    max_tokens: int = 2048
    temperature: float = 0.7
    top_p: float = 0.9
    stop_sequences: List[str] = field(default_factory=list)
    response_format: Any = None


@dataclass(frozen=True)
class GenerationOverrides:
    """Per-request sampling overrides supplied by the client.

    These come from the OpenAI-style request body (proxy) or the SDK
    ``govern`` kwargs. They are honored only by the delivered-answer
    generators (NORMAL_COMPLETE, SAFE_COMPLETE, rewrite, speculative draft);
    the REFUSE wording deliberately ignores them so a low client
    ``max_tokens`` cannot truncate a safety message.

    Semantics of a ``None`` field depend on ``passthrough_unset``:

    * ``passthrough_unset is False`` (SDK / CLI): a ``None`` field means
      "fall back to the policy default" (env/config) — the legacy behavior.
    * ``passthrough_unset is True`` (proxy): a ``None`` field means "the client
      did not send this parameter, so omit it from the OpenAI call" — the model
      then uses its own server-side default. This makes the governed answer
      behave like a plain OpenAI call when the client leaves a field unset.

    Immutable value object: any variation requires a new instance.
    """

    max_tokens: int | None = None
    temperature: float | None = None
    top_p: float | None = None
    passthrough_unset: bool = False

    def is_empty(self) -> bool:
        return self.max_tokens is None and self.temperature is None and self.top_p is None

    @classmethod
    def from_mapping(
        cls,
        data: Mapping[str, Any] | None,
        *,
        passthrough_unset: bool = False,
    ) -> "GenerationOverrides | None":
        """Build overrides from a request mapping.

        ``max_completion_tokens`` (new-model alias) takes precedence over
        ``max_tokens``. Non-numeric or non-positive token values and
        non-numeric sampling values are ignored (defensive cast), so a
        malformed client field never alters generation.

        With ``passthrough_unset=True`` (proxy) the result is **always** an
        instance (even when every field is ``None``), because the omit-unset
        semantics must reach the generator even for an empty body. With the
        default ``passthrough_unset=False`` (SDK/CLI) an all-empty mapping
        yields ``None`` so the legacy env-default path is preserved.
        """
        if not data:
            return cls(passthrough_unset=True) if passthrough_unset else None

        def _as_positive_int(value: Any) -> int | None:
            try:
                parsed = int(value)
            except (TypeError, ValueError):
                return None
            return parsed if parsed > 0 else None

        def _as_float(value: Any) -> float | None:
            try:
                return float(value)
            except (TypeError, ValueError):
                return None

        max_tokens = _as_positive_int(data.get("max_completion_tokens"))
        if max_tokens is None:
            max_tokens = _as_positive_int(data.get("max_tokens"))
        overrides = cls(
            max_tokens=max_tokens,
            temperature=_as_float(data.get("temperature")),
            top_p=_as_float(data.get("top_p")),
            passthrough_unset=passthrough_unset,
        )
        if passthrough_unset:
            return overrides
        return None if overrides.is_empty() else overrides


@dataclass(frozen=True)
class GenerationResult:
    """
    Generation result.
    Immutable value object: any variation requires a new instance.
    """

    text: str
    tokens_used: int
    finish_reason: Literal["stop", "length", "content_filter"]
    logprobs: Optional[List[float]] = None
    prompt_used: Optional[str] = None
    system_used: Optional[str] = None
    messages_used: Optional[List[dict[str, Any]]] = None
    prompt_tokens: Optional[int] = None
    completion_tokens: Optional[int] = None
    token_usage_source: TokenUsageSource = "unknown"

    def token_usage_json(self) -> str | None:
        """Serialize token usage breakdown as JSON string for observability."""
        if self.tokens_used == 0 and not self.prompt_tokens:
            return None
        return TokenUsage.from_generation_result(self).to_json()
