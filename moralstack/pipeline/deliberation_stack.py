"""Shared deliberation stack factory for SDK and CLI.

This module centralizes runtime module construction so SDK bootstrap and CLI
loader use the same environment-driven wiring.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, cast

from moralstack.constitution.openai_config import OpenAIClientConfig
from moralstack.constitution.store import ConstitutionStore, ConstitutionStoreConfig
from moralstack.models.policy import OpenAIPolicy
from moralstack.models.risk import LLMBasedRiskEstimator
from moralstack.models.risk.config_loader import ENV_MODEL as RISK_ENV_MODEL
from moralstack.models.risk.config_loader import get_risk_env_str
from moralstack.runtime.modules.critic_config_loader import ENV_MODEL as CRITIC_ENV_MODEL
from moralstack.runtime.modules.critic_config_loader import get_critic_env_str
from moralstack.runtime.modules.critic_module import LLMConstitutionalCritic
from moralstack.runtime.modules.hindsight_config_loader import ENV_MODEL as HINDSIGHT_ENV_MODEL
from moralstack.runtime.modules.hindsight_config_loader import get_hindsight_env_str
from moralstack.runtime.modules.hindsight_module import LLMHindsightEvaluator
from moralstack.runtime.modules.perspective_config_loader import ENV_MODEL as PERSPECTIVES_ENV_MODEL
from moralstack.runtime.modules.perspective_config_loader import get_perspective_env_str
from moralstack.runtime.modules.perspective_module import create_minimal_ensemble
from moralstack.runtime.modules.simulator_config_loader import ENV_MODEL as SIMULATOR_ENV_MODEL
from moralstack.runtime.modules.simulator_config_loader import get_simulator_env_str
from moralstack.runtime.modules.simulator_module import LLMConsequenceSimulator
from moralstack.utils.env_helpers import get_env_int

ENV_CONSTITUTION_MAX_PARALLEL_AGENTS = "MORALSTACK_CONSTITUTION_MAX_PARALLEL_AGENTS"


@dataclass(frozen=True)
class DeliberationModules:
    """Container for fully-initialized deliberation modules."""

    policy: OpenAIPolicy
    constitution_store: ConstitutionStore
    risk_estimator: Any
    critic: Any | None
    simulator: Any | None
    hindsight: Any | None
    perspectives: Any | None


@dataclass(frozen=True)
class DeliberationBuildMeta:
    """Resolved model ids used by each module."""

    policy_model: str
    risk_model: str
    critic_model: str
    simulator_model: str
    hindsight_model: str
    perspectives_model: str


def resolve_constitution_max_parallel_agents(explicit: int | None = None) -> int:
    """Resolve constitution agent parallelism from explicit value or environment."""
    if explicit is not None:
        return max(1, explicit)
    return get_env_int(ENV_CONSTITUTION_MAX_PARALLEL_AGENTS, 2, 1)


def _resolve_optional_model(
    env_key: str,
    getter: Any,
    primary_policy: OpenAIPolicy,
    api_key: str,
) -> tuple[OpenAIPolicy, str]:
    model = getter(env_key, "")
    if model:
        return OpenAIPolicy(api_key=api_key, model=model), model
    return primary_policy, primary_policy.model


def build_deliberation_modules(
    *,
    api_key: str,
    primary_model: str,
    base_url: str | None = None,
    constitution_dir: str | None = None,
    max_parallel_agents: int | None = None,
    minimal: bool = False,
) -> tuple[DeliberationModules, DeliberationBuildMeta]:
    """Build the full deliberation module graph with env-driven module models."""
    policy = OpenAIPolicy(
        api_key=api_key,
        model=primary_model,
        base_url=base_url,
    )

    resolved_parallel_agents = resolve_constitution_max_parallel_agents(max_parallel_agents)

    store_cfg = ConstitutionStoreConfig(
        policy_llm=policy,
        use_llm_matching=True,
        openai_config=OpenAIClientConfig.with_env_fallback(
            api_key=api_key,
            model=primary_model,
        ),
        max_parallel_agents=resolved_parallel_agents,
    )
    if constitution_dir is not None:
        store_cfg = replace(store_cfg, config_dir=constitution_dir)

    constitution_store = ConstitutionStore(config=store_cfg)

    risk_policy, risk_model = _resolve_optional_model(RISK_ENV_MODEL, get_risk_env_str, policy, api_key)
    risk_estimator = LLMBasedRiskEstimator(
        policy=cast(Any, risk_policy),
        constitution_store=constitution_store,
    )

    if minimal:
        modules = DeliberationModules(
            policy=policy,
            constitution_store=constitution_store,
            risk_estimator=risk_estimator,
            critic=None,
            simulator=None,
            hindsight=None,
            perspectives=None,
        )
        meta = DeliberationBuildMeta(
            policy_model=policy.model,
            risk_model=risk_model,
            critic_model="disabled",
            simulator_model="disabled",
            hindsight_model="disabled",
            perspectives_model="disabled",
        )
        return modules, meta

    critic_policy, critic_model = _resolve_optional_model(CRITIC_ENV_MODEL, get_critic_env_str, policy, api_key)
    critic = LLMConstitutionalCritic(
        policy=cast(Any, critic_policy),
        store=constitution_store,
    )

    simulator_policy, simulator_model = _resolve_optional_model(
        SIMULATOR_ENV_MODEL,
        get_simulator_env_str,
        policy,
        api_key,
    )
    simulator = LLMConsequenceSimulator(policy=cast(Any, simulator_policy))

    hindsight_policy, hindsight_model = _resolve_optional_model(
        HINDSIGHT_ENV_MODEL,
        get_hindsight_env_str,
        policy,
        api_key,
    )
    hindsight = LLMHindsightEvaluator(policy=cast(Any, hindsight_policy))

    perspectives_policy, perspectives_model = _resolve_optional_model(
        PERSPECTIVES_ENV_MODEL,
        get_perspective_env_str,
        policy,
        api_key,
    )
    perspectives = create_minimal_ensemble(policy=cast(Any, perspectives_policy))

    modules = DeliberationModules(
        policy=policy,
        constitution_store=constitution_store,
        risk_estimator=risk_estimator,
        critic=critic,
        simulator=simulator,
        hindsight=hindsight,
        perspectives=perspectives,
    )
    meta = DeliberationBuildMeta(
        policy_model=policy.model,
        risk_model=risk_model,
        critic_model=critic_model,
        simulator_model=simulator_model,
        hindsight_model=hindsight_model,
        perspectives_model=perspectives_model,
    )
    return modules, meta
