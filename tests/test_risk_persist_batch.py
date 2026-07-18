"""Tests for risk-estimator mini-call async windowed persistence."""

from __future__ import annotations

import json
import sqlite3
from unittest.mock import MagicMock

import pytest

import moralstack.models.risk.estimator as risk_estimator_module
from moralstack.models.risk.estimator import LLMBasedRiskEstimator
from moralstack.models.risk.schema import RiskEstimatorConfig
from moralstack.observability import obs, router
from moralstack.observability import service as service_module
from moralstack.observability.context import (
    set_current_cycle,
    set_current_request_id,
    set_current_run_id,
    set_current_session_id,
    set_current_turn_number,
)
from moralstack.observability.events import EVENT_LLM_CALL, EventEnvelope
from moralstack.observability.read_store import SqliteReadStore
from moralstack.observability.service import get_obs
from moralstack.observability.sinks.sqlite_sink import SqliteEventSink, _get_connection, create_run, init_db, upsert_request

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
    "billable_provider_call",
)

GENERIC_ONLY_LLM_CALL_KEYS = {"call_kind", "call_outcome", "cache_status", "related_event_id"}


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


def test_risk_mini_calls_use_emit_batch_not_router_route_batch(monkeypatch):
    """PROJECT_SPEC §7: P2 intentionally removes the old sync request-thread route_batch contract."""
    _set_context()
    batches = _capture_batch(monkeypatch)

    def fail_route_batch(*_args, **_kwargs):
        raise AssertionError("risk mini-call persistence must not call router.route_batch on the request thread")

    monkeypatch.setattr("moralstack.observability.router.route_batch", fail_route_batch)

    _estimator()._persist_mini_llm_calls_batch(_mini_calls())

    assert len(batches) == 1
    assert len(batches[0]) == 3


def test_three_mini_envelopes_have_local_16_key_payload(monkeypatch):
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
        # Real mini-estimators leave the billable flag unset (None → NULL → counted
        # as billable by COALESCE downstream); only synthetic rows force it False.
        assert payload["billable_provider_call"] is None
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


def test_calibration_guard_uses_single_emit_not_batch(monkeypatch):
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


def test_calibration_guard_row_is_marked_non_billable(monkeypatch):
    """The synthetic guard row must not be counted as a real provider call.

    It carries no ``usage`` object, so if it stayed billable it would surface
    as a spurious "missing" token row in the UI. It is persisted for audit but
    excluded from token/cost aggregation via ``billable_provider_call=False``.
    """
    _set_context()
    routed: list[EventEnvelope] = []
    monkeypatch.setattr(risk_estimator_module, "_obs_route", routed.append)

    _estimator()._persist_mini_llm_call(
        system_prompt="[calibration_guard] Automatic recalibration of risk metrics",
        prompt="<synthetic - no LLM call>",
        raw_response='{"guard_applied":true}',
        action="calibration_guard",
        duration_ms=0.0,
        attempts=1,
        sequence_in_cycle=-8,
        billable_provider_call=False,
    )

    assert len(routed) == 1
    assert routed[0].payload["billable_provider_call"] is False


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
    obs.flush(timeout=10.0)

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


def test_write_window_isolates_bad_risk_envelope(tmp_path, monkeypatch):
    db_path = str(tmp_path / "risk-batch-window-isolation.db")
    run_id = "run-risk-batch-window-isolation"
    request_id = "req-risk-batch-window-isolation"
    monkeypatch.setenv("MORALSTACK_OBSERVABILITY_DB_PATH", db_path)
    monkeypatch.setenv("MORALSTACK_DB_PATH", db_path)
    monkeypatch.setenv("MORALSTACK_OBSERVABILITY_MODE", "db_only")
    assert init_db(db_path)
    assert create_run(run_id, run_type="test", meta={})
    assert upsert_request(run_id, request_id, prompt="request", domain="test")
    _set_context(run_id, request_id)
    envelopes = [env for call in _mini_calls() if (env := _estimator()._build_mini_llm_call_envelope(**call)) is not None]
    bad = _estimator()._build_mini_llm_call_envelope(**_mini_calls()[0])
    assert bad is not None
    bad = EventEnvelope(
        event_id=bad.event_id,
        event_type=bad.event_type,
        timestamp_ms=bad.timestamp_ms,
        run_id=bad.run_id,
        request_id=None,
        cycle=bad.cycle,
        session_id=bad.session_id,
        turn_number=bad.turn_number,
        parent_event_id=bad.parent_event_id,
        audit_level=bad.audit_level,
        payload=bad.payload,
    )
    conn = _get_connection(db_path)
    try:
        result = SqliteEventSink().write_window([envelopes[0], bad, envelopes[1]], conn)
    finally:
        conn.close()

    with sqlite3.connect(db_path) as conn:
        count = conn.execute("SELECT COUNT(*) FROM llm_calls").fetchone()[0]
    assert count == 2
    assert result.written == 2
    assert result.failed == 1
