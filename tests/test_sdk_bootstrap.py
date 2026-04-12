"""Tests for moralstack.sdk.bootstrap — _bootstrap_pipeline(), _build_orchestrator_config()."""

import os
from unittest.mock import MagicMock, patch

import pytest

from moralstack.sdk.bootstrap import _bootstrap_pipeline, _build_orchestrator_config, _resolve_api_key, _resolve_model
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


class TestBuildOrchestratorConfig:
    def test_maps_max_cycles(self):
        cfg = GovernanceConfig(max_deliberation_cycles=3)
        orch_cfg = _build_orchestrator_config(cfg)
        assert orch_cfg.max_deliberation_cycles == 3

    def test_maps_timeout(self):
        cfg = GovernanceConfig(timeout_ms=30_000)
        orch_cfg = _build_orchestrator_config(cfg)
        assert orch_cfg.timeout_ms == 30_000

    def test_maps_speculative_generation(self):
        cfg = GovernanceConfig(enable_speculative_generation=False)
        orch_cfg = _build_orchestrator_config(cfg)
        assert orch_cfg.enable_speculative_generation is False

    def test_deliberative_modules_always_enabled(self):
        cfg = GovernanceConfig()
        orch_cfg = _build_orchestrator_config(cfg)
        assert orch_cfg.enable_perspectives is True
        assert orch_cfg.enable_simulation is True
        assert orch_cfg.enable_hindsight is True


class TestBootstrapPipeline:
    def _mock_all_modules(self) -> dict:
        """Return a patch context for all pipeline modules."""
        return {
            "moralstack.models.policy.OpenAIPolicy": MagicMock,
            "moralstack.constitution.store.ConstitutionStore": MagicMock,
            "moralstack.constitution.store.ConstitutionStoreConfig": MagicMock,
            "moralstack.constitution.openai_config.OpenAIClientConfig": MagicMock,
            "moralstack.models.risk.LLMBasedRiskEstimator": MagicMock,
            "moralstack.runtime.modules.critic_module.LLMConstitutionalCritic": MagicMock,
            "moralstack.runtime.modules.simulator_module.LLMConsequenceSimulator": MagicMock,
            "moralstack.runtime.modules.hindsight_module.LLMHindsightEvaluator": MagicMock,
            "moralstack.runtime.modules.perspective_module.create_minimal_ensemble": MagicMock(return_value=MagicMock()),
            "moralstack.runtime.orchestrator.Orchestrator": MagicMock,
        }

    def test_raises_config_error_without_api_key(self):
        cfg = GovernanceConfig()
        env = {k: v for k, v in os.environ.items() if k != "OPENAI_API_KEY"}
        with patch.dict(os.environ, env, clear=True):
            with pytest.raises(GovernanceConfigError):
                _bootstrap_pipeline(cfg)

    def test_raises_pipeline_error_on_module_failure(self):
        """Bootstrap raises GovernancePipelineError when OpenAIPolicy fails."""
        cfg = GovernanceConfig(api_key="sk-test")
        # Inline imports in bootstrap.py happen inside the function, so we must
        # patch the original source module.
        with patch("moralstack.models.policy.OpenAIPolicy", side_effect=RuntimeError("OpenAI down")):
            with pytest.raises(GovernancePipelineError, match="OpenAI"):
                _bootstrap_pipeline(cfg)

    def test_api_key_present_does_not_raise_config_error(self):
        """With a valid api_key, bootstrap must not raise GovernanceConfigError."""
        cfg = GovernanceConfig(api_key="sk-test")
        # Bootstrap may fail for other reasons (missing modules in test env),
        # but it must not be GovernanceConfigError.
        try:
            _bootstrap_pipeline(cfg)
        except GovernanceConfigError:
            pytest.fail("GovernanceConfigError raised even with valid api_key")
        except Exception:
            pass  # Other errors are acceptable in test env
