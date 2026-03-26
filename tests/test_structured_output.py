import json

import pytest
from pydantic import ValidationError

from moralstack.utils.structured_output import (
    JSONParseError,
    parse_and_validate_critic_output,
    parse_and_validate_hindsight_batch_output,
    parse_and_validate_hindsight_single_output,
    parse_and_validate_simulator_output,
)

# ============================================================
# CRITIC OUTPUT TESTS (schema: decision, violated_hard, violations, revision_guidance)
# ============================================================


class TestCriticStructuredOutput:
    def test_valid_critic_output_parses_correctly(self):
        raw = """
        {
          "decision": "PROCEED",
          "violated_hard": false,
          "revision_guidance": "",
          "violations": []
        }
        """
        result = parse_and_validate_critic_output(raw)

        assert result.decision == "PROCEED"
        assert result.violated_hard is False
        assert result.revision_guidance == ""
        assert result.violations == []

    def test_critic_with_violations(self):
        raw = """
        {
          "decision": "REVISE",
          "violated_hard": false,
          "revision_guidance": "Avoid harm.",
          "violations": [
            {
              "principle_id": "non_maleficence",
              "severity": 0.7,
              "rationale": "Could cause harm",
              "evidence": "Step 3"
            }
          ]
        }
        """
        result = parse_and_validate_critic_output(raw)

        assert result.decision == "REVISE"
        assert len(result.violations) == 1
        assert result.violations[0].principle_id == "non_maleficence"
        assert result.violations[0].severity == pytest.approx(0.7)
        assert result.violations[0].rationale == "Could cause harm"

    def test_missing_required_field_fails(self):
        raw = """
        {
          "decision": "PROCEED",
          "violated_hard": false
        }
        """
        with pytest.raises(ValidationError):
            parse_and_validate_critic_output(raw)

    def test_invalid_decision_value_fails(self):
        raw = """
        {
          "decision": "ALLOW_ALL",
          "violated_hard": false,
          "revision_guidance": "",
          "violations": []
        }
        """
        with pytest.raises(ValidationError):
            parse_and_validate_critic_output(raw)

    def test_extra_fields_are_rejected(self):
        raw = """
        {
          "decision": "REFUSE",
          "violated_hard": true,
          "revision_guidance": "",
          "violations": [],
          "unexpected": "should not be here"
        }
        """
        with pytest.raises(ValidationError):
            parse_and_validate_critic_output(raw)


# ============================================================
# SIMULATOR OUTPUT TESTS (schema: consequences)
# ============================================================


class TestSimulatorStructuredOutput:
    def test_valid_simulator_output(self):
        raw = """
        {
          "consequences": [
            {
              "text": "User may replicate harmful process",
              "likelihood": 0.8,
              "scenario_type": "downstream_misuse",
              "outcome_valence": -0.9,
              "affected_stakeholders": ["user"],
              "harm_type": "physical_harm",
              "harm_severity": 0.7,
              "harm_scope": "individual",
              "reversibility": 0.3
            }
          ]
        }
        """
        result = parse_and_validate_simulator_output(raw)

        assert len(result.consequences) == 1
        assert result.consequences[0].text == "User may replicate harmful process"
        assert result.consequences[0].likelihood == pytest.approx(0.8)
        assert result.consequences[0].scenario_type == "downstream_misuse"
        assert result.consequences[0].outcome_valence == pytest.approx(-0.9)
        assert result.consequences[0].harm_type == "physical_harm"
        assert result.consequences[0].harm_severity == pytest.approx(0.7)
        assert result.consequences[0].harm_scope == "individual"
        assert result.consequences[0].reversibility == pytest.approx(0.3)

    def test_simulator_new_harm_types_constitutional(self):
        """emotional_manipulation and financial_deception align with constitution."""
        raw = """
        {
          "consequences": [
            {
              "text": "Covert influence on user decisions",
              "likelihood": 0.6,
              "scenario_type": "social_impact",
              "harm_type": "emotional_manipulation",
              "harm_severity": 0.5,
              "harm_scope": "individual",
              "reversibility": 0.4
            },
            {
              "text": "Misleading investment advice",
              "likelihood": 0.5,
              "scenario_type": "legal_consequence",
              "harm_type": "financial_deception",
              "harm_severity": 0.7,
              "harm_scope": "individual",
              "reversibility": 0.2
            }
          ]
        }
        """
        result = parse_and_validate_simulator_output(raw)
        assert result.consequences[0].harm_type == "emotional_manipulation"
        assert result.consequences[1].harm_type == "financial_deception"

    def test_simulator_with_harm_none(self):
        raw = """
        {
          "consequences": [
            {
              "text": "Some positive outcome",
              "likelihood": 0.5,
              "harm_type": "none",
              "harm_severity": 0.0,
              "harm_scope": "individual",
              "reversibility": 1.0
            }
          ]
        }
        """
        result = parse_and_validate_simulator_output(raw)

        assert result.consequences[0].scenario_type == "social_impact"
        assert result.consequences[0].outcome_valence == 0.0
        assert result.consequences[0].affected_stakeholders == []
        assert result.consequences[0].harm_type == "none"
        assert result.consequences[0].harm_severity == 0.0

    def test_simulator_missing_harm_fields_fails(self):
        raw = """
        {
          "consequences": [
            {
              "text": "Some outcome",
              "likelihood": 0.5
            }
          ]
        }
        """
        with pytest.raises(ValidationError):
            parse_and_validate_simulator_output(raw)

    def test_simulator_invalid_harm_type_fails(self):
        raw = """
        {
          "consequences": [
            {
              "text": "x",
              "likelihood": 0.5,
              "harm_type": "invalid_harm",
              "harm_severity": 0.5,
              "harm_scope": "individual",
              "reversibility": 0.5
            }
          ]
        }
        """
        with pytest.raises(ValidationError):
            parse_and_validate_simulator_output(raw)

    def test_simulator_invalid_harm_scope_fails(self):
        raw = """
        {
          "consequences": [
            {
              "text": "x",
              "likelihood": 0.5,
              "harm_type": "none",
              "harm_severity": 0.0,
              "harm_scope": "invalid_scope",
              "reversibility": 0.5
            }
          ]
        }
        """
        with pytest.raises(ValidationError):
            parse_and_validate_simulator_output(raw)

    def test_simulator_harm_severity_clamped(self):
        raw = """
        {
          "consequences": [
            {
              "text": "x",
              "likelihood": 0.5,
              "harm_type": "none",
              "harm_severity": 1.5,
              "harm_scope": "individual",
              "reversibility": -0.2
            }
          ]
        }
        """
        result = parse_and_validate_simulator_output(raw)
        assert result.consequences[0].harm_severity == pytest.approx(1.0)
        assert result.consequences[0].reversibility == pytest.approx(0.0)

    def test_invalid_scenario_type_fails(self):
        raw = """
        {
          "consequences": [
            {
              "text": "x",
              "likelihood": 0.5,
              "scenario_type": "invalid_type",
              "harm_type": "none",
              "harm_severity": 0.0,
              "harm_scope": "individual",
              "reversibility": 0.0
            }
          ]
        }
        """
        with pytest.raises(ValidationError):
            parse_and_validate_simulator_output(raw)

    def test_simulator_extra_fields_in_consequence_rejected(self):
        raw = """
        {
          "consequences": [
            {
              "text": "x",
              "likelihood": 0.5,
              "harm_type": "none",
              "harm_severity": 0.0,
              "harm_scope": "individual",
              "reversibility": 0.0,
              "extra_field": "not_allowed"
            }
          ]
        }
        """
        with pytest.raises(ValidationError):
            parse_and_validate_simulator_output(raw)

    def test_extra_fields_rejected(self):
        raw = """
        {
          "consequences": [],
          "overall_risk": "HIGH"
        }
        """
        with pytest.raises(ValidationError):
            parse_and_validate_simulator_output(raw)


# ============================================================
# HINDSIGHT OUTPUT TESTS (schema: safety, helpfulness, honesty, ...)
# ============================================================


class TestHindsightStructuredOutput:
    def test_valid_hindsight_single_output(self):
        raw = """
        {
          "safety": 0.6,
          "helpfulness": 0.8,
          "honesty": 0.9,
          "harm_probability": 0.1,
          "benefit_probability": 0.7,
          "confidence": 0.78,
          "rationale": "Decision consistent with constitution",
          "scenario_id": "s1"
        }
        """
        result = parse_and_validate_hindsight_single_output(raw)

        assert result.safety == pytest.approx(0.6)
        assert result.helpfulness == pytest.approx(0.8)
        assert result.confidence == pytest.approx(0.78)
        assert result.rationale == "Decision consistent with constitution"

    def test_valid_hindsight_batch_output(self):
        raw = """
        {
          "evaluations": [
            {
              "safety": 0.5,
              "helpfulness": 0.5,
              "honesty": 0.5,
              "confidence": 0.8,
              "rationale": "Ok"
            }
          ]
        }
        """
        result = parse_and_validate_hindsight_batch_output(raw)

        assert len(result.evaluations) == 1
        assert result.evaluations[0].confidence == pytest.approx(0.8)

    def test_hindsight_batch_malformed_evaluations_key_normalized(self):
        """LLM sometimes emits a broken key (newlines/quotes); normalize to ``evaluations``."""
        bad_key = '\n  "evaluations"'
        payload = {
            bad_key: [
                {
                    "safety": 0.5,
                    "helpfulness": 0.5,
                    "honesty": 0.5,
                    "confidence": 0.8,
                    "rationale": "Ok",
                }
            ]
        }
        raw = json.dumps(payload)
        result = parse_and_validate_hindsight_batch_output(raw)
        assert len(result.evaluations) == 1
        assert result.evaluations[0].safety == pytest.approx(0.5)

    def test_hindsight_batch_duplicate_malformed_key_dropped_for_extra_forbid(self):
        """Canonical ``evaluations`` plus a malformed duplicate must not raise extra-field errors."""
        bad_key = '\n  "evaluations"'
        payload = {
            "evaluations": [
                {
                    "safety": 0.6,
                    "helpfulness": 0.6,
                    "honesty": 0.6,
                    "confidence": 0.8,
                    "rationale": "Primary",
                }
            ],
            bad_key: [
                {
                    "safety": 0.1,
                    "helpfulness": 0.1,
                    "honesty": 0.1,
                    "confidence": 0.5,
                    "rationale": "Duplicate",
                }
            ],
        }
        raw = json.dumps(payload)
        result = parse_and_validate_hindsight_batch_output(raw)
        assert len(result.evaluations) == 1
        assert result.evaluations[0].rationale == "Primary"

    def test_hindsight_batch_smart_quotes_in_key_normalized(self):
        """Unicode curly quotes around *evaluations* in the dict key."""
        bad_key = "\n  \u201cevaluations\u201d"
        payload = {
            bad_key: [
                {
                    "safety": 0.4,
                    "helpfulness": 0.4,
                    "honesty": 0.4,
                    "confidence": 0.7,
                    "rationale": "x",
                }
            ]
        }
        raw = json.dumps(payload)
        result = parse_and_validate_hindsight_batch_output(raw)
        assert len(result.evaluations) == 1
        assert result.evaluations[0].safety == pytest.approx(0.4)

    def test_hindsight_batch_root_array_wrapped(self):
        """Some models return a bare JSON array of evaluation objects."""
        raw = """
        [
          {
            "safety": 0.55,
            "helpfulness": 0.55,
            "honesty": 0.55,
            "confidence": 0.9,
            "rationale": "array root"
          }
        ]
        """
        result = parse_and_validate_hindsight_batch_output(raw)
        assert len(result.evaluations) == 1
        assert "array root" in result.evaluations[0].rationale

    def test_hindsight_single_defaults(self):
        raw = """
        {
          "safety": 0.0,
          "helpfulness": 0.0,
          "honesty": 0.0
        }
        """
        result = parse_and_validate_hindsight_single_output(raw)
        assert result.confidence == pytest.approx(0.8)
        assert result.scenario_id == "default"
        assert result.rationale == ""


# ============================================================
# GENERIC / EDGE CASES
# ============================================================


class TestStructuredOutputEdgeCases:
    def test_non_json_input_fails_cleanly(self):
        raw = "this is not json at all"
        with pytest.raises(JSONParseError):
            parse_and_validate_critic_output(raw)

    def test_empty_string_fails(self):
        with pytest.raises(JSONParseError):
            parse_and_validate_critic_output("")

    def test_markdown_wrapped_json_is_supported(self):
        raw = """
        ```json
        {
          "decision": "PROCEED",
          "violated_hard": false,
          "revision_guidance": "",
          "violations": []
        }
        ```
        """
        result = parse_and_validate_critic_output(raw)
        assert result.decision == "PROCEED"
