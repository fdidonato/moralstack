"""
Safe Refusal Generator: genera la forma linguistica del rifiuto tramite LLM.

PRINCIPIO: Il LLM NON decide, NON valuta il rischio, NON vede la richiesta originale.
Scrive SOLO la forma linguistica del rifiuto, DOPO che la decisione REFUSE è già stata presa.

Usato esclusivamente nel FAST_PATH di rifiuto; nessun leakage operativo.
"""

from __future__ import annotations

import logging
from typing import Any, Protocol

logger = logging.getLogger(__name__)

# Language-agnostic: structural marker when LLM fails or unavailable.
# Consumer should replace with localized message.
REFUSAL_FALLBACK_MARKER = "[REFUSAL_FALLBACK]"

# Mappa codici ISO 639-1 (da Risk Estimator) a nome lingua per il prompt
_ISO_TO_LANGUAGE: dict[str, str] = {
    "en": "English",
    "it": "Italian",
    "es": "Spanish",
    "fr": "French",
    "de": "German",
    "pt": "Portuguese",
    "nl": "Dutch",
    "pl": "Polish",
    "ru": "Russian",
    "ja": "Japanese",
    "zh": "Chinese",
}


def _iso_to_language_name(iso: str | None) -> str:
    """Mappa codice ISO 639-1 (da risk_estimation.detected_language) a nome lingua."""
    if not iso or not str(iso).strip():
        return "English"
    # Supporta sia "it" che "it-IT"
    code = str(iso).strip().lower().split("-")[0][:2]
    return _ISO_TO_LANGUAGE.get(code, "English")


def _detect_language_fallback(prompt: str | None) -> str:
    """
    Fallback quando Risk Estimator non restituisce detected_language.
    Usa langdetect (locale, zero token) solo in quel caso.
    """
    if not prompt or not str(prompt).strip() or len(str(prompt).strip()) < 10:
        return "English"
    try:
        from langdetect import DetectorFactory, detect

        DetectorFactory.seed = 42
        iso = detect(str(prompt).strip())
        return _ISO_TO_LANGUAGE.get(iso, "English")
    except Exception as e:
        logger.debug("_detect_language_fallback failed, using English: %s", e)
        return "English"


def _build_system_message() -> str:
    return (
        "You are an AI assistant responsible for generating safe refusal responses. "
        "Your task is to refuse a user request that has already been classified as harmful. "
        "You must never provide technical details, instructions, examples, or references "
        "that could enable harmful activity."
    )


def _build_user_message(
    risk_category: str,
    policy_reason_codes: list[str],
    language: str,
    domain: str,
    rationale: str | None = None,
    refusal_redirection: str = "",
) -> str:
    reasons_str = ", ".join(policy_reason_codes) if policy_reason_codes else "policy_decision"
    domain_str = domain.strip() if domain else "general"

    rationale_block = ""
    if rationale and str(rationale).strip():
        rationale_block = (
            f"\nThe risk classification system provided this high-level rationale; "
            "use it as a guide for the tone and focus of your refusal "
            "(understand the general nature of the concern—safety, "
            "ethics, or policy—at an abstract level):\n"
            f'"{rationale.strip()[:400]}"\n\n'
            "CRITICAL: You must NOT repeat, quote, paraphrase, or mention any specific "
            "terms, substances, procedures, tools, or techniques from the rationale. "
            "Express your refusal at an abstract level only.\n\n"
        )

    domain_topic_block = ""
    if domain_str != "general":
        redirection = refusal_redirection.strip() if refusal_redirection else ""
        domain_topic_block = (
            f"\nGeneral topic (domain): {domain_str}\n\n"
            "You ARE allowed to mention this general topic at a descriptive level.\n"
            "You must NOT provide operational details, step-by-step instructions, "
            "templates, or concrete reusable examples.\n"
            "You must NOT quote or reference the original request.\n\n"
        )
        if redirection:
            domain_topic_block += (
                f"SPECIFIC REDIRECTION GUIDANCE for {domain_str} domain:\n"
                f"{redirection}\n\n"
                "IMPORTANT: Use these specific suggestions for redirection rather than generic educational platforms. "
                "Tailor your redirection to the domain context provided above.\n\n"
            )

    return (
        f"The user's request has been classified as:\n"
        f"- Risk category: {risk_category}\n"
        f"- Policy reasons: {reasons_str}\n"
        f"- Context domain: {domain_str}\n"
        f"{rationale_block}"
        f"{domain_topic_block}"
        "Write a refusal response that:\n\n"
        "1. Clearly and calmly refuses the request.\n"
        "2. Explains the refusal at an abstract, high-level ethical or safety level.\n"
        "3. Does NOT provide operational content, concrete reusable examples, "
        "templates, or step-by-step instructions.\n"
        "4. Does NOT restate, quote, or paraphrase the original request.\n"
        "5. Interrupts the harmful trajectory explicitly.\n"
        "6. Provides a safe and constructive redirection appropriate to the domain."
        "If specific redirection guidance is provided above, use those suggestions rather than generic alternatives.\n"
        "7. Uses professional, non-judgmental language.\n"
        f"8. Is written entirely in {language}.\n\n"
        "The response must be self-contained and safe."
    )


class _LLMGenerateProtocol(Protocol):
    """Protocollo minimo: solo generate(prompt, system)."""

    def generate(self, prompt: str, system: str = "", config: Any = None) -> Any: ...


def generate_llm_safe_refusal(
    risk_category: str,
    policy_reason_codes: list[str],
    language: str,
    domain: str,
    llm_client: _LLMGenerateProtocol | None = None,
    rationale: str | None = None,
    refusal_redirection: str = "",
) -> str:
    """
    Genera la forma linguistica del rifiuto tramite LLM.

    NON riceve: user_input, prompt originale, testo pericoloso.
    Riceve: risk_category, policy_reason_codes, language, domain, rationale (opzionale).

    Vincoli di sicurezza (rispettati dal prompt):
    - Nessun nome di sostanze, processi chimici, esempi concreti
    - Spiegazione a livello etico/sicurezza/policy generale
    - Nessun tono moralistico o minaccioso
    - Nessun suggerimento di ricerche abusabili

    Args:
        risk_category: Categoria di rischio (es. clearly_harmful, potentially_harmful)
        policy_reason_codes: Codici reason dalla policy (es. risk_clearly_harmful)
        language: Lingua di output (es. Italian, English)
        domain: Dominio di contesto (es. general, cybersecurity, financial, ...)
        llm_client: Policy LLM con metodo generate(); None per fallback
        rationale: Rationale dal risk estimator (opzionale); usato come linea
            guida a livello astratto

    Returns:
        Testo di rifiuto sicuro, self-contained.
    """
    if llm_client is None:
        return _fallback_refusal()

    risk_cat = (risk_category or "").strip() or "classified_as_harmful"
    reason_codes = list(policy_reason_codes) if policy_reason_codes else ["policy_decision"]
    lang = (language or "").strip() or "English"
    dom = (domain or "").strip() or "general"
    rational = (rationale or "").strip() or None

    system = _build_system_message()
    user_msg = _build_user_message(
        risk_cat, reason_codes, lang, dom, rationale=rational, refusal_redirection=refusal_redirection
    )

    try:
        result = llm_client.generate(prompt=user_msg, system=system)
        text = getattr(result, "text", None) or (str(result) if result else "")
        text = (text or "").strip()
        if len(text) > 80:
            return text
        logger.warning("generate_llm_safe_refusal: output troppo breve (<80 char), uso fallback")
    except Exception as e:
        logger.warning(
            "generate_llm_safe_refusal: LLM fallito, uso fallback: %s",
            str(e)[:100],
        )

    return _fallback_refusal()


def _fallback_refusal() -> str:
    """Fallback sicuro se LLM non disponibile o fallisce. Language-agnostic marker."""
    return REFUSAL_FALLBACK_MARKER
