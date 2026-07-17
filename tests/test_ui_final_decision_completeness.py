"""
UI tests for request-spine completeness (Part A of the conversation-spine /
request-spine-completeness plan): ``activated_signals`` (A1), gated
``hard_violation_codes`` visibility on the OUTPUT anchor (A2), and the
``risk_score`` falsy-gate fix (A3).

A1/A2/A3 are template-only / additive: requests with no new fields render
byte-identically.

A fourth item (A4) was planned and dropped: it would have gated ``Semantic Harm``
on ``sim_worst_harm is not none``, on the premise that ``sim_worst_harm`` marks
whether the simulator ran. It does not. ``simulator_module.py:671-684`` skips
consequences whose ``harm_type`` is ``"none"``, so a simulator that ran and found
everything benign yields ``semantic_expected_harm=0.0`` **and**
``worst_harm=None`` — byte-identical to the never-ran defaults left by
``decision_service.py:395-396``. No FINAL-trace field distinguishes the two, so
the gate stays on the pre-existing truthiness check. See ``docs/CODEBASE_FACTS.md``
("Future work / known gaps") for the open defect.
"""

from __future__ import annotations

import html
import json

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402

from moralstack.observability.emit_helpers import (  # noqa: E402
    persist_decision_trace,
    persist_llm_call,
)
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
    dbp = str(tmp_path / "ui_final_decision_completeness.db")
    _bind_observability_db(monkeypatch, dbp)
    init_db(dbp)

    from moralstack.ui.app import create_app

    return TestClient(create_app(), follow_redirects=False)


def _get_request_page(ui_client, run_id: str, request_id: str) -> str:
    token = _make_session_token(ui_client)
    resp = ui_client.get(
        f"/runs/{run_id}/requests/{request_id}",
        cookies={"moralstack_session": token},
    )
    assert resp.status_code == 200, resp.text
    return html.unescape(resp.text)


def _seed_final_trace(run_id: str, request_id: str, trace_json: dict) -> None:
    create_run(run_id, run_type="single", meta={})
    upsert_request(run_id, request_id, prompt="test prompt", domain="general")
    update_request_meta(run_id, request_id, {"final_action": trace_json.get("final_action", "NORMAL_COMPLETE")})
    update_request_response(run_id, request_id, "a delivered answer")
    # The OUTPUT anchor / final-decision-grid only renders when flow_data_cycles
    # is non-empty (request.html gates the whole spine on llm_calls/flow_data_cycles).
    persist_llm_call(
        run_id=run_id,
        request_id=request_id,
        cycle=0,
        phase="policy",
        module="policy",
        action="draft",
        raw_response="a draft",
    )
    persist_decision_trace(
        run_id=run_id,
        request_id=request_id,
        stage="FINAL",
        sequence=1,
        trace_json=json.dumps(trace_json),
    )
    get_obs().flush()


# ---------------------------------------------------------------------------
# A1 — activated_signals on the OUTPUT card
# ---------------------------------------------------------------------------


def test_activated_signals_rendered_on_output_card(ui_client):
    run_id, request_id = "run-a1", "req-a1"
    _seed_final_trace(
        run_id,
        request_id,
        {
            "final_action": "NORMAL_COMPLETE",
            "risk_score": 0.2,
            "activated_signals": ["self_harm_mention", "medical_context"],
        },
    )
    body = _get_request_page(ui_client, run_id, request_id)
    assert "Risk signals (activated)" in body
    assert "self_harm_mention, medical_context" in body


def test_output_card_omits_risk_signals_when_empty(ui_client):
    run_id, request_id = "run-a1-empty", "req-a1-empty"
    _seed_final_trace(
        run_id,
        request_id,
        {"final_action": "NORMAL_COMPLETE", "risk_score": 0.2, "activated_signals": []},
    )
    body = _get_request_page(ui_client, run_id, request_id)
    assert "Risk signals (activated)" not in body


# ---------------------------------------------------------------------------
# A2 — hard_violation_codes on the OUTPUT anchor (added visibility, not moved)
# ---------------------------------------------------------------------------


def test_hard_violation_codes_on_output_anchor(ui_client):
    run_id, request_id = "run-a2", "req-a2"
    _seed_final_trace(
        run_id,
        request_id,
        {
            "final_action": "REFUSE",
            "risk_score": 0.95,
            "hard_violation_codes": ["H_WEAPON_1"],
            "policy_principle_ids": ["H_WEAPON_1"],
        },
    )
    body = _get_request_page(ui_client, run_id, request_id)
    # §5 #3: added visibility, not moved — the principles-card rendering must
    # still be present, so the code appears at least twice in the body.
    assert body.count("H_WEAPON_1") >= 2
    assert "Hard violation codes" in body


def test_output_card_omits_hard_violation_row_when_empty(ui_client):
    run_id, request_id = "run-a2-empty", "req-a2-empty"
    _seed_final_trace(
        run_id,
        request_id,
        {"final_action": "NORMAL_COMPLETE", "risk_score": 0.1, "hard_violation_codes": []},
    )
    body = _get_request_page(ui_client, run_id, request_id)
    assert "badge-hard" not in body


# ---------------------------------------------------------------------------
# A3 — risk_score falsy-gate fix (0.0 must render)
# ---------------------------------------------------------------------------


def test_risk_score_zero_is_rendered(ui_client):
    run_id, request_id = "run-a3", "req-a3"
    _seed_final_trace(
        run_id,
        request_id,
        {"final_action": "NORMAL_COMPLETE", "risk_score": 0.0},
    )
    body = _get_request_page(ui_client, run_id, request_id)
    assert "Final Risk Score" in body
