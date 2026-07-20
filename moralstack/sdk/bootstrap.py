"""Internal factory for the MoralStack SDK runtime pipeline."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, Any

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


_VALID_GENERATION_MODES = frozenset({"internal", "upstream_then_verify"})


def _resolve_generation_mode(config: GovernanceConfig) -> str:
    """Resolve the speculative-draft generation mode: env > config, default 'internal'.

    Tolerant to case/whitespace (mirrors ``_resolve_ledger_enabled``). Fails
    closed: an unknown env/config value never silently activates
    ``upstream_then_verify`` — it resolves to 'internal' so an opt-in-only
    feature never activates without an explicit, recognized value.
    """
    env_val = os.getenv("MORALSTACK_GENERATION_MODE", "").strip().lower()
    if env_val:
        return env_val if env_val in _VALID_GENERATION_MODES else "internal"
    configured = (getattr(config, "generation", None) or "internal").strip().lower()
    return configured if configured in _VALID_GENERATION_MODES else "internal"


def _resolve_ledger_enabled(config: GovernanceConfig) -> bool:
    """Resolve ledger enable flag: env var overrides config when set."""
    env_val = os.getenv("MORALSTACK_LEDGER_ENABLED", "").strip().lower()
    if env_val:
        return env_val in ("true", "1", "yes", "on")
    return bool(config.enable_ledger)


def _resolve_ledger_threshold(config: GovernanceConfig) -> float:
    raw = os.getenv("MORALSTACK_LEDGER_SIMILARITY_THRESHOLD", "").strip()
    if raw:
        try:
            return float(raw)
        except ValueError:
            pass
    return float(config.ledger_similarity_threshold)


def _resolve_ledger_max_entries(config: GovernanceConfig) -> int:
    raw = os.getenv("MORALSTACK_LEDGER_MAX_ENTRIES", "").strip()
    if raw:
        try:
            return int(raw)
        except ValueError:
            pass
    return int(config.ledger_max_entries)


def _resolve_ledger_embedding_model(config: GovernanceConfig) -> str | None:
    raw = config.ledger_embedding_model or os.getenv("MORALSTACK_LEDGER_EMBEDDING_MODEL") or None
    return raw.strip() if raw else None


_VALID_EMBEDDER_PROVIDERS = frozenset({"local", "openai"})


def _resolve_embedder_provider(config: GovernanceConfig) -> str:
    """Resolve embedder provider: env > config. Returns 'local' or 'openai'.

    Raises ValueError for any unknown value so misconfiguration fails loudly
    rather than silently falling back to 'local'.
    """
    raw = (os.getenv("MORALSTACK_EMBEDDER_PROVIDER") or "").strip().lower()
    if raw:
        if raw not in _VALID_EMBEDDER_PROVIDERS:
            raise ValueError(
                f"Unknown MORALSTACK_EMBEDDER_PROVIDER={raw!r}; must be one of {sorted(_VALID_EMBEDDER_PROVIDERS)}"
            )
        return raw
    provider = (getattr(config, "embedder_provider", None) or "local").lower()
    if provider not in _VALID_EMBEDDER_PROVIDERS:
        raise ValueError(f"Unknown embedder_provider={provider!r}; must be one of {sorted(_VALID_EMBEDDER_PROVIDERS)}")
    return provider


def _build_embedder(
    config: GovernanceConfig,
    api_key: str,
    base_url: str | None,
) -> Any:
    """
    Factory: returns the correct EmbedderProtocol implementation.

    Raises on misconfiguration (e.g. openai provider without API key).
    Caller (_build_ledger) wraps this in a try/except.
    """
    provider = _resolve_embedder_provider(config)
    if provider == "openai":
        from moralstack.orchestration.embedder import OpenAIEmbedder

        return OpenAIEmbedder(
            api_key=api_key,
            model=_resolve_ledger_embedding_model(config),
            base_url=base_url,
        )
    from moralstack.orchestration.embedder import LocalEmbedder

    return LocalEmbedder()


def _build_ledger(config: GovernanceConfig, api_key: str, base_url: str | None) -> Any:
    """
    Build ``SemanticDecisionLedger`` with a provider-selected embedder and ``InMemoryLedgerStorage``.

    Returns None when disabled or when construction fails (logged at WARNING); the
    pipeline continues without a fast-path.
    """
    import logging

    logger = logging.getLogger(__name__)

    if not _resolve_ledger_enabled(config):
        logger.info("MoralStack SDK: SemanticDecisionLedger disabled via env/config")
        return None

    try:
        from moralstack.orchestration.ledger import SemanticDecisionLedger
        from moralstack.orchestration.ledger_storage import InMemoryLedgerStorage
    except Exception as e:
        logger.warning("MoralStack SDK: ledger imports failed (%s); proceeding without fast-path", e)
        return None

    try:
        provider = _resolve_embedder_provider(config)
        embedder = _build_embedder(config, api_key=api_key, base_url=base_url)
        max_entries = _resolve_ledger_max_entries(config)
        storage = InMemoryLedgerStorage(max_entries=max_entries)
        threshold = _resolve_ledger_threshold(config)
        ledger = SemanticDecisionLedger(
            embedder=embedder,
            storage=storage,
            similarity_threshold=threshold,
        )
        logger.info(
            "MoralStack SDK: SemanticDecisionLedger enabled (provider=%s, threshold=%.3f, max_entries=%d)",
            provider,
            ledger.similarity_threshold,
            max_entries,
        )
        return ledger
    except Exception as e:
        logger.warning("MoralStack SDK: ledger construction failed (%s); proceeding without fast-path", e)
        return None


def _bootstrap_pipeline(config: GovernanceConfig) -> Orchestrator:
    """Instantiate the full deliberative pipeline for SDK use.

    Runtime tuning (cycles, risk thresholds, module temperatures, etc.) comes from
    ``MORALSTACK_*`` environment variables loaded from ``.env``. ``GovernanceConfig``
    covers provider credentials, constitution path, observability, failure policy,
    and ledger fast-path toggles.
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

    ledger = _build_ledger(config, api_key=api_key, base_url=base_url)

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
            ledger=ledger,
        )
    except Exception as e:
        raise GovernancePipelineError("Failed to initialize orchestrator", cause=e) from e

    return orchestrator
