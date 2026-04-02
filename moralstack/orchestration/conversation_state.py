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

    def should_full_refresh(self) -> bool:
        """
        Placeholder: conservative default for future delta-refresh logic.
        When multi-turn reuse is implemented, this will encode real rules.
        """
        if self.full_refresh_required_hint is True:
            return True
        return True

    def with_last_request_id(self, request_id: str) -> ConversationGovernanceState:
        """Return a copy with last_request_id set (explicit carry-forward anchor)."""
        rid = (request_id or "").strip()
        if not rid:
            return self
        return replace(self, last_request_id=rid)

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
            last_risk_posture_summary=risk_posture_summary
            if risk_posture_summary is not None
            else self.last_risk_posture_summary,
            principle_shortlist=principle_shortlist if principle_shortlist is not None else self.principle_shortlist,
            last_hard_constraints_triggered=hard_constraints_triggered
            if hard_constraints_triggered is not None
            else self.last_hard_constraints_triggered,
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
        }
