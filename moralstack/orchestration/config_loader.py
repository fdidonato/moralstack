"""
Load Orchestrator configuration from environment variables.

Reads MORALSTACK_ORCHESTRATOR_* from os.environ. Empty or missing values fall back
to hardcoded defaults. Used when no explicit OrchestratorConfig is passed.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from moralstack.utils.env_helpers import get_env_bool, get_env_float, get_env_int, get_env_str

if TYPE_CHECKING:
    from moralstack.orchestration.types import OrchestratorConfig

# Backward-compatible aliases for external consumers
get_orchestrator_env_float = get_env_float
get_orchestrator_env_int = get_env_int
get_orchestrator_env_str = get_env_str
get_orchestrator_env_bool = get_env_bool

# Environment variable names (single source of truth)
ENV_MAX_DELIBERATION_CYCLES = "MORALSTACK_ORCHESTRATOR_MAX_DELIBERATION_CYCLES"
ENV_RISK_LOW_THRESHOLD = "MORALSTACK_ORCHESTRATOR_RISK_LOW_THRESHOLD"
ENV_RISK_MEDIUM_THRESHOLD = "MORALSTACK_ORCHESTRATOR_RISK_MEDIUM_THRESHOLD"
ENV_TIMEOUT_MS = "MORALSTACK_ORCHESTRATOR_TIMEOUT_MS"
ENV_ENABLE_PERSPECTIVES = "MORALSTACK_ORCHESTRATOR_ENABLE_PERSPECTIVES"
ENV_NUM_SIMULATIONS = "MORALSTACK_ORCHESTRATOR_NUM_SIMULATIONS"
ENV_MIN_HINDSIGHT_SCORE = "MORALSTACK_ORCHESTRATOR_MIN_HINDSIGHT_SCORE"
ENV_MAX_CRITICAL_VIOLATIONS = "MORALSTACK_ORCHESTRATOR_MAX_CRITICAL_VIOLATIONS"
ENV_EARLY_EXIT_HINDSIGHT_THRESHOLD = "MORALSTACK_ORCHESTRATOR_EARLY_EXIT_HINDSIGHT_THRESHOLD"
ENV_ENABLE_SIMULATION = "MORALSTACK_ORCHESTRATOR_ENABLE_SIMULATION"
ENV_ENABLE_HINDSIGHT = "MORALSTACK_ORCHESTRATOR_ENABLE_HINDSIGHT"
ENV_SAFE_RESPONSE_ON_ERROR = "MORALSTACK_ORCHESTRATOR_SAFE_RESPONSE_ON_ERROR"
ENV_SKIP_OPTIONAL_MODULES_THRESHOLD = "MORALSTACK_ORCHESTRATOR_SKIP_OPTIONAL_MODULES_THRESHOLD"
ENV_SOFT_TIMEOUT_THRESHOLD = "MORALSTACK_ORCHESTRATOR_SOFT_TIMEOUT_THRESHOLD"
ENV_PARALLEL_MODULE_CALLS = "MORALSTACK_ORCHESTRATOR_PARALLEL_MODULE_CALLS"
ENV_ENABLE_THIN_MODE = "MORALSTACK_ORCHESTRATOR_ENABLE_THIN_MODE"
ENV_ENABLE_SIMULATOR_GATING = "MORALSTACK_ORCHESTRATOR_ENABLE_SIMULATOR_GATING"
ENV_ENABLE_HINDSIGHT_GATING = "MORALSTACK_ORCHESTRATOR_ENABLE_HINDSIGHT_GATING"
ENV_SIMULATOR_GATE_SEMANTIC_HARM_THRESHOLD = "MORALSTACK_ORCHESTRATOR_SIMULATOR_GATE_SEMANTIC_HARM_THRESHOLD"
ENV_SIMULATOR_GATE_DELTA_CHARS_THRESHOLD = "MORALSTACK_ORCHESTRATOR_SIMULATOR_GATE_DELTA_CHARS_THRESHOLD"
ENV_SIMULATOR_GATE_SKIP_MAX_PRIOR_SEMANTIC_HARM = "MORALSTACK_ORCHESTRATOR_SIMULATOR_GATE_SKIP_MAX_PRIOR_SEMANTIC_HARM"
ENV_BORDERLINE_REFUSE_UPPER = "MORALSTACK_ORCHESTRATOR_BORDERLINE_REFUSE_UPPER"
ENV_PARALLEL_CRITIC_WITH_MODULES = "MORALSTACK_ORCHESTRATOR_PARALLEL_CRITIC_WITH_MODULES"
ENV_ENABLE_DYNAMIC_PARALLEL_SCHEDULER = "MORALSTACK_ORCHESTRATOR_ENABLE_DYNAMIC_PARALLEL_SCHEDULER"
ENV_ENABLE_SPECULATIVE_GENERATION = "MORALSTACK_ORCHESTRATOR_ENABLE_SPECULATIVE_GENERATION"
ENV_CYCLE1_EARLY_CONVERGENCE_MIN_WEIGHTED_APPROVAL = "MORALSTACK_ORCHESTRATOR_CYCLE1_EARLY_CONVERGENCE_MIN_WEIGHTED_APPROVAL"
ENV_CYCLE1_EARLY_CONVERGENCE_MAX_SEMANTIC_HARM = "MORALSTACK_ORCHESTRATOR_CYCLE1_EARLY_CONVERGENCE_MAX_SEMANTIC_HARM"
ENV_CYCLE1_EARLY_CONVERGENCE_MIN_PER_PERSPECTIVE_APPROVAL = (
    "MORALSTACK_ORCHESTRATOR_CYCLE1_EARLY_CONVERGENCE_MIN_PER_PERSPECTIVE_APPROVAL"
)


def load_orchestrator_config_from_env() -> OrchestratorConfig:
    """
    Build OrchestratorConfig from environment variables.

    Only non-empty env values are used; otherwise hardcoded defaults apply.
    Ints are enforced >= 1 (or >= 0 for max_critical_violations).
    Floats are clamped to [0.0, 1.0] where applicable. Timeout >= 1.

    Builds RiskThresholds from MORALSTACK_ORCHESTRATOR_RISK_LOW_THRESHOLD and
    MORALSTACK_ORCHESTRATOR_RISK_MEDIUM_THRESHOLD.

    There is no dedicated model for the orchestrator (it is not an LLM module).
    """
    from moralstack.orchestration.types import OrchestratorConfig, RiskThresholds

    max_deliberation_cycles = get_env_int(ENV_MAX_DELIBERATION_CYCLES, 2, 1)
    risk_low = get_env_float(ENV_RISK_LOW_THRESHOLD, 0.3, 0.0, 1.0)
    risk_medium = get_env_float(ENV_RISK_MEDIUM_THRESHOLD, 0.7, 0.0, 1.0)
    timeout_ms = get_env_int(ENV_TIMEOUT_MS, 600000, 1)
    enable_perspectives = get_env_bool(ENV_ENABLE_PERSPECTIVES, True)
    num_simulations = get_env_int(ENV_NUM_SIMULATIONS, 3, 1)
    min_hindsight_score = get_env_float(ENV_MIN_HINDSIGHT_SCORE, 0.8, 0.0, 1.0)
    max_critical_violations = get_env_int(ENV_MAX_CRITICAL_VIOLATIONS, 0, 0)
    early_exit_hindsight_threshold = get_env_float(ENV_EARLY_EXIT_HINDSIGHT_THRESHOLD, 0.6, 0.0, 1.0)
    enable_simulation = get_env_bool(ENV_ENABLE_SIMULATION, True)
    enable_hindsight = get_env_bool(ENV_ENABLE_HINDSIGHT, True)
    safe_response_on_error = get_env_bool(ENV_SAFE_RESPONSE_ON_ERROR, True)
    skip_optional_modules_threshold = get_env_float(ENV_SKIP_OPTIONAL_MODULES_THRESHOLD, 0.95, 0.0, 1.0)
    soft_timeout_threshold = get_env_float(ENV_SOFT_TIMEOUT_THRESHOLD, 0.90, 0.0, 1.0)
    parallel_module_calls = get_env_bool(ENV_PARALLEL_MODULE_CALLS, True)
    enable_thin_mode = get_env_bool(ENV_ENABLE_THIN_MODE, False)
    enable_simulator_gating = get_env_bool(ENV_ENABLE_SIMULATOR_GATING, False)
    enable_hindsight_gating = get_env_bool(ENV_ENABLE_HINDSIGHT_GATING, True)
    simulator_gate_semantic_harm_threshold = get_env_float(ENV_SIMULATOR_GATE_SEMANTIC_HARM_THRESHOLD, 0.4, 0.0, 1.0)
    simulator_gate_delta_chars_threshold = get_env_int(ENV_SIMULATOR_GATE_DELTA_CHARS_THRESHOLD, 100, 0)
    simulator_gate_skip_max_prior_semantic_harm = get_env_float(
        ENV_SIMULATOR_GATE_SKIP_MAX_PRIOR_SEMANTIC_HARM, 0.25, 0.0, 1.0
    )
    cycle1_early_convergence_min_weighted_approval = get_env_float(
        ENV_CYCLE1_EARLY_CONVERGENCE_MIN_WEIGHTED_APPROVAL, 0.78, 0.0, 1.0
    )
    cycle1_early_convergence_max_semantic_harm = get_env_float(
        ENV_CYCLE1_EARLY_CONVERGENCE_MAX_SEMANTIC_HARM, 0.35, 0.0, 1.0
    )
    cycle1_early_convergence_min_per_perspective_approval = get_env_float(
        ENV_CYCLE1_EARLY_CONVERGENCE_MIN_PER_PERSPECTIVE_APPROVAL, 0.70, 0.0, 1.0
    )
    borderline_refuse_upper = get_env_float(ENV_BORDERLINE_REFUSE_UPPER, 0.95, 0.0, 1.0)
    parallel_critic_with_modules = get_env_bool(ENV_PARALLEL_CRITIC_WITH_MODULES, True)
    enable_dynamic_parallel_scheduler = get_env_bool(ENV_ENABLE_DYNAMIC_PARALLEL_SCHEDULER, True)
    enable_speculative_generation = get_env_bool(ENV_ENABLE_SPECULATIVE_GENERATION, True)

    return OrchestratorConfig(
        max_deliberation_cycles=max_deliberation_cycles,
        risk_thresholds=RiskThresholds(low=risk_low, medium=risk_medium),
        timeout_ms=timeout_ms,
        enable_perspectives=enable_perspectives,
        num_simulations=num_simulations,
        min_hindsight_score=min_hindsight_score,
        max_critical_violations=max_critical_violations,
        early_exit_hindsight_threshold=early_exit_hindsight_threshold,
        enable_simulation=enable_simulation,
        enable_hindsight=enable_hindsight,
        safe_response_on_error=safe_response_on_error,
        skip_optional_modules_threshold=skip_optional_modules_threshold,
        soft_timeout_threshold=soft_timeout_threshold,
        parallel_module_calls=parallel_module_calls,
        enable_thin_mode=enable_thin_mode,
        enable_simulator_gating=enable_simulator_gating,
        enable_hindsight_gating=enable_hindsight_gating,
        simulator_gate_semantic_harm_threshold=simulator_gate_semantic_harm_threshold,
        simulator_gate_delta_chars_threshold=simulator_gate_delta_chars_threshold,
        simulator_gate_skip_max_prior_semantic_harm=simulator_gate_skip_max_prior_semantic_harm,
        cycle1_early_convergence_min_weighted_approval=cycle1_early_convergence_min_weighted_approval,
        cycle1_early_convergence_max_semantic_harm=cycle1_early_convergence_max_semantic_harm,
        cycle1_early_convergence_min_per_perspective_approval=cycle1_early_convergence_min_per_perspective_approval,
        borderline_refuse_upper=borderline_refuse_upper,
        parallel_critic_with_modules=parallel_critic_with_modules,
        enable_dynamic_parallel_scheduler=enable_dynamic_parallel_scheduler,
        enable_speculative_generation=enable_speculative_generation,
    )
