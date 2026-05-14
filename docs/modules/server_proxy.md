# Module: `moralstack.server` (HTTP proxy)

## Purpose

FastAPI application that exposes `POST /v1/chat/completions` in an OpenAI-compatible shape, runs the same governance path as the SDK (`OrchestrationController.process` on a `ProcessedRequest` built from request messages), then either returns a synthetic `chat.completion` (REFUSE), forwards the original body (NORMAL_COMPLETE), or forwards with an appended synthetic user turn (SAFE_COMPLETE). Adds `X-Moralstack-*` response headers for audit.

Normative reference: multiturn design v1.3 section 4.

## Public entry points

- `moralstack.server.create_app` — factory: `create_app(openai_client=..., orchestrator=..., config=..., session_store=...)`.
- `moralstack.server.conversation_correlation.ConversationCorrelationStore` — process-local lineage mapping for OpenAI-style full-history replays when no explicit `conversation_id` is provided.
- `compute_conversation_fingerprint` — deterministic diagnostic hash from the opening message stem (through the first `user` message); not the authoritative `conversation_id` (use `msconv-*` from the correlation store or client headers).
- `build_governance_headers` — header dict from `OrchestratorResult`.

## Deployment notes

- For multi-turn conversational clients (full history replay per request), run **one** uvicorn worker per process unless you provide a **shared** session store and distributed locking across workers. Each worker has its own `InMemorySessionStore` and `ConversationCorrelationStore`.
- Blocking orchestrator and upstream OpenAI SDK calls run in a Starlette threadpool so the ASGI loop can accept concurrent requests; per-`conversation_id` locks still serialize same-conversation turns.

## Configuration / install

- Optional extras: `[ui]` includes proxy-related deps; `[server]` is a lighter subset (`fastapi`, `uvicorn`, `httpx`).
- Console script `moralstack-server` points at `moralstack.server.proxy:main`, which intentionally raises `NotImplementedError` until a deployer launcher wires real clients (Step 12 examples).

## Tests

- `tests/test_server_proxy.py` — integration tests with `TestClient`.
- `tests/test_server_fingerprint.py` — fingerprint unit tests.
- `tests/test_conversation_correlation.py` — lineage hash and `ConversationCorrelationStore` behaviour.
