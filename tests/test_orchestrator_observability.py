"""Tests for orchestrator routing observability (display-only, from debug events)."""

import json

from moralstack.reports.orchestrator_observability import (
    build_orchestrator_observability,
    orchestrator_observability_to_io_annotations,
    render_orchestrator_observability_markdown,
)


def _payload(message: str, data: dict, hid: str = "H-test") -> str:
    return json.dumps(
        {
            "location": "orchestrator.py:process",
            "message": message,
            "data": data,
            "hypothesisId": hid,
        }
    )


def test_build_from_branch_and_decision_explanation():
    events = [
        {
            "created_at": 1,
            "payload_json": _payload(
                "DECISION_EXPLANATION",
                {
                    "event": "DECISION_EXPLANATION",
                    "final_action": "NORMAL_COMPLETE",
                    "risk_score": 0.2,
                    "winning_rule": "test_rule",
                    "reason_codes": ["A", "B"],
                    "why_not_refuse": "x",
                    "why_not_safe_complete": "y",
                    "why_not_normal_complete": "z",
                },
            ),
        },
        {
            "created_at": 2,
            "payload_json": _payload(
                "branch risk_policy vs deliberative",
                {
                    "risk_policy_action": "ALLOW",
                    "risk_score": 0.2,
                    "threshold_low": 0.3,
                    "decision.path": "FAST_PATH",
                },
            ),
        },
    ]
    traces = [
        {
            "stage": "FINAL",
            "trace_json": json.dumps(
                {
                    "policy_min_action": "NORMAL_COMPLETE",
                    "policy_max_action": "NORMAL_COMPLETE",
                }
            ),
        }
    ]
    obs = build_orchestrator_observability(events, traces)
    assert obs["has_routing_data"]
    assert obs["routing_signals"]["risk_policy_action"] == "ALLOW"
    assert any("path_router branch" in b for b in obs["narrative_bullets"])
    io = orchestrator_observability_to_io_annotations(obs)
    assert any(o.get("label") == "risk_policy_action" for o in io["outputs"])
    md = render_orchestrator_observability_markdown(obs)
    assert "Path routing and risk governance" in md
    assert "DECISION_EXPLANATION payload" in md


def test_empty_debug_events_uses_final_trace_fallback():
    traces = [
        {
            "stage": "FINAL",
            "trace_json": json.dumps(
                {
                    "why_not_refuse": "low risk",
                    "winning_rule": "wr1",
                    "policy_min_action": "SAFE_COMPLETE",
                    "policy_max_action": "NORMAL_COMPLETE",
                }
            ),
        }
    ]
    obs = build_orchestrator_observability([], traces)
    assert obs["has_routing_data"]
    assert any("Why not REFUSE" in b for b in obs["narrative_bullets"])
    assert obs["trace_bounds"]["policy_min_action"] == "SAFE_COMPLETE"


def test_routing_io_annotations_use_the_decision_explanation_in_force_at_the_branch():
    """The deliberative path logs a second DECISION_EXPLANATION after the cycles
    (e.g. winning_rule=hard_violations, produced by the critic). The routing
    node's inputs describe what the path_router branched on, so they must come
    from the explanation logged before the branch, not the post-deliberation one;
    the last explanation stays available as ``decision_explanation``."""
    events = [
        {
            "created_at": 1,
            "payload_json": _payload(
                "DECISION_EXPLANATION",
                {"final_action": "SAFE_COMPLETE", "winning_rule": "policy_bounds_fallback", "risk_score": 0.35},
            ),
        },
        {
            "created_at": 2,
            "payload_json": _payload(
                "branch risk_policy vs deliberative",
                {
                    "risk_policy_action": "DELIBERATE",
                    "risk_score": 0.35,
                    "threshold_low": 0.3,
                    "decision.path": "DELIBERATIVE_PATH",
                },
            ),
        },
        {"created_at": 3, "payload_json": _payload("taking _deliberative_path", {"path_taken": "deliberative"})},
        {
            "created_at": 4,
            "payload_json": _payload(
                "DECISION_EXPLANATION",
                {"final_action": "SAFE_COMPLETE", "winning_rule": "hard_violations", "risk_score": 0.35},
            ),
        },
    ]
    obs = build_orchestrator_observability(events, [])
    assert obs["decision_explanation"]["winning_rule"] == "hard_violations"
    assert obs["decision_explanation_at_branch"]["winning_rule"] == "policy_bounds_fallback"
    io = orchestrator_observability_to_io_annotations(obs)
    by_label = {i["label"]: i["source"] for i in io["inputs"]}
    assert by_label["winning_rule"] == "policy_bounds_fallback"
    assert by_label["final_action (policy)"] == "SAFE_COMPLETE"


def test_routing_io_annotations_fall_back_to_last_explanation_without_branch_event():
    events = [
        {
            "created_at": 1,
            "payload_json": _payload("DECISION_EXPLANATION", {"final_action": "REFUSE", "winning_rule": "hard_refuse"}),
        },
        {"created_at": 2, "payload_json": _payload("early return REFUSE", {"decision.path": "REFUSE_PATH"})},
    ]
    obs = build_orchestrator_observability(events, [])
    assert obs["decision_explanation_at_branch"] is None
    io = orchestrator_observability_to_io_annotations(obs)
    assert {i["label"]: i["source"] for i in io["inputs"]}["winning_rule"] == "hard_refuse"
