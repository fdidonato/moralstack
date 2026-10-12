"""
Load Risk Estimator configuration from environment variables.

Reads MORALSTACK_RISK_* from os.environ. Empty or missing values fall back
to hardcoded defaults (model slots: None = inherit the main risk policy model).
Used when no explicit RiskEstimatorConfig is passed.
"""

from __future__ import annotations

from moralstack.utils.env_helpers import get_env_float, get_env_int, get_env_str

from .schema import RiskEstimatorConfig

# Backward-compatible aliases for external consumers
get_risk_env_float = get_env_float
get_risk_env_int = get_env_int
get_risk_env_str = get_env_str

# Environment variable names (single source of truth)
ENV_MAX_RETRIES = "MORALSTACK_RISK_MAX_RETRIES"
ENV_MAX_TOKENS = "MORALSTACK_RISK_MAX_TOKENS"
ENV_TEMPERATURE = "MORALSTACK_RISK_TEMPERATURE"
ENV_TOP_P = "MORALSTACK_RISK_TOP_P"
ENV_TOP_K = "MORALSTACK_RISK_TOP_K"
ENV_RULE_PREVIEW_LEN = "MORALSTACK_RISK_RULE_PREVIEW_LEN"
ENV_CRISIS_CLAMP_LOW = "MORALSTACK_RISK_CRISIS_CLAMP_LOW"
ENV_CRISIS_CLAMP_HIGH = "MORALSTACK_RISK_CRISIS_CLAMP_HIGH"
ENV_MODEL = "MORALSTACK_RISK_MODEL"
ENV_INTENT_MODEL = "MORALSTACK_RISK_INTENT_MODEL"
ENV_SIGNALS_MODEL = "MORALSTACK_RISK_SIGNALS_MODEL"
ENV_OPERATIONAL_MODEL = "MORALSTACK_RISK_OPERATIONAL_MODEL"


def resolve_risk_mini_model(slot_model: str | None, main_risk_model: str | None) -> str | None:
    """
    Model for one risk mini-estimator: the per-mini slot when set, else the main risk model.

    The main risk model is the risk policy's model (MORALSTACK_RISK_MODEL when the builder
    applied it, otherwise the primary model). None when neither is known.
    Single source of truth for the estimator, the CLI display and the report labels.
    """
    return slot_model or main_risk_model or None


def load_risk_estimator_config_from_env() -> RiskEstimatorConfig:
    """
    Build RiskEstimatorConfig from environment variables.

    Only non-empty env values are used; otherwise defaults apply. Parallel mini-estimator
    model slots (`*_INTENT_MODEL`, etc.) are the raw env value or None when unset/empty;
    None means "inherit the main risk policy model" (see `resolve_risk_mini_model`).
    No primary/risk-wide model variable is read here (the builder applies those to the risk policy).

    Float scores are clamped to [0, 1]. Ints are enforced >= 1 where applicable.
    """
    max_retries = get_env_int(ENV_MAX_RETRIES, 2, 1)
    max_tokens = get_env_int(ENV_MAX_TOKENS, 512, 1)
    temperature = get_env_float(ENV_TEMPERATURE, 0.1, 0.0, 2.0)
    intent_model = get_env_str(ENV_INTENT_MODEL, "") or None
    signals_model = get_env_str(ENV_SIGNALS_MODEL, "") or None
    operational_model = get_env_str(ENV_OPERATIONAL_MODEL, "") or None
    return RiskEstimatorConfig(
        max_retries=max_retries,
        max_tokens=max_tokens,
        temperature=temperature,
        intent_model=intent_model,
        signals_model=signals_model,
        operational_model=operational_model,
    )
