"""
Regression tests for iteration 04: the DCCL draft-reuse path label must be derived from
persisted orchestration events, not from ``decision_traces.trace_json.path`` (which is
persisted empty on COMPLIANCE_LAYER stage rows for every real reuse delivery).

Covers the two view-builders that previously disagreed with each other and with
``_build_path_badge_info`` on the same request page: ``_execution_summary_from_request``
and ``_build_delivery_path_summary``.
"""

from __future__ import annotations

import json

import pytest

pytest.importorskip("fastapi")

from moralstack.orchestration.orchestration_event_taxonomy import (
    COMPLIANCE_DRAFT_REUSED,
    COMPLIANCE_MATCH_DOWNGRADED,
    PROXY_OUTPUT_FINALIZED,
)
from moralstack.ui.app import (
    _build_delivery_path_summary,
    _dccl_draft_reused,
    _execution_summary_from_request,
)


def _final_trace(path: str = "", final_action: str = "NORMAL_COMPLETE", total_cycles: int = 0) -> dict:
    """A decision_traces FINAL-stage row shaped like real COMPLIANCE_LAYER deliveries:
    ``path`` is persisted empty."""
    return {
        "stage": "FINAL",
        "trace_json": json.dumps(
            {
                "path": path,
                "final_action": final_action,
                "total_cycles": total_cycles,
                "stop_reason": "CONVERGED" if total_cycles else "",
            }
        ),
    }


def _proxy_finalized_event(final_text_source: str, final_action: str = "NORMAL_COMPLETE") -> dict:
    return {
        "event_type": PROXY_OUTPUT_FINALIZED,
        "payload_json": json.dumps({"final_text_source": final_text_source, "final_action": final_action}),
    }


# ---------------------------------------------------------------------------
# (a) S6-shape: genuine DCCL draft-reuse delivery (real-data shape)
# ---------------------------------------------------------------------------


def test_dccl_draft_reused_predicate_true_for_s6_shape():
    events = [{"event_type": COMPLIANCE_DRAFT_REUSED}, _proxy_finalized_event("governed")]
    assert _dccl_draft_reused(events) is True


def test_execution_summary_labels_s6_shape_as_compliance_fast_path():
    """S6 shape: trace_json.path is empty (as persisted on real COMPLIANCE_LAYER rows), but
    COMPLIANCE_DRAFT_REUSED is present -> path_badge must be COMPLIANCE_FAST_PATH, not
    DELIBERATIVE_PATH."""
    traces = [_final_trace(path="")]
    events = [{"event_type": COMPLIANCE_DRAFT_REUSED}, _proxy_finalized_event("governed")]

    summary = _execution_summary_from_request(traces, [], events)

    assert summary["path_badge"] == "COMPLIANCE_FAST_PATH"


def test_delivery_summary_reuse_branch_reachable_on_s6_shape():
    """The reuse-specific status/headline/explanation must trigger for the active
    final_text_source value ("governed"), not just the historical "governed_draft"."""
    traces = [_final_trace(path="")]
    events = [{"event_type": COMPLIANCE_DRAFT_REUSED}, _proxy_finalized_event("governed")]

    summary = _build_delivery_path_summary(
        orchestration_events=events,
        traces=traces,
        llm_calls=[],
        final_revalidation_info=None,
        proxy_output_info={"final_text_source": "governed", "final_action": "NORMAL_COMPLETE"},
    )

    assert summary["status"] == "reused"
    assert "DCCL-validated governed draft" in summary["headline"]
    assert "validated the speculative draft" in summary["explanation"]


# ---------------------------------------------------------------------------
# (b) historical "governed_draft" source must keep triggering the reuse branch
# ---------------------------------------------------------------------------


def test_delivery_summary_reuse_branch_still_reachable_on_historical_governed_draft_source():
    traces = [_final_trace(path="")]
    events = [{"event_type": COMPLIANCE_DRAFT_REUSED}, _proxy_finalized_event("governed_draft")]

    summary = _build_delivery_path_summary(
        orchestration_events=events,
        traces=traces,
        llm_calls=[],
        final_revalidation_info=None,
        proxy_output_info={"final_text_source": "governed_draft", "final_action": "NORMAL_COMPLETE"},
    )

    assert summary["status"] == "reused"


# ---------------------------------------------------------------------------
# (c) S7-shape: MATCH downgraded (no reuse) -> behavior must NOT flip to fast path
# ---------------------------------------------------------------------------


def test_dccl_draft_reused_predicate_false_when_downgraded():
    events = [{"event_type": COMPLIANCE_DRAFT_REUSED}, {"event_type": COMPLIANCE_MATCH_DOWNGRADED}]
    assert _dccl_draft_reused(events) is False


def test_execution_summary_unaffected_by_downgrade_only_events():
    traces = [_final_trace(path="")]
    events = [{"event_type": COMPLIANCE_MATCH_DOWNGRADED}]

    summary = _execution_summary_from_request(traces, [], events)

    assert summary["path_badge"] != "COMPLIANCE_FAST_PATH"
    assert summary["path_badge"] == "DELIBERATIVE_PATH"


def test_delivery_summary_not_reuse_story_when_downgraded():
    traces = [_final_trace(path="")]
    events = [{"event_type": COMPLIANCE_MATCH_DOWNGRADED}, _proxy_finalized_event("governed")]

    summary = _build_delivery_path_summary(
        orchestration_events=events,
        traces=traces,
        llm_calls=[],
        final_revalidation_info=None,
        proxy_output_info={"final_text_source": "governed", "final_action": "NORMAL_COMPLETE"},
    )

    assert summary["status"] != "reused"


# ---------------------------------------------------------------------------
# (d) plain deliberative request (no compliance events) -> byte-identical behavior
# ---------------------------------------------------------------------------


def test_execution_summary_byte_identical_for_plain_deliberative_request():
    traces = [_final_trace(path="", final_action="NORMAL_COMPLETE", total_cycles=2)]
    llm_calls = [{"cycle": 1}, {"cycle": 2}]

    with_no_events = _execution_summary_from_request(traces, llm_calls)
    with_empty_events = _execution_summary_from_request(traces, llm_calls, [])

    assert with_no_events == with_empty_events
    assert with_no_events["path_badge"] == "DELIBERATIVE_PATH"


def test_delivery_summary_byte_identical_for_plain_deliberative_request():
    traces = [_final_trace(path="", final_action="NORMAL_COMPLETE", total_cycles=2)]
    proxy_output_info = {"final_text_source": "governed", "final_action": "NORMAL_COMPLETE"}

    summary = _build_delivery_path_summary(
        orchestration_events=[],
        traces=traces,
        llm_calls=[],
        final_revalidation_info=None,
        proxy_output_info=proxy_output_info,
    )

    assert summary["status"] == "delivered"
    assert summary["explanation"] == "The proxy finalization event is the authoritative delivered result."
