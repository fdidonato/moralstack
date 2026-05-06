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
    # Semantic intent flags (LLM-judged, language-agnostic). Default False so
    # absence in older LLM outputs does not break parsing — current intent
    # estimator prompt requires these fields.
    stated_personal_bias: bool = False
    seeks_norm_circumvention: bool = False
    # Topic signal q13: protected-class targeting (differential treatment of
    # individuals based on protected characteristics). Default False.
    q13_protected_class_targeting: bool = False
    # Topic signals q14-q16: reputational harm cluster (defamation, deceptive
    # online campaigns, coordinated harassment). Distinct from q11 (privacy
    # invasion): these target reputation/social standing, not private data.
    # Default False so older traces and fallback paths remain unaffected.
    q14_reputational_harm: bool = False
    q15_deceptive_online_campaign: bool = False
    q16_harassment_smear_campaign: bool = False
    # Topic signal q17: minor exploitation / grooming toward minors online.
    # Default False so older estimator outputs remain valid.
    q17_minor_exploitation: bool = False
