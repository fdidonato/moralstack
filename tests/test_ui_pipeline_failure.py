"""
UI tests for pipeline-failure detection and rendering (governance observability).

A pipeline failure is a request whose controller crashed before the pipeline
reached a governed FINAL decision (see ``OrchestratorController._handle_error``):
``requests.final_response`` is the ``"[SYSTEM_ERROR]"`` sentinel,
``meta_json.triggered_principles`` records the synthetic ``"SYSTEM.ERROR"``
principle, and no ``FINAL`` decision-trace row was ever written.

These tests exercise:
  * ``_detect_pipeline_failure`` from structured signals only;
  * the request-detail page's delivery-path failure state and error-styled
    final-response block;
  * the "no recorded pre-delivery decision" replacement for the old
    "unknown path chose unknown" placeholder sentence (both in the failure
    case and in the unrelated no-FINAL/no-SYSTEM.ERROR fast-path case);
  * the conversation strip's per-turn failure treatment and aggregate note.
"""

from __future__ import annotations

import json

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402

from moralstack.observability.emit_helpers import (  # noqa: E402
    persist_decision_trace,
    persist_orchestration_event,
)
from moralstack.observability.service import get_obs  # noqa: E402
from moralstack.observability.sinks.sqlite_sink import (  # noqa: E402
    create_run,
    init_db,
    update_request_meta,
    update_request_response,
    upsert_request,
)
from moralstack.orchestration.orchestration_event_taxonomy import PROXY_OUTPUT_FINALIZED  # noqa: E402
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
    dbp = str(tmp_path / "ui_pipeline_failure.db")
    _bind_observability_db(monkeypatch, dbp)
    init_db(dbp)

    from moralstack.ui.app import create_app

    return TestClient(create_app(), follow_redirects=False)


def _seed_crashed_request(run_id: str, request_id: str) -> None:
    """A request that crashed pre-FINAL: RISK_ASSESSMENT / PRE_POLICY / RELEVANT_PRINCIPLES,
    no FINAL row, meta_json.triggered_principles == ["SYSTEM.ERROR"]."""
    create_run(run_id, run_type="single", meta={})
    upsert_request(run_id, request_id, prompt="do something", domain="general")
    update_request_meta(
        run_id,
        request_id,
        {"final_action": "NORMAL_COMPLETE", "risk_score": 1.0, "triggered_principles": ["SYSTEM.ERROR"]},
    )
    update_request_response(run_id, request_id, "[SYSTEM_ERROR]")
    persist_decision_trace(
        run_id=run_id,
        request_id=request_id,
        stage="RISK_ASSESSMENT",
        sequence=1,
        trace_json=json.dumps({"risk_score": 0.6, "risk_category": "moderate"}),
    )
    persist_decision_trace(
        run_id=run_id,
        request_id=request_id,
        stage="PRE_POLICY",
        sequence=2,
        trace_json=json.dumps(
            {
                "final_action": "SAFE_COMPLETE",
                "winning_rule": "policy_bounds_fallback",
                "path": "DELIBERATIVE_PATH",
                "risk_score": 0.6,
            }
        ),
    )
    persist_decision_trace(
        run_id=run_id,
        request_id=request_id,
        stage="RELEVANT_PRINCIPLES",
        sequence=3,
        trace_json=json.dumps({"relevant_principle_ids": ["p1", "p2"]}),
    )
    # The proxy still finalizes delivery (see PROXY_OUTPUT_FINALIZED in the real trace):
    # the canonical delivered action code must stay visible beside the failure warning.
    persist_orchestration_event(
        run_id=run_id,
        request_id=request_id,
        stage="PROXY",
        component="proxy",
        event_type=PROXY_OUTPUT_FINALIZED,
        decision="NORMAL_COMPLETE",
        payload={"final_action": "NORMAL_COMPLETE", "final_text_source": "governed"},
    )
    get_obs().flush()


def _seed_normal_request(run_id: str, request_id: str) -> None:
    """A normal request: FINAL trace present, no SYSTEM.ERROR principle."""
    create_run(run_id, run_type="single", meta={})
    upsert_request(run_id, request_id, prompt="hello", domain="general")
    update_request_meta(run_id, request_id, {"final_action": "NORMAL_COMPLETE", "risk_score": 0.05})
    update_request_response(run_id, request_id, "Hi there!")
    persist_decision_trace(
        run_id=run_id,
        request_id=request_id,
        stage="FINAL",
        sequence=2,
        trace_json=json.dumps({"final_action": "NORMAL_COMPLETE", "path": "FAST_PATH", "winning_rule": "rule_default"}),
    )
    get_obs().flush()


def _seed_fast_path_no_final_no_error(run_id: str, request_id: str) -> None:
    """The S6/DCCL fast-path shape: no FINAL trace, but no SYSTEM.ERROR either.

    Must NOT be flagged as a pipeline failure; the placeholder sentence must
    still be replaced (this is the general fix, not failure-specific).
    """
    create_run(run_id, run_type="single", meta={})
    upsert_request(run_id, request_id, prompt="fast path request", domain="general")
    update_request_meta(run_id, request_id, {"final_action": "NORMAL_COMPLETE", "risk_score": 0.1})
    update_request_response(run_id, request_id, "Delivered via compliance fast path.")
    persist_decision_trace(
        run_id=run_id,
        request_id=request_id,
        stage="RELEVANT_PRINCIPLES",
        sequence=1,
        trace_json=json.dumps({"relevant_principle_ids": []}),
    )
    get_obs().flush()


def test_pipeline_failure_request_renders_failure_state(ui_client):
    run_id, request_id = "run-crash-1", "req-crash-1"
    _seed_crashed_request(run_id, request_id)
    token = _make_session_token(ui_client)
    resp = ui_client.get(
        f"/runs/{run_id}/requests/{request_id}",
        cookies={"moralstack_session": token},
    )
    assert resp.status_code == 200, resp.text
    body = resp.text

    # Distinct, error-styled delivery-path state with a text label (never colour-only).
    assert "PIPELINE FAILURE" in body
    # The canonical delivered action code stays visible beside the warning.
    assert "NORMAL_COMPLETE" in body
    # The last recorded pre-delivery decision (from PRE_POLICY) is surfaced, not fabricated.
    assert "policy_bounds_fallback" in body
    assert "SAFE_COMPLETE" in body
    assert "DELIBERATIVE_PATH" in body
    # The error-styled Final Response caption is present.
    assert "SYSTEM ERROR" in body
    assert "[SYSTEM_ERROR]" in body
    # The nonsense placeholder sentence must never be rendered.
    assert "unknown path chose unknown" not in body


def test_normal_request_is_not_flagged_as_failure(ui_client):
    run_id, request_id = "run-normal-1", "req-normal-1"
    _seed_normal_request(run_id, request_id)
    token = _make_session_token(ui_client)
    resp = ui_client.get(
        f"/runs/{run_id}/requests/{request_id}",
        cookies={"moralstack_session": token},
    )
    assert resp.status_code == 200, resp.text
    body = resp.text
    assert "PIPELINE FAILURE" not in body
    assert "system-error placeholder" not in body


def test_fast_path_without_final_trace_is_not_flagged_but_placeholder_fixed(ui_client):
    run_id, request_id = "run-fastpath-1", "req-fastpath-1"
    _seed_fast_path_no_final_no_error(run_id, request_id)
    token = _make_session_token(ui_client)
    resp = ui_client.get(
        f"/runs/{run_id}/requests/{request_id}",
        cookies={"moralstack_session": token},
    )
    assert resp.status_code == 200, resp.text
    body = resp.text
    assert "PIPELINE FAILURE" not in body
    assert "unknown path chose unknown" not in body
    assert "no recorded pre-delivery decision (no FINAL decision trace)" in body


def test_conversation_strip_flags_failed_turn_and_shows_aggregate_note(ui_client):
    run_id, conv_id = "run-conv-crash", "conv-crash-1"
    create_run(run_id, run_type="single", meta={})
    upsert_request(run_id, "req-conv-a", prompt="turn 0", domain="general", conversation_id=conv_id, turn_index=0)
    upsert_request(
        run_id,
        "req-conv-b",
        prompt="turn 1",
        domain="general",
        conversation_id=conv_id,
        turn_index=1,
        parent_request_id="req-conv-a",
    )
    update_request_meta(run_id, "req-conv-a", {"final_action": "NORMAL_COMPLETE", "risk_score": 0.1})
    update_request_meta(
        run_id,
        "req-conv-b",
        {"final_action": "NORMAL_COMPLETE", "risk_score": 1.0, "triggered_principles": ["SYSTEM.ERROR"]},
    )
    update_request_response(run_id, "req-conv-b", "[SYSTEM_ERROR]")
    persist_decision_trace(
        run_id=run_id,
        request_id="req-conv-a",
        stage="FINAL",
        sequence=2,
        trace_json=json.dumps({"final_action": "NORMAL_COMPLETE", "path": "FAST_PATH", "winning_rule": "rule_x"}),
    )
    persist_decision_trace(
        run_id=run_id,
        request_id="req-conv-b",
        stage="RISK_ASSESSMENT",
        sequence=1,
        trace_json=json.dumps({"risk_score": 1.0}),
    )
    get_obs().flush()

    token = _make_session_token(ui_client)
    resp = ui_client.get(
        f"/conversations/{conv_id}",
        cookies={"moralstack_session": token},
    )
    assert resp.status_code == 200, resp.text
    body = resp.text
    assert "conv-strip-cell-failure" in body
    assert "conv-strip-label-failure" in body
    assert "pipeline failure" in body
    assert "ended in a pipeline failure" in body
