"""
Tests for Step 14.6: Conversation strip rendering in the conversation.html
template.

Approach: drive the UI test client, render a conversation page with known
fixture data, and assert the HTML contains the expected strip markup.
"""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402

from moralstack.observability.conversation_events import emit_conversation_state_updated  # noqa: E402
from moralstack.observability.service import get_obs  # noqa: E402
from moralstack.observability.sinks.sqlite_sink import (  # noqa: E402
    create_run,
    init_db,
    update_request_meta,
    upsert_request,
)
from tests.test_ui_conversation_views import (  # noqa: E402
    _bind_observability_db,
    _make_session_token,
    _reinstall_observability_service_writes,
    _reset_observability_singleton,
)


@pytest.fixture(autouse=True)
def _isolate_observability() -> None:
    _reinstall_observability_service_writes()
    _reset_observability_singleton()


def _seed_three_turn_conversation(
    conv_id: str = "conv-strip-1",
    run_id: str = "run-strip-1",
) -> None:
    """Insert a 3-turn conversation with diverse outcomes."""
    create_run(run_id, run_type="single", meta={})
    turns = [
        ("req1", 0, "NORMAL_COMPLETE", 0.10, "NORMAL", False, None),
        ("req2", 1, "SAFE_COMPLETE", 0.55, "ELEVATED", True, 0),
        ("req3", 2, "REFUSE", 0.92, "ESCALATED", False, None),
    ]
    for req_id, turn_idx, action, risk, posture, cached, cached_from in turns:
        upsert_request(
            run_id,
            req_id,
            prompt=f"prompt {turn_idx}",
            domain="legal",
            conversation_id=conv_id,
            turn_index=turn_idx,
        )
        update_request_meta(
            run_id,
            req_id,
            {
                "final_action": action,
                "risk_score": risk,
                "governance_posture": posture,
                "was_cached": cached,
                "cached_from_turn": cached_from,
                "domain_overlay": "legal",
            },
        )
        if cached or posture != "NORMAL":
            emit_conversation_state_updated(
                run_id=run_id,
                request_id=req_id,
                conversation_id=conv_id,
                turn_index=turn_idx,
                state_in=None,
                state_out=None,
                final_action=action,
                risk_score=risk,
                posture=posture,
                was_cached=cached,
                cached_from_turn=cached_from,
                refresh_required=False,
            )
    get_obs().flush()


@pytest.fixture()
def ui_client(tmp_path, monkeypatch):
    dbp = str(tmp_path / "ui_strip.db")
    _bind_observability_db(monkeypatch, dbp)
    init_db(dbp)

    from moralstack.ui.app import create_app

    return TestClient(create_app(), follow_redirects=False)


class TestConversationStripRendering:
    def test_strip_section_present(self, ui_client):
        _seed_three_turn_conversation("conv-strip-1")
        token = _make_session_token(ui_client)
        resp = ui_client.get(
            "/conversations/conv-strip-1",
            cookies={"moralstack_session": token},
        )
        assert resp.status_code == 200, resp.text
        assert "Conversation strip" in resp.text
        assert 'class="conv-strip"' in resp.text

    def test_strip_renders_one_cell_per_turn(self, ui_client):
        _seed_three_turn_conversation("conv-strip-2")
        token = _make_session_token(ui_client)
        resp = ui_client.get(
            "/conversations/conv-strip-2",
            cookies={"moralstack_session": token},
        )
        cell_count = resp.text.count('data-turn="')
        assert cell_count == 3, f"Expected 3 strip cells, got {cell_count}"

    def test_strip_cell_action_class(self, ui_client):
        _seed_three_turn_conversation("conv-strip-3")
        token = _make_session_token(ui_client)
        resp = ui_client.get(
            "/conversations/conv-strip-3",
            cookies={"moralstack_session": token},
        )
        assert "conv-strip-cell-normal_complete" in resp.text
        assert "conv-strip-cell-safe_complete" in resp.text
        assert "conv-strip-cell-refuse" in resp.text

    def test_strip_cached_icon_and_arrow(self, ui_client):
        _seed_three_turn_conversation("conv-strip-4")
        token = _make_session_token(ui_client)
        resp = ui_client.get(
            "/conversations/conv-strip-4",
            cookies={"moralstack_session": token},
        )
        assert "⚡" in resp.text
        assert 'data-from-turn="0"' in resp.text
        assert 'data-to-turn="1"' in resp.text

    def test_strip_escalated_border_class(self, ui_client):
        _seed_three_turn_conversation("conv-strip-5")
        token = _make_session_token(ui_client)
        resp = ui_client.get(
            "/conversations/conv-strip-5",
            cookies={"moralstack_session": token},
        )
        assert "conv-strip-cell-escalated" in resp.text
