# Changelog

All notable changes to this project will be documented in this file.
The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Security

- **Hard-signal gate on the compliance fast-path (P0 invariant #3).** Before a DCCL
  `MATCH` is delivered through the compliance fast-path, the controller now invalidates it
  (emitting `COMPLIANCE_MATCH_DOWNGRADED`) when the risk estimator produced hard topical
  evidence (`path_router.has_hard_signal_evidence`: a hard q-signal or a `clearly_harmful`
  category). Previously a developer contract could authorize a hard-signal request through
  the fast-path before any deterministic hard-signal check ran; `DeveloperContractComplianceLayer.evaluate`
  discards `risk_estimation`, so this gate is the only enforcement point.
- **Safety-override classification is now language-agnostic (LLM-based).** The English
  keyword pre-filter in `compliance/safety_override.py` was removed: it only matched a
  handful of English phrases and silently missed every paraphrase and every other
  language. `classify_safety_override` now uses the existing LLM classifier
  (`use_llm=True` by default; returns `None` with no policy / on LLM error — no keyword
  fallback), routed to a small model via `MORALSTACK_DCCL_SAFETY_OVERRIDE_MODEL`
  (default `gpt-4o-mini`) so the compliance fast-path stays fast. Consequence: a
  structured-path contract MATCH now makes one small-model call for the safety check
  (it was previously keyword-only/LLM-free). Multilingual request-side coverage is
  provided independently by the new hard-signal gate. The classifier LLM call is
  **persisted and observable identically to the other module calls**: it is written to
  `llm_calls` with `module="compliance_layer"`, `action="safety_override"`, its model, and
  full token usage (including `cached_input_tokens`), so it flows into `request_token_usage`,
  the per-model/per-module UI token panels, the per-call badge, and the prompt-cache hit
  rate. Configurable via `MORALSTACK_DCCL_SAFETY_OVERRIDE_MODEL` (documented in the README
  configuration table).

### Added

- **Simulator-metrics-measured marker on decision traces (audit honesty).** `DecisionTrace`
  gained `sim_metrics_measured`, recording whether a FINAL trace's `sim_*` metrics are a
  real measurement or defaults. The metrics could not self-report it:
  `sim_semantic_expected_harm=0.0` together with `sim_worst_harm=None` is produced *both*
  when nothing was measured *and* when a simulation was retained and every consequence was
  benign (the aggregation skips `harm_type == "none"`, leaving no risk records either way),
  so a defaulted value was indistinguishable from a measured one. The field is tri-state
  (`bool | None`, default `None` = not asserted) and is written only by
  `_populate_trace_from_sim` (as `sim_result is not None`, before its early return);
  `_log_final_trace` copies it onto the FINAL row. It reflects whether a `SimulationResult`
  was retained into the trace, **not** whether the simulator module executed — a
  full-parallel simulation discarded on a critic hard violation ran yet reads `False`,
  which is correct because those metrics are then defaults. Purely observability — it feeds
  no gate and changes no decision. No schema migration (`decision_traces` persists a
  `trace_json` blob). Note: traces written before this release carry no
  `sim_metrics_measured` key and stay ambiguous; consumers must read absent/`None` as
  *unknown*, never as "not measured".
- **UI Loop for a better UX Design and Readability of conversation** added a loop feature
  to improve UX Design and Readability in iterative and verificable way
- **Governance steps are now visible in the execution graph (audit completeness).** The
  request-detail flow graph gained synthetic nodes for governance steps that previously
  vanished from the graph: the compliance `MATCH` downgrade / hard-signal safety gate
  (`_synthetic_compliance_downgrade_nodes` — the P0 gate renders as a `safety_gate` alert
  node and the execution-path badge shows `compliance_blocked_p0`), modules deferred to a
  contract (`_synthetic_module_deferred_nodes`), the multi-turn ledger fast-path
  (`_synthetic_ledger_fast_path_node`), early convergence
  (`_synthetic_convergence_node`), and gated/skipped modules
  (`_synthetic_module_skipped_nodes`). The `compliance_layer` (DCCL) and
  `final_revalidation` nodes also gained a legend colour. Presentation only — no change to
  governance decisions. Tests: `tests/test_ui_tier_order.py`.
- **Prompt-cache observability**: every LLM call now records how many input tokens the
  provider served from its prompt cache (`usage.prompt_tokens_details.cached_tokens`),
  persisted to the new nullable `llm_calls.cached_input_tokens` column (additive
  migration, no backfill). `TokenUsage.cached_input_tokens` is `int | None`: `None` means
  the provider reported nothing (pre-migration rows, embeddings, providers that omit the
  field), `0` means it measured a cache miss — the two are never conflated, because a hit
  rate needs both. Extraction is defensive and never raises (`prompt_tokens_details` may
  be absent, `None`, or a `Mapping`; `cached_tokens` is `Optional[int]`; non-`int` values
  are rejected). The field is threaded through `GenerationResult` **and** the deliberative
  modules' own report objects (`CriticReport`, `SimulationResult`, `HindsightResult`,
  `PerspectiveResult`/`EnsembleResult`), which copy token fields rather than forwarding
  the `GenerationResult`. The UI surfaces the hit rate wherever per-module/per-model token
  metrics already appear: the shared per-model panel (dashboard, run, conversation,
  request), the per-module rollup, the per-call badge, and the Domain retrieval table;
  `—` when unknown, `0.0%` when measured. The rate divides by the input tokens of the
  reported calls only, so a model mixing old and new rows is not diluted. Read paths guard
  the new column, so a database written before the migration still renders.
  Measured on a COMPL-AI replay: 63.0% of input tokens cached (−31.5% input cost).
  Cached tokens are billed at a reduced rate; they do not reduce token counts.
- **UI: the request spine's OUTPUT anchor now surfaces `activated_signals`** (the
  risk-signal vocabulary the policy consumed, 194/198 FINAL traces nonempty and
  previously rendered nowhere — labelled "Risk signals (activated)" per invariant
  36) **and `hard_violation_codes`** as an additional, gated row inside
  `.final-decision-grid` (§5 #3: added visibility, not moved — the existing
  "Relevant constitutional principles" card still renders the same codes). The
  `Final Risk Score` gate was also changed from a truthiness check to
  `is not none`, fixing a latent falsy-gate bug that would have hidden a
  genuinely assessed `risk_score=0.0` (0/198 rows currently at that value, so
  no visible change today). Template-only; `_build_final_decision_card` already
  returned both fields. Tests: `tests/test_ui_final_decision_completeness.py`.
- **UI: `/conversations/{id}` opens with a linear conversation-level spine**
  instead of the horizontal risk-height "conversation strip", one node per
  turn (decisional input → decision → response outcome, each linking to its
  request page), a first-turn node for developer-contract/history chips
  (reusing `_build_input_anchor_info` verbatim, so branch order/invariants
  31-34 hold by construction), and a terminal node folding the already
  failure-aware conversation aggregates. Connectors assert only what is
  persisted: a real cache-reuse link ("reused decision from turn N"), a
  posture transition, or a bare pipe — colliding `turn_index` renders a
  dashed non-causal divider ("order not established"), never an invented
  sequence; `meta_json.parent_request_id` is never used for ordering (131/131
  conversation-turn rows are self-referential — see `docs/CODEBASE_FACTS.md`).
  Risk is rendered as an exact value plus a proportional bar — a deliberate
  substitution for the strip's height encoding, not a 1:1 carryover (no
  sparkline). The posture timeline and per-turn detail cards are kept,
  collapsed into `<details>` (invariant 23: reduce density by collapsing,
  never by deleting evidence). New `_build_conversation_spine_node` in
  `moralstack/ui/app.py`, wired into `_build_conversation_timeline`'s existing
  per-turn loop with a best-effort per-turn orchestration-events fetch (§5 #6:
  one malformed turn cannot break the page). Tests:
  `tests/test_ui_conversation_spine.py`,
  `tests/test_ui_conversation_spine_affordances.py` (parity rewrite of the
  retired `tests/test_ui_conversation_strip.py`), extended
  `tests/test_ui_conversation_views.py` / `tests/test_ui_conversation_turn_collision.py`.

### Changed

- **mypy `strict` now covers `moralstack.ui.*`** (the FastAPI observability UI,
  including the 3300-line `ui/app.py`), completing the strict rollout across the three
  user-facing packages (`orchestration.*`, `server.*`, `ui.*`). The package already
  type-checked clean under strict, so no code changed; strictness was proven active
  with a canary (temporary untyped def → `no-untyped-def` fired, then reverted).
  Tooling-only; no runtime behavior change.
- **mypy `strict` now covers `moralstack.server.*`** (the network-facing proxy package),
  matching the strictness level of `moralstack.orchestration.*`. The stale lenient
  overrides for `moralstack.server.*` and `moralstack.ui.app` (`ignore_missing_imports`
  + disabled `untyped-decorator`) were removed — both packages already type-checked
  clean, so no code changed. Tooling-only; no runtime behavior change.
- **Bounded, tenant/principal-aware proxy conversation correlation store** (P3):
  the proxy `ConversationCorrelationStore` lineage map is now keyed by
  `(principal, canonical_history_hash)` instead of the bare history hash, so
  byte-identical conversation histories from different tenants no longer collide
  onto one `conversation_id`. The two hash functions
  (`canonical_history_hash` / `canonical_parent_history_hash`) are **byte-for-byte
  unchanged** — isolation lives entirely in the map key, and an empty principal
  reproduces the previous behavior exactly (`("", hash)` keyspace), so existing
  single-turn and history-based multi-turn correlation is preserved. The store is
  now bounded (TTL lazy-expiry on read + a max-entries FIFO cap, mirroring
  `InMemorySessionStore`; defaults 3600 s / 20 000 entries, overridable via
  `MORALSTACK_CORRELATION_TTL_SECONDS` / `MORALSTACK_CORRELATION_MAX_ENTRIES`),
  removing the previous unbounded-growth/OOM risk. Principal is derived per
  request as `X-Moralstack-Tenant-Id` header → HMAC-SHA256 of an
  `Authorization: Bearer` token (secret from `MORALSTACK_PRINCIPAL_HMAC_SECRET`,
  read per request, raw token/digest never logged) → empty-string sentinel.
  `create_app` gains an optional `correlation_store=` parameter. `resolve()` is
  best-effort on the TTL/eviction path (PROJECT_SPEC §5 invariant #6): a helper
  failure never propagates into the request handler and a valid `msconv-*` id is
  always minted. `ConversationLockManager._locks` bounding is deferred
  (`TODO(P3-followup)`). No P0 decision/governance invariant is affected.
- **Constitution retrieval unified to a single pass**: `get_relevant_principles`
  now runs exactly once per request, owned inside the risk thread
  (`models/risk/estimator.py:_get_principles_context`) at the unified
  `top_k = max(risk_top_k, critic_top_k)`. The controller lifts the result into a
  `RequestAnalysisContext` reused by the deliberation critic, the FAST_PATH
  `quick_check` (filtered to HARD) and the quick-check-failed deliberative
  fallback, so the constitution store is queried a single time across all routes.
  Reuse-vs-fallback is gated on the explicit `RiskEstimation.retrieval_succeeded`
  flag, never on the emptiness of the principle list (an empty-but-successful
  retrieval is authoritative, not degraded). `COMPLIANCE_FAST_PATH` consumes no
  principles and adds no retrieval. Decision/routing behavior is unchanged: this
  is a retrieval-consolidation and observability change, verified by the
  release-time noise-floor gate (route flips 0%, REFUSE-set identical, hard-signal
  codes byte-identical branch-vs-HEAD).
- **Single `RELEVANT_PRINCIPLES_RETRIEVED` emission**: the event is now emitted
  once, before routing, so every route that returns after a successful risk
  retrieval (deliberative, FAST_PATH, COMPLIANCE_FAST_PATH, REFUSE, benign,
  SAFE_COMPLETE) is covered; the deliberation runner only emits on its own
  fallback retrieval. Constitution `llm_calls` rows now carry a `retrieval_phase`
  qualifier (`risk_routing` / `deliberation_retrieval`) on the domain prefilter
  and per-domain agents.
- **Cacheable intent prompt**: the 5 fixed SEMANTIC ANALYSIS GUIDELINES moved from
  the intent mini-estimator's per-request user message into the static
  `INTENT_CONTEXT_SYSTEM_PROMPT` prefix; the signals/operational minis remain
  principle-free.
- **Cacheable domain-prefilter prompt**: the `DomainPrefilter` classifier prompt is
  split into a byte-stable system prompt (classifier instructions, the
  `AVAILABLE DOMAINS` list rendered from the current domain config, procedure,
  falsification checks and JSON schema) and a query-only user message, so the large
  static prefix is cache-eligible for OpenAI automatic prompt caching. `_call_openai`
  threads a single builder output (`_build_prefilter_system_prompt`) into both the
  API call and the persisted `llm_calls` row (persisted `system_prompt` now holds the
  static block, `prompt` only the query). The template is rendered flush-left to drop
  leading-whitespace waste from the cached prefix. No decision/routing change: `core`
  stays excluded from the domain list (P0), and the local retrieval cache key and the
  parse/retry/fallback ladder are unchanged.
- **Parallel domain retrieval default raised to 4**: the effective
  `max_parallel_agents` default is now 4 (was 2) across every source
  (`ConstitutionRetrieverConfig`, `ConstitutionStoreConfig`,
  `ConstitutionStore.__init__`, `CLIConfig`, the `resolve_constitution_max_parallel_agents`
  env fallback, and the CLI `--max-parallel-agents` help), so up to 4 prefilter agents
  (core + up to 3 domains) run in a single `ThreadPoolExecutor` batch instead of two.
  The `MORALSTACK_CONSTITUTION_MAX_PARALLEL_AGENTS` env override still wins when set.

### Fixed

- **UI: the request page's delivery card now surfaces the causal reason and the
  decision risk in the first viewport**, instead of only at the tail of the
  deliberation spine's OUTPUT anchor (~1400 lines / 30-40% scroll depth down).
  The "Authoritative delivery path" card's `delivery-path-facts` grid gained a
  causal block that reads `final_decision_card` / `compliance_fast_path_panel`
  verbatim (a render-site move, no new computation): a governed FINAL decision
  shows "Decision risk" (`risk_score`/`risk_category` from the FINAL trace,
  never a raw or calibration-guard-capped operational value — the S10 case with
  three different risk numbers on one request now unambiguously surfaces the
  0.35/`sensitive` value that actually governed the decision), "Winning rule",
  and the one-sentence "Causal reason" (the `why_not_*` field matching the
  chosen `final_action`); a DCCL fast-path reuse shows the matched-rule summary
  with no risk sentence (risk was computed but unused there); a pipeline
  failure or a request with no `decision_traces` renders no causal slot at all,
  never an invented "unknown" value or the `risk_score=1.0` fail-closed
  sentinel. The full `why_not_*` set and raw trace stay at the existing tail
  render site unchanged (progressive disclosure, not duplication removal).
  Tests: `tests/test_ui_causal_reason_surfacing.py`.

- **UI: the deliberation-spine INPUT anchor's contract/conversation chips now
  come from this request's own persisted evidence.** The "conversation history"
  chip was gated on `conversation_context.turn_count`
  (`len(sibling_requests)`), the conversation's current, complete row count —
  so a genuine opening turn falsely claimed prior context once the
  conversation grew later. The "developer contract" chip was gated only on
  `final_revalidation_info.developer_contract_present`, which requires a
  `PROXY_FINAL_REVALIDATION_*` event this deployment never emits, so the chip
  never rendered even when `COMPLIANCE_LAYER_STARTED.has_contract` was true. A
  new `_build_input_anchor_info` derives both chips per-request from
  `CONVERSATION_CONTEXT_ATTACHED`/`CONTEXT_SHAPE_RECORDED`/
  `COMPLIANCE_LAYER_STARTED` orchestration events (falling back to `turn_index`
  only when no `CONVERSATION_CONTEXT_ATTACHED` row exists), and distinguishes
  "N prior turns" (prompt actually carried history) from "conversation state
  inherited" (state came from the ledger, not the prompt) instead of
  conflating the two.

- **UI: deliberation-spine boxes are now genuinely compact — long input/output
  annotation values are truncated in the collapsed box.** A policy node's
  `→ draft` output carried the full generated answer (~1300+ chars) in the
  always-visible `flow-node-io` block, so a collapsed box could be ~440px tall
  and dump answer text onto the spine. The ← input / → output lines stay on the
  spine (the module-integration view the user asked for), but their values are
  truncated to ~100 chars; the complete value stays reachable via the node's
  Raw Response / Parsed Summary on expand. (Browser-verified: the longest
  compact io value dropped from 1335 to 101 chars; the policy_generate box from
  444px to 147px.)

- **UI: the speculative-draft node no longer leaks its content into the compact
  deliberation-spine box.** The `policy / speculative_generate` node was the only
  module box rendering a preview pill in its collapsed state, showing
  context-envelope internals (`context_shape` / `message_sections`) — and, more
  importantly, a speculative draft is not the delivered answer, so its text must
  not compete with the OUTPUT anchor. The compact box now omits the pill for any
  speculative phase; the full draft stays reachable via the node's expand.

- **UI: per-turn `final_action` badges on the conversation view are now
  colour-coded by the strip's own legend** (green = NORMAL_COMPLETE, amber =
  SAFE_COMPLETE, red = REFUSE), instead of the generic blue badge. The
  posture-timeline Action cell, the per-turn header, and the per-turn detail row
  previously rendered a neutral badge while the strip directly above them
  colour-coded the same `final_action`, so colour was an unreliable secondary
  signal in the table/cards. A new `action_badge` Jinja macro applies the legend
  consistently; a pipeline-failure turn's coerced `NORMAL_COMPLETE` placeholder
  stays neutral (never the green success colour) beside its unchanged "not a
  governed outcome" caveat, and the action code text is always rendered (colour
  is never the sole signal). The delivery card's status-based badge colours
  (reused/blocked/normal) and all non-`final_action` badges are unchanged. Tests:
  `tests/test_ui_action_badge_colour.py`.

- **UI: the `.meta-grid` summary tiles no longer clip content on phones.** At
  `max-width: 768px` the grid is two columns (`1fr 1fr`); an unbreakable cell (the
  `white-space: nowrap` "First / last turn" timestamp) pinned a track wider than the
  clipped `.card` (`overflow: hidden`), silently cutting off content in that column —
  observed as the conversation "Final actions" REFUSE count truncated at 390px. A new
  `@media (max-width: 480px)` breakpoint collapses `.meta-grid` to a single full-width
  column so every tile is fully visible; the >480px layout is unchanged. Tests:
  `tests/test_ui_meta_grid_responsive.py`.

- **UI: colliding `turn_index` values on the conversation timeline no longer render
  as byte-identical labels.** Two structurally different DB shapes previously produced
  identical `T{n}` labels/accessible names: two independent runs sharing one
  `conversation_id` at the same `turn_index` (different `run_id`), and one run whose
  `turn_index` never advanced across a genuine multi-turn escalation (same `run_id`).
  `_build_conversation_timeline` (`moralstack/ui/app.py`) now derives a `turn_index`
  collision grouping from already-persisted fields only (`turn_index`, `run_id`; no new
  DB reads), classifying each colliding group as spanning separate runs or sitting
  within one run — the canonical `turn_index` is never renamed or renumbered, and no
  causal "sequence"/"escalation" is invented, only the verified same-run vs
  separate-runs fact. Rendered on the conversation strip (label, title, unique
  `aria-label`), the posture-timeline table, the per-turn detail heading, and a new
  conversation-level `.turn-index-collision-note` (a neutral/amber note, distinct from
  the red `.pipeline-failure-note`, since a collision is an observability caveat, not an
  error). Every new block is gated on the collision flag, so conversations without a
  collision render unchanged. Tests: `tests/test_ui_conversation_turn_collision.py`.
- **UI: Decision Traces and Debug Events on the request-detail page are now
  collapsed behind `<details>` by default, consistent with every sibling raw
  block on the page** (Raw Response, Parsed Summary, Original System/Developer
  Messages, Conversation History). Previously both sections rendered their raw
  JSON as a bare `<pre>` directly under the `<h2>`, always fully expanded —
  contributing roughly a fifth of an example page's height before a reviewer
  reached anything below them. Each Decision Trace's `<summary>` still shows
  `stage (seq N)`; each Debug Event's `<summary>` now shows a new scannable
  `component · message` label (new `event_label` Jinja filter in
  `moralstack/ui/app.py`, reading the same persisted `payload_json` the `<pre>`
  already showed, falling back to `component`, then `message`/`event_type`, then
  "debug event" — never empty). No raw bytes, classes, `<h2>` counts, or card
  wrappers changed; purely a disclosure/labelling change. Tests:
  `tests/test_ui_progressive_disclosure.py`.
- **UI: the fail-closed risk sentinel is no longer shown as an assessed risk score.**
  On every risk surface of the conversation view (the "Max risk score" tile, the
  posture-timeline Risk cell, the conversation-strip cell title, the per-turn badge, and
  the per-turn "Governance decision" Risk score row), a turn's fail-closed
  `meta_json.risk_score = 1.0` (written by `ResponseMetadata.for_system_error` after a
  pipeline crash) now carries a plain-language "fail-closed default" label, computed
  solely from the existing structured `pipeline_failure` flag — never from response text.
  The raw value is never removed, only annotated: when the crashed turn's decision traces
  recorded a genuine pre-crash assessment (`PRE_POLICY`, falling back to
  `RISK_ASSESSMENT`), it is shown beside the label as "last assessed X.XXX"
  (`_last_assessed_risk`, `moralstack/ui/app.py`). The conversation-level "Max risk score"
  tile additionally gets a `max_risk_is_fail_closed` flag, set only when the overview's
  max risk is reached by a failed turn's sentinel and by no non-failed turn's assessed
  score, plus a `max_assessed_risk` aggregate — so a reviewer no longer sees an
  unreconciled 1.000 next to a genuinely assessed 0.6 for the same request.
- **UI: governance pipeline failures are no longer rendered as a normal delivered
  outcome.** When a request crashes before a `FINAL` decision-trace row is written
  (`OrchestratorController._handle_error`, `meta_json.triggered_principles` contains
  `SYSTEM.ERROR`), the request page now shows a distinct, text-labelled "PIPELINE
  FAILURE" delivery-path state (canonical delivered action code still visible beside
  it) with the last recorded pre-crash decision from the `PRE_POLICY` trace when
  available, and the delivered `[SYSTEM_ERROR]` text is wrapped in an error-styled
  block captioned as a system-error placeholder, not a governed answer. The
  conversation strip marks a failed turn distinctly (never colour-only) and shows an
  aggregate note when any turn failed, without altering the existing raw counts. The
  previously fabricated "unknown path chose unknown before proxy delivery checks"
  sentence (rendered whenever no `FINAL` trace exists, failure or not) is replaced
  with "no recorded pre-delivery decision (no FINAL decision trace)". Detection uses
  structured signals only (no FINAL trace + `SYSTEM.ERROR` principle), never the
  response text, per the decision-policy invariant.
- **UI: pipeline-failure turns are no longer counted or labelled as a governed
  delivery on the conversation view.** The "Final actions" tile, the "Last posture"
  tile, the posture-timeline Action column, and the per-turn card (header badge and
  "Governance decision → Final action" row) previously asserted a crashed turn's
  `meta_json.final_action` (e.g. `NORMAL_COMPLETE`) with no caveat, contradicting the
  same page's own pipeline-failure banner and conversation-strip cell. `_build_conversation_timeline`
  (`moralstack/ui/app.py`) now also returns `pipeline_failure_action_counts` (a
  per-action tally of failed turns) and `last_posture_is_from_pipeline_failure` (a
  conservative attribution — true only when every turn sharing that posture value is
  a pipeline failure; read_store does not expose which request produced
  `overview.last_posture`, so an ambiguous match stays `False`). The template appends
  the banner's exact "not a governed outcome" wording as text beside the canonical
  value on all four surfaces; the raw counts and badges are never altered or removed.
- **Undeclared PyYAML runtime dependency removed.** `moralstack/models/risk/signals/registry.py`
  (the only PyYAML import in the package, on the main runtime path via the risk estimator)
  now loads `signals.yaml` with the already-declared `ruamel.yaml` (`YAML(typ="safe")`)
  instead of `yaml.safe_load`. A clean `pip install moralstack` (no dev extras) no longer
  depends on PyYAML arriving transitively. `signals.yaml` contains no YAML 1.1-only
  constructs, so the parser switch is semantics-preserving. This also unblocks dropping
  the global `--ignore-missing-imports` mypy flag from CI/pre-commit (follow-up).
- **UI: a DCCL `MATCH` vetoed by the hard-signal safety gate no longer renders as a
  live, approved result.** `_build_compliance_card` (`moralstack/ui/app.py`) now branches
  on the persisted downstream events instead of only the verdict event: when a `MATCH` is
  followed by `COMPLIANCE_MATCH_DOWNGRADED`, the decision badge reads "MATCH — vetoed"
  (canonical `MATCH` code kept visible, never colour-only `badge-ok`) and the card shows
  the veto reason, risk category/score, semantic signals and `mismatch_guard_action` taken
  literally from the downgrade payload; when `SPECULATIVE_RESULT_DISCARDED` /
  `SPECULATIVE_JOIN_SKIPPED` is also present, a "Draft discarded" line states the validated
  draft was never delivered, with its persisted reason. A `MATCH` with a reused/regenerated
  draft and no downgrade renders exactly as before (S6). A `MATCH` with no recorded
  consumption event gets an honest "not determined from persisted events" note instead of
  implying delivery. Tests: `tests/test_ui_compliance_card.py`.
- **UI: a genuine DCCL draft-reuse delivery no longer mislabels itself as the
  deliberative path.** `_execution_summary_from_request` and `_build_delivery_path_summary`
  (`moralstack/ui/app.py`) derived "which path ran" from `decision_traces.trace_json.path`,
  which is persisted as an empty string on every real `COMPLIANCE_LAYER` stage row — so
  `path_badge` fell back to `DELIBERATIVE_PATH` and the delivery card missed the
  reuse-specific explanation (it also only matched the historical `governed_draft`
  `final_text_source`, never the active `governed` value) for every real reuse in the
  observability DB. Both view-builders now consult a new shared, event-first predicate
  (`_dccl_draft_reused`: `COMPLIANCE_DRAFT_REUSED` present and no `COMPLIANCE_MATCH_DOWNGRADED`
  veto), mirroring the semantics `_build_path_badge_info` already used for the adjacent
  reuse label on the same page. `path_badge` now reads `COMPLIANCE_FAST_PATH` and the
  delivery card renders its "reused" status/headline/explanation for a genuine reuse; a
  downgraded MATCH or a plain deliberative request is unaffected. Tests:
  `tests/test_ui_execution_path_label.py`.
- **UI: the delivery-path card no longer tells a proxy-authoritative story on
  direct/SDK-path (non-proxy) requests, and no longer collapses three distinct
  "no pre-delivery decision" situations into identical unknown text.**
  `_build_delivery_path_summary` (`moralstack/ui/app.py`) now computes a new
  `_proxy_participated` predicate (any of the six `PROXY_*` orchestration event
  types); when none is present, the card drops the "The proxy finalization event is
  the authoritative delivered result" claim, states plainly that this request never
  went through the OpenAI-compatible proxy layer, and reports which internal
  module/action produced the delivered text via a new `_infer_engine_internal_source`
  helper (an exact byte-identity comparison of persisted `llm_calls.raw_response`
  against `requests.final_response`, never a guess from wording). Separately,
  `_last_final_trace_payload` (used only by the delivery card, not by
  `_execution_summary_from_request`/`_pick_final_trace_row`, which are unchanged) now
  falls back to the last `PRE_POLICY` row when no `FINAL` row exists, so a
  proxy+pipeline-failure request shows its real last pre-crash decision instead of an
  unrelated last-inserted trace row; a genuine DCCL fast-path bypass (no
  `PRE_POLICY`/`FINAL` row by design) instead gets a new structural `pre_delivery_na`
  flag rendered as an explicit "n/a — DCCL fast-path bypass" meta-item, never
  "unknown / unknown". The unlabelled duplicate delivered-source `<span>` (which
  repeated the labelled "Authoritative final source" field with no added
  information) was also removed from the template. Tests:
  `tests/test_ui_delivery_provenance.py`.
- **UI: the "Path routing and risk governance" panel no longer hides a
  calibration-guard reversal of the raw operational-risk signal.** The panel's
  view-model (`build_orchestrator_observability`) reads only `debug_events` and
  the FINAL decision trace, both of which post-date calibration, so a
  `calibration_guard` that capped the raw `estimate_operational` risk (e.g.
  `DENY` -> `DELIBERATE`) rendered as if `DELIBERATE` were the native
  assessment. A new `_extract_calibration_guard_override` helper
  (`moralstack/ui/app.py`) compares the persisted `estimate_operational` and
  `calibration_guard` `llm_calls` raw responses and, when the guard changed
  `risk_policy_action`, the panel now states both the raw and capped
  `risk_policy_action`/`risk_score` before the branch/overlay values. Requests
  with no calibration_guard row, or a guard that did not change the action,
  are unaffected. Tests: `tests/test_ui_calibration_guard_panel.py`.
- **UI: the request-detail "Execution graph" is now a single linear input→output
  deliberation spine**, replacing the "By cycle" / "Execution order" toggle and the
  flat chronological view (`#view-chronological`, now removed along with its JS
  handler). The graph opens on a new INPUT anchor (prompt preview, plus a
  developer-contract chip and a conversation-history chip rendered only when
  `final_revalidation_info.developer_contract_present` / `conversation_context`
  actually say so) and every cycle's tiers now render in one continuous flow —
  cycle boundaries are an inline `.flow-cycle-marker` chip on the spine instead of
  a bordered box — ending on a new OUTPUT anchor (a `delivery_path_summary`-based
  delivered-answer summary, reusing its existing status→badge mapping, in front of
  the unchanged `final-decision-card`). For a pipeline-failure-shaped request the
  OUTPUT anchor renders an explicit failure card instead of the decision card, and
  the coerced `delivered_action` is never coloured/labelled as a governed success
  (invariants from iterations 01/06/12). Every module box keeps its exact header,
  IO annotations, timing bar and full expandable body (Prompt / System Prompt /
  Original Messages / Conversation History / Raw Response / Parsed Summary)
  unchanged, and parallel tiers still branch/rejoin the spine with their "parallel"
  label. Template + CSS only; no view-model or governance data change. Tests:
  `tests/test_ui_deliberation_spine.py`.

### Development

- **Global `--ignore-missing-imports` dropped from mypy.** Both the CI Type Check step
  and the pre-commit mypy hook now run plain `mypy moralstack`: the blanket flag
  suppressed every missing-stub error (it is what masked the undeclared PyYAML
  dependency). Third-party packages without stubs are handled by the existing targeted
  per-module overrides in `pyproject.toml` (`ruamel.yaml.*`, `langdetect.*`,
  `fastembed`/`numpy`). Clean-cache run verified green; tooling-only change.
- **memory-guard trace-doc path casing fixed**: `scripts/check_memory_updated.py`
  referenced `docs/TRACES/*` while the repository directory is `docs/traces/*`, making
  the guard structurally unsatisfiable for `moralstack/server/` and
  `moralstack/compliance/` changes (their only mapped docs were the mis-cased trace
  files). Aligned the four `BEHAVIOR_DOC_MAP` entries to the actual `docs/traces/`
  paths so the gate enforces the intended doc updates again.
- **AI harness**: added a self-maintaining memory contract and evolved the Claude Code
  hook mechanics (Stop verify dedup, PreCompact snapshot, SessionEnd diary, docs-gate)
  with offline harness tests under `tests/harness/`. Test-suite isolation hardened
  (test DB/logs decoupled from the developer `.env`, slow benchmark excluded from the
  default run, report version read from `pyproject.toml`).
- **Changelog guard (pre-commit)**: a local pre-commit hook
  (`scripts/check_changelog_updated.py`) blocks a commit when staged changes outside
  the AI/test infra prefixes (`.claude/`, `ai/`, `tests/`) are not accompanied by a
  `CHANGELOG.md` update. Bypass an intentional infra-only commit with
  `CHANGELOG_GUARD_SKIP=1`.
- **Memory guard (pre-commit)**: a local pre-commit hook
  (`scripts/check_memory_updated.py`) enforces the verified-memory contract at commit
  time — the guarantee the session-scoped Stop nudge could not give. Source of truth is
  `git diff --cached` (not the escapable `.session-edits.json`), and a **test does not
  count** as a memory substitute. Using a fine per-prefix mapping, a staged change under
  a governance-behavior prefix (`orchestration/`, `constitution/`, `observability/`,
  `server/`, `compliance/`, `prompts/`, `runtime/decision/`) requires the matching doc
  (`docs/modules/*`, `docs/TRACES/*`, `docs/decision_policy.md`, `docs/constitution.md`)
  to be staged. Bypass a justified exception with `MEMORY_GUARD_SKIP=1`.
- **Stop docs-gate hardened**: adding a test no longer silences the gate, and only a
  verified-memory ledger (`docs/CODEBASE_FACTS.md`, `docs/MORALSTACK_CODEBASE_INDEX.md`,
  `docs/TRACES/`, `docs/modules/`) satisfies it — an arbitrary `docs/` file no longer
  does. The `PostToolUse` formatter/edit-recorder now also fires on `MultiEdit`, closing
  a blind spot where multi-edits never reached `.session-edits.json`.

## 0.7.0 — 2026-07-06

### Added

- **Token accounting end-to-end**: every billable provider call now records token
  usage, aggregated per request and persisted for audit. New modules
  `moralstack/observability/token_usage.py` (`TokenUsage` value object) and
  `moralstack/observability/request_token_accumulator.py` (per-request
  accumulator). Token usage flows from the model layer (`models/base.py`,
  `models/policy.py`) through the orchestration modules (critic, hindsight,
  perspective, simulator), the deliberation runner, the local embedder, the SDK
  response (`sdk/response.py`) and the OpenAI-compatible proxy (`server/proxy.py`),
  and is stored via the SQLite sink / read store (`token_usage_json`,
  `billable_provider_call`).

### Changed

- **Prompt caching (deliberative LLM modules)**: reordered the prompts of every
  deliberative module (risk mini-estimators, critic, simulator, hindsight,
  perspective) so the static content (instructions, enums, JSON schema/skeleton,
  examples) lives in the system prompt as a byte-identical prefix, while only the
  per-request dynamic data stays in the user message. This enables OpenAI automatic
  prompt caching (stable prefix ≥1024 tokens), cutting input-token cost and latency.
  No behavior change: `response_format` stays `{"type":"json_object"}` everywhere and
  parsers, retries and JSON contracts are unchanged. Path-specific system prompts keep
  differing JSON contracts from colliding (critic full vs quick-check; simulator batch
  vs seeded; hindsight single/individual vs batch); perspective moves request/response/
  risk context into the per-perspective user message; the shared hindsight base framing
  is unified in `moralstack/prompts/_common.py` (`HINDSIGHT_BASE_FRAMING`). Observability
  persistence reflects the new system/user split.
- **AI review harness**: refactored the Codex/Cursor agentic-workflow commands and
  helper scripts (`.claude/commands/ai-review-*`, `scripts/ai/*`, `docs/ai/*`) and
  removed the obsolete `codex-review-coordinator` agent.

### Removed

- **Breaking:** the deprecated public package `moralstack.persistence` and all its
  submodules have been removed. No per-symbol compatibility aliases remain.
  - **Persistence DI:** import `PersistencePort`, `DefaultPersistence`, and
    `NullPersistence` from `moralstack.orchestration.persistence_port`,
    `moralstack.orchestration.default_persistence`, and
    `moralstack.orchestration.null_persistence` respectively.
  - **Emit helpers:** import `persist_*` and `async_persist_*` from
    `moralstack.observability.emit_helpers` (submodule import only — not re-exported
    from `moralstack.observability` top-level `__all__`).
  - **Config/context:** use `moralstack.observability.config` and
    `moralstack.observability.context` directly (`get_persist_mode` remains as an
    alias of `get_observability_mode` on the config module).
  - **SQLite writes:** use `moralstack.observability.sinks.sqlite_sink` for
    `init_db`, `create_run`, `upsert_request`, etc.
  - **SQLite reads:** instantiate `SqliteReadStore()` from
    `moralstack.observability.read_store` (or use `obs.read_store`) instead of
    the removed standalone functions from old `persistence.db`
    (`get_token_usage_totals`, `get_token_usage_breakdown`, `get_runs_page`,
    `get_request_domains`, `get_models_used_for_run`, `get_run`, etc.).
  - **Removed with no replacement:** `PersistenceWriteQueue` and `get_write_queue`
    (zero internal consumers; were public via the old package `__all__`).
  - **Removed alias:** `PersistMode` (was an alias of `ObservabilityMode` on the
    old config wrapper — use `ObservabilityMode` from
    `moralstack.observability.config`).
  - **Logger name change:** modules moved from `moralstack.persistence.default` /
    `moralstack.persistence.sink` to `moralstack.orchestration.default_persistence` /
    `moralstack.observability.emit_helpers`; update external log filters accordingly.

## 0.6.1 — 2026-06-25

### Changed

- **claude**: aggiungi skill release-new-version

### Other

- Fix PackageInfo version

## 0.6.0 — 2026-06-25

### Added — Developer Contract Compliance Layer (DCCL)

- **DCCL subsystem** (`moralstack/compliance/`: `dccl.py`, `types.py`, `config.py`,
  `safety_override.py`): new architectural component that evaluates whether a user
  request invokes a behavior explicitly authorized by the deployer's developer
  contract. It runs after the policy speculative and before the risk estimator,
  coordinating the pipeline via a cooperative early-return mechanism. Public API:
  `DeveloperContractComplianceLayer`, `ComplianceVerdict`, `ComplianceDecision`,
  `ComplianceSignal`, `StructuredRule`, `MatchedRule`, `EvaluationPath`,
  `TriggerType`, `ActionType`.
- **Evaluation paths**: structured rule matching, LLM-based contract compliance
  evaluation, and a hard-signal **safety override** (`safety_override.py`) that
  keeps DCCL subordinate to hard-signal supremacy — an authorized contract can
  never unlock self-harm / child-safety / weapons / physical-harm content.
- **Compliance fast-path** (`controller.py`): on a DCCL `MATCH`, the request takes
  a compliance fast-path; the speculative draft is validated against the matched
  authorized action (`validate_draft_against_action`,
  `DCCL_DRAFT_MATCH_SYSTEM_PROMPT`) rather than regenerated, and a
  `PROXY_OUTPUT_FINALIZED` event is persisted for the match.
- **DCCL LLM evaluation transcript** (`moralstack/compliance/dccl.py`): contract
  compliance prompts include a role-ordered conversation transcript when prior
  turns exist, not only the final user message — fixes history-dependent rules
  (e.g. authorization phrases in earlier user turns previously reported as
  `NO_MATCH`).
- **COMPL-AI llm_rules benign cases** wired through the compliance path.

### Added (Multi-turn context alignment)

- **Shared `ConversationContext` builder** (`moralstack/orchestration/conversation_context.py`):
  SDK and HTTP proxy now parse OpenAI-style `messages` through the same additive
  transcript view — developer contract, prior user/assistant turns, final user
  message, and a role-serialized transcript with a character budget. Single-turn
  callers with no prior history keep legacy behavior.
- **`ProcessedRequest.conversation_context`**: optional field on orchestration
  requests; SDK and proxy attach the shared context object alongside existing
  `prompt`, `developer_contract`, and `conversation_history` fields.
- **DCCL LLM evaluation** (`moralstack/compliance/dccl.py`): contract compliance
  prompts now include a role-ordered conversation transcript when prior turns
  exist, not only the final user message — fixes history-dependent rules (e.g.
  authorization phrases in earlier user turns that DCCL previously reported as
  `NO_MATCH` despite being present in the request body).
- **Delivery–governance context guard** (`evaluate_delivery_context_guard`,
  `moralstack/orchestration/controller.py`): detects when final upstream delivery
  would see materially broader context than speculative/governance paths; surfaces
  `delivery_context_broader_than_governance`, `governance_context_mode`, and
  `candidate_context_mode` on `OrchestratorResult` and observability metadata;
  blocks unsafe reuse of a last-user-only speculative draft when misaligned.
- **`CONVERSATION_CONTEXT_ATTACHED` orchestration event**
  (`moralstack/orchestration/orchestration_event_taxonomy.py`): audit trail of the
  context shape attached per request.
- **Speculative generation alignment** (`controller.py`): when prior turns exist,
  speculative `generate()` uses the role-serialized transcript instead of
  last-user-only input where configured.
- **Trace docs**: governance/multi-turn flow documents moved under `docs/traces/`
  (lowercase path; replaces `docs/TRACES/`).

### Tests (Multi-turn context alignment)

- `tests/test_multiturn_context_alignment.py`: shared builder transcript shape,
  SDK legacy extractors, DCCL prompt includes prior turns, delivery-guard
  blocking vs aligned speculative draft reuse.

### Fixed

- **Concurrent `conversation_id` observability leak (HTTP proxy + threadpool):**
  `OrchestrationController` no longer stores per-request multi-turn / ledger scratch
  state on a shared instance attribute. A stack-local `ProcessCallContext`
  (`moralstack/orchestration/process_context.py`) is passed through `process()` and
  internal helpers, eliminating cross-request contamination when multiple
  `conversation_id` values run in parallel. Regression coverage:
  `tests/test_orchestrator_concurrent_ctx.py`,
  `tests/test_server_proxy.py::TestAsyncConcurrency::test_concurrent_distinct_conversations_jsonl_metadata_matches_session`.

### Added (Step 14.5)

- **JSONL channel semantics documentation and consolidation script**
  (`docs/modules/observability.md`, `scripts/consolidate_jsonl_meta.py`):
  the 16 canonical `event_type` values fall into three persistence categories —
  atomic insert (one row per envelope, identical in JSONL and SQLite),
  merge-update (JSONL holds successive deltas; SQLite consolidates via JSON merge),
  upsert (JSONL holds successive snapshots; SQLite uses INSERT OR REPLACE). The
  documentation explains the divergence and its implications for offline consumers;
  the new `consolidate_jsonl_meta.py` script derives consolidated state from
  JSONL alone, mirroring `update_request_meta(merge=True)` in the SQLite sink.

### Tests (Step 14.5)

- `tests/test_consolidate_jsonl_meta.py`: six tests covering passthrough,
  progressive merge last-write-wins, multi-request isolation, skipping envelopes
  without meta or malformed payloads, and CLI end-to-end.

### Added (Step 14.6)

- **Horizontal conversation strip in the UI**
  (`moralstack/ui/templates/conversation.html`,
  `moralstack/ui/static/css/main.css`): on `/conversations/<cid>`, above the
  existing "Posture timeline" table, a horizontal strip of colored rectangles is
  now rendered. Each rectangle is one turn: height proportional to `risk_score`,
  color by `final_action` (green/yellow/red), orange border for `ESCALATED`
  posture, ⚡ icon on cached turns, hover tooltip with metadata, click navigates
  to the request page. Makes long multi-turn conversations scannable at a glance
  (especially COMPL-AI benchmarks with 12–30 turns). Pure CSS implementation: no
  JavaScript and no backend or view-model changes.

### Tests (Step 14.6)

- `tests/test_ui_conversation_strip.py`: five tests verifying section presence,
  one cell per turn, action-specific CSS class, cached-turn icon and arrow, and
  escalated border.

### Fixed (Step 14.8)

- **Structural `posture` asymmetry between SemanticDecisionLedger store and lookup**
  (`moralstack/orchestration/controller.py`): the posture formula in
  `_extend_state_out_v04` (ledger `store` call site) read `state.active_overlay`,
  but the controller never populated that field because
  `update_from_processing_result` was called without `overlay=`. Result: store
  always used `posture="NORMAL"`, even on sensitive overlays (legal, medical,
  mental_health, political, journalism, financial, healthcare, emergency,
  cybersecurity, children, environment), while lookup used `posture="ELEVATED"`
  (correctly derived from `is_overlay_sensitive`).

  Consequence: `LedgerKey(contract_hash, posture, domain)` differed between store
  and lookup for every sensitive overlay, making cache hits **structurally
  impossible** on all safety-critical domains. The bug was latent — tests missed
  it because `multiturn_quickstart_fastpath_hit.py` used a non-sensitive domain
  (empty or environment normalized to `None`), yielding `NORMAL` posture on both
  sides by coincidence.

  **Fix:** `_extend_state_out_v04` now uses
  `is_overlay_sensitive(self.constitution_store, request.get_domain())` directly —
  the same function as the lookup side — so store and lookup keys match by
  construction. `state.active_overlay` remains a separate UI signal but is no
  longer authoritative for ledger posture.

### Tests (Step 14.8)

- `tests/test_ledger_posture_symmetry.py`: five tests explicitly verifying the
  store-posture == lookup-posture invariant across combinations of
  `(final_action, overlay_sensitive, hard_constraints)`. Includes a regression
  guard that sets `state.active_overlay='legal'` but forces
  `is_overlay_sensitive=False` for the domain, asserting posture is `NORMAL`
  (pre-fix would have been `ELEVATED`).

### Added (Step 14.7)

- **Demonstrable example and E2E tests for the fast-path gate-rejected branch**
  (`examples/multiturn_quickstart_gate_rejected.py`,
  `tests/test_ledger_fast_path_gate_rejected_e2e.py`):
  until now the three branches of `is_safe_to_apply` were covered only by
  synthetic unit tests (Step 14.4). There was no runnable Python example that
  produced `LEDGER_FAST_PATH_NOT_APPLIED` in a real run, and no deterministic
  test verifying end-to-end gate rejection emission.

  The new example builds a three-turn scenario: turn 1 is cached as
  `NORMAL_COMPLETE`; turn 2 — semantically similar on topic but with a more
  technical-operational framing — routes to `route='deliberative'`. The ledger
  hits turn 1 but the gate rejects application, emits `LEDGER_FAST_PATH_NOT_APPLIED`
  with `gate_reason='current_route_requires_deliberation'`, and full deliberation
  runs.

  This illustrates the safety model: caching helps only when applying it does not
  weaken guarantees for the current turn.

### Tests (Step 14.7)

- `tests/test_ledger_fast_path_gate_rejected_e2e.py`: three tests in two classes
  covering (a) rejection emit contract with full payload via real runner and mock
  emitter, (b) `gate_reason` derivation for `deliberative_loop`, (c) defensive
  derivation for unknown routes.

### Docs (Step 14.7)

- `docs/modules/observability.md`: new "Fast-path safety gate" section documenting
  the three logic branches and associated events.

### Added (Step 14.4)

- **Canonical events `LEDGER_FAST_PATH_APPLIED` and `LEDGER_FAST_PATH_NOT_APPLIED`**
  (`moralstack/orchestration/controller.py`,
  `moralstack/orchestration/orchestration_event_taxonomy.py`):
  when the SemanticDecisionLedger hits and the safety gate accepts applying the
  cached decision, the controller emits an explicit `orchestration.event`.
  When the gate rejects application, it emits `LEDGER_FAST_PATH_NOT_APPLIED` with
  the rejection reason.

  Result: skipping deliberation is now visible in the official orchestration event
  channel (`orchestration_events` table, `orchestration.event.jsonl`) and
  automatically in the UI metro map and journey list, without manually joining
  `ledger_events` with `conversation_states`.

  Existing internal `orch_debug_log` entries (`H-ledger-hit-applied`,
  `H-ledger-hit-skipped`) are retained for low-level debugging.

### Tests (Step 14.4)

- `tests/test_ledger_fast_path_events.py`: six tests in three classes verifying
  constant registration in `ALL_EVENT_TYPES`, capturing-emitter contract, and
  every branch of `ConversationalFastPathRunner.is_safe_to_apply`.

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

### Changed — Deliberation context separation

- **Developer contract / conversation history as a separate layer** in every
  deliberative module (Critic, Simulator, Perspectives, diagnostic verdict): prior
  turns and the developer contract are managed in a dedicated context layer and
  are **not injected into module prompts**, so each module reasons over the
  contract/history without polluting its own instruction prompt. Includes coherent
  ambiguity-flag handling and critic awareness of the developer contract.

### Changed — Governed delivery (validated draft reuse)

- **Speculative draft is reused, not regenerated upstream**: every deliberation
  path now returns a single *validated* draft and serves it to the user instead of
  triggering a fresh upstream generation. A final-response validation step runs
  before delivery. Reinforces the governed-delivery invariant (Plan 1): the wrapped
  upstream client never produces the delivered answer.

### Changed — Proxy as a pure upstream client

- **Proxy no longer uses its own model** (`moralstack/server`): model usage was
  removed from the server proxy so MoralStack's configuration is the single source
  of governance truth; the proxy behaves as a pure OpenAI-compatible client.
- **Explicit generation overrides** (`top_p`, `max_tokens`, `temperature`) are now
  applied for proxy-as-GPT-client requests; the policy system prompt was
  reformatted. Covered by `tests/test_generation_overrides.py`.
- **`chat/completions` alternate context** and host/port loaded from environment
  variables.

### Added — Constitution principles & overlays

- **Child-safety and gender-equality principles** (`children.yaml`, `core.yaml`,
  related overlays): new principles strengthening child safety and gender
  equality, with the indiscriminate Q17 hard signal reduced on concrete requests
  so legitimate operational queries are no longer over-flagged.
- **`environment` domain overlay** (`moralstack/constitution/data/overlays/environment.yaml`):
  new domain overlay (joining the v0.4 `violent_crime.yaml`).

### Changed — Decision policy

- **`final_action` field propagation** through the governance pipeline.
- **SAFE_COMPLETE escalation refined**: escalation on sensitive domains now happens
  only under specific conditions (non-actionable request with no risk yields
  `NORMAL_COMPLETE`), not solely because a domain is flagged sensitive.

### Fixed — Observability, correlation & benchmarks

- **Conversation correlation** and assorted observability issues across the
  step 13 work: `llm_calls` persistence and observability rewritten, ledger events
  that were never raised now emitted, mini-estimator save timing reduced to cut
  per-request latency, and duplicate constitution retrieval (by design) no longer
  shown on the same temporal row in the UI.
- **COMPL-AI benchmark suite** fixes.
- **Manual-dispatch publish job** fixed in CI.

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

- **COMPL-AI benchmark path**: `examples/server_quickstart.py` serving `moralstack.server.proxy.create_app` — OpenAI-compatible FastAPI proxy (`/v1/chat/completions`, `/chat/completions`) routing requests through MoralStack governance (env `MORALSTACK_OPENAI_COMPATIBLE_*`).
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
