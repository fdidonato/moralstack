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
  calls it at the request boundary (`wrapper.py:306`). **The proxy does not
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

> **SDK config precedence.** The getters in `observability/config.py` resolve
> explicit `GovernanceConfig` values (`observability_mode`, `db_path`, `jsonl_dir`,
> applied by `apply_explicit_config` from `govern()` / `GovernedClient.__init__` /
> `create_app`) > env vars > defaults. `"off"` / blank / `None` mean "not set here"
> (inherit env/defaults; writing is not disabled). Explicit values are process-wide and
> only *establish* the target: they apply only if the first `apply_explicit_config` call
> happens before the write-queue target is bound (`ObservabilityWriteQueue.target_bound`:
> first enqueue or worker connection open). A later different value is refused with a
> WARNING, because the worker keeps one SQLite connection for its lifetime while parent
> rows are written synchronously to `get_db_path()`; a redirect would split parents and
> children across databases. A separate `moralstack-ui` process reads env only.
> Default when nothing is set: `db_only` if a DB path (explicit or env) is set, else `file_only`.

DB path from `MORALSTACK_OBSERVABILITY_DB_PATH` (legacy alias
`MORALSTACK_DB_PATH`); JSONL dir from `MORALSTACK_OBSERVABILITY_JSONL_DIR`
(default `logs/observability`).

## 3. What is logged to the DB (SQLite)

Schema in `observability/sinks/sqlite_sink.py:48-489`; connection uses WAL +
`foreign_keys=ON` (`_get_connection`, `:577-585`; the rollback->WAL switch of a new file is serialized per file by `_wal_switch_lock`, `:546-574`, within the process only). Tables:

| Table | Holds |
|---|---|
| `runs` | one row per run (`run_type`: sdk_session / proxy / single / benchmark / session…); `meta_json.prompt_config` holds the prompt-set audit summary (A4); in `file_only`/`dual` the same meta is also one line of `run.started.jsonl` |
| `requests` | per request: prompt, domain, `final_response`, merged `meta_json` (incl. the `prompt_set` link `{ps, is_default}` added by the controller right after the upsert); PK `(run_id, request_id)` |
| `llm_calls` | every LLM call: module, action, model, prompt, system_prompt, raw_response, parsed/summary JSON, token usage, cycle, sequence, call_kind/outcome, cache_status, `billable_provider_call` |
| `request_token_usage` | synchronous best-effort per-request token summary (one row per `run_id`/`request_id`); not authoritative — see token accounting notes below |
| `orchestration_events` | pipeline events (speculative, compliance, ledger fast-path, conversation, proxy output finalized, `PIPELINE_FAILED` for handler / generic-except failures) |
| `decision_traces` | stage snapshots (`RISK_ASSESSMENT`, `COMPLIANCE_LAYER`, `DELIBERATION_AGGREGATE`, `RELEVANT_PRINCIPLES`, `FINAL`, …) as `trace_json` (a blob, not typed columns — new trace fields are additive and need no migration, but old rows lack the key) |
| `debug_events` | low-level diagnostic payloads (the generic-`except` `H-except`, ledger-store-failure `H-ledger-store-fail` and hindsight-failure `H-hindsight-path` rows carry `error_type` only, never exception text; other debug rows may still carry text, and rows written before A1 keep the old text) |
| `exports_cache` | cached markdown exports |
| `conversation_states` | per-turn governance state in/out, posture, final_action, risk, was_cached |
| `ledger_events` | ledger lookup/store ops: operation, outcome, similarity, from_turn, posture, intent_clarity |
| `session_store_events` | session store get/put |
| `proxy_request_events` | per-turn proxy summary: action, risk, path, posture in/out, headers, response length |

Most child tables FK to `requests(run_id, request_id)` with
`ON DELETE CASCADE` — so the `requests` row must exist first (the controller and
proxy both pre-insert it). A missing `requests` row is therefore not a partial
loss: every child insert for that request fails the FK and is swallowed, so the
request vanishes from SQLite while JSONL still holds it in full.

`requests` itself FKs to `runs(run_id)`, so the parent run row must exist before
the pre-insert. The proxy creates it when the app is built
(`_initialize_observability_run`, `server/proxy.py`), but the observability DB
path can be re-pointed **after** the app exists — `build_app()` calls
`load_env()` with `override=True`, so a harness isolating one database per run
has to re-assert `MORALSTACK_OBSERVABILITY_DB_PATH` afterwards. The pre-insert
therefore re-ensures schema + run row against the path actually being written
(`_ensure_run_row`, once per `(db_path, run_id)`); without it the proxy writes
into a database that has no parent row and drops every request that reaches it
before the controller's `DefaultPersistence.ensure_run_and_upsert_request`
happens to create one.

Writers: `init_db`, `create_run`, `upsert_request`, `update_request_response`,
`update_request_domain`, `update_request_meta`, `delete_request`, `delete_run`
(`sqlite_sink.py:611+`). Shared finalization: `finalize_governance_audit`
(`observability/governance_audit.py`) writes `final_response`/domain, while
`finalize_audit_sync` emits the synchronous meta/proxy audit envelopes.

**Constitution retrieval events (unify-constitution-retrieval-single-pass).**
`RELEVANT_PRINCIPLES_RETRIEVED` is emitted exactly once per request: by the
controller (`orchestration/controller.py:_emit_relevant_principles_retrieved`,
`payload.source="controller"`) when the single risk-owned wave succeeds, or by
`DeliberationRunner._record_retrieval_start_and_event`
(`payload.source="deliberation_runner"`) only on its own fallback retrieval
(risk-owned retrieval unavailable) — never both. `RELEVANT_PRINCIPLES_REUSED` is
still emitted per consumer reuse (e.g. the critic). The `llm_calls` rows for the
constitution domain-prefilter and per-domain agents carry
`parsed_summary_json.retrieval_phase`: `"risk_routing"` for the single risk-owned
wave (default), `"deliberation_retrieval"` for any fallback wave
(`constitution/retriever.py:_persist_constitution_llm_call`, threaded through
both `EnhancedDomainAgent` and legacy `DomainAgent`).
`DOMAIN_PREFILTER_DOMAINS_REJECTED` (prefilter cache miss only; `reason_codes` = sorted unique reasons, payload lists
the rejected domains) renders as a generic Runtime Decisions row with the reasons in the "reason" column
(`reports/runtime_decisions.py:orchestration_event_to_row`); it is not counted as reuse or invalidation by
`build_retrieval_reuse_summary`.

**`retrieval_metadata` / `domain_channel` provenance (retrieval-request-scoped-state,
P0 fix).** `RiskEstimation.retrieval_metadata` and the `REQUEST_ANALYSIS_CONTEXT`
`stage_payload` (persisted in `decision_traces`) both derive from
`ConstitutionRetriever.retrieve()`'s per-call `PrincipleRetrievalResult.debug_info` —
never from a shared instance attribute read back after the fact. Both payloads carry
an additive `domain_channel` key: `"retrieve"` on the normal path, or
`"fallback_no_retrieve"` when the configured `constitution_store` does not implement
`retrieve()` (a rate-limited WARNING is also logged once per process in that case).
Without the `domain_channel` line in the `REQUEST_ANALYSIS_CONTEXT` `stage_payload`
builder, the marker would only exist in-memory and never reach this table — every
persisted payload here is built key-by-key from an allowlist, `retrieval_metadata`
is never serialized wholesale. `reports/runtime_decisions.py` reads both payloads
entirely through `.get(...)`, so the additive key is safe for older rows that lack it.

### Token usage (proxy `usage` field and DB)

- **Synchronous path**: `ObservabilityService.emit()` accumulates billable
  `llm.call` envelopes in-process (keyed by `(run_id, request_id)`). At request
  end, `controller._finalize_token_accounting` pops the accumulator and emits
  `request.token_usage_finalized`, populating `ResponseMetadata` token fields and
  the proxy/SDK `usage` payload. The in-process billable gate
  (`service._counts_as_billable`) mirrors SQL `COALESCE(billable_provider_call, 1) = 1`:
  a falsy flag excludes the row; `True`, `None` or an absent key counts it (the three
  risk mini-estimator rows carry `None`).
- **Comparability**: `request_token_usage` and the proxy `usage` are unaffected by the
  mini-row gap before `5439aaf` (2026-07-18), miss the three mini calls from `5439aaf` to the
  2026-10-13 fix, and include them after it (best-effort throughout); compare within one
  regime only.
- **`request_token_usage` row**: best-effort summary at finalization time. It may
  be partial (late speculative discards, queue drops) and must not be treated as
  a canonical total.
- **Offline reconstruction**: `SUM(input_tokens/output_tokens/total_tokens) FROM
  llm_calls WHERE … AND COALESCE(billable_provider_call,1)=1` is the most complete
  view among rows actually written to SQLite — still not a completeness guarantee
  because the async queue may drop envelopes before they reach the DB.
- **Per-model attribution**: the per-model token panel groups by
  `COALESCE(NULLIF(TRIM(model),''),'')`, so any billable row written without a `model`
  collapses into a single `'—'` (empty-key) row. Every provider call must therefore
  record its generating model — including the fast-path/benign/safe-complete
  `generate (...)`, the REFUSE (`refuse (fast_path)` / `refuse (deliberative)`),
  compliance-regenerate, `draft_revalidate`, the domain-exclusion message call
  (`orchestration` / `domain_excluded`, billable, counted in both the accumulator and the SQL reconstruction),
  and the critic/simulator/hindsight/
  perspectives/risk_estimator `retry_failed_attempt_*` rows, which each pass the policy/DCCL model
  explicitly. Synthetic rows that are *not* provider calls (speculative reuse,
  `calibration_guard`, module skip/gate/error/timeout and leakage markers) are marked
  `billable_provider_call=False` (or otherwise carry no tokens) and are excluded from the
  panel instead.
- **Wire record (A4b)**: rows of chat-completion calls carry the exact messages sent as
  `parsed_summary_json.wire` (single call) or `wire_calls` + `wire_strings` (aggregated rows and refusal
  attempts), and `parsed_json.wire` for critic `critique` rows; refs point at the row's own `prompt` /
  `system_prompt` columns. See `docs/modules/observability.md` for the decoding rules.
- **Cached input tokens**: `llm_calls.cached_input_tokens` (nullable) carries the
  provider-reported prompt-cache hit for that call. `NULL` = the provider reported no
  cache details (this includes every row written before the column existed);
  `0` = a measured cache miss. `get_token_usage_breakdown` returns `cached_input_tokens`
  (`SUM`, NULL-skipping) and `cached_usage_known` (`COUNT` of non-NULL) so the two are
  never conflated; it guards the `SELECT` with `_column_exists` so a pre-migration DB
  still yields a breakdown instead of failing. Cached tokens are billed at a reduced
  rate — they do not reduce token counts. See `docs/modules/observability.md`.
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
- **Risk minis** (`models/risk/estimator.py:839-845,1019-1061`): `system_prompt` is the
  path-specific `*_SYSTEM_PROMPT` (intent/signals/operational); `prompt` is
  dynamic-only.
- **Critic**, **Simulator**, **Hindsight**, **Perspectives** (persisted by
  `orchestration/deliberation_runner.py` from each module's `CriticReport` /
  `SimulationResult` / `HindsightResult` / `PerspectiveResult`.`system_prompt`
  field): each now carries the path-specific static constant actually sent
  (e.g. `CRITIC_FULL_SYSTEM_PROMPT` vs the unchanged quick-check
  `CRITIC_SYSTEM_PROMPT`; `SIMULATOR_BATCH_SYSTEM_PROMPT`;
  `HINDSIGHT_SINGLE_SYSTEM_PROMPT` — also
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
(`read_store.py:318-320`) so the UI can rebuild execution order without relying
on wall-clock alone. The execution graph's tiers follow the same key
(`ui/app.py:_group_calls_into_tiers_and_enrich`): a node without
`sequence_in_cycle` sinks to the bottom of its cycle, which is why the synthetic
calibration / path-routing nodes carry `-7` / `-2` and the hard-violation
delivery guard's rows carry `7` / `8` (`deliberation_runner.py:242-243`). Guard
rows persisted before 2026-09-18 (regeneration as cycle 0 / `SEQ_POLICY` /
`generate (safe_complete_path)`, re-critique as `SEQ_CRITIC`) are re-homed in
memory by `_rehome_legacy_hard_violation_guard_calls` on the request page only —
the markdown export (`reports/model.py`) still sorts cycle 0 by wall-clock and
shows those legacy rows where they were persisted.

Rows that are written from outside a deliberation cycle must carry their own coordinates, or they land in a
NULL-cycle group of the graph. The risk estimator's `retry_failed` rows carry `cycle=0` / `sequence_in_cycle=-9`
(the mini rows' tier), and the fast-path `quick_check` row carries `cycle=0` / `sequence_in_cycle=2`, which
`_visual_tier_for_call` ranks in the quick-check tier (after the fast draft; unmapped, seq 2 would collapse into the
calibration-guard tier and sort before the draft). The perspectives and hindsight `retry_failed` rows are written
inside a deliberation cycle with the cycle ContextVar and no sequence, so the request page infers their tier from
the module. A failed attempt of a module that runs ALONE in its tier (critic, hindsight) is NOT drawn as a "parallel"
sibling of the call it retried: `_group_calls_into_tiers_and_enrich` gives each one its own sequential tier directly before
the retried call's tier and the pipe into the retried call reads `retry after failed attempt`. Modules that ran concurrently
with siblings keep their failed attempts in the shared tier (the risk minis; simulator and perspectives, which share tier 3):
drawing those as a chain would be a causal falsehood. In every case the `retry_failed` badge uses the failure style
(`badge-fail`) and the node names who failed (`failed: <mini>` / `failed: perspective <name>` / `failed: scenario <id>` /
`failed: simulator batch`, from the additive `parsed_summary_json` keys). `retry_failed` rows are
ignored by `_tier_started_after` (their `started_at` is the emit time, zero duration), so they cannot hide the real
`gate: proceed` label of the next tier. The `quick_check` tier is keyed on the row's action, not on cycle 0 / seq 2 alone, because
the synthetic "critic deferred to compliance" marker also sits at cycle 0 / seq 2 and keeps its own tier. A `quick_check` with
no verdict renders `quick check: no verdict (<error type>)`. Tests: `tests/test_ui_llm_call_visibility*.py`.

**DCCL coordinates and cycle-0 order** (`tests/test_ui_dccl_ordering.py`). DCCL `llm_calls` rows
(`module=compliance_layer`: evaluate seq -5, safety_override seq -6) were persisted with `cycle=NULL`
(`compliance/dccl.py`); since 2026-10-21 they carry `cycle=0` (two literals, `dccl.py` safety_override and evaluate
`record_llm_call` blocks). Rows written before that are normalized at read time by
`_normalize_dccl_legacy_cycles` (called in `request_detail` right after `_rehome_legacy_hard_violation_guard_calls`;
compliance rows with `cycle None` only, flagged `_cycle_normalized` and badged `legacy row: cycle NULL shown as 0`
in the graph and `(legacy cycle NULL)` in the journey). The normalization is deliberately NOT generic: any other
remaining NULL-cycle row stays in its own group, labelled "Unsequenced rows (cycle NULL)" instead of "Initial
assessment", so a future coordinate bug stays visible. `read_store` ordering (`:318-320`) and the markdown export are
unchanged (the raw list there still shows -6 before -5 for new rows; only the no-graph fallback list on the page
shows raw order).

Cycle-0 tier order (`_visual_tier_for_call`; named `_T_*` constants, relative order only, never assert the numbers):
risk-routing `domain_prefilter` (-10) -> its `*domain_agent` rows (-10, parallel among themselves) -> risk minis
(-9) -> calibration guard (-8) -> calibration (-7, synthetic) -> DCCL evaluate
(-5) -> DCCL safety_override (-6) -> compliance regenerate (seq 1, `call_kind=compliance_regenerate` /
`compliance-regenerate` in the action) -> draft revalidate (-4 real row) -> hard-signal gate / ordinary downgrade
(synthetic, phase `safety_gate` / `match_downgraded`; the hard-signal gate never coexists with a
regenerate / revalidate row; an ordinary downgrade has a -4 revalidate row only when the semantic-LLM revalidation
ran, and no -4 row on the substring / early-return branch, `controller.py:1471-1476`, `:1503-1519`) -> deferred markers (phase `deferred_to_compliance` OR action
`skipped - contract authorized`, one tier labelled `skipped · N modules`) -> path routing (-2, synthetic) -> ledger
fast-path (-3, synthetic) -> deliberation retrieval prefilter (-1) -> its agents (-1) -> route draft (seq 1) ->
`quick_check` (action) -> terminal (seq 6: fast-path refusal, domain exclusion). Unmapped cycle-0 sequences land in a
tail (`100 + seq`), never colliding with a mapped step. Precedence (documented once in the function docstring):
quick_check action, deferred marker, gate/downgrade phase, compliance regenerate, -10/-1 action split, static table,
tail. **Speculative draft placement (RV12):** the seq-0 speculative draft (cycle 0; policy or `upstream_speculative`; never a `speculative-reuse` row; only rows whose raw cycle is 0, a NULL-cycle row never counts) joins the risk/retrieval tier (prefilter, agents, minis) with which its `[started_at, started_at+duration_ms]` window overlaps most, measured against the union window of that tier's rows in the same request; tie -> earliest tier; missing/zero timing or no such rows -> the minis tier. `_stamp_speculative_tier_overrides` stamps `_visual_tier_override` (pure, idempotent, never raises; run by the grouping and by `_journey_steps`, which `request_detail` uses as the single sort entry), so graph and journey share one source of truth (request 1166f216: drawn and listed with the prefilter, before the agents). **Connector labels into compliance nodes (RV13, `_compliance_connector_label`):** evaluate -> `risk + draft → DCCL`; safety_override after evaluate -> `verdict → safety check` (on the structured path, after calibration/risk -> `risk + draft → DCCL`); synthetic gate (phase `safety_gate`) -> `hard-signal gate (P0)`; ordinary downgrade (phase `match_downgraded`) -> `MATCH downgraded`; regenerated-draft revalidation (action `draft_revalidate`) -> `regenerated draft → revalidate`. The journey key `_journey_sort_key` delegates to `_visual_tier_for_call`, so journey order equals the flattened
graph tiers (cycles >= 1 unchanged: tiers are monotone in seq). "Journey == graph" holds at tier level: inside one tier the graph orders by `id`/`started_at`, the journey by `seq` first. Journey "Parallel" is time-truthful and tolerates
`_PARALLEL_OVERLAP_TOLERANCE_MS` = 1 ms (wall-clock int `started_at` vs float `perf_counter` duration), so it can
differ from the graph tiers where the speculative draft (DCCL does not await it, `controller.py:1282-1285`) shares a tier with rows it did not wait for. The
graph connector `_tier_started_after` (`gate: proceed`, cycles >= 1) stays strict and unchanged (known difference).
The prefilter (and other cycle-0 rows) can therefore legitimately read "Parallel" in the journey when the
speculative draft (seq 0, not awaited) overlaps it in time: journey Parallel is time-truthful, graph tiers follow
execution dependencies.
The execution timeline lists `compliance_layer` between `risk_estimator` and `orchestrator`
(`_TIMELINE_MODULE_ORDER`), and the path-routing synthetic node's placement time is anchored after the last cycle-0
compliance / compliance-regenerate row and after the synthetic gate / downgrade nodes (built first, passed as
`extra_anchor_nodes`).

## 6. What the UI displays (`moralstack/ui/app.py`)

The dashboard reads **only** from SQLite (`get_db_path()` required;
`_ReadStoreProxy` resolves the read store at call time, `ui/app.py:58-95`). It
reconstructs:
- **Per request** (`/runs/{run_id}/requests/{request_id}`, `ui/app.py:3542`,
  route `request_detail`): the deliberation timeline / "metro map" — calls
  grouped into visual tiers (`_group_calls_into_tiers_and_enrich`), risk
  mini-estimator breakdown, a synthetic calibration node
  (`_build_synthetic_calibration_node`), a synthetic path-routing node (whose
  `final_action`/`winning_rule` inputs are the DECISION_EXPLANATION in force at
  the branch — `decision_explanation_at_branch` — never the post-deliberation
  one), pipe labels that name the real hand-off (`_compute_connector_labels`:
  `gate: proceed` only when simulator/perspectives waited for the critic, the
  guard's `hard violation → regenerate under SAFE_COMPLETE` / `re-critique
  regenerated draft`), the
  final-decision card (`_build_final_decision_card` — now also returns
  `activated_signals`, `hard_violation_codes`; the OUTPUT anchor renders both,
  in addition to the pre-existing `Semantic Harm` truthiness gate — see
  `docs/CODEBASE_FACTS.md`), relevant/triggered principles, and a DCCL/
  compliance card. A **pipeline failure** (no FINAL decision-trace row plus a
  `SYSTEM.ERROR` / `SYSTEM.TIMEOUT` / `SYSTEM.FAIL_SAFE` entry in
  `meta_json.triggered_principles`) is detected by `_detect_pipeline_failure`, which
  delegates to `moralstack/reports/pipeline_failure.py::pipeline_failure_principle`
  (structured signals only, never the `[SYSTEM_*]` text). The request page and the
  conversation timeline flag it, and `_build_delivery_path_summary` plus the
  `request.html` caption name the recorded principle.
- **Per conversation** (`/conversations/{conversation_id}`, `ui/app.py:3823`,
  route `conversation_detail`): full multi-turn timeline via
  `_build_conversation_timeline`; 404 if no requests. Since the
  conversation-spine change, the per-turn loop inside
  `_build_conversation_timeline` also calls `_build_conversation_spine_node`
  (best-effort, one per-turn `get_orchestration_events_for_request` fetch
  wrapped in `try/except`) to populate `item["spine_node"]` — the input the
  conversation-level spine template renders (one node per turn: decisional
  input → decision → response outcome, plus a first-turn contract/history node
  and a terminal node folding the conversation's failure-aware aggregates).
  This replaces the previous horizontal "conversation strip".
- **Developer contract delivery (2026-10-21, `moralstack/ui/contract_delivery.py`, tests
  `tests/test_ui_contract_delivery.py`).** Each real `llm_calls` row that actually received the developer contract shows a
  "Developer contract" `<details>` (macro `contract_delivery_block` in `request.html`: channel label, coverage `full (N chars)`
  / `truncated: first n of N chars`, identity note, excerpt in `<pre>`) plus a `← developer_contract (<channel>)` io-row; a
  node that did not receive it shows nothing. Data-driven, never a per-module map. `_attach_contract_delivery`
  (`ui/app.py`, called in `request_detail` after the io-annotation loop; wrapped, `_LOG.debug`, degrades to "no section")
  runs `resolve_request_contract` once per request and `contract_delivery_for_call` per **real** row only (synthetic nodes
  are never annotated). *Canonical text*: candidates are the `system_messages` / `developer_messages` of each row's
  recorded `message_sections` (top level or `context_shape`); the one whose
  `DeveloperContract.from_text(text, mode).contract_hash` (raw or stripped, `opaque`/`structured`,
  `orchestration/contract.py:81-88`) equals a `COMPLIANCE_LAYER_VERDICT_*` `contract_hash` wins (`verified=True`); else
  exactly one distinct risk-estimator developer message (`verified=False`, "identity not verified"); else nothing is
  claimed. *Per row*: the wire record (`parsed_summary_json["wire"]`, aggregated `wire_calls` + `wire_strings`, or the
  critic's `parsed_json["wire"]`) is decoded with `decode_wire` (pure) and each message classified: native
  developer / system message (stripped body equals the contract, or is a prefix of >= `MIN_PARTIAL_CONTRACT_CHARS`=1000
  chars), embedded in a system / developer / user message (longest contract prefix contained; counts if the whole
  contract or >= 1000 chars), priority native > embedded system > developer > user; aggregated rows report
  `calls_with_contract / calls_total`. A row without any wire falls back to the `system_prompt` / `prompt` columns, then to
  its own recorded message sections (label says "role sent not recorded"); a row WITH a wire that does not carry the
  contract never falls back. Never claimed: `compliance_layer` `safety_override` / `draft_revalidate` (their prompts carry
  rule-action excerpts / the draft by construction), hydrated speculative-reuse rows, synthetic nodes; a malformed wire
  claims nothing. Residual (documented): a contract shorter than 1000 chars quoted in full inside a draft sent to a module
  reads "embedded in the user prompt" (literally true). Static io fixes (`_build_module_io_annotations`):
  `safety_override` -> `action_payload` only, `draft_revalidate` -> `draft` + `rule_action`, policy
  `generate (compliance-regenerate)` -> `rule_action` + `prompt`. The `<summary>` of the recorded request sections is now
  "Original request messages (client body)": those sections describe what the client sent, not what a module received
  (e.g. simulator rows record `system_messages` while the wire carries a developer message). Data exposure: the critic's
  `parsed_json` is read by the page for the first time, decoded only to find the contract; only the contract excerpt is
  rendered, never the full wire; the page stays behind `_require_session` and Jinja autoescape is on.
  *INPUT anchor (2026-10-22)*: `_attach_contract_delivery` returns the resolved request contract, passed to the template
  as `input_contract`; the INPUT node body shows it under "Full prompt" as a "Developer contract" `<details>`, open by
  default since 2026-10-27 (length, `sent by the client as role=system` when it was a system message, identity note, full
  text in `<pre>`), and the node header previews its first 300 chars (`.flow-node-contract-preview`). When
  `input_anchor_info.developer_contract_present` is true but nothing was resolved, a muted line says the text could not be
  identified; without a contract the node shows nothing. The header chip "developer contract" is lit by
  `input_contract` OR `input_anchor_info.developer_contract_present` (request page only), so chip and body agree even when
  no `COMPLIANCE_LAYER_STARTED` event was persisted; the conversation spine keeps the event-derived chip.
- **Node details (2026-10-27, tests `tests/test_ui_node_details.py`).** One macro, `call_details` in `request.html`, renders
  a call's evidence for both the execution-graph node and the flat "LLM Calls" list; the expanded "Result / Summary"
  block and the collapsed module node's result pill (both from `_call_result_preview`, now deleted with its
  `module_result` filter) were removed: a module's outputs stay on its `→` io-rows and in its Parsed Summary; the INPUT
  anchor keeps its prompt pill. Each section renders only
  when it has content (Prompt, System Prompt, Raw Response; "Conversation History" only with prior turns).
  "Original request messages (client body)" leads with the user request: `message_sections.final_user_message`, or
  `req.prompt` when that row's `prompt` contains it verbatim (the risk estimators record an empty final user message
  because they send the request inside their module prompt; across the local DB no recorded row lacked both), with a
  note saying so; otherwise "No final user message recorded". The system / developer messages follow under their own
  labels. "Conversation History" now lists prior turns only (the final user message moved to the section above). Raw
  Response and Parsed Summary pass through the `pretty_json` filter (`ui/app.py`, display only: a JSON object/array is
  re-serialized with `indent=2`, anything else is returned unchanged). In the graph, clicking a node header moves that
  node's own `.flow-node-body` into the side panel `#flow-detail-panel` (and back on close / Esc / another node), so the
  evidence exists once in the DOM and tier layout no longer stretches to thousands of pixels; on wide screens `.main`
  yields the panel width. `.flow-node--input` sets `flex: 0 0 auto`: the inherited `.flow-node { flex: 1 1 210px;
  overflow: hidden }` pinned the anchor's height at 210px inside the column-flex spine, which clipped an opened contract.
- **Markdown exports**: per-request, per-run benchmark, and per-conversation
  AI Act art. 12 audit (`/conversations/{id}/export.md` →
  `reports/conversation_export.export_conversation_to_markdown`).

**Opt-in `generation="upstream_then_verify"` draft provenance (default off).** The
speculative `llm_calls` row (and any reuse row that delivered it) uses the distinct
`module="upstream_speculative"` when the draft was produced by an upstream/client model
— never `module="upstream_provider"`, which the UI/export still read as a *final provider
candidate* (a separate, unrelated concept). `ui/app.py` (`_build_module_io_annotations`,
`_TIMELINE_MODULE_ORDER` adjacent to `policy`, main.css node style) and
`reports/markdown_export.py` render `upstream_speculative` as an upstream speculative
draft; `observability/read_store.get_models_used_for_run` / the markdown "Models used"
table surface it as a distinct `upstream_draft` row. `requests.meta_json` carries
`draft_origin`/`draft_model` only when upstream (`observability/governance_audit.py`);
internal mode adds no new keys anywhere. See `docs/CODEBASE_FACTS.md`.

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
  JSONL-only runs produce no dashboard views (every route gates on
  `get_db_path()`, e.g. `ui/app.py:3545-3546` in `request_detail`).
- **Proxy assistant text vs. governed content.** For streaming SDK SAFE/NORMAL
  paths the audit `final_response` is recorded empty (the body is consumed by the
  caller) (`wrapper.py:384-392,412-417`).
- **Lineage-collided conversations merge in the DB.** If two samples share a
  lineage-derived conversation_id (see the multi-turn trace), their turns land
  under one conversation_id and cannot be separated after the fact.
- **JSONL is not table-shaped.** Reconstructing a conversation from JSONL means
  joining across per-event-type files on `request_id`/`conversation_id` yourself;
  the UI and `conversation_export` only consume SQLite (§4, §6).
- **Context-shape telemetry is payload JSON.** It is present in
  `orchestration_events` / JSONL payloads and request metadata, not in dedicated
  typed SQLite columns or a UI panel.
- **`sim_metrics_measured` is absent on pre-2026-07-17 FINAL traces.** The marker
  (`DecisionTrace.sim_metrics_measured`, see `docs/CODEBASE_FACTS.md`) separates
  "sim metrics never measured" from "measured, every consequence benign" — the
  `sim_*` metrics cannot, both cases yield `sim_semantic_expected_harm=0.0` +
  `sim_worst_harm=None`. It reports whether those FINAL metrics are a measurement,
  **not** whether the simulator module executed: a full-parallel simulation
  discarded on a critic hard violation ran yet reads `False` here (its result was
  not retained into the trace). Rows written before the field existed carry no
  key, so they stay permanently ambiguous. **Reading contract:** the field is
  tri-state (`True`/`False`/absent-or-`None`); map absent/`None` to *unknown*. A
  `.get("sim_metrics_measured", False)` read is a bug — it re-asserts "not
  measured" on every historical row.
- **Reconstruction completeness depends on flush/shutdown for high-frequency
  telemetry.** A process killed before `flush()` / `shutdown()` may lose the last
  queued telemetry window. Lifecycle upserts and decision-audit finalization are
  synchronous; `file_only` finalization is synchronously attempted but not
  crash-durable because JSONL has no fsync contract.
