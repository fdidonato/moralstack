"""
SemanticDecisionLedger — semantic cache of governance decisions.

The ledger detects when a new query is semantically equivalent to a past query
under the same governance context (same developer contract, same posture, same
domain), allowing the controller to reuse the past decision instead of running
the full deliberation pipeline again.

What is cached: the decision (final_action, risk_score, reason_codes,
triggered_principles, winning_rule, decision_reason, governance_posture).
What is NOT cached: response content (always regenerated for linguistic
freshness). This is DAF-4 of the design.

Skip rules (design §5.8):
- lookup returns miss when posture == 'ESCALATED' (session-specific).
- lookup returns miss when turn_index < 1 (first turn always fresh).
- store does not persist entries with posture == 'ESCALATED' or turn_index < 1.

Normative reference: MORALSTACK_MULTITURN_DESIGN.md v1.3 §5.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from moralstack.orchestration.embedder import cosine_similarity

if TYPE_CHECKING:
    from moralstack.orchestration.embedder import EmbedderProtocol
    from moralstack.orchestration.ledger_storage import LedgerStorageProtocol

_LOG = logging.getLogger(__name__)


DEFAULT_SIMILARITY_THRESHOLD = 0.92


# =============================================================================
# Cache key (exact-match components)
# =============================================================================


@dataclass(frozen=True)
class LedgerKey:
    """
    Exact-match components of a ledger lookup.

    Two queries are eligible for cache hit ONLY if their (contract_hash, posture,
    domain) match exactly. The semantic similarity is computed AFTER this filter.

    Fields:
        contract_hash: hash of the developer contract (from DeveloperContract.contract_hash).
            Empty string when no contract is declared.
        posture: governance posture for this lookup. 'NORMAL' | 'ELEVATED'.
            Note: 'ESCALATED' is never used as a key (skip rule).
        domain: domain overlay name, or None when no overlay applies.
    """

    contract_hash: str
    posture: str
    domain: str | None


# =============================================================================
# Cached decision (what we store)
# =============================================================================


@dataclass(frozen=True)
class CachedDecision:
    """
    Subset of decision metadata that is safe to reuse across semantically
    equivalent queries.

    This is intentionally NOT the same as orchestration.types.Decision — the
    ledger only needs the fields that are stable for reuse (final action,
    governance reasoning, and policy outcome). It does not carry signals like
    intent_clarity at this level (those are stored on the LedgerEntry for the
    secondary intent check).
    """

    final_action: str  # 'NORMAL_COMPLETE' | 'SAFE_COMPLETE' | 'REFUSE'
    risk_score: float
    governance_posture: str  # 'NORMAL' | 'ELEVATED'
    winning_rule: str = ""
    decision_reason: str = ""
    reason_codes: tuple[str, ...] = field(default_factory=tuple)
    triggered_principles: tuple[str, ...] = field(default_factory=tuple)


# =============================================================================
# Storage entry (cached decision + retrieval metadata)
# =============================================================================


@dataclass(frozen=True)
class LedgerEntry:
    """
    Single record stored in the ledger. Bundles the cached decision with the
    metadata required for semantic lookup and the secondary intent check.

    Fields:
        cached_decision: the decision to potentially reuse.
        embedding: the embedding vector of the original prompt.
        original_prompt: the original prompt text (for diagnostics; not used in matching).
        intent_clarity: 'LOW' | 'MEDIUM' | 'HIGH'. Required for the secondary intent check.
        request_type: free-form string from risk calibration (e.g. 'factual',
            'crisis_support'). Required for the secondary intent check.
        turn_index: the conversation turn at which the decision was made.
            Used as 'from_turn' in LedgerResult so the caller can attribute the cache hit.
    """

    cached_decision: CachedDecision
    embedding: list[float]
    original_prompt: str
    intent_clarity: str
    request_type: str
    turn_index: int


# =============================================================================
# Lookup result
# =============================================================================


@dataclass(frozen=True)
class LedgerResult:
    """
    Outcome of a ledger lookup.

    Fields:
        is_hit: True only when a candidate cleared both the similarity threshold
            AND the secondary intent check.
        cached_decision: the reusable decision when is_hit is True; None otherwise.
        similarity: the best similarity score observed (0.0 when no candidates).
        from_turn: turn index where the cached decision was originally produced
            (None when miss).
        reason: short machine-readable reason for misses ('no_candidates',
            'below_threshold', 'intent_divergence', 'posture_escalated',
            'turn_index_below_one'). Empty string when is_hit is True.
        query_embedding: the embedding vector computed during _lookup_impl() for
            the query prompt, or None when _lookup_impl() returned before calling
            embed() (posture_escalated, turn_index_below_one, no_candidates paths).
            Excluded from __hash__, __eq__, and __repr__. Callers may pass this to
            store(prompt_embedding=...) to skip a redundant embed() call.
    """

    is_hit: bool
    cached_decision: CachedDecision | None
    similarity: float
    from_turn: int | None
    reason: str
    query_embedding: list[float] | None = field(default=None, hash=False, compare=False, repr=False)


# =============================================================================
# Ledger
# =============================================================================


class SemanticDecisionLedger:
    """
    Semantic cache of governance decisions, gated by the design's skip rules
    and secondary intent check.

    Lookup flow:
        1. Skip if posture == 'ESCALATED' (return miss, reason='posture_escalated').
        2. Skip if turn_index < 1 (return miss, reason='turn_index_below_one').
        3. Retrieve all entries from storage for the exact-match LedgerKey.
        4. If no entries: miss, reason='no_candidates'.
        5. Embed the query prompt.
        6. Compute cosine similarity vs every candidate; pick the best.
        7. If best < threshold: miss, reason='below_threshold'.
        8. Secondary intent check: if best.intent_clarity != current intent_clarity
           OR best.request_type != current request_type: miss, reason='intent_divergence'.
        9. Hit: return cached decision.

    Store flow:
        1. Skip if posture == 'ESCALATED' (no-op).
        2. Skip if turn_index < 1 (no-op).
        3. If prompt_embedding is provided (validated, non-empty, correct dim),
           use it directly; otherwise embed the prompt via self._embedder.
        4. Persist a LedgerEntry under the LedgerKey.
    """

    def __init__(
        self,
        embedder: "EmbedderProtocol",
        storage: "LedgerStorageProtocol",
        similarity_threshold: float = DEFAULT_SIMILARITY_THRESHOLD,
    ) -> None:
        if not (0.0 <= similarity_threshold <= 1.0):
            raise ValueError(f"similarity_threshold must be in [0.0, 1.0], got {similarity_threshold}")
        self._embedder = embedder
        self._storage = storage
        self._threshold = similarity_threshold
        self._embedding_dim: int | None = None

    @property
    def similarity_threshold(self) -> float:
        return self._threshold

    def lookup(
        self,
        prompt: str,
        contract_hash: str,
        posture: str,
        domain: str | None,
        intent_clarity: str,
        request_type: str,
        turn_index: int,
    ) -> LedgerResult:
        """
        Look up a cached decision for the given query and governance context.

        Returns:
            LedgerResult with is_hit=True only when a candidate cleared all gates.
        """
        result = self._lookup_impl(
            prompt=prompt,
            contract_hash=contract_hash,
            posture=posture,
            domain=domain,
            intent_clarity=intent_clarity,
            request_type=request_type,
            turn_index=turn_index,
        )
        self._emit_lookup_event(
            result=result,
            contract_hash=contract_hash,
            posture=posture,
            domain=domain,
            intent_clarity=intent_clarity,
            request_type=request_type,
            turn_index=turn_index,
        )
        return result

    def _lookup_impl(
        self,
        *,
        prompt: str,
        contract_hash: str,
        posture: str,
        domain: str | None,
        intent_clarity: str,
        request_type: str,
        turn_index: int,
    ) -> LedgerResult:
        """Pure semantic-lookup logic without observability side-effects."""
        if posture == "ESCALATED":
            return LedgerResult(
                is_hit=False, cached_decision=None, similarity=0.0, from_turn=None, reason="posture_escalated"
            )
        if turn_index < 1:
            return LedgerResult(
                is_hit=False, cached_decision=None, similarity=0.0, from_turn=None, reason="turn_index_below_one"
            )

        key = LedgerKey(contract_hash=contract_hash, posture=posture, domain=domain)
        candidates = self._storage.get_entries(key)
        if not candidates:
            return LedgerResult(is_hit=False, cached_decision=None, similarity=0.0, from_turn=None, reason="no_candidates")

        query_embedding = self._embedder.embed(prompt)
        if self._embedding_dim is None:
            self._embedding_dim = len(query_embedding)

        best_entry: LedgerEntry | None = None
        best_similarity = -1.0
        for cand in candidates:
            sim = cosine_similarity(query_embedding, cand.embedding)
            if sim > best_similarity:
                best_similarity = sim
                best_entry = cand

        if best_entry is None or best_similarity < self._threshold:
            return LedgerResult(
                is_hit=False,
                cached_decision=None,
                similarity=max(0.0, best_similarity),
                from_turn=None,
                reason="below_threshold",
                query_embedding=query_embedding,
            )

        # Secondary intent check: same embedding magnitude is not enough.
        if best_entry.intent_clarity != intent_clarity or best_entry.request_type != request_type:
            return LedgerResult(
                is_hit=False,
                cached_decision=None,
                similarity=best_similarity,
                from_turn=None,
                reason="intent_divergence",
                query_embedding=query_embedding,
            )

        return LedgerResult(
            is_hit=True,
            cached_decision=best_entry.cached_decision,
            similarity=best_similarity,
            from_turn=best_entry.turn_index,
            reason="",
            query_embedding=query_embedding,
        )

    def store(
        self,
        prompt: str,
        contract_hash: str,
        posture: str,
        domain: str | None,
        decision: CachedDecision,
        intent_clarity: str,
        request_type: str,
        turn_index: int,
        *,
        prompt_embedding: list[float] | None = None,
    ) -> bool:
        """
        Persist a decision under the appropriate LedgerKey.

        Returns:
            True when the entry was stored; False when skipped per the design rules.
        """
        if posture == "ESCALATED":
            _LOG.debug("SemanticDecisionLedger.store skipped: posture=ESCALATED, turn=%d", turn_index)
            self._emit_store_event(
                outcome="skipped",
                reason="posture_escalated",
                contract_hash=contract_hash,
                posture=posture,
                domain=domain,
                decision=decision,
                intent_clarity=intent_clarity,
                request_type=request_type,
                turn_index=turn_index,
            )
            return False
        if turn_index < 1:
            _LOG.debug("SemanticDecisionLedger.store skipped: turn_index=%d < 1", turn_index)
            self._emit_store_event(
                outcome="skipped",
                reason="turn_index_below_one",
                contract_hash=contract_hash,
                posture=posture,
                domain=domain,
                decision=decision,
                intent_clarity=intent_clarity,
                request_type=request_type,
                turn_index=turn_index,
            )
            return False

        if prompt_embedding is not None:
            if len(prompt_embedding) == 0:
                raise ValueError("prompt_embedding must not be empty")
            if self._embedding_dim is not None and len(prompt_embedding) != self._embedding_dim:
                raise ValueError(
                    f"prompt_embedding dimension {len(prompt_embedding)} does not match "
                    f"ledger embedding dimension {self._embedding_dim}"
                )
            embedding = list(prompt_embedding)  # defensive copy; caller's list must not be mutated
        else:
            embedding = self._embedder.embed(prompt)
        if self._embedding_dim is None:
            self._embedding_dim = len(embedding)
        key = LedgerKey(contract_hash=contract_hash, posture=posture, domain=domain)
        entry = LedgerEntry(
            cached_decision=decision,
            embedding=embedding,
            original_prompt=prompt,
            intent_clarity=intent_clarity,
            request_type=request_type,
            turn_index=turn_index,
        )
        self._storage.put(key, entry)
        self._emit_store_event(
            outcome="stored",
            reason="stored",
            contract_hash=contract_hash,
            posture=posture,
            domain=domain,
            decision=decision,
            intent_clarity=intent_clarity,
            request_type=request_type,
            turn_index=turn_index,
        )
        return True

    # ------------------------------------------------------------------
    # Step 13 — observability for lookup/store paths
    # ------------------------------------------------------------------

    def _emit_lookup_event(
        self,
        *,
        result: LedgerResult,
        contract_hash: str,
        posture: str,
        domain: str | None,
        intent_clarity: str,
        request_type: str,
        turn_index: int,
    ) -> None:
        """Emit ``ledger.lookup`` on every code path (hit, miss, skip)."""
        try:
            # Lazy import keeps the orchestration package importable in
            # environments where the observability stack is not initialised.
            from moralstack.observability.conversation_events import emit_ledger_lookup

            cached = result.cached_decision
            emit_ledger_lookup(
                turn_index=turn_index,
                outcome="hit" if result.is_hit else "miss",
                reason=result.reason or None,
                similarity=result.similarity if result.similarity > 0 else None,
                from_turn=result.from_turn,
                contract_hash=contract_hash or None,
                posture=posture or None,
                domain=domain,
                intent_clarity=intent_clarity or None,
                request_type=request_type or None,
                final_action=getattr(cached, "final_action", None) if cached else None,
                risk_score=getattr(cached, "risk_score", None) if cached else None,
                threshold=self._threshold,
            )
        except Exception:
            _LOG.debug("ledger emit_ledger_lookup failed", exc_info=True)

    def _emit_store_event(
        self,
        *,
        outcome: str,
        reason: str,
        contract_hash: str,
        posture: str,
        domain: str | None,
        decision: CachedDecision | None,
        intent_clarity: str,
        request_type: str,
        turn_index: int,
    ) -> None:
        """Emit ``ledger.store`` on every code path (stored/skipped)."""
        try:
            from moralstack.observability.conversation_events import emit_ledger_store

            emit_ledger_store(
                turn_index=turn_index,
                outcome=outcome,
                reason=reason,
                contract_hash=contract_hash or None,
                posture=posture or None,
                domain=domain,
                intent_clarity=intent_clarity or None,
                request_type=request_type or None,
                final_action=getattr(decision, "final_action", None) if decision is not None else None,
                risk_score=getattr(decision, "risk_score", None) if decision is not None else None,
            )
        except Exception:
            _LOG.debug("ledger emit_ledger_store failed", exc_info=True)
