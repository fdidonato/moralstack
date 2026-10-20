"""A3: how ``prompts_dir`` / ``MORALSTACK_PROMPTS_DIR`` reaches the composition roots.

Covers precedence (config > env > packaged defaults), the blank-is-unset rule, fail-fast on a bad directory
(SDK, CLI, benchmark), and the two-set-mix guard: every consumer built by a root must hold the SAME
``PromptSet`` object as the orchestrator, because a request must never mix two sets.

Offline: dummy API key, no network call is made while the pipelines are built.
"""

from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from moralstack.constitution.openai_config import OpenAIClientConfig
from moralstack.constitution.store import ConstitutionStore, ConstitutionStoreConfig
from moralstack.pipeline.deliberation_stack import DeliberationModules, build_deliberation_modules
from moralstack.prompts.catalog import (
    ENV_PROMPTS_DIR,
    PromptConfigError,
    PromptSet,
    default_prompt_set,
    load_prompt_set,
    resolve_prompts_dir,
)
from moralstack.sdk.bootstrap import _bootstrap_pipeline
from moralstack.sdk.config import GovernanceConfig
from moralstack.sdk.errors import GovernancePipelineError
from tests.prompt_markers import make_marker_dir

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "benchmark_moralstack.py"

# Env knobs that make build_deliberation_modules create a separate policy per module.
_MODULE_MODEL_ENVS = (
    "MORALSTACK_RISK_MODEL",
    "MORALSTACK_CRITIC_MODEL",
    "MORALSTACK_SIMULATOR_MODEL",
    "MORALSTACK_HINDSIGHT_MODEL",
    "MORALSTACK_PERSPECTIVES_MODEL",
)


@pytest.fixture(autouse=True)
def _offline_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(ENV_PROMPTS_DIR, raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-dummy")
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    for key in _MODULE_MODEL_ENVS:
        monkeypatch.delenv(key, raising=False)
    # bootstrap/loader tests must not read a developer .env
    monkeypatch.setattr("moralstack.sdk.bootstrap.load_env", lambda: None)


def _cfg(**kwargs: object) -> GovernanceConfig:
    return GovernanceConfig(api_key="sk-test-dummy", enable_ledger=False, **kwargs)  # type: ignore[arg-type]


def _distinct_policies(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in _MODULE_MODEL_ENVS:
        monkeypatch.setenv(key, "gpt-4o-mini")


def _consumer_sets(orch: object) -> dict[str, object]:
    """Every PromptSet held by something the pipeline built for ``orch`` (the orchestrator's own included)."""
    ctrl = orch._controller  # type: ignore[attr-defined]
    retriever = orch.constitution_store._retriever  # type: ignore[attr-defined]
    found: dict[str, object] = {
        "orchestrator": orch.prompt_set,  # type: ignore[attr-defined]
        "controller": ctrl._prompt_set,
        "assembler": ctrl.assembler._prompt_set,
        "runner": ctrl._runner._prompt_set,
        "refusal_handler": ctrl._refusal_handler._prompt_set,
        "policy": orch.policy._prompt_set,  # type: ignore[attr-defined]
        "risk_estimator": orch.risk_estimator._prompt_set,  # type: ignore[attr-defined]
        "risk_policy": orch.risk_estimator.policy._prompt_set,  # type: ignore[attr-defined]
        "critic": orch.critic._prompt_set,  # type: ignore[attr-defined]
        "critic_policy": orch.critic.policy._prompt_set,  # type: ignore[attr-defined]
        "simulator": orch.simulator._prompt_set,  # type: ignore[attr-defined]
        "simulator_policy": orch.simulator.policy._prompt_set,  # type: ignore[attr-defined]
        "hindsight": orch.hindsight._prompt_set,  # type: ignore[attr-defined]
        "hindsight_policy": orch.hindsight.policy._prompt_set,  # type: ignore[attr-defined]
        "perspectives": orch.perspectives._prompt_set,  # type: ignore[attr-defined]
        "perspectives_policy": orch.perspectives.policy._prompt_set,  # type: ignore[attr-defined]
        "store_retriever_config": retriever._config.prompt_set,
        "store_prefilter": retriever._domain_prefilter._prompt_set,
        "output_protector": ctrl._output_protector._prompt_set,
        "canary": ctrl._output_protector._canary._prompt_set,
    }
    return found


def _assert_one_set(orch: object, expected: PromptSet) -> None:
    """The two-set-mix guard: all consumers hold the SAME object as the orchestrator."""
    # the per-module policies really are separate objects, so the checks below are not vacuous
    assert orch.critic.policy is not orch.policy  # type: ignore[attr-defined]
    for name, held in _consumer_sets(orch).items():
        assert held is expected, f"{name} holds a different PromptSet than the orchestrator"


def _bad_dir_missing(tmp_path: Path) -> Path:
    return tmp_path / "does_not_exist"


def _bad_dir_empty(tmp_path: Path) -> Path:
    path = tmp_path / "empty"
    path.mkdir()
    return path


def _bad_dir_invalid_yaml(tmp_path: Path) -> Path:
    path = tmp_path / "bad_yaml"
    path.mkdir()
    (path / "o.yaml").write_text("version: 1\nfragments: [unclosed\n", encoding="utf-8")
    return path


def _bad_dir_undeclared_placeholder(tmp_path: Path) -> Path:
    path = tmp_path / "bad_placeholder"
    path.mkdir()
    (path / "o.yaml").write_text(
        'version: 1\nfragments:\n  risk.intent.user:\n    text: "{request} {not_declared}"\n', encoding="utf-8"
    )
    return path


BAD_DIRS = pytest.mark.parametrize(
    "make_bad",
    [_bad_dir_missing, _bad_dir_empty, _bad_dir_invalid_yaml, _bad_dir_undeclared_placeholder],
    ids=["missing", "empty", "invalid_yaml", "undeclared_placeholder"],
)


# ---------------------------------------------------------------------------------------
# config field + env var registration
# ---------------------------------------------------------------------------------------


def test_governance_config_prompts_dir_defaults_to_none() -> None:
    assert GovernanceConfig().prompts_dir is None


def test_env_var_is_a_registered_optional_var_and_blank_values_are_purged(monkeypatch: pytest.MonkeyPatch) -> None:
    from moralstack.utils import env_loader

    assert ENV_PROMPTS_DIR in env_loader._OPTIONAL_ENV_VARS
    monkeypatch.setenv(ENV_PROMPTS_DIR, "   ")
    env_loader._purge_empty_env_vars()
    assert ENV_PROMPTS_DIR not in os.environ


# ---------------------------------------------------------------------------------------
# precedence: config > env > packaged defaults; blank is unset
# ---------------------------------------------------------------------------------------


def test_precedence_config_over_env_over_default(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config_dir = make_marker_dir(tmp_path, marker="<<CONFIG:{id}>>")
    env_dir = make_marker_dir(tmp_path, marker="<<ENV:{id}>>")
    config_ps = load_prompt_set(str(config_dir))
    env_ps = load_prompt_set(str(env_dir))
    assert config_ps.fingerprint != env_ps.fingerprint

    # 1. nothing configured: packaged defaults (the singleton)
    assert _bootstrap_pipeline(_cfg()).prompt_set is default_prompt_set()

    # 2. env only
    monkeypatch.setenv(ENV_PROMPTS_DIR, str(env_dir))
    assert _bootstrap_pipeline(_cfg()).prompt_set.fingerprint == env_ps.fingerprint

    # 3. config beats env
    assert _bootstrap_pipeline(_cfg(prompts_dir=str(config_dir))).prompt_set.fingerprint == config_ps.fingerprint


@pytest.mark.parametrize("blank", ["", "   ", "\t\n"])
def test_blank_config_prompts_dir_is_unset_and_falls_through_to_env(
    blank: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env_dir = make_marker_dir(tmp_path)
    monkeypatch.setenv(ENV_PROMPTS_DIR, str(env_dir))
    assert resolve_prompts_dir(blank) == str(env_dir)
    orch = _bootstrap_pipeline(_cfg(prompts_dir=blank))
    assert orch.prompt_set.fingerprint == load_prompt_set(str(env_dir)).fingerprint


def test_resolve_prompts_dir_accepts_pathlike_and_returns_the_stripped_value(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert resolve_prompts_dir(tmp_path) == str(tmp_path)
    assert resolve_prompts_dir("  /some/dir \n") == "/some/dir"
    monkeypatch.setenv(ENV_PROMPTS_DIR, "  /from/env  ")
    assert resolve_prompts_dir(None) == "/from/env"
    assert resolve_prompts_dir(Path("  ")) == "/from/env"


@pytest.mark.parametrize("blank", ["", "   "])
def test_blank_config_and_blank_env_mean_packaged_defaults(blank: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ENV_PROMPTS_DIR, blank)
    assert resolve_prompts_dir(blank) is None
    assert _bootstrap_pipeline(_cfg(prompts_dir=blank)).prompt_set is default_prompt_set()


# ---------------------------------------------------------------------------------------
# a bad prompts_dir fails the pipeline build, before any request, and no GovernedClient exists
# ---------------------------------------------------------------------------------------


@BAD_DIRS
def test_bad_prompts_dir_raises_governance_pipeline_error_from_bootstrap(make_bad, tmp_path: Path) -> None:
    bad = make_bad(tmp_path)
    with pytest.raises(GovernancePipelineError, match="deliberation modules") as excinfo:
        _bootstrap_pipeline(_cfg(prompts_dir=str(bad)))
    assert isinstance(excinfo.value.__cause__, PromptConfigError)


@BAD_DIRS
def test_bad_env_prompts_dir_raises_too(make_bad, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ENV_PROMPTS_DIR, str(make_bad(tmp_path)))
    with pytest.raises(GovernancePipelineError):
        _bootstrap_pipeline(_cfg())


@BAD_DIRS
def test_govern_builds_no_governed_client_when_prompts_dir_is_bad(
    make_bad, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from moralstack.sdk import wrapper

    monkeypatch.setattr(wrapper, "load_env", lambda: None)
    governed_client = MagicMock()
    monkeypatch.setattr(wrapper, "GovernedClient", governed_client)
    with pytest.raises(GovernancePipelineError):
        wrapper.govern(MagicMock(), config=_cfg(prompts_dir=str(make_bad(tmp_path))))
    governed_client.assert_not_called()


def test_empty_prompts_dir_error_text_is_the_documented_one(tmp_path: Path) -> None:
    empty = _bad_dir_empty(tmp_path)
    with pytest.raises(GovernancePipelineError) as excinfo:
        _bootstrap_pipeline(_cfg(prompts_dir=str(empty)))
    reason = str(excinfo.value.__cause__)
    assert "contains no *.yaml override files; unset prompts_dir (or MORALSTACK_PROMPTS_DIR)" in reason
    assert "use the packaged defaults" in reason


# ---------------------------------------------------------------------------------------
# DeliberationModules stays buildable by hand (tests and library callers)
# ---------------------------------------------------------------------------------------


def test_deliberation_modules_can_be_built_without_prompt_set() -> None:
    modules = DeliberationModules(
        policy=MagicMock(),
        constitution_store=MagicMock(),
        risk_estimator=MagicMock(),
        critic=None,
        simulator=None,
        hindsight=None,
        perspectives=None,
    )
    assert modules.prompt_set is None


# ---------------------------------------------------------------------------------------
# two-set-mix guard, per composition root
# ---------------------------------------------------------------------------------------


def test_build_deliberation_modules_hands_one_set_to_every_consumer(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _distinct_policies(monkeypatch)
    marker_dir = make_marker_dir(tmp_path)
    modules, _meta = build_deliberation_modules(api_key="sk-test-dummy", primary_model="gpt-4o", prompts_dir=str(marker_dir))
    ps = modules.prompt_set
    assert isinstance(ps, PromptSet) and not ps.is_default
    for name, held in {
        "policy": modules.policy._prompt_set,
        "risk_estimator": modules.risk_estimator._prompt_set,
        "risk_policy": modules.risk_estimator.policy._prompt_set,
        "critic": modules.critic._prompt_set,
        "critic_policy": modules.critic.policy._prompt_set,
        "simulator": modules.simulator._prompt_set,
        "simulator_policy": modules.simulator.policy._prompt_set,
        "hindsight": modules.hindsight._prompt_set,
        "hindsight_policy": modules.hindsight.policy._prompt_set,
        "perspectives": modules.perspectives._prompt_set,
        "perspectives_policy": modules.perspectives.policy._prompt_set,
        "store_retriever_config": modules.constitution_store._retriever._config.prompt_set,
        "store_prefilter": modules.constitution_store._retriever._domain_prefilter._prompt_set,
    }.items():
        assert held is ps, name
    # the distinct per-module policies really are distinct objects (the guard above is not vacuous)
    assert modules.risk_estimator.policy is not modules.policy


def test_build_deliberation_modules_minimal_also_carries_the_set(tmp_path: Path) -> None:
    modules, _meta = build_deliberation_modules(
        api_key="sk-test-dummy", primary_model="gpt-4o", minimal=True, prompts_dir=str(make_marker_dir(tmp_path))
    )
    assert modules.prompt_set is modules.policy._prompt_set is modules.risk_estimator._prompt_set
    assert modules.prompt_set is not default_prompt_set()


def test_build_deliberation_modules_default_is_the_default_singleton() -> None:
    modules, _meta = build_deliberation_modules(api_key="sk-test-dummy", primary_model="gpt-4o")
    assert modules.prompt_set is default_prompt_set()
    assert modules.policy._prompt_set is default_prompt_set()


def test_build_deliberation_modules_raises_prompt_config_error_on_bad_dir(tmp_path: Path) -> None:
    with pytest.raises(PromptConfigError):
        build_deliberation_modules(
            api_key="sk-test-dummy", primary_model="gpt-4o", prompts_dir=str(_bad_dir_empty(tmp_path))
        )


@pytest.mark.parametrize("use_marker", [True, False], ids=["override", "default"])
def test_sdk_bootstrap_two_set_mix_guard(use_marker: bool, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _distinct_policies(monkeypatch)
    kwargs = {"prompts_dir": str(make_marker_dir(tmp_path))} if use_marker else {}
    orch = _bootstrap_pipeline(_cfg(**kwargs))
    assert (not orch.prompt_set.is_default) is use_marker
    _assert_one_set(orch, orch.prompt_set)


def test_cli_honours_the_env_var_and_holds_one_set(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from moralstack.cli.models import CLIConfig
    from moralstack.cli.shell import MoralStackCLI

    _distinct_policies(monkeypatch)
    marker_dir = make_marker_dir(tmp_path)
    monkeypatch.setenv(ENV_PROMPTS_DIR, str(marker_dir))
    cli = MoralStackCLI(CLIConfig(openai_api_key="sk-test-dummy"))
    assert cli.setup() is True
    orch = cli.orchestrator
    assert orch.prompt_set.fingerprint == load_prompt_set(str(marker_dir)).fingerprint
    assert not orch.prompt_set.is_default
    _assert_one_set(orch, orch.prompt_set)


def test_cli_without_env_uses_the_packaged_defaults() -> None:
    from moralstack.cli.models import CLIConfig
    from moralstack.cli.shell import MoralStackCLI

    cli = MoralStackCLI(CLIConfig(openai_api_key="sk-test-dummy"))
    assert cli.setup() is True
    assert cli.orchestrator.prompt_set is default_prompt_set()


def test_cli_fails_fast_on_a_bad_env_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from moralstack.cli.loader import ModuleLoader
    from moralstack.cli.models import CLIConfig

    monkeypatch.setenv(ENV_PROMPTS_DIR, str(_bad_dir_empty(tmp_path)))
    with pytest.raises(PromptConfigError):
        ModuleLoader(CLIConfig(openai_api_key="sk-test-dummy")).load_all()


def test_cli_mock_mode_builds_the_orchestrator_on_the_default_set() -> None:
    from moralstack.cli.models import CLIConfig
    from moralstack.cli.shell import MoralStackCLI

    cli = MoralStackCLI(CLIConfig(use_mock=True))
    assert cli.setup() is True
    assert cli.orchestrator.prompt_set is default_prompt_set()


def test_cli_fallback_store_config_receives_the_set(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Modules without a store (the fallback branch of setup) build ConstitutionStoreConfig(prompt_set=...)."""
    from moralstack.cli import shell
    from moralstack.cli.models import CLIConfig

    ps = load_prompt_set(str(make_marker_dir(tmp_path)))
    seen: list[ConstitutionStoreConfig] = []
    real_store = ConstitutionStore

    def spy_store(*, config: ConstitutionStoreConfig, **kw: object) -> ConstitutionStore:
        seen.append(config)
        return real_store(config=config, **kw)  # type: ignore[arg-type]

    modules = {"policy": None, "risk_estimator": None, "_prompt_set": ps}

    class _Loader:
        def __init__(self, config: object) -> None:
            self.load_status: dict[str, str] = {}

        def load_all(self) -> dict[str, object]:
            return modules

    monkeypatch.setattr(shell, "ModuleLoader", _Loader)
    monkeypatch.setattr("moralstack.constitution.store.ConstitutionStore", spy_store)
    cli = shell.MoralStackCLI(CLIConfig(openai_api_key="sk-test-dummy"))
    assert cli.setup() is True
    assert seen and seen[0].prompt_set is ps
    assert cli.orchestrator.prompt_set is ps


# ---------------------------------------------------------------------------------------
# benchmark: resolve + load once in __init__, before any LLM client; the runner threads the set everywhere
# ---------------------------------------------------------------------------------------


def _load_benchmark(monkeypatch: pytest.MonkeyPatch) -> object:
    name = "benchmark_moralstack_a3"
    spec = importlib.util.spec_from_file_location(name, SCRIPT)
    mod = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    monkeypatch.setattr(sys, "path", list(sys.path))
    monkeypatch.setitem(sys.modules, name, mod)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


def test_benchmark_runner_fails_before_any_llm_client_on_a_bad_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    mod = _load_benchmark(monkeypatch)
    calls: list[str] = []
    monkeypatch.setattr(mod, "OpenAIClient", lambda *a, **k: calls.append("OpenAIClient"))
    monkeypatch.setattr(mod, "MoralStackRunner", lambda *a, **k: calls.append("MoralStackRunner"))
    monkeypatch.setenv(ENV_PROMPTS_DIR, str(_bad_dir_empty(tmp_path)))
    with pytest.raises(PromptConfigError):
        mod.BenchmarkRunner(api_key="sk-test-dummy")
    assert calls == []


def test_benchmark_runner_honours_the_env_var_and_hands_the_set_to_the_moralstack_runner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    mod = _load_benchmark(monkeypatch)
    marker_dir = make_marker_dir(tmp_path)
    monkeypatch.setenv(ENV_PROMPTS_DIR, str(marker_dir))
    runner = mod.BenchmarkRunner(api_key="sk-test-dummy")
    assert runner.prompt_set.fingerprint == load_prompt_set(str(marker_dir)).fingerprint
    assert not runner.prompt_set.is_default
    assert runner.moralstack_runner.prompt_set is runner.prompt_set


def test_benchmark_runner_without_env_uses_the_default_singleton(monkeypatch: pytest.MonkeyPatch) -> None:
    mod = _load_benchmark(monkeypatch)
    runner = mod.BenchmarkRunner(api_key="sk-test-dummy")
    assert runner.prompt_set is default_prompt_set()
    assert runner.moralstack_runner.prompt_set is default_prompt_set()


@pytest.mark.parametrize("use_marker", [True, False], ids=["override", "default"])
def test_benchmark_initialize_two_set_mix_guard(use_marker: bool, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    mod = _load_benchmark(monkeypatch)
    _distinct_policies(monkeypatch)
    if use_marker:
        monkeypatch.setenv(ENV_PROMPTS_DIR, str(make_marker_dir(tmp_path)))
    runner = mod.BenchmarkRunner(api_key="sk-test-dummy")
    runner.moralstack_runner._initialize()
    orch = runner.moralstack_runner.orchestrator
    assert (not orch.prompt_set.is_default) is use_marker
    assert orch.prompt_set is runner.prompt_set
    _assert_one_set(orch, runner.prompt_set)


def test_constitution_store_kwarg_threads_the_set_to_prefilter_and_retriever(tmp_path: Path) -> None:
    """The benchmark builds ConstitutionStore from kwargs (no config object)."""
    ps = load_prompt_set(str(make_marker_dir(tmp_path)))
    store = ConstitutionStore(
        use_llm_matching=True,
        openai_config=OpenAIClientConfig.with_env_fallback(api_key="sk-test-dummy", model="gpt-4o"),
        prompt_set=ps,
    )
    assert store._retriever._config.prompt_set is ps
    assert store._retriever._domain_prefilter._prompt_set is ps


def test_constitution_store_without_prompt_set_keeps_the_default() -> None:
    store = ConstitutionStore(
        use_llm_matching=True,
        openai_config=OpenAIClientConfig.with_env_fallback(api_key="sk-test-dummy", model="gpt-4o"),
    )
    assert store._retriever._domain_prefilter._prompt_set is default_prompt_set()


def test_bootstrap_signature_places_prompts_dir_resolution_after_load_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """The env var must be read after load_env(): a .env-provided MORALSTACK_PROMPTS_DIR has to be seen."""
    order: list[str] = []
    monkeypatch.setattr("moralstack.sdk.bootstrap.load_env", lambda: order.append("load_env"))
    real_resolve = resolve_prompts_dir

    def spy(explicit: str | None) -> str | None:
        order.append("resolve")
        return real_resolve(explicit)

    monkeypatch.setattr("moralstack.sdk.bootstrap.resolve_prompts_dir", spy)
    with patch("moralstack.sdk.bootstrap.build_deliberation_modules", side_effect=RuntimeError("stop")):
        with pytest.raises(GovernancePipelineError):
            _bootstrap_pipeline(_cfg())
    assert order == ["load_env", "resolve"]


def test_hand_built_namespace_without_prompt_set_is_tolerated_by_the_cli_loader(monkeypatch: pytest.MonkeyPatch) -> None:
    """test_risk_model_resolution builds the stack result as a SimpleNamespace with no ``prompt_set`` attribute."""
    from moralstack.cli.loader import ModuleLoader
    from moralstack.cli.models import CLIConfig
    from moralstack.models.risk import LLMBasedRiskEstimator
    from moralstack.models.risk.schema import RiskEstimatorConfig

    built = SimpleNamespace(
        policy=object(),
        constitution_store=SimpleNamespace(get_excluded_domains=lambda: set()),
        risk_estimator=LLMBasedRiskEstimator(policy=None, config=RiskEstimatorConfig()),
    )
    meta = SimpleNamespace(policy_model="p", risk_model="x")
    loader = ModuleLoader(CLIConfig(openai_api_key="k", minimal=True))
    with patch("moralstack.pipeline.deliberation_stack.build_deliberation_modules", return_value=(built, meta)):
        modules = loader.load_all()
    assert modules["_prompt_set"] is None
