"""
LLMBasedRiskEstimator - Classificazione rischio etico per MoralStack.

Classificatore di rischio semantico che usa un LLM per valutare il
potenziale rischio etico di una richiesta. Agisce come un "giudice
semantico" che analizza il significato e l'intento della richiesta,
senza affidarsi a pattern di parole chiave.
"""

from __future__ import annotations

from moralstack.utils.json_utils import JSONParseError, extract_json

from .action import coerce_risk_policy_action
from .calibration import parse_risk_response
from .categories import (
    ActionabilityRisk,
    DomainSensitivity,
    IntentClarity,
    MisusePlausibility,
    OperationalRisk,
    RiskCategory,
    RiskPolicyAction,
)
from .estimator import (
    LLMBasedRiskEstimator,
    create_conservative_estimator,
    create_risk_estimator,
)
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
    "create_risk_estimator",
    "create_conservative_estimator",
    # Parsing / calibration
    "parse_risk_response",
    # Utility re-exports (per test compatibility)
    "extract_json",
    "JSONParseError",
]
