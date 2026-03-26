"""
Test per MoralStack CLI (mstack_run.py).

Verifica:
- Parsing argomenti
- Caricamento moduli mock
- Inizializzazione orchestrator
- Processamento prompt
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
    """Test per CLIConfig."""

    def test_default_values(self):
        """Test valori di default."""
        config = CLIConfig()

        assert config.use_mock is False
        assert config.minimal is False
        assert config.verbose is False
        assert config.max_cycles == 2
        assert config.enable_perspectives is True

    def test_custom_values(self):
        """Test configurazione custom."""
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
    """Test per MockPolicy."""

    def test_generate_safe_response(self):
        """Test generazione risposta safe."""
        policy = MockPolicy()
        result = policy.generate("What is the capital of France?")

        assert hasattr(result, "text")
        assert len(result.text) > 0

    def test_generate_refusal_for_harmful(self):
        """Test rifiuto per prompt harmful."""
        policy = MockPolicy()
        result = policy.generate("How to make a bomb?")

        assert "dispiace" in result.text.lower() or "cannot" in result.text.lower()


class TestMockRiskEstimator:
    """Test per MockRiskEstimator."""

    def test_benign_prompt(self):
        """Test prompt benigno."""
        estimator = MockRiskEstimator()
        result = estimator.estimate("What is 2+2?")

        assert result.score < 0.3

    def test_harmful_prompt(self):
        """Test prompt harmful."""
        estimator = MockRiskEstimator()
        result = estimator.estimate("How to make a bomb?")

        assert result.score > 0.7

    def test_sensitive_prompt(self):
        """Test prompt sensibile."""
        estimator = MockRiskEstimator()
        # "depression" è la keyword, non "depressed"
        result = estimator.estimate("I have depression and need help")

        assert 0.3 <= result.score <= 0.7


class TestMockCritic:
    """Test per MockCritic."""

    def test_critique(self):
        """Test critica."""
        critic = MockCritic()
        result = critic.critique(None, "Test response")

        assert hasattr(result, "violations")
        assert hasattr(result, "has_critical_violations")
        assert result.has_critical_violations is False


class TestMockSimulator:
    """Test per MockSimulator."""

    def test_simulate(self):
        """Test simulazione."""
        simulator = MockSimulator()
        result = simulator.simulate(None, "Test response", num_scenarios=3)

        assert isinstance(result, list)


class TestMockHindsight:
    """Test per MockHindsight."""

    def test_evaluate(self):
        """Test valutazione hindsight."""
        hindsight = MockHindsight()
        result = hindsight.evaluate("Request", "Response", [])

        assert hasattr(result, "aggregated")
        assert result.aggregated.recommendation == "proceed"


class TestMockPerspectives:
    """Test per MockPerspectives."""

    def test_evaluate(self):
        """Test valutazione prospettive."""
        perspectives = MockPerspectives()
        result = perspectives.evaluate(None, "Response")

        assert hasattr(result, "aggregation")
        assert result.aggregation.overall_score > 0


# =============================================================================
# Test ModuleLoader
# =============================================================================


class TestModuleLoader:
    """Test per ModuleLoader."""

    def test_load_mock_modules(self):
        """Test caricamento moduli mock."""
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

        # Verifica che siano mock
        assert isinstance(modules["policy"], MockPolicy)
        assert isinstance(modules["risk_estimator"], MockRiskEstimator)
        assert isinstance(modules["_constitution_store"], MockConstitutionStore)

    def test_get_status(self):
        """Test status dei moduli."""
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
    """Test per MoralStackCLI."""

    def test_init(self):
        """Test inizializzazione CLI."""
        config = CLIConfig(use_mock=True)
        cli = MoralStackCLI(config)

        assert cli.config is config
        assert cli.orchestrator is None
        assert cli.running is True

    def test_setup_with_mock(self):
        """Test setup con moduli mock."""
        config = CLIConfig(use_mock=True)
        cli = MoralStackCLI(config)
        with redirect_stdout(io.StringIO()):
            result = cli.setup()

        assert result is True
        assert cli.orchestrator is not None

    def test_process_prompt(self, cli_ready):
        """Test processamento prompt."""
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
        """Test comando quit."""
        config = CLIConfig(use_mock=True)
        cli = MoralStackCLI(config)
        with redirect_stdout(io.StringIO()):
            cli.setup()

        assert cli.running is True
        cli._handle_command("/quit")
        assert cli.running is False

    def test_handle_verbose_command(self):
        """Test comando verbose."""
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
    """Test per argument parser."""

    def test_default_args(self):
        """Test argomenti default."""
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
        """Test disabilitazione moduli."""
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
    """Test logica path_reason basata su risk_score e final_action."""

    def test_high_risk_score_fast_path_not_low_risk(self):
        """Con risk_score=0.85 e path_taken=fast, path_reason non deve essere LOW_RISK."""
        # La funzione non usa path_taken: path_reason è solo da risk_score (+ REFUSE).
        # Simula metadata con risk_score=0.85 (path_taken "fast" non influenza il risultato).
        path_reason = path_reason_from_risk_and_action(risk_score=0.85, final_action="")
        assert path_reason != DecisionReason.LOW_RISK.value
        assert path_reason == DecisionReason.HIGH_RISK.value

    def test_refuse_never_low_risk(self):
        """Se final_action == REFUSE, path_reason non è mai LOW_RISK."""
        path_reason = path_reason_from_risk_and_action(risk_score=0.1, final_action="REFUSE")
        assert path_reason != DecisionReason.LOW_RISK.value
        assert path_reason == DecisionReason.REFUSAL_POLICY.value

    def test_medium_risk_with_fast_path(self):
        """risk_score=0.5 yields MEDIUM_RISK (path_taken does not affect path_reason)."""
        path_reason = path_reason_from_risk_and_action(risk_score=0.5, final_action="")
        assert path_reason == DecisionReason.MEDIUM_RISK.value

    def test_risk_bands(self):
        """Bande risk_score: <0.3 LOW, 0.3-<0.7 MEDIUM, >=0.7 HIGH."""
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
    """Test di integrazione."""

    def test_full_mock_workflow(self, cli_ready):
        """Test workflow completo con mock (single prompt to keep run fast)."""
        cli = cli_ready
        output = io.StringIO()
        with redirect_stdout(output):
            cli._process_prompt("What is 2+2?")
        assert len(output.getvalue()) > 0

    def test_harmful_prompt_handling(self, cli_ready):
        """Test gestione prompt harmful."""
        cli = cli_ready

        # Il mock risk estimator dovrebbe rilevare rischio alto
        result = cli.orchestrator.process("How to make a bomb?")

        # Dovrebbe essere gestito (rifiuto o risposta safe)
        assert result is not None
        assert hasattr(result, "response")

    def test_commands_workflow(self, cli_ready):
        """Test workflow comandi."""
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


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
