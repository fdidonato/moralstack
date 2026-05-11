"""
Session state for a GovernedClient instance.

Step 1-B (Phase 1): passive tracking (conversation_id, turn counter, ConversationGovernanceState).
Step 5 (v0.4): SessionState now wraps a SessionStore internally. The default
single-conversation use case is preserved byte-identically; multi-conversation
use cases (e.g. server proxy in Step 11) can inject an external SessionStore.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from moralstack.orchestration.conversation_state import ConversationGovernanceState
from moralstack.sdk.session_store import InMemorySessionStore

if TYPE_CHECKING:
    from moralstack.orchestration.types import OrchestratorResult
    from moralstack.sdk.config import GovernanceConfig
    from moralstack.sdk.session_store import SessionStoreProtocol


class SessionState:
    """
    Container for the conversational state of a GovernedClient.

    Internally, SessionState wraps a SessionStore. The default constructor builds
    a private InMemorySessionStore, so single-conversation usage is unaffected.
    Multi-conversation scenarios (e.g. server proxy) can pass an external store
    via the `store=` argument.

    Not thread-safe by design: one GovernedClient maps to a single
    thread/coroutine. Server proxy adds locking at the request layer.

    Usage::

        # Single-conversation (default)
        session = SessionState(config)
        conv_id = session.conversation_id
        turn_idx = session.next_turn_index()
        # ... after the call ...
        session.update_from_result(result)
        # For a new conversation:
        session.reset()

        # Multi-conversation (server proxy)
        store = InMemorySessionStore()
        session = SessionState(config, store=store)
        # The store is shared across many SessionState views, each scoped to
        # its own conversation_id.
    """

    def __init__(
        self,
        config: "GovernanceConfig",
        *,
        store: "SessionStoreProtocol | None" = None,
    ) -> None:
        self._config = config
        self._store: "SessionStoreProtocol" = store if store is not None else InMemorySessionStore()
        self._conversation_id: str = str(uuid.uuid4()) if config.enable_session_tracking else ""
        self._turn_counter: int = 0

    @property
    def conversation_id(self) -> str | None:
        """Unique identifier for the current conversation. None if tracking is disabled."""
        return self._conversation_id or None

    @property
    def current_state(self) -> ConversationGovernanceState | None:
        """Governance state from the last completed turn. None on the first turn or when tracking is disabled."""
        if not self._conversation_id:
            return None
        return self._store.get(self._conversation_id)

    def next_turn_index(self) -> int:
        """
        Return the current turn index and increment it.
        Must be called once per create() call.
        """
        idx = self._turn_counter
        self._turn_counter += 1
        return idx

    def update_from_result(self, result: "OrchestratorResult") -> None:
        """
        Update governance state after a completed turn.
        Reads conversation_governance_state_out from the OrchestratorResult and
        persists it in the store under the current conversation_id.
        No-op when tracking is disabled or when the result has no governance state.
        """
        if not self._conversation_id:
            return
        new_state = getattr(result, "conversation_governance_state_out", None)
        if isinstance(new_state, ConversationGovernanceState):
            self._store.put(self._conversation_id, new_state)

    def reset(self) -> None:
        """
        Start a new conversation: generate a new conversation_id, reset counter,
        and drop any state associated with the previous conversation_id.
        """
        if self._conversation_id:
            self._store.delete(self._conversation_id)
        self._conversation_id = str(uuid.uuid4()) if self._config.enable_session_tracking else ""
        self._turn_counter = 0
