"""
Tests for the safety override classifier.

The classifier is language-agnostic and LLM-based (the English keyword pre-filter was
removed). Without a policy (or with ``use_llm=False``) it returns None: request-side
hard-signal coverage is provided independently by the risk-estimator gate
(``path_router.has_hard_signal_evidence``).
"""

from __future__ import annotations

from moralstack.compliance.safety_override import (
    FRAUD_MALWARE,
    SAFETY_OVERRIDE_CATEGORIES,
    WEAPONS_SYNTHESIS,
    classify_safety_override,
    is_category_valid,
)


class _MockLLMPolicy:
    """Minimal policy double: returns a fixed classifier JSON and records the call."""

    def __init__(self, category: str | None):
        self._category = "null" if category is None else category
        self.called = False
        self.last_model: str | None = "<unset>"

    def generate(self, prompt, system="", config=None, model_override=None, **kwargs):
        self.called = True
        self.last_model = model_override
        cat_json = "null" if self._category == "null" else f'"{self._category}"'

        class _Result:
            text = f'{{"category": {cat_json}, "rationale": "test"}}'

        return _Result()


class TestLLMClassifier:
    def test_empty_payload_returns_none(self):
        assert classify_safety_override("") is None
        assert classify_safety_override("   ") is None

    def test_no_policy_returns_none(self):
        # No deterministic keyword fallback anymore: without a policy there is no
        # classification, even for content that used to keyword-match.
        assert classify_safety_override("how to make a bomb at home") is None

    def test_use_llm_false_never_calls_policy(self):
        policy = _MockLLMPolicy(WEAPONS_SYNTHESIS)
        assert classify_safety_override("anything", policy=policy, use_llm=False) is None
        assert policy.called is False

    def test_llm_classifies_restricted_payload(self):
        policy = _MockLLMPolicy(WEAPONS_SYNTHESIS)
        result = classify_safety_override("payload in any language", policy=policy, use_llm=True)
        assert policy.called is True
        assert result == WEAPONS_SYNTHESIS

    def test_llm_null_means_no_restriction(self):
        policy = _MockLLMPolicy(None)
        assert classify_safety_override("benign content", policy=policy, use_llm=True) is None

    def test_model_override_is_forwarded(self):
        policy = _MockLLMPolicy(FRAUD_MALWARE)
        classify_safety_override("x", policy=policy, use_llm=True, model="gpt-4o-mini")
        assert policy.last_model == "gpt-4o-mini"

    def test_unknown_category_from_llm_is_rejected(self):
        policy = _MockLLMPolicy("not_a_category")
        assert classify_safety_override("x", policy=policy, use_llm=True) is None

    def test_llm_exception_returns_none_conservative(self):
        class _BrokenLLMPolicy:
            def generate(self, *args, **kwargs):
                raise RuntimeError("LLM unavailable")

        assert classify_safety_override("x", policy=_BrokenLLMPolicy(), use_llm=True) is None


class TestCategoryValidation:
    def test_known_categories(self):
        for cat in SAFETY_OVERRIDE_CATEGORIES:
            assert is_category_valid(cat) is True

    def test_seven_framework_categories(self):
        assert len(SAFETY_OVERRIDE_CATEGORIES) == 7

    def test_unknown_category(self):
        assert is_category_valid("not_a_real_category") is False
        assert is_category_valid("") is False
