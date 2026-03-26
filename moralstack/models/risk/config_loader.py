"""
Load Risk Estimator configuration from environment variables.

Reads MORALSTACK_RISK_* from os.environ. Empty or missing values fall back
to hardcoded defaults. Used when no explicit RiskEstimatorConfig is passed.
"""

from __future__ import annotations

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


def load_risk_estimator_config_from_env() -> RiskEstimatorConfig:
    """
    Build RiskEstimatorConfig from environment variables.

    Only non-empty env values are used; otherwise hardcoded defaults apply.
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
    intent_model = get_env_str(ENV_INTENT_MODEL, "gpt-4o")
    signals_model = get_env_str(ENV_SIGNALS_MODEL, "gpt-4o")
    operational_model = get_env_str(ENV_OPERATIONAL_MODEL, "gpt-4o")
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
