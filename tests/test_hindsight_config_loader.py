"""
Unit tests for hindsight config loading from environment.

Verifies that load_hindsight_config_from_env() and helpers use
env when set and non-empty, and fall back to defaults otherwise.
"""

from __future__ import annotations

from moralstack.runtime.modules.hindsight_config_loader import (
    ENV_ENABLE_CACHING,
    ENV_MAX_RETRIES,
    ENV_MAX_TOKENS,
    ENV_MODEL,
    ENV_REFUSE_THRESHOLD,
    ENV_REVISE_THRESHOLD,
    ENV_TEMPERATURE,
    ENV_TOP_P,
    ENV_USE_BATCH_EVALUATION,
    ENV_WEIGHT_HELPFULNESS,
    ENV_WEIGHT_HONESTY,
    ENV_WEIGHT_SAFETY,
    get_hindsight_env_bool,
    get_hindsight_env_float,
    get_hindsight_env_int,
    get_hindsight_env_str,
    load_hindsight_config_from_env,
)


class TestGetHindsightEnvFloat:
    """Tests for get_hindsight_env_float."""

    def test_missing_key_returns_default(self, monkeypatch):
        monkeypatch.delenv(ENV_TEMPERATURE, raising=False)
        assert get_hindsight_env_float(ENV_TEMPERATURE, 0.3) == 0.3

    def test_empty_key_returns_default(self, monkeypatch):
        monkeypatch.setenv(ENV_TEMPERATURE, "")
        assert get_hindsight_env_float(ENV_TEMPERATURE, 0.3) == 0.3

    def test_whitespace_only_returns_default(self, monkeypatch):
        monkeypatch.setenv(ENV_TEMPERATURE, "  ")
        assert get_hindsight_env_float(ENV_TEMPERATURE, 0.3) == 0.3

    def test_valid_value_used(self, monkeypatch):
        monkeypatch.setenv(ENV_TEMPERATURE, "0.5")
        assert get_hindsight_env_float(ENV_TEMPERATURE, 0.3) == 0.5

    def test_clamp_min_max(self, monkeypatch):
        monkeypatch.setenv(ENV_TEMPERATURE, "-0.1")
        assert get_hindsight_env_float(ENV_TEMPERATURE, 0.3, 0.0, 2.0) == 0.0
        monkeypatch.setenv(ENV_TEMPERATURE, "2.5")
        assert get_hindsight_env_float(ENV_TEMPERATURE, 0.3, 0.0, 2.0) == 2.0

    def test_invalid_value_returns_default(self, monkeypatch):
        monkeypatch.setenv(ENV_TEMPERATURE, "not_a_number")
        assert get_hindsight_env_float(ENV_TEMPERATURE, 0.3) == 0.3


class TestGetHindsightEnvInt:
    """Tests for get_hindsight_env_int."""

    def test_missing_key_returns_default(self, monkeypatch):
        monkeypatch.delenv(ENV_MAX_RETRIES, raising=False)
        assert get_hindsight_env_int(ENV_MAX_RETRIES, 3) == 3

    def test_empty_key_returns_default(self, monkeypatch):
        monkeypatch.setenv(ENV_MAX_RETRIES, "")
        assert get_hindsight_env_int(ENV_MAX_RETRIES, 3) == 3

    def test_valid_value_used(self, monkeypatch):
        monkeypatch.setenv(ENV_MAX_RETRIES, "5")
        assert get_hindsight_env_int(ENV_MAX_RETRIES, 3) == 5

    def test_min_enforced(self, monkeypatch):
        monkeypatch.setenv(ENV_MAX_RETRIES, "0")
        assert get_hindsight_env_int(ENV_MAX_RETRIES, 3, min_val=1) == 1

    def test_invalid_value_returns_default(self, monkeypatch):
        monkeypatch.setenv(ENV_MAX_RETRIES, "x")
        assert get_hindsight_env_int(ENV_MAX_RETRIES, 3) == 3


class TestGetHindsightEnvStr:
    """Tests for get_hindsight_env_str."""

    def test_missing_key_returns_default(self, monkeypatch):
        monkeypatch.delenv(ENV_MODEL, raising=False)
        assert get_hindsight_env_str(ENV_MODEL, "gpt-4o") == "gpt-4o"

    def test_empty_key_returns_default(self, monkeypatch):
        monkeypatch.setenv(ENV_MODEL, "")
        assert get_hindsight_env_str(ENV_MODEL, "gpt-4o") == "gpt-4o"

    def test_whitespace_only_returns_default(self, monkeypatch):
        monkeypatch.setenv(ENV_MODEL, "  ")
        assert get_hindsight_env_str(ENV_MODEL, "gpt-4o") == "gpt-4o"

    def test_valid_value_used(self, monkeypatch):
        monkeypatch.setenv(ENV_MODEL, "gpt-4o-mini")
        assert get_hindsight_env_str(ENV_MODEL, "gpt-4o") == "gpt-4o-mini"


class TestGetHindsightEnvBool:
    """Tests for get_hindsight_env_bool."""

    def test_missing_key_returns_default(self, monkeypatch):
        monkeypatch.delenv(ENV_USE_BATCH_EVALUATION, raising=False)
        assert get_hindsight_env_bool(ENV_USE_BATCH_EVALUATION, True) is True
        assert get_hindsight_env_bool(ENV_USE_BATCH_EVALUATION, False) is False

    def test_empty_key_returns_default(self, monkeypatch):
        monkeypatch.setenv(ENV_USE_BATCH_EVALUATION, "")
        assert get_hindsight_env_bool(ENV_USE_BATCH_EVALUATION, True) is True

    def test_true_values(self, monkeypatch):
        for val in ("1", "true", "TRUE", "yes", "Yes"):
            monkeypatch.setenv(ENV_USE_BATCH_EVALUATION, val)
            assert get_hindsight_env_bool(ENV_USE_BATCH_EVALUATION, False) is True

    def test_false_values(self, monkeypatch):
        for val in ("0", "false", "FALSE", "no", "No"):
            monkeypatch.setenv(ENV_USE_BATCH_EVALUATION, val)
            assert get_hindsight_env_bool(ENV_USE_BATCH_EVALUATION, True) is False


class TestLoadHindsightConfigFromEnv:
    """Tests for load_hindsight_config_from_env."""

    def test_empty_env_returns_defaults(self, monkeypatch):
        for key in (
            ENV_MAX_RETRIES,
            ENV_MAX_TOKENS,
            ENV_TEMPERATURE,
            ENV_TOP_P,
            ENV_WEIGHT_SAFETY,
            ENV_WEIGHT_HELPFULNESS,
            ENV_WEIGHT_HONESTY,
            ENV_REFUSE_THRESHOLD,
            ENV_REVISE_THRESHOLD,
            ENV_USE_BATCH_EVALUATION,
            ENV_ENABLE_CACHING,
        ):
            monkeypatch.delenv(key, raising=False)
        config = load_hindsight_config_from_env()
        assert config.max_retries == 3
        assert config.max_tokens == 768
        assert config.temperature == 0.3
        assert config.top_p == 0.9
        assert config.weight_safety == 0.5
        assert config.weight_helpfulness == 0.3
        assert config.weight_honesty == 0.2
        assert config.refuse_threshold == -0.7
        assert config.revise_threshold == 0.0
        assert config.use_batch_evaluation is True
        assert config.enable_caching is True

    def test_temperature_override(self, monkeypatch):
        monkeypatch.setenv(ENV_TEMPERATURE, "0.5")
        config = load_hindsight_config_from_env()
        assert config.temperature == 0.5

    def test_top_p_override(self, monkeypatch):
        monkeypatch.setenv(ENV_TOP_P, "0.85")
        config = load_hindsight_config_from_env()
        assert config.top_p == 0.85

    def test_top_p_clamped(self, monkeypatch):
        monkeypatch.setenv(ENV_TOP_P, "1.5")
        config = load_hindsight_config_from_env()
        assert config.top_p == 1.0

    def test_weight_safety_override(self, monkeypatch):
        monkeypatch.setenv(ENV_WEIGHT_SAFETY, "0.6")
        config = load_hindsight_config_from_env()
        assert config.weight_safety == 0.6

    def test_weight_helpfulness_override(self, monkeypatch):
        monkeypatch.setenv(ENV_WEIGHT_HELPFULNESS, "0.4")
        config = load_hindsight_config_from_env()
        assert config.weight_helpfulness == 0.4

    def test_weight_honesty_override(self, monkeypatch):
        monkeypatch.setenv(ENV_WEIGHT_HONESTY, "0.3")
        config = load_hindsight_config_from_env()
        assert config.weight_honesty == 0.3

    def test_refuse_threshold_override(self, monkeypatch):
        monkeypatch.setenv(ENV_REFUSE_THRESHOLD, "-0.5")
        config = load_hindsight_config_from_env()
        assert config.refuse_threshold == -0.5

    def test_revise_threshold_override(self, monkeypatch):
        monkeypatch.setenv(ENV_REVISE_THRESHOLD, "-0.2")
        config = load_hindsight_config_from_env()
        assert config.revise_threshold == -0.2

    def test_use_batch_evaluation_false(self, monkeypatch):
        monkeypatch.setenv(ENV_USE_BATCH_EVALUATION, "false")
        config = load_hindsight_config_from_env()
        assert config.use_batch_evaluation is False

    def test_enable_caching_false(self, monkeypatch):
        monkeypatch.setenv(ENV_ENABLE_CACHING, "false")
        config = load_hindsight_config_from_env()
        assert config.enable_caching is False

    def test_empty_temperature_uses_default(self, monkeypatch):
        monkeypatch.setenv(ENV_TEMPERATURE, "")
        config = load_hindsight_config_from_env()
        assert config.temperature == 0.3

    def test_refuse_threshold_clamped(self, monkeypatch):
        monkeypatch.setenv(ENV_REFUSE_THRESHOLD, "-2.0")
        config = load_hindsight_config_from_env()
        assert config.refuse_threshold == -1.0
