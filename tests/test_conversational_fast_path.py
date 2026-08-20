"""
Unit tests for moralstack/orchestration/conversational_fast_path.py.
"""

from __future__ import annotations

import pytest

from moralstack.models.reason_codes import ReasonCode, policy_reason_codes_to_reason_codes
from moralstack.orchestration.conversational_fast_path import (
    LEDGER_REUSE_REASON_CODE,
    ConversationalFastPathRunner,
    decision_explanation_for_ledger_reuse,
)
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


def _stale_explanation():
    """The explanation decide_action produced BEFORE the ledger was consulted."""
    from moralstack.models.decision_explanation import DecisionExplanation

    return DecisionExplanation(
        request_id="req-reuse",
        final_action="NORMAL_COMPLETE",
        risk_score=0.35,
        risk_category="benign",
        activated_signals=["sig"],
        overlay_applied="",
        winning_rule="fast_path",
        reason_codes=["RISK_BENIGN", "NORMAL_COMPLETE_REQUIRED"],
        why_not_refuse="stale",
        why_not_safe_complete="stale",
        why_not_normal_complete="stale",
    )


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
        # The cached codes keep their order and are followed by the reuse marker,
        # so the audit trail can tell a replayed decision from a deliberated one.
        runner = ConversationalFastPathRunner()
        cached = _make_cached(reason_codes=("rc_a", "rc_b"))
        hit = _make_hit(cached)
        patched, _ = runner.apply_cached_decision(ledger_result=hit, current_decision=_make_decision())
        assert patched.reason_codes == ["rc_a", "rc_b", LEDGER_REUSE_REASON_CODE]

    def test_reuse_marker_present_when_cache_has_no_reason_codes(self):
        # Regression: a cached REFUSE with no reason codes used to be rendered as
        # DEFAULT_NORMAL_COMPLETE -- a refusal whose recorded reason said the
        # opposite of what was delivered.
        runner = ConversationalFastPathRunner()
        cached = _make_cached(final_action="REFUSE", reason_codes=())
        hit = _make_hit(cached)
        patched, route = runner.apply_cached_decision(ledger_result=hit, current_decision=_make_decision())
        assert route == "refuse"
        assert patched.reason_codes == [LEDGER_REUSE_REASON_CODE]
        assert policy_reason_codes_to_reason_codes(patched.reason_codes) == [ReasonCode.LEDGER_FAST_PATH_REUSE.value]

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


class TestReuseMarkerReachesAuditRecord:
    """The reuse marker must survive the whole audit chain, not just the runner.

    The defect this locks was observed in ``proxy_request_events.metadata_json``:
    11 of 135 replayed refusals recorded ``decision_reason=DEFAULT_NORMAL_COMPLETE``.
    Asserting on the runner alone would not have caught it, so the test drives the
    production chain runner -> _build_decision_explanation -> ResponseMetadata
    -> build_request_meta_from_result and asserts on the persisted field.
    """

    @staticmethod
    def _meta_for(cached_reason_codes: tuple[str, ...]) -> dict:
        from moralstack.observability.governance_audit import build_request_meta_from_result
        from moralstack.orchestration.types import (
            FinalResponse,
            OrchestratorResult,
            ResponseMetadata,
            ResponseType,
        )

        runner = ConversationalFastPathRunner()
        cached = _make_cached(final_action="REFUSE", reason_codes=cached_reason_codes)
        hit = _make_hit(cached)
        patched, _route = runner.apply_cached_decision(ledger_result=hit, current_decision=_make_decision())
        # Must be the SAME helper the controller calls after the patch. An earlier
        # version of this test called _build_decision_explanation instead and passed
        # while the real pipeline still persisted DEFAULT_NORMAL_COMPLETE, because the
        # controller never rebuilds the explanation that way -- it carries the one
        # decide_action produced before the ledger was consulted.
        explanation = decision_explanation_for_ledger_reuse(_stale_explanation(), patched, hit)
        metadata = ResponseMetadata.from_decision(
            decision=patched,
            request_id="req-reuse",
            risk_score=0.35,
            processing_time_ms=1,
            risk_category="benign",
            decision_explanation=explanation,
        )
        result = OrchestratorResult(
            response=FinalResponse(content="", response_type=ResponseType.FULL_REFUSAL, metadata=metadata),
            request_id="req-reuse",
            path_taken="fast",
            path="FAST_PATH",
            total_cycles=0,
            converged=False,
        )
        return build_request_meta_from_result(result)

    def test_empty_cached_codes_no_longer_render_as_default_normal_complete(self):
        meta = self._meta_for(())
        assert meta["final_action"] == "REFUSE"
        assert meta["decision_reason"] == ReasonCode.LEDGER_FAST_PATH_REUSE.value
        assert ReasonCode.DEFAULT_NORMAL_COMPLETE.value not in meta["reason_codes"]

    def test_cached_codes_are_preserved_alongside_the_marker(self):
        meta = self._meta_for(("risk_clearly_harmful",))
        assert meta["reason_codes"] == [
            ReasonCode.RISK_CLEARLY_HARMFUL.value,
            ReasonCode.LEDGER_FAST_PATH_REUSE.value,
        ]


class TestReuseMarkerIsIdempotent:
    """Reusing an already-reused decision must not stack markers.

    ``_maybe_store_in_ledger`` rebuilds the CachedDecision from
    ``ResponseMetadata.reason_codes``, i.e. from the *mapped* codes. A decision can
    therefore be replayed, stored, and replayed again; the marker must converge
    instead of growing one entry per generation.
    """

    def test_second_generation_does_not_stack_the_marker(self):
        runner = ConversationalFastPathRunner()
        # 1st reuse: cache carries no reason codes at all.
        gen1, _ = runner.apply_cached_decision(
            ledger_result=_make_hit(_make_cached(final_action="REFUSE", reason_codes=())),
            current_decision=_make_decision(),
        )
        mapped1 = policy_reason_codes_to_reason_codes(gen1.reason_codes)
        assert mapped1 == [ReasonCode.LEDGER_FAST_PATH_REUSE.value]

        # 2nd reuse: the ledger now holds what the audit record stored (mapped codes).
        gen2, _ = runner.apply_cached_decision(
            ledger_result=_make_hit(_make_cached(final_action="REFUSE", reason_codes=tuple(mapped1))),
            current_decision=_make_decision(),
        )
        mapped2 = policy_reason_codes_to_reason_codes(gen2.reason_codes)
        assert mapped2 == mapped1, f"marker stacked across generations: {mapped2}"

        # 3rd reuse: still a fixed point.
        gen3, _ = runner.apply_cached_decision(
            ledger_result=_make_hit(_make_cached(final_action="REFUSE", reason_codes=tuple(mapped2))),
            current_decision=_make_decision(),
        )
        assert policy_reason_codes_to_reason_codes(gen3.reason_codes) == mapped1

    def test_substantive_codes_survive_repeated_reuse(self):
        runner = ConversationalFastPathRunner()
        mapped = ("RISK_CLEARLY_HARMFUL",)
        for _ in range(3):
            patched, _r = runner.apply_cached_decision(
                ledger_result=_make_hit(_make_cached(final_action="REFUSE", reason_codes=mapped)),
                current_decision=_make_decision(),
            )
            mapped = tuple(policy_reason_codes_to_reason_codes(patched.reason_codes))
        assert mapped == (ReasonCode.RISK_CLEARLY_HARMFUL.value, ReasonCode.LEDGER_FAST_PATH_REUSE.value)


class TestControllerRebuildsTheExplanation:
    """The controller must rebuild the explanation right after applying a cached decision.

    A unit test on the helper cannot catch its removal from the call site, and that call
    site is exactly where the defect lived: `explanation` is built by decide_action and
    the ledger patches `decision` afterwards, so dropping this line silently restores
    the stale-explanation behaviour while every other test still passes. Driving the full
    controller here would need the whole pipeline mocked, so the call site is asserted on
    the source instead.
    """

    def test_apply_cached_decision_is_followed_by_the_explanation_rebuild(self):
        import inspect

        from moralstack.orchestration import controller as controller_mod

        src = inspect.getsource(controller_mod)
        assert "apply_cached_decision(" in src, "call site vanished; update this test"
        after = src.split("apply_cached_decision(", 1)[1]
        window = after[:1200]
        assert "decision_explanation_for_ledger_reuse(" in window, (
            "the controller applies a cached decision without rebuilding the "
            "DecisionExplanation: ResponseMetadata.from_decision prioritizes the stale "
            "explanation's reason_codes, so the replayed decision is persisted with the "
            "pre-ledger reasoning (this is the DEFAULT_NORMAL_COMPLETE-on-REFUSE defect)"
        )
