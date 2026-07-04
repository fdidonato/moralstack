"""Tests for read_store token usage query methods."""

from __future__ import annotations

import json

from moralstack.observability.events import (
    EVENT_LLM_CALL,
    EVENT_REQUEST_TOKEN_USAGE_FINALIZED,
    make_envelope,
)
from moralstack.observability.read_store import ReadStore, SqliteReadStore
from moralstack.observability.router import route
from moralstack.observability.sinks.sqlite_sink import create_run, init_db, upsert_request


def _setup(tmp_path, monkeypatch):
    dbp = str(tmp_path / "token_rs.db")
    monkeypatch.setenv("MORALSTACK_DB_PATH", dbp)
    monkeypatch.setenv("MORALSTACK_OBSERVABILITY_MODE", "db_only")
    init_db(dbp)
    return dbp


def _usage_json(prompt: int, completion: int, total: int) -> str:
    return json.dumps(
        {
            "prompt_tokens": prompt,
            "completion_tokens": completion,
            "total_tokens": total,
            "source": "exact",
        }
    )


def test_get_token_usage_totals_returns_aggregated_values(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    create_run("run-1", run_type="test", meta={})
    upsert_request("run-1", "req-1", prompt="hi", domain="")
    route(
        make_envelope(
            EVENT_REQUEST_TOKEN_USAGE_FINALIZED,
            run_id="run-1",
            request_id="req-1",
            payload={
                "input_tokens": 100,
                "output_tokens": 40,
                "total_tokens": 140,
                "llm_call_count": 2,
                "missing_usage_count": 1,
                "estimated_usage_count": 0,
            },
        )
    )
    rs = SqliteReadStore()
    totals = rs.get_token_usage_totals("run-1", "req-1")
    assert totals is not None
    assert totals["input_tokens"] == 100
    assert totals["total_tokens"] == 140
    assert totals["llm_call_count"] == 2
    assert totals["missing_usage_count"] == 1


def test_get_token_usage_totals_missing_request_returns_none_or_zero(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    rs = SqliteReadStore()
    assert rs.get_token_usage_totals("run-x", "req-missing") is None


def test_get_token_usage_breakdown_groups_by_module_phase_action_model(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    create_run("run-1", run_type="test", meta={})
    upsert_request("run-1", "req-1", prompt="hi", domain="")
    for module, model, tokens in (
        ("policy", "gpt-4", 30),
        ("risk_estimator", "gpt-4", 20),
    ):
        route(
            make_envelope(
                EVENT_LLM_CALL,
                run_id="run-1",
                request_id="req-1",
                payload={
                    "phase": "deliberation",
                    "module": module,
                    "action": "estimate",
                    "model": model,
                    "prompt": "p",
                    "raw_response": "{}",
                    "token_usage_json": _usage_json(tokens, 0, tokens),
                    "billable_provider_call": True,
                },
            )
        )
    route(
        make_envelope(
            EVENT_LLM_CALL,
            run_id="run-1",
            request_id="req-1",
            payload={
                "phase": "deliberation",
                "module": "policy",
                "action": "generate",
                "model": "gpt-4",
                "prompt": "p",
                "raw_response": "{}",
                "token_usage_json": _usage_json(5, 0, 5),
                "billable_provider_call": False,
            },
        )
    )
    rs = SqliteReadStore()
    breakdown = rs.get_token_usage_breakdown("run-1", "req-1")
    assert len(breakdown) == 2
    modules = {row["module"] for row in breakdown}
    assert modules == {"policy", "risk_estimator"}
    policy_row = next(r for r in breakdown if r["module"] == "policy")
    assert policy_row["total_tokens"] == 30


def test_read_store_protocol_declares_new_methods():
    assert hasattr(ReadStore, "get_token_usage_totals")
    assert hasattr(ReadStore, "get_token_usage_breakdown")
