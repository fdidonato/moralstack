"""
Test suite for moralstack/orchestration/ledger.py — SemanticDecisionLedger.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from moralstack.orchestration.embedder import EmbedderProtocol
from moralstack.orchestration.ledger import (
    DEFAULT_SIMILARITY_THRESHOLD,
    CachedDecision,
    LedgerResult,
    SemanticDecisionLedger,
)
from moralstack.orchestration.ledger_storage import InMemoryLedgerStorage


class StubEmbedder:
    def __init__(self, mapping: dict[str, list[float]] | None = None, default: list[float] | None = None) -> None:
        self._mapping = mapping or {}
        self._default = default if default is not None else [0.0, 0.0]

    def embed(self, text: str) -> list[float]:
        return self._mapping.get(text, self._default)


def _decision(action: str = "NORMAL_COMPLETE", risk: float = 0.1) -> CachedDecision:
    return CachedDecision(
        final_action=action,
        risk_score=risk,
        governance_posture="NORMAL",
        winning_rule="test_rule",
        decision_reason="test reason",
        reason_codes=("rc1",),
        triggered_principles=("p1",),
    )


class TestSemanticDecisionLedgerInit:
    def test_default_threshold(self):
        ledger = SemanticDecisionLedger(StubEmbedder(), InMemoryLedgerStorage())
        assert ledger.similarity_threshold == DEFAULT_SIMILARITY_THRESHOLD

    def test_custom_threshold(self):
        ledger = SemanticDecisionLedger(StubEmbedder(), InMemoryLedgerStorage(), similarity_threshold=0.85)
        assert ledger.similarity_threshold == 0.85

    def test_threshold_below_zero_raises(self):
        with pytest.raises(ValueError, match="similarity_threshold"):
            SemanticDecisionLedger(StubEmbedder(), InMemoryLedgerStorage(), similarity_threshold=-0.1)

    def test_threshold_above_one_raises(self):
        with pytest.raises(ValueError, match="similarity_threshold"):
            SemanticDecisionLedger(StubEmbedder(), InMemoryLedgerStorage(), similarity_threshold=1.5)


class TestLookupSkipRules:
    def test_escalated_posture_returns_miss(self):
        ledger = SemanticDecisionLedger(StubEmbedder(), InMemoryLedgerStorage())
        result = ledger.lookup(
            prompt="q",
            contract_hash="abc",
            posture="ESCALATED",
            domain=None,
            intent_clarity="HIGH",
            request_type="factual",
            turn_index=5,
        )
        assert result.is_hit is False
        assert result.reason == "posture_escalated"

    def test_turn_zero_returns_miss(self):
        ledger = SemanticDecisionLedger(StubEmbedder(), InMemoryLedgerStorage())
        result = ledger.lookup(
            prompt="q",
            contract_hash="abc",
            posture="NORMAL",
            domain=None,
            intent_clarity="HIGH",
            request_type="factual",
            turn_index=0,
        )
        assert result.is_hit is False
        assert result.reason == "turn_index_below_one"

    def test_negative_turn_returns_miss(self):
        ledger = SemanticDecisionLedger(StubEmbedder(), InMemoryLedgerStorage())
        result = ledger.lookup(
            prompt="q",
            contract_hash="abc",
            posture="NORMAL",
            domain=None,
            intent_clarity="HIGH",
            request_type="factual",
            turn_index=-1,
        )
        assert result.is_hit is False
        assert result.reason == "turn_index_below_one"


class TestLookupAgainstEmptyStorage:
    def test_no_candidates(self):
        ledger = SemanticDecisionLedger(StubEmbedder(), InMemoryLedgerStorage())
        result = ledger.lookup(
            prompt="q",
            contract_hash="abc",
            posture="NORMAL",
            domain=None,
            intent_clarity="HIGH",
            request_type="factual",
            turn_index=2,
        )
        assert result.is_hit is False
        assert result.reason == "no_candidates"


class TestLookupSimilarityGate:
    def test_hit_when_above_threshold(self):
        emb = StubEmbedder(mapping={"first": [1.0, 0.0], "second": [1.0, 0.0]})
        storage = InMemoryLedgerStorage()
        ledger = SemanticDecisionLedger(emb, storage)
        ledger.store(
            prompt="first",
            contract_hash="abc",
            posture="NORMAL",
            domain=None,
            decision=_decision(),
            intent_clarity="HIGH",
            request_type="factual",
            turn_index=1,
        )
        result = ledger.lookup(
            prompt="second",
            contract_hash="abc",
            posture="NORMAL",
            domain=None,
            intent_clarity="HIGH",
            request_type="factual",
            turn_index=2,
        )
        assert result.is_hit is True
        assert result.similarity == pytest.approx(1.0)
        assert result.from_turn == 1
        assert result.cached_decision is not None
        assert result.cached_decision.final_action == "NORMAL_COMPLETE"

    def test_miss_when_below_threshold(self):
        emb = StubEmbedder(mapping={"first": [1.0, 0.0], "second": [0.0, 1.0]})
        storage = InMemoryLedgerStorage()
        ledger = SemanticDecisionLedger(emb, storage)
        ledger.store(
            prompt="first",
            contract_hash="abc",
            posture="NORMAL",
            domain=None,
            decision=_decision(),
            intent_clarity="HIGH",
            request_type="factual",
            turn_index=1,
        )
        result = ledger.lookup(
            prompt="second",
            contract_hash="abc",
            posture="NORMAL",
            domain=None,
            intent_clarity="HIGH",
            request_type="factual",
            turn_index=2,
        )
        assert result.is_hit is False
        assert result.reason == "below_threshold"

    def test_miss_when_at_threshold_minus_epsilon(self):
        import math

        emb = StubEmbedder(
            mapping={
                "first": [1.0, 0.0],
                "second": [0.91, math.sqrt(1.0 - 0.91**2)],
            }
        )
        storage = InMemoryLedgerStorage()
        ledger = SemanticDecisionLedger(emb, storage)
        ledger.store(
            prompt="first",
            contract_hash="abc",
            posture="NORMAL",
            domain=None,
            decision=_decision(),
            intent_clarity="HIGH",
            request_type="factual",
            turn_index=1,
        )
        result = ledger.lookup(
            prompt="second",
            contract_hash="abc",
            posture="NORMAL",
            domain=None,
            intent_clarity="HIGH",
            request_type="factual",
            turn_index=2,
        )
        assert result.is_hit is False
        assert result.reason == "below_threshold"
        assert result.similarity == pytest.approx(0.91, abs=1e-6)


class TestLookupExactMatchKeyIsolation:
    def test_different_contract_hash_misses(self):
        emb = StubEmbedder(mapping={"q": [1.0, 0.0]})
        storage = InMemoryLedgerStorage()
        ledger = SemanticDecisionLedger(emb, storage)
        ledger.store(
            prompt="q",
            contract_hash="abc",
            posture="NORMAL",
            domain=None,
            decision=_decision(),
            intent_clarity="HIGH",
            request_type="factual",
            turn_index=1,
        )
        result = ledger.lookup(
            prompt="q",
            contract_hash="DIFFERENT",
            posture="NORMAL",
            domain=None,
            intent_clarity="HIGH",
            request_type="factual",
            turn_index=2,
        )
        assert result.is_hit is False
        assert result.reason == "no_candidates"

    def test_different_domain_misses(self):
        emb = StubEmbedder(mapping={"q": [1.0, 0.0]})
        storage = InMemoryLedgerStorage()
        ledger = SemanticDecisionLedger(emb, storage)
        ledger.store(
            prompt="q",
            contract_hash="abc",
            posture="NORMAL",
            domain="legal",
            decision=_decision(),
            intent_clarity="HIGH",
            request_type="factual",
            turn_index=1,
        )
        result = ledger.lookup(
            prompt="q",
            contract_hash="abc",
            posture="NORMAL",
            domain="healthcare",
            intent_clarity="HIGH",
            request_type="factual",
            turn_index=2,
        )
        assert result.is_hit is False
        assert result.reason == "no_candidates"

    def test_different_posture_misses(self):
        emb = StubEmbedder(mapping={"q": [1.0, 0.0]})
        storage = InMemoryLedgerStorage()
        ledger = SemanticDecisionLedger(emb, storage)
        ledger.store(
            prompt="q",
            contract_hash="abc",
            posture="NORMAL",
            domain=None,
            decision=_decision(),
            intent_clarity="HIGH",
            request_type="factual",
            turn_index=1,
        )
        result = ledger.lookup(
            prompt="q",
            contract_hash="abc",
            posture="ELEVATED",
            domain=None,
            intent_clarity="HIGH",
            request_type="factual",
            turn_index=2,
        )
        assert result.is_hit is False
        assert result.reason == "no_candidates"


class TestLookupSecondaryIntentCheck:
    def test_intent_clarity_divergence_misses(self):
        emb = StubEmbedder(mapping={"q": [1.0, 0.0]})
        storage = InMemoryLedgerStorage()
        ledger = SemanticDecisionLedger(emb, storage)
        ledger.store(
            prompt="q",
            contract_hash="abc",
            posture="NORMAL",
            domain=None,
            decision=_decision(),
            intent_clarity="HIGH",
            request_type="factual",
            turn_index=1,
        )
        result = ledger.lookup(
            prompt="q",
            contract_hash="abc",
            posture="NORMAL",
            domain=None,
            intent_clarity="LOW",
            request_type="factual",
            turn_index=2,
        )
        assert result.is_hit is False
        assert result.reason == "intent_divergence"
        assert result.similarity == pytest.approx(1.0)

    def test_request_type_divergence_misses(self):
        emb = StubEmbedder(mapping={"q": [1.0, 0.0]})
        storage = InMemoryLedgerStorage()
        ledger = SemanticDecisionLedger(emb, storage)
        ledger.store(
            prompt="q",
            contract_hash="abc",
            posture="NORMAL",
            domain=None,
            decision=_decision(),
            intent_clarity="HIGH",
            request_type="factual",
            turn_index=1,
        )
        result = ledger.lookup(
            prompt="q",
            contract_hash="abc",
            posture="NORMAL",
            domain=None,
            intent_clarity="HIGH",
            request_type="crisis_support",
            turn_index=2,
        )
        assert result.is_hit is False
        assert result.reason == "intent_divergence"


class TestLookupBestCandidate:
    def test_picks_best_among_multiple(self):
        emb = StubEmbedder(
            mapping={
                "q1": [1.0, 0.0],
                "q2": [0.0, 1.0],
                "query": [0.95, 0.05],
            }
        )
        storage = InMemoryLedgerStorage()
        ledger = SemanticDecisionLedger(emb, storage)
        ledger.store(
            prompt="q1",
            contract_hash="abc",
            posture="NORMAL",
            domain=None,
            decision=_decision(action="NORMAL_COMPLETE"),
            intent_clarity="HIGH",
            request_type="factual",
            turn_index=1,
        )
        ledger.store(
            prompt="q2",
            contract_hash="abc",
            posture="NORMAL",
            domain=None,
            decision=_decision(action="REFUSE"),
            intent_clarity="HIGH",
            request_type="factual",
            turn_index=2,
        )
        result = ledger.lookup(
            prompt="query",
            contract_hash="abc",
            posture="NORMAL",
            domain=None,
            intent_clarity="HIGH",
            request_type="factual",
            turn_index=3,
        )
        assert result.is_hit is True
        assert result.cached_decision.final_action == "NORMAL_COMPLETE"
        assert result.from_turn == 1


class TestStoreSkipRules:
    def test_skip_when_escalated(self):
        emb = StubEmbedder()
        storage = InMemoryLedgerStorage()
        ledger = SemanticDecisionLedger(emb, storage)
        stored = ledger.store(
            prompt="q",
            contract_hash="abc",
            posture="ESCALATED",
            domain=None,
            decision=_decision(),
            intent_clarity="HIGH",
            request_type="factual",
            turn_index=5,
        )
        assert stored is False
        assert storage.size() == 0

    def test_skip_when_turn_zero(self):
        emb = StubEmbedder()
        storage = InMemoryLedgerStorage()
        ledger = SemanticDecisionLedger(emb, storage)
        stored = ledger.store(
            prompt="q",
            contract_hash="abc",
            posture="NORMAL",
            domain=None,
            decision=_decision(),
            intent_clarity="HIGH",
            request_type="factual",
            turn_index=0,
        )
        assert stored is False
        assert storage.size() == 0

    def test_store_persists_when_eligible(self):
        emb = StubEmbedder(mapping={"q": [1.0]})
        storage = InMemoryLedgerStorage()
        ledger = SemanticDecisionLedger(emb, storage)
        stored = ledger.store(
            prompt="q",
            contract_hash="abc",
            posture="NORMAL",
            domain=None,
            decision=_decision(),
            intent_clarity="HIGH",
            request_type="factual",
            turn_index=2,
        )
        assert stored is True
        assert storage.size() == 1


class TestProtocolConformance:
    def test_stub_embedder_satisfies_protocol(self):
        stub: EmbedderProtocol = StubEmbedder()
        assert stub.embed("anything") == [0.0, 0.0]


class _CountingStubEmbedder:
    def __init__(self, vector: list[float] | None = None) -> None:
        self.call_count = 0
        self._default = vector or [1.0, 0.0, 0.0]
        self._vectors: dict[str, list[float]] = {
            "first": [1.0, 0.0, 0.0],
            "second": [0.0, 1.0, 0.0],
        }

    def embed(self, text: str) -> list[float]:
        self.call_count += 1
        return list(self._vectors.get(text, self._default))


class TestLedgerResultQueryEmbedding:
    def test_query_embedding_none_on_posture_escalated(self) -> None:
        ledger = SemanticDecisionLedger(StubEmbedder(), InMemoryLedgerStorage())
        result = ledger.lookup(
            prompt="q",
            contract_hash="abc",
            posture="ESCALATED",
            domain=None,
            intent_clarity="HIGH",
            request_type="factual",
            turn_index=5,
        )
        assert result.query_embedding is None

    def test_query_embedding_none_on_turn_zero(self) -> None:
        ledger = SemanticDecisionLedger(StubEmbedder(), InMemoryLedgerStorage())
        result = ledger.lookup(
            prompt="q",
            contract_hash="abc",
            posture="NORMAL",
            domain=None,
            intent_clarity="HIGH",
            request_type="factual",
            turn_index=0,
        )
        assert result.query_embedding is None

    def test_query_embedding_none_on_no_candidates(self) -> None:
        ledger = SemanticDecisionLedger(StubEmbedder(), InMemoryLedgerStorage())
        result = ledger.lookup(
            prompt="q",
            contract_hash="abc",
            posture="NORMAL",
            domain=None,
            intent_clarity="HIGH",
            request_type="factual",
            turn_index=2,
        )
        assert result.reason == "no_candidates"
        assert result.query_embedding is None

    def test_query_embedding_populated_on_below_threshold(self) -> None:
        emb = StubEmbedder(mapping={"first": [1.0, 0.0], "second": [0.0, 1.0]})
        ledger = SemanticDecisionLedger(emb, InMemoryLedgerStorage())
        ledger.store(
            prompt="first",
            contract_hash="abc",
            posture="NORMAL",
            domain=None,
            decision=_decision(),
            intent_clarity="HIGH",
            request_type="factual",
            turn_index=1,
        )
        result = ledger.lookup(
            prompt="second",
            contract_hash="abc",
            posture="NORMAL",
            domain=None,
            intent_clarity="HIGH",
            request_type="factual",
            turn_index=2,
        )
        assert result.reason == "below_threshold"
        assert result.query_embedding is not None
        assert len(result.query_embedding) == 2

    def test_query_embedding_populated_on_hit(self) -> None:
        emb = StubEmbedder(mapping={"first": [1.0, 0.0], "second": [1.0, 0.0]})
        ledger = SemanticDecisionLedger(emb, InMemoryLedgerStorage())
        ledger.store(
            prompt="first",
            contract_hash="abc",
            posture="NORMAL",
            domain=None,
            decision=_decision(),
            intent_clarity="HIGH",
            request_type="factual",
            turn_index=1,
        )
        result = ledger.lookup(
            prompt="second",
            contract_hash="abc",
            posture="NORMAL",
            domain=None,
            intent_clarity="HIGH",
            request_type="factual",
            turn_index=2,
        )
        assert result.is_hit is True
        assert result.query_embedding is not None


class TestLedgerResultIsFrozen:
    def test_ledger_result_is_frozen(self) -> None:
        import dataclasses

        r = LedgerResult(
            is_hit=False,
            cached_decision=None,
            similarity=0.0,
            from_turn=None,
            reason="no_candidates",
        )
        with pytest.raises((dataclasses.FrozenInstanceError, AttributeError)):
            r.is_hit = True

    def test_ledger_result_with_query_embedding_is_hashable(self) -> None:
        r = LedgerResult(
            is_hit=False,
            cached_decision=None,
            similarity=0.5,
            from_turn=None,
            reason="below_threshold",
            query_embedding=[0.1, 0.2, 0.3],
        )
        # Must not raise TypeError: unhashable type: 'list'
        h = hash(r)
        assert isinstance(h, int)

    def test_query_embedding_excluded_from_equality(self) -> None:
        base = dict(is_hit=False, cached_decision=None, similarity=0.5, from_turn=None, reason="below_threshold")
        r1 = LedgerResult(**base, query_embedding=[1.0, 0.0])
        r2 = LedgerResult(**base, query_embedding=[0.0, 1.0])
        assert r1 == r2  # query_embedding has compare=False

    def test_query_embedding_excluded_from_repr(self) -> None:
        r = LedgerResult(
            is_hit=False,
            cached_decision=None,
            similarity=0.0,
            from_turn=None,
            reason="no_candidates",
            query_embedding=[0.1, 0.2],
        )
        assert "query_embedding" not in repr(r)


class TestStoreSkipsEmbedOnProvidedEmbedding:
    def test_store_with_prompt_embedding_does_not_call_embedder(self) -> None:
        mock = MagicMock()
        mock.embed.return_value = [0.1, 0.2]
        ledger = SemanticDecisionLedger(mock, InMemoryLedgerStorage())
        ledger.store(
            prompt="q",
            contract_hash="abc",
            posture="NORMAL",
            domain=None,
            decision=_decision(),
            intent_clarity="HIGH",
            request_type="factual",
            turn_index=2,
            prompt_embedding=[0.1, 0.2],
        )
        mock.embed.assert_not_called()

    def test_store_without_prompt_embedding_calls_embedder(self) -> None:
        mock = MagicMock()
        mock.embed.return_value = [0.1, 0.2]
        ledger = SemanticDecisionLedger(mock, InMemoryLedgerStorage())
        ledger.store(
            prompt="q",
            contract_hash="abc",
            posture="NORMAL",
            domain=None,
            decision=_decision(),
            intent_clarity="HIGH",
            request_type="factual",
            turn_index=2,
        )
        mock.embed.assert_called_once()

    def test_store_with_none_prompt_embedding_calls_embedder(self) -> None:
        mock = MagicMock()
        mock.embed.return_value = [0.1, 0.2]
        ledger = SemanticDecisionLedger(mock, InMemoryLedgerStorage())
        ledger.store(
            prompt="q",
            contract_hash="abc",
            posture="NORMAL",
            domain=None,
            decision=_decision(),
            intent_clarity="HIGH",
            request_type="factual",
            turn_index=2,
            prompt_embedding=None,
        )
        mock.embed.assert_called_once()

    def test_store_backward_compat_no_kwarg_calls_embedder(self) -> None:
        mock = MagicMock()
        mock.embed.return_value = [0.1, 0.2]
        ledger = SemanticDecisionLedger(mock, InMemoryLedgerStorage())
        ledger.store(
            prompt="q",
            contract_hash="abc",
            posture="NORMAL",
            domain=None,
            decision=_decision(),
            intent_clarity="HIGH",
            request_type="factual",
            turn_index=2,
        )
        mock.embed.assert_called_once()


class TestStorePromptEmbeddingValidation:
    def test_store_empty_prompt_embedding_raises(self) -> None:
        ledger = SemanticDecisionLedger(StubEmbedder(), InMemoryLedgerStorage())
        with pytest.raises(ValueError, match="must not be empty"):
            ledger.store(
                prompt="q",
                contract_hash="abc",
                posture="NORMAL",
                domain=None,
                decision=_decision(),
                intent_clarity="HIGH",
                request_type="factual",
                turn_index=2,
                prompt_embedding=[],
            )

    def test_store_wrong_dim_prompt_embedding_raises(self) -> None:
        emb = StubEmbedder(default=[0.1, 0.2])
        ledger = SemanticDecisionLedger(emb, InMemoryLedgerStorage())
        ledger.store(
            prompt="first",
            contract_hash="abc",
            posture="NORMAL",
            domain=None,
            decision=_decision(),
            intent_clarity="HIGH",
            request_type="factual",
            turn_index=1,
        )
        with pytest.raises(ValueError, match="dimension"):
            ledger.store(
                prompt="second",
                contract_hash="abc",
                posture="NORMAL",
                domain=None,
                decision=_decision(),
                intent_clarity="HIGH",
                request_type="factual",
                turn_index=2,
                prompt_embedding=[1.0, 2.0, 3.0],
            )

    def test_store_correct_dim_prompt_embedding_accepted(self) -> None:
        mock = MagicMock()
        mock.embed.return_value = [0.1, 0.2]
        ledger = SemanticDecisionLedger(mock, InMemoryLedgerStorage())
        ledger.store(
            prompt="first",
            contract_hash="abc",
            posture="NORMAL",
            domain=None,
            decision=_decision(),
            intent_clarity="HIGH",
            request_type="factual",
            turn_index=1,
        )
        mock.reset_mock()
        stored = ledger.store(
            prompt="second",
            contract_hash="abc",
            posture="NORMAL",
            domain=None,
            decision=_decision(),
            intent_clarity="HIGH",
            request_type="factual",
            turn_index=2,
            prompt_embedding=[0.3, 0.4],
        )
        assert stored is True
        mock.embed.assert_not_called()

    def test_store_prompt_embedding_is_keyword_only(self) -> None:
        ledger = SemanticDecisionLedger(StubEmbedder(), InMemoryLedgerStorage())
        with pytest.raises(TypeError):
            ledger.store(
                "q",
                "abc",
                "NORMAL",
                None,
                _decision(),
                "HIGH",
                "factual",
                2,
                [0.1, 0.2],
            )


class TestDoubleEmbedElimination:
    def test_miss_then_store_embeds_once(self) -> None:
        emb = _CountingStubEmbedder(vector=[1.0, 0.0, 0.0])
        ledger = SemanticDecisionLedger(emb, InMemoryLedgerStorage(), similarity_threshold=0.99)
        ledger.store(
            prompt="first",
            contract_hash="abc",
            posture="NORMAL",
            domain=None,
            decision=_decision(),
            intent_clarity="HIGH",
            request_type="factual",
            turn_index=1,
        )
        result = ledger.lookup(
            prompt="second",
            contract_hash="abc",
            posture="NORMAL",
            domain=None,
            intent_clarity="HIGH",
            request_type="factual",
            turn_index=2,
        )
        assert result.reason == "below_threshold"
        assert result.query_embedding is not None
        ledger.store(
            prompt="second",
            contract_hash="abc",
            posture="NORMAL",
            domain=None,
            decision=_decision(),
            intent_clarity="HIGH",
            request_type="factual",
            turn_index=2,
            prompt_embedding=result.query_embedding,
        )
        assert emb.call_count == 2
