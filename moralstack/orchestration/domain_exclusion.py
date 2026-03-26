"""
Domain Exclusion Responder: generates the LLM message for excluded-domain responses.

The LLM does NOT decide policy: it only produces a short, polite message in the
user's language. Domain exclusion is decided earlier by constitution overlay
(excluded=true). Used for early exit after risk estimation; no deliberation.
"""

from __future__ import annotations

import logging
from typing import Any, Protocol

from moralstack.models.base import GenerationConfig

logger = logging.getLogger(__name__)

# Language-agnostic structural marker when LLM fails or is unavailable.
DOMAIN_EXCLUSION_FALLBACK_MARKER = "[DOMAIN_EXCLUDED_FALLBACK]"

_DOMAIN_EXCLUSION_MAX_TOKENS = 128


def _build_system_message() -> str:
    return (
        "You are an AI assistant. Write a polite, empathetic message explaining "
        "you cannot help with a specific topic. Always respond in the same language "
        "as the user's message. Do not provide technical details about configuration "
        "or exclusion."
    )


def _build_user_message(domain: str, user_prompt: str) -> str:
    prompt_snippet = (user_prompt or "").strip()[:200]
    return (
        f"Topic not available: '{domain}'.\n\n"
        f"User text (for language detection only): '{prompt_snippet}'\n\n"
        "Write 2-3 polite sentences explaining you cannot assist with this topic. "
        "Respond in the EXACT SAME LANGUAGE as the user's text. No technical details."
    )


class _LLMGenerateProtocol(Protocol):
    """Minimal protocol: generate(prompt, system, config)."""

    def generate(self, prompt: str, system: str = "", config: Any = None) -> Any: ...


def generate_domain_exclusion_response(
    domain: str,
    user_prompt: str,
    llm_client: _LLMGenerateProtocol | None = None,
) -> str:
    """
    Generate a polite exclusion message in the user's language. max_tokens=128.

    The LLM does not decide anything; it only formats the message. Domain
    exclusion is already determined by constitution overlay (excluded=true).

    Args:
        domain: Domain name that is excluded (e.g. political, medical).
        user_prompt: User request text (first 200 chars used for language detection).
        llm_client: Policy LLM with generate(); None for fallback marker.

    Returns:
        Short polite message in the user's language, or DOMAIN_EXCLUSION_FALLBACK_MARKER.
    """
    if llm_client is None:
        return DOMAIN_EXCLUSION_FALLBACK_MARKER

    domain_str = (domain or "").strip() or "this topic"
    system = _build_system_message()
    user_msg = _build_user_message(domain_str, user_prompt or "")

    try:
        gen_config = GenerationConfig(max_tokens=_DOMAIN_EXCLUSION_MAX_TOKENS)
        result = llm_client.generate(prompt=user_msg, system=system, config=gen_config)
        text = getattr(result, "text", None) or (str(result) if result else "")
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
