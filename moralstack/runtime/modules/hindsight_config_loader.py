"""
Load Hindsight Evaluator configuration from environment variables.

Reads MORALSTACK_HINDSIGHT_* from os.environ. Empty or missing values fall back
to hardcoded defaults. Used when no explicit HindsightConfig is passed.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from moralstack.utils.env_helpers import get_env_bool, get_env_float, get_env_int, get_env_str

if TYPE_CHECKING:
    from moralstack.runtime.modules.hindsight_module import HindsightConfig

# Backward-compatible aliases for external consumers
get_hindsight_env_float = get_env_float
get_hindsight_env_int = get_env_int
get_hindsight_env_str = get_env_str
get_hindsight_env_bool = get_env_bool

# Environment variable names (single source of truth)
ENV_MODEL = "MORALSTACK_HINDSIGHT_MODEL"
ENV_MAX_RETRIES = "MORALSTACK_HINDSIGHT_MAX_RETRIES"
ENV_MAX_TOKENS = "MORALSTACK_HINDSIGHT_MAX_TOKENS"
ENV_TEMPERATURE = "MORALSTACK_HINDSIGHT_TEMPERATURE"
ENV_TOP_P = "MORALSTACK_HINDSIGHT_TOP_P"
ENV_WEIGHT_SAFETY = "MORALSTACK_HINDSIGHT_WEIGHT_SAFETY"
ENV_WEIGHT_HELPFULNESS = "MORALSTACK_HINDSIGHT_WEIGHT_HELPFULNESS"
ENV_WEIGHT_HONESTY = "MORALSTACK_HINDSIGHT_WEIGHT_HONESTY"
ENV_REFUSE_THRESHOLD = "MORALSTACK_HINDSIGHT_REFUSE_THRESHOLD"
ENV_REVISE_THRESHOLD = "MORALSTACK_HINDSIGHT_REVISE_THRESHOLD"
ENV_USE_BATCH_EVALUATION = "MORALSTACK_HINDSIGHT_USE_BATCH_EVALUATION"
ENV_ENABLE_CACHING = "MORALSTACK_HINDSIGHT_ENABLE_CACHING"


def load_hindsight_config_from_env() -> HindsightConfig:
    """
    Build HindsightConfig from environment variables.

    Only non-empty env values are used; otherwise hardcoded defaults apply.
    Ints are enforced >= 1 where applicable. Temperature is clamped to [0.0, 2.0].
    Weights are clamped to [0.0, 1.0]. Thresholds are clamped to [-1.0, 1.0].

    Note: MORALSTACK_HINDSIGHT_MODEL is intentionally not included in HindsightConfig;
    it is read by the call sites (run, benchmark) to construct the policy.
    """
    from moralstack.runtime.modules.hindsight_module import HindsightConfig

    max_retries = get_env_int(ENV_MAX_RETRIES, 3, 1)
    max_tokens = get_env_int(ENV_MAX_TOKENS, 768, 1)
    temperature = get_env_float(ENV_TEMPERATURE, 0.3, 0.0, 2.0)
    top_p = get_env_float(ENV_TOP_P, 0.9, 0.0, 1.0)
    weight_safety = get_env_float(ENV_WEIGHT_SAFETY, 0.5, 0.0, 1.0)
    weight_helpfulness = get_env_float(ENV_WEIGHT_HELPFULNESS, 0.3, 0.0, 1.0)
    weight_honesty = get_env_float(ENV_WEIGHT_HONESTY, 0.2, 0.0, 1.0)
    refuse_threshold = get_env_float(ENV_REFUSE_THRESHOLD, -0.7, -1.0, 1.0)
    revise_threshold = get_env_float(ENV_REVISE_THRESHOLD, 0.0, -1.0, 1.0)
    use_batch_evaluation = get_env_bool(ENV_USE_BATCH_EVALUATION, True)
    enable_caching = get_env_bool(ENV_ENABLE_CACHING, True)

    return HindsightConfig(
        max_retries=max_retries,
        max_tokens=max_tokens,
        temperature=temperature,
        top_p=top_p,
        weight_safety=weight_safety,
        weight_helpfulness=weight_helpfulness,
        weight_honesty=weight_honesty,
        refuse_threshold=refuse_threshold,
        revise_threshold=revise_threshold,
        use_batch_evaluation=use_batch_evaluation,
        enable_caching=enable_caching,
    )
