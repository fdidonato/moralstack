"""Tests for SqliteReadStore: read contract correctness."""

from __future__ import annotations

import json

from moralstack.observability import obs
from moralstack.observability.context import set_current_request_id, set_current_run_id
from moralstack.observability.events import (
    EVENT_DEBUG_EVENT,
    EVENT_DECISION_TRACE,
    EVENT_LLM_CALL,
    EVENT_ORCHESTRATION_EVENT,
    make_envelope,
)
from moralstack.observability.read_store import SqliteReadStore
from moralstack.observability.router import route
from moralstack.observability.sinks.sqlite_sink import (
    create_run,
    init_db,
    upsert_request,
)


def _setup(tmp_path, monkeypatch):
    dbp = str(tmp_path / "rs.db")
    # Prefer canonical env; host shells often set MORALSTACK_OBSERVABILITY_DB_PATH,
    # which would otherwise win over the legacy alias below.
    monkeypatch.setenv("MORALSTACK_OBSERVABILITY_DB_PATH", dbp)
    monkeypatch.setenv("MORALSTACK_DB_PATH", dbp)
    monkeypatch.setenv("MORALSTACK_OBSERVABILITY_MODE", "db_only")
    init_db(dbp)
    return dbp


def test_get_run_returns_run(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    create_run("run-rs-1", run_type="benchmark", meta={"foo": "bar"})
    rs = SqliteReadStore()
    run = rs.get_run("run-rs-1")
    assert run is not None
    assert run["run_id"] == "run-rs-1"
    assert run.get("run_type") == "benchmark"


def test_get_run_returns_none_for_missing(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    rs = SqliteReadStore()
    assert rs.get_run("nonexistent") is None


def test_get_all_runs_returns_list(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    create_run("run-all-1", run_type="test", meta={})
    create_run("run-all-2", run_type="test", meta={})
    rs = SqliteReadStore()
    runs = rs.get_all_runs()
    run_ids = {r["run_id"] for r in runs}
    assert "run-all-1" in run_ids
    assert "run-all-2" in run_ids


def test_get_runs_page_applies_pagination_and_returns_total(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    rs = SqliteReadStore()
    for i in range(25):
        run_id = f"run-page-{i:02d}"
        create_run(run_id, run_type="test", meta={})
    page_1, total_1 = rs.get_runs_page(page=1, page_size=20)
    page_2, total_2 = rs.get_runs_page(page=2, page_size=20)
    assert total_1 == 25
    assert total_2 == 25
    assert len(page_1) == 20
    assert len(page_2) == 5


def test_get_runs_page_filters_by_domain_and_text(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    create_run("run-filter-med", run_type="test", meta={})
    create_run("run-filter-legal", run_type="test", meta={})
    create_run("run-filter-other", run_type="test", meta={})
    upsert_request(
        "run-filter-med",
        "req-med-1",
        prompt="How to interpret these symptoms?",
        domain="medical",
    )
    upsert_request(
        "run-filter-legal",
        "req-legal-1",
        prompt="Need legal opinion",
        domain="legal",
    )
    upsert_request(
        "run-filter-legal",
        "req-legal-2",
        prompt="Second legal question",
        domain="legal",
    )
    upsert_request(
        "run-filter-other",
        "req-gen-1",
        prompt="General topic",
        domain="general",
    )
    from moralstack.observability.sinks.sqlite_sink import update_request_response

    update_request_response("run-filter-med", "req-med-1", "Please contact your physician.")
    update_request_response("run-filter-legal", "req-legal-1", "This is a legal information response.")
    update_request_response("run-filter-legal", "req-legal-2", "Another legal answer.")
    update_request_response("run-filter-other", "req-gen-1", "Generic response.")

    rs = SqliteReadStore()
    legal_runs, legal_total = rs.get_runs_page(page=1, page_size=20, domain="legal")
    assert legal_total == 1
    assert [r["run_id"] for r in legal_runs] == ["run-filter-legal"]

    text_runs, text_total = rs.get_runs_page(page=1, page_size=20, search_text="physician")
    assert text_total == 1
    assert [r["run_id"] for r in text_runs] == ["run-filter-med"]

    combined_runs, combined_total = rs.get_runs_page(
        page=1,
        page_size=20,
        domain="legal",
        search_text="second",
    )
    assert combined_total == 1
    assert [r["run_id"] for r in combined_runs] == ["run-filter-legal"]


def test_get_request_domains_returns_distinct_sorted_values(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    create_run("run-domains", run_type="test", meta={})
    upsert_request("run-domains", "req-1", prompt="a", domain="legal")
    upsert_request("run-domains", "req-2", prompt="b", domain="medical")
    upsert_request("run-domains", "req-3", prompt="c", domain="legal")
    upsert_request("run-domains", "req-4", prompt="d", domain="")
    rs = SqliteReadStore()
    assert rs.get_request_domains() == ["legal", "medical"]


def test_get_requests_for_run(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    create_run("run-req-1", run_type="test", meta={})
    upsert_request("run-req-1", "req-a", prompt="p1", domain="d1")
    upsert_request("run-req-1", "req-b", prompt="p2", domain="d2")
    rs = SqliteReadStore()
    reqs = rs.get_requests_for_run("run-req-1")
    req_ids = {r["request_id"] for r in reqs}
    assert "req-a" in req_ids
    assert "req-b" in req_ids


def test_get_llm_calls_for_request(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    create_run("run-llm-1", run_type="test", meta={})
    upsert_request("run-llm-1", "req-llm", prompt="p", domain="")
    set_current_run_id("run-llm-1")
    set_current_request_id("req-llm")

    env = make_envelope(
        EVENT_LLM_CALL,
        run_id="run-llm-1",
        request_id="req-llm",
        cycle=1,
        payload={
            "phase": "deliberation",
            "module": "critic",
            "action": "critique",
            "prompt": "x",
            "raw_response": "{}",
            "attempts": 1,
        },
    )
    route(env)

    rs = SqliteReadStore()
    calls = rs.get_llm_calls_for_request("run-llm-1", "req-llm")
    assert len(calls) == 1
    assert calls[0]["module"] == "critic"


def test_get_decision_traces_for_request(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    create_run("run-dt-1", run_type="test", meta={})
    upsert_request("run-dt-1", "req-dt", prompt="p", domain="")

    trace_data = {"request_id": "req-dt", "stage": "FINAL", "final_action": "REFUSE"}
    env = make_envelope(
        EVENT_DECISION_TRACE,
        run_id="run-dt-1",
        request_id="req-dt",
        payload={"stage": "FINAL", "sequence": 1, "trace_json": json.dumps(trace_data)},
    )
    route(env)

    rs = SqliteReadStore()
    traces = rs.get_decision_traces_for_request("run-dt-1", "req-dt")
    assert len(traces) == 1
    td = json.loads(traces[0]["trace_json"])
    assert td["final_action"] == "REFUSE"


def test_get_orchestration_events_for_request(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    create_run("run-oe-1", run_type="test", meta={})
    upsert_request("run-oe-1", "req-oe", prompt="p", domain="")
    set_current_run_id("run-oe-1")
    set_current_request_id("req-oe")

    env = make_envelope(
        EVENT_ORCHESTRATION_EVENT,
        run_id="run-oe-1",
        request_id="req-oe",
        cycle=1,
        payload={
            "stage": "deliberation",
            "component": "runner",
            "event_type": "CYCLE_STARTED",
            "status": "ok",
            "sequence": 1,
        },
    )
    route(env)

    rs = SqliteReadStore()
    events = rs.get_orchestration_events_for_request("run-oe-1", "req-oe")
    assert len(events) == 1
    assert events[0]["event_type"] == "CYCLE_STARTED"


def test_context_shape_event_round_trips_payload(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    create_run("run-context-shape", run_type="test", meta={})
    upsert_request("run-context-shape", "req-context-shape", prompt="p", domain="")

    env = make_envelope(
        EVENT_ORCHESTRATION_EVENT,
        run_id="run-context-shape",
        request_id="req-context-shape",
        cycle=1,
        payload={
            "stage": "context",
            "component": "dccl",
            "event_type": "CONTEXT_SHAPE_RECORDED",
            "status": "ok",
            "sequence": 1,
            "payload": {
                "context_mode": "role_serialized_truncated",
                "prior_turn_count": 5,
                "prior_turns_used": 3,
                "history_truncation": "last_3",
            },
        },
    )
    route(env)

    rs = SqliteReadStore()
    events = rs.get_orchestration_events_for_request("run-context-shape", "req-context-shape")
    assert len(events) == 1
    assert events[0]["event_type"] == "CONTEXT_SHAPE_RECORDED"
    payload = json.loads(events[0]["payload_json"])
    assert payload["context_mode"] == "role_serialized_truncated"
    assert payload["prior_turn_count"] == 5
    assert payload["prior_turns_used"] == 3


def test_get_debug_events_for_request(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    create_run("run-de-1", run_type="test", meta={})
    upsert_request("run-de-1", "req-de", prompt="p", domain="")

    env = make_envelope(
        EVENT_DEBUG_EVENT,
        run_id="run-de-1",
        request_id="req-de",
        payload={"location": "test", "message": "debug_msg", "data": {"key": "val"}},
    )
    route(env)

    rs = SqliteReadStore()
    events = rs.get_debug_events_for_request("run-de-1", "req-de")
    assert len(events) == 1


def test_get_models_used_for_run(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    create_run("run-mu-1", run_type="test", meta={})
    upsert_request("run-mu-1", "req-mu", prompt="p", domain="")
    set_current_run_id("run-mu-1")
    set_current_request_id("req-mu")

    for module, action, model in [
        ("risk_estimator", "estimate", "gpt-4o-mini"),
        ("critic", "critique", "gpt-4o"),
    ]:
        env = make_envelope(
            EVENT_LLM_CALL,
            run_id="run-mu-1",
            request_id="req-mu",
            payload={
                "phase": "test",
                "module": module,
                "action": action,
                "model": model,
                "prompt": "",
                "raw_response": "",
                "attempts": 1,
            },
        )
        route(env)

    rs = SqliteReadStore()
    models = rs.get_models_used_for_run("run-mu-1")
    # models is {semantic_key: model_name}
    assert models.get("risk") == "gpt-4o-mini"
    assert models.get("critic") == "gpt-4o"


def test_read_store_protocol_via_obs():
    """obs.read_store exposes the same interface as SqliteReadStore."""
    rs = obs.read_store
    assert hasattr(rs, "get_run")
    assert hasattr(rs, "get_all_runs")
    assert hasattr(rs, "get_requests_for_run")
    assert hasattr(rs, "get_request")
    assert hasattr(rs, "get_llm_calls_for_request")
    assert hasattr(rs, "get_decision_traces_for_request")
    assert hasattr(rs, "get_orchestration_events_for_request")
    assert hasattr(rs, "get_debug_events_for_request")
    assert hasattr(rs, "get_models_used_for_run")


def test_get_models_used_for_run_ignores_skipped_or_discarded_calls(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    create_run("run-mu-2", run_type="test", meta={})
    upsert_request("run-mu-2", "req-mu-2", prompt="p", domain="")
    set_current_run_id("run-mu-2")
    set_current_request_id("req-mu-2")

    rows = [
        ("policy", "generate", "gpt-4o", "used"),
        ("policy", "rewrite", "gpt-4o-mini", "skipped"),
        ("critic", "critique", "gpt-4o", "used"),
        ("simulator", "simulate", "gpt-4o-mini", "discarded"),
    ]
    for module, action, model, call_outcome in rows:
        env = make_envelope(
            EVENT_LLM_CALL,
            run_id="run-mu-2",
            request_id="req-mu-2",
            payload={
                "phase": "test",
                "module": module,
                "action": action,
                "model": model,
                "call_outcome": call_outcome,
                "prompt": "",
                "raw_response": "",
                "attempts": 1,
            },
        )
        route(env)

    rs = SqliteReadStore()
    models = rs.get_models_used_for_run("run-mu-2")
    assert models.get("policy_generate") == "gpt-4o"
    assert "policy_rewrite" not in models
    assert "simulator" not in models
