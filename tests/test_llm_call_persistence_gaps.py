"""NEW-1: retry-failed rows written from pool workers now reach ``llm_calls``.

The risk mini-estimator pool and the perspective ensemble (``parallel_evaluation=True``, the production default) used a
bare ``ThreadPoolExecutor``; the workers did not inherit the observability ContextVars, so ``async_persist_llm_call``
found no run/request id and silently dropped every failed-attempt row.

Unlike ``test_risk_persist_batch.py`` / ``test_runtime_modules_retry_token_accounting.py`` (which monkeypatch
``async_persist_llm_call`` and therefore bypass the ContextVar gate), these tests go through the REAL
``async_persist_llm_call`` and record the envelopes that reach the observability service. This module also holds the
helpers shared with ``test_llm_call_persistence_quick_check.py`` and ``test_llm_call_persistence_hindsight_single.py``.
"""

from __future__ import annotations

import contextlib
import json
import threading
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

import pytest

import moralstack.observability.emit_helpers as emit_helpers
from moralstack.models.risk.estimator import LLMBasedRiskEstimator
from moralstack.models.risk.schema import RiskEstimatorConfig
from moralstack.observability import context as obs_context
from moralstack.observability.events import EVENT_LLM_CALL
from moralstack.orchestration.types import RiskEstimationError
from moralstack.prompts.catalog import default_prompt_set
from moralstack.runtime.modules.perspective_module import EnsembleConfig, LLMPerspectiveEnsemble

RUN_ID = "run-gaps"
REQUEST_ID = "req-gaps"


class _Recorder:
    """Stands in for the observability service: collects the envelopes that pass the emit gate."""

    def __init__(self) -> None:
        self.envelopes: list[Any] = []
        self._lock = threading.Lock()

    def emit(self, envelope: Any) -> None:
        with self._lock:
            self.envelopes.append(envelope)

    @property
    def rows(self) -> list[Any]:
        return [e for e in self.envelopes if e.event_type == EVENT_LLM_CALL]

    def payloads(self, **match: Any) -> list[dict[str, Any]]:
        return [
            {**e.payload, "_cycle": e.cycle, "_run_id": e.run_id, "_request_id": e.request_id}
            for e in self.rows
            if all(e.payload.get(k) == v for k, v in match.items())
        ]


@pytest.fixture(name="recorder")
def recorder_fixture(monkeypatch: pytest.MonkeyPatch) -> _Recorder:
    rec = _Recorder()
    # The real ``async_persist_llm_call`` runs; only the service it emits to is replaced.
    monkeypatch.setattr(emit_helpers, "get_obs", lambda: rec)
    return rec


@contextlib.contextmanager
def _request_scope(cycle: int | None = None) -> Iterator[None]:
    """Bind run/request ids (and optionally the cycle) to this thread, restoring the previous values after."""
    tokens = [
        (obs_context._run_id, obs_context._run_id.set(RUN_ID)),
        (obs_context._request_id, obs_context._request_id.set(REQUEST_ID)),
        (obs_context._cycle, obs_context._cycle.set(cycle)),
    ]
    try:
        yield
    finally:
        for var, token in reversed(tokens):
            var.reset(token)


@dataclass
class _Gen:
    text: str
    tokens_used: int = 14
    prompt_tokens: int = 11
    completion_tokens: int = 3
    token_usage_source: str = "exact"

    def token_usage_json(self) -> str:
        return json.dumps(
            {
                "prompt_tokens": self.prompt_tokens,
                "completion_tokens": self.completion_tokens,
                "total_tokens": self.tokens_used,
                "source": self.token_usage_source,
            }
        )


def _tokens(payload: dict[str, Any]) -> int:
    return json.loads(payload["token_usage_json"])["total_tokens"]


# --------------------------------------------------------------------------------------
# Fix A (NEW-1): the pool workers inherit the observability context
# --------------------------------------------------------------------------------------


class _AlwaysUnparseable:
    model = "gpt-main"
    tracker = None

    def generate(self, *args: Any, **kwargs: Any) -> _Gen:
        return _Gen(text="not json at all")

    generate_messages = generate


def test_estimator_retry_failed_rows_arrive_through_the_real_emit_path(recorder: _Recorder) -> None:
    estimator = LLMBasedRiskEstimator(policy=_AlwaysUnparseable(), config=RiskEstimatorConfig(max_retries=2))
    with _request_scope(), pytest.raises(RiskEstimationError):
        estimator._parallel_mini_analysis("una domanda qualsiasi")

    rows = recorder.payloads(call_outcome="retry_failed")
    # 3 mini-estimators x 2 attempts, each carrying the billed usage of the attempt
    assert len(rows) == 6
    assert {r["module"] for r in rows} == {"risk_estimator"}
    assert {r["_run_id"] for r in rows} == {RUN_ID}
    assert {r["_request_id"] for r in rows} == {REQUEST_ID}
    assert sorted(r["attempts"] for r in rows) == [1, 1, 1, 2, 2, 2]
    assert all(r["billable_provider_call"] is True and _tokens(r) == 14 for r in rows)
    # UI coordinates: the rows sit with the mini-estimator rows (cycle 0, seq -9), not in a NULL-cycle group
    assert {(r["_cycle"], r["sequence_in_cycle"]) for r in rows} == {(0, -9)}


def test_estimator_without_a_request_scope_still_persists_nothing_and_still_fails_closed(recorder: _Recorder) -> None:
    """No run/request ids: the emit gate drops the rows (by design) and the request still fails closed."""
    estimator = LLMBasedRiskEstimator(policy=_AlwaysUnparseable(), config=RiskEstimatorConfig(max_retries=2))
    # Pin a clean scope: another test may leave ids set on this thread's context (GovernedClient never resets them).
    tokens = [(var, var.set(None)) for var in (obs_context._run_id, obs_context._request_id)]
    try:
        with pytest.raises(RiskEstimationError):
            estimator._parallel_mini_analysis("una domanda qualsiasi")
    finally:
        for var, token in reversed(tokens):
            var.reset(token)
    assert recorder.rows == []


class _FailFirstPerPrompt:
    """The first attempt of every perspective is unparseable; the retry (prompt ends with the retry text) is valid."""

    model = "gpt-persp"

    def __init__(self, retry_text: str) -> None:
        self._retry_text = retry_text
        self.threads: set[str] = set()

    def generate(self, *, prompt: str, system: str = "", config: Any = None, **kwargs: Any) -> _Gen:
        self.threads.add(threading.current_thread().name)
        if not prompt.endswith(self._retry_text):
            return _Gen(text="bad", tokens_used=15, prompt_tokens=10, completion_tokens=5)
        verdict = {"approval_score": 0.8, "concerns": [], "suggestions": [], "rationale": "ok"}
        return _Gen(text=json.dumps(verdict), tokens_used=25, prompt_tokens=15, completion_tokens=10)


def test_parallel_perspectives_retry_failed_rows_arrive_with_the_cycle_of_the_request(recorder: _Recorder) -> None:
    retry_text = default_prompt_set().text("perspectives.retry")
    policy = _FailFirstPerPrompt(retry_text)
    ensemble = LLMPerspectiveEnsemble(
        policy, EnsembleConfig(max_retries=2, parallel_evaluation=True, enable_caching=False, max_perspectives=3)
    )
    perspectives = ensemble.perspectives[:3]
    with _request_scope(cycle=2):
        result = ensemble._evaluate_parallel(perspectives, "shared system")

    # the pool really ran off the caller thread (otherwise the test proves nothing)
    assert policy.threads and "MainThread" not in policy.threads
    # decision inputs unchanged: every perspective still succeeded on its second attempt
    assert len(result.results) == len(perspectives) and not result.failed_perspectives
    rows = recorder.payloads(call_outcome="retry_failed")
    assert len(rows) == len(perspectives)
    assert {r["module"] for r in rows} == {"perspectives"}
    assert {r["_run_id"] for r in rows} == {RUN_ID} and {r["_request_id"] for r in rows} == {REQUEST_ID}
    assert {r["_cycle"] for r in rows} == {2}
    assert all(r["billable_provider_call"] is True and _tokens(r) == 15 for r in rows)
    assert {p.id for p in perspectives} == {p.id for p in perspectives if any(p.id in r["prompt"] for r in rows)}


# --------------------------------------------------------------------------------------
# Token accounting: new rows move every reading of a request's usage together
# --------------------------------------------------------------------------------------


def assert_accounting_parity(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch, drive: Any, *, calls: int, total_tokens: int, cycle: int | None = None
) -> None:
    """Run ``drive()`` under a request scope on a real tmp DB and check that the rows it persists are counted alike by
    the in-process accumulator (``llm_call_count`` / SDK and proxy ``usage`` / ``request_token_usage``), the SQL
    reconstruction over ``llm_calls`` and the read store."""
    import moralstack.observability.request_token_accumulator as rta
    from moralstack.observability import obs, router
    from moralstack.observability import service as service_module
    from moralstack.observability.events import EVENT_LLM_CALL as _LLM_CALL
    from moralstack.observability.read_store import SqliteReadStore
    from moralstack.observability.request_token_accumulator import record_llm_call_usage
    from moralstack.observability.service import ObservabilityService, _counts_as_billable
    from moralstack.observability.sinks.sqlite_sink import _get_connection, create_run, init_db, upsert_request

    # Other test modules rebind ``ObservabilityService.emit`` / ``emit_batch`` at class level to queue-only versions (no
    # accumulator feed) and never restore them: bind the real accumulator-feeding behaviour for this test.
    def _feed(envelope: Any) -> None:
        if envelope.event_type == _LLM_CALL and envelope.run_id and envelope.request_id:
            if _counts_as_billable(envelope.payload):
                token_json = envelope.payload.get("token_usage_json")
                record_llm_call_usage(
                    envelope.run_id, envelope.request_id, token_json if isinstance(token_json, str) else None
                )

    def _emit(self: Any, envelope: Any) -> None:
        _feed(envelope)
        self._queue.submit_envelope(envelope)

    def _emit_batch(self: Any, envelopes: Any) -> None:
        for envelope in envelopes:
            _feed(envelope)
        if envelopes:
            self._queue.submit_batch(list(envelopes))

    monkeypatch.setattr(ObservabilityService, "emit", _emit)
    monkeypatch.setattr(ObservabilityService, "emit_batch", _emit_batch)

    db_path = str(tmp_path / "gaps-parity.db")
    monkeypatch.setenv("MORALSTACK_OBSERVABILITY_DB_PATH", db_path)
    monkeypatch.setenv("MORALSTACK_DB_PATH", db_path)
    monkeypatch.setenv("MORALSTACK_OBSERVABILITY_MODE", "db_only")
    with contextlib.suppress(Exception):
        service_module.get_obs().shutdown(timeout=1.0)
    service_module._obs_instance = None
    router._sqlite_sink = None
    router._jsonl_sink = None
    with rta._lock:
        rta._store.clear()
    assert init_db(db_path)
    assert create_run(RUN_ID, run_type="test", meta={})
    assert upsert_request(RUN_ID, REQUEST_ID, prompt="request", domain="test")

    try:
        with _request_scope(cycle=cycle):
            drive()
        obs.flush(timeout=10.0)

        totals = rta.finalize_and_persist(RUN_ID, REQUEST_ID)
        assert totals is not None
        assert totals.llm_call_count == calls
        assert totals.total_tokens == total_tokens
        obs.flush(timeout=10.0)

        conn = _get_connection(db_path)
        try:
            n_rows, sql_total = conn.execute(
                "SELECT COUNT(*), COALESCE(SUM(total_tokens), 0) FROM llm_calls "
                "WHERE run_id = ? AND request_id = ? AND COALESCE(billable_provider_call, 1) = 1",
                (RUN_ID, REQUEST_ID),
            ).fetchone()
            finalized = conn.execute(
                "SELECT total_tokens, llm_call_count FROM request_token_usage WHERE run_id = ? AND request_id = ?",
                (RUN_ID, REQUEST_ID),
            ).fetchone()
        finally:
            conn.close()
        assert (n_rows, sql_total) == (calls, total_tokens)
        assert (finalized["total_tokens"], finalized["llm_call_count"]) == (total_tokens, calls)
        store_totals = SqliteReadStore().get_token_usage_totals(RUN_ID, REQUEST_ID)
        assert store_totals is not None and store_totals["total_tokens"] == total_tokens
    finally:
        with contextlib.suppress(Exception):
            service_module.get_obs().shutdown(timeout=2.0)
        service_module._obs_instance = None
        router._sqlite_sink = None
        router._jsonl_sink = None


def test_estimator_failed_retries_keep_accumulator_sql_and_read_store_in_parity(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """6 failed attempts (3 mini-estimators x 2) x 14 tokens: counted once, alike, by every reading."""

    def drive() -> None:
        estimator = LLMBasedRiskEstimator(policy=_AlwaysUnparseable(), config=RiskEstimatorConfig(max_retries=2))
        with pytest.raises(RiskEstimationError):
            estimator._parallel_mini_analysis("una domanda qualsiasi")

    assert_accounting_parity(tmp_path, monkeypatch, drive, calls=6, total_tokens=6 * 14)


def test_simulator_batch_retry_failed_row_names_the_failed_call(recorder: _Recorder) -> None:
    """Additive key on the simulator retry-failed row, so the UI can say WHO failed."""
    from moralstack.runtime.modules.simulator_module import LLMConsequenceSimulator, SimulatorConfig

    valid = json.dumps(
        {
            "consequences": [
                {
                    "text": "harm",
                    "likelihood": 0.5,
                    "scenario_type": "social_impact",
                    "outcome_valence": -0.5,
                    "affected_stakeholders": ["user"],
                    "harm_type": "physical_harm",
                    "harm_severity": 0.7,
                    "harm_scope": "individual",
                    "reversibility": 0.3,
                }
            ]
        }
    )
    replies = [_Gen(text="bad"), _Gen(text=valid)]

    class _Policy:
        model = "gpt-sim"

        def generate(self, **kwargs: Any) -> _Gen:
            return replies.pop(0)

    simulator = LLMConsequenceSimulator(_Policy(), SimulatorConfig(max_retries=2))
    with _request_scope(cycle=1):
        simulator._simulate_batch("req", "resp", 1)
    (row,) = recorder.payloads(call_outcome="retry_failed")
    assert row["module"] == "simulator"
    summary = json.loads(row["parsed_summary_json"])
    # A4b-2 adds the failed attempt's messages inline under ``wire``; the identifying key is unchanged.
    assert summary.pop("wire")["v"] == 1
    assert summary == {"simulator_path": "batch"}
