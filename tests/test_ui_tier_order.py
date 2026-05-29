"""
Tests for UI tier grouping and DCCL path badge helpers.
"""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi")

from moralstack.orchestration.orchestration_event_taxonomy import (
    COMPLIANCE_DRAFT_REGENERATED,
    COMPLIANCE_DRAFT_REUSED,
    COMPLIANCE_MATCH_DOWNGRADED,
    PROXY_FINAL_REVALIDATION_BLOCKED,
    PROXY_FINAL_REVALIDATION_PASSED,
    PROXY_FINAL_REVALIDATION_STARTED,
    PROXY_OUTPUT_FINALIZED,
)
from moralstack.ui.app import (
    _build_final_revalidation_info,
    _build_path_badge_info,
    _build_proxy_output_info,
    _group_calls_into_tiers_and_enrich,
    _journey_sort_key,
    _synthetic_final_revalidation_call_from_events,
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
