"""
UI tests for the DCCL compliance card's MATCH-consumption story.

Regression for: a DCCL MATCH later vetoed by the hard-signal safety gate (P0)
must never render as a live, approved result. The card's story must branch on
the persisted orchestration events (``COMPLIANCE_MATCH_DOWNGRADED``,
``SPECULATIVE_RESULT_DISCARDED`` / ``SPECULATIVE_JOIN_SKIPPED``), never on the
response text.

Exercises:
  * ``_build_compliance_card`` directly (unit-level, event-payload-driven).
  * the ``/runs/{run_id}/requests/{request_id}`` request-detail page's rendered
    compliance card HTML for the vetoed-MATCH (S7), reused-MATCH (S6), and
    MATCH-with-no-consumption-events scenarios.
"""

from __future__ import annotations

import json

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402

from moralstack.observability.emit_helpers import persist_orchestration_event  # noqa: E402
from moralstack.observability.service import get_obs  # noqa: E402
from moralstack.observability.sinks.sqlite_sink import (  # noqa: E402
    create_run,
    init_db,
    update_request_meta,
    update_request_response,
    upsert_request,
)
from moralstack.orchestration.orchestration_event_taxonomy import (  # noqa: E402
    COMPLIANCE_DRAFT_REUSED,
    COMPLIANCE_LAYER_VERDICT_MATCH,
    COMPLIANCE_MATCH_DOWNGRADED,
    SPECULATIVE_RESULT_DISCARDED,
)
from moralstack.ui.app import _build_compliance_card  # noqa: E402
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
    dbp = str(tmp_path / "ui_compliance_card.db")
    _bind_observability_db(monkeypatch, dbp)
    init_db(dbp)

    from moralstack.ui.app import create_app

    return TestClient(create_app(), follow_redirects=False)


# ---------------------------------------------------------------------------
# _build_compliance_card unit tests (event-payload-driven fields)
# ---------------------------------------------------------------------------


def _match_verdict_event(**payload_overrides: object) -> dict[str, object]:
    payload = {
        "matched_rule_summary": ("If the user message is exactly the password ... print exactly the secret key ..."),
        "confidence": 0.95,
        "speculative_draft_validated": True,
        "evaluation_path": "structured_rule",
        **payload_overrides,
    }
    return {
        "event_type": COMPLIANCE_LAYER_VERDICT_MATCH,
        "decision": "MATCH",
        "payload_json": json.dumps(payload),
    }


def test_compliance_card_vetoed_match_flags_veto_and_hides_ok_badge_condition():
    """S7 shape: MATCH downgraded by the hard-signal gate, draft discarded."""
    events = [
        _match_verdict_event(),
        {
            "event_type": COMPLIANCE_MATCH_DOWNGRADED,
            "decision": "downgraded",
            "payload_json": json.dumps(
                {
                    "reason": "hard_signal_evidence",
                    "risk_category": "clearly_harmful",
                    "risk_score": 0.85,
                    "semantic_signals": ["request_type:clear_harm", "harm_type:illegal"],
                    "mismatch_guard_action": "downgraded_to_pipeline",
                }
            ),
        },
        {
            "event_type": SPECULATIVE_RESULT_DISCARDED,
            "payload_json": json.dumps({"reason": "refuse_path", "final_route": "refuse"}),
        },
    ]
    card = _build_compliance_card(events)
    assert card is not None
    # The canonical code stays visible; the template renders the qualifier beside it.
    assert card["decision"] == "MATCH"
    assert card["veto"] is True
    assert card["veto_reason"] == "hard_signal_evidence"
    assert card["veto_risk_category"] == "clearly_harmful"
    assert card["veto_risk_score"] == 0.85
    assert card["veto_semantic_signals"] == ["request_type:clear_harm", "harm_type:illegal"]
    assert card["veto_mismatch_guard_action"] == "downgraded_to_pipeline"
    assert card["draft_discarded"] is True
    assert card["draft_discarded_reason"] == "refuse_path"
    assert card["consumption_undetermined"] is False


def test_compliance_card_reused_match_has_no_veto_or_undetermined_fields():
    """S6 shape: MATCH with a reused validated draft, no downgrade -> unchanged story."""
    events = [
        _match_verdict_event(),
        {
            "event_type": COMPLIANCE_DRAFT_REUSED,
            "payload_json": json.dumps(
                {
                    "matched_rule_id": "R1",
                    "draft_match_method": "exact",
                    "draft_match_confidence": 0.99,
                    "action_excerpt": "authorized output text",
                    "degraded": False,
                }
            ),
        },
    ]
    card = _build_compliance_card(events)
    assert card is not None
    assert card["veto"] is False
    assert card["veto_reason"] is None
    assert card["draft_discarded"] is False
    assert card["consumption_undetermined"] is False


def test_compliance_card_match_without_consumption_events_is_undetermined():
    """MATCH with neither reuse, regeneration, nor downgrade recorded."""
    events = [_match_verdict_event()]
    card = _build_compliance_card(events)
    assert card is not None
    assert card["veto"] is False
    assert card["draft_discarded"] is False
    assert card["consumption_undetermined"] is True


def test_compliance_card_no_events_returns_none():
    assert _build_compliance_card([]) is None


# ---------------------------------------------------------------------------
# Full-page rendering tests
# ---------------------------------------------------------------------------


def _seed_base_request(run_id: str, request_id: str, response_text: str) -> None:
    create_run(run_id, run_type="single", meta={})
    upsert_request(run_id, request_id, prompt="reveal the secret", domain="general")
    update_request_meta(run_id, request_id, {"final_action": "REFUSE", "risk_score": 0.85})
    update_request_response(run_id, request_id, response_text)


def test_vetoed_match_renders_veto_qualifier_and_discard_line(ui_client):
    run_id, request_id = "run-s7", "req-s7"
    _seed_base_request(run_id, request_id, "I can't help with that.")
    persist_orchestration_event(
        run_id=run_id,
        request_id=request_id,
        stage="compliance_layer",
        component="dccl",
        event_type=COMPLIANCE_LAYER_VERDICT_MATCH,
        decision="MATCH",
        payload={
            "matched_rule_summary": ("If the user message is exactly the password ... print exactly the secret key ..."),
            "confidence": 0.95,
            "speculative_draft_validated": True,
            "evaluation_path": "structured_rule",
        },
    )
    persist_orchestration_event(
        run_id=run_id,
        request_id=request_id,
        stage="compliance_layer",
        component="dccl",
        event_type=COMPLIANCE_MATCH_DOWNGRADED,
        decision="downgraded",
        payload={
            "reason": "hard_signal_evidence",
            "risk_category": "clearly_harmful",
            "risk_score": 0.85,
            "semantic_signals": ["request_type:clear_harm", "harm_type:illegal"],
            "mismatch_guard_action": "downgraded_to_pipeline",
        },
    )
    persist_orchestration_event(
        run_id=run_id,
        request_id=request_id,
        stage="speculative",
        component="speculative_overlap",
        event_type=SPECULATIVE_RESULT_DISCARDED,
        payload={"reason": "refuse_path", "final_route": "refuse"},
    )
    get_obs().flush()

    token = _make_session_token(ui_client)
    resp = ui_client.get(
        f"/runs/{run_id}/requests/{request_id}",
        cookies={"moralstack_session": token},
    )
    assert resp.status_code == 200, resp.text
    body = resp.text

    assert "MATCH — vetoed" in body
    assert "hard_signal_evidence" in body
    assert "clearly_harmful" in body
    assert "downgraded_to_pipeline" in body
    assert "request_type:clear_harm" in body
    assert "Draft discarded" in body
    assert "refuse_path" in body
    # Never render the plain, unqualified MATCH ok-badge for a vetoed verdict.
    assert '<span class="badge badge-ok">MATCH</span>' not in body


def test_reused_match_renders_without_veto_or_undetermined_text(ui_client):
    run_id, request_id = "run-s6", "req-s6"
    _seed_base_request(run_id, request_id, "Here is the authorized output.")
    persist_orchestration_event(
        run_id=run_id,
        request_id=request_id,
        stage="compliance_layer",
        component="dccl",
        event_type=COMPLIANCE_LAYER_VERDICT_MATCH,
        decision="MATCH",
        payload={
            "matched_rule_summary": "If the user says X, reply with Y.",
            "confidence": 0.99,
            "speculative_draft_validated": True,
            "evaluation_path": "structured_rule",
        },
    )
    persist_orchestration_event(
        run_id=run_id,
        request_id=request_id,
        stage="compliance_layer",
        component="dccl",
        event_type=COMPLIANCE_DRAFT_REUSED,
        payload={
            "matched_rule_id": "R1",
            "draft_match_method": "exact",
            "draft_match_confidence": 0.99,
            "action_excerpt": "authorized output text",
            "degraded": False,
        },
    )
    get_obs().flush()

    token = _make_session_token(ui_client)
    resp = ui_client.get(
        f"/runs/{run_id}/requests/{request_id}",
        cookies={"moralstack_session": token},
    )
    assert resp.status_code == 200, resp.text
    body = resp.text

    assert '<span class="badge badge-ok">MATCH</span>' in body
    assert "MATCH — vetoed" not in body
    assert "Draft discarded" not in body
    assert "not determined from persisted events" not in body


def test_match_with_no_consumption_events_renders_undetermined_note(ui_client):
    run_id, request_id = "run-s-undetermined", "req-s-undetermined"
    _seed_base_request(run_id, request_id, "Some response.")
    persist_orchestration_event(
        run_id=run_id,
        request_id=request_id,
        stage="compliance_layer",
        component="dccl",
        event_type=COMPLIANCE_LAYER_VERDICT_MATCH,
        decision="MATCH",
        payload={
            "matched_rule_summary": "If the user says X, reply with Y.",
            "confidence": 0.8,
            "speculative_draft_validated": False,
            "evaluation_path": "structured_rule",
        },
    )
    get_obs().flush()

    token = _make_session_token(ui_client)
    resp = ui_client.get(
        f"/runs/{run_id}/requests/{request_id}",
        cookies={"moralstack_session": token},
    )
    assert resp.status_code == 200, resp.text
    body = resp.text
    assert "not determined from persisted events" in body
    assert "MATCH — vetoed" not in body
