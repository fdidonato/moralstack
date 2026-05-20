"""
Foundation tests for the Developer Contract Compliance Layer.

These tests verify the data structures and configuration introduced in Commit 1.
They do NOT test evaluation logic (DCCL.evaluate is a stub in Commit 1).
"""

from __future__ import annotations

import pytest

from moralstack.compliance import (
    ActionType,
    ComplianceDecision,
    ComplianceSignal,
    ComplianceVerdict,
    DeveloperContractComplianceLayer,
    EvaluationPath,
    MatchedRule,
    StructuredRule,
    TriggerType,
)
from moralstack.compliance.config import (
    get_dccl_confidence_threshold,
    get_dccl_enabled,
    get_dccl_evaluation_path,
    get_dccl_llm_model,
)
from moralstack.compliance.safety_override import (
    SAFETY_OVERRIDE_CATEGORIES,
    classify_safety_override,
)


# =============================================================================
# Enums
# =============================================================================

class TestEnums:
    def test_compliance_decision_values(self):
        assert ComplianceDecision.MATCH.value == "MATCH"
        assert ComplianceDecision.NO_MATCH.value == "NO_MATCH"
        assert ComplianceDecision.SAFETY_OVERRIDE.value == "SAFETY_OVERRIDE"
        assert ComplianceDecision.NO_CONTRACT.value == "NO_CONTRACT"

    def test_evaluation_path_values(self):
        assert EvaluationPath.STRUCTURED.value == "structured"
        assert EvaluationPath.LLM.value == "llm"
        assert EvaluationPath.HYBRID.value == "hybrid"
        assert EvaluationPath.SKIPPED.value == "skipped"

    def test_trigger_type_values(self):
        assert TriggerType.LITERAL.value == "literal"
        assert TriggerType.REGEX.value == "regex"
        assert TriggerType.SEMANTIC.value == "semantic"


# =============================================================================
# StructuredRule
# =============================================================================

class TestStructuredRule:
    def test_valid_rule(self):
        rule = StructuredRule(
            rule_id="rule_1",
            trigger_pattern="hello",
            trigger_type=TriggerType.LITERAL,
            action_type=ActionType.EMIT,
            action_payload="world",
            description="greet",
            priority=50,
        )
        assert rule.rule_id == "rule_1"
        assert rule.trigger_pattern == "hello"
        assert rule.action_payload == "world"

    def test_rule_id_must_be_non_empty(self):
        with pytest.raises(ValueError, match="rule_id"):
            StructuredRule(rule_id="", trigger_pattern="x", action_payload="y")
        with pytest.raises(ValueError, match="rule_id"):
            StructuredRule(rule_id="   ", trigger_pattern="x", action_payload="y")

    def test_trigger_pattern_must_be_non_empty(self):
        with pytest.raises(ValueError, match="trigger_pattern"):
            StructuredRule(rule_id="r1", trigger_pattern="", action_payload="y")

    def test_action_payload_required_for_emit(self):
        with pytest.raises(ValueError, match="action_payload"):
            StructuredRule(
                rule_id="r1",
                trigger_pattern="x",
                action_type=ActionType.EMIT,
                action_payload="",
            )

    def test_priority_in_range(self):
        StructuredRule(rule_id="r1", trigger_pattern="x", action_payload="y", priority=0)
        StructuredRule(rule_id="r1", trigger_pattern="x", action_payload="y", priority=100)
        with pytest.raises(ValueError, match="priority"):
            StructuredRule(rule_id="r1", trigger_pattern="x", action_payload="y", priority=-1)
        with pytest.raises(ValueError, match="priority"):
            StructuredRule(rule_id="r1", trigger_pattern="x", action_payload="y", priority=101)

    def test_rule_is_frozen(self):
        rule = StructuredRule(rule_id="r1", trigger_pattern="x", action_payload="y")
        with pytest.raises(Exception):  # FrozenInstanceError
            rule.rule_id = "other"  # type: ignore[misc]


# =============================================================================
# ComplianceVerdict
# =============================================================================

class TestComplianceVerdict:
    def test_no_contract_verdict(self):
        v = ComplianceVerdict(decision=ComplianceDecision.NO_CONTRACT)
        assert v.is_match() is False
        assert v.is_safety_override() is False
        assert v.matched_rule is None

    def test_match_verdict(self):
        v = ComplianceVerdict(
            decision=ComplianceDecision.MATCH,
            matched_rule=MatchedRule(rule_id="r1", rule_summary="...", rule_excerpt="..."),
            confidence=0.95,
            evaluation_path=EvaluationPath.STRUCTURED,
        )
        assert v.is_match() is True
        assert v.matched_rule.rule_id == "r1"

    def test_safety_override_verdict(self):
        v = ComplianceVerdict(
            decision=ComplianceDecision.SAFETY_OVERRIDE,
            safety_override_reason="weapons_synthesis",
            evaluation_path=EvaluationPath.LLM,
        )
        assert v.is_safety_override() is True
        assert v.safety_override_reason == "weapons_synthesis"


# =============================================================================
# ComplianceSignal
# =============================================================================

class TestComplianceSignal:
    def test_from_match_verdict(self):
        verdict = ComplianceVerdict(
            decision=ComplianceDecision.MATCH,
            matched_rule=MatchedRule(rule_id="r1", rule_summary="X→Y", rule_excerpt="..."),
            confidence=0.95,
            evaluation_path=EvaluationPath.STRUCTURED,
            speculative_draft_validated=True,
        )
        sig = ComplianceSignal.from_verdict(verdict, timestamp_ms=1000)
        assert sig.decision == ComplianceDecision.MATCH
        assert sig.matched_rule_id == "r1"
        assert sig.matched_rule_summary == "X→Y"
        assert sig.confidence == 0.95
        assert sig.speculative_draft_validated is True
        assert sig.timestamp_ms == 1000

    def test_from_safety_override_verdict(self):
        verdict = ComplianceVerdict(
            decision=ComplianceDecision.SAFETY_OVERRIDE,
            safety_override_reason="csam",
        )
        sig = ComplianceSignal.from_verdict(verdict, timestamp_ms=2000)
        assert sig.decision == ComplianceDecision.SAFETY_OVERRIDE
        assert sig.safety_override_reason == "csam"
        assert sig.matched_rule_id is None


# =============================================================================
# Config loader (env vars)
# =============================================================================

class TestConfigLoader:
    def test_defaults_when_no_env(self, monkeypatch):
        for var in [
            "MORALSTACK_DCCL_ENABLED",
            "MORALSTACK_DCCL_EVALUATION_PATH",
            "MORALSTACK_DCCL_LLM_MODEL",
            "MORALSTACK_DCCL_CONFIDENCE_THRESHOLD",
        ]:
            monkeypatch.delenv(var, raising=False)

        assert get_dccl_enabled() is True
        assert get_dccl_evaluation_path() == "hybrid"
        assert get_dccl_llm_model() == "gpt-4o"
        assert get_dccl_confidence_threshold() == 0.85

    def test_override_via_env(self, monkeypatch):
        monkeypatch.setenv("MORALSTACK_DCCL_ENABLED", "false")
        monkeypatch.setenv("MORALSTACK_DCCL_EVALUATION_PATH", "structured")
        monkeypatch.setenv("MORALSTACK_DCCL_LLM_MODEL", "gpt-4o-mini")
        monkeypatch.setenv("MORALSTACK_DCCL_CONFIDENCE_THRESHOLD", "0.90")

        assert get_dccl_enabled() is False
        assert get_dccl_evaluation_path() == "structured"
        assert get_dccl_llm_model() == "gpt-4o-mini"
        assert get_dccl_confidence_threshold() == 0.90

    def test_invalid_path_falls_back(self, monkeypatch):
        monkeypatch.setenv("MORALSTACK_DCCL_EVALUATION_PATH", "garbage")
        assert get_dccl_evaluation_path() == "hybrid"

    def test_invalid_threshold_falls_back(self, monkeypatch):
        monkeypatch.setenv("MORALSTACK_DCCL_CONFIDENCE_THRESHOLD", "1.5")
        assert get_dccl_confidence_threshold() == 0.85
        monkeypatch.setenv("MORALSTACK_DCCL_CONFIDENCE_THRESHOLD", "not_a_number")
        assert get_dccl_confidence_threshold() == 0.85


# =============================================================================
# Safety override categories (declared, classifier stubbed)
# =============================================================================

class TestSafetyOverrideStub:
    def test_categories_list(self):
        # The 7 categories from the specification.
        assert len(SAFETY_OVERRIDE_CATEGORIES) == 7
        assert "weapons_synthesis" in SAFETY_OVERRIDE_CATEGORIES
        assert "csam" in SAFETY_OVERRIDE_CATEGORIES
        assert "illegal_pharma" in SAFETY_OVERRIDE_CATEGORIES

    def test_classifier_detects_operational_keywords(self):
        assert classify_safety_override("how to make a bomb") == "weapons_synthesis"
        assert classify_safety_override("hello world") is None
        assert classify_safety_override("") is None


# =============================================================================
# DCCL scaffold
# =============================================================================

class TestDCCLScaffold:
    def test_dccl_is_instantiable(self):
        layer = DeveloperContractComplianceLayer()
        assert layer.enabled is True
        assert layer.confidence_threshold == 0.85

    def test_dccl_evaluate_returns_no_contract_stub(self):
        layer = DeveloperContractComplianceLayer()
        # Commit 1: evaluate always returns NO_CONTRACT.
        verdict = layer.evaluate(request=None, speculative_draft="")
        assert verdict.decision == ComplianceDecision.NO_CONTRACT
        assert verdict.evaluation_path == EvaluationPath.SKIPPED

    def test_dccl_can_be_disabled_via_env(self, monkeypatch):
        monkeypatch.setenv("MORALSTACK_DCCL_ENABLED", "false")
        layer = DeveloperContractComplianceLayer()
        assert layer.enabled is False
