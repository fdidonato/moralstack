# TRACE — OpenAI-compatible endpoint & multi-turn behavior

How OpenAI-compatible requests arrive, how conversations are identified across
turns, and what is persisted. Claims are grounded in the cited source.
Path-specific caveats are noted inline.

> **Supported path:** the production proxy built by
> `moralstack/server/proxy.py:create_app` and launched via
> `examples/server_quickstart.py`.

| File | Launch | Multi-turn | History used | Output | Observability |
|---|---|---|---|---|---|
| `moralstack/server/proxy.py` (`create_app`) | `examples/server_quickstart.py` (uvicorn, **1 worker**; recommended command port **8080**; `main()` default **8787** via `MORALSTACK_OPENAI_COMPATIBLE_API_PORT`) | yes (conversation_id, locks, session store) | yes (`ConversationContext` + `conversation_history` built from the full request body) | governed pipeline text via `finalize_delivery` | full (requests, events, proxy_request_events, conversation_states) |

The COMPL-AI `llm_rules` path uses the **production proxy** (per
`examples/server_quickstart.py:12`).

---

## Production Proxy (`server/proxy.py`)

### Endpoints
`POST /v1/chat/completions`, `POST /chat/completions`, `GET /healthz`
(`proxy.py:458-468`). The async route reads JSON, validates `messages` is a
non-empty list, then dispatches `_handle_chat_completion_sync` via
`run_in_threadpool` so blocking work doesn't stall the event loop
(`proxy.py:463-518`).

### How messages arrive
The full OpenAI body is received. `messages` is the entire client-sent history
(OpenAI clients resend history every turn). The proxy:
- builds one shared `ConversationContext` from the full message list,
- derives `developer_contract` from the last non-empty `system`/`developer`
  message in that context,
- builds `conversation_history` from prior user/assistant turns before the final
  user message,
- extracts `user_prompt` from the final user message (`proxy.py:239-256`).
- reads `max_tokens` / `max_completion_tokens` / `temperature` / `top_p` from the
  body via `GenerationOverrides.from_mapping(body, passthrough_unset=True)` and attaches
  them as `ProcessedRequest.generation_overrides`. These per-request sampling overrides
  flow to the delivered answer (NORMAL_COMPLETE / SAFE_COMPLETE / rewrite /
  speculative draft). Because the proxy uses `passthrough_unset=True`, a field the
  client does **not** send is **omitted** from the OpenAI call (the model uses its own
  default) — the env defaults (`OPENAI_MAX_TOKENS` / `OPENAI_TEMPERATURE` /
  `OPENAI_TOP_P`) do not apply to delivered-answer generation on the proxy path, so a
  request that omits sampling parameters behaves like a plain OpenAI call. REFUSE wording
  is excluded and still honors the env default. The SDK path
  (`sdk/wrapper.py:_create_inner`) instead uses `passthrough_unset=False`: an unset field
  falls back to the env default (precedence **override > `GenerationConfig` > env
  default**). See `docs/modules/policy.md`.

**Full request-body history is attached** to governance via
`ProcessedRequest.conversation_context` plus the legacy `conversation_history`
field. DCCL and speculative generation can use a role-serialized transcript.

### Governed Delivery

After `controller.process(...)` returns, the proxy calls `finalize_delivery(...)`.
The delivered text is always the text produced inside the MoralStack governed
pipeline for NORMAL_COMPLETE, SAFE_COMPLETE, and REFUSE. The upstream OpenAI
client is not called to generate the delivered answer.

Empty governed content fails closed to a governed refusal
(`final_text_source="governed_pipeline_refusal"`). The stale compliance delivery
guard fields are retained as audit metadata, but they no longer route delivery to
an upstream call. The SDK uses the same governed delivery invariant.

### conversation_id generation / propagation (`proxy.py:218-219`, `121-136`)
Resolution precedence:
1. HTTP header `X-Moralstack-Conversation-Id` (highest).
2. `extra_body.moralstack_conversation_id`.
3. **Lineage correlation** (`ConversationCorrelationStore.resolve`).

Lineage correlation (`server/conversation_correlation.py`):
- `canonical_history_hash(messages)` = SHA-256 over canonicalized role+content.
- `canonical_parent_history_hash` = hash of `messages[:-1]` when the last message
  is `user`.
- `resolve`: if the request hash is known → return its conversation_id; else if
  the parent hash is known → inherit that conversation_id and record the request
  hash; else mint a new `msconv-<uuid16>` id (`conversation_correlation.py:99-114`).
- After a turn completes, `observe_completed_turn` records the history *including*
  the assistant reply so the next request's parent hash links back
  (`conversation_correlation.py:116-129`).

### turn_index handling (`proxy.py:526-541`)
Stateless: `turn_index = max(0, user_message_count - 1)`. Turn 0 = first request
with one user message; turn 1 = two user messages, etc. Chosen so a server
restart or multiple clients sharing a conversation_id don't desync from the
client's view.

### Collision risks
- **Identical histories collide.** The module docstring states it explicitly:
  two distinct samples whose histories (and assistant outputs) are byte-identical
  cannot be distinguished without an external id
  (`conversation_correlation.py:10-12`). For benchmarks that reuse the same
  opening user message across many samples, all of them hash-collide to **one**
  conversation_id.
- **Consequence (verified mechanism)**: the resolved conversation_id is the key
  for the per-conversation lock (`ConversationLockManager`, `proxy.py:87-110`),
  the `SessionStore` entry (`proxy.py:256,303-304`), and the ledger key
  (`ledger.py:254`). So colliding requests are serialized under one lock and
  share one governance-state/ledger entry — i.e. a decision or posture from one
  sample can be read by another. (Whether a given run actually collides depends
  on the benchmark data containing identical-history samples.)
- **Mitigation available**: send a unique `X-Moralstack-Conversation-Id` header
  (or `extra_body.moralstack_conversation_id`) per logical conversation to bypass
  lineage hashing entirely.

### Worker / concurrency implications (`proxy.py:72-119`, `234-373`)
- `ConversationLockManager` hands out one `threading.Lock` per conversation_id;
  empty conversation_id → no lock (independent request). Acquire timeout 30s →
  HTTP 503 with `Retry-After: 10` (`ConversationLockTimeout`).
- Must run a **single uvicorn worker** for multi-turn: each `--workers N` process
  has its own pipeline, session store, and lock namespace; routing turns of one
  conversation to different workers breaks continuity
  (`examples/server_quickstart.py:16-21`).
- Different conversation_ids run concurrently (threadpool); same conversation_id
  is serialized.

### Streaming implications
The proxy supports `stream=true` by replaying the already-governed final text as
OpenAI-compatible synthetic SSE chunks. Governance runs to completion first, then
`_build_synthetic_sse_response` emits `chat.completion.chunk` events whose delta
contents concatenate to the governed answer, followed by `data: [DONE]`. It does
not forward live upstream tokens.

### Response headers (proxy)
`build_governance_headers` (`server/headers.py:40-54`) attaches, on every
response: `X-Moralstack-Decision`, `-Risk-Score` (4dp), `-Posture` (default
`NORMAL`), `-Path`, `-Conversation-Id`, `-Internal-Draft-Reused`
(`true`/`false`). Conditionally: `-Cached-From` (when a cached decision id is
present), and `-Compliance-Decision` + `-Compliance-Rule` (when a DCCL verdict
other than `NO_CONTRACT` is present). REFUSE responses also set
`finish_reason="content_filter"`.

### What is persisted (proxy)
- `requests` row pre-inserted (`_ensure_request_row`, `proxy.py:584-621`) and
  finalized with `final_response`/domain/meta (`_finalize_request`,
  `proxy.py:624-705`).
- `PROXY_OUTPUT_FINALIZED` orchestration event with `final_text_source`
  (`governed`, `governed_refusal`, or `governed_pipeline_refusal`) plus
  governed-delivery audit markers (`proxy.py:374-394`).
- `proxy_request_events` row via `emit_proxy_request_finalized` (posture in/out,
  cache hints, headers, response length) (`proxy.py:678-698`).
- `conversation_states` + `ledger_events` from the controller (multi-turn).
- `session_store.put(conversation_id, governance_state_out)` after a successful
  turn (`proxy.py:303-304`).
