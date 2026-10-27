"""
Unit tests for risk estimator config loading from environment.

Verifies that load_risk_estimator_config_from_env() and helpers use
env when set and non-empty, and fall back to defaults otherwise.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from types import SimpleNamespace

import moralstack.models.risk.estimator as risk_estimator_module
from moralstack.models.risk.config_loader import (
    ENV_INTENT_MODEL,
    ENV_MAX_RETRIES,
    ENV_MODEL,
    ENV_OPERATIONAL_MODEL,
    ENV_SIGNALS_MODEL,
    ENV_TOP_P,
    get_risk_env_float,
    get_risk_env_int,
    get_risk_env_str,
    load_risk_estimator_config_from_env,
)
from moralstack.models.risk.estimator import LLMBasedRiskEstimator
from moralstack.utils.env_helpers import get_env_bool

# Generic bool-parsing key for get_env_bool (no risk-estimator var is boolean any more).
_BOOL_KEY = "MORALSTACK_TEST_BOOL_FLAG"

# Env vars of the retired single-estimator and no-LLM fallback configuration: nothing reads them any more.
_REMOVED_ENV_VARS = {
    "MORALSTACK_RISK_LOW_THRESHOLD": "0.9",
    "MORALSTACK_RISK_MEDIUM_THRESHOLD": "0.95",
    "MORALSTACK_RISK_REQUIRE_DELIBERATION_ON_FALLBACK": "false",
    "MORALSTACK_RISK_CATEGORIZE_BENIGN_THRESHOLD": "0.9",
    "MORALSTACK_RISK_CATEGORIZE_SENSITIVE_THRESHOLD": "0.9",
    "MORALSTACK_RISK_CATEGORIZE_CLEARLY_HARMFUL_THRESHOLD": "0.9",
    "MORALSTACK_RISK_FALLBACK_SCORE": "0.99",
    "MORALSTACK_RISK_FALLBACK_CONFIDENCE": "0.99",
}

_BENIGN_MINI_JSON = json.dumps(
    {
        "request_type": "factual_query",
        "intent_to_harm": "no",
        "requested_instructions": "no",
        "harm_type": "none",
        "q1_confidential": "no",
        "domain_sensitivity": "LOW",
        "risk_score": 0.1,
        "confidence": 0.95,
        "operational_risk": "NONE",
        "risk_policy_action": "ALLOW",
        "rationale": "benign",
    }
)


class _BenignPolicy:
    """Answers every mini-estimator call with the same benign JSON."""

    model = "m"
    tracker = None

    def generate(self, *, prompt, system, config=None):
        return SimpleNamespace(text=_BENIGN_MINI_JSON)


def _clear_ambient_risk_env(monkeypatch):
    """Isolate from the shell: clear every other risk knob and OPENAI_MODEL.

    All three model slots are then None (inherit the policy model), so the estimator can never construct a real
    OpenAIPolicy. The retired vars under test (_REMOVED_ENV_VARS) are left alone on purpose.
    """
    for key in list(os.environ):
        if key.startswith("MORALSTACK_RISK_") and key not in _REMOVED_ENV_VARS:
            monkeypatch.delenv(key, raising=False)
    monkeypatch.delenv("OPENAI_MODEL", raising=False)


def _estimate_hello(monkeypatch):
    _clear_ambient_risk_env(monkeypatch)
    monkeypatch.setattr(risk_estimator_module, "_obs_route_batch", lambda envs: None)
    monkeypatch.setattr(risk_estimator_module, "_obs_route", lambda env: None)
    config = load_risk_estimator_config_from_env()
    policy = _BenignPolicy()
    policy.model = "m"  # slots are None (inherit policy.model): no pooled policy is needed
    return LLMBasedRiskEstimator(policy=policy, config=config).estimate("hello")


class TestGetRiskEnvFloat:
    """Tests for get_risk_env_float."""

    def test_missing_key_returns_default(self, monkeypatch):
        monkeypatch.delenv(ENV_TOP_P, raising=False)
        assert get_risk_env_float(ENV_TOP_P, 0.3) == 0.3

    def test_empty_key_returns_default(self, monkeypatch):
        monkeypatch.setenv(ENV_TOP_P, "")
        assert get_risk_env_float(ENV_TOP_P, 0.3) == 0.3

    def test_whitespace_only_returns_default(self, monkeypatch):
        monkeypatch.setenv(ENV_TOP_P, "  ")
        assert get_risk_env_float(ENV_TOP_P, 0.3) == 0.3

    def test_valid_value_used(self, monkeypatch):
        monkeypatch.setenv(ENV_TOP_P, "0.4")
        assert get_risk_env_float(ENV_TOP_P, 0.3) == 0.4

    def test_clamp_min_max(self, monkeypatch):
        monkeypatch.setenv(ENV_TOP_P, "-0.1")
        assert get_risk_env_float(ENV_TOP_P, 0.3, 0.0, 1.0) == 0.0
        monkeypatch.setenv(ENV_TOP_P, "1.5")
        assert get_risk_env_float(ENV_TOP_P, 0.3, 0.0, 1.0) == 1.0

    def test_invalid_value_returns_default(self, monkeypatch):
        monkeypatch.setenv(ENV_TOP_P, "not_a_number")
        assert get_risk_env_float(ENV_TOP_P, 0.3) == 0.3


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


class TestGetEnvBool:
    """Tests for env_helpers.get_env_bool (the only test of bool parsing)."""

    def test_missing_key_returns_default(self, monkeypatch):
        monkeypatch.delenv(_BOOL_KEY, raising=False)
        assert get_env_bool(_BOOL_KEY, True) is True
        assert get_env_bool(_BOOL_KEY, False) is False

    def test_empty_key_returns_default(self, monkeypatch):
        monkeypatch.setenv(_BOOL_KEY, "")
        assert get_env_bool(_BOOL_KEY, True) is True

    def test_true_values(self, monkeypatch):
        for val in ("1", "true", "TRUE", "yes", "Yes"):
            monkeypatch.setenv(_BOOL_KEY, val)
            assert get_env_bool(_BOOL_KEY, False) is True

    def test_false_values(self, monkeypatch):
        for val in ("0", "false", "FALSE", "no", "No"):
            monkeypatch.setenv(_BOOL_KEY, val)
            assert get_env_bool(_BOOL_KEY, True) is False


class TestLoadRiskEstimatorConfigFromEnv:
    """Tests for load_risk_estimator_config_from_env."""

    def test_empty_env_returns_defaults(self, monkeypatch):
        for key in (
            ENV_MAX_RETRIES,
            "MORALSTACK_RISK_MAX_TOKENS",
            "MORALSTACK_RISK_TEMPERATURE",
        ):
            monkeypatch.delenv(key, raising=False)
        config = load_risk_estimator_config_from_env()
        assert config.max_retries == 2
        assert config.max_tokens == 512
        assert config.temperature == 0.1

    def test_removed_single_estimator_env_vars_are_ignored(self, monkeypatch):
        """The retired routing/categorize/deliberation vars are read by no code: config and estimates are unchanged."""
        _clear_ambient_risk_env(monkeypatch)
        for key in _REMOVED_ENV_VARS:
            monkeypatch.delenv(key, raising=False)
        baseline_config = load_risk_estimator_config_from_env()
        baseline = _estimate_hello(monkeypatch)

        for key, value in _REMOVED_ENV_VARS.items():
            monkeypatch.setenv(key, value)

        assert load_risk_estimator_config_from_env() == baseline_config
        with_vars = _estimate_hello(monkeypatch)
        assert (with_vars.score, with_vars.risk_category) == (baseline.score, baseline.risk_category)

    def test_parallel_mini_slots_are_none_when_unset(self, monkeypatch):
        """Unset mini slots stay None (inherit the main risk policy model); no built-in default is applied."""
        for key in (
            ENV_MODEL,
            ENV_INTENT_MODEL,
            ENV_SIGNALS_MODEL,
            ENV_OPERATIONAL_MODEL,
            "OPENAI_MODEL",
        ):
            monkeypatch.delenv(key, raising=False)
        cfg = load_risk_estimator_config_from_env()
        assert cfg.intent_model is None
        assert cfg.signals_model is None
        assert cfg.operational_model is None

    def test_parallel_mini_slots_ignore_risk_model_and_openai_model(self, monkeypatch):
        """MORALSTACK_RISK_MODEL and OPENAI_MODEL are no longer read by the loader (design A)."""
        monkeypatch.delenv(ENV_INTENT_MODEL, raising=False)
        monkeypatch.delenv(ENV_SIGNALS_MODEL, raising=False)
        monkeypatch.delenv(ENV_OPERATIONAL_MODEL, raising=False)
        monkeypatch.setenv(ENV_MODEL, "risk-dedicated")
        monkeypatch.setenv("OPENAI_MODEL", "openai-primary")
        cfg = load_risk_estimator_config_from_env()
        assert cfg.intent_model is None
        assert cfg.signals_model is None
        assert cfg.operational_model is None

    def test_parallel_mini_slots_blank_values_count_as_unset(self, monkeypatch):
        monkeypatch.setenv(ENV_INTENT_MODEL, "")
        monkeypatch.setenv(ENV_SIGNALS_MODEL, "   ")
        monkeypatch.delenv(ENV_OPERATIONAL_MODEL, raising=False)
        cfg = load_risk_estimator_config_from_env()
        assert cfg.intent_model is None
        assert cfg.signals_model is None
        assert cfg.operational_model is None

    def test_parallel_mini_per_slot_override(self, monkeypatch):
        monkeypatch.setenv(ENV_MODEL, "gpt-4o-mini")
        monkeypatch.setenv(ENV_INTENT_MODEL, "gpt-4o")
        monkeypatch.delenv(ENV_SIGNALS_MODEL, raising=False)
        monkeypatch.setenv(ENV_OPERATIONAL_MODEL, "custom-op")
        cfg = load_risk_estimator_config_from_env()
        assert cfg.intent_model == "gpt-4o"
        assert cfg.signals_model is None
        assert cfg.operational_model == "custom-op"


# Keys no code path reads any more: the single shipped template must not list them, not even commented.
_DEAD_TEMPLATE_KEYS = (
    "MORALSTACK_RISK_LOW_THRESHOLD",
    "MORALSTACK_RISK_MEDIUM_THRESHOLD",
    "MORALSTACK_RISK_CATEGORIZE_BENIGN_THRESHOLD",
    "MORALSTACK_RISK_CATEGORIZE_SENSITIVE_THRESHOLD",
    "MORALSTACK_RISK_CATEGORIZE_CLEARLY_HARMFUL_THRESHOLD",
    "MORALSTACK_RISK_REQUIRE_DELIBERATION_ON_FALLBACK",
    "MORALSTACK_DOMAIN_PREFILTER_MODEL",
    "MORALSTACK_OPENAI_COMPATIBLE_MAX_INFLIGHT",
    "MORALSTACK_OPENAI_COMPATIBLE_RETRY_AFTER_SECONDS",
    "MORALSTACK_CRITIC_INCLUDE_EXAMPLES",
    "MORALSTACK_DECISION_TRACE_PATH",
    "MORALSTACK_ORCHESTRATOR_ENABLE_DYNAMIC_PARALLEL_SCHEDULER",
    "MORALSTACK_SIMULATOR_USE_SEEDED_GENERATION",
    "MORALSTACK_ORCHESTRATOR_PARALLEL_CRITIC_WITH_MODULES",
    "MORALSTACK_ORCHESTRATOR_MAX_CRITICAL_VIOLATIONS",
    "MORALSTACK_ORCHESTRATOR_EARLY_EXIT_HINDSIGHT_THRESHOLD",
    "MORALSTACK_ORCHESTRATOR_SAFE_RESPONSE_ON_ERROR",
)


def test_env_templates_have_no_removed_risk_vars():
    """The shipped template no longer advertises removed or dead env vars (template only)."""
    root = Path(__file__).resolve().parent.parent
    name = ".env.template"
    text = (root / name).read_text(encoding="utf-8")
    assert "MORALSTACK_RISK_FALLBACK_SCORE" not in text, name
    assert "MORALSTACK_RISK_FALLBACK_CONFIDENCE" not in text, name
    # Whole-name match: MORALSTACK_ORCHESTRATOR_RISK_LOW_THRESHOLD is live and must not count as a hit.
    listed = set(re.findall(r"[A-Z][A-Z0-9_]+", text))
    dead_listed = sorted(set(_DEAD_TEMPLATE_KEYS) & listed)
    assert not dead_listed, f"{name} lists dead keys: {dead_listed}"
