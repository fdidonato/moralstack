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
# categories from a flat list of examples.
_HARM_TYPE_CATEGORIES: dict[str, list[str]] = {
    "reputational": [
        "damaging someone's reputation through false or manipulative content",
        "running a deceptive online campaign against a third party",
        "coordinating online harassment or a smear campaign against an identifiable target",
    ],
    "financial": [
        "financial fraud or deception",
        "financial exploitation",
    ],
    "physical": [
        "causing physical harm",
        "making or using a weapon",
    ],
    "child_exploitation": [
        "contacting or grooming minors online",
        "obtaining scripts or persuasion tactics for inappropriate adult-minor interactions",
        "seeking facilitation of exploitation or sexualization of minors",
    ],
    "illegal": [
        "an unlawful action",
    ],
    "emotional_relational": [
        "manipulating or coercing someone in a personal relationship",
    ],
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


def _build_system_message() -> str:
    return (
        "You are an AI assistant responsible for generating safe refusal responses. "
        "Your task is to refuse a user request that has already been classified as harmful. "
        "You must never provide technical details, instructions, examples, or references "
        "that could enable harmful activity."
    )


def _format_harm_type_category_hints(harm_type: str) -> str:
    """Render harm_type-aligned category hints; falls back to a coarse list.

    The LLM is asked to pick a broad unsafe-action category for the first
    sentence of the refusal. Showing categories that match the harm_type
    prevents the previous failure mode where it picked an unrelated entry
    (e.g. "cyber abuse or unauthorized access") from a flat list.
    """
    ht = (harm_type or "").lower().strip()
    bullets = _HARM_TYPE_CATEGORIES.get(ht)
    if bullets:
        return "\n".join(f"    - {b}" for b in bullets)
    # Generic fallback used when harm_type is unset or "none"; keeps prior
    # examples available so the model has anchors.
    return (
        "    - making or using a weapon\n"
        "    - making an improvised weapon\n"
        "    - making an incendiary device\n"
        "    - making an explosive device\n"
        "    - making an explosive device more harmful or lethal\n"
        "    - increasing the lethality of a weapon\n"
        "    - forging prescriptions or medical documents\n"
        "    - obtaining prescription medication fraudulently\n"
        "    - misrepresenting symptoms to a medical professional\n"
        "    - unsafe use of illicit drugs\n"
        "    - cyber abuse or unauthorized access\n"
        "    - fraud or deception\n"
        "    - discriminatory content targeting a protected group\n"
        "    - harassment, doxxing, or privacy invasion\n"
        "    - damaging someone's reputation through false or manipulative content\n"
        "    - running a deceptive online campaign against a third party\n"
        "    - self-harm or suicide instructions"
    )


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
) -> str:
    reasons_str = ", ".join(policy_reason_codes) if policy_reason_codes else "policy_decision"
    domain_str = domain.strip() if domain else "general"

    context_block = ""
    redirection_guidance = ""
    harm_type_for_hints = ""

    if refusal_context is not None:
        redirection_guidance = (
            refusal_context.refusal_redirection
            or refusal_context.safe_redirection_guidance
            or "Redirect toward safe, lawful, constructive alternatives."
        )

        rationale_text = refusal_context.rationale or rationale or ""
        rationale_text = rationale_text[:_RATIONALE_MAX_LEN]
        harm_type_for_hints = refusal_context.harm_type or ""

        context_block = f"""
PRIMARY EVIDENCE — use these to ground the refusal:
- harm_type: {refusal_context.harm_type or "unknown"}
- request_type: {refusal_context.request_type or "unknown"}
- risk-estimator rationale: {rationale_text or "(empty)"}

SECONDARY EVIDENCE (supporting context only, lower weight):
- refusal focus: {refusal_context.safe_refusal_focus}
- operational risk: {refusal_context.operational_risk or "unknown"}
- requested instructions: {refusal_context.requested_instructions}
- intent operational: {refusal_context.intent_operational}

SAFE REDIRECTION GUIDANCE:
{redirection_guidance}

REFUSAL QUALITY REQUIREMENTS — mandatory:
The refusal must be both specific and convincing.

Specificity:
- The first sentence must name the concrete unsafe action category derived from
  PRIMARY EVIDENCE.
- Do not use vague categories such as "unsafe content", "harmful request",
  "unlawful actions", or "policy violation" if PRIMARY EVIDENCE supports a more
  concrete category.
- Ground the refusal in the risk-estimator rationale, harm_type, request_type,
  and activated signals.
- Do not introduce unrelated risk categories.
- Do not mention wrapper-only features such as article format, JSON format,
  roleplay, fictional framing, or creative style unless they are relevant to
  evasion or concealment.

Convincingness:
- Clearly explain why complying would be unsafe, unlawful, deceptive, or harmful
  in practical terms.
- The explanation must be understandable without exposing operational details.
- If the request includes concealment, evasion, optimization, or platform-bypass
  framing, mention that broad goal safely.
- Provide a safe redirection that addresses the same underlying need without
  enabling the harmful action.
- Do not use generic redirections. Redirections must follow PRIMARY EVIDENCE,
  not the surface domain, when the two conflict.

GROUNDING RULES (mandatory):
A. Name a broad unsafe-action category that is consistent with PRIMARY EVIDENCE
   (harm_type and rationale). Do NOT introduce categories absent from BOTH.
B. If harm_type=reputational, do NOT use cyber, hacking, intrusion, malware,
   or unauthorized-access language unless the rationale explicitly mentions
   technical compromise of systems or accounts.
C. If harm_type=financial without an explicit fraud signal in the evidence,
   frame as financial harm or exploitation; do not assume "fraud or deception"
   unless the rationale supports it.
D. The redirection must address the same underlying need expressed in the
   request:
     • reputational → fair competitive practices, honest marketing,
       legitimate differentiation, transparent communication
     • cyber → defensive security, authorized testing
     • weapons/physical → de-escalation, emergency services
     • self-harm → crisis support and trusted contacts
     • privacy → consent, lawful reporting channels
     • financial → lawful financial conduct, consumer protection
E. Do not reveal internal labels, signal names (Qx:...), policy codes, risk
   scores, estimator names, or pipeline details.
F. When refusing predilige well written and safe convincingness and specificity
"""
    else:
        # Backward compatibility block
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
        context_block = f"{rationale_block}{domain_topic_block}"

    request_context_block = ""
    if str(user_prompt).strip():
        request_context_block = (
            "Original user request context (relevance only; never provide operational details):\n"
            f"{str(user_prompt).strip()}\n\n"
        )

    leak_avoidance_block = ""
    if leak_avoidance_terms:
        joined = ", ".join(leak_avoidance_terms)
        leak_avoidance_block = (
            "\nA previous attempt mentioned terms that are NOT supported by the evidence:\n"
            f"{joined}\n"
            "Do not use these terms unless they appear in the rationale above.\n"
        )

    category_hints = _format_harm_type_category_hints(harm_type_for_hints)

    return (
        f"{request_context_block}"
        f"The user's request has been classified as:\n"
        f"- Risk category: {risk_category}\n"
        f"- Policy reasons: {reasons_str}\n"
        f"- Context domain: {domain_str}\n"
        f"{context_block}"
        f"{leak_avoidance_block}"
        "Write a refusal response that:\n\n"
        "1. Clearly refuses the unsafe request.\n"
        "2. Name the broad unsafe action category in the first sentence.\n"
        "   The category must be concrete enough that the refusal makes sense\n"
        "   without reading the original prompt, and consistent with PRIMARY\n"
        "   EVIDENCE (harm_type + rationale). Categories aligned with the\n"
        "   detected harm_type:\n"
        f"{category_hints}\n"
        "3. Does not repeat operational details, tool combinations, procedures, "
        "payloads, substances, or step-by-step content from the original request.\n"
        "4. Explains the refusal briefly based on safety, ethics, or lawfulness.\n"
        "5. Provides a safe redirection aligned with the user's apparent underlying context "
        "and the SAFE REDIRECTION GUIDANCE above:\n"
        "- Explain the concrete harm class.\n"
        "- Redirect to a safe alternative aligned with the same user need/context.\n"
        "6. If domain-specific refusal_redirection is present, use it. "
        "Otherwise do not invent a domain and do not redirect to unrelated topics.\n"
        "7. If the request asks to intensify, optimize, improve, conceal, evade detection, "
        "or make a harmful action more effective, the refusal must mention that broad "
        "optimization goal safely.\n"
        "    Allowed:\n"
        "    'I can't help make an explosive device more harmful or lethal.'\n"
        "    'I can't help make fraud more convincing.'\n"
        "    'I can't help make a smear campaign more damaging.'\n"
        "    Forbidden:\n"
        "    Mentioning tools, materials, parameters, steps, payloads, document fields, "
        "symptoms, or operational techniques.\n"
        "8. Use professional, non-judgmental language.\n"
        f"9. Write entirely in {language}.\n\n"
        "The response must be self-contained and safe."
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
) -> RefusalGenerationResult:
    """Generate the refusal and return its full provenance.

    Produces the same refusal text as `generate_llm_safe_refusal` but exposes
    the synthetic system + user prompts actually sent to the LLM, so callers
    can persist them via `record_llm_call` and surface them in UI / markdown
    export. Also runs a post-LLM anti-leak guardrail: when the response
    contains cyber-vocabulary terms incompatible with the harm_type, a single
    retry is issued instructing the model to avoid those terms.

    See `generate_llm_safe_refusal` for the parameter semantics.
    """
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
        refusal_context=refusal_context,
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
    ).text


def _fallback_refusal() -> str:
    """Fallback sicuro se LLM non disponibile o fallisce. Language-agnostic marker."""
    return REFUSAL_FALLBACK_MARKER
