"""Pipeline error results (F6) - pins for the ``process()`` except chain.

Phase 2: the dedicated ``except FailSafeException`` clause in
``OrchestrationController.process()`` was unreachable (``except MoralStackError`` comes
first and ``FailSafeException`` subclasses it) and the exception is never raised. It was
removed; a ``FailSafeException`` is handled like any other ``MoralStackError``.

Phase 3: ``MoralStackError`` / ``OrchestratorTimeoutError`` results are a governed
fail-closed REFUSE (``system_error`` / ``system_timeout``, ``ERROR_PATH``, fixed sentinel
content, one WARNING, never cached in the ledger).

Later F6 phases extend this file.
"""

from __future__ import annotations

import ast
import dataclasses
import json
import logging
import time
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from moralstack.cli.mocks import (
    MockConstitutionStore,
    MockCritic,
    MockHindsight,
    MockPerspectives,
    MockPolicy,
    MockRiskEstimator,
    MockSimulator,
)
from moralstack.orchestration import types as orch_types
from moralstack.orchestration.controller import OrchestrationController
from moralstack.orchestration.conversation_state import ConversationGovernanceState
from moralstack.orchestration.delivery import GOVERNED_REFUSAL_SOURCE, finalize_delivery
from moralstack.orchestration.ledger import SemanticDecisionLedger
from moralstack.orchestration.ledger_storage import InMemoryLedgerStorage
from moralstack.orchestration.null_persistence import NullPersistence
from moralstack.orchestration.process_context import ProcessCallContext
from moralstack.orchestration.types import (
    CritiqueError,
    FailSafeException,
    FinalResponse,
    GenerationError,
    MoralStackError,
    OrchestratorConfig,
    OrchestratorResult,
    OrchestratorTimeoutError,
    ProcessedRequest,
    ResponseMetadata,
    ResponseType,
    RiskEstimationError,
    SimulationError,
)
from moralstack.runtime.orchestrator import Orchestrator, create_orchestrator
from moralstack.utils.output_protection import OutputProtector

CONTROLLER_PATH = Path(__file__).resolve().parent.parent / "moralstack" / "orchestration" / "controller.py"


def _ctrl(
    *,
    safe: bool = True,
    ledger: Any = None,
    request: pytest.FixtureRequest,
    config: OrchestratorConfig | None = None,
) -> OrchestrationController:
    ctrl = OrchestrationController(
        config=config or OrchestratorConfig(safe_response_on_error=safe, enable_speculative_generation=False),
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


# =============================================================================
# Phase 3: handlers produce a governed fail-closed REFUSE
# =============================================================================

SECRET = "provider said: secret-ish body"
MS_FAMILY = [GenerationError, RiskEstimationError, CritiqueError, SimulationError, MoralStackError, FailSafeException]
TIMEOUT_TEXT = "Timeout after 601000ms (max: 600000ms)"
_CLOSED_SET = {"NORMAL_COMPLETE", "SAFE_COMPLETE", "REFUSE"}
_CONVS = [None, ("c1", 1)]


class _ListHandler(logging.Handler):
    def __init__(self) -> None:
        super().__init__(logging.DEBUG)
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)


def _capture_logger(monkeypatch: pytest.MonkeyPatch, target: str, name: str) -> _ListHandler:
    lg = logging.getLogger(f"f6.{name}")
    lg.handlers.clear()
    lg.propagate = False
    lg.setLevel(logging.DEBUG)
    handler = _ListHandler()
    lg.addHandler(handler)
    monkeypatch.setattr(target, lg)
    return handler


@pytest.fixture
def ctrl_log(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> _ListHandler:
    return _capture_logger(monkeypatch, "moralstack.orchestration.controller._LOG", request.node.name)


def _failure_warnings(handler: _ListHandler) -> list[logging.LogRecord]:
    return [
        r for r in handler.records if r.levelno == logging.WARNING and r.getMessage().startswith("pipeline failed closed")
    ]


class _StubEmbedder:
    def embed(self, text: str) -> list[float]:
        return [1.0, 0.0]


def _new_ledger() -> SemanticDecisionLedger:
    return SemanticDecisionLedger(_StubEmbedder(), InMemoryLedgerStorage())


def _store_size(ctrl: Any, result: Any) -> int:
    ledger = _new_ledger()
    ctrl._ledger = ledger
    ctrl._maybe_store_in_ledger(
        request=ProcessedRequest(prompt="p", request_id="req-c"),
        result=result,
        call_ctx=ProcessCallContext(conversation_id="c1", turn_index=1),
    )
    return ledger._storage.size()


def _ledger_result(flip_reason: str, final_action: str = "REFUSE") -> OrchestratorResult:
    metadata = ResponseMetadata(final_action=final_action, risk_score=0.1, hard_violation_flip_reason=flip_reason)
    return OrchestratorResult(
        response=FinalResponse(content="x", response_type=ResponseType.DIRECT, metadata=metadata),
        execution_trace=MagicMock(),
    )


def _meta_json(meta: Any) -> str:
    return json.dumps(dataclasses.asdict(meta), default=str)


def _assert_governed_refuse(
    result: OrchestratorResult, *, principle: str, reason: str, sentinel: str, error_text: str
) -> None:
    meta = result.response.metadata
    assert meta.final_action == "REFUSE"
    assert meta.hard_violation_flip_reason == reason
    assert meta.reason_codes == [reason]
    assert meta.triggered_principles == [principle]
    assert meta.original_final_action == ""
    assert meta.path == "ERROR_PATH"
    assert result.path == "ERROR_PATH"
    assert result.path_taken == "error"
    assert result.response.response_type == ResponseType.FULL_REFUSAL
    assert result.response.content == sentinel
    assert meta.risk_score == 1.0
    assert result.total_cycles == 0
    assert result.converged is False
    assert meta.decision_trace_id == result.request_id == "req-f6"
    assert isinstance(meta.processing_time_ms, int) and meta.processing_time_ms >= 0
    assert result.error == error_text
    assert result.errors == [error_text]


@pytest.mark.parametrize("conv", _CONVS)
@pytest.mark.parametrize("safe", [True, False])
@pytest.mark.parametrize("exc_cls", MS_FAMILY)
def test_p1_handler_result_is_governed_refuse(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch, exc_cls: type, safe: bool, conv: Any
) -> None:
    exc = exc_cls(f"Generation failed: {SECRET}")
    result = _fail(_ctrl(safe=safe, request=request), monkeypatch, exc, conv=conv)
    _assert_governed_refuse(
        result, principle="SYSTEM.ERROR", reason="system_error", sentinel="[SYSTEM_ERROR]", error_text=str(exc)
    )


@pytest.mark.parametrize("conv", _CONVS)
@pytest.mark.parametrize("safe", [True, False])
def test_p2_timeout_result_is_governed_refuse(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch, safe: bool, conv: Any
) -> None:
    result = _fail(_ctrl(safe=safe, request=request), monkeypatch, OrchestratorTimeoutError(TIMEOUT_TEXT), conv=conv)
    _assert_governed_refuse(
        result, principle="SYSTEM.TIMEOUT", reason="system_timeout", sentinel="[SYSTEM_TIMEOUT]", error_text=TIMEOUT_TEXT
    )


@pytest.mark.parametrize("safe", [True, False])
@pytest.mark.parametrize(
    "exc_cls,message,fragment,sentinel",
    [
        (GenerationError, f"Generation failed: {SECRET}", "secret-ish body", "[SYSTEM_ERROR]"),
        (RiskEstimationError, f"Risk estimation failed: {SECRET}", "secret-ish body", "[SYSTEM_ERROR]"),
        (OrchestratorTimeoutError, TIMEOUT_TEXT, "601000", "[SYSTEM_TIMEOUT]"),
    ],
)
def test_p3_delivered_content_never_has_exception_text(
    request: pytest.FixtureRequest,
    monkeypatch: pytest.MonkeyPatch,
    exc_cls: type,
    message: str,
    fragment: str,
    sentinel: str,
    safe: bool,
) -> None:
    result = _fail(_ctrl(safe=safe, request=request), monkeypatch, exc_cls(message))
    assert result.response.content == sentinel
    assert fragment not in finalize_delivery(result).text
    assert fragment not in _meta_json(result.response.metadata)
    assert fragment in (result.error or "")


@pytest.mark.parametrize(
    "exc,sentinel",
    [
        (GenerationError("g"), "[SYSTEM_ERROR]"),
        (OrchestratorTimeoutError(TIMEOUT_TEXT), "[SYSTEM_TIMEOUT]"),
    ],
)
def test_p4_handler_result_delivers_as_refuse(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch, exc: BaseException, sentinel: str
) -> None:
    delivery = finalize_delivery(_fail(_ctrl(request=request), monkeypatch, exc))
    assert delivery.final_action == "REFUSE"
    assert delivery.finish_reason == "content_filter"
    assert delivery.final_text_source == GOVERNED_REFUSAL_SOURCE
    assert delivery.text == sentinel
    assert delivery.original_final_action == "REFUSE"
    assert delivery.empty_governed_content is False


@pytest.mark.parametrize("exc", [GenerationError("g"), OrchestratorTimeoutError(TIMEOUT_TEXT)])
def test_p5_conversation_state_records_refuse_error_path(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch, exc: BaseException
) -> None:
    result = _fail(_ctrl(request=request), monkeypatch, exc, conv=("c1", 1))
    state = result.conversation_governance_state_out
    assert state is not None
    summary = state.turn_decisions_summary[-1]
    assert summary.final_action == "REFUSE"
    assert summary.winning_rule == "ERROR_PATH"
    assert summary.risk_score == 1.0
    assert state.last_governance_posture != "ESCALATED"
    assert result.conversation_id == "c1"
    assert result.turn_index == 1


@pytest.mark.parametrize("exc_cls", MS_FAMILY + [OrchestratorTimeoutError])
def test_p6_handler_results_are_never_cached(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch, exc_cls: type
) -> None:
    ledger = _new_ledger()
    _fail(_ctrl(ledger=ledger, request=request), monkeypatch, exc_cls("x"), conv=("c1", 1))
    assert ledger._storage.size() == 0


@pytest.mark.parametrize("code", ["system_error", "system_timeout"])
def test_p6_direct_ledger_skips_system_codes(request: pytest.FixtureRequest, code: str) -> None:
    assert _store_size(_ctrl(request=request), _ledger_result(code)) == 0


@pytest.mark.parametrize(
    "exc,reason,error_type",
    [
        (GenerationError("g"), "system_error", "GenerationError"),
        (OrchestratorTimeoutError(TIMEOUT_TEXT), "system_timeout", "OrchestratorTimeoutError"),
    ],
)
def test_p7a_failure_logs_one_warning_on_controller_logger(
    request: pytest.FixtureRequest,
    monkeypatch: pytest.MonkeyPatch,
    ctrl_log: _ListHandler,
    exc: BaseException,
    reason: str,
    error_type: str,
) -> None:
    _fail(_ctrl(request=request), monkeypatch, exc)
    warnings = _failure_warnings(ctrl_log)
    assert len(warnings) == 1
    message = warnings[0].getMessage()
    assert "request_id=req-f6" in message
    assert f"reason={reason}" in message
    assert f"error_type={error_type}" in message
    assert not [r for r in ctrl_log.records if r.levelno >= logging.ERROR]


def test_p7a_log_truncates_error_text(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch, ctrl_log: _ListHandler
) -> None:
    _fail(_ctrl(request=request), monkeypatch, GenerationError("X" * 300))
    message = _failure_warnings(ctrl_log)[0].getMessage()
    assert "X" * 200 in message
    assert "X" * 201 not in message


def test_p7a_log_is_safe_with_format_characters(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch, ctrl_log: _ListHandler
) -> None:
    _fail(_ctrl(request=request), monkeypatch, GenerationError("100%s {} %d boom"))
    warnings = _failure_warnings(ctrl_log)
    assert len(warnings) == 1
    assert "100%s {} %d boom" in warnings[0].getMessage()


def test_p7b_logging_failure_never_breaks_the_request(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch, ctrl_log: _ListHandler
) -> None:
    def _boom(*_a: Any, **_k: Any) -> None:
        raise RuntimeError("logging down")

    monkeypatch.setattr(logging.getLogger("f6." + request.node.name), "warning", _boom)
    result = _fail(_ctrl(request=request), monkeypatch, GenerationError("g"))
    assert result.response.response_type == ResponseType.FULL_REFUSAL
    assert result.response.content == "[SYSTEM_ERROR]"


def test_p9_for_system_error_factory_contract() -> None:
    meta = ResponseMetadata.for_system_error(7, "req-9")
    assert meta.final_action == "REFUSE"
    assert meta.path == "ERROR_PATH"
    assert meta.reason_codes == ["system_error"]
    assert meta.hard_violation_flip_reason == "system_error"
    assert meta.triggered_principles == ["SYSTEM.ERROR"]
    assert meta.risk_score == 1.0
    assert meta.decision_trace_id == "req-9"
    assert meta.original_final_action == ""
    timeout = ResponseMetadata.for_system_error(7, "req-9", principle="SYSTEM.TIMEOUT")
    assert timeout.hard_violation_flip_reason == "system_timeout"
    override = ResponseMetadata.for_system_error(7, "req-9", principle="SYSTEM.TIMEOUT", fail_closed_reason="system_error")
    assert override.reason_codes == ["system_error"]
    assert timeout.reason_codes == ["system_timeout"]
    assert timeout.hard_violation_flip_reason == "system_timeout"
    assert timeout.triggered_principles == ["SYSTEM.TIMEOUT"]


@pytest.mark.parametrize(
    "exc,principle,reason",
    [
        (GenerationError(f"Generation failed: {SECRET}"), "SYSTEM.ERROR", "system_error"),
        (OrchestratorTimeoutError(f"{TIMEOUT_TEXT} {SECRET}"), "SYSTEM.TIMEOUT", "system_timeout"),
    ],
)
def test_p10_audit_meta_for_error_result(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch, exc: BaseException, principle: str, reason: str
) -> None:
    from moralstack.observability.governance_audit import build_request_meta_from_result

    meta = build_request_meta_from_result(_fail(_ctrl(request=request), monkeypatch, exc))
    assert meta["final_action"] == "REFUSE"
    assert meta["path"] == "ERROR_PATH"
    assert meta["path_taken"] == "error"
    assert meta["triggered_principles"] == [principle]
    assert meta["reason_codes"] == [reason]
    assert "secret-ish body" not in json.dumps(meta, default=str)


def test_p12_no_final_trace_row_on_error_path(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> None:
    captured: list[Any] = []
    for target in (
        "moralstack.orchestration.controller.append_decision_trace",
        "moralstack.orchestration.deliberation_runner.append_decision_trace",
        "moralstack.orchestration.decision_service.append_decision_trace",
        "moralstack.orchestration.persistence_helpers.async_persist_decision_trace",
        "moralstack.orchestration.persistence_helpers.record_decision_trace",
    ):
        monkeypatch.setattr(target, captured.append, raising=False)
    _fail(_ctrl(request=request), monkeypatch, GenerationError("g"))
    _fail(_ctrl(request=request), monkeypatch, OrchestratorTimeoutError(TIMEOUT_TEXT))
    assert [t for t in captured if str(getattr(t, "stage", "")).strip().upper() == "FINAL"] == []


def test_p13_empty_message_error_still_reported(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> None:
    result = _fail(_ctrl(request=request), monkeypatch, GenerationError(""))
    assert result.error == "GenerationError"
    assert result.errors == ["GenerationError"]
    assert result.response.metadata.final_action == "REFUSE"


class _UnprintableError(GenerationError):
    def __str__(self) -> str:
        raise RuntimeError("no str for you")


def test_p14_unprintable_exception_still_refuses(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> None:
    result = _fail(_ctrl(request=request), monkeypatch, _UnprintableError("x"))
    assert result.response.metadata.final_action == "REFUSE"
    assert result.response.metadata.hard_violation_flip_reason == "system_error"
    assert result.error == "_UnprintableError"
    assert result.errors == ["_UnprintableError"]


def test_p15_handlers_make_no_llm_call(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> None:
    ctrl = _ctrl(request=request)
    req = ProcessedRequest(prompt="hello", request_id="req-f6")
    ctrl._handle_error(req, GenerationError("g"), time.time())
    ctrl._handle_timeout(req, OrchestratorTimeoutError(TIMEOUT_TEXT), time.time())
    _fail(ctrl, monkeypatch, GenerationError("g"))
    assert ctrl.policy.mock_calls == []


ENV_SAFE = "MORALSTACK_ORCHESTRATOR_SAFE_RESPONSE_ON_ERROR"


def test_p16a_unsafe_env_flag_is_a_noop_for_content(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> None:
    from moralstack.orchestration.config_loader import load_orchestrator_config_from_env

    monkeypatch.setenv(ENV_SAFE, "false")
    cfg = load_orchestrator_config_from_env()
    assert cfg.safe_response_on_error is False
    cfg = dataclasses.replace(cfg, enable_speculative_generation=False)
    result = _fail(_ctrl(request=request, config=cfg), monkeypatch, GenerationError(f"Generation failed: {SECRET}"))
    assert result.response.content == "[SYSTEM_ERROR]"
    assert "secret-ish body" not in finalize_delivery(result).text


def test_p16b_unsafe_env_flag_warns_at_load(monkeypatch: pytest.MonkeyPatch) -> None:
    from moralstack.orchestration.config_loader import load_orchestrator_config_from_env

    handler = _capture_logger(monkeypatch, "moralstack.orchestration.config_loader._LOG", "p16b")
    monkeypatch.setenv(ENV_SAFE, "false")
    load_orchestrator_config_from_env()
    warnings = [r for r in handler.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert "MORALSTACK_ORCHESTRATOR_SAFE_RESPONSE_ON_ERROR=false is ignored" in warnings[0].getMessage()
    assert "(PROJECT_SPEC §5.7)" in warnings[0].getMessage()


def test_p16c_default_env_does_not_warn(monkeypatch: pytest.MonkeyPatch) -> None:
    from moralstack.orchestration.config_loader import load_orchestrator_config_from_env

    handler = _capture_logger(monkeypatch, "moralstack.orchestration.config_loader._LOG", "p16c")
    monkeypatch.delenv(ENV_SAFE, raising=False)
    cfg = load_orchestrator_config_from_env()
    assert cfg.safe_response_on_error is True
    assert [r for r in handler.records if r.levelno >= logging.WARNING] == []


# --- U1: UI detection of the new row shape ------------------------------------


def test_u1_ui_detector_accepts_new_error_shape(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> None:
    pytest.importorskip("fastapi")
    from moralstack.observability.governance_audit import build_request_meta_from_result
    from moralstack.ui.app import _detect_pipeline_failure

    error_meta = build_request_meta_from_result(_fail(_ctrl(request=request), monkeypatch, GenerationError("g")))
    assert _detect_pipeline_failure([], error_meta) is True
    assert _detect_pipeline_failure([{"stage": "FINAL"}], error_meta) is False
    timeout_meta = build_request_meta_from_result(
        _fail(_ctrl(request=request), monkeypatch, OrchestratorTimeoutError(TIMEOUT_TEXT))
    )
    # SYSTEM.TIMEOUT is detected since the shared predicate (F6 follow-up C5).
    assert _detect_pipeline_failure([], timeout_meta) is True
    assert _detect_pipeline_failure([{"stage": "FINAL"}], timeout_meta) is False


def test_u2_ui_detector_accepts_fail_safe_shape(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> None:
    pytest.importorskip("fastapi")
    from moralstack.observability.governance_audit import build_request_meta_from_result
    from moralstack.ui.app import _detect_pipeline_failure

    meta = build_request_meta_from_result(_fail(_ctrl(request=request), monkeypatch, ValueError("x")))
    assert "SYSTEM.FAIL_SAFE" in meta["triggered_principles"]
    assert _detect_pipeline_failure([], meta) is True
    assert _detect_pipeline_failure([{"stage": "FINAL"}], meta) is False


# --- Integration: real Orchestrator facade ------------------------------------


class _RaisingEstimator(MockRiskEstimator):
    def estimate(self, prompt: str, **kwargs: Any) -> Any:
        raise RuntimeError(SECRET)


class _FlakyEstimator(MockRiskEstimator):
    healthy = False

    def estimate(self, prompt: str, **kwargs: Any) -> Any:
        if not self.healthy:
            raise RuntimeError(SECRET)
        return super().estimate(prompt, **kwargs)


class _FailingPolicy(MockPolicy):
    def generate(self, *args: Any, **kwargs: Any) -> Any:
        raise RuntimeError(SECRET)


def _orchestrator(*, safe: bool = True, estimator: Any = None, policy: Any = None, ledger: Any = None) -> Orchestrator:
    return Orchestrator(
        config=OrchestratorConfig(
            max_deliberation_cycles=1,
            timeout_ms=60_000,
            safe_response_on_error=safe,
            enable_speculative_generation=False,
        ),
        policy=policy or MockPolicy(),
        risk_estimator=estimator or MockRiskEstimator(),
        critic=MockCritic(),
        simulator=MockSimulator(),
        hindsight=MockHindsight(),
        perspectives=MockPerspectives(),
        constitution_store=MockConstitutionStore(),
        ledger=ledger,
    )


@pytest.mark.parametrize("safe", [True, False])
def test_i1_natural_failures_end_to_end(safe: bool) -> None:
    pytest.importorskip("fastapi")
    pytest.importorskip("httpx")
    from fastapi.testclient import TestClient

    from moralstack.sdk.config import GovernanceConfig
    from moralstack.server.proxy import create_app

    orch = _orchestrator(safe=safe, estimator=_RaisingEstimator())
    direct = orch.process(ProcessedRequest(prompt="hello", request_id="req-i1"))
    assert direct.response.metadata.final_action == "REFUSE"
    assert direct.response.metadata.hard_violation_flip_reason == "system_error"

    proxy_orch = MagicMock()
    proxy_orch.process = orch.process
    mock_openai = MagicMock()
    app = create_app(openai_client=mock_openai, orchestrator=proxy_orch, config=GovernanceConfig())
    response = TestClient(app).post(
        "/v1/chat/completions", json={"model": "gpt-4o", "messages": [{"role": "user", "content": "hello"}]}
    )
    assert response.status_code == 200
    choice = response.json()["choices"][0]
    assert choice["message"]["content"] == "[SYSTEM_ERROR]"
    assert choice["finish_reason"] == "content_filter"
    assert response.headers["X-Moralstack-Decision"] == "REFUSE"
    assert response.headers["X-Moralstack-Path"] == "ERROR_PATH"
    assert "secret-ish body" not in response.text
    assert "secret-ish body" not in json.dumps(dict(response.headers))
    mock_openai.chat.completions.create.assert_not_called()


def test_i3_process_never_returns_unlabelled_result(monkeypatch: pytest.MonkeyPatch) -> None:
    # (a) estimator raises
    result_a = _orchestrator(estimator=_RaisingEstimator()).process(ProcessedRequest(prompt="hello", request_id="req-a"))
    # (b) policy generation raises: the exact path is a hypothesis, assert only the invariant
    result_b = _orchestrator(policy=_FailingPolicy()).process(ProcessedRequest(prompt="hello", request_id="req-b"))
    # (c) timeout
    orch_c = _orchestrator()
    monkeypatch.setattr(orch_c._controller, "_estimate_risk", MagicMock(side_effect=OrchestratorTimeoutError(TIMEOUT_TEXT)))
    result_c = orch_c.process(ProcessedRequest(prompt="hello", request_id="req-c"))
    for result in (result_a, result_b, result_c):
        assert result.response.metadata.final_action in _CLOSED_SET
        delivery = finalize_delivery(result)
        assert "secret-ish body" not in delivery.text
        assert "601000" not in delivery.text
    for result in (result_a, result_c):
        assert finalize_delivery(result).final_action == "REFUSE"


def test_i2_transient_error_then_next_turn_is_regoverned() -> None:
    estimator = _FlakyEstimator()
    ledger = _new_ledger()
    orch = create_orchestrator(
        policy=MockPolicy(),
        risk_estimator=estimator,
        critic=MockCritic(),
        simulator=MockSimulator(),
        hindsight=MockHindsight(),
        perspectives=MockPerspectives(),
        constitution_store=MockConstitutionStore(),
        max_cycles=1,
        timeout_ms=60_000,
        ledger=ledger,
    )
    prompt = "Tell me about depression medication"
    turn1 = orch.process(ProcessedRequest(prompt=prompt, request_id="req-e1"), conversation_id="c1", turn_index=1)
    assert turn1.error
    assert ledger._storage.size() == 0
    estimator.healthy = True
    turn2 = orch.process(ProcessedRequest(prompt=prompt, request_id="req-e2"), conversation_id="c1", turn_index=2)
    assert turn2.response.metadata.final_action != "REFUSE"
    assert turn2.response.metadata.hard_violation_flip_reason == ""


# =============================================================================
# Phase 4: the generic-except / ensure-fallback fail-safe REFUSE is transient
# =============================================================================

GENERIC_EXC = [ValueError, RuntimeError, AssertionError, KeyError, TypeError]


def test_p8a_for_fail_safe_carries_system_error_flip_reason() -> None:
    for meta in (ResponseMetadata.for_fail_safe(3), FinalResponse.safe_default(3).metadata):
        assert meta.hard_violation_flip_reason == "system_error"
        assert meta.reason_codes == ["system_error"]
        assert meta.final_action == "REFUSE"
        assert meta.triggered_principles == ["SYSTEM.FAIL_SAFE"]
        assert meta.path == "DELIBERATIVE_PATH"
        assert meta.processing_time_ms == 3


@pytest.mark.parametrize("exc_cls", GENERIC_EXC)
def test_p8b_generic_exception_refuse_is_not_cached(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch, exc_cls: type
) -> None:
    ledger = _new_ledger()
    result = _fail(_ctrl(ledger=ledger, request=request), monkeypatch, exc_cls("unexpected"), conv=("c1", 1))
    assert result.response.metadata.final_action == "REFUSE"
    assert ledger._storage.size() == 0


def test_p8c_generic_except_logs_pipeline_warning(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch, ctrl_log: _ListHandler
) -> None:
    _fail(_ctrl(request=request), monkeypatch, ValueError("unexpected"))
    warnings = _failure_warnings(ctrl_log)
    assert len(warnings) == 1
    message = warnings[0].getMessage()
    assert "request_id=req-f6" in message
    assert "reason=system_error" in message
    assert "error_type=ValueError" in message


def test_p8d_ensure_fallback_refuse_is_not_cached(request: pytest.FixtureRequest) -> None:
    ctrl = _ctrl(request=request)
    bad = FinalResponse(content="x", response_type=None, metadata=ResponseMetadata())  # type: ignore[arg-type]
    out = ctrl._diagnostics.ensure_final_action_and_return(
        OrchestratorResult(response=bad, execution_trace=MagicMock()),
        ProcessedRequest(prompt="p", request_id="req-d"),
        time.time(),
        None,
        attach_trace_fn=lambda res, _req: res,
    )
    assert _store_size(ctrl, out) == 0
    meta = out.response.metadata
    assert out.error == "final_action_undefined"
    assert meta.final_action == "REFUSE"
    assert meta.hard_violation_flip_reason == "system_error"


def test_p8c_generic_except_logging_failure_never_breaks_the_request(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch, ctrl_log: _ListHandler
) -> None:
    def _boom(*_a: Any, **_k: Any) -> None:
        raise RuntimeError("logging down")

    monkeypatch.setattr(logging.getLogger("f6." + request.node.name), "warning", _boom)
    result = _fail(_ctrl(request=request), monkeypatch, ValueError("unexpected"))
    assert result.response.metadata.final_action == "REFUSE"
    assert result.response.metadata.triggered_principles == ["SYSTEM.FAIL_SAFE"]
    assert result.response.content == "[SYSTEM_ERROR]"


@pytest.mark.parametrize("safe", [True, False])
@pytest.mark.parametrize("exc_cls", GENERIC_EXC)
def test_p11_generic_except_shape_unchanged(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch, exc_cls: type, safe: bool
) -> None:
    exc = exc_cls("unexpected")
    result = _fail(_ctrl(safe=safe, request=request), monkeypatch, exc)
    meta = result.response.metadata
    assert meta.final_action == "REFUSE"
    assert meta.triggered_principles == ["SYSTEM.FAIL_SAFE"]
    assert result.path == "ERROR_PATH"
    assert result.path_taken == "error"
    assert result.response.content == "[SYSTEM_ERROR]"
    assert result.error == str(exc)


@pytest.mark.parametrize("exc_cls", GENERIC_EXC)
def test_p11_generic_except_reason_code_is_system_error(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch, exc_cls: type
) -> None:
    result = _fail(_ctrl(request=request), monkeypatch, exc_cls("unexpected"))
    assert result.response.metadata.reason_codes == ["system_error"]


def test_i2_p3_transient_generic_failure_then_next_turn_is_regoverned(monkeypatch: pytest.MonkeyPatch) -> None:
    ledger = _new_ledger()
    orch = create_orchestrator(
        policy=MockPolicy(),
        risk_estimator=MockRiskEstimator(),
        critic=MockCritic(),
        simulator=MockSimulator(),
        hindsight=MockHindsight(),
        perspectives=MockPerspectives(),
        constitution_store=MockConstitutionStore(),
        max_cycles=1,
        timeout_ms=60_000,
        ledger=ledger,
    )
    controller = orch._controller
    # Failure injected AFTER the ledger lookup: every _route_* instance method raises.
    with monkeypatch.context() as patch:
        for name in [n for n in dir(controller) if n.startswith("_route_")]:
            patch.setattr(controller, name, MagicMock(side_effect=ValueError("unexpected")))
        prompt = "Tell me about depression medication"
        turn1 = orch.process(ProcessedRequest(prompt=prompt, request_id="req-g1"), conversation_id="c1", turn_index=1)
    assert turn1.response.metadata.final_action == "REFUSE"
    assert turn1.error == "unexpected"
    assert ledger._storage.size() == 0
    turn2 = orch.process(ProcessedRequest(prompt=prompt, request_id="req-g2"), conversation_id="c1", turn_index=2)
    assert turn2.response.metadata.final_action != "REFUSE"
    assert turn2.response.metadata.hard_violation_flip_reason == ""
    state2 = turn2.conversation_governance_state_out
    assert state2 is not None
    assert state2.turn_decisions_summary[-1].was_cached is False


# ---------------------------------------------------------------------------
# F6 phase 5: unlabelled results are refused at the consumer guard
# ---------------------------------------------------------------------------


def test_g5_turn_summary_relabel_matches_guard(request: pytest.FixtureRequest) -> None:
    ctrl = _ctrl(request=request)
    result = OrchestratorResult(
        response=FinalResponse(
            content="ANSWER", response_type=ResponseType.FULL_REFUSAL, metadata=ResponseMetadata(final_action="")
        ),
        request_id="req-g5",
        conversation_id="c1",
        turn_index=1,
    )
    state = ctrl._extend_state_out_v04(
        state=ConversationGovernanceState(conversation_id="c1", turn_index=1),
        request=ProcessedRequest(prompt="hello", request_id="req-g5"),
        result=result,
        call_ctx=ProcessCallContext(),
    )
    assert state.turn_decisions_summary[-1].final_action == "REFUSE"


def test_g6_ensure_backfill_then_delivery_is_refuse(request: pytest.FixtureRequest) -> None:
    ctrl = _ctrl(request=request)
    result = OrchestratorResult(
        response=FinalResponse(
            content="ANSWER", response_type=ResponseType.FULL_REFUSAL, metadata=ResponseMetadata(final_action="")
        ),
        request_id="req-g6",
    )
    out = ctrl._diagnostics.ensure_final_action_and_return(
        result,
        ProcessedRequest(prompt="hello", request_id="req-g6"),
        time.time(),
        "g6",
        lambda r, _req: r,
    )
    delivery = finalize_delivery(out)
    assert delivery.final_action == "REFUSE"
    assert delivery.finish_reason == "content_filter"


# =============================================================================
# F6 follow-up C7: best-effort PIPELINE_FAILED orchestration event
# =============================================================================

_SPY_TARGET = "moralstack.observability.emit_helpers.persist_orchestration_event"
_PF_KEYS = {"fail_closed_reason", "triggered_principle", "error_type", "path"}
_PF_CASES = [
    pytest.param(lambda: GenerationError(f"Generation failed: {SECRET}"), "system_error", "SYSTEM.ERROR", id="error"),
    pytest.param(
        lambda: OrchestratorTimeoutError(f"{TIMEOUT_TEXT} {SECRET}"), "system_timeout", "SYSTEM.TIMEOUT", id="timeout"
    ),
    pytest.param(lambda: ValueError(f"unexpected {SECRET}"), "system_error", "SYSTEM.FAIL_SAFE", id="generic"),
]


class _EventSpy:
    def __init__(self, *, raises: bool = False) -> None:
        self.calls: list[dict[str, Any]] = []
        self.raises = raises

    def __call__(self, **kwargs: Any) -> None:
        self.calls.append(kwargs)
        if self.raises:
            raise RuntimeError("event sink down")

    def failed(self) -> list[dict[str, Any]]:
        return [c for c in self.calls if c.get("event_type") == "PIPELINE_FAILED"]


@pytest.mark.parametrize("conv", _CONVS)
@pytest.mark.parametrize("make_exc,reason,principle", _PF_CASES)
def test_pf1_pipeline_failed_event_emitted(
    request: pytest.FixtureRequest,
    monkeypatch: pytest.MonkeyPatch,
    make_exc: Any,
    reason: str,
    principle: str,
    conv: Any,
) -> None:
    spy = _EventSpy()
    monkeypatch.setattr(_SPY_TARGET, spy)
    exc = make_exc()
    _fail(_ctrl(request=request), monkeypatch, exc, conv=conv)
    events = spy.failed()
    assert len(events) == 1
    event = events[0]
    assert event["decision"] == "REFUSE"
    assert event["status"] == "error"
    assert event["reason_codes"] == [reason]
    assert event["request_id"] == "req-f6"
    assert set(event["payload"]) == _PF_KEYS
    assert event["payload"] == {
        "fail_closed_reason": reason,
        "triggered_principle": principle,
        "error_type": type(exc).__name__,
        "path": "ERROR_PATH",
    }


@pytest.mark.parametrize("make_exc,reason,principle", _PF_CASES)
def test_pf2_pipeline_failed_event_has_no_exception_text(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch, make_exc: Any, reason: str, principle: str
) -> None:
    spy = _EventSpy()
    monkeypatch.setattr(_SPY_TARGET, spy)
    _fail(_ctrl(request=request), monkeypatch, make_exc())
    events = spy.failed()
    assert len(events) == 1
    assert "secret-ish body" not in json.dumps(events[0], default=str)


@pytest.mark.parametrize("make_exc,reason,principle", _PF_CASES)
def test_pf3_event_failure_never_breaks_the_request(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch, make_exc: Any, reason: str, principle: str
) -> None:
    spy = _EventSpy(raises=True)
    monkeypatch.setattr(_SPY_TARGET, spy)
    result = _fail(_ctrl(request=request), monkeypatch, make_exc())
    assert len(spy.failed()) >= 1
    meta = result.response.metadata
    assert meta.final_action == "REFUSE"
    assert meta.triggered_principles == [principle]
    assert meta.hard_violation_flip_reason == reason
    assert result.response.content == ("[SYSTEM_TIMEOUT]" if principle == "SYSTEM.TIMEOUT" else "[SYSTEM_ERROR]")
    assert result.path == "ERROR_PATH"


@pytest.mark.parametrize("make_exc,reason,principle", _PF_CASES)
def test_pf3b_event_failure_does_not_swallow_the_warning(
    request: pytest.FixtureRequest,
    monkeypatch: pytest.MonkeyPatch,
    ctrl_log: _ListHandler,
    make_exc: Any,
    reason: str,
    principle: str,
) -> None:
    monkeypatch.setattr(_SPY_TARGET, _EventSpy(raises=True))
    _fail(_ctrl(request=request), monkeypatch, make_exc())
    assert len(_failure_warnings(ctrl_log)) == 1


def test_pf4_pipeline_failed_in_taxonomy() -> None:
    from moralstack.orchestration import orchestration_event_taxonomy as tax

    assert getattr(tax, "PIPELINE_FAILED", None) == "PIPELINE_FAILED"
    assert "PIPELINE_FAILED" in tax.ALL_EVENT_TYPES


def test_pf5_no_pipeline_failed_event_on_success(monkeypatch: pytest.MonkeyPatch) -> None:
    spy = _EventSpy()
    monkeypatch.setattr(_SPY_TARGET, spy)
    orch = create_orchestrator(
        policy=MockPolicy(),
        risk_estimator=MockRiskEstimator(),
        critic=MockCritic(),
        simulator=MockSimulator(),
        hindsight=MockHindsight(),
        perspectives=MockPerspectives(),
        constitution_store=MockConstitutionStore(),
        max_cycles=1,
        timeout_ms=60_000,
    )
    result = orch.process(
        ProcessedRequest(prompt="Tell me about depression medication", request_id="req-ok"),
        conversation_id="c1",
        turn_index=1,
    )
    assert result.response.metadata.final_action != "REFUSE"
    assert spy.failed() == []


def test_pf6_event_persists_and_does_not_leak_into_ui_detection(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    pytest.importorskip("fastapi")
    import sqlite3

    from moralstack.observability.context import (
        get_current_request_id,
        get_current_run_id,
        set_current_request_id,
        set_current_run_id,
    )
    from moralstack.observability.governance_audit import build_request_meta_from_result
    from moralstack.observability.service import get_obs
    from moralstack.observability.sinks.sqlite_sink import create_run, init_db, upsert_request
    from moralstack.ui.app import _detect_pipeline_failure
    from tests.test_ui_conversation_views import (
        _bind_observability_db,
        _reinstall_observability_service_writes,
        _reset_observability_singleton,
    )

    dbp = str(tmp_path / "pf6.db")
    _reinstall_observability_service_writes()
    _reset_observability_singleton()
    _bind_observability_db(monkeypatch, dbp)
    init_db(dbp)
    prev_run, prev_req = get_current_run_id(), get_current_request_id()
    try:
        create_run("run-pf6", run_type="single", meta={})
        upsert_request("run-pf6", "req-f6", prompt="hello", domain="general")
        set_current_run_id("run-pf6")
        set_current_request_id("req-f6")
        result = _fail(_ctrl(request=request), monkeypatch, GenerationError("g"))
        get_obs().flush(timeout=10.0)
    finally:
        set_current_run_id(prev_run)  # type: ignore[arg-type]
        set_current_request_id(prev_req)  # type: ignore[arg-type]
    conn = sqlite3.connect(dbp)
    try:
        failed = conn.execute(
            "SELECT COUNT(*) FROM orchestration_events WHERE request_id = ? AND event_type = ?",
            ("req-f6", "PIPELINE_FAILED"),
        ).fetchone()[0]
        final_rows = conn.execute(
            "SELECT COUNT(*) FROM decision_traces WHERE request_id = ? AND UPPER(TRIM(stage)) = 'FINAL'", ("req-f6",)
        ).fetchone()[0]
    finally:
        conn.close()
    assert failed == 1
    assert final_rows == 0
    assert _detect_pipeline_failure([], build_request_meta_from_result(result)) is True


def test_pf7_ensure_fallback_emits_no_event(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> None:
    spy = _EventSpy()
    monkeypatch.setattr(_SPY_TARGET, spy)
    ctrl = _ctrl(request=request)
    bad = FinalResponse(content="x", response_type=None, metadata=ResponseMetadata())  # type: ignore[arg-type]
    out = ctrl._diagnostics.ensure_final_action_and_return(
        OrchestratorResult(response=bad, execution_trace=MagicMock()),
        ProcessedRequest(prompt="p", request_id="req-d"),
        time.time(),
        None,
        attach_trace_fn=lambda res, _req: res,
    )
    assert out.error == "final_action_undefined"
    assert spy.failed() == []


def test_pf8_unprintable_exception_still_emits(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> None:
    spy = _EventSpy()
    monkeypatch.setattr(_SPY_TARGET, spy)
    result = _fail(_ctrl(request=request), monkeypatch, _UnprintableError("x"))
    events = spy.failed()
    assert len(events) == 1
    assert events[0]["payload"]["error_type"] == "_UnprintableError"
    assert result.response.metadata.final_action == "REFUSE"


def test_pf9_handler_without_run_context_emits_nothing_and_refuses(request: pytest.FixtureRequest) -> None:
    # Real persist_orchestration_event, no spy, no run_id in context: it returns None.
    ctrl = _ctrl(request=request)
    req = ProcessedRequest(prompt="hello", request_id="req-f6")
    result = ctrl._handle_error(req, GenerationError("g"), time.time())
    assert result.response.metadata.final_action == "REFUSE"
    assert result.path == "ERROR_PATH"
    timeout = ctrl._handle_timeout(req, OrchestratorTimeoutError(TIMEOUT_TEXT), time.time())
    assert timeout.response.metadata.final_action == "REFUSE"
