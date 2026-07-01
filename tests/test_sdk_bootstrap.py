"""Tests for moralstack.sdk.bootstrap."""

import os
from unittest.mock import MagicMock, patch

import pytest

from moralstack.pipeline.deliberation_stack import DeliberationBuildMeta, DeliberationModules
from moralstack.sdk.bootstrap import (
    _bootstrap_pipeline,
    _build_ledger,
    _resolve_api_key,
    _resolve_embedder_provider,
    _resolve_model,
)
from moralstack.sdk.config import GovernanceConfig
from moralstack.sdk.errors import GovernanceConfigError, GovernancePipelineError


class TestResolveApiKey:
    def test_explicit_api_key_used(self):
        cfg = GovernanceConfig(api_key="sk-explicit")
        with patch.dict(os.environ, {"OPENAI_API_KEY": "sk-env"}):
            key = _resolve_api_key(cfg)
        assert key == "sk-explicit"

    def test_falls_back_to_env(self):
        cfg = GovernanceConfig()
        with patch.dict(os.environ, {"OPENAI_API_KEY": "sk-from-env"}):
            key = _resolve_api_key(cfg)
        assert key == "sk-from-env"

    def test_raises_when_no_key(self):
        cfg = GovernanceConfig()
        env = {k: v for k, v in os.environ.items() if k != "OPENAI_API_KEY"}
        with patch.dict(os.environ, env, clear=True):
            with pytest.raises(GovernanceConfigError, match="OPENAI_API_KEY"):
                _resolve_api_key(cfg)


class TestResolveModel:
    def test_explicit_model_used(self):
        cfg = GovernanceConfig(model="gpt-4o-mini")
        model = _resolve_model(cfg)
        assert model == "gpt-4o-mini"

    def test_falls_back_to_env(self):
        cfg = GovernanceConfig()
        with patch.dict(os.environ, {"OPENAI_MODEL": "gpt-4o-custom"}):
            model = _resolve_model(cfg)
        assert model == "gpt-4o-custom"

    def test_default_is_gpt4o(self):
        cfg = GovernanceConfig()
        env = {k: v for k, v in os.environ.items() if k != "OPENAI_MODEL"}
        with patch.dict(os.environ, env, clear=True):
            model = _resolve_model(cfg)
        assert model == "gpt-4o"


class TestBootstrapPipeline:
    def test_raises_config_error_without_api_key(self):
        cfg = GovernanceConfig()
        env = {k: v for k, v in os.environ.items() if k != "OPENAI_API_KEY"}
        with patch.dict(os.environ, env, clear=True):
            with patch("moralstack.sdk.bootstrap.load_env", return_value=False):
                with pytest.raises(GovernanceConfigError):
                    _bootstrap_pipeline(cfg)

    def test_raises_pipeline_error_on_module_failure(self):
        cfg = GovernanceConfig(api_key="sk-test")
        with patch("moralstack.sdk.bootstrap.build_deliberation_modules", side_effect=RuntimeError("OpenAI down")):
            with pytest.raises(GovernancePipelineError, match="deliberation modules"):
                _bootstrap_pipeline(cfg)

    def test_api_key_present_does_not_raise_config_error(self):
        cfg = GovernanceConfig(api_key="sk-test")
        fake_modules = DeliberationModules(
            policy=MagicMock(),
            constitution_store=MagicMock(),
            risk_estimator=MagicMock(),
            critic=MagicMock(),
            simulator=MagicMock(),
            hindsight=MagicMock(),
            perspectives=MagicMock(),
        )
        fake_meta = DeliberationBuildMeta(
            policy_model="gpt-4o",
            risk_model="gpt-4o",
            critic_model="gpt-4o",
            simulator_model="gpt-4o",
            hindsight_model="gpt-4o",
            perspectives_model="gpt-4o",
        )
        with patch("moralstack.sdk.bootstrap.build_deliberation_modules", return_value=(fake_modules, fake_meta)):
            with patch("moralstack.runtime.orchestrator.Orchestrator", return_value=MagicMock()):
                with patch(
                    "moralstack.orchestration.embedder._FastEmbedWrapper.__init__",
                    side_effect=ImportError("fastembed not installed"),
                ):
                    _bootstrap_pipeline(cfg)


def test_bootstrap_creates_ledger_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """_bootstrap_pipeline must wire a SemanticDecisionLedger by default."""
    monkeypatch.setattr("moralstack.sdk.bootstrap.load_env", lambda: None)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-dummy")
    monkeypatch.delenv("MORALSTACK_LEDGER_ENABLED", raising=False)
    monkeypatch.delenv("MORALSTACK_LEDGER_SIMILARITY_THRESHOLD", raising=False)

    from moralstack.orchestration.ledger import SemanticDecisionLedger

    with patch(
        "moralstack.orchestration.embedder._FastEmbedWrapper.__init__",
        side_effect=ImportError("fastembed not installed"),
    ):
        orch = _bootstrap_pipeline(GovernanceConfig())
    assert orch.ledger is not None
    assert isinstance(orch.ledger, SemanticDecisionLedger)
    assert orch.ledger.similarity_threshold == 0.92


def test_bootstrap_disables_ledger_via_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """MORALSTACK_LEDGER_ENABLED=false must disable the ledger."""
    monkeypatch.setattr("moralstack.sdk.bootstrap.load_env", lambda: None)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-dummy")
    monkeypatch.setenv("MORALSTACK_LEDGER_ENABLED", "false")

    orch = _bootstrap_pipeline(GovernanceConfig())
    assert orch.ledger is None


def test_bootstrap_disables_ledger_via_config(monkeypatch: pytest.MonkeyPatch) -> None:
    """GovernanceConfig.enable_ledger=False disables the ledger when env is unset."""
    monkeypatch.setattr("moralstack.sdk.bootstrap.load_env", lambda: None)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-dummy")
    monkeypatch.delenv("MORALSTACK_LEDGER_ENABLED", raising=False)

    orch = _bootstrap_pipeline(GovernanceConfig(enable_ledger=False))
    assert orch.ledger is None


def test_bootstrap_respects_threshold_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """MORALSTACK_LEDGER_SIMILARITY_THRESHOLD must override the default."""
    monkeypatch.setattr("moralstack.sdk.bootstrap.load_env", lambda: None)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-dummy")
    monkeypatch.delenv("MORALSTACK_LEDGER_ENABLED", raising=False)
    monkeypatch.setenv("MORALSTACK_LEDGER_SIMILARITY_THRESHOLD", "0.85")

    with patch(
        "moralstack.orchestration.embedder._FastEmbedWrapper.__init__",
        side_effect=ImportError("fastembed not installed"),
    ):
        orch = _bootstrap_pipeline(GovernanceConfig())
    assert orch.ledger is not None
    assert orch.ledger.similarity_threshold == 0.85


def test_build_ledger_uses_local_embedder_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MORALSTACK_LEDGER_ENABLED", raising=False)
    from moralstack.orchestration.embedder import LocalEmbedder, OpenAIEmbedder

    with patch(
        "moralstack.orchestration.embedder._FastEmbedWrapper.__init__",
        side_effect=ImportError("fastembed not installed"),
    ):
        ledger = _build_ledger(GovernanceConfig(), api_key="sk-x", base_url=None)
    assert ledger is not None
    assert isinstance(ledger._embedder, LocalEmbedder)
    assert not isinstance(ledger._embedder, OpenAIEmbedder)


def test_build_ledger_uses_openai_embedder_when_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MORALSTACK_LEDGER_ENABLED", raising=False)
    from moralstack.orchestration.embedder import OpenAIEmbedder

    with patch("openai.OpenAI"):
        ledger = _build_ledger(
            GovernanceConfig(embedder_provider="openai"),
            api_key="sk-test",
            base_url=None,
        )
    assert ledger is not None
    assert isinstance(ledger._embedder, OpenAIEmbedder)


def test_resolve_embedder_provider_env_overrides_config(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MORALSTACK_EMBEDDER_PROVIDER", "openai")
    assert _resolve_embedder_provider(GovernanceConfig(embedder_provider="local")) == "openai"


def test_resolve_embedder_provider_defaults_to_local(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MORALSTACK_EMBEDDER_PROVIDER", raising=False)
    assert _resolve_embedder_provider(GovernanceConfig()) == "local"


def test_bootstrap_local_embedder_does_not_require_embedder_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MORALSTACK_LEDGER_ENABLED", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with patch(
        "moralstack.orchestration.embedder._FastEmbedWrapper.__init__",
        side_effect=ImportError("fastembed not installed"),
    ):
        ledger = _build_ledger(GovernanceConfig(embedder_provider="local"), api_key="", base_url=None)
    assert ledger is not None


def test_bootstrap_ledger_embedder_type_is_local_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("moralstack.sdk.bootstrap.load_env", lambda: None)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-dummy")
    monkeypatch.delenv("MORALSTACK_LEDGER_ENABLED", raising=False)
    from moralstack.orchestration.embedder import LocalEmbedder, OpenAIEmbedder

    with patch(
        "moralstack.orchestration.embedder._FastEmbedWrapper.__init__",
        side_effect=ImportError("fastembed not installed"),
    ):
        orch = _bootstrap_pipeline(GovernanceConfig())
    assert isinstance(orch.ledger._embedder, LocalEmbedder)
    assert not isinstance(orch.ledger._embedder, OpenAIEmbedder)


def test_build_ledger_openai_provider_without_api_key_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MORALSTACK_LEDGER_ENABLED", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    ledger = _build_ledger(GovernanceConfig(embedder_provider="openai"), api_key="", base_url=None)
    assert ledger is None
