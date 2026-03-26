"""
Characterization tests for safe refusal generator.

Documents current behavior of _iso_to_language_name, _detect_language_fallback,
_fallback_refusal, and generate_llm_safe_refusal.
"""

from moralstack.orchestration.safe_refusal_generator import (
    REFUSAL_FALLBACK_MARKER,
    _detect_language_fallback,
    _fallback_refusal,
    _iso_to_language_name,
    generate_llm_safe_refusal,
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
        risk_category="clearly_harmful",
        policy_reason_codes=["risk_clearly_harmful"],
        language="Italian",
        domain="general",
        llm_client=None,
    )
    assert result == REFUSAL_FALLBACK_MARKER
