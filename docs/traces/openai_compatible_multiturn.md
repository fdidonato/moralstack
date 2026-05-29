# TRACE — OpenAI-compatible endpoint & multi-turn behavior

How OpenAI-compatible requests arrive, how conversations are identified across
turns, and what is persisted. Claims are grounded in the cited source.
Path-specific caveats are noted inline.

> **There are two bridges.** They behave differently. Pick deliberately.

| | Production proxy | Standalone bridge |
|---|---|---|
| File | `moralstack/server/proxy.py` (`create_app`) | `scripts/openai_compatible_server.py` |
| Launch | `examples/server_quickstart.py` (uvicorn, **1 worker**; recommended command port **8080**; `main()` default **8787** via `MORALSTACK_OPENAI_COMPATIBLE_API_PORT`) | `python scripts/openai_compatible_server.py` (port **8787**) |
| Multi-turn | yes (conversation_id, locks, session store) | **no** (single-turn) |
| History used | yes (`ConversationContext` + `conversation_history` built from the full request body) | **no** (only last user message) |
| Output | upstream generation (or guarded governed draft on compliance fast-path) | governed `result.response.content` |
| Observability | full (requests, events, proxy_request_events, conversation_states) | per-request `run`, governance events |

The COMPL-AI `llm_rules` path uses the **production proxy** (per
`examples/server_quickstart.py:12`).

---

## A. Production proxy (`server/proxy.py`)

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

**Full request-body history is attached** to governance via
`ProcessedRequest.conversation_context` plus the legacy `conversation_history`
field. DCCL and speculative generation can use a role-serialized transcript; the
upstream generation body remains the client's original `messages` (minus
`extra_body`, with the model forced to the configured upstream model)
(`_build_upstream_kwargs`, `proxy.py:750-755`).

### Compliance fast-path delivery

When DCCL returns `MATCH`, `process()` computes the governance decision before
the proxy decides how to deliver text. The proxy may return the governed draft
directly only when `path == COMPLIANCE_FAST_PATH`, the content is non-empty, and
`delivery_context_broader_than_governance` is false. If the guard is true, the
proxy falls back to an upstream call with the original full messages, which is a
safe superset of the governance context.

The **SDK** (`sdk/wrapper.py`, `GovernedCompletions`) mirrors this delivery rule:
on `NORMAL_COMPLETE` it returns the governed draft directly
(`GovernedResponse.from_governed_draft`, no upstream call) under the same three
conditions (`path == COMPLIANCE_FAST_PATH`, non-empty content, guard false), and
otherwise regenerates upstream (`upstream_regen`) followed by
`revalidate_final_output`. Outside the compliance fast-path (benign / fast /
deliberative `NORMAL_COMPLETE`), both the proxy and the SDK regenerate upstream —
this is intentional (the delivered text must come from the deployer's own client
with their original messages and sampling params), not an asymmetry.

**Streaming (`stream=True`) in the SDK.** Streaming is a transport contract over
the *final* answer, not over intermediate generations, so it must not force
unvalidated upstream tokens onto the caller. The SDK splits on whether a
developer contract is present (`wrapper.py`, `live_stream` vs `synthetic_stream`):

- **No contract** → `live_stream`: raw upstream tokens are forwarded directly
  (`GovernedStreamResponse`); nothing to revalidate.
- **Contract present** → `synthetic_stream`: the full text is produced and
  revalidated **non-streamed** (governed-draft reuse on the compliance fast-path,
  otherwise a forced `stream=False` upstream call + `revalidate_final_output`),
  then replayed token by token as a `GovernedSyntheticStream`
  (`_iter_word_chunks`; only the final chunk carries `finish_reason="stop"`). If
  revalidation blocks, the refusal text is replayed instead. This replaces the
  earlier fail-closed behavior that refused all contract-bearing streaming
  requests.

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
The proxy has **no streaming branch** (verified). `_build_upstream_kwargs` keeps
`stream` in the body, so `openai_client.chat.completions.create(stream=True)`
returns a `Stream` object; that object has no `model_dump`/`to_dict`, so
`_serialize_upstream_response` falls to `{"raw": str(stream)}` and
`_extract_text_from_upstream` returns `""` (`proxy.py:727-774`). The client
receives a single non-OpenAI JSON body and no streamed tokens. No test exercises
this. Use the SDK directly for streaming.

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
  (`refusal` / `safe_complete_upstream` / `upstream_regen` / `governed_draft` /
  `passthrough_on_error`) (`proxy.py:374-394`).
- `proxy_request_events` row via `emit_proxy_request_finalized` (posture in/out,
  cache hints, headers, response length) (`proxy.py:678-698`).
- `conversation_states` + `ledger_events` from the controller (multi-turn).
- `session_store.put(conversation_id, governance_state_out)` after a successful
  turn (`proxy.py:303-304`).

---

## B. Standalone bridge (`scripts/openai_compatible_server.py`)

- Endpoints: `POST /v1/chat/completions`, `/chat/completions`, plus `/`,
  `/v1/models` (`:287-310`).
- `_extract_prompt` returns the **last user message only**; history is discarded
  (`:98-104`).
- `_run_moralstack(prompt)` calls `orchestrator.process(request)` with **no**
  conversation_id, turn_index, or conversation_state — every request is
  single-turn (`:201-223`).
- Returns the governed `result.response.content` as the assistant message and
  echoes governance under `moralstack_metadata` (`:239-251,347-376`).
- Concurrency bounded by an asyncio `Semaphore` (`MAX_INFLIGHT`, default 8); at
  capacity → 503 with `Retry-After` (`:280-331`).
- Creates a fresh `run` per request and flushes observability in `finally`
  (`:214-237`).

**Implication**: do not use this bridge for `llm_rules` / multi-turn benchmarks —
it cannot see prior turns and will govern each message in isolation.
