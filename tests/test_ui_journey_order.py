"""
Tests for UI journey ordering: sequence_in_cycle ensures logical order in Deliberation Journey.
"""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi")

from moralstack.ui.app import _journey_sort_key, _journey_steps


def test_journey_steps_orders_by_sequence_in_cycle():
    """_journey_steps sorts by (cycle, sequence_in_cycle, started_at, phase)."""
    # Same cycle, out-of-order sequence_in_cycle: critic (2) persisted before policy (1).
    llm_calls = [
        {"cycle": 1, "sequence_in_cycle": 2, "started_at": 1000, "phase": "critic", "module": "critic"},
        {"cycle": 1, "sequence_in_cycle": 1, "started_at": 500, "phase": "policy_generate", "module": "policy"},
        {"cycle": 1, "sequence_in_cycle": 5, "started_at": 2000, "phase": "hindsight", "module": "hindsight"},
    ]
    steps = _journey_steps(llm_calls)
    assert len(steps) == 3
    assert steps[0]["module"] == "policy"
    assert steps[0]["sequence_in_cycle"] == 1
    assert steps[1]["module"] == "critic"
    assert steps[1]["sequence_in_cycle"] == 2
    assert steps[2]["module"] == "hindsight"
    assert steps[2]["sequence_in_cycle"] == 5


def test_journey_sort_key_puts_missing_sequence_last():
    """Calls without sequence_in_cycle get 999 and sort after sequenced calls in same cycle."""
    key_no_seq = _journey_sort_key({"cycle": 1, "id": 0, "started_at": 100})
    key_policy = _journey_sort_key({"cycle": 1, "sequence_in_cycle": 1, "id": 0, "started_at": 50})
    assert key_policy < key_no_seq  # (1, 1, 0, 50, "") < (1, 999, 0, 100, "")
