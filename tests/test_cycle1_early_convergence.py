"""Tests for conservative cycle-1 early convergence (Plan 9) and observability rollups."""

from __future__ import annotations

import json

from moralstack.models.risk.categories import RiskCategory
from moralstack.orchestration.convergence_evaluator import ConvergenceEvaluator, _evaluate_cycle1_early_convergence
from moralstack.orchestration.types import DecisionType, DeliberationState, OrchestratorConfig
from moralstack.reports.runtime_decisions import build_execution_strategy, build_runtime_decision_observability


def _clean_critique():
    return type(
        "C",
        (),
        {
            "violations": [],
            "violated_hard": False,
            "decision": "PROCEED",
            "revision_guidance": "",
            "has_critical_violations": False,
        },
    )()


def _sim(low_harm: float = 0.05, valence: float = 0.5, dom_harms: list[str] | None = None):
    return type(
        "S",
        (),
        {
            "semantic_expected_harm": low_harm,
            "expected_valence": valence,
            "dominant_harm_types": dom_harms or [],
        },
    )()


def _persp(score: float = 0.92, pid: str = "user"):
    return type(
        "P",
        (),
        {
            "perspective_id": pid,
            "approval_score": score,
            "concerns": [],
            "suggestions": [],
        },
    )()


def _low_risk():
    from tests.test_orchestrator import MockRiskEstimation

    return MockRiskEstimation(
        score=0.15,
        risk_category=RiskCategory.BENIGN,
    )


def test_cycle1_strong_alignment_accepted():
    cfg = OrchestratorConfig(
        max_deliberation_cycles=2,
        enable_simulation=True,
        enable_perspectives=True,
    )
    st = DeliberationState(
        cycle=1,
        critiques=[_clean_critique()],
        simulations=[_sim()],
        perspectives=[_persp(0.92), _persp(0.91, "observer")],
    )
    ev = ConvergenceEvaluator(cfg)
    d = ev.determine_decision(st, risk_estimation=_low_risk())
    assert d == DecisionType.CONVERGED
    snap = st._convergence_evaluation_snapshot
    assert snap is not None
    assert snap.get("early_convergence_accepted") is True
    assert "CRITIC_CLEAN" in (snap.get("convergence_reason_codes") or [])


def test_cycle1_hard_violation_rejects_before_early():
    cfg = OrchestratorConfig()
    st = DeliberationState(
        cycle=1,
        critiques=[
            type(
                "C",
                (),
                {
                    "violations": [],
                    "violated_hard": True,
                    "decision": "REVISE",
                    "revision_guidance": "",
                    "has_critical_violations": False,
                },
            )()
        ],
        simulations=[_sim()],
        perspectives=[_persp()],
    )
    ev = ConvergenceEvaluator(cfg)
    d = ev.determine_decision(st, risk_estimation=_low_risk())
    assert d == DecisionType.REVISE
    snap = st._convergence_evaluation_snapshot
    assert snap.get("early_convergence_considered") is False


def test_cycle1_weak_perspectives_rejected():
    cfg = OrchestratorConfig()
    st = DeliberationState(
        cycle=1,
        critiques=[_clean_critique()],
        simulations=[_sim()],
        perspectives=[_persp(0.5)],
    )
    ev = ConvergenceEvaluator(cfg)
    d = ev.determine_decision(st, risk_estimation=_low_risk())
    assert d in (DecisionType.CONTINUE, DecisionType.REVISE)
    snap = st._convergence_evaluation_snapshot
    assert snap.get("early_convergence_considered") is True
    assert snap.get("early_convergence_accepted") is False


def test_cycle1_high_simulator_harm_rejected():
    cfg = OrchestratorConfig()
    # Nontrivial semantic harm: early convergence rejects; vote tally must favor REVISE
    st = DeliberationState(
        cycle=1,
        critiques=[_clean_critique()],
        simulations=[
            _sim(low_harm=0.65, valence=0.5, dom_harms=["physical_harm"]),
        ],
        perspectives=[_persp(0.95), _persp(0.94, "observer")],
    )
    ev = ConvergenceEvaluator(cfg)
    d = ev.determine_decision(st, risk_estimation=_low_risk())
    assert d == DecisionType.REVISE
    snap = st._convergence_evaluation_snapshot
    assert snap.get("early_convergence_accepted") is False
    rc = snap.get("convergence_reason_codes") or []
    assert any("SIMULATOR" in x or "VOTE" in x for x in rc)


def test_cycle1_missing_simulation_when_enabled():
    cfg = OrchestratorConfig(enable_simulation=True)
    st = DeliberationState(
        cycle=1,
        critiques=[_clean_critique()],
        simulations=[],
        perspectives=[_persp(0.95), _persp(0.94, "observer")],
    )
    r = _evaluate_cycle1_early_convergence(st, cfg, _low_risk())
    assert r.accepted is False
    assert "INSUFFICIENT_EVIDENCE" in r.reason_codes


def test_cycle2_uses_legacy_perspectives_path_not_cycle1_helper():
    cfg = OrchestratorConfig(early_exit_perspectives_threshold=0.85)
    st = DeliberationState(
        cycle=2,
        critiques=[_clean_critique()],
        simulations=[_sim()],
        perspectives=[_persp(0.9), _persp(0.89, "observer")],
    )
    ev = ConvergenceEvaluator(cfg)
    d = ev.determine_decision(st, risk_estimation=_low_risk())
    assert d == DecisionType.CONVERGED
    snap = st._convergence_evaluation_snapshot
    assert snap.get("early_convergence_considered") is False
    assert "LEGACY_PERSPECTIVES_EARLY_EXIT" in (snap.get("convergence_reason_codes") or [])


def test_build_execution_strategy_convergence_rollup():
    traces = [
        {
            "stage": "CYCLE_SUMMARY",
            "trace_json": json.dumps(
                {
                    "stage_payload": {
                        "cycle": 1,
                        "early_convergence_considered": True,
                        "early_convergence_accepted": False,
                        "convergence_reason_codes": ["PERSPECTIVES_NOT_STRONG_ENOUGH"],
                        "deliberation_decision": "continue",
                    }
                }
            ),
        }
    ]
    es = build_execution_strategy(traces, orchestration_events=[])
    conv = es.get("convergence") or {}
    assert conv.get("cycle1_early_convergence_considered") is True
    assert conv.get("cycle1_early_convergence_accepted") is False
    assert "PERSPECTIVES_NOT_STRONG_ENOUGH" in (conv.get("cycle1_convergence_reason_codes") or [])


def test_build_runtime_observability_cycle_card_fields():
    traces = [
        {
            "stage": "CYCLE_SUMMARY",
            "trace_json": json.dumps(
                {
                    "stage_payload": {
                        "cycle": 1,
                        "deliberation_decision": "converged",
                        "early_convergence_considered": True,
                        "early_convergence_accepted": True,
                        "convergence_reason_codes": ["CRITIC_CLEAN"],
                    }
                }
            ),
        }
    ]
    vm = build_runtime_decision_observability(traces=traces, orchestration_events=[], llm_calls=[])
    cards = vm.get("cycle_cards") or []
    assert len(cards) == 1
    c0 = cards[0]
    assert c0.get("deliberation_decision") == "converged"
    assert c0.get("early_convergence_considered") is True
    assert c0.get("early_convergence_accepted") is True


def test_legacy_traces_without_convergence_fields():
    traces = [
        {
            "stage": "CYCLE_SUMMARY",
            "trace_json": json.dumps({"stage_payload": {"cycle": 1, "critic_decision": "PROCEED"}}),
        }
    ]
    es = build_execution_strategy(traces, orchestration_events=[])
    conv = es.get("convergence") or {}
    assert conv.get("cycle1_early_convergence_considered") is None
