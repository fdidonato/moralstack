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
  * the conversation strip's per-turn failure treatment and aggregate note;
  * the four conversation-page surfaces (final-actions tile, last-posture
    tile, posture-timeline Action column, per-turn card) that must not
    present a pipeline-failure turn's delivered ``final_action`` as a
    governed outcome without a caveat.
"""

from __future__ import annotations

import json

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402

from moralstack.observability.conversation_events import emit_conversation_state_updated  # noqa: E402
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


def test_conversation_fail_closed_risk_is_labelled_beside_the_raw_sentinel(ui_client):
    """The fail-closed risk_score=1.0 sentinel must never be presented as an
    assessed score: the raw value stays visible, and a plain-language label plus
    the last genuinely assessed risk (from PRE_POLICY) is shown beside it, both
    on the conversation-level 'Max risk score' tile and on the per-turn surfaces."""
    run_id, conv_id = "run-conv-crash-2", "conv-crash-2"
    create_run(run_id, run_type="single", meta={})
    upsert_request(run_id, "req-conv-c", prompt="turn 0", domain="general", conversation_id=conv_id, turn_index=0)
    upsert_request(
        run_id,
        "req-conv-d",
        prompt="turn 1",
        domain="general",
        conversation_id=conv_id,
        turn_index=1,
        parent_request_id="req-conv-c",
    )
    update_request_meta(run_id, "req-conv-c", {"final_action": "NORMAL_COMPLETE", "risk_score": 0.2})
    update_request_meta(
        run_id,
        "req-conv-d",
        {"final_action": "NORMAL_COMPLETE", "risk_score": 1.0, "triggered_principles": ["SYSTEM.ERROR"]},
    )
    update_request_response(run_id, "req-conv-d", "[SYSTEM_ERROR]")
    persist_decision_trace(
        run_id=run_id,
        request_id="req-conv-c",
        stage="FINAL",
        sequence=2,
        trace_json=json.dumps({"final_action": "NORMAL_COMPLETE", "path": "FAST_PATH", "winning_rule": "rule_x"}),
    )
    persist_decision_trace(
        run_id=run_id,
        request_id="req-conv-d",
        stage="PRE_POLICY",
        sequence=1,
        trace_json=json.dumps(
            {"final_action": "SAFE_COMPLETE", "winning_rule": "policy_bounds_fallback", "risk_score": 0.6}
        ),
    )
    get_obs().flush()

    token = _make_session_token(ui_client)
    resp = ui_client.get(
        f"/conversations/{conv_id}",
        cookies={"moralstack_session": token},
    )
    assert resp.status_code == 200, resp.text
    body = resp.text

    # The canonical raw sentinel value stays visible everywhere (never removed).
    assert "1.000" in body

    # Aggregate "Max risk score" tile: labelled as fail-closed, with the assessed alternative.
    assert "fail-closed default (pipeline failure), not an assessed score" in body
    assert "max assessed: 0.600" in body

    # Per-turn surfaces (strip title, posture-timeline cell, card badge, governance row):
    # the fail-closed label plus the last assessed risk (0.6 from PRE_POLICY).
    assert "fail-closed default" in body
    assert "last assessed 0.600" in body or "last assessed 0.6000" in body


def test_normal_conversation_has_no_fail_closed_label(ui_client):
    """A conversation with no pipeline-failed turns must never render the
    fail-closed label — the string must be byte-absent."""
    run_id, conv_id = "run-conv-normal-2", "conv-normal-2"
    create_run(run_id, run_type="single", meta={})
    upsert_request(run_id, "req-conv-g", prompt="turn 0", domain="general", conversation_id=conv_id, turn_index=0)
    upsert_request(
        run_id,
        "req-conv-h",
        prompt="turn 1",
        domain="general",
        conversation_id=conv_id,
        turn_index=1,
        parent_request_id="req-conv-g",
    )
    update_request_meta(run_id, "req-conv-g", {"final_action": "NORMAL_COMPLETE", "risk_score": 0.1})
    update_request_meta(run_id, "req-conv-h", {"final_action": "NORMAL_COMPLETE", "risk_score": 0.3})
    persist_decision_trace(
        run_id=run_id,
        request_id="req-conv-g",
        stage="FINAL",
        sequence=1,
        trace_json=json.dumps({"final_action": "NORMAL_COMPLETE", "path": "FAST_PATH", "winning_rule": "rule_x"}),
    )
    persist_decision_trace(
        run_id=run_id,
        request_id="req-conv-h",
        stage="FINAL",
        sequence=1,
        trace_json=json.dumps({"final_action": "NORMAL_COMPLETE", "path": "FAST_PATH", "winning_rule": "rule_x"}),
    )
    get_obs().flush()

    token = _make_session_token(ui_client)
    resp = ui_client.get(
        f"/conversations/{conv_id}",
        cookies={"moralstack_session": token},
    )
    assert resp.status_code == 200, resp.text
    body = resp.text
    assert "fail-closed default" not in body
    assert "not an assessed score" not in body


def test_max_risk_is_fail_closed_false_when_a_non_failed_turn_also_reaches_the_max(ui_client):
    """When a non-failed turn's meta risk_score equals the overview max, the max
    is a genuine assessed score even though a failed turn also happens to match
    it — the aggregate label must stay off (conservative, provable-only)."""
    run_id, conv_id = "run-conv-crash-3", "conv-crash-3"
    create_run(run_id, run_type="single", meta={})
    upsert_request(run_id, "req-conv-i", prompt="turn 0", domain="general", conversation_id=conv_id, turn_index=0)
    upsert_request(
        run_id,
        "req-conv-j",
        prompt="turn 1",
        domain="general",
        conversation_id=conv_id,
        turn_index=1,
        parent_request_id="req-conv-i",
    )
    update_request_meta(run_id, "req-conv-i", {"final_action": "NORMAL_COMPLETE", "risk_score": 1.0})
    update_request_meta(
        run_id,
        "req-conv-j",
        {"final_action": "NORMAL_COMPLETE", "risk_score": 1.0, "triggered_principles": ["SYSTEM.ERROR"]},
    )
    update_request_response(run_id, "req-conv-j", "[SYSTEM_ERROR]")
    persist_decision_trace(
        run_id=run_id,
        request_id="req-conv-i",
        stage="FINAL",
        sequence=1,
        trace_json=json.dumps({"final_action": "NORMAL_COMPLETE", "path": "FAST_PATH", "winning_rule": "rule_x"}),
    )
    persist_decision_trace(
        run_id=run_id,
        request_id="req-conv-j",
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
    # The aggregate tile note is not provable here and must not be rendered.
    assert "not an assessed score" not in body
    # The per-turn label for the failed turn itself is unaffected by the aggregate.
    assert "fail-closed default" in body


def test_conversation_page_flags_pipeline_failure_action_and_posture(ui_client):
    """A single-turn conversation whose only turn is a pipeline failure must not
    present its delivered final_action / posture as a governed outcome on any of
    the four surfaces: final-actions tile, last-posture tile, posture-timeline
    Action column, and the per-turn card (header badge + governance-decision row).
    The canonical delivered values stay visible everywhere, unqualified deletion
    never happens — the qualifier is appended text beside them."""
    run_id, conv_id = "run-conv-crash-5", "conv-crash-5"
    create_run(run_id, run_type="single", meta={})
    upsert_request(run_id, "req-conv-k", prompt="turn 0", domain="general", conversation_id=conv_id, turn_index=0)
    update_request_meta(
        run_id,
        "req-conv-k",
        {"final_action": "NORMAL_COMPLETE", "risk_score": 1.0, "triggered_principles": ["SYSTEM.ERROR"]},
    )
    update_request_response(run_id, "req-conv-k", "[SYSTEM_ERROR]")
    persist_decision_trace(
        run_id=run_id,
        request_id="req-conv-k",
        stage="RISK_ASSESSMENT",
        sequence=1,
        trace_json=json.dumps({"risk_score": 0.6}),
    )
    emit_conversation_state_updated(
        run_id=run_id,
        request_id="req-conv-k",
        conversation_id=conv_id,
        turn_index=0,
        state_in=None,
        state_out=None,
        final_action="NORMAL_COMPLETE",
        risk_score=1.0,
        posture="ESCALATED",
    )
    get_obs().flush()

    token = _make_session_token(ui_client)
    resp = ui_client.get(
        f"/conversations/{conv_id}",
        cookies={"moralstack_session": token},
    )
    assert resp.status_code == 200, resp.text
    body = resp.text

    # Canonical delivered values stay visible (never removed).
    assert "NORMAL_COMPLETE: 1" in body
    assert "ESCALATED" in body

    # Final-actions tile: qualifier with the correct per-action failure count.
    assert "includes 1 pipeline failure, not a governed outcome" in body

    # Last-posture tile: qualifier, since this conversation's only turn (and
    # therefore the only source of last_posture) is a pipeline failure.
    assert "from a turn that ended in a pipeline failure, not a governed outcome" in body

    # "not a governed outcome" must appear exactly 7 times for this single failed
    # turn: the iteration-01 banner, the conversation-strip cell title (both
    # pre-existing), plus the five surfaces this change adds — final-actions tile,
    # last-posture tile, posture-timeline Action cell, per-turn header badge, and
    # the per-turn Governance decision -> Final action row.
    assert body.count("not a governed outcome") == 7


def test_normal_conversation_has_no_pipeline_failure_action_qualifiers(ui_client):
    """A conversation with no pipeline-failed turns must never render the new
    qualifiers, and the underlying aggregates must be empty/False."""
    run_id, conv_id = "run-conv-normal-3", "conv-normal-3"
    create_run(run_id, run_type="single", meta={})
    upsert_request(run_id, "req-conv-l", prompt="turn 0", domain="general", conversation_id=conv_id, turn_index=0)
    upsert_request(
        run_id,
        "req-conv-m",
        prompt="turn 1",
        domain="general",
        conversation_id=conv_id,
        turn_index=1,
        parent_request_id="req-conv-l",
    )
    update_request_meta(run_id, "req-conv-l", {"final_action": "NORMAL_COMPLETE", "risk_score": 0.1})
    update_request_meta(run_id, "req-conv-m", {"final_action": "NORMAL_COMPLETE", "risk_score": 0.2})
    persist_decision_trace(
        run_id=run_id,
        request_id="req-conv-l",
        stage="FINAL",
        sequence=1,
        trace_json=json.dumps({"final_action": "NORMAL_COMPLETE", "path": "FAST_PATH", "winning_rule": "rule_x"}),
    )
    persist_decision_trace(
        run_id=run_id,
        request_id="req-conv-m",
        stage="FINAL",
        sequence=1,
        trace_json=json.dumps({"final_action": "NORMAL_COMPLETE", "path": "FAST_PATH", "winning_rule": "rule_x"}),
    )
    emit_conversation_state_updated(
        run_id=run_id,
        request_id="req-conv-m",
        conversation_id=conv_id,
        turn_index=1,
        state_in=None,
        state_out=None,
        final_action="NORMAL_COMPLETE",
        risk_score=0.2,
        posture="STABLE",
    )
    get_obs().flush()

    from moralstack.ui.app import _build_conversation_timeline

    timeline = _build_conversation_timeline(conv_id)
    assert timeline["pipeline_failure_action_counts"] == {}
    assert timeline["last_posture_is_from_pipeline_failure"] is False

    token = _make_session_token(ui_client)
    resp = ui_client.get(
        f"/conversations/{conv_id}",
        cookies={"moralstack_session": token},
    )
    assert resp.status_code == 200, resp.text
    body = resp.text
    assert "not a governed outcome" not in body


def test_mixed_conversation_flags_only_the_failed_turn_action(ui_client):
    """One failed turn and one normal turn share the same final_action: the raw
    aggregate total must stay correct, and the qualifier must report exactly the
    failed-turn count, not the whole distribution."""
    run_id, conv_id = "run-conv-mixed-1", "conv-mixed-1"
    create_run(run_id, run_type="single", meta={})
    upsert_request(run_id, "req-conv-n", prompt="turn 0", domain="general", conversation_id=conv_id, turn_index=0)
    upsert_request(
        run_id,
        "req-conv-o",
        prompt="turn 1",
        domain="general",
        conversation_id=conv_id,
        turn_index=1,
        parent_request_id="req-conv-n",
    )
    update_request_meta(run_id, "req-conv-n", {"final_action": "NORMAL_COMPLETE", "risk_score": 0.1})
    update_request_meta(
        run_id,
        "req-conv-o",
        {"final_action": "NORMAL_COMPLETE", "risk_score": 1.0, "triggered_principles": ["SYSTEM.ERROR"]},
    )
    update_request_response(run_id, "req-conv-o", "[SYSTEM_ERROR]")
    persist_decision_trace(
        run_id=run_id,
        request_id="req-conv-n",
        stage="FINAL",
        sequence=1,
        trace_json=json.dumps({"final_action": "NORMAL_COMPLETE", "path": "FAST_PATH", "winning_rule": "rule_x"}),
    )
    persist_decision_trace(
        run_id=run_id,
        request_id="req-conv-o",
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

    # Raw total across both turns stays correct (never reduced by the failure).
    assert "NORMAL_COMPLETE: 2" in body
    # Qualifier reports the failed-turn count only, not the whole distribution.
    assert "includes 1 pipeline failure, not a governed outcome" in body
