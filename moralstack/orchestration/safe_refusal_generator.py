"""
Safe Refusal Generator: genera la forma linguistica del rifiuto tramite LLM.

PRINCIPIO: Il LLM NON decide, NON valuta il rischio.
Riceve solo contesto non operativo dopo che REFUSE è già stato deciso.
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

# Risk signal -> overlay domain mapping used by REFUSE generation.
SIGNAL_DOMAIN_MAP: dict[str, str] = {
    "Q5:physical_harm": "science",
    "Q9:cyber_malware": "cybersecurity",
    "Q10:weapons_explosives_toxins": "science",
    "Q11:privacy_doxxing_stalking": "cybersecurity",
    "Q12:medical_harmful": "medical",
    "Q4:fraud": "financial",
    "Q8:self_harm_suicide": "mental_health",
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


def domain_from_signals(signals: list[str]) -> str | None:
    """Map risk signals to an overlay domain for refusal redirection."""
    for signal in signals:
        domain = SIGNAL_DOMAIN_MAP.get(signal)
        if domain:
            return domain
    return None


def resolve_refusal_domain_and_redirection(
    *,
    request_prompt: str,
    request_domain: str | None,
    detected_domain: str | None,
    risk_signals: list[str] | None,
    constitution_store: Any | None,
) -> tuple[str, str]:
    """
    Resolve domain and refusal_redirection for REFUSE generation.

    Order:
    1) request_domain
    2) detected_domain
    3) risk signal mapping
    4) constitution_store.detect_relevant_domains(prompt)
    """
    domain = (request_domain or detected_domain or "") or "general"
    if domain == "general" and risk_signals:
        domain = domain_from_signals(list(risk_signals)) or domain
    if domain == "general" and constitution_store is not None:
        try:
            detected = constitution_store.detect_relevant_domains(request_prompt)
            chosen = next((d for d in detected if d != "core"), None)
            if chosen:
                domain = chosen
        except Exception as e:
            logger.warning("resolve_refusal_domain_and_redirection: domain detection failed: %s", e)

    refusal_redirection = ""
    if domain != "general" and constitution_store is not None:
        try:
            constitution = constitution_store.get_constitution(domain)
            overlay = getattr(constitution, "active_overlay", None)
            if overlay is not None:
                refusal_redirection = getattr(overlay, "refusal_redirection", "") or ""
        except Exception as e:
            logger.warning("resolve_refusal_domain_and_redirection: overlay read failed: %s", e)
    return domain, refusal_redirection


def _build_system_message() -> str:
    return (
        "You are an AI assistant responsible for generating safe refusal responses. "
        "Your task is to refuse a user request that has already been classified as harmful. "
        "You must never provide technical details, instructions, examples, or references "
        "that could enable harmful activity."
    )


def _build_user_message(
    user_prompt: str,
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

    request_context_block = ""
    if str(user_prompt).strip():
        request_context_block = (
            "Original user request context (relevance only; never provide operational details):\n"
            f"{str(user_prompt).strip()}\n\n"
        )

    return (
        f"{request_context_block}"
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
    user_prompt: str,
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

    Riceve user_prompt (contesto), risk_category, reason_codes, language,
    domain, rationale (opzionale).

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
        str(user_prompt or ""),
        risk_cat,
        reason_codes,
        lang,
        dom,
        rationale=rational,
        refusal_redirection=refusal_redirection,
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
