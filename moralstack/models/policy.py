"""
Policy LLM for MoralStack — OpenAI only.

Single provider: OpenAI API. Configuration via environment variables:
- OPENAI_API_KEY (required)
- OPENAI_MODEL (default: gpt-4o)
- MORALSTACK_POLICY_REWRITE_MODEL (optional; deliberative rewrite at cycle 2+; defaults to OPENAI_MODEL)
- OPENAI_BASE_URL (optional, for proxy/enterprise)
- OPENAI_TIMEOUT_MS (optional)
- OPENAI_MAX_RETRIES (optional)
- OPENAI_TEMPERATURE (optional, default 0.7)
- OPENAI_TOP_P (optional, default 0.9)
- OPENAI_MAX_TOKENS (optional, default 4096; max output tokens for the delivered answer)
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any

from moralstack.models.base import GenerationConfig, GenerationOverrides, GenerationResult
from moralstack.observability.token_usage import TokenUsage, TokenUsageSource
from moralstack.prompts.catalog import PromptSet, default_prompt_set
from moralstack.utils.openai_params import (
    completion_tokens_param,
    supports_json_schema,
    supports_predicted_output,
)
from moralstack.utils.provider_errors import (
    classify_provider_error,
    sleep_with_backoff,
)

logger = logging.getLogger(__name__)


def _get_openai_config(
    api_key_override: str | None = None,
    model_override: str | None = None,
) -> OpenAIPolicyConfig:
    """Configuration from env, with optional overrides (e.g. from --openai-key)."""
    api_key = (api_key_override or os.getenv("OPENAI_API_KEY") or "").strip()
    if not api_key:
        raise ValueError(
            "OPENAI_API_KEY is not set. Set the environment variable OPENAI_API_KEY "
            "or pass --openai-key from the command line. Example: export OPENAI_API_KEY=sk-..."
        )
    base_url = os.getenv("OPENAI_BASE_URL") or None
    timeout_ms_env = os.getenv("OPENAI_TIMEOUT_MS")
    timeout_ms = int(timeout_ms_env) if timeout_ms_env else 60000
    max_retries = int(os.getenv("OPENAI_MAX_RETRIES", "3"))
    model = model_override or os.getenv("OPENAI_MODEL", "gpt-4o")
    temperature = float(os.getenv("OPENAI_TEMPERATURE", "0.7"))
    top_p = float(os.getenv("OPENAI_TOP_P", "0.9"))
    max_tokens = int(os.getenv("OPENAI_MAX_TOKENS", "4096"))
    rewrite_raw = os.getenv("MORALSTACK_POLICY_REWRITE_MODEL")
    rewrite_model = rewrite_raw.strip() if rewrite_raw and rewrite_raw.strip() else None
    return OpenAIPolicyConfig(
        api_key=api_key,
        base_url=base_url,
        timeout_ms=timeout_ms,
        max_retries=max_retries,
        model=model,
        temperature=temperature,
        top_p=top_p,
        max_tokens=max_tokens,
        rewrite_model=rewrite_model,
    )


@dataclass(frozen=True)
class OpenAIPolicyConfig:
    """
    Configuration for OpenAIPolicy (optional env overrides).
    Immutable value object: any variation requires a new instance.
    """

    api_key: str | None = None
    model: str | None = None
    base_url: str | None = None
    timeout_ms: int | None = None
    max_retries: int | None = None
    temperature: float | None = None
    top_p: float | None = None
    max_tokens: int | None = None
    rewrite_model: str | None = None


class OpenAIPolicy:
    """
    Policy LLM based exclusively on the OpenAI API.
    Implements generate, rewrite, refuse with retry and timeout.
    """

    def __init__(
        self,
        api_key: str | None = None,
        model: str | None = None,
        base_url: str | None = None,
        timeout_ms: int | None = None,
        max_retries: int | None = None,
        config: OpenAIPolicyConfig | None = None,
        prompt_set: PromptSet | None = None,
    ) -> None:
        """
        Args:
            api_key: Override OPENAI_API_KEY (default: from env)
            model: Override OPENAI_MODEL (default: from env or gpt-4o)
            base_url: Override OPENAI_BASE_URL (default: from env)
            timeout_ms: Override OPENAI_TIMEOUT_MS in milliseconds
            max_retries: Override OPENAI_MAX_RETRIES
            config: Config object that overrides the individual parameters
            prompt_set: PromptSet from which `rewrite` reads its texts (default: packaged set)
        """
        self._prompt_set = prompt_set or default_prompt_set()
        # 1. Load the defaults from env (with any api_key/model overrides from the constructor)
        env_cfg = _get_openai_config(api_key_override=api_key, model_override=model)

        # 2. Apply any overrides passed explicitly or via the config object.
        #    Priority is: explicit __init__ parameters > config object > env/defaults.

        # Parameters from config (if present)
        final_api_key = config.api_key if config and config.api_key is not None else env_cfg.api_key
        final_model = config.model if config and config.model is not None else env_cfg.model
        final_base_url = config.base_url if config and config.base_url is not None else env_cfg.base_url
        final_timeout_ms = config.timeout_ms if config and config.timeout_ms is not None else env_cfg.timeout_ms
        final_max_retries = config.max_retries if config and config.max_retries is not None else env_cfg.max_retries
        final_temperature = config.temperature if config and config.temperature is not None else env_cfg.temperature
        final_top_p = config.top_p if config and config.top_p is not None else env_cfg.top_p
        final_max_tokens = config.max_tokens if config and config.max_tokens is not None else env_cfg.max_tokens
        final_rewrite_model = config.rewrite_model if config and config.rewrite_model is not None else env_cfg.rewrite_model

        # Explicit overrides from __init__ parameters (they override everything)
        if api_key is not None:
            final_api_key = api_key
        if model is not None:
            final_model = model
        if base_url is not None:
            final_base_url = base_url
        if timeout_ms is not None:
            final_timeout_ms = timeout_ms
        if max_retries is not None:
            final_max_retries = max_retries

        self.api_key = final_api_key
        self.model: str = final_model or "gpt-4o"
        self._rewrite_model: str = final_rewrite_model if final_rewrite_model is not None else self.model
        self._timeout = (final_timeout_ms / 1000.0) if final_timeout_ms is not None else 60.0
        self._max_retries = final_max_retries if final_max_retries is not None else 3
        self._default_temperature = final_temperature if final_temperature is not None else 0.7
        self._default_top_p = final_top_p if final_top_p is not None else 0.9
        self._default_max_tokens = final_max_tokens if final_max_tokens is not None else 4096

        try:
            import openai
        except ImportError:
            raise ImportError("The OpenAI client is required. Install with: pip install openai")

        kwargs: dict[str, Any] = {"api_key": self.api_key}
        if final_base_url:
            kwargs["base_url"] = final_base_url
        self.client = openai.OpenAI(**kwargs)
        self._cost_tracker: Any = None

    @property
    def rewrite_model(self) -> str:
        """Effective model for `rewrite()` (may differ from `model` when env is set)."""
        return self._rewrite_model

    def set_cost_tracker(self, tracker: Any) -> None:
        """Sets a TokenCostTracker to track call costs."""
        self._cost_tracker = tracker

    def _complete(
        self,
        messages: list[dict[str, str]],
        temperature: float | None = None,
        max_tokens: int | None = 1024,
        top_p: float | None = None,
        response_format: Any = None,
        prediction: dict[str, str] | None = None,
        model_override: str | None = None,
        omit_unset: bool = False,
    ) -> tuple[str, int, str, int, int, int | None, TokenUsageSource]:
        """
        Completions call with retry on transient errors (429/503/timeout).
        Returns (text, tokens_used, finish_reason, prompt_tokens, completion_tokens,
        cached_prompt_tokens, token_usage_source). ``cached_prompt_tokens`` is None when
        the provider reports no prompt-cache details.
        Uses classifier and jittered backoff.

        Args:
            prediction: Optional predicted output for speculative decoding.
                Expected format: ``{"type": "content", "content": "..."}``
                Only applied when the current model supports the feature.
            model_override: If set, used instead of ``self.model`` for this call.
            omit_unset: Proxy passthrough mode. When True, a ``None``
                ``temperature``/``top_p``/``max_tokens`` is **omitted** from the
                OpenAI call (the model uses its own default) instead of falling
                back to the policy default. When False (legacy SDK/CLI path),
                ``None`` resolves to the policy default exactly as before.
        """
        effective_model = model_override or self.model
        if temperature is not None:
            temp = temperature
        elif omit_unset:
            temp = None
        else:
            temp = self._default_temperature
        if top_p is not None:
            top_p_val = top_p
        elif omit_unset:
            top_p_val = None
        else:
            top_p_val = self._default_top_p
        use_prediction = prediction is not None and supports_predicted_output(effective_model or "")
        # prediction and response_format are mutually exclusive in
        # the OpenAI API; prefer response_format (structural guarantee)
        if use_prediction and response_format is not None:
            use_prediction = False
        # A strict json_schema is degraded to plain JSON mode on models that do
        # not support Structured Outputs, so the caller keeps working exactly as
        # before (schema validated client-side) instead of the call failing.
        if (
            isinstance(response_format, dict)
            and response_format.get("type") == "json_schema"
            and not supports_json_schema(effective_model or "")
        ):
            response_format = {"type": "json_object"}
        last_error: Exception | None = None
        for attempt in range(self._max_retries):
            try:
                kwargs: dict[str, Any] = {
                    "model": effective_model,
                    "messages": messages,
                    "timeout": self._timeout,
                }
                # In passthrough mode (omit_unset) a None param is left out so the
                # model uses its own default. In the legacy path temp/top_p_val are
                # always resolved to a concrete value, so the payload is unchanged.
                if temp is not None:
                    kwargs["temperature"] = temp
                if top_p_val is not None:
                    kwargs["top_p"] = top_p_val
                if max_tokens is not None:
                    kwargs.update(completion_tokens_param(effective_model, max_tokens))
                if response_format is not None:
                    kwargs["response_format"] = response_format
                if use_prediction:
                    kwargs["prediction"] = prediction
                response = self.client.chat.completions.create(**kwargs)
                choice = response.choices[0]
                text = (choice.message.content or "").strip()
                token_usage = TokenUsage.from_openai_usage(response.usage)
                tokens = token_usage.total_tokens
                prompt_tokens = token_usage.input_tokens
                completion_tokens = token_usage.output_tokens
                if self._cost_tracker is not None and hasattr(self._cost_tracker, "add_call"):
                    self._cost_tracker.add_call(effective_model, prompt_tokens, completion_tokens)
                reason = choice.finish_reason or "stop"
                return (
                    text,
                    tokens,
                    reason,
                    prompt_tokens,
                    completion_tokens,
                    token_usage.cached_input_tokens,
                    token_usage.source,
                )
            except Exception as e:
                last_error = e
                if classify_provider_error(e) == "transient" and attempt < self._max_retries - 1:
                    sleep_with_backoff(
                        attempt,
                        base_delay_sec=2.0,
                        max_delay_sec=60.0,
                        jitter_max_sec=2.0,
                    )
                    continue
                raise RuntimeError(f"OpenAI API call failed: {e}") from last_error
        raise RuntimeError(f"OpenAI API call failed after {self._max_retries} retries: {last_error}") from last_error

    @staticmethod
    def _apply_overrides(
        overrides: GenerationOverrides | None,
        max_tokens: int,
        temperature: float,
        top_p: float,
    ) -> tuple[int, float, float]:
        """Apply per-request client overrides on top of resolved sampling params.

        Only non-None override fields take effect; everything else keeps the
        value already resolved from ``config`` or the policy defaults.
        """
        if overrides is not None:
            if overrides.max_tokens is not None:
                max_tokens = overrides.max_tokens
            if overrides.temperature is not None:
                temperature = overrides.temperature
            if overrides.top_p is not None:
                top_p = overrides.top_p
        return max_tokens, temperature, top_p

    def generate(
        self,
        prompt: str,
        system: str = "",
        config: GenerationConfig | None = None,
        prediction: dict[str, str] | None = None,
        model_override: str | None = None,
        overrides: GenerationOverrides | None = None,
    ) -> GenerationResult:
        """Generate a response for the prompt.

        Args:
            prediction: Optional predicted output for speculative decoding.
                When provided and the model supports it, the API uses
                speculative decoding to produce faster responses when the
                output is expected to be similar to the prediction text.
            model_override: If set, used instead of the primary policy model for this call.
            overrides: Optional per-request client sampling overrides. Non-None
                fields win over both ``config`` and the policy defaults.
        """
        messages: list[dict[str, str]] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        if overrides is not None and overrides.passthrough_unset:
            # Proxy passthrough: use exactly what the client sent; unset fields
            # stay None and are omitted downstream so the model uses its default.
            passthrough = True
            max_tokens = overrides.max_tokens
            temperature = overrides.temperature
            top_p = overrides.top_p
            response_format = getattr(config, "response_format", None) if config is not None else None
        else:
            passthrough = False
            max_tokens = self._default_max_tokens
            temperature = self._default_temperature
            top_p = self._default_top_p
            response_format = None
            if config is not None:
                max_tokens = getattr(config, "max_tokens", max_tokens)
                temperature = getattr(config, "temperature", temperature)
                top_p = getattr(config, "top_p", top_p)
                response_format = getattr(config, "response_format", None)
            max_tokens, temperature, top_p = self._apply_overrides(overrides, max_tokens, temperature, top_p)

        text, tokens_used, finish_reason, p_tok, c_tok, cached_tok, source = self._complete(
            messages,
            temperature=temperature,
            max_tokens=max_tokens,
            top_p=top_p,
            prediction=prediction,
            response_format=response_format,
            model_override=model_override,
            omit_unset=passthrough,
        )
        return GenerationResult(
            text=text,
            tokens_used=tokens_used,
            finish_reason=finish_reason,  # type: ignore[arg-type]
            logprobs=None,
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
        config: GenerationConfig | None = None,
        prediction: dict[str, str] | None = None,
        model_override: str | None = None,
        overrides: GenerationOverrides | None = None,
    ) -> GenerationResult:
        """Generate from an already-structured OpenAI chat messages list.

        ``overrides`` carries per-request client sampling overrides; its
        non-None fields win over both ``config`` and the policy defaults.
        """
        if overrides is not None and overrides.passthrough_unset:
            # Proxy passthrough: use exactly what the client sent; unset fields
            # stay None and are omitted downstream so the model uses its default.
            passthrough = True
            max_tokens = overrides.max_tokens
            temperature = overrides.temperature
            top_p = overrides.top_p
            response_format = getattr(config, "response_format", None) if config is not None else None
        else:
            passthrough = False
            max_tokens = self._default_max_tokens
            temperature = self._default_temperature
            top_p = self._default_top_p
            response_format = None
            if config is not None:
                max_tokens = getattr(config, "max_tokens", max_tokens)
                temperature = getattr(config, "temperature", temperature)
                top_p = getattr(config, "top_p", top_p)
                response_format = getattr(config, "response_format", None)
            max_tokens, temperature, top_p = self._apply_overrides(overrides, max_tokens, temperature, top_p)

        text, tokens_used, finish_reason, p_tok, c_tok, cached_tok, source = self._complete(
            messages,
            temperature=temperature,
            max_tokens=max_tokens,
            top_p=top_p,
            prediction=prediction,
            response_format=response_format,
            model_override=model_override,
            omit_unset=passthrough,
        )
        return GenerationResult(
            text=text,
            tokens_used=tokens_used,
            finish_reason=finish_reason,  # type: ignore[arg-type]
            logprobs=None,
            prompt_used=None,
            system_used=None,
            messages_used=messages,
            prompt_tokens=p_tok,
            completion_tokens=c_tok,
            cached_prompt_tokens=cached_tok,
            token_usage_source=source,
        )

    def rewrite(
        self,
        prompt: str,
        draft: str,
        guidance: str,
        system: str = "",
        config: GenerationConfig | None = None,
        overrides: GenerationOverrides | None = None,
    ) -> GenerationResult:
        """Rewrites a draft based on feedback.

        Uses OpenAI predicted outputs (speculative decoding) when the model
        supports it: the existing draft is provided as a prediction hint so
        that unchanged portions are generated significantly faster.
        """
        ps = self._prompt_set
        rewrite_prompt = ps.format("policy.rewrite.user", prompt=prompt, draft=draft, guidance=guidance)
        rewrite_system = system or ps.text("policy.rewrite.default_system")

        # Append constraints regardless of source
        rewrite_system += ps.text("policy.rewrite.constraints")
        draft_prediction = {"type": "content", "content": draft} if draft else None
        logger.info(
            "policy_rewrite using model=%s (primary=%s)",
            self._rewrite_model,
            self.model,
        )
        return self.generate(
            rewrite_prompt,
            system=rewrite_system,
            config=config,
            prediction=draft_prediction,
            model_override=self._rewrite_model,
            overrides=overrides,
        )

    def refuse(
        self,
        prompt: str,
        guidance: str,
        config: GenerationConfig | None = None,
        language: str | None = None,
    ) -> GenerationResult:
        """Generates an empathetic and informative refusal.

        Args:
            prompt: User request (may be empty for regulated domains).
            guidance: Reason for declining.
            config: Optional generation config.
            language: Explicit output language (e.g. "English") when prompt is empty
                or to reduce LLM non-compliance (e.g. Spanish when request is English).
        """
        refuse_prompt = (
            f"USER REQUEST:\n{prompt}\n\n"
            f"REASON FOR DECLINING:\n{guidance}\n\n"
            "Generate an empathetic and varied response. "
            "The response MUST: (1) acknowledge the user's request with tact, "
            "(2) explain clearly and articulately why you cannot fulfill it (risk/ethics), "
            "(3) actively suggest safe alternatives, legitimate resources, or ways to rephrase. "
            "IMPORTANT: Do not use boilerplate or fixed templates. "
            "Vary the opening and style based on the context. "
            "DO NOT respond with just a one-sentence refusal."
        )
        if language and str(language).strip():
            refuse_prompt = (
                f"CRITICAL: The user's request is in {language.strip()}. "
                f"You MUST respond entirely in {language.strip()}. "
                "Do not add translations.\n\n"
                f"{refuse_prompt}"
            )
        refuse_system = (
            "You are a compassionate ethical assistant. "
            "You help users navigate complex topics safely. "
            "When you must refuse a request, you do so by educating and "
            "redirecting, not just blocking. "
            "Avoid repetitive opening phrases like 'I'm sorry'. "
            "Instead, vary your tone based on the user's intent. "
            "Provide deep reasoning and helpful, specific alternatives. "
        )
        if language and str(language).strip():
            refuse_system += f"CRITICAL: You MUST respond entirely in {language.strip()}. Do not add translations."
        else:
            refuse_system += "Always respond in the SAME LANGUAGE as the user's request."
        return self.generate(refuse_prompt, system=refuse_system, config=config)


# Re-export for compatibility (some modules imported GenerationConfig from policy)
__all__ = ["OpenAIPolicy", "OpenAIPolicyConfig", "GenerationConfig", "GenerationOverrides"]
