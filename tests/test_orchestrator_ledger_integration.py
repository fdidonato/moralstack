"""
Integration tests for ledger store/lookup round-trip via the orchestrator.

Step 14.3 regression: pre-fix code wrote request_type="" to the ledger because
_maybe_store_in_ledger read ResponseMetadata.request_type (field does not exist),
while lookup used the real value from the risk estimator. The secondary intent
check then returned intent_divergence on every subsequent lookup.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from moralstack.orchestration.controller import OrchestrationController
from moralstack.orchestration.process_context import ProcessCallContext
from moralstack.orchestration.types import (
    FinalResponse,
    OrchestratorConfig,
    OrchestratorResult,
    ProcessedRequest,
    ResponseMetadata,
    ResponseType,
)


def _controller_with_ledger() -> OrchestrationController:
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


class TestLedgerRequestTypeConsistency:
    """Verify store and lookup use the same request_type for any given turn."""

    def test_request_type_propagated_via_ctx_not_metadata(self) -> None:
        """
        When ctx carries _ledger_request_type, store must use THAT value,
        not the (missing) metadata.request_type field.
        """
        controller = _controller_with_ledger()

        request = ProcessedRequest(prompt="What's the climate of southern Italy?")
        metadata = ResponseMetadata(
            final_action="NORMAL_COMPLETE",
            risk_score=0.10,
            intent_clarity="HIGH",
            path="FAST_PATH",
        )
        result = OrchestratorResult(
            response=FinalResponse(content="...", response_type=ResponseType.DIRECT, metadata=metadata),
            execution_trace=MagicMock(),
        )

        ctx = {
            "conversation_id": "conv-test-1",
            "turn_index": 1,
            "_ledger_request_type": "factual_query",
            "_ledger_intent_clarity": "HIGH",
        }

        call_ctx = ProcessCallContext(
            conversation_id=ctx["conversation_id"],
            turn_index=ctx["turn_index"],
            ledger_request_type=ctx.get("_ledger_request_type"),
            ledger_intent_clarity=ctx.get("_ledger_intent_clarity"),
        )

        controller._maybe_store_in_ledger(request=request, result=result, call_ctx=call_ctx)

        assert controller._ledger.store.call_count == 1
        kwargs = controller._ledger.store.call_args.kwargs
        assert kwargs["request_type"] == "factual_query"
        assert kwargs["intent_clarity"] == "HIGH"
        assert kwargs["turn_index"] == 1

    def test_request_type_falls_back_to_empty_when_ctx_missing(self) -> None:
        """
        When ctx did not capture _ledger_request_type, store uses empty-string
        fallback (metadata has no request_type field today).
        """
        controller = _controller_with_ledger()

        request = ProcessedRequest(prompt="x")
        metadata = ResponseMetadata(
            final_action="NORMAL_COMPLETE",
            risk_score=0.1,
            intent_clarity="HIGH",
        )
        result = OrchestratorResult(
            response=FinalResponse(content="...", response_type=ResponseType.DIRECT, metadata=metadata),
            execution_trace=MagicMock(),
        )
        ctx = {
            "conversation_id": "conv-test-2",
            "turn_index": 1,
        }

        call_ctx = ProcessCallContext(
            conversation_id=ctx["conversation_id"],
            turn_index=ctx["turn_index"],
            ledger_request_type=ctx.get("_ledger_request_type"),
            ledger_intent_clarity=ctx.get("_ledger_intent_clarity"),
        )

        controller._maybe_store_in_ledger(request=request, result=result, call_ctx=call_ctx)

        assert controller._ledger.store.call_count == 1
        kwargs = controller._ledger.store.call_args.kwargs
        assert kwargs["request_type"] == ""


class TestLedgerRoundTripHit:
    """End-to-end ledger behaviour for request_type alignment."""

    def test_store_then_lookup_with_matching_request_type_hits(self) -> None:
        """
        Store with request_type='factual_query', then lookup with the same
        request_type and identical embedding: must hit, not intent_divergence.
        """
        from moralstack.orchestration.ledger import CachedDecision, SemanticDecisionLedger
        from moralstack.orchestration.ledger_storage import InMemoryLedgerStorage

        class _FixedEmbedder:
            def embed(self, _text: str) -> list[float]:
                return [1.0, 0.0, 0.0]

        ledger = SemanticDecisionLedger(
            embedder=_FixedEmbedder(),
            storage=InMemoryLedgerStorage(),
            similarity_threshold=0.80,
        )

        decision = CachedDecision(
            final_action="NORMAL_COMPLETE",
            risk_score=0.1,
            governance_posture="NORMAL",
            winning_rule="",
            decision_reason="",
            reason_codes=(),
            triggered_principles=(),
        )
        stored = ledger.store(
            prompt="climate of southern Italy",
            contract_hash="",
            posture="NORMAL",
            domain="environment",
            decision=decision,
            intent_clarity="HIGH",
            request_type="factual_query",
            turn_index=1,
        )
        assert stored is True

        result = ledger.lookup(
            prompt="weather of southern Italy",
            contract_hash="",
            posture="NORMAL",
            domain="environment",
            intent_clarity="HIGH",
            request_type="factual_query",
            turn_index=2,
        )
        assert result.is_hit is True, f"Expected hit, got miss with reason={result.reason}"
        assert result.from_turn == 1

    def test_store_with_empty_request_type_lookup_with_real_fails(self) -> None:
        """
        Pre-Step 14.3 bug: store writes request_type="" but lookup uses
        request_type='factual_query' -> intent_divergence miss.
        """
        from moralstack.orchestration.ledger import CachedDecision, SemanticDecisionLedger
        from moralstack.orchestration.ledger_storage import InMemoryLedgerStorage

        class _FixedEmbedder:
            def embed(self, _text: str) -> list[float]:
                return [1.0, 0.0, 0.0]

        ledger = SemanticDecisionLedger(
            embedder=_FixedEmbedder(),
            storage=InMemoryLedgerStorage(),
            similarity_threshold=0.80,
        )
        decision = CachedDecision(
            final_action="NORMAL_COMPLETE",
            risk_score=0.1,
            governance_posture="NORMAL",
            winning_rule="",
            decision_reason="",
            reason_codes=(),
            triggered_principles=(),
        )
        ledger.store(
            prompt="x",
            contract_hash="",
            posture="NORMAL",
            domain="environment",
            decision=decision,
            intent_clarity="HIGH",
            request_type="",
            turn_index=1,
        )
        result = ledger.lookup(
            prompt="y",
            contract_hash="",
            posture="NORMAL",
            domain="environment",
            intent_clarity="HIGH",
            request_type="factual_query",
            turn_index=2,
        )
        assert result.is_hit is False
        assert result.reason == "intent_divergence"
