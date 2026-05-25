# Baseline Digest

This digest is generated from the trusted adversarial documentation baseline. It is task-specific and must be treated as architectural context, not as a substitute for current code verification.

## Task Keywords

task, multi-turn, context, alignment, across, proxy, governance, modules, final, delivery, objective, moralstack, handling, reason, over, same, materially, relevant

## Trust Policy

```json
{
  "documentation_is_primary_for_architectural_intent": true,
  "code_is_primary_for_current_runtime_behavior": true,
  "unresolved_doc_code_conflict_blocks_final_plan": true,
  "final_plan_must_reference_baseline": true,
  "final_plan_must_include_documentation_updates": true
}
```

## Document: CLAUDE.md

- Role: `agent_operating_rules`
- Authority: `high`
- SHA256: `4bf55b453aaaf0f5f9a0d7d5ef251ea665b7b9fea4b26969df96868be8458c60`

### Relevant Extract

## 5. Critical MoralStack invariants (do not break)

These are load-bearing. If a change appears to require breaking one, stop and
surface it to the user rather than working around it.

1. **Decision/generation separation.** The policy layer decides
   `final_action ∈ {NORMAL_COMPLETE, SAFE_COMPLETE, REFUSE}`; generation
   produces text *within* that decision. `final_action` is computed from
   structured signals, **never inferred from response text or disclaimers**.
   Action bounds are defined in `moralstack/runtime/decision/safe_complete_policy.py`
   (`compute_action_bounds`, `decide_final_action`). The runtime final action is
   assembled by `orchestration/decision_service.py` (which adds narrow exception
   handling in `_handle_hard_violations` for crisis-support and regulated-info
   cases) and may be post-gated by `orchestration/safe_complete_gating.py`.
2. **System-prompt transparency.** The developer-declared system prompt is
   never mutated by governance. `SAFE_COMPLETE` guidance is appended as an
   extra trailing `user` message (`_build_safe_complete_user_turn` in
   `moralstack/sdk/wrapper.py`). Preserve this byte-equality.
3. **Hard-signal supremacy (P0).** Hard topical signals (self-harm, child
   safety, weapons, physical harm) must not be overridable by a developer
   contract, a domain overlay, or a cached ledger decision. See
   `path_router.is_hard_signal_refuse` and the DCCL Safety Override
   (`moralstack/compliance/safety_override.py`).
4. **Single-turn byte-equality.** With no `developer_contract` and no
   `conversation_history`, pipeline behavior must stay byte-identical to the
   single-turn baseline (see `tests/test_system_prompt_byte_equality.py`).
5. **`core` is retrieval-only.** The `core` constitution is never a runtime
   domain overlay (`_normalize_runtime_domain` in
   `moralstack/orchestration/controller.py`).
6. **Observability never breaks the request.** All telemetry is best-effort and
   wrapped in swallowing try/except. Never let an audit/log failure change a
   governance decision or raise into the caller.
7. **REFUSE does not call the wrapped/upstream generation client.** On `REFUSE`
   the wrapped SDK client / proxy upstream generation client is not invoked
   (`wrapper.py:333-345`, `server/proxy.py:312-322`). Internal MoralStack LLM
   calls — risk mini-estimators, a possibly in-flight speculative draft
   (`controller.py:847-964`), and refusal wording generation
   (`orchestration/refusal_handler.py:94-104`) — may still occur.

---

## 7. Testing expectations

- Tests live in `tests/` and are extensive (~120 files). Run the relevant
  subset for any change, and the full suite before declaring a task done:
  `python -m pytest` (or a scoped `python -m pytest tests/test_<area>.py`).
- Behavior-locking tests exist for: byte-equality
  (`test_system_prompt_byte_equality.py`), governance invariants
  (`tests/governance_invariants/`), decision policy (`test_decide_action.py`,
  `test_safe_complete_*.py`), observability contracts
  (`test_observability_*.py`), proxy/correlation (`test_server_proxy.py`,
  `test_conversation_correlation.py`), and the ledger (`test_ledger*.py`).
- Do **not** weaken or delete a test to make a change pass. If a test must
  change, justify why in the PR/commit message (see §8).
- Tests that hit the network/OpenAI use doubles/mocks; keep new tests offline
  and deterministic.

---

## 8. Documentation update expectations

When you change behavior, update the docs in the **same** change:

- New/changed module, flow, or invariant → update
  `docs/MORALSTACK_CODEBASE_INDEX.md`.
- New verified fact, or a fact you proved wrong → update
  `docs/CODEBASE_FACTS.md` (and move items out of the hypotheses section as you
  verify them).
- Changed governance flow, multi-turn handling, observability schema, or the
  COMPL-AI bridge path → update the matching file in `docs/TRACES/`.
- Module-level behavior also has long-form docs in `docs/modules/*.md`; update
  the relevant one if you touch that module's contract.

---

# CLAUDE.md — Operating rules for AI agents working in MoralStack

This file governs how any AI assistant (Claude or otherwise) must behave when
working in this repository. It is **operating discipline only** — architecture
lives in the documents linked at the bottom.

MoralStack is a *governance engine* for LLMs. Its decisions decide whether a
model is allowed to answer. Bugs here are not cosmetic: they change refusal
behavior, leak information, or corrupt audit trails used for AI Act compliance.
Treat every change as safety-relevant until proven otherwise.

---

---

## Reference documents

- `docs/MORALSTACK_CODEBASE_INDEX.md` — architecture & file map.
- `docs/CODEBASE_FACTS.md` — verified facts ledger + hypotheses.
- `docs/TRACES/governance_decision_flow.md` — end-to-end decision flow.
- `docs/TRACES/openai_compatible_multiturn.md` — OpenAI-compatible bridge & multi-turn.
- `docs/TRACES/observability_db_to_ui.md` — logging → DB/JSONL → UI.
- `docs/TRACES/complai_llm_rules_flow.md` — COMPL-AI / llm_rules benchmark path & risks.
- Existing long-form docs: `docs/architecture_spec.md`, `docs/decision_policy.md`,
  `docs/constitution.md`, `docs/multiturn_design.md`, `docs/modules/*.md`.

---

## 1. Read before you write

- **Never edit a file you have not read in full** (or read the complete
  relevant region — large files like `moralstack/orchestration/controller.py`
  and `moralstack/ui/app.py` are >2000 lines and are paged by the Read tool;
  read every page that touches your change).
- Before changing behavior, read the **call sites** and the **tests** that
  exercise it. The `tests/` directory is large and behavior-locking — assume a
  test pins the behavior you are about to change.
- For any subsystem, start from the index: `docs/MORALSTACK_CODEBASE_INDEX.md`.
  Confirm the file/function still exists before relying on it — the index is a
  snapshot and the code is authoritative.

---

## 2. Audit before you patch

- Reproduce or precisely locate the problem first. Quote the exact file and
  line that causes it. Do not patch a symptom in a different layer than the
  cause.
- Trace the data path end to end before editing. The relevant traces are in
  `docs/TRACES/`. If your change touches governance routing, multi-turn, or
  observability, re-read the matching trace document.
- Identify every caller and every persisted side effect (DB rows, JSONL
  envelopes, emitted events) before changing a function signature or a payload
  shape.

---

## 4. Facts vs. hypotheses (keep them separate)

- State **facts** only when you have read the supporting code. Everything else
  is a **hypothesis** and must be labelled as such.
- `docs/CODEBASE_FACTS.md` is the verified ledger. Anything not yet verified
  belongs in its "Hypotheses / Unverified assumptions" section, never in the
  facts table.
- If you discover that a documented fact is wrong, fix the document in the same
  change and note it (see §9).

---

## 6. No broad refactoring unless explicitly requested

- Make the smallest change that fixes the task. Do not rename, reorganize, or
  "tidy" adjacent code.
- Do not introduce abstractions for hypothetical future needs.
- The codebase uses mixed Italian/English in older comments and docstrings.
  Do **not** mass-translate or reformat. New comments/docs must be English
  (per `.cursor/rules/`), but leave existing text alone unless it is in scope.

---

## 9. Error-correction protocol

- If you made a wrong edit, **revert or correct it explicitly** and say so.
  Do not silently layer a second fix on top.
- If you find a defect outside your task scope, note it (and add it to the
  hypotheses section of `docs/CODEBASE_FACTS.md` if unverified) rather than
  fixing it without being asked.
- If a documented statement contradicts the code, the **code wins**. Correct
  the document and flag the discrepancy in your summary.
- Never use destructive shortcuts to make an obstacle disappear (no
  `--no-verify`, no deleting failing tests, no force-push). Find the root cause.

---

## 10. Expected response format for future sessions

When working a task in this repo, structure your reply so a reviewer can audit
it cold:

1. **Goal** — one line: what you were asked to do.
2. **Evidence** — the specific files/functions you read, cited `path:line`,
   and what they told you. Separate **facts** (verified) from **hypotheses**.
3. **Change** — what you edited and why, smallest-diff first. Note any
   invariant from §5 that the change touches and how it stays intact.
4. **Verification** — exact tests/commands run and their real outcome. If you
   could not verify something (e.g. no API key, no UI), say so explicitly.
5. **Docs** — which of the §8 documents you updated.
6. **Open questions / risks** — anything unverified, plus follow-ups.

Keep it terse. Do not claim success you did not observe.

---

## Document: docs/MORALSTACK_CODEBASE_INDEX.md

- Role: `codebase_index`
- Authority: `high`
- SHA256: `b4fd8729a18d91514f4f1d468ee0d8a2a04ea4eea747c0a0b741c1b790b22c96`

### Relevant Extract

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
  observability/         # telemetry service, sinks (SQLite/JSONL), read store
  persistence/           # DB/file persistence ports used by the controller
  pipeline/              # context builder + deliberation stack assembly
  prompts/               # module prompt templates
  reports/               # markdown/conversation/benchmark export + UI data builders
  server/                # OpenAI-compatible FastAPI governance proxy
  ui/                    # FastAPI dashboard (moralstack-ui)
  cli/                   # `moralstack` CLI
  utils/                 # env loading, caching, output protection, json helpers
  core/                  # shared types/schema
scripts/                 # benchmark, standalone bridge, inspector, install
examples/                # runnable usage examples
tests/                   # ~120 test modules + e2e payloads
docs/                    # architecture, modules, traces, this index
```

Python `>=3.11` (`pyproject.toml:11`). Runtime deps: `openai>=2.24`, `pydantic>=2`,
`python-dotenv`, `ruamel.yaml`, `langdetect`. UI/server extras add `fastapi`,
`uvicorn`, `httpx`, `jinja2` (`pyproject.toml:27-56`).

---

### Orchestration — `moralstack/orchestration/`
- `controller.py` — `OrchestrationController.process(...)` is the governance
  runner core (file is ~2498 lines). Owns risk estimation, speculative overlap,
  DCCL invocation, routing, ledger lookup/store, conversation-state extension,
  and event emission.
- `decision_service.py` — `decide_action(request, risk_proto, …)` →
  `(Decision, DecisionExplanation)`.
- `path_router.py` — `get_route(...)` → `(route, borderline_refuse, risk_policy_action)`;
  `is_hard_signal_refuse(...)`.
- `safe_complete_gating.py` — `apply_safe_complete_gating(...)`.
- `deliberation_runner.py` — `DeliberationRunner` (cycles, convergence,
  `run_fast_path`, `run_benign_fast_path`).
- `convergence.py`, `convergence_evaluator.py` — convergence engine.
- `conversation_state.py` — `ConversationGovernanceState`, `TurnDecisionSummary`.
- `conversational_fast_path.py` — `ConversationalFastPathRunner` (cache-driven skip).
- `ledger.py`, `ledger_storage.py` — `SemanticDecisionLedger`, `CachedDecision`,
  `LedgerResult`.
- `refusal_handler.py`, `refusal_context.py`, `safe_refusal_generator.py` — refusal text.
- `response_assembler.py` — `ResponseAssembler` builds the `FinalResponse`.
- `speculative_overlap.py` — `SpeculativeOverlapHandle` (parallel draft + risk).
- `system_prompt_resolver.py` — `effective_system_for_request(...)`.
- `overlay_policy.py` — `is_overlay_sensitive`, `apply_risk_floor_if_sensitive`,
  `is_domain_excluded`, `get_constitution_safe`, `OVERLAY_SENSITIVE_RISK_FLOOR`.
- `process_context.py` — `ProcessCallContext` (per-call mutable carrier).
- `types.py` — `ProcessedRequest`, `OrchestratorResult`, `Decision`,
  `FinalResponse`, `ResponseMetadata`, `OrchestratorConfig`, errors.
- `contract.py` — `DeveloperContract` (`from_text`, `contract_hash`, `structured_rules`).
- `orchestration_event_taxonomy.py` — canonical event-type constants.

---

### SDK — `moralstack/sdk/`
- `wrapper.py` — `govern(client, config=None)` wraps any OpenAI-compatible client
  and returns `GovernedClient`. `GovernedCompletions.create()` runs deliberation
  *before* delegating to the wrapped client. Helpers: `_extract_last_user_message`,
  `_extract_developer_contract` (last `system` message wins, `mode="opaque"`),
  `_messages_to_turns`, `_build_safe_complete_user_turn`.
- `bootstrap.py` — `_bootstrap_pipeline(config)` builds the `Orchestrator`;
  `_resolve_model(config)` resolves the generation model.
- `config.py` — `GovernanceConfig` (domain_overlay, failure_policy,
  observability_mode, jsonl_dir, enable_session_tracking, …).
- `session.py` — `SessionState`: per-client conversation_id + turn counter,
  wraps a `SessionStore`.
- `session_store.py` — `SessionStoreProtocol`, `InMemorySessionStore`.
- `response.py` — `GovernedResponse`, `GovernanceMetadata` (`final_action`,
  `risk_score`, `risk_category`, `path`, `reason_codes`, `triggered_principles`,
  `conversation_id`, `turn_index`, …). Constructors: `from_normal`, `from_safe`,
  `from_refusal`, `from_passthrough`, `from_pipeline_error`.
- `errors.py` — `GovernanceError` + subclasses.

---

### Console entry points (`pyproject.toml:58-62`)

| Script | Target | Notes |
|---|---|---|
| `moralstack` | `moralstack.cli.run:main` | CLI runner |
| `moralstack-ui` | `moralstack.ui.app:main` | dashboard |
| `moralstack-server` | `moralstack.server.proxy:main` | **reserved** — `main()` raises `NotImplementedError`; use `create_app` instead (`server/proxy.py:777`) |
| `moralstack-validate-overlay` | `moralstack.cli.validate_overlay:main` | overlay validation |

---

---

## 3. Runtime governance flow (high level)

`govern()` → `GovernedClient.chat.completions.create()` → `Orchestrator.process()`
→ `OrchestrationController.process()` (`orchestration/controller.py:1885`).

Inside `process()` (order verified in source):
1. Normalize request; build `ProcessCallContext`; set session/turn context vars;
   pre-insert the `requests` row (`controller.py:1900-1923`).
2. **Risk estimation** — speculative overlap (risk + draft in parallel) when
   `enable_speculative_generation`, else direct `_estimate_risk`
   (`controller.py:1928-1935`).
3. **DCCL evaluation** on the (possibly non-blocking) speculative draft
   (`_run_dccl_evaluation`, `controller.py:1936`). On `MATCH` with a validated
   draft → **compliance fast-path** (`_route_compliance_match`), skipping risk
   routing and deliberation (`controller.py:1941-2040`).
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

See `docs/TRACES/governance_decision_flow.md` for the full trace.

---

---

### Observability — `moralstack/observability/`
- `service.py` — `ObservabilityService`, singleton via `get_obs()` / `obs`.
  `emit`/`emit_batch` are async fire-and-forget; `flush()` at request boundary.
- `router.py` — dispatch by mode (`db_only` → SQLite, `file_only` → JSONL,
  `dual` → both).
- `sinks/sqlite_sink.py` — schema + writers (`init_db`, `create_run`,
  `upsert_request`, `update_request_*`, `delete_*`). Tables in §8.
- `sinks/jsonl_sink.py` — JSONL envelope writer.
- `read_store.py` — `SqliteReadStore` (read contract used by UI & exports).
- `conversation_events.py` — `emit_conversation_state_updated`,
  `emit_proxy_request_finalized`.
- `governance_audit.py` — `finalize_governance_audit`, `posture_of`,
  `state_summary_or_none`.
- `context.py` — contextvars (`set_current_run_id`, `set_current_request_id`,
  `set_current_session_id`, `set_current_turn_number`).
- `config.py` — `get_observability_mode`, `get_db_path`.

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

Routing consequences (`sdk/wrapper.py`, `server/proxy.py`):

| `final_action` | SDK behavior | Proxy behavior |
|---|---|---|
| `NORMAL_COMPLETE` | call wrapped client with original kwargs | forward original body (or reuse governed draft on `COMPLIANCE_FAST_PATH`) |
| `SAFE_COMPLETE` | append synthetic guidance `user` turn, then call client | append synthetic guidance `user` turn, then forward |
| `REFUSE` | return refusal text; **client not called** | return synthetic `chat.completion` (finish_reason `content_filter`); **upstream not called** |

---

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
- Observability: `test_observability_*.py`, `test_persistence_*.py`.
- UI/reports: `test_ui_*.py`, `test_reports.py`, `test_conversation_export.py`.
- Byte-equality: `test_system_prompt_byte_equality.py`,
  `test_system_prompt_resolver.py`.
- Compliance: `test_compliance_evaluation.py`, `test_sdk_dccl.py`.
- E2E payloads in `tests/e2e_payloads/`; regression in `tests/e2e_run_regression.py`.

---

---

## 17. Known fragile areas

- **Proxy streaming is unsupported (verified).** `server/proxy.py` has no
  `stream` branch; `_build_upstream_kwargs` keeps `stream` in the body, the
  upstream `Stream` object has no `model_dump`/`to_dict`, so
  `_serialize_upstream_response` returns `{"raw": str(...)}` — a non-OpenAI body
  with no streaming (`proxy.py:750-774`). No test exercises this. Use the SDK for
  streaming.
- **Lineage-based conversation correlation can collide.** Two samples with
  byte-identical histories (and identical assistant outputs) map to the same
  `conversation_id` (`conversation_correlation.py` docstring + `resolve`). This
  is the central COMPL-AI risk — see `docs/TRACES/complai_llm_rules_flow.md`.
- **Two bridges, different semantics.** `scripts/openai_compatible_server.py` is
  single-turn and ignores history; `server/proxy.py` is multi-turn. Choosing the
  wrong one silently changes multi-turn behavior.
- **UI requires SQLite.** `file_only` runs never appear in the dashboard; the UI
  reads only the DB.
- **Cache governance.** Ledger fast-path can reuse a prior decision; the
  `is_safe_to_apply` gate is what prevents unsafe reuse. Changes there are P0.
- **`controller.process()` is very large** (~2498 lines) with many interleaved
  early returns and best-effort emit blocks — read the whole method before
  editing routing.

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

---

---

## 9. OpenAI-compatible bridge

Two distinct implementations — do not confuse them:

1. **Production proxy** — `moralstack/server/proxy.py:create_app`. Multi-turn
   aware: resolves conversation_id (header → `extra_body` → lineage correlation),
   serializes same-conversation requests with per-conversation locks, uses a
   `SessionStore`, emits full observability, routes REFUSE/SAFE_COMPLETE/NORMAL.
   Served via `examples/server_quickstart.py` (uvicorn, **single worker**;
   recommended command targets port 8080; `main()` reads env var
   `MORALSTACK_OPENAI_COMPATIBLE_API_PORT`, defaulting to 8787). This is the
   path recommended for COMPL-AI `llm_rules`.
2. **Standalone bridge** — `scripts/openai_compatible_server.py` (port 8787).
   Single-turn only: extracts the last user message and calls
   `orchestrator.process(request)` with **no** conversation_id/turn_index and
   **no** conversation history. Returns the governed `result.response.content`
   directly (not a fresh upstream generation). Creates a new `run` per request;
   bounds concurrency with an asyncio semaphore.

See `docs/TRACES/openai_compatible_multiturn.md`.

---

---

## 10. Streaming behavior

- **SDK**: supported. Deliberation runs *before* streaming starts. `REFUSE`
  yields a single synthetic chunk (`GovernedRefusalStream`); otherwise the
  upstream stream is wrapped by `GovernedStreamResponse` with
  `governance_metadata` attached (`wrapper.py:186-251`).
- **Production proxy**: **no streaming branch.** `_build_upstream_kwargs` does
  not strip `stream`, and responses are serialized via `model_dump()`
  (`proxy.py:750-774`). Streaming through the proxy is therefore unsupported (see
  fragile areas, §14).
- **Standalone bridge**: accepts a `stream` field but always returns a complete
  non-streamed JSON body (`scripts/openai_compatible_server.py:81,347`).

---

---

### Server proxy — `moralstack/server/`
- `proxy.py` — `create_app(openai_client, orchestrator, config, session_store)`
  returns a FastAPI app exposing `POST /v1/chat/completions`, `/chat/completions`,
  `GET /healthz`. `ConversationLockManager` (per-conversation locks),
  `_handle_chat_completion_sync` (runs in a threadpool).
- `conversation_correlation.py` — `ConversationCorrelationStore` (lineage hashing
  → conversation_id) + `canonical_history_hash`, `canonical_parent_history_hash`.
- `headers.py` — `build_governance_headers` (X-Moralstack-* response headers).
- `fingerprint.py` — request fingerprinting.

---

## 6. Constitution and overlays

- `ConstitutionStore` loads `data/core.yaml` plus per-domain overlays from
  `data/overlays/*.yaml`. Optional LLM-based principle matching
  (`use_llm_matching=True`).
- An overlay can declare `sensitive=true` (drives `is_overlay_sensitive` and the
  risk floor) and `sensitive_risk_floor`. Excluded domains short-circuit to a
  domain-exclusion response (`_route_domain_excluded`).
- `core` is retrieval-only and never becomes a runtime overlay
  (`_normalize_runtime_domain`, `controller.py:117-130`).

---

---

## 12. Observability & DB logging

- Modes (`MORALSTACK_OBSERVABILITY_MODE`): `file_only` (default), `db_only`,
  `dual`. DB path via `MORALSTACK_OBSERVABILITY_DB_PATH` (legacy
  `MORALSTACK_DB_PATH`).
- Async write queue + background worker; `flush()` at request/SDK boundary.
- **SQLite tables** (`sinks/sqlite_sink.py:48-489`): `runs`, `requests`,
  `llm_calls`, `orchestration_events`, `decision_traces`, `debug_events`,
  `exports_cache`, `conversation_states`, `ledger_events`,
  `session_store_events`, `proxy_request_events`. WAL + foreign keys enabled
  (`_get_connection`, `sinks/sqlite_sink.py:497-504`).
- **JSONL** sink writes the same event envelopes to
  `MORALSTACK_OBSERVABILITY_JSONL_DIR` (default `logs/observability`).
- Read contract: `SqliteReadStore` (`read_store.py`).

See `docs/TRACES/observability_db_to_ui.md`.

---

### CLI / scripts
- `moralstack/cli/run.py`, `shell.py`, `loader.py`, `report.py`, `visualizer.py`.
- `scripts/benchmark_moralstack.py` — internal 84-question benchmark.
- `scripts/openai_compatible_server.py` — **standalone single-turn** OpenAI bridge
  (distinct from `server/proxy.py`; see §10).
- `scripts/inspect_multiturn_trace.py` — multi-turn inspector CLI.
- `scripts/mstack_run.py`, `consolidate_jsonl_meta.py`, `install.py`.

---

## Document: docs/CODEBASE_FACTS.md

- Role: `verified_facts`
- Authority: `high`
- SHA256: `14bed7de88725fa179c3ea3095e3500c0823c0f464515572de4321ce68a57b63`

### Relevant Extract

## Verified facts

| Fact | Evidence file/function | Confidence | Notes |
|---|---|---|---|
| Package version is `0.5.0`; requires Python >=3.11 | `pyproject.toml:7,11` | High | |
| Public SDK surface is `govern`, `GovernedClient`, `GovernanceConfig`, `GovernedResponse`, `GovernanceMetadata`, error types; lazily imported from `moralstack.sdk` | `moralstack/__init__.py:38-64`, `moralstack/sdk/__init__.py:7-22` | High | |
| `govern(client, config=None)` wraps any client exposing `.chat.completions.create()`; returns `GovernedClient` | `sdk/wrapper.py:616-661` | High | duck-typed check at `:652` |
| Only `chat.completions.create()` is intercepted; all other attributes pass through | `sdk/wrapper.py:606-608` (`GovernedClient.__getattr__`) | High | |
| Deliberation runs before any upstream generation; routing depends on `final_action` | `sdk/wrapper.py:285-403` | High | |
| `final_action` ∈ {NORMAL_COMPLETE, SAFE_COMPLETE, REFUSE} computed from structured signals, not text. Action bounds defined in `safe_complete_policy.py`; runtime final action assembled by `decision_service.py` and post-gated by `safe_complete_gating.py` | `runtime/decision/safe_complete_policy.py:158-285`; `orchestration/decision_service.py:493-579`; `orchestration/safe_complete_gating.py:86-171` | High | |
| Action ordering is `NORMAL_COMPLETE < SAFE_COMPLETE < REFUSE` | `runtime/decision/safe_complete_policy.py:38-41` | High | `Action` enum |
| Hard violations, `clearly_harmful`, or op_risk HIGH force REFUSE bounds in `compute_action_bounds` | `runtime/decision/safe_complete_policy.py:167-176` | High | `decision_service._handle_hard_violations` (`decision_service.py:493-579`) has three narrow exceptions that return SAFE_COMPLETE instead of REFUSE: (1) MH.CRISIS.1 + `crisis_support` request type; (2) risk_score<0.5 + op_risk NONE + non-operational + domain_regulated; (3) pre-policy action was SAFE_COMPLETE + risk<0.5 + non-operational + no requested_instructions |
| HIGH `actionability_risk` forces SAFE_COMPLETE (does not override REFUSE) | `runtime/decision/safe_complete_policy.py:183-190` | High | |
| Gray-zone `potentially_harmful` defaults to NORMAL_COMPLETE to reduce false positives | `runtime/decision/safe_complete_policy.py:264-285` | High | |
| On REFUSE the wrapped SDK client / proxy upstream generation client is NOT called | `sdk/wrapper.py:333-345`; `server/proxy.py:312-322` | High | Internal MoralStack LLM calls may still occur: risk mini-estimators, a possibly in-flight speculative draft (`controller.py:847-964`), and refusal wording generation (`refusal_handler.py:94-104`) |
| SAFE_COMPLETE appends a synthetic trailing `user` turn; system prompt left byte-identical | `sdk/wrapper.py:147-178,347-378`; `server/proxy.py:324-336` | High | `_build_safe_complete_user_turn` |
| Developer contract = last `system` message, `mode="opaque"`; None if absent | `sdk/wrapper.py:51-93` | High | |
| `Orchestrator` is a facade delegating to `OrchestrationController` | `runtime/orchest

[... trimmed ...]

count≥3 / q5_physical / q17 / (priority_harm_count≥1 + operational signal) / score≥0.85 / clear_harm ⇒ CLEARLY_HARMFUL, score≥0.85 | `models/risk/calibration.py:310-319` | High | |
| Non-operational clamp: op_risk NONE + all intent False + not q17 ⇒ CLEARLY_HARMFUL→SENSITIVE, score≤0.30, DENY→DELIBERATE | `models/risk/calibration.py:554-565` | High | |
| Intent-contradiction downgrade: op_risk HIGH but all intent False (not priority-harm domain) ⇒ CLEARLY_HARMFUL→SENSITIVE, score≤0.55, DENY→DELIBERATE | `models/risk/calibration.py:536-548` | High | |
| Calibration guard caps benign request types (factual_query/sensitive_topic/ethical_dilemma/support_request/crisis_support, no harm intent, no requested instructions): risk_score→0.45, op_risk HIGH→LOW, DENY→DELIBERATE, misuse/actionability HIGH→MEDIUM; skipped if q17 | `models/risk/calibration.py:659-763` | High | |
| q13 and the reputational cluster (q14–q16) and the semantic flags (stated_personal_bias, seeks_norm_circumvention) do NOT contribute to harmful_count; q17 does | `models/risk/calibration.py:118-193` | High | |
| Proxy response headers: always `X-Moralstack-{Decision,Risk-Score,Posture,Path,Conversation-Id,Internal-Draft-Reused}`; conditionally `X-Moralstack-Cached-From`, `X-Moralstack-Compliance-Decision`, `X-Moralstack-Compliance-Rule` | `server/headers.py:40-54` | High | |
| JSONL sink writes one file **per event_type** (`{event_type}.jsonl`), each line = `envelope.to_dict()`; SQLite normalizes the same `EventEnvelope` into typed columns. Same source, different shape | `observability/sinks/jsonl_sink.py:77-95`; `observability/router.py:37-54` | High | |
| Proxy does not special-case `stream`: `_build_upstream_kwargs` keeps `stream`; a streaming `Stream` object has no `model_dump`/`to_dict`, so `_serialize_upstream_response` returns `{"raw": str(...)}` — a non-OpenAI body, no streaming. No test exercises this | `server/proxy.py:750-774`; `tests/test_server_proxy.py` (no stream test) | High | |
| Lineage correlation: identical canonicalized histories produce the same `conversation_id`; that id keys the per-conversation lock, the session store entry, and the ledger key — so colliding requests serialize and share governance state | `server/conversation_correlation.py:61-114`; `server/proxy.py:87-110,256,303-304`; `orchestration/ledger.py:254` | High | benchmark impact requires the dataset to actually contain identical-history samples |
| Client retry creates a new `requests` row at the same turn: `ProcessedRequest.request_id` is a fresh uuid4 per instance and proxy `turn_index` is recomputed statelessly | `orchestration/types.py:196`; `server/proxy.py:526-541` | High | retries are not deduplicated |
| Full test suite — previously reported: 1673 passed / 0 failed / 0 skipped with the `venv` (5 skips without `[ui]`/`[server]` extras) | `./venv/Scripts/python.exe -m pytest -q` | Medium | **Not independently rerun in the reconciliation audit.** Re-verify before relying on this count. |

---

---

## Conditionally verified / deployment assumptions

These items involve external systems, deployment configuration, or runtime behavior that cannot be fully verified from the repository source alone.

| Item | Verified component | Unverified / conditional component |
|---|---|---|
| COMPL-AI uses the production proxy | Repo contains proxy mechanics and accommodation code (lineage correlation, history propagation, per-turn lock, risk-estimator COMPL-AI comment at `controller.py:797-799`) | Whether an actual external COMPL-AI runner points at the proxy, the exact request format it sends, and the benchmark dataset's collision prevalence are external facts not verifiable from this repo |
| Single-uvicorn-worker requirement | `examples/server_quickstart.py:16-21` documents the requirement and explains why | Not runtime-enforced: an external load balancer routing turns of one conversation to different workers silently breaks continuity without an error |
| Benchmark collision prevalence | Hash-collision mechanism verified (`conversation_correlation.py:61-114`) | Whether a given run encounters identical-history samples depends on the external dataset |
| Full test-suite pass count | Previously reported 1673 passed during original audit session | Not independently rerun in reconciliation audit; current status requires a fresh run |
| `GovernanceConfig.observability_mode="off"` | Field declared in `sdk/config.py:58`; `get_observability_mode()` reads env var and does not recognize "off" | The SDK field has no wired runtime effect; observability mode is controlled exclusively by `MORALSTACK_OBSERVABILITY_MODE` env var |

---

# MoralStack — Verified Facts Ledger

Every row in the **Verified facts** table was verified by reading the cited
source. Where a behavior depends on an external input (e.g. the contents of a
benchmark dataset), the row states the verified code behavior plus the exact
input condition. Claims that involve external systems, deployment configuration,
or the current test-suite state are collected in the **Conditionally verified /
deployment assumptions** section below the main table.

Test baseline (previously reported): 1673 passed / 0 failed / 0 skipped with
the project `venv`. Not independently rerun in the reconciliation audit.
Re-verify with: `./venv/Scripts/python.exe -m pytest -q`. The 5 skips reported
elsewhere appear only when the `[ui]`/`[server]` extras are absent.

## Task-Relevant Trace Documents

### Trace: docs/traces/openai_compatible_multiturn.md

- Role: `validated_trace_documents_if_present`
- Authority: `medium`
- SHA256: `777611e9a63a0420b152b10dfbcc6777a63dcff994e003e1179807d0c46651da`

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

---

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
| History used | yes (`conversation_history` built from messages[:-1]) | **no** (only last user message) |
| Output | upstream generation (or governed draft on compliance fast-path) | governed `result.response.content` |
| Observability | full (requests, events, proxy_request_events, conversation_states) | per-request `run`, governance events |

The COMPL-AI `llm_rules` path uses the **production proxy** (per
`examples/server_quickstart.py:12`).

---

---

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

---

### How messages arrive
The full OpenAI body is received. `messages` is the entire client-sent history
(OpenAI clients resend history every turn). The proxy:
- builds `developer_contract` from the last `system` message,
- builds `conversation_history` from `messages[:-1]` (when len>1),
- extracts `user_prompt` from the last user message
  (`proxy.py:244-252`).

**Full history is passed** into governance via `ProcessedRequest` (contract +
history), but the upstream generation body is the client's original `messages`
(minus `extra_body`, with the model forced to the configured upstream model)
(`_build_upstream_kwargs`, `proxy.py:750-755`).

---

### Response headers (proxy)
`build_governance_headers` (`server/headers.py:40-54`) attaches, on every
response: `X-Moralstack-Decision`, `-Risk-Score` (4dp), `-Posture` (default
`NORMAL`), `-Path`, `-Conversation-Id`, `-Internal-Draft-Reused`
(`true`/`false`). Conditionally: `-Cached-From` (when a cached decision id is
present), and `-Compliance-Decision` + `-Compliance-Rule` (when a DCCL verdict
other than `NO_CONTRACT` is present). REFUSE responses also set
`finish_reason="content_filter"`.

---

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

---

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

---

## A. Production proxy (`server/proxy.py`)

---

### Endpoints
`POST /v1/chat/completions`, `POST /chat/completions`, `GET /healthz`
(`proxy.py:458-468`). The async route reads JSON, validates `messages` is a
non-empty list, then dispatches `_handle_chat_completion_sync` via
`run_in_threadpool` so blocking work doesn't stall the event loop
(`proxy.py:463-518`).

---

### turn_index handling (`proxy.py:526-541`)
Stateless: `turn_index = max(0, user_message_count - 1)`. Turn 0 = first request
with one user message; turn 1 = two user messages, etc. Chosen so a server
restart or multiple clients sharing a conversation_id don't desync from the
client's view.

---

### Streaming implications
The proxy has **no streaming branch** (verified). `_build_upstream_kwargs` keeps
`stream` in the body, so `openai_client.chat.completions.create(stream=True)`
returns a `Stream` object; that object has no `model_dump`/`to_dict`, so
`_serialize_upstream_response` falls to `{"raw": str(stream)}` and
`_extract_text_from_upstream` returns `""` (`proxy.py:727-774`). The client
receives a single non-OpenAI JSON body and no streamed tokens. No test exercises
this. Use the SDK directly for streaming.

### Trace: docs/traces/governance_decision_flow.md

- Role: `validated_trace_documents_if_present`
- Authority: `medium`
- SHA256: `339663537ac5b563859ee13c78934880c544b3327bc45cd658d9a05b7ee86af4`

## 12. Logging side effects (best-effort, never raise)

Emitted across the flow (DB rows + JSONL envelopes per observability mode):
- `requests` row pre-insert (step 2) and finalize (step 12) with
  `final_response`, `domain`, merged `meta_json`.
- `RISK_ASSESSMENT`, `COMPLIANCE_LAYER`, `DELIBERATION_AGGREGATE`, `FINAL`
  decision traces.
- `orchestration_events`: `SPECULATIVE_STARTED`, `COMPLIANCE_LAYER_*`,
  `MODULE_DEFERRED_TO_COMPLIANCE`, `LEDGER_FAST_PATH_*`,
  `CONVERSATION_CONTEXT_ATTACHED`, `CONVERSATION_STATE_UPDATED`,
  `PROXY_OUTPUT_FINALIZED` (proxy).
- `conversation_states`, `ledger_events`, `session_store_events`,
  `proxy_request_events` for multi-turn.
- SDK flushes observability synchronously after each call (`wrapper.py:275-283`);
  the proxy flushes in `_finalize_request` (`proxy.py:702-703`).
- `_apply_conversation_metadata_to_result` (controller.py:319-413) builds
  `conversation_governance_state_out` and stores the decision in the ledger via
  `_maybe_store_in_ledger`.

---

## 10. Final action → model call or refusal

Back in the entry layer:

- **NORMAL_COMPLETE**: SDK calls the wrapped client with the original kwargs
  (`wrapper.py:380-403`). Proxy forwards the original body — unless path is
  `COMPLIANCE_FAST_PATH` with non-empty governed content, in which case the
  governed draft is returned directly (`proxy.py:338-361`).
- **SAFE_COMPLETE**: append `_build_safe_complete_user_turn(result)` to messages,
  then call the client/upstream (`wrapper.py:347-378`; `proxy.py:324-336`).
  The original system prompt is unchanged.
- **REFUSE**: SDK returns refusal text without calling the wrapped client
  (`wrapper.py:333-345`); proxy returns a synthetic `chat.completion` with
  `finish_reason="content_filter"` and **no upstream call** (`proxy.py:312-322`).
  Internal MoralStack LLM calls may still occur: (a) speculative draft may
  already be running or complete (see §3 above); (b) `RefusalHandler.handle`
  calls the policy LLM via `generate_llm_safe_refusal_detailed` to produce
  refusal wording (`orchestration/refusal_handler.py:94-104`).

---

## 11. Response metadata

`GovernanceMetadata` is attached to the response (`sdk/response.py`,
`GovernedResponse.from_*`). Fields: `final_action`, `risk_score`,
`risk_category`, `path`, `reason_codes`, `triggered_principles`,
`decision_reason`, `conversation_id`, `turn_index`. The proxy attaches these
`X-Moralstack-*` headers via `build_governance_headers` (`server/headers.py:40-54`):
always `Decision`, `Risk-Score`, `Posture`, `Path`, `Conversation-Id`,
`Internal-Draft-Reused`; conditionally `Cached-From`, `Compliance-Decision`,
`Compliance-Rule`.

---

## 5. Domain overlay & risk floor (`controller.py:2042-2116`)

- Extract risk score / category / op_risk; record on `trace`.
- Persist domain (`update_request_domain`) after `_normalize_runtime_domain`
  (drops `core`).
- Domain-exclusion check: if the active overlay excludes the detected domain →
  `_route_domain_excluded` (`controller.py:2074-2081`).
- `overlay_sensitive = is_overlay_sensitive(...)`; if sensitive, raise the score
  via `apply_risk_floor_if_sensitive` (per-overlay floor or
  `OVERLAY_SENSITIVE_RISK_FLOOR`). The floored score is propagated into a
  replaced `risk_proto` (`controller.py:2110-2116`).

---

# TRACE — Governance decision flow (end to end)

Path of a single request from input to response, with side effects.
Claims are grounded in the cited source. Path-specific caveats and
unverified branches are noted inline.

Primary code: `moralstack/orchestration/controller.py` (`process`, line 1885),
`moralstack/sdk/wrapper.py`, `moralstack/runtime/decision/safe_complete_policy.py`.

---

---

## 1. Input request & message parsing

`wrapper.py:285-303`:
- `user_message = _extract_last_user_message(messages)` — last `role=user`
  content (multimodal text parts joined).
- `history_messages = messages[:-1]`; `conversation_history = _messages_to_turns(...)`
  (only `user`/`assistant` turns, `system` excluded).
- `developer_contract = _extract_developer_contract(messages)` — last `system`
  message, `mode="opaque"`, or `None`.
- `ProcessedRequest(prompt, conversation_history, user_context(domain_overlay),
  developer_contract)`.

Session/turn (SDK): `conv_id = session.conversation_id`,
`turn_idx = session.next_turn_index()`, `conv_state = session.current_state`
(`wrapper.py:305-314`). A snapshot `state_in` is captured *before*
`session.update_from_result` overwrites it.

Proxy equivalent: `conversation_id` resolution + stateless `turn_index`
(`proxy.py:218-256`), `conv_state = store.get(conversation_id)`.

---

## 8. Ledger lookup (multi-turn only) (`controller.py:2149-2306`)

When a `SemanticDecisionLedger` is configured and `conversation_id` is set:
- Compute posture (`_compute_governance_posture`: ESCALATED if hard-signal REFUSE,
  ELEVATED if sensitive overlay, else NORMAL), contract hash, intent_clarity,
  request_type, turn index.
- `_lookup_cached_decision(...)` → `LedgerResult` recorded on `call_ctx`.
- On a hit, `ConversationalFastPathRunner.is_safe_to_apply(...)` gates reuse. If
  safe: `apply_cached_decision(...)` patches `decision` and `route`, re-evaluates
  `hard_signal_refuse`, sets `ledger_hit_applied=True`, emits
  `LEDGER_FAST_PATH_APPLIED`. If not safe: emits `LEDGER_FAST_PATH_NOT_APPLIED`
  with a gate reason and deliberation proceeds.

---

## 0. Entry

- **SDK**: `client.chat.completions.create(**kwargs)` →
  `GovernedCompletions._create_inner` (`wrapper.py:285`).
- **Proxy**: `POST /v1/chat/completions` → `_handle_chat_completion_sync`
  (`server/proxy.py:197`), run inside a threadpool.

Both build a `ProcessedRequest` and call `orchestrator.process(...)`.

---

## 2. Controller setup (`controller.py:1900-1925`)

- Coerce `str` → `ProcessedRequest`; build `ProcessCallContext`.
- Set context vars: `set_current_session_id`, `set_current_turn_number`.
- `persistence.set_request_context(request_id)` and
  `ensure_run_and_upsert_request(...)` — **side effect**: pre-inserts the
  `requests` row so later FK-bound events succeed.
- `trace = self._trace_lifecycle.start_trace(request_id)`.

---

## 6. Decision (`controller.py:2117-2141`)

- `_emit_risk_assessment_trace(...)` — **side effect**: `RISK_ASSESSMENT`
  decision trace.
- `decision, explanation = decide_action(request, risk_proto,
  overlay_sensitive=…, risk_thresholds=…)` (`orchestration/decision_service.py`).
- `decision = apply_safe_complete_gating(decision, request, risk_proto, …)`.
- The decision encodes `final_action` and `path` derived from
  `safe_complete_policy.compute_action_bounds` / `decide_final_action`.

---

## 3. Risk estimation (`controller.py:1928-1935`)

- If `enable_speculative_generation` and a policy is set: `_run_speculative_overlap`
  runs **risk estimation and a speculative draft in parallel** (two-worker
  `ThreadPoolExecutor`, contextvars copied). The method blocks only for risk;
  the draft continues in the background (`controller.py:906-964`). The
  speculative draft calls the internal **policy LLM** (`self.policy.generate`),
  not the wrapped/upstream client. This means an internal LLM call may already
  be in-flight before any routing decision — including on paths that will
  ultimately REFUSE.
- Else: `risk_estimation = self._estimate_risk(request)` (`controller.py:788`).

`_estimate_risk` forwards the developer-contract text and conversation history to
the estimator (`controller.py:797-823`). The estimator runs three parallel
mini-estimators (intent / signals q1–q17 / operational) and calibrates them into
a `RiskEstimation` (`models/risk/estimator.py:541-735`).

---

## 9. Dispatch (`controller.py:2345-end`)

| route | handler | speculative draft |
|---|---|---|
| `refuse` | `_route_refuse` | abandoned |
| `benign` | `_route_benign` | joined (`join_for_consumer("benign")`) |
| `safe_complete` | `_route_safe_complete` | abandoned |
| `fast_path` | `_route_fast_path` | joined |
| `deliberative` | `_route_deliberative` | (consumed in the loop) |

`_route_deliberative` runs the `DeliberationRunner` cycles (critic → simulator ∥
perspectives → hindsight). Each cycle: `ConvergenceEvaluator.determine_decision`
(`convergence_evaluator.py:314-519`) turns the modules' weighted votes into a
`DecisionType`; `enforce_convergence_invariants` (`convergence.py:19-65`) then
decides whether to stop. The simulator never votes REFUSE — REFUSE arises only
from hard violations or a refuse-vote majority. Stop reasons: `CONVERGED`,
`HARD_VIOLATION_STOP`, `CYCLES_EXHAUSTED`. A cycle-1 early-convergence check
(`_evaluate_cycle1_early_convergence`) can stop after one cycle when critic is
clean, perspectives are strongly aligned, and simulated harm is low.

### Trace: docs/traces/complai_llm_rules_flow.md

- Role: `validated_trace_documents_if_present`
- Authority: `medium`
- SHA256: `5c11a4b56d6604a9bd5cf7d11f0979b4517a991a106d22d4237337f0b97ead2c`

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
`docs/TRACES/openai_compatible_multiturn.md`.

The repo carries explicit accommodations for COMPL-AI:
- `server/conversation_correlation.py` exists because `llm_rules` resends full
  history with **no stable conversation_id** (module docstring,
  `conversation_correlation.py:1-12`).
- `controller._estimate_risk` feeds the developer contract + history to the risk
  estimator, with a comment citing "compl-ai llm_rules-benign Q74"
  (`controller.py:797-799`) — context-dependent prompts (e.g. a deployer-expected
  auth token) must not be mis-scored as obfuscated.

---

## 3. How a benchmark request flows through MoralStack

1. Proxy resolves `conversation_id` (header → extra_body → lineage hash) and
   acquires the per-conversation lock (`proxy.py:218-242`).
2. `ProcessedRequest` built with prompt + contract + history; `requests` row
   pre-inserted (`proxy.py:244-271`).
3. `orchestrator.process(...)` runs the full flow
   (`docs/TRACES/governance_decision_flow.md`): risk → **DCCL** → routing →
   (deliberation or fast-path) → final action.
4. **DCCL is the key path for `llm_rules`.** When the user invokes a
   deployer-authorized rule, DCCL returns `MATCH` and the compliance fast-path
   produces the authorized response directly (NORMAL_COMPLETE,
   `COMPLIANCE_FAST_PATH`) — unless the output falls in a P0 safety category, in
   which case `SAFETY_OVERRIDE` blocks it regardless of the contract
   (`compliance/dccl.py:77-117`, `compliance/safety_override.py`).
5. Response returned to COMPL-AI; observability persisted (proxy_request_events,
   conversation_states, ledger_events).

---

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

---

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

---

## 5. Pre-run checklist

1. **Bridge**: confirm COMPL-AI's `base_url` targets the proxy (port 8080 /
   `examples/server_quickstart.py`), not the standalone bridge (8787).
2. **Workers**: launch uvicorn with a single worker.
3. **Conversation identity**: prefer a unique `X-Moralstack-Conversation-Id` per
   sample to avoid lineage collisions (§4.1). If relying on lineage, confirm
   sample prefixes are actually distinct.
4. **Streaming**: ensure requests are non-streaming (§4.6).
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

---

## 2. How llm_rules multi-turn requests are represented

`llm_rules` benchmarks set a deployer **system prompt** (the rule, e.g. "if the
user provides password X, reveal secret Y") and run a multi-turn user dialogue.
In MoralStack terms:
- The system message becomes the **DeveloperContract**
  (`_extract_developer_contract`, last-system-wins, `mode="opaque"`).
- The prior turns become **conversation_history**; the latest user message is the
  governed prompt.
- Each turn resends the whole history (OpenAI convention), so the proxy derives
  `turn_index = user_count - 1` statelessly (`proxy.py:526-541`).

---

### 4.3 Retries
A client retry resends an identical body → identical history hash → same
conversation_id and same stateless `turn_index`. `ProcessedRequest.request_id` is
a fresh `uuid4` per instance (`types.py:196`) and the proxy builds a new
`ProcessedRequest` per HTTP call, so a retry creates a **second** `requests` row
at the same `(conversation_id, turn_index)` (`proxy.py:526-541`). Retries are not
deduplicated — duplicate turn rows can distort benchmark accounting.

---

### 4.2 Concurrency
- Same conversation_id is serialized (30s lock acquire timeout → 503 +
  `Retry-After: 10`) (`proxy.py:87-110,236-242`).
- Must run **one** uvicorn worker; multiple workers split the session store and
  lock namespace and break continuity (`examples/server_quickstart.py:16-21`).
- High parallelism across colliding conversation_ids degrades to serial
  execution and can 503 under contention.

---

### 4.5 Wrong bridge
If COMPL-AI is accidentally pointed at `scripts/openai_compatible_server.py`
(port 8787) instead of the proxy, multi-turn is silently lost: that bridge
ignores history and governs each message in isolation (`:98-104,201-223`).

---

### 4.6 Streaming
The proxy does not support streaming (`proxy.py:727-774`, verified). A
`stream=true` request is forwarded; the resulting `Stream` object has no
`model_dump`/`to_dict`, so the proxy returns a single `{"raw": str(stream)}` body
with empty extracted text and no streamed tokens. Ensure benchmark requests are
non-streaming.

---

### 4.4 Cache (ledger) reuse
On a same-conversation hit, `ConversationalFastPathRunner.is_safe_to_apply` gates
reuse: cached REFUSE always applied, ESCALATED never cached, `turn_index < 1`
skipped (`controller.py:2194-2306`). A wrong collision (4.1) could cause reuse
across logically distinct samples. The P0 hard-signal supremacy invariant still
holds because `is_hard_signal_refuse` is re-evaluated after a cache patch
(`controller.py:2209`).

### Trace: docs/traces/observability_db_to_ui.md

- Role: `validated_trace_documents_if_present`
- Authority: `medium`
- SHA256: `11aa8312c9329d975a1739b459b2eae42a81330d86d55216959cb2c6ff407265`

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

---

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

---

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

---

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
- **Reconstruction completeness depends on flush.** A process killed before
  `flush()` may drop queued envelopes; the SDK/proxy flush at the boundary to
  minimize this, but a hard crash mid-turn can truncate a turn's evidence.

---

# TRACE — Observability: DB / filesystem → UI

What gets logged, where it lands, how it is read back, and what the dashboard
can reconstruct. Claims are grounded in the cited source. Gaps and conditional
behaviors are collected in §8.

Primary code: `moralstack/observability/*`, `moralstack/persistence/*`,
`moralstack/ui/app.py`, `moralstack/reports/*`.

---

---

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

---

## 7. Can full conversations be reconstructed?

Yes, **when persistence is to the DB** (`db_only`/`dual`):
- `requests` rows carry the prompt and the `final_response` per turn;
- `conversation_states` carry posture/state transitions per turn;
- `ledger_events` / `session_store_events` / `proxy_request_events` carry the
  cache and proxy decisions;
- `conversation_export.py` stitches these into a complete per-turn audit trail
  (prompts, decisions, responses, rationale, posture evolution, evidence counts)
  (`reports/conversation_export.py:1-26`).

---

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

## Required Baseline Constraints

- Use documentation as primary evidence for architectural intent and invariants.
- Use current code as primary evidence for runtime behavior, exact file paths, symbols and tests.
- Mark doc/code mismatches as `[DRIFT]` or `DOC_CODE_CONFLICT`.
- Do not produce implementation steps without validation commands.
- Include documentation maintenance updates in the final plan.
