"""Regression tests for UI calibration summary alignment with risk calibration.py."""

from __future__ import annotations

from moralstack.ui.app import _describe_calibration_path


def test_ui_counts_q17_in_harmful_count_signals() -> None:
    """q17 must contribute to harm_signals positive count (matches calibration harmful_count)."""
    intent_data = {
        "request_type": "clear_harm",
        "intent_to_harm": "yes",
        "requested_instructions": "yes",
        "intent_operational": True,
    }
    signal_data = {"q17_minor_exploitation": "yes"}
    operational_data = {"operational_risk": "HIGH", "risk_score": 0.9}
    inputs, outputs, raw_json = _describe_calibration_path(intent_data, signal_data, operational_data, 0.9, 0.9)
    harm_row = next(i for i in inputs if i.get("label") == "harm_signals")
    assert "1 positive" in harm_row["source"]
    assert "q17" in harm_row["source"]
    assert "harm_signals_present" in raw_json


def test_ui_reputational_cluster_opens_harm_path_without_harmful_count() -> None:
    """q14–q16 route through harm calibration without incrementing harmful_count."""
    intent_data = {
        "request_type": "factual_query",
        "intent_to_harm": "no",
        "requested_instructions": "no",
        "intent_operational": False,
    }
    signal_data = {"q14_reputational_harm": "yes"}
    operational_data = {"operational_risk": "NONE", "risk_score": 0.35}
    inputs, _, raw_json = _describe_calibration_path(intent_data, signal_data, operational_data, 0.35, 0.35)
    topic_row = next(i for i in inputs if i.get("label") == "topic_signals")
    assert "q14" in topic_row["source"] or "q14–q16" in topic_row["source"]
    harm_row = next(i for i in inputs if i.get("label") == "harm_signals")
    assert "0 positive" in harm_row["source"]
    assert "harm_signals_present" in raw_json
    assert "reputational_cluster" in raw_json


def test_ui_q13_only_does_not_trigger_harm_calibration_path_in_summary() -> None:
    """q13 alone does not open calibration harm_signals_present (matches calibration._apply_calibration)."""
    intent_data = {
        "request_type": "factual_query",
        "intent_to_harm": "no",
        "requested_instructions": "no",
        "intent_operational": False,
    }
    signal_data = {"q13_protected_class_targeting": "yes"}
    operational_data = {"operational_risk": "NONE", "risk_score": 0.2}
    inputs, _, raw_json = _describe_calibration_path(intent_data, signal_data, operational_data, 0.2, 0.2)
    topic_row = next(i for i in inputs if i.get("label") == "topic_signals")
    assert "q13" in topic_row["source"]
    assert "no_harm_routing" in raw_json
