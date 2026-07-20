"""Unit tests for `_resolve_generation_mode` (opt-in `generation="upstream_then_verify"`).

Default `internal`; explicit config wins over unset env; env overrides config when
set; unknown value fails closed to `internal` (never silently activates an unknown
mode); tolerant to case/whitespace.
"""

from __future__ import annotations

from moralstack.sdk.bootstrap import _resolve_generation_mode
from moralstack.sdk.config import GovernanceConfig


class TestDefault:
    def test_default_is_internal(self, monkeypatch):
        monkeypatch.delenv("MORALSTACK_GENERATION_MODE", raising=False)
        assert _resolve_generation_mode(GovernanceConfig()) == "internal"


class TestConfigWins:
    def test_config_explicit_wins_over_unset_env(self, monkeypatch):
        monkeypatch.delenv("MORALSTACK_GENERATION_MODE", raising=False)
        cfg = GovernanceConfig(generation="upstream_then_verify")
        assert _resolve_generation_mode(cfg) == "upstream_then_verify"


class TestEnvOverride:
    def test_env_sets_mode_when_config_unset(self, monkeypatch):
        monkeypatch.setenv("MORALSTACK_GENERATION_MODE", "upstream_then_verify")
        assert _resolve_generation_mode(GovernanceConfig()) == "upstream_then_verify"

    def test_env_overrides_config_when_both_set(self, monkeypatch):
        monkeypatch.setenv("MORALSTACK_GENERATION_MODE", "internal")
        cfg = GovernanceConfig(generation="upstream_then_verify")
        assert _resolve_generation_mode(cfg) == "internal"


class TestFailClosed:
    def test_unknown_env_value_fails_closed_to_internal(self, monkeypatch):
        monkeypatch.setenv("MORALSTACK_GENERATION_MODE", "yolo_mode")
        assert _resolve_generation_mode(GovernanceConfig()) == "internal"

    def test_unknown_config_value_fails_closed_to_internal(self, monkeypatch):
        monkeypatch.delenv("MORALSTACK_GENERATION_MODE", raising=False)
        cfg = GovernanceConfig()
        cfg.generation = "yolo_mode"  # type: ignore[assignment]
        assert _resolve_generation_mode(cfg) == "internal"


class TestCaseAndWhitespaceTolerant:
    def test_env_case_and_whitespace_tolerant(self, monkeypatch):
        monkeypatch.setenv("MORALSTACK_GENERATION_MODE", "  Upstream_Then_Verify  ")
        assert _resolve_generation_mode(GovernanceConfig()) == "upstream_then_verify"

    def test_config_case_and_whitespace_tolerant(self, monkeypatch):
        monkeypatch.delenv("MORALSTACK_GENERATION_MODE", raising=False)
        cfg = GovernanceConfig()
        cfg.generation = "  Upstream_Then_Verify  "  # type: ignore[assignment]
        assert _resolve_generation_mode(cfg) == "upstream_then_verify"
