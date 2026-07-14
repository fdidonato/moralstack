"""
UI tests for iteration 05: honest delivery-path provenance on the request-detail page.

``_build_delivery_path_summary`` used to tell a proxy-authoritative story
("The proxy finalization event is the authoritative delivered result.") on
*every* request, including direct/SDK-path runs that never went through the
OpenAI-compatible proxy layer (no ``PROXY_*`` orchestration event was ever
recorded). It also collapsed three distinct situations — a real FINAL
decision, a pipeline-failure fallback to the last PRE_POLICY decision, and the
DCCL fast-path structural absence of any pre-delivery row — behind identical
"unknown / unknown" text.

Covers:
  * ``_proxy_participated`` — true/false over the six ``PROXY_*`` event types.
  * ``_infer_engine_internal_source`` — exact-match, latest-match-wins,
    no-match, and empty-response cases.
  * ``_build_delivery_path_summary`` non-proxy vs proxy branches.
  * ``pre_delivery_na`` (S6 DCCL fast-path structural absence) vs the S8
    proxy+failure fallback to the real PRE_POLICY row.
  * The rendered request-detail page: non-proxy explanation/source, the S6
    "n/a" meta-item (never "unknown / unknown"), and the removal of the
    unlabelled duplicate delivered-source span.
"""

from __future__ import annotations

import json

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
    PROXY_FINAL_REVALIDATION_SKIPPED,
    PROXY_OUTPUT_FINALIZED,
    SPECULATIVE_STARTED,
)
from moralstack.ui.app import (  # noqa: E402
    _build_delivery_path_summary,
    _infer_engine_internal_source,
    _proxy_participated,
)
from tests.test_ui_conversation_views import (  # noqa: E402
    _bind_observability_db,
    _make_session_token,
    _reinstall_observability_service_writes,
    _reset_observability_singleton,
)

_NON_PROXY_EXPLANATION = (
    "No PROXY_* orchestration event was recorded for this request — delivery did not go through the "
    "OpenAI-compatible proxy layer (this is a direct/SDK-path run). The last governance decision-trace "
    "row is the authoritative record of what was delivered."
)


@pytest.fixture(autouse=True)
def _isolate_observability() -> None:
    _reinstall_observability_service_writes()
    _reset_observability_singleton()


@pytest.fixture()
def ui_client(tmp_path, monkeypatch):
    dbp = str(tmp_path / "ui_delivery_provenance.db")
    _bind_observability_db(monkeypatch, dbp)
    init_db(dbp)

    from moralstack.ui.app import create_app

    return TestClient(create_app(), follow_redirects=False)


def _final_trace_row(final_action: str = "NORMAL_COMPLETE", path: str = "FAST_PATH") -> dict:
    return {
        "stage": "FINAL",
        "trace_json": json.dumps({"final_action": final_action, "path": path, "total_cycles": 0}),
    }


def _proxy_finalized_event(final_text_source: str, final_action: str = "NORMAL_COMPLETE") -> dict:
    return {
        "event_type": PROXY_OUTPUT_FINALIZED,
        "payload_json": json.dumps({"final_text_source": final_text_source, "final_action": final_action}),
    }


# ---------------------------------------------------------------------------
# (a) _proxy_participated
# ---------------------------------------------------------------------------


def test_proxy_participated_true_for_any_of_the_six_proxy_event_types():
    assert _proxy_participated([{"event_type": PROXY_OUTPUT_FINALIZED}]) is True
    assert _proxy_participated([{"event_type": PROXY_FINAL_REVALIDATION_SKIPPED}]) is True


def test_proxy_participated_false_when_no_proxy_events_recorded():
    assert _proxy_participated([]) is False
    assert _proxy_participated([{"event_type": SPECULATIVE_STARTED}]) is False


# ---------------------------------------------------------------------------
# (b) _infer_engine_internal_source
# ---------------------------------------------------------------------------


def test_infer_engine_internal_source_exact_match_after_stripping():
    llm_calls = [
        {"module": "risk_estimator", "action": "estimate", "raw_response": "other text"},
        {"module": "policy", "action": "generate", "raw_response": "  Hello world  "},
    ]
    assert _infer_engine_internal_source(llm_calls, "Hello world") == "policy/generate"


def test_infer_engine_internal_source_prefers_latest_chronological_match():
    """llm_calls is chronological (read_store ORDER BY); when two calls both match,
    the later one in the list — not the first — is the true source."""
    llm_calls = [
        {"module": "policy", "action": "generate", "raw_response": "same text"},
        {"module": "compliance_layer", "action": "verdict", "raw_response": "same text"},
    ]
    assert _infer_engine_internal_source(llm_calls, "same text") == "compliance_layer/verdict"


def test_infer_engine_internal_source_falls_back_to_phase_when_action_absent():
    llm_calls = [{"module": "policy", "phase": "policy_generate", "raw_response": "hi"}]
    assert _infer_engine_internal_source(llm_calls, "hi") == "policy/policy_generate"


def test_infer_engine_internal_source_no_match_returns_empty_never_guesses():
    llm_calls = [{"module": "policy", "action": "generate", "raw_response": "abc"}]
    assert _infer_engine_internal_source(llm_calls, "xyz") == ""


def test_infer_engine_internal_source_empty_final_response_returns_empty():
    llm_calls = [{"module": "policy", "action": "generate", "raw_response": "abc"}]
    assert _infer_engine_internal_source(llm_calls, "") == ""


# ---------------------------------------------------------------------------
# (c) non-proxy run shape: honest explanation + engine-internal source
# ---------------------------------------------------------------------------


def test_delivery_summary_non_proxy_run_uses_honest_explanation_and_engine_internal_source():
    traces = [_final_trace_row(final_action="NORMAL_COMPLETE", path="FAST_PATH")]
    llm_calls = [{"module": "policy", "action": "generate", "raw_response": "Hello there"}]

    summary = _build_delivery_path_summary(
        orchestration_events=[],
        traces=traces,
        llm_calls=llm_calls,
        final_revalidation_info=None,
        proxy_output_info=None,
        final_response="Hello there",
    )

    assert summary["explanation"] == _NON_PROXY_EXPLANATION
    assert summary["delivered_source"] == "governed (engine-internal, produced by policy/generate)"
    # Pre-delivery fields are untouched: a real FINAL row exists.
    assert summary["pre_delivery_action"] == "NORMAL_COMPLETE"
    assert summary["pre_delivery_path"] == "FAST_PATH"


def test_delivery_summary_non_proxy_run_with_no_match_says_source_undetermined():
    traces = [_final_trace_row()]

    summary = _build_delivery_path_summary(
        orchestration_events=[],
        traces=traces,
        llm_calls=[],
        final_revalidation_info=None,
        proxy_output_info=None,
        final_response="Hi there!",
    )

    assert summary["delivered_source"] == "governed (engine-internal, source undetermined)"


# ---------------------------------------------------------------------------
# (d) proxy run shape: old explanation/source byte-stable
# ---------------------------------------------------------------------------


def test_delivery_summary_proxy_run_keeps_the_old_explanation_and_source():
    traces = [_final_trace_row()]
    events = [_proxy_finalized_event("governed")]

    summary = _build_delivery_path_summary(
        orchestration_events=events,
        traces=traces,
        llm_calls=[],
        final_revalidation_info=None,
        proxy_output_info={"final_text_source": "governed", "final_action": "NORMAL_COMPLETE"},
    )

    assert summary["explanation"] == "The proxy finalization event is the authoritative delivered result."
    assert summary["delivered_source"] == "governed"


# ---------------------------------------------------------------------------
# (e) S8-shape: proxy + no FINAL + PRE_POLICY -> pre-delivery fields from PRE_POLICY
# ---------------------------------------------------------------------------


def test_delivery_summary_s8_shape_pre_delivery_fields_come_from_pre_policy_not_unknown():
    traces = [
        {
            "stage": "PRE_POLICY",
            "trace_json": json.dumps(
                {
                    "final_action": "SAFE_COMPLETE",
                    "path": "DELIBERATIVE_PATH",
                    "winning_rule": "policy_bounds_fallback",
                }
            ),
        }
    ]
    events = [_proxy_finalized_event("governed", final_action="NORMAL_COMPLETE")]

    summary = _build_delivery_path_summary(
        orchestration_events=events,
        traces=traces,
        llm_calls=[],
        final_revalidation_info=None,
        proxy_output_info={"final_text_source": "governed", "final_action": "NORMAL_COMPLETE"},
        pipeline_failure=True,
    )

    assert summary["pre_delivery_action"] == "SAFE_COMPLETE"
    assert summary["pre_delivery_path"] == "DELIBERATIVE_PATH"
    assert summary["pre_delivery_na"] is False
    step_details = " ".join(step["detail"] for step in summary["steps"])
    assert "DELIBERATIVE_PATH chose SAFE_COMPLETE before proxy delivery checks." in step_details


# ---------------------------------------------------------------------------
# (f) S6-shape: DCCL fast-path bypass -> structural n/a, never "unknown / unknown"
# ---------------------------------------------------------------------------


def test_pre_delivery_na_true_for_s6_shape_no_final_no_pre_policy():
    events = [{"event_type": COMPLIANCE_DRAFT_REUSED}, _proxy_finalized_event("governed")]

    summary = _build_delivery_path_summary(
        orchestration_events=events,
        traces=[],
        llm_calls=[],
        final_revalidation_info=None,
        proxy_output_info={"final_text_source": "governed", "final_action": "NORMAL_COMPLETE"},
    )

    assert summary["pre_delivery_na"] is True
    assert summary["pre_delivery_action"] == ""
    assert summary["pre_delivery_path"] == ""


def test_s6_shape_request_page_renders_na_not_unknown_unknown(ui_client):
    run_id, request_id = "run-s6-1", "req-s6-1"
    create_run(run_id, run_type="single", meta={})
    upsert_request(run_id, request_id, prompt="hello", domain="general")
    update_request_meta(run_id, request_id, {"final_action": "NORMAL_COMPLETE", "risk_score": 0.05})
    update_request_response(run_id, request_id, "Hi there!")
    # DCCL fast-path bypass: modules skipped, no PRE_POLICY/FINAL decision-trace row.
    persist_decision_trace(
        run_id=run_id,
        request_id=request_id,
        stage="RELEVANT_PRINCIPLES",
        sequence=1,
        trace_json=json.dumps({"relevant_principle_ids": []}),
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

    token = _make_session_token(ui_client)
    resp = ui_client.get(
        f"/runs/{run_id}/requests/{request_id}",
        cookies={"moralstack_session": token},
    )
    assert resp.status_code == 200, resp.text
    body = resp.text

    assert "n/a — DCCL fast-path bypass (no PRE_POLICY/FINAL row by design)" in body
    assert "unknown / unknown" not in body


# ---------------------------------------------------------------------------
# (g) rendered non-proxy page never shows the unlabelled duplicate source span
# ---------------------------------------------------------------------------


def test_non_proxy_request_page_has_no_unlabelled_duplicate_source_span(ui_client):
    run_id, request_id = "run-nonproxy-1", "req-nonproxy-1"
    create_run(run_id, run_type="single", meta={})
    upsert_request(run_id, request_id, prompt="hello", domain="general")
    update_request_meta(run_id, request_id, {"final_action": "NORMAL_COMPLETE", "risk_score": 0.05})
    update_request_response(run_id, request_id, "Hi there!")
    persist_decision_trace(
        run_id=run_id,
        request_id=request_id,
        stage="FINAL",
        sequence=1,
        trace_json=json.dumps({"final_action": "NORMAL_COMPLETE", "path": "FAST_PATH", "winning_rule": "rule_x"}),
    )
    persist_llm_call(
        run_id=run_id,
        request_id=request_id,
        phase="policy_generate",
        module="policy",
        action="generate",
        raw_response="Hi there!",
    )
    # No orchestration events at all recorded: a direct/SDK-path (non-proxy) run.
    get_obs().flush()

    token = _make_session_token(ui_client)
    resp = ui_client.get(
        f"/runs/{run_id}/requests/{request_id}",
        cookies={"moralstack_session": token},
    )
    assert resp.status_code == 200, resp.text
    body = resp.text

    assert "No PROXY_* orchestration event was recorded for this request" in body
    source = "governed (engine-internal, produced by policy/generate)"
    assert source in body
    # The removed unlabelled duplicate span (bare "mono" class, no label) must be gone;
    # only the labelled "Authoritative final source" meta-item ("meta-value mono") renders it.
    assert f'<span class="mono">{source}</span>' not in body
    assert f'<span class="meta-value mono">{source}</span>' in body
