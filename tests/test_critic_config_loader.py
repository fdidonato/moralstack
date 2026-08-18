"""
Unit tests for Critic config loading from environment.

Verifies that load_critic_config_from_env() and helpers use
env when set and non-empty, and fall back to defaults otherwise.
"""

from __future__ import annotations

from moralstack.runtime.modules.critic_config_loader import (
    ENV_INCLUDE_EXAMPLES,
    ENV_MAX_RETRIES,
    ENV_MAX_RULE_LEN,
    ENV_MAX_TOKENS,
    ENV_MODEL,
    ENV_TEMPERATURE,
    ENV_TOP_K_PRINCIPLES,
    ENV_TOP_P,
    get_critic_env_bool,
    get_critic_env_float,
    get_critic_env_int,
    get_critic_env_str,
    load_critic_config_from_env,
)


class TestGetCriticEnvFloat:
    """Tests for get_critic_env_float."""

    def test_missing_key_returns_default(self, monkeypatch):
        monkeypatch.delenv(ENV_TEMPERATURE, raising=False)
        assert get_critic_env_float(ENV_TEMPERATURE, 0.1) == 0.1

    def test_empty_key_returns_default(self, monkeypatch):
        monkeypatch.setenv(ENV_TEMPERATURE, "")
        assert get_critic_env_float(ENV_TEMPERATURE, 0.1) == 0.1

    def test_whitespace_only_returns_default(self, monkeypatch):
        monkeypatch.setenv(ENV_TEMPERATURE, "  ")
        assert get_critic_env_float(ENV_TEMPERATURE, 0.1) == 0.1

    def test_valid_value_used(self, monkeypatch):
        monkeypatch.setenv(ENV_TEMPERATURE, "0.2")
        assert get_critic_env_float(ENV_TEMPERATURE, 0.1) == 0.2

    def test_clamp_min_max(self, monkeypatch):
        monkeypatch.setenv(ENV_TEMPERATURE, "-0.1")
        assert get_critic_env_float(ENV_TEMPERATURE, 0.1, 0.0, 2.0) == 0.0
        monkeypatch.setenv(ENV_TEMPERATURE, "2.5")
        assert get_critic_env_float(ENV_TEMPERATURE, 0.1, 0.0, 2.0) == 2.0

    def test_invalid_value_returns_default(self, monkeypatch):
        monkeypatch.setenv(ENV_TEMPERATURE, "not_a_number")
        assert get_critic_env_float(ENV_TEMPERATURE, 0.1) == 0.1


class TestGetCriticEnvInt:
    """Tests for get_critic_env_int."""

    def test_missing_key_returns_default(self, monkeypatch):
        monkeypatch.delenv(ENV_MAX_RETRIES, raising=False)
        assert get_critic_env_int(ENV_MAX_RETRIES, 2) == 2

    def test_empty_key_returns_default(self, monkeypatch):
        monkeypatch.setenv(ENV_MAX_RETRIES, "")
        assert get_critic_env_int(ENV_MAX_RETRIES, 2) == 2

    def test_valid_value_used(self, monkeypatch):
        monkeypatch.setenv(ENV_MAX_RETRIES, "5")
        assert get_critic_env_int(ENV_MAX_RETRIES, 2) == 5

    def test_min_enforced(self, monkeypatch):
        monkeypatch.setenv(ENV_MAX_RETRIES, "0")
        assert get_critic_env_int(ENV_MAX_RETRIES, 2, min_val=1) == 1

    def test_invalid_value_returns_default(self, monkeypatch):
        monkeypatch.setenv(ENV_MAX_RETRIES, "x")
        assert get_critic_env_int(ENV_MAX_RETRIES, 2) == 2


class TestGetCriticEnvStr:
    """Tests for get_critic_env_str."""

    def test_missing_key_returns_default(self, monkeypatch):
        monkeypatch.delenv(ENV_MODEL, raising=False)
        assert get_critic_env_str(ENV_MODEL, "gpt-4o") == "gpt-4o"

    def test_empty_key_returns_default(self, monkeypatch):
        monkeypatch.setenv(ENV_MODEL, "")
        assert get_critic_env_str(ENV_MODEL, "gpt-4o") == "gpt-4o"

    def test_whitespace_only_returns_default(self, monkeypatch):
        monkeypatch.setenv(ENV_MODEL, "  ")
        assert get_critic_env_str(ENV_MODEL, "gpt-4o") == "gpt-4o"

    def test_valid_value_used(self, monkeypatch):
        monkeypatch.setenv(ENV_MODEL, "gpt-4o-mini")
        assert get_critic_env_str(ENV_MODEL, "gpt-4o") == "gpt-4o-mini"


class TestGetCriticEnvBool:
    """Tests for get_critic_env_bool."""

    def test_missing_key_returns_default(self, monkeypatch):
        monkeypatch.delenv(ENV_INCLUDE_EXAMPLES, raising=False)
        assert get_critic_env_bool(ENV_INCLUDE_EXAMPLES, False) is False
        assert get_critic_env_bool(ENV_INCLUDE_EXAMPLES, True) is True

    def test_empty_key_returns_default(self, monkeypatch):
        monkeypatch.setenv(ENV_INCLUDE_EXAMPLES, "")
        assert get_critic_env_bool(ENV_INCLUDE_EXAMPLES, False) is False

    def test_true_values(self, monkeypatch):
        for val in ("1", "true", "TRUE", "yes", "Yes"):
            monkeypatch.setenv(ENV_INCLUDE_EXAMPLES, val)
            assert get_critic_env_bool(ENV_INCLUDE_EXAMPLES, False) is True

    def test_false_values(self, monkeypatch):
        for val in ("0", "false", "FALSE", "no", "No"):
            monkeypatch.setenv(ENV_INCLUDE_EXAMPLES, val)
            assert get_critic_env_bool(ENV_INCLUDE_EXAMPLES, True) is False


class TestLoadCriticConfigFromEnv:
    """Tests for load_critic_config_from_env."""

    def test_empty_env_returns_defaults(self, monkeypatch):
        for key in (
            ENV_MAX_RETRIES,
            ENV_MAX_TOKENS,
            ENV_TEMPERATURE,
            ENV_TOP_P,
            ENV_TOP_K_PRINCIPLES,
            ENV_INCLUDE_EXAMPLES,
            ENV_MAX_RULE_LEN,
        ):
            monkeypatch.delenv(key, raising=False)
        config = load_critic_config_from_env()
        assert config.max_retries == 2
        assert config.max_tokens == 384
        assert config.temperature == 0.1
        assert config.top_p == 0.9
        assert config.top_k_principles == 20
        assert config.include_examples is False
        assert config.max_rule_len == 512

    def test_max_retries_override(self, monkeypatch):
        monkeypatch.delenv("MORALSTACK_CRITIC_MAX_TOKENS", raising=False)
        monkeypatch.setenv(ENV_MAX_RETRIES, "5")
        config = load_critic_config_from_env()
        assert config.max_retries == 5
        assert config.max_tokens == 384

    def test_empty_max_retries_uses_default(self, monkeypatch):
        monkeypatch.setenv(ENV_MAX_RETRIES, "")
        config = load_critic_config_from_env()
        assert config.max_retries == 2

    def test_temperature_override(self, monkeypatch):
        monkeypatch.setenv(ENV_TEMPERATURE, "0.3")
        config = load_critic_config_from_env()
        assert config.temperature == 0.3

    def test_top_p_override(self, monkeypatch):
        monkeypatch.setenv(ENV_TOP_P, "0.8")
        config = load_critic_config_from_env()
        assert config.top_p == 0.8

    def test_top_p_clamped(self, monkeypatch):
        monkeypatch.setenv(ENV_TOP_P, "1.5")
        config = load_critic_config_from_env()
        assert config.top_p == 1.0

    def test_top_k_principles_override(self, monkeypatch):
        monkeypatch.setenv(ENV_TOP_K_PRINCIPLES, "10")
        config = load_critic_config_from_env()
        assert config.top_k_principles == 10

    def test_include_examples_true(self, monkeypatch):
        monkeypatch.setenv(ENV_INCLUDE_EXAMPLES, "true")
        config = load_critic_config_from_env()
        assert config.include_examples is True

    def test_include_examples_false(self, monkeypatch):
        monkeypatch.setenv(ENV_INCLUDE_EXAMPLES, "false")
        config = load_critic_config_from_env()
        assert config.include_examples is False

    def test_max_tokens_override(self, monkeypatch):
        monkeypatch.setenv(ENV_MAX_TOKENS, "512")
        config = load_critic_config_from_env()
        assert config.max_tokens == 512

    def test_max_rule_len_override(self, monkeypatch):
        monkeypatch.setenv(ENV_MAX_RULE_LEN, "512")
        config = load_critic_config_from_env()
        assert config.max_rule_len == 512

    def test_max_rule_len_min_enforced(self, monkeypatch):
        monkeypatch.setenv(ENV_MAX_RULE_LEN, "0")
        config = load_critic_config_from_env()
        assert config.max_rule_len == 1

    def test_max_rule_len_invalid_uses_default(self, monkeypatch):
        monkeypatch.setenv(ENV_MAX_RULE_LEN, "wide")
        config = load_critic_config_from_env()
        assert config.max_rule_len == 512
