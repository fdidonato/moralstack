"""Pipeline error results (F6) - pins for the ``process()`` except chain.

Phase 2: the dedicated ``except FailSafeException`` clause in
``OrchestrationController.process()`` was unreachable (``except MoralStackError`` comes
first and ``FailSafeException`` subclasses it) and the exception is never raised. It was
removed; a ``FailSafeException`` is handled like any other ``MoralStackError``.

Later F6 phases extend this file.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from moralstack.orchestration import types as orch_types
from moralstack.orchestration.controller import OrchestrationController
from moralstack.orchestration.null_persistence import NullPersistence
from moralstack.orchestration.types import (
    FailSafeException,
    OrchestratorConfig,
    OrchestratorResult,
    ProcessedRequest,
    ResponseType,
)
from moralstack.utils.output_protection import OutputProtector

CONTROLLER_PATH = Path(__file__).resolve().parent.parent / "moralstack" / "orchestration" / "controller.py"


def _ctrl(*, safe: bool = True, ledger: Any = None, request: pytest.FixtureRequest) -> OrchestrationController:
    ctrl = OrchestrationController(
        config=OrchestratorConfig(safe_response_on_error=safe, enable_speculative_generation=False),
        policy=MagicMock(),
        risk_estimator=MagicMock(),
        critic=None,
        simulator=None,
        hindsight=None,
        perspectives=None,
        constitution_store=None,
        output_protector=OutputProtector(),
        protected_system_prompt="system",
        persistence=NullPersistence(),
        ledger=ledger,
    )
    close = getattr(ctrl._runner, "close", None)
    if close is not None:
        request.addfinalizer(close)
    return ctrl


def _fail(
    ctrl: OrchestrationController, monkeypatch: pytest.MonkeyPatch, exc: BaseException, *, conv: Any = None
) -> OrchestratorResult:
    monkeypatch.setattr(ctrl, "_estimate_risk", MagicMock(side_effect=exc))
    req = ProcessedRequest(prompt="hello", request_id="req-f6")
    if conv is None:
        return ctrl.process(req)
    conversation_id, turn_index = conv
    return ctrl.process(req, conversation_id=conversation_id, turn_index=turn_index)


def test_failsafe_exception_behaves_like_moralstack_error(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> None:
    ctrl = _ctrl(safe=True, request=request)
    result = _fail(ctrl, monkeypatch, FailSafeException("x"))

    assert result.response.metadata.triggered_principles == ["SYSTEM.ERROR"]
    assert result.error == "x"
    assert result.response.response_type == ResponseType.FULL_REFUSAL
    assert result.response.content == "[SYSTEM_ERROR]"
    assert result.total_cycles == 0


def _names_failsafe(node: ast.expr) -> bool:
    return (isinstance(node, ast.Name) and node.id == "FailSafeException") or (
        isinstance(node, ast.Attribute) and node.attr == "FailSafeException"
    )


def test_no_failsafe_except_clause_in_controller() -> None:
    tree = ast.parse(CONTROLLER_PATH.read_text(encoding="utf-8"))
    offenders: list[int] = []
    imports: list[int] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ExceptHandler) and node.type is not None:
            types = node.type.elts if isinstance(node.type, ast.Tuple) else [node.type]
            if any(_names_failsafe(t) for t in types):
                offenders.append(node.lineno)
        if isinstance(node, (ast.Import, ast.ImportFrom)) and any(
            a.name.split(".")[-1] == "FailSafeException" or a.asname == "FailSafeException" for a in node.names
        ):
            imports.append(node.lineno)
    assert offenders == []
    assert imports == []
    assert issubclass(orch_types.FailSafeException, orch_types.MoralStackError)
