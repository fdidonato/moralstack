"""Tests for risk-estimator mini-call synchronous batch persistence."""

from __future__ import annotations

import json
import sqlite3
from unittest.mock import MagicMock

import moralstack.models.risk.estimator as risk_estimator_module
from moralstack.models.risk.estimator import LLMBasedRiskEstimator
from moralstack.models.risk.schema import RiskEstimatorConfig
from moralstack.observability.context import (
    set_current_cycle,
    set_current_request_id,
    set_current_run_id,
    set_current_session_id,
    set_current_turn_number,
)
from moralstack.observability.events import EVENT_LLM_CALL, EventEnvelope
from moralstack.observability.read_store import SqliteReadStore
from moralstack.observability.sinks import sqlite_sink
from moralstack.observability.sinks.sqlite_sink import SqliteEventSink, create_run, init_db, upsert_request

LOCAL_LLM_CALL_PAYLOAD_KEYS = (
    "phase",
    "module",
    "action",
    "model",
    "started_at",
    "duration_ms",
    "prompt",
    "system_prompt",
    "raw_response",
    "parsed_json",
    "parsed_summary_json",
    "token_usage_json",
    "attempts",
    "error",
    "sequence_in_cycle",
)

GENERIC_ONLY_LLM_CALL_KEYS = {"call_kind", "call_outcome", "cache_status", "related_event_id"}


def _set_context(run_id: str = "run-risk-batch", request_id: str = "req-risk-batch") -> None:
    set_current_run_id(run_id)
    set_current_request_id(request_id)
    set_current_cycle(0)
    set_current_session_id("session-risk-batch")
    set_current_turn_number(1)


def _estimator() -> LLMBasedRiskEstimator:
    policy = MagicMock()
    policy.model = "gpt-main"
    policy.tracker = None
    return LLMBasedRiskEstimator(policy=policy, config=RiskEstimatorConfig())


def _mini_calls() -> list[dict[str, object]]:
    return [
        {
            "system_prompt": "intent system",
            "prompt": "intent prompt",
            "raw_response": '{"intent":"benign"}',
            "action": "estimate_intent",
            "started_at": 1001,
            "duration_ms": 1.1,
            "attempts": 1,
            "parse_contract": {"strict_json_requested": True},
            "token_usage_json": '{"prompt_tokens":1,"completion_tokens":2,"total_tokens":3}',
            "llm_model": "gpt-intent",
            "message_sections": {"developer_messages": ["contract"], "final_user_message": "request"},
        },
        {
            "system_prompt": "signals system",
            "prompt": "signals prompt",
            "raw_response": '{"signals":[]}',
            "action": "estimate_signals",
            "started_at": 1002,
            "duration_ms": 2.2,
            "attempts": 2,
            "parse_contract": {"strict_json_requested": True, "recovered": False},
            "token_usage_json": '{"prompt_tokens":4,"completion_tokens":5,"total_tokens":9}',
            "llm_model": "gpt-signals",
            "message_sections": {"history_messages": [{"role": "user", "content": "prior"}]},
        },
        {
            "system_prompt": "operational system",
            "prompt": "operational prompt",
            "raw_response": '{"risk_score":0.1}',
            "action": "estimate_operational",
            "started_at": 1003,
            "duration_ms": 3.3,
            "attempts": 1,
            "parse_contract": {"strict_json_requested": True},
            "token_usage_json": '{"prompt_tokens":6,"completion_tokens":7,"total_tokens":13}',
            "llm_model": "gpt-operational",
            "message_sections": {"final_user_message": "request"},
        },
    ]


def _capture_batch(monkeypatch) -> list[list[EventEnvelope]]:
    batches: list[list[EventEnvelope]] = []

    def capture(envelopes):
        batches.append(list(envelopes))

    monkeypatch.setattr(risk_estimator_module, "_obs_route_batch", capture)
    return batches


def test_phase1_uses_router_route_batch_not_async_queue(monkeypatch):
    _set_context()
    batches = _capture_batch(monkeypatch)

    def fail_async_queue():
        raise AssertionError("risk mini-call persistence must not use ObservabilityService.emit_batch")

    monkeypatch.setattr("moralstack.observability.service.get_obs", fail_async_queue)

    _estimator()._persist_mini_llm_calls_batch(_mini_calls())

    assert len(batches) == 1
    assert len(batches[0]) == 3


def test_three_mini_envelopes_have_local_15_key_payload(monkeypatch):
    _set_context()
    batches = _capture_batch(monkeypatch)
    calls = _mini_calls()

    _estimator()._persist_mini_llm_calls_batch(calls)

    envelopes = batches[0]
    assert [env.payload["action"] for env in envelopes] == [
        "estimate_intent",
        "estimate_signals",
        "estimate_operational",
    ]
    for env, call in zip(envelopes, calls, strict=True):
        payload = env.payload
        assert tuple(payload.keys()) == LOCAL_LLM_CALL_PAYLOAD_KEYS
        assert not (GENERIC_ONLY_LLM_CALL_KEYS & set(payload))
        assert payload["phase"] == "risk_estimation"
        assert payload["module"] == "risk_estimator"
        assert payload["model"] == call["llm_model"]
        assert payload["sequence_in_cycle"] == -9
        assert payload["token_usage_json"] == call["token_usage_json"]
        assert payload["system_prompt"] == call["system_prompt"]
        assert payload["prompt"] == call["prompt"]
        assert payload["raw_response"] == call["raw_response"]
        summary = json.loads(payload["parsed_summary_json"])
        assert summary["mini_estimator"] == call["action"]
        assert summary["estimation_mode"] == "parallel"
        assert summary["parse_contract"] == call["parse_contract"]
        assert summary["message_sections"] == call["message_sections"]


def test_calibration_guard_uses_single_route_not_batch(monkeypatch):
    _set_context()
    routed: list[EventEnvelope] = []
    monkeypatch.setattr(risk_estimator_module, "_obs_route", routed.append)
    monkeypatch.setattr(
        risk_estimator_module,
        "_obs_route_batch",
        lambda envelopes: (_ for _ in ()).throw(AssertionError("calibration_guard must not be batched")),
    )

    _estimator()._persist_mini_llm_call(
        system_prompt="[calibration_guard] Automatic recalibration of risk metrics",
        prompt="<synthetic - no LLM call>",
        raw_response='{"guard_applied":true}',
        action="calibration_guard",
        duration_ms=0.0,
        attempts=1,
        sequence_in_cycle=-8,
    )

    assert len(routed) == 1
    payload = routed[0].payload
    assert payload["action"] == "calibration_guard"
    assert payload["sequence_in_cycle"] == -8
    assert payload["duration_ms"] == 0.0
    assert payload["prompt"] == "<synthetic - no LLM call>"


def test_batch_route_failure_is_best_effort(monkeypatch):
    _set_context()
    monkeypatch.setattr(
        risk_estimator_module,
        "_obs_route_batch",
        lambda envelopes: (_ for _ in ()).throw(RuntimeError("write failed")),
    )

    _estimator()._persist_mini_llm_calls_batch(_mini_calls())


def test_batch_sqlite_and_jsonl_readback(tmp_path, monkeypatch):
    db_path = str(tmp_path / "risk-batch.db")
    jsonl_dir = tmp_path / "obs"
    run_id = "run-risk-batch-readback"
    request_id = "req-risk-batch-readback"
    monkeypatch.setenv("MORALSTACK_OBSERVABILITY_DB_PATH", db_path)
    monkeypatch.setenv("MORALSTACK_DB_PATH", db_path)
    monkeypatch.setenv("MORALSTACK_OBSERVABILITY_MODE", "dual")
    monkeypatch.setenv("MORALSTACK_OBSERVABILITY_JSONL_DIR", str(jsonl_dir))
    assert init_db(db_path)
    assert create_run(run_id, run_type="test", meta={})
    assert upsert_request(run_id, request_id, prompt="request", domain="test")
    _set_context(run_id, request_id)

    _estimator()._persist_mini_llm_calls_batch(_mini_calls())

    rows = SqliteReadStore().get_llm_calls_for_request(run_id, request_id)
    assert [row["action"] for row in rows] == [
        "estimate_intent",
        "estimate_signals",
        "estimate_operational",
    ]
    assert [row["model"] for row in rows] == ["gpt-intent", "gpt-signals", "gpt-operational"]
    assert rows[0]["system_prompt"] == "intent system"
    assert rows[0]["raw_response"] == '{"intent":"benign"}'
    assert json.loads(rows[0]["parsed_summary_json"])["message_sections"] == {
        "developer_messages": ["contract"],
        "final_user_message": "request",
    }

    jsonl_path = jsonl_dir / f"{EVENT_LLM_CALL}.jsonl"
    jsonl_rows = [json.loads(line) for line in jsonl_path.read_text(encoding="utf-8").splitlines() if line]
    assert len(jsonl_rows) == 3
    assert [row["payload"]["action"] for row in jsonl_rows] == [
        "estimate_intent",
        "estimate_signals",
        "estimate_operational",
    ]
    assert all(tuple(row["payload"].keys()) == LOCAL_LLM_CALL_PAYLOAD_KEYS for row in jsonl_rows)


def test_batch_all_or_none_on_sqlite_write_failure(tmp_path, monkeypatch):
    db_path = str(tmp_path / "risk-batch-failure.db")
    run_id = "run-risk-batch-failure"
    request_id = "req-risk-batch-failure"
    monkeypatch.setenv("MORALSTACK_OBSERVABILITY_DB_PATH", db_path)
    monkeypatch.setenv("MORALSTACK_DB_PATH", db_path)
    monkeypatch.setenv("MORALSTACK_OBSERVABILITY_MODE", "db_only")
    assert init_db(db_path)
    assert create_run(run_id, run_type="test", meta={})
    assert upsert_request(run_id, request_id, prompt="request", domain="test")
    _set_context(run_id, request_id)
    envelopes = [
        env
        for call in _mini_calls()
        if (env := _estimator()._build_mini_llm_call_envelope(**call)) is not None
    ]
    real_insert = sqlite_sink.insert_llm_calls_batch

    def partial_insert_then_fail(conn, rows):
        real_insert(conn, [rows[0]])
        raise RuntimeError("injected batch failure")

    monkeypatch.setattr(sqlite_sink, "insert_llm_calls_batch", partial_insert_then_fail)

    SqliteEventSink().write_batch(envelopes)

    with sqlite3.connect(db_path) as conn:
        count = conn.execute("SELECT COUNT(*) FROM llm_calls").fetchone()[0]
    assert count == 0
