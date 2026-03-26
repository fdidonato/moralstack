"""
Unit tests for risk estimator config loading from environment.

Verifies that load_risk_estimator_config_from_env() and helpers use
env when set and non-empty, and fall back to defaults otherwise.
"""

from __future__ import annotations

from moralstack.models.risk.config_loader import (
    ENV_LOW_THRESHOLD,
    ENV_MAX_RETRIES,
    ENV_MEDIUM_THRESHOLD,
    ENV_MODEL,
    ENV_REQUIRE_DELIBERATION_ON_FALLBACK,
    get_risk_env_bool,
    get_risk_env_float,
    get_risk_env_int,
    get_risk_env_str,
    load_risk_estimator_config_from_env,
)


class TestGetRiskEnvFloat:
    """Tests for get_risk_env_float."""

    def test_missing_key_returns_default(self, monkeypatch):
        monkeypatch.delenv(ENV_LOW_THRESHOLD, raising=False)
        assert get_risk_env_float(ENV_LOW_THRESHOLD, 0.3) == 0.3

    def test_empty_key_returns_default(self, monkeypatch):
        monkeypatch.setenv(ENV_LOW_THRESHOLD, "")
        assert get_risk_env_float(ENV_LOW_THRESHOLD, 0.3) == 0.3

    def test_whitespace_only_returns_default(self, monkeypatch):
        monkeypatch.setenv(ENV_LOW_THRESHOLD, "  ")
        assert get_risk_env_float(ENV_LOW_THRESHOLD, 0.3) == 0.3

    def test_valid_value_used(self, monkeypatch):
        monkeypatch.setenv(ENV_LOW_THRESHOLD, "0.4")
        assert get_risk_env_float(ENV_LOW_THRESHOLD, 0.3) == 0.4

    def test_clamp_min_max(self, monkeypatch):
        monkeypatch.setenv(ENV_LOW_THRESHOLD, "-0.1")
        assert get_risk_env_float(ENV_LOW_THRESHOLD, 0.3, 0.0, 1.0) == 0.0
        monkeypatch.setenv(ENV_LOW_THRESHOLD, "1.5")
        assert get_risk_env_float(ENV_LOW_THRESHOLD, 0.3, 0.0, 1.0) == 1.0

    def test_invalid_value_returns_default(self, monkeypatch):
        monkeypatch.setenv(ENV_LOW_THRESHOLD, "not_a_number")
        assert get_risk_env_float(ENV_LOW_THRESHOLD, 0.3) == 0.3


class TestGetRiskEnvInt:
    """Tests for get_risk_env_int."""

    def test_missing_key_returns_default(self, monkeypatch):
        monkeypatch.delenv(ENV_MAX_RETRIES, raising=False)
        assert get_risk_env_int(ENV_MAX_RETRIES, 2) == 2

    def test_empty_key_returns_default(self, monkeypatch):
        monkeypatch.setenv(ENV_MAX_RETRIES, "")
        assert get_risk_env_int(ENV_MAX_RETRIES, 2) == 2

    def test_valid_value_used(self, monkeypatch):
        monkeypatch.setenv(ENV_MAX_RETRIES, "5")
        assert get_risk_env_int(ENV_MAX_RETRIES, 2) == 5

    def test_min_enforced(self, monkeypatch):
        monkeypatch.setenv(ENV_MAX_RETRIES, "0")
        assert get_risk_env_int(ENV_MAX_RETRIES, 2, min_val=1) == 1

    def test_invalid_value_returns_default(self, monkeypatch):
        monkeypatch.setenv(ENV_MAX_RETRIES, "x")
        assert get_risk_env_int(ENV_MAX_RETRIES, 2) == 2


class TestGetRiskEnvStr:
    """Tests for get_risk_env_str."""

    def test_missing_key_returns_default(self, monkeypatch):
        monkeypatch.delenv(ENV_MODEL, raising=False)
        assert get_risk_env_str(ENV_MODEL, "gpt-4o") == "gpt-4o"

    def test_empty_key_returns_default(self, monkeypatch):
        monkeypatch.setenv(ENV_MODEL, "")
        assert get_risk_env_str(ENV_MODEL, "gpt-4o") == "gpt-4o"

    def test_whitespace_only_returns_default(self, monkeypatch):
        monkeypatch.setenv(ENV_MODEL, "  ")
        assert get_risk_env_str(ENV_MODEL, "gpt-4o") == "gpt-4o"

    def test_valid_value_used(self, monkeypatch):
        monkeypatch.setenv(ENV_MODEL, "gpt-4o-mini")
        assert get_risk_env_str(ENV_MODEL, "gpt-4o") == "gpt-4o-mini"


class TestGetRiskEnvBool:
    """Tests for get_risk_env_bool."""

    def test_missing_key_returns_default(self, monkeypatch):
        monkeypatch.delenv(ENV_REQUIRE_DELIBERATION_ON_FALLBACK, raising=False)
        assert get_risk_env_bool(ENV_REQUIRE_DELIBERATION_ON_FALLBACK, True) is True
        assert get_risk_env_bool(ENV_REQUIRE_DELIBERATION_ON_FALLBACK, False) is False

    def test_empty_key_returns_default(self, monkeypatch):
        monkeypatch.setenv(ENV_REQUIRE_DELIBERATION_ON_FALLBACK, "")
        assert get_risk_env_bool(ENV_REQUIRE_DELIBERATION_ON_FALLBACK, True) is True

    def test_true_values(self, monkeypatch):
        for val in ("1", "true", "TRUE", "yes", "Yes"):
            monkeypatch.setenv(ENV_REQUIRE_DELIBERATION_ON_FALLBACK, val)
            assert get_risk_env_bool(ENV_REQUIRE_DELIBERATION_ON_FALLBACK, False) is True

    def test_false_values(self, monkeypatch):
        for val in ("0", "false", "FALSE", "no", "No"):
            monkeypatch.setenv(ENV_REQUIRE_DELIBERATION_ON_FALLBACK, val)
            assert get_risk_env_bool(ENV_REQUIRE_DELIBERATION_ON_FALLBACK, True) is False


class TestLoadRiskEstimatorConfigFromEnv:
    """Tests for load_risk_estimator_config_from_env."""

    def test_empty_env_returns_defaults(self, monkeypatch):
        for key in (
            ENV_LOW_THRESHOLD,
            ENV_MEDIUM_THRESHOLD,
            ENV_MAX_RETRIES,
            "MORALSTACK_RISK_MAX_TOKENS",
            "MORALSTACK_RISK_TEMPERATURE",
            "MORALSTACK_RISK_FALLBACK_SCORE",
            "MORALSTACK_RISK_FALLBACK_CONFIDENCE",
            ENV_REQUIRE_DELIBERATION_ON_FALLBACK,
        ):
            monkeypatch.delenv(key, raising=False)
        config = load_risk_estimator_config_from_env()
        assert config.low_threshold == 0.3
        assert config.medium_threshold == 0.7
        assert config.max_retries == 2
        assert config.max_tokens == 512
        assert config.temperature == 0.1
        assert config.fallback_risk_score == 0.5
        assert config.fallback_confidence == 0.3
        assert config.require_deliberation_on_fallback is True

    def test_low_threshold_override(self, monkeypatch):
        monkeypatch.delenv("MORALSTACK_RISK_MEDIUM_THRESHOLD", raising=False)
        monkeypatch.setenv(ENV_LOW_THRESHOLD, "0.4")
        config = load_risk_estimator_config_from_env()
        assert config.low_threshold == 0.4
        assert config.medium_threshold == 0.7

    def test_empty_low_threshold_uses_default(self, monkeypatch):
        monkeypatch.setenv(ENV_LOW_THRESHOLD, "")
        config = load_risk_estimator_config_from_env()
        assert config.low_threshold == 0.3

    def test_medium_threshold_override(self, monkeypatch):
        monkeypatch.setenv(ENV_MEDIUM_THRESHOLD, "0.8")
        config = load_risk_estimator_config_from_env()
        assert config.medium_threshold == 0.8

    def test_require_deliberation_false(self, monkeypatch):
        monkeypatch.setenv(ENV_REQUIRE_DELIBERATION_ON_FALLBACK, "false")
        config = load_risk_estimator_config_from_env()
        assert config.require_deliberation_on_fallback is False
