"""NEW-2 in the UI: the ``quick_check`` node sits between the fast draft and delivery (shared helpers:
``test_ui_llm_call_visibility.py``)."""

from __future__ import annotations

import json

import pytest

pytest.importorskip("fastapi")

from moralstack.constitution.schema import Constitution, Principle  # noqa: E402
from moralstack.runtime.modules.critic_module import CriticConfig, LLMConstitutionalCritic  # noqa: E402
from tests.test_llm_call_persistence_quick_check import _QuickPolicy  # noqa: E402
from tests.test_ui_llm_call_visibility import (  # noqa: E402
    T0,
    _call,
    _graph_sequence,
    _idx,
    _render,
    _scope,
    _seed_request,
    isolate_observability_fixture,  # noqa: F401  (autouse fixture, registered as "_isolate_observability")
    ui_client_fixture,  # noqa: F401  (a fixture, registered as "ui_client")
)

# --------------------------------------------------------------------------------------
# NEW-2: the quick_check node sits between the fast draft and delivery
# --------------------------------------------------------------------------------------


def test_quick_check_node_follows_the_fast_draft_in_the_initial_assessment_cycle(ui_client):
    run_id, request_id = "run-ui-qc", "req-ui-qc"
    _seed_request(run_id, request_id, path="FAST_PATH", total_cycles=0)
    for action in ("estimate_intent", "estimate_signals", "estimate_operational"):
        _call(
            run_id,
            request_id,
            cycle=0,
            phase="risk_estimation",
            module="risk_estimator",
            action=action,
            sequence_in_cycle=-9,
            started_at=T0 + 100,
            duration_ms=900,
            raw_response=json.dumps({"risk_score": 0.1}),
        )
    _call(
        run_id,
        request_id,
        cycle=0,
        phase="policy_generate",
        module="policy",
        action="generate (fast_path)",
        sequence_in_cycle=1,
        started_at=T0 + 1_100,
        duration_ms=1_500,
        raw_response="THE FAST DRAFT",
    )
    critic = LLMConstitutionalCritic(policy=_QuickPolicy('{"violated": false}'), config=CriticConfig(max_retries=2))
    constitution = Constitution(
        core_principles=[Principle(id="H1", level="hard", priority=100, title="No harm", rule="Never help harm.")]
    )
    with _scope(run_id, request_id, cycle=None):
        assert critic.quick_check("how do I pick a strong password?", "THE FAST DRAFT", constitution).passed

    body = _render(ui_client, run_id, request_id)
    seq = _graph_sequence(body)

    draft, quick = _idx(seq, "generate (fast_path)"), _idx(seq, "quick_check")
    # the check comes after the draft it checks, in the same (initial-assessment) cycle, and the pipe says so
    assert draft < quick, seq
    assert _idx(seq, "estimate_operational") < draft, seq
    assert seq[quick - 1] == "draft", seq
    assert _idx(seq, "Initial assessment") < draft, seq
    assert not any("Deliberation" in s for s in seq), seq
    # recognizable label and verdict on the node
    assert "quick check passed" in body


def test_quick_check_violation_node_precedes_the_escalated_deliberation_cycle(ui_client):
    run_id, request_id = "run-ui-qc-esc", "req-ui-qc-esc"
    _seed_request(run_id, request_id, path="DELIBERATIVE_PATH", total_cycles=1)
    _call(
        run_id,
        request_id,
        cycle=0,
        phase="policy_generate",
        module="policy",
        action="generate (fast_path)",
        sequence_in_cycle=1,
        started_at=T0 + 100,
        duration_ms=1_000,
        raw_response="THE FAST DRAFT",
    )
    critic = LLMConstitutionalCritic(
        policy=_QuickPolicy('{"violated": true, "principle_id": "H1", "evidence": "x"}'),
        config=CriticConfig(max_retries=2),
    )
    constitution = Constitution(
        core_principles=[Principle(id="H1", level="hard", priority=100, title="No harm", rule="Never help harm.")]
    )
    with _scope(run_id, request_id, cycle=None):
        assert not critic.quick_check("q", "THE FAST DRAFT", constitution).passed
    _call(
        run_id,
        request_id,
        cycle=1,
        phase="critic",
        module="critic",
        action="critique",
        sequence_in_cycle=2,
        started_at=T0 + 3_000,
        duration_ms=1_000,
        parsed_summary_json="Violations: 1, Guidance: x",
    )

    seq = _graph_sequence(_render(ui_client, run_id, request_id))
    draft, quick = _idx(seq, "generate (fast_path)"), _idx(seq, "quick_check")
    deliberation, critique = _idx(seq, "Deliberation"), _idx(seq, "critique")
    assert draft < quick < deliberation < critique, seq


def test_quick_check_without_a_verdict_renders_as_no_verdict_with_the_error_type(ui_client):
    run_id, request_id = "run-ui-qc-nv", "req-ui-qc-nv"
    _seed_request(run_id, request_id, path="DELIBERATIVE_PATH", total_cycles=1)
    critic = LLMConstitutionalCritic(policy=_QuickPolicy("not json"), config=CriticConfig(max_retries=2))
    constitution = Constitution(
        core_principles=[Principle(id="H1", level="hard", priority=100, title="No harm", rule="Never help harm.")]
    )
    with _scope(run_id, request_id, cycle=None), pytest.raises(Exception):
        critic.quick_check("q", "THE FAST DRAFT", constitution)

    body = _render(ui_client, run_id, request_id)
    assert "quick check passed" not in body
    assert "no verdict (JSONParseError)" in body


def test_critic_deferred_marker_keeps_its_cycle0_placement_before_the_fast_draft():
    """The synthetic "critic deferred to compliance" marker is cycle 0 / seq 2 too; only the quick_check ACTION moves
    to the late tier, so the marker keeps the tier it had before the quick_check row existed (before the policy draft)."""
    from moralstack.ui.app import _group_calls_into_tiers_and_enrich, _visual_tier_for_call

    marker = {"module": "critic", "action": "skipped - contract authorized", "cycle": 0, "sequence_in_cycle": 2, "id": 0}
    draft = {"module": "policy", "action": "generate (fast_path)", "cycle": 0, "sequence_in_cycle": 1, "id": 1}
    quick = {"module": "critic", "action": "quick_check", "cycle": 0, "sequence_in_cycle": 2, "id": 2}
    # Relative order only (literal tier numbers are an implementation detail): the marker ranks with the deferred
    # markers (after the DCCL verdict that authorized the rule), before the fast draft, and the quick_check ACTION
    # keeps the last tier, after the draft.
    assert _visual_tier_for_call(marker) < _visual_tier_for_call(draft) < _visual_tier_for_call(quick)
    tiers = _group_calls_into_tiers_and_enrich([quick, draft, marker])
    assert [t[0]["action"] for t in tiers] == ["skipped - contract authorized", "generate (fast_path)", "quick_check"]
