"""Tests for moralstack.sdk.config — GovernanceConfig."""

import pytest

from moralstack.sdk.bootstrap import _resolve_embedder_provider
from moralstack.sdk.config import GovernanceConfig


class TestGovernanceConfigDefaults:
    def test_defaults(self):
        cfg = GovernanceConfig()
        assert cfg.api_key is None
        assert cfg.model is None
        assert cfg.base_url is None
        assert cfg.constitution_dir is None
        assert cfg.domain_overlay is None
        assert cfg.observability_mode == "off"
        assert cfg.jsonl_dir is None
        assert cfg.db_path is None
        assert cfg.failure_policy == "refuse"
        assert cfg.enable_session_tracking is True
        assert cfg.max_history_tokens is None

    def test_explicit_values(self):
        cfg = GovernanceConfig(
            api_key="sk-test",
            model="gpt-4o-mini",
            domain_overlay="healthcare",
            failure_policy="refuse",
            enable_session_tracking=False,
        )
        assert cfg.api_key == "sk-test"
        assert cfg.model == "gpt-4o-mini"
        assert cfg.domain_overlay == "healthcare"
        assert cfg.failure_policy == "refuse"
        assert cfg.enable_session_tracking is False

    def test_failure_policy_values(self):
        cfg_refuse = GovernanceConfig(failure_policy="refuse")
        assert cfg_refuse.failure_policy == "refuse"

    def test_passthrough_failure_policy_is_deprecated_and_mapped_to_refuse(self):
        # Plan 1: passthrough delivery was removed; the deprecated value maps to
        # a fail-closed refusal and emits a DeprecationWarning.
        with pytest.warns(DeprecationWarning):
            cfg = GovernanceConfig(failure_policy="passthrough")
        assert cfg.failure_policy == "refuse"

    def test_observability_mode_values(self):
        for mode in ("off", "file_only", "db_only", "dual"):
            cfg = GovernanceConfig(observability_mode=mode)
            assert cfg.observability_mode == mode

    def test_max_history_tokens_optional(self):
        cfg = GovernanceConfig(max_history_tokens=2000)
        assert cfg.max_history_tokens == 2000

    def test_config_is_mutable_dataclass(self):
        cfg = GovernanceConfig()
        cfg.api_key = "sk-new"
        assert cfg.api_key == "sk-new"


class TestGovernanceConfigEmbedderProvider:
    def test_embedder_provider_defaults_to_local(self) -> None:
        assert GovernanceConfig().embedder_provider == "local"

    def test_embedder_provider_accepts_openai(self) -> None:
        assert GovernanceConfig(embedder_provider="openai").embedder_provider == "openai"

    def test_embedder_provider_rejects_invalid_value(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("MORALSTACK_EMBEDDER_PROVIDER", raising=False)
        cfg = GovernanceConfig()
        object.__setattr__(cfg, "embedder_provider", "sagemaker")
        with pytest.raises(ValueError, match="sagemaker"):
            _resolve_embedder_provider(cfg)
