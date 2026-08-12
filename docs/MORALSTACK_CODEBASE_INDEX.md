# MoralStack Codebase Index

> A file/function map of the MoralStack codebase, grounded in inspected source.
> Snapshot of `main` at package version `0.7.0` (`pyproject.toml:7`).
> The code is authoritative — verify a symbol still exists before relying on it.
> Confidence and evidence for individual claims live in `docs/CODEBASE_FACTS.md`.
>
> For the AI agentic workflow (Claude orchestrates · Codex reviews · a Claude
> Sonnet sub-agent implements) that consumes this index at planning time, see
> `docs/ai/` — start from `docs/ai/AGENTIC_WORKFLOW.md`.

---

## 1. Repository layout

```
moralstack/
  __init__.py            # public package; lazy-exports the SDK surface
  sdk/                   # public SDK: govern(), GovernedClient, GovernanceConfig
  runtime/               # Orchestrator facade + decision policy + deliberative modules
  orchestration/         # OrchestrationController and all routing/deliberation services
  models/                # risk estimator, policy LLM, decision explanation
    risk/                # LLMBasedRiskEstimator, calibration, signal catalog
  constitution/          # ConstitutionStore, schema/loader/retriever, YAML data
    data/core.yaml       # baseline constitution
    data/overlays/*.yaml # 21 domain overlays
  compliance/            # DCCL (Developer Contract Compliance Layer)
  observability/         # telemetry service, sinks (SQLite/JSONL), read store, emit helpers
  pipeline/              # context builder + deliberation stack assembly
                         #   output_contract.py: Tier-1 enumerated-output
                         #   detection (TRUE/FALSE etc.) used by the critic gate
  prompts/               # module prompt templates
  reports/               # markdown/conversation/benchmark export + UI data builders
  server/                # OpenAI-compatible FastAPI governance proxy
  ui/                    # FastAPI dashboard (moralstack-ui)
  cli/                   # `moralstack` CLI
  utils/                 # env loading, caching, output protection, json helpers
  core/                  # shared types/schema
scripts/                 # benchmark, inspector, install
examples/                # runnable usage examples
tests/                   # ~120 test modules + e2e payloads
  harness/               # offline unit tests for the .claude/hooks/* scripts
docs/                    # architecture, modules, traces, this index
.claude/                 # AI harness: hooks, path-scoped rules, agents, skills
                         #   (fail-open; inventory in .claude/hooks/README.md)
```

Python `>=3.11` (`pyproject.toml:11`). Runtime deps: `openai>=2.24`, `pydantic>=2`,
`python-dotenv`, `ruamel.yaml`, `langdetect`. UI/server extras add `fastapi`,
`uvicorn`, `httpx`, `jinja2` (`pyproject.toml:27-56`).

### Console entry points (`pyproject.toml:58-62`)

| Script | Target | Notes |
|---|---|---|
| `moralstack` | `moralstack.cli.run:main` | CLI runner |
| `moralstack-ui` | `moralstack.ui.app:main` | dashboard |
| `moralstack-server` | `moralstack.server.proxy:main` | **reserved** — `main()` raises `NotImplementedError`; use `create_app` instead (`server/proxy.py:777`) |
| `moralstack-validate-overlay` | `moralstack.cli.validate_overlay:main` | overlay validation |

---

## 2. Main packages and their roles

### SDK — `moralstack/sdk/`
- `wrapper.py` — `govern(client, config=None)` wraps any OpenAI-compatible client
  and returns `GovernedClient`. `GovernedCompletions.create()` runs deliberation
  *before* delegating to the wrapped client. Helpers: `_extract_last_user_message`,
  `_extract_developer_contract` (delegates to shared `ConversationContext`; last
  non-empty `system`/`developer` message wins, `mode="opaque"`),
  `_messages_to_turns`, `_build_safe_complete_user_turn`.
- `bootstrap.py` — `_bootstrap_pipeline(config)` builds the `Orchestrator`;
  `_resolve_model(config)` resolves the generation model; `_resolve_generation_mode(config)`
  resolves `generation` ("internal" default | "upstream_then_verify" opt-in; env
  `MORALSTACK_GENERATION_MODE` overrides; unknown value fails closed to "internal");
  `_build_ledger(config)` wires `SemanticDecisionLedger` with a provider-selected embedder
  (`LocalEmbedder` by default, `OpenAIEmbedder` when `embedder_provider="openai"` or
  `MORALSTACK_EMBEDDER_PROVIDER=openai`).
- `config.py` — `GovernanceConfig` (domain_overlay, failure_policy,
  observability_mode, jsonl_dir, enable_session_tracking, `embedder_provider`,
  `generation` ("internal" | "upstream_then_verify"), …).
- `session.py` — `SessionState`: per-client conversation_id + turn counter,
  wraps a `SessionStore`.
- `session_store.py` — `SessionStoreProtocol`, `InMemorySessionStore`.
- `response.py` — `GovernedResponse`, `GovernanceMetadata` (`final_action`,
  `risk_score`, `risk_category`, `path`, `reason_codes`, `triggered_principles`,
  `conversation_id`, `turn_index`, `draft_origin` ("internal" default | "upstream"),
  `draft_model` (""  default) — additive fields, opt-in `generation=
  "upstream_then_verify"`, NOT held to byte-identity (the one deliberate exception) …)
  plus governed-delivery model attribution (`requested_model`, `generation_model`,
  `rewrite_model` — `generation_model`/`GovernedResponse.model` report the client draft
  model on an unrevised upstream-origin delivery). Constructors:
  `from_governed_text` (Plan 1 primary), `from_refusal`, `from_pipeline_error`,
  and the deprecated `from_normal` / `from_safe` / `from_governed_draft`;
  `from_passthrough` is a deprecated fail-closed alias (never passthrough).
  `is_passthrough` is always `False`.
- `errors.py` — `GovernanceError` + subclasses.

### Runtime — `moralstack/runtime/`
- `orchestrator.py` — `Orchestrator` facade. Builds an `OrchestrationController`
  and forwards `.process(...)`. Factories `create_orchestrator`,
  `create_minimal_orchestrator`. Re-exports public types.
- `decision/safe_complete_policy.py` — **single source of truth** for action
  bounds. `Action` enum (`NORMAL_COMPLETE < SAFE_COMPLETE < REFUSE`),
  `PolicyContext`, `PolicyBounds`, `compute_action_bounds`, `decide_final_action`.
- `decision_policy.py`, `decision_correctness.py` — supporting decision logic.
- `modules/` — deliberative modules and their config loaders:
  `critic_module.LLMConstitutionalCritic`, `simulator_module.LLMConsequenceSimulator`,
  `perspective_module` (`create_minimal_ensemble`), `hindsight_module.LLMHindsightEvaluator`.
- `trace/` — `decision_trace.DecisionTrace`, `trace_stages`.

### Orchestration — `moralstack/orchestration/`
- `controller.py` — `OrchestrationController.process(...)` is the governance
  runner core (file is ~2498 lines). Owns risk estimation, speculative overlap,
  DCCL invocation, routing, ledger lookup/store, conversation-state extension,
  and event emission. `_estimate_risk` computes the single-wave retrieval query
  policy (RAW prompt iff no developer-contract text AND no conversation history,
  else `_build_enriched_retrieval_query`, imported from `deliberation_runner`)
  and the unified `retrieval_top_k = max(risk_top_k, critic_top_k)` (guarded
  `getattr(risk_estimator, "_top_k", DEFAULT_RISK_TOP_K)` since
  `RiskEstimatorProtocol` exposes only `estimate(prompt)`), passing both into
  `risk_estimator.estimate(...)`. `_build_request_analysis_from_risk` lifts the
  risk-owned retrieval into a `RequestAnalysisContext` (authoritative even when
  `relevant_principles == ()`, gated on `RiskEstimation.retrieval_succeeded` —
  never on emptiness) and emits the single `RELEVANT_PRINCIPLES_RETRIEVED` event;
  `_route_fast_path`/`_route_deliberative` build it once and pass it into
  `run_fast_path`/`run_deliberative_path` so deliberation, the fast-path
  `quick_check`, and the quick-check-failed deliberative fallback all reuse the
  same retrieval (exactly one `get_relevant_principles` call per request across
  routes). `COMPLIANCE_FAST_PATH` (`run_benign_fast_path`) consumes no
  principles, so it adds no retrieval regardless.
- `conversation_context.py` — shared OpenAI-message parser for SDK/proxy. Builds
  `ConversationContext` with final user message, prior user/assistant turns,
  developer contract, `history_source`, role-serialized transcript helpers,
  context-shape metadata, and the compliance delivery mismatch guard.
- `delivery.py` — pure governed-delivery finalizer (Plan 1). `GovernedDelivery`
  dataclass + `finalize_delivery(result, *, config)`: turns an already-governed
  `OrchestratorResult` into the text to deliver (sources `governed`,
  `governed_refusal`, blank-content fail-closed `governed_pipeline_refusal`). No
  model call, no wrapped/upstream client, no observability writes. Used by SDK
  and proxy so the delivered answer is always the governed pipeline text.
- `decision_service.py` — `decide_action(request, risk_proto, …)` →
  `(Decision, DecisionExplanation)`.
- `path_router.py` — `get_route(...)` → `(route, borderline_refuse, risk_policy_action)`;
  `is_hard_signal_refuse(...)`.
- `safe_complete_gating.py` — `apply_safe_complete_gating(...)`.
- `deliberation_runner.py` — `DeliberationRunner` (cycles, convergence,
  `run_fast_path`, `run_benign_fast_path`). `run_deliberative_path`/
  `run_fast_path` accept an optional `request_analysis: RequestAnalysisContext |
  None` (the controller's risk-owned retrieval); when supplied it is
  authoritative (used as-is, even empty) and the runner does NOT re-retrieve or
  emit `RELEVANT_PRINCIPLES_RETRIEVED` itself. Only when `None` does the runner
  fall back to its own retrieval via `_try_build_request_analysis_context`
  (`retrieval_phase="deliberation_retrieval"`) and emit the event
  (`_record_retrieval_start_and_event`) — controller-emit and runner-emit are
  mutually exclusive per request. `retrieval_top_k_for_request()` (public;
  aligns with `critic.config.top_k_principles`) is also used by the controller to
  compute the unified top_k. The critic-reuse gate in `_critique` no longer
  requires `len(relevant_principles) > 0` (an empty supplied context is used
  as-is); critic reuse slices to `retrieval_top_k_for_request()` so the critic is
  never widened beyond its own configured top_k. `run_fast_path` forwards the
  shared principles to `critic.quick_check(..., pre_retrieved_principles)`
  (filtered to HARD there) and to the quick-check-failed `run_deliberative_path`
  fallback call, so FAST_PATH never re-retrieves either.
- `convergence.py`, `convergence_evaluator.py` — convergence engine.
- `conversation_state.py` — `ConversationGovernanceState`, `TurnDecisionSummary`.
- `conversational_fast_path.py` — `ConversationalFastPathRunner` (cache-driven skip).
- `embedder.py` — `EmbedderProtocol`, `HashingEmbedder` (pure-Python fallback),
  `LocalEmbedder` (fastembed when installed, else `HashingEmbedder`; default via
  `GovernanceConfig.embedder_provider="local"`), `OpenAIEmbedder` (opt-in OpenAI),
  `cosine_similarity`. Provider selection: config `embedder_provider` or env
  `MORALSTACK_EMBEDDER_PROVIDER`; optional dep `moralstack[local-embeddings]`.
- `ledger.py`, `ledger_storage.py` — `SemanticDecisionLedger`, `CachedDecision`,
  `LedgerResult` (`query_embedding` carries the lookup-time vector for reuse in
  `store(prompt_embedding=…)`, eliminating double-embedding on miss→store).
- `refusal_handler.py`, `refusal_context.py`, `safe_refusal_generator.py` — refusal text.
- `response_assembler.py` — `ResponseAssembler` builds the `FinalResponse`. `assemble(...,
  draft_provenance: DraftProvenance | None = None)` — centralized draft-provenance
  attribution (opt-in `generation="upstream_then_verify"`): sets `metadata.draft_origin`/
  `draft_model`/`internal_draft_reused` whenever it delivers the reused, still-unmodified
  `state.draft_response` (`DeliberationState._draft_verbatim_reuse`). Covers FAST_PATH and
  deliberative reuse (incl. the fast-path→deliberative escalation via
  `_build_deliberative_result`); the benign route sets provenance via its own
  `ResponseMetadata.from_decision` call instead (no separate reuse `llm_call`). Default
  `None` = internal mode, byte-identical to before this feature.
- `speculative_overlap.py` — `SpeculativeOverlapHandle` (parallel draft + risk).
- `upstream_draft.py` — **NEW** (opt-in `generation="upstream_then_verify"`).
  `UpstreamDraftGenerator(client, model)`: adapter exposing `generate`/`generate_messages`
  (same `GenerationResult` shape as `OpenAIPolicy`) so `_speculative_generate` can route the
  speculative draft to a caller-supplied client model instead of `self.policy`. Empty
  completion content returns `GenerationResult(text="")`, never raises — the empty-draft
  fallback to internal regeneration is handled by the caller. Never used for the delivered
  answer, rewrite, or refusal wording.
- `system_prompt_resolver.py` — `effective_system_for_request(...)`.
- `overlay_policy.py` — `is_overlay_sensitive`, `apply_risk_floor_if_sensitive`,
  `is_domain_excluded`, `get_constitution_safe`, `OVERLAY_SENSITIVE_RISK_FLOOR`.
- `process_context.py` — `ProcessCallContext` (per-call mutable carrier).
- `types.py` — `ProcessedRequest`, `OrchestratorResult`, `Decision`,
  `FinalResponse`, `ResponseMetadata`, `OrchestratorConfig`, errors.
- `contract.py` — `DeveloperContract` (`from_text`, `contract_hash`, `structured_rules`).
- `orchestration_event_taxonomy.py` — canonical event-type constants.
- `persistence_port.py` — `PersistencePort` protocol (request-scoped upsert abstraction).
- `default_persistence.py` — `DefaultPersistence` (`ensure_run_and_upsert_request`,
  `set_request_context`, `update_request_domain`); used by the controller via DI.
- `null_persistence.py` — `NullPersistence` (no-op default).
- `persistence_helpers.py` — `record_llm_call`, `record_decision_trace` (async emit + swallow).
- `default_event_emitter.py` — `DefaultEventEmitter` (orchestration event persistence).

### Risk — `moralstack/models/risk/`
- `estimator.py` — `LLMBasedRiskEstimator`. `estimate(prompt, developer_contract_text=…,
  conversation_history=…, retrieval_query=…, retrieval_top_k=…)` runs **three
  parallel mini-estimators** via a `ThreadPoolExecutor`: `estimate_intent`,
  `estimate_signals` (q1–q17), `estimate_operational`; merged by
  `calibration.merge_mini_estimator_results`. The three real mini-estimator
  `llm_call` envelopes are built with the local 16-key risk payload (the 16th key
  is `billable_provider_call`) and enqueued as one observability batch; a synthetic
  `calibration_guard` row remains a separate single enqueue and is emitted with
  `billable_provider_call=False` (it is no real provider call: audit-tracked but
  excluded from token/cost aggregation, so it never surfaces as a spurious "missing"
  token row). The three real mini rows leave the flag unset (`None` → billable by
  the `COALESCE(...,1)` default).
- **Unified single-wave constitution retrieval** (unify-constitution-retrieval-
  single-pass): `_get_principles_context` is the ONE `get_relevant_principles`
  call per request, owned here (risk thread), reused by deliberation/critic/
  fast-path. It accepts optional `retrieval_query` (raw prompt vs the
  controller's enriched query) and `retrieval_top_k` (`max(risk_top_k,
  critic_top_k)`, computed by the controller) and returns a
  `_PrinciplesContextResult` carrying the formatted intent-mini string (sliced to
  `self._top_k`), the FULL retrieved principle tuple, the domain-prefilter debug
  snapshot, and retrieval-status flags (`retrieval_attempted`/`_succeeded`/
  `_error`) — the status flags, not emptiness, decide reuse-vs-fallback
  downstream. These are carried on `RiskEstimation` (`relevant_principles`,
  `retrieval_metadata`, `retrieval_count`, `retrieval_duration_ms`,
  `retrieval_started_at_ms`, `retrieval_top_k`, `retrieval_attempted`,
  `retrieval_succeeded`, `retrieval_error`) — in-memory `Principle` objects that
  must NEVER be serialized into the persisted `llm_calls` payload
  (`_LOCAL_LLM_CALL_PAYLOAD_KEYS`). The signals/operational minis still receive
  no principles (§5.3 unaffected); only the intent mini does, and the 5 fixed
  SEMANTIC ANALYSIS GUIDELINES now live in `INTENT_CONTEXT_SYSTEM_PROMPT` (static,
  cacheable) rather than the per-request user message.
- `calibration.py` — `merge_mini_estimator_results`, `parse_risk_dict`, score
  calibration rules (defensive override, harm escalation, non-operational clamp,
  calibration guard).
- `categories.py` — `RiskCategory` (BENIGN, SENSITIVE, MORALLY_NUANCED,
  POTENTIALLY_HARMFUL, CLEARLY_HARMFUL), `OperationalRisk`.
- `config/signals.yaml` — signal catalog; `prompts.py` — mini-estimator prompts.
- `schema.py` — `RiskEstimation`.

### Constitution — `moralstack/constitution/`
- `store.py` — `ConstitutionStore` (optional LLM-based principle matching).
  `get_relevant_principles(query, top_k=10, domain=None, *,
  retrieval_phase="risk_routing")` delegates to `ConstitutionRetriever`; both are
  pure, stateless projections of `retrieve(...)`.
- `retrieval_result.py` — **NEW** (retrieval-request-scoped-state, P0 fix). Leaf
  module: `PrincipleRetrievalResult` (frozen dataclass: `principles`,
  `prefiltered_domains`, `debug_info`), the typed return value of
  `ConstitutionStore.retrieve(...)` / `ConstitutionRetriever.retrieve(...)`.
  Every per-request retrieval value now travels on this return value; neither
  class writes request-scoped state onto `self`. Replaces the removed
  `get_debug_info()` on both classes, which read
  `ConstitutionRetriever._last_debug_info` — a mutable instance attribute on a
  store built once per process and shared by concurrent request threads (see
  `docs/CODEBASE_FACTS.md`, `CHANGELOG.md`). `ConstitutionStoreProtocol`
  (`orchestration/types.py`) declares `retrieve` as an optional method (readers
  duck-type via `getattr(store, "retrieve", None)`).
- `loader.py`, `schema.py`, `retriever.py`, `prompt_formatter.py`, `helpers.py`.
  `retriever.py` domain-agent caches hash rendered OpenAI messages plus
  generation params, not only principle ids/counts. `retrieval_phase`
  (`RETRIEVAL_PHASE_RISK_ROUTING` / `RETRIEVAL_PHASE_DELIBERATION`) is threaded
  through the domain prefilter AND both agent kinds — `EnhancedDomainAgent`/
  `DomainAgent.evaluate(query, *, retrieval_phase=...)` → their `_call_openai` →
  `_persist_constitution_llm_call` — so the risk-owned single wave persists
  `llm_calls.retrieval_phase="risk_routing"` and any fallback wave (no
  controller-supplied context) persists `"deliberation_retrieval"`.
- `data/core.yaml` — baseline constitution.
- `data/overlays/*.yaml` — 21 domain overlays: children, coding, creative,
  customer_service, cybersecurity, education, emergency, enterprise, environment,
  financial, gaming, healthcare, journalism, legal, medical, mental_health,
  political, relationships, research, science, violent_crime.

### Compliance / COMPL-AI bridge — `moralstack/compliance/`
- `dccl.py` — `DeveloperContractComplianceLayer.evaluate(...)` → `ComplianceVerdict`.
  Three evaluation paths (structured / LLM / hybrid). `validate_draft_against_action`.
- `safety_override.py` — `classify_safety_override` (P0 categories that can never
  be authorized).
- `types.py` — `ComplianceDecision` (MATCH, NO_MATCH, SAFETY_OVERRIDE, NO_CONTRACT),
  `ComplianceVerdict`, `MatchedRule`, `StructuredRule`, `EvaluationPath`.
- `config.py` — DCCL env config getters.

### Observability — `moralstack/observability/`
- `service.py` — `ObservabilityService`, singleton via `get_obs()` / `obs`.
  `emit`/`emit_batch` are async fire-and-forget via the worker queue; `flush()`
  drains queued writes for read-after-write tests and short-lived SDK callers.
- `router.py` — synchronous dispatch by mode (`db_only` → SQLite,
  `file_only` → JSONL, `dual` → both). `route_window` is counted for the worker;
  `route_audit_sync` is counted and synchronous for finalization.
- `sinks/sqlite_sink.py` — schema + writers (`init_db`, `create_run`,
  `upsert_request`, `update_request_*`, `delete_*`) plus FK-ordered
  `write_window` with per-envelope isolation. Tables in §8.
- `sinks/jsonl_sink.py` — JSONL envelope writer with counted `write_window`.
- `read_store.py` — `SqliteReadStore` (read contract used by UI & exports).
- `conversation_events.py` — `emit_conversation_state_updated`,
  `emit_proxy_request_finalized`, `finalize_audit_sync`.
- `governance_audit.py` — `finalize_governance_audit`, `posture_of`,
  `state_summary_or_none`.
- `context.py` — contextvars (`set_current_run_id`, `set_current_request_id`,
  `set_current_session_id`, `set_current_turn_number`).
- `config.py` — `get_observability_mode`, `get_db_path`.
- `emit_helpers.py` — `persist_*` / `async_persist_*` telemetry wrappers.

### Server proxy — `moralstack/server/`
- `proxy.py` — `create_app(openai_client, orchestrator, config, session_store,
  correlation_store)` returns a FastAPI app exposing `POST /v1/chat/completions`,
  `/chat/completions`, `GET /healthz`. `ConversationLockManager` (per-conversation
  locks; `_locks` growth-bounding is deferred — see FACTS), `_handle_chat_completion_sync`
  (runs in a threadpool). Reads client
  `max_tokens`/`max_completion_tokens`/`temperature`/`top_p` into
  `ProcessedRequest.generation_overrides` via
  `GenerationOverrides.from_mapping(body, passthrough_unset=True)`; the SDK
  (`sdk/wrapper.py`) does the same from `govern` kwargs but with
  `passthrough_unset=False`. These influence the delivered answer. On the **proxy**
  an unset field is **omitted** from the OpenAI call (model default), so the env
  defaults do not apply there; on the **SDK/CLI** an unset field falls back to the env
  default (precedence override > `GenerationConfig` > env defaults
  `OPENAI_MAX_TOKENS`/`OPENAI_TEMPERATURE`/`OPENAI_TOP_P`). REFUSE wording is excluded
  on every path. See `docs/modules/policy.md`. Per request, `_extract_principal(request)`
  derives a tenant/principal string (A: `X-Moralstack-Tenant-Id` header → B: HMAC-SHA256
  of an `Authorization: Bearer` token via `MORALSTACK_PRINCIPAL_HMAC_SECRET`, read
  per-request → C: empty-string sentinel) that keys the correlation store's lineage map
  (P3 / P0-3 / A3; see `docs/traces/openai_compatible_multiturn.md`).
- `conversation_correlation.py` — `ConversationCorrelationStore` (bounded, TTL +
  max-entries FIFO eviction; lineage hashing → conversation_id, internal map keyed by
  `(principal, history_hash)`) + `canonical_history_hash`, `canonical_parent_history_hash`
  (unchanged by the principal-keying design — isolation lives entirely in the map key).
  Constructor: `ttl_seconds` (default `DEFAULT_CORRELATION_TTL_SECONDS=3600`),
  `max_entries` (default `DEFAULT_MAX_CORRELATION_ENTRIES=20_000`), `time_fn` (clock seam
  for tests). `create_app` wires `MORALSTACK_CORRELATION_TTL_SECONDS` /
  `MORALSTACK_CORRELATION_MAX_ENTRIES` (best-effort parse + range-validate, never raises).
- `headers.py` — `build_governance_headers` (X-Moralstack-* response headers). Emits
  `X-Moralstack-Draft-Origin` / `X-Moralstack-Draft-Model` only when
  `metadata.draft_origin == "upstream"` (opt-in `generation="upstream_then_verify"`);
  internal mode never adds these headers.
- `fingerprint.py` — request fingerprinting (calls the unchanged `canonical_history_hash`;
  verified unaffected by the principal-keying change).

### UI — `moralstack/ui/`
- `app.py` — FastAPI dashboard (`moralstack-ui`). Reads exclusively from the
  observability SQLite DB via `SqliteReadStore`. Routes in §11. Templates in
  `templates/`, assets in `static/`.

### CLI / scripts
- `moralstack/cli/run.py`, `shell.py`, `loader.py`, `report.py`, `visualizer.py`.
- `scripts/benchmark_moralstack.py` — internal 84-question benchmark.
- `scripts/inspect_multiturn_trace.py` — multi-turn inspector CLI.
- `scripts/mstack_run.py`, `consolidate_jsonl_meta.py`, `install.py`.

---

## 3. Runtime governance flow (high level)

`govern()` → `GovernedClient.chat.completions.create()` → `Orchestrator.process()`
→ `OrchestrationController.process()` (`orchestration/controller.py:1885`).

Inside `process()` (order verified in source):
1. Normalize request; build `ProcessCallContext`; set session/turn context vars;
   pre-insert the `requests` row (`controller.py:1900-1923`).
2. **Risk estimation** — speculative overlap (risk + draft in parallel) when
   `enable_speculative_generation`, else direct `_estimate_risk`
   (`controller.py:1928-1935`). `_speculative_generate` selects the draft generator:
   `request.upstream_draft_generator` (opt-in `generation="upstream_then_verify"` +
   client `model`, wired by the SDK/proxy) when present, else `self.policy` (default,
   byte-identical). An empty/whitespace upstream draft is treated as "no draft" →
   internal governed regeneration (never a passthrough, never a refusal). The
   speculative `llm_call` row is `module="upstream_speculative"` + client model when
   upstream, else the unchanged `module="policy"`. Draft provenance
   (`DraftProvenance(origin, model)`) is derived at route time from
   `(request.upstream_draft_generator, draft_is_speculative)` and threaded to the
   reuse rows and `ResponseAssembler.assemble` — see `upstream_draft.py` above and
   `.claude/rules/governed-delivery.md`.
3. **DCCL evaluation** on the (possibly non-blocking) speculative draft
   (`_run_dccl_evaluation`, `controller.py:1936`). The LLM path receives a
   budgeted role-ordered transcript from `ConversationContext`; when the draft
   is about to be reused, the mismatch guard prevents a last-user-only draft from
   becoming final if governance saw broader prior context. **Hard-signal gate (P0
   invariant #3):** before the `MATCH` block dispatches, `process()` invalidates the
   MATCH (`cv = None`, emits `COMPLIANCE_MATCH_DOWNGRADED`) when
   `path_router.has_hard_signal_evidence(risk_estimation)` is true — the risk-owned
   hard signal (a `_HARD_SEMANTIC_SIGNALS` member or `clearly_harmful` category) can
   never be overridden by a developer contract, and `dccl.evaluate` discards
   `risk_estimation` so this is the only enforcement point. On `MATCH` with a
   validated aligned draft (and no hard signal) → **compliance fast-path**
   (`_route_compliance_match`), skipping risk routing and deliberation.
4. Apply overlay sensitivity risk floor (`apply_risk_floor_if_sensitive`),
   normalize domain, domain-exclusion check (`controller.py:2062-2116`).
5. **Decision** — `decide_action(...)` then `apply_safe_complete_gating(...)`
   (`controller.py:2118-2130`).
6. **Routing** — `get_route(...)` → one of `refuse | benign | safe_complete |
   fast_path | deliberative`; `is_hard_signal_refuse(...)`
   (`controller.py:2143-2144`).
7. **Ledger lookup** (multi-turn) — when a ledger + conversation_id exist, a
   cache hit may patch the decision/route to skip deliberation
   (`controller.py:2149-2306`).
8. Dispatch to the matching `_route_*` handler (`controller.py:2345-end`).
9. `_apply_conversation_metadata_to_result` stamps conversation linkage, builds
   `conversation_governance_state_out`, emits conversation events, and stores the
   decision in the ledger (`controller.py:319-413`).

See `docs/traces/governance_decision_flow.md` for the full trace.

---

## 4. Decision actions: NORMAL_COMPLETE / SAFE_COMPLETE / REFUSE

Computed in `runtime/decision/safe_complete_policy.py` from structured signals,
never from text:

- `compute_action_bounds(ctx)` → `PolicyBounds(min_required, max_allowed,
  reason_codes)`. Rules (in order): hard violations / `clearly_harmful` /
  op_risk HIGH ⇒ REFUSE bounds; HIGH actionability ⇒ SAFE_COMPLETE; sensitive /
  morally_nuanced ⇒ SAFE_COMPLETE (factual non-sensitive exemption allows
  NORMAL); potentially_harmful ⇒ SAFE_COMPLETE in sensitive overlays else gray
  zone; benign ⇒ NORMAL_COMPLETE.
- `decide_final_action(ctx)` derives the action from bounds; gray zone defaults
  to `NORMAL_COMPLETE` to reduce false positives (`safe_complete_policy.py:264-285`).
- **Runtime final action is assembled by `decision_service.py` and post-gated
  by `safe_complete_gating.py`.** `_handle_hard_violations`
  (`decision_service.py:493-579`) has three narrow cases that return
  SAFE_COMPLETE even when bounds say REFUSE: (1) MH.CRISIS.1 + crisis_support
  request type; (2) low-risk + non-operational + domain_regulated; (3) pre-policy
  action SAFE_COMPLETE + low-risk + non-operational + no requested_instructions.
  `apply_safe_complete_gating` (`safe_complete_gating.py:73-171`) can downgrade
  gray-zone SAFE_COMPLETE → NORMAL_COMPLETE (not applied to SENSITIVE /
  MORALLY_NUANCED categories).
  `_handle_informational_recovery` floors benign+regulated informational
  requests to SAFE_COMPLETE by default; opt-in
  `OrchestratorConfig.regulated_informational_normal_complete`
  (`MORALSTACK_ORCHESTRATOR_REGULATED_INFORMATIONAL_NORMAL_COMPLETE`, default
  false) lets clearly benign, non-operational requests (same benignity guards as
  the unregulated branch) return NORMAL_COMPLETE instead. Any positive signal
  keeps SAFE_COMPLETE; non-BENIGN categories and hard-signal REFUSE are
  unaffected.

Routing consequences (`sdk/wrapper.py`, `server/proxy.py`) — **governed delivery
only (Plan 1)**. The delivered text is always the governed pipeline result,
finalized by the pure `finalize_delivery` in
`orchestration/delivery.py` (`GovernedDelivery`). The wrapped/upstream client is
**never** called to generate the delivered answer for any `final_action`:

| `final_action` | SDK behavior | Proxy behavior |
|---|---|---|
| `NORMAL_COMPLETE` | deliver governed text via `GovernedResponse.from_governed_text`; **client not called** | synthetic `chat.completion` from governed text (or synthetic SSE replay when `stream=True`); **upstream not called** |
| `SAFE_COMPLETE` | deliver governed text via `from_governed_text`; **client not called** | synthetic `chat.completion` / SSE replay from governed text; **upstream not called** |
| `REFUSE` | return governed refusal text; **client not called** | synthetic `chat.completion` (finish_reason `content_filter`); **upstream not called** |

Blank/invalid governed content fails closed to a deterministic governed refusal
(`final_text_source="governed_pipeline_refusal"`). Pipeline failures also fail
closed (no passthrough). `final_revalidation` is retained only for historical
readers (UI/reports); it is not invoked on the active delivery paths.

---

## 5. Risk estimation

`LLMBasedRiskEstimator.estimate(...)` (`models/risk/estimator.py:249`) delegates
to the parallel mini-estimator path (`estimator.py:549-678`, `ThreadPoolExecutor`
at `:674-678`), which runs three mini-estimators and merges them:
- `estimate_intent` — detected_language, intent flags, request_type, harm_type.
- `estimate_signals` — q1–q17 harm/topic signals.
- `estimate_operational` — operational_risk, raw risk_score, confidence,
  misuse/actionability.

`calibration.py` then applies deterministic guardrails to produce the final
`RiskEstimation` (score, `risk_category`, `operational_risk`, detected_domain,
semantic_signals). Verified rules (`models/risk/calibration.py`):
- **Defensive override** (`:284-304`): harmful_count≥1 but all intent flags False
  and op_risk NONE/LOW ⇒ SENSITIVE, score≤0.30 (blocked for weapons+intent and q17).
- **Harm escalation** (`:310-319`): harmful_count≥3 / q5 / q17 / priority-harm +
  operational signal / score≥0.85 / clear_harm ⇒ CLEARLY_HARMFUL, score≥0.85.
- **Intent-contradiction** (`:536-548`): op_risk HIGH but all intent False ⇒
  CLEARLY_HARMFUL→SENSITIVE, score≤0.55, DENY→DELIBERATE.
- **Non-operational clamp** (`:554-565`): op_risk NONE + all intent False + not q17
  ⇒ CLEARLY_HARMFUL→SENSITIVE, score≤0.30, DENY→DELIBERATE.
- **Calibration guard** (`:659-763`): benign request types with no harm intent and
  no requested instructions ⇒ risk_score→0.45, op_risk HIGH→LOW, DENY→DELIBERATE,
  misuse/actionability HIGH→MEDIUM.

q1–q6, q8–q12 and q17 feed `harmful_count`; q13 and the reputational cluster
q14–q16 are topic-only and excluded from the count (`:118-193`). The controller
can additionally raise the score with a sensitive-overlay floor
(`apply_risk_floor_if_sensitive`).

### 5.1 Prompt-caching system/user split (Part A)

To engage OpenAI automatic prompt caching, every deliberative LLM module and
call path was reordered so the system message is a byte-stable, path-specific
static prefix and the user message carries only per-request dynamic data
(`response_format` stays `json_object` everywhere; retries/parsers unchanged):
- **Risk minis** (`models/risk/prompts.py`): `INTENT_CONTEXT_SYSTEM_PROMPT` /
  `HARM_SIGNAL_SYSTEM_PROMPT` (composed by
  `models/risk/signals/prompt_renderer.get_harm_signal_prompts()`) /
  `OPERATIONAL_RISK_SYSTEM_PROMPT` carry all static procedure/schema text; user
  templates carry `REQUEST` (+ `constitution_context` for intent).
- **Critic** (`prompts/critic_prompt.py`): `CRITIC_FULL_SYSTEM_PROMPT` for the two
  FULL-critique call sites only. The quick-check fast path keeps its own
  short, UNCHANGED `CRITIC_SYSTEM_PROMPT` + `{"violated"}` contract
  (`runtime/modules/critic_module.py`) — never merged with the full-critique prompt.
- **Simulator** (`prompts/simulator_prompt.py`): path-specific
  `SIMULATOR_BATCH_SYSTEM_PROMPT` / `SIMULATOR_SEEDED_SYSTEM_PROMPT` (batch vs
  seeded contracts never collide).
- **Hindsight** (`runtime/modules/hindsight_module.py` /
  `prompts/hindsight_prompt.py`): `HINDSIGHT_SINGLE_SYSTEM_PROMPT` (single-scenario
  + non-batch aggregate) vs `HINDSIGHT_BATCH_SYSTEM_PROMPT` (`"evaluations"`-rooted).
- **Perspectives** (`prompts/perspectives_prompt.py`): `build_perspectives_system_prompt()`
  is now ctx-independent (static only); REQUEST/RESPONSE/risk signals moved into
  the per-perspective user message via `build_perspectives_user_prompt(...)`.
- **DomainPrefilter** (`constitution/retriever.py`): `DomainPrefilter._build_prefilter_system_prompt(domain_list)`
  composes a per-config-instance (not module-level constant) SYSTEM prompt —
  classifier role, `AVAILABLE DOMAINS` list rendered from the current
  keywords/descriptions, classification procedure, falsification checks,
  confidence scale, JSON schema. Byte-identical across requests while the
  domain config is unchanged (`set_domain_keywords`/`set_domain_descriptions`
  clear `self._cache` in lockstep with any prompt-byte change). The USER
  message is query-only: `f"USER QUERY:\n{query}"`. `_call_openai(prompt, *,
  system_prompt, retrieval_phase=...)` threads the same builder output into
  both the OpenAI system message and the persisted `system_prompt` (single
  source); the old hardcoded 11-word `sys_msg` is gone.
- **DCCL**: verified already static system + dynamic later messages — no code
  change (A6, verify-only).

Verified by `tests/test_static_prefix_stability.py`.

---

## 6. Constitution and overlays

- `ConstitutionStore` loads `data/core.yaml` plus per-domain overlays from
  `data/overlays/*.yaml`. Optional LLM-based principle matching
  (`use_llm_matching=True`).
- An overlay can declare `sensitive=true` (drives `is_overlay_sensitive` and the
  risk floor) and `sensitive_risk_floor`. Excluded domains short-circuit to a
  domain-exclusion response (`_route_domain_excluded`).
- `core` is retrieval-only and never becomes a runtime overlay
  (`_normalize_runtime_domain`, `controller.py:117-130`); `DomainPrefilter`
  independently excludes `core` from `domains_to_check` (`retriever.py:447`),
  so `core` never appears in the prefilter's `AVAILABLE DOMAINS` section either
  (`ALWAYS_EVALUATE = {"core"}`, `retriever.py:279`).
- `DomainPrefilter` sends a byte-stable SYSTEM prompt + query-only USER message
  (see §5.1) — cache-eligible for OpenAI automatic prompt caching, but an
  actual cache hit still depends on prompt length (>=1024 tokens) and provider
  conditions.
- The effective `max_parallel_agents` default is **4** (bumped from 2) across
  all runtime sources: `ConstitutionRetrieverConfig.max_parallel_agents`
  (`retriever.py:1099`), `ConstitutionStoreConfig.max_parallel_agents` /
  `ConstitutionStore.__init__` kwarg default (`store.py:462`,`:498` — the store
  overrides the retriever default on every store-mediated path), `CLIConfig.max_parallel_agents`
  (`cli/models.py:492`), and `resolve_constitution_max_parallel_agents`'s env
  fallback (`pipeline/deliberation_stack.py:64`, env var
  `MORALSTACK_CONSTITUTION_MAX_PARALLEL_AGENTS` still wins when set). With
  prefilter enabled (default), the wave is core + up to 3 domains, so 4 agents
  now run in a single `ThreadPoolExecutor` batch; on non-default paths
  (prefilter disabled, or legacy retrieval with core + every overlay) the same
  batch size applies to a larger wave — operators hitting provider throttling
  there can set the env override back to 2.

---

## 7. Deliberative modules & convergence

When `route == "deliberative"`, `DeliberationRunner` runs cycles (default
`max_deliberation_cycles=2`) over the following modules. **Modules only run
on the eligible deliberative route** — benign, safe_complete, ledger
fast-path, and compliance fast-path routes bypass them entirely. Individual
modules can also be absent, disabled, or skipped by timeout or gating:
- **Constitutional Critic** (`LLMConstitutionalCritic`) — principle violations.
  `quick_check(request, response, constitution, pre_retrieved_principles=None)`
  (FAST_PATH): when the runner supplies the risk-owned shared principles list,
  filters it to `level == "hard"` instead of self-retrieving (single retrieval
  per request, global scope); still falls back to the constitution's own top HARD
  constraints if the filtered shared list has zero HARD principles (never skips
  the check); self-retrieves as before when no shared list is supplied
  (fail-safe).
- **Consequence Simulator** (`LLMConsequenceSimulator`) — projected harm /
  expected valence. Runs in parallel with perspectives.
- **Perspectives Ensemble** — multi-stakeholder approval scores.
- **Hindsight Evaluator** — retrospective quality score.

The **Convergence Engine** decides the loop outcome (verified):
- `ConvergenceEvaluator.determine_decision` (`convergence_evaluator.py:314-519`)
  tallies weighted votes from critic/simulator/perspectives/hindsight into a
  `DecisionType` (PROCEED→CONVERGED, REVISE, REFUSE, CONTINUE, plus
  CONVERGED_WITH_SUGGESTIONS). The **simulator can never vote REFUSE** — REFUSE
  comes only from hard violations or a refuse-vote majority. Perspective weights:
  vulnerable 1.2, compliance 1.1, user/observer 1.0, adversary 0.8 (`:24-42`).
  A conservative cycle-1 early-convergence check can stop after one cycle
  (`_evaluate_cycle1_early_convergence`, `:168-301`).
- `enforce_convergence_invariants` (`convergence.py:19-65`) is the sole loop
  authority: CONVERGED ⇒ stop+converged; REFUSE ⇒ stop (`HARD_VIOLATION_STOP`);
  cycle≥max ⇒ stop (`CYCLES_EXHAUSTED`); else continue while cycles remain.

The controller emits a `DELIBERATION_AGGREGATE` decision trace
(`controller.py:721-786`). Fast paths (`run_fast_path`, `run_benign_fast_path`)
skip the deliberative loop.

---

## 8. SDK wrapper

`GovernedClient` is a transparent proxy: only `chat.completions.create()` is
intercepted; everything else passes through via `__getattr__`
(`wrapper.py:606-608`). On construction it generates a session `run_id` and (for
`db_only`/`dual`) ensures the DB schema + a `runs` row exist
(`wrapper.py:578-604`). After each call it flushes observability synchronously
(`wrapper.py:275-283`). Failure handling honors `config.failure_policy`
(`refuse` default, or `passthrough`).

---

## 9. OpenAI-compatible proxy

The supported HTTP OpenAI-compatible path is the **production proxy**:
`moralstack/server/proxy.py:create_app`. It is multi-turn aware: resolves
conversation_id (header → `extra_body` → lineage correlation), serializes
same-conversation requests with per-conversation locks, uses a `SessionStore`,
emits full observability, and routes REFUSE/SAFE_COMPLETE/NORMAL through governed
delivery. Serve it via `examples/server_quickstart.py` (uvicorn, **single
worker**; recommended command targets port 8080; `main()` reads env var
`MORALSTACK_OPENAI_COMPATIBLE_API_PORT`, defaulting to 8787). This is the path
recommended for COMPL-AI `llm_rules` and IFBench-style proxy runs.

See `docs/traces/openai_compatible_multiturn.md`.

---

## 10. Streaming behavior

- **SDK**: supported. Deliberation runs *before* streaming starts. `REFUSE`
  yields a single synthetic chunk (`GovernedRefusalStream`); otherwise the
  upstream stream is wrapped by `GovernedStreamResponse` with
  `governance_metadata` attached (`wrapper.py:186-251`).
- **Production proxy**: supported as governed synthetic SSE replay. Governance
  runs to completion first; `_build_synthetic_sse_response` replays the final
  governed answer as OpenAI-compatible `chat.completion.chunk` events without
  forwarding live upstream tokens.

---

## 11. Multi-turn behavior

- **Identity**: `conversation_id` + `turn_index`. SDK uses a per-`SessionState`
  counter (`next_turn_index`, `session.py:77-84`). Proxy derives `turn_index`
  statelessly as `user_message_count - 1` (`proxy.py:526-541`) and resolves
  `conversation_id` via header/extra_body/lineage.
- **State**: `ConversationGovernanceState` carries posture, last contract hash,
  hard constraints, and `turn_decisions_summary`. The controller extends it per
  turn (`_extend_state_out_v04`, `controller.py:478-543`).
- **Cache**: `SemanticDecisionLedger` can short-circuit deliberation on a
  same-conversation cache hit, gated by `ConversationalFastPathRunner.is_safe_to_apply`
  (cached REFUSE always applied; ESCALATED never cached; `turn_index < 1` skipped).
- **Risk in context**: history + developer contract are passed into the risk
  estimator so context-dependent prompts are not mis-scored
  (`controller.py:788-845`).
- **Request transcript**: SDK and proxy both attach `ConversationContext` to
  `ProcessedRequest`. DCCL and speculative generation can use a role-serialized
  transcript; risk/deliberative modules may use smaller declared windows.
- **Compliance delivery guard**: records governance/candidate context modes and
  blocks governed-draft reuse only when the reused draft was generated from a
  narrower last-user-only context than the governance transcript.

---

## 12. Observability & DB logging

- Modes (`MORALSTACK_OBSERVABILITY_MODE`): `file_only` (default), `db_only`,
  `dual`. DB path via `MORALSTACK_OBSERVABILITY_DB_PATH` (legacy
  `MORALSTACK_DB_PATH`).
- Async write queue + background worker for `ObservabilityService.emit*`;
  the worker owns a persistent SQLite connection and sets
  `PRAGMA synchronous=NORMAL` on that connection only. `flush()` drains queued
  writes for read-after-write callers. Lifecycle upserts and request finalization
  remain synchronous request-thread writes.
- Finalization uses `conversation_events.finalize_audit_sync` for both proxy and
  SDK: it writes `request.meta_updated` plus exactly-one
  `proxy.request_finalized` through resultful `router.route_audit_sync`. In
  `dual`, SQLite drives persisted/failed semantics and JSONL failures are counted
  separately; in `file_only`, JSONL drives the result and is synchronously
  attempted, not crash-durable.
- **SQLite tables** (`sinks/sqlite_sink.py:48-489`): `runs`, `requests`,
  `llm_calls`, `request_token_usage`, `orchestration_events`, `decision_traces`, `debug_events`,
  `exports_cache`, `conversation_states`, `ledger_events`,
  `session_store_events`, `proxy_request_events`. WAL + foreign keys enabled
  (`_get_connection`, `sinks/sqlite_sink.py:497-504`).
- `decision_traces` stores the whole `DecisionTrace` as a **`trace_json` blob**
  (`_DECISION_TRACES_INSERT`, `sinks/sqlite_sink.py:612-615`), not typed columns:
  adding a trace field is additive and needs no migration, but old rows simply
  lack the new key — readers must distinguish "absent" from a falsy value.
- **`DecisionTrace.sim_metrics_measured`** (`runtime/trace/decision_trace.py`)
  records whether a FINAL trace's `sim_*` metrics are a **measurement or
  defaults**. Those metrics cannot self-report it: `sim_semantic_expected_harm=0.0`
  + `sim_worst_harm=None` is produced both when nothing was measured and when a
  simulation was retained and every consequence was benign
  (`runtime/modules/simulator_module.py:669-684` skips `harm_type == "none"`, so
  `risk_records` ends up empty either way). It is **tri-state** (`bool | None`,
  default `None` = not asserted), written only by `_populate_trace_from_sim`
  (`orchestration/decision_service.py`) as `sim_result is not None` before its
  early return; `_log_final_trace` copies it onto the FINAL row. **It is NOT a
  "did the module execute" flag:** a full-parallel simulation runs but is
  discarded on a critic hard violation without merging `state.simulations`
  (`deliberation_runner._run_full_parallel_evaluation:2525-2533`), so the
  simulator can have executed while this reads `False` — correct, because those
  FINAL metrics are then defaults. A carried-forward result counts as measured.
  Never read it as a plain bool.
- **JSONL** sink writes the same event envelopes to
  `MORALSTACK_OBSERVABILITY_JSONL_DIR` (default `logs/observability`).
- Read contract: `SqliteReadStore` (`read_store.py`).
- **Context-shape telemetry**: `CONTEXT_SHAPE_RECORDED` orchestration events
  record context mode, raw/system/developer counts, available/used prior turns,
  truncation, `history_source`, and guard metadata. These fields are available
  from JSONL and SQLite event payload JSON; there are no dedicated typed columns
  or UI cards yet.

See `docs/traces/observability_db_to_ui.md`.

## 13. Filesystem logging

The JSONL sink (`sinks/jsonl_sink.py`) is the filesystem audit stream, active in
`file_only` and `dual` modes. Each emitted `EventEnvelope` becomes one JSON line.
`scripts/consolidate_jsonl_meta.py` post-processes JSONL meta. The UI reads from
SQLite only, **not** from JSONL — `file_only` runs are not visible in the
dashboard (see fragile areas).

## 14. UI integration

`moralstack-ui` (`ui/app.py`) is a FastAPI app with form-based auth
(`MORALSTACK_UI_USERNAME` / `_PASSWORD`). It requires a configured SQLite DB
(`get_db_path()`), and renders per-request and per-conversation views by reading
the observability tables and rebuilding the deliberation timeline / metro map.

Routes (`ui/app.py:1738-2185`): `/`, `/login`, `/logout`, `/auth-status`,
`/runs`, `/runs/{run_id}`, `/runs/{run_id}/requests/{request_id}`,
`/runs/{run_id}/requests/{request_id}/export.md`,
`/runs/{run_id}/export_benchmark.md`, `/conversations`,
`/conversations/{conversation_id}` (multi-turn timeline),
`/conversations/{conversation_id}/export.md` (AI Act art. 12 audit export).

Token usage per model is rendered on `/runs` (all runs), `/runs/{run_id}`,
`/conversations/{conversation_id}`, and `/runs/{run_id}/requests/{request_id}`
via `_token_usage_view()` + the shared `templates/_token_usage.html` partial,
backed by `SqliteReadStore.get_token_usage_by_model_{global,for_run,for_request,
for_conversation}` (billable-only; conversation variant joins `requests`).

**Governance nodes in the execution graph.** The flow graph is built from real
`llm_calls` plus *synthetic nodes* that surface governance steps which are not LLM
calls (so an auditor can read the concrete path a request took). Alongside the
existing `_build_synthetic_calibration_node` / `_build_synthetic_path_routing_node` /
`_synthetic_speculative_draft_reuse_from_events` / `_synthetic_upstream_provider_call_from_events`
/ `_synthetic_final_revalidation_call_from_events` / `_synthetic_constitution_call_from_traces`,
the following make otherwise-invisible steps explicit: `_synthetic_compliance_downgrade_nodes`
(one node per `COMPLIANCE_MATCH_DOWNGRADED`; the hard-signal gate `reason=hard_signal_evidence`
renders as a `safety_gate` alert node), `_synthetic_module_deferred_nodes`
(`MODULE_DEFERRED_TO_COMPLIANCE`), `_synthetic_ledger_fast_path_node`
(`LEDGER_FAST_PATH_APPLIED/NOT_APPLIED`), `_synthetic_convergence_node`
(`EARLY_CONVERGENCE_ACCEPTED/REJECTED`), and `_synthetic_module_skipped_nodes`
(`SIMULATOR_SKIPPED`/`CRITIC_SKIPPED`). All carry `module`, `sequence_in_cycle`
(placed in the module's canonical tier via `_SEQ_BY_DEFERRED_MODULE`) and
`io_annotations` so they render and expand like any node. `_build_path_badge_info`
distinguishes the P0 hard-signal block (`kind=compliance_blocked_p0`) from ordinary
downgrades. `compliance_layer` and `final_revalidation` now have a legend colour in
`main.css` + `request.html`. Tests: `tests/test_ui_tier_order.py`.

The request-detail page also surfaces per-call token cost inline: a `token_badge`
Jinja macro (`templates/request.html`) reads the numeric `llm_calls` columns on
each flow-graph node and journey step; `_module_summaries()` adds a per-module
token rollup; and a dedicated **Domain retrieval** section (`_domain_retrieval_view()`)
lists every `constitution_retriever` call (prefilter + per-domain agents) with its
domain, phase, model and tokens. Per-domain attribution relies on the `domain`
field that `constitution/retriever.py::_persist_constitution_llm_call` now writes
into `parsed_summary_json` for the enhanced/legacy domain agents.

**Prompt-cache observability.** `llm_calls.cached_input_tokens` (nullable) records the
provider-reported cached prefix per call, extracted by
`observability/token_usage.py::extract_cached_input_tokens` and threaded through
`GenerationResult` → the module report objects (`CriticReport`, `SimulationResult`,
`HindsightResult`, `PerspectiveResult`/`EnsembleResult`), which copy token fields
rather than forwarding the `GenerationResult`. `NULL` means "provider reported
nothing" and `0` means "measured cache miss"; the two are never merged.
`get_token_usage_breakdown` and the per-model aggregations expose
`cached_input_tokens` + `cached_usage_known`. The UI shows the hit rate at every scope
where token metrics already appear: the shared per-model panel (4 scopes), the
per-module rollup, the per-call badge, and the Domain retrieval table — `—` when
unknown, `0.0%` when measured. Contract and hit-rate caveats:
`docs/modules/observability.md`.

**Request-spine completeness (`request.html`, `.final-decision-grid`).** The
OUTPUT anchor of the deliberation spine now also renders `activated_signals`
(labelled "Risk signals (activated)") and, when non-empty, `hard_violation_codes`
(§5 #3 — additive; the "Relevant constitutional principles" card at `:1137` still
renders the same codes, unmoved). `Final Risk Score` gates on `is not none`
(fixing a latent falsy-gate bug: `risk_score=0.0` would have been silently
dropped). `Semantic Harm` stays on HEAD's `sim_semantic_expected_harm`
truthiness gate — an evidence gate keyed on `sim_worst_harm` was proposed and
**dropped** (the field is not a "metrics measured" marker). That marker now
exists: `DecisionTrace.sim_metrics_measured` (see §12), persisted on FINAL traces
from 2026-07-17. The UI does **not** yet gate on it — that was deliberately left
out of this branch, and rows written before 2026-07-17 carry no
`sim_metrics_measured` key, so a UI gate must treat absent/None as "unknown",
never as "not measured". Tests: `tests/test_ui_final_decision_completeness.py`.

**Conversation-level spine (`/conversations/{id}`, `conversation.html`).** The
horizontal "conversation strip" is replaced by a vertical spine: a first-turn
node (developer-contract / conversation-history chips, built by calling
`_build_input_anchor_info` verbatim — the same function the request page uses,
so branch order/invariants 31-34 hold by construction), one node per turn
(decisional input → decision → response outcome, linking to the request page),
and a terminal node folding the already failure-aware conversation aggregates
(`overview.last_posture`/`final_actions`/`max_risk_score` +
`pipeline_failure_action_counts`/`max_risk_is_fail_closed`/
`last_posture_is_from_pipeline_failure`). New `_build_conversation_spine_node`
(`app.py`, placed immediately before `_build_conversation_timeline`) is called
inside that function's existing per-turn loop, so it reuses the `turn_traces`
already fetched there (no new N+1) plus one best-effort per-turn
`get_orchestration_events_for_request` call (wrapped in `try/except`, §5 #6 —
one malformed turn cannot break the page). Connectors assert only persisted
evidence: a cache-reuse link (`state.cached_from_turn`, "reused decision from
turn N"), a posture transition, or a bare pipe; colliding `turn_index` renders a
dashed `.conv-spine-pipe--unordered` divider ("order not established"), never an
invented sequence. `meta_json.parent_request_id` is **never** used for ordering
— it is 100% self-referential on conversation-turn rows (proxy/SDK pass the
current request id as the parent); order is the existing `seq_pos`
(`turn_index ASC, created_at ASC`). Risk renders as an exact value + a
proportional bar, a deliberate substitution for the strip's height encoding (no
sparkline). The posture-timeline table and per-turn detail cards survive,
collapsed into `<details>` (invariant 23). Tests:
`tests/test_ui_conversation_spine.py`, `tests/test_ui_conversation_spine_affordances.py`
(parity rewrite of the retired `tests/test_ui_conversation_strip.py`), extended
`tests/test_ui_conversation_views.py` / `tests/test_ui_conversation_turn_collision.py`.

---

## 15. COMPL-AI integration points

There is **no `compl-ai` package** in this repo. COMPL-AI integrates from the
outside by pointing its OpenAI-compatible client at the MoralStack proxy. The
codebase contains targeted accommodations for it:
- `server/conversation_correlation.py` exists specifically because COMPL-AI
  `llm_rules` resends the full history with no stable `conversation_id`
  (module docstring).
- `controller._estimate_risk` passes the developer contract + history to the
  risk estimator citing "compl-ai llm_rules-benign Q74" (`controller.py:797-799`).
- `examples/server_quickstart.py` documents the recommended COMPL-AI launch
  (uvicorn, single worker, port 8080).
- `scripts/benchmark_moralstack.py` is MoralStack's **own** 84-question
  benchmark, separate from COMPL-AI.

See `docs/traces/complai_llm_rules_flow.md`.

---

## 16. Test layout

`tests/` (~120 modules). Notable groups:
- Decision/policy: `test_decide_action.py`, `test_decision_policy.py`,
  `test_safe_complete_*.py`, `test_decision_correctness.py`.
- Governance invariants: `tests/governance_invariants/` (e.g.
  `test_q17_hard_signal_invariant.py`).
- Risk: `test_risk*.py`, `test_calibration_guard.py`, `test_signal*.py`,
  `test_axis_mapping.py`.
- Multi-turn / ledger / session: `test_ledger*.py`, `test_session_store.py`,
  `test_conversation_state_v04.py`, `test_multiturn_context_propagation.py`,
  `test_conversational_fast_path.py`.
- SDK: `test_sdk_*.py` (wrapper, session, stream, dccl, integration, …).
- Server/proxy: `test_server_proxy.py`, `test_conversation_correlation.py`,
  `test_server_fingerprint.py`.
- Observability: `test_observability_*.py`, `test_persistence_*.py`, `test_observability_config.py`.
- UI/reports: `test_ui_*.py`, `test_reports.py`, `test_conversation_export.py`.
- Byte-equality: `test_system_prompt_byte_equality.py`,
  `test_system_prompt_resolver.py`.
- Compliance: `test_compliance_evaluation.py`, `test_sdk_dccl.py`.
- E2E payloads in `tests/e2e_payloads/`; regression in `tests/e2e_run_regression.py`.
- AI harness: `tests/harness/` — offline unit tests for the `.claude/hooks/*`
  scripts (stop-gate verify dedup + docs-gate/nudge cap, PreCompact snapshot,
  SessionEnd diary, UserPromptSubmit, fail-open on malformed input). Not
  governance code; do not confuse with the 84-question benchmark.

---

## 17. Known fragile areas

- **Lineage-based conversation correlation can still collide within a principal.**
  Two samples with byte-identical histories (and identical assistant outputs) **for
  the same principal** map to the same `conversation_id` (`conversation_correlation.py`
  docstring + `resolve`) — this is the central COMPL-AI risk (see
  `docs/traces/complai_llm_rules_flow.md`). As of P3 / P0-3 / A3, this no longer
  crosses tenant/principal boundaries: the internal lineage map is keyed by
  `(principal, history_hash)`, so identical histories from *different* principals
  never collide (see `docs/traces/openai_compatible_multiturn.md`).
- **`ConversationLockManager._locks` growth is still unbounded (deferred).** One
  `threading.Lock` is created per distinct `conversation_id` and never removed
  (`proxy.py:83` area, `# TODO(P3-followup)`); the correlation store's TTL/max-entries
  bound already removes the dominant growth source (~2 entries/turn vs 1 lock/conversation).
  A safe idle-prune has a real race (see the plan for P3 / P0-3 / A3) and is a follow-up.
- **UI requires SQLite.** `file_only` runs never appear in the dashboard; the UI
  reads only the DB.
- **Cache governance.** Ledger fast-path can reuse a prior decision; the
  `is_safe_to_apply` gate is what prevents unsafe reuse. Changes there are P0.
- **`controller.process()` is very large** (~2498 lines) with many interleaved
  early returns and best-effort emit blocks — read the whole method before
  editing routing.
- **Do not bypass `ConversationContext` in entry points.** SDK and proxy must use
  the shared builder so DCCL, speculative generation, risk context, and audit
  metadata agree about the same request transcript.
