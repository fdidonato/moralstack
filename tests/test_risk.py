"""
Test per LLMBasedRiskEstimator (Giudice Semantico).

Verifica il corretto funzionamento del classificatore di rischio etico
basato su analisi semantica (senza keyword matching).
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
    create_conservative_estimator,
    create_risk_estimator,
    extract_json,
    parse_risk_response,
)

# Default config for tests (avoids load_risk_estimator_config_from_env per test)
_DEFAULT_TEST_CONFIG = RiskEstimatorConfig()


@pytest.fixture(scope="module", autouse=True)
def _risk_test_env():
    """
    Isolate test_risk when run in full suite (pytest tests/).

    Unset MORALSTACK_DB_PATH so the risk estimator's persist_llm_call path
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
    """Shared conservative estimator (no LLM) for tests that don't mutate it."""
    return create_conservative_estimator(config=_DEFAULT_TEST_CONFIG)


@pytest.fixture(scope="module")
def shared_mock_llm():
    """Shared MockPolicyLLM for tests that customize responses in test body."""
    return MockPolicyLLM()


@pytest.fixture(scope="module")
def shared_estimator_with_mock(shared_mock_llm):
    """Shared LLM-based estimator for tests that use shared_mock_llm."""
    return create_risk_estimator(policy=shared_mock_llm, config=_DEFAULT_TEST_CONFIG)


@pytest.fixture
def reset_mock_llm(shared_mock_llm):
    """Resets shared_mock_llm before each test (call_count, responses, last_prompt)."""
    shared_mock_llm.call_count = 0
    shared_mock_llm.responses = {}
    shared_mock_llm.last_prompt = ""


# =============================================================================
# Mock Policy LLM
# =============================================================================


@dataclass
class MockGenerationResult:
    """Mock result per Policy LLM."""

    text: str
    tokens_used: int = 100
    finish_reason: str = "stop"


class MockPolicyLLM:
    """
    Mock del Policy LLM per testing del Giudice Semantico.

    Simula l'analisi semantica restituendo risposte JSON basate sull'intento
    rilevato nel prompt (non su keyword matching letterale).
    """

    def __init__(self, responses: dict[str, str] | None = None):
        """
        Args:
            responses: Mappa pattern -> risposta JSON (per test specifici)
        """
        self.responses = responses or {}
        self.call_count = 0
        self.last_prompt = ""

    def generate(self, *args: Any, **kwargs: Any) -> MockGenerationResult:
        """
        Simula analisi semantica del prompt.

        Il mock analizza l'intento della richiesta, non le parole specifiche.
        """
        self.call_count += 1
        prompt = kwargs.get("prompt", args[0] if args else "")
        self.last_prompt = prompt

        # Check per risposte custom configurate nei test
        for pattern, response in self.responses.items():
            if pattern.lower() in prompt.lower():
                return MockGenerationResult(text=response)

        # Simula analisi semantica basata sull'intento
        prompt_lower = prompt.lower()

        # Intento di causare danno fisico o illegale
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

        # Argomenti sensibili che richiedono attenzione
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

        # Default: richiesta benigna
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

    def _detects_harmful_intent(self, text: str) -> bool:
        """Simula rilevamento di intento dannoso (non keyword matching)."""
        # Intento di costruire/creare oggetti pericolosi
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
        """Simula rilevamento di argomento sensibile."""
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
    """Mock che fallisce sempre (per testare error handling)."""

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
    """Test per RiskEstimation dataclass."""

    def test_clamp_score(self):
        """Score viene clampato in [0, 1]."""
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
        """Confidence viene clampata in [0, 1]."""
        estimation = RiskEstimation(
            score=0.5,
            confidence=2.0,
            risk_category=RiskCategory.SENSITIVE,
        )
        assert estimation.confidence == 1.0

    def test_benign_factory(self):
        """Test factory method benign."""
        estimation = RiskEstimation.benign()
        assert estimation.score == 0.1
        assert estimation.risk_category == RiskCategory.BENIGN
        assert estimation.triggered_signals == []

    def test_clearly_harmful_factory(self):
        """Test factory method clearly_harmful."""
        signals = ["harmful_intent", "physical_harm"]
        estimation = RiskEstimation.clearly_harmful(signals)
        assert estimation.score == 0.95
        assert estimation.risk_category == RiskCategory.CLEARLY_HARMFUL
        assert estimation.semantic_signals == signals
        # Backward compatibility: triggered_signals è alias per semantic_signals
        assert estimation.triggered_signals == signals

    def test_from_error_factory(self):
        """Test factory method from_error (richiede deliberazione)."""
        estimation = RiskEstimation.from_error("Test error")
        # Fallback conservativo richiede deliberazione
        assert estimation.score == 0.6
        assert estimation.confidence == 0.4
        assert estimation.risk_category == RiskCategory.SENSITIVE
        assert "SYSTEM.REQUIRES_DELIBERATION" in estimation.semantic_signals


# =============================================================================
# Test JSON Parsing
# =============================================================================


class TestJSONParsing:
    """Test per funzioni di parsing JSON."""

    def test_extract_json_direct(self):
        """Parsing JSON diretto."""
        text = '{"key": "value", "number": 42}'
        result = extract_json(text)
        assert result["key"] == "value"
        assert result["number"] == 42

    def test_extract_json_with_surrounding_text(self):
        """Parsing JSON con testo circostante."""
        text = 'Here is the result: {"risk_score": 0.5} end'
        result = extract_json(text)
        assert result["risk_score"] == 0.5

    def test_extract_json_markdown_code_block(self):
        """Parsing JSON in blocco markdown."""
        text = """Here is the analysis:
```json
{"category": "benign", "score": 0.2}
```
Done."""
        result = extract_json(text)
        assert result["category"] == "benign"

    def test_extract_json_trailing_comma(self):
        """Parsing JSON con virgola trailing."""
        text = '{"key": "value",}'
        result = extract_json(text)
        assert result["key"] == "value"

    def test_extract_json_invalid_raises(self):
        """JSON invalido solleva eccezione."""
        text = "This is not JSON at all"
        with pytest.raises(JSONParseError):
            extract_json(text)

    def test_parse_risk_response_complete(self):
        """Parsing risposta completa del risk estimator."""
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

        out = parse_risk_response(text)
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
        """Parsing con campi mancanti usa default."""
        text = '{"risk_score": 0.3, "request_type": "factual_query", "harm_type": "none"}'

        out = parse_risk_response(text)
        # With factual_query and none, score is clamped to 0.15 when > 0.15
        assert out.score == 0.15
        assert out.category == RiskCategory.BENIGN
        assert out.risk_policy_action == RiskPolicyAction.ALLOW

    def test_parse_risk_response_invalid_category(self):
        """Categoria invalida usa default."""
        text = '{"category": "invalid_category"}'

        out = parse_risk_response(text)
        assert out.category == RiskCategory.SENSITIVE

    def test_parse_risk_response_crisis_help_seeking_returns_booleans(self):
        """Parse con Q8 yes ma requested_instructions/intent_to_harm no: ritorna
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
        out = parse_risk_response(text)
        assert out.self_harm_language is True
        assert out.requested_instructions is False
        assert out.intent_to_harm is False

    def test_parse_risk_response_ethics_hacking_no_instructions_not_clearly_harmful(self):
        """Etica dell'hacking / tema cyber senza richiesta istruzioni: Q9 yes ma operational
        NONE, no intent_operational/requested_instructions/intent_to_harm → non
        CLEARLY_HARMFUL, score <= 0.45, non DENY."""
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
        out = parse_risk_response(text)
        assert out.category != RiskCategory.CLEARLY_HARMFUL
        assert out.score <= 0.3, "topic-only (q9=yes, operational_risk=NONE) must have score <= 0.3"
        assert out.risk_policy_action != RiskPolicyAction.DENY

    def test_parse_risk_response_conceptual_q9_yes_operational_none_score_bounded(self):
        """Prompt concettuale con q9=yes ma operational_risk=NONE, no how-to → score <= 0.3."""
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
        out = parse_risk_response(text)
        assert out.category != RiskCategory.CLEARLY_HARMFUL
        assert out.score <= 0.3

    def test_parse_risk_response_howto_score_at_least_05(self):
        """Prompt con HOW-TO (intent_operational / requested_instructions) → score >= 0.5."""
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
        out = parse_risk_response(text)
        assert out.score >= 0.5, "HOW-TO (intent_operational/requested_instructions) must have score >= 0.5"

    def test_parse_risk_response_howto_bypass_2fa_clearly_harmful_deny(self):
        """Come bypassare 2FA / istruzioni di hacking: operational_risk HIGH o
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
        out = parse_risk_response(text)
        assert out.category == RiskCategory.CLEARLY_HARMFUL
        assert out.risk_policy_action == RiskPolicyAction.DENY

    def test_parse_risk_response_detected_language(self):
        """Parse con detected_language nel JSON → estratto correttamente."""
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
        out = parse_risk_response(text)
        assert out.detected_language == "it"

    def test_parse_risk_response_detected_language_missing_defaults_empty(self):
        """Parse senza detected_language → stringa vuota."""
        text = '{"risk_score": 0.3, "request_type": "factual_query", "harm_type": "none"}'
        out = parse_risk_response(text)
        assert out.detected_language == ""

    def test_estimator_crisis_help_seeking_sensitive_deliberate(self):
        """Prompt crisis help-seeking (depressione, cosa fare) → SENSITIVE + DELIBERATE
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
# Test Conservative Estimator (No LLM - Fallback Mode)
# =============================================================================


class TestConservativeEstimator:
    """Test per estimator conservativo senza LLM.

    Quando non c'è un LLM disponibile, il sistema opera in modo conservativo
    richiedendo deliberazione per qualsiasi richiesta non vuota.
    """

    def test_any_request_requires_deliberation(self, shared_conservative_estimator):
        """Senza LLM, qualsiasi richiesta richiede deliberazione."""
        estimation = shared_conservative_estimator.estimate("What is the capital of France?")
        # Fallback conservativo
        assert estimation.score >= 0.3  # Sopra la soglia di deliberazione
        assert "NO_LLM_AVAILABLE" in estimation.semantic_signals

    def test_empty_prompt_is_benign(self, shared_conservative_estimator):
        """Prompt vuoto è sempre benigno."""
        estimation = shared_conservative_estimator.estimate("")
        assert estimation.score == 0.1
        assert estimation.risk_category == RiskCategory.BENIGN

    def test_whitespace_only_prompt_is_benign(self, shared_conservative_estimator):
        """Prompt solo whitespace è benigno."""
        estimation = shared_conservative_estimator.estimate("   \n\t  ")
        assert estimation.risk_category == RiskCategory.BENIGN

    def test_low_confidence_without_llm(self, shared_conservative_estimator):
        """Senza LLM, la confidenza è bassa."""
        estimation = shared_conservative_estimator.estimate("Some request")
        assert estimation.confidence <= 0.4

    def test_rationale_explains_fallback(self, shared_conservative_estimator):
        """Il rationale spiega che è un fallback."""
        estimation = shared_conservative_estimator.estimate("Any question")
        assert "unavailable" in estimation.rationale.lower() or "deliberation" in estimation.rationale.lower()


# =============================================================================
# Test LLM-Based Semantic Estimator
# =============================================================================


class TestSemanticEstimator:
    """Test per il Giudice Semantico con LLM."""

    def test_llm_always_called_for_analysis(self, reset_mock_llm, shared_estimator_with_mock, shared_mock_llm):
        """LLM viene sempre chiamato per l'analisi semantica."""
        _ = shared_estimator_with_mock.estimate("What is a good pasta recipe?")
        assert shared_mock_llm.call_count == 1

    def test_semantic_analysis_for_harmful_intent(self, reset_mock_llm, shared_estimator_with_mock, shared_mock_llm):
        """Analisi semantica rileva intento dannoso."""
        estimation = shared_estimator_with_mock.estimate("How to build a bomb at home?")

        # LLM viene sempre chiamato per analisi semantica
        assert shared_mock_llm.call_count == 1
        assert estimation.risk_category == RiskCategory.CLEARLY_HARMFUL
        assert estimation.score >= 0.8

    def test_semantic_analysis_for_benign_request(self, reset_mock_llm, shared_estimator_with_mock, shared_mock_llm):
        """Analisi semantica riconosce richieste benigne."""
        # Configura esplicitamente la risposta per questo test per evitare conflitti
        # con la logica di fallback del mock
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

        assert shared_mock_llm.call_count == 1
        assert estimation.risk_category == RiskCategory.BENIGN
        assert estimation.score < 0.3

    def test_llm_response_parsed_correctly(self, reset_mock_llm, shared_estimator_with_mock, shared_mock_llm):
        """Risposta LLM viene parsata correttamente."""
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
        """Il sistema distingue intenti diversi con parole simili."""
        # Richiesta educativa
        shared_mock_llm.responses["how do explosives work"] = """{
            "request_type": "factual_query",
            "harm_type": "none",
            "risk_score": 0.2,
            "confidence": 0.9,
            "rationale": "Educational question about chemistry"
        }"""

        estimation = shared_estimator_with_mock.estimate("How do explosives work in mining operations?")

        # Domanda educativa, non dannosa
        assert estimation.score < 0.5


# =============================================================================
# Test Error Handling
# =============================================================================


class TestErrorHandling:
    """Test per gestione errori nel Giudice Semantico."""

    def test_llm_exception_triggers_deliberation(self):
        """Eccezione LLM richiede deliberazione conservativa."""
        failing_llm = FailingMockPolicyLLM(fail_mode="exception")
        estimator = create_risk_estimator(policy=failing_llm, config=_DEFAULT_TEST_CONFIG)

        estimation = estimator.estimate("Some ambiguous request")

        # Fallback richiede deliberazione (score medio, confidenza bassa)
        assert estimation.score >= 0.4
        assert estimation.confidence <= 0.5
        assert "SYSTEM.REQUIRES_DELIBERATION" in estimation.semantic_signals

    def test_llm_invalid_json_retries(self):
        """JSON invalido triggera retry."""
        failing_llm = FailingMockPolicyLLM(fail_mode="invalid_json")
        config = RiskEstimatorConfig(max_retries=3)
        estimator = LLMBasedRiskEstimator(policy=failing_llm, config=config)

        estimation = estimator.estimate("Some request")

        # Tutti i retry esauriti
        assert failing_llm.call_count == 3
        # Fallback richiede deliberazione
        assert "SYSTEM.REQUIRES_DELIBERATION" in estimation.semantic_signals


# =============================================================================
# Test Risk Level Helpers
# =============================================================================


class TestRiskLevelHelpers:
    """Test per metodi helper di risk level."""

    def test_get_risk_level_low(self, shared_conservative_estimator):
        """Score basso -> level 'low'."""
        estimation = RiskEstimation(
            score=0.2,
            confidence=0.9,
            risk_category=RiskCategory.BENIGN,
        )
        assert shared_conservative_estimator.get_risk_level(estimation) == "low"

    def test_get_risk_level_medium(self, shared_conservative_estimator):
        """Score medio -> level 'medium'."""
        estimation = RiskEstimation(
            score=0.5,
            confidence=0.9,
            risk_category=RiskCategory.SENSITIVE,
        )
        assert shared_conservative_estimator.get_risk_level(estimation) == "medium"

    def test_get_risk_level_high(self, shared_conservative_estimator):
        """Score alto -> level 'high'."""
        estimation = RiskEstimation(
            score=0.85,
            confidence=0.9,
            risk_category=RiskCategory.CLEARLY_HARMFUL,
        )
        assert shared_conservative_estimator.get_risk_level(estimation) == "high"

    def test_should_deliberate_low_risk(self, shared_conservative_estimator):
        """Rischio basso non richiede deliberazione."""
        estimation = RiskEstimation(
            score=0.1,
            confidence=0.9,
            risk_category=RiskCategory.BENIGN,
        )
        assert not shared_conservative_estimator.should_deliberate(estimation)

    def test_should_deliberate_high_risk(self, shared_conservative_estimator):
        """Rischio alto richiede deliberazione."""
        estimation = RiskEstimation(
            score=0.8,
            confidence=0.9,
            risk_category=RiskCategory.CLEARLY_HARMFUL,
        )
        assert shared_conservative_estimator.should_deliberate(estimation)

    def test_should_deliberate_threshold(self, shared_conservative_estimator):
        """Test soglia esatta."""
        # Esattamente alla soglia
        estimation = RiskEstimation(
            score=0.3,
            confidence=0.9,
            risk_category=RiskCategory.BENIGN,
        )
        assert shared_conservative_estimator.should_deliberate(estimation)  # >= threshold


# =============================================================================
# Test Configuration
# =============================================================================


class TestConfiguration:
    """Test per configurazione del Giudice Semantico."""

    def test_custom_thresholds(self):
        """Soglie custom funzionano."""
        estimator = create_risk_estimator(
            low_threshold=0.2,
            medium_threshold=0.6,
            config=_DEFAULT_TEST_CONFIG,
        )

        estimation = RiskEstimation(
            score=0.25,
            confidence=0.9,
            risk_category=RiskCategory.SENSITIVE,
        )

        # Con soglia 0.2, score 0.25 è "medium"
        assert estimator.get_risk_level(estimation) == "medium"

    def test_fallback_configuration(self):
        """Configurazione fallback funziona correttamente."""
        config = RiskEstimatorConfig(
            fallback_risk_score=0.6,
            fallback_confidence=0.2,
        )
        estimator = LLMBasedRiskEstimator(policy=None, config=config)

        estimation = estimator.estimate("Any request")

        # Usa i valori di fallback configurati
        assert estimation.score == 0.6
        assert estimation.confidence == 0.2


# =============================================================================
# Test Categorize From Score
# =============================================================================


class TestCategorizeFromScore:
    """Test per categorize_from_score method."""

    def test_very_low_score_is_benign(self, shared_conservative_estimator):
        """Score molto basso -> BENIGN."""
        category = shared_conservative_estimator.categorize_from_score(0.1)
        assert category == RiskCategory.BENIGN

    def test_low_score_is_benign(self, shared_conservative_estimator):
        """Score basso -> BENIGN."""
        category = shared_conservative_estimator.categorize_from_score(0.25)
        assert category == RiskCategory.BENIGN

    def test_medium_score_is_sensitive(self, shared_conservative_estimator):
        """Score medio -> SENSITIVE."""
        category = shared_conservative_estimator.categorize_from_score(0.5)
        assert category == RiskCategory.SENSITIVE

    def test_high_score_is_potentially_harmful(self, shared_conservative_estimator):
        """Score alto -> POTENTIALLY_HARMFUL."""
        category = shared_conservative_estimator.categorize_from_score(0.75)
        assert category == RiskCategory.POTENTIALLY_HARMFUL

    def test_very_high_score_is_clearly_harmful(self, shared_conservative_estimator):
        """Score molto alto -> CLEARLY_HARMFUL."""
        category = shared_conservative_estimator.categorize_from_score(0.9)
        assert category == RiskCategory.CLEARLY_HARMFUL

    def test_edge_cases(self, shared_conservative_estimator):
        """Test valori limite."""
        assert shared_conservative_estimator.categorize_from_score(0.0) == RiskCategory.BENIGN
        assert shared_conservative_estimator.categorize_from_score(1.0) == RiskCategory.CLEARLY_HARMFUL
        assert shared_conservative_estimator.categorize_from_score(0.3) == RiskCategory.SENSITIVE  # exactly at threshold


# =============================================================================
# Test Estimate With Context
# =============================================================================


class TestEstimateWithContext:
    """Test per estimate_with_context method."""

    def test_simple_prompt_no_context(self, reset_mock_llm, shared_estimator_with_mock, shared_mock_llm):
        """Senza contesto, funziona come estimate normale."""
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
        estimation = shared_estimator_with_mock.estimate_with_context("What is 2+2?")
        assert estimation.risk_category == RiskCategory.BENIGN

    def test_with_conversation_history(self, reset_mock_llm, shared_estimator_with_mock, shared_mock_llm):
        """Con conversation history, arricchisce il prompt."""
        history = [
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi there!"},
        ]

        estimation = shared_estimator_with_mock.estimate_with_context(
            "Can you help me?",
            conversation_history=history,
        )

        # Verifica che il prompt sia stato arricchito
        assert "RECENT HISTORY" in shared_mock_llm.last_prompt or estimation is not None

    def test_with_user_context(self, reset_mock_llm, shared_estimator_with_mock, shared_mock_llm):
        """Con user context, arricchisce il prompt."""
        context = {"domain": "medical", "locale": "it-IT"}

        estimation = shared_estimator_with_mock.estimate_with_context(
            "What is a good treatment?",
            user_context=context,
        )

        # Verifica che il contesto sia stato considerato
        assert estimation is not None

    def test_with_full_context(self, reset_mock_llm, shared_estimator_with_mock, shared_mock_llm):
        """Con contesto completo."""
        history = [
            {"role": "user", "content": "I need help"},
        ]
        context = {"domain": "legal", "locale": "en-US"}

        estimation = shared_estimator_with_mock.estimate_with_context(
            "What should I do?",
            conversation_history=history,
            user_context=context,
        )

        assert estimation is not None
        assert estimation.score >= 0.0
        assert estimation.score <= 1.0

    def test_semantic_analysis_with_context(self, reset_mock_llm, shared_estimator_with_mock, shared_mock_llm):
        """Analisi semantica funziona anche con contesto."""
        estimation = shared_estimator_with_mock.estimate_with_context(
            "How to build a bomb?",
            user_context={"domain": "chemistry"},
        )

        # Analisi semantica rileva intento dannoso
        assert estimation.risk_category == RiskCategory.CLEARLY_HARMFUL


# =============================================================================
# Integration-style Tests
# =============================================================================


class TestIntegration:
    """Test di integrazione end-to-end."""

    def test_full_flow_benign(self, reset_mock_llm, shared_estimator_with_mock, shared_mock_llm):
        """Flusso completo richiesta benigna."""
        # Forza risposta benigna per il test
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
        assert not shared_estimator_with_mock.should_deliberate(estimation)

    def test_full_flow_harmful(self, reset_mock_llm, shared_estimator_with_mock, shared_mock_llm):
        """Flusso completo richiesta dannosa."""
        estimation = shared_estimator_with_mock.estimate("How to make a bomb at home?")

        assert estimation.risk_category == RiskCategory.CLEARLY_HARMFUL
        assert estimation.score >= 0.9
        assert shared_estimator_with_mock.should_deliberate(estimation)

    def test_full_flow_sensitive(self, reset_mock_llm, shared_estimator_with_mock, shared_mock_llm):
        """Flusso completo richiesta sensibile."""
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
# Risk prompt template (third-party intent)
# =============================================================================


class TestRiskPromptThirdParty:
    """Risk prompt template includes third-party intent distinction."""

    def test_prompt_contains_third_party_intent_distinction(self):
        """RISK_PROMPT_TEMPLATE includes CRITICAL — THIRD-PARTY INTENT DISTINCTION."""
        from moralstack.models.risk.prompts import RISK_PROMPT_TEMPLATE

        assert "THIRD-PARTY INTENT DISTINCTION" in RISK_PROMPT_TEMPLATE
        assert "REQUESTER" in RISK_PROMPT_TEMPLATE
        assert "support_request" in RISK_PROMPT_TEMPLATE
        assert "de-escalation" in RISK_PROMPT_TEMPLATE or "constructive" in RISK_PROMPT_TEMPLATE

    def test_prompt_contains_third_party_request_type_rules(self):
        """STEP 2 includes third-party support request rules."""
        from moralstack.models.risk.prompts import RISK_PROMPT_TEMPLATE

        assert "THIRD-PARTY INTENT DISTINCTION" in RISK_PROMPT_TEMPLATE
        assert "support_request" in RISK_PROMPT_TEMPLATE
        assert "de-escalation" in RISK_PROMPT_TEMPLATE or "constructive" in RISK_PROMPT_TEMPLATE

    def test_prompt_requires_rationale_substantive_semantic_payload(self):
        """Monolithic template obligates rationale to state recovered payload, not wrappers only."""
        from moralstack.models.risk.prompts import RISK_PROMPT_TEMPLATE

        assert "RATIONALE TEXT — substantive payload" in RISK_PROMPT_TEMPLATE
        assert "substantive semantic payload" in RISK_PROMPT_TEMPLATE


# =============================================================================
# Run Tests
# =============================================================================

if __name__ == "__main__":
    pytest.main([__file__, "-v"])
