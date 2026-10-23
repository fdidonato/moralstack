"""
Tests for LLMPerspectiveEnsemble.

Unit and integration tests for the perspective module.
"""

import os
import sys

# Add the project path for direct import
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dataclasses import dataclass, field
from typing import Any, Literal

import pytest

# Direct import from the perspective module (without going through __init__)
from moralstack.runtime.modules.perspective_module import (
    DEFAULT_PERSPECTIVES,
    PERSPECTIVES_BY_ID,
    EnsembleConfig,
    EnsembleResult,
    JSONParseError,
    LLMPerspectiveEnsemble,
    Perspective,
    PerspectiveAggregation,
    PerspectiveResult,
    apply_constitutional_override,
    create_minimal_ensemble,
    create_perspective_ensemble,
    create_safety_focused_ensemble,
    extract_json,
    parse_perspective_response,
)

# =============================================================================
# Mock Policy LLM
# =============================================================================


@dataclass
class MockGenerationConfig:
    """Mock config for generation."""

    max_tokens: int = 2048
    temperature: float = 0.7
    top_p: float = 0.9
    stop_sequences: list[str] = field(default_factory=list)


@dataclass
class MockGenerationResult:
    """Mock generation result."""

    text: str
    tokens_used: int = 100
    finish_reason: Literal["stop", "length", "content_filter"] = "stop"
    logprobs: list[float] | None = None


class MockPolicyLLM:
    """Mock Policy LLM for testing."""

    def __init__(self, responses: dict[str, str] | None = None):
        """
        Args:
            responses: Map perspective_name -> JSON response
        """
        self.responses = responses or {}
        self.call_count = 0
        self.last_prompt = ""
        self.last_system = ""

    def generate(self, *args: Any, **kwargs: Any) -> MockGenerationResult:
        """Generate mock response. Accepts prompt=, system=, config= from real caller."""
        self.call_count += 1
        prompt = kwargs.get("prompt", args[0] if args else "")
        system = kwargs.get("system", args[1] if len(args) > 1 else "")
        self.last_prompt = prompt
        self.last_system = system

        # Look for a specific response for the perspective
        for perspective_name, response in self.responses.items():
            if perspective_name in prompt:
                return MockGenerationResult(text=response)

        # Default response
        return MockGenerationResult(
            text='{"approval_score": 0.8, "concerns": [], "suggestions": [], ' '"rationale": "Good response"}'
        )


class FailingMockPolicyLLM:
    """Mock that always generates invalid JSON."""

    def __init__(self, fail_count: int = 999):
        self.fail_count = fail_count
        self.call_count = 0

    def generate(self, *args: Any, **kwargs: Any) -> MockGenerationResult:
        self.call_count += 1
        if self.call_count <= self.fail_count:
            return MockGenerationResult(text="Not valid JSON at all!")
        return MockGenerationResult(text='{"approval_score": 0.5, "concerns": [], "suggestions": [], "rationale": "test"}')


# =============================================================================
# Test Data Models
# =============================================================================


class TestPerspective:
    """Tests for Perspective dataclass."""

    def test_create_perspective(self):
        """Test perspective creation."""
        p = Perspective(
            id="test",
            name="Test Perspective",
            prompt_template="Test template",
            weight=1.0,
        )
        assert p.id == "test"
        assert p.name == "Test Perspective"
        assert p.weight == 1.0

    def test_weight_clamping(self):
        """Test that weight is clamped to range [0, 2]."""
        p1 = Perspective(id="t1", name="T1", prompt_template="", weight=-1.0)
        p2 = Perspective(id="t2", name="T2", prompt_template="", weight=5.0)

        assert p1.weight == 0.0
        assert p2.weight == 2.0


class TestPerspectiveResult:
    """Tests for PerspectiveResult dataclass."""

    def test_create_result(self):
        """Test result creation."""
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

    def test_approval_score_clamping(self):
        """Test that approval score is clamped to range [0, 1]."""
        r1 = PerspectiveResult(perspective_id="t", approval_score=-0.5)
        r2 = PerspectiveResult(perspective_id="t", approval_score=1.5)

        assert r1.approval_score == 0.0
        assert r2.approval_score == 1.0


class TestPerspectiveAggregation:
    """Tests for PerspectiveAggregation dataclass."""

    def test_empty_aggregation(self):
        """Test empty aggregation."""
        agg = PerspectiveAggregation.empty()
        assert agg.weighted_approval == 0.0
        assert agg.perspective_count == 0

    def test_has_concerns(self):
        """Test has_concerns property."""
        agg1 = PerspectiveAggregation(all_concerns=[])
        agg2 = PerspectiveAggregation(all_concerns=["A concern"])

        assert agg1.has_concerns is False
        assert agg2.has_concerns is True

    def test_recommendation_proceed(self):
        """Test proceed recommendation (min_approval >= 0.2 to not
        trigger conservative rule)."""
        agg = PerspectiveAggregation(weighted_approval=0.8, min_approval=0.8)
        assert agg.recommendation == "proceed"

    def test_recommendation_revise(self):
        """Test revise recommendation (min_approval >= 0.3)."""
        agg = PerspectiveAggregation(weighted_approval=0.5, min_approval=0.5)
        assert agg.recommendation == "revise"

    def test_recommendation_refuse(self):
        """Test refuse recommendation."""
        agg = PerspectiveAggregation(weighted_approval=0.2, min_approval=0.2)
        assert agg.recommendation == "refuse"


class TestEnsembleResult:
    """Tests for EnsembleResult dataclass."""

    def test_empty_result(self):
        """Test empty result."""
        result = EnsembleResult.empty()
        assert result.evaluation_count == 0
        assert len(result.results) == 0

    def test_from_error(self):
        """Test result from error."""
        result = EnsembleResult.from_error("Test error")
        assert result.aggregation.weighted_approval == 0.5
        assert "Test error" in result.raw_responses[0]


# =============================================================================
# Test JSON Parsing
# =============================================================================


class TestJSONParsing:
    """Tests for JSON parsing."""

    def test_extract_json_direct(self):
        """Test direct JSON parsing."""
        text = '{"approval_score": 0.9, "concerns": [], "suggestions": [], "rationale": "test"}'
        data = extract_json(text)
        assert data["approval_score"] == 0.9

    def test_extract_json_with_text(self):
        """Test JSON parsing with surrounding text."""
        text = (
            'Here is the evaluation: {"approval_score": 0.7, "concerns": ["c1"], ' '"suggestions": [], "rationale": "test"}'
        )
        data = extract_json(text)
        assert data["approval_score"] == 0.7
        assert "c1" in data["concerns"]

    def test_extract_json_markdown_block(self):
        """Test JSON parsing in markdown block."""
        text = """```json
{"approval_score": 0.6, "concerns": [], "suggestions": [], "rationale": "test"}
```"""
        data = extract_json(text)
        assert data["approval_score"] == 0.6

    def test_extract_json_trailing_comma(self):
        """Test JSON repair with trailing comma."""
        text = '{"approval_score": 0.5, "concerns": ["c1",], "suggestions": [], "rationale": "test"}'
        data = extract_json(text)
        assert data["approval_score"] == 0.5

    def test_extract_json_invalid(self):
        """Test failure on invalid JSON."""
        text = "This is not JSON at all"
        with pytest.raises(JSONParseError):
            extract_json(text)

    def test_parse_perspective_response(self):
        """Test parsing of a complete perspective response."""
        text = """{
            "approval_score": 0.85,
            "concerns": ["Privacy concern", "Accuracy issue"],
            "suggestions": ["Add disclaimer"],
            "rationale": "Good but needs improvement"
        }"""

        perspective = Perspective(id="test", name="Test", prompt_template="")
        result = parse_perspective_response(text, perspective)

        assert result.perspective_id == "test"
        assert result.approval_score == 0.85
        assert len(result.concerns) == 2
        assert len(result.suggestions) == 1
        assert "improvement" in result.rationale


# =============================================================================
# Test Default Perspectives
# =============================================================================


class TestDefaultPerspectives:
    """Tests for default perspectives."""

    def test_default_perspectives_exist(self):
        """Test that default perspectives exist."""
        assert len(DEFAULT_PERSPECTIVES) == 5

        ids = [p.id for p in DEFAULT_PERSPECTIVES]
        assert "user" in ids
        assert "vulnerable" in ids
        assert "observer" in ids
        assert "adversary" in ids
        assert "compliance" in ids

    def test_perspectives_by_id_map(self):
        """Test PERSPECTIVES_BY_ID map."""
        assert "user" in PERSPECTIVES_BY_ID
        assert PERSPECTIVES_BY_ID["user"].name == "Direct User"
        assert PERSPECTIVES_BY_ID["vulnerable"].weight == 1.2
        assert PERSPECTIVES_BY_ID["adversary"].weight == 0.8


# =============================================================================
# Test LLMPerspectiveEnsemble
# =============================================================================


class TestLLMPerspectiveEnsemble:
    """Tests for LLMPerspectiveEnsemble."""

    def test_create_ensemble(self):
        """Test ensemble creation."""
        policy = MockPolicyLLM()
        ensemble = LLMPerspectiveEnsemble(policy)

        assert ensemble.policy == policy
        assert len(ensemble.perspectives) == 5

    def test_create_ensemble_with_custom_perspectives(self):
        """Test creation with custom perspectives."""
        policy = MockPolicyLLM()
        custom = [
            Perspective(id="custom1", name="Custom 1", prompt_template="test"),
            Perspective(id="custom2", name="Custom 2", prompt_template="test"),
        ]

        ensemble = LLMPerspectiveEnsemble(policy, perspectives=custom)
        assert len(ensemble.perspectives) == 2

    def test_evaluate_basic(self):
        """Test basic evaluation."""
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

    def test_evaluate_with_custom_responses(self):
        """Test evaluation with custom responses."""
        responses = {
            "Direct User": ('{"approval_score": 0.9, "concerns": [], "suggestions": [], ' '"rationale": "Excellent"}'),
            "Vulnerable User": (
                '{"approval_score": 0.7, "concerns": ["Could be clearer"], '
                '"suggestions": ["Add context"], "rationale": "OK"}'
            ),
            "Neutral Observer": ('{"approval_score": 0.8, "concerns": [], "suggestions": [], "rationale": "Good"}'),
            "Potential Misuser": ('{"approval_score": 0.95, "concerns": [], "suggestions": [], ' '"rationale": "No risk"}'),
            "Compliance Officer": (
                '{"approval_score": 0.85, "concerns": [], "suggestions": [], ' '"rationale": "Compliant"}'
            ),
        }

        policy = MockPolicyLLM(responses=responses)
        config = EnsembleConfig(parallel_evaluation=False, max_perspectives=5)
        ensemble = LLMPerspectiveEnsemble(policy, config=config)

        result = ensemble.evaluate(
            request="Test request",
            response="Test response",
        )

        assert result.evaluation_count == 5

        # Verify that concerns are aggregated
        assert "Could be clearer" in result.aggregation.all_concerns
        assert "Add context" in result.aggregation.all_suggestions

    def test_evaluate_parallel(self):
        """Test parallel evaluation."""
        policy = MockPolicyLLM()
        config = EnsembleConfig(parallel_evaluation=True, max_workers=3, max_perspectives=5)
        ensemble = LLMPerspectiveEnsemble(policy, config=config)

        result = ensemble.evaluate(
            request="Test request",
            response="Test response",
        )

        assert result.evaluation_count == 5

    def test_evaluate_single(self):
        """Test single-perspective evaluation."""
        policy = MockPolicyLLM()
        ensemble = LLMPerspectiveEnsemble(policy)

        result = ensemble.evaluate_single(
            request="Test",
            response="Test response",
            perspective_id="user",
        )

        assert result is not None
        assert result.perspective_id == "user"

    def test_evaluate_single_unknown_perspective(self):
        """Test evaluation of unknown perspective."""
        policy = MockPolicyLLM()
        ensemble = LLMPerspectiveEnsemble(policy)

        result = ensemble.evaluate_single(
            request="Test",
            response="Test response",
            perspective_id="nonexistent",
        )

        assert result is None

    def test_aggregate_empty(self):
        """Test aggregation of empty list."""
        policy = MockPolicyLLM()
        ensemble = LLMPerspectiveEnsemble(policy)

        agg = ensemble.aggregate([])
        assert agg.weighted_approval == 0.0
        assert agg.perspective_count == 0

    def test_aggregate_weighted(self):
        """Test weighted aggregation."""
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

    def test_aggregate_deduplication(self):
        """Test deduplication of concerns and suggestions."""
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
                concerns=["privacy concern", "New concern"],  # Duplicate (case-insensitive)
                suggestions=["Add disclaimer", "Be clearer"],  # Duplicate
            ),
        ]

        agg = ensemble.aggregate(results)

        # Should be deduplicated
        assert len(agg.all_concerns) == 3  # Privacy, Safety, New
        assert len(agg.all_suggestions) == 2  # Disclaimer, Clearer

    def test_add_perspective(self):
        """Test adding a perspective."""
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

    def test_add_perspective_replaces_existing(self):
        """Test that adding a perspective with the same ID replaces it."""
        policy = MockPolicyLLM()
        ensemble = LLMPerspectiveEnsemble(policy)

        initial_count = len(ensemble.perspectives)

        # Add perspective with existing ID
        replacement = Perspective(
            id="user",
            name="Replaced User",
            prompt_template="New template",
        )

        ensemble.add_perspective(replacement)

        # Count should be equal
        assert len(ensemble.perspectives) == initial_count

        # But the perspective should be replaced
        user_perspective = next(p for p in ensemble.perspectives if p.id == "user")
        assert user_perspective.name == "Replaced User"

    def test_remove_perspective(self):
        """Test removing a perspective."""
        policy = MockPolicyLLM()
        ensemble = LLMPerspectiveEnsemble(policy)

        initial_count = len(ensemble.perspectives)
        removed = ensemble.remove_perspective("user")

        assert removed is True
        assert len(ensemble.perspectives) == initial_count - 1
        assert "user" not in ensemble.get_active_perspectives()

    def test_remove_nonexistent_perspective(self):
        """Test removing a nonexistent perspective."""
        policy = MockPolicyLLM()
        ensemble = LLMPerspectiveEnsemble(policy)

        removed = ensemble.remove_perspective("nonexistent")
        assert removed is False

    def test_set_perspectives(self):
        """Test setting perspectives from ID list."""
        policy = MockPolicyLLM()
        ensemble = LLMPerspectiveEnsemble(policy)

        ensemble.set_perspectives(["user", "compliance"])

        assert len(ensemble.perspectives) == 2
        ids = ensemble.get_active_perspectives()
        assert "user" in ids
        assert "compliance" in ids

    def test_get_active_perspectives(self):
        """Test getting the list of active perspectives."""
        policy = MockPolicyLLM()
        ensemble = LLMPerspectiveEnsemble(policy)

        ids = ensemble.get_active_perspectives()
        assert len(ids) == 5
        assert "user" in ids


class TestLLMPerspectiveEnsembleRetry:
    """Tests for retry on JSON errors."""

    def test_retry_on_invalid_json(self):
        """Test retry when JSON is invalid."""
        # Mock that fails 2 times then succeeds
        policy = FailingMockPolicyLLM(fail_count=2)
        config = EnsembleConfig(max_retries=3, parallel_evaluation=False)

        # Use only one perspective to simplify
        perspectives = [PERSPECTIVES_BY_ID["user"]]
        ensemble = LLMPerspectiveEnsemble(policy, config=config, perspectives=perspectives)

        result = ensemble.evaluate(
            request="Test",
            response="Test response",
        )

        # Should succeed after retry
        assert result.evaluation_count == 1

    def test_fail_after_max_retries(self):
        """Test failure after max retry."""
        policy = FailingMockPolicyLLM(fail_count=999)
        config = EnsembleConfig(max_retries=2, parallel_evaluation=False)

        perspectives = [PERSPECTIVES_BY_ID["user"]]
        ensemble = LLMPerspectiveEnsemble(policy, config=config, perspectives=perspectives)

        result = ensemble.evaluate(
            request="Test",
            response="Test response",
        )

        # Should fail
        assert result.evaluation_count == 0
        assert "user" in result.failed_perspectives


# =============================================================================
# Test Factory Functions
# =============================================================================


class TestFactoryFunctions:
    """Tests for factory functions."""

    def test_create_perspective_ensemble(self):
        """Test base factory."""
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

    def test_create_perspective_ensemble_with_ids(self):
        """Test factory with ID list."""
        policy = MockPolicyLLM()
        ensemble = create_perspective_ensemble(
            policy=policy,
            perspective_ids=["user", "compliance"],
        )

        assert len(ensemble.perspectives) == 2

    def test_create_minimal_ensemble(self):
        """Test minimal ensemble factory."""
        policy = MockPolicyLLM()
        ensemble = create_minimal_ensemble(policy)

        ids = ensemble.get_active_perspectives()
        assert len(ids) == 2
        assert "user" in ids
        assert "compliance" in ids
        assert "compliance" in ids

    def test_create_safety_focused_ensemble(self):
        """Test safety-focused ensemble factory."""
        policy = MockPolicyLLM()
        ensemble = create_safety_focused_ensemble(policy)

        ids = ensemble.get_active_perspectives()
        assert len(ids) == 3
        assert "vulnerable" in ids
        assert "adversary" in ids
        assert "compliance" in ids


# =============================================================================
# Integration Tests
# =============================================================================


class TestIntegration:
    """Integration tests."""

    def test_full_evaluation_flow(self):
        """Test full evaluation flow."""
        # Prepare different responses for each perspective
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
            response=(
                "To learn Python programming, start with online tutorials like "
                "Codecademy or freeCodeCamp. Practice daily with small projects. "
                "Read documentation and join communities like Stack Overflow for help."
            ),
        )

        # Verify results
        assert result.evaluation_count == 5
        assert len(result.failed_perspectives) == 0

        # Verify aggregation
        agg = result.aggregation
        assert agg.weighted_approval > 0.8
        assert agg.recommendation == "proceed"
        assert len(agg.all_concerns) == 1
        assert len(agg.all_suggestions) == 1

        # Verify individual results
        user_result = next(r for r in result.results if r.perspective_id == "user")
        assert user_result.approval_score == 0.9

        vulnerable_result = next(r for r in result.results if r.perspective_id == "vulnerable")
        assert vulnerable_result.approval_score == 0.75
        assert len(vulnerable_result.concerns) == 1

    def test_low_approval_triggers_revise(self):
        """Test that low approval triggers revise recommendation."""
        responses = {
            "Direct User": (
                '{"approval_score": 0.4, "concerns": ["Incomplete answer"], '
                '"suggestions": ["Add more details"], "rationale": "Needs work"}'
            ),
            "Vulnerable User": (
                '{"approval_score": 0.3, "concerns": ["Confusing"], '
                '"suggestions": ["Simplify"], "rationale": "Too complex"}'
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

        # Weighted average should be below 0.7
        assert result.aggregation.weighted_approval < 0.7
        assert result.aggregation.recommendation == "revise"

    def test_very_low_approval_triggers_refuse(self):
        """Test that very low approval triggers refuse recommendation."""
        responses = {
            "Direct User": (
                '{"approval_score": 0.1, "concerns": ["Harmful"], ' '"suggestions": [], "rationale": "Dangerous"}'
            ),
            "Vulnerable User": (
                '{"approval_score": 0.05, "concerns": ["Very harmful"], ' '"suggestions": [], "rationale": "Dangerous"}'
            ),
            "Neutral Observer": (
                '{"approval_score": 0.2, "concerns": ["Problematic"], ' '"suggestions": [], "rationale": "Bad"}'
            ),
            "Potential Misuser": (
                '{"approval_score": 0.1, "concerns": ["Exploitable"], ' '"suggestions": [], "rationale": "Risky"}'
            ),
            "Compliance Officer": (
                '{"approval_score": 0.15, "concerns": ["Violation"], ' '"suggestions": [], "rationale": "Non-compliant"}'
            ),
        }

        policy = MockPolicyLLM(responses=responses)
        config = EnsembleConfig(parallel_evaluation=False)
        ensemble = LLMPerspectiveEnsemble(policy, config=config)

        result = ensemble.evaluate(
            request="Test",
            response="Test response",
        )

        # Weighted average should be below 0.3
        assert result.aggregation.weighted_approval < 0.3
        assert result.aggregation.recommendation == "refuse"


# =============================================================================
# Test apply_constitutional_override
# =============================================================================


class TestApplyConstitutionalOverride:
    """Tests for apply_constitutional_override (EnsembleResult vs PerspectiveAggregation)."""

    def test_ensemble_result_with_hard_violation_caps_approval(self):
        """Passing EnsembleResult with violated_hard=True must not raise and must cap inner aggregation."""
        agg = PerspectiveAggregation(weighted_approval=0.8, min_approval=0.5, all_concerns=[])
        result = EnsembleResult(
            results=[],
            aggregation=agg,
            raw_responses=[],
            evaluation_count=0,
            failed_perspectives=[],
        )
        critic = type("Critic", (), {"violated_hard": True, "violations": []})()
        out = apply_constitutional_override(result, critic)
        assert out is result
        assert result.aggregation.weighted_approval == 0.2
        assert any("Constitutional HARD violation" in c for c in result.aggregation.all_concerns)

    def test_perspective_aggregation_with_hard_violation_caps_approval(self):
        """Passing PerspectiveAggregation with violated_hard=True caps weighted_approval (regression)."""
        agg = PerspectiveAggregation(weighted_approval=0.9, min_approval=0.5, all_concerns=[])
        critic = type("Critic", (), {"violated_hard": True, "violations": []})()
        out = apply_constitutional_override(agg, critic)
        assert out is agg
        assert agg.weighted_approval == 0.2
        assert any("Constitutional HARD violation" in c for c in agg.all_concerns)

    def test_ensemble_result_no_hard_violation_unchanged(self):
        """Passing EnsembleResult with no hard violation returns unchanged."""
        agg = PerspectiveAggregation(weighted_approval=0.8, min_approval=0.5, all_concerns=[])
        result = EnsembleResult(
            results=[],
            aggregation=agg,
            raw_responses=[],
            evaluation_count=0,
            failed_perspectives=[],
        )
        critic = type("Critic", (), {"violated_hard": False, "violations": []})()
        out = apply_constitutional_override(result, critic)
        assert out is result
        assert result.aggregation.weighted_approval == 0.8
        assert not any("Constitutional HARD violation" in c for c in result.aggregation.all_concerns)

    def test_none_critic_returns_unchanged(self):
        """Passing critic_result=None returns aggregation unchanged."""
        agg = PerspectiveAggregation(weighted_approval=0.8, min_approval=0.5, all_concerns=[])
        out = apply_constitutional_override(agg, None)
        assert out is agg
        assert agg.weighted_approval == 0.8


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
