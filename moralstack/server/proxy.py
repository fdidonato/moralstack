"""
MoralStack server proxy: FastAPI app exposing OpenAI-compatible endpoints
governed by MoralStack.

Per design v1.3 §4.2, the proxy is a thin HTTP wrapper on the already-validated
SDK (GovernedClient). It receives OpenAI-style requests, applies governance,
then forwards to the upstream OpenAI client (for NORMAL_COMPLETE / SAFE_COMPLETE)
or returns a synthetic ChatCompletion (for REFUSE).

Concurrency: two concurrent calls with the same conversation_id are serialized
via per-conversation locks (design v1.3 §4.4). Blocking orchestrator and upstream
OpenAI SDK calls run in a thread pool so the ASGI event loop stays responsive
under parallel COMPL-AI-style samples (single uvicorn worker).
"""

from __future__ import annotations

import json
import logging
import threading
import time
import uuid
from typing import Any, Iterator

from fastapi import FastAPI, Header, HTTPException, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse
from starlette.concurrency import run_in_threadpool

from moralstack.models.base import GenerationOverrides
from moralstack.observability.conversation_events import finalize_audit_sync
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
from moralstack.orchestration.conversation_context import build_conversation_context, context_to_turns
from moralstack.orchestration.delivery import finalize_delivery
from moralstack.orchestration.final_revalidation import (
    DEFAULT_POST_REVALIDATION_REFUSAL,
)
from moralstack.orchestration.orchestration_event_taxonomy import PROXY_OUTPUT_FINALIZED
from moralstack.orchestration.types import ProcessedRequest
from moralstack.persistence.sink import persist_orchestration_event
from moralstack.sdk.bootstrap import _resolve_model
from moralstack.sdk.config import GovernanceConfig
from moralstack.sdk.session_store import InMemorySessionStore, SessionStoreProtocol
from moralstack.server.conversation_correlation import ConversationCorrelationStore
from moralstack.server.headers import build_governance_headers

logger = logging.getLogger("moralstack.server.proxy")


# Per-conversation lock acquisition timeout (design v1.3 §4.4).
_LOCK_ACQUIRE_TIMEOUT_S = 30.0
_LOCK_RETRY_AFTER_SECONDS = 10


class ConversationLockTimeout(RuntimeError):
    """Raised when a per-conversation lock cannot be acquired within the deadline."""

    def __init__(self, conversation_id: str) -> None:
        self.conversation_id = conversation_id
        super().__init__(conversation_id)


class ConversationLockManager:
    """
    Per-conversation lock manager for serializing concurrent requests on the
    same conversation_id (design v1.3 §4.4).

    The manager itself is thread-safe. It hands out per-conversation locks
    keyed by conversation_id. Calls with no conversation_id (empty string after
    resolution) get a no-op pass-through lock — those requests are independent
    and do not need serialization.
    """

    def __init__(self) -> None:
        self._locks: dict[str, threading.Lock] = {}
        self._meta_lock = threading.Lock()

    def acquire(self, conversation_id: str, timeout: float = _LOCK_ACQUIRE_TIMEOUT_S) -> threading.Lock | None:
        """
        Acquire the lock for the given conversation_id.

        Returns the acquired lock so the caller can release it. Returns None
        when conversation_id is empty (no serialization needed). Raises
        :class:`ConversationLockTimeout` when ``conversation_id`` is non-empty
        and the lock cannot be acquired within ``timeout``.
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
                "ConversationLockManager: timeout acquiring lock for conversation_id=%s after %.1fs",
                conversation_id,
                timeout,
            )
            raise ConversationLockTimeout(conversation_id)
        return lock

    def release(self, lock: threading.Lock | None) -> None:
        if lock is not None:
            try:
                lock.release()
            except RuntimeError:
                # Lock not held — should not happen but defensive.
                pass


def _resolve_conversation_id_from_body_and_correlation(
    messages: list[dict[str, Any]],
    extra_body: dict[str, Any] | None,
    correlation_store: ConversationCorrelationStore,
) -> str:
    """
    Resolve ``conversation_id`` from ``extra_body`` or lineage correlation.

    The HTTP ``X-Moralstack-Conversation-Id`` header is handled in the route
    handler and takes precedence over this function.
    """
    if extra_body:
        explicit = extra_body.get("moralstack_conversation_id")
        if explicit:
            return str(explicit)
    return correlation_store.resolve(messages)


_SENSITIVE_HEADER_MARKERS = (
    "authorization",
    "api-key",
    "cookie",
    "set-cookie",
    "proxy-authorization",
)


def _collect_safe_headers(request: Request) -> dict[str, str]:
    """Return a whitelist of header names/values safe for debug logs (no secrets)."""
    out: dict[str, str] = {}
    for key, value in request.headers.items():
        lk = key.lower()
        if any(marker in lk for marker in _SENSITIVE_HEADER_MARKERS):
            continue
        if not (lk == "user-agent" or lk.startswith("x-") or lk.startswith("openai-")):
            continue
        val = value if len(value) <= 200 else value[:200] + "\u2026"
        out[str(key)] = val
    return out


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


def _iter_word_chunks(text: str) -> list[str]:
    """Split ``text`` into pieces whose concatenation reproduces it byte-for-byte."""
    if not text:
        return []
    import re

    return re.findall(r"\S+|\s+", text)


def _build_synthetic_sse_response(
    content: str,
    *,
    model: str,
    finish_reason: str,
    headers: dict[str, str] | None = None,
) -> StreamingResponse:
    """
    Replay an already-governed final text as an OpenAI-compatible SSE stream.

    Per product decision D2, the proxy never forwards live upstream tokens: it
    runs governance to completion, obtains the full governed answer, and replays
    that exact text as ``chat.completion.chunk`` events. The concatenation of all
    delta contents reproduces the governed text byte-for-byte.
    """
    stream_id = f"chatcmpl-msgov-{uuid.uuid4().hex[:16]}"
    created = int(time.time())

    def _chunk(delta: dict[str, Any], reason: str | None) -> str:
        payload = {
            "id": stream_id,
            "object": "chat.completion.chunk",
            "created": created,
            "model": model,
            "choices": [{"index": 0, "delta": delta, "finish_reason": reason}],
        }
        return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"

    def _event_stream() -> Iterator[str]:
        yield _chunk({"role": "assistant"}, None)
        pieces = _iter_word_chunks(content)
        for piece in pieces:
            yield _chunk({"content": piece}, None)
        yield _chunk({}, finish_reason)
        yield "data: [DONE]\n\n"

    return StreamingResponse(
        _event_stream(),
        media_type="text/event-stream",
        headers=headers,
    )


def _handle_chat_completion_sync(
    *,
    body: dict[str, Any],
    messages: list[dict[str, Any]],
    upstream_model: str,
    extra_body: dict[str, Any] | None,
    moralstack_conversation_id_header: str | None,
    proxy_run_id: str,
    correlation_store: ConversationCorrelationStore,
    lock_manager: ConversationLockManager,
    store: SessionStoreProtocol,
    openai_client: Any,
    orchestrator: Any,
    cfg: GovernanceConfig,
) -> Response:
    """
    Synchronous request handler: governance, session store, upstream OpenAI call.

    Intended to run inside ``run_in_threadpool`` so blocking SDK calls do not stall
    the ASGI event loop.
    """
    hdr = (moralstack_conversation_id_header or "").strip()
    conversation_id = hdr or _resolve_conversation_id_from_body_and_correlation(messages, extra_body, correlation_store)

    request_id_for_audit: str = ""
    final_response_text: str = ""
    final_text_source: str = ""
    final_action_for_event: str | None = None
    finish_reason_for_event: str = "stop"
    original_final_action_for_event: str = ""
    empty_governed_content_for_event: bool = False
    domain_for_audit: str | None = None
    result_for_audit: Any | None = None
    governance_headers_for_audit: dict[str, str] | None = None
    state_in_for_audit: Any | None = None
    state_out_for_audit: Any | None = None
    lock: threading.Lock | None = None
    out_response: Response | None = None

    try:
        try:
            lock = lock_manager.acquire(conversation_id)
        except ConversationLockTimeout:
            raise HTTPException(
                status_code=503,
                detail="Conversation busy: per-conversation lock not acquired in time.",
                headers={"Retry-After": str(_LOCK_RETRY_AFTER_SECONDS)},
            )

        conversation_context = build_conversation_context(messages)
        developer_contract = conversation_context.developer_contract
        conversation_history = context_to_turns(conversation_context)
        user_prompt = conversation_context.final_user_message

        processed = ProcessedRequest(
            prompt=user_prompt,
            developer_contract=developer_contract,
            conversation_history=conversation_history,
            conversation_context=conversation_context,
            generation_overrides=GenerationOverrides.from_mapping(body, passthrough_unset=True),
        )
        request_id_for_audit = processed.request_id

        turn_index = _resolve_turn_index(messages)
        conv_state = store.get(conversation_id) if conversation_id else None
        state_in_for_audit = conv_state
        try:
            from moralstack.observability.context import set_current_request_id

            set_current_request_id(processed.request_id)
        except Exception:
            pass

        _ensure_request_row(
            proxy_run_id=proxy_run_id,
            request_id=processed.request_id,
            prompt=user_prompt,
            conversation_id=conversation_id or None,
            turn_index=turn_index,
        )

        is_stream = bool(body.get("stream", False))

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
            # Fail closed: governance could not complete. Never deliver upstream
            # text after a pipeline failure (no passthrough) — return a governed
            # deterministic refusal. ``cfg.failure_policy`` no longer routes to
            # the wrapped client for delivery (Plan 1 invariant).
            logger.exception("Pipeline failure (failing closed): %s", exc)
            final_response_text = DEFAULT_POST_REVALIDATION_REFUSAL
            final_text_source = "governed_pipeline_refusal"
            final_action_for_event = "REFUSE"
            original_final_action_for_event = ""
            empty_governed_content_for_event = True
            finish_reason_for_event = "content_filter"
            if is_stream:
                out_response = _build_synthetic_sse_response(
                    final_response_text,
                    model=upstream_model,
                    finish_reason="content_filter",
                )
            else:
                payload = _build_synthetic_chat_completion(
                    content=final_response_text,
                    model=upstream_model,
                    finish_reason="content_filter",
                )
                out_response = JSONResponse(content=payload)
        else:
            governance_state_out = getattr(result, "conversation_governance_state_out", None)
            state_out_for_audit = governance_state_out
            if conversation_id and governance_state_out is not None:
                store.put(conversation_id, governance_state_out)

            governance_headers = build_governance_headers(result, conversation_id=conversation_id)
            governance_headers_for_audit = dict(governance_headers) if governance_headers else None
            domain_for_audit = getattr(result.response.metadata, "domain_overlay", None)

            # Governed delivery: the delivered text is ALWAYS the text produced
            # inside the MoralStack governed pipeline. The wrapped/upstream client
            # is never called to generate the delivered answer (Plan 1 invariant).
            delivery = finalize_delivery(result, config=cfg)
            final_response_text = delivery.text
            final_text_source = delivery.final_text_source
            final_action_for_event = delivery.final_action
            finish_reason_for_event = delivery.finish_reason
            original_final_action_for_event = delivery.original_final_action
            empty_governed_content_for_event = delivery.empty_governed_content

            if is_stream:
                # Product decision D2: replay the full governed answer as an
                # OpenAI-compatible synthetic SSE stream. No live upstream tokens.
                out_response = _build_synthetic_sse_response(
                    delivery.text,
                    model=upstream_model,
                    finish_reason=delivery.finish_reason,
                    headers=governance_headers,
                )
            else:
                payload = _build_synthetic_chat_completion(
                    content=delivery.text,
                    model=upstream_model,
                    finish_reason=delivery.finish_reason,
                )
                out_response = JSONResponse(content=payload, headers=governance_headers)

    finally:
        if conversation_id and final_response_text:
            try:
                correlation_store.observe_completed_turn(
                    messages=messages,
                    assistant_content=final_response_text,
                    conversation_id=conversation_id,
                )
            except Exception:
                logger.debug("observe_completed_turn failed (non-fatal)", exc_info=True)
        lock_manager.release(lock)
        if proxy_run_id and request_id_for_audit and final_text_source:
            try:
                persist_orchestration_event(
                    run_id=proxy_run_id,
                    request_id=request_id_for_audit,
                    stage="proxy",
                    component="proxy",
                    event_type=PROXY_OUTPUT_FINALIZED,
                    decision=final_action_for_event,
                    status="ok",
                    payload={
                        "final_action": final_action_for_event,
                        "final_text_source": final_text_source,
                        # Plan 1 invariant audit markers: the delivered text is the
                        # governed pipeline text; the wrapped/upstream client is
                        # never called to generate the delivered answer.
                        "governed_delivery": True,
                        "wrapped_client_delivery_call": False,
                        "original_final_action": original_final_action_for_event,
                        "empty_governed_content": empty_governed_content_for_event,
                        "final_response_length": len(final_response_text or ""),
                        "finish_reason": finish_reason_for_event,
                        "model": upstream_model,
                        # Stale delivery-guard fields are retained as audit-only
                        # metadata; they no longer route delivery.
                        "delivery_context_broader_than_governance": getattr(
                            result_for_audit, "delivery_context_broader_than_governance", False
                        ),
                        "mismatch_guard_action": getattr(result_for_audit, "mismatch_guard_action", "none"),
                        "governance_context_mode": getattr(result_for_audit, "governance_context_mode", "none"),
                        "candidate_context_mode": getattr(result_for_audit, "candidate_context_mode", "none"),
                        "prior_turn_count": getattr(result_for_audit, "prior_turn_count", 0),
                    },
                )
            except Exception:
                logger.debug("persist PROXY_OUTPUT_FINALIZED failed (non-fatal)", exc_info=True)
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
            final_action=final_action_for_event,
        )

    if out_response is None:
        raise HTTPException(status_code=500, detail="Internal error: empty proxy response.")
    return out_response


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
    upstream_generation_model = _resolve_model(cfg)
    logger.info("MoralStack proxy upstream generation model: %s", upstream_generation_model)
    store: SessionStoreProtocol = session_store if session_store is not None else InMemorySessionStore()
    lock_manager = ConversationLockManager()
    correlation_store = ConversationCorrelationStore()
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

    @app.on_event("shutdown")
    def _drain_observability_queue() -> None:
        """
        Drain the async observability queue on process exit.

        The per-request flush was removed because under burst load it timed
        out without achieving visibility. Here we block (up to 30s) for the
        background worker to finish persisting all queued events so nothing is
        lost on graceful shutdown.
        """
        try:
            from moralstack.observability import obs

            obs.shutdown(timeout=30.0)
        except Exception as exc:  # noqa: BLE001
            logger.warning("observability shutdown drain failed: %s", exc)

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        """Liveness probe."""
        return {"status": "ok", "run_id": proxy_run_id or ""}

    @app.post("/v1/chat/completions")
    @app.post("/chat/completions")
    async def chat_completions(
        request: Request,
        moralstack_conversation_id: str | None = Header(default=None, alias="X-Moralstack-Conversation-Id"),
    ) -> Response:
        """
        OpenAI-compatible chat completions endpoint with MoralStack governance.

        Per design v1.3 section 4.2:
        1. Resolve conversation_id (header / extra_body / lineage correlation).
        2. Acquire conversation lock (section 4.4).
        3. Build ProcessedRequest from messages.
        4. Call controller.process().
        5. Route: REFUSE -> synthetic completion; SAFE_COMPLETE -> forward with
           appended synthetic user turn; NORMAL_COMPLETE -> forward original.
        6. Attach X-Moralstack-* headers.
        """
        try:
            body = await request.json()
        except Exception:
            raise HTTPException(status_code=400, detail="Invalid JSON body")

        messages = body.get("messages")
        if not isinstance(messages, list):
            raise HTTPException(status_code=400, detail="`messages` must be a list")
        if not messages:
            raise HTTPException(status_code=400, detail="`messages` must not be empty")

        extra_body = body.get("extra_body") if isinstance(body.get("extra_body"), dict) else None

        safe_headers = _collect_safe_headers(request)
        has_extra_conv = bool(extra_body and extra_body.get("moralstack_conversation_id"))
        logger.debug(
            "proxy correlation diagnostics: header_conversation_id=%r "
            "extra_body_has_moralstack_conversation_id=%s safe_headers=%r",
            moralstack_conversation_id,
            has_extra_conv,
            safe_headers,
        )

        return await run_in_threadpool(
            _handle_chat_completion_sync,
            body=body,
            messages=messages,
            upstream_model=upstream_generation_model,
            extra_body=extra_body,
            moralstack_conversation_id_header=moralstack_conversation_id,
            proxy_run_id=proxy_run_id,
            correlation_store=correlation_store,
            lock_manager=lock_manager,
            store=store,
            openai_client=openai_client,
            orchestrator=orchestrator,
            cfg=cfg,
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
    final_action: str | None = None,
) -> None:
    """
    Finalize an HTTP request: update the requests row with final_response and
    emit the canonical request.meta_updated / proxy.request_finalized envelopes.

    Step 13 extension:
        - Build governance metadata from ``result.response.metadata`` and emit
          ``request.meta_updated`` so SQLite + JSONL receive the full payload.
        - Emit ``proxy.request_finalized`` with state in/out, posture in/out,
          cache hints, X-MoralStack headers and response length.

    Visibility model: final response/domain, request.meta_updated, and
    proxy.request_finalized are routed synchronously through finalize_audit_sync.
    High-frequency telemetry remains queued on the async observability worker;
    the per-request flush has been removed because under bursty load it added
    overhead without reliably improving telemetry visibility. Drainage for that
    queued telemetry is handled by the background worker and a process-shutdown
    hook in create_app.

    Best-effort: never raises. Skipped silently when observability is not
    configured (proxy_run_id == "").
    """
    if not proxy_run_id or not request_id:
        return
    try:
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
            final_action_override=final_action,
            emit_meta=False,
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
            summary = {
                "conversation_id": conversation_id,
                "turn_index": turn_index,
                "final_action": (meta.get("final_action") if isinstance(meta, dict) else None),
                "risk_score": (meta.get("risk_score") if isinstance(meta, dict) else None),
                "path": (meta.get("path_taken") or meta.get("path") if isinstance(meta, dict) else None),
                "domain": domain,
                "posture_in": posture_in,
                "posture_out": posture_out,
                "state_provided": state_in is not None,
                "state_updated": state_out is not None,
                "was_cached": (meta.get("was_cached") if isinstance(meta, dict) else None),
                "cached_from_turn": (meta.get("cached_from_turn") if isinstance(meta, dict) else None),
                "final_response_length": response_len,
                "headers": dict(governance_headers) if governance_headers else None,
                "metadata": meta if meta else None,
                "state_in": _state_summary_or_none(state_in),
                "state_out": _state_summary_or_none(state_out),
            }
            final_action_value = summary.get("final_action")
            finalize_audit_sync(
                run_id=proxy_run_id,
                request_id=request_id,
                final_action=final_action_value if isinstance(final_action_value, str) else None,
                final_response=final_response_text or "",
                domain=domain,
                proxy_summary=summary,
            )
        except Exception as exc:
            logger.debug("finalize_audit_sync failed (non-fatal): %s", exc)

        # Drainage of the async observability queue is the background worker's
        # job; a bounded per-request flush only added overhead under load (the
        # queue grew faster than it drained, hitting the timeout on every call)
        # without actually achieving visibility. Process shutdown drains via
        # the lifespan hook registered in create_app.
    except Exception as exc:
        logger.warning("Failed to finalize request observability: %s", exc)


def main() -> None:
    """CLI entry point: `moralstack-server`. Starts uvicorn with a stub orchestrator."""
    raise NotImplementedError(
        "The `moralstack-server` CLI requires explicit injection of openai_client + orchestrator. "
        "Use `from moralstack.server import create_app` in your own launcher module instead. "
        "See examples/server_quickstart.py (Step 12)."
    )
