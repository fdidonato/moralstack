"""
Domain Exclusion Responder: generates the LLM message for excluded-domain responses.

The LLM does NOT decide policy: it only produces a short, polite message in the
user's language. Domain exclusion is decided earlier by constitution overlay
(excluded=true). Used for early exit after risk estimation; no deliberation.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Protocol

from moralstack.models.base import GenerationConfig
from moralstack.observability.token_usage import TokenUsage
from moralstack.observability.wire import generate_wire_summary, wire_summary_json
from moralstack.prompts.catalog import PromptSet, default_prompt_set

logger = logging.getLogger(__name__)

# Language-agnostic structural marker when LLM fails or is unavailable.
DOMAIN_EXCLUSION_FALLBACK_MARKER = "[DOMAIN_EXCLUDED_FALLBACK]"

_DOMAIN_EXCLUSION_MAX_TOKENS = 128


def _build_system_message(prompt_set: PromptSet | None = None) -> str:
    return (prompt_set or default_prompt_set()).text("exclusion.system")


def _build_user_message(domain: str, user_prompt: str, prompt_set: PromptSet | None = None) -> str:
    prompt_snippet = (user_prompt or "").strip()[:200]
    return (prompt_set or default_prompt_set()).format("exclusion.user", domain=domain, prompt_snippet=prompt_snippet)


class _LLMGenerateProtocol(Protocol):
    """Minimal protocol: generate(prompt, system, config)."""

    def generate(self, prompt: str, system: str = "", config: Any = None) -> Any: ...


def _persist_exclusion_call(
    llm_client: Any,
    result: Any,
    text: str,
    user_msg: str,
    system: str,
    started_at_ms: int,
    duration_ms: float,
) -> None:
    """Persist the exclusion-message call as an ``llm_calls`` row (cycle 0), so its prompt and tokens are auditable.

    Best-effort observability (PROJECT_SPEC invariant 6): it never raises and never changes the response. Without a
    request scope (a direct call outside a request) nothing is written.
    """
    try:
        from moralstack.orchestration.persistence_helpers import record_llm_call

        record_llm_call(
            None,
            None,
            {
                "cycle": 0,
                "phase": "domain_exclusion",
                "module": "orchestration",
                "action": "domain_excluded",
                "model": str(getattr(llm_client, "model", "") or ""),
                "started_at": started_at_ms,
                "duration_ms": duration_ms,
                "prompt": user_msg,
                "system_prompt": system,
                "raw_response": text,
                "parsed_summary_json": wire_summary_json(generate_wire_summary(user_msg, system)),
                "token_usage_json": TokenUsage.from_generation_result(result).to_json(),
                "attempts": 1,
                "sequence_in_cycle": 6,
                "call_kind": "domain_exclusion",
            },
        )
    except Exception:
        logger.debug("domain exclusion llm call persist skipped", exc_info=True)


def generate_domain_exclusion_response(
    domain: str,
    user_prompt: str,
    llm_client: _LLMGenerateProtocol | None = None,
    prompt_set: PromptSet | None = None,
) -> str:
    """
    Generate a polite exclusion message in the user's language. max_tokens=128.

    The LLM does not decide anything; it only formats the message. Domain
    exclusion is already determined by constitution overlay (excluded=true).

    Args:
        domain: Domain name that is excluded (e.g. political, medical).
        user_prompt: User request text (first 200 chars used for language detection).
        llm_client: Policy LLM with generate(); None for fallback marker.
        prompt_set: source of the system and user texts (None = packaged defaults).

    Returns:
        Short polite message in the user's language, or DOMAIN_EXCLUSION_FALLBACK_MARKER.
    """
    if llm_client is None:
        return DOMAIN_EXCLUSION_FALLBACK_MARKER

    domain_str = (domain or "").strip() or "this topic"
    ps = prompt_set or default_prompt_set()
    system = _build_system_message(ps)
    user_msg = _build_user_message(domain_str, user_prompt or "", ps)

    try:
        gen_config = GenerationConfig(max_tokens=_DOMAIN_EXCLUSION_MAX_TOKENS)
        started_at_ms = int(time.time() * 1000)
        started = time.perf_counter()
        result = llm_client.generate(prompt=user_msg, system=system, config=gen_config)
        text = getattr(result, "text", None) or (str(result) if result else "")
        _persist_exclusion_call(
            llm_client, result, text, user_msg, system, started_at_ms, (time.perf_counter() - started) * 1000
        )
        text = (text or "").strip()
        if len(text) > 15:
            return text
        logger.warning("generate_domain_exclusion_response: output too short (<15 char), using fallback")
    except Exception as e:
        logger.warning(
            "generate_domain_exclusion_response: LLM failed, using fallback: %s",
            str(e)[:100],
        )

    return DOMAIN_EXCLUSION_FALLBACK_MARKER
