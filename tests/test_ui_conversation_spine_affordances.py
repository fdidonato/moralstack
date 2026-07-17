"""
Parity checklist for the conversation-strip -> conversation-spine
replacement (rewrite of the retired ``test_ui_conversation_strip.py``).

Maps each old strip-markup assertion to its spine equivalent:

  * cells                        -> nodes (one ``.conv-spine-slot--input`` per turn)
  * ``conv-strip-cell-refuse``   -> the node's REFUSE ``action_badge`` class
  * ``data-from-turn``           -> the "reused decision from turn N" connector label
  * ``conv-strip-cell-escalated``-> the outcome badge's ``conv-spine-posture-escalated`` class

Per the plan's B6 decision (user-confirmed, not relitigated): the strip's
height-encoded risk affordance is a deliberate *substitution*, not a 1:1
carryover — the spine renders an exact risk value plus a proportional bar
instead. ``test_spine_node_encodes_risk_magnitude`` is the one assertion the
old strip test suite never had (the height encoding was pinned by no test),
added here per the Codex review finding so the parity rewrite cannot silently
lose that affordance a second time.
"""

from __future__ import annotations

import json

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402

from moralstack.observability.conversation_events import emit_conversation_state_updated  # noqa: E402
from moralstack.observability.emit_helpers import persist_decision_trace  # noqa: E402
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
    conv_id: str = "conv-spine-aff-1",
    run_id: str = "run-spine-aff-1",
) -> None:
    """Insert a 3-turn conversation with diverse outcomes (same shape as the
    retired strip test's seed helper), plus a FINAL decision trace per turn
    so the spine's decision slot (risk value + bar) has evidence to render."""
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
        persist_decision_trace(
            run_id=run_id,
            request_id=req_id,
            stage="FINAL",
            sequence=1,
            trace_json=json.dumps(
                {
                    "final_action": action,
                    "path": "DELIBERATIVE_PATH",
                    "risk_score": risk,
                    "winning_rule": "rule_x",
                    f"why_not_{action.lower()}": f"{action} chosen: reason.",
                }
            ),
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
    dbp = str(tmp_path / "ui_spine_affordances.db")
    _bind_observability_db(monkeypatch, dbp)
    init_db(dbp)

    from moralstack.ui.app import create_app

    return TestClient(create_app(), follow_redirects=False)


class TestConversationSpineAffordanceParity:
    def test_spine_section_present(self, ui_client):
        """Was: 'Conversation strip' heading + '.conv-strip' class present."""
        _seed_three_turn_conversation("conv-spine-aff-1")
        token = _make_session_token(ui_client)
        resp = ui_client.get(
            "/conversations/conv-spine-aff-1",
            cookies={"moralstack_session": token},
        )
        assert resp.status_code == 200, resp.text
        assert "Conversation spine" in resp.text
        assert 'class="conv-spine"' in resp.text
        # The strip itself is gone.
        assert "Conversation strip" not in resp.text
        assert 'class="conv-strip"' not in resp.text

    def test_spine_renders_one_node_per_turn(self, ui_client):
        """Was: one strip cell per turn (data-turn count)."""
        _seed_three_turn_conversation("conv-spine-aff-2")
        token = _make_session_token(ui_client)
        resp = ui_client.get(
            "/conversations/conv-spine-aff-2",
            cookies={"moralstack_session": token},
        )
        node_count = resp.text.count("conv-spine-slot--input")
        assert node_count == 3, f"Expected 3 spine nodes, got {node_count}"

    def test_node_action_badge_classes(self, ui_client):
        """Was: conv-strip-cell-normal_complete / -safe_complete / -refuse."""
        _seed_three_turn_conversation("conv-spine-aff-3")
        token = _make_session_token(ui_client)
        resp = ui_client.get(
            "/conversations/conv-spine-aff-3",
            cookies={"moralstack_session": token},
        )
        body = resp.text
        assert 'badge-ok">NORMAL_COMPLETE' in body
        assert 'badge-running">SAFE_COMPLETE' in body
        assert 'badge-fail">REFUSE' in body

    def test_cached_edge_label(self, ui_client):
        """Was: ⚡ icon + data-from-turn/data-to-turn arrow attributes."""
        _seed_three_turn_conversation("conv-spine-aff-4")
        token = _make_session_token(ui_client)
        resp = ui_client.get(
            "/conversations/conv-spine-aff-4",
            cookies={"moralstack_session": token},
        )
        assert "⚡" in resp.text
        assert "reused decision from turn 0" in resp.text

    def test_escalated_posture_emphasis(self, ui_client):
        """Was: conv-strip-cell-escalated box-shadow class on the cell."""
        _seed_three_turn_conversation("conv-spine-aff-5")
        token = _make_session_token(ui_client)
        resp = ui_client.get(
            "/conversations/conv-spine-aff-5",
            cookies={"moralstack_session": token},
        )
        assert "conv-spine-posture-escalated" in resp.text

    def test_spine_node_encodes_risk_magnitude(self, ui_client):
        """New assertion (Codex review): the strip's height encoding
        (bar_height = 24 + risk*56) was pinned by no test. Per B6 this is a
        documented *substitution*, not a 1:1 carryover — the spine renders
        the exact risk value plus a proportional bar instead of height."""
        _seed_three_turn_conversation("conv-spine-aff-6")
        token = _make_session_token(ui_client)
        resp = ui_client.get(
            "/conversations/conv-spine-aff-6",
            cookies={"moralstack_session": token},
        )
        body = resp.text
        # Exact risk value (0.92 for the REFUSE turn), not a relative height.
        assert "0.92" in body
        assert "conv-spine-risk-bar-fill" in body
        assert "width:" in body
