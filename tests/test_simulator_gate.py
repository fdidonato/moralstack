"""Tests for conservative simulator gating (run vs skip) and observability."""

from __future__ import annotations

from dataclasses import replace
from unittest.mock import MagicMock

from moralstack.models.risk.categories import RiskCategory
from moralstack.models.risk.schema import RiskEstimation
from moralstack.orchestration.deliberation_runner import DeliberationRunner
from moralstack.orchestration.types import DeliberationDependencies, DeliberationState, OrchestratorConfig


def _runner(cfg: OrchestratorConfig) -> DeliberationRunner:
    deps = DeliberationDependencies(
        policy=None,
        critic=None,
        simulator=None,
        hindsight=None,
        perspectives=None,
        constitution_store=None,
        output_protector=MagicMock(),
    )
    return DeliberationRunner(
        cfg,
        deps,
        protected_system_prompt="sys",
        logger=None,
        assembler=MagicMock(),
    )


def _prev_sim(sem: float) -> MagicMock:
    m = MagicMock()
    m.semantic_expected_harm = sem
    return m


def test_cycle_one_always_run():
    cfg = OrchestratorConfig(enable_simulator_gating=True)
    r = _runner(cfg)
    st = DeliberationState(cycle=1, simulations=[_prev_sim(0.9)])
    g = r._evaluate_simulator_gate(
        st,
        RiskEstimation.benign(),
        None,
        1,
        current_critique_available=True,
    )
    assert g.should_run is True
    assert "FIRST_CYCLE_REQUIRE_RUN" in g.reason_codes


def test_gating_disabled_always_run():
    cfg = OrchestratorConfig(enable_simulator_gating=False)
    r = _runner(cfg)
    st = DeliberationState(cycle=2, simulations=[_prev_sim(0.05)])
    g = r._evaluate_simulator_gate(
        st,
        RiskEstimation.benign(),
        None,
        2,
        current_critique_available=True,
    )
    assert g.should_run is True
    assert "GATING_DISABLED_ALWAYS_RUN" in g.reason_codes


def test_high_risk_requires_run():
    cfg = OrchestratorConfig(enable_simulator_gating=True)
    r = _runner(cfg)
    st = DeliberationState(cycle=2, simulations=[_prev_sim(0.05)])
    g = r._evaluate_simulator_gate(
        st,
        RiskEstimation.clearly_harmful(["x"]),
        None,
        2,
        current_critique_available=True,
    )
    assert g.should_run is True
    assert "HIGH_RISK_POSTURE_REQUIRE_RUN" in g.reason_codes


def test_prior_harm_elevated_requires_run():
    cfg = OrchestratorConfig(enable_simulator_gating=True, simulator_gate_semantic_harm_threshold=0.4)
    r = _runner(cfg)
    st = DeliberationState(cycle=2, simulations=[_prev_sim(0.5)])
    g = r._evaluate_simulator_gate(
        st,
        RiskEstimation.benign(),
        None,
        2,
        current_critique_available=True,
    )
    assert g.should_run is True
    assert "PRIOR_SEMANTIC_HARM_ELEVATED_REQUIRE_RUN" in g.reason_codes


def test_borderline_band_requires_run():
    cfg = OrchestratorConfig(
        enable_simulator_gating=True,
        simulator_gate_semantic_harm_threshold=0.4,
        simulator_gate_skip_max_prior_semantic_harm=0.25,
    )
    r = _runner(cfg)
    st = DeliberationState(cycle=2, simulations=[_prev_sim(0.3)])
    g = r._evaluate_simulator_gate(
        st,
        RiskEstimation.benign(),
        None,
        2,
        current_critique_available=True,
    )
    assert g.should_run is True
    assert "PRIOR_HARM_BORDERLINE_BAND_REQUIRE_RUN" in g.reason_codes


def test_critique_violations_require_run():
    cfg = OrchestratorConfig(enable_simulator_gating=True)
    r = _runner(cfg)
    st = DeliberationState(cycle=2, simulations=[_prev_sim(0.05)])
    crit = MagicMock()
    crit.violated_hard = False
    crit.has_critical_violations = False
    crit.decision = "PROCEED"
    crit.violations = [MagicMock()]
    st.critiques = [crit]
    g = r._evaluate_simulator_gate(
        st,
        RiskEstimation.benign(),
        None,
        2,
        current_critique_available=True,
    )
    assert g.should_run is True
    assert "CURRENT_CRITIC_VIOLATIONS_PRESENT_REQUIRE_RUN" in g.reason_codes


def test_conservative_skip_post_critique():
    cfg = OrchestratorConfig(
        enable_simulator_gating=True,
        simulator_gate_semantic_harm_threshold=0.4,
        simulator_gate_skip_max_prior_semantic_harm=0.25,
        simulator_gate_delta_chars_threshold=500,
    )
    r = _runner(cfg)
    st = DeliberationState(cycle=2, simulations=[_prev_sim(0.1)])
    crit = MagicMock()
    crit.violated_hard = False
    crit.has_critical_violations = False
    crit.decision = "PROCEED"
    crit.violations = []
    st.critiques = [crit]
    g = r._evaluate_simulator_gate(
        st,
        RiskEstimation.benign(),
        None,
        2,
        current_critique_available=True,
    )
    assert g.should_run is False
    assert "LOW_PRIOR_HARM_CONSERVATIVE_SKIP" in g.reason_codes


def test_parallel_precritic_insufficient_when_not_benign():
    cfg = OrchestratorConfig(
        enable_simulator_gating=True,
        simulator_gate_semantic_harm_threshold=0.4,
        simulator_gate_skip_max_prior_semantic_harm=0.25,
        simulator_gate_delta_chars_threshold=500,
    )
    r = _runner(cfg)
    st = DeliberationState(cycle=2, simulations=[_prev_sim(0.1)])
    est = replace(RiskEstimation.benign(), risk_category=RiskCategory.SENSITIVE)
    g = r._evaluate_simulator_gate(
        st,
        est,
        None,
        2,
        current_critique_available=False,
    )
    assert g.should_run is True
    assert "PARALLEL_PRECRITIC_INSUFFICIENT_SIGNAL_REQUIRE_RUN" in g.reason_codes


def test_gating_view_model_from_traces():
    from moralstack.reports.runtime_decisions import build_execution_strategy

    traces = [
        {
            "stage": "CYCLE_SUMMARY",
            "trace_json": '{"stage_payload": {"cycle": 2, "simulator_ran_this_cycle": false, '
            '"simulator_gate_enabled": true, "simulator_gate_reason_codes": ["LOW_PRIOR_HARM_CONSERVATIVE_SKIP"], '
            '"simulator_carry_forward": true}}',
        }
    ]
    es = build_execution_strategy(traces, orchestration_events=[])
    rows = es.get("simulator_gating_by_cycle") or []
    assert len(rows) == 1
    assert rows[0].get("status") == "skipped"
    assert rows[0].get("carry_forward") is True
