"""
Load Risk Estimator configuration from environment variables.

Reads MORALSTACK_RISK_* from os.environ. Empty or missing values fall back
to hardcoded defaults. Used when no explicit RiskEstimatorConfig is passed.
"""

from __future__ import annotations

import os

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

_DEFAULT_MODEL_ID = "gpt-4o"


def resolve_risk_base_model_from_env() -> str:
    """
    Default OpenAI model id for parallel mini-estimators when per-slot env vars are unset.

    Resolution order matches deliberation wiring (`build_deliberation_modules`): explicit
    `MORALSTACK_RISK_MODEL`, then `OPENAI_MODEL`, then a built-in default.
    """
    explicit_risk = get_env_str(ENV_MODEL, "")
    if explicit_risk:
        return explicit_risk
    primary = (os.getenv("OPENAI_MODEL") or "").strip()
    return primary if primary else _DEFAULT_MODEL_ID


def resolve_parallel_mini_model_slot(env_key: str, base_model: str) -> str:
    """Return mini-estimator model id: non-empty env override, else *base_model*."""
    return get_env_str(env_key, "") or base_model


def load_risk_estimator_config_from_env() -> RiskEstimatorConfig:
    """
    Build RiskEstimatorConfig from environment variables.

    Only non-empty env values are used; otherwise defaults apply. Parallel mini-estimator
    model slots (`*_INTENT_MODEL`, etc.) fall back to `MORALSTACK_RISK_MODEL`, then
    `OPENAI_MODEL`, then ``gpt-4o`` when unset.

    Float scores are clamped to [0, 1]. Ints are enforced >= 1 where applicable.
    """
    max_retries = get_env_int(ENV_MAX_RETRIES, 2, 1)
    max_tokens = get_env_int(ENV_MAX_TOKENS, 512, 1)
    temperature = get_env_float(ENV_TEMPERATURE, 0.1, 0.0, 2.0)
    mini_base = resolve_risk_base_model_from_env()
    intent_model = resolve_parallel_mini_model_slot(ENV_INTENT_MODEL, mini_base)
    signals_model = resolve_parallel_mini_model_slot(ENV_SIGNALS_MODEL, mini_base)
    operational_model = resolve_parallel_mini_model_slot(ENV_OPERATIONAL_MODEL, mini_base)
    return RiskEstimatorConfig(
        max_retries=max_retries,
        max_tokens=max_tokens,
        temperature=temperature,
        intent_model=intent_model,
        signals_model=signals_model,
        operational_model=operational_model,
    )
