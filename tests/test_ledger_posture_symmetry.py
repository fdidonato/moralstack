"""
Step 14.8 — verify that the posture computed at STORE time matches the posture
computed at LOOKUP time for any combination of (final_action, overlay_sensitive,
hard_constraints).

This is a structural invariant: if the two postures diverge, the LedgerKey
differs between store and lookup, and the cache is unreachable. The test
exercises both formulae and asserts equality.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from moralstack.orchestration.process_context import ProcessCallContext


class TestLedgerPostureSymmetry:
    """The posture used at store time must equal the posture used at lookup time."""

    def _build_controller_with_constitution(self, monkeypatch, *, overlay_is_sensitive: bool):
        """
        Build a minimal controller with a mocked constitution_store that
        reports overlay_is_sensitive for any domain query.

        Uses monkeypatch (pytest fixture) so the patches are auto-restored
        at test teardown — without this, the direct module-attribute
        assignment leaks across tests and breaks test_orchestrator.py and
        other tests that depend on the real is_overlay_sensitive function.
        """
        from moralstack.orchestration import controller as controller_module
        from moralstack.orchestration import overlay_policy
        from moralstack.orchestration.controller import OrchestrationController
        from moralstack.orchestration.types import OrchestratorConfig

        def _sensitive(store, domain):
            return overlay_is_sensitive

        monkeypatch.setattr(overlay_policy, "is_overlay_sensitive", _sensitive)
        monkeypatch.setattr(controller_module, "is_overlay_sensitive", _sensitive)

        return OrchestrationController(
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
            event_emitter=MagicMock(),
            ledger=MagicMock(),
        )

    def _compute_store_posture(self, controller, *, final_action, hard_constraints, domain):
        """
        Invoke the store-side posture computation in isolation.
        Replicates _extend_state_out_v04 lines 485-491 of controller.py.
        """
        from moralstack.orchestration.conversation_state import ConversationGovernanceState
        from moralstack.orchestration.types import (
            FinalResponse,
            OrchestratorResult,
            ProcessedRequest,
            ResponseMetadata,
            ResponseType,
        )

        state = ConversationGovernanceState(
            last_hard_constraints_triggered=hard_constraints,
        )
        request = ProcessedRequest(prompt="x")
        if domain:
            request.user_context.domain_overlay = domain
        metadata = ResponseMetadata(final_action=final_action, risk_score=0.1)
        result = OrchestratorResult(
            response=FinalResponse(content="", response_type=ResponseType.DIRECT, metadata=metadata),
            execution_trace=MagicMock(),
        )

        new_state = controller._extend_state_out_v04(
            state=state,
            request=request,
            result=result,
            call_ctx=ProcessCallContext(),
        )
        return new_state.last_governance_posture

    def _compute_lookup_posture(self, controller, *, final_action, overlay_sensitive, hard_signal_refuse):
        """Invoke the lookup-side posture computation directly."""
        from moralstack.orchestration.types import Decision

        decision = Decision(
            final_action=final_action,
            path="FAST_PATH",
            intent_clarity="HIGH",
            misuse_plausibility="LOW",
            actionability_risk="LOW",
            triggered_principles=[],
            hard_violations=[],
            risk_signals=[],
        )
        return controller._compute_governance_posture(
            decision=decision,
            overlay_sensitive=overlay_sensitive,
            hard_signal_refuse=hard_signal_refuse,
        )

    def test_normal_case_sensitive_overlay_both_elevated(self, monkeypatch):
        """SAFE_COMPLETE on a sensitive overlay must produce ELEVATED on both sides."""
        controller = self._build_controller_with_constitution(monkeypatch, overlay_is_sensitive=True)
        store_posture = self._compute_store_posture(
            controller,
            final_action="SAFE_COMPLETE",
            hard_constraints=(),
            domain="legal",
        )
        lookup_posture = self._compute_lookup_posture(
            controller,
            final_action="SAFE_COMPLETE",
            overlay_sensitive=True,
            hard_signal_refuse=False,
        )
        assert store_posture == lookup_posture == "ELEVATED", (
            f"Step 14.8 regression: store={store_posture}, lookup={lookup_posture}. "
            f"Before the fix, store would have been 'NORMAL' here."
        )

    def test_normal_case_non_sensitive_both_normal(self, monkeypatch):
        """NORMAL_COMPLETE without sensitive overlay produces NORMAL on both sides."""
        controller = self._build_controller_with_constitution(monkeypatch, overlay_is_sensitive=False)
        store_posture = self._compute_store_posture(
            controller,
            final_action="NORMAL_COMPLETE",
            hard_constraints=(),
            domain=None,
        )
        lookup_posture = self._compute_lookup_posture(
            controller,
            final_action="NORMAL_COMPLETE",
            overlay_sensitive=False,
            hard_signal_refuse=False,
        )
        assert store_posture == lookup_posture == "NORMAL"

    def test_refuse_with_hard_constraint_both_escalated(self, monkeypatch):
        """REFUSE on a hard-signal path produces ESCALATED on both sides."""
        controller = self._build_controller_with_constitution(monkeypatch, overlay_is_sensitive=False)
        store_posture = self._compute_store_posture(
            controller,
            final_action="REFUSE",
            hard_constraints=("CBRN_EXPLOSIVES",),
            domain=None,
        )
        lookup_posture = self._compute_lookup_posture(
            controller,
            final_action="REFUSE",
            overlay_sensitive=False,
            hard_signal_refuse=True,
        )
        assert store_posture == lookup_posture == "ESCALATED"

    def test_sensitive_overlay_with_refuse_but_no_hard_constraint(self, monkeypatch):
        """REFUSE on sensitive overlay without hard constraints → ELEVATED both sides.

        This is a subtle case: a soft refusal (no hard signal triggered) on a
        sensitive overlay should produce ELEVATED, not ESCALATED. Both formulae
        must agree.
        """
        controller = self._build_controller_with_constitution(monkeypatch, overlay_is_sensitive=True)
        store_posture = self._compute_store_posture(
            controller,
            final_action="REFUSE",
            hard_constraints=(),
            domain="medical",
        )
        lookup_posture = self._compute_lookup_posture(
            controller,
            final_action="REFUSE",
            overlay_sensitive=True,
            hard_signal_refuse=False,
        )
        assert store_posture == lookup_posture == "ELEVATED"


class TestActiveOverlayFieldNotRelied:
    """
    Regression guard: after Step 14.8, state.active_overlay is no longer the
    source of truth for posture. Verify the formula uses is_overlay_sensitive
    via the constitution_store, NOT state.active_overlay.
    """

    def test_posture_derives_from_constitution_not_from_state_active_overlay(self, monkeypatch):
        """
        Set state.active_overlay = 'legal' but make is_overlay_sensitive return
        False for that domain. Verify posture is NORMAL, NOT ELEVATED (pre-fix
        behaviour would have read state.active_overlay and produced ELEVATED).
        """
        from moralstack.orchestration import overlay_policy
        from moralstack.orchestration.controller import OrchestrationController
        from moralstack.orchestration.conversation_state import ConversationGovernanceState
        from moralstack.orchestration.types import (
            FinalResponse,
            OrchestratorConfig,
            OrchestratorResult,
            ProcessedRequest,
            ResponseMetadata,
            ResponseType,
        )

        monkeypatch.setattr(overlay_policy, "is_overlay_sensitive", lambda store, domain: False)
        monkeypatch.setattr(
            "moralstack.orchestration.controller.is_overlay_sensitive",
            lambda store, domain: False,
        )

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
            event_emitter=MagicMock(),
            ledger=MagicMock(),
        )

        state = ConversationGovernanceState(
            active_overlay="legal",
            last_hard_constraints_triggered=(),
        )
        request = ProcessedRequest(prompt="x")
        request.user_context.domain_overlay = "legal"
        metadata = ResponseMetadata(final_action="NORMAL_COMPLETE", risk_score=0.1)
        result = OrchestratorResult(
            response=FinalResponse(content="", response_type=ResponseType.DIRECT, metadata=metadata),
            execution_trace=MagicMock(),
        )

        new_state = controller._extend_state_out_v04(
            state=state,
            request=request,
            result=result,
            call_ctx=ProcessCallContext(),
        )
        assert new_state.last_governance_posture == "NORMAL"
