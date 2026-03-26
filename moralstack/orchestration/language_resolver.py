"""
Single entry point for resolving user language and building prompts with language prefix.
Reuses implementations from safe_refusal_generator and _policy_helpers.
"""

from __future__ import annotations

from moralstack.orchestration._policy_helpers import force_language_prefix
from moralstack.orchestration.safe_refusal_generator import (
    _detect_language_fallback,
    _iso_to_language_name,
)


def get_explicit_language(detected_iso: str, fallback_prompt: str | None) -> str:
    """
    Resolve explicit language name for prompts.
    Uses detected_iso (e.g. from risk_estimation.detected_language) when non-empty,
    otherwise falls back to detection from fallback_prompt text.
    """
    if detected_iso and str(detected_iso).strip():
        return _iso_to_language_name(detected_iso)
    return _detect_language_fallback(fallback_prompt)


def resolve_prompt_with_language(
    prompt: str,
    detected_iso: str,
    fallback_prompt: str | None,
) -> str:
    """
    Return the user prompt with language instruction prefix so the LLM responds
    in the user's language. Uses get_explicit_language then force_language_prefix.
    """
    explicit_lang = get_explicit_language(detected_iso, fallback_prompt)
    return force_language_prefix(prompt, explicit_language=explicit_lang)
