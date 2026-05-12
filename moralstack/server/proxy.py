"""
MoralStack server proxy: FastAPI app exposing OpenAI-compatible endpoints
governed by MoralStack.

Per design v1.3 §4.2, the proxy is a thin HTTP wrapper on the already-validated
SDK (GovernedClient). It receives OpenAI-style requests, applies governance,
then forwards to the upstream OpenAI client (for NORMAL_COMPLETE / SAFE_COMPLETE)
or returns a synthetic ChatCompletion (for REFUSE).

Concurrency: two concurrent calls with the same conversation_id are serialized
via per-conversation locks (design v1.3 §4.4).
"""

from __future__ import annotations

import logging
import threading
import time
import uuid
from typing import Any

from fastapi import FastAPI, Header, HTTPException, Request, Response
from fastapi.responses import JSONResponse

from moralstack.orchestration.controller import OrchestrationController
from moralstack.orchestration.types import ProcessedRequest
from moralstack.sdk.config import GovernanceConfig
from moralstack.sdk.session_store import InMemorySessionStore, SessionStoreProtocol
from moralstack.sdk.wrapper import (
    _build_safe_complete_user_turn,
    _extract_developer_contract,
    _extract_last_user_message,
    _messages_to_turns,
)
from moralstack.server.fingerprint import compute_conversation_fingerprint
from moralstack.server.headers import build_governance_headers

logger = logging.getLogger("moralstack.server.proxy")


# Per-conversation lock acquisition timeout (design v1.3 §4.4).
_LOCK_ACQUIRE_TIMEOUT_S = 30.0


class ConversationLockManager:
    """
    Per-conversation lock manager for serializing concurrent requests on the
    same conversation_id (design v1.3 §4.4).

    The manager itself is thread-safe. It hands out per-conversation locks
    keyed by conversation_id. Calls with no conversation_id (single-turn,
    empty fingerprint) get a no-op pass-through lock — single-turn requests
    are independent and don't need serialization.
    """

    def __init__(self) -> None:
        self._locks: dict[str, threading.Lock] = {}
        self._meta_lock = threading.Lock()

    def acquire(self, conversation_id: str, timeout: float = _LOCK_ACQUIRE_TIMEOUT_S) -> threading.Lock | None:
        """
        Acquire the lock for the given conversation_id.

        Returns the acquired lock so the caller can release it. Returns None
        when conversation_id is empty (no serialization needed) or the lock
        cannot be acquired within `timeout`.
        """
        if not conversation_id:
            return None
        with self._meta_lock:
            if conversation_id not in self._locks:
                self._locks[conversation_id] = threading.Lock()
            lock = self._locks[conversation_id]
        acquired = lock.acquire(timeout=timeout)
        if not acquired:
            logger.warning(
                "ConversationLockManager: timeout acquiring lock for conversation_id=%s "
                "after %.1fs (proceeding with stale state)",
                conversation_id,
                timeout,
            )
            return None
        return lock

    def release(self, lock: threading.Lock | None) -> None:
        if lock is not None:
            try:
                lock.release()
            except RuntimeError:
                # Lock not held — should not happen but defensive.
                pass


def _resolve_conversation_id(
    messages: list[dict[str, Any]],
    extra_body: dict[str, Any] | None,
) -> str:
    """
    Resolve the conversation_id from extra_body or via fingerprint.

    Per design v1.3 §4.3: client-provided id wins; otherwise, deterministic
    fingerprint of the message prefix.
    """
    if extra_body:
        explicit = extra_body.get("moralstack_conversation_id")
        if explicit:
            return str(explicit)
    return compute_conversation_fingerprint(messages)


def _build_synthetic_chat_completion(
    content: str,
    *,
    model: str,
    finish_reason: str = "stop",
) -> dict[str, Any]:
    """
    Build a synthetic ChatCompletion JSON for REFUSE responses.

    The shape matches openai.types.ChatCompletion so OpenAI-SDK clients can
    parse it without changes.
    """
    return {
        "id": f"chatcmpl-msrefuse-{uuid.uuid4().hex[:16]}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": content,
                },
                "finish_reason": finish_reason,
            }
        ],
        "usage": {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
        },
    }


def create_app(
    *,
    openai_client: Any,
    orchestrator: OrchestrationController,
    config: GovernanceConfig | None = None,
    session_store: SessionStoreProtocol | None = None,
) -> FastAPI:
    """
    Build a FastAPI app instance.

    Args:
        openai_client: an instance of the upstream OpenAI client (or any
            duck-typed equivalent exposing `.chat.completions.create(...)`).
        orchestrator: a configured OrchestrationController.
        config: optional GovernanceConfig. Defaults to GovernanceConfig().
        session_store: optional SessionStoreProtocol (defaults to InMemorySessionStore).

    Returns:
        A FastAPI app ready to be served by uvicorn.
    """
    cfg = config or GovernanceConfig()
    store: SessionStoreProtocol = session_store if session_store is not None else InMemorySessionStore()
    lock_manager = ConversationLockManager()
    turn_counter = TurnCounter()

    app = FastAPI(
        title="MoralStack Server Proxy",
        description="OpenAI-compatible governance proxy. See https://github.com/fdidonato/moralstack",
        version="0.4.0",
    )

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        """Liveness probe."""
        return {"status": "ok"}

    @app.post("/v1/chat/completions")
    async def chat_completions(
        request: Request,
        moralstack_conversation_id: str | None = Header(default=None, alias="X-Moralstack-Conversation-Id"),
    ) -> Response:
        """
        OpenAI-compatible chat completions endpoint with MoralStack governance.

        Per design v1.3 section 4.2:
        1. Resolve conversation_id (header / extra_body / fingerprint).
        2. Acquire conversation lock (section 4.4).
        3. Build ProcessedRequest from messages.
        4. Call controller.process().
        5. Route: REFUSE -> synthetic completion; SAFE_COMPLETE -> forward with
           appended synthetic user turn; NORMAL_COMPLETE -> forward original.
        6. Attach X-Moralstack-* headers.
        """
        # Parse body
        try:
            body = await request.json()
        except Exception:
            raise HTTPException(status_code=400, detail="Invalid JSON body")

        messages = body.get("messages")
        if not isinstance(messages, list):
            raise HTTPException(status_code=400, detail="`messages` must be a list")
        if not messages:
            raise HTTPException(status_code=400, detail="`messages` must not be empty")

        model = str(body.get("model", "") or "gpt-4o")
        extra_body = body.get("extra_body") if isinstance(body.get("extra_body"), dict) else None

        # Resolve conversation_id (header > extra_body > fingerprint)
        conversation_id = moralstack_conversation_id or _resolve_conversation_id(messages, extra_body)

        lock = lock_manager.acquire(conversation_id)
        try:
            # Extract governance context from messages
            developer_contract = _extract_developer_contract(messages)
            conversation_history = _messages_to_turns(messages[:-1]) if len(messages) > 1 else []
            user_prompt = _extract_last_user_message(messages)

            processed = ProcessedRequest(
                prompt=user_prompt,
                developer_contract=developer_contract,
                conversation_history=conversation_history,
            )

            # Resolve turn_index + conversation state
            turn_index = turn_counter.next_index(conversation_id)
            conv_state = store.get(conversation_id) if conversation_id else None

            # Run governance pipeline
            try:
                result = orchestrator.process(
                    processed,
                    conversation_id=conversation_id or None,
                    turn_index=turn_index,
                    parent_request_id=processed.request_id,
                    conversation_state=conv_state,
                )
            except Exception as exc:
                logger.exception("Pipeline failure: %s", exc)
                if cfg.failure_policy == "passthrough":
                    try:
                        upstream_response = openai_client.chat.completions.create(**_build_upstream_kwargs(body))
                        return _serialize_upstream_response(
                            upstream_response, headers={"X-Moralstack-Decision": "PASSTHROUGH_ON_ERROR"}
                        )
                    except Exception as upstream_exc:
                        raise HTTPException(status_code=502, detail=f"Upstream failure: {upstream_exc}")
                raise HTTPException(status_code=500, detail=f"Pipeline failure: {exc}")

            # Persist conversation_governance_state_out for next turn.
            # Note: mypy requires a local variable for type narrowing — the inline
            # `getattr(...) is not None` check does NOT narrow the type of the attribute.
            governance_state_out = getattr(result, "conversation_governance_state_out", None)
            if conversation_id and governance_state_out is not None:
                store.put(conversation_id, governance_state_out)

            final_action = result.response.metadata.final_action
            governance_headers = build_governance_headers(result, conversation_id=conversation_id)

            # Routing per design v1.3 section 4.2
            if final_action == "REFUSE":
                payload = _build_synthetic_chat_completion(
                    content=result.response.content or "I cannot help with that request.",
                    model=model,
                    finish_reason="content_filter",
                )
                return JSONResponse(content=payload, headers=governance_headers)

            if final_action == "SAFE_COMPLETE":
                safe_turn = _build_safe_complete_user_turn(result)
                upstream_kwargs = _build_upstream_kwargs(body)
                upstream_kwargs["messages"] = list(upstream_kwargs.get("messages", [])) + [safe_turn]
                try:
                    upstream_response = openai_client.chat.completions.create(**upstream_kwargs)
                except Exception as exc:
                    logger.exception("Upstream call failed: %s", exc)
                    raise HTTPException(status_code=502, detail=f"Upstream call failed: {exc}")
                return _serialize_upstream_response(upstream_response, headers=governance_headers)

            # NORMAL_COMPLETE (default)
            upstream_kwargs = _build_upstream_kwargs(body)
            try:
                upstream_response = openai_client.chat.completions.create(**upstream_kwargs)
            except Exception as exc:
                logger.exception("Upstream call failed: %s", exc)
                raise HTTPException(status_code=502, detail=f"Upstream call failed: {exc}")
            return _serialize_upstream_response(upstream_response, headers=governance_headers)

        finally:
            lock_manager.release(lock)

    return app


# ─── Helpers for FastAPI handler (placed at module level for testability) ───


class TurnCounter:
    """Per-conversation turn counter, thread-safe."""

    def __init__(self) -> None:
        self._counters: dict[str, int] = {}
        self._lock = threading.Lock()

    def next_index(self, conversation_id: str) -> int:
        if not conversation_id:
            return 0
        with self._lock:
            idx = self._counters.get(conversation_id, 0)
            self._counters[conversation_id] = idx + 1
            return idx


def _build_upstream_kwargs(body: dict[str, Any]) -> dict[str, Any]:
    """Strip MoralStack-specific fields from the body before passing to OpenAI."""
    kwargs = dict(body)
    kwargs.pop("extra_body", None)
    return kwargs


def _serialize_upstream_response(upstream_response: Any, *, headers: dict[str, str]) -> Response:
    """
    Serialize an upstream OpenAI response object into a JSONResponse.

    The OpenAI SDK returns Pydantic models for ChatCompletion. We use
    model_dump() when available, otherwise fallback to dict() or the raw value.
    """
    if hasattr(upstream_response, "model_dump"):
        payload = upstream_response.model_dump()
    elif hasattr(upstream_response, "to_dict"):
        payload = upstream_response.to_dict()
    elif isinstance(upstream_response, dict):
        payload = upstream_response
    else:
        # Last-resort string serialization
        payload = {"raw": str(upstream_response)}
    return JSONResponse(content=payload, headers=headers)


def main() -> None:
    """CLI entry point: `moralstack-server`. Starts uvicorn with a stub orchestrator."""
    raise NotImplementedError(
        "The `moralstack-server` CLI requires explicit injection of openai_client + orchestrator. "
        "Use `from moralstack.server import create_app` in your own launcher module instead. "
        "See examples/server_quickstart.py (Step 12)."
    )
