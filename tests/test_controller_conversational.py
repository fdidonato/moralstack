"""
Test suite for Step 6 — controller wiring to conversational mode.

These tests exercise:
1. Backward compatibility: when ledger=None and session_store=None, the controller
   behaves identically to baseline (existing tests in the repo cover this; we add
   construction-time invariants here).
2. Ledger lookup is invoked when ledger and conversation_id are provided.
3. Ledger store is invoked after a successful turn.
4. state_out carries the v0.4 fields (last_developer_contract_hash, posture, summary).
5. Skip rules: turn_index=0 means no store; ledger=None means no lookup/store.

We use mock orchestration dependencies; the controller is not run end-to-end.
Step 6 has no behavioral effect on routing, so a thin test surface is sufficient.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

from moralstack.orchestration.contract import DeveloperContract
from moralstack.orchestration.conversation_state import (
    ConversationGovernanceState,
    TurnDecisionSummary,
)
from moralstack.orchestration.ledger import (
    CachedDecision,
    SemanticDecisionLedger,
)
from moralstack.orchestration.ledger_storage import InMemoryLedgerStorage
from moralstack.orchestration.process_context import ProcessCallContext
from moralstack.orchestration.types import ProcessedRequest


def _call_ctx_from_dict(d: dict[str, Any]) -> ProcessCallContext:
    """Build ProcessCallContext from legacy test dict keys (underscore ledger fields)."""
    return ProcessCallContext(
        conversation_id=d.get("conversation_id"),
        turn_index=d.get("turn_index"),
        parent_request_id=d.get("parent_request_id"),
        conversation_state=d.get("conversation_state"),
        ledger_lookup=d.get("_ledger_lookup"),
        ledger_request_type=d.get("_ledger_request_type"),
        ledger_intent_clarity=d.get("_ledger_intent_clarity"),
        ledger_hit_applied=bool(d.get("_ledger_hit_applied")),
    )


class StubEmbedder:
    """Embedder returning fixed vectors for deterministic similarity."""

    def __init__(self, mapping: dict[str, list[float]] | None = None) -> None:
        self._mapping = mapping or {}

    def embed(self, text: str) -> list[float]:
        return self._mapping.get(text, [1.0, 0.0])


def _build_controller_minimal(
    *,
    ledger: SemanticDecisionLedger | None = None,
    session_store: Any = None,
):
    """
    Build a minimal OrchestrationController with mocked dependencies.

    NOTE: the class is named `OrchestrationController` (NOT `Orchestrator`).
    `RiskThresholds` has only `low` and `medium` fields (no `high`); use defaults.

    The dependencies are sufficient to construct the controller, NOT to run process()
    end-to-end. Tests of Step 6 wiring inspect attributes and helper method behavior
    without invoking the full pipeline.
    """
    from moralstack.orchestration.controller import OrchestrationController
    from moralstack.orchestration.types import OrchestratorConfig, RiskThresholds

    config = OrchestratorConfig(risk_thresholds=RiskThresholds())
    return OrchestrationController(
        config=config,
        policy=MagicMock(),
        risk_estimator=MagicMock(),
        critic=MagicMock(),
        simulator=MagicMock(),
        hindsight=MagicMock(),
        perspectives=MagicMock(),
        constitution_store=MagicMock(),
        output_protector=MagicMock(),
        protected_system_prompt="test prompt",
        ledger=ledger,
        session_store=session_store,
    )


class TestControllerInitBackwardCompat:
    """The new v0.4 parameters are opt-in; existing construction patterns work."""

    def test_default_no_v04_params(self):
        orch = _build_controller_minimal()
        assert orch._ledger is None
        assert orch._session_store is None

    def test_construction_with_ledger(self):
        ledger = SemanticDecisionLedger(StubEmbedder(), InMemoryLedgerStorage())
        orch = _build_controller_minimal(ledger=ledger)
        assert orch._ledger is ledger

    def test_construction_with_session_store(self):
        from moralstack.sdk.session_store import InMemorySessionStore

        store = InMemorySessionStore()
        orch = _build_controller_minimal(session_store=store)
        assert orch._session_store is store


class TestLookupHelper:
    """The _lookup_cached_decision helper invokes the ledger or returns None."""

    def test_returns_none_when_no_ledger(self):
        orch = _build_controller_minimal()
        result = orch._lookup_cached_decision(
            prompt="x",
            contract_hash="abc",
            posture="NORMAL",
            domain=None,
            intent_clarity="HIGH",
            request_type="factual",
            turn_index=2,
        )
        assert result is None

    def test_returns_miss_when_ledger_empty(self):
        ledger = SemanticDecisionLedger(StubEmbedder(), InMemoryLedgerStorage())
        orch = _build_controller_minimal(ledger=ledger)
        result = orch._lookup_cached_decision(
            prompt="x",
            contract_hash="abc",
            posture="NORMAL",
            domain=None,
            intent_clarity="HIGH",
            request_type="factual",
            turn_index=2,
        )
        assert result is not None
        assert result.is_hit is False
        assert result.reason == "no_candidates"

    def test_returns_hit_after_store(self):
        ledger = SemanticDecisionLedger(StubEmbedder(), InMemoryLedgerStorage())
        orch = _build_controller_minimal(ledger=ledger)

        # Seed the ledger directly.
        cached = CachedDecision(
            final_action="NORMAL_COMPLETE",
            risk_score=0.1,
            governance_posture="NORMAL",
        )
        ledger.store(
            prompt="x",
            contract_hash="abc",
            posture="NORMAL",
            domain=None,
            decision=cached,
            intent_clarity="HIGH",
            request_type="factual",
            turn_index=1,
        )

        result = orch._lookup_cached_decision(
            prompt="x",
            contract_hash="abc",
            posture="NORMAL",
            domain=None,
            intent_clarity="HIGH",
            request_type="factual",
            turn_index=2,
        )
        assert result is not None
        assert result.is_hit is True
        assert result.from_turn == 1


class TestPostureComputation:
    """_compute_governance_posture maps signals correctly."""

    def test_normal_posture(self):
        orch = _build_controller_minimal()
        decision = MagicMock()
        decision.final_action = "NORMAL_COMPLETE"
        posture = orch._compute_governance_posture(decision=decision, overlay_sensitive=False, hard_signal_refuse=False)
        assert posture == "NORMAL"

    def test_elevated_posture_when_overlay_sensitive(self):
        orch = _build_controller_minimal()
        decision = MagicMock()
        decision.final_action = "NORMAL_COMPLETE"
        posture = orch._compute_governance_posture(decision=decision, overlay_sensitive=True, hard_signal_refuse=False)
        assert posture == "ELEVATED"

    def test_escalated_posture_when_hard_signal_refuse(self):
        orch = _build_controller_minimal()
        decision = MagicMock()
        decision.final_action = "REFUSE"
        posture = orch._compute_governance_posture(decision=decision, overlay_sensitive=False, hard_signal_refuse=True)
        assert posture == "ESCALATED"

    def test_escalated_takes_precedence_over_elevated(self):
        orch = _build_controller_minimal()
        decision = MagicMock()
        decision.final_action = "REFUSE"
        posture = orch._compute_governance_posture(decision=decision, overlay_sensitive=True, hard_signal_refuse=True)
        assert posture == "ESCALATED"


class TestStoreInLedgerHelper:
    """_maybe_store_in_ledger persists or is a no-op based on context."""

    def _build_result(
        self,
        *,
        final_action: str = "NORMAL_COMPLETE",
        risk_score: float = 0.1,
        decision_path: str = "BENIGN_FAST_PATH",
        intent_clarity: str = "HIGH",
        request_type: str = "factual",
    ) -> Any:
        """Build a minimal result-like object that _maybe_store_in_ledger can read."""
        metadata = MagicMock()
        metadata.final_action = final_action
        metadata.risk_score = risk_score
        metadata.decision_path = decision_path
        metadata.path = decision_path
        metadata.intent_clarity = intent_clarity
        metadata.request_type = request_type
        metadata.winning_decision_reason = "test reason"
        metadata.reason_codes = ["rc1"]
        metadata.triggered_principles = ["p1"]

        response = MagicMock()
        response.metadata = metadata

        result = MagicMock()
        result.response = response
        result.conversation_governance_state_out = ConversationGovernanceState(
            last_governance_posture="NORMAL",
        )
        return result

    def test_no_op_when_no_ledger(self):
        orch = _build_controller_minimal()
        request = ProcessedRequest(prompt="x")
        ctx = {"conversation_id": "c1", "turn_index": 1}
        # Should not raise.
        orch._maybe_store_in_ledger(request=request, result=self._build_result(), call_ctx=_call_ctx_from_dict(ctx))

    def test_no_op_when_no_conversation_id(self):
        ledger = SemanticDecisionLedger(StubEmbedder(), InMemoryLedgerStorage())
        orch = _build_controller_minimal(ledger=ledger)
        request = ProcessedRequest(prompt="x")
        ctx = {"conversation_id": None, "turn_index": 1}
        orch._maybe_store_in_ledger(request=request, result=self._build_result(), call_ctx=_call_ctx_from_dict(ctx))
        # Ledger storage stays empty.
        assert ledger._storage.size() == 0

    def test_stores_when_eligible(self):
        ledger = SemanticDecisionLedger(StubEmbedder(), InMemoryLedgerStorage())
        orch = _build_controller_minimal(ledger=ledger)
        contract = DeveloperContract.from_text("you are an assistant")
        request = ProcessedRequest(prompt="hello", developer_contract=contract)
        ctx = {"conversation_id": "c1", "turn_index": 2}
        orch._maybe_store_in_ledger(request=request, result=self._build_result(), call_ctx=_call_ctx_from_dict(ctx))
        assert ledger._storage.size() == 1

    def test_skip_when_turn_zero(self):
        """Ledger's own skip rule trips on turn_index=0."""
        ledger = SemanticDecisionLedger(StubEmbedder(), InMemoryLedgerStorage())
        orch = _build_controller_minimal(ledger=ledger)
        request = ProcessedRequest(prompt="hello")
        ctx = {"conversation_id": "c1", "turn_index": 0}
        orch._maybe_store_in_ledger(request=request, result=self._build_result(), call_ctx=_call_ctx_from_dict(ctx))
        assert ledger._storage.size() == 0

    def test_no_crash_when_metadata_missing(self):
        """Defensive: a result without metadata should not crash _maybe_store_in_ledger."""
        ledger = SemanticDecisionLedger(StubEmbedder(), InMemoryLedgerStorage())
        orch = _build_controller_minimal(ledger=ledger)
        request = ProcessedRequest(prompt="hello")
        ctx = {"conversation_id": "c1", "turn_index": 1}

        bad_result = MagicMock()
        bad_result.response = None
        # Should not raise.
        orch._maybe_store_in_ledger(request=request, result=bad_result, call_ctx=_call_ctx_from_dict(ctx))
        # Nothing stored (final_action was empty).
        assert ledger._storage.size() == 0


class TestStateOutV04Extension:
    """_extend_state_out_v04 adds the new v0.4 fields to state_out."""

    def _build_result_for_state(self, final_action: str = "NORMAL_COMPLETE", risk: float = 0.2) -> Any:
        metadata = MagicMock()
        metadata.final_action = final_action
        metadata.risk_score = risk
        metadata.decision_path = "BENIGN_FAST_PATH"
        metadata.path = "BENIGN_FAST_PATH"
        response = MagicMock()
        response.metadata = metadata
        result = MagicMock()
        result.response = response
        return result

    def test_contract_hash_propagated(self):
        orch = _build_controller_minimal()
        contract = DeveloperContract.from_text("you are an assistant")
        request = ProcessedRequest(prompt="hello", developer_contract=contract)
        base_state = ConversationGovernanceState(turn_index=2)
        extended = orch._extend_state_out_v04(
            state=base_state,
            request=request,
            result=self._build_result_for_state(),
            call_ctx=ProcessCallContext(),
        )
        assert extended.last_developer_contract_hash == contract.contract_hash

    def test_turn_decisions_summary_appended(self):
        orch = _build_controller_minimal()
        request = ProcessedRequest(prompt="hello")
        prior = TurnDecisionSummary(turn_index=0, final_action="NORMAL_COMPLETE", risk_score=0.1)
        base_state = ConversationGovernanceState(turn_index=1, turn_decisions_summary=(prior,))
        extended = orch._extend_state_out_v04(
            state=base_state,
            request=request,
            result=self._build_result_for_state(final_action="SAFE_COMPLETE", risk=0.5),
            call_ctx=ProcessCallContext(),
        )
        assert len(extended.turn_decisions_summary) == 2
        assert extended.turn_decisions_summary[-1].final_action == "SAFE_COMPLETE"
        assert extended.turn_decisions_summary[-1].risk_score == 0.5

    def test_posture_normal_by_default(self, monkeypatch):
        monkeypatch.setattr(
            "moralstack.orchestration.controller.is_overlay_sensitive",
            lambda store, domain: False,
        )
        orch = _build_controller_minimal()
        request = ProcessedRequest(prompt="hello")
        base_state = ConversationGovernanceState()
        extended = orch._extend_state_out_v04(
            state=base_state,
            request=request,
            result=self._build_result_for_state(),
            call_ctx=ProcessCallContext(),
        )
        assert extended.last_governance_posture == "NORMAL"

    def test_posture_escalated_on_refuse_with_hard_constraints(self):
        orch = _build_controller_minimal()
        request = ProcessedRequest(prompt="hello")
        base_state = ConversationGovernanceState(
            last_hard_constraints_triggered=("self_harm_crisis",),
        )
        extended = orch._extend_state_out_v04(
            state=base_state,
            request=request,
            result=self._build_result_for_state(final_action="REFUSE"),
            call_ctx=ProcessCallContext(),
        )
        assert extended.last_governance_posture == "ESCALATED"

    def test_posture_elevated_when_overlay_sensitive(self, monkeypatch):
        """Step 14.8: posture follows is_overlay_sensitive(domain), not state.active_overlay."""
        monkeypatch.setattr(
            "moralstack.orchestration.controller.is_overlay_sensitive",
            lambda store, domain: domain == "healthcare",
        )
        orch = _build_controller_minimal()
        request = ProcessedRequest(prompt="hello")
        request.user_context.domain_overlay = "healthcare"
        base_state = ConversationGovernanceState(active_overlay="healthcare")
        extended = orch._extend_state_out_v04(
            state=base_state,
            request=request,
            result=self._build_result_for_state(),
            call_ctx=ProcessCallContext(),
        )
        assert extended.last_governance_posture == "ELEVATED"


class TestStep7Integration:
    """Verify Step 7 wiring: runner attribute, was_cached flag propagation."""

    def test_controller_has_fast_path_runner(self):
        """The controller owns an instance of ConversationalFastPathRunner."""
        from moralstack.orchestration.conversational_fast_path import ConversationalFastPathRunner

        orch = _build_controller_minimal()
        assert hasattr(orch, "_fast_path_runner")
        assert isinstance(orch._fast_path_runner, ConversationalFastPathRunner)

    def test_extend_state_out_marks_cached_when_flag_set(self):
        """_extend_state_out_v04 reads ledger_hit_applied from call_ctx and marks was_cached."""
        orch = _build_controller_minimal()
        request = ProcessedRequest(prompt="hello")
        base_state = ConversationGovernanceState()

        # Build a minimal result like the existing tests do.
        metadata = MagicMock()
        metadata.final_action = "NORMAL_COMPLETE"
        metadata.risk_score = 0.1
        metadata.decision_path = "BENIGN_FAST_PATH"
        response = MagicMock()
        response.metadata = metadata
        result = MagicMock()
        result.response = response

        # Without the flag: was_cached is False (Step 6 baseline).
        extended_no_flag = orch._extend_state_out_v04(
            state=base_state, request=request, result=result, call_ctx=ProcessCallContext()
        )
        assert extended_no_flag.turn_decisions_summary[-1].was_cached is False

        # With the flag: was_cached is True.
        extended_with_flag = orch._extend_state_out_v04(
            state=base_state,
            request=request,
            result=result,
            call_ctx=_call_ctx_from_dict({"_ledger_hit_applied": True}),
        )
        assert extended_with_flag.turn_decisions_summary[-1].was_cached is True
