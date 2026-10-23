"""
LLMBasedRiskEstimator - Ethical risk classification for MoralStack.

Semantic risk classifier that uses an LLM to assess the
potential ethical risk of a request. It acts as a "semantic
judge" that analyzes the meaning and intent of the request,
without relying on keyword patterns.
"""

from __future__ import annotations

from moralstack.utils.json_utils import JSONParseError, extract_json

from .action import coerce_risk_policy_action
from .categories import (
    ActionabilityRisk,
    DomainSensitivity,
    IntentClarity,
    MisusePlausibility,
    OperationalRisk,
    RiskCategory,
    RiskPolicyAction,
)
from .estimator import LLMBasedRiskEstimator
from .parse_result import RiskParseResult
from .schema import RiskEstimation, RiskEstimatorConfig

__all__ = [
    # Data models
    "RiskEstimation",
    "RiskEstimatorConfig",
    "RiskParseResult",
    # Enums / categories
    "RiskCategory",
    "DomainSensitivity",
    "OperationalRisk",
    "IntentClarity",
    "MisusePlausibility",
    "ActionabilityRisk",
    "RiskPolicyAction",
    "coerce_risk_policy_action",
    # Estimator
    "LLMBasedRiskEstimator",
    # Utility re-exports (per test compatibility)
    "extract_json",
    "JSONParseError",
]
