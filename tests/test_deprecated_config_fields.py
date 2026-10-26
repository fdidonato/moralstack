"""Deprecated no-op config fields (known-gaps-cleanup S2).

``OrchestratorConfig.max_critical_violations`` / ``early_exit_hindsight_threshold`` /
``safe_response_on_error``, ``HindsightConfig.revise_threshold`` and
``EnsembleConfig.conservative_on_failure`` still parse and still have no effect. A non-default
value warns (``DeprecationWarning`` on construction, one WARNING per env load); defaults are silent.
"""

from __future__ import annotations

import logging
import warnings
from collections.abc import Iterator
from contextlib import contextmanager
from unittest.mock import MagicMock

import pytest

from moralstack.orchestration.config_loader import (
    ENV_EARLY_EXIT_HINDSIGHT_THRESHOLD,
    ENV_MAX_CRITICAL_VIOLATIONS,
    ENV_SAFE_RESPONSE_ON_ERROR,
    load_orchestrator_config_from_env,
)
from moralstack.orchestration.convergence_evaluator import ConvergenceEvaluator
from moralstack.orchestration.types import DeliberationState, OrchestratorConfig
from moralstack.runtime.modules.hindsight_config_loader import ENV_REVISE_THRESHOLD, load_hindsight_config_from_env
from moralstack.runtime.modules.hindsight_module import (
    HindsightConfig,
    HindsightEvaluation,
    HindsightScores,
    LLMHindsightEvaluator,
)
from moralstack.runtime.modules.perspective_config_loader import (
    ENV_CONSERVATIVE_ON_FAILURE,
    load_perspective_config_from_env,
)
from moralstack.runtime.modules.perspective_module import (
    PERSPECTIVES_BY_ID,
    EnsembleConfig,
    LLMPerspectiveEnsemble,
)
from tests.test_perspective_module import FailingMockPolicyLLM

ORCH_ENVS = (ENV_MAX_CRITICAL_VIOLATIONS, ENV_EARLY_EXIT_HINDSIGHT_THRESHOLD, ENV_SAFE_RESPONSE_ON_ERROR)
ALL_ENVS = (*ORCH_ENVS, ENV_REVISE_THRESHOLD, ENV_CONSERVATIVE_ON_FAILURE)

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


# --- (a) direct construction, non-default -> DeprecationWarning -----------------------------


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
def test_non_default_construction_warns(cls: type, field: str, value: object) -> None:
    with pytest.warns(DeprecationWarning, match=f"{cls.__name__}.{field}") as record:
        obj = cls(**{field: value})
    assert getattr(obj, field) == value
    assert len([w for w in record if issubclass(w.category, DeprecationWarning)]) == 1
    assert record[0].filename == __file__  # stacklevel points at the caller


# --- (b) default construction is silent -----------------------------------------------------


def test_default_construction_is_silent() -> None:
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        OrchestratorConfig()
        HindsightConfig()
        EnsembleConfig()
        EnsembleConfig(max_perspectives=2, parallel_evaluation=False)
    assert [w for w in caught if issubclass(w.category, DeprecationWarning)] == []


# --- (c) loaders: one WARNING per non-default env var ---------------------------------------


def test_orchestrator_loader_warns_once_per_non_default_var(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ENV_MAX_CRITICAL_VIOLATIONS, "2")
    with _capture(LOGGERS["orch"]) as handler, pytest.warns(DeprecationWarning):
        cfg = load_orchestrator_config_from_env()
    assert cfg.max_critical_violations == 2
    msgs = handler.warnings()
    assert len(msgs) == 1
    assert ENV_MAX_CRITICAL_VIOLATIONS in msgs[0]
    assert "removal in 3.0" in msgs[0]


def test_orchestrator_loader_early_exit_warns_once(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ENV_EARLY_EXIT_HINDSIGHT_THRESHOLD, "0.75")
    with _capture(LOGGERS["orch"]) as handler, pytest.warns(DeprecationWarning):
        cfg = load_orchestrator_config_from_env()
    assert cfg.early_exit_hindsight_threshold == 0.75
    msgs = handler.warnings()
    assert len(msgs) == 1
    assert ENV_EARLY_EXIT_HINDSIGHT_THRESHOLD in msgs[0]


def test_orchestrator_loader_three_vars_three_distinct_warnings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ENV_MAX_CRITICAL_VIOLATIONS, "2")
    monkeypatch.setenv(ENV_EARLY_EXIT_HINDSIGHT_THRESHOLD, "0.75")
    monkeypatch.setenv(ENV_SAFE_RESPONSE_ON_ERROR, "false")
    with _capture(LOGGERS["orch"]) as handler, pytest.warns(DeprecationWarning):
        load_orchestrator_config_from_env()
    msgs = handler.warnings()
    assert len(msgs) == 3
    assert len(set(msgs)) == 3
    for name in ORCH_ENVS:
        assert sum(name in m for m in msgs) == 1


def test_hindsight_loader_warns_once(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ENV_REVISE_THRESHOLD, "0.4")
    with _capture(LOGGERS["hind"]) as handler, pytest.warns(DeprecationWarning):
        cfg = load_hindsight_config_from_env()
    assert cfg.revise_threshold == 0.4
    msgs = handler.warnings()
    assert len(msgs) == 1
    assert ENV_REVISE_THRESHOLD in msgs[0]


def test_perspective_loader_warns_once(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ENV_CONSERVATIVE_ON_FAILURE, "false")
    with _capture(LOGGERS["persp"]) as handler, pytest.warns(DeprecationWarning):
        cfg = load_perspective_config_from_env()
    assert cfg.conservative_on_failure is False
    msgs = handler.warnings()
    assert len(msgs) == 1
    assert ENV_CONSERVATIVE_ON_FAILURE in msgs[0]


# --- (d) env at the default value -> silent ---------------------------------------------------


def test_default_valued_env_is_silent(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ENV_MAX_CRITICAL_VIOLATIONS, "0")
    monkeypatch.setenv(ENV_EARLY_EXIT_HINDSIGHT_THRESHOLD, "0.6")
    monkeypatch.setenv(ENV_SAFE_RESPONSE_ON_ERROR, "true")
    monkeypatch.setenv(ENV_REVISE_THRESHOLD, "0.0")
    monkeypatch.setenv(ENV_CONSERVATIVE_ON_FAILURE, "true")
    with (
        _capture(LOGGERS["orch"]) as orch,
        _capture(LOGGERS["hind"]) as hind,
        _capture(LOGGERS["persp"]) as persp,
        warnings.catch_warnings(record=True) as caught,
    ):
        warnings.simplefilter("always")
        load_orchestrator_config_from_env()
        load_hindsight_config_from_env()
        load_perspective_config_from_env()
    assert orch.warnings() == []
    assert hind.warnings() == []
    assert persp.warnings() == []
    assert [w for w in caught if issubclass(w.category, DeprecationWarning)] == []


# --- (e) behavior unchanged -------------------------------------------------------------------


def _evals() -> list[HindsightEvaluation]:
    return [
        HindsightEvaluation(scenario_id="a", scores=HindsightScores(safety=0.2, helpfulness=0.1, honesty=0.1, total=0.15)),
        HindsightEvaluation(scenario_id="b", scores=HindsightScores(safety=-0.3, helpfulness=0.2, honesty=0.0, total=-0.1)),
    ]


def test_hindsight_recommendation_ignores_revise_threshold() -> None:
    results = []
    for threshold in (0.0, 0.9):
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            cfg = HindsightConfig(revise_threshold=threshold)
        agg = LLMHindsightEvaluator(policy=MagicMock(), config=cfg).aggregate(_evals())
        results.append((agg.recommendation, agg.expected_value))
    assert results[0] == results[1]


def test_ensemble_output_ignores_conservative_on_failure() -> None:
    outputs = []
    for flag in (True, False):
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            cfg = EnsembleConfig(max_retries=1, parallel_evaluation=False, conservative_on_failure=flag)
        ensemble = LLMPerspectiveEnsemble(
            FailingMockPolicyLLM(fail_count=999), config=cfg, perspectives=[PERSPECTIVES_BY_ID["user"]]
        )
        result = ensemble.evaluate(request="Test", response="Test response")
        outputs.append((result.evaluation_count, list(result.failed_perspectives)))
    assert outputs[0] == outputs[1]
    assert outputs[0][0] == 0
    assert "user" in outputs[0][1]


def _clean_state() -> DeliberationState:
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
    persp = type("P", (), {"perspective_id": "user", "approval_score": 0.9, "concerns": [], "suggestions": []})()
    return DeliberationState(cycle=1, critiques=[critique], perspectives=[persp])


def _critical_state() -> DeliberationState:
    # One critical violation, so the deprecated max_critical_violations would matter if it were wired in.
    # (early_exit_hindsight_threshold is probed by _hindsight_cycle2_state.)
    critique = type(
        "C",
        (),
        {
            "violations": ["v"],
            "violated_hard": False,
            "decision": "PROCEED",
            "revision_guidance": "",
            "has_critical_violations": True,
        },
    )()
    persp = type("P", (), {"perspective_id": "user", "approval_score": 0.9, "concerns": [], "suggestions": []})()
    hind = type("H", (), {"expected_value": 0.7, "recommendation": "proceed"})()
    return DeliberationState(cycle=1, critiques=[critique], perspectives=[persp], hindsight=hind)


def _hindsight_cycle2_state() -> DeliberationState:
    # Cycle 2, no violations, hindsight 0.7: under defaults CONTINUE (below min_hindsight_score 0.8,
    # convergence_evaluator.py:493). A reader of early_exit_hindsight_threshold next to it would
    # flip to CONVERGED at 0.6 but not at 0.99.
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


def test_convergence_decisions_ignore_deprecated_thresholds() -> None:
    decisions = []
    for mcv, eeh in ((0, 0.6), (5, 0.99)):
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            cfg = OrchestratorConfig(max_critical_violations=mcv, early_exit_hindsight_threshold=eeh)
        for state in (_clean_state(), _critical_state(), _hindsight_cycle2_state()):
            decision = ConvergenceEvaluator(cfg).determine_decision(state)
            snap = state._convergence_evaluation_snapshot
            decisions.append((decision, (snap or {}).get("convergence_reason_codes")))
    assert decisions[:3] == decisions[3:]


def test_hindsight_cycle2_state_stays_meaningful() -> None:
    state = _hindsight_cycle2_state()
    decision = ConvergenceEvaluator(OrchestratorConfig()).determine_decision(state)
    assert decision.name == "CONTINUE"
    assert state._convergence_evaluation_snapshot["convergence_reason_codes"] == ["CONTINUE_DEFAULT"]
