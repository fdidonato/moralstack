"""Tests for aggregated guidance signal-strength filtering."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from moralstack.orchestration.guidance_builder import build_aggregated_guidance
from moralstack.orchestration.types import DeliberationState


@dataclass
class _V:
    principle_id: str = "p1"
    rationale: str = "r"
    severity: float = 0.5


@dataclass
class _Critique:
    violations: list[Any]
    has_critical_violations: bool
    decision: str
    revision_guidance: str


@dataclass
class _Cons:
    likelihood: float
    harm_severity: float
    outcome_valence: float = 0.0
    text: str = "x"
    is_negative: bool = False


@dataclass
class _Sim:
    expected_valence: float = 0.0
    consequences: list[Any] = field(default_factory=list)
    semantic_expected_harm: float = 0.0


@dataclass
class _Persp:
    perspective_id: str
    perspective_name: str
    approval_score: float
    concerns: list[str] = field(default_factory=list)
    suggestions: list[str] = field(default_factory=list)
    rationale: str = ""


@dataclass
class _AggHigh:
    expected_value: float = 0.85


@dataclass
class _HindsightStub:
    aggregated: Any = field(default_factory=lambda: _AggHigh(expected_value=0.85))
    raw_response: str = ""
    parse_attempts: int = 1
    prompt: str = ""
    system_prompt: str = ""


def test_filter_marginal_proceed_no_violations_drops_critic() -> None:
    lc = _Critique(
        violations=[],
        has_critical_violations=False,
        decision="PROCEED",
        revision_guidance="Consider adding a disclaimer.",
    )
    state = DeliberationState(
        critiques=[lc],
        perspectives=[
            _Persp("user", "User", 0.9, suggestions=["be nice"], concerns=[]),
        ],
    )
    tel: dict[str, Any] = {}
    g = build_aggregated_guidance(state, telemetry=tel)
    assert "[CRITIC]" not in g
    assert tel.get("critic_included") is False


def test_filter_marginal_weighted_approval_skips_perspectives() -> None:
    state = DeliberationState(
        perspectives=[
            _Persp("user", "User", 0.95, suggestions=["s1"], concerns=["c1"]),
            _Persp("observer", "Obs", 0.95, suggestions=["s2"], concerns=[]),
        ],
    )
    tel: dict[str, Any] = {}
    g = build_aggregated_guidance(state, telemetry=tel)
    assert "PERSPECTIVES" not in g
    assert tel.get("perspectives_section_skipped_high_approval") is True


def test_filter_marginal_hindsight_high_score_skips_hindsight_block() -> None:
    lc = _Critique(
        violations=[_V()],
        has_critical_violations=False,
        decision="REVISE",
        revision_guidance="Fix tone.",
    )
    state = DeliberationState(
        critiques=[lc],
        perspectives=[],
        hindsight=_HindsightStub(),
        simulations=[_Sim(semantic_expected_harm=0.4, consequences=[_Cons(0.9, 0.9, text="bad")])],
    )
    tel: dict[str, Any] = {}
    g = build_aggregated_guidance(state, telemetry=tel)
    assert "[HINDSIGHT]" not in g
    assert tel.get("hindsight_section_included") is False


def test_filter_marginal_semantic_harm_below_threshold_skips_simulator() -> None:
    lc = _Critique(
        violations=[_V()],
        has_critical_violations=False,
        decision="REVISE",
        revision_guidance="x",
    )
    state = DeliberationState(
        critiques=[lc],
        perspectives=[],
        simulations=[_Sim(semantic_expected_harm=0.1, consequences=[_Cons(0.9, 0.9, text="bad")])],
    )
    tel: dict[str, Any] = {}
    g = build_aggregated_guidance(state, telemetry=tel)
    assert "SIMULATOR" not in g
    assert tel.get("simulator_section_included") is False


def test_has_critical_bypasses_filter() -> None:
    lc = _Critique(
        violations=[],
        has_critical_violations=True,
        decision="PROCEED",
        revision_guidance="cosmetic",
    )
    state = DeliberationState(
        critiques=[lc],
        perspectives=[
            _Persp("user", "User", 0.99, suggestions=["s"], concerns=[]),
        ],
        simulations=[_Sim(semantic_expected_harm=0.01)],
    )
    g = build_aggregated_guidance(state, filter_marginal=True)
    assert "[CRITIC]" in g
    assert "PERSPECTIVES" in g


def test_filter_marginal_false_matches_legacy_unfiltered() -> None:
    state = DeliberationState()
    g = build_aggregated_guidance(state, filter_marginal=False)
    assert "[BALANCE]" in g
    assert "[HINDSIGHT]" in g


def test_filter_marginal_all_marginal_returns_empty() -> None:
    lc = _Critique(
        violations=[],
        has_critical_violations=False,
        decision="PROCEED",
        revision_guidance="only cosmetic",
    )
    state = DeliberationState(
        critiques=[lc],
        perspectives=[
            _Persp("user", "User", 0.9, suggestions=["s"], concerns=[]),
        ],
        simulations=[_Sim(semantic_expected_harm=0.1)],
    )
    g = build_aggregated_guidance(state, filter_marginal=True)
    assert g == ""
