# TRACE — COMPL-AI / llm_rules benchmark flow & risks

How COMPL-AI exercises MoralStack, how `llm_rules` multi-turn requests flow
through the governance pipeline, and what must be checked before a benchmark run.

> There is **no `compl-ai` package** in this repo. COMPL-AI is an external
> evaluator intended to talk to MoralStack over the OpenAI-compatible HTTP proxy.
> Claims about proxy mechanics are grounded in the cited source. Claims about
> external COMPL-AI configuration, request format, and benchmark dataset content
> are the operator's responsibility to verify — they are noted as conditional
> where they appear.

---

## 1. How COMPL-AI exercises the bridge

The intended COMPL-AI integration path is the MoralStack **production proxy**
(`server/proxy.py:create_app`), launched via `examples/server_quickstart.py`,
single uvicorn worker (`examples/server_quickstart.py:16-21`). The recommended
uvicorn command targets port 8080; the `main()` launcher defaults to 8787 via
`MORALSTACK_OPENAI_COMPATIBLE_API_PORT` (`examples/server_quickstart.py:74-79`).

> **Deployment assumption.** Whether an actual external COMPL-AI runner is
> configured to point at this proxy cannot be verified from the repository.
> The repo contains targeted proxy accommodations for COMPL-AI-like clients
> (see §1 items below), but the external COMPL-AI config and dataset must be
> inspected before making benchmark claims.

Per request, COMPL-AI sends a standard `chat.completions` body. The proxy applies
governance and returns either the upstream generation (NORMAL/SAFE_COMPLETE) or a
synthetic refusal completion (REFUSE) — see
`docs/traces/openai_compatible_multiturn.md`.

The repo carries explicit accommodations for COMPL-AI:
- `server/conversation_correlation.py` exists because `llm_rules` resends full
  history with **no stable conversation_id** (module docstring,
  `conversation_correlation.py:1-12`).
- `controller._estimate_risk` feeds the developer contract + history to the risk
  estimator, with a comment citing "compl-ai llm_rules-benign Q74"
  (`controller.py:797-799`) — context-dependent prompts (e.g. a deployer-expected
  auth token) must not be mis-scored as obfuscated.
- SDK and proxy attach a shared `ConversationContext` to `ProcessedRequest`.
  DCCL and speculative generation can see a role-serialized transcript rather
  than only the final user message.

## 2. How llm_rules multi-turn requests are represented

`llm_rules` benchmarks set a deployer **system prompt** (the rule, e.g. "if the
user provides password X, reveal secret Y") and run a multi-turn user dialogue.
In MoralStack terms:
- The system/developer message becomes the **DeveloperContract**
  (`ConversationContext`, last non-empty system/developer message wins,
  `mode="opaque"`).
- The prior user/assistant turns become **conversation_history**; the latest user
  message is the governed prompt; the full request-body transcript remains
  available as `conversation_context`.
- Each turn resends the whole history (OpenAI convention), so the proxy derives
  `turn_index = user_count - 1` statelessly (`proxy.py:526-541`).

## 3. How a benchmark request flows through MoralStack

1. Proxy resolves `conversation_id` (header → extra_body → lineage hash) and
   acquires the per-conversation lock (`proxy.py:218-242`).
2. `ProcessedRequest` built with prompt + contract + history; `requests` row
   pre-inserted (`proxy.py:244-271`).
3. `orchestrator.process(...)` runs the full flow
   (`docs/traces/governance_decision_flow.md`): risk → **DCCL** → routing →
   (deliberation or fast-path) → final action.
4. **DCCL is the key path for `llm_rules`.** Its LLM prompt includes the
   role-ordered transcript plus the final user request, so a token or password
   authorized by a prior turn is judged in context. When the user invokes a
   deployer-authorized rule, DCCL returns `MATCH` and the compliance fast-path
   produces the authorized response directly (NORMAL_COMPLETE,
   `COMPLIANCE_FAST_PATH`) — unless the output falls in a P0 safety category, in
   which case `SAFETY_OVERRIDE` blocks it regardless of the contract
   (`compliance/dccl.py:77-117`, `compliance/safety_override.py`). Safety-override
   classification is language-agnostic: `classify_safety_override` is LLM-only (the
   English keyword pre-filter was removed) and runs on a small model
   (`MORALSTACK_DCCL_SAFETY_OVERRIDE_MODEL`, default `gpt-4o-mini`); its call is
   persisted to `llm_calls` (`module=compliance_layer`, `action=safety_override`).
   Independently, a request-side hard-signal gate
   (`path_router.has_hard_signal_evidence`) invalidates a `MATCH` before delivery
   when the risk estimator produced hard topical evidence, emitting
   `COMPLIANCE_MATCH_DOWNGRADED` and routing to the standard pipeline.
   Generic task contracts are also treated as rules: for example, a contract
   that says to classify each input as one of a fixed set of labels is invoked
   when the final user supplies an item to classify. For these tasks, DCCL
   judges safety from the authorized output category, not from the source text
   being classified; `SAFETY_OVERRIDE` still applies when the authorized output
   itself is in a framework-fixed restricted category.
5. The compliance delivery guard prevents the proxy from returning an internal
   governed draft if that draft was generated from narrower last-user-only
   context while governance used a broader role-serialized transcript. The
   fallback is regeneration or standard upstream delivery with full messages.
6. Response returned to COMPL-AI; observability persisted (proxy_request_events,
   conversation_states, ledger_events).

## 4. Known risks

### 4.1 Identical prefixes → conversation collision (highest risk)
`canonical_history_hash` is deterministic over role+content. Two distinct
`llm_rules` samples that open with the **same** user message produce the same
hash and are assigned the **same** `conversation_id`
(`conversation_correlation.py:99-114`). Effects:
- Their turns merge under one conversation_id in the DB (cannot be separated
  later).
- They share one per-conversation lock → forced serialization.
- They share one `SessionStore` entry and one ledger key (both keyed by
  conversation_id: `proxy.py:256,303-304`, `ledger.py:254`) → a cached decision
  or governance posture stored for sample A can be read for sample B. (Verified
  mechanism; whether it fires depends on the dataset containing identical-history
  samples.)

**Check/mitigation**: assign a unique `X-Moralstack-Conversation-Id` header (or
`extra_body.moralstack_conversation_id`) per sample. This bypasses lineage
hashing (`proxy.py:218-219`).

### 4.2 Concurrency
- Same conversation_id is serialized (30s lock acquire timeout → 503 +
  `Retry-After: 10`) (`proxy.py:87-110,236-242`).
- Must run **one** uvicorn worker; multiple workers split the session store and
  lock namespace and break continuity (`examples/server_quickstart.py:16-21`).
- High parallelism across colliding conversation_ids degrades to serial
  execution and can 503 under contention.

### 4.3 Retries
A client retry resends an identical body → identical history hash → same
conversation_id and same stateless `turn_index`. `ProcessedRequest.request_id` is
a fresh `uuid4` per instance (`types.py:196`) and the proxy builds a new
`ProcessedRequest` per HTTP call, so a retry creates a **second** `requests` row
at the same `(conversation_id, turn_index)` (`proxy.py:526-541`). Retries are not
deduplicated — duplicate turn rows can distort benchmark accounting.

### 4.4 Cache (ledger) reuse
On a same-conversation hit, `ConversationalFastPathRunner.is_safe_to_apply` gates
reuse: cached REFUSE always applied, ESCALATED never cached, `turn_index < 1`
skipped (`controller.py:2194-2306`). A wrong collision (4.1) could cause reuse
across logically distinct samples. The P0 hard-signal supremacy invariant still
holds because `is_hard_signal_refuse` is re-evaluated after a cache patch
(`controller.py:2209`).

### 4.5 Endpoint selection
COMPL-AI / IFBench runs should point at the production proxy launched via
`examples/server_quickstart.py`. Custom single-turn launchers that extract only
the latest user message are not suitable for `llm_rules` / multi-turn benchmarks
because they bypass the proxy's `ConversationContext`, conversation_id, lock,
session-store, and ledger path.

### 4.6 Streaming
The proxy supports `stream=true` as governed synthetic SSE replay: governance
runs to completion first, then the final governed text is emitted as
OpenAI-compatible `chat.completion.chunk` events. The proxy does not forward live
upstream tokens. For COMPL-AI / IFBench score extraction, prefer non-streaming
requests unless the benchmark harness explicitly consumes SSE.

## 5. Pre-run checklist

1. **Bridge**: confirm COMPL-AI's `base_url` targets the production proxy
   launched via `examples/server_quickstart.py` (recommended uvicorn port 8080;
   `main()` default 8787).
2. **Workers**: launch uvicorn with a single worker.
3. **Conversation identity**: prefer a unique `X-Moralstack-Conversation-Id` per
   sample to avoid lineage collisions (§4.1). If relying on lineage, confirm
   sample prefixes are actually distinct.
4. **Streaming**: prefer non-streaming requests for score extraction unless the
   harness explicitly consumes SSE (§4.6).
5. **Observability**: set `MORALSTACK_OBSERVABILITY_MODE=db_only` (or `dual`) and
   `MORALSTACK_OBSERVABILITY_DB_PATH` so the run is reconstructable and visible in
   `moralstack-ui` (file_only is invisible in the UI).
6. **Generation model**: `OPENAI_MODEL` controls upstream generation; the
   `model` field in the request body is a client alias and is overridden
   (`proxy.py:434,750-755`).
7. **DCCL**: confirm DCCL is enabled for contract-driven `llm_rules` samples
   (`compliance/config.get_dccl_enabled`) — without it, deployer-authorized rule
   execution will not take the compliance fast-path.
8. **Capacity**: size client parallelism against per-conversation serialization
   and the lock acquire timeout to avoid spurious 503s.
