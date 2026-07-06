# TRACE — Observability: DB / filesystem → UI

What gets logged, where it lands, how it is read back, and what the dashboard
can reconstruct. Claims are grounded in the cited source. Gaps and conditional
behaviors are collected in §8.

Primary code: `moralstack/observability/*`, `moralstack/orchestration/default_persistence.py`,
`moralstack/ui/app.py`, `moralstack/reports/*`.

---

## 1. Emission

- Singleton `ObservabilityService` via `get_obs()` / `obs`
  (`observability/service.py:80-87`).
- `obs.emit(envelope)` / `emit_batch(...)` are **async fire-and-forget**: the
  envelope is submitted to a background `ObservabilityWriteQueue`. The worker
  drains FK-ordered windows through `router.route_window(...)` and owns a
  persistent SQLite connection with `PRAGMA synchronous=NORMAL` scoped to that
  worker connection only (`service.py`, `write_queue.py`, `router.py`).
- `obs.flush(timeout)` blocks until pending writes drain. The SDK wrapper
  calls it at the request boundary (`wrapper.py:281`). **The proxy does not
  flush per-request**: under burst load the queue grew faster than the
  single-writer drained it, so the bounded flush timed out on every call
  without delivering visibility while adding ~5s overhead per response.
  Drainage on the proxy side is the worker's job during the process lifetime
  and a FastAPI `shutdown` hook (`obs.shutdown(timeout=30.0)`) on exit. Tests
  or consumers that need synchronous visibility against the proxy must call
  `obs.flush(...)` explicitly before reading.
- Context is carried via contextvars: `run_id`, `request_id`, `session_id`,
  `turn_number` (`observability/context.py`).
- High-frequency producers, including the risk estimator mini-calls, enqueue
  through `obs.emit(...)` / `emit_batch(...)`; request-thread
  `router.route(...)` / `route_batch(...)` is not used for that telemetry.
- Audit-critical finalization is the synchronous exception:
  `conversation_events.finalize_audit_sync(...)` writes `request.meta_updated`
  and exactly-one `proxy.request_finalized` via resultful
  `router.route_audit_sync(...)`.

## 2. Routing by mode (`observability/router.py:37-54`)

`MORALSTACK_OBSERVABILITY_MODE` ∈ `{file_only (default), db_only, dual}`:
- `db_only` → `SqliteEventSink` only.
- `file_only` → `JsonlEventSink` only.
- `dual` → both.

> **SDK config caveat.** `GovernanceConfig.observability_mode` (default `"off"`)
> is not wired into the runtime `get_observability_mode()` function
> (`sdk/config.py:58`; `observability/config.py:64-77`). The authoritative
> source is the `MORALSTACK_OBSERVABILITY_MODE` environment variable; the
> `"off"` SDK value has no runtime effect. Default when env var is unset:
> `db_only` if `MORALSTACK_OBSERVABILITY_DB_PATH` is set, else `file_only`.

DB path from `MORALSTACK_OBSERVABILITY_DB_PATH` (legacy alias
`MORALSTACK_DB_PATH`); JSONL dir from `MORALSTACK_OBSERVABILITY_JSONL_DIR`
(default `logs/observability`).

## 3. What is logged to the DB (SQLite)

Schema in `observability/sinks/sqlite_sink.py:48-489`; connection uses WAL +
`foreign_keys=ON` (`:497-504`). Tables:

| Table | Holds |
|---|---|
| `runs` | one row per run (`run_type`: sdk_session / proxy / single / benchmark…) |
| `requests` | per request: prompt, domain, `final_response`, merged `meta_json`; PK `(run_id, request_id)` |
| `llm_calls` | every LLM call: module, action, model, prompt, system_prompt, raw_response, parsed/summary JSON, token usage, cycle, sequence, call_kind/outcome, cache_status, `billable_provider_call` |
| `request_token_usage` | synchronous best-effort per-request token summary (one row per `run_id`/`request_id`); not authoritative — see token accounting notes below |
| `orchestration_events` | pipeline events (speculative, compliance, ledger fast-path, conversation, proxy output finalized) |
| `decision_traces` | stage snapshots (`RISK_ASSESSMENT`, `COMPLIANCE_LAYER`, `DELIBERATION_AGGREGATE`, `RELEVANT_PRINCIPLES`, `FINAL`, …) as `trace_json` |
| `debug_events` | low-level diagnostic payloads |
| `exports_cache` | cached markdown exports |
| `conversation_states` | per-turn governance state in/out, posture, final_action, risk, was_cached |
| `ledger_events` | ledger lookup/store ops: operation, outcome, similarity, from_turn, posture, intent_clarity |
| `session_store_events` | session store get/put |
| `proxy_request_events` | per-turn proxy summary: action, risk, path, posture in/out, headers, response length |

Most child tables FK to `requests(run_id, request_id)` with
`ON DELETE CASCADE` — so the `requests` row must exist first (the controller and
proxy both pre-insert it).

Writers: `init_db`, `create_run`, `upsert_request`, `update_request_response`,
`update_request_domain`, `update_request_meta`, `delete_request`, `delete_run`
(`sqlite_sink.py:611+`). Shared finalization: `finalize_governance_audit`
(`observability/governance_audit.py`) writes `final_response`/domain, while
`finalize_audit_sync` emits the synchronous meta/proxy audit envelopes.

### Token usage (proxy `usage` field and DB)

- **Synchronous path**: `ObservabilityService.emit()` accumulates billable
  `llm.call` envelopes in-process (keyed by `(run_id, request_id)`). At request
  end, `controller._finalize_token_accounting` pops the accumulator and emits
  `request.token_usage_finalized`, populating `ResponseMetadata` token fields and
  the proxy/SDK `usage` payload.
- **`request_token_usage` row**: best-effort summary at finalization time. It may
  be partial (late speculative discards, queue drops) and must not be treated as
  a canonical total.
- **Offline reconstruction**: `SUM(input_tokens/output_tokens/total_tokens) FROM
  llm_calls WHERE … AND COALESCE(billable_provider_call,1)=1` is the most complete
  view among rows actually written to SQLite — still not a completeness guarantee
  because the async queue may drop envelopes before they reach the DB.
- **UI**: `ReadStore.get_token_usage_totals`/`get_token_usage_breakdown` and the
  per-model aggregations `get_token_usage_by_model_{global,for_run,for_request,
  for_conversation}` all filter non-billable diagnostic rows the same way as the
  SQL sums. `moralstack/ui/app.py` wires the per-model views into four scopes via
  the shared `_token_usage_view()` helper and the `templates/_token_usage.html`
  partial: the dashboard (`/runs`, all runs), the run detail (`/runs/{id}`), the
  conversation detail (`/conversations/{id}`, joined through `requests.conversation_id`),
  and the single-question detail (`/runs/{id}/requests/{id}`). Each panel shows
  tokens per model plus `estimated`/`missing` quality badges; the request panel
  also surfaces `usage_may_be_incomplete` from `request_token_usage`.

### Context-shape fields

The multi-turn alignment layer emits `CONTEXT_SHAPE_RECORDED` orchestration
events for LLM-using modules. The payload records:

- `context_mode`
- raw/system/developer message counts
- available and used prior user/assistant turns
- `history_truncation` and `history_truncated_count`
- `contains_full_native_messages`
- `developer_contract_included`
- `final_user_included`
- `history_source`
- delivery guard fields such as `delivery_context_broader_than_governance`,
  `mismatch_guard_action`, `governance_context_mode`, `candidate_context_mode`,
  and `prior_turn_count` where applicable

These fields are queryable from SQLite `orchestration_events.payload_json` and
from the corresponding JSONL envelope. They are also folded into existing
request/proxy metadata where a result is finalized. There is intentionally no
SQLite migration for dedicated typed columns in this version, and the UI does
not render a dedicated context-shape panel yet.

### `system_prompt`/`prompt` after the prompt-caching reorder (Part A, 2026-07-06)

The `llm_calls.system_prompt`/`prompt` columns (and the equivalent
`parsed_summary_json`/persistence payloads for the risk estimator and each
deliberative module) now reflect the reordered static/dynamic split so the
audit trail still shows the full prompt actually sent:
- **Risk minis** (`models/risk/estimator.py:888-924`): `system_prompt` is the
  path-specific `*_SYSTEM_PROMPT` (intent/signals/operational); `prompt` is
  dynamic-only.
- **Critic**, **Simulator**, **Hindsight**, **Perspectives** (persisted by
  `orchestration/deliberation_runner.py` from each module's `CriticReport` /
  `SimulationResult` / `HindsightResult` / `PerspectiveResult`.`system_prompt`
  field): each now carries the path-specific static constant actually sent
  (e.g. `CRITIC_FULL_SYSTEM_PROMPT` vs the unchanged quick-check
  `CRITIC_SYSTEM_PROMPT`; `SIMULATOR_BATCH_SYSTEM_PROMPT` vs
  `SIMULATOR_SEEDED_SYSTEM_PROMPT`; `HINDSIGHT_SINGLE_SYSTEM_PROMPT` — also
  used by the non-batch aggregate `_evaluate_individual` result — vs
  `HINDSIGHT_BATCH_SYSTEM_PROMPT`). No new columns or schema change; only the
  string content differs from pre-reorder. See `docs/MORALSTACK_CODEBASE_INDEX.md`
  §5.1 and `tests/test_static_prefix_stability.py` (observability split
  assertions).

## 4. What is logged to the filesystem (JSONL)

`JsonlEventSink` (`observability/sinks/jsonl_sink.py:77-95`) writes **one file per
event_type** — `{jsonl_dir}/{event_type}.jsonl` — appending one line per event,
where each line is `envelope.to_dict()` (the full `EventEnvelope`). Active in
`file_only` and `dual`; per-file locks prevent interleaving; writes are
synchronous attempts (`flush`/`close` are no-ops). In `dual`, SQLite drives the
route's persisted/failed identity and JSONL failures are counted separately; in
`file_only`, JSONL drives the result but is not crash-durable.
`scripts/consolidate_jsonl_meta.py`
post-processes JSONL meta.

**JSONL vs. SQLite shape**: both sinks consume the *same* `EventEnvelope` via
`router.route`, so they carry the same information, but the shape differs — JSONL
stores the raw envelope dict grouped into per-event-type files, while SQLite
decomposes the envelope into typed columns across the 12 tables. They are not a
column-for-column mirror.

## 5. How logs are retrieved

`SqliteReadStore` (`observability/read_store.py`) is the single read contract.
Per-request accessors: `get_request`, `get_llm_calls_for_request`,
`get_orchestration_events_for_request`, `get_decision_traces_for_request`,
`get_debug_events_for_request`. Per-conversation accessors:
`get_requests_for_conversation` (ordered by `turn_index`),
`get_conversation_states`, `get_ledger_events_for_conversation`,
`get_session_store_events_for_conversation`,
`get_proxy_request_events_for_conversation`, `get_conversation_overview`,
`get_conversation_ids_for_run` (`read_store.py:53-97,229-564`).

`llm_calls` are ordered by `(cycle, sequence_in_cycle, started_at, phase)`
(`read_store.py:276-282`) so the UI can rebuild execution order without relying
on wall-clock alone.

## 6. What the UI displays (`moralstack/ui/app.py`)

The dashboard reads **only** from SQLite (`get_db_path()` required;
`_ReadStoreProxy` resolves the read store at call time, `ui/app.py:58-94`). It
reconstructs:
- **Per request** (`/runs/{run_id}/requests/{request_id}`, `ui/app.py:1931`): the
  deliberation timeline / "metro map" — calls grouped into visual tiers
  (`_group_calls_into_tiers_and_enrich`), risk mini-estimator breakdown, a
  synthetic calibration node (`_build_synthetic_calibration_node`), a synthetic
  path-routing node, the final-decision card (`_build_final_decision_card`),
  relevant/triggered principles, and a DCCL/compliance card.
- **Per conversation** (`/conversations/{conversation_id}`, `ui/app.py:2143`):
  full multi-turn timeline via `_build_conversation_timeline`; 404 if no requests.
- **Markdown exports**: per-request, per-run benchmark, and per-conversation
  AI Act art. 12 audit (`/conversations/{id}/export.md` →
  `reports/conversation_export.export_conversation_to_markdown`).

## 7. Can full conversations be reconstructed?

Yes, **when persistence is to the DB** (`db_only`/`dual`):
- `requests` rows carry the prompt and the `final_response` per turn;
- `conversation_states` carry posture/state transitions per turn;
- `ledger_events` / `session_store_events` / `proxy_request_events` carry the
  cache and proxy decisions;
- `conversation_export.py` stitches these into a complete per-turn audit trail
  (prompts, decisions, responses, rationale, posture evolution, evidence counts)
  (`reports/conversation_export.py:1-26`).

## 8. Gaps / missing fields

- **`file_only` runs are invisible in the UI.** The dashboard reads SQLite only;
  JSONL-only runs produce no dashboard views (`ui/app.py:2147-2148`).
- **Proxy assistant text vs. governed content.** For streaming SDK SAFE/NORMAL
  paths the audit `final_response` is recorded empty (the body is consumed by the
  caller) (`wrapper.py:358-366,386-391`).
- **Lineage-collided conversations merge in the DB.** If two samples share a
  lineage-derived conversation_id (see the multi-turn trace), their turns land
  under one conversation_id and cannot be separated after the fact.
- **JSONL is not table-shaped.** Reconstructing a conversation from JSONL means
  joining across per-event-type files on `request_id`/`conversation_id` yourself;
  the UI and `conversation_export` only consume SQLite (§4, §6).
- **Context-shape telemetry is payload JSON.** It is present in
  `orchestration_events` / JSONL payloads and request metadata, not in dedicated
  typed SQLite columns or a UI panel.
- **Reconstruction completeness depends on flush/shutdown for high-frequency
  telemetry.** A process killed before `flush()` / `shutdown()` may lose the last
  queued telemetry window. Lifecycle upserts and decision-audit finalization are
  synchronous; `file_only` finalization is synchronously attempted but not
  crash-durable because JSONL has no fsync contract.
