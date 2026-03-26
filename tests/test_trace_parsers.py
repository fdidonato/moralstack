"""
Unit tests for trace parsers in MoralStack CLI.

Focus: regression tests for call string parsing in _build_trace_from_calls.
Representative strings derived from expected LLM output formats.
"""

from moralstack.cli.run import (
    DecisionReason,
    PhaseType,
    _parse_critic_trace,
    _parse_hindsight_trace,
    _parse_perspectives_trace,
    _parse_policy_trace,
    _parse_risk_trace,
    _parse_simulator_trace,
)

# =============================================================================
# Risk trace
# =============================================================================


class TestParseRiskTrace:
    """Tests for _parse_risk_trace."""

    def test_low_risk_fast_path(self):
        """Low risk (< 0.3) yields FAST PATH."""
        call = {"response": "Risk: 0.15, Category: benign"}
        r = _parse_risk_trace(call)
        assert r.phase_type == PhaseType.RISK_ESTIMATION
        assert r.decision == "FAST PATH"
        assert r.decision_reason == DecisionReason.LOW_RISK.value
        assert r.risk_score == 0.15
        assert r.risk_category == "benign"
        assert r.details["risk_score"] == "0.15"
        assert r.details["category"] == "benign"

    def test_medium_risk_deliberative(self):
        """Medium risk (0.3-0.7) yields DELIBERATIVE (1 cycle)."""
        call = {"response": "Risk: 0.45, Category: sensitive"}
        r = _parse_risk_trace(call)
        assert r.decision == "DELIBERATIVE (1 cycle)"
        assert r.decision_reason == DecisionReason.MEDIUM_RISK.value
        assert r.risk_score == 0.45

    def test_high_risk_full_deliberation(self):
        """High risk (> 0.7) yields DELIBERATIVE (full)."""
        call = {"response": "Risk: 0.85, Category: harmful"}
        r = _parse_risk_trace(call)
        assert r.decision == "DELIBERATIVE (full)"
        assert r.decision_reason == DecisionReason.HIGH_RISK.value
        assert r.risk_score == 0.85

    def test_moral_dilemma_ethical_path(self):
        """morally_nuanced category yields DELIBERATIVE (ethical)."""
        call = {"response": "Risk: 0.2, Category: morally_nuanced"}
        r = _parse_risk_trace(call)
        assert r.decision == "DELIBERATIVE (ethical)"
        assert "moral deliberation" in (r.decision_reason or "")

    def test_uses_full_response_when_available(self):
        """Prefers full_response over response."""
        call = {"response": "Risk: 0.1", "full_response": "Risk: 0.9, Category: harmful"}
        r = _parse_risk_trace(call)
        assert r.risk_score == 0.9
        assert r.decision == "DELIBERATIVE (full)"


# =============================================================================
# Policy trace
# =============================================================================


class TestParsePolicyTrace:
    """Tests for _parse_policy_trace."""

    def test_generate_draft_generated(self):
        """generate action yields DRAFT GENERATED and draft revision."""
        call = {"action": "generate", "response": "Hello world"}
        r = _parse_policy_trace(call, current_cycle=0)
        assert r is not None
        assert r.phase_type == PhaseType.GENERATION
        assert r.decision == "DRAFT GENERATED"
        assert len(r.draft_revisions) == 1
        assert r.draft_revisions[0].draft_text == "Hello world"
        assert r.draft_revisions[0].is_initial is True

    def test_generate_fast_path_direct(self):
        """generate with fast_path yields DIRECT GENERATION."""
        call = {"action": "generate_fast_path", "response": "Hi"}
        r = _parse_policy_trace(call, current_cycle=0)
        assert r is not None
        assert r.decision == "DIRECT GENERATION"

    def test_rewrite_revised_with_guidance(self):
        """rewrite action yields REVISED and revision with guidance."""
        call = {
            "action": "rewrite",
            "prompt": "Guidance: Add a disclaimer",
            "response": "Revised text",
        }
        r = _parse_policy_trace(call, current_cycle=1)
        assert r is not None
        assert r.phase_type == PhaseType.REVISION
        assert r.decision == "REVISED"
        assert r.draft_revisions[0].guidance_used == "Add a disclaimer"
        assert r.draft_revisions[0].is_initial is False

    def test_unmatched_action_returns_none(self):
        """Action without generate/rewrite returns None."""
        assert _parse_policy_trace({"action": "other"}, 0) is None


# =============================================================================
# Critic trace
# =============================================================================


class TestParseCriticTrace:
    """Tests for _parse_critic_trace."""

    def test_no_violations_approved(self):
        """Zero violations yields APPROVED."""
        call = {"response": "Violations: 0, Severity score: 0.0"}
        r = _parse_critic_trace(call)
        assert r.phase_type == PhaseType.CRITIQUE
        assert r.decision == "APPROVED"
        assert r.decision_reason == DecisionReason.NO_VIOLATIONS.value
        assert r.details["violations_count"] == 0

    def test_critical_violations(self):
        """Critical violations: True yields CRITICAL VIOLATION."""
        call = {"response": "Violations: 2, Critical violations: True\nSeverity score: 0.8"}
        r = _parse_critic_trace(call)
        assert r.decision == "CRITICAL VIOLATION"
        assert r.decision_reason == DecisionReason.CRITICAL_VIOLATION.value

    def test_soft_violations(self):
        """Violations without critical yields SOFT VIOLATIONS."""
        call = {"response": "Violations: 1, Critical violations: False\nGuidance: Add caveat"}
        r = _parse_critic_trace(call)
        assert r.decision == "SOFT VIOLATIONS"
        assert r.decision_reason == DecisionReason.SOFT_VIOLATION.value
        assert "Add caveat" in r.details.get("guidance", "")

    def test_guidance_refuse_overrides_approved(self):
        """Guidance starting with REFUSE yields CRITICAL VIOLATION."""
        call = {"response": "Violations: 0\nGuidance: REFUSE: harmful content"}
        r = _parse_critic_trace(call)
        assert r.decision == "CRITICAL VIOLATION"
        assert "REFUSE" in (r.decision_reason or "")

    def test_error_in_action_appends_error(self):
        """ERROR in action adds error to list."""
        call = {"action": "ERROR", "response": "Violations: 0"}
        r = _parse_critic_trace(call)
        assert "Error during constitutional critique" in r.errors


# =============================================================================
# Simulator trace
# =============================================================================


class TestParseSimulatorTrace:
    """Tests for _parse_simulator_trace."""

    def test_positive_valence(self):
        """Expected valence > 0.5 yields POSITIVE OUTLOOK."""
        call = {"response": "Expected valence: 0.7, Semantic harm: 0.1"}
        r = _parse_simulator_trace(call)
        assert r.phase_type == PhaseType.SIMULATION
        assert r.decision == "POSITIVE OUTLOOK"
        assert r.details["expected_valence"] == "0.7"
        assert r.details["semantic_harm"] == "0.1"

    def test_mixed_valence(self):
        """Expected valence in (0, 0.5] yields MIXED OUTLOOK."""
        call = {"response": "Expected valence: 0.3"}
        r = _parse_simulator_trace(call)
        assert r.decision == "MIXED OUTLOOK"

    def test_negative_valence(self):
        """Expected valence <= 0 yields NEGATIVE OUTLOOK."""
        call = {"response": "Expected valence: -0.2"}
        r = _parse_simulator_trace(call)
        assert r.decision == "NEGATIVE OUTLOOK"

    def test_dominant_harms_and_worst_case(self):
        """Parses Dominant harms, Worst harm, Consequences, Worst case."""
        call = {"response": ("Dominant harms: physical, Worst harm: severe\n" "Consequences: 3, Worst case: User harmed")}
        r = _parse_simulator_trace(call)
        assert r.details.get("dominant_harms") == "physical"
        assert r.details.get("worst_harm") == "severe"
        assert r.details.get("scenarios_count") == "3"
        assert r.details.get("worst_case") == "User harmed"


# =============================================================================
# Hindsight trace
# =============================================================================


class TestParseHindsightTrace:
    """Tests for _parse_hindsight_trace."""

    def test_proceed_recommendation(self):
        """Recommendation: proceed yields PROCEED."""
        call = {"response": "Expected value: 0.8\nRecommendation: proceed"}
        r = _parse_hindsight_trace(call)
        assert r.phase_type == PhaseType.HINDSIGHT
        assert r.decision == "PROCEED"
        assert r.decision_reason == DecisionReason.HINDSIGHT_POSITIVE.value
        assert r.details["expected_value"] == "0.8"
        assert r.details["recommendation"] == "proceed"

    def test_revise_recommendation(self):
        """Recommendation: revise yields REVISE."""
        call = {"response": "Recommendation: revise"}
        r = _parse_hindsight_trace(call)
        assert r.decision == "REVISE"
        assert "revision" in (r.decision_reason or "").lower()

    def test_refuse_recommendation(self):
        """Recommendation other than proceed/revise yields REFUSE."""
        call = {"response": "Recommendation: refuse"}
        r = _parse_hindsight_trace(call)
        assert r.decision == "REFUSE"
        assert r.decision_reason == DecisionReason.HINDSIGHT_NEGATIVE.value


# =============================================================================
# Perspectives trace
# =============================================================================


class TestParsePerspectivesTrace:
    """Tests for _parse_perspectives_trace."""

    def test_high_approval(self):
        """Weighted approval >= 0.8 yields HIGH APPROVAL."""
        call = {"response": "Weighted approval: 0.9\nMin approval: 0.7"}
        r = _parse_perspectives_trace(call)
        assert r.phase_type == PhaseType.PERSPECTIVES
        assert r.decision == "HIGH APPROVAL"
        assert r.details["weighted_approval"] == "0.9"
        assert r.details["min_approval"] == "0.7"

    def test_moderate_approval(self):
        """Weighted approval in [0.6, 0.8) yields MODERATE APPROVAL."""
        call = {"response": "Weighted approval: 0.65"}
        r = _parse_perspectives_trace(call)
        assert r.decision == "MODERATE APPROVAL"

    def test_low_approval(self):
        """Weighted approval < 0.6 yields LOW APPROVAL."""
        call = {"response": "Weighted approval: 0.4"}
        r = _parse_perspectives_trace(call)
        assert r.decision == "LOW APPROVAL"
