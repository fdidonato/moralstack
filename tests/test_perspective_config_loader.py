"""
Unit tests for Perspective Ensemble config loading from environment.

Verifies that load_perspective_config_from_env() and helpers use
env when set and non-empty, and fall back to defaults otherwise.
"""

from __future__ import annotations

from moralstack.runtime.modules.perspective_config_loader import (
    ENV_CONSERVATIVE_ON_FAILURE,
    ENV_MAX_PERSPECTIVES,
    ENV_MAX_RETRIES,
    ENV_MAX_TOKENS,
    ENV_MODEL,
    ENV_PARALLEL_EVALUATION,
    ENV_TEMPERATURE,
    ENV_TOP_P,
    get_perspective_env_bool,
    get_perspective_env_float,
    get_perspective_env_int,
    get_perspective_env_str,
    load_perspective_config_from_env,
)


class TestGetPerspectiveEnvFloat:
    """Tests for get_perspective_env_float."""

    def test_missing_key_returns_default(self, monkeypatch):
        monkeypatch.delenv(ENV_TEMPERATURE, raising=False)
        assert get_perspective_env_float(ENV_TEMPERATURE, 0.1) == 0.1

    def test_empty_key_returns_default(self, monkeypatch):
        monkeypatch.setenv(ENV_TEMPERATURE, "")
        assert get_perspective_env_float(ENV_TEMPERATURE, 0.1) == 0.1

    def test_whitespace_only_returns_default(self, monkeypatch):
        monkeypatch.setenv(ENV_TEMPERATURE, "  ")
        assert get_perspective_env_float(ENV_TEMPERATURE, 0.1) == 0.1

    def test_valid_value_used(self, monkeypatch):
        monkeypatch.setenv(ENV_TEMPERATURE, "0.2")
        assert get_perspective_env_float(ENV_TEMPERATURE, 0.1) == 0.2

    def test_clamp_min_max(self, monkeypatch):
        monkeypatch.setenv(ENV_TEMPERATURE, "-0.1")
        assert get_perspective_env_float(ENV_TEMPERATURE, 0.1, 0.0, 2.0) == 0.0
        monkeypatch.setenv(ENV_TEMPERATURE, "2.5")
        assert get_perspective_env_float(ENV_TEMPERATURE, 0.1, 0.0, 2.0) == 2.0

    def test_invalid_value_returns_default(self, monkeypatch):
        monkeypatch.setenv(ENV_TEMPERATURE, "not_a_number")
        assert get_perspective_env_float(ENV_TEMPERATURE, 0.1) == 0.1


class TestGetPerspectiveEnvInt:
    """Tests for get_perspective_env_int."""

    def test_missing_key_returns_default(self, monkeypatch):
        monkeypatch.delenv(ENV_MAX_RETRIES, raising=False)
        assert get_perspective_env_int(ENV_MAX_RETRIES, 3) == 3

    def test_empty_key_returns_default(self, monkeypatch):
        monkeypatch.setenv(ENV_MAX_RETRIES, "")
        assert get_perspective_env_int(ENV_MAX_RETRIES, 3) == 3

    def test_valid_value_used(self, monkeypatch):
        monkeypatch.setenv(ENV_MAX_RETRIES, "5")
        assert get_perspective_env_int(ENV_MAX_RETRIES, 3) == 5

    def test_min_enforced(self, monkeypatch):
        monkeypatch.setenv(ENV_MAX_RETRIES, "0")
        assert get_perspective_env_int(ENV_MAX_RETRIES, 3, min_val=1) == 1

    def test_invalid_value_returns_default(self, monkeypatch):
        monkeypatch.setenv(ENV_MAX_RETRIES, "x")
        assert get_perspective_env_int(ENV_MAX_RETRIES, 3) == 3


class TestGetPerspectiveEnvStr:
    """Tests for get_perspective_env_str."""

    def test_missing_key_returns_default(self, monkeypatch):
        monkeypatch.delenv(ENV_MODEL, raising=False)
        assert get_perspective_env_str(ENV_MODEL, "gpt-4o") == "gpt-4o"

    def test_empty_key_returns_default(self, monkeypatch):
        monkeypatch.setenv(ENV_MODEL, "")
        assert get_perspective_env_str(ENV_MODEL, "gpt-4o") == "gpt-4o"

    def test_whitespace_only_returns_default(self, monkeypatch):
        monkeypatch.setenv(ENV_MODEL, "  ")
        assert get_perspective_env_str(ENV_MODEL, "gpt-4o") == "gpt-4o"

    def test_valid_value_used(self, monkeypatch):
        monkeypatch.setenv(ENV_MODEL, "gpt-4o-mini")
        assert get_perspective_env_str(ENV_MODEL, "gpt-4o") == "gpt-4o-mini"


class TestGetPerspectiveEnvBool:
    """Tests for get_perspective_env_bool."""

    def test_missing_key_returns_default(self, monkeypatch):
        monkeypatch.delenv(ENV_PARALLEL_EVALUATION, raising=False)
        assert get_perspective_env_bool(ENV_PARALLEL_EVALUATION, False) is False
        assert get_perspective_env_bool(ENV_PARALLEL_EVALUATION, True) is True

    def test_empty_key_returns_default(self, monkeypatch):
        monkeypatch.setenv(ENV_PARALLEL_EVALUATION, "")
        assert get_perspective_env_bool(ENV_PARALLEL_EVALUATION, False) is False

    def test_true_values(self, monkeypatch):
        for val in ("1", "true", "TRUE", "yes", "Yes"):
            monkeypatch.setenv(ENV_PARALLEL_EVALUATION, val)
            assert get_perspective_env_bool(ENV_PARALLEL_EVALUATION, False) is True

    def test_false_values(self, monkeypatch):
        for val in ("0", "false", "FALSE", "no", "No"):
            monkeypatch.setenv(ENV_CONSERVATIVE_ON_FAILURE, val)
            assert get_perspective_env_bool(ENV_CONSERVATIVE_ON_FAILURE, True) is False


class TestLoadPerspectiveConfigFromEnv:
    """Tests for load_perspective_config_from_env."""

    def test_empty_env_returns_defaults(self, monkeypatch):
        for key in (
            ENV_MAX_RETRIES,
            ENV_MAX_TOKENS,
            ENV_TEMPERATURE,
            ENV_TOP_P,
            ENV_PARALLEL_EVALUATION,
            "MORALSTACK_PERSPECTIVES_MAX_WORKERS",
            "MORALSTACK_PERSPECTIVES_TIMEOUT_SECONDS",
            ENV_MAX_PERSPECTIVES,
            ENV_CONSERVATIVE_ON_FAILURE,
            "MORALSTACK_PERSPECTIVES_ENABLE_CACHING",
        ):
            monkeypatch.delenv(key, raising=False)
        config = load_perspective_config_from_env()
        assert config.max_retries == 3
        assert config.max_tokens == 512
        assert config.temperature == 0.1
        assert config.top_p == 0.9
        assert config.parallel_evaluation is True
        assert config.max_workers == 3
        assert config.timeout_seconds == 60.0
        assert config.max_perspectives == 2
        assert config.conservative_on_failure is True
        assert config.enable_caching is False

    def test_max_retries_override(self, monkeypatch):
        monkeypatch.setenv(ENV_MAX_RETRIES, "5")
        config = load_perspective_config_from_env()
        assert config.max_retries == 5
        assert config.max_tokens == 512

    def test_empty_max_retries_uses_default(self, monkeypatch):
        monkeypatch.setenv(ENV_MAX_RETRIES, "")
        config = load_perspective_config_from_env()
        assert config.max_retries == 3

    def test_temperature_override(self, monkeypatch):
        monkeypatch.setenv(ENV_TEMPERATURE, "0.2")
        config = load_perspective_config_from_env()
        assert config.temperature == 0.2

    def test_top_p_override(self, monkeypatch):
        monkeypatch.setenv(ENV_TOP_P, "0.85")
        config = load_perspective_config_from_env()
        assert config.top_p == 0.85

    def test_top_p_clamped(self, monkeypatch):
        monkeypatch.setenv(ENV_TOP_P, "1.5")
        config = load_perspective_config_from_env()
        assert config.top_p == 1.0

    def test_parallel_evaluation_true(self, monkeypatch):
        monkeypatch.setenv(ENV_PARALLEL_EVALUATION, "true")
        config = load_perspective_config_from_env()
        assert config.parallel_evaluation is True

    def test_max_perspectives_override(self, monkeypatch):
        monkeypatch.setenv(ENV_MAX_PERSPECTIVES, "5")
        config = load_perspective_config_from_env()
        assert config.max_perspectives == 5

    def test_conservative_on_failure_false(self, monkeypatch):
        monkeypatch.setenv(ENV_CONSERVATIVE_ON_FAILURE, "false")
        config = load_perspective_config_from_env()
        assert config.conservative_on_failure is False
