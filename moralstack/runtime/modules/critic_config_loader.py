"""
Load Constitutional Critic configuration from environment variables.

Reads MORALSTACK_CRITIC_* from os.environ. Empty or missing values fall back
to hardcoded defaults. Used when no explicit CriticConfig is passed.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from moralstack.utils.env_helpers import get_env_bool, get_env_float, get_env_int, get_env_str

if TYPE_CHECKING:
    from moralstack.runtime.modules.critic_module import CriticConfig

# Backward-compatible aliases for external consumers
get_critic_env_float = get_env_float
get_critic_env_int = get_env_int
get_critic_env_str = get_env_str
get_critic_env_bool = get_env_bool

# Environment variable names (single source of truth)
ENV_MODEL = "MORALSTACK_CRITIC_MODEL"
ENV_MAX_RETRIES = "MORALSTACK_CRITIC_MAX_RETRIES"
ENV_MAX_TOKENS = "MORALSTACK_CRITIC_MAX_TOKENS"
ENV_TEMPERATURE = "MORALSTACK_CRITIC_TEMPERATURE"
ENV_TOP_P = "MORALSTACK_CRITIC_TOP_P"
ENV_TOP_K_PRINCIPLES = "MORALSTACK_CRITIC_TOP_K_PRINCIPLES"
ENV_INCLUDE_EXAMPLES = "MORALSTACK_CRITIC_INCLUDE_EXAMPLES"


def load_critic_config_from_env() -> CriticConfig:
    """
    Build CriticConfig from environment variables.

    Only non-empty env values are used; otherwise hardcoded defaults apply.
    Ints are enforced >= 1 where applicable. Temperature is clamped to [0.0, 2.0].

    Note: MORALSTACK_CRITIC_MODEL is intentionally not included in CriticConfig;
    it is read by the call sites (run, benchmark) to construct the policy.
    """
    from moralstack.runtime.modules.critic_module import CriticConfig

    max_retries = get_env_int(ENV_MAX_RETRIES, 2, 1)
    max_tokens = get_env_int(ENV_MAX_TOKENS, 384, 1)
    temperature = get_env_float(ENV_TEMPERATURE, 0.1, 0.0, 2.0)
    top_p = get_env_float(ENV_TOP_P, 0.9, 0.0, 1.0)
    top_k_principles = get_env_int(ENV_TOP_K_PRINCIPLES, 20, 1)
    include_examples = get_env_bool(ENV_INCLUDE_EXAMPLES, False)

    return CriticConfig(
        max_retries=max_retries,
        max_tokens=max_tokens,
        temperature=temperature,
        top_p=top_p,
        top_k_principles=top_k_principles,
        include_examples=include_examples,
    )
