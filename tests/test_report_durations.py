"""
Tests for wall-clock aggregation of phase durations and total duration in
RequestReport.

Bug being fixed: parallel modules (e.g., 3 risk_estimator calls) report
their individual durations; the report sums them, inflating both
`phase_durations[phase_type]` and `total_duration_ms` (e.g., 9427ms instead
of the actual 4341ms wall-clock for parallel estimators).

Fix: aggregate by merging overlapping intervals (start, end) and summing
the merged-interval lengths, instead of summing individual durations.
"""

from __future__ import annotations

import json

import pytest

from moralstack.persistence.db import create_run, init_db, upsert_request
from moralstack.persistence.sink import persist_decision_trace, persist_llm_call
from moralstack.reports.model import request_report_from_db


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    db_path = str(tmp_path / "report_durations.db")
    monkeypatch.setenv("MORALSTACK_DB_PATH", db_path)
    monkeypatch.setenv("MORALSTACK_PERSIST_MODE", "db_only")
    assert init_db(db_path)
    run_id = "run-durations"
    request_id = "req-durations"
    assert create_run(run_id, run_type="test", meta={})
    assert upsert_request(run_id, request_id, prompt="Hello", domain="general")
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


def _seed_call(*, run_id, request_id, started_at, duration_ms, module, phase, action="estimate"):
    return persist_llm_call(
        run_id=run_id,
        request_id=request_id,
        cycle=0,
        phase=phase,
        module=module,
        action=action,
        model="test",
        started_at=started_at,
        duration_ms=duration_ms,
        prompt="p",
        system_prompt="s",
        raw_response="r",
        sequence_in_cycle=None,
    )


class TestTotalDurationWallClock:
    """`total_duration_ms` must reflect wall-clock time, merging overlapping
    intervals from parallel calls — NOT the naive sum of individual durations.
    """

    def test_total_ms_uses_wall_clock_when_calls_overlap(self, tmp_db):
        run_id, request_id = tmp_db
        # Call A: [1000, 1500] (duration 500)
        # Call B: [1200, 1800] (duration 600), overlaps A in [1200, 1500]
        # Naive sum = 1100. Wall-clock merged = 800 (1000 → 1800).
        assert _seed_call(
            run_id=run_id,
            request_id=request_id,
            started_at=1000,
            duration_ms=500,
            module="risk_estimator",
            phase="estimate_intent",
            action="estimate_intent",
        )
        assert _seed_call(
            run_id=run_id,
            request_id=request_id,
            started_at=1200,
            duration_ms=600,
            module="risk_estimator",
            phase="estimate_operational",
            action="estimate_operational",
        )

        report = request_report_from_db(run_id, request_id)
        assert report is not None
        assert report.total_duration_ms == 800.0, f"expected wall-clock merged 800ms, got {report.total_duration_ms}"


class TestPhaseDurationsWallClock:
    """`phase_durations` must aggregate by wall-clock per phase_type, merging
    overlapping intervals. Disjoint intervals must still sum (no over-merge).
    """

    def test_phase_durations_per_phase_uses_wall_clock_with_overlap(self, tmp_db):
        run_id, request_id = tmp_db
        # Two risk_estimator/estimate calls overlap → wall-clock 800ms, not 1100.
        assert _seed_call(
            run_id=run_id,
            request_id=request_id,
            started_at=1000,
            duration_ms=500,
            module="risk_estimator",
            phase="estimate",
            action="estimate",
        )
        assert _seed_call(
            run_id=run_id,
            request_id=request_id,
            started_at=1200,
            duration_ms=600,
            module="risk_estimator",
            phase="estimate",
            action="estimate",
        )

        report = request_report_from_db(run_id, request_id)
        assert report is not None
        # phase_type key built as `module + " / " + phase`
        key = "risk_estimator / estimate"
        assert (
            key in report.phase_durations
        ), f"expected phase '{key}' in phase_durations; got {list(report.phase_durations.keys())}"
        assert (
            report.phase_durations[key] == 800.0
        ), f"expected merged wall-clock 800ms for '{key}', got {report.phase_durations[key]}"

    def test_phase_durations_disjoint_intervals_still_sum(self, tmp_db):
        run_id, request_id = tmp_db
        # Two NON-overlapping calls of same phase: [1000,1500] and [2000,2400].
        # Wall-clock total = 500 + 400 = 900 (no overlap to merge).
        assert _seed_call(
            run_id=run_id,
            request_id=request_id,
            started_at=1000,
            duration_ms=500,
            module="critic",
            phase="critique",
            action="critique",
        )
        assert _seed_call(
            run_id=run_id,
            request_id=request_id,
            started_at=2000,
            duration_ms=400,
            module="critic",
            phase="critique",
            action="critique",
        )

        report = request_report_from_db(run_id, request_id)
        assert report is not None
        key = "critic / critique"
        assert (
            report.phase_durations[key] == 900.0
        ), f"disjoint intervals must sum (no over-merge); expected 900, got {report.phase_durations[key]}"
