"""
Internal factory for the MoralStack pipeline used by the SDK.

Mirrors the logic of cli/loader.py in a programmatic way:
- No console output
- Typed exceptions (GovernancePipelineError, GovernanceConfigError)
- No dependency on FastAPI, uvicorn, or ui/

Single bridge between GovernanceConfig (public API) and internal types.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, Any, cast

from moralstack.sdk.errors import GovernanceConfigError, GovernancePipelineError

if TYPE_CHECKING:
    from moralstack.runtime.orchestrator import Orchestrator
    from moralstack.sdk.config import GovernanceConfig


def _resolve_api_key(config: GovernanceConfig) -> str:
    """Resolve API key: explicit config > env var. Raises if missing."""
    key = (config.api_key or os.getenv("OPENAI_API_KEY") or "").strip()
    if not key:
        raise GovernanceConfigError(
            "OPENAI_API_KEY is required. " "Set the environment variable or pass api_key in GovernanceConfig."
        )
    return key


def _resolve_model(config: GovernanceConfig) -> str:
    """Resolve model: explicit config > env var > default gpt-4o."""
    return (config.model or os.getenv("OPENAI_MODEL") or "gpt-4o").strip()


def _build_orchestrator_config(config: GovernanceConfig) -> Any:
    """Map public GovernanceConfig fields to OrchestratorConfig."""
    from moralstack.orchestration.types import OrchestratorConfig

    return OrchestratorConfig(
        max_deliberation_cycles=config.max_deliberation_cycles,
        timeout_ms=config.timeout_ms,
        enable_speculative_generation=config.enable_speculative_generation,
        # Deliberative modules are always enabled (not minimal mode)
        enable_perspectives=True,
        enable_simulation=True,
        enable_hindsight=True,
    )


def _bootstrap_pipeline(config: GovernanceConfig) -> Orchestrator:
    """
    Instantiate the full deliberative pipeline.

    Follows the same sequence as ModuleLoader._load_real_modules() in cli/loader.py
    but without console output and with typed exceptions.

    Raises:
        GovernanceConfigError: Missing API key or invalid configuration.
        GovernancePipelineError: Error while initializing a critical module.
    """
    api_key = _resolve_api_key(config)
    model = _resolve_model(config)
    base_url = config.base_url or os.getenv("OPENAI_BASE_URL") or None

    try:
        from moralstack.models.policy import OpenAIPolicy

        openai_policy = OpenAIPolicy(
            api_key=api_key,
            model=model,
            base_url=base_url,
        )
    except Exception as e:
        raise GovernancePipelineError("Failed to initialize OpenAI policy", cause=e) from e

    try:
        from moralstack.constitution.openai_config import OpenAIClientConfig
        from moralstack.constitution.store import ConstitutionStore, ConstitutionStoreConfig

        store_config_kwargs: dict[str, Any] = {
            "policy_llm": openai_policy,
            "use_llm_matching": True,
            "openai_config": OpenAIClientConfig.with_env_fallback(
                api_key=api_key,
                model=model,
            ),
        }
        if config.constitution_dir is not None:
            store_config_kwargs["config_dir"] = config.constitution_dir

        constitution_store = ConstitutionStore(config=ConstitutionStoreConfig(**store_config_kwargs))
    except Exception as e:
        raise GovernancePipelineError("Failed to initialize ConstitutionStore", cause=e) from e

    try:
        from moralstack.models.risk import LLMBasedRiskEstimator

        risk_estimator = LLMBasedRiskEstimator(
            policy=cast(Any, openai_policy),
            constitution_store=constitution_store,
        )
    except Exception as e:
        raise GovernancePipelineError("Failed to initialize risk estimator", cause=e) from e

    try:
        from moralstack.runtime.modules.critic_module import LLMConstitutionalCritic

        critic = LLMConstitutionalCritic(
            policy=cast(Any, openai_policy),
            store=constitution_store,
        )
    except Exception as e:
        raise GovernancePipelineError("Failed to initialize critic", cause=e) from e

    try:
        from moralstack.runtime.modules.simulator_module import LLMConsequenceSimulator

        simulator = LLMConsequenceSimulator(policy=cast(Any, openai_policy))
    except Exception as e:
        raise GovernancePipelineError("Failed to initialize simulator", cause=e) from e

    try:
        from moralstack.runtime.modules.hindsight_module import LLMHindsightEvaluator

        hindsight = LLMHindsightEvaluator(policy=cast(Any, openai_policy))
    except Exception as e:
        raise GovernancePipelineError("Failed to initialize hindsight evaluator", cause=e) from e

    try:
        from moralstack.runtime.modules.perspective_module import create_minimal_ensemble

        perspectives = create_minimal_ensemble(policy=cast(Any, openai_policy))
    except Exception as e:
        raise GovernancePipelineError("Failed to initialize perspective ensemble", cause=e) from e

    try:
        from moralstack.runtime.orchestrator import Orchestrator

        orch_config = _build_orchestrator_config(config)
        orchestrator = Orchestrator(
            config=orch_config,
            policy=openai_policy,
            risk_estimator=cast(Any, risk_estimator),
            critic=critic,
            simulator=simulator,
            hindsight=cast(Any, hindsight),
            perspectives=perspectives,
            constitution_store=cast(Any, constitution_store),
        )
    except Exception as e:
        raise GovernancePipelineError("Failed to initialize orchestrator", cause=e) from e

    return orchestrator
