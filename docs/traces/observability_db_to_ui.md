# TRACE — Observability: DB / filesystem → UI

What gets logged, where it lands, how it is read back, and what the dashboard
can reconstruct. Claims are grounded in the cited source. Gaps and conditional
behaviors are collected in §8.

Primary code: `moralstack/observability/*`, `moralstack/persistence/*`,
`moralstack/ui/app.py`, `moralstack/reports/*`.

---

## 1. Emission

- Singleton `ObservabilityService` via `get_obs()` / `obs`
  (`observability/service.py:80-87`).
- `obs.emit(envelope)` / `emit_batch(...)` are **async fire-and-forget**: the
  envelope is submitted to a background `ObservabilityWriteQueue` that calls
  `router.route` with a captured contextvars snapshot (`service.py:44-52`).
- `obs.flush(timeout)` blocks until pending writes drain — called at the request
  boundary (SDK: `wrapper.py:281`; proxy: `proxy.py:703`).
- Context is carried via contextvars: `run_id`, `request_id`, `session_id`,
  `turn_number` (`observability/context.py`).

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
| `llm_calls` | every LLM call: module, action, model, prompt, system_prompt, raw_response, parsed/summary JSON, token usage, cycle, sequence, call_kind/outcome, cache_status |
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
(`observability/governance_audit.py`) merges meta and writes `final_response`.

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

## 4. What is logged to the filesystem (JSONL)

`JsonlEventSink` (`observability/sinks/jsonl_sink.py:77-95`) writes **one file per
event_type** — `{jsonl_dir}/{event_type}.jsonl` — appending one line per event,
where each line is `envelope.to_dict()` (the full `EventEnvelope`). Active in
`file_only` and `dual`; per-file locks prevent interleaving; writes are
synchronous (`flush`/`close` are no-ops). `scripts/consolidate_jsonl_meta.py`
post-processes JSONL meta.

**JSONL vs. SQLite shape**: both sinks consume the *same* `EventEnvelope` via
`router.route`, so they carry the same information, but the shape differs — JSONL
stores the raw envelope dict grouped into per-event-type files, while SQLite
decomposes the envelope into typed columns across the 11 tables. They are not a
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
- **Reconstruction completeness depends on flush.** A process killed before
  `flush()` may drop queued envelopes; the SDK/proxy flush at the boundary to
  minimize this, but a hard crash mid-turn can truncate a turn's evidence.
