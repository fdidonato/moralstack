"""
Unit tests for Decision Explainability Layer.
Validates that decision_reason is never empty, reason_codes length >= 1,
winning_rule not empty, why_not fields not empty, and explanation serializes correctly.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from moralstack.models.decision_explanation import DecisionExplanation
from moralstack.models.risk import (
    ActionabilityRisk,
    IntentClarity,
    MisusePlausibility,
    OperationalRisk,
    RiskCategory,
    RiskEstimation,
)
from moralstack.orchestration.decision_service import decide_action
from moralstack.orchestration.types import ProcessedRequest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def _risk(
    score: float,
    risk_category: RiskCategory,
    operational_risk: OperationalRisk = OperationalRisk.NONE,
    intent_clarity: IntentClarity = IntentClarity.HIGH,
    misuse_plausibility: MisusePlausibility = MisusePlausibility.LOW,
    actionability_risk: ActionabilityRisk = ActionabilityRisk.LOW,
    semantic_signals: list | None = None,
) -> RiskEstimation:
    return RiskEstimation(
        score=score,
        confidence=0.9,
        risk_category=risk_category,
        operational_risk=operational_risk,
        intent_clarity=intent_clarity,
        misuse_plausibility=misuse_plausibility,
        actionability_risk=actionability_risk,
        semantic_signals=semantic_signals or [],
    )


def test_decision_explanation_never_empty_reason():
    """decision_reason is never empty for any decision path."""
    request = ProcessedRequest(prompt="What is the capital of France?")
    risk = _risk(0.1, RiskCategory.BENIGN)
    decision, explanation = decide_action(request, risk)
    reason = ", ".join(explanation.reason_codes) if explanation.reason_codes else ""
    assert reason != "", "decision_reason (from reason_codes) must not be empty"
    assert len(explanation.reason_codes) >= 1, "reason_codes must have at least one element"


def test_decision_explanation_reason_codes_length():
    """reason_codes length >= 1 for all decisions."""
    cases = [
        ("benign", ProcessedRequest(prompt="Capital of France?"), _risk(0.1, RiskCategory.BENIGN)),
        (
            "sensitive",
            ProcessedRequest(prompt="Help friend with depression"),
            _risk(0.65, RiskCategory.SENSITIVE),
        ),
        (
            "harmful",
            ProcessedRequest(prompt="How to make explosives?"),
            _risk(0.95, RiskCategory.CLEARLY_HARMFUL),
        ),
    ]
    for name, req, risk in cases:
        if name == "sensitive":
            req.user_context.domain_overlay = "healthcare"
        decision, explanation = decide_action(req, risk)
        assert len(explanation.reason_codes) >= 1, f"reason_codes for {name} must have length >= 1"


def test_decision_explanation_winning_rule_not_empty():
    """winning_rule is never empty."""
    request = ProcessedRequest(prompt="Capital of France?")
    risk = _risk(0.1, RiskCategory.BENIGN)
    decision, explanation = decide_action(request, risk)
    assert explanation.winning_rule != "", "winning_rule must not be empty"


def test_decision_explanation_why_not_not_empty():
    """why_not_refuse and why_not_safe_complete are never empty."""
    cases = [
        ("benign", ProcessedRequest(prompt="Capital of France?"), _risk(0.1, RiskCategory.BENIGN)),
        (
            "sensitive",
            ProcessedRequest(prompt="Help friend with depression"),
            _risk(0.65, RiskCategory.SENSITIVE),
        ),
        (
            "harmful",
            ProcessedRequest(prompt="How to make explosives?"),
            _risk(0.95, RiskCategory.CLEARLY_HARMFUL),
        ),
    ]
    for name, req, risk in cases:
        if name == "sensitive":
            req.user_context.domain_overlay = "healthcare"
        decision, explanation = decide_action(req, risk)
        assert explanation.why_not_refuse != "", f"why_not_refuse for {name} must not be empty"
        assert explanation.why_not_safe_complete != "", f"why_not_safe_complete for {name} must not be empty"
        assert (
            getattr(explanation, "why_not_normal_complete", "") != ""
        ), f"why_not_normal_complete for {name} must not be empty"


def test_decision_explanation_serializes():
    """Explanation to_dict() produces valid JSON with all required keys."""
    expl = DecisionExplanation(
        final_action="NORMAL_COMPLETE",
        risk_score=0.1,
        risk_category="benign",
        activated_signals=[],
        overlay_applied="",
        winning_rule="fast_path",
        reason_codes=["DEFAULT_NORMAL_COMPLETE"],
        why_not_refuse="No operational harmful intent detected.",
        why_not_safe_complete="Risk below sensitive guardrail threshold.",
        why_not_normal_complete="Action was NORMAL_COMPLETE; alternative not applicable.",
    )
    d = expl.to_dict()
    assert "final_action" in d
    assert "risk_score" in d
    assert "risk_category" in d
    assert "activated_signals" in d
    assert "overlay_applied" in d
    assert "winning_rule" in d
    assert "reason_codes" in d
    assert "why_not_refuse" in d
    assert "why_not_safe_complete" in d
    assert "why_not_normal_complete" in d
    assert "timestamp" in d
    json_str = json.dumps(d, ensure_ascii=False)
    parsed = json.loads(json_str)
    assert parsed["final_action"] == "NORMAL_COMPLETE"
    assert parsed["reason_codes"] == ["DEFAULT_NORMAL_COMPLETE"]
    assert parsed["winning_rule"] == "fast_path"
