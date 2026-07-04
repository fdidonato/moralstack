"""Tests for orchestration_events persistence, runtime view-models, and trace stages (observability-only)."""

from __future__ import annotations

import json
import re

import pytest

from moralstack.observability import obs, router
from moralstack.observability import service as service_module
from moralstack.observability.context import set_current_request_id, set_current_run_id
from moralstack.observability.emit_helpers import (
    persist_decision_trace,
    persist_llm_call,
    persist_orchestration_event,
    persist_orchestration_events_batch,
)
from moralstack.observability.service import get_obs
from moralstack.observability.sinks import sqlite_sink as db_module
from moralstack.observability.sinks.sqlite_sink import create_run, init_db, upsert_request
from moralstack.reports.markdown_export import export_request_markdown
from moralstack.reports.runtime_decisions import (
    build_execution_strategy,
    build_runtime_decision_observability,
    build_runtime_observability_contract,
    enrich_llm_call_for_ui,
)

get_orchestration_events_for_request = obs.read_store.get_orchestration_events_for_request
get_llm_calls_for_request = obs.read_store.get_llm_calls_for_request


@pytest.fixture(autouse=True)
def _fresh_obs_singleton():
    try:
        get_obs().shutdown(timeout=1.0)
    except Exception:
        pass
    service_module._obs_instance = None
    router._sqlite_sink = None
    router._jsonl_sink = None
    yield
    try:
        get_obs().shutdown(timeout=1.0)
    except Exception:
        pass
    service_module._obs_instance = None
    router._sqlite_sink = None
    router._jsonl_sink = None


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
    persist_orchestration_event(
        stage="deliberation",
        component="runner",
        event_type="PARALLEL_STRATEGY_SELECTED",
        decision="parallel",
        status="ok",
        sequence=1,
        payload={"x": 1},
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
    obs.flush(timeout=10.0)
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
    from moralstack.observability.emit_helpers import persist_llm_call

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
        parsed_summary_json={"context_shape": {"context_mode": "full_native"}},
    )
    obs.flush(timeout=10.0)
    rows = get_llm_calls_for_request("r1", "q1")
    assert len(rows) == 1
    assert rows[0].get("call_kind") == "normal"
    assert rows[0].get("call_outcome") == "used"
    assert rows[0].get("cache_status") == "miss"
    assert json.loads(rows[0].get("parsed_summary_json")) == {"context_shape": {"context_mode": "full_native"}}


def test_speculative_skip_elapsed_is_clamped_to_total_duration():
    orch = [
        {"event_type": "SPECULATIVE_STARTED", "payload_json": "{}"},
        {
            "event_type": "SPECULATIVE_JOIN_SKIPPED",
            "payload_json": '{"elapsed_since_spec_start_ms": 99.0}',
        },
    ]
    llm_calls = [{"duration_ms": 12.0}, {"duration_ms": 8.0}]
    es = build_execution_strategy([], llm_calls=llm_calls, orchestration_events=orch)
    sp = es.get("speculative") or {}
    assert sp.get("total_duration_ms") == 20.0
    assert 0.0 <= float(sp.get("skip_elapsed_ms")) <= float(sp.get("total_duration_ms"))


def test_cross_trace_activated_signals_coherence_and_contract():
    traces = [
        {
            "stage": "RISK_ASSESSMENT",
            "trace_json": json.dumps(
                {
                    "risk_score": 0.6,
                    "stage_payload": {
                        "activated_signals": [],
                    },
                }
            ),
        },
        {
            "stage": "PRE_POLICY",
            "trace_json": json.dumps({"activated_signals": []}),
        },
        {
            "stage": "FINAL",
            "trace_json": json.dumps(
                {
                    "path": "FAST_PATH",
                    "final_action": "REFUSE",
                    "activated_signals": ["Q10:weapons_explosives_toxins"],
                }
            ),
        },
    ]
    vm = build_runtime_decision_observability(traces=traces, orchestration_events=[], llm_calls=[])
    risk = (vm.get("execution_strategy") or {}).get("risk_assessment") or {}
    assert risk.get("activated_signals") == ["Q10:weapons_explosives_toxins"]
    contract = build_runtime_observability_contract(
        traces=traces,
        execution_strategy=vm.get("execution_strategy") or {},
        orchestration_events=[],
        runtime_decisions=vm.get("runtime_decisions") or [],
        cycle_cards=vm.get("cycle_cards") or [],
    )
    assert contract["valid"] is False
    assert "activated_signals_missing_in_PRE_POLICY" in contract["anomalies"]


def test_export_fast_path_refuse_contract_and_models(tmp_path, monkeypatch):
    dbp = str(tmp_path / "exp.db")
    monkeypatch.setenv("MORALSTACK_DB_PATH", dbp)
    monkeypatch.setenv("MORALSTACK_PERSIST_MODE", "db_only")
    init_db(dbp)
    assert create_run("run-exp", run_type="test", meta={})
    assert upsert_request("run-exp", "req-exp", prompt="p", domain="")
    set_current_run_id("run-exp")
    set_current_request_id("req-exp")

    assert persist_llm_call(
        phase="fast",
        module="policy",
        action="generate",
        model="gpt-4o",
        call_outcome="used",
        raw_response="I cannot assist with that.",
    )
    assert persist_llm_call(
        phase="fast",
        module="policy",
        action="rewrite",
        model="gpt-4o-mini",
        call_outcome="skipped",
        raw_response="",
    )
    assert persist_decision_trace(
        stage="RISK_ASSESSMENT",
        sequence=-10,
        trace_json=json.dumps(
            {
                "risk_score": 0.92,
                "stage_payload": {
                    "activated_signals": ["Q10:weapons_explosives_toxins"],
                },
            }
        ),
    )
    assert persist_decision_trace(
        stage="PRE_POLICY",
        sequence=1,
        trace_json=json.dumps(
            {
                "path": "FAST_PATH",
                "activated_signals": ["Q10:weapons_explosives_toxins"],
            }
        ),
    )
    assert persist_decision_trace(
        stage="FINAL",
        sequence=2,
        trace_json=json.dumps(
            {
                "path": "FAST_PATH",
                "final_action": "REFUSE",
                "risk_score": 0.92,
                "activated_signals": ["Q10:weapons_explosives_toxins"],
            }
        ),
    )
    persist_orchestration_event(
        stage="orchestration",
        component="router",
        event_type="ROUTE_SELECTED",
        decision="refuse",
        status="ok",
        sequence=1,
    )
    obs.flush(timeout=10.0)
    md = export_request_markdown("run-exp", "req-exp")
    assert "| **MoralStack policy (rewrite)** | gpt-4o |" in md
    assert "gpt-4o-mini" not in md

    match = re.search(r"## Runtime observability \(structured JSON\).*?```json\n(.*?)\n```", md, re.S)
    assert match is not None
    payload = json.loads(match.group(1))
    contract = payload.get("metric_contract") or {}
    assert contract.get("valid") is True
