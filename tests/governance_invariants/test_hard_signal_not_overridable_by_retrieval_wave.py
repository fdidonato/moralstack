"""
Governance invariant: hard-signal supremacy (§5.3, P0) is not overridable by the
single-wave constitution retrieval unification.

`is_hard_signal_refuse` / `get_route` (`path_router.py`) never take principles or
a `RequestAnalysisContext` as input — they derive the hard-signal verdict
exclusively from `decision.risk_signals` / `risk_estimation.semantic_signals`,
which come from the signals mini (principle-free, see
`tests/test_signals_mini_principle_free.py`). This test locks that structural
guarantee for every member of the hard-signal set (Q4/Q5/Q8/Q9/Q10/Q11/Q12/Q17),
not only Q17, and confirms it holds even when a constitution retrieval wave
(real or simulated) returns only benign/soft principles — the hard-signal
verdict is byte-identical regardless of what the retrieval wave returned.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from moralstack.models.risk.categories import OperationalRisk
from moralstack.orchestration.path_router import _HARD_SEMANTIC_SIGNALS, get_route, is_hard_signal_refuse
from moralstack.orchestration.types import Decision, OrchestratorConfig, RiskThresholds


@dataclass
class _RiskProto:
    """Minimal RiskEstimationProtocol double carrying only a semantic signal."""

    semantic_signals: list[str]
    score: float = 0.9
    risk_category: object = None
    detected_language: str = "en"
    intent_type: str = ""
    actionability_risk: object = None
    detected_domain: str | None = None
    rationale: str = ""
    operational_risk: object = OperationalRisk.HIGH
    raw_response: str = ""
    used_fallback_parse: bool = False
    risk_policy_action: object = None
    harm_type: str = ""


def _refuse_decision() -> Decision:
    return Decision(
        final_action="REFUSE",
        path="FAST_PATH",
        intent_clarity="LOW",
        misuse_plausibility="HIGH",
        actionability_risk="HIGH",
        triggered_principles=[],
        hard_violations=[],
        risk_signals=[],
    )


@pytest.mark.parametrize("hard_signal", sorted(_HARD_SEMANTIC_SIGNALS))
def test_every_hard_signal_forces_refuse_regardless_of_retrieved_principles(hard_signal: str) -> None:
    """
    Each of the 8 hard-signal codes alone (no hard_violations from the
    critic/retrieval side, only the signals-mini semantic signal) is sufficient
    for `is_hard_signal_refuse` to return True and `get_route` to route "refuse" —
    this holds structurally regardless of what a constitution retrieval wave
    (simulated here as absent/benign-only, since these functions take no
    principles at all) would have returned.
    """
    decision = _refuse_decision()
    risk = _RiskProto(semantic_signals=[hard_signal])

    assert is_hard_signal_refuse(decision, risk, OperationalRisk.HIGH) is True

    config = OrchestratorConfig(risk_thresholds=RiskThresholds())
    route, borderline_refuse, _ = get_route(decision, risk, risk.score, config, OperationalRisk.HIGH)
    assert route == "refuse"
    assert borderline_refuse is False


def test_no_hard_signal_and_benign_principles_does_not_force_hard_refuse() -> None:
    """Negative control: without a hard signal, is_hard_signal_refuse is False
    (borderline handling applies), confirming the parametrized positives above
    are not vacuously true."""
    decision = _refuse_decision()
    risk = _RiskProto(semantic_signals=["SOME_OTHER_SIGNAL"])

    assert is_hard_signal_refuse(decision, risk, OperationalRisk.HIGH) is False
