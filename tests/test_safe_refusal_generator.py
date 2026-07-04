"""
Characterization tests for safe refusal generator.

Documents current behavior of _iso_to_language_name, _detect_language_fallback,
_fallback_refusal, and generate_llm_safe_refusal.
"""

from moralstack.orchestration.safe_refusal_generator import (
    REFUSAL_FALLBACK_MARKER,
    SIGNAL_DOMAIN_MAP,
    _detect_language_fallback,
    _fallback_refusal,
    _iso_to_language_name,
    _normalize_refusal_domain,
    generate_llm_safe_refusal,
    resolve_refusal_domain_and_redirection,
)


def test_iso_to_language_name_it():
    """Italian ISO code maps to Italian."""
    assert _iso_to_language_name("it") == "Italian"


def test_iso_to_language_name_en():
    """English ISO code maps to English."""
    assert _iso_to_language_name("en") == "English"


def test_iso_to_language_name_unknown():
    """Unknown ISO code falls back to English."""
    assert _iso_to_language_name("xx") == "English"


def test_iso_to_language_name_it_it():
    """Locale format it-IT extracts it and maps to Italian."""
    assert _iso_to_language_name("it-IT") == "Italian"


def test_iso_to_language_name_none_or_empty():
    """None or empty string returns English."""
    assert _iso_to_language_name(None) == "English"
    assert _iso_to_language_name("") == "English"
    assert _iso_to_language_name("   ") == "English"


def test_detect_language_fallback_short_prompt():
    """Prompt shorter than 10 chars returns English."""
    assert _detect_language_fallback("Hi") == "English"
    assert _detect_language_fallback("") == "English"
    assert _detect_language_fallback(None) == "English"


def test_fallback_refusal():
    """_fallback_refusal returns REFUSAL_FALLBACK_MARKER."""
    assert _fallback_refusal() == REFUSAL_FALLBACK_MARKER
    assert _fallback_refusal() == REFUSAL_FALLBACK_MARKER


def test_generate_llm_safe_refusal_no_client():
    """generate_llm_safe_refusal with no LLM client returns fallback marker."""
    result = generate_llm_safe_refusal(
        user_prompt="Harmful request",
        risk_category="clearly_harmful",
        policy_reason_codes=["risk_clearly_harmful"],
        language="Italian",
        domain="general",
        llm_client=None,
    )
    assert result == REFUSAL_FALLBACK_MARKER


def test_llm_refusal_call_returns_tuple_text_and_token_usage_on_success():
    from moralstack.observability.token_usage import TokenUsage
    from moralstack.orchestration.safe_refusal_generator import _llm_refusal_call

    class _Client:
        def generate(self, **kwargs):
            from moralstack.models.base import GenerationResult

            return GenerationResult(
                text="Refusal text long enough to pass the guardrail threshold easily.",
                tokens_used=12,
                finish_reason="stop",
                prompt_tokens=8,
                completion_tokens=4,
                token_usage_source="exact",
            )

    text, usage = _llm_refusal_call(llm_client=_Client(), system="sys", user_msg="user")
    assert "Refusal" in text
    assert isinstance(usage, TokenUsage)
    assert usage.total_tokens == 12


def test_generate_llm_safe_refusal_detailed_no_client_returns_missing_token_usage():
    from moralstack.orchestration.safe_refusal_generator import generate_llm_safe_refusal_detailed

    result = generate_llm_safe_refusal_detailed(
        user_prompt="x",
        risk_category="clearly_harmful",
        policy_reason_codes=["risk_clearly_harmful"],
        language="English",
        domain="general",
        llm_client=None,
    )
    assert result.token_usage.source == "missing"
    assert result.attempts == 0


# =============================================================================
# Refusal domain normalization
# =============================================================================
# `core` is a retrieval-only pseudo-domain; it must never guide refusal
# redirection (would surface implementation detail and has no overlay anyway).


def test_normalize_refusal_domain_strips_core():
    assert _normalize_refusal_domain("core") is None


def test_normalize_refusal_domain_strips_whitespace_core():
    assert _normalize_refusal_domain("  core  ") is None


def test_normalize_refusal_domain_passes_real_domain():
    assert _normalize_refusal_domain("legal") == "legal"
    assert _normalize_refusal_domain("  legal  ") == "legal"


def test_normalize_refusal_domain_none_and_empty():
    assert _normalize_refusal_domain(None) is None
    assert _normalize_refusal_domain("") is None
    assert _normalize_refusal_domain("   ") is None


def test_resolve_refusal_domain_core_detected_falls_back_to_general():
    """When detected_domain == 'core', resolver MUST NOT propagate it."""
    domain, redirection = resolve_refusal_domain_and_redirection(
        request_prompt="Some prompt",
        request_domain=None,
        detected_domain="core",
        risk_signals=None,
        constitution_store=None,
    )
    assert domain == "general"
    assert redirection == ""


def test_resolve_refusal_domain_core_request_falls_back_to_general():
    """When request_domain == 'core', resolver MUST NOT propagate it."""
    domain, redirection = resolve_refusal_domain_and_redirection(
        request_prompt="Some prompt",
        request_domain="core",
        detected_domain=None,
        risk_signals=None,
        constitution_store=None,
    )
    assert domain == "general"
    assert redirection == ""


def test_resolve_refusal_domain_no_longer_uses_signal_fallback():
    """Signals MUST NOT drive domain selection anymore."""
    domain, _ = resolve_refusal_domain_and_redirection(
        request_prompt="Some prompt",
        request_domain=None,
        detected_domain=None,
        risk_signals=["Q5:physical_harm"],
        constitution_store=None,
    )
    assert domain == "general"


def test_signal_domain_map_does_not_route_to_legal():
    """Invariant: physical_harm / weapons signals must NOT map to 'legal'."""
    assert SIGNAL_DOMAIN_MAP.get("Q5:physical_harm") != "legal"
    assert SIGNAL_DOMAIN_MAP.get("Q10:weapons_explosives_toxins") != "legal"
    assert SIGNAL_DOMAIN_MAP.get("Q17:minor_exploitation") != "legal"
    assert "legal" not in SIGNAL_DOMAIN_MAP.values()
