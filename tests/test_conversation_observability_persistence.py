"""
Step 13 persistence tests for multi-turn conversation observability.

Validates that:
  * the new SQLite schema (conversation_states, ledger_events,
    session_store_events, proxy_request_events) is created on init.
  * the conversation_events emitters generate envelopes that route through
    the SqliteEventSink and produce expected rows.
  * SqliteReadStore exposes the new conversation-scoped read methods and
    aggregates an accurate `get_conversation_overview`.
  * `update_request_meta` correctly merges meta_json on the `requests` row.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from moralstack.observability import obs
from moralstack.observability.conversation_events import (
    emit_conversation_state_updated,
    emit_ledger_lookup,
    emit_ledger_store,
    emit_proxy_request_finalized,
    emit_request_meta_updated,
    emit_session_store_get,
    emit_session_store_put,
)
from moralstack.observability.read_store import SqliteReadStore
from moralstack.observability.sinks.sqlite_sink import (
    _get_connection,
    create_run,
    init_db,
    update_request_meta,
    upsert_request,
)


class _DummyState:
    """Lightweight state object exposing ``to_summary_dict()`` for tests."""

    def __init__(self, **fields: Any) -> None:
        self._fields = fields

    def to_summary_dict(self) -> dict[str, Any]:
        return dict(self._fields)


def _setup_db(tmp_path, monkeypatch):
    dbp = str(tmp_path / "conv_obs.db")
    # Set both the preferred and legacy env vars; some import chains
    # (e.g. moralstack.ui.app) call load_dotenv(override=True) which would
    # otherwise override the legacy-only setting from a project .env.
    monkeypatch.setenv("MORALSTACK_OBSERVABILITY_DB_PATH", dbp)
    monkeypatch.setenv("MORALSTACK_DB_PATH", dbp)
    monkeypatch.setenv("MORALSTACK_OBSERVABILITY_MODE", "db_only")
    init_db(dbp)
    return dbp


def _table_names(dbp: str) -> set[str]:
    conn = _get_connection(dbp)
    try:
        rows = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        return {r["name"] for r in rows}
    finally:
        conn.close()


def test_schema_contains_step13_tables(tmp_path, monkeypatch):
    dbp = _setup_db(tmp_path, monkeypatch)
    tables = _table_names(dbp)
    assert "conversation_states" in tables
    assert "ledger_events" in tables
    assert "session_store_events" in tables
    assert "proxy_request_events" in tables


def test_update_request_meta_merges(tmp_path, monkeypatch):
    _setup_db(tmp_path, monkeypatch)
    create_run("r1", run_type="single", meta={})
    upsert_request("r1", "req-1", prompt="hello", domain="general")
    assert update_request_meta("r1", "req-1", {"final_action": "NORMAL_COMPLETE", "risk_score": 0.1})
    assert update_request_meta("r1", "req-1", {"path": "fast_path", "risk_score": 0.2})

    rs = SqliteReadStore()
    req = rs.get_request("r1", "req-1")
    assert req is not None
    parsed = json.loads(req["meta_json"]) if isinstance(req["meta_json"], str) else req["meta_json"]
    assert parsed["final_action"] == "NORMAL_COMPLETE"
    assert parsed["risk_score"] == pytest.approx(0.2)
    assert parsed["path"] == "fast_path"


def test_request_meta_event_round_trip(tmp_path, monkeypatch):
    _setup_db(tmp_path, monkeypatch)
    create_run("r2", run_type="single", meta={})
    upsert_request("r2", "req-2", prompt="hi", domain="general")
    emit_request_meta_updated(
        run_id="r2",
        request_id="req-2",
        meta={"final_action": "SAFE_COMPLETE", "risk_score": 0.55, "path": "deliberative"},
    )
    obs.flush()
    rs = SqliteReadStore()
    parsed = json.loads(rs.get_request("r2", "req-2")["meta_json"])
    assert parsed["final_action"] == "SAFE_COMPLETE"
    assert parsed["risk_score"] == pytest.approx(0.55)


def test_conversation_state_updated_persists(tmp_path, monkeypatch):
    _setup_db(tmp_path, monkeypatch)
    create_run("r3", run_type="single", meta={})
    upsert_request("r3", "req-3", prompt="?", conversation_id="conv-A", turn_index=0)

    state_out = _DummyState(
        active_overlay="medical",
        last_governance_posture="ESCALATED",
        principle_shortlist=["p1"],
    )
    emit_conversation_state_updated(
        run_id="r3",
        request_id="req-3",
        conversation_id="conv-A",
        turn_index=0,
        state_in=None,
        state_out=state_out,
        final_action="SAFE_COMPLETE",
        risk_score=0.6,
        posture="ESCALATED",
        was_cached=False,
        cached_from_turn=None,
        refresh_required=True,
        refresh_reason="ESCALATED posture",
    )
    obs.flush()

    rs = SqliteReadStore()
    rows = rs.get_conversation_states("conv-A")
    assert len(rows) == 1
    row = rows[0]
    assert row["posture"] == "ESCALATED"
    assert row["final_action"] == "SAFE_COMPLETE"
    assert row["risk_score"] == pytest.approx(0.6)
    assert row["refresh_required"] == 1
    parsed_summary = json.loads(row["state_summary_json"])
    assert parsed_summary["active_overlay"] == "medical"


def test_ledger_lookup_and_store_events(tmp_path, monkeypatch):
    _setup_db(tmp_path, monkeypatch)
    create_run("r4", run_type="single", meta={})
    upsert_request("r4", "req-4a", prompt="?", conversation_id="conv-B", turn_index=0)
    upsert_request("r4", "req-4b", prompt="?", conversation_id="conv-B", turn_index=1)

    emit_ledger_lookup(
        run_id="r4",
        request_id="req-4a",
        conversation_id="conv-B",
        turn_index=0,
        outcome="miss",
        reason="no_prior_turn",
        similarity=None,
        posture="STABLE",
    )
    emit_ledger_store(
        run_id="r4",
        request_id="req-4a",
        conversation_id="conv-B",
        turn_index=0,
        outcome="stored",
        final_action="NORMAL_COMPLETE",
        risk_score=0.05,
        posture="STABLE",
    )
    emit_ledger_lookup(
        run_id="r4",
        request_id="req-4b",
        conversation_id="conv-B",
        turn_index=1,
        outcome="hit",
        similarity=0.93,
        from_turn=0,
        posture="STABLE",
    )
    obs.flush()

    rs = SqliteReadStore()
    events = rs.get_ledger_events_for_conversation("conv-B")
    assert len(events) == 3
    assert {e["operation"] for e in events} == {"lookup", "store"}
    outcomes = {(e["operation"], e["outcome"]) for e in events}
    assert ("lookup", "miss") in outcomes
    assert ("lookup", "hit") in outcomes
    assert ("store", "stored") in outcomes


def test_session_store_events_round_trip(tmp_path, monkeypatch):
    _setup_db(tmp_path, monkeypatch)
    create_run("r5", run_type="single", meta={})
    upsert_request("r5", "req-5", prompt="?", conversation_id="conv-C", turn_index=0)

    emit_session_store_get(
        run_id="r5",
        request_id="req-5",
        conversation_id="conv-C",
        turn_index=0,
        outcome="miss",
    )
    emit_session_store_put(
        run_id="r5",
        request_id="req-5",
        conversation_id="conv-C",
        turn_index=0,
        outcome="stored",
        state=_DummyState(last_governance_posture="STABLE"),
        evicted_ids=["conv-old-1", "conv-old-2"],
    )
    obs.flush()

    rs = SqliteReadStore()
    rows = rs.get_session_store_events_for_conversation("conv-C")
    assert len(rows) == 2
    operations = {r["operation"] for r in rows}
    assert operations == {"get", "put"}
    put_row = next(r for r in rows if r["operation"] == "put")
    payload = json.loads(put_row["payload_json"])
    assert payload["evicted_ids"] == ["conv-old-1", "conv-old-2"]


def test_proxy_request_finalized_round_trip(tmp_path, monkeypatch):
    _setup_db(tmp_path, monkeypatch)
    create_run("r6", run_type="single", meta={})
    upsert_request("r6", "req-6", prompt="?", conversation_id="conv-D", turn_index=0)

    emit_proxy_request_finalized(
        run_id="r6",
        request_id="req-6",
        conversation_id="conv-D",
        turn_index=0,
        final_action="NORMAL_COMPLETE",
        risk_score=0.12,
        path="fast_path",
        domain="general",
        posture_in=None,
        posture_out="STABLE",
        state_provided=False,
        state_updated=True,
        was_cached=False,
        cached_from_turn=None,
        final_response_length=128,
        headers={"X-MoralStack-Final-Action": "NORMAL_COMPLETE"},
        metadata={"final_action": "NORMAL_COMPLETE", "risk_score": 0.12},
    )
    obs.flush()

    rs = SqliteReadStore()
    rows = rs.get_proxy_request_events_for_conversation("conv-D")
    assert len(rows) == 1
    row = rows[0]
    assert row["final_action"] == "NORMAL_COMPLETE"
    assert row["state_updated"] == 1
    assert row["was_cached"] == 0
    headers = json.loads(row["headers_json"])
    assert headers["X-MoralStack-Final-Action"] == "NORMAL_COMPLETE"


def test_conversation_overview_and_ids_for_run(tmp_path, monkeypatch):
    _setup_db(tmp_path, monkeypatch)
    create_run("r7", run_type="single", meta={})

    upsert_request("r7", "req-7a", prompt="q1", conversation_id="conv-E", turn_index=0)
    upsert_request("r7", "req-7b", prompt="q2", conversation_id="conv-E", turn_index=1)
    update_request_meta("r7", "req-7a", {"final_action": "NORMAL_COMPLETE", "risk_score": 0.05})
    update_request_meta(
        "r7",
        "req-7b",
        {"final_action": "SAFE_COMPLETE", "risk_score": 0.62, "governance_posture": "ESCALATED", "was_cached": False},
    )

    emit_conversation_state_updated(
        run_id="r7",
        request_id="req-7b",
        conversation_id="conv-E",
        turn_index=1,
        state_in=None,
        state_out=_DummyState(last_governance_posture="ESCALATED"),
        final_action="SAFE_COMPLETE",
        risk_score=0.62,
        posture="ESCALATED",
        was_cached=False,
        cached_from_turn=None,
        refresh_required=False,
        refresh_reason=None,
    )
    emit_ledger_lookup(
        run_id="r7",
        request_id="req-7a",
        conversation_id="conv-E",
        turn_index=0,
        outcome="miss",
    )
    emit_ledger_lookup(
        run_id="r7",
        request_id="req-7b",
        conversation_id="conv-E",
        turn_index=1,
        outcome="hit",
        similarity=0.99,
        from_turn=0,
    )
    emit_session_store_get(
        run_id="r7",
        request_id="req-7b",
        conversation_id="conv-E",
        turn_index=1,
        outcome="hit",
    )
    obs.flush()

    rs = SqliteReadStore()
    ids = rs.get_conversation_ids_for_run("r7")
    assert any(c.get("conversation_id") == "conv-E" for c in ids)
    overview = rs.get_conversation_overview("conv-E")
    assert overview["turn_count"] == 2
    assert overview["ledger_hits"] == 1
    assert overview["ledger_misses"] == 1
    assert overview["session_store_hits"] == 1
    assert overview["max_risk_score"] == pytest.approx(0.62)
    assert overview["last_posture"] == "ESCALATED"


def test_read_methods_return_empty_when_tables_missing(tmp_path, monkeypatch):
    """A bare DB without Step 13 tables should return empty results gracefully."""
    dbp = str(tmp_path / "bare.db")
    monkeypatch.setenv("MORALSTACK_OBSERVABILITY_DB_PATH", dbp)
    monkeypatch.setenv("MORALSTACK_DB_PATH", dbp)
    monkeypatch.setenv("MORALSTACK_OBSERVABILITY_MODE", "db_only")
    conn = _get_connection(dbp)
    conn.execute("CREATE TABLE runs (run_id TEXT PRIMARY KEY)")
    conn.execute("CREATE TABLE requests (run_id TEXT, request_id TEXT)")
    conn.commit()
    conn.close()

    rs = SqliteReadStore()
    # Reads against an older DB schema (missing Step 13 columns/tables) should
    # never raise — at worst they return an empty list / empty dict.
    assert rs.get_conversation_states("anything") == []
    assert rs.get_ledger_events_for_conversation("anything") == []
    assert rs.get_session_store_events_for_conversation("anything") == []
    assert rs.get_proxy_request_events_for_conversation("anything") == []
    assert rs.get_conversation_ids_for_run("anything") == []
    overview = rs.get_conversation_overview("anything")
    assert isinstance(overview, dict)
    if overview:
        assert overview.get("turn_count") in (0, None)


# ---------------------------------------------------------------------------
# Step 14.1 — SDK emission of proxy.request_finalized round-trip
# ---------------------------------------------------------------------------


def test_sdk_emits_proxy_request_finalized_into_readstore(tmp_path, monkeypatch):
    """
    End-to-end: a GovernedClient.create() must populate the
    ``proxy_request_events`` table just like the HTTP proxy does, so that
    SDK-driven runs share the same audit surface consumed by ``moralstack-ui``.

    The test uses a real SQLite path (via ``_setup_db``) and a mock orchestrator
    + mock OpenAI client; observability runs end-to-end through the router,
    sinks, write queue, and read store.
    """
    from unittest.mock import MagicMock

    from moralstack.sdk.config import GovernanceConfig
    from moralstack.sdk.wrapper import GovernedClient

    _setup_db(tmp_path, monkeypatch)

    # Build the mock OpenAI client (chat.completions.create() returns a stub).
    mock_openai = MagicMock()
    mock_openai.chat.completions.create.return_value = MagicMock(
        choices=[MagicMock(message=MagicMock(content="hello world"))],
        model="gpt-4o",
    )

    # Build the mock orchestrator with a controlled OrchestratorResult.
    orchestrator = MagicMock()
    result = MagicMock()
    result.response.content = "hello world"
    result.response.metadata.final_action = "NORMAL_COMPLETE"
    result.response.metadata.risk_score = 0.10
    result.response.metadata.path = "FAST_PATH"
    result.response.metadata.domain_overlay = None
    result.response.metadata.reason_codes = []
    result.response.metadata.triggered_principles = []
    # finalize_governance_audit reads several metadata accessors and tries
    # ``.to_dict()`` on decision_explanation; using None avoids a MagicMock
    # round-trip that produces unstable output.
    result.response.metadata.decision_explanation = None
    result.response.metadata.winning_decision_reason = "ok"
    result.response.metadata.decision_reason = "ok"
    result.response.metadata.winning_rule = "low_risk"
    result.response.metadata.governance_posture = "NORMAL"
    result.conversation_id = "conv-sdk-finalized-1"
    result.turn_index = 0
    result.parent_request_id = None
    result.conversation_state_provided = False
    result.conversation_state_updated = False
    result.conversation_governance_state_out = None
    result.path_taken = "FAST_PATH"
    # Default MagicMock children are non-None and would leak into meta / SQLite binds.
    result.was_cached = False
    result.ledger_hit_applied = None
    result.cached_from_turn = None
    result.ledger_from_turn = None

    # Capture the auto-generated request_id from the SDK so we can pre-insert
    # the ``requests`` row that satisfies the FK on proxy_request_events. The
    # SDK calls orchestrator.process(request, conversation_id=..., ...) with a
    # ProcessedRequest whose request_id is a fresh UUID4; the side_effect lets
    # us read it and pre-insert before the event is emitted.
    captured: dict[str, Any] = {}

    def _process_side_effect(request, **_kwargs):
        captured["request_id"] = request.request_id
        upsert_request(
            run_id=client._run_id,
            request_id=request.request_id,
            prompt=request.prompt,
            conversation_id="conv-sdk-finalized-1",
            turn_index=0,
        )
        return result

    orchestrator.process.side_effect = _process_side_effect

    client = GovernedClient(mock_openai, orchestrator, GovernanceConfig())
    # Override the auto-generated UUID with the deterministic conversation id
    # we assert below.
    client._session._conversation_id = "conv-sdk-finalized-1"

    client.chat.completions.create(
        model="gpt-4o",
        messages=[{"role": "user", "content": "Hi"}],
    )
    obs.flush(timeout=10.0)

    assert "request_id" in captured

    rs = SqliteReadStore()
    rows = rs.get_proxy_request_events_for_conversation("conv-sdk-finalized-1")
    assert len(rows) == 1
    row = rows[0]
    assert row["request_id"] == captured["request_id"]
    assert row["final_action"] == "NORMAL_COMPLETE"
    assert row["risk_score"] == pytest.approx(0.10)
    # SQLite stores booleans as 0/1.
    assert row["state_provided"] == 0  # First turn: no incoming state.
    assert row["state_updated"] == 0  # Mock left conversation_state_updated=False.
    assert row["final_response_length"] == len("hello world")
    # The SDK does not produce X-MoralStack-* headers.
    assert row["headers_json"] is None
