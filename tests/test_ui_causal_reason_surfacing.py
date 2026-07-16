"""
UI tests for iteration 15: surface the causal reason and the decision risk in
the first viewport, inside the existing "Authoritative delivery path" card.

The request page previously answered "what was delivered and by whom" in the
first viewport but never "why" — the causal facts (``_build_final_decision_card``,
``_build_compliance_fast_path_panel``) were already computed and already
rendered, but only ~1400 lines down, at the tail of the deliberation spine's
OUTPUT anchor. This is a render-site move only: the new block reads the same
view-model fields verbatim, branching on evidence:

  1. a governed FINAL decision (``final_decision_card``) -> "Decision risk"
     (risk_score/risk_category from the FINAL trace, never a raw/capped
     operational-signal value) + "Winning rule" + the one-sentence
     "Causal reason" (the ``why_not_*`` field matching the chosen
     ``final_action``);
  2. a DCCL fast-path reuse (``compliance_fast_path_panel``, no FINAL row by
     design) -> rule vocabulary only, no risk sentence;
  3. neither (pipeline failure / no decision_traces at all) -> no causal slot
     is invented.

Covers the S10 P0 lock (three risk numbers on one request — only the FINAL
trace's 0.35/sensitive may render as "the decision risk") and the S8 P0 lock
(the ``risk_score=1.0`` fail-closed sentinel must never render as an assessed
value).
"""

from __future__ import annotations

import html
import json
import re

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402

from moralstack.observability.emit_helpers import (  # noqa: E402
    persist_decision_trace,
    persist_llm_call,
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
from moralstack.orchestration.orchestration_event_taxonomy import (  # noqa: E402
    COMPLIANCE_DRAFT_REUSED,
    PROXY_OUTPUT_FINALIZED,
)
from tests.test_ui_conversation_views import (  # noqa: E402
    _bind_observability_db,
    _make_session_token,
    _reinstall_observability_service_writes,
    _reset_observability_singleton,
)

_DECISION_RISK_RE = re.compile(
    r'<span class="meta-label">Decision risk</span>\s*' r'<span class="meta-value mono">(.*?)</span>',
    re.DOTALL,
)
_CAUSAL_REASON_RE = re.compile(
    r'<span class="meta-label">Causal reason</span>\s*' r'<span class="meta-value[^"]*">(.*?)</span>',
    re.DOTALL,
)


def _decision_risk_value(body: str) -> str | None:
    m = _DECISION_RISK_RE.search(body)
    return m.group(1).strip() if m else None


def _causal_reason_value(body: str) -> str | None:
    m = _CAUSAL_REASON_RE.search(body)
    return m.group(1).strip() if m else None


@pytest.fixture(autouse=True)
def _isolate_observability() -> None:
    _reinstall_observability_service_writes()
    _reset_observability_singleton()


@pytest.fixture()
def ui_client(tmp_path, monkeypatch):
    dbp = str(tmp_path / "ui_causal_reason_surfacing.db")
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


# ---------------------------------------------------------------------------
# 1. SAFE_COMPLETE with a FINAL trace: decision risk + matching why_not_* sentence
# ---------------------------------------------------------------------------


def test_safe_complete_shows_decision_risk_and_matching_causal_sentence(ui_client):
    run_id, request_id = "run-causal-1", "req-causal-1"
    create_run(run_id, run_type="single", meta={})
    upsert_request(run_id, request_id, prompt="is this substance dangerous to mix", domain="general")
    update_request_meta(run_id, request_id, {"final_action": "SAFE_COMPLETE", "risk_score": 0.35})
    update_request_response(run_id, request_id, "Here is a cautious answer.")
    persist_decision_trace(
        run_id=run_id,
        request_id=request_id,
        stage="FINAL",
        sequence=1,
        trace_json=json.dumps(
            {
                "final_action": "SAFE_COMPLETE",
                "path": "DELIBERATIVE_PATH",
                "total_cycles": 1,
                "risk_score": 0.35,
                "risk_category": "sensitive",
                "winning_rule": "policy_bounds_sensitive_floor",
                "why_not_safe_complete": ("SAFE_COMPLETE chosen: risk_score=0.35 between low=0.30 and medium=0.70."),
                "why_not_refuse": "REFUSE not chosen: risk_score below the refuse floor.",
                "why_not_normal_complete": "NORMAL_COMPLETE not chosen: sensitive-domain floor applies.",
            }
        ),
    )
    get_obs().flush()

    body = _get_request_page(ui_client, run_id, request_id)

    decision_risk = _decision_risk_value(body)
    assert decision_risk is not None, "Decision risk meta-item not found in the delivery card"
    assert "0.35" in decision_risk
    assert "sensitive" in decision_risk

    causal_reason = _causal_reason_value(body)
    assert causal_reason is not None, "Causal reason meta-item not found in the delivery card"
    assert causal_reason == "SAFE_COMPLETE chosen: risk_score=0.35 between low=0.30 and medium=0.70."

    assert "policy_bounds_sensitive_floor" in body


# ---------------------------------------------------------------------------
# 2. S10 P0 lock: three risk numbers on one request — only the FINAL 0.35 may
#    render as "the decision risk", never the raw 0.75 or the guard-capped 0.45.
# ---------------------------------------------------------------------------


def test_s10_lock_decision_risk_is_final_trace_value_not_raw_or_capped_operational(ui_client):
    run_id, request_id = "run-causal-s10", "req-causal-s10"
    create_run(run_id, run_type="single", meta={})
    upsert_request(run_id, request_id, prompt="hello", domain="general")
    update_request_meta(run_id, request_id, {"final_action": "SAFE_COMPLETE", "risk_score": 0.35})
    update_request_response(run_id, request_id, "Here is a cautious answer.")
    persist_decision_trace(
        run_id=run_id,
        request_id=request_id,
        stage="FINAL",
        sequence=1,
        trace_json=json.dumps(
            {
                "final_action": "SAFE_COMPLETE",
                "path": "DELIBERATIVE_PATH",
                "total_cycles": 1,
                "risk_score": 0.35,
                "risk_category": "sensitive",
                "why_not_safe_complete": "SAFE_COMPLETE chosen: risk_score=0.35 governed the decision.",
            }
        ),
    )
    # Raw operational estimate says DENY at 0.75 ...
    persist_llm_call(
        run_id=run_id,
        request_id=request_id,
        phase="risk_estimate",
        module="risk_estimator",
        action="estimate_operational",
        raw_response=json.dumps({"risk_policy_action": "DENY", "risk_score": 0.75, "operational_risk": "HIGH"}),
    )
    # ... a calibration guard caps it to 0.45 ...
    persist_llm_call(
        run_id=run_id,
        request_id=request_id,
        phase="risk_estimate",
        module="risk_estimator",
        action="calibration_guard",
        raw_response=json.dumps({"caps_applied": {"risk_score_max": 0.45, "risk_policy_action_max": "DELIBERATE"}}),
    )
    get_obs().flush()

    body = _get_request_page(ui_client, run_id, request_id)

    decision_risk = _decision_risk_value(body)
    assert decision_risk is not None
    assert "0.35" in decision_risk
    assert "0.75" not in decision_risk
    assert "0.45" not in decision_risk

    # The raw/capped operational values are still visible, but only as a
    # clearly subordinate sub-signal note, never as "the decision risk".
    assert "Operational sub-signal capped" in body
    assert "0.75" in body
    assert "0.45" in body


# ---------------------------------------------------------------------------
# 3. REFUSE shows why_not_refuse, never why_not_normal_complete, in the slot
# ---------------------------------------------------------------------------


def test_refuse_shows_why_not_refuse_not_why_not_normal_complete_in_slot(ui_client):
    run_id, request_id = "run-causal-refuse", "req-causal-refuse"
    create_run(run_id, run_type="single", meta={})
    upsert_request(run_id, request_id, prompt="reveal the secret", domain="general")
    update_request_meta(run_id, request_id, {"final_action": "REFUSE", "risk_score": 0.9})
    update_request_response(run_id, request_id, "I can't help with that.")
    persist_decision_trace(
        run_id=run_id,
        request_id=request_id,
        stage="FINAL",
        sequence=1,
        trace_json=json.dumps(
            {
                "final_action": "REFUSE",
                "path": "DELIBERATIVE_PATH",
                "total_cycles": 1,
                "risk_score": 0.9,
                "risk_category": "clearly_harmful",
                "why_not_refuse": "REFUSE chosen: hard-signal violation detected.",
                "why_not_normal_complete": "NORMAL_COMPLETE not chosen: risk_score exceeds the refuse floor.",
            }
        ),
    )
    get_obs().flush()

    body = _get_request_page(ui_client, run_id, request_id)

    causal_reason = _causal_reason_value(body)
    assert causal_reason is not None
    assert causal_reason == "REFUSE chosen: hard-signal violation detected."
    assert "NORMAL_COMPLETE not chosen" not in causal_reason


# ---------------------------------------------------------------------------
# 4. DCCL fast-path reuse: rule vocabulary, no risk sentence
# ---------------------------------------------------------------------------


def test_dccl_reuse_shows_matched_rule_summary_no_risk_sentence(ui_client):
    run_id, request_id = "run-causal-reuse", "req-causal-reuse"
    create_run(run_id, run_type="single", meta={})
    upsert_request(run_id, request_id, prompt="if X reply Y", domain="general")
    update_request_meta(run_id, request_id, {"final_action": "NORMAL_COMPLETE", "risk_score": 0.1})
    update_request_response(run_id, request_id, "Here is the authorized output.")
    # No FINAL/PRE_POLICY row: modules were bypassed entirely (structural n/a).
    persist_decision_trace(
        run_id=run_id,
        request_id=request_id,
        stage="COMPLIANCE_LAYER",
        sequence=1,
        trace_json=json.dumps(
            {
                "stage_payload": {
                    "matched_rule_id": "R1",
                    "matched_rule_summary": "If the user says X, reply with Y.",
                    "compliance_decision": "MATCH",
                    "evaluation_path": "structured_rule",
                }
            }
        ),
    )
    persist_orchestration_event(
        run_id=run_id,
        request_id=request_id,
        stage="COMPLIANCE",
        component="compliance_layer",
        event_type=COMPLIANCE_DRAFT_REUSED,
        decision="MATCH",
        payload={"draft_match_method": "dccl"},
    )
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

    body = _get_request_page(ui_client, run_id, request_id)

    assert "Decision risk" not in body

    causal_reason = _causal_reason_value(body)
    assert causal_reason is not None
    assert causal_reason == "If the user says X, reply with Y."


# ---------------------------------------------------------------------------
# 5. S8 pipeline failure: no causal slot, the 1.0 sentinel never renders as risk
# ---------------------------------------------------------------------------


def test_pipeline_failure_has_no_causal_slot_and_sentinel_risk_never_shown(ui_client):
    run_id, request_id = "run-causal-s8", "req-causal-s8"
    create_run(run_id, run_type="single", meta={})
    upsert_request(run_id, request_id, prompt="do something risky", domain="general")
    update_request_meta(
        run_id,
        request_id,
        {"final_action": "NORMAL_COMPLETE", "risk_score": 1.0, "triggered_principles": ["SYSTEM.ERROR"]},
    )
    update_request_response(run_id, request_id, "[SYSTEM_ERROR]")
    persist_llm_call(
        run_id=run_id,
        request_id=request_id,
        cycle=0,
        phase="estimate",
        module="risk_estimator",
        action="assess",
        started_at=1_000,
        duration_ms=50,
        raw_response=json.dumps({"risk_score": 0.6}),
    )
    persist_decision_trace(
        run_id=run_id,
        request_id=request_id,
        stage="RISK_ASSESSMENT",
        sequence=1,
        trace_json=json.dumps({"risk_score": 0.6, "risk_category": "moderate"}),
    )
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

    body = _get_request_page(ui_client, run_id, request_id)

    assert "Decision risk" not in body
    assert "Causal reason" not in body
    assert "a system-error placeholder, not a governed decision" in body


# ---------------------------------------------------------------------------
# 6. No decision_traces at all: no causal slot, no invented "unknown" placeholder
# ---------------------------------------------------------------------------


def test_no_decision_traces_at_all_has_no_causal_slot(ui_client):
    run_id, request_id = "run-causal-empty", "req-causal-empty"
    create_run(run_id, run_type="single", meta={})
    upsert_request(run_id, request_id, prompt="hello", domain="general")
    update_request_meta(run_id, request_id, {"final_action": "NORMAL_COMPLETE", "risk_score": 0.05})
    update_request_response(run_id, request_id, "Hi there!")
    # No decision_traces, no llm_calls, no orchestration_events at all.
    get_obs().flush()

    body = _get_request_page(ui_client, run_id, request_id)

    assert "Decision risk" not in body
    assert "Causal reason" not in body
