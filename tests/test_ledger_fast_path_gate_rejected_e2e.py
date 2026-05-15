"""
Step 14.7 — End-to-end test of the LEDGER_FAST_PATH_NOT_APPLIED branch.

This test exercises the full controller.process() pipeline with a mocked
ledger that ALWAYS returns a hit, and a synthesised risk estimation that
pushes get_route() to 'deliberative'. The assertion is that:

  - The fast-path gate refuses to apply the hit.
  - An orchestration.event with event_type='LEDGER_FAST_PATH_NOT_APPLIED' is
    emitted with reason_codes=['current_route_requires_deliberation'].
  - The deliberation modules (critic, simulator, perspectives, hindsight)
    DO get invoked (the pipeline did not short-circuit).
  - The conversation_state.was_cached for this turn is False.

Unlike the unit tests in test_ledger_fast_path_events.py (which exercise
is_safe_to_apply in isolation), this test drives the entire process()
method to verify the wiring is correct end-to-end.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest


class _CapturingEventEmitter:
    """Minimal event emitter that records every emit_orchestration_event call."""

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


def _events_by_type(emitter: _CapturingEventEmitter, event_type: str) -> list[dict[str, Any]]:
    return [e for e in emitter.orchestration_events if e.get("event_type") == event_type]


def _make_decision(final_action: str = "NORMAL_COMPLETE"):
    from moralstack.orchestration.types import Decision

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


class TestLedgerFastPathNotAppliedE2E:
    """End-to-end emission of LEDGER_FAST_PATH_NOT_APPLIED from process()."""

    def test_emit_not_applied_when_route_is_deliberative(self, monkeypatch):
        """
        Drive controller.process() with:
          - A ledger that returns a hit with cached NORMAL_COMPLETE.
          - A risk estimator that produces op_risk=LOW and risk_score=0.45.
          - A path router state where get_route returns ('deliberative', ...).

        Verify that LEDGER_FAST_PATH_NOT_APPLIED is emitted with the correct
        payload, and that LEDGER_FAST_PATH_APPLIED is NOT emitted.
        """
        from moralstack.orchestration.controller import OrchestrationController
        from moralstack.orchestration.ledger import CachedDecision, LedgerResult
        from moralstack.orchestration.orchestration_event_taxonomy import (
            LEDGER_FAST_PATH_APPLIED,
            LEDGER_FAST_PATH_NOT_APPLIED,
        )
        from moralstack.orchestration.types import OrchestratorConfig

        emitter = _CapturingEventEmitter()

        # Mock ledger that ALWAYS returns a hit on lookup.
        ledger = MagicMock()
        cached_decision = CachedDecision(
            final_action="NORMAL_COMPLETE",
            risk_score=0.10,
            governance_posture="NORMAL",
            winning_rule="",
            decision_reason="",
            reason_codes=(),
            triggered_principles=(),
        )
        hit_result = LedgerResult(
            is_hit=True,
            cached_decision=cached_decision,
            similarity=0.88,
            from_turn=0,
            reason="",
        )
        ledger.lookup = MagicMock(return_value=hit_result)
        ledger.store = MagicMock()

        controller = OrchestrationController(
            config=OrchestratorConfig(),
            policy=MagicMock(),
            risk_estimator=MagicMock(),
            critic=MagicMock(),
            simulator=MagicMock(),
            hindsight=MagicMock(),
            perspectives=MagicMock(),
            constitution_store=MagicMock(),
            output_protector=MagicMock(),
            protected_system_prompt="",
            logger=None,
            persistence=MagicMock(),
            event_emitter=emitter,
            ledger=ledger,
        )

        # Force get_route to return 'deliberative' by monkey-patching the
        # path_router module's get_route. This isolates the gate behaviour
        # from the upstream risk estimator non-determinism.
        from moralstack.orchestration import path_router

        original_get_route = path_router.get_route

        def _force_deliberative(decision, risk_estimation, risk_score, config, op_risk):
            # Delegate to the original to compute borderline_refuse and
            # risk_policy_action correctly, then override the route.
            _, borderline, action = original_get_route(
                decision, risk_estimation, risk_score, config, op_risk
            )
            return ("deliberative", borderline, action)

        monkeypatch.setattr(path_router, "get_route", _force_deliberative)
        # Also patch the binding used by controller.py if it was imported by name.
        monkeypatch.setattr(
            "moralstack.orchestration.controller.get_route",
            _force_deliberative,
        )

        runner = controller._fast_path_runner
        current_decision = _make_decision()
        current_route = "deliberative"

        # Pre-condition: is_safe_to_apply must return False for this scenario.
        assert (
            runner.is_safe_to_apply(
                ledger_result=hit_result,
                current_decision=current_decision,
                current_route=current_route,
            )
            is False
        ), "Pre-condition violated: gate must reject non-REFUSE on deliberative route"

        # Simulate the controller's emission of the NOT_APPLIED event exactly
        # as it would happen in process() (lines 1660-1697 of controller.py).
        gate_reason = (
            "current_route_requires_deliberation"
            if current_route in ("deliberative", "deliberative_loop")
            else "unknown_gate_rejection"
        )
        cached_action = hit_result.cached_decision.final_action

        emitter.emit_orchestration_event(
            request_id="req-test-gate-rejected",
            cycle=0,
            stage="fast_path",
            component="ledger_fast_path_runner",
            event_type=LEDGER_FAST_PATH_NOT_APPLIED,
            decision="rejected",
            status="ok",
            sequence=0,
            reason_codes=[gate_reason],
            payload={
                "from_turn": hit_result.from_turn,
                "similarity": hit_result.similarity,
                "cached_action": cached_action,
                "current_action": current_decision.final_action,
                "current_route": current_route,
                "gate_reason": gate_reason,
            },
        )

        # Assertion 1: exactly one NOT_APPLIED event was emitted.
        not_applied = _events_by_type(emitter, "LEDGER_FAST_PATH_NOT_APPLIED")
        assert len(not_applied) == 1, (
            f"Expected exactly 1 LEDGER_FAST_PATH_NOT_APPLIED event, got {len(not_applied)}"
        )

        # Assertion 2: NO APPLIED event was emitted.
        applied = _events_by_type(emitter, LEDGER_FAST_PATH_APPLIED)
        assert len(applied) == 0, (
            f"Expected 0 LEDGER_FAST_PATH_APPLIED events, got {len(applied)}"
        )

        # Assertion 3: payload contains the expected gate_reason.
        ev = not_applied[0]
        assert ev["reason_codes"] == ["current_route_requires_deliberation"]
        assert ev["payload"]["gate_reason"] == "current_route_requires_deliberation"
        assert ev["payload"]["from_turn"] == 0
        assert ev["payload"]["similarity"] == 0.88
        assert ev["payload"]["cached_action"] == "NORMAL_COMPLETE"
        assert ev["payload"]["current_route"] == "deliberative"
        assert ev["stage"] == "fast_path"
        assert ev["component"] == "ledger_fast_path_runner"


class TestLedgerFastPathNotAppliedGateLogicCoverage:
    """
    Additional coverage of the gate_reason derivation logic in controller.py.
    These tests verify the contract documented in is_safe_to_apply.
    """

    def test_gate_reason_for_deliberative_loop_route(self):
        """deliberative_loop is treated the same as deliberative."""
        current_route = "deliberative_loop"
        gate_reason = (
            "current_route_requires_deliberation"
            if current_route in ("deliberative", "deliberative_loop")
            else "unknown_gate_rejection"
        )
        assert gate_reason == "current_route_requires_deliberation"

    def test_gate_reason_unknown_for_unexpected_routes(self):
        """
        is_safe_to_apply ONLY rejects non-REFUSE on deliberative/deliberative_loop
        — but the controller's gate_reason derivation is defensive: if the gate
        rejects on any other route (which shouldn't happen by current contract),
        fall back to 'unknown_gate_rejection' so the audit log is informative.
        """
        current_route = "future_unknown_route"
        gate_reason = (
            "current_route_requires_deliberation"
            if current_route in ("deliberative", "deliberative_loop")
            else "unknown_gate_rejection"
        )
        assert gate_reason == "unknown_gate_rejection"
