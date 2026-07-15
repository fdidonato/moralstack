"""
UI tests for iteration 13: the request-page "Execution graph" redesigned as a
single linear vertical spine from an explicit INPUT anchor to an explicit
OUTPUT anchor (replacing the "By cycle" / "Execution order" toggle and the
flat chronological view).

Covers:
  * The INPUT anchor: prompt preview always renders; the developer-contract
    chip and the conversation-history chip are truthful — present only when
    the underlying data (``final_revalidation_info.developer_contract_present``,
    ``conversation_context.turn_index``/``turn_count``) actually says so.
  * The OUTPUT anchor: the delivered final_action renders for a normal
    delivery; for a pipeline-failure-shaped request the anchor reads as a
    FAILURE and the coerced ``delivered_action`` (e.g. a stray
    ``NORMAL_COMPLETE`` code from the proxy) is never coloured/labelled as a
    governed success (invariants from iterations 01/06/12).
  * The toggle buttons and the ``#view-chronological`` flat view are gone.
  * A parallel tier (simulator + perspectives, the real architectural pair —
    see ``_SEQ_TO_VISUAL_TIER``) still renders its "parallel" label, and a
    module box still exposes its expandable detail (Parsed Summary) —
    reachability of per-module detail is preserved.
"""

from __future__ import annotations

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
    PROXY_FINAL_REVALIDATION_PASSED,
    PROXY_OUTPUT_FINALIZED,
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
    dbp = str(tmp_path / "ui_deliberation_spine.db")
    _bind_observability_db(monkeypatch, dbp)
    init_db(dbp)

    from moralstack.ui.app import create_app

    return TestClient(create_app(), follow_redirects=False)


def _seed_basic_calls(run_id: str, request_id: str) -> None:
    """One cycle-0 call plus a cycle-1 parallel tier (simulator + perspectives,
    the architecturally real parallel pair — see ``_SEQ_TO_VISUAL_TIER``), so
    ``flow_data_cycles`` is populated and includes a genuine parallel tier."""
    persist_llm_call(
        run_id=run_id,
        request_id=request_id,
        cycle=0,
        phase="estimate",
        module="risk_estimator",
        action="assess",
        started_at=1_000,
        duration_ms=50,
        raw_response=json.dumps({"risk_score": 0.2}),
    )
    persist_llm_call(
        run_id=run_id,
        request_id=request_id,
        cycle=1,
        phase="simulate",
        module="simulator",
        action="simulate",
        sequence_in_cycle=3,
        started_at=2_000,
        duration_ms=80,
        raw_response=json.dumps({"semantic_expected_harm": "low"}),
        parsed_summary_json=json.dumps({"semantic_expected_harm": "low"}),
    )
    persist_llm_call(
        run_id=run_id,
        request_id=request_id,
        cycle=1,
        phase="perspectives",
        module="perspectives",
        action="review",
        sequence_in_cycle=4,
        started_at=2_010,
        duration_ms=70,
        raw_response=json.dumps({"summary": "no concerns"}),
    )


def _seed_final_trace(run_id: str, request_id: str) -> None:
    persist_decision_trace(
        run_id=run_id,
        request_id=request_id,
        stage="FINAL",
        sequence=5,
        trace_json=json.dumps(
            {
                "final_action": "NORMAL_COMPLETE",
                "path": "DELIBERATIVE_PATH",
                "total_cycles": 2,
                "stop_reason": "converged",
            }
        ),
    )


# ---------------------------------------------------------------------------
# INPUT anchor
# ---------------------------------------------------------------------------


def test_input_anchor_renders_prompt_preview_with_no_chips_when_absent(ui_client):
    run_id, request_id = "run-spine-1", "req-spine-1"
    create_run(run_id, run_type="single", meta={})
    upsert_request(run_id, request_id, prompt="Please summarize the attached report.", domain="general")
    update_request_meta(run_id, request_id, {"final_action": "NORMAL_COMPLETE", "risk_score": 0.1})
    update_request_response(run_id, request_id, "Here is the summary.")
    _seed_basic_calls(run_id, request_id)
    _seed_final_trace(run_id, request_id)
    get_obs().flush()

    token = _make_session_token(ui_client)
    resp = ui_client.get(
        f"/runs/{run_id}/requests/{request_id}",
        cookies={"moralstack_session": token},
    )
    assert resp.status_code == 200, resp.text
    body = resp.text

    assert "INPUT &middot; request" in body
    assert "Please summarize the attached report." in body
    # No developer contract and no conversation: neither chip is fabricated.
    assert 'title="A developer contract constrains this request"' not in body
    assert "conversation history" not in body


def test_input_anchor_shows_developer_contract_and_conversation_history_chips(ui_client):
    run_id, conv_id = "run-spine-2", "conv-spine-2"
    request_id = "req-spine-2-c"
    create_run(run_id, run_type="single", meta={})
    upsert_request(run_id, "req-spine-2-a", prompt="turn 0", domain="general", conversation_id=conv_id, turn_index=0)
    upsert_request(
        run_id,
        "req-spine-2-b",
        prompt="turn 1",
        domain="general",
        conversation_id=conv_id,
        turn_index=1,
        parent_request_id="req-spine-2-a",
    )
    upsert_request(
        run_id,
        request_id,
        prompt="turn 2, please continue",
        domain="general",
        conversation_id=conv_id,
        turn_index=2,
        parent_request_id="req-spine-2-b",
    )
    update_request_meta(run_id, request_id, {"final_action": "NORMAL_COMPLETE", "risk_score": 0.1})
    update_request_response(run_id, request_id, "Continuing as requested.")
    _seed_basic_calls(run_id, request_id)
    _seed_final_trace(run_id, request_id)
    persist_orchestration_event(
        run_id=run_id,
        request_id=request_id,
        stage="PROXY",
        component="proxy",
        event_type=PROXY_FINAL_REVALIDATION_PASSED,
        decision="passed",
        payload={
            "developer_contract_present": True,
            "final_text_source_original": "governed",
            "final_text_source_after_revalidation": "governed",
            "violated_hard": False,
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

    assert 'title="A developer contract constrains this request"' in body
    assert "conversation history &middot; 2 prior turns" in body


# ---------------------------------------------------------------------------
# OUTPUT anchor
# ---------------------------------------------------------------------------


def test_output_anchor_normal_delivery_shows_final_action_no_failure_framing(ui_client):
    run_id, request_id = "run-spine-3", "req-spine-3"
    create_run(run_id, run_type="single", meta={})
    upsert_request(run_id, request_id, prompt="hello", domain="general")
    update_request_meta(run_id, request_id, {"final_action": "NORMAL_COMPLETE", "risk_score": 0.1})
    update_request_response(run_id, request_id, "Hi there!")
    _seed_basic_calls(run_id, request_id)
    _seed_final_trace(run_id, request_id)
    get_obs().flush()

    token = _make_session_token(ui_client)
    resp = ui_client.get(
        f"/runs/{run_id}/requests/{request_id}",
        cookies={"moralstack_session": token},
    )
    assert resp.status_code == 200, resp.text
    body = resp.text

    assert "OUTPUT &middot; delivered answer" in body
    assert "Final Decision" in body
    assert "NORMAL_COMPLETE" in body
    assert "flow-node--output-failure" not in body
    assert "PIPELINE FAILURE" not in body

    # Invariant: a parallel tier still renders the "parallel" label.
    assert "flow-tier--parallel" in body
    assert "parallel &middot;" in body
    # Reachability preserved: a module box still exposes its expandable detail.
    assert "Parsed Summary" in body


def test_output_anchor_pipeline_failure_shows_failure_not_governed_success(ui_client):
    run_id, request_id = "run-spine-4", "req-spine-4"
    create_run(run_id, run_type="single", meta={})
    upsert_request(run_id, request_id, prompt="do something risky", domain="general")
    update_request_meta(
        run_id,
        request_id,
        {"final_action": "NORMAL_COMPLETE", "risk_score": 1.0, "triggered_principles": ["SYSTEM.ERROR"]},
    )
    update_request_response(run_id, request_id, "[SYSTEM_ERROR]")
    # A risk-assessment call happened before the crash — enough to populate
    # flow_data_cycles even though the pipeline never reached a FINAL decision.
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
    # The proxy still finalizes delivery with a coerced NORMAL_COMPLETE code —
    # this must never read as a governed success on the OUTPUT anchor.
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

    token = _make_session_token(ui_client)
    resp = ui_client.get(
        f"/runs/{run_id}/requests/{request_id}",
        cookies={"moralstack_session": token},
    )
    assert resp.status_code == 200, resp.text
    body = resp.text

    assert "flow-node--output-failure" in body
    assert "flow-output-summary--failure" in body
    assert "a system-error placeholder, not a governed decision" in body

    # The coerced NORMAL_COMPLETE delivered_action must never be coloured or
    # labelled as a governed success in the delivered-response summary.
    match = re.search(r'<div class="flow-output-summary[^"]*">(.*?)</div>', body, re.DOTALL)
    assert match, "flow-output-summary block not found in the rendered page"
    summary_html = match.group(1)
    assert "NORMAL_COMPLETE" in summary_html
    assert "badge-fail" in summary_html
    assert "badge-ok" not in summary_html


# ---------------------------------------------------------------------------
# Toggle / chronological view removal
# ---------------------------------------------------------------------------


def test_toggle_and_chronological_view_are_gone(ui_client):
    run_id, request_id = "run-spine-5", "req-spine-5"
    create_run(run_id, run_type="single", meta={})
    upsert_request(run_id, request_id, prompt="hello", domain="general")
    update_request_meta(run_id, request_id, {"final_action": "NORMAL_COMPLETE", "risk_score": 0.1})
    update_request_response(run_id, request_id, "Hi there!")
    _seed_basic_calls(run_id, request_id)
    _seed_final_trace(run_id, request_id)
    get_obs().flush()

    token = _make_session_token(ui_client)
    resp = ui_client.get(
        f"/runs/{run_id}/requests/{request_id}",
        cookies={"moralstack_session": token},
    )
    assert resp.status_code == 200, resp.text
    body = resp.text

    assert 'id="view-chronological"' not in body
    assert 'data-view="chronological"' not in body
    assert 'data-view="by-cycle"' not in body


def test_node_headers_are_keyboard_operable(ui_client):
    """The removed chronological view was the keyboard path to the raw per-call
    audit evidence; the surviving node headers must be server-rendered focusable
    and operable (tabindex/role/aria-expanded) so that evidence stays reachable
    without a mouse."""
    run_id, request_id = "run-spine-kbd", "req-spine-kbd"
    create_run(run_id, run_type="single", meta={})
    upsert_request(run_id, request_id, prompt="hello", domain="general")
    update_request_meta(run_id, request_id, {"final_action": "NORMAL_COMPLETE", "risk_score": 0.1})
    update_request_response(run_id, request_id, "Hi there!")
    _seed_basic_calls(run_id, request_id)
    _seed_final_trace(run_id, request_id)
    get_obs().flush()

    token = _make_session_token(ui_client)
    resp = ui_client.get(
        f"/runs/{run_id}/requests/{request_id}",
        cookies={"moralstack_session": token},
    )
    assert resp.status_code == 200, resp.text
    body = resp.text

    # Every node header carries the keyboard affordances, and there is no bare
    # (mouse-only) header left behind.
    assert '<div class="flow-node-header">' not in body
    assert body.count('class="flow-node-header" tabindex="0" role="button" aria-expanded="false"') >= 2
    # The Enter/Space keydown handler is wired.
    assert "addEventListener('keydown'" in body


def test_speculative_node_hides_result_text_in_compact_box(ui_client):
    """A speculative draft is not the delivered answer: its content must not
    render in the compact (collapsed) box preview pill, only behind expand —
    unlike a governed module result, which still shows its pill."""
    run_id, request_id = "run-spine-spec", "req-spine-spec"
    create_run(run_id, run_type="single", meta={})
    upsert_request(run_id, request_id, prompt="hello", domain="general")
    update_request_meta(run_id, request_id, {"final_action": "NORMAL_COMPLETE", "risk_score": 0.1})
    update_request_response(run_id, request_id, "Hi there!")
    # Speculative draft node: its parsed_summary is context-envelope internals that
    # would otherwise leak into the compact box.
    persist_llm_call(
        run_id=run_id,
        request_id=request_id,
        cycle=0,
        phase="speculative_generate",
        module="policy",
        action="generate",
        started_at=1_000,
        duration_ms=40,
        raw_response="SPECULATIVE_DRAFT_TEXT_BODY",
        parsed_summary_json=json.dumps({"context_shape": {"module": "speculative_generate"}}),
    )
    # A governed module result that SHOULD still show its pill.
    persist_llm_call(
        run_id=run_id,
        request_id=request_id,
        cycle=1,
        phase="simulate",
        module="simulator",
        action="simulate",
        started_at=2_000,
        duration_ms=80,
        raw_response=json.dumps({"semantic_expected_harm": "low"}),
        parsed_summary_json=json.dumps({"semantic_expected_harm": "low"}),
    )
    _seed_final_trace(run_id, request_id)
    get_obs().flush()

    token = _make_session_token(ui_client)
    resp = ui_client.get(
        f"/runs/{run_id}/requests/{request_id}",
        cookies={"moralstack_session": token},
    )
    assert resp.status_code == 200, resp.text
    body = resp.text

    # The speculative node's envelope preview is NOT rendered as a compact pill.
    assert '<div class="flow-node-pill">context_shape' not in body
    # A governed module still shows its pill (feature not globally disabled).
    assert '<div class="flow-node-pill">' in body
    # The full speculative draft stays reachable via the node's expand body.
    assert "SPECULATIVE_DRAFT_TEXT_BODY" in body
