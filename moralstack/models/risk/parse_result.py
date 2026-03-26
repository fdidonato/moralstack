"""
Result type for risk response parsing.

Immutable dataclass returned by parse_risk_response instead of a large tuple.
"""

from __future__ import annotations

from dataclasses import dataclass

from .categories import (
    ActionabilityRisk,
    DomainSensitivity,
    IntentClarity,
    MisusePlausibility,
    OperationalRisk,
    RiskCategory,
    RiskPolicyAction,
)


@dataclass(frozen=True)
class RiskParseResult:
    """
    Immutable result of parsing a risk estimator LLM response.

    Replaces the previous 18-element tuple for clearer API and type safety.
    """

    score: float
    confidence: float
    category: RiskCategory
    signals: list[str]
    rationale: str
    risk_policy_action: RiskPolicyAction
    domain_sensitivity: DomainSensitivity
    operational_risk: OperationalRisk
    intent_clarity: IntentClarity
    misuse_plausibility: MisusePlausibility
    actionability_risk: ActionabilityRisk
    harm_type: str
    self_harm_language: bool
    requested_instructions: bool
    intent_to_harm: bool
    request_type: str
    intent_operational: bool
    detected_language: str
