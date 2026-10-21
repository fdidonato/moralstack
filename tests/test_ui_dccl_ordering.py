"""DCCL (Developer Contract Compliance Layer) ordering on the request page.

The UI must draw the cycle-0 steps in the order the controller runs them
(``controller.py``): prefilter -> domain agents -> risk minis (parallel with the speculative draft) -> calibration
-> DCCL evaluate -> DCCL safety_override -> hard-signal gate / regenerate / revalidate / downgrade -> deferred markers
-> path routing -> ledger -> deliberation retrieval -> fast draft -> quick check -> terminal refusal.

Legacy DCCL ``llm_calls`` rows were persisted with ``cycle=NULL`` (``dccl.py``); the UI normalizes them to cycle 0 at
read time, and ``dccl.py`` now persists ``cycle=0``. Both shapes must render identically.

Unit tests use pure dicts shaped after request 1166f216 (offsets from a fixed ``T0``); page tests seed a tmp SQLite DB.
Tests never assert literal tier numbers, only relative order.
"""

from __future__ import annotations

import copy
import json
import random
from collections import defaultdict
from typing import Any

import pytest

pytest.importorskip("fastapi")

from moralstack.observability.emit_helpers import (  # noqa: E402
    persist_debug_event,
    persist_orchestration_event,
)
from moralstack.observability.service import get_obs  # noqa: E402
from moralstack.orchestration.orchestration_event_taxonomy import (  # noqa: E402
    COMPLIANCE_MATCH_DOWNGRADED,
    MODULE_DEFERRED_TO_COMPLIANCE,
)
from moralstack.ui import app as ui_app  # noqa: E402
from moralstack.ui.app import (  # noqa: E402
    _build_execution_timeline,
    _enrich_journey_with_timing_and_parallel,
    _group_calls_into_tiers_and_enrich,
    _journey_sort_key,
    _normalize_post_output_cycles,
    _visual_tier_for_call,
)
from tests.test_ui_llm_call_visibility import (  # noqa: E402
    T0,
    _call,
    _graph_sequence,
    _idx,
    _render,
    _seed_request,
    isolate_observability_fixture,  # noqa: F401  (autouse fixture)
    ui_client_fixture,  # noqa: F401  (a fixture, registered as "ui_client")
)

_B = 1_000_000  # unit-test wall-clock anchor (ms)


@pytest.fixture(autouse=True)
def _no_leaked_cycle():
    """``persist_llm_call(cycle=None)`` inherits the cycle ContextVar; a worker thread that served an earlier test may
    still hold one (FACTS H1). The NULL-cycle seeds below must really be NULL, so start from a clean context."""
    from moralstack.observability import context as obs_context

    token = obs_context._cycle.set(None)
    try:
        yield
    finally:
        obs_context._cycle.reset(token)


def _norm(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Deferred import: the normalizer is the new helper under test."""
    return ui_app._normalize_dccl_legacy_cycles(rows)  # type: ignore[attr-defined]


def _r(
    seq: int | None,
    module: str,
    action: str,
    start: float,
    dur: float = 0.0,
    *,
    id: int | None = None,
    cycle: int | None = 0,
    phase: str = "",
    **kw: Any,
) -> dict[str, Any]:
    row: dict[str, Any] = {
        "cycle": cycle,
        "sequence_in_cycle": seq,
        "module": module,
        "action": action,
        "phase": phase or module,
        "started_at": _B + start,
        "duration_ms": dur,
    }
    if id is not None:
        row["id"] = id
    row.update(kw)
    return row


def _prefilter(start: float = 0, dur: float = 480, id: int = 1) -> dict[str, Any]:
    return _r(-10, "constitution_retriever", "domain_prefilter", start, dur, id=id, phase="constitution_retrieval")


def _agents() -> list[dict[str, Any]]:
    return [
        _r(-10, "constitution_retriever", "enhanced_domain_agent", 481, 600, id=2, phase="constitution_retrieval"),
        _r(-10, "constitution_retriever", "enhanced_domain_agent", 557, 700, id=3, phase="constitution_retrieval"),
    ]


def _minis(start: float = 1800) -> list[dict[str, Any]]:
    return [
        _r(-9, "risk_estimator", a, start, d, id=i, phase="risk_estimation")
        for i, (a, d) in zip(
            (5, 6, 7), (("estimate_intent", 2900), ("estimate_signals", 2950), ("estimate_operational", 3000))
        )
    ]


def _speculative(start: float = 1800, dur: float = 3000) -> dict[str, Any]:
    return _r(0, "policy", "generate (speculative)", start, dur, id=4, phase="speculative_generate")


def _calibrate(start: float = 4801) -> dict[str, Any]:
    return _r(-7, "risk_estimator", "calibrate", start, 0, phase="calibration", is_synthetic=True)


def _evaluate(cycle: int | None, start: float = 5000, dur: float = 1742.12, id: int = 8) -> dict[str, Any]:
    return _r(-5, "compliance_layer", "evaluate", start, dur, id=id, cycle=cycle, phase="compliance_layer")


def _override(cycle: int | None, start: float = 6742, dur: float = 1871.12, id: int = 9) -> dict[str, Any]:
    return _r(-6, "compliance_layer", "safety_override", start, dur, id=id, cycle=cycle, phase="compliance_layer")


def _gate(start: float = 8620) -> dict[str, Any]:
    return _r(
        -4,
        "compliance_layer",
        "MATCH blocked - hard-signal gate (P0)",
        start,
        0,
        phase="safety_gate",
        is_synthetic=True,
    )


def _route(start: float = 8621) -> dict[str, Any]:
    return _r(-2, "orchestrator", "route_resolution", start, 0, phase="path_routing", is_synthetic=True)


def _refuse(start: float = 8700, dur: float = 900, id: int = 10) -> dict[str, Any]:
    return _r(6, "orchestration", "refuse (fast_path)", start, dur, id=id, phase="refusal")


def _shape_1166(dccl_cycle: int | None) -> list[dict[str, Any]]:
    """LLM MATCH -> hard-signal gate -> fast-path refusal (request 1166f216)."""
    return [
        _prefilter(),
        *_agents(),
        _speculative(),
        *_minis(),
        _calibrate(),
        _evaluate(dccl_cycle),
        _override(dccl_cycle),
        _gate(),
        _route(),
        _refuse(),
    ]


def _marker(module: str, seq: int, start: float = 8700, *, phase: str = "deferred_to_compliance") -> dict[str, Any]:
    return _r(
        seq,
        module,
        "skipped - contract authorized",
        start,
        0,
        phase=phase,
        is_synthetic=True,
    )


def _shape_f5c5() -> list[dict[str, Any]]:
    """LLM MATCH, draft reused: five modules skipped after the verdict."""
    return [
        _prefilter(),
        _speculative(),
        *_minis(),
        _calibrate(),
        _evaluate(0),
        _override(0),
        _marker("policy", 1),
        _marker("critic", 2),
        _marker("simulator", 3),
        _marker("perspectives", 4),
        _marker("hindsight", 5),
    ]


def _shape_ca81() -> list[dict[str, Any]]:
    """Safety override: evaluate -> route -> refuse."""
    return [_prefilter(), *_minis(), _calibrate(), _evaluate(0), _route(6760), _refuse(6800)]


def _regen_rows() -> list[dict[str, Any]]:
    return [
        _prefilter(),
        *_minis(),
        _calibrate(),
        _evaluate(0),
        _override(0),
        _r(
            1,
            "policy",
            "generate (compliance-regenerate)",
            8700,
            1500,
            id=11,
            phase="policy_generate",
            call_kind="compliance_regenerate",
        ),
        _r(-4, "compliance_layer", "draft_revalidate", 10200, 800, id=12, phase="compliance_layer"),
    ]


def _shape_regen_ok() -> list[dict[str, Any]]:
    return [*_regen_rows(), _marker("critic", 2, 11001)]


def _downgrade(start: float) -> dict[str, Any]:
    return _r(
        -4,
        "compliance_layer",
        "MATCH downgraded (regenerated_draft_unvalidated)",
        start,
        0,
        phase="match_downgraded",
        is_synthetic=True,
    )


def _shape_regen_fail() -> list[dict[str, Any]]:
    return [
        *_regen_rows(),
        _downgrade(11001),
        _route(11002),
        _r(1, "policy", "generate (fast_path)", 11100, 1100, id=13, phase="policy_generate"),
        _r(2, "critic", "quick_check", 12300, 700, id=14, phase="critic"),
    ]


def _shape_regen_no_revalidate() -> list[dict[str, Any]]:
    """RV5: a downgrade may coexist with a regenerate row and no revalidate row (semantic LLM branch early return)."""
    rows = [r for r in _regen_rows() if r["action"] != "draft_revalidate"]
    return [*rows, _downgrade(10300), _route(10301)]


def _shape_structured() -> list[dict[str, Any]]:
    """Structured MATCH: only the safety_override row exists (-6); then deferred markers."""
    return [_prefilter(), *_minis(), _calibrate(), _override(0, 5000, 1800), _marker("critic", 2, 6900)]


_SHAPES = {
    "1166": lambda: _shape_1166(0),
    "f5c5": _shape_f5c5,
    "ca81": _shape_ca81,
    "regen_ok": _shape_regen_ok,
    "regen_fail": _shape_regen_fail,
    "regen_no_revalidate": _shape_regen_no_revalidate,
    "structured": _shape_structured,
}


def _label(c: dict[str, Any]) -> str:
    return f"{c['module']}/{c['action']}"


def _flat(tiers: list[list[dict[str, Any]]]) -> list[str]:
    return [_label(c) for t in tiers for c in t]


def _tier_labels(tiers: list[list[dict[str, Any]]]) -> list[set[str]]:
    return [{_label(c) for c in t} for t in tiers]


def _groups(rows: list[dict[str, Any]]) -> dict[Any, list[list[dict[str, Any]]]]:
    """Mirror ``request_detail``: group by raw cycle, tier each group."""
    by_cycle: dict[Any, list[dict[str, Any]]] = defaultdict(list)
    for c in rows:
        by_cycle[c.get("cycle")].append(c)
    return {cy: _group_calls_into_tiers_and_enrich(cs) for cy, cs in by_cycle.items()}


# --------------------------------------------------------------------------------------
# T1 / T1b: legacy NULL rows collapse into the cycle-0 group, in execution order
# --------------------------------------------------------------------------------------

_EXPECTED_1166 = [
    {"constitution_retriever/domain_prefilter"},
    {"constitution_retriever/enhanced_domain_agent"},
    {
        "risk_estimator/estimate_intent",
        "risk_estimator/estimate_signals",
        "risk_estimator/estimate_operational",
        "policy/generate (speculative)",
    },
    {"risk_estimator/calibrate"},
    {"compliance_layer/evaluate"},
    {"compliance_layer/safety_override"},
    {"compliance_layer/MATCH blocked - hard-signal gate (P0)"},
    {"orchestrator/route_resolution"},
    {"orchestration/refuse (fast_path)"},
]


def test_t1_legacy_null_dccl_rows_normalize_into_one_cycle0_group_in_execution_order():
    rows = _shape_1166(None)
    changed = _norm(rows)
    assert len(changed) == 2
    assert all(c["cycle"] == 0 and c["_cycle_normalized"] is True for c in changed)
    groups = _groups(rows)
    assert set(groups) == {0}, "None must not survive as a group key"
    tiers = groups[0]
    assert _tier_labels(tiers) == _EXPECTED_1166
    # gate and refusal are different tiers: nothing is falsely "parallel"
    assert all(len(t) == 1 for t in tiers if "compliance_layer/safety_override" in _tier_labels([t])[0])


def test_t1_cycle0_rows_render_in_the_same_order_without_normalization():
    """Option 2 (dccl.py persists cycle=0) must converge on the same tiers as the normalized legacy rows."""
    tiers = _groups(_shape_1166(0))[0]
    assert _tier_labels(tiers) == _EXPECTED_1166


def test_t1b_normalizer_touches_only_compliance_null_rows():
    rows = [
        _evaluate(None),
        _override(0),
        _r(1, "upstream_provider", "generate", 100, 10, cycle=None, phase="upstream_regen"),
        _r(6, "final_revalidation", "revalidate", 200, 10, cycle=None, phase="final_revalidation"),
        _r(None, "risk_estimator", "estimate_intent", 1, 1, cycle=None),
    ]
    snapshot = copy.deepcopy(rows)
    changed = _norm(rows)
    assert changed == [rows[0]]
    assert rows[0]["cycle"] == 0 and rows[0]["_cycle_normalized"] is True
    assert "_cycle_normalized" not in rows[1]
    for i in (2, 3, 4):
        assert rows[i] == snapshot[i]  # non-compliance NULL rows are not hidden


def test_t1b_post_output_cycles_still_derived_after_normalization():
    rows = [
        _evaluate(None),
        _r(1, "upstream_provider", "generate", 9000, 10, cycle=None, phase="upstream_regen"),
    ]
    _norm(rows)
    _normalize_post_output_cycles(rows)
    assert rows[0]["cycle"] == 0
    assert rows[1]["cycle"] == 1  # base cycle 0 + 1
    only_compliance = [_evaluate(None)]
    _norm(only_compliance)
    _normalize_post_output_cycles(only_compliance)
    assert only_compliance[0]["cycle"] == 0


# --------------------------------------------------------------------------------------
# T2: -6 after -5 (graph and journey, any insertion order)
# --------------------------------------------------------------------------------------


def test_t2_safety_override_after_evaluate_in_graph_and_journey_for_any_insertion_order():
    for order in ([0, 1], [1, 0]):
        base = [_calibrate(), _evaluate(0), _override(0)]
        rows = [base[i + 1] for i in order]
        rows = [base[0], *rows]
        flat = _flat(_groups(copy.deepcopy(rows))[0])
        assert flat.index("compliance_layer/evaluate") < flat.index("compliance_layer/safety_override")
        journey = [_label(c) for c in sorted(copy.deepcopy(rows), key=_journey_sort_key)]
        assert journey.index("compliance_layer/evaluate") < journey.index("compliance_layer/safety_override")


def test_t2_structured_path_has_safety_override_alone_after_calibration():
    flat = _flat(_groups(_shape_structured())[0])
    assert flat.index("risk_estimator/calibrate") < flat.index("compliance_layer/safety_override")
    assert "compliance_layer/evaluate" not in flat


# --------------------------------------------------------------------------------------
# T3: regenerate / revalidate / downgrade chain
# --------------------------------------------------------------------------------------


def test_t3_regenerate_revalidate_downgrade_route_fastdraft_quickcheck_chain():
    tiers = _groups(_shape_regen_fail())[0]
    flat = _flat(tiers)
    chain = [
        "compliance_layer/safety_override",
        "policy/generate (compliance-regenerate)",
        "compliance_layer/draft_revalidate",
        "compliance_layer/MATCH downgraded (regenerated_draft_unvalidated)",
        "orchestrator/route_resolution",
        "policy/generate (fast_path)",
        "critic/quick_check",
    ]
    positions = [flat.index(x) for x in chain]
    assert positions == sorted(positions), flat
    revalidate_tier = next(t for t in tiers if any(c["action"] == "draft_revalidate" for c in t))
    assert all(c["action"] == "draft_revalidate" for c in revalidate_tier), "downgrade must not share the revalidate tier"


def test_t3_regenerate_ok_precedes_revalidate_then_markers():
    flat = _flat(_groups(_shape_regen_ok())[0])
    assert (
        flat.index("compliance_layer/safety_override")
        < flat.index("policy/generate (compliance-regenerate)")
        < flat.index("compliance_layer/draft_revalidate")
        < flat.index("critic/skipped - contract authorized")
    )


def test_t3_rv5_downgrade_with_regenerate_but_no_revalidate_row():
    flat = _flat(_groups(_shape_regen_no_revalidate())[0])
    assert (
        flat.index("policy/generate (compliance-regenerate)")
        < flat.index("compliance_layer/MATCH downgraded (regenerated_draft_unvalidated)")
        < flat.index("orchestrator/route_resolution")
    )


# --------------------------------------------------------------------------------------
# T4 / T5: deferred markers, path routing / ledger / retrieval / draft
# --------------------------------------------------------------------------------------


def test_t4_deferred_markers_form_one_tier_after_the_verdict():
    tiers = _groups(_shape_f5c5())[0]
    marker_tiers = [t for t in tiers if any(c.get("phase") == "deferred_to_compliance" for c in t)]
    assert len(marker_tiers) == 1 and len(marker_tiers[0]) == 5
    assert tiers[-1] is marker_tiers[0]
    assert all(c.get("phase") == "deferred_to_compliance" for c in marker_tiers[0])


def test_t5_routing_before_ledger_before_deliberation_retrieval_before_route_draft():
    rows = [
        _r(1, "policy", "generate (fast_path)", 9000, 500, id=9, phase="policy_generate"),
        _r(-1, "constitution_retriever", "domain_prefilter", 8500, 100, id=8),
        _r(-3, "orchestrator", "ledger fast-path", 8400, 0, phase="ledger_fast_path", is_synthetic=True),
        _route(8300),
        _evaluate(0),
    ]
    flat = _flat(_groups(rows)[0])
    assert [
        flat.index(x)
        for x in (
            "compliance_layer/evaluate",
            "orchestrator/route_resolution",
            "orchestrator/ledger fast-path",
            "constitution_retriever/domain_prefilter",
            "policy/generate (fast_path)",
        )
    ] == [0, 1, 2, 3, 4]


def test_t5_ca81_shape_routes_before_refusing():
    flat = _flat(_groups(_shape_ca81())[0])
    assert flat.index("orchestrator/route_resolution") < flat.index("orchestration/refuse (fast_path)")


# --------------------------------------------------------------------------------------
# T6: every known cycle-0 step has its own rank (except minis with speculative)
# --------------------------------------------------------------------------------------


def _step(seq: int, action: str = "", module: str = "x", **kw: Any) -> dict[str, Any]:
    return {"cycle": 0, "sequence_in_cycle": seq, "module": module, "action": action, **kw}


def test_t6_cycle0_steps_are_strictly_ordered_and_distinct():
    ordered = [
        ("prefilter", _step(-10, "domain_prefilter", "constitution_retriever")),
        ("agents", _step(-10, "enhanced_domain_agent", "constitution_retriever")),
        ("minis", _step(-9, "estimate_intent", "risk_estimator")),
        ("guard", _step(-8, "calibration_guard", "risk_estimator")),
        ("calibrate", _step(-7, "calibrate", "risk_estimator")),
        ("evaluate", _step(-5, "evaluate", "compliance_layer")),
        ("override", _step(-6, "safety_override", "compliance_layer")),
        (
            "regenerate",
            _step(1, "generate (compliance-regenerate)", "policy", call_kind="compliance_regenerate"),
        ),
        ("revalidate", _step(-4, "draft_revalidate", "compliance_layer")),
        ("gate", _step(-4, "MATCH blocked", "compliance_layer", phase="safety_gate")),
        ("markers", _step(2, "skipped - contract authorized", "critic", phase="deferred_to_compliance")),
        ("route", _step(-2, "route_resolution", "orchestrator")),
        ("ledger", _step(-3, "ledger", "orchestrator")),
        ("delib_prefilter", _step(-1, "domain_prefilter", "constitution_retriever")),
        ("delib_agents", _step(-1, "enhanced_domain_agent", "constitution_retriever")),
        ("draft", _step(1, "generate (fast_path)", "policy")),
        ("quick", _step(2, "quick_check", "critic")),
        ("terminal", _step(6, "refuse (fast_path)", "orchestration")),
    ]
    tiers = [_visual_tier_for_call(c) for _, c in ordered]
    assert tiers == sorted(set(tiers)), dict(zip((n for n, _ in ordered), tiers))
    spec = _step(0, "generate (speculative)", "policy", phase="speculative_generate")
    assert _visual_tier_for_call(spec) == _visual_tier_for_call(ordered[2][1])
    # downgrade shares the gate tier (they never coexist) and is after revalidate
    downgrade = _step(-4, "MATCH downgraded", "compliance_layer", phase="match_downgraded")
    assert _visual_tier_for_call(downgrade) == _visual_tier_for_call(ordered[9][1]) > _visual_tier_for_call(ordered[8][1])


def test_t6_phase_less_seq2_marker_uses_action_and_quick_check_keeps_its_own_tier():
    marker_no_phase = _step(2, "skipped - contract authorized", "critic")
    marker_phase = _step(2, "skipped - contract authorized", "critic", phase="deferred_to_compliance")
    quick = _step(2, "quick_check", "critic")
    assert _visual_tier_for_call(marker_no_phase) == _visual_tier_for_call(marker_phase)
    assert _visual_tier_for_call(quick) > _visual_tier_for_call(marker_no_phase)


def test_t6_unmapped_cycle0_seq_lands_in_the_tail_without_colliding():
    mapped = [_visual_tier_for_call(_step(s)) for s in (-10, -9, -8, -7, -6, -5, -4, -3, -2, -1, 0, 1, 6)]
    path_routing = _visual_tier_for_call(_step(-2, "route_resolution", "orchestrator"))
    for seq in (7, 8, 90):
        t = _visual_tier_for_call(_step(seq))
        assert t > max(mapped)
        assert t != path_routing
    assert _visual_tier_for_call(_step(7)) < _visual_tier_for_call(_step(8))


def test_t6_refusal_is_not_parallel_with_the_downgrade_nodes_connector():
    rows = [_downgrade(1000), _route(1001), _refuse(1100)]
    tiers = _group_calls_into_tiers_and_enrich(rows)
    assert [len(t) for t in tiers] == [1, 1, 1]


# --------------------------------------------------------------------------------------
# T7: journey order == flattened graph tiers
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize("shape", sorted(_SHAPES))
def test_t7_journey_order_equals_flattened_graph_tiers(shape):
    rows = _SHAPES[shape]()
    tiers = _group_calls_into_tiers_and_enrich(rows)
    journey = sorted(rows, key=_journey_sort_key)
    tier_of = {id(c): i for i, t in enumerate(tiers) for c in t}
    sequence = [tier_of[id(c)] for c in journey]
    assert sequence == sorted(sequence), f"{shape}: journey disagrees with the graph tiers"
    assert [t for t in sequence] and set(sequence) == set(range(len(tiers)))


def test_t7_speculative_is_before_route_resolution_in_journey():
    journey = [_label(c) for c in sorted(_shape_1166(0), key=_journey_sort_key)]
    assert journey.index("policy/generate (speculative)") < journey.index("orchestrator/route_resolution")


# --------------------------------------------------------------------------------------
# T8 / T8b: journey overlap tolerance
# --------------------------------------------------------------------------------------


def test_t8_real_back_to_back_dccl_rows_are_not_parallel_but_agents_and_speculative_are():
    rows = _shape_1166(0)
    enriched = {_label(c): c for c in _enrich_journey_with_timing_and_parallel(sorted(rows, key=_journey_sort_key))}
    assert enriched["compliance_layer/evaluate"]["is_parallel"] is False
    assert enriched["compliance_layer/safety_override"]["is_parallel"] is False
    # genuinely concurrent rows stay Parallel: the two domain agents overlap each other, speculative overlaps the minis
    assert enriched["constitution_retriever/enhanced_domain_agent"]["is_parallel"] is True
    assert enriched["constitution_retriever/domain_prefilter"]["is_parallel"] is False
    assert enriched["policy/generate (speculative)"]["is_parallel"] is True


def test_t8b_speculative_overlapping_evaluate_is_parallel_in_journey_but_graph_keeps_sequential_tier():
    rows = [
        _prefilter(),
        *_minis(),
        _speculative(1800, 5000),  # ends after evaluate starts (DCCL does not await it)
        _calibrate(),
        _evaluate(0),
    ]
    enriched = {_label(c): c for c in _enrich_journey_with_timing_and_parallel(sorted(rows, key=_journey_sort_key))}
    assert enriched["compliance_layer/evaluate"]["is_parallel"] is True
    tiers = _group_calls_into_tiers_and_enrich(rows)
    eval_tier = next(t for t in tiers if any(c["action"] == "evaluate" for c in t))
    assert len(eval_tier) == 1


# --------------------------------------------------------------------------------------
# T9: timeline order
# --------------------------------------------------------------------------------------


def test_t9_timeline_places_compliance_layer_between_risk_estimator_and_orchestrator():
    rows = [
        _r(6, "orchestration", "refuse", 9, 1, id=1),
        _r(-10, "constitution_retriever", "domain_prefilter", 0, 1, id=2),
        _route(5),
        _evaluate(0, 3, 1),
        _r(-9, "risk_estimator", "estimate_intent", 1, 1, id=4),
    ]
    modules = [row["module"] for row in _build_execution_timeline(rows)["rows"]]
    assert modules.index("risk_estimator") < modules.index("compliance_layer") < modules.index("orchestrator")
    # unlisted modules render after the listed ones
    for unlisted in ("constitution_retriever", "orchestration"):
        assert modules.index(unlisted) > modules.index("orchestrator")


# --------------------------------------------------------------------------------------
# T10: path-routing anchor
# --------------------------------------------------------------------------------------


@pytest.fixture()
def _routing_data(monkeypatch):
    monkeypatch.setattr(ui_app, "build_orchestrator_observability", lambda *a, **k: {"has_routing_data": True})
    monkeypatch.setattr(
        ui_app,
        "orchestrator_observability_to_io_annotations",
        lambda obs: {"inputs": [], "outputs": [{"label": "x", "value": "y"}]},
    )


def test_t10_path_routing_is_anchored_after_the_last_compliance_row(_routing_data):
    llm_calls = _shape_1166(0)[:-3]  # real rows only (no gate / route / refuse)
    llm_calls = [c for c in llm_calls if not c.get("is_synthetic")]
    last_compliance_end = max(c["started_at"] + c["duration_ms"] for c in llm_calls if c["module"] == "compliance_layer")
    node = ui_app._build_synthetic_path_routing_node([], [], None, llm_calls)
    assert node is not None
    assert node["started_at"] >= last_compliance_end + 1


def test_t10_path_routing_anchor_also_clears_a_late_gate_event(_routing_data):
    llm_calls = [c for c in _shape_1166(0) if not c.get("is_synthetic") and c["module"] != "orchestration"]
    late_gate = _gate(start=20_000)
    node = ui_app._build_synthetic_path_routing_node([], [], None, llm_calls, extra_anchor_nodes=[late_gate])
    assert node is not None
    assert node["started_at"] >= late_gate["started_at"] + late_gate["duration_ms"] + 1


def test_t10_downgrade_node_without_event_time_sorts_after_dccl_rows():
    events = [
        {
            "event_type": COMPLIANCE_MATCH_DOWNGRADED,
            "payload_json": json.dumps({"reason": "hard_signal_evidence", "matched_rule_id": "r1"}),
        }
    ]
    calls = [_evaluate(0), _override(0)]
    nodes = ui_app._synthetic_compliance_downgrade_nodes(events, calls)
    assert nodes[0]["started_at"] > calls[1]["started_at"] + calls[1]["duration_ms"]
    flat = _flat(_group_calls_into_tiers_and_enrich([*calls, nodes[0]]))
    assert flat[-1].startswith("compliance_layer/MATCH blocked")


# --------------------------------------------------------------------------------------
# T13: static rank never contradicts real timestamps; stable under shuffling
# --------------------------------------------------------------------------------------


def _is_speculative_or_retry(c: dict[str, Any]) -> bool:
    return c.get("phase") == "speculative_generate" or c.get("call_outcome") == "retry_failed"


@pytest.mark.parametrize("shape", sorted(_SHAPES))
def test_t13_rank_never_contradicts_timestamps(shape):
    rows = [c for c in _SHAPES[shape]() if not _is_speculative_or_retry(c)]
    for a in rows:
        for b in rows:
            if _visual_tier_for_call(a) < _visual_tier_for_call(b):
                assert b["started_at"] >= a["started_at"] + a["duration_ms"] - 1, (shape, _label(a), _label(b))


@pytest.mark.parametrize("shape", sorted(_SHAPES))
def test_t13_tier_order_is_independent_of_row_insertion_order(shape):
    base = _SHAPES[shape]()
    expected = _tier_labels(_group_calls_into_tiers_and_enrich(copy.deepcopy(base)))
    rng = random.Random(1234)
    for _ in range(25):
        rows = copy.deepcopy(base)
        rng.shuffle(rows)
        assert _tier_labels(_group_calls_into_tiers_and_enrich(rows)) == expected


def test_journey_key_matches_the_legacy_key_for_deliberation_cycles():
    """For cycles >= 1 the new key (delegating to the graph tiers) orders rows exactly as the old
    (cycle, seq, id, started_at, phase) key did, retry_failed critic/hindsight rows included."""

    def old_key(c: dict[str, Any]) -> tuple[Any, ...]:
        cycle = c.get("cycle") if c.get("cycle") is not None else -1
        seq = c.get("sequence_in_cycle") if c.get("sequence_in_cycle") is not None else 999
        return (cycle, seq, c.get("id") or 0, c.get("started_at") or 0, c.get("phase") or "")

    rng = random.Random(1234)
    for _ in range(40):
        rows: list[dict[str, Any]] = []
        next_id = 1
        for cycle in (1, 2):
            for seq in rng.sample([1, 2, 3, 4, 5, 6, 7, 8, 90, None], k=7):
                module = {2: "critic", 5: "hindsight"}.get(seq, "policy") if seq else "policy"
                if module in ("critic", "hindsight") and rng.random() < 0.5:
                    rows.append(
                        {
                            "id": next_id,
                            "cycle": cycle,
                            "sequence_in_cycle": seq,
                            "module": module,
                            "call_outcome": "retry_failed",
                            "started_at": 500 + next_id,
                            "phase": module,
                        }
                    )
                    next_id += 1
                rows.append(
                    {
                        "id": next_id,
                        "cycle": cycle,
                        "sequence_in_cycle": seq,
                        "module": module,
                        "started_at": 500 + next_id,
                        "phase": module,
                    }
                )
                next_id += 1
        shuffled = rows[:]
        rng.shuffle(shuffled)
        assert [c["id"] for c in sorted(shuffled, key=_journey_sort_key)] == [c["id"] for c in sorted(shuffled, key=old_key)]


# --------------------------------------------------------------------------------------
# Page level (tmp SQLite + TestClient)
# --------------------------------------------------------------------------------------


def _seed_routing_debug(run_id: str, request_id: str, at: int) -> None:
    def debug(created_at: int, message: str, data: dict) -> None:
        persist_debug_event(
            run_id=run_id,
            request_id=request_id,
            payload={"location": "orchestrator.py:process", "message": message, "data": data, "timestamp": created_at},
        )

    debug(
        at,
        "branch risk_policy vs deliberative",
        {"risk_policy_action": "REFUSE", "risk_score": 0.9, "threshold_low": 0.3, "decision.path": "REFUSE_PATH"},
    )
    debug(at + 1, "taking refuse path", {"path_taken": "refuse"})


def _seed_1166_page(run_id: str, request_id: str, *, dccl_cycle: int | None) -> None:
    _seed_request(run_id, request_id, path="REFUSE_PATH", total_cycles=0)

    def call(**kw: Any) -> None:
        _call(run_id, request_id, **kw)

    call(
        cycle=0,
        phase="constitution_retrieval",
        module="constitution_retriever",
        action="domain_prefilter",
        sequence_in_cycle=-10,
        started_at=T0,
        duration_ms=480,
        parsed_summary_json=json.dumps({"retrieval_phase": "risk_routing"}),
    )
    call(
        cycle=0,
        phase="constitution_retrieval",
        module="constitution_retriever",
        action="enhanced_domain_agent",
        sequence_in_cycle=-10,
        started_at=T0 + 481,
        duration_ms=600,
        parsed_summary_json=json.dumps({"retrieval_phase": "risk_routing"}),
    )
    for action, dur in (("estimate_intent", 2900), ("estimate_signals", 2950), ("estimate_operational", 3000)):
        call(
            cycle=0,
            phase="risk_estimation",
            module="risk_estimator",
            action=action,
            sequence_in_cycle=-9,
            started_at=T0 + 1800,
            duration_ms=dur,
            raw_response=json.dumps({"risk_score": 0.9, "risk_policy_action": "REFUSE"}),
        )
    call(
        cycle=dccl_cycle,
        phase="compliance_layer",
        module="compliance_layer",
        action="evaluate",
        sequence_in_cycle=-5,
        started_at=T0 + 5000,
        duration_ms=1742.12,
        call_kind="compliance_layer",
    )
    call(
        cycle=dccl_cycle,
        phase="compliance_layer",
        module="compliance_layer",
        action="safety_override",
        sequence_in_cycle=-6,
        started_at=T0 + 6742,
        duration_ms=1871.12,
        call_kind="compliance_layer",
    )
    call(
        cycle=0,
        phase="refusal",
        module="orchestration",
        action="refuse (fast_path)",
        sequence_in_cycle=6,
        started_at=T0 + 8700,
        duration_ms=900,
    )
    persist_orchestration_event(
        run_id=run_id,
        request_id=request_id,
        cycle=0,
        stage="COMPLIANCE",
        component="compliance_layer",
        event_type=COMPLIANCE_MATCH_DOWNGRADED,
        started_at=T0 + 8620,
        payload={"reason": "hard_signal_evidence", "matched_rule_id": "r1"},
    )
    _seed_routing_debug(run_id, request_id, T0 + 8650)
    get_obs().flush()


def _page_order(seq: list[str]) -> list[int]:
    return [
        _idx(seq, "domain_prefilter"),
        _idx(seq, "enhanced_domain_agent"),
        _idx(seq, "estimate_operational"),
        _idx(seq, "evaluate"),
        _idx(seq, "safety_override"),
        _idx(seq, "hard-signal gate"),
        _idx(seq, "route_resolution"),
        _idx(seq, "refuse (fast_path)"),
    ]


def test_t11_legacy_null_page_renders_one_cycle0_group_in_order_with_legacy_badge(ui_client):
    run_id, request_id = "run-ui-dccl-null", "req-ui-dccl-null"
    _seed_1166_page(run_id, request_id, dccl_cycle=None)
    body = _render(ui_client, run_id, request_id)
    seq = _graph_sequence(body)
    assert sum("Initial assessment" in s for s in seq) == 1, seq
    assert not any("Unsequenced" in s for s in seq), seq
    positions = _page_order(seq)
    assert positions == sorted(positions), seq
    assert "legacy row: cycle NULL shown as 0" in body
    gate, refuse = _idx(seq, "hard-signal gate"), _idx(seq, "refuse (fast_path)")
    assert not any("parallel" in s and "2 calls" in s for s in seq[gate : refuse + 1]), seq


def test_t11b_cycle0_rows_render_the_same_graph_without_the_legacy_badge(ui_client):
    legacy = ("run-ui-dccl-b1", "req-ui-dccl-b1")
    new = ("run-ui-dccl-b2", "req-ui-dccl-b2")
    _seed_1166_page(*legacy, dccl_cycle=None)
    _seed_1166_page(*new, dccl_cycle=0)
    legacy_body, new_body = _render(ui_client, *legacy), _render(ui_client, *new)
    assert _graph_sequence(legacy_body) == _graph_sequence(new_body)
    assert "legacy row: cycle NULL shown as 0" in legacy_body
    assert "legacy row: cycle NULL shown as 0" not in new_body


def test_t4_page_labels_the_deferred_tier_as_skipped_modules(ui_client):
    run_id, request_id = "run-ui-dccl-defer", "req-ui-dccl-defer"
    _seed_request(run_id, request_id, path="FAST_PATH", total_cycles=0)
    _call(
        run_id,
        request_id,
        cycle=0,
        phase="compliance_layer",
        module="compliance_layer",
        action="evaluate",
        sequence_in_cycle=-5,
        started_at=T0,
        duration_ms=500,
    )
    for i, module in enumerate(("policy", "critic", "simulator", "perspectives", "hindsight")):
        persist_orchestration_event(
            run_id=run_id,
            request_id=request_id,
            cycle=0,
            stage="COMPLIANCE",
            component="compliance_layer",
            event_type=MODULE_DEFERRED_TO_COMPLIANCE,
            started_at=T0 + 600,
            payload={"module": module, "cycle": 0},
        )
    get_obs().flush()
    seq = _graph_sequence(_render(ui_client, run_id, request_id))
    assert any(s == "skipped &middot; 5 modules" for s in seq), seq
    assert not any("parallel &middot; 5 calls" in s for s in seq), seq
    assert _idx(seq, "evaluate") < _idx(seq, "skipped &middot; 5 modules")


def test_non_compliance_null_cycle_row_stays_visible_as_unsequenced_group(ui_client):
    run_id, request_id = "run-ui-dccl-gap", "req-ui-dccl-gap"
    _seed_request(run_id, request_id, path="FAST_PATH", total_cycles=0)
    _call(
        run_id,
        request_id,
        cycle=0,
        phase="risk_estimation",
        module="risk_estimator",
        action="estimate_intent",
        sequence_in_cycle=-9,
        started_at=T0,
        duration_ms=100,
    )
    _call(
        run_id,
        request_id,
        cycle=None,
        phase="constitution_retrieval",
        module="constitution_retriever",
        action="domain_prefilter",
        sequence_in_cycle=-1,
        started_at=T0 + 200,
        duration_ms=100,
    )
    seq = _graph_sequence(_render(ui_client, run_id, request_id))
    assert any("Unsequenced rows (cycle NULL)" in s for s in seq), seq
    assert sum("Initial assessment" in s for s in seq) == 1, seq


# --------------------------------------------------------------------------------------
# M1: page-level shapes (relative order only, no literal tier numbers)
# --------------------------------------------------------------------------------------


def _seed_deliberative_debug(run_id: str, request_id: str, at: int) -> None:
    persist_debug_event(
        run_id=run_id,
        request_id=request_id,
        payload={
            "location": "orchestrator.py:process",
            "message": "branch risk_policy vs deliberative",
            "data": {
                "risk_policy_action": "NORMAL_COMPLETE",
                "risk_score": 0.5,
                "threshold_low": 0.3,
                "decision.path": "DELIBERATIVE_PATH",
            },
            "timestamp": at,
        },
    )


def _seed_cycle0_prefix(run_id: str, request_id: str, *, path: str) -> None:
    """prefilter -> minis -> DCCL evaluate (-5), shared by the two page shapes below."""
    _seed_request(run_id, request_id, path=path, total_cycles=1)
    _call(
        run_id,
        request_id,
        cycle=0,
        phase="constitution_retrieval",
        module="constitution_retriever",
        action="domain_prefilter",
        sequence_in_cycle=-10,
        started_at=T0,
        duration_ms=480,
        parsed_summary_json=json.dumps({"retrieval_phase": "risk_routing"}),
    )
    for action in ("estimate_intent", "estimate_signals", "estimate_operational"):
        _call(
            run_id,
            request_id,
            cycle=0,
            phase="risk_estimation",
            module="risk_estimator",
            action=action,
            sequence_in_cycle=-9,
            started_at=T0 + 1800,
            duration_ms=1000,
            raw_response=json.dumps({"risk_score": 0.5, "risk_policy_action": "NORMAL_COMPLETE"}),
        )
    _call(
        run_id,
        request_id,
        cycle=0,
        phase="compliance_layer",
        module="compliance_layer",
        action="evaluate",
        sequence_in_cycle=-5,
        started_at=T0 + 3000,
        duration_ms=1000,
        call_kind="compliance_layer",
    )


def test_page_no_match_to_deliberative_orders_evaluate_routing_retrieval_then_critic_cycle(ui_client):
    run_id, request_id = "run-ui-dccl-delib", "req-ui-dccl-delib"
    _seed_cycle0_prefix(run_id, request_id, path="DELIBERATIVE_PATH")
    _call(
        run_id,
        request_id,
        cycle=0,
        phase="constitution_retrieval",
        module="constitution_retriever",
        action="domain_prefilter",
        sequence_in_cycle=-1,
        started_at=T0 + 4200,
        duration_ms=300,
        parsed_summary_json=json.dumps({"retrieval_phase": "deliberation"}),
    )
    _call(
        run_id,
        request_id,
        cycle=1,
        phase="critic",
        module="critic",
        action="critique",
        sequence_in_cycle=2,
        started_at=T0 + 5000,
        duration_ms=500,
    )
    _seed_deliberative_debug(run_id, request_id, T0 + 4100)
    get_obs().flush()
    seq = _graph_sequence(_render(ui_client, run_id, request_id))
    evaluate = _idx(seq, "evaluate")
    routing = _idx(seq, "route_resolution")
    # the deliberation retrieval prefilter is the second one (the first is the risk-routing prefilter)
    risk_prefilter = _idx(seq, "domain_prefilter")
    delib_prefilter = _idx(seq, "domain_prefilter", risk_prefilter + 1)
    critic = _idx(seq, "critique")
    assert risk_prefilter < _idx(seq, "estimate_operational") < evaluate < routing < delib_prefilter < critic, seq
    assert sum("Initial assessment" in s for s in seq) == 1, seq
    cycle1_marker = next(i for i, s in enumerate(seq) if s.startswith("Deliberation"))
    assert cycle1_marker < critic and cycle1_marker > delib_prefilter, seq


def test_page_regenerate_revalidate_downgrade_route_fast_draft_quick_check_order(ui_client):
    run_id, request_id = "run-ui-dccl-regen", "req-ui-dccl-regen"
    _seed_cycle0_prefix(run_id, request_id, path="FAST_PATH")
    _call(
        run_id,
        request_id,
        cycle=0,
        phase="compliance_layer",
        module="compliance_layer",
        action="safety_override",
        sequence_in_cycle=-6,
        started_at=T0 + 4100,
        duration_ms=300,
        call_kind="compliance_layer",
    )
    _call(
        run_id,
        request_id,
        cycle=0,
        phase="policy_generate",
        module="policy",
        action="generate (compliance-regenerate)",
        sequence_in_cycle=1,
        started_at=T0 + 4500,
        duration_ms=1000,
        call_kind="compliance_regenerate",
    )
    _call(
        run_id,
        request_id,
        cycle=0,
        phase="compliance_layer",
        module="compliance_layer",
        action="draft_revalidate",
        sequence_in_cycle=-4,
        started_at=T0 + 5600,
        duration_ms=400,
        call_kind="compliance_layer",
    )
    persist_orchestration_event(
        run_id=run_id,
        request_id=request_id,
        cycle=0,
        stage="COMPLIANCE",
        component="compliance_layer",
        event_type=COMPLIANCE_MATCH_DOWNGRADED,
        started_at=T0 + 6100,
        payload={"reason": "regenerated_draft_unvalidated", "matched_rule_id": "r1"},
    )
    _seed_deliberative_debug(run_id, request_id, T0 + 6200)
    _call(
        run_id,
        request_id,
        cycle=0,
        phase="policy_generate",
        module="policy",
        action="generate (fast_path)",
        sequence_in_cycle=1,
        started_at=T0 + 6400,
        duration_ms=800,
    )
    _call(
        run_id,
        request_id,
        cycle=0,
        phase="critic",
        module="critic",
        action="quick_check",
        sequence_in_cycle=2,
        started_at=T0 + 7300,
        duration_ms=300,
    )
    get_obs().flush()
    seq = _graph_sequence(_render(ui_client, run_id, request_id))
    order = [
        _idx(seq, "evaluate"),
        _idx(seq, "safety_override"),
        _idx(seq, "generate (compliance-regenerate)"),
        _idx(seq, "draft_revalidate"),
        _idx(seq, "MATCH downgraded"),
        _idx(seq, "route_resolution"),
        _idx(seq, "generate (fast_path)"),
        _idx(seq, "quick_check"),
    ]
    assert order == sorted(order) and len(set(order)) == len(order), seq


# --------------------------------------------------------------------------------------
# RV12: the speculative draft joins the cycle-0 risk/retrieval tier it overlaps most (from timings)
# --------------------------------------------------------------------------------------


def _spec_tier_members(rows: list[dict[str, Any]]) -> set[str]:
    """Labels of the graph tier that holds the speculative row."""
    spec_label = "policy/generate (speculative)"
    for tier in _group_calls_into_tiers_and_enrich(rows):
        if spec_label in {_label(c) for c in tier}:
            return {_label(c) for c in tier}
    raise AssertionError("speculative row missing from the graph")


def _spec_rows(spec: dict[str, Any], *, with_agents: bool = True, with_minis: bool = True) -> list[dict[str, Any]]:
    rows = [_prefilter(), spec]
    if with_agents:
        rows.extend(_agents())
    if with_minis:
        rows.extend(_minis())
    return rows


def test_rv12_speculative_overlapping_prefilter_joins_the_prefilter_tier():
    # Request 1166f216: prefilter 0..1572, speculative 2..1106 (entirely inside the prefilter window).
    rows = _spec_rows(_speculative(2, 1104), with_agents=False)
    rows[0] = _prefilter(0, 1572)
    members = _spec_tier_members(rows)
    assert "constitution_retriever/domain_prefilter" in members
    assert not any(m.startswith("risk_estimator/") for m in members)


def test_rv12_speculative_overlapping_agents_joins_the_agents_tier():
    # prefilter 0..480, agents 481..1081 / 557..1257, speculative 600..1200 (all inside the agents union window).
    members = _spec_tier_members(_spec_rows(_speculative(600, 600)))
    assert "constitution_retriever/enhanced_domain_agent" in members
    assert "constitution_retriever/domain_prefilter" not in members


def test_rv12_speculative_overlapping_minis_stays_in_the_minis_tier():
    members = _spec_tier_members(_spec_rows(_speculative(1800, 3000)))
    assert "risk_estimator/estimate_intent" in members


@pytest.mark.parametrize("missing", ["duration", "started_at", "zero_duration"])
def test_rv12_speculative_without_timing_defaults_to_the_minis_tier(missing: str):
    spec = _speculative(2, 1104)
    if missing == "duration":
        spec["duration_ms"] = None
    elif missing == "started_at":
        spec["started_at"] = None
    else:
        spec["duration_ms"] = 0
    members = _spec_tier_members(_spec_rows(spec))
    assert "risk_estimator/estimate_intent" in members


def test_rv12_speculative_without_risk_rows_uses_the_default_tier():
    spec = _speculative(2, 1104)
    tiers = _group_calls_into_tiers_and_enrich([spec])
    assert len(tiers) == 1 and tiers[0] == [spec]
    # Same tier rank as the legacy default (minis), so it still precedes calibration.
    minis_tier = _visual_tier_for_call(_minis()[0])
    assert _visual_tier_for_call(spec) == minis_tier


def test_rv12_gap_between_risk_windows_keeps_the_default_tier():
    # Timing + risk rows present, but the speculative window (200..300) overlaps no risk tier: no stamp.
    spec = _speculative(200, 100)
    minis = [_r(-9, "risk_estimator", "estimate_intent", 5000, 100, id=5, phase="risk_estimation")]
    rows = [_prefilter(0, 100), minis[0], spec]
    members = _spec_tier_members(rows)
    assert "risk_estimator/estimate_intent" in members
    assert "_visual_tier_override" not in spec
    assert _visual_tier_for_call(spec) == _visual_tier_for_call(minis[0])


def test_rv12_none_cycle_risk_like_row_does_not_widen_the_window():
    # A None-cycle prefilter-like row overlapping the speculative must not count (graph keeps None rows apart).
    spec = _speculative(2000, 500)
    stray = _r(-10, "constitution_retriever", "domain_prefilter", 2000, 500, id=30, cycle=None)
    minis = [_r(-9, "risk_estimator", "estimate_intent", 5000, 100, id=5, phase="risk_estimation")]
    ui_app._stamp_speculative_tier_overrides([stray, minis[0], spec])
    assert "_visual_tier_override" not in spec
    assert not ui_app._is_cycle0_speculative_draft({**spec, "cycle": None})


def test_rv12_tie_goes_to_the_earliest_tier():
    # prefilter 0..100, minis 100..200 (no agents): speculative 50..150 overlaps both by 50 ms.
    prefilter = _prefilter(0, 100)
    minis = [_r(-9, "risk_estimator", "estimate_intent", 100, 100, id=5, phase="risk_estimation")]
    members = _spec_tier_members([prefilter, minis[0], _speculative(50, 100)])
    assert "constitution_retriever/domain_prefilter" in members


def test_rv12_graph_and_journey_agree_on_the_speculative_position():
    rows = [
        *_spec_rows(_speculative(2, 1104)),
        _calibrate(),
        _evaluate(0),
    ]
    rows[0] = _prefilter(0, 1572)
    graph = _flat(_group_calls_into_tiers_and_enrich(rows))
    journey = [_label(c) for c in sorted(rows, key=_journey_sort_key)]
    assert journey == graph
    assert journey.index("policy/generate (speculative)") < journey.index("constitution_retriever/enhanced_domain_agent")
    assert journey.index("constitution_retriever/domain_prefilter") < journey.index("policy/generate (speculative)")


def test_rv12_hydrated_reuse_rows_are_not_repositioned():
    reuse = _r(1, "policy", "generate (speculative-reuse)", 2, 1104, id=20, phase="policy_generate")
    rows = [_prefilter(0, 1572), *_minis(), reuse]
    assert _visual_tier_for_call(reuse) == _visual_tier_for_call(reuse)  # no override stamped
    _group_calls_into_tiers_and_enrich(rows)
    assert "_visual_tier_override" not in reuse


def test_rv12_upstream_speculative_row_is_placed_like_the_policy_one():
    spec = _speculative(2, 1104)
    spec["module"] = "upstream_speculative"
    rows = [_prefilter(0, 1572), spec, *_minis()]
    tiers = _group_calls_into_tiers_and_enrich(rows)
    first = {_label(c) for c in tiers[0]}
    assert "upstream_speculative/generate (speculative)" in first


# --------------------------------------------------------------------------------------
# RV13: connector labels into compliance nodes say what flows
# --------------------------------------------------------------------------------------


def _label_into(dst: dict[str, Any]) -> str | None:
    src = _r(-9, "risk_estimator", "estimate_intent", 0, 10)
    return ui_app._compute_connector_labels([[src], [dst]])[0]


def test_rv13_into_evaluate_says_risk_and_draft():
    assert _label_into(_evaluate(0)) == "risk + draft → DCCL"


def test_rv13_into_safety_override_after_evaluate_says_verdict():
    labels = ui_app._compute_connector_labels([[_evaluate(0)], [_override(0)]])
    assert labels == ["verdict → safety check"]


def test_rv13_into_safety_override_on_structured_path_says_risk_and_draft():
    labels = ui_app._compute_connector_labels([[_calibrate()], [_override(0)]])
    assert labels == ["risk + draft → DCCL"]


def test_rv13_into_hard_signal_gate_node():
    labels = ui_app._compute_connector_labels([[_override(0)], [_gate()]])
    assert labels == ["hard-signal gate (P0)"]


def test_rv13_into_ordinary_downgrade_node():
    labels = ui_app._compute_connector_labels([[_r(-4, "compliance_layer", "draft_revalidate", 0, 5)], [_downgrade(10)]])
    assert labels == ["MATCH downgraded"]


def test_rv13_into_draft_revalidate_says_regenerated_draft():
    regen = _r(-4, "compliance_layer", "regenerate", 0, 5)
    reval = _r(-4, "compliance_layer", "draft_revalidate", 10, 5)
    assert ui_app._compute_connector_labels([[regen], [reval]]) == ["regenerated draft → revalidate"]


def test_rv13_other_labels_are_unchanged():
    assert ui_app._compute_connector_labels([[_prefilter()], [_minis()[0]]]) == [None]
    pol = _r(1, "policy", "generate", 0, 5, phase="policy_generate")
    crit = _r(2, "critic", "critique", 10, 5, phase="critic")
    assert ui_app._compute_connector_labels([[pol], [crit]]) == ["draft"]
