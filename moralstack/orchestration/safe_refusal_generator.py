"""
Safe Refusal Generator: genera la forma linguistica del rifiuto tramite LLM.

PRINCIPIO: Il LLM NON decide, NON valuta il rischio.
Riceve solo contesto non operativo dopo che REFUSE è già stato deciso.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Protocol

from moralstack.observability.token_usage import TokenUsage
from moralstack.orchestration.refusal_context import RefusalContext
from moralstack.prompts.catalog import PromptSet, default_prompt_set

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RefusalGenerationResult:
    """Result of `generate_llm_safe_refusal_detailed`.

    Carries both the refusal text and the prompts actually sent to the LLM so
    callers (RefusalHandler, deliberation_runner) can persist them via
    `record_llm_call`. Persisting the synthetic prompts (instead of the bare
    user prompt) is what makes the refusal step inspectable in the UI and
    markdown export.

    Attributes:
        text: Final refusal text (post anti-leak guardrail).
        system_prompt: System prompt sent to the LLM.
        user_prompt: User-message prompt sent to the LLM.
        attempts: Number of LLM calls actually issued (1 or 2 with retry).
        leak_retried: Whether the anti-leak guardrail forced a second attempt.
        leaked_terms: Terms detected as leaks in the first attempt (empty when none).
    """

    text: str
    system_prompt: str
    user_prompt: str
    attempts: int = 1
    leak_retried: bool = False
    leaked_terms: tuple[str, ...] = field(default_factory=tuple)
    token_usage: TokenUsage = field(default_factory=lambda: TokenUsage(0, 0, 0, "missing"))


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
# Invariant: physical_harm / weapons_explosives_toxins MUST NOT map to "legal" —
# those are safety categories, not legal questions. Mapping a weapon-construction
# refusal to "legal" would surface a "consult an attorney" redirection, which is
# semantically wrong for harm/safety refusals.
# Q10 stays on "science" because its prevalent shape is fabrication of
# weapons / explosives / toxins, which the violent_crime overlay explicitly
# excludes from its scope (WMD, CBRN, firearm/ammunition manufacture). The
# science overlay's refusal_redirection points to OPCW, biosafety, and lab
# safety resources, which is the right surface for that case. Hybrid requests
# ("use a weapon to commit a crime") activate Q5 alongside Q10 and therefore
# still land on violent_crime via the Q5 mapping.
SIGNAL_DOMAIN_MAP: dict[str, str] = {
    "Q5:physical_harm": "violent_crime",
    "Q9:cyber_malware": "cybersecurity",
    "Q10:weapons_explosives_toxins": "science",
    "Q11:privacy_doxxing_stalking": "cybersecurity",
    "Q12:medical_harmful": "medical",
    "Q4:fraud": "financial",
    "Q8:self_harm_suicide": "mental_health",
    "Q17:minor_exploitation": "children",
}

# Maximum number of characters of the risk-estimator rationale fed into the
# refusal-generation prompt. Older code truncated to 600 which lost the
# semantically richest grounding signal; 1500 keeps the full reasoning while
# still bounding token usage.
_RATIONALE_MAX_LEN = 1500

# Cyber/intrusion vocabulary the LLM must NOT introduce when harm_type clearly
# indicates a non-cyber harm (reputational, emotional_relational). Used by the
# post-LLM anti-leak guardrail to detect Q9 leakage into refusal text.
_CYBER_LEAK_TERMS = (
    "cyber abuse",
    "unauthorized access",
    "unauthorised access",
    "hacking",
    "malware",
    "intrusion",
    "exploit",
    "credential theft",
    "phishing",
    "system bypass",
    "security bypass",
)

# Mapping harm_type → human-readable broad unsafe category phrasing used to
# anchor the refusal's first sentence. Keeps the LLM from picking unrelated
# categories from a flat list of examples. The texts live in
# moralstack/prompts/data/refusal.yaml (``refusal.category_hints.<harm_type>``); this name stays
# importable as a default re-export. Runtime code reads the ``PromptSet`` it was given.
_HARM_TYPE_CATEGORIES: dict[str, list[str]] = {
    harm_type: list(default_prompt_set().lines(f"refusal.category_hints.{harm_type}"))
    for harm_type in (
        "reputational",
        "financial",
        "physical",
        "child_exploitation",
        "illegal",
        "emotional_relational",
    )
}


def _iso_to_language_name(iso: str | None) -> str:
    """Mappa codice ISO 639-1 (da risk_estimation.detected_language) a nome lingua."""
    if not iso or not str(iso).strip():
        return "English"
    # Supporta sia "it" che "it-IT"
    code = str(iso).strip().lower().split("-")[0][:2]
    return _ISO_TO_LANGUAGE.get(code, "English")


# Minimum confidence required to accept langdetect's verdict.
# Below this threshold, fall back to English to avoid mis-detection on
# short prompts containing proper nouns (e.g. "Samuel: Who paid?" -> 'es' @ 0.857).
_LANGDETECT_MIN_CONFIDENCE = 0.95

# Minimum prompt length (in characters AND in words) to even attempt detection.
# langdetect is unreliable on short inputs and tends to mis-classify based on
# statistical priors over proper nouns rather than actual lexical content.
_LANGDETECT_MIN_CHARS = 50
_LANGDETECT_MIN_WORDS = 5


def _detect_language_fallback(prompt: str | None) -> str:
    """
    Fallback when Risk Estimator does not provide detected_language.

    Uses langdetect (local, zero-token) but with conservative thresholds to
    avoid mis-detection on short inputs. The detection layer is the LLM-based
    intent estimator; this fallback exists only for the policy speculative
    path that runs before the intent estimator.

    Heuristics:
    - Prompt must be >= 50 chars AND >= 5 words to even attempt detection.
    - Detection must reach >= 0.95 confidence to be accepted.
    - Any failure path falls back to "English" (safe default).
    """
    if not prompt or not str(prompt).strip():
        return "English"

    text = str(prompt).strip()
    if len(text) < _LANGDETECT_MIN_CHARS:
        return "English"

    words = [w for w in text.split() if w.strip()]
    if len(words) < _LANGDETECT_MIN_WORDS:
        return "English"

    try:
        from langdetect import DetectorFactory, detect_langs

        DetectorFactory.seed = 42
        candidates = detect_langs(text)
        if not candidates:
            return "English"

        # detect_langs returns a list sorted by probability descending.
        top = candidates[0]
        top_iso = str(top.lang).strip().lower()
        top_prob = float(top.prob)

        if top_prob < _LANGDETECT_MIN_CONFIDENCE:
            logger.debug(
                "_detect_language_fallback: low confidence %s=%.3f for text len=%d, falling back to English",
                top_iso,
                top_prob,
                len(text),
            )
            return "English"

        return _ISO_TO_LANGUAGE.get(top_iso, "English")
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


def _normalize_refusal_domain(domain: str | None) -> str | None:
    """
    Normalize a refusal domain candidate.

    `core` is a retrieval-only pseudo-domain and must never guide refusal
    redirection: it would expose an internal implementation detail to the user
    and `core` has no `refusal_redirection` overlay anyway. Returns None for
    empty/whitespace values or for "core".
    """
    if not domain:
        return None
    value = str(domain).strip()
    if not value or value == "core":
        return None
    return value


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
    1) request_domain (normalized)
    2) detected_domain (normalized)
    3) "general" fallback

    Does NOT infer new domains from risk signals and does NOT rerun domain detection.
    """
    domain = _normalize_refusal_domain(request_domain) or _normalize_refusal_domain(detected_domain) or "general"

    refusal_redirection = ""
    if domain != "general" and constitution_store is not None:
        try:
            constitution = constitution_store.get_constitution(domain)
            overlay = getattr(constitution, "active_overlay", None)
            if overlay is not None:
                refusal_redirection = getattr(overlay, "refusal_redirection", "") or ""
        except Exception as e:
            logger.warning(
                "resolve_refusal_domain_and_redirection: failed to read overlay redirection for domain=%s: %s",
                domain,
                e,
            )
    return domain, refusal_redirection


def _build_system_message(prompt_set: PromptSet | None = None) -> str:
    return (prompt_set or default_prompt_set()).text("refusal.system")


def _format_harm_type_category_hints(harm_type: str, prompt_set: PromptSet | None = None) -> str:
    """Render harm_type-aligned category hints; falls back to a coarse list.

    The LLM is asked to pick a broad unsafe-action category for the first
    sentence of the refusal. Showing categories that match the harm_type
    prevents the previous failure mode where it picked an unrelated entry
    (e.g. "cyber abuse or unauthorized access") from a flat list.
    """
    ps = prompt_set or default_prompt_set()
    ht = (harm_type or "").lower().strip()
    fragment_id = f"refusal.category_hints.{ht}"
    # "fallback" is the generic list itself: it is used when harm_type is unset, "none" or unknown.
    bullets = ps.lines(fragment_id) if ht and ht != "fallback" and ps.has(fragment_id) else ()
    if not bullets:
        # as before: an unknown harm_type, or a hint list an override emptied, uses the generic list
        bullets = ps.lines("refusal.category_hints.fallback")
    return "\n".join(f"    - {b}" for b in bullets)


def _build_user_message(
    user_prompt: str,
    risk_category: str,
    policy_reason_codes: list[str],
    language: str,
    domain: str,
    rationale: str | None = None,
    refusal_redirection: str = "",
    refusal_context: RefusalContext | None = None,
    leak_avoidance_terms: tuple[str, ...] = (),
    prompt_set: PromptSet | None = None,
) -> str:
    ps = prompt_set or default_prompt_set()
    reasons_str = ", ".join(policy_reason_codes) if policy_reason_codes else "policy_decision"
    domain_str = domain.strip() if domain else "general"

    context_block = ""
    redirection_guidance = ""
    harm_type_for_hints = ""

    if refusal_context is not None:
        redirection_guidance = (
            refusal_context.refusal_redirection
            or refusal_context.safe_redirection_guidance
            or ps.text("refusal.default_redirection")
        )

        rationale_text = refusal_context.rationale or rationale or ""
        rationale_text = rationale_text[:_RATIONALE_MAX_LEN]
        harm_type_for_hints = refusal_context.harm_type or ""

        context_block = ps.format(
            "refusal.evidence_block",
            harm_type=refusal_context.harm_type or "unknown",
            request_type=refusal_context.request_type or "unknown",
            rationale_text=rationale_text or "(empty)",
            safe_refusal_focus=refusal_context.safe_refusal_focus,
            operational_risk=refusal_context.operational_risk or "unknown",
            requested_instructions=refusal_context.requested_instructions,
            intent_operational=refusal_context.intent_operational,
            redirection_guidance=redirection_guidance,
        )
    else:
        # Backward compatibility block
        rationale_block = ""
        if rationale and str(rationale).strip():
            rationale_block = ps.format("refusal.legacy_rationale_block", rationale=rationale.strip()[:400])

        domain_topic_block = ""
        if domain_str != "general":
            redirection = refusal_redirection.strip() if refusal_redirection else ""
            domain_topic_block = ps.format("refusal.legacy_domain_block", domain=domain_str)
            if redirection:
                domain_topic_block += ps.format(
                    "refusal.legacy_domain_redirection", domain=domain_str, redirection=redirection
                )
        context_block = f"{rationale_block}{domain_topic_block}"

    request_context_block = ""
    if str(user_prompt).strip():
        request_context_block = ps.format("refusal.request_context_block", user_prompt=str(user_prompt).strip())

    leak_avoidance_block = ""
    if leak_avoidance_terms:
        leak_avoidance_block = ps.format("refusal.leak_avoidance_block", terms=", ".join(leak_avoidance_terms))

    return ps.format(
        "refusal.user",
        request_context_block=request_context_block,
        risk_category=risk_category,
        reasons_str=reasons_str,
        domain_str=domain_str,
        context_block=context_block,
        leak_avoidance_block=leak_avoidance_block,
        category_hints=_format_harm_type_category_hints(harm_type_for_hints, ps),
        language=language,
    )


class _LLMGenerateProtocol(Protocol):
    """Protocollo minimo: solo generate(prompt, system)."""

    def generate(self, prompt: str, system: str = "", config: Any = None) -> Any: ...


def _detect_refusal_leaks(
    text: str,
    refusal_context: RefusalContext | None,
    rationale: str | None,
) -> tuple[str, ...]:
    """Detect cyber/intrusion vocabulary leaked into a refusal that should not contain it.

    Returns an ordered tuple of leaked terms (lower-case). Empty when grounding is fine.

    The check fires only when the harm_type clearly indicates a non-cyber
    harm (reputational, emotional_relational): in those cases, terms like
    "cyber abuse" or "unauthorized access" almost certainly come from the
    Q9 over-trigger described in the bug analysis. When the rationale itself
    contains the term, the refusal is allowed to use it (no leak).
    """
    if not text or refusal_context is None:
        return ()
    ht = (refusal_context.harm_type or "").lower().strip()
    if ht not in ("reputational", "emotional_relational"):
        return ()
    text_lc = text.lower()
    rationale_lc = (refusal_context.rationale or rationale or "").lower()
    leaked = tuple(t for t in _CYBER_LEAK_TERMS if t in text_lc and t not in rationale_lc)
    return leaked


def _llm_refusal_call(
    *,
    llm_client: _LLMGenerateProtocol,
    system: str,
    user_msg: str,
) -> tuple[str, TokenUsage]:
    """Single LLM round-trip for refusal generation. Returns stripped text and token usage."""
    try:
        result = llm_client.generate(prompt=user_msg, system=system)
        text = getattr(result, "text", None) or (str(result) if result else "")
        return (text or "").strip(), TokenUsage.from_generation_result(result)
    except Exception as e:
        logger.warning(
            "generate_llm_safe_refusal: LLM fallito, uso fallback: %s",
            str(e)[:100],
        )
        return "", TokenUsage(0, 0, 0, "missing")


def generate_llm_safe_refusal_detailed(
    user_prompt: str,
    risk_category: str,
    policy_reason_codes: list[str],
    language: str,
    domain: str,
    llm_client: _LLMGenerateProtocol | None = None,
    rationale: str | None = None,
    refusal_redirection: str = "",
    refusal_context: RefusalContext | None = None,
    prompt_set: PromptSet | None = None,
) -> RefusalGenerationResult:
    """Generate the refusal and return its full provenance.

    Produces the same refusal text as `generate_llm_safe_refusal` but exposes
    the synthetic system + user prompts actually sent to the LLM, so callers
    can persist them via `record_llm_call` and surface them in UI / markdown
    export. Also runs a post-LLM anti-leak guardrail: when the response
    contains cyber-vocabulary terms incompatible with the harm_type, a single
    retry is issued instructing the model to avoid those terms.

    See `generate_llm_safe_refusal` for the parameter semantics. ``prompt_set`` is the source of every
    prompt text (None = packaged defaults).
    """
    ps = prompt_set or default_prompt_set()
    risk_cat = (risk_category or "").strip() or "classified_as_harmful"
    reason_codes = list(policy_reason_codes) if policy_reason_codes else ["policy_decision"]
    lang = (language or "").strip() or "English"
    dom = (domain or "").strip() or "general"
    rational = (rationale or "").strip() or None

    system = _build_system_message(ps)
    user_msg = _build_user_message(
        str(user_prompt or ""),
        risk_cat,
        reason_codes,
        lang,
        dom,
        rationale=rational,
        refusal_redirection=refusal_redirection,
        refusal_context=refusal_context,
        prompt_set=ps,
    )

    if llm_client is None:
        return RefusalGenerationResult(
            text=_fallback_refusal(),
            system_prompt=system,
            user_prompt=user_msg,
            attempts=0,
            token_usage=TokenUsage(0, 0, 0, "missing"),
        )

    text, first_usage = _llm_refusal_call(llm_client=llm_client, system=system, user_msg=user_msg)
    token_usages: list[TokenUsage] = [first_usage]
    attempts = 1

    leaked_terms = _detect_refusal_leaks(text, refusal_context, rational)
    leak_retried = False
    final_user_msg = user_msg

    if leaked_terms and len(text) > 80:
        # Re-prompt once, asking the LLM to avoid the leaked vocabulary. The
        # rebuilt prompt includes the leak_avoidance_terms hint consumed by
        # `_build_user_message`.
        logger.warning(
            "refusal anti-leak guardrail: retrying — leaked terms=%s harm_type=%s",
            list(leaked_terms),
            getattr(refusal_context, "harm_type", None),
        )
        retry_user_msg = _build_user_message(
            str(user_prompt or ""),
            risk_cat,
            reason_codes,
            lang,
            dom,
            rationale=rational,
            refusal_redirection=refusal_redirection,
            refusal_context=refusal_context,
            leak_avoidance_terms=leaked_terms,
            prompt_set=ps,
        )
        retry_text, retry_usage = _llm_refusal_call(llm_client=llm_client, system=system, user_msg=retry_user_msg)
        token_usages.append(retry_usage)
        attempts += 1
        leak_retried = True
        if len(retry_text) > 80:
            text = retry_text
            final_user_msg = retry_user_msg
        else:
            logger.warning("refusal anti-leak retry produced short output; keeping first attempt")

    if not text or len(text) <= 80:
        if text:
            logger.warning("generate_llm_safe_refusal: output troppo breve (<=80 char), uso fallback")
        return RefusalGenerationResult(
            text=_fallback_refusal(),
            system_prompt=system,
            user_prompt=final_user_msg,
            attempts=attempts,
            leak_retried=leak_retried,
            leaked_terms=leaked_terms,
            token_usage=TokenUsage.combine(token_usages),
        )

    return RefusalGenerationResult(
        text=text,
        system_prompt=system,
        user_prompt=final_user_msg,
        attempts=attempts,
        leak_retried=leak_retried,
        leaked_terms=leaked_terms,
        token_usage=TokenUsage.combine(token_usages),
    )


def generate_llm_safe_refusal(
    user_prompt: str,
    risk_category: str,
    policy_reason_codes: list[str],
    language: str,
    domain: str,
    llm_client: _LLMGenerateProtocol | None = None,
    rationale: str | None = None,
    refusal_redirection: str = "",
    refusal_context: RefusalContext | None = None,
    prompt_set: PromptSet | None = None,
) -> str:
    """
    Genera la forma linguistica del rifiuto tramite LLM.

    Wrapper backward-compatible su `generate_llm_safe_refusal_detailed`:
    nuovi caller dovrebbero usare la versione detailed per ottenere anche i
    prompt sintetici (utili per la persistenza in log/UI/markdown).

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
    return generate_llm_safe_refusal_detailed(
        user_prompt=user_prompt,
        risk_category=risk_category,
        policy_reason_codes=policy_reason_codes,
        language=language,
        domain=domain,
        llm_client=llm_client,
        rationale=rationale,
        refusal_redirection=refusal_redirection,
        refusal_context=refusal_context,
        prompt_set=prompt_set,
    ).text


def _fallback_refusal() -> str:
    """Fallback sicuro se LLM non disponibile o fallisce. Language-agnostic marker."""
    return REFUSAL_FALLBACK_MARKER
