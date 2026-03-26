"""
Unit tests for simulator config loading from environment.

Verifies that load_simulator_config_from_env() and helpers use
env when set and non-empty, and fall back to defaults otherwise.
"""

from __future__ import annotations

from moralstack.runtime.modules.simulator_config_loader import (
    ENV_DEFAULT_NUM_SCENARIOS,
    ENV_ENABLE_CACHING,
    ENV_MAX_RETRIES,
    ENV_MAX_TOKENS,
    ENV_MODEL,
    ENV_TEMPERATURE,
    ENV_TOP_P,
    ENV_USE_SEEDED_GENERATION,
    get_simulator_env_bool,
    get_simulator_env_float,
    get_simulator_env_int,
    get_simulator_env_str,
    load_simulator_config_from_env,
)


class TestGetSimulatorEnvFloat:
    """Tests for get_simulator_env_float."""

    def test_missing_key_returns_default(self, monkeypatch):
        monkeypatch.delenv(ENV_TEMPERATURE, raising=False)
        assert get_simulator_env_float(ENV_TEMPERATURE, 0.8) == 0.8

    def test_empty_key_returns_default(self, monkeypatch):
        monkeypatch.setenv(ENV_TEMPERATURE, "")
        assert get_simulator_env_float(ENV_TEMPERATURE, 0.8) == 0.8

    def test_whitespace_only_returns_default(self, monkeypatch):
        monkeypatch.setenv(ENV_TEMPERATURE, "  ")
        assert get_simulator_env_float(ENV_TEMPERATURE, 0.8) == 0.8

    def test_valid_value_used(self, monkeypatch):
        monkeypatch.setenv(ENV_TEMPERATURE, "0.5")
        assert get_simulator_env_float(ENV_TEMPERATURE, 0.8) == 0.5

    def test_clamp_min_max(self, monkeypatch):
        monkeypatch.setenv(ENV_TEMPERATURE, "-0.1")
        assert get_simulator_env_float(ENV_TEMPERATURE, 0.8, 0.0, 2.0) == 0.0
        monkeypatch.setenv(ENV_TEMPERATURE, "2.5")
        assert get_simulator_env_float(ENV_TEMPERATURE, 0.8, 0.0, 2.0) == 2.0

    def test_invalid_value_returns_default(self, monkeypatch):
        monkeypatch.setenv(ENV_TEMPERATURE, "not_a_number")
        assert get_simulator_env_float(ENV_TEMPERATURE, 0.8) == 0.8


class TestGetSimulatorEnvInt:
    """Tests for get_simulator_env_int."""

    def test_missing_key_returns_default(self, monkeypatch):
        monkeypatch.delenv(ENV_MAX_RETRIES, raising=False)
        assert get_simulator_env_int(ENV_MAX_RETRIES, 3) == 3

    def test_empty_key_returns_default(self, monkeypatch):
        monkeypatch.setenv(ENV_MAX_RETRIES, "")
        assert get_simulator_env_int(ENV_MAX_RETRIES, 3) == 3

    def test_valid_value_used(self, monkeypatch):
        monkeypatch.setenv(ENV_MAX_RETRIES, "5")
        assert get_simulator_env_int(ENV_MAX_RETRIES, 3) == 5

    def test_min_enforced(self, monkeypatch):
        monkeypatch.setenv(ENV_MAX_RETRIES, "0")
        assert get_simulator_env_int(ENV_MAX_RETRIES, 3, min_val=1) == 1

    def test_invalid_value_returns_default(self, monkeypatch):
        monkeypatch.setenv(ENV_MAX_RETRIES, "x")
        assert get_simulator_env_int(ENV_MAX_RETRIES, 3) == 3


class TestGetSimulatorEnvStr:
    """Tests for get_simulator_env_str."""

    def test_missing_key_returns_default(self, monkeypatch):
        monkeypatch.delenv(ENV_MODEL, raising=False)
        assert get_simulator_env_str(ENV_MODEL, "gpt-4o") == "gpt-4o"

    def test_empty_key_returns_default(self, monkeypatch):
        monkeypatch.setenv(ENV_MODEL, "")
        assert get_simulator_env_str(ENV_MODEL, "gpt-4o") == "gpt-4o"

    def test_whitespace_only_returns_default(self, monkeypatch):
        monkeypatch.setenv(ENV_MODEL, "  ")
        assert get_simulator_env_str(ENV_MODEL, "gpt-4o") == "gpt-4o"

    def test_valid_value_used(self, monkeypatch):
        monkeypatch.setenv(ENV_MODEL, "gpt-4o-mini")
        assert get_simulator_env_str(ENV_MODEL, "gpt-4o") == "gpt-4o-mini"


class TestGetSimulatorEnvBool:
    """Tests for get_simulator_env_bool."""

    def test_missing_key_returns_default(self, monkeypatch):
        monkeypatch.delenv(ENV_USE_SEEDED_GENERATION, raising=False)
        assert get_simulator_env_bool(ENV_USE_SEEDED_GENERATION, False) is False
        assert get_simulator_env_bool(ENV_USE_SEEDED_GENERATION, True) is True

    def test_empty_key_returns_default(self, monkeypatch):
        monkeypatch.setenv(ENV_USE_SEEDED_GENERATION, "")
        assert get_simulator_env_bool(ENV_USE_SEEDED_GENERATION, False) is False

    def test_true_values(self, monkeypatch):
        for val in ("1", "true", "TRUE", "yes", "Yes"):
            monkeypatch.setenv(ENV_USE_SEEDED_GENERATION, val)
            assert get_simulator_env_bool(ENV_USE_SEEDED_GENERATION, False) is True

    def test_false_values(self, monkeypatch):
        for val in ("0", "false", "FALSE", "no", "No"):
            monkeypatch.setenv(ENV_USE_SEEDED_GENERATION, val)
            assert get_simulator_env_bool(ENV_USE_SEEDED_GENERATION, True) is False


class TestLoadSimulatorConfigFromEnv:
    """Tests for load_simulator_config_from_env."""

    def test_empty_env_returns_defaults(self, monkeypatch):
        for key in (
            ENV_MAX_RETRIES,
            ENV_MAX_TOKENS,
            ENV_TEMPERATURE,
            ENV_TOP_P,
            ENV_DEFAULT_NUM_SCENARIOS,
            ENV_USE_SEEDED_GENERATION,
            ENV_ENABLE_CACHING,
        ):
            monkeypatch.delenv(key, raising=False)
        config = load_simulator_config_from_env()
        assert config.max_retries == 3
        assert config.max_tokens == 384
        assert config.temperature == 0.8
        assert config.top_p == 0.95
        assert config.default_num_scenarios == 3
        assert config.use_seeded_generation is False
        assert config.enable_caching is True

    def test_max_retries_override(self, monkeypatch):
        monkeypatch.setenv(ENV_MAX_RETRIES, "5")
        config = load_simulator_config_from_env()
        assert config.max_retries == 5

    def test_temperature_override(self, monkeypatch):
        monkeypatch.setenv(ENV_TEMPERATURE, "0.5")
        config = load_simulator_config_from_env()
        assert config.temperature == 0.5

    def test_temperature_clamped(self, monkeypatch):
        monkeypatch.setenv(ENV_TEMPERATURE, "3.0")
        config = load_simulator_config_from_env()
        assert config.temperature == 2.0

    def test_top_p_override(self, monkeypatch):
        monkeypatch.setenv(ENV_TOP_P, "0.8")
        config = load_simulator_config_from_env()
        assert config.top_p == 0.8

    def test_top_p_clamped(self, monkeypatch):
        monkeypatch.setenv(ENV_TOP_P, "1.2")
        config = load_simulator_config_from_env()
        assert config.top_p == 1.0

    def test_default_num_scenarios_override(self, monkeypatch):
        monkeypatch.setenv(ENV_DEFAULT_NUM_SCENARIOS, "5")
        config = load_simulator_config_from_env()
        assert config.default_num_scenarios == 5

    def test_use_seeded_generation_true(self, monkeypatch):
        monkeypatch.setenv(ENV_USE_SEEDED_GENERATION, "true")
        config = load_simulator_config_from_env()
        assert config.use_seeded_generation is True

    def test_enable_caching_false(self, monkeypatch):
        monkeypatch.setenv(ENV_ENABLE_CACHING, "false")
        config = load_simulator_config_from_env()
        assert config.enable_caching is False

    def test_empty_temperature_uses_default(self, monkeypatch):
        monkeypatch.setenv(ENV_TEMPERATURE, "")
        config = load_simulator_config_from_env()
        assert config.temperature == 0.8
