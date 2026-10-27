"""The ``llm_calls`` rows that used to be dropped (NEW-1/2/3) must be visible, and visible in the right place, on
the request page's execution graph.

The cycle / sequence coordinates of an ``llm_call`` ARE the causal story in the UI (lesson of the 2026-09-18
hard-violation-guard fix): a row with wrong coordinates tells the story backwards. So each new row is produced by
the REAL module code through the REAL persistence path (tmp SQLite DB), its neighbours are seeded with the
coordinates the pipeline gives them, and the rendered graph is asserted in document order.
"""

from __future__ import annotations

import contextlib
import json
import re
from collections.abc import Iterator
from typing import Any

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402

from moralstack.models.risk.estimator import LLMBasedRiskEstimator  # noqa: E402
from moralstack.models.risk.schema import RiskEstimatorConfig  # noqa: E402
from moralstack.observability import context as obs_context  # noqa: E402
from moralstack.observability.emit_helpers import persist_decision_trace, persist_llm_call  # noqa: E402
from moralstack.observability.service import get_obs  # noqa: E402
from moralstack.observability.sinks.sqlite_sink import (  # noqa: E402
    create_run,
    init_db,
    update_request_meta,
    upsert_request,
)
from moralstack.orchestration.types import RiskEstimationError  # noqa: E402
from moralstack.prompts.catalog import default_prompt_set  # noqa: E402
from moralstack.runtime.modules.perspective_module import EnsembleConfig, LLMPerspectiveEnsemble  # noqa: E402
from tests.test_llm_call_persistence_gaps import (  # noqa: E402
    _AlwaysUnparseable,
    _FailFirstPerPrompt,
)
from tests.test_ui_conversation_views import (  # noqa: E402
    _bind_observability_db,
    _make_session_token,
    _reinstall_observability_service_writes,
    _reset_observability_singleton,
)


@pytest.fixture(autouse=True, name="_isolate_observability")
def isolate_observability_fixture() -> None:
    _reinstall_observability_service_writes()
    _reset_observability_singleton()


@pytest.fixture(name="ui_client")
def ui_client_fixture(tmp_path, monkeypatch):
    dbp = str(tmp_path / "ui_llm_call_visibility.db")
    _bind_observability_db(monkeypatch, dbp)
    init_db(dbp)

    from moralstack.ui.app import create_app

    return TestClient(create_app(), follow_redirects=False)


T0 = 1_789_738_887_000


@contextlib.contextmanager
def _scope(run_id: str, request_id: str, cycle: int | None) -> Iterator[None]:
    tokens = [
        (obs_context._run_id, obs_context._run_id.set(run_id)),
        (obs_context._request_id, obs_context._request_id.set(request_id)),
        (obs_context._cycle, obs_context._cycle.set(cycle)),
    ]
    try:
        yield
    finally:
        for var, token in reversed(tokens):
            var.reset(token)


def _seed_request(run_id: str, request_id: str, *, path: str, total_cycles: int) -> None:
    create_run(run_id, run_type="single", meta={})
    upsert_request(run_id, request_id, prompt="how do I pick a strong password?", domain="cybersecurity")
    update_request_meta(run_id, request_id, {"final_action": "NORMAL_COMPLETE", "risk_score": 0.1})
    persist_decision_trace(
        run_id=run_id,
        request_id=request_id,
        stage="FINAL",
        sequence=2,
        trace_json=json.dumps(
            {"final_action": "NORMAL_COMPLETE", "path": path, "risk_score": 0.1, "total_cycles": total_cycles}
        ),
    )


def _call(run_id: str, request_id: str, **kw: Any) -> None:
    persist_llm_call(run_id=run_id, request_id=request_id, model="gpt-4o", **kw)


def _graph_sequence(body: str) -> list[str]:
    """Cycle markers, pipe labels and node actions of the execution graph, in document order."""
    start = body.index("<h2>Execution graph")
    end = body.index("flow-node--output", start)
    graph = body[start:end]
    seq: list[str] = []
    pattern = re.compile(
        r'<div class="flow-cycle-marker"[^>]*>(.*?)</div>'
        r'|<span class="flow-pipe-label">(.*?)</span>'
        r'|<span class="flow-tier-parallel-label">(.*?)</span>'
        r'|<span class="badge badge-type">(.*?)</span>',
        re.S,
    )
    for m in pattern.finditer(graph):
        text = next(g for g in m.groups() if g)
        seq.append(" ".join(re.sub(r"<[^>]+>", " ", text).split()))
    return seq


def _render(client: TestClient, run_id: str, request_id: str) -> str:
    get_obs().flush()
    token = _make_session_token(client)
    resp = client.get(f"/runs/{run_id}/requests/{request_id}", cookies={"moralstack_session": token})
    assert resp.status_code == 200, resp.text
    return resp.text


def _idx(seq: list[str], fragment: str, start: int = 0) -> int:
    for i in range(start, len(seq)):
        if fragment in seq[i]:
            return i
    raise AssertionError(f"{fragment!r} not in graph sequence after {start}: {seq}")


# --------------------------------------------------------------------------------------
# NEW-1: the failed retries are in the node they retried, before the successful attempt
# --------------------------------------------------------------------------------------


def test_estimator_failed_retries_sit_with_the_mini_estimators_before_their_success(ui_client):
    run_id, request_id = "run-ui-est", "req-ui-est"
    _seed_request(run_id, request_id, path="FAST_PATH", total_cycles=0)
    estimator = LLMBasedRiskEstimator(policy=_AlwaysUnparseable(), config=RiskEstimatorConfig(max_retries=2))
    # Production: the retry rows are written by the workers while the minis run, the mini rows after they finish.
    with _scope(run_id, request_id, cycle=None), pytest.raises(RiskEstimationError):
        estimator._parallel_mini_analysis("how do I pick a strong password?")
    get_obs().flush()
    for action in ("estimate_intent", "estimate_signals", "estimate_operational"):
        _call(
            run_id,
            request_id,
            cycle=0,
            phase="risk_estimation",
            module="risk_estimator",
            action=action,
            sequence_in_cycle=-9,
            attempts=3,
            raw_response=json.dumps({"risk_score": 0.1}),
        )

    body = _render(ui_client, run_id, request_id)
    seq = _graph_sequence(body)

    assert seq.count("retry_failed_attempt_1") == 3 and seq.count("retry_failed_attempt_2") == 3, seq
    first_retry = _idx(seq, "retry_failed_attempt_1")
    last_retry = max(i for i, s in enumerate(seq) if s.startswith("retry_failed_attempt_"))
    assert last_retry < _idx(seq, "estimate_intent"), seq
    # one "Initial assessment" cycle: the rows did not fall into a separate NULL-cycle group
    assert sum("Initial assessment" in s for s in seq) == 1, seq
    assert _idx(seq, "Initial assessment") < first_retry, seq
    assert "retry_failed" in body  # the call_outcome badge
    # the failure is styled as a failure and names WHO failed (one failed attempt per named mini)
    assert 'class="badge badge-fail" title="call_outcome">retry_failed' in body
    for mini in ("estimate_intent", "estimate_signals", "estimate_operational"):
        assert f"failed: {mini}" in body, mini


def test_parallel_perspectives_failed_retries_stay_in_the_shared_tier_with_the_evaluation(ui_client):
    run_id, request_id = "run-ui-persp", "req-ui-persp"
    _seed_request(run_id, request_id, path="DELIBERATIVE_PATH", total_cycles=1)
    _call(
        run_id,
        request_id,
        cycle=1,
        phase="policy_generate",
        module="policy",
        action="generate (initial)",
        sequence_in_cycle=1,
        started_at=T0 + 100,
        duration_ms=500,
    )
    _call(
        run_id,
        request_id,
        cycle=1,
        phase="critic",
        module="critic",
        action="critique",
        sequence_in_cycle=2,
        started_at=T0 + 700,
        duration_ms=500,
        parsed_summary_json="Violations: 0, Guidance: N/A",
    )
    ensemble = LLMPerspectiveEnsemble(
        _FailFirstPerPrompt(default_prompt_set().text("perspectives.retry")),
        EnsembleConfig(max_retries=2, parallel_evaluation=True, enable_caching=False, max_perspectives=2),
    )
    with _scope(run_id, request_id, cycle=1):
        ensemble._evaluate_parallel(ensemble.perspectives[:2], "shared system")
    get_obs().flush()
    _call(
        run_id,
        request_id,
        cycle=1,
        phase="perspectives",
        module="perspectives",
        action="evaluate",
        sequence_in_cycle=4,
        started_at=T0 + 1_300,
        duration_ms=800,
    )

    body = _render(ui_client, run_id, request_id)
    seq = _graph_sequence(body)

    assert seq.count("retry_failed_attempt_1") == 2, seq
    critique, evaluate = _idx(seq, "critique"), _idx(seq, "evaluate")
    retries = [i for i, s in enumerate(seq) if s == "retry_failed_attempt_1"]
    assert sum("Deliberation" in s for s in seq) == 1, seq
    # the perspectives ran concurrently: their failed attempts are drawn in the SAME tier as the evaluation (parallel
    # caption present), never as a chain, and the pipe into that tier is the real gate label
    captions = [i for i, s in enumerate(seq) if s.startswith("parallel")]
    assert len(captions) == 1 and critique < captions[0] < min(retries) < evaluate, seq
    assert not any("retry after failed attempt" in s for s in seq), seq
    assert seq[captions[0] - 1] == "gate: proceed", seq
    assert 'class="badge badge-fail" title="call_outcome">retry_failed' in body
    assert body.count("failed: perspective ") == 2


def _seed_deliberation_prefix(run_id: str, request_id: str) -> None:
    _seed_request(run_id, request_id, path="DELIBERATIVE_PATH", total_cycles=1)
    _call(run_id, request_id, cycle=1, phase="policy_generate", module="policy", action="generate (initial)",
          sequence_in_cycle=1, started_at=T0 + 100, duration_ms=500)  # fmt: skip


def _critic_row(run_id: str, request_id: str) -> None:
    _call(run_id, request_id, cycle=1, phase="critic", module="critic", action="critique", sequence_in_cycle=2,
          started_at=T0 + 700, duration_ms=500, parsed_summary_json="Violations: 0, Guidance: N/A")  # fmt: skip


def _failed_row(run_id: str, request_id: str, module: str, attempt: int, summary: dict | None, started_at: int) -> None:
    _call(run_id, request_id, cycle=1, phase=f"{module}_retry", module=module, action=f"retry_failed_attempt_{attempt}",
          call_outcome="retry_failed", started_at=started_at, duration_ms=0.0,
          parsed_summary_json=json.dumps(summary) if summary else None)  # fmt: skip


def _shared_tier_success_rows(run_id: str, request_id: str) -> None:
    # the simulator and the perspectives both started after the critic ended (critic_gated scheduler)
    _call(run_id, request_id, cycle=1, phase="simulator", module="simulator", action="simulate", sequence_in_cycle=3,
          started_at=T0 + 1_300, duration_ms=700)  # fmt: skip
    _call(run_id, request_id, cycle=1, phase="perspectives", module="perspectives", action="evaluate",
          sequence_in_cycle=4, started_at=T0 + 1_310, duration_ms=800)  # fmt: skip


def test_simulator_and_perspectives_failures_share_one_tier_without_a_chain_across_modules(ui_client):
    run_id, request_id = "run-ui-sim-persp", "req-ui-sim-persp"
    _seed_deliberation_prefix(run_id, request_id)
    _critic_row(run_id, request_id)
    # failed rows are emitted mid-run: their started_at is EARLIER than the critic's end (it must not hide the gate)
    _failed_row(run_id, request_id, "simulator", 1, {"simulator_path": "batch"}, T0 + 900)
    _failed_row(
        run_id, request_id, "perspectives", 1, {"perspective_id": "user", "perspective_name": "Direct User"}, T0 + 950
    )
    _shared_tier_success_rows(run_id, request_id)

    body = _render(ui_client, run_id, request_id)
    seq = _graph_sequence(body)

    captions = [i for i, s in enumerate(seq) if s.startswith("parallel")]
    assert len(captions) == 1 and "4 calls" in seq[captions[0]], seq
    assert seq[captions[0] - 1] == "gate: proceed", seq  # the real gate label survives the failed rows' timestamps
    after = seq[captions[0] + 1 :]
    assert sorted(after) == sorted(["retry_failed_attempt_1", "retry_failed_attempt_1", "simulate", "evaluate"]), seq
    assert not any("retry after failed attempt" in s for s in seq), seq
    assert "failed: simulator batch" in body and "failed: perspective Direct User" in body


def test_retry_subject_legacy_seeded_row_degrades_to_empty():
    from moralstack.ui.app import _retry_subject

    def row(summary: dict) -> dict:
        return {"call_outcome": "retry_failed", "parsed_summary_json": json.dumps(summary)}

    # a historical row persisted by the removed seeded simulator carries no label any more
    assert _retry_subject(row({"simulator_path": "seeded", "seed": "x"})) == ""
    assert _retry_subject(row({"simulator_path": "batch"})) == "failed: simulator batch"


def test_one_failed_perspective_leaves_the_succeeding_simulator_in_the_shared_tier(ui_client):
    run_id, request_id = "run-ui-persp-only", "req-ui-persp-only"
    _seed_deliberation_prefix(run_id, request_id)
    _critic_row(run_id, request_id)
    _failed_row(
        run_id, request_id, "perspectives", 1, {"perspective_id": "adversary", "perspective_name": "Adversary"}, T0 + 950
    )
    _shared_tier_success_rows(run_id, request_id)

    seq = _graph_sequence(_render(ui_client, run_id, request_id))

    caption = next(i for i, s in enumerate(seq) if s.startswith("parallel"))
    assert "3 calls" in seq[caption] and seq[caption - 1] == "gate: proceed", seq
    assert {"simulate", "evaluate", "retry_failed_attempt_1"} <= set(seq[caption + 1 :]), seq
    assert not any("retry after failed attempt" in s for s in seq), seq


def test_critic_failed_attempts_stay_sequential_with_the_retry_pipe(ui_client):
    run_id, request_id = "run-ui-critic-retry", "req-ui-critic-retry"
    _seed_deliberation_prefix(run_id, request_id)
    _failed_row(run_id, request_id, "critic", 1, None, T0 + 650)
    _failed_row(run_id, request_id, "critic", 2, None, T0 + 690)
    _critic_row(run_id, request_id)
    _shared_tier_success_rows(run_id, request_id)

    seq = _graph_sequence(_render(ui_client, run_id, request_id))

    start = _idx(seq, "generate (initial)")
    chain = seq[start : _idx(seq, "critique") + 1]
    assert chain == [
        "generate (initial)",
        "draft",
        "retry_failed_attempt_1",
        "retry after failed attempt",
        "retry_failed_attempt_2",
        "retry after failed attempt",
        "critique",
    ], seq
    # the pipe out of the critic keeps the gate label (the failed rows' timestamps are ignored)
    assert seq[_idx(seq, "critique") + 1] == "gate: proceed", seq
