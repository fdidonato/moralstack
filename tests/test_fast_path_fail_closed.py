"""``DeliberationRunner.run_fast_path`` never delivers an unchecked draft (fail closed).

Before the fix, the HARD ``quick_check`` gate sat inside a ``try`` whose
``except Exception`` only logged a warning, after which the original NORMAL_COMPLETE
decision was assembled with the fast draft:

* (a) ``quick_check`` raised -> the unchecked draft was delivered;
* (b) ``quick_check`` said ``passed=False`` and the escalation to the deliberative
  path raised (or built no result) -> the draft that FAILED the HARD check was
  delivered as NORMAL_COMPLETE;
* F5 at the escalation site: the escalation returned with no completed critique ->
  the decision rested on risk alone and the flagged draft was delivered with no
  exception at all;
* F3: a configured constitution store whose load failed (constitution ``None``) made
  the whole check silently skipped.

Contract pinned here: every such outcome is either a verified deliberative result
(completed, non-skipped last critique) or a governed REFUSE (``fast_path_escalation_failed``
/ ``fast_path_escalation_unverified``) assembled through the REFUSE branch, so
``finalize_delivery`` reports REFUSE/content_filter. Offline and deterministic.
"""

from __future__ import annotations

import copy
import dataclasses
import json
import time
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from moralstack.models.risk import RiskCategory
from moralstack.models.risk.schema import RiskEstimation
from moralstack.orchestration.controller import OrchestrationController
from moralstack.orchestration.conversational_fast_path import ConversationalFastPathRunner
from moralstack.orchestration.deliberation_runner import (
    DeliberationRunner,
    _append_hard_violation_flip_final_trace,
)
from moralstack.orchestration.delivery import GOVERNED_REFUSAL_SOURCE, finalize_delivery
from moralstack.orchestration.ledger import CachedDecision, LedgerResult
from moralstack.orchestration.null_persistence import NullPersistence
from moralstack.orchestration.response_assembler import ResponseAssembler
from moralstack.orchestration.types import (
    Decision,
    DeliberationDependencies,
    DeliberationState,
    DraftProvenance,
    GenerationError,
    OrchestratorConfig,
    OrchestratorTimeoutError,
    ProcessedRequest,
)
from moralstack.runtime.modules.critic_module import CriticReport, LLMConstitutionalCritic
from moralstack.utils.output_protection import OutputProtector
from tests.test_fast_path_single_retrieval import (
    _CountingStore,
    _make_controller,
    _patch_decide_action,
    _Principle,
    _quick_check_result,
)

DRAFT = "DRAFT-X"
BASE_SYSTEM = "protected"
_RUNNER_MOD = "moralstack.orchestration.deliberation_runner"
# Literals on purpose: the audit values are a persisted contract, pinned verbatim.
FAST_PATH_ESCALATION_FAILED = "fast_path_escalation_failed"
FAST_PATH_ESCALATION_UNVERIFIED = "fast_path_escalation_unverified"


# =============================================================================
# Harness (copied from tests/test_policy_typeerror_fail_closed.py)
# =============================================================================


class _Result:
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


class _DraftPolicy:
    model = "gpt-policy"

    def generate(self, *args: Any, **kw: Any) -> _Result:
        return _Result(DRAFT)


@pytest.fixture
def persisted(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []

    def _record(_logger: Any, _diag: Any, persist_kwargs: dict[str, Any] | None) -> None:
        if persist_kwargs is not None:
            rows.append(persist_kwargs)

    monkeypatch.setattr(f"{_RUNNER_MOD}.record_llm_call", _record)
    monkeypatch.setattr(f"{_RUNNER_MOD}.build_aggregated_guidance", lambda *_a, **_k: "guidance")
    return rows


@pytest.fixture
def final_traces(monkeypatch: pytest.MonkeyPatch) -> list[Any]:
    """Captures every trace handed to ``append_decision_trace`` by the runner."""
    traces: list[Any] = []
    monkeypatch.setattr(f"{_RUNNER_MOD}.append_decision_trace", traces.append)
    return traces


def _passthrough_protector() -> MagicMock:
    m = MagicMock()
    m.validate.side_effect = lambda text: SimpleNamespace(cleaned=text, had_leakage=False)
    return m


def _runner(policy: Any, critic: Any = None, store: Any = None) -> DeliberationRunner:
    deps = DeliberationDependencies(
        policy=policy,
        critic=critic,
        simulator=None,
        hindsight=None,
        perspectives=None,
        constitution_store=store,
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


def _request() -> ProcessedRequest:
    return ProcessedRequest(prompt="hello", request_id="req-fast-fc")


def _completed_state() -> DeliberationState:
    state = DeliberationState(cycle=1)
    state.critiques.append(SimpleNamespace(skipped=False, violations=[], revision_guidance=""))
    # Completion is cycle-scoped and recorded by DeliberationRunner._critique (F5, D5).
    state._critique_outcome = "completed"
    return state


def _critic(*, passed: bool | None = False, exc: Exception | None = None) -> MagicMock:
    critic = MagicMock()
    if exc is not None:
        critic.quick_check.side_effect = exc
    else:
        critic.quick_check.return_value = _quick_check_result(bool(passed))
    return critic


def _drive(
    runner: DeliberationRunner,
    *,
    decision: Decision | None = None,
    constitution: Any = "default",
    start_time: float | None = None,
    **kw: Any,
) -> Any:
    return runner.run_fast_path(
        _request(),
        _risk(),
        time.time() if start_time is None else start_time,
        decision=decision or _decision(),
        constitution=SimpleNamespace(principles=[]) if constitution == "default" else constitution,
        **kw,
    )


def _metadata_blob(result: Any) -> str:
    return json.dumps(dataclasses.asdict(result.response.metadata), default=str)


def _assert_fail_closed(result: Any, code: str) -> None:
    meta = result.response.metadata
    assert meta.final_action == "REFUSE"
    assert result.response.content.strip()
    assert result.response.content != DRAFT
    assert DRAFT not in _metadata_blob(result)
    assert meta.original_final_action == "NORMAL_COMPLETE"
    assert meta.hard_violation_flip_reason == code
    assert code in meta.reason_codes
    assert meta.why_not_refuse.strip()
    assert meta.internal_draft_reused is False
    assert meta.draft_origin == "internal"
    assert result.path_taken == "fast"
    assert result.path == "FAST_PATH"
    assert result.total_cycles == 0
    delivery = finalize_delivery(result)
    assert delivery.final_action == "REFUSE"
    assert delivery.finish_reason == "content_filter"
    assert delivery.final_text_source == GOVERNED_REFUSAL_SOURCE


# =============================================================================
# R-b: the escalation (or the result build) raises
# =============================================================================

_ESC_ERRORS = [RuntimeError("boom"), GenerationError("gen"), OrchestratorTimeoutError("timeout"), TypeError("type")]


@pytest.mark.parametrize("exc", _ESC_ERRORS, ids=lambda e: type(e).__name__)
def test_rb1_escalation_raises_refuses_instead_of_delivering_flagged_draft(persisted: Any, exc: Exception) -> None:
    runner = _runner(_DraftPolicy(), _critic(passed=False))
    with patch.object(runner, "run_deliberative_path", side_effect=exc):
        result = _drive(runner)
    _assert_fail_closed(result, FAST_PATH_ESCALATION_FAILED)


@pytest.mark.parametrize("exc", _ESC_ERRORS, ids=lambda e: type(e).__name__)
def test_rb3_build_deliberative_result_raises_refuses(persisted: Any, exc: Exception) -> None:
    runner = _runner(_DraftPolicy(), _critic(passed=False))
    with (
        patch.object(runner, "run_deliberative_path", return_value=(_completed_state(), 0.1, None)),
        patch.object(runner, "_build_deliberative_result", side_effect=exc),
    ):
        result = _drive(runner)
    _assert_fail_closed(result, FAST_PATH_ESCALATION_FAILED)


# =============================================================================
# R-u: the escalation returns no completed critique (F5 at the escalation site)
# =============================================================================


def test_ru1_no_critique_after_escalation_is_unverified_refuse(persisted: Any) -> None:
    runner = _runner(_DraftPolicy(), _critic(passed=False))
    with patch.object(runner, "run_deliberative_path", return_value=(DeliberationState(cycle=1), 0.1, None)):
        result = _drive(runner)
    _assert_fail_closed(result, FAST_PATH_ESCALATION_UNVERIFIED)


def test_ru2_real_empty_skipped_critique_is_unverified_refuse(persisted: Any) -> None:
    state = DeliberationState(cycle=1)
    state.critiques.append(CriticReport.empty_skipped("no principles"))
    state._critique_outcome = "skipped"
    assert state.last_critique.skipped is True
    runner = _runner(_DraftPolicy(), _critic(passed=False))
    with patch.object(runner, "run_deliberative_path", return_value=(state, 0.1, None)):
        result = _drive(runner)
    _assert_fail_closed(result, FAST_PATH_ESCALATION_UNVERIFIED)


def _clean_report() -> SimpleNamespace:
    return SimpleNamespace(skipped=False, violations=[], revision_guidance="")


def test_f1_stale_critique_with_non_completed_outcome_is_unverified(persisted: Any) -> None:
    """D5: a non-skipped report left over from another cycle does not vouch for the escalation."""
    state = DeliberationState(cycle=2)
    state.critiques.append(_clean_report())
    state._critique_outcome = "failed"
    runner = _runner(_DraftPolicy(), _critic(passed=False))
    with (
        patch.object(runner, "run_deliberative_path", return_value=(state, 0.1, None)),
        patch.object(runner, "_build_deliberative_result") as build,
    ):
        result = _drive(runner)
    build.assert_not_called()
    _assert_fail_closed(result, FAST_PATH_ESCALATION_UNVERIFIED)


@pytest.mark.parametrize(
    ("outcome", "builds"),
    [("failed", False), ("timeout", False), ("skipped", False), ("", False), ("completed", True)],
)
def test_f2_escalation_outcome_matrix(persisted: Any, outcome: str, builds: bool) -> None:
    state = DeliberationState(cycle=1)
    state.critiques.append(CriticReport.empty_skipped("no principles") if outcome == "skipped" else _clean_report())
    state._critique_outcome = outcome
    runner = _runner(_DraftPolicy(), _critic(passed=False))
    sentinel = object()
    with (
        patch.object(runner, "run_deliberative_path", return_value=(state, 0.1, None)),
        patch.object(runner, "_build_deliberative_result", return_value=sentinel) as build,
    ):
        result = _drive(runner)
    if builds:
        assert result is sentinel
        build.assert_called_once()
    else:
        build.assert_not_called()
        _assert_fail_closed(result, FAST_PATH_ESCALATION_UNVERIFIED)


# =============================================================================
# R-a: quick_check itself raises -> escalate, never deliver
# =============================================================================


def test_ra1_quick_check_error_escalates_with_original_draft_and_start_time(persisted: Any) -> None:
    runner = _runner(_DraftPolicy(), _critic(exc=RuntimeError("no verdict")))
    sentinel = object()
    start = time.time()
    with (
        patch.object(runner, "run_deliberative_path", return_value=(_completed_state(), 0.1, None)) as esc,
        patch.object(runner, "_build_deliberative_result", return_value=sentinel),
    ):
        result = _drive(runner, start_time=start)
    assert result is sentinel
    esc.assert_called_once()
    assert esc.call_args.kwargs["speculative_draft"] == DRAFT
    assert esc.call_args.args[2] == start


def test_ra2_quick_check_error_and_unverified_escalation_refuses_with_trigger(
    persisted: Any, final_traces: list[Any]
) -> None:
    runner = _runner(_DraftPolicy(), _critic(exc=AttributeError("boom")))
    with patch.object(runner, "run_deliberative_path", return_value=(DeliberationState(cycle=1), 0.1, None)):
        result = _drive(runner)
    _assert_fail_closed(result, FAST_PATH_ESCALATION_UNVERIFIED)
    assert final_traces[-1].stage_payload["escalation_trigger"] == "quick_check_error"


# =============================================================================
# R-up: upstream provenance is never claimed on a fail-closed REFUSE
# =============================================================================


def test_rup_upstream_draft_fail_closed_does_not_claim_upstream_provenance(persisted: Any) -> None:
    runner = _runner(_DraftPolicy(), _critic(passed=False))
    with patch.object(runner, "run_deliberative_path", side_effect=RuntimeError("boom")):
        result = _drive(
            runner,
            speculative_draft="UP-DRAFT",
            draft_provenance=DraftProvenance(origin="upstream", model="client-model-C"),
        )
    meta = result.response.metadata
    assert meta.final_action == "REFUSE"
    assert meta.draft_origin == "internal"
    assert meta.draft_model == ""
    assert meta.internal_draft_reused is False
    assert "UP-DRAFT" not in result.response.content
    assert "UP-DRAFT" not in _metadata_blob(result)


def test_p_r4_refusal_gets_neutral_rationale_and_input_risk_is_untouched(persisted: Any) -> None:
    """Wording only: the refusal LLM must not be told to refuse a benign request for risk reasons."""
    runner = _runner(_DraftPolicy(), _critic(passed=False))
    risk = RiskEstimation(score=0.1, confidence=0.9, risk_category=RiskCategory.BENIGN, rationale="benign factual query")
    with (
        patch.object(runner, "run_deliberative_path", side_effect=RuntimeError("boom")),
        patch.object(runner.assembler, "assemble", wraps=runner.assembler.assemble) as assemble,
    ):
        runner.run_fast_path(
            _request(), risk, time.time(), decision=_decision(), constitution=SimpleNamespace(principles=[])
        )
    passed_risk = assemble.call_args.kwargs["risk_estimation"]
    assert passed_risk.rationale == "The safety check could not be completed."
    assert passed_risk.score == risk.score
    assert risk.rationale == "benign factual query"


# =============================================================================
# R-tr: audit trace
# =============================================================================


def test_rtr_final_trace_exact_payload(persisted: Any, final_traces: list[Any]) -> None:
    runner = _runner(_DraftPolicy(), _critic(passed=False))
    with patch.object(runner, "run_deliberative_path", side_effect=RuntimeError("boom")):
        _drive(runner)
    assert len(final_traces) == 1
    trace = final_traces[0]
    assert trace.stage == "FINAL"
    assert trace.final_action == "REFUSE"
    assert trace.path == "FAST_PATH"
    assert trace.hard_violation_codes == []
    assert trace.hard_violation_source == "fast_path_fail_closed"
    assert trace.stage_payload == {
        "original_final_action": "NORMAL_COMPLETE",
        "flip_reason": FAST_PATH_ESCALATION_FAILED,
        "escalation_trigger": "quick_check_violation",
    }
    assert FAST_PATH_ESCALATION_FAILED in trace.why_not_refuse
    assert "hard_violation" not in trace.why_not_refuse


def test_rtr_trace_append_failure_still_refuses(persisted: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom(_trace: Any) -> None:
        raise RuntimeError("sink down")

    monkeypatch.setattr(f"{_RUNNER_MOD}.append_decision_trace", _boom)
    runner = _runner(_DraftPolicy(), _critic(passed=False))
    with patch.object(runner, "run_deliberative_path", side_effect=RuntimeError("boom")):
        result = _drive(runner)
    _assert_fail_closed(result, FAST_PATH_ESCALATION_FAILED)


def test_rtr_reader_takes_last_final_row_which_is_the_fail_closed_one() -> None:
    """P-R10: the controller-side FINAL row (decision_service, sequence=2) is written
    before ``run_fast_path``; the fail-closed row (also sequence=2) is appended after
    it. Readers take the LAST FINAL row, so the appended row wins."""
    from moralstack.reports.markdown_export import _last_final_trace

    traces = [
        {"stage": "FINAL", "sequence": 2, "trace_json": json.dumps({"final_action": "NORMAL_COMPLETE"})},
        {"stage": "FINAL", "sequence": 2, "trace_json": json.dumps({"final_action": "REFUSE"})},
    ]
    assert _last_final_trace(traces).get("final_action") == "REFUSE"


def test_p_r9a_default_kwargs_guard_trace_is_byte_identical(final_traces: list[Any]) -> None:
    """Literal pin of the hard-violation guard's FINAL trace (default kwargs)."""
    decision = _decision(final_action="REFUSE", path="DELIBERATIVE_PATH", hard=["CORE.PRIV.1"])
    _append_hard_violation_flip_final_trace(
        request_id="req-guard",
        decision=decision,
        original_final_action="SAFE_COMPLETE",
        flip_reason="hard_violation_regeneration_failed",
        decision_explanation=None,
    )
    assert len(final_traces) == 1
    trace = final_traces[0]
    assert trace.hard_violation_source == "hard_violation_guard_fail_closed"
    assert trace.why_not_refuse == "hard_violation_guard flipped to REFUSE: hard_violation_regeneration_failed"
    assert trace.stage_payload == {
        "original_final_action": "SAFE_COMPLETE",
        "flip_reason": "hard_violation_regeneration_failed",
    }
    assert trace.sequence == 2
    assert trace.hard_violation_codes == ["CORE.PRIV.1"]


# =============================================================================
# F3: store configured but constitution unavailable
# =============================================================================


def _failing_store() -> MagicMock:
    store = MagicMock()
    store.get_constitution.side_effect = RuntimeError("overlay load error")
    return store


def test_f3_store_configured_constitution_none_escalates_then_refuses(persisted: Any, final_traces: list[Any]) -> None:
    critic = _critic(passed=True)
    runner = _runner(_DraftPolicy(), critic, store=_failing_store())
    with patch.object(runner, "run_deliberative_path", return_value=(DeliberationState(cycle=1), 0.1, None)) as esc:
        result = _drive(runner, constitution=None)
    esc.assert_called_once()
    critic.quick_check.assert_not_called()
    _assert_fail_closed(result, FAST_PATH_ESCALATION_UNVERIFIED)
    assert final_traces[-1].stage_payload["escalation_trigger"] == "constitution_unavailable"


def test_f3_no_store_configured_check_is_skipped_by_design(persisted: Any) -> None:
    critic = _critic(passed=False)
    runner = _runner(_DraftPolicy(), critic, store=None)
    with patch.object(runner, "run_deliberative_path") as esc:
        result = _drive(runner, constitution=None)
    esc.assert_not_called()
    critic.quick_check.assert_not_called()
    assert result.response.content == DRAFT
    assert result.response.metadata.final_action == "NORMAL_COMPLETE"


def test_f4_critic_none_check_is_skipped_by_design(persisted: Any) -> None:
    runner = _runner(_DraftPolicy(), None, store=_failing_store())
    with patch.object(runner, "run_deliberative_path") as esc:
        result = _drive(runner, constitution=None)
    esc.assert_not_called()
    assert result.response.content == DRAFT
    assert result.response.metadata.final_action == "NORMAL_COMPLETE"


# =============================================================================
# Ledger characterization (D6): a cached fail-closed REFUSE is always reapplied
# (lookup/reapply unchanged; the store skip for these codes is pinned by C11c in
# test_deliberation_critique_fail_closed.py)
# =============================================================================


def test_d6_cached_fail_closed_refuse_is_always_reapplied(persisted: Any) -> None:
    runner = _runner(_DraftPolicy(), _critic(passed=False))
    with patch.object(runner, "run_deliberative_path", side_effect=RuntimeError("boom")):
        result = _drive(runner)
    meta = result.response.metadata
    # Inputs the controller stores in the ledger (controller.py reads metadata.final_action / reason_codes).
    cached = CachedDecision(
        final_action=meta.final_action,
        risk_score=meta.risk_score,
        governance_posture="NORMAL",
        reason_codes=tuple(meta.reason_codes),
    )
    assert cached.final_action == "REFUSE"
    assert FAST_PATH_ESCALATION_FAILED in cached.reason_codes
    ledger_result = LedgerResult(is_hit=True, cached_decision=cached, similarity=0.99, from_turn=1, reason="")
    fp = ConversationalFastPathRunner()
    assert fp.is_safe_to_apply(ledger_result=ledger_result, current_decision=_decision(), current_route="deliberative")
    patched, _route = fp.apply_cached_decision(ledger_result=ledger_result, current_decision=_decision())
    assert patched.final_action == "REFUSE"


# =============================================================================
# Pins
# =============================================================================


def test_rnoop_passed_true_delivers_draft_unchanged(persisted: Any, final_traces: list[Any]) -> None:
    runner = _runner(_DraftPolicy(), _critic(passed=True))
    with patch.object(runner, "run_deliberative_path") as esc:
        result = _drive(runner)
    esc.assert_not_called()
    assert result.response.content == DRAFT
    assert result.response.metadata.final_action == "NORMAL_COMPLETE"
    assert result.response.metadata.hard_violation_flip_reason == ""
    assert final_traces == []


def test_resc_ok_completed_escalation_is_returned_unchanged(persisted: Any) -> None:
    runner = _runner(_DraftPolicy(), _critic(passed=False))
    sentinel = object()
    with (
        patch.object(runner, "run_deliberative_path", return_value=(_completed_state(), 0.1, None)),
        patch.object(runner, "_build_deliberative_result", return_value=sentinel) as build,
    ):
        result = _drive(runner)
    assert result is sentinel
    build.assert_called_once()


def test_edge_quick_check_returning_none_is_no_verdict_and_escalates(persisted: Any) -> None:
    critic = MagicMock()
    critic.quick_check.return_value = None
    runner = _runner(_DraftPolicy(), critic)
    with patch.object(runner, "run_deliberative_path", return_value=(DeliberationState(cycle=1), 0.1, None)) as esc:
        result = _drive(runner)
    esc.assert_called_once()
    _assert_fail_closed(result, FAST_PATH_ESCALATION_UNVERIFIED)


def test_edge_relevant_principles_raising_is_quick_check_error(persisted: Any, final_traces: list[Any]) -> None:
    class _BadAnalysis:
        @property
        def relevant_principles(self) -> Any:
            raise RuntimeError("analysis broken")

    critic = _critic(passed=True)
    runner = _runner(_DraftPolicy(), critic)
    with patch.object(runner, "run_deliberative_path", return_value=(DeliberationState(cycle=1), 0.1, None)) as esc:
        result = _drive(runner, request_analysis=_BadAnalysis())
    esc.assert_called_once()
    critic.quick_check.assert_not_called()
    _assert_fail_closed(result, FAST_PATH_ESCALATION_UNVERIFIED)
    assert final_traces[-1].stage_payload["escalation_trigger"] == "quick_check_error"


def test_edge_empty_draft_fail_closed_still_returns_governed_refusal(persisted: Any) -> None:
    class _EmptyPolicy:
        model = "gpt-policy"

        def generate(self, *args: Any, **kw: Any) -> _Result:
            return _Result("")

    runner = _runner(_EmptyPolicy(), _critic(passed=False))
    with patch.object(runner, "run_deliberative_path", side_effect=RuntimeError("boom")):
        result = _drive(runner)
    meta = result.response.metadata
    assert meta.final_action == "REFUSE"
    assert result.response.content.strip()
    assert finalize_delivery(result).final_text_source == GOVERNED_REFUSAL_SOURCE


def test_rdec_input_decision_is_not_mutated(persisted: Any) -> None:
    decision = _decision()
    before = copy.deepcopy(decision)
    runner = _runner(_DraftPolicy(), _critic(passed=False))
    with patch.object(runner, "run_deliberative_path", side_effect=RuntimeError("boom")):
        _drive(runner, decision=decision)
    assert decision == before


def test_rhv_hard_violations_still_refuse_never_safe_complete(persisted: Any) -> None:
    runner = _runner(_DraftPolicy(), _critic(passed=False))
    with patch.object(runner, "run_deliberative_path", side_effect=RuntimeError("boom")):
        result = _drive(runner, decision=_decision(hard=["CORE.PRIV.1"]))
    assert result.response.metadata.final_action == "REFUSE"
    assert result.response.content != DRAFT


# =============================================================================
# Integration
# =============================================================================


class _RaisingPolicy:
    model = "critic-policy"

    def generate(self, *args: Any, **kw: Any) -> Any:
        raise RuntimeError("provider down")


def test_i1_real_critic_provider_error_escalates_and_never_delivers_draft(persisted: Any) -> None:
    critic = LLMConstitutionalCritic(policy=_RaisingPolicy(), store=None)
    constitution = SimpleNamespace(principles=[_Principle("HARD.1", level="hard")])
    runner = _runner(_DraftPolicy(), critic)
    with patch.object(runner, "run_deliberative_path", wraps=runner.run_deliberative_path) as esc:
        result = _drive(runner, constitution=constitution)
    esc.assert_called_once()
    meta = result.response.metadata
    assert meta.final_action == "REFUSE"
    assert meta.hard_violation_flip_reason == FAST_PATH_ESCALATION_UNVERIFIED
    assert result.response.content != DRAFT
    assert finalize_delivery(result).final_action == "REFUSE"


def test_i2_controller_escalation_failure_is_a_governed_refuse() -> None:
    store = _CountingStore([_Principle("HARD.1", level="hard")])
    critic = MagicMock()
    critic.quick_check.return_value = _quick_check_result(False)
    controller = _make_controller(store, critic)
    with (
        _patch_decide_action(),
        patch.object(DeliberationRunner, "run_deliberative_path", side_effect=RuntimeError("boom")),
    ):
        result = controller.process(ProcessedRequest(prompt="What is the weather like today?"))
    meta = result.response.metadata
    assert meta.hard_violation_flip_reason == FAST_PATH_ESCALATION_FAILED
    assert meta.original_final_action == "NORMAL_COMPLETE"
    delivery = finalize_delivery(result)
    assert delivery.final_action == "REFUSE"
    assert delivery.finish_reason == "content_filter"
    assert delivery.final_text_source == GOVERNED_REFUSAL_SOURCE


def test_i3_controller_quick_check_error_escalates_to_the_full_critique() -> None:
    store = _CountingStore([_Principle("HARD.1", level="hard")])
    critic = MagicMock()
    critic.config = SimpleNamespace(top_k_principles=5)
    critic.quick_check.side_effect = RuntimeError("no verdict")
    critic.critique.return_value = MagicMock(
        violations=[],
        severity_score=0.0,
        has_critical_violations=False,
        violated_hard=False,
        decision="PROCEED",
        revision_guidance="",
        raw_response="{}",
        parse_attempts=1,
        prompt="",
        system_prompt="",
        skipped=False,
    )
    critic.critique_with_relevant_principles.side_effect = AssertionError("must not re-retrieve")
    controller = _make_controller(store, critic)
    with _patch_decide_action():
        result = controller.process(ProcessedRequest(prompt="What is the weather like today?"))
    assert critic.critique.called, "quick_check error did not escalate to the full critique"
    meta = result.response.metadata
    assert meta.final_action == "NORMAL_COMPLETE"
    assert meta.hard_violation_flip_reason == ""


class _EscalatingCritic:
    def quick_check(self, *args: Any, **kwargs: Any) -> Any:
        return SimpleNamespace(passed=False, critical_violation=None)


def test_i4_upstream_draft_is_not_delivered_when_escalation_raises() -> None:
    upstream = "CLIENT-MODEL-C VERBATIM REUSE DRAFT"
    ctrl = OrchestrationController(
        config=OrchestratorConfig(enable_speculative_generation=True),
        policy=MagicMock(model="governance-model-G"),
        risk_estimator=MagicMock(),
        critic=_EscalatingCritic(),
        simulator=None,
        hindsight=None,
        perspectives=None,
        constitution_store=None,
        output_protector=OutputProtector(),
        protected_system_prompt="system",
        persistence=NullPersistence(),
    )
    with (
        patch("moralstack.orchestration.persistence_helpers.async_persist_llm_call"),
        patch.object(ctrl._runner, "run_deliberative_path", side_effect=RuntimeError("boom")),
    ):
        result = ctrl._runner.run_fast_path(
            ProcessedRequest(prompt="p", request_id="req-i4"),
            _risk(0.05),
            time.time(),
            decision=_decision(),
            constitution=SimpleNamespace(),
            speculative_draft=upstream,
            draft_provenance=DraftProvenance(origin="upstream", model="client-model-C"),
        )
    meta = result.response.metadata
    assert meta.final_action == "REFUSE"
    assert result.response.content != upstream
    assert upstream not in _metadata_blob(result)
    assert meta.draft_origin == "internal"
    assert meta.internal_draft_reused is False
