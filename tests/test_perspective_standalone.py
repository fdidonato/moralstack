"""
Test standalone per LLMPerspectiveEnsemble.

Questo test non usa pytest e può essere eseguito direttamente.
Evita i problemi di import chain del package moralstack.
"""

import importlib.util
import os
import sys
from dataclasses import dataclass
from typing import Any, Literal

# =============================================================================
# Carica il modulo perspective direttamente dal file
# =============================================================================


def load_module_from_file(module_name: str, file_path: str):
    """Carica un modulo Python direttamente da file."""
    spec = importlib.util.spec_from_file_location(module_name, file_path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


# Ottieni il path assoluto del modulo perspective
base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
perspective_path = os.path.join(base_dir, "moralstack", "runtime", "modules", "perspective_module.py")

# Carica il modulo
pm = load_module_from_file("perspective_module", perspective_path)

# Estrai le classi e funzioni dal modulo
Perspective = pm.Perspective
PerspectiveResult = pm.PerspectiveResult
PerspectiveAggregation = pm.PerspectiveAggregation
EnsembleConfig = pm.EnsembleConfig
EnsembleResult = pm.EnsembleResult
LLMPerspectiveEnsemble = pm.LLMPerspectiveEnsemble
DEFAULT_PERSPECTIVES = pm.DEFAULT_PERSPECTIVES
PERSPECTIVES_BY_ID = pm.PERSPECTIVES_BY_ID
create_perspective_ensemble = pm.create_perspective_ensemble
create_minimal_ensemble = pm.create_minimal_ensemble
create_safety_focused_ensemble = pm.create_safety_focused_ensemble
extract_json = pm.extract_json
parse_perspective_response = pm.parse_perspective_response
JSONParseError = pm.JSONParseError


# =============================================================================
# Mock Policy LLM
# =============================================================================


@dataclass
class MockGenerationResult:
    """Risultato mock della generazione."""

    text: str
    tokens_used: int = 100
    finish_reason: Literal["stop", "length", "content_filter"] = "stop"
    logprobs: list[float] | None = None


class MockPolicyLLM:
    """Mock del Policy LLM per testing."""

    def __init__(self, responses: dict[str, str] | None = None):
        self.responses = responses or {}
        self.call_count = 0

    def generate(self, *args: Any, **kwargs: Any) -> MockGenerationResult:
        self.call_count += 1
        prompt = kwargs.get("prompt", args[0] if args else "")

        for perspective_name, response in self.responses.items():
            if perspective_name in prompt:
                return MockGenerationResult(text=response)

        return MockGenerationResult(
            text='{"approval_score": 0.8, "concerns": [], "suggestions": [], ' '"rationale": "Good response"}'
        )


class FailingMockPolicyLLM:
    """Mock che genera sempre JSON invalido."""

    def __init__(self, fail_count: int = 999):
        self.fail_count = fail_count
        self.call_count = 0

    def generate(self, *args: Any, **kwargs: Any) -> MockGenerationResult:
        self.call_count += 1
        if self.call_count <= self.fail_count:
            return MockGenerationResult(text="Not valid JSON at all!")
        return MockGenerationResult(text='{"approval_score": 0.5, "concerns": [], "suggestions": [], "rationale": "test"}')


# =============================================================================
# Test Runner
# =============================================================================


class TestRunner:
    """Simple test runner."""

    def __init__(self):
        self.passed = 0
        self.failed = 0
        self.errors = []

    def run_test(self, name: str, test_func):
        """Esegue un singolo test."""
        try:
            test_func()
            self.passed += 1
            print(f"  ✓ {name}")
        except AssertionError as e:
            self.failed += 1
            self.errors.append((name, str(e)))
            print(f"  ✗ {name}: {e}")
        except Exception as e:
            self.failed += 1
            self.errors.append((name, str(e)))
            print(f"  ✗ {name}: ERROR - {e}")

    def summary(self):
        """Stampa il sommario dei test."""
        print("\n" + "=" * 60)
        print(f"RESULTS: {self.passed} passed, {self.failed} failed")
        if self.errors:
            print("\nFailed tests:")
            for name, error in self.errors:
                print(f"  - {name}: {error}")
        print("=" * 60)
        return self.failed == 0


# =============================================================================
# Test Functions
# =============================================================================


def test_create_perspective():
    """Test creazione prospettiva."""
    p = Perspective(
        id="test",
        name="Test Perspective",
        prompt_template="Test template",
        weight=1.0,
    )
    assert p.id == "test"
    assert p.name == "Test Perspective"
    assert p.weight == 1.0


def test_weight_clamping():
    """Test che il peso venga clampato in range [0, 2]."""
    p1 = Perspective(id="t1", name="T1", prompt_template="", weight=-1.0)
    p2 = Perspective(id="t2", name="T2", prompt_template="", weight=5.0)

    assert p1.weight == 0.0
    assert p2.weight == 2.0


def test_create_result():
    """Test creazione risultato."""
    r = PerspectiveResult(
        perspective_id="user",
        perspective_name="Direct User",
        approval_score=0.85,
        concerns=["Minor concern"],
        suggestions=["A suggestion"],
        rationale="Good overall",
    )
    assert r.perspective_id == "user"
    assert r.approval_score == 0.85
    assert len(r.concerns) == 1


def test_approval_score_clamping():
    """Test che approval score venga clampato in range [0, 1]."""
    r1 = PerspectiveResult(perspective_id="t", approval_score=-0.5)
    r2 = PerspectiveResult(perspective_id="t", approval_score=1.5)

    assert r1.approval_score == 0.0
    assert r2.approval_score == 1.0


def test_empty_aggregation():
    """Test aggregazione vuota."""
    agg = PerspectiveAggregation.empty()
    assert agg.weighted_approval == 0.0
    assert agg.perspective_count == 0


def test_has_concerns():
    """Test property has_concerns."""
    agg1 = PerspectiveAggregation(all_concerns=[])
    agg2 = PerspectiveAggregation(all_concerns=["A concern"])

    assert agg1.has_concerns is False
    assert agg2.has_concerns is True


def test_recommendation_proceed():
    """Test raccomandazione proceed (min_approval alto per non triggerare regola conservativa)."""
    agg = PerspectiveAggregation(weighted_approval=0.8, min_approval=0.8)
    assert agg.recommendation == "proceed"


def test_recommendation_revise():
    """Test raccomandazione revise (min_approval >= 0.3)."""
    agg = PerspectiveAggregation(weighted_approval=0.5, min_approval=0.5)
    assert agg.recommendation == "revise"


def test_recommendation_refuse():
    """Test raccomandazione refuse."""
    agg = PerspectiveAggregation(weighted_approval=0.2, min_approval=0.2)
    assert agg.recommendation == "refuse"


def test_extract_json_direct():
    """Test parsing JSON diretto."""
    text = '{"approval_score": 0.9, "concerns": [], "suggestions": [], "rationale": "test"}'
    data = extract_json(text)
    assert data["approval_score"] == 0.9


def test_extract_json_with_text():
    """Test parsing JSON con testo intorno."""
    text = 'Here is the evaluation: {"approval_score": 0.7, "concerns": ["c1"], ' '"suggestions": [], "rationale": "test"}'
    data = extract_json(text)
    assert data["approval_score"] == 0.7
    assert "c1" in data["concerns"]


def test_extract_json_markdown_block():
    """Test parsing JSON in blocco markdown."""
    text = """```json
{"approval_score": 0.6, "concerns": [], "suggestions": [], "rationale": "test"}
```"""
    data = extract_json(text)
    assert data["approval_score"] == 0.6


def test_extract_json_invalid():
    """Test fallimento su JSON invalido."""
    text = "This is not JSON at all"
    try:
        extract_json(text)
        assert False, "Should have raised JSONParseError"
    except JSONParseError:
        pass  # Expected


def test_default_perspectives_exist():
    """Test che esistano le prospettive predefinite."""
    assert len(DEFAULT_PERSPECTIVES) == 5

    ids = [p.id for p in DEFAULT_PERSPECTIVES]
    assert "user" in ids
    assert "vulnerable" in ids
    assert "observer" in ids
    assert "adversary" in ids
    assert "compliance" in ids


def test_perspectives_by_id_map():
    """Test mappa PERSPECTIVES_BY_ID."""
    assert "user" in PERSPECTIVES_BY_ID
    assert PERSPECTIVES_BY_ID["user"].name == "Direct User"
    assert PERSPECTIVES_BY_ID["vulnerable"].weight == 1.2
    assert PERSPECTIVES_BY_ID["adversary"].weight == 0.8


def test_create_ensemble():
    """Test creazione ensemble."""
    policy = MockPolicyLLM()
    ensemble = LLMPerspectiveEnsemble(policy)

    assert ensemble.policy == policy
    assert len(ensemble.perspectives) == 5


def test_create_ensemble_with_custom_perspectives():
    """Test creazione con prospettive custom."""
    policy = MockPolicyLLM()
    custom = [
        Perspective(id="custom1", name="Custom 1", prompt_template="test"),
        Perspective(id="custom2", name="Custom 2", prompt_template="test"),
    ]

    ensemble = LLMPerspectiveEnsemble(policy, perspectives=custom)
    assert len(ensemble.perspectives) == 2


def test_evaluate_basic():
    """Test valutazione base."""
    policy = MockPolicyLLM()
    config = EnsembleConfig(parallel_evaluation=False, max_perspectives=5)
    ensemble = LLMPerspectiveEnsemble(policy, config=config)

    result = ensemble.evaluate(
        request="What is the capital of France?",
        response="The capital of France is Paris.",
    )

    assert result.evaluation_count == 5
    assert len(result.results) == 5
    assert result.aggregation.weighted_approval > 0


def test_evaluate_with_custom_responses():
    """Test valutazione con risposte personalizzate."""
    responses = {
        "Direct User": ('{"approval_score": 0.9, "concerns": [], "suggestions": [], "rationale": "Excellent"}'),
        "Vulnerable User": (
            '{"approval_score": 0.7, "concerns": ["Could be clearer"], ' '"suggestions": ["Add context"], "rationale": "OK"}'
        ),
        "Neutral Observer": ('{"approval_score": 0.8, "concerns": [], "suggestions": [], "rationale": "Good"}'),
        "Potential Misuser": ('{"approval_score": 0.95, "concerns": [], "suggestions": [], "rationale": "No risk"}'),
        "Compliance Officer": ('{"approval_score": 0.85, "concerns": [], "suggestions": [], "rationale": "Compliant"}'),
    }

    policy = MockPolicyLLM(responses=responses)
    config = EnsembleConfig(parallel_evaluation=False, max_perspectives=5)
    ensemble = LLMPerspectiveEnsemble(policy, config=config)

    result = ensemble.evaluate(
        request="Test request",
        response="Test response",
    )

    assert result.evaluation_count == 5
    assert "Could be clearer" in result.aggregation.all_concerns
    assert "Add context" in result.aggregation.all_suggestions


def test_evaluate_single():
    """Test valutazione singola prospettiva."""
    policy = MockPolicyLLM()
    ensemble = LLMPerspectiveEnsemble(policy)

    result = ensemble.evaluate_single(
        request="Test",
        response="Test response",
        perspective_id="user",
    )

    assert result is not None
    assert result.perspective_id == "user"


def test_evaluate_single_unknown_perspective():
    """Test valutazione prospettiva sconosciuta."""
    policy = MockPolicyLLM()
    ensemble = LLMPerspectiveEnsemble(policy)

    result = ensemble.evaluate_single(
        request="Test",
        response="Test response",
        perspective_id="nonexistent",
    )

    assert result is None


def test_aggregate_empty():
    """Test aggregazione lista vuota."""
    policy = MockPolicyLLM()
    ensemble = LLMPerspectiveEnsemble(policy)

    agg = ensemble.aggregate([])
    assert agg.weighted_approval == 0.0
    assert agg.perspective_count == 0


def test_aggregate_weighted():
    """Test aggregazione pesata."""
    policy = MockPolicyLLM()
    ensemble = LLMPerspectiveEnsemble(policy)

    results = [
        PerspectiveResult(perspective_id="user", approval_score=0.8),  # weight 1.0
        PerspectiveResult(perspective_id="vulnerable", approval_score=0.6),  # weight 1.2
    ]

    agg = ensemble.aggregate(results)

    # weighted avg = (0.8 * 1.0 + 0.6 * 1.2) / (1.0 + 1.2) = (0.8 + 0.72) / 2.2 = 0.69...
    assert 0.68 < agg.weighted_approval < 0.71
    assert agg.min_approval == 0.6
    assert agg.max_approval == 0.8


def test_aggregate_deduplication():
    """Test deduplicazione concerns e suggestions."""
    policy = MockPolicyLLM()
    ensemble = LLMPerspectiveEnsemble(policy)

    results = [
        PerspectiveResult(
            perspective_id="user",
            concerns=["Privacy concern", "Safety issue"],
            suggestions=["Add disclaimer"],
        ),
        PerspectiveResult(
            perspective_id="vulnerable",
            concerns=["privacy concern", "New concern"],
            suggestions=["Add disclaimer", "Be clearer"],
        ),
    ]

    agg = ensemble.aggregate(results)

    assert len(agg.all_concerns) == 3
    assert len(agg.all_suggestions) == 2


def test_add_perspective():
    """Test aggiunta prospettiva."""
    policy = MockPolicyLLM()
    ensemble = LLMPerspectiveEnsemble(policy, perspectives=[])

    new_perspective = Perspective(
        id="custom",
        name="Custom",
        prompt_template="Custom template",
    )

    ensemble.add_perspective(new_perspective)
    assert len(ensemble.perspectives) == 1
    assert ensemble.perspectives[0].id == "custom"


def test_remove_perspective():
    """Test rimozione prospettiva."""
    policy = MockPolicyLLM()
    ensemble = LLMPerspectiveEnsemble(policy)

    initial_count = len(ensemble.perspectives)
    removed = ensemble.remove_perspective("user")

    assert removed is True
    assert len(ensemble.perspectives) == initial_count - 1
    assert "user" not in ensemble.get_active_perspectives()


def test_remove_nonexistent_perspective():
    """Test rimozione prospettiva inesistente."""
    policy = MockPolicyLLM()
    ensemble = LLMPerspectiveEnsemble(policy)

    removed = ensemble.remove_perspective("nonexistent")
    assert removed is False


def test_set_perspectives():
    """Test impostazione prospettive da lista ID."""
    policy = MockPolicyLLM()
    ensemble = LLMPerspectiveEnsemble(policy)

    ensemble.set_perspectives(["user", "compliance"])

    assert len(ensemble.perspectives) == 2
    ids = ensemble.get_active_perspectives()
    assert "user" in ids
    assert "compliance" in ids


def test_retry_on_invalid_json():
    """Test retry quando JSON è invalido."""
    policy = FailingMockPolicyLLM(fail_count=2)
    config = EnsembleConfig(max_retries=3, parallel_evaluation=False)

    perspectives = [PERSPECTIVES_BY_ID["user"]]
    ensemble = LLMPerspectiveEnsemble(policy, config=config, perspectives=perspectives)

    result = ensemble.evaluate(
        request="Test",
        response="Test response",
    )

    assert result.evaluation_count == 1


def test_fail_after_max_retries():
    """Test fallimento dopo max retry."""
    policy = FailingMockPolicyLLM(fail_count=999)
    config = EnsembleConfig(max_retries=2, parallel_evaluation=False)

    perspectives = [PERSPECTIVES_BY_ID["user"]]
    ensemble = LLMPerspectiveEnsemble(policy, config=config, perspectives=perspectives)

    result = ensemble.evaluate(
        request="Test",
        response="Test response",
    )

    assert result.evaluation_count == 0
    assert "user" in result.failed_perspectives


def test_create_perspective_ensemble_factory():
    """Test factory base."""
    policy = MockPolicyLLM()
    ensemble = create_perspective_ensemble(
        policy=policy,
        max_retries=2,
        temperature=0.5,
        parallel=False,
    )

    assert ensemble.config.max_retries == 2
    assert ensemble.config.temperature == 0.5
    assert ensemble.config.parallel_evaluation is False


def test_create_minimal_ensemble_factory():
    """Test factory ensemble minimo."""
    policy = MockPolicyLLM()
    ensemble = create_minimal_ensemble(policy)

    ids = ensemble.get_active_perspectives()
    assert len(ids) == 2
    assert "user" in ids
    assert "compliance" in ids
    assert "compliance" in ids


def test_create_safety_focused_ensemble_factory():
    """Test factory ensemble safety-focused."""
    policy = MockPolicyLLM()
    ensemble = create_safety_focused_ensemble(policy)

    ids = ensemble.get_active_perspectives()
    assert len(ids) == 3
    assert "vulnerable" in ids
    assert "adversary" in ids
    assert "compliance" in ids


def test_full_evaluation_flow():
    """Test flusso completo di valutazione."""
    responses = {
        "Direct User": (
            '{"approval_score": 0.9, "concerns": [], "suggestions": [], ' '"rationale": "Helpful and accurate"}'
        ),
        "Vulnerable User": (
            '{"approval_score": 0.75, "concerns": ["May need more context for beginners"], '
            '"suggestions": ["Add beginner-friendly explanation"], '
            '"rationale": "Good but could be clearer"}'
        ),
        "Neutral Observer": (
            '{"approval_score": 0.85, "concerns": [], "suggestions": [], ' '"rationale": "Well-balanced response"}'
        ),
        "Potential Misuser": (
            '{"approval_score": 0.95, "concerns": [], "suggestions": [], ' '"rationale": "No misuse potential"}'
        ),
        "Compliance Officer": (
            '{"approval_score": 0.88, "concerns": [], "suggestions": [], ' '"rationale": "Complies with guidelines"}'
        ),
    }

    policy = MockPolicyLLM(responses=responses)
    config = EnsembleConfig(parallel_evaluation=False, max_perspectives=5)
    ensemble = LLMPerspectiveEnsemble(policy, config=config)

    result = ensemble.evaluate(
        request="How do I learn Python programming?",
        response="To learn Python programming, start with online tutorials.",
    )

    assert result.evaluation_count == 5
    assert len(result.failed_perspectives) == 0

    agg = result.aggregation
    assert agg.weighted_approval > 0.8
    assert agg.recommendation == "proceed"
    assert len(agg.all_concerns) == 1
    assert len(agg.all_suggestions) == 1


def test_low_approval_triggers_revise():
    """Test che basso approval trigger raccomandazione revise."""
    responses = {
        "Direct User": (
            '{"approval_score": 0.4, "concerns": ["Incomplete answer"], '
            '"suggestions": ["Add more details"], "rationale": "Needs work"}'
        ),
        "Vulnerable User": (
            '{"approval_score": 0.3, "concerns": ["Confusing"], ' '"suggestions": ["Simplify"], "rationale": "Too complex"}'
        ),
        "Neutral Observer": ('{"approval_score": 0.5, "concerns": [], "suggestions": [], "rationale": "Average"}'),
        "Potential Misuser": ('{"approval_score": 0.9, "concerns": [], "suggestions": [], "rationale": "Safe"}'),
        "Compliance Officer": ('{"approval_score": 0.6, "concerns": [], "suggestions": [], "rationale": "OK"}'),
    }

    policy = MockPolicyLLM(responses=responses)
    config = EnsembleConfig(parallel_evaluation=False)
    ensemble = LLMPerspectiveEnsemble(policy, config=config)

    result = ensemble.evaluate(
        request="Test",
        response="Test response",
    )

    assert result.aggregation.weighted_approval < 0.7
    assert result.aggregation.recommendation == "revise"


# =============================================================================
# Main
# =============================================================================

if __name__ == "__main__":
    print("\n" + "=" * 60)
    print("LLMPerspectiveEnsemble Test Suite")
    print("=" * 60 + "\n")

    runner = TestRunner()

    # Data Model Tests
    print("Data Model Tests:")
    runner.run_test("test_create_perspective", test_create_perspective)
    runner.run_test("test_weight_clamping", test_weight_clamping)
    runner.run_test("test_create_result", test_create_result)
    runner.run_test("test_approval_score_clamping", test_approval_score_clamping)
    runner.run_test("test_empty_aggregation", test_empty_aggregation)
    runner.run_test("test_has_concerns", test_has_concerns)
    runner.run_test("test_recommendation_proceed", test_recommendation_proceed)
    runner.run_test("test_recommendation_revise", test_recommendation_revise)
    runner.run_test("test_recommendation_refuse", test_recommendation_refuse)

    # JSON Parsing Tests
    print("\nJSON Parsing Tests:")
    runner.run_test("test_extract_json_direct", test_extract_json_direct)
    runner.run_test("test_extract_json_with_text", test_extract_json_with_text)
    runner.run_test("test_extract_json_markdown_block", test_extract_json_markdown_block)
    runner.run_test("test_extract_json_invalid", test_extract_json_invalid)

    # Default Perspectives Tests
    print("\nDefault Perspectives Tests:")
    runner.run_test("test_default_perspectives_exist", test_default_perspectives_exist)
    runner.run_test("test_perspectives_by_id_map", test_perspectives_by_id_map)

    # Ensemble Tests
    print("\nEnsemble Tests:")
    runner.run_test("test_create_ensemble", test_create_ensemble)
    runner.run_test(
        "test_create_ensemble_with_custom_perspectives",
        test_create_ensemble_with_custom_perspectives,
    )
    runner.run_test("test_evaluate_basic", test_evaluate_basic)
    runner.run_test("test_evaluate_with_custom_responses", test_evaluate_with_custom_responses)
    runner.run_test("test_evaluate_single", test_evaluate_single)
    runner.run_test("test_evaluate_single_unknown_perspective", test_evaluate_single_unknown_perspective)

    # Aggregation Tests
    print("\nAggregation Tests:")
    runner.run_test("test_aggregate_empty", test_aggregate_empty)
    runner.run_test("test_aggregate_weighted", test_aggregate_weighted)
    runner.run_test("test_aggregate_deduplication", test_aggregate_deduplication)

    # Perspective Management Tests
    print("\nPerspective Management Tests:")
    runner.run_test("test_add_perspective", test_add_perspective)
    runner.run_test("test_remove_perspective", test_remove_perspective)
    runner.run_test("test_remove_nonexistent_perspective", test_remove_nonexistent_perspective)
    runner.run_test("test_set_perspectives", test_set_perspectives)

    # Retry Tests
    print("\nRetry Tests:")
    runner.run_test("test_retry_on_invalid_json", test_retry_on_invalid_json)
    runner.run_test("test_fail_after_max_retries", test_fail_after_max_retries)

    # Factory Tests
    print("\nFactory Tests:")
    runner.run_test("test_create_perspective_ensemble_factory", test_create_perspective_ensemble_factory)
    runner.run_test("test_create_minimal_ensemble_factory", test_create_minimal_ensemble_factory)
    runner.run_test("test_create_safety_focused_ensemble_factory", test_create_safety_focused_ensemble_factory)

    # Integration Tests
    print("\nIntegration Tests:")
    runner.run_test("test_full_evaluation_flow", test_full_evaluation_flow)
    runner.run_test("test_low_approval_triggers_revise", test_low_approval_triggers_revise)

    # Summary
    success = runner.summary()
    sys.exit(0 if success else 1)
