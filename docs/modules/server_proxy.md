# Module: `moralstack.server` (HTTP proxy)

## Purpose

FastAPI application that exposes `POST /v1/chat/completions` in an OpenAI-compatible shape, runs the same governance path as the SDK (`OrchestrationController.process` on a `ProcessedRequest` built from request messages), then either returns a synthetic `chat.completion` (REFUSE), forwards the original body (NORMAL_COMPLETE), or forwards with an appended synthetic user turn (SAFE_COMPLETE). Adds `X-Moralstack-*` response headers for audit.

Normative reference: multiturn design v1.3 section 4.

## Public entry points

- `moralstack.server.create_app` — factory: `create_app(openai_client=..., orchestrator=..., config=..., session_store=...)`.
- `compute_conversation_fingerprint` — deterministic `conversation_id` fallback from message prefix.
- `build_governance_headers` — header dict from `OrchestratorResult`.

## Configuration / install

- Optional extras: `[ui]` includes proxy-related deps; `[server]` is a lighter subset (`fastapi`, `uvicorn`, `httpx`).
- Console script `moralstack-server` points at `moralstack.server.proxy:main`, which intentionally raises `NotImplementedError` until a deployer launcher wires real clients (Step 12 examples).

## Tests

- `tests/test_server_proxy.py` — integration tests with `TestClient`.
- `tests/test_server_fingerprint.py` — fingerprint unit tests.
