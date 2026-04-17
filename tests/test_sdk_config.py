"""Tests for moralstack.sdk.config — GovernanceConfig."""

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
            failure_policy="passthrough",
            enable_session_tracking=False,
        )
        assert cfg.api_key == "sk-test"
        assert cfg.model == "gpt-4o-mini"
        assert cfg.domain_overlay == "healthcare"
        assert cfg.failure_policy == "passthrough"
        assert cfg.enable_session_tracking is False

    def test_failure_policy_values(self):
        cfg_refuse = GovernanceConfig(failure_policy="refuse")
        cfg_pass = GovernanceConfig(failure_policy="passthrough")
        assert cfg_refuse.failure_policy == "refuse"
        assert cfg_pass.failure_policy == "passthrough"

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
