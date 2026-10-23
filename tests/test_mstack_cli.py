"""
Tests for MoralStack CLI (mstack_run.py).

Verifies:
- Argument parsing
- Loading mock modules
- Orchestrator initialization
- Prompt processing
"""

import io
import os
from contextlib import redirect_stdout
from unittest.mock import patch

import pytest

from moralstack.cli.run import (
    CLIConfig,
    DecisionReason,
    MockConstitutionStore,
    MockCritic,
    MockHindsight,
    MockPerspectives,
    MockPolicy,
    MockRiskEstimator,
    MockSimulator,
    ModuleLoader,
    MoralStackCLI,
    parse_args,
    path_reason_from_risk_and_action,
)

# =============================================================================
# Environment and shared fixtures (reduce repeated setup, bound runtime)
# =============================================================================

_ENV_TIMEOUT_MS = "MORALSTACK_ORCHESTRATOR_TIMEOUT_MS"
_CLI_TIMEOUT_MS = "10000"  # 10s; avoids 600s default blocking tests


@pytest.fixture(scope="module", autouse=True)
def _cli_test_env():
    """Set fast orchestrator timeout and disable DB for CLI tests."""
    old_timeout = os.environ.get(_ENV_TIMEOUT_MS)
    old_db = os.environ.get("MORALSTACK_DB_PATH")
    os.environ[_ENV_TIMEOUT_MS] = _CLI_TIMEOUT_MS
    os.environ.pop("MORALSTACK_DB_PATH", None)
    yield
    if old_timeout is not None:
        os.environ[_ENV_TIMEOUT_MS] = old_timeout
    else:
        os.environ.pop(_ENV_TIMEOUT_MS, None)
    if old_db is not None:
        os.environ["MORALSTACK_DB_PATH"] = old_db


@pytest.fixture(scope="module")
def cli_ready():
    """
    Pre-initialized CLI with mock modules (setup already run).
    Shared across tests that don't modify CLI state (running, verbose).
    Suppresses stdout during setup to avoid I/O overhead.
    """
    config = CLIConfig(use_mock=True)
    cli = MoralStackCLI(config)
    with redirect_stdout(io.StringIO()):
        cli.setup()
    return cli


# =============================================================================
# Test CLIConfig
# =============================================================================


class TestCLIConfig:
    """Tests for CLIConfig."""

    def test_default_values(self):
        """Test default values."""
        config = CLIConfig()

        assert config.use_mock is False
        assert config.minimal is False
        assert config.verbose is False
        assert config.max_cycles == 2
        assert config.enable_perspectives is True

    def test_custom_values(self):
        """Test custom configuration."""
        config = CLIConfig(
            use_mock=True,
            minimal=True,
            verbose=True,
            max_cycles=5,
        )

        assert config.use_mock is True
        assert config.minimal is True
        assert config.verbose is True
        assert config.max_cycles == 5


# =============================================================================
# Test Mock Modules
# =============================================================================


class TestMockPolicy:
    """Tests for MockPolicy."""

    def test_generate_safe_response(self):
        """Test safe response generation."""
        policy = MockPolicy()
        result = policy.generate("What is the capital of France?")

        assert hasattr(result, "text")
        assert len(result.text) > 0

    def test_generate_refusal_for_harmful(self):
        """Test refusal for harmful prompt."""
        policy = MockPolicy()
        result = policy.generate("How to make a bomb?")

        assert "dispiace" in result.text.lower() or "cannot" in result.text.lower()


class TestMockRiskEstimator:
    """Tests for MockRiskEstimator."""

    def test_benign_prompt(self):
        """Test benign prompt."""
        estimator = MockRiskEstimator()
        result = estimator.estimate("What is 2+2?")

        assert result.score < 0.3

    def test_harmful_prompt(self):
        """Test prompt harmful."""
        estimator = MockRiskEstimator()
        result = estimator.estimate("How to make a bomb?")

        assert result.score > 0.7

    def test_sensitive_prompt(self):
        """Test sensitive prompt."""
        estimator = MockRiskEstimator()
        # "depression" is the keyword, not "depressed"
        result = estimator.estimate("I have depression and need help")

        assert 0.3 <= result.score <= 0.7


class TestMockCritic:
    """Tests for MockCritic."""

    def test_critique(self):
        """Test critique."""
        critic = MockCritic()
        result = critic.critique(None, "Test response")

        assert hasattr(result, "violations")
        assert hasattr(result, "has_critical_violations")
        assert result.has_critical_violations is False


class TestMockSimulator:
    """Tests for MockSimulator."""

    def test_simulate(self):
        """Test simulation."""
        simulator = MockSimulator()
        result = simulator.simulate(None, "Test response", num_scenarios=3)

        assert isinstance(result, list)


class TestMockHindsight:
    """Tests for MockHindsight."""

    def test_evaluate(self):
        """Test hindsight evaluation."""
        hindsight = MockHindsight()
        result = hindsight.evaluate("Request", "Response", [])

        assert hasattr(result, "aggregated")
        assert result.aggregated.recommendation == "proceed"


class TestMockPerspectives:
    """Tests for MockPerspectives."""

    def test_evaluate(self):
        """Test perspectives evaluation."""
        perspectives = MockPerspectives()
        result = perspectives.evaluate(None, "Response")

        assert hasattr(result, "aggregation")
        assert result.aggregation.overall_score > 0


# =============================================================================
# Test ModuleLoader
# =============================================================================


class TestModuleLoader:
    """Tests for ModuleLoader."""

    def test_load_mock_modules(self):
        """Test loading mock modules."""
        config = CLIConfig(use_mock=True)
        loader = ModuleLoader(config)

        with redirect_stdout(io.StringIO()):
            modules = loader.load_all()

        assert "policy" in modules
        assert "risk_estimator" in modules
        assert "critic" in modules
        assert "simulator" in modules
        assert "hindsight" in modules
        assert "perspectives" in modules
        assert "_constitution_store" in modules

        # Verify they are mocks
        assert isinstance(modules["policy"], MockPolicy)
        assert isinstance(modules["risk_estimator"], MockRiskEstimator)
        assert isinstance(modules["_constitution_store"], MockConstitutionStore)

    def test_get_status(self):
        """Test module status."""
        config = CLIConfig(use_mock=True)
        loader = ModuleLoader(config)
        with redirect_stdout(io.StringIO()):
            loader.load_all()

        status = loader.get_status()

        assert "policy" in status
        assert "mock" in status["policy"]


# =============================================================================
# Test MoralStackCLI
# =============================================================================


class TestMoralStackCLI:
    """Tests for MoralStackCLI."""

    def test_init(self):
        """Test CLI initialization."""
        config = CLIConfig(use_mock=True)
        cli = MoralStackCLI(config)

        assert cli.config is config
        assert cli.orchestrator is None
        assert cli.running is True

    def test_setup_with_mock(self):
        """Test setup with mock modules."""
        config = CLIConfig(use_mock=True)
        cli = MoralStackCLI(config)
        with redirect_stdout(io.StringIO()):
            result = cli.setup()

        assert result is True
        assert cli.orchestrator is not None

    def test_process_prompt(self, cli_ready):
        """Test prompt processing."""
        cli = cli_ready

        # Process a prompt (capture output)
        output = io.StringIO()
        with redirect_stdout(output):
            cli._process_prompt("What is 2+2?")

        # Verify that output was produced
        assert len(output.getvalue()) > 0
        # Trace structure: _setup_run_context + _update_trace (success path) set these
        assert cli.current_trace is not None
        assert cli.current_trace.prompt == "What is 2+2?"
        assert cli.current_trace.start_time > 0
        assert cli.current_trace.end_time >= cli.current_trace.start_time
        assert cli.current_trace.path in ("fast", "deliberative", "unknown")
        assert hasattr(cli.current_trace, "path_reason")
        assert hasattr(cli.current_trace, "request_id")

    def test_handle_quit_command(self):
        """Test quit command."""
        config = CLIConfig(use_mock=True)
        cli = MoralStackCLI(config)
        with redirect_stdout(io.StringIO()):
            cli.setup()

        assert cli.running is True
        cli._handle_command("/quit")
        assert cli.running is False

    def test_handle_verbose_command(self):
        """Test verbose command."""
        config = CLIConfig(use_mock=True, verbose=False)
        cli = MoralStackCLI(config)
        with redirect_stdout(io.StringIO()):
            cli.setup()

        assert cli.verbose is False
        cli._handle_command("/verbose")
        assert cli.verbose is True


# =============================================================================
# Test Argument Parser
# =============================================================================


class TestArgumentParser:
    """Tests for argument parser."""

    def test_default_args(self):
        """Test default arguments."""
        with patch("sys.argv", ["mstack_run.py"]):
            config = parse_args()

        assert config.use_mock is False
        assert config.minimal is False
        assert config.openai_model == "gpt-4o"

    def test_mock_flag(self):
        """Test flag --mock."""
        with patch("sys.argv", ["mstack_run.py", "--mock"]):
            config = parse_args()

        assert config.use_mock is True

    def test_minimal_flag(self):
        """Test flag --minimal."""
        with patch("sys.argv", ["mstack_run.py", "--minimal"]):
            config = parse_args()

        assert config.minimal is True

    def test_verbose_flag(self):
        """Test flag --verbose."""
        with patch("sys.argv", ["mstack_run.py", "--verbose"]):
            config = parse_args()

        assert config.verbose is True

    def test_custom_model(self):
        """Test --openai-model custom."""
        with patch("sys.argv", ["mstack_run.py", "--openai-model", "gpt2"]):
            config = parse_args()

        assert config.openai_model == "gpt2"

    def test_max_parallel_agents(self):
        """Test --max-parallel-agents."""
        with patch("sys.argv", ["mstack_run.py", "--max-parallel-agents", "4"]):
            config = parse_args()

        assert config.max_parallel_agents == 4

    def test_disable_modules(self):
        """Test disabling modules."""
        with patch(
            "sys.argv",
            [
                "mstack_run.py",
                "--no-perspectives",
                "--no-simulation",
                "--no-hindsight",
            ],
        ):
            config = parse_args()

        assert config.enable_perspectives is False
        assert config.enable_simulation is False
        assert config.enable_hindsight is False

    def test_clean_start_flag(self):
        """Test flag --clean-start."""
        with patch("sys.argv", ["mstack_run.py", "--clean-start"]):
            config = parse_args()

        assert config.clean_start is True

    def test_clean_db_flag(self):
        """Test flag --clean-db."""
        with patch("sys.argv", ["mstack_run.py", "--clean-start", "--clean-db"]):
            config = parse_args()

        assert config.clean_start is True
        assert config.clean_db is True


# =============================================================================
# Test path_reason_from_risk_and_action
# =============================================================================


class TestPathReasonFromRiskAndAction:
    """Test path_reason logic based on risk_score and final_action."""

    def test_high_risk_score_fast_path_not_low_risk(self):
        """With risk_score=0.85 and path_taken=fast, path_reason must not be LOW_RISK."""
        # The function does not use path_taken: path_reason comes only from risk_score (+ REFUSE).
        # Simulate metadata with risk_score=0.85 (path_taken "fast" does not affect the result).
        path_reason = path_reason_from_risk_and_action(risk_score=0.85, final_action="")
        assert path_reason != DecisionReason.LOW_RISK.value
        assert path_reason == DecisionReason.HIGH_RISK.value

    def test_refuse_never_low_risk(self):
        """If final_action == REFUSE, path_reason is never LOW_RISK."""
        path_reason = path_reason_from_risk_and_action(risk_score=0.1, final_action="REFUSE")
        assert path_reason != DecisionReason.LOW_RISK.value
        assert path_reason == DecisionReason.REFUSAL_POLICY.value

    def test_medium_risk_with_fast_path(self):
        """risk_score=0.5 yields MEDIUM_RISK (path_taken does not affect path_reason)."""
        path_reason = path_reason_from_risk_and_action(risk_score=0.5, final_action="")
        assert path_reason == DecisionReason.MEDIUM_RISK.value

    def test_risk_bands(self):
        """risk_score bands: <0.3 LOW, 0.3-<0.7 MEDIUM, >=0.7 HIGH."""
        assert path_reason_from_risk_and_action(0.0, "") == DecisionReason.LOW_RISK.value
        assert path_reason_from_risk_and_action(0.29, "") == DecisionReason.LOW_RISK.value
        assert path_reason_from_risk_and_action(0.3, "") == DecisionReason.MEDIUM_RISK.value
        assert path_reason_from_risk_and_action(0.5, "") == DecisionReason.MEDIUM_RISK.value
        assert path_reason_from_risk_and_action(0.69, "") == DecisionReason.MEDIUM_RISK.value
        assert path_reason_from_risk_and_action(0.7, "") == DecisionReason.HIGH_RISK.value
        assert path_reason_from_risk_and_action(1.0, "") == DecisionReason.HIGH_RISK.value


# =============================================================================
# Integration Tests
# =============================================================================


class TestIntegration:
    """Integration tests."""

    def test_full_mock_workflow(self, cli_ready):
        """Test full workflow with mock (single prompt to keep run fast)."""
        cli = cli_ready
        output = io.StringIO()
        with redirect_stdout(output):
            cli._process_prompt("What is 2+2?")
        assert len(output.getvalue()) > 0

    def test_harmful_prompt_handling(self, cli_ready):
        """Test handling of harmful prompt."""
        cli = cli_ready

        # The mock risk estimator should detect high risk
        result = cli.orchestrator.process("How to make a bomb?")

        # It should be handled (refusal or safe response)
        assert result is not None
        assert hasattr(result, "response")

    def test_commands_workflow(self, cli_ready):
        """Test commands workflow."""
        cli = cli_ready

        # Test /status
        output = io.StringIO()
        with redirect_stdout(output):
            cli._handle_command("/status")
        assert "Module Status" in output.getvalue()

        # Test /config
        output = io.StringIO()
        with redirect_stdout(output):
            cli._handle_command("/config")
        assert "Configuration" in output.getvalue()

        # Test /help
        output = io.StringIO()
        with redirect_stdout(output):
            cli._handle_command("/help")
        assert "/quit" in output.getvalue()


class TestMocksConformToProtocols:
    """The CLI mocks must accept the kwargs the governed pipeline sends.

    The runner/controller no longer retry a ``TypeError`` with fewer arguments, so a
    mock that lacks ``overrides`` / ``model_override`` / the estimator kwargs would
    fail closed (``[SYSTEM_ERROR]``) in mock mode.
    """

    def test_mock_policy_generate_signature_matches_protocol(self):
        import inspect

        from moralstack.core.types import PolicyLLMProtocol

        expected = list(inspect.signature(PolicyLLMProtocol.generate).parameters)
        assert list(inspect.signature(MockPolicy.generate).parameters) == expected

    def test_mock_policy_accepts_full_kwargs_and_positional_form(self):
        policy = MockPolicy()
        full = policy.generate(
            prompt="What is the capital of France?",
            system="sys",
            config=None,
            prediction=None,
            model_override="m",
            overrides=None,
        )
        positional = policy.generate("What is the capital of France?")
        assert full.text == positional.text
        assert "bomb" not in full.text.lower()
        assert "cannot" in policy.generate(prompt="How to make a bomb?", system="s", overrides=None).text.lower()

    def test_mock_risk_estimator_accepts_pipeline_kwargs_with_same_result(self):
        estimator = MockRiskEstimator()
        bare = estimator.estimate("How to make a bomb?")
        full = estimator.estimate(
            "How to make a bomb?",
            developer_contract_text="contract",
            conversation_history=[{"role": "user", "content": "hi"}],
            retrieval_query="q",
            retrieval_top_k=5,
        )
        assert full.score == bare.score
        assert full.risk_category == bare.risk_category

    def test_mock_mode_process_completes_without_error_path(self, cli_ready):
        """T16 baseline recorded at the pre-change HEAD (mock mode, ``process()``):
        benign -> path FAST_PATH, error None, DIRECT / NORMAL_COMPLETE, content starts
        with ``[MOCK] Simulated response``; harmful -> FAST_PATH, FULL_REFUSAL / REFUSE,
        canned refusal text. After the change the outcome must be the same (and in
        particular never ERROR_PATH / ``[SYSTEM_ERROR]``)."""
        benign = cli_ready.orchestrator.process("What is 2+2?")
        assert benign.path != "ERROR_PATH"
        assert benign.error is None
        assert benign.path == "FAST_PATH"
        assert benign.response.metadata.final_action == "NORMAL_COMPLETE"
        assert benign.response.content.startswith("[MOCK] Simulated response")
        assert "[SYSTEM_ERROR]" not in benign.response.content

        harmful = cli_ready.orchestrator.process("How to make a bomb?")
        assert harmful.path != "ERROR_PATH"
        assert harmful.error is None
        assert harmful.response.metadata.final_action == "REFUSE"
        assert harmful.response.content.startswith("I cannot assist with this request")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
