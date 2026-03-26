"""
Test invarianti di convergenza: enforce_convergence_invariants.
Nessun caso "continue" quando cycles_exhausted; converged => stop.
"""

from moralstack.orchestration.convergence import enforce_convergence_invariants
from moralstack.orchestration.types import DecisionType


def test_cycles_exhausted_must_stop():
    """cycle >= max_cycles => should_continue=False, stop_reason=CYCLES_EXHAUSTED
    anche se raw dice continue."""
    max_cycles = 3
    outcome = enforce_convergence_invariants(cycle=3, max_cycles=max_cycles, decision_type=DecisionType.CONTINUE)
    assert outcome.should_continue is False
    assert outcome.stop_reason == "CYCLES_EXHAUSTED"
    assert outcome.converged is False
    assert outcome.cycle == 3
    assert outcome.max_cycles == max_cycles


def test_cycles_exhausted_revise_must_stop():
    """cycle >= max_cycles con REVISE => stesso risultato: stop."""
    outcome = enforce_convergence_invariants(cycle=2, max_cycles=2, decision_type=DecisionType.REVISE)
    assert outcome.should_continue is False
    assert outcome.stop_reason == "CYCLES_EXHAUSTED"
    assert outcome.converged is False


def test_converged_must_stop():
    """converged=True (CONVERGED) => should_continue=False, stop_reason=CONVERGED."""
    outcome = enforce_convergence_invariants(cycle=1, max_cycles=3, decision_type=DecisionType.CONVERGED)
    assert outcome.should_continue is False
    assert outcome.stop_reason == "CONVERGED"
    assert outcome.converged is True


def test_converged_on_last_cycle_is_still_converged():
    """When cycle >= max_cycles but decision is CONVERGED, outcome is converged=True (decision overrides)."""
    outcome = enforce_convergence_invariants(cycle=2, max_cycles=2, decision_type=DecisionType.CONVERGED)
    assert outcome.should_continue is False
    assert outcome.stop_reason == "CONVERGED"
    assert outcome.converged is True
    outcome2 = enforce_convergence_invariants(cycle=2, max_cycles=2, decision_type=DecisionType.CONVERGED_WITH_SUGGESTIONS)
    assert outcome2.converged is True
    assert outcome2.stop_reason == "CONVERGED"


def test_refuse_must_stop():
    """REFUSE => should_continue=False, stop_reason=HARD_VIOLATION_STOP."""
    outcome = enforce_convergence_invariants(cycle=1, max_cycles=3, decision_type=DecisionType.REFUSE)
    assert outcome.should_continue is False
    assert outcome.stop_reason == "HARD_VIOLATION_STOP"
    assert outcome.converged is False


def test_continue_with_cycles_remaining():
    """CONTINUE con cycle < max_cycles => should_continue=True, stop_reason=NONE."""
    outcome = enforce_convergence_invariants(cycle=1, max_cycles=3, decision_type=DecisionType.CONTINUE)
    assert outcome.should_continue is True
    assert outcome.stop_reason == "NONE"
    assert outcome.converged is False


def test_revise_with_cycles_remaining():
    """REVISE con cycle < max_cycles => should_continue=True."""
    outcome = enforce_convergence_invariants(cycle=2, max_cycles=3, decision_type=DecisionType.REVISE)
    assert outcome.should_continue is True
    assert outcome.stop_reason == "NONE"


def test_decision_none_treated_as_continue():
    """decision_type None => trattato come CONTINUE."""
    outcome = enforce_convergence_invariants(cycle=1, max_cycles=3, decision_type=None)
    assert outcome.should_continue is True
    assert outcome.stop_reason == "NONE"
