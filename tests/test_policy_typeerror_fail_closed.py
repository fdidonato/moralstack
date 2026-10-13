"""Governed generation calls are never retried on ``TypeError`` (fail closed).

Before this change ten call sites (six in ``deliberation_runner.py``, four in
``controller.py``) wrapped the governed generation call in
``try/except TypeError`` and retried with fewer arguments. The retry dropped the
composed system prompt (and ``overrides`` / ``config`` / ``model_override``), could
bill the provider twice, and persisted an audit row whose ``system_prompt`` described
a composition that was never sent.

Contract pinned here: one call per site; a ``TypeError`` (whether it is an
"unexpected keyword argument" mismatch or a real internal error) reaches the existing
fail-closed handler of that site. Nothing produced by a second call can be delivered
or persisted. The only compatibility shim left is ``_estimate_risk``, which decides
from the estimator signature *before* calling and never by catching.

Sites: R1 ``run_benign_fast_path``, R2 ``_generate_safe_complete_text``, R3
``run_fast_path``, R4 ``_soft_revision_pass``, R5/R6 ``_generate_or_revise``;
C1 ``_estimate_risk``, C2 ``_speculative_generate``, C3
``_regenerate_for_contract``, C4 ``_revalidate_draft``.
"""

from __future__ import annotations

import ast
import json
import logging
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from moralstack.compliance import ComplianceDecision, StructuredRule
from moralstack.compliance.types import ComplianceVerdict, EvaluationPath, MatchedRule
from moralstack.core.types import Turn
from moralstack.models.base import GenerationConfig
from moralstack.models.risk import OperationalRisk, RiskCategory
from moralstack.models.risk.schema import RiskEstimation
from moralstack.orchestration.contract import DeveloperContract
from moralstack.orchestration.controller import OrchestrationController, RiskEstimationError
from moralstack.orchestration.deliberation_runner import (
    HARD_VIOLATION_REGENERATION_FAILED,
    DeliberationRunner,
)
from moralstack.orchestration.null_persistence import NullPersistence
from moralstack.orchestration.response_assembler import ResponseAssembler
from moralstack.orchestration.system_prompt_resolver import effective_system_for_request
from moralstack.orchestration.types import (
    Decision,
    DeliberationDependencies,
    DeliberationState,
    GenerationError,
    OrchestratorConfig,
    ProcessedRequest,
)
from moralstack.utils.output_protection import OutputProtector

SENTINEL = "SECOND-CALL-UNGOVERNED"
BASE_SYSTEM = "protected"
REPO_ROOT = Path(__file__).resolve().parents[1]


# =============================================================================
# Doubles
# =============================================================================


class _Result:
    """Minimal generation result (also accepted by ``TokenUsage.from_generation_result``)."""

    def __init__(self, text: str) -> None:
        self.text = text
        self.tokens_used = 10
        self.prompt_tokens = 5
        self.completion_tokens = 5
        self.token_usage_source = "exact"
        self.prompt_used = None
        self.system_used = None

    def token_usage_json(self) -> str | None:
        return None


class _Recorder:
    """Records every call as ``(method, args, kwargs)``; the first call raises."""

    def __init__(self, message: str = "boom from inside") -> None:
        self.model = "gpt-policy"
        self.calls: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []
        self._message = message

    def _record(self, name: str, args: tuple[Any, ...], kwargs: dict[str, Any]) -> _Result:
        self.calls.append((name, args, kwargs))
        if len(self.calls) == 1:
            raise TypeError(self._message)
        return _Result(SENTINEL)


class _RaisingOncePolicy(_Recorder):
    """Accepts any kwargs on generate/rewrite/generate_messages; first call raises."""

    def generate(self, *args: Any, **kw: Any) -> _Result:
        return self._record("generate", args, kw)

    def rewrite(self, *args: Any, **kw: Any) -> _Result:
        return self._record("rewrite", args, kw)

    def generate_messages(self, *args: Any, **kw: Any) -> _Result:
        return self._record("generate_messages", args, kw)


class _GenerateOnlyPolicy(_Recorder):
    """Internal policy WITHOUT ``generate_messages`` (multi-turn ``hasattr`` branch)."""

    def generate(self, *args: Any, **kw: Any) -> _Result:
        return self._record("generate", args, kw)


# =============================================================================
# Runner harness (R1-R6)
# =============================================================================


@pytest.fixture
def persisted(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []

    def _record(_logger: Any, _diag: Any, persist_kwargs: dict[str, Any] | None) -> None:
        if persist_kwargs is not None:
            rows.append(persist_kwargs)

    monkeypatch.setattr("moralstack.orchestration.deliberation_runner.record_llm_call", _record)
    monkeypatch.setattr(
        "moralstack.orchestration.deliberation_runner.build_aggregated_guidance",
        lambda *_a, **_k: "guidance",
    )
    return rows


def _passthrough_protector() -> MagicMock:
    m = MagicMock()
    m.validate.side_effect = lambda text: SimpleNamespace(cleaned=text, had_leakage=False)
    return m


def _runner(policy: Any, critic: Any = None) -> DeliberationRunner:
    deps = DeliberationDependencies(
        policy=policy,
        critic=critic,
        simulator=None,
        hindsight=None,
        perspectives=None,
        constitution_store=None,
        output_protector=_passthrough_protector(),
    )
    return DeliberationRunner(OrchestratorConfig(), deps, BASE_SYSTEM, None, ResponseAssembler(policy=None))


def _risk(score: float = 0.1) -> RiskEstimation:
    return RiskEstimation(score=score, confidence=0.9, risk_category=RiskCategory.BENIGN)


def _decision(final_action: str = "NORMAL_COMPLETE", path: str = "FAST_PATH", hard: list[str] | None = None) -> Decision:
    return Decision(
        final_action=final_action,
        path=path,
        intent_clarity="HIGH",
        misuse_plausibility="LOW",
        actionability_risk="LOW",
        triggered_principles=list(hard or []),
        hard_violations=list(hard or []),
        risk_signals=[],
    )


def _request(with_contract: bool = False, overrides: Any = None) -> ProcessedRequest:
    return ProcessedRequest(
        prompt="hello",
        request_id="req-typeerror",
        developer_contract=DeveloperContract.from_text("DEV CONTRACT TEXT", mode="opaque") if with_contract else None,
        generation_overrides=overrides,
    )


def _drive_r1(runner: DeliberationRunner, request: ProcessedRequest) -> Any:
    return runner.run_benign_fast_path(request, _risk(), 0.0, decision=_decision())


def _drive_r2(runner: DeliberationRunner, request: ProcessedRequest) -> Any:
    return runner._generate_safe_complete_text(request, _risk())


def _drive_r3(runner: DeliberationRunner, request: ProcessedRequest) -> Any:
    return runner.run_fast_path(request, _risk(), 0.0, decision=_decision())


def _drive_r4(runner: DeliberationRunner, request: ProcessedRequest) -> Any:
    return runner._soft_revision_pass(DeliberationState(cycle=1, draft_response="orig"), request)


def _drive_r5(runner: DeliberationRunner, request: ProcessedRequest) -> Any:
    return runner._generate_or_revise(DeliberationState(cycle=1, draft_response=""), request, risk_estimation=_risk())


def _drive_r6(runner: DeliberationRunner, request: ProcessedRequest) -> Any:
    return runner._generate_or_revise(DeliberationState(cycle=2, draft_response="prior"), request, risk_estimation=_risk())


_DRIVERS = {
    "R1": _drive_r1,
    "R2": _drive_r2,
    "R3": _drive_r3,
    "R4": _drive_r4,
    "R5": _drive_r5,
    "R6": _drive_r6,
}


def _drive_swallowing(site: str, runner: DeliberationRunner, request: ProcessedRequest) -> None:
    try:
        _DRIVERS[site](runner, request)
    except GenerationError:
        pass


def _row_blob(rows: list[dict[str, Any]]) -> str:
    return json.dumps(rows, default=str)


# =============================================================================
# T1-T6: one test per runner site
# =============================================================================


class TestRunnerSitesFailClosed:
    @pytest.mark.parametrize("message", ["boom from inside", "got an unexpected keyword argument 'overrides'"])
    def test_t1_r1_benign_fast_path_single_call_fails_closed(self, persisted: list[dict[str, Any]], message: str) -> None:
        policy = _RaisingOncePolicy(message)
        runner = _runner(policy)
        with pytest.raises(GenerationError):
            _drive_r1(runner, _request(with_contract=True))
        assert len(policy.calls) == 1
        assert SENTINEL not in _row_blob(persisted)

    def test_t2_r2_safe_complete_text_single_call_raises_generation_error(self, persisted: list[dict[str, Any]]) -> None:
        policy = _RaisingOncePolicy()
        runner = _runner(policy)
        with pytest.raises(GenerationError):
            _drive_r2(runner, _request())
        assert len(policy.calls) == 1
        assert SENTINEL not in _row_blob(persisted)

    def test_t2_r2_guard_path_refuses_hard_violation_regeneration_failed(self, persisted: list[dict[str, Any]]) -> None:
        policy = _RaisingOncePolicy()
        runner = _runner(policy, critic=MagicMock())
        state = DeliberationState(cycle=1, draft_response="REJECTED DRAFT")
        decision = _decision(final_action="SAFE_COMPLETE", path="DELIBERATIVE_PATH", hard=["CORE.PRIV.1"])
        out, original, reason = runner.enforce_no_rejected_draft_delivery(
            _request(), state, decision, risk_estimation=_risk()
        )
        assert out.final_action == "REFUSE"
        assert original == "SAFE_COMPLETE"
        assert reason == HARD_VIOLATION_REGENERATION_FAILED
        assert out.hard_violations == ["CORE.PRIV.1"]
        assert len(policy.calls) == 1
        assert SENTINEL not in _row_blob(persisted)

    def test_t3_r3_fast_path_without_speculative_draft_single_call(self, persisted: list[dict[str, Any]]) -> None:
        policy = _RaisingOncePolicy()
        runner = _runner(policy)
        with pytest.raises(GenerationError):
            _drive_r3(runner, _request())
        assert len(policy.calls) == 1
        assert SENTINEL not in _row_blob(persisted)

    def test_t4_r4_soft_revision_typeerror_keeps_original_draft(self, persisted: list[dict[str, Any]]) -> None:
        policy = _RaisingOncePolicy()
        runner = _runner(policy)
        state = DeliberationState(cycle=1, draft_response="orig")
        out = runner._soft_revision_pass(state, _request())
        assert [c[0] for c in policy.calls] == ["rewrite"]
        assert out.draft_response == "orig"
        assert any("Soft revision failed" in e for e in out.errors)
        assert out.soft_revision_applied is False
        assert SENTINEL not in _row_blob(persisted)

    def test_t5_r5_generate_typeerror_without_draft_raises_generation_error(self, persisted: list[dict[str, Any]]) -> None:
        policy = _RaisingOncePolicy()
        runner = _runner(policy)
        with pytest.raises(GenerationError):
            _drive_r5(runner, _request())
        assert [c[0] for c in policy.calls] == ["generate"]
        assert SENTINEL not in _row_blob(persisted)

    def test_t6_r6_rewrite_typeerror_keeps_prior_draft(self, persisted: list[dict[str, Any]]) -> None:
        policy = _RaisingOncePolicy()
        runner = _runner(policy)
        state = DeliberationState(cycle=2, draft_response="prior")
        out = runner._generate_or_revise(state, _request(), risk_estimation=_risk())
        assert [c[0] for c in policy.calls] == ["rewrite"]
        assert out.draft_response == "prior"
        assert out.errors
        assert SENTINEL not in _row_blob(persisted)

    def test_policy_none_skips_generation_without_error(self, persisted: list[dict[str, Any]]) -> None:
        runner = _runner(None)
        assert _drive_r2(runner, _request()).startswith("[SAFE_COMPLETE mock:")

    def test_speculative_draft_present_skips_generation_r1_and_r3(self, persisted: list[dict[str, Any]]) -> None:
        policy = _RaisingOncePolicy()
        runner = _runner(policy)
        runner.run_benign_fast_path(_request(), _risk(), 0.0, decision=_decision(), speculative_draft="DRAFT")
        runner.run_fast_path(_request(), _risk(), 0.0, decision=_decision(), speculative_draft="DRAFT")
        assert policy.calls == []


# =============================================================================
# T13: primary call is byte-identical to the governed composition
# =============================================================================


class TestPrimaryCallComposition:
    @pytest.mark.parametrize("with_contract", [False, True])
    @pytest.mark.parametrize("site", sorted(_DRIVERS))
    def test_t13_each_generation_site_sends_composed_system(
        self, persisted: list[dict[str, Any]], site: str, with_contract: bool
    ) -> None:
        overrides = object() if with_contract else None
        policy = _RaisingOncePolicy()
        runner = _runner(policy)
        request = _request(with_contract=with_contract, overrides=overrides)
        _drive_swallowing(site, runner, request)

        assert policy.calls, f"{site} did not call the policy"
        _, _, kwargs = policy.calls[0]
        expected = effective_system_for_request(base=BASE_SYSTEM, request=request, mode="normal")
        assert kwargs["system"] == expected
        assert expected
        assert "overrides" in kwargs
        assert kwargs["overrides"] is overrides
        if not with_contract:
            assert kwargs["system"] == BASE_SYSTEM
        else:
            assert kwargs["system"].startswith("DEV" + " CONTRACT TEXT")

    @pytest.mark.parametrize("site", sorted(_DRIVERS))
    def test_t14_failed_call_leaves_no_persisted_generation_row(self, persisted: list[dict[str, Any]], site: str) -> None:
        """At base the retry's row carried a ``system_prompt`` that was never sent."""
        policy = _RaisingOncePolicy()
        runner = _runner(policy)
        _drive_swallowing(site, runner, _request(with_contract=True))
        assert len(policy.calls) == 1
        gen_rows = [r for r in persisted if r.get("phase") in {"policy_generate", "policy_rewrite", "soft_revision"}]
        assert gen_rows == []
        assert SENTINEL not in _row_blob(persisted)


# =============================================================================
# Controller harness (C1-C4)
# =============================================================================


def _controller(policy: Any = None, risk_estimator: Any = None, *, speculative: bool = True) -> OrchestrationController:
    return OrchestrationController(
        config=OrchestratorConfig(enable_speculative_generation=speculative),
        policy=policy if policy is not None else MagicMock(),
        risk_estimator=risk_estimator if risk_estimator is not None else MagicMock(),
        critic=None,
        simulator=None,
        hindsight=None,
        perspectives=None,
        constitution_store=None,
        output_protector=OutputProtector(),
        protected_system_prompt="system",
        persistence=NullPersistence(),
    )


def _verdict(action: str = "PONG", **kw: Any) -> ComplianceVerdict:
    return ComplianceVerdict(
        decision=ComplianceDecision.MATCH,
        matched_rule=MatchedRule(rule_id="r1", rule_summary="ping", rule_excerpt="PING", action_payload_summary=action),
        confidence=0.5,
        rationale="match",
        evaluation_path=EvaluationPath.LLM,
        **kw,
    )


# --- C2 ---------------------------------------------------------------------


class TestC2SpeculativeGenerate:
    @pytest.mark.parametrize("message", ["boom from inside", "got an unexpected keyword argument 'overrides'"])
    def test_t7_single_turn_typeerror_returns_no_draft_and_no_second_call(
        self, message: str, caplog: pytest.LogCaptureFixture
    ) -> None:
        governance = _RaisingOncePolicy()
        upstream = _RaisingOncePolicy(message)
        ctrl = _controller(policy=governance)
        req = ProcessedRequest(prompt="hello", request_id="c2-single")
        req.upstream_draft_generator = upstream
        with caplog.at_level(logging.WARNING):
            assert ctrl._speculative_generate(req) == (None, None)
        assert len(upstream.calls) == 1
        assert governance.calls == []
        assert "Speculative generation failed" in caplog.text

    def test_h2_real_upstream_generator_parse_tail_typeerror_is_not_retried(self, caplog: pytest.LogCaptureFixture) -> None:
        """A malformed client reply (``choices=None``) makes the real generator's parse
        tail raise ``TypeError``. At base this triggered a second ``create`` call."""
        from moralstack.orchestration.upstream_draft import UpstreamDraftGenerator

        create = MagicMock(return_value=SimpleNamespace(choices=None, usage=None))
        client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
        governance = _RaisingOncePolicy()
        ctrl = _controller(policy=governance)
        req = ProcessedRequest(prompt="hello", request_id="c2-h2")
        req.upstream_draft_generator = UpstreamDraftGenerator(client, "client-model-C")
        with caplog.at_level(logging.WARNING):
            assert ctrl._speculative_generate(req) == (None, None)
        assert create.call_count == 1
        assert "Speculative generation failed" in caplog.text
        assert governance.calls == []

    def test_t7_multi_turn_generate_messages_typeerror_returns_no_draft(self) -> None:
        from moralstack.orchestration.conversation_context import ConversationContext

        governance = _RaisingOncePolicy()
        upstream = _RaisingOncePolicy()
        ctrl = _controller(policy=governance)
        req = ProcessedRequest(prompt="second", request_id="c2-multi")
        req.upstream_draft_generator = upstream
        conv = MagicMock(spec=ConversationContext)
        conv.prior_turn_count = 1
        conv.native_context_messages = MagicMock(return_value=[{"role": "user", "content": "first"}])
        req.conversation_context = conv
        assert ctrl._speculative_generate(req) == (None, None)
        assert [c[0] for c in upstream.calls] == ["generate_messages"]
        assert governance.calls == []

    def test_t7_multi_turn_internal_policy_without_generate_messages(self) -> None:
        from moralstack.orchestration.conversation_context import ConversationContext

        policy = _GenerateOnlyPolicy()
        ctrl = _controller(policy=policy)
        req = ProcessedRequest(prompt="second", request_id="c2-multi-internal")
        conv = MagicMock(spec=ConversationContext)
        conv.prior_turn_count = 1
        conv.native_context_messages = MagicMock(return_value=[{"role": "user", "content": "first"}])
        req.conversation_context = conv
        assert ctrl._speculative_generate(req) == (None, None)
        assert len(policy.calls) == 1
        name, _, kwargs = policy.calls[0]
        assert name == "generate"
        assert set(kwargs) == {"prompt", "system", "overrides"}


# --- C3 ---------------------------------------------------------------------


class TestC3RegenerateForContract:
    def test_t8_single_call_carries_config_and_error_propagates(self) -> None:
        policy = _RaisingOncePolicy()
        ctrl = _controller(policy=policy)
        req = ProcessedRequest(prompt="PING", request_id="c3-direct")
        with pytest.raises(TypeError, match="boom from inside"):
            ctrl._regenerate_for_contract(req, _verdict(), _risk())
        assert len(policy.calls) == 1
        _, _, kwargs = policy.calls[0]
        assert kwargs["config"] == GenerationConfig(1024, 0.0, 1.0)
        assert kwargs["system"] == effective_system_for_request(base="system", request=req, mode="normal")

    def test_t8_via_process_goes_to_error_path_and_never_delivers_second_call(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MORALSTACK_DCCL_EVALUATION_PATH", "structured")
        risk = RiskEstimation(
            score=0.1, confidence=0.9, risk_category=RiskCategory.BENIGN, operational_risk=OperationalRisk.NONE
        )

        class _FastRisk:
            def estimate(self, prompt: str, **kwargs: Any) -> RiskEstimation:
                return risk

        # Speculative generation off: the first policy call must be the C3 regeneration.
        ctrl = _controller(risk_estimator=_FastRisk(), speculative=False)
        ctrl.policy = _RaisingOncePolicy()
        rule = StructuredRule(rule_id="r1", trigger_pattern="PING", action_payload="PONG")
        contract = replace(DeveloperContract.from_text("if user says PING reply PONG"), structured_rules=(rule,))
        req = ProcessedRequest(prompt="PING", request_id="c3-process", developer_contract=contract)
        degraded = _verdict(degraded=True, degraded_reason="llm_timeout", speculative_draft_validated=False)
        with (
            patch.object(ctrl, "_nonblocking_speculative_draft", return_value=""),
            patch("moralstack.compliance.dccl.DeveloperContractComplianceLayer.evaluate", return_value=degraded),
            patch("moralstack.observability.emit_helpers.persist_orchestration_event"),
        ):
            result = ctrl.process(req)
        assert len(ctrl.policy.calls) == 1
        assert result.path == "ERROR_PATH"
        assert SENTINEL not in result.response.content


# --- C4 ---------------------------------------------------------------------


class TestC4RevalidateDraft:
    def test_t9_typeerror_returns_false_none_with_one_call(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MORALSTACK_DCCL_LLM_MODEL", "dccl-model-x")
        from moralstack.compliance.config import get_dccl_llm_model
        from moralstack.compliance.dccl import DCCL_DRAFT_MATCH_SYSTEM_PROMPT

        policy = _RaisingOncePolicy()
        ctrl = _controller(policy=policy)
        ok, method, conf = ctrl._revalidate_draft(_verdict("AUTHORIZED PAYLOAD"), "some unrelated draft text")
        assert (ok, method) == (False, "none")
        assert conf == 0.0
        assert len(policy.calls) == 1
        _, _, kwargs = policy.calls[0]
        assert kwargs["model_override"] == get_dccl_llm_model() == "dccl-model-x"
        assert kwargs["config"].max_tokens == 128
        assert kwargs["system"] == DCCL_DRAFT_MATCH_SYSTEM_PROMPT


# --- C1 ---------------------------------------------------------------------


def _contract_history_request() -> ProcessedRequest:
    return ProcessedRequest(
        prompt="q",
        developer_contract=DeveloperContract.from_text("SECRET SYS PROMPT", mode="opaque"),
        conversation_history=[Turn(role="user", content="private hi"), Turn(role="assistant", content="private hello")],
    )


class _ProtocolOnlyEstimator:
    def __init__(self) -> None:
        self.calls: list[tuple[tuple[Any, ...], dict[str, Any]]] = []

    def estimate(self, prompt: str) -> RiskEstimation:
        self.calls.append(((prompt,), {}))
        return RiskEstimation.benign()


class _PartialEstimator:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def estimate(
        self, prompt: str, *, developer_contract_text: Any = None, conversation_history: Any = None
    ) -> RiskEstimation:
        self.calls.append({"developer_contract_text": developer_contract_text, "conversation_history": conversation_history})
        return RiskEstimation.benign()


class _FullEstimator:
    _top_k = 7

    def __init__(self) -> None:
        self.kwargs: dict[str, Any] | None = None

    def estimate(self, prompt: str, **kwargs: Any) -> RiskEstimation:
        self.kwargs = kwargs
        return RiskEstimation.benign()


class _RaisingKwargsEstimator:
    def __init__(self) -> None:
        self.call_count = 0

    def estimate(self, prompt: str, **kwargs: Any) -> RiskEstimation:
        self.call_count += 1
        raise TypeError("internal type error")


class TestC1EstimateRisk:
    def test_t10_protocol_only_estimator_called_once_without_kwargs_and_warns(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        est = _ProtocolOnlyEstimator()
        ctrl = _controller(risk_estimator=est)
        with caplog.at_level(logging.DEBUG, logger="moralstack.orchestration.controller"):
            ctrl._estimate_risk(_contract_history_request())
        assert est.calls == [(("q",), {})]
        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert warnings, "dropping contract/history must warn"
        text = " ".join(r.getMessage() for r in caplog.records)
        assert "_ProtocolOnlyEstimator" in text
        assert "developer_contract_text" in text and "conversation_history" in text
        for secret in ("SECRET SYS PROMPT", "private hi", "private hello"):
            assert secret not in text

    def test_t10_no_warning_when_nothing_meaningful_is_dropped(self, caplog: pytest.LogCaptureFixture) -> None:
        est = _ProtocolOnlyEstimator()
        ctrl = _controller(risk_estimator=est)
        with caplog.at_level(logging.DEBUG, logger="moralstack.orchestration.controller"):
            ctrl._estimate_risk(ProcessedRequest(prompt="q"))
        assert est.calls == [(("q",), {})]
        assert not [r for r in caplog.records if r.levelno >= logging.WARNING]

    def test_t11_partial_estimator_receives_exactly_its_kwargs(self) -> None:
        est = _PartialEstimator()
        ctrl = _controller(risk_estimator=est)
        ctrl._estimate_risk(_contract_history_request())
        assert len(est.calls) == 1
        assert est.calls[0]["developer_contract_text"] == "SECRET SYS PROMPT"
        assert est.calls[0]["conversation_history"] == [
            {"role": "user", "content": "private hi"},
            {"role": "assistant", "content": "private hello"},
        ]

    def test_production_shaped_estimator_receives_all_four_kwargs(self) -> None:
        est = _FullEstimator()
        ctrl = _controller(risk_estimator=est)
        ctrl._estimate_risk(_contract_history_request())
        assert est.kwargs is not None
        assert set(est.kwargs) == {
            "developer_contract_text",
            "conversation_history",
            "retrieval_query",
            "retrieval_top_k",
        }

    def test_t12_internal_typeerror_is_not_masked(self) -> None:
        est = _RaisingKwargsEstimator()
        ctrl = _controller(risk_estimator=est)
        with pytest.raises(RiskEstimationError):
            ctrl._estimate_risk(_contract_history_request())
        assert est.call_count == 1


# =============================================================================
# T17: structural pin - no TypeError handler left on the governed path
# =============================================================================


def _typeerror_handlers(path: Path) -> list[tuple[str, int]]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: list[tuple[str, int]] = []

    def visit(node: ast.AST, func: str) -> None:
        for child in ast.iter_child_nodes(node):
            name = child.name if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)) else func
            if isinstance(child, ast.ExceptHandler) and child.type is not None:
                types = child.type.elts if isinstance(child.type, ast.Tuple) else [child.type]
                if any(isinstance(x, ast.Name) and x.id == "TypeError" for x in types):
                    found.append((func, child.lineno))
            visit(child, name)

    visit(tree, "<module>")
    return found


@pytest.mark.parametrize(
    "relpath,allowed_functions",
    [
        ("moralstack/orchestration/deliberation_runner.py", set()),
        (
            "moralstack/orchestration/controller.py",
            # inspect.signature guard in the C1 helper; unrelated value parsing.
            {"_supported_estimate_kwargs", "_emit_canonical_conversation_state_updated"},
        ),
    ],
)
def test_t17_no_typeerror_retry_handlers(relpath: str, allowed_functions: set[str]) -> None:
    offenders = [(f, ln) for f, ln in _typeerror_handlers(REPO_ROOT / relpath) if f not in allowed_functions]
    assert offenders == []
