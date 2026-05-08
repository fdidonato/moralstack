"""
Unit tests for orchestrator config loading from environment.

Verifies that load_orchestrator_config_from_env() and helpers use
env when set and non-empty, and fall back to defaults otherwise.
"""

from __future__ import annotations

import pytest

from moralstack.orchestration.config_loader import (
    ENV_BORDERLINE_REFUSE_UPPER,
    ENV_EARLY_EXIT_HINDSIGHT_THRESHOLD,
    ENV_ENABLE_HINDSIGHT,
    ENV_ENABLE_HINDSIGHT_GATING,
    ENV_ENABLE_PERSPECTIVES,
    ENV_ENABLE_SIMULATION,
    ENV_ENABLE_SIMULATOR_GATING,
    ENV_MAX_CRITICAL_VIOLATIONS,
    ENV_MAX_DELIBERATION_CYCLES,
    ENV_MIN_HINDSIGHT_SCORE,
    ENV_NUM_SIMULATIONS,
    ENV_PARALLEL_MODULE_CALLS,
    ENV_RISK_LOW_THRESHOLD,
    ENV_RISK_MEDIUM_THRESHOLD,
    ENV_SAFE_RESPONSE_ON_ERROR,
    ENV_SIMULATOR_GATE_DELTA_CHARS_THRESHOLD,
    ENV_SIMULATOR_GATE_SEMANTIC_HARM_THRESHOLD,
    ENV_SKIP_OPTIONAL_MODULES_THRESHOLD,
    ENV_SOFT_TIMEOUT_THRESHOLD,
    ENV_TIMEOUT_MS,
    get_orchestrator_env_bool,
    get_orchestrator_env_float,
    get_orchestrator_env_int,
    get_orchestrator_env_str,
    load_orchestrator_config_from_env,
)


class TestGetOrchestratorEnvFloat:
    """Tests for get_orchestrator_env_float."""

    def test_missing_key_returns_default(self, monkeypatch):
        monkeypatch.delenv(ENV_RISK_LOW_THRESHOLD, raising=False)
        assert get_orchestrator_env_float(ENV_RISK_LOW_THRESHOLD, 0.3) == 0.3

    def test_empty_key_returns_default(self, monkeypatch):
        monkeypatch.setenv(ENV_RISK_LOW_THRESHOLD, "")
        assert get_orchestrator_env_float(ENV_RISK_LOW_THRESHOLD, 0.3) == 0.3

    def test_whitespace_only_returns_default(self, monkeypatch):
        monkeypatch.setenv(ENV_RISK_LOW_THRESHOLD, "  ")
        assert get_orchestrator_env_float(ENV_RISK_LOW_THRESHOLD, 0.3) == 0.3

    def test_valid_value_used(self, monkeypatch):
        monkeypatch.setenv(ENV_RISK_LOW_THRESHOLD, "0.4")
        assert get_orchestrator_env_float(ENV_RISK_LOW_THRESHOLD, 0.3) == 0.4

    def test_clamp_min_max(self, monkeypatch):
        monkeypatch.setenv(ENV_RISK_LOW_THRESHOLD, "-0.1")
        assert get_orchestrator_env_float(ENV_RISK_LOW_THRESHOLD, 0.3, 0.0, 1.0) == 0.0
        monkeypatch.setenv(ENV_RISK_LOW_THRESHOLD, "1.5")
        assert get_orchestrator_env_float(ENV_RISK_LOW_THRESHOLD, 0.3, 0.0, 1.0) == 1.0

    def test_invalid_value_returns_default(self, monkeypatch):
        monkeypatch.setenv(ENV_RISK_LOW_THRESHOLD, "not_a_number")
        assert get_orchestrator_env_float(ENV_RISK_LOW_THRESHOLD, 0.3) == 0.3


class TestGetOrchestratorEnvInt:
    """Tests for get_orchestrator_env_int."""

    def test_missing_key_returns_default(self, monkeypatch):
        monkeypatch.delenv(ENV_MAX_DELIBERATION_CYCLES, raising=False)
        assert get_orchestrator_env_int(ENV_MAX_DELIBERATION_CYCLES, 2) == 2

    def test_empty_key_returns_default(self, monkeypatch):
        monkeypatch.setenv(ENV_MAX_DELIBERATION_CYCLES, "")
        assert get_orchestrator_env_int(ENV_MAX_DELIBERATION_CYCLES, 2) == 2

    def test_valid_value_used(self, monkeypatch):
        monkeypatch.setenv(ENV_MAX_DELIBERATION_CYCLES, "5")
        assert get_orchestrator_env_int(ENV_MAX_DELIBERATION_CYCLES, 2) == 5

    def test_min_enforced(self, monkeypatch):
        monkeypatch.setenv(ENV_MAX_DELIBERATION_CYCLES, "0")
        assert get_orchestrator_env_int(ENV_MAX_DELIBERATION_CYCLES, 2, min_val=1) == 1

    def test_invalid_value_returns_default(self, monkeypatch):
        monkeypatch.setenv(ENV_MAX_DELIBERATION_CYCLES, "x")
        assert get_orchestrator_env_int(ENV_MAX_DELIBERATION_CYCLES, 2) == 2


class TestGetOrchestratorEnvStr:
    """Tests for get_orchestrator_env_str."""

    def test_missing_key_returns_default(self, monkeypatch):
        monkeypatch.delenv(ENV_PARALLEL_MODULE_CALLS, raising=False)
        assert get_orchestrator_env_str(ENV_PARALLEL_MODULE_CALLS, "false") == "false"

    def test_empty_key_returns_default(self, monkeypatch):
        monkeypatch.setenv(ENV_PARALLEL_MODULE_CALLS, "")
        assert get_orchestrator_env_str(ENV_PARALLEL_MODULE_CALLS, "false") == "false"

    def test_whitespace_only_returns_default(self, monkeypatch):
        monkeypatch.setenv(ENV_PARALLEL_MODULE_CALLS, "  ")
        assert get_orchestrator_env_str(ENV_PARALLEL_MODULE_CALLS, "false") == "false"

    def test_valid_value_used(self, monkeypatch):
        monkeypatch.setenv(ENV_PARALLEL_MODULE_CALLS, "true")
        assert get_orchestrator_env_str(ENV_PARALLEL_MODULE_CALLS, "false") == "true"


class TestGetOrchestratorEnvBool:
    """Tests for get_orchestrator_env_bool."""

    def test_missing_key_returns_default(self, monkeypatch):
        monkeypatch.delenv(ENV_ENABLE_PERSPECTIVES, raising=False)
        assert get_orchestrator_env_bool(ENV_ENABLE_PERSPECTIVES, True) is True
        assert get_orchestrator_env_bool(ENV_ENABLE_PERSPECTIVES, False) is False

    def test_empty_key_returns_default(self, monkeypatch):
        monkeypatch.setenv(ENV_ENABLE_PERSPECTIVES, "")
        assert get_orchestrator_env_bool(ENV_ENABLE_PERSPECTIVES, True) is True

    def test_true_values(self, monkeypatch):
        for val in ("1", "true", "TRUE", "yes", "Yes"):
            monkeypatch.setenv(ENV_ENABLE_PERSPECTIVES, val)
            assert get_orchestrator_env_bool(ENV_ENABLE_PERSPECTIVES, False) is True

    def test_false_values(self, monkeypatch):
        for val in ("0", "false", "FALSE", "no", "No"):
            monkeypatch.setenv(ENV_ENABLE_PERSPECTIVES, val)
            assert get_orchestrator_env_bool(ENV_ENABLE_PERSPECTIVES, True) is False


class TestLoadOrchestratorConfigFromEnv:
    """Tests for load_orchestrator_config_from_env."""

    _ALL_KEYS = (
        ENV_MAX_DELIBERATION_CYCLES,
        ENV_RISK_LOW_THRESHOLD,
        ENV_RISK_MEDIUM_THRESHOLD,
        ENV_TIMEOUT_MS,
        ENV_ENABLE_PERSPECTIVES,
        ENV_NUM_SIMULATIONS,
        ENV_MIN_HINDSIGHT_SCORE,
        ENV_MAX_CRITICAL_VIOLATIONS,
        ENV_EARLY_EXIT_HINDSIGHT_THRESHOLD,
        ENV_ENABLE_SIMULATION,
        ENV_ENABLE_HINDSIGHT,
        ENV_SAFE_RESPONSE_ON_ERROR,
        ENV_SKIP_OPTIONAL_MODULES_THRESHOLD,
        ENV_SOFT_TIMEOUT_THRESHOLD,
        ENV_PARALLEL_MODULE_CALLS,
        ENV_ENABLE_SIMULATOR_GATING,
        ENV_ENABLE_HINDSIGHT_GATING,
        ENV_SIMULATOR_GATE_SEMANTIC_HARM_THRESHOLD,
        ENV_SIMULATOR_GATE_DELTA_CHARS_THRESHOLD,
        ENV_BORDERLINE_REFUSE_UPPER,
    )

    def test_empty_env_returns_defaults(self, monkeypatch):
        for key in self._ALL_KEYS:
            monkeypatch.delenv(key, raising=False)
        config = load_orchestrator_config_from_env()
        assert config.max_deliberation_cycles == 2
        assert config.risk_thresholds.low == 0.3
        assert config.risk_thresholds.medium == 0.7
        assert config.timeout_ms == 600000
        assert config.enable_perspectives is True
        assert config.num_simulations == 3
        assert config.min_hindsight_score == 0.8
        assert config.max_critical_violations == 0
        assert config.early_exit_hindsight_threshold == 0.6
        assert config.enable_simulation is True
        assert config.enable_hindsight is True
        assert config.safe_response_on_error is True
        assert config.skip_optional_modules_threshold == 0.95
        assert config.soft_timeout_threshold == 0.90
        assert config.parallel_module_calls is True
        assert config.enable_simulator_gating is False
        assert config.enable_hindsight_gating is True
        assert config.simulator_gate_semantic_harm_threshold == 0.4
        assert config.simulator_gate_delta_chars_threshold == 100
        assert config.borderline_refuse_upper == 0.95

    def test_max_cycles_override(self, monkeypatch):
        monkeypatch.setenv(ENV_MAX_DELIBERATION_CYCLES, "3")
        config = load_orchestrator_config_from_env()
        assert config.max_deliberation_cycles == 3

    def test_risk_thresholds_override(self, monkeypatch):
        monkeypatch.setenv(ENV_RISK_LOW_THRESHOLD, "0.4")
        monkeypatch.setenv(ENV_RISK_MEDIUM_THRESHOLD, "0.8")
        config = load_orchestrator_config_from_env()
        assert config.risk_thresholds.low == 0.4
        assert config.risk_thresholds.medium == 0.8

    def test_timeout_override(self, monkeypatch):
        monkeypatch.setenv(ENV_TIMEOUT_MS, "300000")
        config = load_orchestrator_config_from_env()
        assert config.timeout_ms == 300000

    def test_enable_perspectives_false(self, monkeypatch):
        monkeypatch.setenv(ENV_ENABLE_PERSPECTIVES, "false")
        config = load_orchestrator_config_from_env()
        assert config.enable_perspectives is False

    def test_enable_simulation_false(self, monkeypatch):
        monkeypatch.setenv(ENV_ENABLE_SIMULATION, "false")
        config = load_orchestrator_config_from_env()
        assert config.enable_simulation is False

    def test_enable_hindsight_false(self, monkeypatch):
        monkeypatch.setenv(ENV_ENABLE_HINDSIGHT, "false")
        config = load_orchestrator_config_from_env()
        assert config.enable_hindsight is False

    def test_enable_hindsight_gating_false(self, monkeypatch):
        monkeypatch.setenv(ENV_ENABLE_HINDSIGHT_GATING, "false")
        config = load_orchestrator_config_from_env()
        assert config.enable_hindsight_gating is False

    def test_borderline_refuse_upper_override(self, monkeypatch):
        monkeypatch.setenv(ENV_BORDERLINE_REFUSE_UPPER, "0.90")
        config = load_orchestrator_config_from_env()
        assert config.borderline_refuse_upper == 0.90

    def test_min_hindsight_score_override(self, monkeypatch):
        monkeypatch.setenv(ENV_MIN_HINDSIGHT_SCORE, "0.9")
        config = load_orchestrator_config_from_env()
        assert config.min_hindsight_score == 0.9

    def test_risk_low_threshold_clamped(self, monkeypatch):
        monkeypatch.setenv(ENV_RISK_LOW_THRESHOLD, "-0.5")
        config = load_orchestrator_config_from_env()
        assert config.risk_thresholds.low == 0.0

    def test_empty_timeout_uses_default(self, monkeypatch):
        monkeypatch.setenv(ENV_TIMEOUT_MS, "")
        config = load_orchestrator_config_from_env()
        assert config.timeout_ms == 600000

    def test_config_is_frozen(self, monkeypatch):
        config = load_orchestrator_config_from_env()
        with pytest.raises(AttributeError):
            config.max_deliberation_cycles = 5
