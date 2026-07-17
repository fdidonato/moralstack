"""
Locks the FINAL-trace `sim_metrics_measured` marker.

Context: (sim_semantic_expected_harm=0.0, sim_worst_harm=None) is produced BOTH by
"no retained simulation" and by "a simulation was retained and every consequence was
benign" (simulator_module.py:671-684 skips harm_type == "none", leaving risk_records
empty). No metric field separates the two, so `sim_metrics_measured` is the only signal
telling whether those metrics are a real measurement or defaults.

The field is NOT "did the simulator module execute": a full-parallel simulation can run
and then be discarded on a critic hard violation (_run_full_parallel_evaluation), leaving
the FINAL metrics at their defaults — that case must read `sim_metrics_measured=False`.
The final test drives that real discard path rather than asserting the claim by stub.

The benign SimulationResult here is built through the REAL aggregation
(LLMConsequenceSimulator._build_result), not hand-assembled: a stub asserting 0.0/None
would only restate the premise instead of demonstrating it.
"""

from __future__ import annotations

import types
from unittest.mock import MagicMock

from moralstack.models.risk import RiskCategory, RiskEstimation
from moralstack.orchestration import decision_service
from moralstack.orchestration.decision_service import _populate_trace_from_sim, decide_action
from moralstack.orchestration.deliberation_runner import DeliberationRunner
from moralstack.orchestration.types import (
    DeliberationDependencies,
    DeliberationState,
    OrchestratorConfig,
    ProcessedRequest,
)
from moralstack.runtime.modules.simulator_module import (
    Consequence,
    LLMConsequenceSimulator,
    SimulatorConfig,
)
from moralstack.runtime.trace.decision_trace import DecisionTrace


def _benign_simulation_result():
    """A real simulator output whose consequences all carry harm_type 'none'."""
    simulator = LLMConsequenceSimulator(policy=MagicMock(), config=SimulatorConfig())
    consequences = [
        Consequence(text="user learns the recipe", likelihood=0.9, outcome_valence=0.5, harm_type="none"),
        Consequence(text="user cooks dinner", likelihood=0.7, outcome_valence=0.3, harm_type="none"),
    ]
    return simulator._build_result(consequences, raw_response="{}", parse_attempts=1)


def test_benign_run_is_indistinguishable_from_never_measured_on_metrics_alone():
    """Premise check: the metric fields genuinely collide, so the marker is necessary."""
    benign = _benign_simulation_result()
    assert benign.semantic_expected_harm == 0.0
    assert benign.worst_harm is None

    not_measured = DecisionTrace(request_id="req-none")
    _populate_trace_from_sim(not_measured, None)
    measured_benign = DecisionTrace(request_id="req-benign")
    _populate_trace_from_sim(measured_benign, benign)

    assert not_measured.sim_semantic_expected_harm == measured_benign.sim_semantic_expected_harm == 0.0
    assert not_measured.sim_worst_harm is measured_benign.sim_worst_harm is None


def test_marker_separates_not_measured_from_measured_benign():
    not_measured = DecisionTrace(request_id="req-none")
    _populate_trace_from_sim(not_measured, None)
    measured_benign = DecisionTrace(request_id="req-benign")
    _populate_trace_from_sim(measured_benign, _benign_simulation_result())

    assert not_measured.sim_metrics_measured is False
    assert measured_benign.sim_metrics_measured is True


def test_marker_defaults_to_none_when_not_asserted():
    """Tri-state: an untouched trace must not claim the metrics are (un)measured."""
    trace = DecisionTrace(request_id="req-default")
    assert trace.sim_metrics_measured is None
    assert trace.to_dict()["sim_metrics_measured"] is None


def test_marker_survives_to_dict():
    trace = DecisionTrace(request_id="req-dict")
    _populate_trace_from_sim(trace, _benign_simulation_result())
    assert trace.to_dict()["sim_metrics_measured"] is True


def _capture_final_trace(monkeypatch) -> list[DecisionTrace]:
    """Captures every trace decide_action appends, so the FINAL row can be inspected."""
    captured: list[DecisionTrace] = []
    monkeypatch.setattr(decision_service, "append_decision_trace", lambda trace, *a, **k: captured.append(trace))
    return captured


def _decide(sim_result, monkeypatch) -> DecisionTrace:
    captured = _capture_final_trace(monkeypatch)
    risk = RiskEstimation(score=0.1, confidence=0.9, risk_category=RiskCategory.BENIGN)
    decide_action(ProcessedRequest(prompt="How do I bake bread?"), risk, sim_result=sim_result)
    final = [t for t in captured if t.stage == "FINAL"]
    assert len(final) == 1, f"expected exactly one FINAL trace, got {len(final)}"
    return final[0]


def test_final_trace_measured_true_for_retained_benign_result(monkeypatch):
    """The persisted FINAL row — not just the PRE trace — must carry the marker."""
    final = _decide(_benign_simulation_result(), monkeypatch)
    assert final.sim_metrics_measured is True
    # The marker is the only thing separating this row from a not-measured one.
    assert final.sim_semantic_expected_harm == 0.0
    assert final.sim_worst_harm is None


def test_final_trace_measured_false_when_no_result_retained(monkeypatch):
    final = _decide(None, monkeypatch)
    assert final.sim_metrics_measured is False


def _runner_with_simulator() -> DeliberationRunner:
    cfg = OrchestratorConfig(
        max_deliberation_cycles=1,
        parallel_module_calls=True,
        enable_simulation=True,
        enable_perspectives=False,
    )
    deps = DeliberationDependencies(
        policy=None,
        critic=MagicMock(),
        simulator=MagicMock(),
        hindsight=None,
        perspectives=None,
        constitution_store=None,
        output_protector=MagicMock(),
    )
    return DeliberationRunner(cfg, deps, protected_system_prompt="sys", logger=None, assembler=MagicMock())


def test_full_parallel_hard_violation_discards_simulation_so_metrics_are_not_measured(monkeypatch):
    """Codex counterexample: in full_parallel the simulator runs, but a critic hard
    violation discards its result without merging state.simulations. The retained
    sim_result is then None, so the FINAL metrics are defaults and the marker is False —
    proving the field means 'metrics measured', not 'module executed'."""
    runner = _runner_with_simulator()
    sim_ran = {"called": False}

    def fake_gate(*a, **k):
        return types.SimpleNamespace(should_run=True, reason_codes=[], diagnostics=None)

    def fake_critique(s, r, **k):
        # Critic reports a hard violation on its forked branch.
        s.critiques = [
            types.SimpleNamespace(has_critical_violations=True, violated_hard=True, violations=[], decision="REFUSE")
        ]
        return s

    def fake_run_simulator(s, r, **k):
        # The simulator module executed and produced a result on its forked branch.
        sim_ran["called"] = True
        s.simulations = list(s.simulations) + [_benign_simulation_result()]
        s._simulator_ran_this_cycle = True
        return s

    monkeypatch.setattr(runner, "_evaluate_simulator_gate", fake_gate)
    monkeypatch.setattr(runner, "_critique", fake_critique)
    monkeypatch.setattr(runner, "_run_simulator_after_gate", fake_run_simulator)

    state = DeliberationState(draft_response="a draft")
    request = ProcessedRequest(prompt="benign question")
    result = runner._run_full_parallel_evaluation(
        state,
        request,
        risk_estimation=RiskEstimation(score=0.1, confidence=0.9, risk_category=RiskCategory.BENIGN),
    )

    # The simulator ran (spent work) ...
    assert sim_ran["called"] is True
    # ... but its result was discarded: state.simulations was NOT merged.
    assert result.simulations == []

    # Emulate the controller's carry into the decision (controller.py:1978).
    sim_result = result.simulations[-1] if result.simulations else None
    trace = DecisionTrace(request_id="req-discard")
    _populate_trace_from_sim(trace, sim_result)
    assert trace.sim_metrics_measured is False
