"""Conversation-ready schema and ConversationGovernanceState (Plan 10); single-turn unchanged by default."""

from __future__ import annotations

from moralstack.orchestration.conversation_state import ConversationGovernanceState
from moralstack.orchestration.types import ProcessedRequest
from moralstack.persistence import db as db_module
from moralstack.persistence.db import create_run, get_request, init_db, upsert_request
from moralstack.runtime.orchestrator import create_orchestrator
from tests.test_orchestrator import MockPolicyLLM, MockRiskEstimator


def test_conversation_governance_state_minimal_and_helpers():
    s = ConversationGovernanceState(conversation_id="c1", turn_index=0)
    assert s.should_full_refresh() is True
    s2 = s.with_last_request_id("req-a")
    assert s2.last_request_id == "req-a"
    d = s2.to_summary_dict()
    assert d["conversation_id"] == "c1"
    assert d["turn_index"] == 0
    s3 = s2.update_from_processing_result(request_id="req-b", domain="healthcare")
    assert s3.last_request_id == "req-b"
    assert s3.active_domain == "healthcare"


def test_upsert_request_with_conversation_fields(tmp_path, monkeypatch):
    dbp = str(tmp_path / "tc.db")
    monkeypatch.setenv("MORALSTACK_DB_PATH", dbp)
    monkeypatch.setenv("MORALSTACK_PERSIST_MODE", "db_only")
    assert init_db(dbp)
    assert create_run("run-x", run_type="test", meta={})
    assert upsert_request(
        "run-x",
        "req-1",
        prompt="hi",
        domain="",
        conversation_id="conv-1",
        turn_index=1,
        parent_request_id="req-0",
    )
    row = get_request("run-x", "req-1")
    assert row is not None
    assert row.get("conversation_id") == "conv-1"
    assert row.get("turn_index") == 1
    assert row.get("parent_request_id") == "req-0"
    conn = db_module._get_connection(dbp)
    idx = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='index' AND name='idx_requests_conversation_id'"
    ).fetchone()
    conn.close()
    assert idx is not None


def test_upsert_request_without_conversation_fields(tmp_path, monkeypatch):
    dbp = str(tmp_path / "tc2.db")
    monkeypatch.setenv("MORALSTACK_DB_PATH", dbp)
    monkeypatch.setenv("MORALSTACK_PERSIST_MODE", "db_only")
    assert init_db(dbp)
    assert create_run("run-y", run_type="test", meta={})
    assert upsert_request("run-y", "req-2", prompt="x", domain="")
    row = get_request("run-y", "req-2")
    assert row is not None
    assert row.get("conversation_id") in (None, "")
    assert row.get("parent_request_id") in (None, "")


def test_process_single_turn_unchanged():
    orch = create_orchestrator(
        policy=MockPolicyLLM(response="ok."),
        risk_estimator=MockRiskEstimator(default_score=0.1),
    )
    r = orch.process("Hello")
    assert r.response is not None
    assert r.conversation_id is None
    assert r.conversation_state_provided is False
    assert r.conversation_governance_state_out is None


def test_process_with_conversation_metadata_carried():
    orch = create_orchestrator(
        policy=MockPolicyLLM(response="ok."),
        risk_estimator=MockRiskEstimator(default_score=0.1),
    )
    st = ConversationGovernanceState(conversation_id="c99")
    r = orch.process(
        ProcessedRequest(prompt="Hello"),
        conversation_id="c99",
        turn_index=2,
        parent_request_id="parent-1",
        conversation_state=st,
    )
    assert r.conversation_id == "c99"
    assert r.turn_index == 2
    assert r.parent_request_id == "parent-1"
    assert r.conversation_state_provided is True
    assert r.conversation_governance_state_out is not None
    assert r.conversation_state_updated is True
