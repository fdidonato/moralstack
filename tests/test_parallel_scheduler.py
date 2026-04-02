"""Tests for risk-aware parallel deliberation scheduling (critic_gated vs full_parallel)."""

from __future__ import annotations

from dataclasses import replace
from unittest.mock import MagicMock

from moralstack.models.risk.schema import RiskEstimation
from moralstack.orchestration.deliberation_runner import DeliberationRunner, ParallelStrategySelection
from moralstack.orchestration.types import DeliberationDependencies, DeliberationState, OrchestratorConfig


def _minimal_runner(cfg: OrchestratorConfig | None = None) -> DeliberationRunner:
    cfg = cfg or OrchestratorConfig(
        max_deliberation_cycles=1,
        parallel_module_calls=True,
        enable_dynamic_parallel_scheduler=True,
    )
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


def test_select_full_parallel_benign():
    runner = _minimal_runner()
    est = RiskEstimation.benign()
    state = DeliberationState()
    sel = runner._select_parallel_strategy(risk_estimation=est, state=state)
    assert isinstance(sel, ParallelStrategySelection)
    assert sel.strategy == "full_parallel"
    assert "DEFAULT_LOWER_RISK_PARALLEL" in sel.reason_codes


def test_select_critic_gated_clearly_harmful():
    runner = _minimal_runner()
    est = RiskEstimation.clearly_harmful(["x"])
    state = DeliberationState()
    sel = runner._select_parallel_strategy(risk_estimation=est, state=state)
    assert sel.strategy == "critic_gated"
    assert "HIGH_RISK_POSTURE" in sel.reason_codes
    assert "OPERATIONAL_RISK_HIGH" in sel.reason_codes
    assert "RISK_POLICY_ACTION_DENY" in sel.reason_codes


def test_select_critic_gated_intent_to_harm():
    runner = _minimal_runner()
    est = replace(RiskEstimation.benign(), intent_to_harm=True)
    state = DeliberationState()
    sel = runner._select_parallel_strategy(risk_estimation=est, state=state)
    assert sel.strategy == "critic_gated"
    assert "INTENT_TO_HARM_TRUE" in sel.reason_codes


def test_select_critic_gated_prior_hard_violation_critique():
    runner = _minimal_runner()
    est = RiskEstimation.benign()
    prev = MagicMock()
    prev.violated_hard = True
    state = DeliberationState(critiques=[prev])
    sel = runner._select_parallel_strategy(risk_estimation=est, state=state)
    assert sel.strategy == "critic_gated"
    assert sel.reason_codes[0] == "PREVIOUS_HARD_VIOLATION"


def test_select_fallback_no_risk_estimation():
    runner = _minimal_runner(
        OrchestratorConfig(
            max_deliberation_cycles=1,
            parallel_module_calls=True,
            parallel_critic_with_modules=True,
            enable_dynamic_parallel_scheduler=True,
        )
    )
    state = DeliberationState()
    sel = runner._select_parallel_strategy(risk_estimation=None, state=state)
    assert sel.strategy == "full_parallel"
    assert sel.reason_codes == ("CONFIG_FALLBACK_NO_RISK_ESTIMATION",)


def test_select_fallback_no_risk_respects_parallel_critic_false():
    runner = _minimal_runner(
        OrchestratorConfig(
            max_deliberation_cycles=1,
            parallel_module_calls=True,
            parallel_critic_with_modules=False,
            enable_dynamic_parallel_scheduler=True,
        )
    )
    sel = runner._select_parallel_strategy(risk_estimation=None, state=DeliberationState())
    assert sel.strategy == "critic_gated"


def test_execution_strategy_parallel_scheduler_rollup():
    from moralstack.reports.runtime_decisions import build_execution_strategy

    traces = [
        {
            "stage": "CYCLE_SUMMARY",
            "trace_json": '{"stage_payload": {"cycle": 1, "scheduler_strategy": "critic_gated", '
            '"scheduler_reason_codes": ["HIGH_RISK_POSTURE"], "critic_short_circuit": false}}',
        }
    ]
    es = build_execution_strategy(traces, orchestration_events=[])
    rows = es.get("parallel_scheduler_by_cycle") or []
    assert len(rows) == 1
    assert rows[0].get("strategy") == "critic_gated"
    assert rows[0].get("reason_codes") == ["HIGH_RISK_POSTURE"]


def test_build_cycle_cards_scheduler_fields():
    from moralstack.reports.runtime_decisions import build_cycle_cards

    traces = [
        {
            "stage": "CYCLE_SUMMARY",
            "trace_json": '{"stage_payload": {"cycle": 1, "scheduler_strategy": "full_parallel", '
            '"scheduler_reason_codes": ["DEFAULT_LOWER_RISK_PARALLEL"], "critic_short_circuit": false}}',
        }
    ]
    cards = build_cycle_cards(traces, orchestration_events=[])
    assert len(cards) == 1
    assert cards[0].get("scheduler_reason_codes") == ["DEFAULT_LOWER_RISK_PARALLEL"]


def test_scheduler_events_count_in_execution_strategy():
    from moralstack.reports.runtime_decisions import build_execution_strategy

    orch = [
        {"event_type": "PARALLEL_STRATEGY_SELECTED", "payload_json": "{}"},
        {"event_type": "CRITIC_SHORT_CIRCUIT_TRIGGERED", "payload_json": "{}"},
    ]
    es = build_execution_strategy([], orchestration_events=orch)
    assert es.get("scheduler_events_count") == 2
