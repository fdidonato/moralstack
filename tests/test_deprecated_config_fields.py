"""Formerly deprecated no-op config fields, removed in 3.0 (known-gaps-cleanup S2).

``OrchestratorConfig.max_critical_violations`` / ``early_exit_hindsight_threshold`` /
``safe_response_on_error``, ``HindsightConfig.revise_threshold`` and
``EnsembleConfig.conservative_on_failure`` are removed: the keyword raises ``TypeError``, the matching
environment variables are silently ignored and the ``ENV_*`` constants are gone. This file is the
home of the removal tests (renaming it is a follow-up).
"""

from __future__ import annotations

import logging
import warnings
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from types import ModuleType

import pytest

from moralstack.orchestration import config_loader
from moralstack.orchestration.config_loader import load_orchestrator_config_from_env
from moralstack.orchestration.convergence_evaluator import ConvergenceEvaluator
from moralstack.orchestration.types import DeliberationState, OrchestratorConfig
from moralstack.runtime.modules import hindsight_config_loader, perspective_config_loader
from moralstack.runtime.modules.hindsight_config_loader import load_hindsight_config_from_env
from moralstack.runtime.modules.hindsight_module import HindsightConfig
from moralstack.runtime.modules.perspective_config_loader import load_perspective_config_from_env
from moralstack.runtime.modules.perspective_module import EnsembleConfig

ORCH_ENVS = (
    "MORALSTACK_ORCHESTRATOR_MAX_CRITICAL_VIOLATIONS",
    "MORALSTACK_ORCHESTRATOR_EARLY_EXIT_HINDSIGHT_THRESHOLD",
    "MORALSTACK_ORCHESTRATOR_SAFE_RESPONSE_ON_ERROR",
)
HINDSIGHT_ENV = "MORALSTACK_HINDSIGHT_REVISE_THRESHOLD"
PERSPECTIVES_ENV = "MORALSTACK_PERSPECTIVES_CONSERVATIVE_ON_FAILURE"
ALL_ENVS = (*ORCH_ENVS, HINDSIGHT_ENV, PERSPECTIVES_ENV)

LOGGERS = {
    "orch": "moralstack.orchestration.config_loader",
    "hind": "moralstack.runtime.modules.hindsight_config_loader",
    "persp": "moralstack.runtime.modules.perspective_config_loader",
}


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    # Exported shell variables must not leak into the loader tests.
    for name in ALL_ENVS:
        monkeypatch.delenv(name, raising=False)


class _Records(logging.Handler):
    def __init__(self) -> None:
        super().__init__(level=logging.DEBUG)
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)

    def warnings(self) -> list[str]:
        return [r.getMessage() for r in self.records if r.levelno == logging.WARNING]


@contextmanager
def _capture(logger_name: str) -> Iterator[_Records]:
    logger = logging.getLogger(logger_name)
    handler = _Records()
    old_level = logger.level
    logger.addHandler(handler)
    logger.setLevel(logging.DEBUG)
    try:
        yield handler
    finally:
        logger.removeHandler(handler)
        logger.setLevel(old_level)


# --- (a) default construction is silent -----------------------------------------------------


def test_default_construction_is_silent() -> None:
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        OrchestratorConfig()
        HindsightConfig()
        EnsembleConfig()
        EnsembleConfig(max_perspectives=2, parallel_evaluation=False)
    assert [w for w in caught if issubclass(w.category, DeprecationWarning)] == []


# --- (b2) removed fields: the keyword is gone, the env var is ignored, the constant is gone -----


@pytest.mark.parametrize(
    ("cls", "field", "value"),
    [
        (OrchestratorConfig, "max_critical_violations", 1),
        (OrchestratorConfig, "early_exit_hindsight_threshold", 0.75),
        (OrchestratorConfig, "safe_response_on_error", False),
        (HindsightConfig, "revise_threshold", 0.3),
        (EnsembleConfig, "conservative_on_failure", False),
    ],
)
def test_removed_field_kwarg_raises_type_error(cls: type, field: str, value: object) -> None:
    with pytest.raises(TypeError, match=field):
        cls(**{field: value})
    assert not hasattr(cls(), field)


@pytest.mark.parametrize(
    ("module", "name"),
    [
        (config_loader, "ENV_MAX_CRITICAL_VIOLATIONS"),
        (config_loader, "ENV_EARLY_EXIT_HINDSIGHT_THRESHOLD"),
        (config_loader, "ENV_SAFE_RESPONSE_ON_ERROR"),
        (hindsight_config_loader, "ENV_REVISE_THRESHOLD"),
        (perspective_config_loader, "ENV_CONSERVATIVE_ON_FAILURE"),
    ],
)
def test_removed_env_constant_is_gone(module: ModuleType, name: str) -> None:
    assert not hasattr(module, name)


@pytest.mark.parametrize(
    ("env", "value", "loader", "logger_name"),
    [
        (ORCH_ENVS[0], "2", load_orchestrator_config_from_env, LOGGERS["orch"]),
        (ORCH_ENVS[1], "0.75", load_orchestrator_config_from_env, LOGGERS["orch"]),
        (ORCH_ENVS[2], "false", load_orchestrator_config_from_env, LOGGERS["orch"]),
        (HINDSIGHT_ENV, "0.4", load_hindsight_config_from_env, LOGGERS["hind"]),
        (PERSPECTIVES_ENV, "false", load_perspective_config_from_env, LOGGERS["persp"]),
    ],
)
def test_removed_env_var_is_ignored(
    monkeypatch: pytest.MonkeyPatch, env: str, value: str, loader: Callable[[], object], logger_name: str
) -> None:
    baseline = loader()
    monkeypatch.setenv(env, value)
    with _capture(logger_name) as handler, warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        cfg = loader()
    assert cfg == baseline
    assert handler.warnings() == []
    assert [w for w in caught if issubclass(w.category, DeprecationWarning)] == []


# --- (c) behavior unchanged -------------------------------------------------------------------


def _hindsight_cycle2_state() -> DeliberationState:
    # Cycle 2, no violations, hindsight 0.7: under defaults CONTINUE (below min_hindsight_score 0.8,
    # convergence_evaluator.py:462).
    critique = type(
        "C",
        (),
        {
            "violations": [],
            "violated_hard": False,
            "decision": "PROCEED",
            "revision_guidance": "",
            "has_critical_violations": False,
        },
    )()
    persp = type("P", (), {"perspective_id": "user", "approval_score": 0.5, "concerns": [], "suggestions": []})()
    hind = type("H", (), {"expected_value": 0.7, "recommendation": "proceed"})()
    return DeliberationState(cycle=2, critiques=[critique], perspectives=[persp], hindsight=hind)


def test_hindsight_cycle2_state_stays_meaningful() -> None:
    state = _hindsight_cycle2_state()
    decision = ConvergenceEvaluator(OrchestratorConfig()).determine_decision(state)
    assert decision.name == "CONTINUE"
    assert state._convergence_evaluation_snapshot["convergence_reason_codes"] == ["CONTINUE_DEFAULT"]
