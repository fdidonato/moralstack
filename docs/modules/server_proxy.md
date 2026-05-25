# Module: `moralstack.server` (HTTP proxy)

## Purpose

FastAPI application that exposes `POST /v1/chat/completions` in an OpenAI-compatible shape, runs the same governance path as the SDK (`OrchestrationController.process` on a `ProcessedRequest` built from request messages), then either returns a synthetic `chat.completion` (REFUSE), forwards the original body (NORMAL_COMPLETE), or forwards with an appended synthetic user turn (SAFE_COMPLETE). Adds `X-Moralstack-*` response headers for audit.

Normative reference: multiturn design v1.3 section 4.

## Public entry points

- `moralstack.server.create_app` — factory: `create_app(openai_client=..., orchestrator=..., config=..., session_store=...)`.
- `moralstack.server.conversation_correlation.ConversationCorrelationStore` — process-local lineage mapping for OpenAI-style full-history replays when no explicit `conversation_id` is provided.
- `compute_conversation_fingerprint` — deterministic diagnostic hash from the opening message stem (through the first `user` message); not the authoritative `conversation_id` (use `msconv-*` from the correlation store or client headers).
- `build_governance_headers` — header dict from `OrchestratorResult`.

## Governance response headers

`build_governance_headers` (`moralstack/server/headers.py`) attaches:

| Header | Description |
|--------|-------------|
| `X-Moralstack-Decision` | `final_action` (`NORMAL_COMPLETE`, `SAFE_COMPLETE`, `REFUSE`, …) |
| `X-Moralstack-Risk-Score` | Normalized risk score |
| `X-Moralstack-Posture` | Conversation governance posture |
| `X-Moralstack-Path` | Processing path (includes `COMPLIANCE_FAST_PATH` on DCCL match) |
| `X-Moralstack-Conversation-Id` | Resolved conversation id |
| `X-Moralstack-Internal-Draft-Reused` | Whether an internal speculative draft was reused |
| `X-Moralstack-Cached-From` | Present when a ledger cache hit was applied |
| `X-Moralstack-Compliance-Decision` | DCCL verdict when a developer contract was evaluated (`MATCH`, `NO_MATCH`, `SAFETY_OVERRIDE`; omitted for `NO_CONTRACT`) |
| `X-Moralstack-Compliance-Rule` | Matched structured rule id when decision is `MATCH` |

## Deployment notes

- For multi-turn conversational clients (full history replay per request), run **one** uvicorn worker per process unless you provide a **shared** session store and distributed locking across workers. Each worker has its own `InMemorySessionStore` and `ConversationCorrelationStore`.
- Request parsing uses `moralstack.orchestration.conversation_context.build_conversation_context` so the proxy and SDK agree on the final user message, prior turns, developer contract, and `history_source`. The legacy `conversation_history` field is still populated for existing modules, but the full request-body transcript is also available as `ProcessedRequest.conversation_context`.
- Blocking orchestrator and upstream OpenAI SDK calls run in a Starlette threadpool so the ASGI loop can accept concurrent requests; per-`conversation_id` locks still serialize same-conversation turns.
- **Per-request controller state:** `OrchestrationController` is typically a process-wide singleton (for example one instance per `create_app`). Multi-turn linkage and ledger intent fields for a single `process()` call are held in a stack-local `ProcessCallContext` (`moralstack/orchestration/process_context.py`) passed through internal helpers — not on the controller instance — so concurrent proxy requests on different `conversation_id` values cannot cross-contaminate observability metadata.

## Compliance fast-path delivery

On `COMPLIANCE_FAST_PATH`, the proxy can return the governed draft directly
instead of making a second upstream call. This is intentionally different from
the SDK, which calls the wrapped client with the original full `messages` after
governance. The divergence is post-governance text delivery only: `final_action`,
`path`, compliance verdict, risk category, and reason codes are computed in the
shared `process()` call before either entry layer delivers content.

The proxy reuses governed content only when:

- `result.path == "COMPLIANCE_FAST_PATH"`
- `result.response.content` is non-empty
- `result.delivery_context_broader_than_governance` is false

If the delivery/governance mismatch guard is true, the proxy falls back to an
upstream call with the original full messages. `PROXY_OUTPUT_FINALIZED` records
`final_text_source`, `reused_governed_content`, context modes, `prior_turn_count`,
and the guard decision.

## Upstream generation model

The `model` field in the client JSON body is **not** forwarded to OpenAI for final
generation. The proxy always uses the resolved upstream model:

`GovernanceConfig.model` → `OPENAI_MODEL` → `gpt-4o` (same precedence as the SDK bootstrap).

Clients may send a virtual alias (for example a COMPL-AI benchmark model id); only
`OPENAI_MODEL` (or `GovernanceConfig.model`) is passed to `chat.completions.create`.
Synthetic REFUSE responses echo the same resolved model in the `model` field of the
JSON payload.

## Configuration / install

- Optional extras: `[ui]` includes proxy-related deps; `[server]` is a lighter subset (`fastapi`, `uvicorn`, `httpx`).
- Console script `moralstack-server` points at `moralstack.server.proxy:main`, which intentionally raises `NotImplementedError` until a deployer launcher wires real clients (Step 12 examples).

## Tests

- `tests/test_server_proxy.py` — integration tests with `TestClient`; async overlap tests (`httpx.AsyncClient` + `ASGITransport`); JSONL alignment under concurrent distinct `conversation_id` with a real orchestrator.
- `tests/test_server_fingerprint.py` — fingerprint unit tests.
- `tests/test_conversation_correlation.py` — lineage hash and `ConversationCorrelationStore` behaviour.
