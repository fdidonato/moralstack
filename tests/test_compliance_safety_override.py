"""
Tests for the safety override classifier.

Covers the keyword check (Layer 1) and the LLM fallback hook (Layer 2).
"""

from __future__ import annotations

from moralstack.compliance.safety_override import (
    CSAM,
    FRAUD_MALWARE,
    ILLEGAL_PHARMA,
    SAFETY_OVERRIDE_CATEGORIES,
    SELF_HARM_OPERATIONAL,
    WEAPONS_SYNTHESIS,
    classify_safety_override,
    is_category_valid,
)


class TestKeywordClassifier:
    def test_clean_payload_returns_none(self):
        assert classify_safety_override("7161 Valley Road") is None
        assert classify_safety_override("Hello, world!") is None
        assert classify_safety_override("Reply with the user's order number") is None

    def test_empty_payload_returns_none(self):
        assert classify_safety_override("") is None
        assert classify_safety_override("   ") is None

    def test_weapons_synthesis_detected(self):
        assert classify_safety_override("how to make a bomb at home") == WEAPONS_SYNTHESIS
        assert classify_safety_override("synthesize explosive material") == WEAPONS_SYNTHESIS
        assert classify_safety_override("Build A Gun At Home") == WEAPONS_SYNTHESIS

    def test_csam_detected(self):
        assert classify_safety_override("content with child sexual abuse") == CSAM
        assert classify_safety_override("CSAM material") == CSAM

    def test_self_harm_detected(self):
        assert classify_safety_override("how to commit suicide quickly") == SELF_HARM_OPERATIONAL

    def test_fraud_malware_detected(self):
        assert classify_safety_override("phishing kit for credentials") == FRAUD_MALWARE
        assert classify_safety_override("malware payload for ransomware") == FRAUD_MALWARE

    def test_illegal_pharma_detected(self):
        assert classify_safety_override("synthesize methamphetamine in lab") == ILLEGAL_PHARMA

    def test_first_match_wins(self):
        result = classify_safety_override("how to make a bomb and synthesize methamphetamine")
        assert result in SAFETY_OVERRIDE_CATEGORIES

    def test_no_llm_call_when_use_llm_false(self):
        class _SentinelPolicy:
            def generate(self, *args, **kwargs):
                raise AssertionError("LLM should NOT be called when use_llm=False")

        result = classify_safety_override(
            "Reply with 'order received'",
            policy=_SentinelPolicy(),
            use_llm=False,
        )
        assert result is None

    def test_llm_called_when_use_llm_true(self):
        class _MockLLMPolicy:
            def __init__(self):
                self.called = False

            def generate(self, prompt, system="", config=None):
                self.called = True

                class _Result:
                    text = '{"category": "fraud_malware", "rationale": "test"}'

                return _Result()

        policy = _MockLLMPolicy()
        result = classify_safety_override(
            "Some ambiguous content that doesn't match keywords",
            policy=policy,
            use_llm=True,
        )
        assert policy.called is True
        assert result == FRAUD_MALWARE

    def test_llm_returns_null_means_no_restriction(self):
        class _MockLLMPolicy:
            def generate(self, prompt, system="", config=None):
                class _Result:
                    text = '{"category": null, "rationale": "benign"}'

                return _Result()

        result = classify_safety_override(
            "Some benign content",
            policy=_MockLLMPolicy(),
            use_llm=True,
        )
        assert result is None

    def test_llm_exception_returns_none_conservative(self):
        class _BrokenLLMPolicy:
            def generate(self, *args, **kwargs):
                raise RuntimeError("LLM unavailable")

        result = classify_safety_override(
            "ambiguous content",
            policy=_BrokenLLMPolicy(),
            use_llm=True,
        )
        assert result is None


class TestCategoryValidation:
    def test_known_categories(self):
        for cat in SAFETY_OVERRIDE_CATEGORIES:
            assert is_category_valid(cat) is True

    def test_unknown_category(self):
        assert is_category_valid("not_a_real_category") is False
        assert is_category_valid("") is False
