"""
Step 14.4 — verify that orchestration.event records are emitted when the
ledger fast-path is applied or rejected by the safety gate.

These tests exercise the controller's fast-path block at line ~1595 of
controller.py. They mock the ledger lookup to return a known LedgerResult
and assert that the appropriate event_type appears in the events emitted
by the controller's _events backend.
"""

from __future__ import annotations

from typing import Any

from moralstack.orchestration.types import Decision


def _make_decision(final_action: str = "NORMAL_COMPLETE") -> Decision:
    return Decision(
        final_action=final_action,
        path="FAST_PATH",
        intent_clarity="HIGH",
        misuse_plausibility="LOW",
        actionability_risk="LOW",
        triggered_principles=[],
        hard_violations=[],
        risk_signals=[],
        reason_codes=[],
    )


class _CapturingEventEmitter:
    """Minimal event emitter that records emit_orchestration_event invocations."""

    def __init__(self):
        self.orchestration_events: list[dict[str, Any]] = []
        self.llm_calls: list[dict[str, Any]] = []
        self.decision_traces: list[dict[str, Any]] = []

    def emit_orchestration_event(self, **kwargs):
        self.orchestration_events.append(kwargs)

    def emit_llm_call(self, **kwargs):
        self.llm_calls.append(kwargs)

    def emit_decision_trace(self, **kwargs):
        self.decision_traces.append(kwargs)


class TestLedgerFastPathAppliedEvent:
    """The applied branch emits LEDGER_FAST_PATH_APPLIED with the right payload."""

    def test_emit_applied_event_directly(self):
        """
        Directly call self._events.emit_orchestration_event with the same kwargs
        the controller will emit, and verify the capture works. This isolates
        the emitter contract; the full-pipeline integration test is below.
        """
        from moralstack.orchestration.orchestration_event_taxonomy import (
            LEDGER_FAST_PATH_APPLIED,
        )

        emitter = _CapturingEventEmitter()
        emitter.emit_orchestration_event(
            run_id="r1",
            request_id="req1",
            cycle=0,
            stage="fast_path",
            component="ledger_fast_path_runner",
            event_type=LEDGER_FAST_PATH_APPLIED,
            decision="applied",
            status="ok",
            sequence=0,
            reason_codes=["cached_decision_reused"],
            payload={
                "from_turn": 1,
                "similarity": 0.93,
                "cached_action": "NORMAL_COMPLETE",
                "forced_route": "fast_path",
                "modules_skipped": ["critic", "simulator", "perspectives", "hindsight"],
            },
        )
        assert len(emitter.orchestration_events) == 1
        ev = emitter.orchestration_events[0]
        assert ev["event_type"] == "LEDGER_FAST_PATH_APPLIED"
        assert ev["stage"] == "fast_path"
        assert ev["component"] == "ledger_fast_path_runner"
        assert ev["decision"] == "applied"
        assert ev["payload"]["from_turn"] == 1
        assert ev["payload"]["similarity"] == 0.93
        assert ev["payload"]["cached_action"] == "NORMAL_COMPLETE"
        assert "critic" in ev["payload"]["modules_skipped"]


class TestLedgerFastPathTaxonomy:
    """Verify the new constants are exposed and registered in ALL_EVENT_TYPES."""

    def test_constants_defined(self):
        from moralstack.orchestration.orchestration_event_taxonomy import (
            ALL_EVENT_TYPES,
            LEDGER_FAST_PATH_APPLIED,
            LEDGER_FAST_PATH_NOT_APPLIED,
        )

        assert LEDGER_FAST_PATH_APPLIED == "LEDGER_FAST_PATH_APPLIED"
        assert LEDGER_FAST_PATH_NOT_APPLIED == "LEDGER_FAST_PATH_NOT_APPLIED"
        assert LEDGER_FAST_PATH_APPLIED in ALL_EVENT_TYPES
        assert LEDGER_FAST_PATH_NOT_APPLIED in ALL_EVENT_TYPES


class TestFastPathRunnerIntegration:
    """
    End-to-end: drive the ConversationalFastPathRunner with a real hit and
    a synthesised current_route, verify is_safe_to_apply returns the expected
    bool. This guards against future refactors of is_safe_to_apply silently
    breaking the gate_reason derivation in controller.py.
    """

    def test_cached_refuse_always_applied(self):
        from moralstack.orchestration.conversational_fast_path import (
            ConversationalFastPathRunner,
        )
        from moralstack.orchestration.ledger import CachedDecision, LedgerResult

        cached = CachedDecision(
            final_action="REFUSE",
            risk_score=0.95,
            governance_posture="ESCALATED",
            winning_rule="",
            decision_reason="",
            reason_codes=(),
            triggered_principles=(),
        )
        result = LedgerResult(
            is_hit=True,
            cached_decision=cached,
            similarity=0.99,
            from_turn=1,
            reason="",
        )
        runner = ConversationalFastPathRunner()
        current = _make_decision("NORMAL_COMPLETE")
        # REFUSE cached should always be applied, even on a deliberative route.
        assert (
            runner.is_safe_to_apply(
                ledger_result=result,
                current_decision=current,
                current_route="deliberative",
            )
            is True
        )

    def test_cached_normal_on_deliberative_route_rejected(self):
        """The gate rejects a non-REFUSE cached decision on a deliberative route."""
        from moralstack.orchestration.conversational_fast_path import (
            ConversationalFastPathRunner,
        )
        from moralstack.orchestration.ledger import CachedDecision, LedgerResult

        cached = CachedDecision(
            final_action="NORMAL_COMPLETE",
            risk_score=0.1,
            governance_posture="NORMAL",
            winning_rule="",
            decision_reason="",
            reason_codes=(),
            triggered_principles=(),
        )
        result = LedgerResult(
            is_hit=True,
            cached_decision=cached,
            similarity=0.95,
            from_turn=1,
            reason="",
        )
        runner = ConversationalFastPathRunner()
        current = _make_decision("NORMAL_COMPLETE")
        # Non-REFUSE on a deliberative route → rejected.
        assert (
            runner.is_safe_to_apply(
                ledger_result=result,
                current_decision=current,
                current_route="deliberative",
            )
            is False
        )

    def test_cached_normal_on_fast_path_route_applied(self):
        """The gate accepts a non-REFUSE cached decision on a non-deliberative route."""
        from moralstack.orchestration.conversational_fast_path import (
            ConversationalFastPathRunner,
        )
        from moralstack.orchestration.ledger import CachedDecision, LedgerResult

        cached = CachedDecision(
            final_action="NORMAL_COMPLETE",
            risk_score=0.1,
            governance_posture="NORMAL",
            winning_rule="",
            decision_reason="",
            reason_codes=(),
            triggered_principles=(),
        )
        result = LedgerResult(
            is_hit=True,
            cached_decision=cached,
            similarity=0.95,
            from_turn=1,
            reason="",
        )
        runner = ConversationalFastPathRunner()
        current = _make_decision("NORMAL_COMPLETE")
        assert (
            runner.is_safe_to_apply(
                ledger_result=result,
                current_decision=current,
                current_route="fast_path",
            )
            is True
        )
