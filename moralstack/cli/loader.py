"""
Module loader and CLI UI utilities for MoralStack.
"""

import os
from typing import Any, Callable

from .mocks import (
    MockConstitutionStore,
    MockCritic,
    MockHindsight,
    MockPerspectives,
    MockPolicy,
    MockRiskEstimator,
    MockSimulator,
)
from .models import CLIConfig

BANNER = """
╔═══════════════════════════════════════════════════════════════════════════════════════════╗
║                                                                                           ║
║   ███╗   ███╗ ██████╗ ██████╗  █████╗ ██╗     ███████╗████████╗ █████╗  ██████╗██╗  ██╗   ║
║   ████╗ ████║██╔═══██╗██╔══██╗██╔══██╗██║     ██╔════╝╚══██╔══╝██╔══██╗██╔════╝██║ ██╔╝   ║
║   ██╔████╔██║██║   ██║██████╔╝███████║██║     ███████╗   ██║   ███████║██║     █████╔╝    ║
║   ██║╚██╔╝██║██║   ██║██╔══██╗██╔══██║██║     ╚════██║   ██║   ██╔══██║██║     ██╔═██╗    ║
║   ██║ ╚═╝ ██║╚██████╔╝██║  ██║██║  ██║███████╗███████║   ██║   ██║  ██║╚██████╗██║  ██╗   ║
║   ╚═╝     ╚═╝ ╚═════╝ ╚═╝  ╚═╝╚═╝  ╚═╝╚══════╝╚══════╝   ╚═╝   ╚═╝  ╚═╝ ╚═════╝╚═╝  ╚═╝   ║
║                                                                                           ║
║   Auditable AI governance, by design                                                      ║
║                                                                                           ║
╚═══════════════════════════════════════════════════════════════════════════════════════════╝
"""

HELP_TEXT = """
Available commands:
  /help       - Show this message
  /status     - Show status of loaded modules
  /config     - Show current configuration
  /verbose    - Toggle verbose mode
  /report     - Show report of last processing
  /clear      - Clear screen
  /quit       - Exit

Type any prompt to receive a response from the MoralStack system.
The system will evaluate the ethical risk and respond accordingly.

At the end of each processing, a detailed report of the
deliberative process with phase map, decisions and error analysis is shown.
"""


def print_colored(text: str, color: str = "white") -> None:
    """Prints colored text (if supported)."""
    colors = {
        "red": "\033[91m",
        "green": "\033[92m",
        "yellow": "\033[93m",
        "blue": "\033[94m",
        "magenta": "\033[95m",
        "cyan": "\033[96m",
        "white": "\033[97m",
        "reset": "\033[0m",
    }

    # Try to use colors, fallback to plain text
    try:
        print(f"{colors.get(color, '')}{text}{colors['reset']}")
    except Exception:
        print(text)


def print_banner() -> None:
    """Prints the welcome banner."""
    print_colored(BANNER, "cyan")


def print_separator() -> None:
    """Prints separator."""
    print_colored("─" * 80, "blue")


class ModuleLoader:
    """
    Loads the runtime stack modules.

    Supports real loading (with API) or mock (for testing).
    """

    def __init__(self, config: CLIConfig):
        self.config = config
        self.modules: dict[str, Any] = {}
        self.load_status: dict[str, str] = {}

    def load_all(self) -> dict[str, Any]:
        """Loads all modules."""
        print_colored("\n🔄 Loading modules...\n", "yellow")

        if self.config.use_mock:
            return self._load_mock_modules()
        else:
            return self._load_real_modules()

    def _load_mock_modules(self) -> dict[str, Any]:
        """Loads mock modules."""
        modules = {
            "policy": MockPolicy(),
            "risk_estimator": MockRiskEstimator(),
            "critic": MockCritic(),
            "simulator": MockSimulator(),
            "hindsight": MockHindsight(),
            "perspectives": MockPerspectives(),
            "_constitution_store": MockConstitutionStore(),
        }

        for name in modules:
            self.load_status[name] = "✓ mock"
            print_colored(f"  ✓ {name}: mock", "green")

        print_colored("  ○  Excluded domains: none (mock)", "blue")
        self.modules = modules
        return modules

    def _load_optional_module(
        self,
        name: str,
        build_fn: Callable[[], tuple[Any, str]],
        mock_class: type,
        *,
        success_status_template: str = "✓ loaded ({display})",
        success_print_template: str = "  ✓ {name}: ({display})",
        error_prefix: str = "⚠",
        error_color: str = "yellow",
    ) -> Any:
        """Load optional module with try/except and mock fallback; messages are parameterized."""
        try:
            instance, display = build_fn()
            self.load_status[name] = success_status_template.format(display=display)
            print_colored(success_print_template.format(name=name, display=display), "green")
            return instance
        except Exception as e:
            print_colored(f"  {error_prefix} {name}: {e}", error_color)
            self.load_status[name] = "⚠ fallback mock"
            return mock_class()

    def _load_real_modules(self) -> dict[str, Any]:
        """Loads real modules (OpenAI-only)."""
        modules: dict[str, Any] = {}

        api_key = self.config.openai_api_key or os.getenv("OPENAI_API_KEY")
        if not (api_key or "").strip():
            print_colored("⚠️  ERROR: OPENAI_API_KEY not set.", "red")
            print_colored("   Set the environment variable: export OPENAI_API_KEY=sk-...", "yellow")
            print_colored("   Or pass --openai-key from the command line.", "yellow")
            raise ValueError("OPENAI_API_KEY is required. Set OPENAI_API_KEY or use --openai-key.")

        from moralstack.pipeline.deliberation_stack import build_deliberation_modules

        built_modules, meta = build_deliberation_modules(
            api_key=api_key,
            primary_model=self.config.openai_model,
            max_parallel_agents=self.config.max_parallel_agents,
            minimal=self.config.minimal,
        )

        modules["policy"] = built_modules.policy
        self.load_status["policy"] = f"✓ OpenAI ({meta.policy_model})"
        print_colored(f"  ✓ policy: OpenAI ({meta.policy_model})", "green")

        modules["_constitution_store"] = built_modules.constitution_store
        excluded_domains = built_modules.constitution_store.get_excluded_domains()
        if excluded_domains:
            print_colored(f"  ⚠  Excluded domains: {', '.join(sorted(excluded_domains))}", "yellow")
        else:
            print_colored("  ○  Excluded domains: none", "blue")

        modules["risk_estimator"] = built_modules.risk_estimator
        risk_cfg = getattr(built_modules.risk_estimator, "config", None)
        if risk_cfg is not None and getattr(risk_cfg, "use_parallel_estimators", False):
            display_info = (
                f"Parallel Mode ON | main: {meta.risk_model} | "
                f"intent: {risk_cfg.intent_model}, signals: {risk_cfg.signals_model}, "
                f"op: {risk_cfg.operational_model}"
            )
        else:
            display_info = f"Monolithic Mode | {meta.risk_model}"
        self.load_status["risk_estimator"] = f"✓ {display_info}"
        print_colored(f"  ✓ risk_estimator: {display_info}", "green")

        if self.config.minimal:
            for name in ["critic", "simulator", "hindsight", "perspectives"]:
                modules[name] = None
                self.load_status[name] = "○ disabled"
                print_colored(f"  ○ {name}: disabled (minimal mode)", "blue")
        else:
            modules["critic"] = built_modules.critic
            self.load_status["critic"] = f"✓ loaded ({meta.critic_model})"
            print_colored(f"  ✓ critic: LLMConstitutionalCritic ({meta.critic_model})", "green")

            modules["simulator"] = built_modules.simulator
            self.load_status["simulator"] = f"✓ loaded ({meta.simulator_model})"
            print_colored(f"  ✓ simulator: LLMConsequenceSimulator ({meta.simulator_model})", "green")

            modules["hindsight"] = built_modules.hindsight
            self.load_status["hindsight"] = f"✓ loaded ({meta.hindsight_model})"
            print_colored(f"  ✓ hindsight: LLMHindsightEvaluator ({meta.hindsight_model})", "green")

            modules["perspectives"] = built_modules.perspectives
            self.load_status["perspectives"] = f"✓ loaded ({meta.perspectives_model})"
            print_colored(
                f"  ✓ perspectives: create_minimal_ensemble (2 perspectives, {meta.perspectives_model})",
                "green",
            )

        self.modules = modules
        return modules

    def get_status(self) -> dict[str, str]:
        """Returns module status."""
        return self.load_status
