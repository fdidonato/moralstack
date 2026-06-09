# Module: `moralstack.server` (HTTP proxy)

## Purpose

FastAPI application that exposes `POST /v1/chat/completions` in an OpenAI-compatible shape, runs the same governance path as the SDK (`OrchestrationController.process` on a `ProcessedRequest` built from request messages), then delivers the **governed pipeline text** as a synthetic `chat.completion` for every `final_action` (NORMAL_COMPLETE, SAFE_COMPLETE, REFUSE). **Governed delivery only (Plan 1): the upstream OpenAI client is never called to generate the delivered answer.** Non-stream responses are built from `finalize_delivery(...)` (`orchestration/delivery.py`); `stream=True` replays the governed text as OpenAI-compatible synthetic SSE chunks (never live upstream tokens). Adds `X-Moralstack-*` response headers for audit.

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

## Governed delivery (Plan 1)

All delivered text is the governed pipeline result, finalized by the pure
`finalize_delivery(result, config=...)` (`orchestration/delivery.py`). The proxy
serializes `GovernedDelivery.text` into a synthetic `chat.completion` (or
synthetic SSE when `stream=True`). The upstream client is never called to
generate the delivered answer, so there is no SAFE/NORMAL upstream branch and no
pipeline-failure passthrough: a pipeline error fails closed to a deterministic
governed refusal.

`final_text_source` values produced by the active path are `governed`,
`governed_refusal`, and the blank-content fail-closed `governed_pipeline_refusal`.
`PROXY_OUTPUT_FINALIZED` records `governed_delivery=true`,
`wrapped_client_delivery_call=false`, `final_text_source`,
`original_final_action`, `empty_governed_content`, and retains the older guard
fields (`delivery_context_broader_than_governance`, context modes,
`prior_turn_count`) as **audit-only** — `delivery_context_broader_than_governance`
no longer routes delivery to an upstream call.

### Historical: Final Output Revalidation

`revalidate_final_output(...)` and the `PROXY_FINAL_REVALIDATION_*` events were
part of the pre-Plan-1 upstream-delivery flow (sources `safe_complete_upstream` /
`upstream_regen`). They are **no longer invoked on the active delivery paths**.
`moralstack/orchestration/final_revalidation.py` and the `PROXY_FINAL_REVALIDATION_*`
event names are retained only so historical UI/report rows keep rendering.

## Governed answer model

The `model` field in the client JSON body is a **requested alias only** and does
not select the model that generates the delivered answer. The governed answer is
produced by the resolved policy model:

`GovernanceConfig.model` → `OPENAI_MODEL` → `gpt-4o` (same precedence as the SDK
bootstrap); governed revisions use `MORALSTACK_POLICY_REWRITE_MODEL` when set.

Synthetic responses echo the resolved model in the `model` field of the JSON
payload.

## Configuration / install

- Optional extras: `[ui]` includes proxy-related deps; `[server]` is a lighter subset (`fastapi`, `uvicorn`, `httpx`).
- Console script `moralstack-server` points at `moralstack.server.proxy:main`, which intentionally raises `NotImplementedError` until a deployer launcher wires real clients (Step 12 examples).

## Tests

- `tests/test_server_proxy.py` — integration tests with `TestClient`; async overlap tests (`httpx.AsyncClient` + `ASGITransport`); JSONL alignment under concurrent distinct `conversation_id` with a real orchestrator.
- `tests/test_server_fingerprint.py` — fingerprint unit tests.
- `tests/test_conversation_correlation.py` — lineage hash and `ConversationCorrelationStore` behaviour.
