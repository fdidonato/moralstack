"""
Load Consequence Simulator configuration from environment variables.

Reads MORALSTACK_SIMULATOR_* from os.environ. Empty or missing values fall back
to hardcoded defaults. Used when no explicit SimulatorConfig is passed.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from moralstack.utils.env_helpers import get_env_bool, get_env_float, get_env_int, get_env_str

if TYPE_CHECKING:
    from moralstack.runtime.modules.simulator_module import SimulatorConfig

# Backward-compatible aliases for external consumers
get_simulator_env_float = get_env_float
get_simulator_env_int = get_env_int
get_simulator_env_str = get_env_str
get_simulator_env_bool = get_env_bool

# Environment variable names (single source of truth)
ENV_MODEL = "MORALSTACK_SIMULATOR_MODEL"
ENV_MAX_RETRIES = "MORALSTACK_SIMULATOR_MAX_RETRIES"
ENV_MAX_TOKENS = "MORALSTACK_SIMULATOR_MAX_TOKENS"
ENV_TEMPERATURE = "MORALSTACK_SIMULATOR_TEMPERATURE"
ENV_TOP_P = "MORALSTACK_SIMULATOR_TOP_P"
ENV_DEFAULT_NUM_SCENARIOS = "MORALSTACK_SIMULATOR_DEFAULT_NUM_SCENARIOS"
ENV_USE_SEEDED_GENERATION = "MORALSTACK_SIMULATOR_USE_SEEDED_GENERATION"
ENV_ENABLE_CACHING = "MORALSTACK_SIMULATOR_ENABLE_CACHING"


def load_simulator_config_from_env() -> SimulatorConfig:
    """
    Build SimulatorConfig from environment variables.

    Only non-empty env values are used; otherwise hardcoded defaults apply.
    Ints are enforced >= 1 where applicable. Temperature is clamped to [0.0, 2.0].

    Note: MORALSTACK_SIMULATOR_MODEL is intentionally not included in SimulatorConfig;
    it is read by the call sites (run, benchmark) to construct the policy.
    """
    from moralstack.runtime.modules.simulator_module import SimulatorConfig

    max_retries = get_env_int(ENV_MAX_RETRIES, 3, 1)
    max_tokens = get_env_int(ENV_MAX_TOKENS, 384, 1)
    temperature = get_env_float(ENV_TEMPERATURE, 0.8, 0.0, 2.0)
    top_p = get_env_float(ENV_TOP_P, 0.95, 0.0, 1.0)
    default_num_scenarios = get_env_int(ENV_DEFAULT_NUM_SCENARIOS, 3, 1)
    use_seeded_generation = get_env_bool(ENV_USE_SEEDED_GENERATION, False)
    enable_caching = get_env_bool(ENV_ENABLE_CACHING, True)

    return SimulatorConfig(
        max_retries=max_retries,
        max_tokens=max_tokens,
        temperature=temperature,
        top_p=top_p,
        default_num_scenarios=default_num_scenarios,
        use_seeded_generation=use_seeded_generation,
        enable_caching=enable_caching,
    )
