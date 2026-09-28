"""
The request-page "Execution graph" must tell the true causal story of the
hard-violation delivery guard (PROJECT_SPEC §5.3): a critic hard violation in
the deliberation cycle -> SAFE_COMPLETE regeneration -> one re-critique ->
delivery. Reproduces run ``0d4a091a`` / request ``9eef1009`` (2026-09-18),
whose graph rendered the guard's regeneration inside "Initial assessment"
(cycle 0, before calibration and routing, under a "risk + principles" pipe)
and tiered the re-critique "parallel" with the critique it followed.

Two shapes are seeded: the *legacy* rows exactly as they were persisted before
the guard got its own ``sequence_in_cycle`` (regeneration = cycle 0 /
``SEQ_POLICY`` / ``generate (safe_complete_path)``, re-critique =
``SEQ_CRITIC``), and the *current* rows. Both must render the same order; only
the legacy one carries the "re-homed" badge.
"""

from __future__ import annotations

import json
import re

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402

from moralstack.observability.emit_helpers import (  # noqa: E402
    persist_debug_event,
    persist_decision_trace,
    persist_llm_call,
)
from moralstack.observability.service import get_obs  # noqa: E402
from moralstack.observability.sinks.sqlite_sink import (  # noqa: E402
    create_run,
    init_db,
    update_request_meta,
    update_request_response,
    upsert_request,
)
from moralstack.orchestration.deliberation_runner import (  # noqa: E402
    HARD_VIOLATION_REGENERATION_ACTION,
    HARD_VIOLATION_REVALIDATION_ACTION,
    SEQ_CRITIC,
    SEQ_HARD_VIOLATION_REGENERATION,
    SEQ_HARD_VIOLATION_REVALIDATION,
    SEQ_PERSPECTIVES,
    SEQ_POLICY,
    SEQ_SIMULATOR,
)
from tests.test_ui_conversation_views import (  # noqa: E402
    _bind_observability_db,
    _make_session_token,
    _reinstall_observability_service_writes,
    _reset_observability_singleton,
)


@pytest.fixture(autouse=True)
def _isolate_observability() -> None:
    _reinstall_observability_service_writes()
    _reset_observability_singleton()


@pytest.fixture()
def ui_client(tmp_path, monkeypatch):
    dbp = str(tmp_path / "ui_hard_violation_guard_graph.db")
    _bind_observability_db(monkeypatch, dbp)
    init_db(dbp)

    from moralstack.ui.app import create_app

    return TestClient(create_app(), follow_redirects=False)


T0 = 1_789_738_887_000  # wall-clock anchor (ms); offsets mirror the real request


def _seed_request(run_id: str, request_id: str, *, legacy: bool) -> None:
    create_run(run_id, run_type="single", meta={})
    upsert_request(run_id, request_id, prompt="qual è la dose di naloxone da somministrare?", domain="medical")
    update_request_meta(run_id, request_id, {"final_action": "SAFE_COMPLETE", "risk_score": 0.35})
    update_request_response(run_id, request_id, "REGENERATED SAFE_COMPLETE TEXT")

    def call(**kw):
        persist_llm_call(run_id=run_id, request_id=request_id, model="gpt-4o", **kw)

    # cycle 0: prefilter, risk minis (parallel) + speculative draft
    call(
        cycle=0,
        phase="constitution_retrieval",
        module="constitution_retriever",
        action="domain_prefilter",
        sequence_in_cycle=-10,
        started_at=T0 + 400,
        duration_ms=6900,
        parsed_summary_json=json.dumps({"retrieval_phase": "risk_routing"}),
    )
    call(
        cycle=0,
        phase="speculative_generate",
        module="policy",
        action="generate (speculative)",
        sequence_in_cycle=0,
        started_at=T0 + 2300,
        duration_ms=4977,
        call_kind="speculative",
        call_outcome="used",
        raw_response="SPECULATIVE DRAFT",
    )
    call(
        cycle=0,
        phase="risk_estimation",
        module="risk_estimator",
        action="estimate_intent",
        sequence_in_cycle=-9,
        started_at=T0 + 10_400,
        duration_ms=2950,
        raw_response=json.dumps({"request_type": "crisis_support", "intent_to_harm": "no"}),
    )
    call(
        cycle=0,
        phase="risk_estimation",
        module="risk_estimator",
        action="estimate_signals",
        sequence_in_cycle=-9,
        started_at=T0 + 10_400,
        duration_ms=2930,
        raw_response=json.dumps({"q1_confidential": "no"}),
    )
    call(
        cycle=0,
        phase="risk_estimation",
        module="risk_estimator",
        action="estimate_operational",
        sequence_in_cycle=-9,
        started_at=T0 + 10_400,
        duration_ms=3010,
        raw_response=json.dumps({"risk_score": 0.45, "operational_risk": "LOW", "risk_policy_action": "DELIBERATE"}),
    )
    # cycle 1: speculative reuse, then critic / simulator / perspectives in full_parallel
    call(
        cycle=1,
        phase="policy_generate",
        module="policy",
        action="generate (speculative-reuse)",
        sequence_in_cycle=SEQ_POLICY,
        started_at=T0 + 13_438,
        duration_ms=0,
        raw_response="SPECULATIVE DRAFT",
    )
    call(
        cycle=1,
        phase="critic",
        module="critic",
        action="critique",
        sequence_in_cycle=SEQ_CRITIC,
        started_at=T0 + 13_439,
        duration_ms=2305,
        parsed_summary_json="Violations: 1, Guidance: suggest",
        raw_response=json.dumps({"decision": "REFUSE", "violated_hard": True}),
    )
    call(
        cycle=1,
        phase="simulator",
        module="simulator",
        action="simulate",
        sequence_in_cycle=SEQ_SIMULATOR,
        started_at=T0 + 13_439,
        duration_ms=3742,
        parsed_summary_json=json.dumps({"semantic_expected_harm": 0.32}),
    )
    call(
        cycle=1,
        phase="perspectives",
        module="perspectives",
        action="evaluate",
        sequence_in_cycle=SEQ_PERSPECTIVES,
        started_at=T0 + 13_441,
        duration_ms=4182,
    )
    # the guard: regeneration after the cycle, then the re-critique
    if legacy:
        call(
            cycle=0,
            phase="policy_generate",
            module="policy",
            action="generate (safe_complete_path)",
            sequence_in_cycle=SEQ_POLICY,
            started_at=T0 + 17_624,
            duration_ms=1890,
            raw_response="REGENERATED SAFE_COMPLETE TEXT",
        )
        call(
            cycle=1,
            phase="critic",
            module="critic",
            action=HARD_VIOLATION_REVALIDATION_ACTION,
            sequence_in_cycle=SEQ_CRITIC,
            started_at=T0 + 19_515,
            duration_ms=1715,
            parsed_summary_json="Violations: 0, Guidance: N/A",
        )
    else:
        call(
            cycle=1,
            phase="policy_generate",
            module="policy",
            action=HARD_VIOLATION_REGENERATION_ACTION,
            sequence_in_cycle=SEQ_HARD_VIOLATION_REGENERATION,
            started_at=T0 + 17_624,
            duration_ms=1890,
            raw_response="REGENERATED SAFE_COMPLETE TEXT",
        )
        call(
            cycle=1,
            phase="critic",
            module="critic",
            action=HARD_VIOLATION_REVALIDATION_ACTION,
            sequence_in_cycle=SEQ_HARD_VIOLATION_REVALIDATION,
            started_at=T0 + 19_515,
            duration_ms=1715,
            parsed_summary_json="Violations: 0, Guidance: N/A",
        )

    # routing debug events: the explanation in force at the branch, the branch,
    # then the post-deliberation explanation (winning_rule=hard_violations).
    def debug(created_at: int, message: str, data: dict) -> None:
        persist_debug_event(
            run_id=run_id,
            request_id=request_id,
            payload={"location": "orchestrator.py:process", "message": message, "data": data, "timestamp": created_at},
        )

    debug(
        T0 + 13_486,
        "DECISION_EXPLANATION",
        {
            "event": "DECISION_EXPLANATION",
            "final_action": "SAFE_COMPLETE",
            "winning_rule": "policy_bounds_fallback",
            "risk_score": 0.35,
        },
    )
    debug(
        T0 + 13_487,
        "branch risk_policy vs deliberative",
        {"risk_policy_action": "DELIBERATE", "risk_score": 0.35, "threshold_low": 0.3, "decision.path": "DELIBERATIVE_PATH"},
    )
    debug(T0 + 13_487, "taking _deliberative_path", {"path_taken": "deliberative"})
    debug(
        T0 + 17_675,
        "DECISION_EXPLANATION",
        {
            "event": "DECISION_EXPLANATION",
            "final_action": "SAFE_COMPLETE",
            "winning_rule": "hard_violations",
            "risk_score": 0.35,
            "reason_codes": ["HARD_VIOLATION_DOWNGRADED_TO_SAFE_COMPLETE"],
        },
    )
    persist_decision_trace(
        run_id=run_id,
        request_id=request_id,
        stage="FINAL",
        sequence=2,
        trace_json=json.dumps(
            {
                "final_action": "SAFE_COMPLETE",
                "path": "DELIBERATIVE_PATH",
                "risk_score": 0.35,
                "total_cycles": 1,
                "stop_reason": "HARD_VIOLATION_STOP",
                "hard_violation_codes": ["CORE.NM.1"],
            }
        ),
    )
    get_obs().flush()


def _graph_sequence(body: str) -> list[str]:
    """Cycle markers, pipe labels, and node actions of the execution graph, in
    document order (the rendered spine)."""
    start = body.index("<h2>Execution graph")
    end = body.index("flow-node--output", start)
    graph = body[start:end]
    seq: list[str] = []
    pattern = re.compile(
        r'<div class="flow-cycle-marker"[^>]*>(.*?)</div>'
        r'|<span class="flow-pipe-label">(.*?)</span>'
        r'|<span class="badge badge-type">(.*?)</span>',
        re.S,
    )
    for m in pattern.finditer(graph):
        text = next(g for g in m.groups() if g)
        seq.append(" ".join(re.sub(r"<[^>]+>", " ", text).split()))
    return seq


def _render(client: TestClient, run_id: str, request_id: str) -> str:
    token = _make_session_token(client)
    resp = client.get(f"/runs/{run_id}/requests/{request_id}", cookies={"moralstack_session": token})
    assert resp.status_code == 200, resp.text
    return resp.text


def _assert_true_causal_story(seq: list[str]) -> None:
    def idx(fragment: str) -> int:
        for i, s in enumerate(seq):
            if fragment in s:
                return i
        raise AssertionError(f"{fragment!r} not in graph sequence: {seq}")

    cycle0, cycle1 = idx("Initial assessment"), idx("Deliberation")
    routing, regen, recrit = (
        idx("route_resolution"),
        idx(HARD_VIOLATION_REGENERATION_ACTION),
        idx("hard_violation_revalidation"),
    )
    assert cycle0 < idx("calibrate") < routing < cycle1, seq
    # Both guard rows render in the deliberation cycle, after simulator/perspectives.
    assert cycle1 < idx("simulate") < regen < recrit, seq
    assert cycle1 < idx("evaluate") < regen, seq
    # The pipes name the real cause; the historical labels are gone.
    assert idx("hard violation → regenerate under SAFE_COMPLETE") == regen - 1, seq
    assert idx("re-critique regenerated draft") == recrit - 1, seq
    assert not any("gate: proceed" in s for s in seq), seq
    assert any("scheduled in parallel with critic" in s for s in seq), seq
    # Nothing labelled as a fast-path SAFE_COMPLETE draft is left in cycle 0.
    assert not any("safe_complete_path" in s for s in seq[cycle0:cycle1]), seq


def test_legacy_guard_rows_render_after_the_deliberation_that_caused_them(ui_client):
    run_id, request_id = "run-hvg-legacy", "req-hvg-legacy"
    _seed_request(run_id, request_id, legacy=True)
    body = _render(ui_client, run_id, request_id)

    _assert_true_causal_story(_graph_sequence(body))
    assert "legacy row re-homed to guard" in body
    # The regeneration's inputs name its cause and the re-critique its object.
    assert "hard_violations" in body and "regenerated_draft" in body
    # Routing node inputs come from the explanation in force at the branch.
    assert 'winning_rule <span class="muted">(policy_bounds_fallback)</span>' in body
    assert 'winning_rule <span class="muted">(hard_violations)</span>' not in body


def test_current_guard_rows_render_identically_without_the_legacy_badge(ui_client):
    run_id, request_id = "run-hvg-new", "req-hvg-new"
    _seed_request(run_id, request_id, legacy=False)
    body = _render(ui_client, run_id, request_id)

    _assert_true_causal_story(_graph_sequence(body))
    assert "legacy row re-homed to guard" not in body
