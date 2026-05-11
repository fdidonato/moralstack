"""
Conversation-scoped governance state (foundation only).

Carry-forward rules for multi-turn governance are not implemented here; this module
defines an explicit, auditable container for future conversational reuse.
Single-turn flows do not require this type.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any


@dataclass(frozen=True)
class TurnContext:
    """
    Snapshot of the current turn, used as argument for
    ConversationGovernanceState.should_full_refresh().

    Fields are populated by the controller BEFORE deciding whether to activate
    the conversational fast path or route to deliberation.

    Reference: MORALSTACK_MULTITURN_DESIGN.md v1.3 §2.3.
    """

    current_domain: str | None = None
    current_developer_contract_hash: str | None = None
    current_hard_signals_present: bool = False
    current_risk_posture: str = "NORMAL"  # "NORMAL" | "ELEVATED" | "ESCALATED"


@dataclass(frozen=True)
class RefreshDecision:
    """
    Extended result for should_full_refresh() for future API evolution.

    In Step 1 this is not used directly (should_full_refresh returns bool).
    It exists now to support Step 6, where the API may evolve to return
    RefreshDecision instead of bool, while preserving compatibility via
    __bool__ coercion.

    Reference: MORALSTACK_MULTITURN_DESIGN.md v1.3 §2.3 (c).
    """

    should_refresh: bool
    reason_codes: tuple[str, ...] = field(default_factory=tuple)

    def __bool__(self) -> bool:
        """Enables `if state.should_full_refresh(...):` even if return type evolves."""
        return self.should_refresh


@dataclass(frozen=True)
class TurnDecisionSummary:
    """
    Summary of a turn decision, stored in ConversationGovernanceState.turn_decisions_summary.

    Used by the conversational fast-path runner (Step 7) to detect escalation
    patterns (for example, SAFE_COMPLETE in the last three turns).

    Reference: MORALSTACK_MULTITURN_DESIGN.md v1.3 §2.3 (a).
    """

    turn_index: int
    final_action: str  # "NORMAL_COMPLETE" | "SAFE_COMPLETE" | "REFUSE"
    risk_score: float
    winning_rule: str = ""
    was_cached: bool = False


@dataclass(frozen=True)
class ConversationGovernanceState:
    """
    Minimal governance snapshot that may be carried across turns in a future
    conversational runtime. Immutable; use replace() or helpers for updates.
    """

    conversation_id: str | None = None
    turn_index: int | None = None
    active_domain: str | None = None
    active_overlay: str | None = None
    detected_language: str | None = None
    last_risk_posture_summary: str | None = None
    principle_shortlist: tuple[str, ...] = field(default_factory=tuple)
    last_hard_constraints_triggered: tuple[str, ...] = field(default_factory=tuple)
    conversation_safety_summary: str | None = None
    last_request_id: str | None = None
    # Future: explicit flags for "full refresh required" vs "reuse eligible" (no default behavior yet)
    full_refresh_required_hint: bool | None = None
    # --- NEW v0.4 (additive fields) ---
    last_developer_contract_hash: str | None = None
    last_governance_posture: str = "NORMAL"  # "NORMAL" | "ELEVATED" | "ESCALATED"
    turn_decisions_summary: tuple[TurnDecisionSummary, ...] = field(default_factory=tuple)

    def should_full_refresh(
        self,
        *,
        current_turn: TurnContext | None = None,
    ) -> bool:
        """
        Decide whether to recompute the full pipeline (no cache, no fast-path)
        for the current turn.

        Backward compatibility:
            When invoked WITHOUT arguments (legacy), always returns True
            (conservative behavior). This preserves tests/test_conversation_readiness.py:18
            (`assert s.should_full_refresh() is True`).

        New logic (Step 6 wires `should_full_refresh(current_turn=...)`):
            Returns True if at least one of the following is true:
            - full_refresh_required_hint == True (explicit)
            - current_turn.current_hard_signals_present == True
            - current_turn.current_domain != self.active_domain (domain changed)
            - current_turn.current_developer_contract_hash != self.last_developer_contract_hash
              (contract changed)
            - self.last_governance_posture == "ESCALATED"
            - len(self.last_hard_constraints_triggered) > 0
            Otherwise returns False (cache/fast-path eligible).

        Args:
            current_turn: snapshot of current turn. If None (legacy), conservative behavior.

        Returns:
            True when full refresh is required, False when cache/fast-path is eligible.
        """
        # Legacy path (no arguments): byte-identical compatibility.
        if current_turn is None:
            if self.full_refresh_required_hint is True:
                return True
            return True

        # New path (Step 6 will call with current_turn).
        if self.full_refresh_required_hint is True:
            return True
        if current_turn.current_hard_signals_present:
            return True
        if current_turn.current_domain != self.active_domain:
            return True
        if current_turn.current_developer_contract_hash != self.last_developer_contract_hash:
            return True
        if self.last_governance_posture == "ESCALATED":
            return True
        if len(self.last_hard_constraints_triggered) > 0:
            return True
        return False

    def with_last_request_id(self, request_id: str) -> ConversationGovernanceState:
        """Return a copy with last_request_id set (explicit carry-forward anchor)."""
        rid = (request_id or "").strip()
        if not rid:
            return self
        return replace(self, last_request_id=rid)

    def with_developer_contract_hash(self, contract_hash: str | None) -> ConversationGovernanceState:
        """Return a copy with last_developer_contract_hash set."""
        return replace(self, last_developer_contract_hash=contract_hash)

    def with_turn_metadata(
        self,
        *,
        conversation_id: str | None = None,
        turn_index: int | None = None,
    ) -> ConversationGovernanceState:
        """Return a copy with conversation identifiers updated."""
        return replace(
            self,
            conversation_id=conversation_id if conversation_id is not None else self.conversation_id,
            turn_index=turn_index if turn_index is not None else self.turn_index,
        )

    def update_from_processing_result(
        self,
        *,
        request_id: str,
        domain: str | None = None,
        overlay: str | None = None,
        detected_language: str | None = None,
        risk_posture_summary: str | None = None,
        principle_shortlist: tuple[str, ...] | None = None,
        hard_constraints_triggered: tuple[str, ...] | None = None,
    ) -> ConversationGovernanceState:
        """
        Prepare a new state after a single request completes (no routing impact).
        Does not change deliberation or decide_action; audit-only carry-forward.
        """
        return replace(
            self,
            last_request_id=(request_id or "").strip() or self.last_request_id,
            active_domain=domain if domain is not None else self.active_domain,
            active_overlay=overlay if overlay is not None else self.active_overlay,
            detected_language=detected_language if detected_language is not None else self.detected_language,
            last_risk_posture_summary=(
                risk_posture_summary if risk_posture_summary is not None else self.last_risk_posture_summary
            ),
            principle_shortlist=principle_shortlist if principle_shortlist is not None else self.principle_shortlist,
            last_hard_constraints_triggered=(
                hard_constraints_triggered
                if hard_constraints_triggered is not None
                else self.last_hard_constraints_triggered
            ),
        )

    def to_summary_dict(self) -> dict[str, Any]:
        """JSON-friendly summary for traces and reports."""
        return {
            "conversation_id": self.conversation_id,
            "turn_index": self.turn_index,
            "active_domain": self.active_domain,
            "active_overlay": self.active_overlay,
            "detected_language": self.detected_language,
            "last_risk_posture_summary": self.last_risk_posture_summary,
            "principle_shortlist": list(self.principle_shortlist),
            "last_hard_constraints_triggered": list(self.last_hard_constraints_triggered),
            "conversation_safety_summary": self.conversation_safety_summary,
            "last_request_id": self.last_request_id,
            "full_refresh_required_hint": self.full_refresh_required_hint,
            # --- NEW v0.4 ---
            "last_developer_contract_hash": self.last_developer_contract_hash,
            "last_governance_posture": self.last_governance_posture,
            "turn_decisions_summary": [
                {
                    "turn_index": t.turn_index,
                    "final_action": t.final_action,
                    "risk_score": t.risk_score,
                    "winning_rule": t.winning_rule,
                    "was_cached": t.was_cached,
                }
                for t in self.turn_decisions_summary
            ],
        }
