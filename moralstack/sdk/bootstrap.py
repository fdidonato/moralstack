"""Internal factory for the MoralStack SDK runtime pipeline."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

from moralstack.pipeline.deliberation_stack import build_deliberation_modules
from moralstack.sdk.errors import GovernanceConfigError, GovernancePipelineError
from moralstack.utils.env_loader import load_env

if TYPE_CHECKING:
    from moralstack.runtime.orchestrator import Orchestrator
    from moralstack.sdk.config import GovernanceConfig


def _resolve_api_key(config: GovernanceConfig) -> str:
    """Resolve API key: explicit config > env var. Raises if missing."""
    key = (config.api_key or os.getenv("OPENAI_API_KEY") or "").strip()
    if not key:
        raise GovernanceConfigError(
            "OPENAI_API_KEY is required. Set the environment variable or pass api_key in GovernanceConfig."
        )
    return key


def _resolve_model(config: GovernanceConfig) -> str:
    """Resolve model: explicit config > env var > default gpt-4o."""
    return (config.model or os.getenv("OPENAI_MODEL") or "gpt-4o").strip()


def _bootstrap_pipeline(config: GovernanceConfig) -> Orchestrator:
    """Instantiate the full deliberative pipeline for SDK use.

    All runtime tuning (orchestrator cycles, risk thresholds, module
    temperatures, etc.) is driven by MORALSTACK_* environment variables
    loaded from .env.  GovernanceConfig only controls provider credentials,
    constitution path, observability, and failure policy.
    """
    load_env()

    api_key = _resolve_api_key(config)
    model = _resolve_model(config)
    base_url = config.base_url or os.getenv("OPENAI_BASE_URL") or None

    try:
        modules, _ = build_deliberation_modules(
            api_key=api_key,
            primary_model=model,
            base_url=base_url,
            constitution_dir=config.constitution_dir,
            minimal=False,
        )
    except Exception as e:
        raise GovernancePipelineError("Failed to initialize deliberation modules", cause=e) from e

    try:
        from moralstack.orchestration.config_loader import load_orchestrator_config_from_env
        from moralstack.runtime.orchestrator import Orchestrator

        orchestrator = Orchestrator(
            config=load_orchestrator_config_from_env(),
            policy=modules.policy,
            risk_estimator=modules.risk_estimator,
            critic=modules.critic,
            simulator=modules.simulator,
            hindsight=modules.hindsight,
            perspectives=modules.perspectives,
            constitution_store=modules.constitution_store,
        )
    except Exception as e:
        raise GovernancePipelineError("Failed to initialize orchestrator", cause=e) from e

    return orchestrator
