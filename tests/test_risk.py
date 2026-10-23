"""
Tests for LLMBasedRiskEstimator (Semantic Judge).

Verifies the correct behavior of the ethical risk classifier
based on semantic analysis (without keyword matching).
"""

import os
from dataclasses import dataclass
from typing import Any

import pytest

from moralstack.models.risk import (
    JSONParseError,
    LLMBasedRiskEstimator,
    RiskCategory,
    RiskEstimation,
    RiskEstimatorConfig,
    RiskParseResult,
    RiskPolicyAction,
    extract_json,
)
from moralstack.models.risk.calibration import parse_risk_dict

# Default config for tests (avoids load_risk_estimator_config_from_env per test)
_DEFAULT_TEST_CONFIG = RiskEstimatorConfig()


def _parse_risk_text(text: str) -> RiskParseResult:
    """Inline of the removed ``parse_risk_response`` wrapper: ``parse_risk_dict(extract_json(text))``."""
    return parse_risk_dict(extract_json(text))


@pytest.fixture(scope="module", autouse=True)
def _risk_test_env():
    """
    Isolate test_risk when run in full suite (pytest tests/).

    Unset MORALSTACK_DB_PATH so the risk estimator's persistence path
    never touches the shared in-memory DB (avoids cross-test interaction/lock).
    Set a short orchestrator timeout in case any import reads it.
    """
    old_db = os.environ.get("MORALSTACK_DB_PATH")
    old_timeout = os.environ.get("MORALSTACK_ORCHESTRATOR_TIMEOUT_MS")
    os.environ.pop("MORALSTACK_DB_PATH", None)
    os.environ["MORALSTACK_ORCHESTRATOR_TIMEOUT_MS"] = "10000"
    yield
    if old_db is not None:
        os.environ["MORALSTACK_DB_PATH"] = old_db
    else:
        os.environ.pop("MORALSTACK_DB_PATH", None)
    if old_timeout is not None:
        os.environ["MORALSTACK_ORCHESTRATOR_TIMEOUT_MS"] = old_timeout
    else:
        os.environ.pop("MORALSTACK_ORCHESTRATOR_TIMEOUT_MS", None)


@pytest.fixture(scope="module")
def shared_conservative_estimator():
    """Shared policy-less estimator (fails closed via from_error) for tests that don't mutate it."""
    return LLMBasedRiskEstimator(policy=None, config=_DEFAULT_TEST_CONFIG)


@pytest.fixture(scope="module")
def shared_mock_llm():
    """Shared MockPolicyLLM for tests that customize responses in test body."""
    return MockPolicyLLM()


@pytest.fixture(scope="module")
def shared_estimator_with_mock(shared_mock_llm):
    """Shared LLM-based estimator for tests that use shared_mock_llm."""
    return LLMBasedRiskEstimator(policy=shared_mock_llm, config=_DEFAULT_TEST_CONFIG)


@pytest.fixture
def reset_mock_llm(shared_mock_llm):
    """Resets shared_mock_llm before each test (call_count, responses, last_prompt)."""
    shared_mock_llm.call_count = 0
    shared_mock_llm.responses = {}
    shared_mock_llm.last_prompt = ""
    shared_mock_llm.last_messages = []


# =============================================================================
# Mock Policy LLM
# =============================================================================


@dataclass
class MockGenerationResult:
    """Mock result for Policy LLM."""

    text: str
    tokens_used: int = 100
    finish_reason: str = "stop"


class MockPolicyLLM:
    """
    Mock Policy LLM for testing the Semantic Judge.

    Simulates semantic analysis by returning JSON responses based on the intent
    detected in the prompt (not on literal keyword matching).
    """

    def __init__(self, responses: dict[str, str] | None = None):
        """
        Args:
            responses: Map pattern -> JSON response (for specific tests)
        """
        self.responses = responses or {}
        self.call_count = 0
        self.last_prompt = ""
        self.last_messages: list[dict[str, str]] = []

    def generate(self, *args: Any, **kwargs: Any) -> MockGenerationResult:
        """
        Simulates semantic analysis of the prompt.

        The mock analyzes the request's intent, not specific words.
        """
        self.call_count += 1
        prompt = kwargs.get("prompt", args[0] if args else "")
        self.last_prompt = prompt

        # Check for custom responses configured in tests
        for pattern, response in self.responses.items():
            if pattern.lower() in prompt.lower():
                return MockGenerationResult(text=response)

        # Simulates semantic analysis based on intent
        prompt_lower = prompt.lower()

        # Intent to cause physical or illegal harm
        if self._detects_harmful_intent(prompt_lower):
            return MockGenerationResult(text="""{
                "q1_confidential": "no",
                "q2_unauthorized": "no",
                "q3_breach": "no",
                "q4_fraud": "no",
                "q5_physical": "yes",
                "q6_illegal_items": "yes",
                "q7_only_emotional": "no",
                "request_type": "clear_harm",
                "harm_type": "physical",
                "risk_score": 0.95,
                "confidence": 0.9,
                "domain_sensitivity": "LOW",
                "operational_risk": "HIGH",
                "rationale": "Semantic analysis: request seeks to cause harm",
                "detected_language": "en"
            }""")

        # Sensitive topics requiring attention
        if self._detects_sensitive_topic(prompt_lower):
            return MockGenerationResult(text="""{
                "q1_confidential": "no",
                "q2_unauthorized": "no",
                "q3_breach": "no",
                "q4_fraud": "no",
                "q5_physical": "no",
                "q6_illegal_items": "no",
                "q7_only_emotional": "yes",
                "request_type": "sensitive_topic",
                "harm_type": "emotional_relational",
                "risk_score": 0.45,
                "confidence": 0.85,
                "domain_sensitivity": "MEDIUM",
                "operational_risk": "LOW",
                "risk_policy_action": "DELIBERATE",
                "rationale": "Semantic analysis: sensitive topic requiring careful handling"
            }""")

        # Default: benign request
        return MockGenerationResult(text="""{
            "q1_confidential": "no",
            "q2_unauthorized": "no",
            "q3_breach": "no",
            "q4_fraud": "no",
            "q5_physical": "no",
            "q6_illegal_items": "no",
            "q7_only_emotional": "no",
            "request_type": "factual_query",
            "harm_type": "none",
            "risk_score": 0.1,
            "confidence": 0.95,
            "domain_sensitivity": "LOW",
            "operational_risk": "NONE",
            "risk_policy_action": "ALLOW",
            "rationale": "Semantic analysis: standard informational request"
        }""")

    def generate_messages(self, *, messages: list[dict[str, str]], config: Any = None) -> MockGenerationResult:
        self.last_messages = messages
        prompt = messages[-1]["content"] if messages else ""
        return self.generate(prompt=prompt, system=messages[0]["content"] if messages else "", config=config)

    def _detects_harmful_intent(self, text: str) -> bool:
        """Simulates detection of harmful intent (not keyword matching)."""
        # Intent to build/create dangerous objects
        harmful_intents = [
            "how to build a bomb",
            "how to make a bomb",
            "how to kill",
            "how to hack into",
            "how to steal",
            "instructions to harm",
        ]
        return any(intent in text for intent in harmful_intents)

    def _detects_sensitive_topic(self, text: str) -> bool:
        """Simulates detection of a sensitive topic."""
        sensitive_topics = [
            "struggling with depression",
            "feeling suicidal",
            "need help with medication",
            "mental health",
            "lie or tell the truth",
            "don't like their cooking",
            "euthanasia",
            "eat meat",
            "suffer psychologically",
            "symptoms of depression",
        ]
        return any(topic in text for topic in sensitive_topics)


class FailingMockPolicyLLM:
    """Mock that always fails (to test error handling)."""

    def __init__(self, fail_mode: str = "exception"):
        self.fail_mode = fail_mode
        self.call_count = 0

    def generate(self, *args: Any, **kwargs: Any) -> MockGenerationResult:
        self.call_count += 1

        if self.fail_mode == "exception":
            raise RuntimeError("LLM unavailable")
        elif self.fail_mode == "invalid_json":
            return MockGenerationResult(text="This is not valid JSON at all!")
        else:
            return MockGenerationResult(text="")


# =============================================================================
# Test RiskEstimation Dataclass
# =============================================================================


class TestRiskEstimation:
    """Tests for RiskEstimation dataclass."""

    def test_clamp_score(self):
        """Score is clamped to [0, 1]."""
        estimation = RiskEstimation(
            score=1.5,
            confidence=0.8,
            risk_category=RiskCategory.BENIGN,
        )
        assert estimation.score == 1.0

        estimation2 = RiskEstimation(
            score=-0.5,
            confidence=0.8,
            risk_category=RiskCategory.BENIGN,
        )
        assert estimation2.score == 0.0

    def test_clamp_confidence(self):
        """Confidence is clamped to [0, 1]."""
        estimation = RiskEstimation(
            score=0.5,
            confidence=2.0,
            risk_category=RiskCategory.SENSITIVE,
        )
        assert estimation.confidence == 1.0

    def test_benign_factory(self):
        """Test benign factory method."""
        estimation = RiskEstimation.benign()
        assert estimation.score == 0.1
        assert estimation.risk_category == RiskCategory.BENIGN

    def test_clearly_harmful_factory(self):
        """Test clearly_harmful factory method."""
        signals = ["harmful_intent", "physical_harm"]
        estimation = RiskEstimation.clearly_harmful(signals)
        assert estimation.score == 0.95
        assert estimation.risk_category == RiskCategory.CLEARLY_HARMFUL
        assert estimation.semantic_signals == signals

    def test_from_error_factory(self):
        """Test from_error factory method (requires deliberation)."""
        estimation = RiskEstimation.from_error("Test error")
        # Conservative fallback requires deliberation
        assert estimation.score == 0.6
        assert estimation.confidence == 0.4
        assert estimation.risk_category == RiskCategory.SENSITIVE
        assert "SYSTEM.REQUIRES_DELIBERATION" in estimation.semantic_signals


# =============================================================================
# Test JSON Parsing
# =============================================================================


class TestJSONParsing:
    """Tests for JSON parsing functions."""

    def test_extract_json_direct(self):
        """Direct JSON parsing."""
        text = '{"key": "value", "number": 42}'
        result = extract_json(text)
        assert result["key"] == "value"
        assert result["number"] == 42

    def test_extract_json_with_surrounding_text(self):
        """JSON parsing with surrounding text."""
        text = 'Here is the result: {"risk_score": 0.5} end'
        result = extract_json(text)
        assert result["risk_score"] == 0.5

    def test_extract_json_markdown_code_block(self):
        """JSON parsing in markdown block."""
        text = """Here is the analysis:
```json
{"category": "benign", "score": 0.2}
```
Done."""
        result = extract_json(text)
        assert result["category"] == "benign"

    def test_extract_json_trailing_comma(self):
        """JSON parsing with trailing comma."""
        text = '{"key": "value",}'
        result = extract_json(text)
        assert result["key"] == "value"

    def test_extract_json_invalid_raises(self):
        """Invalid JSON raises an exception."""
        text = "This is not JSON at all"
        with pytest.raises(JSONParseError):
            extract_json(text)

    def test_parse_risk_response_complete(self):
        """Parsing a complete risk estimator response."""
        # intent_operational=true avoids intent_contradiction downgrade (op_risk HIGH + all intent false)
        text = """{
            "risk_score": 0.75,
            "confidence": 0.85,
            "request_type": "potential_harm",
            "harm_type": "illegal",
            "signals": ["violence", "weapons"],
            "rationale": "Contains concerning content",
            "domain_sensitivity": "HIGH",
            "operational_risk": "HIGH",
            "intent_operational": true
        }"""

        out = _parse_risk_text(text)
        assert isinstance(out, RiskParseResult)
        assert out.score == 0.75
        assert out.confidence == 0.85
        assert out.risk_policy_action == RiskPolicyAction.DENY
        assert out.category == RiskCategory.POTENTIALLY_HARMFUL
        # signals are derived from request_type and harm_type when no Q-YES; legacy format
        # would use JSON "signals" only when request_type/harm_type are missing
        assert out.domain_sensitivity.name == "HIGH"
        assert out.operational_risk.name == "HIGH"

    def test_parse_risk_response_missing_fields(self):
        """Parsing with missing fields uses defaults."""
        text = '{"risk_score": 0.3, "request_type": "factual_query", "harm_type": "none"}'

        out = _parse_risk_text(text)
        # With factual_query and none, score is clamped to 0.15 when > 0.15
        assert out.score == 0.15
        assert out.category == RiskCategory.BENIGN
        assert out.risk_policy_action == RiskPolicyAction.ALLOW

    def test_parse_risk_response_invalid_category(self):
        """Invalid category uses default."""
        text = '{"category": "invalid_category"}'

        out = _parse_risk_text(text)
        assert out.category == RiskCategory.SENSITIVE

    def test_parse_risk_response_crisis_help_seeking_returns_booleans(self):
        """Parse with Q8 yes but requested_instructions/intent_to_harm no: returns
        self_harm_language True, requested_instructions/intent_to_harm False."""
        text = """{
            "q8_self_harm_suicide": "yes",
            "requested_instructions": "no",
            "intent_to_harm": "no",
            "request_type": "sensitive_topic",
            "harm_type": "emotional_relational",
            "risk_score": 0.7,
            "confidence": 0.8,
            "domain_sensitivity": "MEDIUM",
            "operational_risk": "NONE",
            "risk_policy_action": "DELIBERATE"
        }"""
        out = _parse_risk_text(text)
        assert out.self_harm_language is True
        assert out.requested_instructions is False
        assert out.intent_to_harm is False

    def test_parse_risk_response_ethics_hacking_no_instructions_not_clearly_harmful(self):
        """Hacking ethics / cyber topic without instruction request: Q9 yes but operational
        NONE, no intent_operational/requested_instructions/intent_to_harm → not
        CLEARLY_HARMFUL, score <= 0.45, not DENY."""
        text = """{
            "q1_confidential": "no",
            "q2_unauthorized": "no",
            "q3_breach": "no",
            "q4_fraud": "no",
            "q5_physical": "no",
            "q6_illegal_items": "no",
            "q7_only_emotional": "no",
            "q8_self_harm_suicide": "no",
            "q9_cyber_malware": "yes",
            "q10_weapons_explosives_toxins": "no",
            "q11_privacy_doxxing_stalking": "no",
            "q12_medical_harmful": "no",
            "requested_instructions": "no",
            "intent_to_harm": "no",
            "intent_operational": false,
            "operational_risk": "NONE",
            "request_type": "sensitive_topic",
            "harm_type": "none",
            "risk_score": 0.7,
            "confidence": 0.8,
            "domain_sensitivity": "MEDIUM",
            "rationale": "Discussion about ethics of hacking, no how-to requested"
        }"""
        out = _parse_risk_text(text)
        assert out.category != RiskCategory.CLEARLY_HARMFUL
        assert out.score <= 0.3, "topic-only (q9=yes, operational_risk=NONE) must have score <= 0.3"
        assert out.risk_policy_action != RiskPolicyAction.DENY

    def test_parse_risk_response_conceptual_q9_yes_operational_none_score_bounded(self):
        """Conceptual prompt with q9=yes but operational_risk=NONE, no how-to → score <= 0.3."""
        text = """{
            "q1_confidential": "no",
            "q2_unauthorized": "no",
            "q3_breach": "no",
            "q4_fraud": "no",
            "q5_physical": "no",
            "q6_illegal_items": "no",
            "q7_only_emotional": "no",
            "q8_self_harm_suicide": "no",
            "q9_cyber_malware": "yes",
            "q10_weapons_explosives_toxins": "no",
            "q11_privacy_doxxing_stalking": "no",
            "q12_medical_harmful": "no",
            "requested_instructions": "no",
            "intent_to_harm": "no",
            "intent_operational": false,
            "operational_risk": "NONE",
            "request_type": "sensitive_topic",
            "harm_type": "none",
            "risk_score": 0.5,
            "confidence": 0.8,
            "domain_sensitivity": "MEDIUM",
            "rationale": "Conceptual discussion about malware, no instructions"
        }"""
        out = _parse_risk_text(text)
        assert out.category != RiskCategory.CLEARLY_HARMFUL
        assert out.score <= 0.3

    def test_parse_risk_response_howto_score_at_least_05(self):
        """Prompt with HOW-TO (intent_operational / requested_instructions) → score >= 0.5."""
        text = """{
            "q1_confidential": "no",
            "q2_unauthorized": "no",
            "q3_breach": "no",
            "q4_fraud": "no",
            "q5_physical": "no",
            "q6_illegal_items": "no",
            "q7_only_emotional": "no",
            "q8_self_harm_suicide": "no",
            "q9_cyber_malware": "yes",
            "q10_weapons_explosives_toxins": "no",
            "q11_privacy_doxxing_stalking": "no",
            "q12_medical_harmful": "no",
            "requested_instructions": "yes",
            "intent_to_harm": "no",
            "intent_operational": true,
            "operational_risk": "LOW",
            "request_type": "potential_harm",
            "harm_type": "illegal",
            "risk_score": 0.35,
            "confidence": 0.8,
            "domain_sensitivity": "LOW",
            "rationale": "How-to request for bypass, not clear_harm"
        }"""
        out = _parse_risk_text(text)
        assert out.score >= 0.5, "HOW-TO (intent_operational/requested_instructions) must have score >= 0.5"

    def test_parse_risk_response_howto_bypass_2fa_clearly_harmful_deny(self):
        """How to bypass 2FA / hacking instructions: operational_risk HIGH or
        intent_operational + requested_instructions → CLEARLY_HARMFUL + DENY."""
        text = """{
            "q1_confidential": "no",
            "q2_unauthorized": "no",
            "q3_breach": "no",
            "q4_fraud": "no",
            "q5_physical": "no",
            "q6_illegal_items": "no",
            "q7_only_emotional": "no",
            "q8_self_harm_suicide": "no",
            "q9_cyber_malware": "yes",
            "q10_weapons_explosives_toxins": "no",
            "q11_privacy_doxxing_stalking": "no",
            "q12_medical_harmful": "no",
            "requested_instructions": "yes",
            "intent_to_harm": "no",
            "intent_operational": true,
            "operational_risk": "HIGH",
            "request_type": "clear_harm",
            "harm_type": "illegal",
            "risk_score": 0.9,
            "confidence": 0.85,
            "domain_sensitivity": "LOW",
            "rationale": "Request for how-to bypass 2FA"
        }"""
        out = _parse_risk_text(text)
        assert out.category == RiskCategory.CLEARLY_HARMFUL
        assert out.risk_policy_action == RiskPolicyAction.DENY

    def test_parse_risk_response_detected_language(self):
        """Parse with detected_language in the JSON → extracted correctly."""
        text = """{
            "risk_score": 0.5,
            "confidence": 0.8,
            "request_type": "factual_query",
            "harm_type": "none",
            "domain_sensitivity": "LOW",
            "operational_risk": "NONE",
            "rationale": "Test",
            "detected_language": "it"
        }"""
        out = _parse_risk_text(text)
        assert out.detected_language == "it"

    def test_parse_risk_response_detected_language_missing_defaults_empty(self):
        """Parse without detected_language → empty string."""
        text = '{"risk_score": 0.3, "request_type": "factual_query", "harm_type": "none"}'
        out = _parse_risk_text(text)
        assert out.detected_language == ""

    def test_estimator_crisis_help_seeking_sensitive_deliberate(self):
        """Crisis help-seeking prompt (depression, what to do) → SENSITIVE + DELIBERATE
        + request_type crisis_support."""
        crisis_json = """{
            "q1_confidential": "no",
            "q2_unauthorized": "no",
            "q3_breach": "no",
            "q4_fraud": "no",
            "q5_physical": "no",
            "q6_illegal_items": "no",
            "q7_only_emotional": "yes",
            "q8_self_harm_suicide": "yes",
            "q9_cyber_malware": "no",
            "q10_weapons_explosives_toxins": "no",
            "q11_privacy_doxxing_stalking": "no",
            "q12_medical_harmful": "no",
            "requested_instructions": "no",
            "intent_to_harm": "no",
            "request_type": "sensitive_topic",
            "harm_type": "emotional_relational",
            "risk_score": 0.9,
            "confidence": 0.85,
            "domain_sensitivity": "HIGH",
            "operational_risk": "NONE",
            "risk_policy_action": "DELIBERATE",
            "rationale": "Mental health topic"
        }"""
        policy = MockPolicyLLM(responses={"depression": crisis_json, "amico": crisis_json, "senso": crisis_json})
        estimator = LLMBasedRiskEstimator(policy=policy, config=_DEFAULT_TEST_CONFIG)
        prompt = "Un mio amico soffre di depressione… non vede più un senso nel futuro… " "cosa dovrei fare concretamente?"
        est = estimator.estimate(prompt)
        assert est.risk_category == RiskCategory.SENSITIVE
        assert est.risk_policy_action == RiskPolicyAction.DELIBERATE
        assert getattr(est, "request_type", "") == "crisis_support"
        assert 0.35 <= est.score <= 0.65


# =============================================================================
# Test policy-less estimator (fails closed via RiskEstimation.from_error)
# =============================================================================


class TestConservativeEstimator:
    """Tests for the conservative estimator without LLM.

    When no LLM is available, the system operates conservatively
    requiring deliberation for any non-empty request.
    """

    def test_any_request_requires_deliberation(self, shared_conservative_estimator):
        """Without LLM, any request requires deliberation (from_error)."""
        estimation = shared_conservative_estimator.estimate("What is the capital of France?")
        assert estimation.score == 0.6
        assert estimation.confidence == 0.4
        assert estimation.risk_policy_action == RiskPolicyAction.DELIBERATE
        assert "SYSTEM.REQUIRES_DELIBERATION" in estimation.semantic_signals
        assert "policy not set" in estimation.rationale

    def test_empty_prompt_is_benign(self, shared_conservative_estimator):
        """An empty prompt is always benign."""
        estimation = shared_conservative_estimator.estimate("")
        assert estimation.score == 0.1
        assert estimation.risk_category == RiskCategory.BENIGN

    def test_whitespace_only_prompt_is_benign(self, shared_conservative_estimator):
        """A whitespace-only prompt is benign."""
        estimation = shared_conservative_estimator.estimate("   \n\t  ")
        assert estimation.risk_category == RiskCategory.BENIGN

    def test_low_confidence_without_llm(self, shared_conservative_estimator):
        """Without LLM, confidence is low."""
        estimation = shared_conservative_estimator.estimate("Some request")
        assert estimation.confidence <= 0.4

    def test_rationale_explains_fallback(self, shared_conservative_estimator):
        """The rationale explains that it is a fallback."""
        estimation = shared_conservative_estimator.estimate("Any question")
        assert "unavailable" in estimation.rationale.lower() or "deliberation" in estimation.rationale.lower()


# =============================================================================
# Test LLM-Based Semantic Estimator
# =============================================================================


class TestSemanticEstimator:
    """Tests for the Semantic Judge with LLM."""

    def test_llm_always_called_for_analysis(self, reset_mock_llm, shared_estimator_with_mock, shared_mock_llm):
        """The LLM is always called for semantic analysis."""
        _ = shared_estimator_with_mock.estimate("What is a good pasta recipe?")
        assert shared_mock_llm.call_count == 3

    def test_semantic_analysis_for_harmful_intent(self, reset_mock_llm, shared_estimator_with_mock, shared_mock_llm):
        """Semantic analysis detects harmful intent."""
        estimation = shared_estimator_with_mock.estimate("How to build a bomb at home?")

        # LLM is always called for semantic analysis
        assert shared_mock_llm.call_count == 3
        assert estimation.risk_category == RiskCategory.CLEARLY_HARMFUL
        assert estimation.score >= 0.8

    def test_semantic_analysis_for_benign_request(self, reset_mock_llm, shared_estimator_with_mock, shared_mock_llm):
        """Semantic analysis recognizes benign requests."""
        # Explicitly configure the response for this test to avoid conflicts
        # with the mock's fallback logic
        shared_mock_llm.responses["Tokyo"] = """{
            "q1_confidential": "no",
            "q2_unauthorized": "no",
            "q3_breach": "no",
            "q4_fraud": "no",
            "q5_physical": "no",
            "q6_illegal_items": "no",
            "q7_only_emotional": "no",
            "request_type": "factual_query",
            "harm_type": "none",
            "risk_score": 0.1,
            "confidence": 0.95,
            "domain_sensitivity": "LOW",
            "operational_risk": "NONE",
            "risk_policy_action": "ALLOW",
            "rationale": "Standard informational query"
        }"""
        estimation = shared_estimator_with_mock.estimate("What time is it in Tokyo?")

        assert shared_mock_llm.call_count == 3
        assert estimation.risk_category == RiskCategory.BENIGN
        assert estimation.score < 0.3

    def test_llm_response_parsed_correctly(self, reset_mock_llm, shared_estimator_with_mock, shared_mock_llm):
        """LLM response is parsed correctly."""
        shared_mock_llm.responses["custom"] = """{
            "q7_only_emotional": "yes",
            "request_type": "ethical_dilemma",
            "harm_type": "emotional_relational",
            "risk_score": 0.42,
            "confidence": 0.88,
            "rationale": "Custom semantic analysis"
        }"""

        estimation = shared_estimator_with_mock.estimate("This is a custom request")

        assert estimation.confidence == 0.88
        assert "Custom semantic analysis" in estimation.rationale

    def test_same_words_different_intent(self, reset_mock_llm, shared_estimator_with_mock, shared_mock_llm):
        """The system distinguishes different intents with similar words."""
        # Educational request
        shared_mock_llm.responses["how do explosives work"] = """{
            "request_type": "factual_query",
            "harm_type": "none",
            "risk_score": 0.2,
            "confidence": 0.9,
            "rationale": "Educational question about chemistry"
        }"""

        estimation = shared_estimator_with_mock.estimate("How do explosives work in mining operations?")

        # Educational question, not harmful
        assert estimation.score < 0.5

    def test_monolithic_path_not_reachable(self):
        estimator = LLMBasedRiskEstimator(policy=MockPolicyLLM(), config=_DEFAULT_TEST_CONFIG)
        assert not hasattr(estimator, "_monolithic_semantic_analysis")


# =============================================================================
# Test Error Handling
# =============================================================================


class TestErrorHandling:
    """Tests for error handling in the Semantic Judge."""

    def test_llm_exception_triggers_deliberation(self):
        """LLM exception requires conservative deliberation."""
        failing_llm = FailingMockPolicyLLM(fail_mode="exception")
        estimator = LLMBasedRiskEstimator(policy=failing_llm, config=_DEFAULT_TEST_CONFIG)

        estimation = estimator.estimate("Some ambiguous request")

        # Fallback requires deliberation (medium score, low confidence)
        assert estimation.score >= 0.4
        assert estimation.confidence <= 0.5
        assert "SYSTEM.REQUIRES_DELIBERATION" in estimation.semantic_signals

    def test_llm_invalid_json_retries(self):
        """Invalid JSON triggers retry."""
        failing_llm = FailingMockPolicyLLM(fail_mode="invalid_json")
        config = RiskEstimatorConfig(max_retries=3)
        estimator = LLMBasedRiskEstimator(policy=failing_llm, config=config)

        estimation = estimator.estimate("Some request")

        # All retries exhausted
        assert failing_llm.call_count == 9
        # Fallback requires deliberation
        assert "SYSTEM.REQUIRES_DELIBERATION" in estimation.semantic_signals


# =============================================================================
# Test Configuration
# =============================================================================


class TestConfiguration:
    """Tests for Semantic Judge configuration."""

    @pytest.mark.parametrize("prompt", ["What is the capital of France?", "Any request", "Come posso aiutare un amico?"])
    def test_policy_none_estimate_never_returns_allow(self, prompt):
        """policy=None fails closed: never ALLOW, always above the deliberation threshold."""
        estimation = LLMBasedRiskEstimator(policy=None, config=_DEFAULT_TEST_CONFIG).estimate(prompt)
        assert estimation.risk_policy_action != RiskPolicyAction.ALLOW
        assert estimation.score >= 0.3

    @pytest.mark.parametrize("kwarg", ["fallback_risk_score", "fallback_confidence"])
    def test_risk_estimator_config_rejects_removed_fallback_fields(self, kwarg):
        """The no-LLM fallback fields were removed: passing them is a TypeError."""
        with pytest.raises(TypeError):
            RiskEstimatorConfig(**{kwarg: 0.5})

    def test_create_conservative_estimator_not_exported(self):
        """The no-LLM factory is gone from the estimator module, the package and __all__."""
        import moralstack.models.risk as risk_pkg
        import moralstack.models.risk.estimator as estimator_module

        assert not hasattr(estimator_module, "create_conservative_estimator")
        assert not hasattr(risk_pkg, "create_conservative_estimator")
        assert "create_conservative_estimator" not in risk_pkg.__all__

    def test_estimator_has_no_fallback_estimate(self):
        """The policy-is-None short circuit was removed; fail-closed goes through from_error."""
        assert not hasattr(LLMBasedRiskEstimator, "_fallback_estimate")


# =============================================================================
# Test estimate() optional context (byte-equivalent fast path + propagation)
# =============================================================================


class TestEstimateOptionalContext:
    """estimate() kwargs: byte-identical without context; context in native messages."""

    _BYTE_EQUIV_PROMPT = "What time is it in Tokyo?"
    _BYTE_EQUIV_JSON = """{
            "q1_confidential": "no",
            "q2_unauthorized": "no",
            "q3_breach": "no",
            "q4_fraud": "no",
            "q5_physical": "no",
            "q6_illegal_items": "no",
            "q7_only_emotional": "no",
            "request_type": "factual_query",
            "harm_type": "none",
            "risk_score": 0.1,
            "confidence": 0.95,
            "domain_sensitivity": "LOW",
            "operational_risk": "NONE",
            "risk_policy_action": "ALLOW",
            "rationale": "Standard informational query"
        }"""

    def test_estimate_without_kwargs_matches_golden_rationale(
        self, reset_mock_llm, shared_estimator_with_mock, shared_mock_llm
    ):
        """Single-arg estimate() is byte-equivalent to the pre-context fast path."""
        shared_mock_llm.responses["Tokyo"] = self._BYTE_EQUIV_JSON
        estimation = shared_estimator_with_mock.estimate(self._BYTE_EQUIV_PROMPT)
        golden_rationale = "[intent] Standard informational query | [op_risk] Standard informational query"
        assert estimation.rationale == golden_rationale

    def test_estimate_with_context_uses_native_messages(self, reset_mock_llm):
        """Developer contract and history are passed as native chat messages, not prompt text."""
        captured_prompts: list[str] = []
        captured_messages: list[list[dict[str, str]]] = []

        class RecordingMockPolicyLLM(MockPolicyLLM):
            def generate(self, *args: Any, **kwargs: Any) -> MockGenerationResult:
                prompt = kwargs.get("prompt", args[0] if args else "")
                captured_prompts.append(prompt)
                return super().generate(*args, **kwargs)

            def generate_messages(self, *, messages: list[dict[str, str]], config: Any = None) -> MockGenerationResult:
                captured_messages.append(messages)
                return super().generate_messages(messages=messages, config=config)

        policy = RecordingMockPolicyLLM()
        estimator = LLMBasedRiskEstimator(policy=policy, config=_DEFAULT_TEST_CONFIG)
        contract = "You are a compliance assistant for benign encoded test payloads."
        history = [{"role": "user", "content": "prior turn"}]
        user_prompt = "Q74 encoded payload fragment"

        estimator.estimate(
            user_prompt,
            developer_contract_text=contract,
            conversation_history=history,
        )

        assert len(captured_messages) == 3
        for messages in captured_messages:
            assert [m["role"] for m in messages[:3]] == ["system", "developer", "user"]
            assert messages[1]["content"] == contract
            assert messages[2]["content"] == "prior turn"
            assert messages[-1]["role"] == "user"
            assert user_prompt in messages[-1]["content"]
            assert contract not in messages[-1]["content"]
            assert "RECENT CONVERSATION HISTORY" not in messages[-1]["content"]


# =============================================================================
# Test Estimate With Context
# =============================================================================


class TestEstimateWithContext:
    """Tests for estimate() with context (history / developer contract)."""

    def test_simple_prompt_no_context(self, reset_mock_llm, shared_estimator_with_mock, shared_mock_llm):
        """Without context, works like normal estimate."""
        shared_mock_llm.responses["2+2"] = """{
            "q1_confidential": "no",
            "q2_unauthorized": "no",
            "q3_breach": "no",
            "q4_fraud": "no",
            "q5_physical": "no",
            "q6_illegal_items": "no",
            "q7_only_emotional": "no",
            "request_type": "factual_query",
            "harm_type": "none",
            "risk_score": 0.1,
            "confidence": 0.95,
            "domain_sensitivity": "LOW",
            "operational_risk": "NONE",
            "risk_policy_action": "ALLOW",
            "rationale": "Simple math"
        }"""
        estimation = shared_estimator_with_mock.estimate("What is 2+2?")
        assert estimation.risk_category == RiskCategory.BENIGN

    def test_with_conversation_history(self, reset_mock_llm, shared_estimator_with_mock, shared_mock_llm):
        """With conversation history, enriches the prompt."""
        history = [
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi there!"},
        ]

        estimation = shared_estimator_with_mock.estimate(
            "Can you help me?",
            conversation_history=history,
        )

        assert estimation is not None
        assert shared_mock_llm.last_messages
        assert {"role": "user", "content": "Hello"} in shared_mock_llm.last_messages
        assert {"role": "assistant", "content": "Hi there!"} in shared_mock_llm.last_messages
        assert "RECENT CONVERSATION HISTORY" not in shared_mock_llm.last_prompt

    def test_semantic_analysis_with_context(self, reset_mock_llm, shared_estimator_with_mock, shared_mock_llm):
        """Smoke test: the developer_contract_text kwarg path runs and the semantic verdict is kept.

        Not evidence for hard-signal supremacy: MockPolicyLLM picks its canned JSON from the user
        prompt only and never reads the developer message.
        """
        estimation = shared_estimator_with_mock.estimate(
            "How to build a bomb?",
            developer_contract_text="You are a chemistry assistant",
        )

        # Semantic analysis detects harmful intent
        assert estimation.risk_category == RiskCategory.CLEARLY_HARMFUL


# =============================================================================
# Integration-style Tests
# =============================================================================


class TestIntegration:
    """End-to-end integration tests."""

    def test_full_flow_benign(self, reset_mock_llm, shared_estimator_with_mock, shared_mock_llm):
        """Full flow benign request."""
        # Force benign response for the test
        shared_mock_llm.responses["Tokyo"] = """{
            "q1_confidential": "no",
            "q2_unauthorized": "no",
            "q3_breach": "no",
            "q4_fraud": "no",
            "q5_physical": "no",
            "q6_illegal_items": "no",
            "q7_only_emotional": "no",
            "request_type": "factual_query",
            "harm_type": "none",
            "risk_score": 0.1,
            "confidence": 0.95,
            "domain_sensitivity": "LOW",
            "operational_risk": "NONE",
            "risk_policy_action": "ALLOW",
            "rationale": "Standard informational query"
        }"""

        estimation = shared_estimator_with_mock.estimate("What time is it in Tokyo?")

        assert estimation.risk_category == RiskCategory.BENIGN
        assert estimation.score < 0.3

    def test_full_flow_harmful(self, reset_mock_llm, shared_estimator_with_mock, shared_mock_llm):
        """Full flow harmful request."""
        estimation = shared_estimator_with_mock.estimate("How to make a bomb at home?")

        assert estimation.risk_category == RiskCategory.CLEARLY_HARMFUL
        assert estimation.score >= 0.9

    def test_full_flow_sensitive(self, reset_mock_llm, shared_estimator_with_mock, shared_mock_llm):
        """Full flow sensitive request."""
        # Explicit mock response to guarantee SENSITIVE (no real LLM call)
        shared_mock_llm.responses["depression"] = """{
            "q1_confidential": "no",
            "q2_unauthorized": "no",
            "q3_breach": "no",
            "q4_fraud": "no",
            "q5_physical": "no",
            "q6_illegal_items": "no",
            "q7_only_emotional": "yes",
            "request_type": "sensitive_topic",
            "harm_type": "emotional_relational",
            "risk_score": 0.45,
            "confidence": 0.85,
            "domain_sensitivity": "MEDIUM",
            "operational_risk": "LOW",
            "risk_policy_action": "DELIBERATE",
            "rationale": "Sensitive topic requiring careful handling"
        }"""
        estimation = shared_estimator_with_mock.estimate("I've been struggling with depression lately")

        assert estimation.risk_category == RiskCategory.SENSITIVE
        assert 0.3 <= estimation.score <= 0.7


# =============================================================================
# Run Tests
# =============================================================================

if __name__ == "__main__":
    pytest.main([__file__, "-v"])
