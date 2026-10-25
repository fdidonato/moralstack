"""Exception text stays out of the diagnostic sinks of the deliberation modules (S1).

Rows/payloads carry the exception class name; the text goes to ``state.errors`` (kept) and a
guarded server-side log. An exception whose ``__str__`` raises must not escape any except body.
Offline and deterministic.
"""

from __future__ import annotations

import logging
import time
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest

from moralstack.orchestration.deliberation_runner import DeliberationRunner
from moralstack.orchestration.response_assembler import ResponseAssembler
from moralstack.orchestration.types import (
    DeliberationDependencies,
    DeliberationState,
    OrchestratorConfig,
    OrchestratorTimeoutError,
    ProcessedRequest,
)

_RUNNER_MOD = "moralstack.orchestration.deliberation_runner"
SECRET = "sk-SECRET boom"


class _BadStr(Exception):
    def __str__(self) -> str:
        raise ValueError("unprintable")


class _BadStrTimeout(OrchestratorTimeoutError):
    def __str__(self) -> str:
        raise ValueError("unprintable")


class _EmptyStr(Exception):
    def __str__(self) -> str:
        return ""


def _raiser(exc: BaseException) -> Any:
    def _raise(*_a: Any, **_k: Any) -> Any:
        raise exc

    return _raise


def _runner(
    *, critic: Any = None, simulator: Any = None, hindsight: Any = None, perspectives: Any = None
) -> DeliberationRunner:
    deps = DeliberationDependencies(
        policy=MagicMock(),
        critic=critic,
        simulator=simulator,
        hindsight=hindsight,
        perspectives=perspectives,
        constitution_store=None,
        output_protector=MagicMock(),
    )
    return DeliberationRunner(OrchestratorConfig(timeout_ms=1000), deps, "protected", None, ResponseAssembler(policy=None))


def _request() -> ProcessedRequest:
    return ProcessedRequest(prompt="the prompt", request_id="req-sinks")


def _state() -> DeliberationState:
    state = DeliberationState(cycle=1)
    state.draft_response = "the draft"
    return state


@pytest.fixture
def diags(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    captured: list[dict[str, Any]] = []
    monkeypatch.setattr(f"{_RUNNER_MOD}.record_llm_call", lambda _l, diag, _p: captured.append(diag))
    return captured


@pytest.fixture
def debug_log(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    m = MagicMock()
    monkeypatch.setattr(f"{_RUNNER_MOD}.orch_debug_log", m)
    return m


def _run_critique(exc: BaseException) -> DeliberationState:
    critic = SimpleNamespace(critique=_raiser(exc), config=MagicMock(top_k_principles=2))
    state = _state()
    _runner(critic=critic)._critique(state, _request(), constitution=SimpleNamespace(name="c"))
    return state


@pytest.mark.parametrize("exc_cls", [RuntimeError, ValueError, OrchestratorTimeoutError])
def test_critique_row_carries_class_name_only(diags: list[dict[str, Any]], exc_cls: type[Exception]) -> None:
    state = _run_critique(exc_cls(SECRET))
    assert len(diags) == 1
    assert diags[0]["action"] == "critique (ERROR)"
    assert diags[0]["response"] == f"ERROR: {exc_cls.__name__}"
    assert SECRET not in repr(diags)
    assert state.errors == [f"Critique error: {SECRET}"]
    assert state._critique_outcome == "failed"


def test_critique_unprintable_exception_does_not_escape(diags: list[dict[str, Any]]) -> None:
    state = _run_critique(_BadStr())
    assert diags[0]["response"] == "ERROR: _BadStr"
    assert state.errors == ["Critique error: _BadStr"]
    assert state._critique_outcome == "failed"


def _module(exc: BaseException) -> Any:
    return SimpleNamespace(simulate=_raiser(exc), evaluate=_raiser(exc))


_MODULES = [
    ("Simulation", "simulator", "_simulate", "simulate (ERROR)"),
    ("Perspectives", "perspectives", "_evaluate_perspectives", "evaluate (ERROR)"),
]


def _call_module(kind: str, method: str, exc: BaseException, **call_kw: Any) -> DeliberationState:
    runner = _runner(**{kind: _module(exc)})
    state = _state()
    getattr(runner, method)(state, _request(), **call_kw)
    return state


@pytest.mark.parametrize(("label", "kind", "method", "action"), _MODULES)
def test_simulate_perspectives_row_clean_and_class_only(
    diags: list[dict[str, Any]], label: str, kind: str, method: str, action: str
) -> None:
    state = _call_module(kind, method, RuntimeError(SECRET))
    assert [d["action"] for d in diags] == [action]
    assert diags[0]["response"] == "ERROR: RuntimeError"
    assert SECRET not in repr(diags)
    assert state.errors == [f"{label} error: {SECRET}"]


@pytest.mark.parametrize(("label", "kind", "method", "action"), _MODULES)
def test_simulate_perspectives_unprintable_does_not_escape(
    diags: list[dict[str, Any]], label: str, kind: str, method: str, action: str
) -> None:
    state = _call_module(kind, method, _BadStr())
    assert diags[0]["response"] == "ERROR: _BadStr"
    assert state.errors == [f"{label} error: _BadStr"]


@pytest.mark.parametrize(("label", "kind", "method", "action"), _MODULES)
def test_simulate_perspectives_warning_with_exc_info(
    diags: list[dict[str, Any]], caplog: pytest.LogCaptureFixture, label: str, kind: str, method: str, action: str
) -> None:
    with caplog.at_level(logging.DEBUG, logger=_RUNNER_MOD):
        _call_module(kind, method, RuntimeError(SECRET))
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING and "error_type=RuntimeError" in r.getMessage()]
    assert len(warnings) == 1
    assert warnings[0].exc_info is not None
    assert SECRET not in warnings[0].getMessage()


@pytest.mark.parametrize(("label", "kind", "method", "action"), _MODULES)
@pytest.mark.parametrize("designed_guard", [False, True])
def test_simulate_perspectives_timeout_is_info_without_traceback(
    diags: list[dict[str, Any]],
    caplog: pytest.LogCaptureFixture,
    label: str,
    kind: str,
    method: str,
    action: str,
    designed_guard: bool,
) -> None:
    kw: dict[str, Any] = {"start_time": time.time() - 1000} if designed_guard else {}
    with caplog.at_level(logging.DEBUG, logger=_RUNNER_MOD):
        _call_module(kind, method, OrchestratorTimeoutError("deadline exceeded"), **kw)
    relevant = [r for r in caplog.records if "error_type=OrchestratorTimeoutError" in r.getMessage()]
    assert len(relevant) == 1
    assert relevant[0].levelno == logging.INFO
    assert relevant[0].exc_info is None
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]


def _hindsight_state(exc: BaseException, **kw: Any) -> DeliberationState:
    return _call_module("hindsight", "_evaluate_hindsight", exc, **kw)


def _outcomes(debug_log: MagicMock) -> list[dict[str, Any]]:
    return [c.args[2] for c in debug_log.call_args_list if c.args[2].get("component") == "hindsight_diagnostic"]


def test_hindsight_row_and_payload_are_clean(diags: list[dict[str, Any]], debug_log: MagicMock) -> None:
    state = _hindsight_state(RuntimeError(SECRET))
    assert diags[0]["action"] == "evaluate (ERROR)"
    assert diags[0]["response"] == "ERROR: RuntimeError"
    assert state.errors == [f"Hindsight error: {SECRET}"]
    payloads = _outcomes(debug_log)
    assert len(payloads) == 1
    assert set(payloads[0]) == {"component", "outcome", "persist_mode", "error_type", "state_cycle"}
    assert payloads[0]["error_type"] == "RuntimeError"
    assert SECRET not in repr(debug_log.call_args_list)
    assert SECRET not in repr(diags)


@pytest.mark.parametrize(
    ("exc", "guard", "expected"),
    [
        (OrchestratorTimeoutError("deadline exceeded"), True, "evaluate_aborted_timeout_guard"),
        (OrchestratorTimeoutError("deadline exceeded"), False, "evaluate_failed_orchestrator_timeout"),
        (_BadStrTimeout(), False, "evaluate_failed_orchestrator_timeout"),
        (RuntimeError(SECRET), False, "evaluate_failed"),
        (_BadStr(), False, "evaluate_failed"),
    ],
)
def test_hindsight_routing(
    diags: list[dict[str, Any]], debug_log: MagicMock, exc: BaseException, guard: bool, expected: str
) -> None:
    kw: dict[str, Any] = {"start_time": time.time() - 1000} if guard else {}
    _hindsight_state(exc, **kw)
    payloads = _outcomes(debug_log)
    assert [p["outcome"] for p in payloads] == [expected]
    assert set(payloads[0]) == {"component", "outcome", "persist_mode", "error_type", "state_cycle"}
    assert len(diags) == 1


def test_empty_message_exception_renders_class_name(diags: list[dict[str, Any]]) -> None:
    state = _call_module("simulator", "_simulate", _EmptyStr())
    assert diags[0]["response"] == "ERROR: _EmptyStr"
    assert state.errors == ["Simulation error: _EmptyStr"]
    assert _run_critique(_EmptyStr()).errors == ["Critique error: _EmptyStr"]


def test_chained_cause_never_reaches_row(diags: list[dict[str, Any]]) -> None:
    def _chained(*_a: Any, **_k: Any) -> Any:
        try:
            raise RuntimeError(SECRET)
        except RuntimeError as inner:
            raise ValueError("outer") from inner

    runner = _runner(simulator=SimpleNamespace(simulate=_chained))
    runner._simulate(_state(), _request())
    assert diags[0]["response"] == "ERROR: ValueError"
    assert SECRET not in repr(diags)
