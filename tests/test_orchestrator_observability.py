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
