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

from moralstack.observability.conversation_events import (
    emit_proxy_request_finalized,
)
from moralstack.observability.governance_audit import (
    finalize_governance_audit,
)
from moralstack.observability.governance_audit import (
    posture_of as _posture_of,
)
from moralstack.observability.governance_audit import (
    state_summary_or_none as _state_summary_or_none,
)
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
    # Initialize observability for the proxy lifetime so all governance events
    # are persisted to DB / JSONL per MORALSTACK_OBSERVABILITY_* env vars.
    # Pattern parallels cli/shell.py: init_db + create_run + set_current_run_id.
    # Each HTTP request then sets its own request_id in the context var.
    proxy_run_id = _initialize_observability_run()
    if proxy_run_id:
        logger.info("MoralStack proxy observability run initialized: run_id=%s", proxy_run_id)
    else:
        logger.info(
            "MoralStack proxy observability not configured "
            "(set MORALSTACK_OBSERVABILITY_DB_PATH or MORALSTACK_OBSERVABILITY_MODE=file_only to enable persistence)."
        )

    app = FastAPI(
        title="MoralStack Server Proxy",
        description="OpenAI-compatible governance proxy. See https://github.com/fdidonato/moralstack",
        version="0.5.0",
    )

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        """Liveness probe."""
        return {"status": "ok", "run_id": proxy_run_id or ""}

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
        # State accumulated during request processing for the finalize step.
        request_id_for_audit: str = ""
        final_response_text: str = ""
        domain_for_audit: str | None = None
        result_for_audit: Any | None = None
        governance_headers_for_audit: dict[str, str] | None = None
        state_in_for_audit: Any | None = None
        state_out_for_audit: Any | None = None
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
            request_id_for_audit = processed.request_id

            # Resolve turn_index from payload (stateless: count user messages).
            # Persists correctly across server restarts and stateless HTTP clients.
            turn_index = _resolve_turn_index(messages)
            conv_state = store.get(conversation_id) if conversation_id else None
            state_in_for_audit = conv_state
            # Bind the request_id to the observability context so all events
            # emitted by the pipeline (LLM calls, decision traces, orchestration
            # events) are correctly attributed to this request. Without this,
            # persistence is silently skipped (run_id+request_id are required).
            try:
                from moralstack.observability.context import set_current_request_id

                set_current_request_id(processed.request_id)
            except Exception:
                # Context binding is best-effort; persistence will no-op if it
                # fails but governance still proceeds normally.
                pass

            # Pre-insert the requests row BEFORE the pipeline emits any
            # orchestration_event / llm_call / decision_trace events. Those
            # event tables have FK constraints on requests(run_id, request_id),
            # so without this pre-insert the SQLite sink would reject events
            # with "FOREIGN KEY constraint failed". The pipeline itself does
            # NOT call upsert_request for proxy-originated requests (only the
            # CLI does, via DefaultPersistence).
            _ensure_request_row(
                proxy_run_id=proxy_run_id,
                request_id=processed.request_id,
                prompt=user_prompt,
                conversation_id=conversation_id or None,
                turn_index=turn_index,
            )

            # Run governance pipeline
            try:
                result = orchestrator.process(
                    processed,
                    conversation_id=conversation_id or None,
                    turn_index=turn_index,
                    parent_request_id=processed.request_id,
                    conversation_state=conv_state,
                )
                result_for_audit = result
            except Exception as exc:
                logger.exception("Pipeline failure: %s", exc)
                if cfg.failure_policy == "passthrough":
                    try:
                        upstream_response = openai_client.chat.completions.create(**_build_upstream_kwargs(body))
                        final_response_text = _extract_text_from_upstream(upstream_response)
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
            state_out_for_audit = governance_state_out
            if conversation_id and governance_state_out is not None:
                store.put(conversation_id, governance_state_out)

            final_action = result.response.metadata.final_action
            governance_headers = build_governance_headers(result, conversation_id=conversation_id)
            governance_headers_for_audit = dict(governance_headers) if governance_headers else None
            domain_for_audit = getattr(result.response.metadata, "domain_overlay", None)

            # Routing per design v1.3 section 4.2
            if final_action == "REFUSE":
                refusal_content = result.response.content or "I cannot help with that request."
                final_response_text = refusal_content
                payload = _build_synthetic_chat_completion(
                    content=refusal_content,
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
                final_response_text = _extract_text_from_upstream(upstream_response)
                return _serialize_upstream_response(upstream_response, headers=governance_headers)

            # NORMAL_COMPLETE (default)
            upstream_kwargs = _build_upstream_kwargs(body)
            try:
                upstream_response = openai_client.chat.completions.create(**upstream_kwargs)
            except Exception as exc:
                logger.exception("Upstream call failed: %s", exc)
                raise HTTPException(status_code=502, detail=f"Upstream call failed: {exc}")
            final_response_text = _extract_text_from_upstream(upstream_response)
            return _serialize_upstream_response(upstream_response, headers=governance_headers)

        finally:
            lock_manager.release(lock)
            # Finalize observability: update final_response column, populate
            # meta_json with governance metadata, emit proxy.request_finalized,
            # and flush async queue so data is visible immediately after the
            # response.
            _finalize_request(
                proxy_run_id=proxy_run_id,
                request_id=request_id_for_audit,
                final_response_text=final_response_text,
                domain=domain_for_audit,
                conversation_id=conversation_id or None,
                turn_index=_resolve_turn_index(messages) if isinstance(messages, list) else None,
                result=result_for_audit,
                governance_headers=governance_headers_for_audit,
                state_in=state_in_for_audit,
                state_out=state_out_for_audit,
            )

    return app


# ─── Helpers for FastAPI handler (placed at module level for testability) ───


def _resolve_turn_index(messages: list[dict[str, Any]]) -> int:
    """
    Derive the turn index from the messages payload (stateless).

    Per OpenAI Chat Completions API, clients re-send the full conversation
    history on every request. The turn index is therefore the count of
    user messages minus 1 (zero-indexed): turn 0 is the first request with
    one user message, turn 1 has two user messages (one assistant in between),
    etc.

    This is the correct multi-turn pattern for stateless HTTP proxies. A
    server-side counter would diverge from the client's view on restart or
    when multiple clients share a conversation_id.
    """
    user_count = sum(1 for m in messages if (m.get("role") or "") == "user")
    return max(0, user_count - 1)


def _initialize_observability_run() -> str:
    """
    Initialize the observability stack for the proxy lifetime.

    Pattern parallels cli/shell.py: when an observability DB path is configured,
    initialize the schema, create a single `runs` row of type "proxy", and set
    the run_id in the observability context var. All subsequent governance
    events (LLM calls, decision traces, orchestration events) emitted by the
    pipeline will then be persisted under this run_id.

    Returns the run_id string when observability is configured, or "" when
    persistence is disabled (no DB path set and not in file_only mode).
    Never raises: persistence is best-effort.
    """
    try:
        from moralstack.observability.config import get_db_path, get_observability_mode
        from moralstack.observability.context import set_current_run_id
        from moralstack.observability.sinks.sqlite_sink import create_run, init_db

        mode = get_observability_mode()
        db_path = get_db_path()
        # In file_only mode there is no DB to initialize but we still set a run_id
        # so JSONL envelopes carry a stable identifier across proxy requests.
        run_id = str(uuid.uuid4())
        if db_path and mode in ("db_only", "dual"):
            init_db(db_path)
            create_run(run_id=run_id, run_type="proxy", meta={"source": "moralstack-server"})
        elif mode == "file_only":
            # JSONL-only: nothing to init in DB.
            pass
        else:
            # No persistence configured.
            return ""
        set_current_run_id(run_id)
        return run_id
    except Exception as exc:
        logger.warning("Failed to initialize observability run for proxy: %s", exc)
        return ""


def _ensure_request_row(
    *,
    proxy_run_id: str,
    request_id: str,
    prompt: str,
    conversation_id: str | None,
    turn_index: int,
) -> None:
    """
    Pre-insert the requests row before the pipeline runs.

    The `orchestration_events`, `llm_calls`, and `decision_traces` tables in
    SQLite have FK constraints pointing at `requests(run_id, request_id)`.
    The pipeline emits events DURING `controller.process()`, so the requests
    row must exist BEFORE the call to avoid FK failures.

    The CLI relies on `DefaultPersistence.ensure_run_and_upsert_request()`
    called inside the persistence layer; the proxy bypasses that path, so we
    upsert the row explicitly here.

    Best-effort: never raises. Skipped silently when observability is not
    configured (proxy_run_id == "").
    """
    if not proxy_run_id or not request_id:
        return
    try:
        from moralstack.observability.sinks.sqlite_sink import upsert_request

        upsert_request(
            run_id=proxy_run_id,
            request_id=request_id,
            prompt=prompt or "",
            domain=None,  # Domain will be filled later by update_request_domain.
            conversation_id=conversation_id,
            turn_index=turn_index,
        )
    except Exception as exc:
        logger.debug("upsert_request (pre-pipeline) failed (non-fatal): %s", exc)


def _finalize_request(
    *,
    proxy_run_id: str,
    request_id: str,
    final_response_text: str,
    domain: str | None = None,
    conversation_id: str | None = None,
    turn_index: int | None = None,
    result: Any | None = None,
    governance_headers: dict[str, str] | None = None,
    state_in: Any | None = None,
    state_out: Any | None = None,
) -> None:
    """
    Finalize an HTTP request: update the requests row with final_response and
    flush the observability queue so writes land before the response is sent.

    Step 13 extension:
        - Build governance metadata from ``result.response.metadata`` and emit
          ``request.meta_updated`` so SQLite + JSONL receive the full payload.
        - Emit ``proxy.request_finalized`` with state in/out, posture in/out,
          cache hints, X-MoralStack headers and response length.

    Best-effort: never raises. Skipped silently when observability is not
    configured (proxy_run_id == "").
    """
    if not proxy_run_id or not request_id:
        return
    try:
        from moralstack.observability import obs

        # Step 13 — shared finalization: writes final_response + domain on the
        # ``requests`` row, builds the governance meta dict, and emits the
        # canonical ``request.meta_updated`` envelope so SQLite + JSONL stay
        # consistent across proxy- and SDK-driven runs.
        meta = finalize_governance_audit(
            run_id=proxy_run_id,
            request_id=request_id,
            result=result,
            final_response_text=final_response_text or "",
            conversation_id=conversation_id,
            turn_index=turn_index,
            domain=domain,
        )

        # Step 13 — emit canonical proxy.request_finalized envelope.
        try:
            posture_in = _posture_of(state_in)
            posture_out = _posture_of(state_out)
            response_len: int | None
            try:
                response_len = len(final_response_text or "")
            except Exception:
                response_len = None
            emit_proxy_request_finalized(
                run_id=proxy_run_id,
                request_id=request_id,
                conversation_id=conversation_id,
                turn_index=turn_index,
                final_action=(meta.get("final_action") if isinstance(meta, dict) else None),
                risk_score=(meta.get("risk_score") if isinstance(meta, dict) else None),
                path=(meta.get("path_taken") or meta.get("path") if isinstance(meta, dict) else None),
                domain=domain,
                posture_in=posture_in,
                posture_out=posture_out,
                state_provided=state_in is not None,
                state_updated=state_out is not None,
                was_cached=(meta.get("was_cached") if isinstance(meta, dict) else None),
                cached_from_turn=(meta.get("cached_from_turn") if isinstance(meta, dict) else None),
                final_response_length=response_len,
                headers=dict(governance_headers) if governance_headers else None,
                metadata=meta if meta else None,
                state_in=_state_summary_or_none(state_in),
                state_out=_state_summary_or_none(state_out),
            )
        except Exception as exc:
            logger.debug("emit_proxy_request_finalized failed (non-fatal): %s", exc)

        # Flush the async queue so the data is visible to readers right away.
        obs.flush(timeout=5.0)
    except Exception as exc:
        logger.warning("Failed to finalize request observability: %s", exc)


def _extract_text_from_upstream(upstream_response: Any) -> str:
    """
    Best-effort extraction of the assistant text content from an upstream
    ChatCompletion response. Used for audit logging only.
    """
    try:
        if hasattr(upstream_response, "model_dump"):
            payload = upstream_response.model_dump()
        elif hasattr(upstream_response, "to_dict"):
            payload = upstream_response.to_dict()
        elif isinstance(upstream_response, dict):
            payload = upstream_response
        else:
            return ""
        choices = payload.get("choices") or []
        if not choices:
            return ""
        msg = choices[0].get("message") or {}
        return str(msg.get("content") or "")
    except Exception:
        return ""


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
