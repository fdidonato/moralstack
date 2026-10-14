"""F5 critique fail-closed: ``DeliberationRunner._critique`` outcome + delivery check.

* O13: the critic-call arguments (all three branches), the prev-violations format and
  the ``llm_calls`` diag are byte-identical (phase 0 pin, unchanged).
* O13b: a critic exception and the 90% gate are recorded into ``state.errors`` plus a
  diagnostic ``critique (ERROR)`` row, and (phase 1) set the per-cycle outcome.
* A (O*): the per-cycle ``DeliberationState._critique_outcome`` recorded by ``_critique``.
* B (C*): the single delivery check in ``OrchestrationController._route_deliberative``
  (``DeliberationRunner.enforce_verified_draft_delivery``): a draft whose cycle critique
  did not complete is never delivered; it becomes a governed REFUSE.
* D (I*): offline end-to-end.

String literals are used for outcomes and reason codes on purpose: they pin the persisted
audit contract and keep this module importable independently of the production constants.
Offline and deterministic.
"""

from __future__ import annotations

import copy
import dataclasses
import json
import logging
import time
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

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
from moralstack.models.decision_explanation import DecisionExplanation
from moralstack.models.delib_context import DelibContext
from moralstack.models.risk import RiskCategory, RiskPolicyAction
from moralstack.models.risk.schema import RiskEstimation
from moralstack.orchestration.controller import OrchestrationController
from moralstack.orchestration.deliberation_runner import DeliberationRunner
from moralstack.orchestration.delivery import GOVERNED_REFUSAL_SOURCE, finalize_delivery
from moralstack.orchestration.null_persistence import NullPersistence
from moralstack.orchestration.orchestration_event_taxonomy import RELEVANT_PRINCIPLES_REUSED
from moralstack.orchestration.process_context import ProcessCallContext
from moralstack.orchestration.response_assembler import ResponseAssembler
from moralstack.orchestration.trace import Trace
from moralstack.orchestration.types import (
    ConvergenceOutcome,
    Decision,
    DeliberationDependencies,
    DeliberationState,
    DraftProvenance,
    OrchestratorConfig,
    ProcessedRequest,
    RequestAnalysisContext,
)
from moralstack.runtime.modules.critic_module import CriticReport
from moralstack.runtime.orchestrator import create_orchestrator

_RUNNER_MOD = "moralstack.orchestration.deliberation_runner"
PROMPT = "the prompt"
DRAFT = "the draft"
CONSTITUTION = SimpleNamespace(name="const")


def _report(**kw: Any) -> SimpleNamespace:
    base: dict[str, Any] = {"violations": [], "revision_guidance": "", "skipped": False}
    base.update(kw)
    return SimpleNamespace(**base)


class _Critic:
    """Critic double recording every call as ``(method, args, kwargs)``."""

    def __init__(self, *, with_principles: bool, store: Any = None, exc: Exception | None = None) -> None:
        self.calls: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []
        self.config = MagicMock(top_k_principles=2)
        self.store = store
        self._exc = exc
        if not with_principles:
            self.critique_with_relevant_principles = None

    def critique(self, *args: Any, **kwargs: Any) -> SimpleNamespace:
        self.calls.append(("critique", args, kwargs))
        if self._exc is not None:
            raise self._exc
        return _report()

    def critique_with_relevant_principles(self, *args: Any, **kwargs: Any) -> SimpleNamespace:
        self.calls.append(("critique_with_relevant_principles", args, kwargs))
        return _report()


def _runner(
    critic: Any,
    store: Any = None,
    timeout_ms: int = 600_000,
    *,
    policy: Any = None,
    config: OrchestratorConfig | None = None,
    output_protector: Any = None,
) -> DeliberationRunner:
    deps = DeliberationDependencies(
        policy=policy if policy is not None else MagicMock(),
        critic=critic,
        simulator=None,
        hindsight=None,
        perspectives=None,
        constitution_store=store,
        output_protector=output_protector if output_protector is not None else MagicMock(),
    )
    cfg = config if config is not None else OrchestratorConfig(timeout_ms=timeout_ms)
    return DeliberationRunner(cfg, deps, "protected", None, ResponseAssembler(policy=None))


@pytest.fixture
def diags(monkeypatch: pytest.MonkeyPatch) -> list[tuple[dict[str, Any], dict[str, Any] | None]]:
    captured: list[tuple[dict[str, Any], dict[str, Any] | None]] = []

    def _record(_logger: Any, diag: Any, persist_kwargs: Any) -> None:
        captured.append((diag, persist_kwargs))

    monkeypatch.setattr(f"{_RUNNER_MOD}.record_llm_call", _record)
    return captured


def _request() -> ProcessedRequest:
    return ProcessedRequest(
        prompt=PROMPT,
        request_id="req-o13",
        developer_contract="DEVCONTRACT",
        conversation_history=[{"role": "user", "content": "hi"}],
        user_context={"domain": "medical"},
    )


def _seeded_state() -> DeliberationState:
    state = DeliberationState(cycle=1)
    state.draft_response = DRAFT
    violations = [SimpleNamespace(principle_id=f"P{i}", rationale=f"{i}" * 100) for i in range(4)]
    state.critiques.append(_report(violations=violations, revision_guidance="PREV-GUIDANCE"))
    return state


EXPECTED_PREV_VIOLATIONS = "; ".join(f"P{i}: {str(i) * 80}" for i in range(3))
EXPECTED_PREV_GUIDANCE = "PREV-GUIDANCE"


def _assert_diag(diags: list[tuple[dict[str, Any], dict[str, Any] | None]]) -> None:
    assert len(diags) == 1
    diag, persist_kwargs = diags[0]
    assert diag["action"] == "critique"
    assert diag["prompt"] == f"CRITIQUE\nPrompt: {PROMPT}\nResponse: {DRAFT}"
    assert diag["response"] == "Violations: 0, Guidance: N/A"
    assert persist_kwargs is not None
    assert persist_kwargs["parsed_summary_json"] == "Violations: 0, Guidance: N/A"


def test_o13_critic_call_arguments_byte_identical(diags: list[tuple[dict[str, Any], dict[str, Any] | None]]) -> None:
    common_kwargs = {
        "request_id": "req-o13",
        "delib_context": None,
        "previous_violations": EXPECTED_PREV_VIOLATIONS,
        "previous_guidance": EXPECTED_PREV_GUIDANCE,
        "developer_contract": "DEVCONTRACT",
        "conversation_history": [{"role": "user", "content": "hi"}],
    }

    # Branch 1: precomputed request analysis (principles sliced to critic top_k=2).
    p1, p2, p3 = (SimpleNamespace(id=n) for n in ("a", "b", "c"))
    analysis = RequestAnalysisContext(relevant_principles=(p1, p2, p3), constitution=CONSTITUTION, detected_domain="medical")
    critic1 = _Critic(with_principles=True)
    state1 = _seeded_state()
    out1 = _runner(critic1, store=object())._critique(state1, _request(), constitution=None, request_analysis=analysis)
    assert out1 is state1
    assert critic1.calls == [
        (
            "critique",
            (PROMPT, DRAFT, CONSTITUTION),
            {"principles": [p1, p2], **common_kwargs},
        )
    ]
    assert state1.errors == []
    assert len(state1.critiques) == 2
    assert state1._request_analysis_reuse_targets == ["critic"]
    _assert_diag(diags)
    diags.clear()

    # Branch 2: critique_with_relevant_principles with a store.
    critic2 = _Critic(with_principles=True, store=object())
    state2 = _seeded_state()
    _runner(critic2, store=object())._critique(state2, _request(), constitution=None, request_analysis=None)
    assert critic2.calls == [
        (
            "critique_with_relevant_principles",
            (),
            {"request": PROMPT, "response": DRAFT, "domain": "medical", **common_kwargs},
        )
    ]
    assert state2.errors == []
    assert len(state2.critiques) == 2
    _assert_diag(diags)
    diags.clear()

    # Branch 3: plain critique with the constitution passed in.
    critic3 = _Critic(with_principles=False)
    state3 = _seeded_state()
    _runner(critic3)._critique(state3, _request(), constitution=CONSTITUTION, request_analysis=None)
    assert critic3.calls == [("critique", (PROMPT, DRAFT, CONSTITUTION), common_kwargs)]
    assert state3.errors == []
    assert len(state3.critiques) == 2
    _assert_diag(diags)


def test_o13b_critique_error_path_unchanged(diags: list[tuple[dict[str, Any], dict[str, Any] | None]]) -> None:
    critic = _Critic(with_principles=False, exc=RuntimeError("boom"))
    state = DeliberationState(cycle=1)
    state.draft_response = DRAFT
    out = _runner(critic)._critique(state, _request(), constitution=CONSTITUTION)
    assert out is state
    assert state.errors == ["Critique error: boom"]
    assert state.critiques == []
    assert state._critique_outcome == "failed"
    assert len(diags) == 1
    diag, persist_kwargs = diags[0]
    assert diag["action"] == "critique (ERROR)"
    assert persist_kwargs is None

    # 90% gate: the critic is not called and the timeout is swallowed the same way.
    diags.clear()
    critic2 = _Critic(with_principles=False)
    state2 = DeliberationState(cycle=1)
    state2.draft_response = DRAFT
    _runner(critic2, timeout_ms=600_000)._critique(
        state2, _request(), constitution=CONSTITUTION, start_time=time.time() - 570
    )
    assert critic2.calls == []
    assert state2.errors[0].startswith("Critique error: Timeout approaching before critique")
    assert state2.critiques == []
    assert state2._critique_outcome == "timeout"
    assert len(diags) == 1
    assert diags[0][0]["action"] == "critique (ERROR)"
    assert diags[0][1] is None


# =============================================================================
# Harness (copied, not imported across modules)
# =============================================================================

DRAFT_TEXT = "UNVERIFIED-DRAFT"
REFUSAL_TEXT = "GOVERNED-REFUSAL-TEXT"
CODE_FAILED = "deliberation_critique_failed"
CODE_TIMEOUT = "deliberation_critique_timeout"
CODE_UNVERIFIED = "deliberation_critique_unverified"


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


class _StubCritic:
    """Critic with only ``critique`` (never a MagicMock): ``_critique`` takes branch 3.

    ``items`` are returned/raised in order; the last item is repeated once exhausted
    (an exhausted ``side_effect`` list would raise ``StopIteration`` and look like a failure).
    """

    def __init__(self, items: list[Any] | None = None, *, read_principles: bool = False) -> None:
        self.config = MagicMock(top_k_principles=20)
        self.calls: list[tuple[tuple[Any, ...], dict[str, Any]]] = []
        self._items = list(items) if items else [_report()]
        self._read_principles = read_principles

    def critique(self, *args: Any, **kwargs: Any) -> Any:
        idx = min(len(self.calls), len(self._items) - 1)
        self.calls.append((args, kwargs))
        if self._read_principles:
            _ = args[2].principles  # AttributeError when the constitution is None
        item = self._items[idx]
        if isinstance(item, BaseException):
            raise item
        return item


class _FailingStore:
    """A configured store whose constitution load fails (F3 analog)."""

    def get_constitution(self, domain: str | None = None) -> Any:
        raise RuntimeError("overlay load error")

    def get_relevant_principles(self, query: str, top_k: int = 10, domain: str | None = None) -> list[Any]:
        return []


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
    """Every trace handed to ``append_decision_trace`` by the runner."""
    traces: list[Any] = []
    monkeypatch.setattr(f"{_RUNNER_MOD}.append_decision_trace", traces.append)
    return traces


def _passthrough_protector() -> MagicMock:
    m = MagicMock()
    m.validate.side_effect = lambda text: SimpleNamespace(cleaned=text, had_leakage=False)
    return m


def _state(outcome: str = "", *, cycle: int = 1, errors: list[str] | None = None) -> DeliberationState:
    state = DeliberationState(cycle=cycle, draft_response="DRAFT-X")
    state.errors = list(errors or [])
    state._critique_outcome = outcome
    return state


def _risk(score: float = 0.1, **kw: Any) -> RiskEstimation:
    return RiskEstimation(score=score, confidence=0.9, risk_category=RiskCategory.BENIGN, **kw)


def _sensitive_risk() -> RiskEstimation:
    """SENSITIVE category: ``apply_safe_complete_gating`` keeps a SAFE_COMPLETE as is."""
    return RiskEstimation(score=0.5, confidence=0.9, risk_category=RiskCategory.SENSITIVE)


def _decision(final_action: str = "NORMAL_COMPLETE", hard: list[str] | None = None) -> Decision:
    return Decision(
        final_action=final_action,
        path="DELIBERATIVE_PATH",
        intent_clarity="HIGH",
        misuse_plausibility="LOW",
        actionability_risk="LOW",
        triggered_principles=list(hard or []),
        hard_violations=list(hard or []),
        risk_signals=[],
    )


def _explanation(final_action: str = "NORMAL_COMPLETE") -> DecisionExplanation:
    return DecisionExplanation(
        request_id="req-c",
        final_action=final_action,
        risk_score=0.1,
        risk_category="benign",
        winning_rule="decide_action",
        reason_codes=[],
        why_not_refuse="orig why_not_refuse",
        why_not_safe_complete="orig why_not_safe_complete",
        why_not_normal_complete="orig why_not_normal_complete",
    )


# =============================================================================
# A. Outcome recording (runner level)
# =============================================================================


def _fresh_state(cycle: int = 1) -> DeliberationState:
    state = DeliberationState(cycle=cycle)
    state.draft_response = DRAFT
    return state


def _crit(runner: DeliberationRunner, state: DeliberationState, **kw: Any) -> DeliberationState:
    kw.setdefault("constitution", CONSTITUTION)
    return runner._critique(state, _request(), **kw)


def test_o1_completed_report_sets_completed(diags: Any) -> None:
    state = _fresh_state()
    _crit(_runner(_StubCritic()), state)
    assert state._critique_outcome == "completed"
    assert len(state.critiques) == 1
    assert state.errors == []


def test_o2_real_empty_skipped_sets_skipped(diags: Any) -> None:
    report = CriticReport.empty_skipped("no principles")
    state = _fresh_state()
    _crit(_runner(_StubCritic([report])), state)
    assert state._critique_outcome == "skipped"
    assert state.critiques == [report]
    assert state.errors == []


def test_o3_unconfigured_magicmock_report_is_completed_not_skipped(diags: Any) -> None:
    """``skipped`` on a bare MagicMock is a truthy mock: the outcome check must be ``is True``."""
    state = _fresh_state()
    _crit(_runner(_StubCritic([MagicMock(violations=[], revision_guidance="")])), state)
    assert state._critique_outcome == "completed"
    assert len(state.critiques) == 1


def test_o4_critic_raises_sets_failed_logs_warning(diags: Any, caplog: pytest.LogCaptureFixture) -> None:
    state = _fresh_state()
    with caplog.at_level(logging.WARNING, logger=_RUNNER_MOD):
        out = _crit(_runner(_StubCritic([RuntimeError("boom")])), state)
    assert out is state
    assert state._critique_outcome == "failed"
    assert state.critiques == []
    assert state.errors == ["Critique error: boom"]
    msgs = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    hits = [m for m in msgs if m.startswith("deliberation critique did not complete")]
    assert len(hits) == 1
    assert "outcome=failed" in hits[0]
    assert "RuntimeError" in hits[0]
    assert "request_id=req-o13" in hits[0]


def test_o4_warning_truncates_error_text(diags: Any, caplog: pytest.LogCaptureFixture) -> None:
    """Provider exceptions can embed response bodies: the WARNING carries at most 200 chars."""
    state = _fresh_state()
    with caplog.at_level(logging.WARNING, logger=_RUNNER_MOD):
        _crit(_runner(_StubCritic([RuntimeError("X" * 500)])), state)
    hit = next(r.getMessage() for r in caplog.records if r.getMessage().startswith("deliberation critique did not"))
    assert "X" * 200 in hit
    assert "X" * 201 not in hit
    # the (pre-existing, unchanged) errors text keeps the full message
    assert state.errors == ["Critique error: " + "X" * 500]


def test_o4b_failure_diagnostic_row_still_written(diags: Any) -> None:
    state = _fresh_state()
    _crit(_runner(_StubCritic([RuntimeError("boom")])), state)
    assert len(diags) == 1
    diag, persist_kwargs = diags[0]
    assert diag["action"] == "critique (ERROR)"
    assert diag["response"] == "ERROR: boom"
    assert persist_kwargs is None


def test_o5_ninety_percent_gate_sets_timeout(diags: Any) -> None:
    critic = _StubCritic()
    state = _fresh_state()
    _crit(_runner(critic, timeout_ms=600_000), state, start_time=time.time() - 570)
    assert critic.calls == []
    assert state._critique_outcome == "timeout"
    assert state.errors[0].startswith("Critique error: Timeout approaching before critique")


@pytest.mark.parametrize(("elapsed_s", "expected_calls", "expected"), [(540.0, 1, "completed"), (540.6, 0, "timeout")])
def test_o5b_gate_boundary(
    monkeypatch: pytest.MonkeyPatch, diags: Any, elapsed_s: float, expected_calls: int, expected: str
) -> None:
    """Exactly 90.0% of the budget still runs the critic; 90.1% does not (fake clock, no sleeping)."""
    t0 = 1_000.0
    monkeypatch.setattr(f"{_RUNNER_MOD}.time", SimpleNamespace(time=lambda: t0 + elapsed_s))
    critic = _StubCritic()
    state = _fresh_state()
    _crit(_runner(critic, timeout_ms=600_000), state, start_time=t0)
    assert len(critic.calls) == expected_calls
    assert state._critique_outcome == expected


def test_o5c_unevaluable_deadline_fails_closed_without_raising(diags: Any) -> None:
    """timeout_ms=0 makes the deadline division raise: _critique still never raises."""
    critic = _StubCritic()
    state = _fresh_state()
    out = _crit(_runner(critic, timeout_ms=0), state, start_time=time.time())
    assert out is state
    assert critic.calls == []
    assert state._critique_outcome == "failed"
    assert state.errors and state.errors[0].startswith("Critique error")


@pytest.mark.parametrize(
    "bad",
    [None, object(), SimpleNamespace(revision_guidance="")],
    ids=["none", "object", "no-violations"],
)
def test_o6_non_conforming_report_is_failed(diags: Any, bad: Any) -> None:
    """A report that is not a critic report is never "no violation" (rejects a plain getattr)."""
    state = _fresh_state()
    _crit(_runner(_StubCritic([bad])), state)
    assert state._critique_outcome == "failed"
    assert state.critiques == []
    assert state.errors and state.errors[0].startswith("Critique error")


def test_o6b_acceptance_set_unchanged(diags: Any) -> None:
    state = _fresh_state()
    _crit(_runner(_StubCritic([SimpleNamespace(violations=[], revision_guidance=None, skipped=False)])), state)
    assert state._critique_outcome == "completed"
    assert len(state.critiques) == 1
    assert diags[0][0]["response"] == "Violations: 0, Guidance: N/A"


@pytest.mark.parametrize("closed", ["no-critic", "no-store-no-constitution"])
def test_o7a_gate_closed_is_silent_noop(diags: Any, closed: str) -> None:
    critic = _StubCritic()
    state = _fresh_state()
    if closed == "no-critic":
        out = _runner(None, store=object())._critique(state, _request(), constitution=CONSTITUTION)
    else:
        out = _runner(critic, store=None)._critique(state, _request(), constitution=None)
    assert out is state
    assert critic.calls == []
    assert state.errors == []
    assert state.critiques == []
    assert diags == []


@pytest.mark.parametrize("closed", ["no-critic", "no-store-no-constitution"])
def test_o7b_gate_closed_leaves_outcome_empty(diags: Any, closed: str) -> None:
    state = _fresh_state()
    if closed == "no-critic":
        _runner(None, store=object())._critique(state, _request(), constitution=CONSTITUTION)
    else:
        _runner(_StubCritic(), store=None)._critique(state, _request(), constitution=None)
    assert state._critique_outcome == ""


def test_o7c_store_configured_constitution_unavailable_still_calls_critic(diags: Any) -> None:
    critic = _StubCritic(read_principles=True)
    state = _fresh_state()
    _runner(critic, store=_FailingStore())._critique(state, _request(), constitution=None)
    assert len(critic.calls) == 1
    assert state._critique_outcome == "failed"


def test_o8_outcome_reset_at_start_of_each_cycle(persisted: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    runner = _runner(_StubCritic(), config=OrchestratorConfig(parallel_module_calls=False))
    monkeypatch.setattr(runner, "_generate_or_revise", lambda s, r, **k: s)
    monkeypatch.setattr(runner, "_run_critique_simulate_perspectives_sequential", lambda s, r, **k: s)
    monkeypatch.setattr(runner, "_apply_hindsight_if_needed", lambda s, r, d, **k: s)
    monkeypatch.setattr(runner, "_finalize_cycle", lambda s, m, **k: s)
    state = _state("completed", cycle=0)
    runner._deliberation_cycle(state, _request(), risk_estimation=None, max_cycles=1)
    assert state.cycle == 1
    assert state._critique_outcome == ""


def test_o8b_recovery_failed_then_completed(diags: Any) -> None:
    runner = _runner(_StubCritic([RuntimeError("boom"), _report()]))
    state = _fresh_state()
    _crit(runner, state)
    assert state._critique_outcome == "failed"
    _crit(runner, state)
    assert state._critique_outcome == "completed"
    assert len(state.critiques) == 1


def test_o9_fork_carries_outcome() -> None:
    state = DeliberationState(cycle=1)
    state._critique_outcome = "timeout"
    assert state.fork()._critique_outcome == "timeout"


def test_o9_fork_copies_every_field() -> None:
    """Structural: a new DeliberationState field that ``fork()`` forgets to copy fails here."""
    not_copied: frozenset[str] = frozenset()  # explicit allow-list: nothing is exempt today
    state = DeliberationState()
    for f in dataclasses.fields(state):
        if f.name in not_copied:
            continue
        value: Any
        if f.default_factory is list:
            value = [f"m-{f.name}"]
        elif f.name == "_convergence_evaluation_snapshot":
            value = {"m": f.name}
        else:
            value = f"m-{f.name}"
        setattr(state, f.name, value)
    forked = state.fork()
    missing = [
        f.name
        for f in dataclasses.fields(state)
        if f.name not in not_copied and getattr(forked, f.name) != getattr(state, f.name)
    ]
    assert missing == []


@pytest.mark.parametrize("raises", [True, False], ids=["raises", "clean"])
@pytest.mark.parametrize("mode", ["sequential", "critic_gated", "full_parallel"])
def test_o10_outcome_survives_every_scheduler(persisted: Any, mode: str, raises: bool) -> None:
    cfg = OrchestratorConfig(
        max_deliberation_cycles=1,
        enable_simulation=False,
        enable_perspectives=False,
        enable_hindsight=False,
        parallel_module_calls=(mode != "sequential"),
        parallel_critic_with_modules=(mode == "full_parallel"),
        enable_dynamic_parallel_scheduler=False,
    )
    critic = _StubCritic([RuntimeError("boom")] if raises else None)
    runner = _runner(critic, policy=_DraftPolicy(), config=cfg, output_protector=_passthrough_protector())
    try:
        state, _score, _outcome = runner.run_deliberative_path(_request(), _risk(), time.time(), constitution=CONSTITUTION)
    finally:
        runner.close()
    assert len(critic.calls) == 1
    assert state._critique_outcome == ("failed" if raises else "completed")


def test_o10b_full_parallel_hard_violation_early_return_keeps_completed(persisted: Any) -> None:
    """The merge must sit with the critic merge, before the hard-violation early return."""
    cfg = OrchestratorConfig(
        enable_simulation=False,
        enable_perspectives=False,
        enable_hindsight=False,
        enable_dynamic_parallel_scheduler=False,
    )
    hard_report = _report(violated_hard=True, decision="REFUSE", violations=[])
    runner = _runner(_StubCritic([hard_report]), config=cfg)
    state = _fresh_state()
    try:
        out = runner._run_full_parallel_evaluation(state, _request(), constitution=CONSTITUTION)
    finally:
        runner.close()
    assert out.last_critique is hard_report
    assert out._critique_outcome == "completed"


def test_o11_telemetry_failure_never_drops_a_completed_critique(monkeypatch: pytest.MonkeyPatch) -> None:
    def _record(_logger: Any, diag: Any, _persist: Any) -> None:
        if diag["action"] == "critique":
            raise RuntimeError("telemetry down")

    monkeypatch.setattr(f"{_RUNNER_MOD}.record_llm_call", _record)
    state = _fresh_state()
    _crit(_runner(_StubCritic()), state)
    assert state._critique_outcome == "completed"
    assert len(state.critiques) == 1
    assert not any(e.startswith("Critique error") for e in state.errors)


def test_o11b_failure_diagnostic_write_cannot_escape(monkeypatch: pytest.MonkeyPatch) -> None:
    def _record(_logger: Any, _diag: Any, _persist: Any) -> None:
        raise RuntimeError("diagnostics down")

    monkeypatch.setattr(f"{_RUNNER_MOD}.record_llm_call", _record)
    state = _fresh_state()
    out = _crit(_runner(_StubCritic([RuntimeError("boom")])), state)
    assert out is state
    assert state._critique_outcome == "failed"


def test_o12_post_append_context_propagation_failure_keeps_critique(diags: Any) -> None:
    report = _report(violations=[object()])
    state = _fresh_state()
    _crit(_runner(_StubCritic([report])), state, delib_context=DelibContext())
    assert state.critiques == [report]
    assert state._critique_outcome == "completed"
    assert state.errors and state.errors[-1].startswith("Critique error")


def test_o14_reuse_target_and_event_still_emitted(diags: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    events: list[dict[str, Any]] = []
    monkeypatch.setattr(f"{_RUNNER_MOD}.persist_orchestration_event", lambda **kw: events.append(kw))
    analysis = RequestAnalysisContext(
        relevant_principles=(SimpleNamespace(id="a"),), constitution=CONSTITUTION, detected_domain="medical"
    )
    state = _fresh_state()
    _runner(_StubCritic(), store=object())._critique(state, _request(), constitution=None, request_analysis=analysis)
    assert state._request_analysis_reuse_targets == ["critic"]
    reused = [e for e in events if e.get("event_type") == RELEVANT_PRINCIPLES_REUSED]
    assert len(reused) == 1
    assert reused[0]["payload"]["reuse_target"] == "critic"


# =============================================================================
# B. Delivery site (controller)
# =============================================================================


class _GenResult:
    text = REFUSAL_TEXT
    prompt_used = None
    system_used = None
    tokens_used = 5
    prompt_tokens = 3
    completion_tokens = 2
    token_usage_source = "exact"

    def token_usage_json(self) -> str | None:
        return None


def _controller(
    *, critic: Any = "stub", store: Any = "mock", policy: Any = None, config: OrchestratorConfig | None = None
) -> Any:
    ctrl = OrchestrationController(
        config=config or OrchestratorConfig(),
        policy=policy or MagicMock(model="policy-model"),
        risk_estimator=MagicMock(),
        critic=_StubCritic() if critic == "stub" else critic,
        simulator=None,
        hindsight=None,
        perspectives=None,
        constitution_store=MockConstitutionStore() if store == "mock" else store,
        output_protector=_passthrough_protector(),
        protected_system_prompt="system",
        persistence=NullPersistence(),
    )
    if policy is None:
        ctrl._runner.policy.generate.return_value = _GenResult()
    return ctrl


def _outcome(stop_reason: str = "CONVERGED", cycle: int = 1) -> ConvergenceOutcome:
    return ConvergenceOutcome(
        should_continue=False,
        converged=(stop_reason == "CONVERGED"),
        stop_reason=stop_reason,
        cycle=cycle,
        max_cycles=2,
    )


def _route(
    ctrl: Any,
    state: DeliberationState,
    *,
    risk: RiskEstimation | None = None,
    category: RiskCategory = RiskCategory.BENIGN,
    overlay_sensitive: bool = False,
    stop_reason: str = "CONVERGED",
    start_time: float | None = None,
    speculative_draft: str | None = None,
    draft_provenance: DraftProvenance | None = None,
    trace: Trace | None = None,
    patch_run: bool = True,
) -> Any:
    request = ProcessedRequest(prompt="p", request_id="req-c")
    kwargs: dict[str, Any] = {
        "pre_decision": None,
        "speculative_draft": speculative_draft,
        "call_ctx": ProcessCallContext(),
        "request_analysis": None,
        "draft_provenance": draft_provenance,
    }
    args = (
        request,
        risk or _risk(),
        category,
        False,
        overlay_sensitive,
        time.time() if start_time is None else start_time,
        trace or Trace(request_id="req-c"),
    )
    if not patch_run:
        return ctrl._route_deliberative(*args, **kwargs)
    with patch.object(ctrl._runner, "run_deliberative_path", return_value=(state, 0.1, _outcome(stop_reason, state.cycle))):
        return ctrl._route_deliberative(*args, **kwargs)


def _draft_state(outcome: str, *, cycle: int = 1, draft: str = DRAFT_TEXT, **kw: Any) -> DeliberationState:
    state = DeliberationState(cycle=cycle, draft_response=draft)
    state._critique_outcome = outcome
    for k, v in kw.items():
        setattr(state, k, v)
    return state


def _blob(result: Any) -> str:
    return json.dumps(dataclasses.asdict(result.response.metadata), default=str)


def _assert_deliberation_fail_closed(
    result: Any,
    code: str,
    *,
    original: str = "NORMAL_COMPLETE",
    state: DeliberationState | None = None,
    draft: str = DRAFT_TEXT,
) -> None:
    meta = result.response.metadata
    assert meta.final_action == "REFUSE"
    assert meta.hard_violation_flip_reason == code
    assert meta.original_final_action == original
    assert code in meta.reason_codes
    assert meta.why_not_refuse.strip()
    assert result.response.content.strip()
    assert draft not in result.response.content
    assert draft not in _blob(result)
    assert meta.draft_origin == "internal"
    assert meta.internal_draft_reused is False
    assert result.path == "DELIBERATIVE_PATH"
    if state is not None:
        assert result.total_cycles == state.cycle
    assert meta.winning_rule == "deliberation_fail_closed"
    delivery = finalize_delivery(result)
    assert delivery.final_action == "REFUSE"
    assert delivery.finish_reason == "content_filter"
    assert delivery.final_text_source == GOVERNED_REFUSAL_SOURCE


def test_c1_failed_final_cycle_refuses() -> None:
    state = _draft_state("failed")
    result = _route(_controller(), state)
    _assert_deliberation_fail_closed(result, CODE_FAILED, state=state)


def test_c2_timeout_final_cycle_refuses() -> None:
    state = _draft_state("timeout")
    result = _route(_controller(), state)
    _assert_deliberation_fail_closed(result, CODE_TIMEOUT, state=state)


@pytest.mark.parametrize("cycle", [1, 0])
def test_c3_unverified_empty_outcome_refuses(cycle: int) -> None:
    state = _draft_state("", cycle=cycle)
    result = _route(_controller(), state)
    _assert_deliberation_fail_closed(result, CODE_UNVERIFIED, state=state)


def test_c4_skipped_is_accepted(final_traces: list[Any]) -> None:
    """S2: a skipped critique (zero retrieved principles) is accepted as covered."""
    state = _draft_state("skipped", critiques=[CriticReport.empty_skipped("no principles")])
    result = _route(_controller(), state)
    meta = result.response.metadata
    assert meta.final_action == "NORMAL_COMPLETE"
    assert meta.hard_violation_flip_reason == ""
    assert meta.original_final_action == ""
    assert final_traces == []


def test_c4b_skipped_logs_warning(caplog: pytest.LogCaptureFixture) -> None:
    state = _draft_state("skipped", critiques=[CriticReport.empty_skipped("no principles")])
    with caplog.at_level(logging.WARNING, logger=_RUNNER_MOD):
        result = _route(_controller(), state)
    assert result.response.metadata.final_action == "NORMAL_COMPLETE"
    hits = [r.getMessage() for r in caplog.records if r.getMessage().startswith("deliberation critique skipped")]
    assert len(hits) == 1
    assert "delivered under S2" in hits[0]
    assert "skip_reason=no principles" in hits[0]
    assert "cycle=1" in hits[0]


def test_c5_completed_is_byte_identical_noop(final_traces: list[Any]) -> None:
    risk = _risk(rationale="benign factual query")
    explanation = _explanation()

    def _passthrough(req: Any, st: Any, dec: Any, *, constitution: Any, decision_explanation: Any) -> Any:
        return dec, decision_explanation, "", ""

    def _run(passthrough: bool) -> tuple[Any, Any]:
        ctrl = _controller()
        state = _draft_state("completed")
        with (
            patch("moralstack.orchestration.controller.decide_action", return_value=(_decision(), explanation)),
            patch.object(ctrl.assembler, "assemble", wraps=ctrl.assembler.assemble) as assemble,
        ):
            if passthrough:
                with patch.object(ctrl._runner, "enforce_verified_draft_delivery", _passthrough, create=True):
                    result = _route(ctrl, state, risk=risk)
            else:
                result = _route(ctrl, state, risk=risk)
        return result, assemble

    plain, assemble = _run(False)
    meta = plain.response.metadata
    assert meta.final_action == "NORMAL_COMPLETE"
    assert meta.hard_violation_flip_reason == ""
    assert meta.original_final_action == ""
    assert final_traces == []
    assert assemble.call_args.kwargs["risk_estimation"] is risk
    assert assemble.call_args.kwargs["decision_explanation"] is explanation

    ref, _ = _run(True)

    def _stable(res: Any) -> dict[str, Any]:
        d = dataclasses.asdict(res.response.metadata)
        return {k: v for k, v in d.items() if "time" not in k}

    assert _stable(plain) == _stable(ref)
    # The private per-cycle outcome never leaks into metadata or any trace payload.
    assert "critique_outcome" not in _blob(plain)


def test_c6_gate_closed_is_unchanged(final_traces: list[Any]) -> None:
    for kwargs in ({"critic": None}, {"store": None}):
        for outcome in ("failed", ""):
            result = _route(_controller(**kwargs), _draft_state(outcome))
            meta = result.response.metadata
            assert meta.final_action == "NORMAL_COMPLETE", (kwargs, outcome)
            assert meta.hard_violation_flip_reason == ""
            assert DRAFT_TEXT in result.response.content
    assert final_traces == []


def test_c7_already_refuse_is_not_reflipped(final_traces: list[Any]) -> None:
    refuse = _decision("REFUSE")
    with patch("moralstack.orchestration.controller.decide_action", return_value=(refuse, _explanation("REFUSE"))):
        result = _route(_controller(), _draft_state("failed"))
    meta = result.response.metadata
    assert meta.final_action == "REFUSE"
    assert meta.hard_violation_flip_reason == ""
    assert meta.original_final_action == ""
    assert final_traces == []


def test_c8_hard_signal_supremacy_flip_keeps_hard_violations() -> None:
    critic = _StubCritic()
    ctrl = _controller(critic=critic)
    decision = _decision("SAFE_COMPLETE", hard=["CORE.PRIV.1"])
    with patch("moralstack.orchestration.controller.decide_action", return_value=(decision, _explanation("SAFE_COMPLETE"))):
        result = _route(ctrl, _draft_state("failed"))
    meta = result.response.metadata
    _assert_deliberation_fail_closed(result, CODE_FAILED, original="SAFE_COMPLETE")
    assert list(meta.hard_violations) == ["CORE.PRIV.1"]
    assert not meta.hard_violation_flip_reason.startswith("hard_violation")
    assert critic.calls == []  # the guard did not regenerate / re-critique


def test_c8b_check_runs_after_cycles_exhausted_fallback() -> None:
    ctrl = _controller()
    with patch(
        "moralstack.orchestration.controller.decide_action",
        return_value=(_decision("NORMAL_COMPLETE"), _explanation()),
    ):
        result = _route(
            ctrl,
            _draft_state("failed"),
            risk=_sensitive_risk(),
            category=RiskCategory.SENSITIVE,
            stop_reason="CYCLES_EXHAUSTED",
        )
    # The fallback first made it SAFE_COMPLETE, then the check flipped it to REFUSE; the
    # fallback's own explanation (winning_rule "cycles_exhausted_fallback") is not delivered.
    _assert_deliberation_fail_closed(result, CODE_FAILED, original="SAFE_COMPLETE")


def test_c9_stale_cycle1_report_does_not_vouch_for_cycle2_draft() -> None:
    state = _draft_state("failed", cycle=2, critiques=[_report()])
    result = _route(_controller(), state)
    _assert_deliberation_fail_closed(result, CODE_FAILED, state=state)


def test_c10_override_cannot_downgrade_the_flip() -> None:
    with patch(
        "moralstack.orchestration.controller.evaluate_deliberation_override",
        return_value=_decision("SAFE_COMPLETE"),
    ):
        result = _route(_controller(), _draft_state("failed"), risk=_sensitive_risk())
    _assert_deliberation_fail_closed(result, CODE_FAILED, original="SAFE_COMPLETE")


def test_c18b_check_raises_on_already_refuse_leaves_decision_untouched(final_traces: list[Any]) -> None:
    ctrl = _controller()
    explanation = _explanation("REFUSE")
    with (
        patch("moralstack.orchestration.controller.decide_action", return_value=(_decision("REFUSE"), explanation)),
        patch.object(ctrl._runner, "enforce_verified_draft_delivery", side_effect=RuntimeError("check broke"), create=True),
        patch.object(ctrl.assembler, "assemble", wraps=ctrl.assembler.assemble) as assemble,
    ):
        result = _route(ctrl, _draft_state("failed"))
    meta = result.response.metadata
    assert meta.final_action == "REFUSE"
    assert meta.hard_violation_flip_reason == ""
    assert meta.original_final_action == ""
    assert assemble.call_args.kwargs["decision_explanation"] is explanation
    assert final_traces == []


def test_c12_final_trace_exact_payload(final_traces: list[Any]) -> None:
    _route(_controller(), _draft_state("failed"))
    assert len(final_traces) == 1
    trace = final_traces[0]
    assert trace.stage == "FINAL"
    assert trace.final_action == "REFUSE"
    assert trace.path == "DELIBERATIVE_PATH"
    assert trace.sequence == 2
    assert trace.hard_violation_source == "deliberation_fail_closed"
    assert trace.hard_violation_codes == []
    assert trace.stage_payload == {
        "original_final_action": "NORMAL_COMPLETE",
        "flip_reason": CODE_FAILED,
        "critique_outcome": "failed",
        "cycle": "1",
    }
    assert trace.why_not_refuse.startswith("deliberation_fail_closed flipped to REFUSE:")

    final_traces.clear()
    _route(_controller(), _draft_state(""))
    assert final_traces[0].stage_payload["critique_outcome"] == "not_invoked"
    assert final_traces[0].stage_payload["flip_reason"] == CODE_UNVERIFIED


def test_c12b_trace_append_failure_still_refuses(monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom(_trace: Any) -> None:
        raise RuntimeError("sink down")

    monkeypatch.setattr(f"{_RUNNER_MOD}.append_decision_trace", _boom)
    state = _draft_state("failed")
    result = _route(_controller(), state)
    _assert_deliberation_fail_closed(result, CODE_FAILED, state=state)


def test_c12c_flip_logs_warning(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING, logger=_RUNNER_MOD):
        _route(_controller(), _draft_state("failed"))
    hits = [r.getMessage() for r in caplog.records if r.getMessage().startswith("deliberation fail-closed")]
    assert len(hits) == 1
    assert "critique_outcome=failed" in hits[0]
    assert f"reason={CODE_FAILED}" in hits[0]


def test_c13_refusal_uses_neutral_rationale() -> None:
    risk = _risk(rationale="benign factual query")
    ctrl = _controller()
    with patch.object(ctrl.assembler, "assemble", wraps=ctrl.assembler.assemble) as assemble:
        _route(ctrl, _draft_state("failed"), risk=risk)
    passed = assemble.call_args.kwargs["risk_estimation"]
    assert passed.rationale == "The safety check could not be completed."
    assert passed.score == risk.score
    assert risk.rationale == "benign factual query"

    ctrl2 = _controller()
    with patch.object(ctrl2.assembler, "assemble", wraps=ctrl2.assembler.assemble) as assemble2:
        _route(ctrl2, _draft_state("completed"), risk=risk)
    assert assemble2.call_args.kwargs["risk_estimation"] is risk


def test_c14_trace_and_explanation_synced() -> None:
    ctrl = _controller()
    trace = Trace(request_id="req-c")
    with patch("moralstack.orchestration.controller.log_decision_explanation") as log_expl:
        _route(ctrl, _draft_state("failed"), trace=trace)
    assert trace.final_action == "REFUSE"
    logged = log_expl.call_args.args[0]
    assert logged.winning_rule == "deliberation_fail_closed"
    assert logged.final_action == "REFUSE"
    assert CODE_FAILED in logged.why_not_normal_complete


def test_c15_upstream_draft_never_delivered_on_fail_closed() -> None:
    upstream = "UPSTREAM-VERBATIM-DRAFT"
    state = _draft_state("failed", draft=upstream, _draft_verbatim_reuse=True)
    result = _route(
        _controller(),
        state,
        speculative_draft=upstream,
        draft_provenance=DraftProvenance(origin="upstream", model="client-model-C"),
    )
    _assert_deliberation_fail_closed(result, CODE_FAILED, draft=upstream)
    assert result.response.metadata.draft_model == ""


def test_c16_recovery_cycle2_completes_after_cycle1_failed_is_delivered(final_traces: list[Any]) -> None:
    state = _draft_state("completed", cycle=2, errors=["Critique error: boom"], critiques=[_report()])
    result = _route(_controller(), state)
    meta = result.response.metadata
    assert meta.final_action == "NORMAL_COMPLETE"
    assert meta.hard_violation_flip_reason == ""
    assert final_traces == []


def test_c17_enforce_unit(final_traces: list[Any]) -> None:
    runner = _runner(_StubCritic(), store=object())
    request = ProcessedRequest(prompt="p", request_id="req-u")

    def _call(
        outcome: str,
        decision: Decision | None = None,
        *,
        expl: DecisionExplanation | None = None,
        constitution: Any = CONSTITUTION,
        which: Any = runner,
    ) -> Any:
        return which.enforce_verified_draft_delivery(
            request,
            _state(outcome),
            decision or _decision(),
            constitution=constitution,
            decision_explanation=expl,
        )

    # no-ops
    for outcome in ("completed", "skipped"):
        dec = _decision()
        out, expl, orig, reason = _call(outcome, dec, expl=_explanation())
        assert out is dec and orig == "" and reason == ""
        assert expl is not None and expl.winning_rule == "decide_action"
    refuse = _decision("REFUSE")
    assert _call("failed", refuse)[0] is refuse
    assert _call("failed", which=_runner(None, store=object()))[3] == ""
    assert _call("failed", which=_runner(_StubCritic(), store=None), constitution=None)[3] == ""
    assert final_traces == []

    # flips
    for outcome, code in (
        ("failed", CODE_FAILED),
        ("timeout", CODE_TIMEOUT),
        ("", CODE_UNVERIFIED),
        ("weird", CODE_UNVERIFIED),
    ):
        dec = _decision("SAFE_COMPLETE", hard=["CORE.PRIV.1"])
        before = copy.deepcopy(dec)
        out, expl, orig, reason = _call(outcome, dec, expl=_explanation())
        assert dec == before  # input not mutated
        assert out.final_action == "REFUSE" and reason == code and orig == "SAFE_COMPLETE"
        assert out.hard_violations == ["CORE.PRIV.1"]
        assert code in out.reason_codes
        assert expl is not None and expl.winning_rule == "deliberation_fail_closed"
        assert code in expl.why_not_refuse
    # explanation=None is tolerated
    out, expl, _orig, reason = _call("failed", expl=None)
    assert out.final_action == "REFUSE" and reason == CODE_FAILED
    assert expl is not None and expl.winning_rule == "deliberation_fail_closed"


def test_c18_check_raises_still_refuses_with_audit_shape(final_traces: list[Any]) -> None:
    ctrl = _controller()
    state = _draft_state("completed")  # healthy state: the only failure is the check itself
    with patch.object(ctrl._runner, "enforce_verified_draft_delivery", side_effect=RuntimeError("check broke"), create=True):
        result = _route(ctrl, state)
    meta = result.response.metadata
    assert meta.final_action == "REFUSE"
    assert meta.hard_violation_flip_reason == CODE_UNVERIFIED
    assert meta.original_final_action == "NORMAL_COMPLETE"
    assert CODE_UNVERIFIED in meta.reason_codes
    # explanation is None on this path: the metadata must not carry the stale NORMAL_COMPLETE wording
    assert meta.winning_rule == ""
    assert "orig why_not" not in meta.why_not_refuse
    assert DRAFT_TEXT not in result.response.content
    assert len(final_traces) == 1
    assert final_traces[0].hard_violation_source == "deliberation_fail_closed"
    assert final_traces[0].stage_payload["flip_reason"] == CODE_UNVERIFIED
    assert finalize_delivery(result).final_action == "REFUSE"
    # phase 2: ledger skip


# =============================================================================
# D. Offline end-to-end
# =============================================================================

_DELIBERATIVE_PROMPT = "Tell me about depression medication"


class _RaisingMockCritic(MockCritic):
    def critique(self, *args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("critic provider down")


def _mock_orchestrator(critic: Any) -> Any:
    return create_orchestrator(
        policy=MockPolicy(),
        risk_estimator=MockRiskEstimator(),
        critic=critic,
        simulator=MockSimulator(),
        hindsight=MockHindsight(),
        perspectives=MockPerspectives(),
        constitution_store=MockConstitutionStore(),
        max_cycles=1,
        timeout_ms=60_000,
    )


def test_i1_orchestrator_critic_raises_is_governed_refuse() -> None:
    result = _mock_orchestrator(_RaisingMockCritic()).process(
        ProcessedRequest(prompt=_DELIBERATIVE_PROMPT, request_id="req-i1")
    )
    meta = result.response.metadata
    assert result.path == "DELIBERATIVE_PATH"
    assert meta.final_action == "REFUSE"
    assert meta.hard_violation_flip_reason == CODE_FAILED
    delivery = finalize_delivery(result)
    assert delivery.final_action == "REFUSE"
    assert delivery.finish_reason == "content_filter"
    assert delivery.final_text_source == GOVERNED_REFUSAL_SOURCE


def test_i1_twin_plain_mock_critic_is_delivered() -> None:
    result = _mock_orchestrator(MockCritic()).process(ProcessedRequest(prompt=_DELIBERATIVE_PROMPT, request_id="req-i1b"))
    meta = result.response.metadata
    assert result.path == "DELIBERATIVE_PATH"
    assert meta.final_action != "REFUSE"
    assert meta.hard_violation_flip_reason == ""


def test_i2_sdk_upstream_draft_not_delivered_when_critic_raises() -> None:
    from moralstack.sdk.config import GovernanceConfig
    from moralstack.sdk.wrapper import GovernedClient

    upstream = "UPSTREAM-CLIENT-DRAFT-TEXT"
    openai_client = MagicMock()
    openai_client.chat.completions.create.return_value = MagicMock(
        choices=[MagicMock(message=MagicMock(content=upstream, role="assistant"), finish_reason="stop")],
        model="client-model-C",
        usage=MagicMock(total_tokens=50, prompt_tokens=30, completion_tokens=20),
    )
    client = GovernedClient(
        openai_client,
        _mock_orchestrator(_RaisingMockCritic()),
        GovernanceConfig(model="governance-model-G", generation="upstream_then_verify"),
    )
    with patch(
        "moralstack.orchestration.controller.get_route",
        return_value=("deliberative", False, RiskPolicyAction.DELIBERATE),
    ):
        resp = client.chat.completions.create(
            model="client-model-C",
            messages=[{"role": "user", "content": "What is the speed of light?"}],
        )
    assert upstream not in resp.content
    assert resp.governance_metadata.final_action == "REFUSE"
    assert resp.governance_metadata.draft_origin == "internal"


def test_i4_real_timeout_gate_end_to_end(persisted: Any) -> None:
    cfg = OrchestratorConfig(
        max_deliberation_cycles=1,
        enable_simulation=False,
        enable_perspectives=False,
        enable_hindsight=False,
        timeout_ms=600_000,
    )
    ctrl = _controller(policy=_DraftPolicy(), config=cfg)
    try:
        result = _route(ctrl, _draft_state(""), start_time=time.time() - 570, patch_run=False)
    finally:
        ctrl._runner.close()
    meta = result.response.metadata
    assert meta.final_action == "REFUSE"
    assert meta.hard_violation_flip_reason == CODE_TIMEOUT
    assert finalize_delivery(result).final_action == "REFUSE"


def test_i6_deliberative_store_configured_constitution_unavailable_refuses(persisted: Any) -> None:
    critic = _StubCritic(read_principles=True)
    cfg = OrchestratorConfig(
        max_deliberation_cycles=1, enable_simulation=False, enable_perspectives=False, enable_hindsight=False
    )
    ctrl = _controller(critic=critic, store=_FailingStore(), policy=_DraftPolicy(), config=cfg)
    try:
        result = _route(ctrl, _draft_state(""), patch_run=False)
    finally:
        ctrl._runner.close()
    assert critic.calls, "the critic must still be invoked when the store is configured"
    meta = result.response.metadata
    assert meta.final_action == "REFUSE"
    assert meta.hard_violation_flip_reason == CODE_FAILED
