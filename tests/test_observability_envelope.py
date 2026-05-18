"""Tests for EventEnvelope dataclass and make_envelope factory."""

from __future__ import annotations

import json
import time

from moralstack.observability.events import (
    ALL_EVENT_TYPES,
    EVENT_DEBUG_EVENT,
    EVENT_DECISION_TRACE,
    EVENT_LLM_CALL,
    EVENT_ORCHESTRATION_EVENT,
    EVENT_REQUEST_UPSERTED,
    EVENT_RUN_ENDED,
    EVENT_RUN_STARTED,
    make_envelope,
)


def test_make_envelope_sets_uuid_and_timestamp():
    env = make_envelope(EVENT_LLM_CALL, run_id="r1", request_id="q1")
    assert len(env.event_id) == 36  # UUID4 format
    assert env.timestamp_ms > 0
    assert env.timestamp_ms <= int(time.time() * 1000) + 100


def test_make_envelope_event_type():
    env = make_envelope(EVENT_RUN_STARTED, run_id="r1")
    assert env.event_type == EVENT_RUN_STARTED


def test_make_envelope_payload_defaults_empty():
    env = make_envelope(EVENT_RUN_ENDED, run_id="r1")
    assert isinstance(env.payload, dict)
    assert env.payload == {}


def test_make_envelope_payload_set():
    env = make_envelope(EVENT_LLM_CALL, run_id="r1", request_id="q1", payload={"module": "risk", "phase": "test"})
    assert env.payload["module"] == "risk"
    assert env.payload["phase"] == "test"


def test_make_envelope_cycle():
    env = make_envelope(EVENT_LLM_CALL, run_id="r1", request_id="q1", cycle=3)
    assert env.cycle == 3


def test_make_envelope_multi_turn_fields():
    env = make_envelope(
        EVENT_REQUEST_UPSERTED,
        run_id="r1",
        request_id="q1",
        session_id="sess-1",
        turn_number=2,
        parent_event_id="parent-id",
    )
    assert env.session_id == "sess-1"
    assert env.turn_number == 2
    assert env.parent_event_id == "parent-id"


def test_envelope_is_frozen():
    env = make_envelope(EVENT_LLM_CALL, run_id="r1")
    try:
        env.event_type = "modified"  # type: ignore[misc]
        assert False, "Should have raised FrozenInstanceError"
    except Exception:
        pass  # expected: frozen dataclass


def test_envelope_to_dict_roundtrip():
    env = make_envelope(
        EVENT_DECISION_TRACE,
        run_id="r1",
        request_id="q1",
        cycle=1,
        payload={"stage": "FINAL", "trace_json": '{"key": 1}'},
    )
    d = env.to_dict()
    assert d["event_type"] == EVENT_DECISION_TRACE
    assert d["run_id"] == "r1"
    assert d["request_id"] == "q1"
    assert d["cycle"] == 1
    assert d["payload"]["stage"] == "FINAL"


def test_envelope_json_serializable():
    env = make_envelope(EVENT_ORCHESTRATION_EVENT, run_id="r1", request_id="q1", payload={"x": [1, 2, None]})
    d = env.to_dict()
    serialized = json.dumps(d)
    parsed = json.loads(serialized)
    assert parsed["event_type"] == EVENT_ORCHESTRATION_EVENT
    assert parsed["payload"]["x"] == [1, 2, None]


def test_envelope_unique_ids():
    ids = {make_envelope(EVENT_DEBUG_EVENT, run_id="r1").event_id for _ in range(100)}
    assert len(ids) == 100


def test_all_event_types_is_frozenset():
    assert isinstance(ALL_EVENT_TYPES, frozenset)
    assert EVENT_LLM_CALL in ALL_EVENT_TYPES
    assert EVENT_DECISION_TRACE in ALL_EVENT_TYPES
    assert EVENT_RUN_STARTED in ALL_EVENT_TYPES
    # 10 legacy + 6 Step 13 multi-turn observability event types
    assert len(ALL_EVENT_TYPES) == 16


def test_envelope_audit_level_default():
    env = make_envelope(EVENT_LLM_CALL, run_id="r1")
    assert env.audit_level == "turn"
