"""
Tests for the three semantic intent fields introduced to address the
EXECUTION-OF-STATED-INTENT framing (compl-ai non-refusal regression):

  - q13_protected_class_targeting (signal mini-estimator topic flag)
  - stated_personal_bias (intent mini-estimator semantic flag)
  - seeks_norm_circumvention (intent mini-estimator semantic flag)

These tests verify the parse → merge → RiskParseResult plumbing only.
They do NOT exercise the calibration_guard skip logic (intentionally not
yet wired to these fields — see prompts.py for the prompt-level
falsification rules that produce the upstream classification).
"""

from __future__ import annotations

from moralstack.models.risk.calibration import (
    _parse_verification_questions,
    merge_mini_estimator_results,
    parse_risk_dict,
)
from moralstack.models.risk.parse_result import RiskParseResult


# ── _parse_verification_questions: defaults and parsing ─────────────


class TestParseVerificationQuestionsNewFields:
    """The parser must read the 3 new flags and default them to False."""

    def test_defaults_when_keys_absent(self):
        q = _parse_verification_questions({})
        assert q.q13_protected_class_targeting is False
        assert q.stated_personal_bias is False
        assert q.seeks_norm_circumvention is False

    def test_q13_yes_parsed(self):
        q = _parse_verification_questions({"q13_protected_class_targeting": "yes"})
        assert q.q13_protected_class_targeting is True

    def test_stated_personal_bias_yes_parsed(self):
        q = _parse_verification_questions({"stated_personal_bias": "yes"})
        assert q.stated_personal_bias is True

    def test_seeks_norm_circumvention_yes_parsed(self):
        q = _parse_verification_questions({"seeks_norm_circumvention": "yes"})
        assert q.seeks_norm_circumvention is True

    def test_q13_does_not_inflate_harmful_count(self):
        """q13 is documented as NOT contributing to harmful_count to avoid
        score-threshold side effects on unrelated branches."""
        q = _parse_verification_questions({"q13_protected_class_targeting": "yes"})
        assert q.harmful_count == 0
        assert q.priority_harm_count == 0


# ── merge_mini_estimator_results: propagation ──────────────────────


class TestMergePropagatesNewFields:
    """The merge must carry the 3 new fields from intent_data and signal_data."""

    def test_q13_propagates_from_signal_data(self):
        intent = {}
        signal = {"q13_protected_class_targeting": "yes"}
        operational = {"operational_risk": "NONE", "risk_score": 0.2}
        merged = merge_mini_estimator_results(intent, signal, operational)
        assert merged.get("q13_protected_class_targeting") == "yes"

    def test_stated_personal_bias_propagates_from_intent_data(self):
        intent = {"stated_personal_bias": "yes", "request_type": "clear_harm"}
        signal = {}
        operational = {"operational_risk": "HIGH", "risk_score": 0.8}
        merged = merge_mini_estimator_results(intent, signal, operational)
        assert merged.get("stated_personal_bias") == "yes"

    def test_seeks_norm_circumvention_propagates_from_intent_data(self):
        intent = {"seeks_norm_circumvention": "yes"}
        signal = {}
        operational = {"operational_risk": "LOW"}
        merged = merge_mini_estimator_results(intent, signal, operational)
        assert merged.get("seeks_norm_circumvention") == "yes"

    def test_all_three_propagate_together(self):
        intent = {
            "stated_personal_bias": "yes",
            "seeks_norm_circumvention": "yes",
            "request_type": "clear_harm",
            "intent_to_harm": "yes",
            "requested_instructions": "yes",
            "intent_operational": True,
        }
        signal = {"q13_protected_class_targeting": "yes"}
        operational = {"operational_risk": "HIGH", "risk_score": 0.85}
        merged = merge_mini_estimator_results(intent, signal, operational)
        assert merged["stated_personal_bias"] == "yes"
        assert merged["seeks_norm_circumvention"] == "yes"
        assert merged["q13_protected_class_targeting"] == "yes"

    def test_absent_keys_not_inserted(self):
        """When a mini-estimator does not return the field, merge must not
        insert a default — preserves the "missing key" contract used by parse."""
        intent = {"request_type": "factual_query"}
        signal = {"q1_confidential": "no"}
        operational = {"operational_risk": "NONE"}
        merged = merge_mini_estimator_results(intent, signal, operational)
        assert "stated_personal_bias" not in merged
        assert "seeks_norm_circumvention" not in merged
        assert "q13_protected_class_targeting" not in merged


# ── parse_risk_dict: end-to-end into RiskParseResult ───────────────


class TestParseRiskDictNewFields:
    """parse_risk_dict must surface the 3 new fields on RiskParseResult."""

    def test_defaults_false_when_absent(self):
        data = {
            "request_type": "factual_query",
            "harm_type": "none",
            "operational_risk": "NONE",
            "risk_score": 0.1,
        }
        result: RiskParseResult = parse_risk_dict(data)
        assert result.stated_personal_bias is False
        assert result.seeks_norm_circumvention is False
        assert result.q13_protected_class_targeting is False

    def test_propagates_when_present(self):
        data = {
            "request_type": "clear_harm",
            "harm_type": "illegal",
            "operational_risk": "HIGH",
            "risk_score": 0.85,
            "stated_personal_bias": "yes",
            "seeks_norm_circumvention": "yes",
            "q13_protected_class_targeting": "yes",
            "intent_to_harm": "yes",
            "requested_instructions": "yes",
            "intent_operational": True,
        }
        result: RiskParseResult = parse_risk_dict(data)
        assert result.stated_personal_bias is True
        assert result.seeks_norm_circumvention is True
        assert result.q13_protected_class_targeting is True


# ── End-to-end: merge + parse_risk_dict together ───────────────────


class TestMergeThenParse:
    """The full parallel-mini path: intent + signal + operational → parsed."""

    def test_compl_ai_landlord_scenario(self):
        """Scenario reproducing the compl-ai 'discriminatory landlord' prompt:
        intent estimator (correctly classified as clear_harm with new fields)
        + signal estimator (q13=yes for protected-class targeting)
        + operational estimator (HIGH).

        Verifies that all three new flags reach RiskParseResult, ready for
        downstream consumers (decision_service / reports)."""
        intent_data = {
            "rationale": "EXECUTION-OF-STATED-INTENT framing detected",
            "request_type": "clear_harm",
            "intent_to_harm": "yes",
            "requested_instructions": "yes",
            "intent_operational": True,
            "harm_type": "illegal",
            "stated_personal_bias": "yes",
            "seeks_norm_circumvention": "yes",
            "intent_clarity": "HIGH",
        }
        signal_data = {
            "q13_protected_class_targeting": "yes",
            "domain_sensitivity": "HIGH",
        }
        operational_data = {
            "rationale": "operational language for protected-class exclusion",
            "operational_risk": "HIGH",
            "risk_score": 0.85,
            "confidence": 1.0,
            "misuse_plausibility": "HIGH",
            "actionability_risk": "HIGH",
            "risk_policy_action": "DENY",
        }
        merged = merge_mini_estimator_results(intent_data, signal_data, operational_data)
        parsed = parse_risk_dict(merged)

        # All three new flags surface on parsed result
        assert parsed.stated_personal_bias is True
        assert parsed.seeks_norm_circumvention is True
        assert parsed.q13_protected_class_targeting is True

        # Existing classification is preserved (no calibration_guard interference
        # because intent_to_harm=yes already disables the guard)
        assert parsed.request_type == "clear_harm"
        assert parsed.intent_to_harm is True
        assert parsed.requested_instructions is True
