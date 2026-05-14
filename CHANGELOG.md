# Changelog

All notable changes to this project will be documented in this file.
The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## Unreleased

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
