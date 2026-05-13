"""
UI tests for Step 13 conversation timeline views.

Exercises:
  * ``_build_conversation_timeline`` helper against seeded SQLite data.
  * The ``/conversations/{id}`` HTML route (renders template).
  * The ``/conversations/{id}/export.md`` markdown export route.
  * The ``/conversations?q=...`` redirect helper.
  * The Conversations section in the ``/runs/{run_id}`` view.

All tests run against a temporary SQLite DB with the standard fixtures.
"""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402

from moralstack.observability import obs  # noqa: E402
from moralstack.observability.conversation_events import (  # noqa: E402
    emit_conversation_state_updated,
    emit_ledger_lookup,
    emit_proxy_request_finalized,
)
from moralstack.observability.sinks.sqlite_sink import (  # noqa: E402
    create_run,
    init_db,
    update_request_meta,
    upsert_request,
)


def _make_session_token(client: TestClient) -> str:
    """Authenticate to the UI dashboard via the form-based login."""
    import os

    os.environ["MORALSTACK_UI_USERNAME"] = "admin"
    os.environ["MORALSTACK_UI_PASSWORD"] = "test"
    from moralstack.ui import app as app_module

    # Refresh credentials cache for the running module.
    app_module._UI_USERNAME = "admin"
    app_module._UI_PASSWORD = "test"
    resp = client.post(
        "/login",
        data={"username": "admin", "password": "test"},
        follow_redirects=False,
    )
    assert resp.status_code in (200, 303), resp.text
    cookie = resp.cookies.get("moralstack_session")
    assert cookie, "expected session cookie after login"
    return cookie


def _seed_two_turn_conversation(run_id: str = "run-ui-1", conv_id: str = "conv-ui-1") -> None:
    """Populate a minimal two-turn conversation."""
    create_run(run_id, run_type="single", meta={})
    upsert_request(
        run_id,
        "req-ui-a",
        prompt="first turn question",
        domain="general",
        conversation_id=conv_id,
        turn_index=0,
    )
    upsert_request(
        run_id,
        "req-ui-b",
        prompt="follow-up question",
        domain="general",
        conversation_id=conv_id,
        turn_index=1,
        parent_request_id="req-ui-a",
    )
    update_request_meta(
        run_id,
        "req-ui-a",
        {"final_action": "NORMAL_COMPLETE", "risk_score": 0.05, "governance_posture": "STABLE"},
    )
    update_request_meta(
        run_id,
        "req-ui-b",
        {
            "final_action": "SAFE_COMPLETE",
            "risk_score": 0.62,
            "governance_posture": "ESCALATED",
            "was_cached": True,
            "cached_from_turn": 0,
        },
    )
    emit_conversation_state_updated(
        run_id=run_id,
        request_id="req-ui-b",
        conversation_id=conv_id,
        turn_index=1,
        state_in=None,
        state_out=None,
        final_action="SAFE_COMPLETE",
        risk_score=0.62,
        posture="ESCALATED",
        was_cached=True,
        cached_from_turn=0,
        refresh_required=False,
    )
    emit_ledger_lookup(
        run_id=run_id,
        request_id="req-ui-b",
        conversation_id=conv_id,
        turn_index=1,
        outcome="hit",
        similarity=0.91,
        from_turn=0,
    )
    emit_proxy_request_finalized(
        run_id=run_id,
        request_id="req-ui-b",
        conversation_id=conv_id,
        turn_index=1,
        final_action="SAFE_COMPLETE",
        risk_score=0.62,
        path="cached",
        domain="general",
        posture_in="STABLE",
        posture_out="ESCALATED",
        state_provided=True,
        state_updated=True,
        was_cached=True,
        cached_from_turn=0,
        final_response_length=210,
        headers={"X-MoralStack-Final-Action": "SAFE_COMPLETE"},
    )
    obs.flush()


@pytest.fixture()
def ui_client(tmp_path, monkeypatch):
    # Import first; `moralstack.ui.app` calls load_dotenv(override=True) which
    # would otherwise wipe our test env vars set before import.
    from moralstack.ui.app import create_app

    dbp = str(tmp_path / "ui_obs.db")
    monkeypatch.setenv("MORALSTACK_OBSERVABILITY_DB_PATH", dbp)
    monkeypatch.setenv("MORALSTACK_DB_PATH", dbp)
    monkeypatch.setenv("MORALSTACK_OBSERVABILITY_MODE", "db_only")
    init_db(dbp)

    app = create_app()
    client = TestClient(app, follow_redirects=False)
    return client


def test_build_conversation_timeline_returns_structured_data(tmp_path, monkeypatch):
    """The helper aggregates requests + state + ledger + proxy snapshots."""
    # `moralstack.ui.app` calls load_dotenv(override=True) at import time, which
    # can override test env vars. Import first, then set env vars to win.
    from moralstack.ui.app import _build_conversation_timeline

    dbp = str(tmp_path / "obs.db")
    monkeypatch.setenv("MORALSTACK_OBSERVABILITY_DB_PATH", dbp)
    monkeypatch.setenv("MORALSTACK_DB_PATH", dbp)
    monkeypatch.setenv("MORALSTACK_OBSERVABILITY_MODE", "db_only")
    init_db(dbp)
    _seed_two_turn_conversation()

    timeline = _build_conversation_timeline("conv-ui-1")
    assert timeline["conversation_id"] == "conv-ui-1"
    assert timeline["run_id"] == "run-ui-1"
    assert len(timeline["requests"]) == 2

    # Per-request enrichment is present.
    turn_a = next(r for r in timeline["requests"] if r["turn_index"] == 0)
    turn_b = next(r for r in timeline["requests"] if r["turn_index"] == 1)
    assert turn_a["meta_json__parsed"]["final_action"] == "NORMAL_COMPLETE"
    assert turn_b["meta_json__parsed"]["was_cached"] is True
    assert turn_b["state"] is not None
    assert turn_b["state"]["posture"] == "ESCALATED"
    assert turn_b["proxy_event"] is not None
    assert turn_b["proxy_event"]["was_cached"] == 1
    assert any(ev["operation"] == "lookup" for ev in turn_b["ledger_events"])


def test_conversation_route_renders_template(ui_client):
    """GET /conversations/{id} produces a valid HTML page (after login)."""
    _seed_two_turn_conversation()
    token = _make_session_token(ui_client)
    resp = ui_client.get(
        "/conversations/conv-ui-1",
        cookies={"moralstack_session": token},
    )
    assert resp.status_code == 200, resp.text
    body = resp.text
    assert "Conversation" in body
    assert "conv-ui-1" in body
    assert "SAFE_COMPLETE" in body
    assert "ESCALATED" in body


def test_conversation_export_route_returns_markdown(ui_client):
    """GET /conversations/{id}/export.md returns a non-empty markdown body."""
    _seed_two_turn_conversation()
    token = _make_session_token(ui_client)
    resp = ui_client.get(
        "/conversations/conv-ui-1/export.md",
        cookies={"moralstack_session": token},
    )
    assert resp.status_code == 200, resp.text
    assert resp.headers["content-type"].startswith("text/markdown")
    body = resp.text
    assert "# Conversation Audit Export" in body
    assert "conv-ui-1" in body
    assert "follow-up question" in body
    assert "SAFE_COMPLETE" in body


def test_conversations_search_redirects_to_detail(ui_client):
    """GET /conversations?q=conv-id issues a 303 redirect to the timeline page."""
    token = _make_session_token(ui_client)
    resp = ui_client.get(
        "/conversations?q=conv-xyz",
        cookies={"moralstack_session": token},
    )
    assert resp.status_code == 303
    assert resp.headers["location"] == "/conversations/conv-xyz"


def test_run_detail_lists_conversations(ui_client):
    """Run detail page surfaces a Conversations section when present."""
    _seed_two_turn_conversation()
    token = _make_session_token(ui_client)
    resp = ui_client.get(
        "/runs/run-ui-1",
        cookies={"moralstack_session": token},
    )
    assert resp.status_code == 200, resp.text
    body = resp.text
    assert "Conversations" in body
    assert "conv-ui-1" in body
    assert "/conversations/conv-ui-1" in body


def test_conversation_route_returns_404_for_missing(ui_client):
    """Missing conversation_ids yield a 404."""
    token = _make_session_token(ui_client)
    resp = ui_client.get(
        "/conversations/does-not-exist",
        cookies={"moralstack_session": token},
    )
    assert resp.status_code == 404
