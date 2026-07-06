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


def _emit_llm_call(
    run_id: str,
    request_id: str,
    *,
    model: str,
    total: int,
    source: str = "exact",
    billable: bool = True,
) -> None:
    route(
        make_envelope(
            EVENT_LLM_CALL,
            run_id=run_id,
            request_id=request_id,
            payload={
                "phase": "deliberation",
                "module": "policy",
                "action": "generate",
                "model": model,
                "prompt": "p",
                "raw_response": "{}",
                "token_usage_json": json.dumps(
                    {
                        "prompt_tokens": total,
                        "completion_tokens": 0,
                        "total_tokens": total,
                        "source": source,
                    }
                ),
                "billable_provider_call": billable,
            },
        )
    )


def test_get_token_usage_by_model_for_request_groups_and_excludes_non_billable(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    create_run("run-1", run_type="test", meta={})
    upsert_request("run-1", "req-1", prompt="hi", domain="")
    _emit_llm_call("run-1", "req-1", model="gpt-4", total=30)
    _emit_llm_call("run-1", "req-1", model="gpt-4", total=20)
    _emit_llm_call("run-1", "req-1", model="gpt-4o-mini", total=10)
    _emit_llm_call("run-1", "req-1", model="gpt-4", total=999, billable=False)
    rs = SqliteReadStore()
    by_model = rs.get_token_usage_by_model_for_request("run-1", "req-1")
    totals = {row["model"]: row for row in by_model}
    assert totals["gpt-4"]["total_tokens"] == 50
    assert totals["gpt-4"]["calls"] == 2
    assert totals["gpt-4o-mini"]["total_tokens"] == 10
    # Non-billable call is never summed.
    assert all(row["total_tokens"] != 999 for row in by_model)


def test_get_token_usage_by_model_for_run_sums_across_requests(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    create_run("run-1", run_type="test", meta={})
    upsert_request("run-1", "req-1", prompt="a", domain="")
    upsert_request("run-1", "req-2", prompt="b", domain="")
    _emit_llm_call("run-1", "req-1", model="gpt-4", total=30)
    _emit_llm_call("run-1", "req-2", model="gpt-4", total=70)
    rs = SqliteReadStore()
    by_model = rs.get_token_usage_by_model_for_run("run-1")
    assert len(by_model) == 1
    assert by_model[0]["model"] == "gpt-4"
    assert by_model[0]["total_tokens"] == 100


def test_get_token_usage_by_model_global_sums_across_runs(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    for run_id in ("run-1", "run-2"):
        create_run(run_id, run_type="test", meta={})
        upsert_request(run_id, "req-1", prompt="hi", domain="")
        _emit_llm_call(run_id, "req-1", model="gpt-4", total=25)
    rs = SqliteReadStore()
    by_model = rs.get_token_usage_by_model_global()
    assert len(by_model) == 1
    assert by_model[0]["total_tokens"] == 50


def test_get_token_usage_by_model_for_conversation_joins_requests(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    create_run("run-1", run_type="test", meta={})
    upsert_request("run-1", "req-1", prompt="a", domain="", conversation_id="conv-1", turn_index=0)
    upsert_request("run-1", "req-2", prompt="b", domain="", conversation_id="conv-1", turn_index=1)
    upsert_request("run-1", "req-3", prompt="c", domain="", conversation_id="conv-2", turn_index=0)
    _emit_llm_call("run-1", "req-1", model="gpt-4", total=30)
    _emit_llm_call("run-1", "req-2", model="gpt-4", total=40)
    _emit_llm_call("run-1", "req-3", model="gpt-4", total=999)
    rs = SqliteReadStore()
    by_model = rs.get_token_usage_by_model_for_conversation("conv-1")
    assert len(by_model) == 1
    assert by_model[0]["total_tokens"] == 70


def test_get_token_usage_by_model_counts_estimated_and_missing(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    create_run("run-1", run_type="test", meta={})
    upsert_request("run-1", "req-1", prompt="hi", domain="")
    _emit_llm_call("run-1", "req-1", model="gpt-4", total=100, source="exact")
    _emit_llm_call("run-1", "req-1", model="gpt-4", total=70, source="estimated")
    _emit_llm_call("run-1", "req-1", model="gpt-4", total=0, source="missing")
    rs = SqliteReadStore()
    by_model = rs.get_token_usage_by_model_for_request("run-1", "req-1")
    row = by_model[0]
    assert row["estimated_usage"] == 1
    assert row["missing_usage"] == 1
    assert row["calls"] == 3


def test_read_store_protocol_declares_new_methods():
    assert hasattr(ReadStore, "get_token_usage_totals")
    assert hasattr(ReadStore, "get_token_usage_breakdown")
    assert hasattr(ReadStore, "get_token_usage_by_model_global")
    assert hasattr(ReadStore, "get_token_usage_by_model_for_run")
    assert hasattr(ReadStore, "get_token_usage_by_model_for_request")
    assert hasattr(ReadStore, "get_token_usage_by_model_for_conversation")
