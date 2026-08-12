"""T10/T10b/T11/T11b — ``DeliberationRunner`` per-request fields, commit 2 of
``ai/plans/retrieval-request-scoped-state.md`` §8b
(``ai/handoffs/retrieval-request-scoped-state-commit2-handoff.md``).

Pre-fix, ``DeliberationRunner`` holds two per-request values on the shared
instance: ``_current_start_time`` (read by the four timeout gates in
``_critique``/``_simulate``/``_evaluate_hindsight``/``_evaluate_perspectives``)
and ``_request_analysis_reuse_targets`` (the audit trail persisted into the
``REQUEST_ANALYSIS_CONTEXT`` trace). Both are last-writer-wins across
concurrent ``run_deliberative_path`` calls on real threads
(``server/proxy.py:729``).

Every interleave below is driven by blocking ``threading.Event`` waits with
timeouts — never ``sleep()`` — per the plan's falsifiability rule (memory:
``a4-census-tautology-lesson``): a test that cannot fail is not evidence.
"""

from __future__ import annotations

import threading
import time
from typing import Any
from unittest.mock import MagicMock

import pytest

from moralstack.core.types import UserContext
from moralstack.orchestration.deliberation_runner import DeliberationRunner
from moralstack.orchestration.orchestration_event_taxonomy import RELEVANT_PRINCIPLES_REUSED
from moralstack.orchestration.types import (
    DeliberationDependencies,
    OrchestratorConfig,
    OrchestratorTimeoutError,
    ProcessedRequest,
)
from moralstack.runtime.trace.trace_stages import REQUEST_ANALYSIS_CONTEXT


class _RiskProto:
    """Minimal RiskEstimationProtocol double — same shape already proven to
    drive a full ``run_deliberative_path`` call in
    ``tests/test_request_analysis_reuse.py``."""

    score = 0.5
    risk_category = MagicMock(value="benign")
    detected_language = "en"
    intent_type = ""
    actionability_risk = MagicMock(value="LOW")
    detected_domain = None
    rationale = ""
    operational_risk = MagicMock(value="NONE")
    raw_response = ""
    used_fallback_parse = False
    risk_policy_action = MagicMock(value="ALLOW")
    harm_type = ""
    intent_to_harm = False
    requested_instructions = False


class _GatedStore:
    """Constitution store double.

    ``retrieve()`` blocks (signals ``entered``, waits on ``release``) when
    called for ``gate_domain`` — the T10/T10b choke point, placed right
    after the runner's own ``:1452``-equivalent write of the (then still
    shared) start-time attribute and before ``_critique`` is ever reached.

    ``get_constitution()`` returns a non-``None`` object only for the
    domains listed in ``reuse_domains`` — controls whether a request's
    critique takes the "reuse precomputed principles" branch
    (``deliberation_runner.py:2942-2960``), the T11/T11b choke point.
    """

    def __init__(
        self,
        *,
        gate_domain: str | None = None,
        reuse_domains: frozenset[str] = frozenset(),
    ) -> None:
        self._gate_domain = gate_domain
        self._reuse_domains = reuse_domains
        self.entered = threading.Event()
        self.release = threading.Event()

    def retrieve(
        self,
        query: str,
        top_k: int = 10,
        domain: str | None = None,
        *,
        retrieval_phase: str = "risk_routing",
    ) -> Any:
        from moralstack.constitution.retrieval_result import PrincipleRetrievalResult

        if domain is not None and domain == self._gate_domain:
            self.entered.set()
            assert self.release.wait(timeout=5.0), "release not signaled: broken test setup"
        return PrincipleRetrievalResult(principles=(), prefiltered_domains=(), debug_info={})

    def get_constitution(self, domain: str | None = None) -> Any:
        if domain in self._reuse_domains:
            return MagicMock(active_overlay=None)
        return None


class _RecordingCritic:
    """Critic double: records the ``request_id`` of every ``critique()``
    call. Deliberately exposes neither ``critique_with_relevant_principles``
    nor ``store``, so the runner's branch choice depends only on
    ``request_analysis``/constitution availability — the exact shape T11
    needs to make one request reuse and the other not."""

    def __init__(self) -> None:
        self.config = MagicMock(top_k_principles=20)
        self.calls: list[str] = []
        self._lock = threading.Lock()

    def critique(self, prompt: str, draft: str, constitution: Any, *, request_id: str = "", **_kwargs: Any) -> Any:
        with self._lock:
            self.calls.append(request_id)
        return MagicMock(
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
        )


class _ReuseEventGate:
    """Monkeypatch target for ``persist_orchestration_event``: blocks only on
    the ``RELEVANT_PRINCIPLES_REUSED`` event, which the runner emits
    immediately after appending to the reuse-targets list
    (``deliberation_runner.py:2959-2977``) — i.e. exactly between the append
    and the eventual ``REQUEST_ANALYSIS_CONTEXT`` read at ``:602``. Every
    other event type is a silent no-op (best-effort telemetry; not asserted
    on here)."""

    def __init__(self) -> None:
        self.entered = threading.Event()
        self.release = threading.Event()

    def __call__(self, *, event_type: str, **_kwargs: Any) -> None:
        if event_type == RELEVANT_PRINCIPLES_REUSED:
            self.entered.set()
            assert self.release.wait(timeout=5.0), "release not signaled: broken test setup"


def _make_runner(store: Any, critic: Any, *, parallel_module_calls: bool) -> DeliberationRunner:
    deps = DeliberationDependencies(
        policy=None,
        critic=critic,
        simulator=None,
        hindsight=None,
        perspectives=None,
        constitution_store=store,
        output_protector=MagicMock(),
    )
    cfg = OrchestratorConfig(
        max_deliberation_cycles=1,
        timeout_ms=60_000,
        parallel_module_calls=parallel_module_calls,
        # Force the strategy deterministically instead of relying on the
        # risk-based dynamic scheduler: full_parallel is the strategy that
        # forks the critic (`_run_full_parallel_evaluation`,
        # `state_critic = state.fork()`), which is exactly what T10b/T11b
        # must exercise.
        enable_dynamic_parallel_scheduler=False,
        parallel_critic_with_modules=True,
        enable_simulation=False,
        enable_perspectives=False,
        enable_hindsight=False,
        enable_soft_revision=False,
    )
    return DeliberationRunner(cfg, deps, protected_system_prompt="sys", logger=None, assembler=MagicMock())


@pytest.mark.parametrize("parallel_module_calls", [False, True], ids=["sequential", "full_parallel"])
def test_slow_request_does_not_make_concurrent_request_skip_critique(parallel_module_calls: bool) -> None:
    """T10 (sequential) / T10b (full_parallel) — the critique-skip reproducer.

    Request A poisons the (pre-fix) shared ``_current_start_time`` with a
    wildly backdated clock; A's own call legitimately times out via the
    OUTER loop check (``run_deliberative_path:1494-1496``, which always used
    the *local* ``start_time`` parameter, unaffected by this fix either way)
    — that raise is expected and asserted with ``pytest.raises``.

    Request B is gated inside its own retrieval so it only reaches
    ``_critique`` after A has already overwritten the shared attribute.
    Pre-fix, B's gate (``:2907-2908``) reads A's stale clock, computes
    elapsed_ratio > 0.90 and raises ``OrchestratorTimeoutError`` *inside*
    ``_critique`` — caught by that method's own ``except Exception`` and
    appended to ``state.errors`` as "Critique error: ..." (never propagates:
    handoff "Assertion shape matters"). So this asserts on the critic double
    having been invoked for B and on the absence of that error string, never
    on an exception escaping B's own call.

    Fails today (pre-fix): B's critique is skipped because of A's clock.
    """
    store = _GatedStore(gate_domain="domain-b")
    critic = _RecordingCritic()
    runner = _make_runner(store, critic, parallel_module_calls=parallel_module_calls)

    req_a = ProcessedRequest(
        request_id="req-a",
        prompt="request A, a benign question about scheduling",
        user_context=UserContext(domain_overlay="domain-a"),
    )
    req_b = ProcessedRequest(
        request_id="req-b",
        prompt="request B, a benign question about scheduling",
        user_context=UserContext(domain_overlay="domain-b"),
    )

    result_b: dict[str, Any] = {}

    def run_b() -> None:
        state, _, _ = runner.run_deliberative_path(req_b, _RiskProto(), time.time())
        result_b["state"] = state

    t_b = threading.Thread(target=run_b, name="thread-b")
    t_b.start()
    assert store.entered.wait(timeout=5.0), "thread B did not reach the retrieval gate"

    # 10,000s in the past: pre-fix, this poisons the shared clock attribute
    # for every concurrent reader; post-fix it only ever affects A's own call.
    old_start = time.time() - 10_000.0
    with pytest.raises(OrchestratorTimeoutError):
        runner.run_deliberative_path(req_a, _RiskProto(), old_start)

    store.release.set()
    t_b.join(timeout=10.0)
    assert not t_b.is_alive(), "thread B did not complete"

    state_b = result_b["state"]
    assert "req-b" in critic.calls, "B's critique must be invoked despite A's stale clock"
    assert not any(
        "Critique error" in e for e in state_b.errors
    ), f"B's critique was incorrectly skipped due to A's clock: {state_b.errors}"


@pytest.mark.parametrize("parallel_module_calls", [False, True], ids=["sequential", "full_parallel"])
def test_reuse_targets_in_persisted_trace_belong_to_own_request(
    parallel_module_calls: bool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """T11 (sequential) / T11b (full_parallel) — reuse targets belong to
    their own request.

    Request B's critique genuinely reuses the precomputed principles
    (``get_constitution("domain-b")`` returns a real object); request A's
    does not (``get_constitution("domain-a")`` returns ``None``). B is
    gated immediately after its own append to the (pre-fix) shared
    ``_request_analysis_reuse_targets`` list, via the
    ``RELEVANT_PRINCIPLES_REUSED`` event the runner emits right next to
    that append (``:2959-2977``) — i.e. strictly between B's append and B's
    own read at the final emit (``:602``). A then runs to completion in
    that window and rebinds the shared list (its own ``:1472``-equivalent
    reset) to a fresh, empty list *without* B's entry. Released, B's read
    lands on A's list, not its own.

    Pre-fix: B's persisted ``reuse_targets``/``reuse_count`` are empty
    (``[]``/``0``) even though B genuinely reused — A's later reset silently
    dropped B's own audit entry. Post-fix: the field lives on the per-call
    ``DeliberationState`` (plus its ``fork()`` line and the full-parallel
    merge at ``:2575``), so B's own value is unaffected by A regardless of
    interleave or strategy.
    """
    captured: list[Any] = []
    capture_lock = threading.Lock()

    def _capture(dt: Any) -> None:
        with capture_lock:
            captured.append(dt)

    monkeypatch.setattr("moralstack.orchestration.deliberation_runner.append_decision_trace", _capture)
    gate = _ReuseEventGate()
    monkeypatch.setattr("moralstack.orchestration.deliberation_runner.persist_orchestration_event", gate)

    store = _GatedStore(reuse_domains=frozenset({"domain-b"}))
    critic = _RecordingCritic()
    runner = _make_runner(store, critic, parallel_module_calls=parallel_module_calls)

    req_a = ProcessedRequest(
        request_id="req-a",
        prompt="request A, does not reuse precomputed principles",
        user_context=UserContext(domain_overlay="domain-a"),
    )
    req_b = ProcessedRequest(
        request_id="req-b",
        prompt="request B, reuses precomputed principles",
        user_context=UserContext(domain_overlay="domain-b"),
    )

    def run_b() -> None:
        runner.run_deliberative_path(req_b, _RiskProto(), time.time())

    t_b = threading.Thread(target=run_b, name="thread-b")
    t_b.start()
    assert gate.entered.wait(timeout=5.0), "thread B did not reach the reuse-append gate"

    runner.run_deliberative_path(req_a, _RiskProto(), time.time())

    gate.release.set()
    t_b.join(timeout=10.0)
    assert not t_b.is_alive(), "thread B did not complete"

    assert "req-b" in critic.calls, "B's critique must have run (sanity check on the test double wiring)"

    b_traces = [dt for dt in captured if dt.stage == REQUEST_ANALYSIS_CONTEXT and dt.request_id == "req-b"]
    assert len(b_traces) == 1, f"expected exactly one REQUEST_ANALYSIS_CONTEXT trace for req-b, got {len(b_traces)}"
    payload = b_traces[0].stage_payload
    assert payload["reuse_targets"] == ["critic"], f"B's own reuse must survive A's interleave: {payload}"
    assert payload["reuse_count"] == 1, f"B's own reuse_count must survive A's interleave: {payload}"


def test_runner_has_no_shared_start_time_or_reuse_targets_attributes() -> None:
    """Structural guard: after a run, a constructed ``DeliberationRunner``
    carries neither ``_current_start_time`` nor
    ``_request_analysis_reuse_targets`` as instance attributes. Complements
    the static ``rg`` acceptance check with a runtime one (round-2 review,
    missing tests point 3)."""
    store = _GatedStore()
    critic = _RecordingCritic()
    runner = _make_runner(store, critic, parallel_module_calls=False)

    req = ProcessedRequest(
        request_id="req-structural",
        prompt="hello",
        user_context=UserContext(domain_overlay="domain-a"),
    )
    runner.run_deliberative_path(req, _RiskProto(), time.time())

    assert not hasattr(runner, "_current_start_time")
    assert not hasattr(runner, "_request_analysis_reuse_targets")
