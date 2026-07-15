"""
UI tests for iteration 08: surface a calibration_guard reversal of the raw
operational-risk signal on the request-detail "Path routing and risk
governance" panel.

``build_orchestrator_observability`` (moralstack/reports/orchestrator_observability.py)
only reads debug_events + the FINAL decision trace, both of which post-date
calibration. When a ``calibration_guard`` llm_call capped the raw
``estimate_operational`` risk_policy_action (e.g. DENY -> DELIBERATE), the
panel previously showed only the post-guard value, with no hint that the raw
estimator said something stronger.

Covers:
  * ``_extract_calibration_guard_override`` — guard present and changed
    risk_policy_action, no guard row, guard present but unchanged action,
    and malformed/empty raw_response (never raises).
  * The rendered request-detail page: the reversal note appears when the
    guard fired, and is absent when it did not.
"""

from __future__ import annotations

import json

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402

from moralstack.observability.emit_helpers import (  # noqa: E402
    persist_debug_event,
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
from moralstack.ui.app import _extract_calibration_guard_override  # noqa: E402
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
    dbp = str(tmp_path / "ui_calibration_guard_panel.db")
    _bind_observability_db(monkeypatch, dbp)
    init_db(dbp)

    from moralstack.ui.app import create_app

    return TestClient(create_app(), follow_redirects=False)


def _estimate_operational_call(risk_policy_action: str, risk_score: float, operational_risk: str) -> dict:
    return {
        "module": "risk_estimator",
        "action": "estimate_operational",
        "raw_response": json.dumps(
            {
                "risk_policy_action": risk_policy_action,
                "risk_score": risk_score,
                "operational_risk": operational_risk,
            }
        ),
    }


def _calibration_guard_call(raw_response: str) -> dict:
    return {
        "module": "risk_estimator",
        "action": "calibration_guard",
        "raw_response": raw_response,
    }


# ---------------------------------------------------------------------------
# (a) _extract_calibration_guard_override — unit
# ---------------------------------------------------------------------------


def test_guard_that_changed_risk_policy_action_returns_raw_vs_capped():
    llm_calls = [
        _estimate_operational_call("DENY", 0.75, "HIGH"),
        _calibration_guard_call(
            json.dumps(
                {
                    "caps_applied": {
                        "risk_score_max": 0.45,
                        "operational_risk_max": "MEDIUM",
                        "risk_policy_action_max": "DELIBERATE",
                        "misuse_plausibility_max": 0.5,
                        "actionability_risk_max": 0.5,
                    }
                }
            )
        ),
    ]

    result = _extract_calibration_guard_override(llm_calls)

    assert result == {
        "raw_risk_policy_action": "DENY",
        "capped_risk_policy_action": "DELIBERATE",
        "raw_risk_score": 0.75,
        "capped_risk_score": 0.45,
    }


def test_no_calibration_guard_row_returns_none():
    llm_calls = [_estimate_operational_call("DENY", 0.75, "HIGH")]

    assert _extract_calibration_guard_override(llm_calls) is None


def test_guard_present_but_did_not_change_risk_policy_action_returns_none():
    llm_calls = [
        _estimate_operational_call("DELIBERATE", 0.4, "MEDIUM"),
        _calibration_guard_call(
            json.dumps({"caps_applied": {"risk_score_max": 0.45, "risk_policy_action_max": "DELIBERATE"}})
        ),
    ]

    assert _extract_calibration_guard_override(llm_calls) is None


def test_guard_present_with_missing_risk_policy_action_max_returns_none():
    llm_calls = [
        _estimate_operational_call("DENY", 0.75, "HIGH"),
        _calibration_guard_call(json.dumps({"caps_applied": {"risk_score_max": 0.45}})),
    ]

    assert _extract_calibration_guard_override(llm_calls) is None


def test_malformed_raw_response_on_guard_row_returns_none_and_does_not_raise():
    llm_calls = [
        _estimate_operational_call("DENY", 0.75, "HIGH"),
        _calibration_guard_call("not json"),
    ]

    assert _extract_calibration_guard_override(llm_calls) is None


def test_empty_raw_response_on_guard_row_returns_none():
    llm_calls = [
        _estimate_operational_call("DENY", 0.75, "HIGH"),
        _calibration_guard_call(""),
    ]

    assert _extract_calibration_guard_override(llm_calls) is None


# ---------------------------------------------------------------------------
# (b) rendered request-detail page
# ---------------------------------------------------------------------------

_BRANCH_DEBUG_PAYLOAD = json.dumps(
    {
        "location": "orchestrator.py:process",
        "message": "branch risk_policy vs deliberative",
        "data": {
            "risk_policy_action": "DELIBERATE",
            "risk_score": 0.45,
            "threshold_low": 0.3,
            "decision.path": "DELIBERATIVE_PATH",
        },
        "hypothesisId": "H-test",
    }
)


def test_request_page_renders_calibration_guard_reversal_note(ui_client):
    run_id, request_id = "run-guard-1", "req-guard-1"
    create_run(run_id, run_type="single", meta={})
    upsert_request(run_id, request_id, prompt="hello", domain="general")
    update_request_meta(run_id, request_id, {"final_action": "SAFE_COMPLETE", "risk_score": 0.45})
    update_request_response(run_id, request_id, "Hi there!")
    persist_decision_trace(
        run_id=run_id,
        request_id=request_id,
        stage="FINAL",
        sequence=1,
        trace_json=json.dumps({"final_action": "SAFE_COMPLETE", "path": "DELIBERATIVE_PATH", "total_cycles": 1}),
    )
    persist_debug_event(run_id=run_id, request_id=request_id, payload=json.loads(_BRANCH_DEBUG_PAYLOAD))
    persist_llm_call(
        run_id=run_id,
        request_id=request_id,
        phase="risk_estimate",
        module="risk_estimator",
        action="estimate_operational",
        raw_response=json.dumps({"risk_policy_action": "DENY", "risk_score": 0.75, "operational_risk": "HIGH"}),
    )
    persist_llm_call(
        run_id=run_id,
        request_id=request_id,
        phase="risk_estimate",
        module="risk_estimator",
        action="calibration_guard",
        raw_response=json.dumps({"caps_applied": {"risk_score_max": 0.45, "risk_policy_action_max": "DELIBERATE"}}),
    )
    get_obs().flush()

    token = _make_session_token(ui_client)
    resp = ui_client.get(
        f"/runs/{run_id}/requests/{request_id}",
        cookies={"moralstack_session": token},
    )
    assert resp.status_code == 200, resp.text
    body = resp.text

    assert "Calibration guard reversed the raw operational signal" in body
    assert '<span class="mono">DENY</span>' in body
    assert '<span class="mono">DELIBERATE</span>' in body


def test_request_page_without_calibration_guard_has_no_reversal_note(ui_client):
    run_id, request_id = "run-noguard-1", "req-noguard-1"
    create_run(run_id, run_type="single", meta={})
    upsert_request(run_id, request_id, prompt="hello", domain="general")
    update_request_meta(run_id, request_id, {"final_action": "NORMAL_COMPLETE", "risk_score": 0.1})
    update_request_response(run_id, request_id, "Hi there!")
    persist_decision_trace(
        run_id=run_id,
        request_id=request_id,
        stage="FINAL",
        sequence=1,
        trace_json=json.dumps({"final_action": "NORMAL_COMPLETE", "path": "FAST_PATH", "total_cycles": 0}),
    )
    persist_debug_event(run_id=run_id, request_id=request_id, payload=json.loads(_BRANCH_DEBUG_PAYLOAD))
    persist_llm_call(
        run_id=run_id,
        request_id=request_id,
        phase="risk_estimate",
        module="risk_estimator",
        action="estimate_operational",
        raw_response=json.dumps({"risk_policy_action": "ALLOW", "risk_score": 0.1, "operational_risk": "NONE"}),
    )
    get_obs().flush()

    token = _make_session_token(ui_client)
    resp = ui_client.get(
        f"/runs/{run_id}/requests/{request_id}",
        cookies={"moralstack_session": token},
    )
    assert resp.status_code == 200, resp.text
    body = resp.text

    assert "Calibration guard reversed the raw operational signal" not in body
