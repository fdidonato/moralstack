"""
Load Risk Estimator configuration from environment variables.

Reads MORALSTACK_RISK_* from os.environ. Empty or missing values fall back
to hardcoded defaults. Used when no explicit RiskEstimatorConfig is passed.
"""

from __future__ import annotations

import os

from moralstack.utils.env_helpers import get_env_bool, get_env_float, get_env_int, get_env_str

from .schema import RiskEstimatorConfig

# Backward-compatible aliases for external consumers
get_risk_env_float = get_env_float
get_risk_env_int = get_env_int
get_risk_env_str = get_env_str
get_risk_env_bool = get_env_bool

# Environment variable names (single source of truth)
ENV_LOW_THRESHOLD = "MORALSTACK_RISK_LOW_THRESHOLD"
ENV_MEDIUM_THRESHOLD = "MORALSTACK_RISK_MEDIUM_THRESHOLD"
ENV_MAX_RETRIES = "MORALSTACK_RISK_MAX_RETRIES"
ENV_MAX_TOKENS = "MORALSTACK_RISK_MAX_TOKENS"
ENV_TEMPERATURE = "MORALSTACK_RISK_TEMPERATURE"
ENV_FALLBACK_SCORE = "MORALSTACK_RISK_FALLBACK_SCORE"
ENV_FALLBACK_CONFIDENCE = "MORALSTACK_RISK_FALLBACK_CONFIDENCE"
ENV_REQUIRE_DELIBERATION_ON_FALLBACK = "MORALSTACK_RISK_REQUIRE_DELIBERATION_ON_FALLBACK"
ENV_TOP_P = "MORALSTACK_RISK_TOP_P"
ENV_TOP_K = "MORALSTACK_RISK_TOP_K"
ENV_RULE_PREVIEW_LEN = "MORALSTACK_RISK_RULE_PREVIEW_LEN"
ENV_CRISIS_CLAMP_LOW = "MORALSTACK_RISK_CRISIS_CLAMP_LOW"
ENV_CRISIS_CLAMP_HIGH = "MORALSTACK_RISK_CRISIS_CLAMP_HIGH"
ENV_CATEGORIZE_BENIGN_THRESHOLD = "MORALSTACK_RISK_CATEGORIZE_BENIGN_THRESHOLD"
ENV_CATEGORIZE_SENSITIVE_THRESHOLD = "MORALSTACK_RISK_CATEGORIZE_SENSITIVE_THRESHOLD"
ENV_CATEGORIZE_CLEARLY_HARMFUL_THRESHOLD = "MORALSTACK_RISK_CATEGORIZE_CLEARLY_HARMFUL_THRESHOLD"
ENV_MODEL = "MORALSTACK_RISK_MODEL"
ENV_INTENT_MODEL = "MORALSTACK_RISK_INTENT_MODEL"
ENV_SIGNALS_MODEL = "MORALSTACK_RISK_SIGNALS_MODEL"
ENV_OPERATIONAL_MODEL = "MORALSTACK_RISK_OPERATIONAL_MODEL"
ENV_PARALLEL_ESTIMATORS = "MORALSTACK_RISK_PARALLEL_ESTIMATORS"

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

    Float thresholds and scores are clamped to [0, 1]. Ints are enforced >= 1 where applicable.
    """
    low = get_env_float(ENV_LOW_THRESHOLD, 0.3, 0.0, 1.0)
    medium = get_env_float(ENV_MEDIUM_THRESHOLD, 0.7, 0.0, 1.0)
    max_retries = get_env_int(ENV_MAX_RETRIES, 2, 1)
    max_tokens = get_env_int(ENV_MAX_TOKENS, 512, 1)
    temperature = get_env_float(ENV_TEMPERATURE, 0.1, 0.0, 2.0)
    fallback_score = get_env_float(ENV_FALLBACK_SCORE, 0.5, 0.0, 1.0)
    fallback_confidence = get_env_float(ENV_FALLBACK_CONFIDENCE, 0.3, 0.0, 1.0)
    require_delib = get_env_bool(ENV_REQUIRE_DELIBERATION_ON_FALLBACK, True)
    use_parallel = get_env_bool(ENV_PARALLEL_ESTIMATORS, False)
    mini_base = resolve_risk_base_model_from_env()
    intent_model = resolve_parallel_mini_model_slot(ENV_INTENT_MODEL, mini_base)
    signals_model = resolve_parallel_mini_model_slot(ENV_SIGNALS_MODEL, mini_base)
    operational_model = resolve_parallel_mini_model_slot(ENV_OPERATIONAL_MODEL, mini_base)
    return RiskEstimatorConfig(
        low_threshold=low,
        medium_threshold=medium,
        max_retries=max_retries,
        max_tokens=max_tokens,
        temperature=temperature,
        fallback_risk_score=fallback_score,
        fallback_confidence=fallback_confidence,
        require_deliberation_on_fallback=require_delib,
        use_parallel_estimators=use_parallel,
        intent_model=intent_model,
        signals_model=signals_model,
        operational_model=operational_model,
    )
