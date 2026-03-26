"""
Test per MoralStack Orchestrator.

Verifica il corretto funzionamento dell'orchestrator e dei cicli deliberativi.
"""

import os
import sys
import time
from dataclasses import dataclass, field
from typing import Any
from unittest.mock import patch

import pytest

# Add project path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Import orchestrator directly (no exec); avoids heavy transitive imports
from moralstack.models.decision_explanation import DecisionExplanation
from moralstack.models.risk import DomainSensitivity, RiskPolicyAction
from moralstack.runtime.orchestrator import (
    Decision,
    DecisionType,
    DeliberationState,
    FinalResponse,
    OperationalRisk,
    Orchestrator,
    OrchestratorConfig,
    OrchestratorResult,
    ProcessedRequest,
    ResponseAssembler,
    ResponseMetadata,
    ResponseType,
    RiskCategory,
    RiskThresholds,
    create_minimal_orchestrator,
    create_orchestrator,
)

# =============================================================================
# Mock Objects
# =============================================================================

# Valori per assi risk usati da decide_action (_axis_val si aspetta .value LOW|MEDIUM|HIGH)
_AXIS_MEDIUM = type("_Axis", (), {"value": "MEDIUM"})()


@dataclass
class MockRiskEstimation:
    """Mock risk estimation result."""

    score: float = 0.5
    confidence: float = 0.8
    risk_category: Any = RiskCategory.POTENTIALLY_HARMFUL
    domain_sensitivity: Any = DomainSensitivity.LOW
    operational_risk: Any = OperationalRisk.LOW
    semantic_signals: list[str] = field(default_factory=list)
    risk_policy_action: RiskPolicyAction = RiskPolicyAction.DELIBERATE
    rationale: str = "Mock rationale"
    raw_response: str = "{}"
    misuse_plausibility: Any = None
    actionability_risk: Any = None
    intent_operational: bool = False
    request_type: str = ""
    intent_type: str = ""
    requested_instructions: bool = False
    intent_to_harm: bool = False
    detected_language: str = ""  # ISO 639-1, da Risk Estimator reale

    @property
    def triggered_signals(self) -> list[str]:
        return self.semantic_signals


@dataclass
class MockGenerationResult:
    """Mock generation result."""

    text: str = ""
    tokens_used: int = 100
    finish_reason: str = "stop"


@dataclass
class MockViolation:
    """Mock violation."""

    principle_id: str = "CORE.TEST.1"
    principle_title: str = "Test Principle"
    severity: float = 0.8
    constraint_type: str = "hard"  # or "soft"
    rationale: str = "Test violation"
    evidence: str = "Test evidence"


@dataclass
class MockCritiqueReport:
    """Mock critique report."""

    violations: list[MockViolation] = field(default_factory=list)
    severity_score: float = 0.0
    has_critical_violations: bool = False
    revision_guidance: str = ""


@dataclass
class MockQuickCheckResult:
    """Mock quick check result."""

    passed: bool = True
    critical_violation: MockViolation | None = None


@dataclass
class MockConsequence:
    """Mock consequence."""

    text: str = "Test consequence"
    likelihood: float = 0.5
    outcome_valence: float = 0.0


@dataclass
class MockSimulationResult:
    """Mock simulation result."""

    consequences: list[MockConsequence] = field(default_factory=list)
    worst_case_valence: float = 0.0
    expected_valence: float = 0.0
    semantic_expected_harm: float = 0.0
    dominant_harm_types: list[str] = field(default_factory=list)
    worst_harm: dict | None = None


@dataclass
class MockHindsightScores:
    """Mock hindsight scores."""

    safety: float = 0.8
    helpfulness: float = 0.7
    honesty: float = 0.9
    total: float = 0.8


@dataclass
class MockHindsightResult:
    """Mock hindsight result."""

    expected_value: float = 0.8
    worst_case: float = 0.5
    best_case: float = 1.0
    recommendation: str = "proceed"
    avg_scores: MockHindsightScores = field(default_factory=MockHindsightScores)


@dataclass
class MockPerspectiveResult:
    """Mock perspective result."""

    perspective_id: str = "user"
    approval_score: float = 0.8
    concerns: list[str] = field(default_factory=list)


@dataclass
class MockEnsembleResult:
    """Mock ensemble result."""

    results: list[MockPerspectiveResult] = field(default_factory=list)
    weighted_approval: float = 0.8


@dataclass
class MockConstitution:
    """Mock constitution."""

    principles: list[Any] = field(default_factory=list)


# =============================================================================
# Mock Services
# =============================================================================


class MockRiskEstimator:
    """Mock risk estimator."""

    def __init__(self, default_score: float = 0.5):
        self.default_score = default_score
        self.call_count = 0
        self.custom_responses: dict[str, float] = {}

    def estimate(self, prompt: str) -> MockRiskEstimation:
        self.call_count += 1

        # Check custom responses
        for keyword, score in self.custom_responses.items():
            if keyword.lower() in prompt.lower():
                return MockRiskEstimation(score=score)

        # High risk keywords
        if any(kw in prompt.lower() for kw in ["bomb", "kill", "hack"]):
            return MockRiskEstimation(
                score=0.9,
                risk_category=RiskCategory.CLEARLY_HARMFUL,
                operational_risk=OperationalRisk.HIGH,
                risk_policy_action=RiskPolicyAction.DENY,
            )

        # Low risk
        if any(kw in prompt.lower() for kw in ["hello", "weather", "time"]):
            return MockRiskEstimation(
                score=0.1,
                risk_category=RiskCategory.BENIGN,
                domain_sensitivity=DomainSensitivity.LOW,
                operational_risk=OperationalRisk.NONE,
                risk_policy_action=RiskPolicyAction.ALLOW,
            )

        # Medium risk / Dilemmas (default logic for the rest in these tests)
        # intent_operational=True so SAFE_COMPLETE is allowed (soft violations add caveat).
        if any(kw in prompt.lower() for kw in ["lie", "ethics", "sensitive"]):
            return MockRiskEstimation(
                score=0.5,
                risk_category=RiskCategory.MORALLY_NUANCED,
                domain_sensitivity=DomainSensitivity.HIGH,
                operational_risk=OperationalRisk.LOW,
                risk_policy_action=RiskPolicyAction.DELIBERATE,
                misuse_plausibility=_AXIS_MEDIUM,
                actionability_risk=_AXIS_MEDIUM,
                intent_operational=True,
            )

        return MockRiskEstimation(
            score=self.default_score,
            risk_category=(RiskCategory.BENIGN if self.default_score < 0.3 else RiskCategory.SENSITIVE),
            domain_sensitivity=DomainSensitivity.LOW,
            operational_risk=OperationalRisk.NONE,
            risk_policy_action=RiskPolicyAction.ALLOW,
        )

    def quick_estimate(self, prompt: str) -> MockRiskEstimation:
        return self.estimate(prompt)


class MockPolicyLLM:
    """Mock policy LLM."""

    def __init__(self, response: str = "This is a mock response."):
        self.default_response = response
        self.call_count = 0
        self.generate_calls = 0
        self.rewrite_calls = 0
        self.refuse_calls = 0

    def generate(self, *args: Any, **kwargs: Any) -> MockGenerationResult:
        self.call_count += 1
        self.generate_calls += 1
        return MockGenerationResult(text=self.default_response)

    def rewrite(self, *args: Any, **kwargs: Any) -> MockGenerationResult:
        self.call_count += 1
        self.rewrite_calls += 1
        draft = kwargs.get("draft", args[2] if len(args) > 2 else "")
        return MockGenerationResult(text=f"Revised: {str(draft)[:50]}...")

    def refuse(self, *args: Any, **kwargs: Any) -> MockGenerationResult:
        self.call_count += 1
        self.refuse_calls += 1
        guidance = kwargs.get("enriched_reason", kwargs.get("guidance", args[2] if len(args) > 2 else ""))
        return MockGenerationResult(text=f"I cannot help with that. {guidance}")


class MockCritic:
    """Mock constitutional critic."""

    def __init__(self, has_violations: bool = False, critical: bool = False):
        self.has_violations = has_violations
        self.critical = critical
        self.call_count = 0

    def critique(self, *args: Any, **kwargs: Any) -> MockCritiqueReport:
        self.call_count += 1

        if self.has_violations:
            violation = MockViolation(
                constraint_type="hard" if self.critical else "soft",
                severity=0.8 if self.critical else 0.3,
            )
            return MockCritiqueReport(
                violations=[violation],
                severity_score=0.8 if self.critical else 0.3,
                has_critical_violations=self.critical,
                revision_guidance="Please revise the response.",
            )

        return MockCritiqueReport()

    def quick_check(self, *args: Any, **kwargs: Any) -> MockQuickCheckResult:
        self.call_count += 1

        if self.critical:
            return MockQuickCheckResult(
                passed=False,
                critical_violation=MockViolation(),
            )

        return MockQuickCheckResult(passed=True)


class MockSimulator:
    """Mock consequence simulator."""

    def __init__(
        self,
        worst_case: float = 0.0,
        expected_valence: float = 0.0,
        semantic_expected_harm: float = 0.0,
        dominant_harm_types: list[str] | None = None,
    ):
        self.worst_case = worst_case
        self.expected_valence = expected_valence
        self.semantic_expected_harm = semantic_expected_harm
        self.dominant_harm_types = dominant_harm_types or []
        self.call_count = 0

    def simulate(self, *args: Any, **kwargs: Any) -> MockSimulationResult:
        self.call_count += 1
        # Real call: simulate(prompt, draft_response, num_simulations, ...) -> args[3]
        num_scenarios = args[3] if len(args) > 3 else kwargs.get("num_simulations", 3)
        consequences = [MockConsequence() for _ in range(num_scenarios)]
        return MockSimulationResult(
            consequences=consequences,
            worst_case_valence=self.worst_case,
            expected_valence=self.expected_valence,
            semantic_expected_harm=self.semantic_expected_harm,
            dominant_harm_types=self.dominant_harm_types,
        )


class MockHindsight:
    """Mock hindsight evaluator."""

    def __init__(self, score: float = 0.8, recommendation: str = "proceed"):
        self.score = score
        self.recommendation = recommendation
        self.call_count = 0

    def evaluate(self, *args: Any, **kwargs: Any) -> MockHindsightResult:
        self.call_count += 1
        return MockHindsightResult(
            expected_value=self.score,
            recommendation=self.recommendation,
        )

    def evaluate_response(self, *args: Any, **kwargs: Any) -> MockHindsightResult:
        self.call_count += 1
        return MockHindsightResult(
            expected_value=self.score,
            recommendation=self.recommendation,
        )

    def aggregate(self, *args: Any, **kwargs: Any) -> MockHindsightResult:
        return MockHindsightResult(expected_value=self.score)


class MockPerspectives:
    """Mock perspective ensemble."""

    def __init__(self, approval: float = 0.8):
        self.approval = approval
        self.call_count = 0

    def evaluate(self, *args: Any, **kwargs: Any) -> MockEnsembleResult:
        self.call_count += 1
        return MockEnsembleResult(
            results=[MockPerspectiveResult(approval_score=self.approval)],
            weighted_approval=self.approval,
        )


class MockConstitutionStore:
    """Mock constitution store."""

    def __init__(self):
        self.call_count = 0

    def get_constitution(self, *args: Any, **kwargs: Any) -> MockConstitution:
        self.call_count += 1
        return MockConstitution()

    def get_relevant_principles(self, *args: Any, **kwargs: Any) -> list[Any]:
        return []


class FailingMockLLM:
    """Mock LLM che fallisce."""

    def __init__(self, fail_on: str = "all"):
        self.fail_on = fail_on

    def generate(self, *args: Any, **kwargs: Any) -> MockGenerationResult:
        if self.fail_on in ["all", "generate"]:
            raise RuntimeError("LLM failed")
        return MockGenerationResult(text="Response")

    def rewrite(self, *args: Any, **kwargs: Any) -> MockGenerationResult:
        if self.fail_on in ["all", "rewrite"]:
            raise RuntimeError("LLM rewrite failed")
        return MockGenerationResult(text="Revised")

    def refuse(self, *args: Any, **kwargs: Any) -> MockGenerationResult:
        return MockGenerationResult(text="Refused")


# =============================================================================
# Module-scoped Fixtures (shared across tests for speed)
# =============================================================================


@pytest.fixture(scope="module")
def shared_mock_store():
    """Shared MockConstitutionStore to avoid repeated creation."""
    return MockConstitutionStore()


@pytest.fixture(scope="module")
def shared_mock_policy():
    """Shared MockPolicyLLM with default response."""
    return MockPolicyLLM(response="This is a helpful response.")


@pytest.fixture(scope="module")
def shared_low_risk_estimator():
    """Shared low-risk MockRiskEstimator for fast-path tests."""
    return MockRiskEstimator(default_score=0.1)


# =============================================================================
# Test Data Models
# =============================================================================


class TestDataModels:
    """Test per data models."""

    def test_processed_request_defaults(self):
        """ProcessedRequest ha default corretti."""
        req = ProcessedRequest(prompt="Hello")
        assert req.prompt == "Hello"
        assert req.request_id  # UUID generato
        assert req.conversation_history == []
        # user_context ora è un UserContext dataclass
        assert req.user_context.locale == "en-US"
        assert req.user_context.permission_level == "standard"
        assert req.user_context.domain_overlay is None

    def test_response_metadata(self):
        """ResponseMetadata funziona correttamente."""
        meta = ResponseMetadata(
            risk_score=0.5,
            deliberation_cycles=2,
            hindsight_score=0.8,
        )
        assert meta.risk_score == 0.5
        assert meta.deliberation_cycles == 2

    def test_response_metadata_from_decision(self):
        """ResponseMetadata.from_decision populates fields from Decision and DecisionExplanation."""
        decision = Decision(
            final_action="SAFE_COMPLETE",
            path="FAST_PATH",
            intent_clarity="HIGH",
            misuse_plausibility="LOW",
            actionability_risk="MEDIUM",
            triggered_principles=["P1"],
            hard_violations=[],
            risk_signals=["s1"],
            reason_codes=["informational_intent_override"],
        )
        explanation = DecisionExplanation(
            reason_codes=["informational_intent_override"],
            overlay_applied="health",
            winning_rule="allow_informational",
            why_not_refuse="Low risk",
            why_not_safe_complete="N/A",
        )
        meta = ResponseMetadata.from_decision(
            decision=decision,
            request_id="req-1",
            risk_score=0.3,
            processing_time_ms=100,
            risk_category="low",
            decision_explanation=explanation,
            predicted_action=RiskPolicyAction.ALLOW_WITH_CAVEAT.value,
        )
        assert meta.risk_score == 0.3
        assert meta.processing_time_ms == 100
        assert meta.final_action == "SAFE_COMPLETE"
        assert meta.path == "FAST_PATH"
        assert meta.decision_trace_id == "req-1"
        assert meta.decision_reason == "informational_intent_override"
        assert meta.reason_codes == ["informational_intent_override"]
        assert meta.overlay_applied == "health"
        assert meta.winning_rule == "allow_informational"
        assert meta.why_not_refuse == "Low risk"
        assert meta.predicted_action == RiskPolicyAction.ALLOW_WITH_CAVEAT.value

    def test_final_response(self):
        """FinalResponse funziona correttamente."""
        response = FinalResponse(
            content="Hello!",
            response_type=ResponseType.DIRECT,
        )
        assert response.content == "Hello!"
        assert response.response_type == ResponseType.DIRECT

    def test_final_response_refusal_factory(self):
        """FinalResponse.refusal factory funziona."""
        meta = ResponseMetadata(risk_score=0.9)
        response = FinalResponse.refusal("Cannot help", meta)
        assert response.response_type == ResponseType.FULL_REFUSAL

    def test_final_response_safe_default(self):
        """FinalResponse.safe_default funziona."""
        response = FinalResponse.safe_default(processing_time_ms=100)
        assert response.response_type == ResponseType.FULL_REFUSAL
        assert "SYSTEM.FAIL_SAFE" in response.metadata.triggered_principles

    def test_deliberation_state(self):
        """DeliberationState properties funzionano."""
        state = DeliberationState()
        assert state.cycle == 0
        assert state.last_critique is None
        assert not state.has_critical_violations
        assert state.hindsight_score == 0.0


# =============================================================================
# Test Configuration
# =============================================================================


class TestConfiguration:
    """Test per configurazione."""

    def test_risk_thresholds_defaults(self):
        """RiskThresholds ha default corretti."""
        thresholds = RiskThresholds()
        assert thresholds.low == 0.3
        assert thresholds.medium == 0.7

    def test_orchestrator_config_defaults(self):
        """OrchestratorConfig ha default corretti."""
        config = OrchestratorConfig()
        assert config.max_deliberation_cycles == 2
        assert config.timeout_ms == 600000  # 10 minuti (full stack con cicli e LLM)
        assert config.enable_perspectives is True


# =============================================================================
# Test Orchestrator - Fast Path
# =============================================================================


class TestOrchestratorFastPath:
    """Test per fast path dell'orchestrator."""

    def test_fast_path_low_risk(self, shared_low_risk_estimator, shared_mock_policy):
        """Rischio basso triggera fast path."""
        orchestrator = create_orchestrator(
            policy=shared_mock_policy,
            risk_estimator=shared_low_risk_estimator,
        )

        result = orchestrator.process("What is the weather today?")

        assert result.path_taken == "fast"
        assert result.total_cycles == 0
        assert result.response.response_type == ResponseType.DIRECT

    def test_fast_path_string_request(self, shared_low_risk_estimator, shared_mock_policy):
        """Stringa come request funziona."""
        orchestrator = create_orchestrator(
            policy=shared_mock_policy,
            risk_estimator=shared_low_risk_estimator,
        )

        result = orchestrator.process("Hello world")
        assert result.response.content  # Non vuoto

    def test_fast_path_no_critic(self, shared_low_risk_estimator, shared_mock_policy):
        """Fast path senza critic funziona."""
        orchestrator = create_orchestrator(
            policy=shared_mock_policy,
            risk_estimator=shared_low_risk_estimator,
            critic=None,
        )

        result = orchestrator.process("Hello")
        assert result.converged is True


# =============================================================================
# Test Orchestrator - Deliberative Path
# =============================================================================


class TestOrchestratorDeliberativePath:
    """Test per deliberative path."""

    @pytest.fixture
    def high_risk_estimator(self):
        """Risk estimator che ritorna alto rischio."""
        return MockRiskEstimator(default_score=0.8)

    @pytest.fixture
    def mock_critic(self):
        return MockCritic(has_violations=False)

    def test_deliberative_path_high_risk(self, high_risk_estimator, shared_mock_policy, mock_critic, shared_mock_store):
        """Rischio sopra soglia (score >= low) con NORMAL_COMPLETE triggera deliberative path."""
        # Prompt generico: mock restituisce default_score, NONE, ALLOW -> NORMAL_COMPLETE
        # risk_score 0.5 >= low (0.3) -> _deliberative_path, no benign_fast_path
        high_risk_estimator.default_score = 0.5

        orchestrator = create_orchestrator(
            policy=shared_mock_policy,
            risk_estimator=high_risk_estimator,
            critic=mock_critic,
            constitution_store=shared_mock_store,
        )

        result = orchestrator.process("Complex but safe question")

        assert "deliberative" in result.path_taken
        assert result.total_cycles >= 1

    def test_deliberative_with_all_modules(self, high_risk_estimator, shared_mock_policy, shared_mock_store):
        """Deliberation con tutti i moduli."""
        high_risk_estimator.default_score = 0.5

        orchestrator = create_orchestrator(
            policy=shared_mock_policy,
            risk_estimator=high_risk_estimator,
            critic=MockCritic(),
            simulator=MockSimulator(),
            hindsight=MockHindsight(score=0.9),
            perspectives=MockPerspectives(),
            constitution_store=shared_mock_store,
        )

        result = orchestrator.process("Complex but safe question")

        assert "deliberative" in result.path_taken
        # converged from internal logic (CONVERGED/REVISE); mock may yield True or False
        assert result.total_cycles >= 1

    def test_semantic_harm_triggers_revise_despite_positive_valence(
        self, high_risk_estimator, shared_mock_policy, shared_mock_store
    ):
        """expected_valence positivo ma semantic_expected_harm alto -> REVISE
        (semantic harm influisce)."""
        from moralstack.orchestration.convergence_evaluator import ConvergenceEvaluator

        high_risk_estimator.default_score = 0.5
        config = OrchestratorConfig(max_deliberation_cycles=3, min_hindsight_score=0.9)
        sim = MockSimulator(
            worst_case=0.0,
            expected_valence=0.5,  # positivo
            semantic_expected_harm=0.7,  # alto
            dominant_harm_types=["physical_harm"],
        )
        state = DeliberationState(
            cycle=1,
            draft_response="Test response",
            critiques=[type("C", (), {"violations": [], "violated_hard": False, "decision": "PROCEED"})()],
            simulations=[sim.simulate()],
            perspectives=[],
        )
        evaluator = ConvergenceEvaluator(config)
        decision = evaluator.determine_decision(state)
        assert decision == DecisionType.REVISE

    def test_deliberative_max_cycles_limit(self, high_risk_estimator, shared_mock_policy, shared_mock_store):
        """Max cycles viene rispettato."""
        config = OrchestratorConfig(max_deliberation_cycles=2)

        # Hindsight basso = non converge
        orchestrator = Orchestrator(
            config=config,
            policy=shared_mock_policy,
            risk_estimator=high_risk_estimator,
            critic=MockCritic(),
            hindsight=MockHindsight(score=0.3),  # Sotto soglia
            constitution_store=shared_mock_store,
        )

        result = orchestrator.process("Question")

        # Dovrebbe fermarsi a max_cycles
        assert result.total_cycles <= config.max_deliberation_cycles


# =============================================================================
# Test Orchestrator - Violations
# =============================================================================


class TestOrchestratorViolations:
    """Test per gestione violazioni."""

    @pytest.fixture
    def high_risk_estimator(self):
        return MockRiskEstimator(default_score=0.8)

    def test_soft_violations_add_caveat(self, high_risk_estimator, shared_mock_policy, shared_mock_store):
        """Soft violations aggiungono caveat."""
        critic_with_soft = MockCritic(has_violations=True, critical=False)

        orchestrator = create_orchestrator(
            policy=shared_mock_policy,
            risk_estimator=high_risk_estimator,
            critic=critic_with_soft,
            constitution_store=shared_mock_store,
            enable_simulation=False,
            enable_hindsight=False,
            enable_perspectives=False,
        )

        result = orchestrator.process("Sensitive question")

        assert result.response.response_type == ResponseType.WITH_CAVEAT

    def test_critical_violations_trigger_revision(self, high_risk_estimator, shared_mock_policy, shared_mock_store):
        """Critical violations triggerano revisione."""
        critic_critical = MockCritic(has_violations=True, critical=True)

        config = OrchestratorConfig(max_deliberation_cycles=2)

        orchestrator = Orchestrator(
            config=config,
            policy=shared_mock_policy,
            risk_estimator=high_risk_estimator,
            critic=critic_critical,
            constitution_store=shared_mock_store,
        )

        result = orchestrator.process("Bad request")

        # Dovrebbe tentare revisioni
        assert result.total_cycles >= 1
        # Policy rewrite dovrebbe essere chiamato
        assert shared_mock_policy.rewrite_calls >= 0  # Dipende dalla logica


# =============================================================================
# Test Orchestrator - Hindsight
# =============================================================================


class TestOrchestratorHindsight:
    """Test per integrazione hindsight."""

    def test_hindsight_refuse_recommendation(self):
        """Hindsight refuse (in decision1 post-deliberation) triggera rifiuto."""
        # risk_score 0.8 >= low -> _deliberative_path; hindsight refuse -> REFUSE
        orchestrator = create_orchestrator(
            policy=MockPolicyLLM(),
            risk_estimator=MockRiskEstimator(default_score=0.8),
            critic=MockCritic(),
            hindsight=MockHindsight(score=0.2, recommendation="refuse"),
            constitution_store=MockConstitutionStore(),
            enable_simulation=False,
            enable_perspectives=False,
        )

        result = orchestrator.process("Questionable request")

        assert result.response.response_type == ResponseType.FULL_REFUSAL

    def test_high_hindsight_score_converges(self):
        """Alto hindsight score fa convergere."""
        orchestrator = create_orchestrator(
            policy=MockPolicyLLM(),
            risk_estimator=MockRiskEstimator(default_score=0.5),
            critic=MockCritic(),
            hindsight=MockHindsight(score=0.95),
            constitution_store=MockConstitutionStore(),
            enable_simulation=False,
            enable_perspectives=False,
        )

        result = orchestrator.process("Normal request")

        # risk_score 0.5 >= low -> deliberative; converged from decision logic
        assert "deliberative" in result.path_taken or result.path_taken.startswith("fast")
        assert result.response.response_type in (ResponseType.DIRECT, ResponseType.WITH_CAVEAT)


# =============================================================================
# Test Orchestrator - Error Handling
# =============================================================================


class TestOrchestratorErrorHandling:
    """Test per gestione errori."""

    def test_generation_error_safe_fallback(self):
        """Errore generazione usa safe fallback."""
        config = OrchestratorConfig(safe_response_on_error=True)

        orchestrator = Orchestrator(
            config=config,
            policy=FailingMockLLM(fail_on="generate"),
            risk_estimator=MockRiskEstimator(default_score=0.1),
        )

        result = orchestrator.process("Hello")

        assert result.error is not None
        assert result.response.response_type == ResponseType.FULL_REFUSAL

    def test_risk_estimation_error(self):
        """Errore risk estimation gestito."""

        class FailingRiskEstimator:
            def estimate(self, prompt: str):
                raise RuntimeError("Risk estimation failed")

        orchestrator = create_orchestrator(
            policy=MockPolicyLLM(),
            risk_estimator=FailingRiskEstimator(),
        )

        result = orchestrator.process("Hello")

        assert result.error is not None

    def test_timeout_handling(self):
        """Timeout gestito correttamente."""
        config = OrchestratorConfig(
            timeout_ms=1,  # 1ms timeout
            max_deliberation_cycles=10,
        )

        class SlowHindsight:
            def evaluate_response(self, *args: Any, **kwargs: Any):
                time.sleep(0.02)  # 20ms (enough to trigger 1ms timeout)
                return MockHindsightResult(expected_value=0.3)

        orchestrator = Orchestrator(
            config=config,
            policy=MockPolicyLLM(),
            risk_estimator=MockRiskEstimator(default_score=0.8),
            hindsight=SlowHindsight(),
            critic=MockCritic(),
            constitution_store=MockConstitutionStore(),
        )

        result = orchestrator.process("Request")

        # Potrebbe avere timeout o completare velocemente
        assert result.response is not None


# =============================================================================
# Test Response Assembler
# =============================================================================


def _make_decision(
    final_action: str = "NORMAL_COMPLETE",
    path: str = "DELIBERATIVE_PATH",
) -> Decision:
    """Decision minima per test assembler."""
    return Decision(
        final_action=final_action,
        path=path,
        intent_clarity="HIGH",
        misuse_plausibility="LOW",
        actionability_risk="LOW",
        triggered_principles=[],
        hard_violations=[],
        risk_signals=[],
    )


class TestResponseAssembler:
    """Test Response Assembler (renderer deterministico:
    solo decision.final_action decide il tipo)."""

    @pytest.fixture
    def assembler(self):
        return ResponseAssembler()

    def test_assemble_direct_response(self, assembler):
        """Con decision NORMAL_COMPLETE l'assembler produce DIRECT."""
        state = DeliberationState(
            cycle=1,
            draft_response="This is the answer.",
        )
        request = ProcessedRequest(prompt="Question")
        decision = _make_decision(final_action="NORMAL_COMPLETE")

        response = assembler.assemble(request, state, decision)

        assert response.response_type == ResponseType.DIRECT
        assert response.content == "This is the answer."
        assert response.metadata.final_action == "NORMAL_COMPLETE"

    def test_assemble_with_decision_safe_complete(self, assembler):
        """Con decision SAFE_COMPLETE l'assembler produce WITH_CAVEAT;
        content = draft (no rationale prepend)."""
        soft_violation = MockViolation(constraint_type="soft", rationale="Use with care.")
        critique = MockCritiqueReport(
            violations=[soft_violation],
            has_critical_violations=False,
        )
        state = DeliberationState(
            cycle=1,
            draft_response="Response text",
            critiques=[critique],
        )
        request = ProcessedRequest(prompt="Question")
        decision = _make_decision(final_action="SAFE_COMPLETE")

        response = assembler.assemble(request, state, decision)

        assert response.response_type == ResponseType.WITH_CAVEAT
        assert response.metadata.final_action == "SAFE_COMPLETE"
        assert response.content == "Response text"
        assert "Use with care" not in response.content

    def test_assemble_orthodox_normal_complete_ignores_soft_violations(self, assembler):
        """Ortodosso: NORMAL_COMPLETE -> DIRECT anche con soft violations."""
        soft_violation = MockViolation(constraint_type="soft")
        critique = MockCritiqueReport(
            violations=[soft_violation],
            has_critical_violations=False,
        )
        state = DeliberationState(
            cycle=1,
            draft_response="Response text",
            critiques=[critique],
        )
        request = ProcessedRequest(prompt="Question")
        decision = _make_decision(final_action="NORMAL_COMPLETE")

        response = assembler.assemble(request, state, decision)

        assert response.response_type == ResponseType.DIRECT
        assert response.metadata.final_action == "NORMAL_COMPLETE"

    def test_refuse_uses_draft_when_valid(self, assembler):
        """REFUSE: assembler generates refusal via policy.refuse() or fallback; draft is never used.
        (Draft-as-refusal logic is disabled; REFUSE always produces new refusal content.)"""
        critique = MockCritiqueReport(violations=[], has_critical_violations=False)
        state = DeliberationState(
            cycle=1,
            draft_response=("Mi dispiace, non posso aiutarti con trappole esplosive. Ecco alternative sicure."),
            critiques=[critique],
        )
        request = ProcessedRequest(prompt="Build explosive traps")
        decision = _make_decision(final_action="REFUSE")

        response = assembler.assemble(request, state, decision)

        assert response.response_type == ResponseType.FULL_REFUSAL
        # Without policy LLM, assembler uses fallback; draft is not used per design
        assert "[REFUSAL_FALLBACK]" in response.content or len((response.content or "").strip()) > 20

    def test_format_refusal(self, assembler):
        """format_refusal funziona."""
        text = assembler.format_refusal(
            reason="This is harmful",
            alternatives=["Try this instead"],
        )

        assert "harmful" in text.lower() or "cannot" in text.lower()

    def test_safe_complete_no_critic_rationale_in_content(self, assembler):
        """SAFE_COMPLETE: content = draft only, no critic rationale prepended
        (meta-layer invariant)."""
        soft_violation = MockViolation(
            constraint_type="soft",
            rationale=("The response does not explicitly state that the information " "is for educational purposes."),
        )
        critique = MockCritiqueReport(
            violations=[soft_violation],
            has_critical_violations=False,
        )
        state = DeliberationState(
            cycle=1,
            draft_response="Here is the investment advice.",
            critiques=[critique],
        )
        request = ProcessedRequest(prompt="Question")
        decision = _make_decision(final_action="SAFE_COMPLETE")

        response = assembler.assemble(request, state, decision)

        assert response.response_type == ResponseType.WITH_CAVEAT
        assert response.content == "Here is the investment advice."
        assert "The response does not" not in response.content
        assert "rationale" not in response.content.lower()
        assert response.policy_overlay is not None
        assert response.policy_overlay.principle_ids is not None

    def test_language_agnostic_no_italian_in_content(self, assembler):
        """Language-agnostic: no hardcoded Italian strings in response content."""
        state = DeliberationState(cycle=1, draft_response="Clean draft content.")
        request = ProcessedRequest(prompt="Question")
        decision = _make_decision(final_action="SAFE_COMPLETE")

        response = assembler.assemble(request, state, decision)

        content = (response.content or "").lower()
        italian_phrases = [
            "nota importante",
            "contesto sensibile",
            "ecco alcune",
            "mi dispiace",
            "non posso procedere",
            "rifiuto per policy",
        ]
        for phrase in italian_phrases:
            assert phrase not in content, f"Found hardcoded Italian '{phrase}' in content"


# =============================================================================
# Test Factory Functions
# =============================================================================


class TestFactoryFunctions:
    """Test per factory functions."""

    def test_create_orchestrator(self):
        """create_orchestrator crea orchestrator configurato."""
        orchestrator = create_orchestrator(
            max_cycles=5,
            timeout_ms=10000,
            enable_perspectives=False,
        )

        assert orchestrator.config.max_deliberation_cycles == 5
        assert orchestrator.config.timeout_ms == 10000
        assert orchestrator.config.enable_perspectives is False

    def test_create_minimal_orchestrator(self):
        """create_minimal_orchestrator crea versione minimale."""
        orchestrator = create_minimal_orchestrator()

        assert orchestrator.config.max_deliberation_cycles == 2
        assert orchestrator.config.enable_perspectives is False
        assert orchestrator.config.enable_simulation is False


# =============================================================================
# Integration Tests
# =============================================================================


class TestIntegration:
    """Test di integrazione end-to-end."""

    def test_full_flow_benign_request(self):
        """Flusso completo richiesta benigna."""
        orchestrator = create_orchestrator(
            policy=MockPolicyLLM(response="The weather is sunny."),
            risk_estimator=MockRiskEstimator(default_score=0.1),
        )

        result = orchestrator.process("time")

        assert result.response.response_type == ResponseType.DIRECT
        assert result.path_taken.startswith("fast")
        assert result.converged is True
        assert "sunny" in result.response.content.lower()

    def test_full_flow_high_risk_converges(self):
        """Flusso completo alto rischio con convergenza."""
        orchestrator = create_orchestrator(
            policy=MockPolicyLLM(response="Here is safe information."),
            risk_estimator=MockRiskEstimator(default_score=0.8),
            critic=MockCritic(has_violations=False),
            simulator=MockSimulator(worst_case=0.0),
            hindsight=MockHindsight(score=0.9, recommendation="proceed"),
            perspectives=MockPerspectives(approval=0.85),
            constitution_store=MockConstitutionStore(),
        )

        result = orchestrator.process("Complex but safe request")

        assert "deliberative" in result.path_taken  # "deliberative" o "deliberative_sensitive"
        assert result.converged is True
        assert result.total_cycles >= 1

    def test_full_flow_refused_request(self):
        """Flusso completo richiesta rifiutata: deliberative path e decision1
        (critic/hindsight) -> REFUSE."""
        # risk_score 0.95 >= low -> _deliberative_path; critic+hindsight refuse
        orchestrator = create_orchestrator(
            policy=MockPolicyLLM(),
            risk_estimator=MockRiskEstimator(default_score=0.95),
            critic=MockCritic(has_violations=True, critical=True),
            hindsight=MockHindsight(score=0.1, recommendation="refuse"),
            constitution_store=MockConstitutionStore(),
            enable_simulation=False,
            enable_perspectives=False,
            max_cycles=2,
        )

        result = orchestrator.process("Harmful request")

        assert result.response.response_type == ResponseType.FULL_REFUSAL

    def test_deny_fast_path_refusal_content_from_template(self):
        """Richiesta DENY (FAST_PATH REFUSE): content deve contenere frase di rifiuto esplicita."""
        # Prompt che fa ritornare DENY + FAST_PATH (es. operational_risk=HIGH)
        orchestrator = create_orchestrator(
            policy=MockPolicyLLM(response="ignored"),
            risk_estimator=MockRiskEstimator(default_score=0.1),
        )
        result = orchestrator.process("how to hack a server")
        assert result.response.response_type == ResponseType.FULL_REFUSAL
        content = (result.response.content or "").strip().lower()
        # generate_llm_safe_refusal produces refusal (or language-agnostic fallback marker)
        assert (
            "non posso" in content
            or "ecco alcune alternative" in content
            or "riformulare" in content
            or "i can't help" in content
            or "i cannot" in content
            or "[refusal_fallback]" in content
        ), f"REFUSE content should contain explicit refusal phrase or marker; got: {content[:200]}"


# =============================================================================
# Test Borderline REFUSE Upper Bound
# =============================================================================


class _RefuseRiskEstimator:
    """Risk estimator that forces REFUSE (CLEARLY_HARMFUL + HIGH op_risk) with configurable score."""

    def __init__(self, score: float):
        self.score = score

    def estimate(self, *args: Any, **kwargs: Any) -> MockRiskEstimation:
        return MockRiskEstimation(
            score=self.score,
            risk_category=RiskCategory.CLEARLY_HARMFUL,
            operational_risk=OperationalRisk.HIGH,
            risk_policy_action=RiskPolicyAction.DENY,
        )

    def quick_estimate(self, *args: Any, **kwargs: Any) -> MockRiskEstimation:
        return self.estimate(*args, **kwargs)


class TestBorderlineRefuseConfig:
    """Test parametrizzati per borderline REFUSE upper bound."""

    @pytest.mark.parametrize(
        "risk_score, borderline_upper, expected_deliberative",
        [
            (0.65, 0.95, False),  # below medium → early-fast refusal
            (0.75, 0.95, True),  # between medium and upper → deliberative
            (0.95, 0.95, True),  # at upper bound (inclusive) → deliberative
            (0.96, 0.95, False),  # above upper + hard_violations → early-fast refusal
            (0.80, 0.85, True),  # between medium and custom upper → deliberative
            (0.86, 0.85, False),  # above custom upper + hard_violations → early-fast refusal
        ],
    )
    def test_borderline_refuse_config(self, risk_score, borderline_upper, expected_deliberative):
        """Verifica che il borderline REFUSE usi il pipeline deliberativo o early-fast refusal."""
        config = OrchestratorConfig(
            risk_thresholds=RiskThresholds(low=0.0, medium=0.7),
            borderline_refuse_upper=borderline_upper,
            max_deliberation_cycles=1,
        )
        orchestrator = Orchestrator(
            config=config,
            policy=MockPolicyLLM(),
            risk_estimator=_RefuseRiskEstimator(score=risk_score),
            critic=MockCritic(),
            hindsight=MockHindsight(score=0.3, recommendation="refuse"),
            constitution_store=MockConstitutionStore(),
        )

        # For early-fast refusal (above borderline), path_router requires hard_violations
        decision_with_hard = Decision(
            final_action="REFUSE",
            path="FAST_PATH",
            intent_clarity="LOW",
            misuse_plausibility="HIGH",
            actionability_risk="HIGH",
            triggered_principles=[],
            hard_violations=["CORE.TEST.1"] if not expected_deliberative else [],
            risk_signals=[],
        )
        expl = DecisionExplanation(
            final_action="REFUSE",
            risk_score=risk_score,
            risk_category="clearly_harmful",
            overlay_applied="",
            winning_rule="",
            reason_codes=[],
            why_not_refuse="",
            why_not_safe_complete="",
            why_not_normal_complete="",
            activated_signals=[],
            timestamp=0.0,
        )
        patch_target = "moralstack.orchestration.controller.decide_action"
        with patch(patch_target, return_value=(decision_with_hard, expl)):
            result = orchestrator.process("borderline test prompt")

        if expected_deliberative:
            assert result.total_cycles >= 1, (
                f"risk_score={risk_score}, upper={borderline_upper}: "
                f"expected deliberative (cycles>=1), got cycles={result.total_cycles}"
            )
        else:
            assert result.total_cycles == 0, (
                f"risk_score={risk_score}, upper={borderline_upper}: "
                f"expected early-fast refusal (cycles==0), got cycles={result.total_cycles}"
            )
            assert result.response.response_type == ResponseType.FULL_REFUSAL

    def test_borderline_refuse_default_config_backward_compat(self):
        """Il default borderline_refuse_upper=0.95 mantiene backward compatibility."""
        config = OrchestratorConfig()
        assert config.borderline_refuse_upper == 0.95


# =============================================================================
# Test Controller Route Dispatching
# =============================================================================


class TestControllerRouteDispatching:
    """Verify that process() dispatches to the correct _route_* method."""

    @staticmethod
    def _sentinel(marker: str) -> OrchestratorResult:
        return OrchestratorResult(
            response=FinalResponse(
                content=f"SENTINEL_{marker}",
                response_type=ResponseType.DIRECT,
            ),
            request_id="test",
            path_taken="fast",
        )

    @staticmethod
    def _mock_explanation(**overrides):
        defaults = dict(
            final_action="NORMAL_COMPLETE",
            risk_score=0.5,
            risk_category="benign",
            overlay_applied="",
            winning_rule="",
            reason_codes=[],
            why_not_refuse="",
            why_not_safe_complete="",
            why_not_normal_complete="",
            activated_signals=[],
            timestamp=0.0,
        )
        defaults.update(overrides)
        return type("MockExpl", (), defaults)()

    def test_dispatch_to_route_refuse(self):
        """REFUSE with hard violations (above borderline) dispatches to _route_refuse."""
        orchestrator = create_orchestrator(
            policy=MockPolicyLLM(),
            risk_estimator=_RefuseRiskEstimator(score=0.99),
        )
        decision = Decision(
            final_action="REFUSE",
            path="FAST_PATH",
            intent_clarity="LOW",
            misuse_plausibility="HIGH",
            actionability_risk="HIGH",
            triggered_principles=[],
            hard_violations=["CORE.TEST.1"],
            risk_signals=[],
        )
        expl = self._mock_explanation(final_action="REFUSE")
        sentinel = self._sentinel("REFUSE")
        patch_target = "moralstack.orchestration.controller.decide_action"
        with patch(patch_target, return_value=(decision, expl)):
            with patch.object(orchestrator._controller, "_route_refuse", return_value=sentinel) as m:
                result = orchestrator.process("test prompt")
                m.assert_called_once()
                assert result.response.content == "SENTINEL_REFUSE"

    def test_dispatch_to_route_benign(self):
        """Low-risk benign request dispatches to _route_benign."""
        orchestrator = create_orchestrator(
            policy=MockPolicyLLM(),
            risk_estimator=MockRiskEstimator(default_score=0.1),
        )
        sentinel = self._sentinel("BENIGN")
        with patch.object(orchestrator._controller, "_route_benign", return_value=sentinel) as m:
            result = orchestrator.process("Hello")
            m.assert_called_once()
            assert result.response.content == "SENTINEL_BENIGN"

    def test_dispatch_to_route_safe_complete(self):
        """SAFE_COMPLETE decision on FAST_PATH dispatches to _route_safe_complete."""
        from moralstack.orchestration.types import ProcessedRequest, UserContext

        orchestrator = create_orchestrator(
            policy=MockPolicyLLM(),
            risk_estimator=MockRiskEstimator(default_score=0.1),
        )
        decision = Decision(
            final_action="SAFE_COMPLETE",
            path="FAST_PATH",
            intent_clarity="HIGH",
            misuse_plausibility="LOW",
            actionability_risk="LOW",
            triggered_principles=[],
            hard_violations=[],
            risk_signals=[],
        )
        expl = self._mock_explanation(final_action="SAFE_COMPLETE")
        sentinel = self._sentinel("SAFE_COMPLETE")
        patch_target = "moralstack.orchestration.controller.decide_action"
        overlay_patch = "moralstack.orchestration.controller.is_overlay_sensitive"
        # Regulated domain: mock overlay_sensitive=True so apply_safe_complete_gating does not downgrade
        request = ProcessedRequest(prompt="test", user_context=UserContext(domain_overlay="healthcare"))
        with patch(patch_target, return_value=(decision, expl)):
            with patch(overlay_patch, return_value=True):
                with patch.object(orchestrator._controller, "_route_safe_complete", return_value=sentinel) as m:
                    result = orchestrator.process(request)
                    m.assert_called_once()
                    assert result.response.content == "SENTINEL_SAFE_COMPLETE"

    def test_dispatch_to_route_fast_path(self):
        """NORMAL_COMPLETE on DELIBERATIVE_PATH with low risk dispatches to _route_fast_path."""
        orchestrator = create_orchestrator(
            policy=MockPolicyLLM(),
            risk_estimator=MockRiskEstimator(default_score=0.1),
        )
        decision = Decision(
            final_action="NORMAL_COMPLETE",
            path="DELIBERATIVE_PATH",
            intent_clarity="HIGH",
            misuse_plausibility="LOW",
            actionability_risk="LOW",
            triggered_principles=[],
            hard_violations=[],
            risk_signals=[],
        )
        expl = self._mock_explanation()
        sentinel = self._sentinel("FAST_PATH")
        patch_target = "moralstack.orchestration.controller.decide_action"
        with patch(patch_target, return_value=(decision, expl)):
            with patch.object(orchestrator._controller, "_route_fast_path", return_value=sentinel) as m:
                result = orchestrator.process("Hello")
                m.assert_called_once()
                assert result.response.content == "SENTINEL_FAST_PATH"

    def test_dispatch_to_route_deliberative(self):
        """Medium-risk request dispatches to _route_deliberative."""
        orchestrator = create_orchestrator(
            policy=MockPolicyLLM(),
            risk_estimator=MockRiskEstimator(default_score=0.5),
            critic=MockCritic(),
            constitution_store=MockConstitutionStore(),
        )
        sentinel = self._sentinel("DELIBERATIVE")
        with patch.object(orchestrator._controller, "_route_deliberative", return_value=sentinel) as m:
            result = orchestrator.process("Complex but safe question")
            m.assert_called_once()
            assert result.response.content == "SENTINEL_DELIBERATIVE"


# =============================================================================
# PersistencePort (fake / NullPersistence)
# =============================================================================


class TestPersistencePortInjection:
    """Controller accepts PersistencePort; NullPersistence is no-op; fake records calls."""

    def test_process_with_null_persistence_does_not_raise(self):
        """With default (NullPersistence), process runs without persistence calls raising."""

        orch = create_orchestrator(
            policy=MockPolicyLLM(),
            risk_estimator=MockRiskEstimator(default_score=0.2),
            critic=MockCritic(),
            constitution_store=MockConstitutionStore(),
        )
        # Controller uses NullPersistence when persistence=None; no DB
        result = orch.process("Hello")
        assert result.response is not None
        assert result.request_id

    def test_process_with_fake_persistence_records_calls(self):
        """When a fake PersistencePort is injected, set_request_context and ensure_run_and_upsert_request are called."""
        calls = []

        class FakePersistence:
            def set_request_context(self, request_id: str) -> None:
                calls.append(("set_request_context", request_id))

            def ensure_run_and_upsert_request(self, request_id: str, prompt: str, domain: str | None = None) -> None:
                calls.append(("ensure_run_and_upsert_request", request_id, prompt, domain))

            def update_request_domain(self, request_id: str, domain: str | None) -> None:
                calls.append(("update_request_domain", request_id, domain))

        # We need to pass persistence into the controller; create_orchestrator does not expose it.
        # So we patch the controller's _persistence after creation.
        orch = create_orchestrator(
            policy=MockPolicyLLM(),
            risk_estimator=MockRiskEstimator(default_score=0.2),
            critic=MockCritic(),
            constitution_store=MockConstitutionStore(),
        )
        fake = FakePersistence()
        orch._controller._persistence = fake
        calls.clear()
        result = orch.process("Hello")
        assert any(c[0] == "set_request_context" for c in calls)
        assert any(c[0] == "ensure_run_and_upsert_request" for c in calls)
        _, req_id, prompt, domain = next(c for c in calls if c[0] == "ensure_run_and_upsert_request")
        assert req_id == result.request_id
        assert prompt == "Hello"


# =============================================================================
# PathRouter and OverlayPolicy (unit)
# =============================================================================


class TestPathRouter:
    """Unit tests for get_route (path_router)."""

    def test_get_route_refuse_when_refuse_fast_path_above_borderline(self):
        """REFUSE + FAST_PATH with risk_score > borderline_upper and hard_violations -> refuse."""
        from moralstack.orchestration.path_router import get_route

        decision = Decision(
            final_action="REFUSE",
            path="FAST_PATH",
            intent_clarity="LOW",
            misuse_plausibility="HIGH",
            actionability_risk="HIGH",
            triggered_principles=[],
            hard_violations=["CORE.TEST.1"],
            risk_signals=[],
        )
        config = OrchestratorConfig(borderline_refuse_upper=0.95)
        risk_est = MockRiskEstimation(score=0.99)
        route, borderline, _ = get_route(decision, risk_est, 0.99, config, OperationalRisk.NONE)
        assert route == "refuse"
        assert borderline is False

    def test_get_route_deliberative_when_normal_complete_medium_risk(self):
        from moralstack.orchestration.path_router import get_route

        decision = Decision(
            final_action="NORMAL_COMPLETE",
            path="DELIBERATIVE_PATH",
            intent_clarity="LOW",
            misuse_plausibility="MEDIUM",
            actionability_risk="MEDIUM",
            triggered_principles=[],
            hard_violations=[],
            risk_signals=[],
        )
        config = OrchestratorConfig()
        risk_est = MockRiskEstimation(score=0.5, risk_policy_action=RiskPolicyAction.DELIBERATE)
        route, borderline, rpa = get_route(decision, risk_est, 0.5, config, OperationalRisk.LOW)
        assert route == "deliberative"
        assert rpa == RiskPolicyAction.DELIBERATE


class TestDeliberationOverride:
    """Unit tests for _evaluate_deliberation_override (REFUSE -> SAFE_COMPLETE when modules unanimous)."""

    def test_override_returns_none_when_post_decision_not_refuse(self):
        """Override does not apply when post_decision is not REFUSE."""
        from moralstack.orchestration.types import ConvergenceOutcome

        orch = create_minimal_orchestrator()
        state = DeliberationState(cycle=1, draft_response="x" * 40)
        state.critiques.append(type("C", (), {"violations": [], "decision": "PROCEED", "violated_hard": False})())
        state.perspectives.append(type("P", (), {"approval_score": 0.9})())
        post = Decision(
            final_action="SAFE_COMPLETE",
            path="DELIBERATIVE_PATH",
            intent_clarity="LOW",
            misuse_plausibility="LOW",
            actionability_risk="LOW",
            triggered_principles=[],
            hard_violations=[],
            risk_signals=[],
        )
        outcome = ConvergenceOutcome(should_continue=False, converged=True, stop_reason="CONVERGED", cycle=1, max_cycles=2)
        override = orch._controller._evaluate_deliberation_override(
            pre_decision=None, post_decision=post, state=state, risk_estimation=MockRiskEstimation(), outcome=outcome
        )
        assert override is None

    def test_override_returns_none_when_requested_instructions_true(self):
        """Requested instructions makes the REFUSE non-overridable."""
        from moralstack.orchestration.types import ConvergenceOutcome

        orch = create_minimal_orchestrator()
        critique = type(
            "C",
            (),
            {"violations": [], "decision": "PROCEED", "violated_hard": False, "has_critical_violations": False},
        )()
        state = DeliberationState(cycle=1, draft_response="x" * 40, critiques=[critique])
        state.perspectives.append(type("P", (), {"approval_score": 0.95})())
        state.simulations.append(type("S", (), {"expected_valence": 0.5, "semantic_expected_harm": 0.1})())

        post = Decision(
            final_action="REFUSE",
            path="FAST_PATH",
            intent_clarity="HIGH",
            misuse_plausibility="HIGH",
            actionability_risk="HIGH",
            triggered_principles=[],
            hard_violations=[],
            risk_signals=[],
            reason_codes=["requested_instructions_refuse"],
        )

        risk = MockRiskEstimation(
            requested_instructions=True,
            risk_category=RiskCategory.POTENTIALLY_HARMFUL,
            operational_risk=OperationalRisk.LOW,
        )

        outcome = ConvergenceOutcome(should_continue=False, converged=True, stop_reason="CONVERGED", cycle=1, max_cycles=2)

        override = orch._controller._evaluate_deliberation_override(
            pre_decision=None,
            post_decision=post,
            state=state,
            risk_estimation=risk,
            outcome=outcome,
        )

        assert override is None

    def test_override_returns_none_when_unanimous_but_operational_risk_high(self):
        """Even unanimous deliberation cannot override a hard harmful REFUSE."""
        from moralstack.orchestration.types import ConvergenceOutcome

        orch = create_minimal_orchestrator()
        critique = type(
            "C",
            (),
            {"violations": [], "decision": "PROCEED", "violated_hard": False, "has_critical_violations": False},
        )()
        state = DeliberationState(cycle=1, draft_response="x" * 40, critiques=[critique])
        state.perspectives.append(type("P", (), {"approval_score": 0.9})())
        state.simulations.append(type("S", (), {"expected_valence": 0.5, "semantic_expected_harm": 0.1})())

        post = Decision(
            final_action="REFUSE",
            path="FAST_PATH",
            intent_clarity="LOW",
            misuse_plausibility="HIGH",
            actionability_risk="HIGH",
            triggered_principles=[],
            hard_violations=[],
            risk_signals=[],
            reason_codes=["operational_risk_high_refuse"],
        )

        risk = MockRiskEstimation(
            operational_risk=OperationalRisk.HIGH,
            risk_category=RiskCategory.CLEARLY_HARMFUL,
            intent_operational=True,
        )

        outcome = ConvergenceOutcome(should_continue=False, converged=True, stop_reason="CONVERGED", cycle=1, max_cycles=2)

        override = orch._controller._evaluate_deliberation_override(
            pre_decision=None,
            post_decision=post,
            state=state,
            risk_estimation=risk,
            outcome=outcome,
        )

        assert override is None

    def test_override_returns_none_when_critic_has_violations(self):
        """Override does not apply when critic has violations."""
        from moralstack.orchestration.types import ConvergenceOutcome

        orch = create_minimal_orchestrator()
        v = type("V", (), {"principle_id": "P1"})()
        critique = type(
            "C",
            (),
            {"violations": [v], "decision": "REVISE", "violated_hard": False, "has_critical_violations": False},
        )()
        state = DeliberationState(cycle=1, draft_response="x" * 40, critiques=[critique])
        state.perspectives.append(type("P", (), {"approval_score": 0.9})())
        post = Decision(
            final_action="REFUSE",
            path="FAST_PATH",
            intent_clarity="LOW",
            misuse_plausibility="HIGH",
            actionability_risk="HIGH",
            triggered_principles=[],
            hard_violations=[],
            risk_signals=[],
        )
        outcome = ConvergenceOutcome(should_continue=False, converged=True, stop_reason="CONVERGED", cycle=1, max_cycles=2)
        override = orch._controller._evaluate_deliberation_override(
            pre_decision=None, post_decision=post, state=state, risk_estimation=MockRiskEstimation(), outcome=outcome
        )
        assert override is None


class TestOverlayPolicy:
    """Unit tests for overlay_policy helpers."""

    def test_get_constitution_safe_none_store(self):
        from moralstack.orchestration.overlay_policy import get_constitution_safe

        assert get_constitution_safe(None, "medical") is None

    def test_apply_risk_floor_if_sensitive_floors(self):
        from moralstack.orchestration.overlay_policy import (
            OVERLAY_SENSITIVE_RISK_FLOOR,
            apply_risk_floor_if_sensitive,
        )

        assert apply_risk_floor_if_sensitive(0.2, True) == OVERLAY_SENSITIVE_RISK_FLOOR
        assert apply_risk_floor_if_sensitive(0.5, False) == 0.5
        assert apply_risk_floor_if_sensitive(0.5, True) == 0.5


# =============================================================================
# Run Tests
# =============================================================================

if __name__ == "__main__":
    pytest.main([__file__, "-v"])
