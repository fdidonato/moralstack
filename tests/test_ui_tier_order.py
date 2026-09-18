"""
Tests for UI tier grouping and DCCL path badge helpers.
"""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi")

from moralstack.orchestration.deliberation_runner import (
    HARD_VIOLATION_REGENERATION_ACTION,
    HARD_VIOLATION_REVALIDATION_ACTION,
    SEQ_CRITIC,
    SEQ_HARD_VIOLATION_REGENERATION,
    SEQ_HARD_VIOLATION_REVALIDATION,
    SEQ_PERSPECTIVES,
    SEQ_POLICY,
    SEQ_SIMULATOR,
)
from moralstack.orchestration.orchestration_event_taxonomy import (
    COMPLIANCE_DRAFT_REGENERATED,
    COMPLIANCE_DRAFT_REUSED,
    COMPLIANCE_MATCH_DOWNGRADED,
    CRITIC_SKIPPED,
    EARLY_CONVERGENCE_ACCEPTED,
    LEDGER_FAST_PATH_APPLIED,
    LEDGER_FAST_PATH_NOT_APPLIED,
    MODULE_DEFERRED_TO_COMPLIANCE,
    PROXY_FINAL_REVALIDATION_BLOCKED,
    PROXY_FINAL_REVALIDATION_PASSED,
    PROXY_FINAL_REVALIDATION_STARTED,
    PROXY_OUTPUT_FINALIZED,
    SIMULATOR_SKIPPED,
)
from moralstack.ui.app import (
    _SEQ_SYNTHETIC_CALIBRATION,
    _SEQ_SYNTHETIC_PATH_ROUTING,
    _build_final_revalidation_info,
    _build_path_badge_info,
    _build_proxy_output_info,
    _compute_connector_labels,
    _group_calls_into_tiers_and_enrich,
    _journey_sort_key,
    _rehome_legacy_hard_violation_guard_calls,
    _synthetic_compliance_downgrade_nodes,
    _synthetic_convergence_node,
    _synthetic_final_revalidation_call_from_events,
    _synthetic_ledger_fast_path_node,
    _synthetic_module_deferred_nodes,
    _synthetic_module_skipped_nodes,
    _tag_constitution_phases,
)


def test_cycle0_tiers_ordered_by_sequence_not_started_at():
    """Cycle 0 DCCL pipeline: constitution → risk → calibration → compliance → policy."""
    calls = [
        {"id": 5, "cycle": 0, "sequence_in_cycle": 1, "started_at": 9000, "module": "policy", "phase": "regen"},
        {
            "id": 4,
            "cycle": 0,
            "sequence_in_cycle": -5,
            "started_at": 8000,
            "module": "compliance_layer",
            "phase": "evaluate",
        },
        {"id": 3, "cycle": 0, "sequence_in_cycle": -8, "started_at": 7000, "module": "risk_estimator", "phase": "calibrate"},
        {"id": 2, "cycle": 0, "sequence_in_cycle": -9, "started_at": 6000, "module": "risk_estimator", "phase": "intent"},
        {"id": 1, "cycle": 0, "sequence_in_cycle": -10, "started_at": 5000, "module": "constitution", "phase": "prefilter"},
    ]
    tiers = _group_calls_into_tiers_and_enrich(calls)
    flat = [c["module"] for tier in tiers for c in tier]
    assert flat == [
        "constitution",
        "risk_estimator",
        "risk_estimator",
        "compliance_layer",
        "policy",
    ]


def test_cycle0_risk_routing_and_deliberation_retrieval_in_separate_tiers():
    """Two domain_prefilter calls must not share a visual tier (seq -10 vs -1)."""
    calls = [
        {"id": 3, "cycle": 0, "sequence_in_cycle": 1, "started_at": 9000, "module": "policy", "phase": "generate"},
        {
            "id": 2,
            "cycle": 0,
            "sequence_in_cycle": -1,
            "started_at": 8000,
            "module": "constitution_retriever",
            "action": "domain_prefilter",
            "phase": "constitution_retrieval",
        },
        {
            "id": 1,
            "cycle": 0,
            "sequence_in_cycle": -10,
            "started_at": 5000,
            "module": "constitution_retriever",
            "action": "domain_prefilter",
            "phase": "constitution_retrieval",
        },
    ]
    tiers = _group_calls_into_tiers_and_enrich(calls)
    tier_sizes = [len(t) for t in tiers]
    assert tier_sizes == [1, 1, 1]
    assert tiers[0][0]["sequence_in_cycle"] == -10
    assert tiers[1][0]["sequence_in_cycle"] == -1
    assert tiers[2][0]["module"] == "policy"


def test_tag_constitution_phases_from_retrieval_phase_metadata():
    calls = [
        {
            "module": "constitution_retriever",
            "action": "domain_prefilter",
            "parsed_summary_json": '{"retrieval_phase": "deliberation_retrieval"}',
        },
        {
            "module": "constitution_retriever",
            "action": "domain_prefilter",
            "parsed_summary_json": '{"retrieval_phase": "risk_routing"}',
        },
    ]
    _tag_constitution_phases(calls)
    by_phase = {c["parsed_summary_json"]: c["_constitution_phase"] for c in calls}
    assert "deliberation retrieval" in by_phase['{"retrieval_phase": "deliberation_retrieval"}']
    assert "risk routing" in by_phase['{"retrieval_phase": "risk_routing"}']


def test_deliberation_parallel_simulator_perspectives_same_tier():
    calls = [
        {"id": 3, "cycle": 1, "sequence_in_cycle": 4, "started_at": 3000, "module": "perspectives", "phase": "p"},
        {"id": 2, "cycle": 1, "sequence_in_cycle": 3, "started_at": 2000, "module": "simulator", "phase": "s"},
        {"id": 1, "cycle": 1, "sequence_in_cycle": 1, "started_at": 1000, "module": "policy", "phase": "g"},
    ]
    tiers = _group_calls_into_tiers_and_enrich(calls)
    assert len(tiers) == 2
    assert tiers[0][0]["module"] == "policy"
    parallel_mods = {c["module"] for c in tiers[1]}
    assert parallel_mods == {"simulator", "perspectives"}


def test_journey_sort_key_uses_id_before_started_at():
    key_a = _journey_sort_key({"cycle": 1, "sequence_in_cycle": 1, "id": 1, "started_at": 500})
    key_b = _journey_sort_key({"cycle": 1, "sequence_in_cycle": 1, "id": 2, "started_at": 100})
    assert key_a < key_b


def test_path_badge_compliance_draft_reused():
    info = _build_path_badge_info([{"event_type": COMPLIANCE_DRAFT_REUSED}])
    assert "draft reused" in info["label"]
    assert info["kind"] == "compliance_reused"


def test_path_badge_compliance_draft_reused_degraded_timeout():
    info = _build_path_badge_info(
        [
            {
                "event_type": COMPLIANCE_DRAFT_REUSED,
                "payload_json": '{"degraded": true, "degraded_reason": "llm_timeout"}',
            }
        ]
    )
    assert "slow verdict" in info["label"]
    assert info.get("degraded") is True


def test_path_badge_compliance_regenerated_degraded():
    info = _build_path_badge_info(
        [
            {
                "event_type": COMPLIANCE_DRAFT_REGENERATED,
                "payload_json": '{"reason": "degraded:llm_timeout"}',
            }
        ]
    )
    assert "regenerated (degraded)" in info["label"]
    assert info.get("degraded") is True


def test_path_badge_compliance_downgraded():
    info = _build_path_badge_info([{"event_type": COMPLIANCE_MATCH_DOWNGRADED}])
    assert "downgraded" in info["label"]


def test_path_badge_deliberative_default():
    info = _build_path_badge_info([])
    assert info["label"] == "Standard deliberative pipeline"


def test_proxy_output_info_from_event():
    info = _build_proxy_output_info(
        [
            {
                "event_type": PROXY_OUTPUT_FINALIZED,
                "payload_json": '{"final_text_source": "governed_draft", "final_action": "NORMAL_COMPLETE"}',
            }
        ]
    )
    assert info is not None
    assert info["final_text_source"] == "governed_draft"


def test_final_revalidation_info_prefers_terminal_event():
    info = _build_final_revalidation_info(
        [
            {
                "event_type": PROXY_FINAL_REVALIDATION_PASSED,
                "payload_json": (
                    '{"final_text_source_original": "safe_complete_upstream", '
                    '"final_text_source_after_revalidation": "safe_complete_upstream", '
                    '"developer_contract_present": true, "violated_hard": false}'
                ),
            }
        ]
    )
    assert info is not None
    assert info["status"] == "passed"
    assert info["final_text_source_original"] == "safe_complete_upstream"
    assert info["developer_contract_present"] is True


def test_final_revalidation_info_exposes_block_reason_without_sensitive_values():
    info = _build_final_revalidation_info(
        [
            {
                "event_type": PROXY_FINAL_REVALIDATION_BLOCKED,
                "payload_json": (
                    '{"final_text_source_original": "upstream_regen", '
                    '"final_text_source_after_revalidation": "refusal_post_revalidation", '
                    '"developer_contract_present": true, "violated_hard": true, '
                    '"violated_principles": ["CORE.DEVCONTRACT.1"], '
                    '"block_reason": "contract_literal_disclosure", '
                    '"match_kind": "protected_literal_near_match"}'
                ),
            }
        ]
    )
    assert info is not None
    assert info["status"] == "blocked"
    assert info["violated_principles"] == ["CORE.DEVCONTRACT.1"]
    assert info["block_reason"] == "contract_literal_disclosure"
    assert info["match_kind"] == "protected_literal_near_match"


def test_synthetic_final_revalidation_node_is_added_after_flow_calls():
    events = [
        {
            "event_type": PROXY_FINAL_REVALIDATION_STARTED,
            "started_at": 2000,
            "payload_json": '{"final_text_source_original": "upstream_regen"}',
        },
        {
            "event_type": PROXY_FINAL_REVALIDATION_BLOCKED,
            "started_at": 2500,
            "payload_json": (
                '{"final_text_source_original": "upstream_regen", '
                '"final_text_source_after_revalidation": "refusal_post_revalidation", '
                '"developer_contract_present": true, "violated_hard": true, '
                '"violated_principles": ["CORE.DEVCONTRACT.1"], '
                '"block_reason": "contract_literal_disclosure", '
                '"match_kind": "protected_literal_near_match"}'
            ),
        },
    ]
    info = _build_final_revalidation_info(events)
    node = _synthetic_final_revalidation_call_from_events(
        events,
        info,
        [{"module": "policy", "cycle": 1, "started_at": 1000, "duration_ms": 400}],
    )

    assert node is not None
    assert node["module"] == "final_revalidation"
    assert node["phase"] == "contract_check"
    assert node["cycle_label"] == "Final response validation"
    assert node["cycle"] == 2
    assert node["duration_ms"] == 500
    assert node["io_annotations"]["outputs"][0] == {"label": "status", "value": "blocked"}


# ---------------------------------------------------------------------------
# Audit-completeness graph nodes: governance steps that otherwise vanish
# ---------------------------------------------------------------------------


def test_path_badge_hard_signal_gate_is_distinguished():
    """The P0 hard-signal gate must be visually distinct from ordinary downgrades."""
    info = _build_path_badge_info(
        [
            {
                "event_type": COMPLIANCE_MATCH_DOWNGRADED,
                "payload_json": '{"reason": "hard_signal_evidence", "semantic_signals": ["Q10:weapons_explosives_toxins"]}',
            }
        ]
    )
    assert info["kind"] == "compliance_blocked_p0"
    assert "hard-signal safety gate (P0)" in info["label"]
    assert "Q10:weapons_explosives_toxins" in info["label"]


def test_path_badge_ordinary_downgrade_shows_reason():
    info = _build_path_badge_info(
        [
            {
                "event_type": COMPLIANCE_MATCH_DOWNGRADED,
                "payload_json": '{"reason": "regenerated_draft_unvalidated"}',
            }
        ]
    )
    assert info["kind"] == "compliance_downgraded"
    assert "regenerated_draft_unvalidated" in info["label"]


def test_synthetic_downgrade_node_hard_signal_has_alert_action():
    nodes = _synthetic_compliance_downgrade_nodes(
        [
            {
                "event_type": COMPLIANCE_MATCH_DOWNGRADED,
                "started_at": 8000,
                "payload_json": (
                    '{"reason": "hard_signal_evidence", "matched_rule_id": "R1", '
                    '"risk_category": "clearly_harmful", "semantic_signals": ["Q8:self_harm_suicide"]}'
                ),
            }
        ],
        all_flow_calls=[{"module": "compliance_layer", "started_at": 7000, "duration_ms": 500}],
    )
    assert len(nodes) == 1
    node = nodes[0]
    assert node["module"] == "compliance_layer"
    assert node["phase"] == "safety_gate"
    assert "P0" in node["action"]
    assert "hard-signal P0" in node["semantic_badges"]
    labels = {o["label"] for o in node["io_annotations"]["outputs"]}
    assert "semantic_signals" in labels


def test_synthetic_downgrade_node_ordinary_is_still_shown():
    nodes = _synthetic_compliance_downgrade_nodes(
        [
            {
                "event_type": COMPLIANCE_MATCH_DOWNGRADED,
                "started_at": 100,
                "payload_json": '{"reason": "regenerated_draft_unvalidated"}',
            }
        ],
        all_flow_calls=[],
    )
    assert len(nodes) == 1
    assert nodes[0]["phase"] == "match_downgraded"
    assert "hard-signal P0" not in nodes[0]["semantic_badges"]


def test_synthetic_downgrade_nodes_empty_without_event():
    assert _synthetic_compliance_downgrade_nodes([], all_flow_calls=[]) == []


def test_synthetic_module_deferred_node():
    nodes = _synthetic_module_deferred_nodes(
        [
            {
                "event_type": MODULE_DEFERRED_TO_COMPLIANCE,
                "started_at": 500,
                "payload_json": '{"module": "critic", "cycle": 1, "reason": "compliance_match"}',
            }
        ]
    )
    assert len(nodes) == 1
    node = nodes[0]
    assert node["module"] == "critic"
    assert node["cycle"] == 1
    assert node["sequence_in_cycle"] == 2  # critic canonical seq
    assert "deferred" in node["semantic_badges"]


def test_synthetic_ledger_fast_path_applied():
    node = _synthetic_ledger_fast_path_node(
        [
            {
                "event_type": LEDGER_FAST_PATH_APPLIED,
                "started_at": 900,
                "payload_json": '{"from_turn": 2, "similarity": 0.97, "modules_skipped": ["critic", "simulator"]}',
            }
        ],
        all_flow_calls=[{"module": "risk_estimator", "started_at": 100, "duration_ms": 200}],
    )
    assert node is not None
    assert node["phase"] == "ledger_fast_path"
    assert "deliberation skipped" in node["action"]
    assert "ledger cache hit" in node["semantic_badges"]


def test_synthetic_ledger_fast_path_not_applied():
    node = _synthetic_ledger_fast_path_node(
        [
            {
                "event_type": LEDGER_FAST_PATH_NOT_APPLIED,
                "started_at": 900,
                "payload_json": '{"from_turn": 2, "similarity": 0.97, "gate_reason": "route_mismatch"}',
            }
        ],
        all_flow_calls=[],
    )
    assert node is not None
    assert "refused" in node["action"]
    assert "ledger cache refused" in node["semantic_badges"]


def test_synthetic_ledger_fast_path_none_without_event():
    assert _synthetic_ledger_fast_path_node([], all_flow_calls=[]) is None


def test_synthetic_convergence_node_accepted():
    node = _synthetic_convergence_node(
        [{"event_type": EARLY_CONVERGENCE_ACCEPTED, "started_at": 400, "payload_json": '{"cycle": 2, "agreement": 0.9}'}]
    )
    assert node is not None
    assert node["phase"] == "convergence"
    assert node["cycle"] == 2
    assert "converged" in node["semantic_badges"]


def test_synthetic_module_skipped_nodes():
    nodes = _synthetic_module_skipped_nodes(
        [
            {
                "event_type": SIMULATOR_SKIPPED,
                "started_at": 300,
                "payload_json": '{"cycle": 1, "reason": "gate_below_threshold"}',
            },
            {"event_type": CRITIC_SKIPPED, "started_at": 310, "payload_json": '{"cycle": 1, "reason": "short_circuit"}'},
        ]
    )
    mods = {n["module"] for n in nodes}
    assert mods == {"simulator", "critic"}
    assert all("skipped" in n["semantic_badges"] for n in nodes)


# ---------------------------------------------------------------------------
# Hard-violation delivery guard rows: placement, legacy re-homing, labels.
# The graph claims "exact execution order"; these pin that the guard's
# regeneration + re-critique render after the cycle that raised the violation
# (never in "Initial assessment") and that the synthetic calibration / routing
# governance nodes precede the policy draft they cause.
# ---------------------------------------------------------------------------


def _row(**kw) -> dict:
    return kw


def _delib_cycle_rows(*, regen_seq: int, recrit_seq: int, regen_cycle: int = 1) -> list[dict]:
    """One deliberation cycle (full_parallel: critic/simulator/perspectives
    overlap) followed by the guard's two rows, in real wall-clock order."""
    return [
        _row(
            id=1,
            cycle=1,
            sequence_in_cycle=SEQ_POLICY,
            started_at=1000,
            duration_ms=0,
            module="policy",
            phase="policy_generate",
            action="generate (speculative-reuse)",
        ),
        _row(
            id=2,
            cycle=1,
            sequence_in_cycle=SEQ_CRITIC,
            started_at=1001,
            duration_ms=2300,
            module="critic",
            phase="critic",
            action="critique",
        ),
        _row(
            id=3,
            cycle=1,
            sequence_in_cycle=SEQ_SIMULATOR,
            started_at=1001,
            duration_ms=3700,
            module="simulator",
            phase="simulator",
            action="simulate",
        ),
        _row(
            id=4,
            cycle=1,
            sequence_in_cycle=SEQ_PERSPECTIVES,
            started_at=1003,
            duration_ms=4100,
            module="perspectives",
            phase="perspectives",
            action="evaluate",
        ),
        _row(
            id=5,
            cycle=regen_cycle,
            sequence_in_cycle=regen_seq,
            started_at=5200,
            duration_ms=1900,
            module="policy",
            phase="policy_generate",
            action="generate (safe_complete_path)",
        ),
        _row(
            id=6,
            cycle=1,
            sequence_in_cycle=recrit_seq,
            started_at=7100,
            duration_ms=1700,
            module="critic",
            phase="critic",
            action=HARD_VIOLATION_REVALIDATION_ACTION,
        ),
    ]


def test_guard_rows_tier_after_every_deliberation_module_and_never_share_the_critic_tier():
    rows = _delib_cycle_rows(regen_seq=SEQ_HARD_VIOLATION_REGENERATION, recrit_seq=SEQ_HARD_VIOLATION_REVALIDATION)
    rows[4]["action"] = HARD_VIOLATION_REGENERATION_ACTION
    tiers = _group_calls_into_tiers_and_enrich(rows)
    flat = [[c["action"] for c in tier] for tier in tiers]
    assert flat == [
        ["generate (speculative-reuse)"],
        ["critique"],
        ["simulate", "evaluate"],
        [HARD_VIOLATION_REGENERATION_ACTION],
        [HARD_VIOLATION_REVALIDATION_ACTION],
    ]
    labels = _compute_connector_labels(tiers)
    assert labels == [
        "draft",
        "scheduled in parallel with critic (not gated)",
        "hard violation → regenerate under SAFE_COMPLETE",
        "re-critique regenerated draft",
    ]


def test_critic_gated_scheduler_keeps_gate_proceed_label():
    """simulator/perspectives that started after the critic ended waited for its verdict."""
    tiers = [
        [{"module": "critic", "started_at": 1000, "duration_ms": 500}],
        [
            {"module": "simulator", "started_at": 1600, "duration_ms": 100},
            {"module": "perspectives", "started_at": 1600, "duration_ms": 100},
        ],
    ]
    assert _compute_connector_labels(tiers) == ["gate: proceed"]
    # Legacy rows without timestamps stay on the historical label.
    tiers_no_ts = [[{"module": "critic"}], [{"module": "simulator"}, {"module": "perspectives"}]]
    assert _compute_connector_labels(tiers_no_ts) == ["gate: proceed"]


def test_rehome_legacy_guard_rows_into_the_violation_cycle():
    """Rows persisted before the guard got its own coordinates (regeneration as
    the cycle-0 fast-path draft, re-critique as SEQ_CRITIC) are re-homed so the
    graph renders them after the deliberation, not before it."""
    rows = _delib_cycle_rows(regen_seq=SEQ_POLICY, recrit_seq=SEQ_CRITIC, regen_cycle=0)
    changed = _rehome_legacy_hard_violation_guard_calls(rows)
    assert {c["id"] for c in changed} == {5, 6}
    regen, recrit = rows[4], rows[5]
    assert regen["cycle"] == 1
    assert regen["sequence_in_cycle"] == SEQ_HARD_VIOLATION_REGENERATION
    assert regen["action"] == HARD_VIOLATION_REGENERATION_ACTION
    assert regen["_legacy_guard_rehomed"] is True
    assert recrit["sequence_in_cycle"] == SEQ_HARD_VIOLATION_REVALIDATION
    assert recrit["_legacy_guard_rehomed"] is True
    # After re-homing the legacy request renders exactly like a new one.
    tiers = _group_calls_into_tiers_and_enrich(rows)
    assert [[c["action"] for c in tier] for tier in tiers][-2:] == [
        [HARD_VIOLATION_REGENERATION_ACTION],
        [HARD_VIOLATION_REVALIDATION_ACTION],
    ]


def test_rehome_handles_fail_closed_regeneration_without_recritique():
    """Regeneration produced empty text -> guard failed closed, no re-critique
    row exists; the cycle-0 row is still the guard's (it postdates the cycle)."""
    rows = _delib_cycle_rows(regen_seq=SEQ_POLICY, recrit_seq=SEQ_CRITIC, regen_cycle=0)[:5]
    changed = _rehome_legacy_hard_violation_guard_calls(rows)
    assert [c["id"] for c in changed] == [5]
    assert rows[4]["cycle"] == 1 and rows[4]["sequence_in_cycle"] == SEQ_HARD_VIOLATION_REGENERATION


def test_rehome_leaves_fast_path_safe_complete_and_new_rows_untouched():
    # Fast path: a cycle-0 SAFE_COMPLETE draft with no deliberation cycle at all.
    fast_path = [
        _row(
            id=1, cycle=0, sequence_in_cycle=-9, started_at=100, module="risk_estimator", phase="r", action="estimate_intent"
        ),
        _row(
            id=2,
            cycle=0,
            sequence_in_cycle=SEQ_POLICY,
            started_at=900,
            module="policy",
            phase="policy_generate",
            action="generate (safe_complete_path)",
        ),
    ]
    assert _rehome_legacy_hard_violation_guard_calls(fast_path) == []
    assert fast_path[1]["cycle"] == 0 and fast_path[1]["action"] == "generate (safe_complete_path)"
    # New-shape rows already carry the guard's coordinates.
    new_rows = _delib_cycle_rows(regen_seq=SEQ_HARD_VIOLATION_REGENERATION, recrit_seq=SEQ_HARD_VIOLATION_REVALIDATION)
    new_rows[4]["action"] = HARD_VIOLATION_REGENERATION_ACTION
    assert _rehome_legacy_hard_violation_guard_calls(new_rows) == []
    assert all("_legacy_guard_rehomed" not in c for c in new_rows)


def test_cycle0_synthetic_calibration_and_routing_precede_the_policy_draft():
    """The synthetic governance nodes carry a sequence so they are tiered where
    the controller runs them (after the risk minis / before the branch), not
    appended after the fast-path policy draft they precede."""
    calls = [
        _row(
            id=9,
            cycle=0,
            sequence_in_cycle=SEQ_POLICY,
            started_at=9000,
            module="policy",
            phase="policy_generate",
            action="generate (safe_complete_path)",
        ),
        _row(
            cycle=0,
            sequence_in_cycle=_SEQ_SYNTHETIC_PATH_ROUTING,
            started_at=7001,
            module="orchestrator",
            phase="path_routing",
            action="route_resolution",
            is_synthetic=True,
        ),
        _row(
            id=4,
            cycle=0,
            sequence_in_cycle=-5,
            started_at=7500,
            module="compliance_layer",
            phase="evaluate",
            action="evaluate",
        ),
        _row(
            cycle=0,
            sequence_in_cycle=_SEQ_SYNTHETIC_CALIBRATION,
            started_at=7000,
            module="risk_estimator",
            phase="calibration",
            action="calibrate",
            is_synthetic=True,
        ),
        _row(
            id=3,
            cycle=0,
            sequence_in_cycle=-8,
            started_at=6900,
            module="risk_estimator",
            phase="risk_estimation",
            action="calibration_guard",
        ),
        _row(
            id=2,
            cycle=0,
            sequence_in_cycle=-9,
            started_at=6000,
            module="risk_estimator",
            phase="risk_estimation",
            action="estimate_intent",
        ),
        _row(
            id=7,
            cycle=0,
            sequence_in_cycle=0,
            started_at=5500,
            module="policy",
            phase="speculative_generate",
            action="generate (speculative)",
        ),
        _row(
            id=1,
            cycle=0,
            sequence_in_cycle=-10,
            started_at=5000,
            module="constitution_retriever",
            phase="constitution_retrieval",
            action="domain_prefilter",
        ),
    ]
    tiers = _group_calls_into_tiers_and_enrich(calls)
    assert [[c["action"] for c in tier] for tier in tiers] == [
        ["domain_prefilter"],
        ["estimate_intent", "generate (speculative)"],
        ["calibration_guard"],
        ["calibrate"],
        ["evaluate"],
        ["route_resolution"],
        ["generate (safe_complete_path)"],
    ]
    # The journey/timeline sort agrees: both governance nodes precede the draft.
    actions = [c["action"] for c in sorted(calls, key=_journey_sort_key)]
    assert actions.index("calibrate") < actions.index("route_resolution") < actions.index("generate (safe_complete_path)")
