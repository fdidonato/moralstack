"""
Unit tests for moralstack/orchestration/conversational_fast_path.py.
"""

from __future__ import annotations

import pytest

from moralstack.orchestration.conversational_fast_path import ConversationalFastPathRunner
from moralstack.orchestration.ledger import CachedDecision, LedgerResult
from moralstack.orchestration.types import Decision


def _make_decision(final_action: str = "NORMAL_COMPLETE", path: str = "FAST_PATH") -> Decision:
    return Decision(
        final_action=final_action,
        path=path,
        intent_clarity="HIGH",
        misuse_plausibility="LOW",
        actionability_risk="LOW",
        triggered_principles=["original_principle"],
        hard_violations=[],
        risk_signals=["original_signal"],
        reason_codes=["original_reason"],
    )


def _make_cached(
    final_action: str = "NORMAL_COMPLETE",
    risk: float = 0.1,
    posture: str = "NORMAL",
    reason_codes: tuple[str, ...] = ("cached_reason",),
    triggered_principles: tuple[str, ...] = ("cached_principle",),
) -> CachedDecision:
    return CachedDecision(
        final_action=final_action,
        risk_score=risk,
        governance_posture=posture,
        winning_rule="cached_rule",
        decision_reason="cached_reason_text",
        reason_codes=reason_codes,
        triggered_principles=triggered_principles,
    )


def _make_hit(cached: CachedDecision, similarity: float = 0.95, from_turn: int = 1) -> LedgerResult:
    return LedgerResult(is_hit=True, cached_decision=cached, similarity=similarity, from_turn=from_turn, reason="")


def _make_miss(reason: str = "no_candidates") -> LedgerResult:
    return LedgerResult(is_hit=False, cached_decision=None, similarity=0.0, from_turn=None, reason=reason)


class TestApplyCachedDecision:
    def test_normal_complete_maps_to_benign(self):
        runner = ConversationalFastPathRunner()
        cached = _make_cached(final_action="NORMAL_COMPLETE")
        hit = _make_hit(cached)
        patched, route = runner.apply_cached_decision(ledger_result=hit, current_decision=_make_decision())
        assert patched.final_action == "NORMAL_COMPLETE"
        assert route == "benign"

    def test_safe_complete_maps_to_safe_complete(self):
        runner = ConversationalFastPathRunner()
        cached = _make_cached(final_action="SAFE_COMPLETE")
        hit = _make_hit(cached)
        patched, route = runner.apply_cached_decision(ledger_result=hit, current_decision=_make_decision())
        assert patched.final_action == "SAFE_COMPLETE"
        assert route == "safe_complete"

    def test_refuse_maps_to_refuse(self):
        runner = ConversationalFastPathRunner()
        cached = _make_cached(final_action="REFUSE")
        hit = _make_hit(cached)
        patched, route = runner.apply_cached_decision(ledger_result=hit, current_decision=_make_decision())
        assert patched.final_action == "REFUSE"
        assert route == "refuse"

    def test_patched_decision_uses_cached_reason_codes(self):
        runner = ConversationalFastPathRunner()
        cached = _make_cached(reason_codes=("rc_a", "rc_b"))
        hit = _make_hit(cached)
        patched, _ = runner.apply_cached_decision(ledger_result=hit, current_decision=_make_decision())
        assert patched.reason_codes == ["rc_a", "rc_b"]

    def test_patched_decision_uses_cached_triggered_principles(self):
        runner = ConversationalFastPathRunner()
        cached = _make_cached(triggered_principles=("p1", "p2"))
        hit = _make_hit(cached)
        patched, _ = runner.apply_cached_decision(ledger_result=hit, current_decision=_make_decision())
        assert patched.triggered_principles == ["p1", "p2"]

    def test_patched_decision_preserves_intent_clarity(self):
        runner = ConversationalFastPathRunner()
        cached = _make_cached()
        hit = _make_hit(cached)
        current = _make_decision()
        patched, _ = runner.apply_cached_decision(ledger_result=hit, current_decision=current)
        # intent_clarity, misuse_plausibility, actionability_risk are NOT replaced — they reflect
        # the fresh risk estimation.
        assert patched.intent_clarity == current.intent_clarity
        assert patched.misuse_plausibility == current.misuse_plausibility
        assert patched.actionability_risk == current.actionability_risk

    def test_patched_decision_preserves_path_and_risk_signals(self):
        runner = ConversationalFastPathRunner()
        cached = _make_cached()
        hit = _make_hit(cached)
        current = _make_decision(path="DELIBERATIVE_PATH")
        patched, _ = runner.apply_cached_decision(ledger_result=hit, current_decision=current)
        # path and risk_signals also reflect the current run.
        assert patched.path == "DELIBERATIVE_PATH"
        assert patched.risk_signals == current.risk_signals

    def test_does_not_mutate_input_decision(self):
        runner = ConversationalFastPathRunner()
        cached = _make_cached(final_action="REFUSE", reason_codes=("new_rc",))
        hit = _make_hit(cached)
        original = _make_decision(final_action="NORMAL_COMPLETE")
        original_reason_codes = list(original.reason_codes)
        runner.apply_cached_decision(ledger_result=hit, current_decision=original)
        # The original is unchanged.
        assert original.final_action == "NORMAL_COMPLETE"
        assert original.reason_codes == original_reason_codes


class TestApplyCachedDecisionErrors:
    def test_raises_on_miss(self):
        runner = ConversationalFastPathRunner()
        miss = _make_miss()
        with pytest.raises(ValueError, match="is_hit=True"):
            runner.apply_cached_decision(ledger_result=miss, current_decision=_make_decision())

    def test_raises_on_none_cached_decision(self):
        runner = ConversationalFastPathRunner()
        # Construct a LedgerResult that is is_hit=True but with cached_decision=None.
        # This is a pathological case — should not happen in practice but the runner
        # defends against it.
        bad_hit = LedgerResult(is_hit=True, cached_decision=None, similarity=1.0, from_turn=1, reason="")
        with pytest.raises(ValueError, match="cached_decision"):
            runner.apply_cached_decision(ledger_result=bad_hit, current_decision=_make_decision())

    def test_raises_on_unknown_final_action(self):
        runner = ConversationalFastPathRunner()
        bad_cached = CachedDecision(final_action="UNKNOWN_ACTION", risk_score=0.5, governance_posture="NORMAL")
        bad_hit = _make_hit(bad_cached)
        with pytest.raises(ValueError, match="UNKNOWN_ACTION"):
            runner.apply_cached_decision(ledger_result=bad_hit, current_decision=_make_decision())


class TestIsSafeToApply:
    def test_refuse_is_always_safe(self):
        runner = ConversationalFastPathRunner()
        cached = _make_cached(final_action="REFUSE")
        hit = _make_hit(cached)
        # Even on deliberative route, REFUSE cache is applied.
        assert (
            runner.is_safe_to_apply(ledger_result=hit, current_decision=_make_decision(), current_route="deliberative")
            is True
        )

    def test_normal_complete_safe_when_route_is_benign(self):
        runner = ConversationalFastPathRunner()
        cached = _make_cached(final_action="NORMAL_COMPLETE")
        hit = _make_hit(cached)
        assert runner.is_safe_to_apply(ledger_result=hit, current_decision=_make_decision(), current_route="benign") is True

    def test_safe_complete_safe_when_route_is_safe_complete(self):
        runner = ConversationalFastPathRunner()
        cached = _make_cached(final_action="SAFE_COMPLETE")
        hit = _make_hit(cached)
        assert (
            runner.is_safe_to_apply(ledger_result=hit, current_decision=_make_decision(), current_route="safe_complete")
            is True
        )

    def test_normal_complete_NOT_safe_when_route_is_deliberative(self):
        """
        Safety gate: cache NORMAL_COMPLETE while current run wants deliberation → SKIP.
        The current deliberation may detect risk the cache could not anticipate.
        """
        runner = ConversationalFastPathRunner()
        cached = _make_cached(final_action="NORMAL_COMPLETE")
        hit = _make_hit(cached)
        assert (
            runner.is_safe_to_apply(ledger_result=hit, current_decision=_make_decision(), current_route="deliberative")
            is False
        )

    def test_safe_complete_NOT_safe_when_route_is_deliberative_loop(self):
        runner = ConversationalFastPathRunner()
        cached = _make_cached(final_action="SAFE_COMPLETE")
        hit = _make_hit(cached)
        assert (
            runner.is_safe_to_apply(ledger_result=hit, current_decision=_make_decision(), current_route="deliberative_loop")
            is False
        )

    def test_miss_is_never_safe(self):
        runner = ConversationalFastPathRunner()
        miss = _make_miss()
        assert (
            runner.is_safe_to_apply(ledger_result=miss, current_decision=_make_decision(), current_route="benign") is False
        )

    def test_none_cached_is_never_safe(self):
        runner = ConversationalFastPathRunner()
        bad_hit = LedgerResult(is_hit=True, cached_decision=None, similarity=1.0, from_turn=1, reason="")
        assert (
            runner.is_safe_to_apply(ledger_result=bad_hit, current_decision=_make_decision(), current_route="benign")
            is False
        )


class TestRouteMapping:
    """The mapping FINAL_ACTION → ROUTE is the contract of the runner."""

    def test_all_three_routes_are_distinct(self):
        from moralstack.orchestration.conversational_fast_path import _FINAL_ACTION_TO_ROUTE

        assert len(set(_FINAL_ACTION_TO_ROUTE.values())) == 3

    def test_mapping_is_exhaustive_for_final_actions(self):
        from moralstack.orchestration.conversational_fast_path import _FINAL_ACTION_TO_ROUTE

        assert set(_FINAL_ACTION_TO_ROUTE.keys()) == {"NORMAL_COMPLETE", "SAFE_COMPLETE", "REFUSE"}
