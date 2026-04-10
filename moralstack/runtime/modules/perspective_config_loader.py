"""
Load Perspective Ensemble configuration from environment variables.

Reads MORALSTACK_PERSPECTIVES_* from os.environ. Empty or missing values fall back
to hardcoded defaults. Used when no explicit EnsembleConfig is passed.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from moralstack.utils.env_helpers import get_env_bool, get_env_float, get_env_int, get_env_str

if TYPE_CHECKING:
    from moralstack.runtime.modules.perspective_module import EnsembleConfig

# Backward-compatible aliases for external consumers
get_perspective_env_float = get_env_float
get_perspective_env_int = get_env_int
get_perspective_env_str = get_env_str
get_perspective_env_bool = get_env_bool

# Environment variable names (single source of truth)
ENV_MODEL = "MORALSTACK_PERSPECTIVES_MODEL"
ENV_MAX_RETRIES = "MORALSTACK_PERSPECTIVES_MAX_RETRIES"
ENV_MAX_TOKENS = "MORALSTACK_PERSPECTIVES_MAX_TOKENS"
ENV_TEMPERATURE = "MORALSTACK_PERSPECTIVES_TEMPERATURE"
ENV_TOP_P = "MORALSTACK_PERSPECTIVES_TOP_P"
ENV_PARALLEL_EVALUATION = "MORALSTACK_PERSPECTIVES_PARALLEL_EVALUATION"
ENV_MAX_WORKERS = "MORALSTACK_PERSPECTIVES_MAX_WORKERS"
ENV_TIMEOUT_SECONDS = "MORALSTACK_PERSPECTIVES_TIMEOUT_SECONDS"
ENV_MAX_PERSPECTIVES = "MORALSTACK_PERSPECTIVES_MAX_PERSPECTIVES"
ENV_CONSERVATIVE_ON_FAILURE = "MORALSTACK_PERSPECTIVES_CONSERVATIVE_ON_FAILURE"
ENV_ENABLE_CACHING = "MORALSTACK_PERSPECTIVES_ENABLE_CACHING"


def load_perspective_config_from_env() -> EnsembleConfig:
    """
    Build EnsembleConfig from environment variables.

    Only non-empty env values are used; otherwise hardcoded defaults apply.
    """
    from moralstack.runtime.modules.perspective_module import EnsembleConfig

    max_retries = get_env_int(ENV_MAX_RETRIES, 3, 1)
    max_tokens = get_env_int(ENV_MAX_TOKENS, 512, 1)
    temperature = get_env_float(ENV_TEMPERATURE, 0.1, 0.0, 2.0)
    top_p = get_env_float(ENV_TOP_P, 0.9, 0.0, 1.0)
    parallel_evaluation = get_env_bool(ENV_PARALLEL_EVALUATION, True)
    max_workers = get_env_int(ENV_MAX_WORKERS, 3, 1)
    timeout_seconds = get_env_float(ENV_TIMEOUT_SECONDS, 60.0, 1.0)
    max_perspectives = get_env_int(ENV_MAX_PERSPECTIVES, 2, 0)
    conservative_on_failure = get_env_bool(ENV_CONSERVATIVE_ON_FAILURE, True)
    enable_caching = get_env_bool(ENV_ENABLE_CACHING, False)

    return EnsembleConfig(
        max_retries=max_retries,
        max_tokens=max_tokens,
        temperature=temperature,
        top_p=top_p,
        parallel_evaluation=parallel_evaluation,
        max_workers=max_workers,
        timeout_seconds=timeout_seconds,
        max_perspectives=max_perspectives,
        conservative_on_failure=conservative_on_failure,
        enable_caching=enable_caching,
    )
