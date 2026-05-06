"""
Tests for markdown report ordering of the deliberation journey.

Mirrors the UI's `_journey_sort_key` test (test_ui_journey_order.py) but for the
markdown report path (`request_report_from_db` in moralstack/reports/model.py).

The bug being fixed: for cycle=0 (FAST_PATH, no deliberation), modules emit
inconsistent `sequence_in_cycle` values:
- constitution_retriever: NULL (coalesced to 999 by SQL ORDER BY)
- risk_estimator (initial assessment): NULL → 999
- speculative_generate: 0
- refusal_handler (FAST_PATH refuse): 6 (SEQ_REFUSAL_OR_FINALIZE, designed for cycle>=1)

Naive sort by sequence_in_cycle puts refusal (seq=6) BEFORE constitution/risk
(seq=999) even though refusal happens later in wall-clock time. The SEQ_*
constants are designed for deliberation cycles (cycle>=1) where each module
runs in a known logical order; on cycle=0 they don't apply consistently.

Fix: for cycle=0, sort by started_at (wall-clock truth). For cycle>=1, sort by
sequence_in_cycle (logical order).
"""

from __future__ import annotations

import json

import pytest

from moralstack.persistence.db import create_run, init_db, upsert_request
from moralstack.persistence.sink import persist_decision_trace, persist_llm_call
from moralstack.reports.model import request_report_from_db


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    """Create a fresh SQLite DB for an isolated test, with a run + request seeded."""
    db_path = str(tmp_path / "report_journey_order.db")
    monkeypatch.setenv("MORALSTACK_DB_PATH", db_path)
    monkeypatch.setenv("MORALSTACK_PERSIST_MODE", "db_only")
    assert init_db(db_path)
    run_id = "run-journey-order"
    request_id = "req-journey-order"
    assert create_run(run_id, run_type="test", meta={})
    assert upsert_request(run_id, request_id, prompt="Hello", domain="general")
    # Minimal FINAL trace so request_report_from_db can build the report
    final_trace = {
        "path": "FAST_PATH",
        "total_cycles": 0,
        "stop_reason": "",
        "final_action": "REFUSE",
        "risk_score": 0.95,
        "risk_category": "clearly_harmful",
    }
    assert persist_decision_trace(
        run_id=run_id,
        request_id=request_id,
        stage="FINAL",
        sequence=2,
        trace_json=json.dumps(final_trace),
    )
    return run_id, request_id


def _seed_call(*, run_id, request_id, cycle, sequence_in_cycle, started_at, module, phase):
    """Helper to persist a single llm_call with explicit ordering metadata."""
    return persist_llm_call(
        run_id=run_id,
        request_id=request_id,
        cycle=cycle,
        phase=phase,
        module=module,
        action="estimate" if module.startswith("risk") else phase,
        model="test-model",
        started_at=started_at,
        duration_ms=100.0,
        prompt="p",
        system_prompt="s",
        raw_response="r",
        sequence_in_cycle=sequence_in_cycle,
    )


class TestCycle0OrderingIsByStartedAt:
    """For cycle=0 (FAST_PATH), sequence_in_cycle is inconsistent across modules:
    refusal_handler emits seq=6 (SEQ_REFUSAL_OR_FINALIZE designed for deliberation),
    while constitution_retriever and risk_estimator (initial) emit NULL→999.
    The temporally-correct order must use started_at, not sequence_in_cycle.
    """

    def test_cycle0_refusal_with_seq6_does_not_precede_earlier_calls_with_seq_null(self, tmp_db):
        run_id, request_id = tmp_db

        # Reproduce the strong_reject scenario: constitution and risk_estimator
        # run first (no sequence_in_cycle → NULL → 999), refusal runs LAST but
        # emits sequence_in_cycle=6 (because it inherits SEQ_REFUSAL_OR_FINALIZE
        # which is designed for cycle>=1 deliberation).
        assert _seed_call(
            run_id=run_id,
            request_id=request_id,
            cycle=0,
            sequence_in_cycle=None,
            started_at=1000,
            module="constitution_retriever",
            phase="constitution_retrieval",
        )
        assert _seed_call(
            run_id=run_id,
            request_id=request_id,
            cycle=0,
            sequence_in_cycle=None,
            started_at=1500,
            module="risk_estimator",
            phase="risk_assessment",
        )
        assert _seed_call(
            run_id=run_id,
            request_id=request_id,
            cycle=0,
            sequence_in_cycle=6,
            started_at=5000,  # LATER in wall-clock!
            module="orchestration",
            phase="refusal",
        )

        report = request_report_from_db(run_id, request_id)
        assert report is not None
        cycle0_phases = dict(report.phases_by_cycle).get(0, [])
        phase_types_in_order = [p.phase_type for p in cycle0_phases]

        # The refusal step (started_at=5000) must NOT come before the constitution
        # (1000) or risk_estimator (1500) calls. Wall-clock order must win when
        # sequence_in_cycle is inconsistent across modules within cycle=0.
        refusal_index = next(i for i, t in enumerate(phase_types_in_order) if "refusal" in t)
        constitution_index = next(i for i, t in enumerate(phase_types_in_order) if "constitution" in t)
        risk_index = next(i for i, t in enumerate(phase_types_in_order) if "risk" in t)

        assert (
            constitution_index < refusal_index
        ), f"constitution must precede refusal (started_at 1000 < 5000); got {phase_types_in_order}"
        assert (
            risk_index < refusal_index
        ), f"risk_estimator must precede refusal (started_at 1500 < 5000); got {phase_types_in_order}"
        # Constitution started at 1000, risk at 1500 → constitution first.
        assert constitution_index < risk_index


class TestCycleGE1OrderingIsBySequenceInCycle:
    """For cycle>=1 (deliberation cycles), the SEQ_* constants in
    deliberation_runner.py define the logical order: SEQ_POLICY=1, SEQ_CRITIC=2,
    SEQ_SIMULATOR=3, SEQ_PERSPECTIVES=4, SEQ_HINDSIGHT=5. Modules may run in
    parallel (overlapping started_at) but the journey must reflect the logical
    pipeline order, not wall-clock.
    """

    def test_cycle1_phases_sorted_by_sequence_in_cycle_even_when_started_at_overlaps(self, tmp_db):
        run_id, request_id = tmp_db

        # Critic and simulator may start near-simultaneously in parallel mode;
        # critic has seq=2, simulator has seq=3 → critic must come first
        # regardless of which started slightly earlier in wall-clock.
        assert _seed_call(
            run_id=run_id,
            request_id=request_id,
            cycle=1,
            sequence_in_cycle=3,
            started_at=2000,  # simulator started slightly earlier
            module="simulator",
            phase="simulate",
        )
        assert _seed_call(
            run_id=run_id,
            request_id=request_id,
            cycle=1,
            sequence_in_cycle=2,
            started_at=2050,  # critic started slightly later
            module="critic",
            phase="critique",
        )
        assert _seed_call(
            run_id=run_id,
            request_id=request_id,
            cycle=1,
            sequence_in_cycle=1,
            started_at=1900,
            module="policy",
            phase="policy_generate",
        )

        report = request_report_from_db(run_id, request_id)
        assert report is not None
        cycle1_phases = dict(report.phases_by_cycle).get(1, [])
        phase_types_in_order = [p.phase_type for p in cycle1_phases]

        # Logical pipeline order must hold: policy(1) → critic(2) → simulator(3).
        policy_index = next(i for i, t in enumerate(phase_types_in_order) if "policy" in t)
        critic_index = next(i for i, t in enumerate(phase_types_in_order) if "critic" in t)
        simulator_index = next(i for i, t in enumerate(phase_types_in_order) if "simulat" in t)

        assert policy_index < critic_index < simulator_index, (
            f"deliberation order must be policy→critic→simulator by sequence_in_cycle; " f"got {phase_types_in_order}"
        )
