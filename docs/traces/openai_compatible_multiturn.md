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

### conversation_id generation / propagation (`proxy.py:134-152`, resolver;
`proxy.py:654-709`, route handler)
Resolution precedence (principal keying only affects step 3):
1. HTTP header `X-Moralstack-Conversation-Id` (highest).
2. `extra_body.moralstack_conversation_id`.
3. **Lineage correlation** (`ConversationCorrelationStore.resolve`), keyed
   additionally by `principal` (see below).

**Principal derivation (P3 / P0-3 / A3, `proxy.py:178-215`, `_extract_principal`)**
— layered, first match wins:
- **(A)** Trusted internal header `X-Moralstack-Tenant-Id` — used verbatim
  (whitespace-stripped) when present and non-empty. Set by a trusted fronting
  layer; this is a **documented trust boundary, not enforced by MoralStack**
  (a hostile client could forge the header unless the fronting layer strips
  client-supplied copies). Note this header starts with `x-` and therefore
  passes `_collect_safe_headers` (`proxy.py:164-175`) and appears in the
  correlation debug line (`proxy.py:683-691`) — acceptable for an identifier,
  not for a secret.
- **(B)** HMAC-SHA256 of an `Authorization: Bearer <token>` value, keyed by
  `MORALSTACK_PRINCIPAL_HMAC_SECRET` (read **per-request** via `os.environ.get`,
  not captured once at `create_app`, so secret rotation takes effect
  immediately). Only a `Bearer` scheme triggers this path; a non-Bearer scheme
  (e.g. `Basic`) or a malformed/empty value skips to (C). If the secret is
  unset, (B) is silently skipped (no hardcoded fallback). Neither the raw
  token nor the derived digest is ever logged (the token stays filtered by
  `_SENSITIVE_HEADER_MARKERS`, `proxy.py:155-161`). **Token rotation mid-conversation
  changes the principal and silently splits lineage into a new conversation_id**
  (expected, not a bug).
- **(C)** Empty-string sentinel `""` — the pre-change, no-principal behavior.
  All existing single-tenant deployments and the byte-equality-locked tests
  take this path.

Composite keying is **anti-collision only, not an authentication/authorization
boundary**: it prevents two tenants' identical histories from sharing one
`conversation_id`, but it does not verify tenant identity beyond the (A)/(B)
trust assumptions above.

Lineage correlation (`server/conversation_correlation.py`):
- `canonical_history_hash(messages)` = SHA-256 over canonicalized role+content
  (`:67-75`). **Unchanged by the principal-keying design** — no `salt` param, no
  envelope; the isolation lives entirely in the store's internal map key.
- `canonical_parent_history_hash` = hash of `messages[:-1]` when the last message
  is `user` (`:78-91`).
- The store's internal map is `OrderedDict[tuple[str, str], _Entry]` keyed by
  `(principal, hash)` (`:112-138`) — bounded via TTL (default
  `DEFAULT_CORRELATION_TTL_SECONDS=3600`) and a max-entries FIFO cap (default
  `DEFAULT_MAX_CORRELATION_ENTRIES=20_000`, `:41-42`), overridable via
  `create_app(correlation_store=...)` or the env vars below. `resolve`: if the
  `(principal, request_hash)` key is known and not expired → return its
  conversation_id (hit path does **not** refresh the entry's TTL); else if
  `(principal, parent_hash)` is known and not expired → inherit that
  conversation_id and record the request hash under the same principal; else
  mint a new `msconv-<uuid16>` id (`:140-159`).
- After a turn completes, `observe_completed_turn` records the completed history
  (including the assistant reply) under `(principal, completed_hash)` so the next
  request's parent hash links back within the same principal
  (`:161-176`).
- Env wiring (`create_app`, `proxy.py:544-574`, `608-611`):
  `MORALSTACK_CORRELATION_TTL_SECONDS` / `MORALSTACK_CORRELATION_MAX_ENTRIES`.
  Best-effort parsed **and** range-validated (`ttl_seconds > 0`, `max_entries >= 1`);
  a missing var, a parse error, or an out-of-range value falls back to the
  constructor defaults — `create_app` never raises on a malformed value.
  **Caveat**: the correlation TTL is not automatically aligned with the
  `SessionStore` TTL; if an operator overrides only one, a lineage entry can
  outlive (or expire before) its governance session state.

### turn_index handling (`proxy.py:717-732`)
Stateless: `turn_index = max(0, user_message_count - 1)`. Turn 0 = first request
with one user message; turn 1 = two user messages, etc. Chosen so a server
restart or multiple clients sharing a conversation_id don't desync from the
client's view.

### Collision risks
- **Identical histories from the same principal still collide.** The module
  docstring states it explicitly: two distinct samples whose histories (and
  assistant outputs) are byte-identical, **for the same principal**, cannot be
  distinguished without an external id (`conversation_correlation.py:17-18`).
  For benchmarks that reuse the same opening user message across many samples
  under one principal (the common no-tenant-header COMPL-AI case, principal
  `""`), all of them hash-collide to **one** `conversation_id`. As of P3 / P0-3 /
  A3, this collision **no longer crosses principal/tenant boundaries**: the
  internal lineage map is keyed by `(principal, hash)`, so two tenants with
  byte-identical histories resolve to different `conversation_id`s.
- **Consequence (verified mechanism)**: the resolved conversation_id is the key
  for the per-conversation lock (`ConversationLockManager`, `proxy.py:78-131`),
  the `SessionStore` entry (`proxy.py:381`, `:438`). **The ledger is NOT keyed by
  `conversation_id`** — its key is `(contract_hash, posture, domain)`
  (`orchestration/ledger.py:262`, `LedgerKey`); an earlier version of this trace
  incorrectly cited `ledger.py:254` (a `LedgerResult` return statement, not a
  key) as a collision-shared surface — corrected here (code wins, PROJECT_SPEC
  §9). So same-principal colliding requests are serialized under one lock and
  share one governance-state entry — i.e. a decision or posture from one sample
  can be read by another sample under the *same* principal. (Whether a given
  run actually collides depends on the benchmark data containing
  identical-history samples under the same principal.)
- **Mitigation available**: send a unique `X-Moralstack-Conversation-Id` header
  (or `extra_body.moralstack_conversation_id`) per logical conversation to bypass
  lineage hashing entirely, or a per-tenant `X-Moralstack-Tenant-Id` /
  `Authorization` to isolate by principal.
- **Cross-tenant availability effect (new, P3 / P0-3 / A3)**: all principals
  share one `max_entries` eviction pool, so a noisy tenant can evict another
  tenant's lineage entries under load, causing a mid-conversation
  `conversation_id` split for the affected tenant. This is an availability
  effect, not a confidentiality leak (composite keying still prevents any
  content/state sharing across principals). A per-principal cap is a possible
  follow-up.

### Worker / concurrency implications (`proxy.py:78-131`)
- `ConversationLockManager` hands out one `threading.Lock` per conversation_id;
  empty conversation_id → no lock (independent request). Acquire timeout 30s →
  HTTP 503 with `Retry-After: 10` (`ConversationLockTimeout`). **`_locks` growth
  is unbounded and its bounding is deferred** (`proxy.py:90-96`,
  `# TODO(P3-followup)`): a naive idle-prune races `acquire()` releasing
  `_meta_lock` before the blocking `lock.acquire()` call. The correlation
  store's TTL/max-entries bound (above) already removes the dominant growth
  source (~2 entries/turn vs 1 lock/conversation); a refcounted-waiter design is
  the follow-up.
- Must run a **single uvicorn worker** for multi-turn: each `--workers N` process
  has its own pipeline, session store, and lock/correlation-store namespace;
  routing turns of one conversation to different workers breaks continuity, and
  principal isolation and bounding are per-process, not cluster-wide
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
