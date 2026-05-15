# Changelog

All notable changes to this project will be documented in this file.
The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## Unreleased

### Fixed (Step 14.8)

- **Asimmetria strutturale `posture` tra store e lookup del SemanticDecisionLedger**
  (`moralstack/orchestration/controller.py`): la formula della posture in
  `_extend_state_out_v04` (call site del ledger.store) leggeva
  `state.active_overlay`, ma quel campo non veniva MAI popolato dal
  controller perché `update_from_processing_result` viene chiamato senza
  passare `overlay=`. Risultato: lo store usava sempre `posture="NORMAL"`,
  anche su overlay sensibili (legal, medical, mental_health, political,
  journalism, financial, healthcare, emergency, cybersecurity, children,
  environment), mentre il lookup usava `posture="ELEVATED"` (correttamente
  derivato da `is_overlay_sensitive`).

  Conseguenza: la `LedgerKey(contract_hash, posture, domain)` differiva
  tra store e lookup per ogni overlay sensibile, rendendo le cache hit
  **strutturalmente impossibili** su tutti i domini di interesse
  safety-critical. Il bug era latente — non rilevato dai test perché lo
  scenario `multiturn_quickstart_fastpath_hit.py` usava un dominio non
  sensitive (vuoto o environment normalizzato a None), che produceva
  posture "NORMAL" su entrambi i lati per coincidenza.

  Fix: la formula di `_extend_state_out_v04` ora usa direttamente
  `is_overlay_sensitive(self.constitution_store, request.get_domain())`,
  la stessa funzione usata dal lookup side. Byte-coerente per costruzione.
  Il campo `state.active_overlay` resta disponibile come segnale separato
  per la UI ma non è più la fonte autoritativa della posture.

### Tests (Step 14.8)

- `tests/test_ledger_posture_symmetry.py`: 5 test che verificano
  esplicitamente l'invariante store-posture == lookup-posture per tutte
  le combinazioni di (final_action, overlay_sensitive, hard_constraints).
  Include una regression guard che imposta `state.active_overlay='legal'`
  ma forza `is_overlay_sensitive=False` per il domain, e verifica che la
  posture risulti NORMAL (pre-fix sarebbe stata ELEVATED).

### Added (Step 14.7)

- **Esempio dimostrabile e test E2E del branch gate-rejected del fast-path**
  (`examples/multiturn_quickstart_gate_rejected.py`,
  `tests/test_ledger_fast_path_gate_rejected_e2e.py`):
  finora i tre branch della `is_safe_to_apply` erano coperti solo da test
  unitari sintetici (Step 14.4). Mancava sia un esempio Python eseguibile
  che producesse l'evento `LEDGER_FAST_PATH_NOT_APPLIED` in vita reale, sia
  un test deterministico che verificasse l'emissione end-to-end della
  rejection del gate.

  Il nuovo esempio costruisce uno scenario a tre turni: il turno 1 viene
  cached come `NORMAL_COMPLETE`, e il turno 2 — semanticamente vicino sul
  topic ma con framing più tecnico-operativo — porta il path router a
  `route='deliberative'`. Il ledger fa hit dal turno 1 ma il gate rifiuta
  l'applicazione, emette `LEDGER_FAST_PATH_NOT_APPLIED` con
  `gate_reason='current_route_requires_deliberation'`, e la deliberazione
  parte in pieno.

  È un esempio di safety: il cache aiuta solo quando applicarlo non
  abbassa la garanzia di sicurezza del turno corrente.

### Tests (Step 14.7)

- `tests/test_ledger_fast_path_gate_rejected_e2e.py`: 3 test in 2 classi
  che coprono (a) l'emit contract della rejection con payload completo
  via runner reale + emitter mock, (b) la derivazione del `gate_reason`
  per `deliberative_loop`, (c) la derivazione difensiva per route ignote.

### Docs (Step 14.7)

- `docs/modules/observability.md`: nuova sezione "Fast-path safety gate"
  che documenta i tre branch della logica e gli eventi associati.

### Added (Step 14.4)

- **Eventi canonici `LEDGER_FAST_PATH_APPLIED` e `LEDGER_FAST_PATH_NOT_APPLIED`**
  (`moralstack/orchestration/controller.py`,
  `moralstack/orchestration/orchestration_event_taxonomy.py`):
  quando il SemanticDecisionLedger fa hit e la safety gate accetta di
  applicare la decisione cached, il controller emette un evento esplicito
  nel canale `orchestration.event`. Specularmente, quando il gate rifiuta
  l'applicazione, emette `LEDGER_FAST_PATH_NOT_APPLIED` con il motivo del
  rifiuto.

  Risultato: il salto della deliberazione è ora visibile sia nel canale
  ufficiale degli eventi di orchestrazione (tabella `orchestration_events`,
  file `orchestration.event.jsonl`) sia automaticamente nella metro map e
  nella journey list della UI, senza richiedere di joinare manualmente
  `ledger_events` con `conversation_states`.

  Il `orch_debug_log` interno esistente (`H-ledger-hit-applied`,
  `H-ledger-hit-skipped`) è mantenuto per il low-level debugging.

### Tests (Step 14.4)

- `tests/test_ledger_fast_path_events.py`: 6 test in 3 classi che verificano
  la registrazione delle costanti in `ALL_EVENT_TYPES`, il contract del
  capturing emitter, e ogni branch della safety gate
  `ConversationalFastPathRunner.is_safe_to_apply`.

### Fixed (Step 14.2)

- **SemanticDecisionLedger wired into production SDK bootstrap**
  (`moralstack/sdk/bootstrap.py`, `moralstack/runtime/orchestrator.py`): Steps 4–7
  implemented the semantic fast-path (ledger, embedder, storage, runner) and Step 13
  added observability for it, but no production bootstrap ever constructed a ledger
  instance. As a result, `ledger_events` stayed empty in real runs, semantically
  equivalent turns always ran full deliberation, and the UI “cached” marker never
  appeared.

  Changes:

  - `Orchestrator.__init__` accepts optional `ledger` and forwards it to
    `OrchestrationController`.
  - `_bootstrap_pipeline` builds a default `SemanticDecisionLedger` with
    `OpenAIEmbedder` and `InMemoryLedgerStorage`, unless disabled via
    `MORALSTACK_LEDGER_ENABLED=false`.
  - New tuning env vars: `MORALSTACK_LEDGER_SIMILARITY_THRESHOLD` (default `0.92`),
    `MORALSTACK_LEDGER_MAX_ENTRIES` (default `1000`),
    `MORALSTACK_LEDGER_EMBEDDING_MODEL`.
  - `GovernanceConfig` adds matching fields for programmatic overrides without env.

  Skip rules from multi-turn design v1.3 (no cache for `ESCALATED`, no cache when
  `turn_index < 1`, similarity threshold) are unchanged: the fast-path accelerates
  benign repeated queries, not hard-signal refusals.

### Tests (Step 14.2)

- `tests/test_runtime_orchestrator.py::TestOrchestratorLedgerWiring`: `ledger`
  is forwarded from `Orchestrator` to the internal controller.
- `tests/test_sdk_bootstrap.py`: four tests for default ledger, env disable,
  config disable, and threshold override.

### Fixed (Step 14.3)

- **SemanticDecisionLedger `request_type` round-trip** (`moralstack/orchestration/controller.py`):
  `_maybe_store_in_ledger` read `metadata.request_type`, but `ResponseMetadata`
  has no such field, so `getattr` always yielded `""`. The store wrote
  `request_type=""` while the next lookup used the real value from the risk
  estimator (e.g. `"factual_query"`). The ledger’s secondary intent check then
  rejected the match with `reason='intent_divergence'` even when cosine
  similarity was above the threshold — so the semantic fast-path produced no
  cache hits and the UI “cached” badge never appeared.

  **Fix:** the lookup block in `process()` saves `_request_type` and
  `_intent_clarity` (as used for `lookup`) into `_conversation_process_ctx`;
  `_maybe_store_in_ledger` reads them back so `store` uses the same key shape as
  future lookups. Metadata remains a forward-compatible fallback if
  `request_type` is ever added to `ResponseMetadata`.

### Tests (Step 14.3)

- `tests/test_orchestrator_ledger_integration.py::TestLedgerRequestTypeConsistency`:
  `_maybe_store_in_ledger` honours ctx vs empty fallback.
- `tests/test_orchestrator_ledger_integration.py::TestLedgerRoundTripHit`:
  ledger hit with aligned `request_type`, and `intent_divergence` when store
  used `""` and lookup used a non-empty type.
- `tests/test_sdk_bootstrap.py`: `test_bootstrap_creates_ledger_by_default` now
  clears `MORALSTACK_LEDGER_SIMILARITY_THRESHOLD` so a developer `.env` cannot
  break the default-threshold assertion.

### Added

- **SDK emits `proxy.request_finalized` per turn** (`moralstack/sdk/wrapper.py`):
  `GovernedClient` now fills the `proxy_request_events` table and the
  `logs/observability/proxy.request_finalized.jsonl` stream with the same
  per-turn summary envelope as the HTTP proxy, closing the Step 13
  observability gap between entry points.

  - `GovernedCompletions._create_inner` captures
    `state_in_snapshot = session.current_state` before deliberation and passes
    it to all five `_finalize_audit` call sites (REFUSE; SAFE_COMPLETE
    streaming/non-streaming; NORMAL_COMPLETE streaming/non-streaming).
  - `_finalize_audit` still calls `finalize_governance_audit`, then emits the
    canonical envelope via `emit_proxy_request_finalized` with
    `posture_in` / `posture_out` from `posture_of()`, `state_in` / `state_out`
    serialized via `state_summary_or_none()`, and `headers=None` because the
    SDK does not produce `X-MoralStack-*` response headers.
  - The event name remains `proxy.request_finalized` for backwards
    compatibility (table, JSONL filename, read store); the docstring notes the
    name is historic and the event is semantically transport-agnostic.

### Fixed

- **UI `/conversations/<cid>` shows the "Proxy finalization" block for SDK-only
  conversations** (`moralstack/ui/templates/conversation.html`): the template
  already gated on `{% if proxy %}`; it now receives data because the SDK
  emits the same finalized envelope.

### Tests

- Four new unit tests in `tests/test_sdk_wrapper.py` (`TestRequestFinalizedEmission`):
  NORMAL_COMPLETE, REFUSE, SAFE_COMPLETE, and two-turn state propagation.
- One new integration test in `tests/test_conversation_observability_persistence.py`
  (`test_sdk_emits_proxy_request_finalized_into_readstore`): round-trip via
  `SqliteReadStore.get_proxy_request_events_for_conversation`.

## 0.5.0 — 2026-05-13

### Fixed

- **Server proxy observability persistence** (`moralstack/server/proxy.py`):
  the Step 11/12 proxy never initialized the observability context, causing
  the SQLite DB and JSONL files to remain empty even when
  `MORALSTACK_OBSERVABILITY_DB_PATH` was set. The audit conversation export
  (Step 12) consequently returned no data for conversations served by the
  proxy.

  Root cause: `set_current_run_id()` and `set_current_request_id()` were never
  called, so all `persist_*` helpers silently no-op'd (they early-return when
  the context vars are unset). Additionally, the FK constraints from
  `orchestration_events`, `llm_calls`, `decision_traces` to `requests`
  rejected events emitted by the pipeline because no `requests` row had been
  inserted yet.

  Fix: at `create_app()` startup, init DB schema + create a `runs` row of
  type `"proxy"` + set `run_id` in the context var. Per request, pre-insert
  the `requests` row BEFORE calling `controller.process()` (to satisfy FK
  constraints), bind `request_id` in the context, then in the finally block
  update `final_response` + `domain` columns and flush the async queue so
  data is visible to downstream readers immediately.

  The `/healthz` endpoint now returns `{"status": "ok", "run_id": "<uuid>"}`
  so operators can verify observability is configured at deploy time.

- **Audit conversation export now works for proxy-served conversations**
  (`moralstack.reports.conversation_export.export_conversation_to_markdown`):
  this is a direct consequence of the persistence fix above. No code change
  required in the export module itself.

### Migration notes

- No API change. Existing proxy deployments will automatically start
  persisting data when restarted with `MORALSTACK_OBSERVABILITY_DB_PATH`
  (or `MORALSTACK_OBSERVABILITY_MODE=file_only`) set.
- The `/healthz` response shape changed: previously
  `{"status": "ok"}`, now `{"status": "ok", "run_id": "<uuid-or-empty>"}`.
  Clients that strictly checked equality on the body must be updated.

### Verification

- 3 new integration tests in `tests/test_server_proxy.py`:
  `test_proxy_persists_to_sqlite_db`,
  `test_healthz_reports_run_id_when_persistence_active`,
  `test_proxy_persists_orchestration_events`.
- 1442 tests pass on the full repo (1439 baseline + 3 new).

## 0.4.0 — 2026-05-13

### Added — Multi-turn governance

- **DeveloperContract** (`moralstack.orchestration.contract`): typed representation
  of deployer system prompt with `mode='opaque' | 'structured'` and `raw_text` /
  `contract_hash` properties. Used for governance scoping in
  `classify_refusal_focus` P1.
- **ConversationGovernanceState** extension (Step 1): added `posture`, `last_domain`,
  `last_risk_signals`, `last_decision_ledger_keys` fields for cross-turn state.
- **SemanticDecisionLedger** (Step 4): embedding-based cache for governance
  decisions, scoped by `(prompt_embedding, contract_hash, posture)`.
- **SessionState / InMemorySessionStore** (Step 5): SDK-level session management
  for multi-turn conversations.
- **ConversationalFastPathRunner** (Step 7): optimized routing for low-risk
  conversational continuations.
- **Cache `context_fingerprint`** (Step 9): per-module caches (perspectives /
  simulator / hindsight) now scope their entries by conversational context,
  closing the multi-turn governance hole (design v1.3 §6.7).
- **RefusalContext extended** (Step 10): added `developer_contract_summary` and
  `conversation_history_snippet` fields for richer refusal context.
- **`classify_refusal_focus` 7-priority hierarchy** (Step 10): added P0 (hard
  topical signals, never overridable) and P1 (developer_contract redirection
  for `mode='structured'`).
- **Caveat-as-extra-user-turn** (Step 10): SAFE_COMPLETE guidance is now injected
  as a synthetic user turn appended to messages. The developer-declared system
  prompt is preserved byte-identical (transparency invariant §1.3).
- **Server proxy** (`moralstack.server`, Step 11): FastAPI app exposing
  `POST /v1/chat/completions` for OpenAI-compatible clients. Includes per-conversation
  concurrency lock, deterministic conversation fingerprinting, and
  `X-Moralstack-*` governance headers.
- **Stateless `turn_index` resolution** (Step 12): the proxy now derives the
  turn index from the messages payload (`count(user_msgs) - 1`) instead of
  a server-side counter, ensuring correctness across server restarts and with
  stateless HTTP clients.
- **Conversation audit export** (`moralstack.reports.conversation_export`,
  Step 12): markdown export of complete multi-turn audit trail for AI Act
  art. 12 compliance.

### Added — Benchmark & infrastructure

- **COMPL-AI benchmark path**: `scripts/openai_compatible_server.py` — OpenAI-compatible FastAPI bridge (`/v1/chat/completions`, `/chat/completions`) routing requests through MoralStack governance (env `MORALSTACK_OPENAI_COMPATIBLE_*`).
- **Objective benchmark runner**: `scripts/benchmark_moralstack.py` — grounded-truth evaluation harness (expected actions/risk, parallel execution, markdown reports, optional judge model); aligns MoralStack scoring with `final_action`-only compliance semantics.
- Constitution overlay `violent_crime.yaml` plus coordinated overlay YAML adjustments across domains.
- `moralstack/orchestration/refusal_context.py` — refusal contextualization and grounding helpers wired through refusal assembly.
- `moralstack/observability/read_store.py` — read helpers over persisted observability artifacts.
- SQLite persistence extension for benchmark/report consumption (`moralstack/persistence/db.py`).
- Large expansion of automated tests: refusal contextualization and grounding, domain prefilter descriptions, intent falsification and operational-risk signals, observability read store, report durations and journey ordering, risk config/runtime-domain behavior, UI calibration path, refusal handler duration metadata, and related suites.

### Changed

- `cli/report.py`: framework version is now read dynamically from
  `moralstack.__version__` instead of being hardcoded.
- `moralstack/__init__.py`: version bumped to `0.4.0`.
- 4 deliberation modules (Critic, Simulator, Hindsight, Perspectives) accept
  optional `developer_contract` and `conversation_history` keyword arguments
  for conversational context injection (Step 9).
- Minimum `openai` dependency raised to `>=2.24.0` in `pyproject.toml`.
- README architecture diagram: risk-estimator parallel mini-estimator ordering/labels updated (`intent · signal detection (q1–q17) · operational risk`).
- **Risk layer**: richer estimation prompts and schema, calibration logic, config-loader/env wiring, estimator behavior (including runtime/normalized domain handling); documentation updates in `docs/modules/risk_estimator.md`.
- **Constitution**: retriever and store updates supporting benchmark-grade retrieval and policy behavior; related docs (`docs/modules/constitution_store.md`, `docs/constitution.md`, `docs/architecture_spec.md`).
- **Orchestration**: `safe_refusal_generator`, `refusal_handler`, `response_assembler`, `controller`, `deliberation_runner`, and `decision_service` updated for contextualized refusals and benchmark-aligned flows.
- **Reports & UI**: request report model enhancements (e.g. duration/journey-oriented fields); dashboard runs view and styling updates for calibration-oriented workflows.
- Environment templates (`.env.template`, `.env.minimal`) and `INSTALL.md` updated for new variables and setup paths.

### Fixed

- Domain-detection / refusal end-state specificity issues called out in the COMPL-AI integration work.
- Lint/format hygiene: Ruff and Black fixes with aligned test updates.

### Benchmark

- 84-question benchmark: compliance preserved at **98.81%** across Steps 8, 9, 10
  (3 sequential validations). The single off-diagonal question (Q70 healthcare
  informational) is unchanged from v0.3.

### Migration notes

- **Single-turn callers**: zero migration required. The pipeline is byte-identical
  when no developer_contract and no conversation_history are provided.
- **Multi-turn callers**: `govern(client)` now auto-manages conversation_id and
  applies multi-turn governance transparently. See `examples/multiturn_quickstart.py`.
- **HTTP clients**: point your OpenAI base_url at the MoralStack proxy
  (`moralstack-server` or `from moralstack.server import create_app`). See
  `examples/server_quickstart.py`.

## 0.3.3
22/04/2026

- fixed a bug in the audit of sdk

## 0.3.1
21/04/2026

- create publish on pypi workflow

## 0.3.0
21/04/2026

### Added
- `examples/` directory with runnable scripts: quickstart, forced overlay, automatic detection, custom overlay, batch evaluation, audit export.
- PyPI publishing workflow (`.github/workflows/publish.yml`) using trusted publishing.
- Project metadata in `pyproject.toml`: license, keywords, classifiers, URLs.

### Fixed
- improved traceability in file_only mode when using SDK

## 0.2.0
17/04/2026

- Python SDK: `govern(client)` wraps any OpenAI-compatible client with MoralStack governance
- New public API: `govern`, `GovernedClient`, `GovernanceConfig`, `GovernedResponse`, `GovernanceMetadata`
- `GovernedCompletions.create()` intercepts `chat.completions.create()` with pre-call deliberation
- Decision routing: NORMAL_COMPLETE passes through, SAFE_COMPLETE injects governance constraints, REFUSE skips OpenAI call entirely
- Streaming support: `GovernedStreamResponse` for normal/safe, `GovernedRefusalStream` for REFUSE
- Session tracking: `conversation_id` and `turn_index` persisted across calls on the same `GovernedClient`
- `GovernanceMetadata`: immutable audit snapshot of every deliberation (risk score, reason codes, triggered principles, counterfactual reasoning)
- `failure_policy`: configurable behavior on pipeline error (`refuse` or `passthrough`)
- Lazy imports in `moralstack/__init__.py`: `import moralstack` loads nothing at import time
- `moralstack/sdk/` package: `errors.py`, `config.py`, `session.py`, `response.py`, `bootstrap.py`, `wrapper.py`
- 106 new SDK tests (unit + integration with mock pipeline)
- Orchestration decoupling: `govern()` has zero FastAPI/uvicorn dependencies

## 0.1.0
30/03/2026

- \#1 Deliberative latency and observability: speculative overlap, structured outputs, and UI alignment
