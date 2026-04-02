"""Tests for orchestration_events persistence, runtime view-models, and trace stages (observability-only)."""

from __future__ import annotations

import json

from moralstack.persistence import db as db_module
from moralstack.persistence.context import set_current_request_id, set_current_run_id
from moralstack.persistence.db import (
    create_run,
    get_orchestration_events_for_request,
    init_db,
    upsert_request,
)
from moralstack.persistence.sink import persist_orchestration_event, persist_orchestration_events_batch
from moralstack.reports.runtime_decisions import (
    build_execution_strategy,
    build_runtime_decision_observability,
    enrich_llm_call_for_ui,
)


def test_orchestration_events_table_created(tmp_path, monkeypatch):
    monkeypatch.setenv("MORALSTACK_DB_PATH", str(tmp_path / "t.db"))
    monkeypatch.setenv("MORALSTACK_PERSIST_MODE", "db_only")
    assert init_db(str(tmp_path / "t.db")) is True
    conn = db_module._get_connection(str(tmp_path / "t.db"))
    row = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='orchestration_events'").fetchone()
    conn.close()
    assert row is not None


def test_orchestration_events_insert_and_order(tmp_path, monkeypatch):
    dbp = str(tmp_path / "o.db")
    monkeypatch.setenv("MORALSTACK_DB_PATH", dbp)
    monkeypatch.setenv("MORALSTACK_PERSIST_MODE", "db_only")

    init_db(dbp)
    assert create_run("run-a", run_type="test", meta={})
    assert upsert_request("run-a", "req-1", prompt="p", domain="d")
    set_current_run_id("run-a")
    set_current_request_id("req-1")
    assert (
        persist_orchestration_event(
            stage="deliberation",
            component="runner",
            event_type="PARALLEL_STRATEGY_SELECTED",
            decision="parallel",
            status="ok",
            sequence=1,
            payload={"x": 1},
        )
        is not None
    )
    assert persist_orchestration_events_batch(
        [
            {
                "stage": "deliberation",
                "component": "runner",
                "event_type": "CONVERGENCE_EVALUATED",
                "cycle": 1,
                "sequence": 2,
                "decision": "CONTINUE",
                "status": "ok",
            }
        ]
    )
    rows = get_orchestration_events_for_request("run-a", "req-1")
    assert len(rows) == 2
    assert rows[0]["event_type"] == "PARALLEL_STRATEGY_SELECTED"
    assert rows[1]["cycle"] == 1


def test_build_runtime_decision_observability_empty():
    vm = build_runtime_decision_observability(traces=[], orchestration_events=[], llm_calls=[])
    assert vm["has_orchestration_events"] is False
    assert vm["runtime_decisions"] == []
    assert vm["cycle_cards"] == []


def test_build_execution_strategy_speculative_from_events():
    orch = [
        {"event_type": "SPECULATIVE_STARTED", "payload_json": "{}"},
        {
            "event_type": "SPECULATIVE_JOIN_SKIPPED",
            "payload_json": '{"final_route": "refuse", "reason": "refuse_path", "elapsed_since_spec_start_ms": 12.3}',
        },
        {
            "event_type": "SPECULATIVE_RESULT_DISCARDED",
            "payload_json": '{"reason": "refuse_path", "final_route": "refuse"}',
        },
    ]
    es = build_execution_strategy([], llm_calls=[], orchestration_events=orch)
    sp = es.get("speculative") or {}
    assert sp.get("speculative_started") is True
    assert sp.get("speculative_outcome") == "discarded"
    assert sp.get("join_skipped") is True
    assert sp.get("skip_elapsed_ms") == 12.3


def test_enrich_llm_call_speculative_used_badge():
    row = enrich_llm_call_for_ui({"call_kind": "speculative", "call_outcome": "used", "parsed_summary_json": "{}"})
    assert "speculative" in row["semantic_badges"]
    assert "used" in row["semantic_badges"]


def test_build_execution_strategy_from_risk_trace():
    traces = [
        {
            "stage": "RISK_ASSESSMENT",
            "sequence": -10,
            "trace_json": json.dumps(
                {
                    "risk_score": 0.4,
                    "risk_category": "benign",
                    "operational_risk": "NONE",
                    "intent_to_harm": False,
                    "requested_instructions": False,
                    "intent_operational": False,
                    "estimation_mode": "parallel",
                    "stage_payload": {
                        "risk_policy_action": "ALLOW",
                        "detected_domain": "general",
                        "activated_signals": ["Q1"],
                    },
                }
            ),
        }
    ]
    es = build_execution_strategy(traces, llm_calls=[])
    ra = es.get("risk_assessment") or {}
    assert ra.get("risk_score") == 0.4
    assert ra.get("risk_policy_action") == "ALLOW"


def test_llm_calls_extended_columns_roundtrip(tmp_path, monkeypatch):
    """Nullable llm_calls semantic columns persist and read back."""
    from moralstack.persistence.sink import persist_llm_call

    dbp = str(tmp_path / "lc.db")
    monkeypatch.setenv("MORALSTACK_DB_PATH", dbp)
    monkeypatch.setenv("MORALSTACK_PERSIST_MODE", "db_only")
    init_db(dbp)
    assert create_run("r1", run_type="test", meta={})
    assert upsert_request("r1", "q1", prompt="x", domain="")
    set_current_run_id("r1")
    set_current_request_id("q1")
    assert persist_llm_call(
        phase="risk",
        module="risk_estimator",
        action="estimate",
        call_kind="normal",
        call_outcome="used",
        cache_status="miss",
    )
    from moralstack.persistence.db import get_llm_calls_for_request

    rows = get_llm_calls_for_request("r1", "q1")
    assert len(rows) == 1
    assert rows[0].get("call_kind") == "normal"
    assert rows[0].get("call_outcome") == "used"
    assert rows[0].get("cache_status") == "miss"
