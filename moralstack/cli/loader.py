"""
Module loader and CLI UI utilities for MoralStack.
"""

import os
from typing import Any, Callable, cast

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
            print_colored(
                "   Set the environment variable: export OPENAI_API_KEY=sk-...",
                "yellow",
            )
            print_colored("   Or pass --openai-key from the command line.", "yellow")
            raise ValueError("OPENAI_API_KEY is required. Set OPENAI_API_KEY or use --openai-key.")

        from moralstack.models.policy import OpenAIPolicy

        openai_policy = OpenAIPolicy(
            api_key=api_key,
            model=self.config.openai_model,
        )
        modules["policy"] = openai_policy
        self.load_status["policy"] = f"✓ OpenAI ({self.config.openai_model})"
        print_colored(f"  ✓ policy: OpenAI ({self.config.openai_model})", "green")

        # Constitution Store (requires openai_api_key)
        from moralstack.constitution.openai_config import OpenAIClientConfig
        from moralstack.constitution.store import ConstitutionStore, ConstitutionStoreConfig

        constitution_store = ConstitutionStore(
            config=ConstitutionStoreConfig(
                policy_llm=openai_policy,
                use_llm_matching=True,
                openai_config=OpenAIClientConfig.with_env_fallback(
                    api_key=api_key,
                    model=self.config.openai_model,
                ),
                max_parallel_agents=self.config.max_parallel_agents,
            )
        )
        modules["_constitution_store"] = constitution_store
        excluded_domains = constitution_store.get_excluded_domains()
        if excluded_domains:
            print_colored(
                f"  ⚠  Excluded domains: {', '.join(sorted(excluded_domains))}",
                "yellow",
            )
        else:
            print_colored("  ○  Excluded domains: none", "blue")

        def build_risk_estimator() -> tuple[Any, str]:
            from moralstack.models.risk import LLMBasedRiskEstimator
            from moralstack.models.risk.config_loader import ENV_MODEL, get_risk_env_str

            risk_model = get_risk_env_str(ENV_MODEL, "")
            policy_for_risk = OpenAIPolicy(api_key=api_key, model=risk_model) if risk_model else openai_policy
            inst = LLMBasedRiskEstimator(
                policy=cast(Any, policy_for_risk),
                constitution_store=constitution_store,
            )

            # Formatta la stringa di visualizzazione
            display_model = risk_model or self.config.openai_model
            if inst.config.use_parallel_estimators:
                display_info = (
                    f"Parallel Mode ON | main: {display_model} | "
                    f"intent: {inst.config.intent_model}, signals: {inst.config.signals_model}, "
                    f"op: {inst.config.operational_model}"
                )
            else:
                display_info = f"Monolithic Mode | {display_model}"

            return inst, display_info

        modules["risk_estimator"] = self._load_optional_module(
            name="risk_estimator",
            build_fn=build_risk_estimator,
            mock_class=MockRiskEstimator,
            success_status_template="✓ {display}",
            success_print_template="  ✓ risk_estimator: {display}",
            error_prefix="✗",
            error_color="red",
        )

        if self.config.minimal:
            # Minimal mode: policy and risk only
            for name in ["critic", "simulator", "hindsight", "perspectives"]:
                modules[name] = None
                self.load_status[name] = "○ disabled"
                print_colored(f"  ○ {name}: disabled (minimal mode)", "blue")
        else:
            # Full mode: load all modules
            def build_critic() -> tuple[Any, str]:
                from moralstack.runtime.modules.critic_config_loader import (
                    ENV_MODEL as CRITIC_ENV_MODEL,
                )
                from moralstack.runtime.modules.critic_config_loader import (
                    get_critic_env_str,
                )
                from moralstack.runtime.modules.critic_module import LLMConstitutionalCritic

                critic_model = get_critic_env_str(CRITIC_ENV_MODEL, "")
                policy_for_critic = OpenAIPolicy(api_key=api_key, model=critic_model) if critic_model else openai_policy
                inst = LLMConstitutionalCritic(
                    policy=cast(Any, policy_for_critic),
                    store=constitution_store,
                )
                return inst, (critic_model or self.config.openai_model)

            modules["critic"] = self._load_optional_module(
                name="critic",
                build_fn=build_critic,
                mock_class=MockCritic,
                success_status_template="✓ loaded ({display})",
                success_print_template="  ✓ critic: LLMConstitutionalCritic ({display})",
            )

            def build_simulator() -> tuple[Any, str]:
                from moralstack.runtime.modules.simulator_config_loader import (
                    ENV_MODEL as SIMULATOR_ENV_MODEL,
                )
                from moralstack.runtime.modules.simulator_config_loader import (
                    get_simulator_env_str,
                )
                from moralstack.runtime.modules.simulator_module import LLMConsequenceSimulator

                simulator_model = get_simulator_env_str(SIMULATOR_ENV_MODEL, "")
                policy_for_simulator = (
                    OpenAIPolicy(api_key=api_key, model=simulator_model) if simulator_model else openai_policy
                )
                inst = LLMConsequenceSimulator(policy=cast(Any, policy_for_simulator))
                return inst, (simulator_model or self.config.openai_model)

            modules["simulator"] = self._load_optional_module(
                name="simulator",
                build_fn=build_simulator,
                mock_class=MockSimulator,
                success_status_template="✓ loaded ({display})",
                success_print_template="  ✓ simulator: LLMConsequenceSimulator ({display})",
            )

            def build_hindsight() -> tuple[Any, str]:
                from moralstack.runtime.modules.hindsight_config_loader import (
                    ENV_MODEL as HINDSIGHT_ENV_MODEL,
                )
                from moralstack.runtime.modules.hindsight_config_loader import (
                    get_hindsight_env_str,
                )
                from moralstack.runtime.modules.hindsight_module import LLMHindsightEvaluator

                hindsight_model = get_hindsight_env_str(HINDSIGHT_ENV_MODEL, "")
                policy_for_hindsight = (
                    OpenAIPolicy(api_key=api_key, model=hindsight_model) if hindsight_model else openai_policy
                )
                inst = LLMHindsightEvaluator(policy=cast(Any, policy_for_hindsight))
                return inst, (hindsight_model or self.config.openai_model)

            modules["hindsight"] = self._load_optional_module(
                name="hindsight",
                build_fn=build_hindsight,
                mock_class=MockHindsight,
                success_status_template="✓ loaded ({display})",
                success_print_template="  ✓ hindsight: LLMHindsightEvaluator ({display})",
            )

            def build_perspectives() -> tuple[Any, str]:
                from moralstack.runtime.modules.perspective_config_loader import (
                    ENV_MODEL as PERSPECTIVES_ENV_MODEL,
                )
                from moralstack.runtime.modules.perspective_config_loader import (
                    get_perspective_env_str,
                )
                from moralstack.runtime.modules.perspective_module import create_minimal_ensemble

                perspectives_model = get_perspective_env_str(PERSPECTIVES_ENV_MODEL, "")
                policy_for_perspectives = (
                    OpenAIPolicy(api_key=api_key, model=perspectives_model) if perspectives_model else openai_policy
                )
                inst = create_minimal_ensemble(policy=cast(Any, policy_for_perspectives))
                return inst, (perspectives_model or self.config.openai_model)

            modules["perspectives"] = self._load_optional_module(
                name="perspectives",
                build_fn=build_perspectives,
                mock_class=MockPerspectives,
                success_status_template="✓ loaded ({display})",
                success_print_template="  ✓ perspectives: create_minimal_ensemble (2 perspectives, {display})",
            )

        # Save constitution_store for later access
        modules["_constitution_store"] = constitution_store

        self.modules = modules
        return modules

    def get_status(self) -> dict[str, str]:
        """Returns module status."""
        return self.load_status
