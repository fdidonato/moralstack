"""
Session state for a single GovernedClient instance.

Phase 1-B: passive tracking (conversation_id, turn counter, ConversationGovernanceState).
Phase 2.1: will be extended with SessionEngine, DecisionLedger, conversational fast-path.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from moralstack.orchestration.conversation_state import ConversationGovernanceState

if TYPE_CHECKING:
    from moralstack.orchestration.types import OrchestratorResult
    from moralstack.sdk.config import GovernanceConfig


class SessionState:
    """
    Container for conversational state of a GovernedClient.

    Not thread-safe by design: one GovernedClient maps to a single
    thread/coroutine. Document if concurrent use is required.

    Usage::

        session = SessionState(config)
        conv_id = session.conversation_id
        turn_idx = session.next_turn_index()
        # ... after the call ...
        session.update_from_result(result)
        # For a new conversation:
        session.reset()
    """

    def __init__(self, config: GovernanceConfig) -> None:
        self._config = config
        self._conversation_id: str = str(uuid.uuid4()) if config.enable_session_tracking else ""
        self._turn_counter: int = 0
        self._governance_state: ConversationGovernanceState | None = None

    @property
    def conversation_id(self) -> str | None:
        """Unique identifier for the current conversation. None if tracking is disabled."""
        return self._conversation_id or None

    @property
    def current_state(self) -> ConversationGovernanceState | None:
        """Governance state from the last completed turn. None on the first turn."""
        return self._governance_state

    def next_turn_index(self) -> int:
        """
        Return the current turn index and increment it.
        Must be called once per create() call.
        """
        idx = self._turn_counter
        self._turn_counter += 1
        return idx

    def update_from_result(self, result: OrchestratorResult) -> None:
        """
        Update governance state after a completed turn.
        Expects conversation_governance_state_out on OrchestratorResult.
        """
        new_state = getattr(result, "conversation_governance_state_out", None)
        if isinstance(new_state, ConversationGovernanceState):
            self._governance_state = new_state

    def reset(self) -> None:
        """
        Start a new conversation: generate a new conversation_id and
        reset counter and state.
        """
        self._conversation_id = str(uuid.uuid4()) if self._config.enable_session_tracking else ""
        self._turn_counter = 0
        self._governance_state = None
