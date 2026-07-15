"""
UI tests for iteration 12: per-turn ``final_action`` badges on the conversation
view are colour-coded by the strip's legend (green=NORMAL_COMPLETE,
amber=SAFE_COMPLETE, red=REFUSE), and a pipeline-failure turn's coerced
``final_action`` placeholder stays NEUTRAL (never the green success colour).

The colour is a secondary signal only — the action code text is always
rendered — so these assert the class beside the code, and that the failure case
never gains ``badge-ok`` (which would reintroduce the false-success signal that
iterations 01/06 removed).
"""

from __future__ import annotations

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
    update_request_response,
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
    dbp = str(tmp_path / "ui_action_badge_colour.db")
    _bind_observability_db(monkeypatch, dbp)
    init_db(dbp)

    from moralstack.ui.app import create_app

    return TestClient(create_app(), follow_redirects=False)


def _seed_governed_turn(run_id: str, request_id: str, conv_id: str, turn_index: int, action: str, risk: float) -> None:
    """A governed turn: FINAL trace present (not a pipeline failure), given final_action."""
    upsert_request(
        run_id, request_id, prompt=f"turn {turn_index}", domain="general", conversation_id=conv_id, turn_index=turn_index
    )
    update_request_meta(run_id, request_id, {"final_action": action, "risk_score": risk})
    update_request_response(run_id, request_id, "response")
    persist_decision_trace(
        run_id=run_id,
        request_id=request_id,
        stage="FINAL",
        sequence=2,
        trace_json=json.dumps({"final_action": action, "path": "DELIBERATIVE_PATH"}),
    )


def _get_conversation(client: TestClient, conv_id: str) -> str:
    token = _make_session_token(client)
    resp = client.get(f"/conversations/{conv_id}", cookies={"moralstack_session": token})
    assert resp.status_code == 200, resp.text
    return resp.text


def test_governed_actions_are_colour_coded_by_legend(ui_client) -> None:
    run_id, conv_id = "run-badge-colour", "conv-badge-colour"
    create_run(run_id, run_type="single", meta={})
    _seed_governed_turn(run_id, "req-nc", conv_id, 0, "NORMAL_COMPLETE", 0.05)
    _seed_governed_turn(run_id, "req-sc", conv_id, 1, "SAFE_COMPLETE", 0.35)
    _seed_governed_turn(run_id, "req-rf", conv_id, 2, "REFUSE", 0.85)
    get_obs().flush()

    body = _get_conversation(ui_client, conv_id)

    # Each governed action carries its legend colour beside the (unchanged) code.
    assert '<span class="badge badge-ok">NORMAL_COMPLETE</span>' in body
    assert '<span class="badge badge-running">SAFE_COMPLETE</span>' in body
    assert '<span class="badge badge-fail">REFUSE</span>' in body


def test_pipeline_failure_action_badge_stays_neutral(ui_client) -> None:
    run_id, conv_id = "run-badge-fail", "conv-badge-fail"
    create_run(run_id, run_type="single", meta={})
    # A crashed turn: coerced NORMAL_COMPLETE placeholder, SYSTEM.ERROR principle, no FINAL trace.
    upsert_request(run_id, "req-crash", prompt="turn 0", domain="general", conversation_id=conv_id, turn_index=0)
    update_request_meta(
        run_id,
        "req-crash",
        {"final_action": "NORMAL_COMPLETE", "risk_score": 1.0, "triggered_principles": ["SYSTEM.ERROR"]},
    )
    update_request_response(run_id, "req-crash", "[SYSTEM_ERROR]")
    persist_decision_trace(
        run_id=run_id,
        request_id="req-crash",
        stage="RISK_ASSESSMENT",
        sequence=1,
        trace_json=json.dumps({"risk_score": 0.6}),
    )
    get_obs().flush()

    body = _get_conversation(ui_client, conv_id)

    # The failed turn's coerced NORMAL_COMPLETE must NEVER be coloured as a green
    # success anywhere on the page — the neutral badge + caveat carries the truth.
    assert '<span class="badge badge-ok">NORMAL_COMPLETE</span>' not in body
    # The canonical code is still rendered (neutral), beside the honest caveat.
    assert '<span class="badge">NORMAL_COMPLETE</span>' in body
    assert "not a governed outcome" in body
