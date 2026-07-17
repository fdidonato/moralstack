"""
UI tests for the ``turn_index`` collision disambiguation on the conversation
timeline (UI-loop iteration 10).

Two structurally different situations can make ``get_requests_for_conversation``
return rows with a repeated ``turn_index``:

  * multi-run collision — two independent runs share one ``conversation_id``,
    each contributing a row at the same ``turn_index`` (different ``run_id``);
  * same-run collision — one run's ``turn_index`` never advanced across a
    genuine multi-turn escalation (identical ``run_id``).

``_build_conversation_timeline`` must classify which shape produced the
collision from already-present fields (``turn_index``, ``run_id``) without
inventing a causal sequence, and the template must render that classification
without disturbing byte-identical output for conversations with no collision.
"""

from __future__ import annotations

import html
import json

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402

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


@pytest.fixture()
def ui_client(tmp_path, monkeypatch):
    dbp = str(tmp_path / "ui_turn_collision.db")
    _bind_observability_db(monkeypatch, dbp)
    init_db(dbp)

    from moralstack.ui.app import create_app

    return TestClient(create_app(), follow_redirects=False)


def _seed_final_trace(run_id: str, request_id: str, final_action: str = "NORMAL_COMPLETE") -> None:
    persist_decision_trace(
        run_id=run_id,
        request_id=request_id,
        stage="FINAL",
        sequence=1,
        trace_json=json.dumps({"final_action": final_action, "path": "FAST_PATH", "winning_rule": "rule_x"}),
    )


def test_multi_run_collision_view_model_and_rendering(ui_client):
    """C1 shape: two independent runs sharing one conversation_id, both rows at
    turn_index=1 with different run_ids."""
    conv_id = "conv-collision-multi-run"
    run_a, run_b = "run-collision-a", "run-collision-b"
    req_a, req_b = "req-collision-a", "req-collision-b"

    create_run(run_a, run_type="single", meta={})
    create_run(run_b, run_type="single", meta={})
    upsert_request(run_a, req_a, prompt="attempt 1", domain="general", conversation_id=conv_id, turn_index=1)
    upsert_request(run_b, req_b, prompt="attempt 1 retried", domain="general", conversation_id=conv_id, turn_index=1)
    update_request_meta(run_a, req_a, {"final_action": "NORMAL_COMPLETE", "risk_score": 0.1})
    update_request_meta(run_b, req_b, {"final_action": "NORMAL_COMPLETE", "risk_score": 0.2})
    _seed_final_trace(run_a, req_a)
    _seed_final_trace(run_b, req_b)
    get_obs().flush()

    from moralstack.ui.app import _build_conversation_timeline

    timeline = _build_conversation_timeline(conv_id)
    assert timeline["turn_index_collision"] is True
    assert timeline["turn_index_collision_multi_run"] is True

    by_request_id = {r["request_id"]: r for r in timeline["requests"]}
    first, second = by_request_id[req_a], by_request_id[req_b]
    assert first["turn_index_collision"] is True
    assert second["turn_index_collision"] is True
    assert first["turn_index_collision_multi_run"] is True
    assert second["turn_index_collision_multi_run"] is True
    assert {first["turn_index_collision_pos"], second["turn_index_collision_pos"]} == {1, 2}
    assert first["turn_index_collision_group_size"] == 2
    assert second["turn_index_collision_group_size"] == 2
    assert {first["seq_pos"], second["seq_pos"]} == {1, 2}

    token = _make_session_token(ui_client)
    resp = ui_client.get(f"/conversations/{conv_id}", cookies={"moralstack_session": token})
    assert resp.status_code == 200, resp.text
    body = html.unescape(resp.text)

    # Canonical turn_index still rendered verbatim (spine node header).
    assert "Turn 1" in body

    # Spine node labels disambiguate positions.
    assert "#1/2" in body
    assert "#2/2" in body

    # Posture-timeline table classification (kept, collapsed into <details>).
    assert "separate runs share this turn_index" in body

    # Conversation-level note.
    assert "turn-index-collision-note" in body
    assert "spans separate runs" in body

    # Each spine node link aria-label must be unique (distinct request fragments).
    assert f"request {req_a[:8]}" in body
    assert f"request {req_b[:8]}" in body

    # The connector into a colliding node is a non-causal divider, never a
    # claim of causality/sequence.
    assert "conv-spine-pipe--unordered" in body
    assert "order not established" in body
    assert "escalation" not in body.lower()
    assert "sequence" not in body.lower()


def test_same_run_collision_view_model_and_rendering(ui_client):
    """C2 shape: one run whose turn_index never advanced across a genuine
    3-turn escalation (identical run_id for all three rows)."""
    conv_id = "conv-collision-same-run"
    run_id = "run-collision-same"
    reqs = ["req-collision-s1", "req-collision-s2", "req-collision-s3"]

    create_run(run_id, run_type="single", meta={})
    for i, rid in enumerate(reqs):
        upsert_request(run_id, rid, prompt=f"turn text {i}", domain="general", conversation_id=conv_id, turn_index=0)
    update_request_meta(run_id, reqs[0], {"final_action": "NORMAL_COMPLETE", "risk_score": 0.1})
    update_request_meta(run_id, reqs[1], {"final_action": "NORMAL_COMPLETE", "risk_score": 0.3})
    update_request_meta(run_id, reqs[2], {"final_action": "REFUSE", "risk_score": 0.9})
    for rid in reqs:
        _seed_final_trace(run_id, rid, final_action="NORMAL_COMPLETE")
    get_obs().flush()

    from moralstack.ui.app import _build_conversation_timeline

    timeline = _build_conversation_timeline(conv_id)
    assert timeline["turn_index_collision"] is True
    assert timeline["turn_index_collision_multi_run"] is False

    by_request_id = {r["request_id"]: r for r in timeline["requests"]}
    positions = sorted(by_request_id[rid]["turn_index_collision_pos"] for rid in reqs)
    assert positions == [1, 2, 3]
    for rid in reqs:
        item = by_request_id[rid]
        assert item["turn_index_collision"] is True
        assert item["turn_index_collision_multi_run"] is False
        assert item["turn_index_collision_group_size"] == 3

    token = _make_session_token(ui_client)
    resp = ui_client.get(f"/conversations/{conv_id}", cookies={"moralstack_session": token})
    assert resp.status_code == 200, resp.text
    body = html.unescape(resp.text)

    assert "Turn 0" in body
    assert "same run, turn_index did not advance" in body
    assert "within a single run" in body

    # The connector into each colliding node is a non-causal divider.
    assert "conv-spine-pipe--unordered" in body
    assert "order not established" in body
    assert "escalation" not in body.lower()
    assert "sequence" not in body.lower()


def test_no_collision_renders_byte_identical_to_unmodified_shape(ui_client):
    """C3 / control shape: turn_index 0/1/2, no repeats. None of the new
    disambiguation strings must appear (non-regression guard)."""
    conv_id = "conv-no-collision"
    run_id = "run-no-collision"
    reqs = ["req-nc-0", "req-nc-1", "req-nc-2"]

    create_run(run_id, run_type="single", meta={})
    for i, rid in enumerate(reqs):
        upsert_request(run_id, rid, prompt=f"turn {i}", domain="general", conversation_id=conv_id, turn_index=i)
        update_request_meta(run_id, rid, {"final_action": "NORMAL_COMPLETE", "risk_score": 0.1 * i})
        _seed_final_trace(run_id, rid)
    get_obs().flush()

    from moralstack.ui.app import _build_conversation_timeline

    timeline = _build_conversation_timeline(conv_id)
    assert timeline["turn_index_collision"] is False
    assert timeline["turn_index_collision_multi_run"] is False
    for item in timeline["requests"]:
        assert item["turn_index_collision"] is False
        assert item["turn_index_collision_multi_run"] is False
        assert item["turn_index_collision_group_size"] == 1

    token = _make_session_token(ui_client)
    resp = ui_client.get(f"/conversations/{conv_id}", cookies={"moralstack_session": token})
    assert resp.status_code == 200, resp.text
    body = resp.text

    # Canonical turn_index still rendered verbatim (spine node header).
    assert "Turn 0" in body
    assert "Turn 1" in body
    assert "Turn 2" in body

    # None of the new disambiguation surfaces are present.
    assert "turn-index-collision-note" not in body
    assert "sharing this turn_index" not in body
    assert "#1/" not in body
    assert "separate runs" not in body
    assert "same run, turn_index" not in body
    assert "conv-spine-pipe--unordered" not in body
    assert "order not established" not in body
