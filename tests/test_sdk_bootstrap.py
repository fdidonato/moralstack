"""Tests for moralstack.sdk.bootstrap."""

import os
from unittest.mock import MagicMock, patch

import pytest

from moralstack.orchestration.types import OrchestratorConfig
from moralstack.pipeline.deliberation_stack import DeliberationBuildMeta, DeliberationModules
from moralstack.sdk.bootstrap import (
    _bootstrap_pipeline,
    _load_orchestrator_config,
    _resolve_api_key,
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


class TestLoadOrchestratorConfig:
    def test_uses_env_loader_config_as_base(self):
        base = OrchestratorConfig(max_deliberation_cycles=7, timeout_ms=12345, enable_speculative_generation=False)
        cfg = GovernanceConfig()
        with patch("moralstack.orchestration.config_loader.load_orchestrator_config_from_env", return_value=base):
            orch_cfg = _load_orchestrator_config(cfg)
        assert orch_cfg.max_deliberation_cycles == 7
        assert orch_cfg.timeout_ms == 12345
        assert orch_cfg.enable_speculative_generation is False

    def test_applies_explicit_overrides_only(self):
        base = OrchestratorConfig(max_deliberation_cycles=2, timeout_ms=600000, enable_speculative_generation=True)
        cfg = GovernanceConfig(max_deliberation_cycles=3, timeout_ms=30_000, enable_speculative_generation=False)
        with patch("moralstack.orchestration.config_loader.load_orchestrator_config_from_env", return_value=base):
            orch_cfg = _load_orchestrator_config(cfg)
        assert orch_cfg.max_deliberation_cycles == 3
        assert orch_cfg.timeout_ms == 30_000
        assert orch_cfg.enable_speculative_generation is False


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
                _bootstrap_pipeline(cfg)
