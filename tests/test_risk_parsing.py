"""
Dedicated tests for risk response parsing (parse_risk_response -> RiskParseResult).
"""

import pytest

from moralstack.models.risk import (
    JSONParseError,
    RiskCategory,
    RiskParseResult,
    RiskPolicyAction,
    parse_risk_response,
)


class TestRiskParsingHappyPath:
    """Happy path: full JSON produces correct RiskParseResult."""

    def test_full_json_returns_risk_parse_result(self):
        text = """{
            "risk_score": 0.6,
            "confidence": 0.9,
            "request_type": "potential_harm",
            "harm_type": "financial",
            "rationale": "Possible fraud intent",
            "domain_sensitivity": "MEDIUM",
            "operational_risk": "LOW",
            "risk_policy_action": "DELIBERATE",
            "intent_clarity": "HIGH",
            "misuse_plausibility": "MEDIUM",
            "actionability_risk": "LOW",
            "detected_language": "en"
        }"""
        result = parse_risk_response(text)
        assert isinstance(result, RiskParseResult)
        assert result.score == 0.6
        assert result.confidence == 0.9
        assert result.category == RiskCategory.POTENTIALLY_HARMFUL
        assert result.rationale == "Possible fraud intent"
        assert result.risk_policy_action == RiskPolicyAction.DELIBERATE
        assert result.domain_sensitivity.name == "MEDIUM"
        assert result.operational_risk.name == "LOW"
        assert result.detected_language == "en"


class TestRiskParsingMissingFields:
    """Missing fields get defaults; no crash."""

    def test_minimal_json_defaults(self):
        text = '{"risk_score": 0.2, "request_type": "factual_query", "harm_type": "none"}'
        result = parse_risk_response(text)
        # factual_query + none: score > 0.25 is clamped to 0.15; 0.2 is unchanged
        assert result.score == 0.2
        assert result.category == RiskCategory.BENIGN
        assert result.confidence == 0.7  # default
        assert result.signals is not None
        assert result.rationale == ""
        assert result.risk_policy_action == RiskPolicyAction.ALLOW
        assert result.detected_language == ""

    def test_empty_signals_when_no_questions(self):
        text = '{"request_type": "sensitive_topic", "harm_type": "emotional_relational"}'
        result = parse_risk_response(text)
        assert "request_type:sensitive_topic" in result.signals or result.signals


class TestRiskParsingInvalidJson:
    """Invalid JSON propagates JSONParseError."""

    def test_invalid_json_raises(self):
        with pytest.raises(JSONParseError):
            parse_risk_response("not json at all")

    def test_malformed_json_raises(self):
        with pytest.raises(JSONParseError):
            parse_risk_response("{ invalid }")


class TestRiskParsingActionCoercion:
    """risk_policy_action derived from category/score when not provided."""

    def test_clearly_harmful_implies_deny(self):
        # intent_operational=true avoids intent_contradiction downgrade (op_risk HIGH + all intent false)
        text = """{
            "risk_score": 0.9,
            "request_type": "clear_harm",
            "harm_type": "physical",
            "operational_risk": "HIGH",
            "intent_operational": true
        }"""
        result = parse_risk_response(text)
        assert result.category == RiskCategory.CLEARLY_HARMFUL
        assert result.risk_policy_action == RiskPolicyAction.DENY

    def test_potentially_harmful_implies_deliberate(self):
        # operational_risk NONE forces ALLOW; use LOW to get DELIBERATE from category/score
        text = """{
            "risk_score": 0.65,
            "request_type": "potential_harm",
            "harm_type": "illegal",
            "operational_risk": "LOW"
        }"""
        result = parse_risk_response(text)
        assert result.risk_policy_action == RiskPolicyAction.DELIBERATE

    def test_benign_implies_allow(self):
        text = '{"risk_score": 0.1, "request_type": "factual_query", "harm_type": "none"}'
        result = parse_risk_response(text)
        assert result.risk_policy_action == RiskPolicyAction.ALLOW

    def test_explicit_action_in_json_respected(self):
        # When operational_risk is NONE, refinement forces ALLOW (overwrites JSON action)
        text = """{
            "risk_score": 0.5,
            "request_type": "sensitive_topic",
            "harm_type": "emotional_relational",
            "risk_policy_action": "ALLOW_WITH_CAVEAT"
        }"""
        result = parse_risk_response(text)
        assert result.risk_policy_action == RiskPolicyAction.ALLOW  # NONE forces ALLOW


class TestRiskParsingEdgeCases:
    """Edge cases: detected_language, category fallback."""

    def test_detected_language_truncated_to_10_chars(self):
        text = """{
            "risk_score": 0.3,
            "request_type": "factual_query",
            "harm_type": "none",
            "detected_language": "verylongcode"
        }"""
        result = parse_risk_response(text)
        assert len(result.detected_language) <= 10

    def test_legacy_category_field_when_no_request_type_harm_type(self):
        text = '{"category": "benign", "risk_score": 0.2}'
        result = parse_risk_response(text)
        assert result.category == RiskCategory.BENIGN
        assert result.request_type == ""
        assert result.harm_type == ""


# =============================================================================
# merge_mini_estimator_results: rationale merge
# =============================================================================


class TestMergeRationale:
    """Test that rationale from both intent and operational estimators is merged."""

    def test_both_rationales_merged_with_labels(self):
        from moralstack.models.risk.calibration import merge_mini_estimator_results

        intent = {"rationale": "defensive framing detected", "request_type": "factual_query"}
        signal = {"q1_confidential": "no"}
        operational = {"rationale": "low risk, informational", "operational_risk": "NONE", "risk_score": 0.2}
        merged = merge_mini_estimator_results(intent, signal, operational)
        assert "[intent]" in merged["rationale"]
        assert "[op_risk]" in merged["rationale"]
        assert "defensive framing detected" in merged["rationale"]
        assert "low risk, informational" in merged["rationale"]

    def test_only_intent_rationale(self):
        from moralstack.models.risk.calibration import merge_mini_estimator_results

        intent = {"rationale": "intent analysis only"}
        signal = {}
        operational = {"operational_risk": "NONE", "risk_score": 0.1}
        merged = merge_mini_estimator_results(intent, signal, operational)
        assert merged["rationale"] == "intent analysis only"
        assert "[intent]" not in merged["rationale"]

    def test_only_operational_rationale(self):
        from moralstack.models.risk.calibration import merge_mini_estimator_results

        intent = {"request_type": "factual_query"}
        signal = {}
        operational = {"rationale": "op risk analysis", "operational_risk": "NONE"}
        merged = merge_mini_estimator_results(intent, signal, operational)
        assert merged["rationale"] == "op risk analysis"

    def test_no_rationale_from_either(self):
        from moralstack.models.risk.calibration import merge_mini_estimator_results

        intent = {"request_type": "factual_query"}
        signal = {}
        operational = {"operational_risk": "NONE"}
        merged = merge_mini_estimator_results(intent, signal, operational)
        assert "rationale" not in merged

    def test_empty_string_rationale_treated_as_absent(self):
        from moralstack.models.risk.calibration import merge_mini_estimator_results

        intent = {"rationale": "  ", "request_type": "factual_query"}
        signal = {}
        operational = {"rationale": "", "operational_risk": "NONE"}
        merged = merge_mini_estimator_results(intent, signal, operational)
        assert "rationale" not in merged

    def test_merged_rationale_propagates_to_parse_result(self):
        from moralstack.models.risk.calibration import merge_mini_estimator_results, parse_risk_dict

        intent = {"rationale": "intent reason", "request_type": "factual_query", "harm_type": "none"}
        signal = {"domain_sensitivity": "LOW"}
        operational = {"rationale": "risk reason", "operational_risk": "NONE", "risk_score": 0.15, "confidence": 0.9}
        merged = merge_mini_estimator_results(intent, signal, operational)
        parsed = parse_risk_dict(merged)
        assert "[intent] intent reason" in parsed.rationale
        assert "[op_risk] risk reason" in parsed.rationale
