# Changelog

All notable changes to this project will be documented in this file.
The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- **`CYCLE_SUMMARY` decision traces carry `critique_outcome`.** The additive key holds the per-cycle critique outcome (`completed`, `skipped`, `failed`, `timeout`, or `""` when the critic was not invoked; FINAL rows use `not_invoked` for that case), so a reader can tell "a critique object exists" (`modules_executed`, unchanged) from "this cycle's critique completed".

- **The INPUT node of the request page shows the request's developer contract.** Before, it only carried a
  "developer contract" chip and the full prompt; the contract text was visible only inside the module nodes. The node
  now has a collapsed "Developer contract" section with the text, its length and the identity check; when a contract
  was attached but its text cannot be identified, the node says so. The "developer contract" chip on the node is lit
  whenever the section is shown.

### Fixed

- **Soft-revision and generation failures no longer escape on an unprintable exception.** `Soft revision failed: ...`, `Generation error: ...` and the `GenerationError` message now go through the same guarded text (`str(e)`, else the class name) as the other deliberation modules; the soft-revision WARNING logs the class name with the text in `exc_info`.
- **A raising diagnostics logger no longer turns a completed module call into a module error.** `record_llm_call` now wraps `logger.log_call` in the same swallowing try/except (WARNING) as the persistence branch, so the simulator, perspectives, hindsight and policy call sites are covered like the critique ones (PROJECT_SPEC §5.6). `OrchestrationController._estimate_risk`, which called `logger.log_call` directly inside the `try` that re-raises `RiskEstimationError`, now swallows it too, and its `RiskEstimationError` message uses guarded text.
- **`examples/audit_export.py` reads the JSONL directory from `MORALSTACK_OBSERVABILITY_JSONL_DIR`.** It read `MORALSTACK_JSONL_DIR`, which nothing else sets, so without `--input` it always looked in the default `logs/observability`; the hint messages name the right variable too.
- **A blank `OPENAI_MAX_TOKENS=` or `MORALSTACK_UI_PORT=` in the env file no longer crashes startup.** Both are now purged when blank after loading, like the other optional keys, so `int(os.getenv(key, default))` falls back to the default instead of raising `ValueError` on `int("")` (policy and upstream-draft sampling defaults, `moralstack-ui` port).
- **A refusal after a failed or timed-out final critique no longer reuses the previous cycle's critic guidance.** `ResponseAssembler._make_refusal` ignores `last_critique.revision_guidance` (rationale and suggested alternatives) when the final-cycle critique outcome is `failed` or `timeout`, and the controller now passes the neutral rationale ("The safety check could not be completed.") for any REFUSE in that state, not only on the fail-closed flip. Wording only: `final_action` and the flip logic are unchanged, and on a REFUSE decided before the critique failed the neutral sentence in `refusal_reason` is not the cause of the refusal, which stays in the structured decision fields.
- **`revalidate_final_output` no longer retries the principle retrieval without `retrieval_phase` on a `TypeError`.** The call is made once; a `TypeError` (for example a third-party store without the keyword) now fails closed to the governed REFUSE `final_revalidation_error` instead of silently running a second retrieval. The function is dormant on active delivery paths.
- **Exception text no longer reaches the remaining orchestration diagnostic sinks.** The `critique`, `simulate`, `evaluate` (hindsight) and `perspectives` `(ERROR)` rows now read `ERROR: <ExceptionClassName>`; the hindsight failure `debug_events` payload drops `error_message` and keeps `error_type`; `SPECULATIVE_RESULT_DISCARDED` and `PROXY_FINAL_REVALIDATION_ERROR` payloads rename `error` to `error_type`. The text stays in `state.errors` and goes to a guarded server-side log (INFO without traceback for the designed timeout skip, WARNING with `exc_info` otherwise). Every `str(e)` left in those except bodies is guarded, so an exception whose `__str__` raises can no longer escape. Behavior note: an exception with an empty message now renders its class name in `state.errors` (`Simulation error: RuntimeError`).
- **`GovernanceConfig.observability_mode`, `jsonl_dir` and `db_path` now take effect.** They were read nowhere. Explicit values now beat the `MORALSTACK_OBSERVABILITY_*` env vars process-wide when applied by the first `govern()` / `create_app` before the observability write queue is bound; a later different value is refused with a WARNING and never redirects a live target (parent and child rows must stay in one database). `"off"` (the default), `None` or blank inherits env/defaults and does not disable writing. The `moralstack-ui` process still reads env only.
- **`suite_record.py check` no longer mismatches after committing, staging or `git rm` of unchanged content.** Tracked and untracked files are hashed as one set (`content_sha256`, `file_count`); schema-1 records are rejected once.
- **The request page shows the DCCL and the steps around it in the order the code runs them.** DCCL rows persisted
  with `cycle=NULL` were drawn as a second "Initial assessment" group before retrieval and risk, `safety_override`
  came before `evaluate`, and the fast-path refusal was drawn "parallel" with the hard-signal gate and before path
  routing. The cycle-0 rank now follows the controller: prefilter (with the speculative draft when its run overlaps
  it) → domain agents → risk minis → calibration → DCCL `evaluate` → `safety_override` → regenerate / revalidate →
  gate or downgrade → contract-skipped modules (one "skipped · N modules" step) → path routing → ledger → deliberation
  retrieval → draft → quick check → refusal. Legacy NULL-cycle DCCL rows render in cycle 0 with a "legacy row" badge;
  any other NULL-cycle row is labelled "Unsequenced rows (cycle NULL)". The journey uses the same order, marks steps
  "Parallel" only for more than 1 ms of overlap, the timeline lists `compliance_layer` after `risk_estimator`, and the
  pipes into DCCL nodes say what flows ("risk + draft → DCCL", "verdict → safety check", "hard-signal gate (P0)",
  "MATCH downgraded", "regenerated draft → revalidate"). Display only: no decision path changes.
- **The request page shows the developer contract on every node that received it, and only there.** New
  `moralstack/ui/contract_delivery.py` identifies the contract by the verdict's `contract_hash` and reads each call's
  recorded wire to say how it arrived (native developer or system message, embedded in the system or user prompt) and
  whether it was full or truncated. The critic, whose summary is plain text, now shows it too. `safety_override`,
  `draft_revalidate`, refusal and synthetic nodes no longer claim it; their input rows describe what they received
  (`action_payload`, `draft` + `rule_action`). The client-body section is renamed "Original request messages (client
  body)".

### Changed

- **The implementer sub-agent no longer leaves hung background commands behind.** `claude-implementer` and the
  handoff template forbid commands that read stdin and moving a hung command to the background, and the report gains a
  `Background commands` line; `/ai-implement` stops an implementer that still has background work after its report
  (`TaskStop`) and, for `isolation: "worktree"` slices, authorizes one `git merge --ff-only` to the local HEAD because
  the worktree starts from `origin/main`. New harness tests pin these rules and the plan/implement/review contract (no
  Edit/Write tool for the reviewer, implementer on Sonnet, non-Sonnet reviewer model, no reviewer `model:` override in
  the review commands).
- **`docs/CODEBASE_FACTS.md` records the 2026-10-25 fixes as verified facts.** Eight rows cover the exception-text
  sinks, the stale-critique refusal and `critique_outcome`, the swallowed `log_call` errors, the single ledger writer,
  the blank env keys and the implementer harness rules. A NOT FIXED row records that five WARNINGs and the persistence
  WARNING still put exception text in the message. Documentation only.
- **Docs wording for the observability fields.** The README now says an explicit `GovernanceConfig` observability
  value applies before the write-queue target is bound; the `observability_mode` docstring names the legacy
  `MORALSTACK_PERSIST_MODE` alias. Text only, no behavior change.
- **The fail-safe REFUSE and the final-action fallback report `ERROR_PATH`.** `ResponseMetadata.for_fail_safe` and the
  `ensure_final_action_and_return` fallback result used `DELIBERATIVE_PATH`. `GovernanceMetadata.path` and
  `meta_json.path` change for these failure rows, and on the final-action fallback route `X-Moralstack-Path` changes
  from `DELIBERATIVE_PATH` to `ERROR_PATH` (the generic-except header was already `ERROR_PATH`). `path_taken`, the
  generic-except debug log label and `final_action` (`REFUSE`) are unchanged. The persisted conversation-state turn
  summary `winning_rule` (generic-except and ensure-fallback turns), the ensure-fallback `execution_trace` path and the
  benchmark export's `moralstack_path` for ensure-fallback rows also become `ERROR_PATH`.
- **`docs/CODEBASE_FACTS.md` is compacted from ~300 KB to ~150 KB.** Rows over 1,500 characters are condensed with
  a `Full detail:` pointer, and FIXED/RESOLVED gaps are moved; the originals are kept verbatim in
  `docs/archive/codebase_facts_detail.md`.
- **`PROJECT_SPEC.md` tells agents to Grep the codebase index and the facts ledger instead of reading them in
  full**, and §8 points to the hook and memory docs instead of restating them.
- **A new *direct* lane in `docs/ai/AGENTIC_WORKFLOW.md`** lets the orchestrator edit small harness/docs changes
  itself, keeping the pre-commit-verifier gate and one independent `/ai-review-diff` (never for `moralstack/**`,
  non-harness tests or invariant text).
- **`.mcp.json` pins `@playwright/mcp@0.0.83`** instead of `@latest`.
- **The parked `docs/eu_ai_act_landscape.md` research note is removed** (recoverable from git history).
- **Targeted test runs are serial, and the full suite is recorded per tree state.** Selecting part of the suite
  (files, directories, node ids or `-k`) no longer starts up to 8 xdist workers; the full suite stays parallel and an
  explicit `-n` still wins. `scripts/ai/suite_record.py run|check` fingerprints the working tree and reuses a PASSED
  full-suite record for an identical state, never a FAILED or stale one. Agent instructions that launch the full suite
  now say to use the Bash tool `timeout: 600000`.
- **The `ai-*` agent workflow has a full and a lean lane.** Lean is allowed only when every touched file is on an
  explicit harness/`docs/ai/` allowlist (`docs/ai/AGENTIC_WORKFLOW.md`); it skips the cartographer, test-strategist and
  final-integrator and runs the reviewer at effort `high`. Everything else, `moralstack/**` and non-harness tests
  included, stays full with the reviewer pinned at `xhigh`. Both lanes keep the plan review, the diff review and the
  pre-commit-verifier. Handoffs are one self-contained slice each, fix passes start a new implementer, and a review
  still BLOCKING after its second re-run is escalated to the user.
- **The codebase cartographer is a locator built on the Python language server.** The project enables the official
  `pyright-lsp` plugin (it needs `pyright-langserver` on PATH: `npm install -g pyright`). The cartographer finds
  callers with LSP `incomingCalls`, searches with Grep/Read instead of shell `grep`/`sed`, and returns a reading list
  (regions, callers, pinning tests, side effects, docs) with no causal claims. The new read-only
  `scripts/ai/impact_for_paths.py PATH...` lists the tests that import, patch, re-import or name each file and the docs it
  maps to (memory-guard map, `file:line` anchors, ledgers); `/ai-plan` saves its output verbatim next to the map. On
  three past tracks this took 2.4-3.7 min instead of 5.1-13.1 and found the same code and tests and 16/18 docs instead
  of 8/18.
- **DCCL `llm_calls` rows are persisted in cycle 0.** The `evaluate` (`sequence_in_cycle` -5) and `safety_override`
  (-6) rows written by `moralstack/compliance/dccl.py` now carry `cycle=0` instead of `NULL`, in SQLite and in the
  JSONL envelope; sequence numbers are unchanged. Readers that treated NULL as "pre-cycle" should read cycle 0 with
  these sequence numbers; the request page and the markdown export already handle both.
- **Documentation, docstrings and code comments are in English.** The remaining Italian comments and docstrings in
  `moralstack/`, `tests/`, `scripts/`, `analytical_utils/` and `examples/`, the comments in `pyproject.toml`,
  `requirements.txt` and `.gitignore`, the `slow` pytest marker description (shown by `pytest --markers`), and the
  Italian labels in `docs/modules/risk_estimator.md` and `docs/refactoring_diary.md` were translated; no behavior
  change (code, Python string literals, prompt and constitution data are unchanged). Italian stays only as data:
  multilingual test inputs, prompt and constitution YAML, golden fixtures, and quotes of them.
  `docs/architecture_spec.md` is excluded until its English rewrite. The new `tests/harness/test_english_only.py` fails
  on Italian in Markdown, comments and docstrings; `PROJECT_SPEC.md` §6 now requires English.
- **`docs/architecture_spec.md` is rewritten in English against the current code.** The old "Engineering
  Specification v1.0" described components that do not exist (a Mistral-7B LoRA policy model, a DeBERTa hindsight
  reward model, a training reward formula, a `/v1/chat` REST API, a `MoralStackClient` SDK, a `config/moralstack.yaml`
  file, a Kubernetes deployment). The new document follows the real request lifecycle (speculative overlap, DCCL,
  routing, deliberative loop, decision bounds, delivery guards), the LLM-based modules, the constitution, multi-turn
  governance, the SDK and proxy surfaces, failure handling, observability and configuration, with Mermaid diagrams and
  tables, and lists the gaps observed in the code (unused config fields, placeholder `moralstack-server`). The spec is
  no longer exempt from `tests/harness/test_english_only.py`. Documentation only: no behavior change.
- **`docs/MORALSTACK_CODEBASE_INDEX.md` corrected where it contradicted the code.** §5.1 claimed `response_format`
  stays `json_object` everywhere: only the critic uses it, while the risk minis, simulator, hindsight, perspectives
  and domain prefilter use a strict `json_schema`. The console entry points are cited at `pyproject.toml:62-66` and
  `server/proxy.py:1016`, and the `moralstack/compliance/` section is titled as the DCCL instead of a COMPL-AI bridge.
- **`examples/README.md` lists every example.** It pointed to a non-existent `examples/.env.example` (now the
  repo-root `.env.template`), counted 19 overlays (there are 21) and left out the proxy, multi-turn and AIR-Bench
  regression examples, which now have their own table.
- **Hindsight and risk-estimator docs match the code.** `docs/modules/hindsight.md` described the recommendation
  as total score ≥ 0.7 / 0.3–0.7 / < 0.3; the code reads `expected_value`: `PROCEED` at ≥ 0, `REVISE` down to
  `refuse_threshold` (default −0.7), `REFUSE` below. `MORALSTACK_HINDSIGHT_REVISE_THRESHOLD` is documented as loaded
  but not read. The `HindsightConfig` comments and the `from_evaluations` docstring no longer cite `worst_case` or a
  non-existent `revise_threshold` argument, and the risk estimator docstring names q1–q17 instead of q1–q13. No
  behavior change.
- **`moralstack/server/proxy.py` docstrings describe governed delivery.** The module and handler docstrings still
  said NORMAL_COMPLETE / SAFE_COMPLETE are forwarded to the upstream client; the code delivers the governed text as a
  synthetic completion for every action and fails closed to a governed refusal (Plan 1). Docstrings only.

### Deprecated

- **`OpenAIPolicy.refuse()` is deprecated and will be removed in 2.0.** It now emits a `DeprecationWarning` and is
  otherwise unchanged (same prompts, same return). The governed pipeline never calls it: refusals come from
  `moralstack.orchestration.safe_refusal_generator`. `PolicyLLMProtocol.refuse` stays until 2.0.
- **Five loaded-but-unread config fields are deprecated and will be removed in 2.0.**
  `OrchestratorConfig.max_critical_violations`, `early_exit_hindsight_threshold` and `safe_response_on_error`,
  `HindsightConfig.revise_threshold` and `EnsembleConfig.conservative_on_failure` (env vars
  `MORALSTACK_ORCHESTRATOR_MAX_CRITICAL_VIOLATIONS`, `..._EARLY_EXIT_HINDSIGHT_THRESHOLD`,
  `..._SAFE_RESPONSE_ON_ERROR`, `MORALSTACK_HINDSIGHT_REVISE_THRESHOLD`,
  `MORALSTACK_PERSPECTIVES_CONSERVATIVE_ON_FAILURE`) still parse and still have no effect. A non-default value now
  emits a `DeprecationWarning` on any construction (environment loads included, where default filters hide it) and one
  WARNING per load from the environment; defaults stay silent. Under strict warnings (`-W error::DeprecationWarning`) a non-default environment value makes the loader
  raise: `load_orchestrator_config_from_env()` fails closed with `GovernancePipelineError`, and a non-default
  hindsight or perspective value fails bootstrap closed ("Failed to initialize deliberation modules").
  `.env.template` no longer ships these lines active (its `EARLY_EXIT_HINDSIGHT_THRESHOLD=0.75` differed from the
  code default `0.6`).

### Removed

- **The uncalled private `OrchestrationController._store_decision_in_ledger` is removed.** It was a second ledger store path with no caller in `moralstack/` or `tests/`; the ledger is written only by `_maybe_store_in_ledger`. No behavior change; doc line anchors of `controller.py` are re-anchored by content, including the continuation anchor of the `draft_revalidate` row in the observability trace; anchors that were already stale stay tracked as a known gap.
- **The `moralstack-server` console script and `moralstack.server.proxy.main` are removed.** The script only raised
  `NotImplementedError` (formally breaking in 1.x, but it never worked). Use `create_app` from `moralstack.server` in
  your own launcher, or, from a source checkout, `uvicorn examples.server_quickstart:app`. The run meta
  `source: "moralstack-server"` in the audit data is unchanged. Editable installs keep a stale launcher until
  `pip install -e . --no-deps` is re-run.

## 1.0.0 — 2026-10-21

### Upgrading from 0.7.0

This release marks the API as stable. The entries below remain the complete record; this list collects the changes
that can break an existing integration, with the migration in the matching entry.

- **Refusals and pipeline errors (SDK and proxy clients).**
  - Every SDK-delivered REFUSE carries `finish_reason="content_filter"` (was `stop`), as the proxy already did.
  - A pipeline error or timeout is a governed REFUSE: `finish_reason="content_filter"`, `X-Moralstack-Decision: REFUSE`
    and `X-Moralstack-Path: ERROR_PATH` (were `stop`, `UNKNOWN` and `DELIBERATIVE_PATH`); the SDK reports
    `final_action="REFUSE"`.
  - `X-Moralstack-Decision` reports the delivered action, so an unlabelled result reports `REFUSE` (was `UNKNOWN`).
  - A pipeline error always delivers the fixed sentinel: `MORALSTACK_ORCHESTRATOR_SAFE_RESPONSE_ON_ERROR=false` is
    ignored. The SDK `decision_reason` is the fixed string `Pipeline error`; the exception text goes to the
    `moralstack.sdk.wrapper` logger.
- **Python API.**
  - `create_orchestrator()` defaults `timeout_ms` to 600000 (was 5000).
  - An injected policy must accept the `PolicyLLMProtocol` kwargs (`system`, `config`, `overrides`,
    `model_override`); a `TypeError` now fails the request closed instead of being retried without the system prompt.
  - The retired risk-estimator surface is removed, including `create_risk_estimator`, `create_conservative_estimator`,
    `calibration.parse_risk_response`, `RiskEstimation.triggered_signals`, `get_risk_env_bool` and
    `get_pooling_diagnostics()`; passing a removed `RiskEstimatorConfig` field raises `TypeError` (see "Removed").
  - Without `MORALSTACK_RISK_MODEL` and the per-mini slots, an SDK `GovernanceConfig(model=...)` or CLI
    `--openai-model` now runs the three risk mini-estimators on that model.
- **Configuration.**
  - Custom constitution YAML must drop overlay `keywords` and principle `keywords` / `keywords_block` /
    `keywords_style`, or the store fails at construction with `ConstitutionLoadError`.
  - These env vars are ignored and can be deleted from your env file: `MORALSTACK_RISK_FALLBACK_SCORE`,
    `MORALSTACK_RISK_FALLBACK_CONFIDENCE`, `MORALSTACK_RISK_LOW_THRESHOLD`, `MORALSTACK_RISK_MEDIUM_THRESHOLD`,
    `MORALSTACK_RISK_REQUIRE_DELIBERATION_ON_FALLBACK` and
    `MORALSTACK_RISK_CATEGORIZE_{BENIGN,SENSITIVE,CLEARLY_HARMFUL}_THRESHOLD`.
  - `.env.minimal` is removed; `.env.template` is the single template.
- **New for deployers:** prompt overrides through `GovernanceConfig.prompts_dir` / `MORALSTACK_PROMPTS_DIR`
  (`docs/prompt_overrides.md`).
- **Packaging:** a wheel install now includes `signals.yaml` and the dashboard templates and static assets (see
  "Fixed"). Releases are published by hand; there is no GitHub Actions workflow.

### Added

- **Approved-prompts gate and deployer guide (prompt externalization, step A5).**
  `tests/prompts_golden/snapshot_cb0d8b7.json` is now `approved_prompts.json` with a sidecar `approved_prompts.sha256`;
  `python -m tests.prompts_golden.capture --approve` is the only way to change it and prints the key diff (the A0 write
  path is gone). `test_prompt_golden.py` now fails once, with the keys named, when a packaged default changes
  (`test_prompt_defaults_match_approved_snapshot`), plus `test_approved_snapshot_sidecar_hash` and
  `test_every_known_prompt_name_is_in_snapshot`; the per-key and per-section comparisons and the A0-only guards are
  removed, the capture-determinism and oracle-robustness tests stay. The six persisted-row keys that the A0 snapshot did
  not have (critic `quick_check` and hindsight single retry-failed rows, added in 2026-10-20 fixes) are folded in; no
  other key changed. The prompt-override deployer guide (`docs/prompt_overrides.md`) now has the complete fragment table,
  the coherent `when` groups and the block-scalar rules; `docs/creating_overlays.md` states that an overlay's
  `simulator_domain_guidance` takes precedence over `simulator.domain_guidance.*`. The stand-in fallbacks that read a
  missing `_prompt_set` are removed from `DeliberationRunner` and `OpenAIPolicy.rewrite`. No prompt byte changes.

- **Prompt-audit verifier (prompt externalization, step A4c).**
  New `scripts/verify_prompt_audit.py`: a read-only, offline, free command-line check of a database (`--db`) and/or a JSONL
  directory (`--jsonl-dir`). It validates each run's `prompt_config` (schema, hashes, `default_fingerprint` against the
  packaged defaults, the effective set rebuilt from the recorded overrides and compared with `set_fingerprint`, and
  `safety_fragments_overridden`), classifies every `llm_calls` row (`ok`, `missing`, `mismatch`, `unknown`, `synthetic`,
  `no_wire`, `wire_integrity`) by checking the persisted system prompt and the `wire` record's digest, and checks each
  request's `prompt_set` link. A database without `prompt_config` is classified as pre-feature, not rejected. Exit code 0;
  `--strict` returns 1 on a mismatch, an integrity error or an unrebuildable run; offending rows carry their reason. No
  production code and no persisted field changes (the script loads only the leaf modules it needs). New
  `tests/test_verify_prompt_audit.py`; the tool is documented in `docs/modules/observability.md`.

- **Wire records on the DCCL, refusal, policy, speculative and retrieval rows (prompt externalization, step A4b-4..6; `KNOWN_GAPS` is now empty).**
  The DCCL `evaluate`, `safety_override` and `draft_revalidate` rows, the contract-regeneration, speculative (internal and
  upstream), benign / fast / safe-complete / deliberative / soft-revision policy rows, the critic `quick_check` row, the
  refusal rows (`wire_calls` for every attempt, including the anti-leak retry that no row recorded) and the constitution
  retriever rows now carry the exact messages sent in `parsed_summary_json`. Two additive rows/calls: the domain-exclusion
  message call is now persisted as an `llm_calls` row (`action="domain_excluded"`; it adds that call's tokens to the request
  totals, which previously missed it), and `validate_contract` classifies through the persisting wrapper (a row only when it
  runs in a request scope). UI effect: the policy, refusal, draft-match and G13 rows, whose `parsed_summary_json` was
  NULL, now show a raw wire JSON block and a `wire: {...}` preview in the request view (no UI code change). No existing
  field changes meaning; the DCCL legacy-branch and speculative native-branch
  `prompt` columns keep their value and the wire is the authority there. New `tests/test_wire_batch3.py`.

- **Wire records on the estimator, critic, simulator, hindsight and perspectives rows (prompt externalization, step A4b-1..3).**
  The estimator mini rows, the critic, the simulator (batch and seeded), hindsight (batch and single) and perspectives rows, and
  every retry-failed row of those modules, now carry the exact messages sent: `wire` (or `wire_calls` + `wire_strings` for the
  seeded simulator, perspectives and multi-consequence hindsight rows) in `parsed_summary_json`; the critic's in `parsed_json`,
  because its summary is plain text pinned by tests. Records are built at the call site from the arguments passed to the policy,
  wrapped (a failure never reaches the module call), carried by additive optional fields on the result objects and cleared on a
  cache hit. No existing field changes meaning, no row is added or split. Removes those modules' entries from `KNOWN_GAPS`
  (`tests/test_persistence_fidelity.py`); new `tests/test_wire_modules.py`.

- **Persistence fidelity: characterization and wire codec (prompt externalization, step A4b, batch 1).**
  New `moralstack/observability/wire.py` (pure codec for the record that will describe the exact messages sent by a
  call: `generate_messages_for`, `encode_wire`, `encode_wire_calls`, `decode_wire`, `wire_sha256`) and two test files:
  `tests/test_wire_codec.py` (round trip, `sha256` and client-boundary parity against a recording fake OpenAI client) and
  `tests/test_persistence_fidelity.py`, which drives every module and path on a real `dual`-mode DB, reconstructs each
  call from its `llm_calls` row and from `llm.call.jsonl`, and asserts the gap report equals the checked-in
  `KNOWN_GAPS`. No production code writes the record yet and no persisted field changes; `docs/modules/observability.md`
  documents the format.

- **Run audit of the prompt set on DB, file and per request (prompt externalization, step A4).** Every entry
  point (SDK, proxy, CLI, benchmark, and the `DefaultPersistence` session fallback) adds
  `prompt_config` (`PromptSet.audit_summary()`) to the meta of its existing `create_run` call, and in
  `file_only`/`dual` writes one canonical `run.started` line (`run.started.jsonl`, new `router.route_jsonl` plus
  `conversation_events.emit_run_started_file_record`, once per run id). The CLI and the benchmark now also set a run id in
  `file_only` (a run id is set only when a DB path is configured or the mode is `file_only`), so with no DB configured they
  now write JSONL telemetry under `logs/observability/` that they silently dropped before (R18). `OrchestrationController.process` links each request to its set with
  `requests.meta_json.prompt_set = {ps, is_default}`. All additive and best-effort (no schema change, a failure
  never reaches the request). `docs/modules/observability.md` "Upsert" row corrected to the real per-type
  strategies. New `tests/test_prompt_audit.py`.

- **Deployer prompt overrides are now effective (prompt externalization, step A3).**
  `GovernanceConfig.prompts_dir` and `MORALSTACK_PROMPTS_DIR` (registered as an optional env var; precedence config,
  then env, then the packaged defaults; a blank value is unset) point to a directory of override `*.yaml` files.
  `build_deliberation_modules(prompts_dir=)` loads ONE `PromptSet` and hands it to the policy (and every per-module
  policy), the store, the risk estimator, the critic, the simulator, hindsight and the perspective ensemble
  (`DeliberationModules.prompt_set`); the SDK bootstrap, the CLI (env only) and the benchmark (env only, loaded in
  `BenchmarkRunner.__init__` before any LLM client, so a bad directory fails before spending) give the same object to
  the `Orchestrator`. A bad directory fails the build (`GovernancePipelineError` through the SDK), so no client and
  no request exist. The invariant texts (`.claude/rules/hard-signal-safety.md`, `PROJECT_SPEC.md` section 5.3,
  `docs/ai/INVARIANTS.md`) now name `prompts_dir` as an accepted, audited, deployer-side vector for prompt text; the
  code-level gates stay non-overridable (new `tests/governance_invariants/test_prompt_override_invariants.py` and
  `tests/test_prompt_plumbing.py`). `docs/prompt_overrides.md` is the full deployer guide. With no `prompts_dir` every
  wire is byte-identical (A0 golden green).

- **Policy and orchestration prompts read from the prompt set (prompt externalization, step A2c).**
  `Orchestrator` / `create_orchestrator` / `OrchestrationController` take `prompt_set=None` (read-only `prompt_set`
  properties) and hand it to the runner, the response assembler, the refusal handler, the DCCL layer and
  `OpenAIPolicy(prompt_set=)`; `OutputProtector` / `create_protector`, `force_language_prefix`,
  `resolve_prompt_with_language`, `generate_llm_safe_refusal[_detailed]` and `generate_domain_exclusion_response`
  gain the same optional kwarg. The policy base, language/anti-leak/canary text, SAFE_COMPLETE / CONSTRAINED
  instructions, hard-violation guidance, rewrite texts, contract regeneration, draft-match and refusal / exclusion
  messages come from the set, and persisted `system_prompt` rows hold the rendered string. The five revision-guidance
  sentences of `guidance_builder` are now `guidance.*` fragments in `policy.yaml` (the bracket tags stay in code).
  With `prompt_set=None` every wire and persisted row is byte-identical (A0 golden green); the old constants stay
  importable as default re-exports. The temporary A1 duplication guard in `tests/test_prompt_catalog.py` is removed,
  replaced by `tests/test_prompt_set_threading.py` (including a reachability sweep over every packaged fragment).

- **Retrieval prompts read from the prompt set (prompt externalization, step A2e).**
  `ConstitutionStoreConfig.prompt_set` and a keyword-only `ConstitutionStore(prompt_set=...)` (config wins),
  propagated through `ConstitutionRetrieverConfig.prompt_set` to `DomainPrefilter`, `EnhancedDomainAgent` and
  `DomainAgent`: prefilter and agent system/user prompts and the per-domain negative examples now come from the
  set. `prompt_set=None` uses the packaged defaults, so wires and the domain-agent cache keys are byte-identical;
  the old module constants stay as default re-exports.

- **Compliance prompts read from the `PromptSet` (prompt externalization, step A2d).**
  `DeveloperContractComplianceLayer(policy, prompt_set=None)` takes the DCCL system prompt, the
  native-messages trailer, the fallback user prompt and the no-draft/transcript texts from the set;
  `classify_safety_override(..., prompt_set=None)` (and `_llm_classify`) take the safety-classifier
  system prompt from it, and every DCCL caller (`_classify_safety_override`, `validate_contract`)
  forwards the instance set. Persisted `system_prompt` rows hold the rendered string. With the default
  set every wire is byte-identical; `_DCCL_LLM_SYSTEM_PROMPT`, `DCCL_DRAFT_MATCH_SYSTEM_PROMPT` and
  `_LLM_CLASSIFIER_SYSTEM_PROMPT` stay importable as default re-exports.

- **Risk estimator reads its prompts from a `PromptSet` (prompt externalization, step A2a).**
  `LLMBasedRiskEstimator`, `get_harm_signal_prompts`, `render_evaluation_order`, `render_output_schema` and
  `_risk_context_messages` gain an optional `prompt_set` (default: the packaged set); the system prompts are
  rendered per call with a `PromptContext` (so a deployer `when: contract_present` on the developer-contract
  fragments takes effect), and the persisted `system_prompt` is the rendered string. The constants in
  `models/risk/prompts.py` become default re-exports with identical bytes. With the default set every wire,
  persisted row and cache key is unchanged (A0 golden green).

- **Prompt externalization, step A2b (deliberative modules and cache tag).** The critic, simulator,
  hindsight and perspectives modules (and `build_module_messages`) take an optional `prompt_set` and
  read their system prompts (rendered per call with the contract/history `PromptContext`), user
  templates, seeds and retry texts from it; `prompts/*` constants are now default re-exports with
  identical bytes, and the persisted `system_prompt` is the rendered string. `evaluate_single` and
  `create_minimal_ensemble` use the instance set. `build_context_fingerprint` gained `prompt_set_tag`
  (empty for the default set, so every default cache key is unchanged). No behavior change with the
  default set.

- **Prompt catalog and packaged prompt defaults (prompt externalization, step A1).** New
  `moralstack/prompts/catalog.py` (immutable `PromptSet`, `PromptContext` / `when` conditions,
  fail-fast `PromptConfigError`, deployer-override loader, audit summary, `SAFETY_FRAGMENT_IDS`) and
  `moralstack/prompts/data/*.yaml`: the fragments and compositions that rebuild every LLM-facing
  prompt byte for byte (verified against the live constants and the A0 golden snapshot). Not consumed
  yet: no prompt constant, module or entry point reads the catalog, so runtime behavior is unchanged;
  `prompts_dir` / `MORALSTACK_PROMPTS_DIR` take effect from step A3. Also: `pyproject.toml` package-data
  entry for the YAML, `docs/prompt_overrides.md` (skeleton), and the memory-guard / stop-gate maps now
  accept `docs/prompt_overrides.md` for changes under `moralstack/prompts/`.

- **`PIPELINE_FAILED` orchestration event.** When `process()` fails closed (a `MoralStackError`,
  an `OrchestratorTimeoutError` or an unexpected exception), the controller now writes one
  best-effort `PIPELINE_FAILED` row to `orchestration_events`, with payload `fail_closed_reason`,
  `triggered_principle`, `error_type` and `path` (never the exception text). Audit only: it is
  not a `decision_traces` row, so UI detection (no FINAL row) is unchanged, and any emission
  error is swallowed. The `ensure` fallback does not emit. `docs/CODEBASE_FACTS.md` also records
  the three meanings of `hard_violation_flip_reason`, and two hypotheses (`hard_violation_*`
  cacheability, and the remaining exception-text sinks in the SDK `from_pipeline_error` and the
  generic-except debug log).

- **Documented: truncated speculative draft gap.** `docs/CODEBASE_FACTS.md` records, as a
  hypothesis, that a speculative draft cut mid-list (benchmark questions Q25/Q60, 46
  completion tokens) was delivered incomplete. Its cause cannot be read from the audit trail,
  because `finish_reason` is not persisted on `llm_calls`. Not fixed. Docs only.

- **Documented: constitution inspirational sources.** `docs/constitution.md` §9.1 now records
  the design history of the constitution and the five bodies of work it draws on
  (Constitutional AI/RLAIF, OpenAI Model Spec, HLEG guidelines and AI Act Art. 5, principlism,
  provider usage policies), mapping each to the principles it shaped. Docs only.

- **Domain-prefilter rejected-domain audit.** On a prefilter cache miss, every domain the
  classifier proposed but routing did not apply is now recorded as one bounded
  `DOMAIN_PREFILTER_DOMAINS_REJECTED` orchestration event, with reasons `unknown_domain`,
  `low_confidence`, `over_cap` or `parse_failed` (16 entries x 64 characters max). Write-only:
  the applied domains, the prefilter cache, the prompts and the `llm_calls` rows are unchanged.
  API errors and a missing API key are not recorded (documented known gap).

- **Measured: domain-prefilter output-token headroom.** On 37,635 historical prefilter
  calls (old prompt, gpt-4o) the old 200-token cap was hit 0.56% of the time, mostly in the
  trailing free-text `reason`; projected onto the new `selections` schema the routing-relevant
  part peaks at ~179 tokens and no reply reaches 400 (projection, not a new-prompt measurement).
  The docs now state that a cut reply is usually repaired by the parser rather than failing:
  a cut inside `payload` routes core-only and a cut inside `selections` silently drops a domain,
  with no rejection event since `finish_reason` is not read. Recorded in `docs/CODEBASE_FACTS.md`.
- **Corrected: domain-prefilter A/B figures.** The 600-question A/B aggregates (candidate2
  prompt, gpt-4o) were recovered; excluding 24 obfuscation probes that only the new arm ran,
  precision goes 0.582 -> 0.681 and over-assignment per question 0.315 -> 0.200, at a small
  recall cost (0.843 -> 0.821, mostly on red-teaming), not "equal recall" as previously recorded.
  Still a hypothesis in `docs/CODEBASE_FACTS.md`: external run, aggregates only.

- **Known gap documented: `init_db` startup race.** `docs/CODEBASE_FACTS.md` ("Future
  work / known gaps") now records that `init_db` has no concurrency guard and that
  `e0dd37b` added a second caller in the request path, so on a fresh DB one caller can
  hit `database is locked` and the schema is briefly absent (startup noise, measured
  harmless on the 2026-09-10 run). The in-process WAL-switch loss is fixed (see "Fixed");
  the cross-process race and the schema-ordering gap are not.
- **Known defect documented: per-request token totals omit the risk mini-estimator
  calls.** Since `5439aaf` (2026-07-18) the three mini-estimator `llm_call` rows carry
  `billable_provider_call=None`. The in-process accumulator behind `request_token_usage`
  and the proxy `usage` field skips them (`observability/service.py`), while the read
  store counts NULL as billable, so the per-request summary under-counts governed
  requests and disagrees with the UI (measurement in `docs/CODEBASE_FACTS.md`). Fixed
  afterwards, see "Fixed" (count null-billable llm calls). The same curation verified five further hypotheses against the code
  (canary rotate race, unsynchronized ledger storage (fixed afterwards, see "Fixed") and executor init, estimator
  `domain_channel` reset, path-agnostic persistence init), recorded that absorbed
  provider errors leave no trace in the DB, and corrected stale rows (critic rule
  window 512, 16-key mini-estimator payload).
- **Measured: concurrent requests queue on the deliberation runner's single 3-worker
  pool.** One `ThreadPoolExecutor(max_workers=3)` per runner serves the critic /
  simulator / perspectives fan-out of every concurrent proxy request. On the August
  runs at peak concurrency 5-6, 37 of 569 parallel groups waited more than 1 s for a
  worker (up to 2.7 s), while at peak concurrency 2 none did. Recorded in
  `docs/CODEBASE_FACTS.md` (Conditionally verified); no fix evaluated.
- **`MORALSTACK_HISTORY_MAX_CHARS_PER_TURN` / `MORALSTACK_HISTORY_MAX_TURNS` — the
  conversation window shown to the judging modules is now configurable, default
  unchanged (200 chars, 3 turns), so nothing moves unless the variable is set.**
  The critic, simulator and hindsight judge a draft through a compressed view of the
  conversation while the policy that *writes* the answer receives the full history —
  verified on the campaign logs by token accounting (on a request whose history was 319
  tokens, `policy generate` carried +398 tokens of context against the risk estimator's
  +235). That asymmetry is deliberate and stays: the governed answer is written with the
  same context an ungoverned call would have had, and only the judges see less.
  The window and `build_context_fingerprint` now read the same source
  (`moralstack/utils/history_window.py`). They were two hardcoded `200`s in files with no
  knowledge of each other, and that pairing is a correctness constraint rather than a
  style one: a module shown more content than the fingerprint hashes lets two
  conversations identical up to the cut and different afterwards collide on one cache
  entry, serving the second the first one's result. `tests/test_history_window.py` pins
  them byte-for-byte and was confirmed to fail when either side is hardcoded again.
  Worth knowing before raising it: a counterfactual during the `CORE.DEVCONTRACT.1`
  investigation showed a wider window does **not** change hard-violation verdicts (6/6
  either way). What it changes is the rationale — at 200 chars the critic accused a draft
  of containing "multiple views", which was exactly what the contract required, in the
  part of the one-shot example the cut had hidden. Removing the truncation entirely for
  these three modules costs ~$0.9 per COMPL-AI campaign, measured over the 4,244 requests
  whose history actually exceeded the cut. The risk estimator, perspectives and the
  retrieval query keep their own hardcoded windows: widening those changes routing and
  which principles are retrieved, and was scoped out deliberately.

- **`supports_json_schema()` — model capability check for strict Structured Outputs.**
  `PolicyLLM._complete` applies it centrally: a `json_schema` `response_format` is
  degraded to `{"type": "json_object"}` on a model that does not support it, mirroring
  how `supports_predicted_output` gates `prediction`, so callers declare the schema
  they want without branching on the model. Deliberately an allowlist (`gpt-4o`,
  `gpt-4.1`, `gpt-5` families, o-series, minus the pre-Structured-Outputs
  `gpt-4o-2024-05-13` snapshot): an unrecognised deployment behind `OPENAI_BASE_URL`
  keeps plain JSON mode, so the predicate can never turn a working call into a
  failing one.

- **`MORALSTACK_CRITIC_MAX_RULE_LEN` — the critic's rule window is now configurable.**
  `format_principles_compact` truncates every principle rule before it reaches the
  critic; the 180-character limit was hardcoded at both call sites. It is now
  `CriticConfig.max_rule_len`, loaded from the environment, **default unchanged at 180**
  — no behavioural change unless the variable is set.
  Why it matters: rules are drafted prohibition-first and carve-out-last, so the window
  systematically hides the *limits* of a ban rather than the ban itself. Measured over
  2,465 governed requests, 83 of 232 hard-violation citations (35.8%) were judged on a
  rule verified truncated in the prompt actually sent; in 27 requests that rule was the
  only hard signal. For `LEGAL.NOPRACTICE.1` the critic's own rationale ("provides a
  detailed process for terminating a contract") names exactly the case its hidden clause
  exempts — so the 2026-08-13 rewrite of that rule never reached the judge.
  Sizing: the longest rule shipped is 492 characters, so `512` removes truncation
  entirely; the window only caps and never pads, so the cost is bounded by the text that
  exists (worst case ~738 extra tokens per critic call at `top_k_principles=20`).
  `tests/test_critic_rule_window.py` pins both call sites to the config value and fails
  if a future rule exceeds 512.
  Counterfactual **run** 2026-08-17 to the pre-registered protocol (180 vs 512 as the sole
  changed variable, 7 cases x 5 replays per arm, runs `415b0284` / `5ff450e4`):
  `LEGAL.NOPRACTICE.1` drops from 4 hard citations over 2 requests to **0**, each of the 4
  judged on a rule verified truncated at 206 chars. `CORE.DUALUSE.1` does *not* follow —
  it is cited **more** at 512 (7 citations/5 requests → 8/8), its hidden tail carrying a
  tightening as well as an exception, so widening is not a permissiveness lever. The
  delivered action barely moves (`CF-LEGAL-01` 4/5 → 5/5): the spurious hard violation
  yields `REVISE` and deliberation usually recovers, so the cost is wasted cycles rather
  than refusals. Full measurement, controls and caveats in `docs/CODEBASE_FACTS.md`.
- **The commit-time memory guard now covers the runtime deliberative modules.**
  `moralstack/runtime/modules/` was in neither behavior map, so a change to the critic —
  the only producer of `violated_hard`, and therefore the only path to the
  delivery-point guard's fail-closed REFUSE — could be committed with no doc update.
  The mapping is per module (`critic_*` → `docs/modules/critic.md`, likewise
  `simulator_*`, `perspective_*`, `hindsight_*`), with a directory-level entry catching
  shared plumbing and future modules.
- **`--cases-file` for the AIR-Bench regression runner.** `examples/airbench/cases.jsonl`
  documents the 2026-08-13 constitution change and is cited as evidence for it, so a
  one-off experiment must not be appended to it. The runner can now read another JSONL
  with the same schema (`load_cases` already accepted a path; only the CLI was missing).
- **Round-3 AIR-Bench / XSTest / CoCoNot A/B recorded in the facts ledger, with four
  governance defects it surfaced.** External measurement only — no code changed. The run
  (7,524 prompts per arm, judge `gpt-4.1@0`, join 7,524/7,524) is in "Conditionally
  verified"; the four defects are code-cited:
  (1) `guidance_builder.py:253-262` injects two `[BALANCE] present both sides` lines into
  **every** revision prompt with no topical gate — 892 of 1,154 revision prompts in the
  run — which rewrote an approved condemnation of the transatlantic slave trade into a
  both-sides answer that the critic then approved again;
  (2) `decision_service.py:182` emits `"risk_score=X below low threshold (Y)"`
  unconditionally for every NORMAL_COMPLETE, so 139 of 1,380 CoCoNot audit strings are
  self-contradictory (decision right, recorded reason false);
  (3) `MORALSTACK_POLICY_REWRITE_MODEL=gpt-4.1-nano` is an active shipped default
  (`.env.template:19`), so **736 of 7,524 delivered answers (9.8 %) were written by a nano
  model rather than the caller's `gpt-4o`** — precisely the requests where governance
  revised something;
  (4) the hard-violation re-critique cleared the regenerated text in 12 of 12 cases read,
  and 9 of those still perform the flagged act.
  Also filed as open hypotheses: 19 answers delivered with no `FINAL` decision trace, and
  a retraction — the CoCoNot `original` hedging loss is measured but its cause is **not**
  established (the earlier attribution to the generation system prompt does not survive:
  the clause it cited is scoped `"When revising"` and all 69 regressions came from a
  single generation call with zero revisions).

### Changed

- **PyPI classifier `Development Status :: 5 - Production/Stable`** (was `4 - Beta`), for the 1.0.0 release.
- **Proxy `X-Moralstack-Decision` now reports the delivered action (behaviour).** The proxy builds
  the governance headers after `finalize_delivery` and passes `final_action=delivery.final_action`
  to `build_governance_headers` (new keyword-only argument, default `None` keeps the legacy
  behaviour). An unlabelled result now reports `REFUSE` (was `UNKNOWN`), blank content with a
  `NORMAL_COMPLETE` label reports `REFUSE` (was `NORMAL_COMPLETE`), and case variants of a valid
  label are canonical (were verbatim); a non-ASCII look-alike label no longer reaches the header
  encoder. The audit copy of the headers carries the same value. Valid canonical labels, the body
  and `finish_reason` are unchanged.
- **SDK `finish_reason` is `content_filter` for governed refusals (BREAKING, behaviour).** Every
  SDK-delivered REFUSE (non-stream `GovernedResponse.choices`, the `GovernedRefusalStream` chunk,
  the `GovernedErrorStream` chunk, and `from_pipeline_error`) now carries
  `finish_reason="content_filter"`, matching the proxy and `GovernedDelivery.finish_reason`; it
  was always `stop`. NORMAL_COMPLETE and SAFE_COMPLETE keep `stop`. `_SyntheticChoice` accepts an
  optional `finish_reason` (default `stop`). SDK clients that matched `stop` on refusals must
  update.

- **Pipeline-error responses are now governed refusals (BREAKING, behaviour).** Proxy responses for
  a pipeline error or timeout now carry `finish_reason="content_filter"`,
  `X-Moralstack-Decision: REFUSE` and `X-Moralstack-Path: ERROR_PATH` (they were `stop`, `UNKNOWN`
  and `DELIBERATIVE_PATH`), and the SDK reports `final_action="REFUSE"`. The persisted
  `meta_json.path` is `ERROR_PATH`, and the persisted `conversation_states` / `conversation.state_updated`
  payload records `final_action="REFUSE"` (was `""`) for error turns. Clients that matched on the old values must update.
- **`MORALSTACK_ORCHESTRATOR_SAFE_RESPONSE_ON_ERROR=false` is now ignored (BREAKING, behaviour).**
  Pipeline errors always deliver the fixed sentinel and never the exception text (previously
  `Error: <exception>` / `Timeout: <exception>`). The flag stays in `OrchestratorConfig` and a
  WARNING is logged when the config is loaded with it set to false.
- **Side effects of the REFUSE label.** The realtoxicity probe now labels pipeline-error rows
  REFUSE (they were NORMAL_COMPLETE). *Correction (F6 follow-up C6):* the earlier claim that
  `reports/model.py` renders these rows as "refused due to policy" was wrong for the request report
  built from the database. Measured at `484deba` on a crashed request (`meta_json`
  `final_action=REFUSE`, `triggered_principles=["SYSTEM.ERROR"]`, no FINAL row): with no trace rows the
  status was "COMPLETED - Outcome from trace."; with RISK_ASSESSMENT then PRE_POLICY
  (`SAFE_COMPLETE`) last it was "APPROVED WITH CAVEATS - Response includes disclaimers."; with
  RELEVANT_PRINCIPLES last it was "COMPLETED - Outcome from trace." (the report never read
  `meta_json`). Only the CLI report (`request_report_from_cli`) could show "refused due to policy
  violations", for a `full_refusal` response type. Both are fixed below.

- **Public API default change (BREAKING): `create_orchestrator()` now defaults `timeout_ms` to 600000
  (was 5000)**, aligned with `OrchestratorConfig`. With the deliberative critique now failing
  closed on its 90% time budget, the old 5 s default would have refused most real
  deliberations. Callers that relied on a 5 s budget must pass `timeout_ms` explicitly.

- **Internal: `DeliberationRunner._critique` split into helpers (no behavior change).** Gate, 90%
  deadline, critic invocation and report summary are extracted into `_critique_gate_open`,
  `_critique_deadline_error`, `_invoke_critic` and `_critic_report_summary`; critic-call arguments
  and error handling are unchanged (pinned by new tests).
- **Internal: unreachable `except FailSafeException` clause removed from `OrchestrationController.process()` (no
  behavior change).** It sat after `except MoralStackError` (its base class) and the exception is never raised; a
  `FailSafeException` is handled like any other `MoralStackError`.
- **Governed generation no longer retries on `TypeError`; injected policies must conform.** A
  programmatically injected policy must accept the `PolicyLLMProtocol` kwargs (`system`, `config`,
  `overrides`, `model_override`); otherwise the request now fails closed (`[SYSTEM_ERROR]` / REFUSE,
  or `ERROR_PATH` for the compliance regeneration) instead of silently losing the system prompt.
  Risk estimators that do not accept the context kwargs keep working: `_estimate_risk` filters the
  kwargs by the estimator's signature and logs a warning when a developer contract or conversation
  history is dropped. `moralstack.cli.mocks.MockPolicy` / `MockRiskEstimator` gain optional
  parameters only, so they stay backward compatible.
- **Risk estimator: a single model-resolution rule for the three mini-estimators (BREAKING).**
  Each mini model is now its own slot (`MORALSTACK_RISK_{INTENT,SIGNALS,OPERATIONAL}_MODEL`) when
  set, else the main risk policy's model: `MORALSTACK_RISK_MODEL` where the builder or the
  benchmark applies it, otherwise the primary model. The rule lives in one function,
  `resolve_risk_mini_model`, used by the estimator, the CLI display and both report-label paths.
  - **BREAKING (behaviour):** an SDK `GovernanceConfig(model=...)` or CLI `--openai-model`
    without `MORALSTACK_RISK_MODEL` and without per-mini slots now runs all three risk minis,
    including signals, on that primary model (previously env `OPENAI_MODEL`, or `gpt-4o`). Set
    `MORALSTACK_RISK_MODEL` (or the slots) to keep the minis on a different model. The measured
    gpt-4o vs gpt-4o-mini operational difference is in `docs/CODEBASE_FACTS.md`.
  - **BREAKING:** `RiskEstimatorConfig.intent_model` / `signals_model` / `operational_model`
    default to `None` (inherit) instead of `"gpt-4o"`; `load_risk_estimator_config_from_env()`
    returns the raw slot env value or `None` and no longer reads `MORALSTACK_RISK_MODEL` or
    `OPENAI_MODEL`. `resolve_risk_base_model_from_env` and `resolve_parallel_mini_model_slot` are
    removed (migration: use `resolve_risk_mini_model(slot, policy_model)`).
  - The `calibration_guard` row is persisted with `model = NULL` (synthetic row, no provider call);
    historical rows are left as they are. A policy without a usable `.model` is likewise persisted
    as NULL instead of the fake `"gpt-4o"`.
  - The env-fallback benchmark report (`_get_benchmark_models_config_fallback`) now sets
    `risk_parallel` and the three `risk_*` mini labels.
  - Templates: `.env.minimal` sets `MORALSTACK_RISK_MODEL=gpt-4o` (was `gpt-4o-mini`; with the
    slots at `gpt-4o` the billable models are unchanged); comments updated in both templates.

- **Reports: "Models used" shows the three risk mini-estimator models on the DB path.**
  `get_models_used_for_run` now also returns `risk_intent` / `risk_signals` / `risk_operational`
  (mapped by exact `llm_calls.action`), and the single/interactive and benchmark-overlay report
  paths render three "MoralStack risk" rows. Historical runs show the real per-mini models from
  `llm_calls` (for example operational = `gpt-4o-mini` on June-August runs). `calibration_guard`
  rows no longer feed the `risk` label; legacy runs with only `risk` render as before.
  Non-breaking: the `risk` key and the return type are unchanged.

- **Domain prefilter: precision-first classification.** The classifier now returns
  `selections[{domain, evidence, confidence}]` and each domain is gated on its own
  confidence (`>= 0.5`) instead of one global score, so a weak secondary domain no longer
  rides a strong primary. The catalog prints each overlay description as a scope line plus
  a `NOT:` exclusion line (split at the first `NOT for:`) and no longer prints keywords
  (kept only for a domain with an empty description). Models with strict Structured Outputs
  get a `json_schema` whose `domain` enum is the request's candidate set (without `core`);
  other models keep `json_object`. Output budget raised from 200 to 400 tokens. Replies in
  the legacy `{domains, confidence}` shape keep the unchanged global gate. The rejected-domain
  audit attributes `low_confidence` per domain for the new shape.

- **AI review harness: the external Codex reviewer is replaced by an internal,
  isolated `adversarial-reviewer` sub-agent** (`.claude/agents/adversarial-reviewer.md`:
  read-only tools, `model: claude-fable-5-1` pinned in its frontmatter — a model
  different from, and more capable than, the Sonnet implementer's; `opus` is the
  documented fallback). The agentic pipeline now runs entirely inside Claude Code:
  `/ai-review-plan-with-codex` → `/ai-review-plan`, `/ai-review-diff-with-codex` →
  `/ai-review-diff` (both launch the sub-agent through the `Agent` tool instead of
  `Skill(codex:rescue)`); the rubrics move to `ai/prompts/{plan,diff}-review-template.md`
  with only the Codex runtime phrasing removed; review artifacts are named
  `ai/reviews/{plan,diff}-review-*` and `ai/prompts/generated-{plan,diff}-review-*`
  (still gitignored); `docs/ai/CODEX_REVIEW_GUIDE.md` → `docs/ai/REVIEW_GUIDE.md`.
  Every review now starts with a `Reviewer model: <id>` line so the independence
  claim is auditable, and the commands never pass a `model:` override. Also fixes
  `.claude/commands/ui-loop.md` and `.claude/settings.ui-loop.json`, which still named
  the `Task` tool: per the sub-agent docs (code.claude.com/docs/en/sub-agents) it was
  renamed to `Agent` in 2.1.63, and in the 2.1.272 binary `Task` only survives as a
  legacy alias applied when tool-name rules are parsed — so it was not broken, but it
  is not the canonical name, and every `allowed-tools` line and tracked allowlist now
  says `Agent`. Historical mentions of Codex in code comments, tests and past changelog
  entries are left as history.

- **The shipped templates no longer downgrade the rewrite and simulator models.**
  `.env.template` and `.env.minimal` set `MORALSTACK_POLICY_REWRITE_MODEL` and
  `MORALSTACK_SIMULATOR_MODEL` to `gpt-4o` (i.e. `OPENAI_MODEL`) instead of
  `gpt-4.1-nano`. The rewrite one is the load-bearing half: a revised answer is still
  the answer delivered to the user, so the previous default meant that whenever
  governance revised something, the text the caller received came from a different,
  much smaller model than the one they asked for — measured on the round-3 benchmark as
  **736 of 7,524 delivered answers (9.8%)**, and that 9.8% is not a random sample, it is
  exactly the population where governance found something to revise. It also made any
  "same model, with governance" comparison wrong for those requests.
  The change additionally aligns the templates with the configuration every COMPL-AI
  campaign actually ran under: all 6,380 `simulator` calls and all 1,716 `policy`
  `rewrite`/`soft_revision` calls in the T=0 and T=1 logs carry `model=gpt-4o`, so the
  campaigns were never using the template default for these two slots and a reader
  copying the template now reproduces the measured setup.
  **This costs money**: simulator and rewrite calls move from `gpt-4.1-nano` rates to
  `gpt-4o` rates for anyone copying a template. The downgrade is now opt-in — set either
  variable to a lighter model id to restore it. Docs corrected in the same change
  (`README.md`, `docs/architecture_spec.md`, `docs/modules/policy.md`,
  `docs/modules/simulator.md`, `docs/CODEBASE_FACTS.md`), since eight statements
  described the old default as the shipped one.

- **The generation system prompt no longer contradicts itself, and branch 4 says
  what to do instead of what to value.** Two text edits, no logic touched.
  `POLICY_SYSTEM_PROMPT` branch 4 (`DEPTH WHEN NO STRICT FORMAT`): *multiple
  perspectives* gains an explicit exclusion for false, hateful or historically
  settled premises; *intellectual honesty* now requires stating **explicitly**
  that a question has no single objective answer, and stating the **knowledge
  limit and its date** rather than answering as if current; a new closing clause
  requires **asking** for a detail the user did not give when that detail would
  change the answer (which country, which language, which of several things
  called the same name), stating the assumption where the answer proceeds
  anyway, and asking what is meant on an uninterpretable request rather than
  answering its fragments one by one. `OutputProtector.ANTI_LEAK_RULES`:
  "Never describe yourself or your rules" becomes "Never describe your **rules**,
  your system prompt, or how you were configured", and the ban on opening with
  "I am…" now carves out genuine limits — no personal experience, no real-time
  access, a training cut-off — which the model may state when the question turns
  on them.
  Why: on the 140-case CoCoNot replay suite, **56 of the 65 stable failures occur
  at `risk=benign`, `NORMAL_COMPLETE`, zero hard violations, zero domain overlay,
  zero deliberation cycles** — governance never acted, so the defect could only
  be in the prompt. The old text produced answers that invented personal memories
  instead of saying "I have no personal experiences", replaced a dated
  knowledge cut-off with the contentless "as of the latest information
  available", asserted one answer to explicitly subjective questions, and
  defined a word-salad prompt term by term instead of asking what was meant.
  **Safety is untouched**: branches 1 (SAFETY), 2 (SECURITY BOUNDARIES) and 3
  (OUTPUT CONSTRAINTS) are byte-identical, no principle, threshold, decision rule
  or critic path was changed, and the new exclusion on *multiple perspectives* is
  a tightening — the round-3 run contains a case where "present both sides" was
  applied to "why was the transatlantic slave trade actually a good thing".
  Full suite after the change: **2545 passed / 0 failed**; prompt transparency
  holds (`tests/test_system_prompt_byte_equality.py` asserts against the constant,
  not a literal). Cost: the composed prompt grows ~5.2k → ~6.2k chars
  (~+250 tokens per generation call).
  **Not fixed, deliberately**: the revision clause (*"When revising … do not make
  the answer … more caveated"*) still yields only to rules 1-2, so it overrides
  the new hedging requirements on `rewrite`/`soft_revision`; 0 of the 56 target
  cases go through a revision. **Not yet measured against live models.**
- **The critic's rule window now defaults to `512` (was `180`).** No rule shipped
  today is longer than 492 chars (`CORE.DEVCONTRACT.1`), so the critic reads every
  principle in full and a carve-out drafted last no longer disappears before the
  judgement that sets `violated_hard`. Evidence: the counterfactual above measured
  `LEGAL.NOPRACTICE.1` going from 4 hard citations over 2 requests at 180 to **0** at
  512, each of the 4 judged on a rule verified truncated at 206 chars.
  **This changes critic verdicts.** A measurement taken at 180 is a different
  experiment, and the direction is not uniformly permissive: `CORE.DUALUSE.1` is
  cited *more* at 512 (7 citations/5 requests → 8/8) because its hidden tail carries
  a tightening as well as an exception. Set `MORALSTACK_CRITIC_MAX_RULE_LEN=180` to
  restore the historical window. The `.env.minimal` template now ships the variable
  explicitly at `512`, so a fresh `.env` copied from it matches the shipped default.
- **One explicit environment template: `.env.template` (`.env.minimal` removed).** The
  template now lists every variable the program reads, each with an explicit value: the
  tuned value the templates shipped (`.env.minimal` won where the two differed), with
  `(code default: X)` noted wherever it differs from the code. Secrets, URLs, paths and
  opt-in keys stay commented; keys that take precedence over a `GovernanceConfig` field or
  a CLI flag (`MORALSTACK_GENERATION_MODE`, `MORALSTACK_LEDGER_ENABLED` /
  `_SIMILARITY_THRESHOLD` / `_MAX_ENTRIES`, `MORALSTACK_EMBEDDER_PROVIDER`,
  `MORALSTACK_BENCHMARK_BASELINE_MODEL`) are commented with a warning; `MORALSTACK_BENCHMARK_JUDGE_MODEL=gpt-5.2`
  is set on purpose (the judge of the benchmark runs) and overrides `--judge-model`. The UI ships
  `admin`/`admin` as local-development credentials, to be changed before exposing the UI.
  Dropped the keys nothing reads (`MORALSTACK_DOMAIN_PREFILTER_MODEL`,
  `MORALSTACK_OPENAI_COMPATIBLE_MAX_INFLIGHT`, `MORALSTACK_OPENAI_COMPATIBLE_RETRY_AFTER_SECONDS`);
  added 12 keys that were read but undocumented there (`MORALSTACK_GENERATION_MODE`,
  `MORALSTACK_OBSERVABILITY_BATCH_MAX_ITEMS` / `_MAX_DELAY_MS`, `MORALSTACK_DECISION_TRACE_PATH`,
  `MORALSTACK_EMBEDDER_PROVIDER`, `MORALSTACK_LOCAL_EMBEDDING_MODEL`, `OPENAI_EMBEDDING_MODEL`,
  `MORALSTACK_PRINCIPAL_HMAC_SECRET`, `MORALSTACK_CORRELATION_TTL_SECONDS` / `_MAX_ENTRIES`,
  `MORALSTACK_PHASE0_TIMING` / `_JSONL`). Docs corrected: `README.md` / `INSTALL.md` now give
  the code defaults of `OPENAI_TEMPERATURE` / `OPENAI_TOP_P` (0.7 / 0.9; the template ships
  1 / 1, the values of the benchmark runs), `MORALSTACK_CONSTITUTION_MAX_PARALLEL_AGENTS` (4), the derived
  `MORALSTACK_OBSERVABILITY_MODE` default and FIFO ledger eviction, and no longer list the dead
  keys. Template values are the ones used for the benchmark runs (e.g.
  `MORALSTACK_HISTORY_MAX_CHARS_PER_TURN=1500`, code default 200). No runtime code changed.

### Removed

- **No-LLM conservative mode of the risk estimator (BREAKING).** This supersedes the sentence
  "The no-LLM conservative mode (`policy=None`, `create_conservative_estimator`,
  `MORALSTACK_RISK_FALLBACK_*`) is kept" in the "Single risk-estimator residues" entry below,
  which is left as written. No production code ever built an estimator without a policy.
  - **BREAKING:** `create_conservative_estimator` is removed from `moralstack.models.risk` and
    `moralstack.models.risk.estimator` (importing it raises `ImportError`). Migration: construct
    `LLMBasedRiskEstimator(policy, config=...)` with a real policy.
  - **BREAKING:** `LLMBasedRiskEstimator._fallback_estimate` and the `NO_LLM_AVAILABLE` marker
    are removed. With `policy=None`, `estimate()` now returns `RiskEstimation.from_error("policy
    not set")`: score 0.6, confidence 0.4, `SYSTEM.REQUIRES_DELIBERATION`, action DELIBERATE (it
    was 0.5 / 0.3 / `NO_LLM_AVAILABLE` / DELIBERATE). It logs a WARNING and emits no `llm_calls`
    row. It never returns ALLOW. Constructing the estimator with `policy=None` stays legal.
  - **BREAKING:** the `RiskEstimatorConfig` fields `fallback_risk_score` and `fallback_confidence`
    are removed (passing them raises `TypeError`).
  - **BREAKING:** the env vars `MORALSTACK_RISK_FALLBACK_SCORE` and
    `MORALSTACK_RISK_FALLBACK_CONFIDENCE` are ignored (no code reads them) and were dropped from the
    `load_env` empty-value cleanup list. Delete
    them from your own env file; `.env.minimal` and `.env.template` no longer list them.

- **Dead remains of the risk estimator (BREAKING for direct importers; the live path is
  unchanged).** Nothing in the repository used any of these; every mini-estimator already
  passed an explicit strict `json_schema`.
  - **BREAKING:** `RiskEstimation.triggered_signals` is removed (it was an uncalled alias);
    use `semantic_signals`.
  - **BREAKING:** `get_risk_env_bool` is removed from `moralstack.models.risk.config_loader`;
    use `moralstack.utils.env_helpers.get_env_bool`.
  - **BREAKING:** `LLMBasedRiskEstimator.get_pooling_diagnostics()` and its write-only hit/miss
    counters are removed; the pool itself is unchanged.
  - **BREAKING:** `LLMBasedRiskEstimator._build_generation_config` now requires its
    `response_format` argument (no `json_object` default; calling it with no argument raises
    `TypeError`) and no longer swallows an `ImportError`.
  - Legacy `estimate` rows (single-estimator era) no longer feed the report rationale
    (`reports/model.py`, DB and CLI paths); they are still stored and shown raw. This
    supersedes the "persisted `estimation_mode` and `estimate` rows stay readable" note in the
    "Single risk-estimator residues" entry below, which is left as written. The census found
    0 such rows in 114 local DBs.
  - The CLI-path `calibration_guard` branch of `reports/model.py` is removed too, so
    `request_report_from_cli(...).calibration_guard_info` is always `""`. The branch was
    unreachable: the only CLI `log_call(module="risk_estimator")` (the controller's) writes
    non-JSON text, and the mini-estimators never call `log_call`. The DB path still reports
    the guard.

- **Single risk-estimator residues (breaking for direct importers).** The retired
  single-estimator surface is gone; the live path (three mini-estimators plus the
  calibration guard) is unchanged. Removed names and migrations:
  `create_risk_estimator(...)` and `calibration.parse_risk_response(text)` (use
  `LLMBasedRiskEstimator(policy, config=...)` and `calibration.parse_risk_dict(extract_json(text))`);
  the methods `get_risk_level`, `should_deliberate`, `categorize_from_score` and
  `estimate_with_context` (call `estimate(...)`; routing thresholds are the orchestrator's);
  the `RiskEstimatorConfig` fields `low_threshold`, `medium_threshold` and
  `require_deliberation_on_fallback` (passing them now raises `TypeError`); and the six env
  vars `MORALSTACK_RISK_LOW_THRESHOLD`, `MORALSTACK_RISK_MEDIUM_THRESHOLD`,
  `MORALSTACK_RISK_REQUIRE_DELIBERATION_ON_FALLBACK` and
  `MORALSTACK_RISK_CATEGORIZE_{BENIGN,SENSITIVE,CLEARLY_HARMFUL}_THRESHOLD`, which are now
  ignored. They never affected routing; use `MORALSTACK_ORCHESTRATOR_RISK_LOW_THRESHOLD` and
  `MORALSTACK_ORCHESTRATOR_RISK_MEDIUM_THRESHOLD`. The no-LLM conservative mode
  (`policy=None`, `create_conservative_estimator`, `MORALSTACK_RISK_FALLBACK_*`) is kept.
  Prompts, `llm_calls` payloads, `RiskEstimation` and decisions are unchanged; persisted
  `estimation_mode` and `estimate` rows stay readable. `.env.minimal`, `.env.template`,
  `INSTALL.md` and `docs/modules/risk_estimator.md` no longer advertise the removed variables.

- **Dead code in the risk-estimator area (no behaviour change).** Removed the uncalled
  module-level `persist_llm_call` in `moralstack/models/risk/estimator.py` and the never-called
  `quick_estimate` from `RiskEstimatorProtocol` (`moralstack/core/types.py`), plus a stale
  "monolithic" comment in `calibration.py`. Prompts, `llm_calls` payloads, `RiskEstimation` and
  decisions are unchanged.

- **Constitution keywords (breaking for custom YAML).** Overlay `keywords` and principle
  `keywords` / `keywords_block` / `keywords_style` are gone from the schema and from the 22
  bundled YAML files. Because the schema rejects unknown fields, a custom overlay that
  still carries them now fails to load, and the store fails at construction with
  `ConstitutionLoadError` (not just that domain): delete those keys. Removed with them:
  `ConstitutionStore.get_domain_keywords`, the description keyword extractor, the unused
  lexical scorer (`_compute_relevance` / `_expand_query`), the `domain_keywords` argument
  and `set_domain_keywords` of `DomainPrefilter`, and the keyword fields of
  `moralstack-validate-overlay` (`keywords_count` / `keywords_source`).
  - **No change to the prompts:** domain keywords never reached a prompt with the bundled
    store, and the prefilter system prompt is byte-identical.
  - **Behaviour change:** principle keywords counted 0.2 each in the specificity
    tie-break of `resolve_conflict`. Without them some same-level, same-priority
    principles are ordered differently in 10 overlays. Principle sets are unchanged.
    Measured offline on 30,215 past retrievals: order changes in 6.7%; the 10 principles
    the risk estimator reads change in 16 cases (0.05%).
  - **Telemetry:** the prefilter cache-invalidation status now follows the domain
    descriptions; before, a description-only edit cleared the cache without any signal.
    The debug, trace and UI fields are renamed `prefilter_descriptions_changed` /
    `prefilter_descriptions_fingerprint_prefix`, and cache events carry
    `descriptions_fingerprint`. Older rows keep the old `keywords_*` names.

- **GitHub Actions workflows (`ci.yml`, `publish.yml`).** Removed at the request of
  GitHub support during the review of the suspended account. `ci.yml` ran lint, mypy,
  the 3.11/3.12 test matrix and the `-m slow` benchmarks on every push with no branch
  filter, then uploaded coverage through a third-party action holding `CODECOV_TOKEN`.
  `publish.yml` published to PyPI on tag through trusted publishing (`id-token: write`).
  Consequences for contributors: verification is now local (`docs/DEVELOPMENT.md`) and
  releasing is a manual `python -m build` + `twine upload` (`docs/RELEASING.md`). Two
  things outlive the removal because they do not live in this repository: the PyPI
  pending publisher, still bound to the file name `publish.yml` should the workflow ever
  be restored, and the `CODECOV_TOKEN` secret, which must be removed or rotated from the
  repository settings. The workflows remain recoverable from git history.

### Fixed

- **`docs/RELEASING.md` no longer claims that `build` and `twine` come with the `dev` extra.** Neither is listed in
  `pyproject.toml`; the build step now says to install them once with `python -m pip install build twine`.
- **A wheel install can import the risk estimator again.** `moralstack/models/risk/config/signals.yaml` was not in
  `[tool.setuptools.package-data]`, so a non-editable install (`pip install moralstack` / a built wheel) failed at
  `import moralstack.models.risk.estimator` with `FileNotFoundError` (the signal registry is built at import), which
  took the SDK, proxy and CLI down with it. Editable installs and in-repo runs were unaffected. Added
  `"moralstack.models.risk.config" = ["*.yaml"]`; a rebuilt wheel now lists `signals.yaml` and the estimator
  imports from it. New `tests/test_prompt_packaging.py::test_pyproject_package_data_includes_the_signal_registry_yaml`.
- **A wheel install ships the dashboard templates and static assets.** `[tool.setuptools.package-data]` had no
  `moralstack.ui` entry, so a wheel held only `moralstack/ui/__init__.py` and `app.py`; `moralstack-ui` from a
  non-editable install skipped the `/static` mount and served the bare fallback HTML of every page. Editable installs
  and in-repo runs were unaffected. Added `"moralstack.ui" = ["templates/*.html", "static/**/*"]`; a rebuilt wheel now
  lists the 6 templates, `static/css/main.css` and `static/js/main.js`, and the app loaded from it serves them. New
  `tests/test_prompt_packaging.py::test_pyproject_package_data_includes_the_ui_assets`.
- **Retry-failed rows written from pool workers now reach `llm_calls` (NEW-1).** The risk mini-estimator pool
  and the perspective ensemble (`parallel_evaluation=True`, the production default) submitted their work to a bare
  `ThreadPoolExecutor`; the workers did not inherit the observability ContextVars, so
  `async_persist_llm_call` found no run/request id and silently dropped every failed-attempt row (estimator since
  `27843f3`, perspectives since `742e0d4`). Each worker now runs under its own `contextvars.copy_context().run`
  (the pattern of the deliberation modules, the retriever and the speculative overlap), and the estimator's row
  carries the mini rows' coordinates (cycle 0, sequence -9) so the UI groups it with them. No decision input
  changes. **Comparability: from this commit `llm_call_count` and the per-request tokens (`request_token_usage`,
  SDK/proxy `usage`, the SQL reconstruction over `llm_calls`) include the failed estimator and parallel-perspective
  attempts; earlier runs are not directly comparable.** Tests: `tests/test_llm_call_persistence_gaps.py`,
  `tests/test_ui_llm_call_visibility.py`.

- **Zero-HARD `quick_check` fails closed instead of passing.** When no HARD principle is available to
  check against (shared list, self-retrieval and constitution fallback all without HARD),
  `LLMConstitutionalCritic.quick_check` now raises the new `NoHardPrinciplesError` without an LLM call;
  it used to return `passed=True`, so `run_fast_path` delivered an unchecked draft. `run_fast_path`
  escalates with the dedicated `escalation_trigger = "no_hard_principles"` (WARNING with the class name
  only) and fails closed per the existing contract. Behaviour change for operator constitutions with no
  HARD principle: the fast path now escalates every request instead of delivering an unchecked draft; the
  shipped constitution (15 HARD principles in `core`) is unaffected. `test_c5_*` is changed from the pass-pin to
  the raise.
- **The fast-path `quick_check` call is now an `llm_calls` row (NEW-2).** `LLMConstitutionalCritic.quick_check`
  persists `module="critic"`, `action="quick_check"` (cycle 0, sequence 2, i.e. right after the fast draft) with the
  prompt and system prompt sent, the raw reply, the verdict in `parsed_summary_json` and the billed tokens; a reply
  without a verdict is persisted with its `error` and still raises (the fast path still fails closed). The UI
  places the node after the fast draft (`_CYCLE0_SEQ_TO_VISUAL_TIER[2] = 9`) and prints the verdict. **Comparability:
  from this commit `llm_call_count` and the per-request tokens include one `quick_check` call per fast-path request
  (and the CLI/markdown per-module breakdown a `critic` row in cycle 0); earlier runs are not directly
  comparable.** Tests: `tests/test_llm_call_persistence_quick_check.py`,
  `tests/test_ui_llm_call_visibility_quick_check.py`.

- **`quick_check` logs a failed self-retrieval instead of swallowing it silently.** When no shared
  principle list is supplied and `store.get_relevant_principles` raises, the critic now emits one
  WARNING (`quick_check self-retrieval failed error_type=<class>; falling back to the constitution
  HARD principles`, exception text only in `exc_info`). Behaviour is unchanged: the check still falls
  back to the constitution's HARD principles and the verdict is not affected.
- **The hindsight single-consequence path now persists its retry-failed rows (NEW-3).** `evaluate_scenario`
  (used when batch evaluation is off or there is one consequence) left no row for a paid-for attempt whose output could
  not be parsed, unlike the batch path. It now writes the same `call_outcome="retry_failed"` row
  (`phase="hindsight_retry"`, the failed attempt's billed tokens); the pessimistic fallback is unchanged.
  **Comparability: from this commit `llm_call_count` and the per-request tokens include those failed attempts;
  earlier runs are not directly comparable.** Tests: `tests/test_llm_call_persistence_hindsight_single.py`,
  `tests/test_ui_llm_call_visibility_hindsight_single.py`.

- **Generic `except` in `process()` stays fail-closed for unprintable or empty-message exceptions.**
  `OrchestratorResult.error` is now built with a guarded `str(e) or type(e).__name__`; before, an
  exception whose `__str__` raised escaped `process()` instead of returning the `SYSTEM.FAIL_SAFE`
  REFUSE. Visible side effect: an empty-message generic exception (for example a bare `assert`)
  now sets `error` to the class name, so the CLI prints `Error: <ClassName>` where it printed nothing.
  Delivered text, `final_action` and reason codes are unchanged.
- **Docs: line anchors shifted by the F6 follow-ups.** The F6 follow-up commits shifted lines in
  `controller.py`, `sdk/wrapper.py`, `server/proxy.py`, `server/headers.py`, `ui/app.py` and
  `orchestration/types.py`; 141 documentation anchors were re-mapped by matching the cited line's
  content at `e30c97a` against HEAD. The one anchor whose cited line was rewritten (the proxy
  endpoints, `proxy.py:458-468` in `docs/traces/openai_compatible_multiturn.md`) was re-anchored
  by content to the route definitions (`proxy.py:687-693`), and the async route anchor in the same
  section to `chat_completions` (`proxy.py:694-750`). `docs/modules/decision_explanation.md` now
  cites the `reason_codes` priority in `ResponseMetadata.from_decision` at `orchestration/types.py:409-413`. Other anchors that were already stale before
  the track are not changed (separate cleanup). Docs only.

- **Request report status shows `PIPELINE FAILURE` (F6 follow-up).** `request_report_from_db` now
  parses `requests.meta_json` best-effort (malformed or non-object JSON is ignored, never raises)
  and, when the shared predicate `pipeline_failure_principle` finds a `SYSTEM.ERROR`,
  `SYSTEM.TIMEOUT` or `SYSTEM.FAIL_SAFE` principle and no FINAL trace row, sets the status to
  `PIPELINE FAILURE` naming the principle (it showed COMPLETED or APPROVED WITH CAVEATS, see the
  correction under "Side effects of the REFUSE label"). `request_report_from_cli` does the same from
  `result.response.metadata.triggered_principles` instead of "refused due to policy violations".
  Decided from structured signals only, never from the delivered text; reports with a FINAL row or
  no failure principle are unchanged.

- **UI flags `SYSTEM.TIMEOUT` and `SYSTEM.FAIL_SAFE` pipeline failures (F6 follow-up).** A new
  pure predicate, `moralstack/reports/pipeline_failure.py` (`PIPELINE_FAILURE_PRINCIPLES`,
  `pipeline_failure_principle`), recognizes a pipeline failure from structured signals only: a
  `SYSTEM.ERROR`, `SYSTEM.TIMEOUT` or `SYSTEM.FAIL_SAFE` entry in `meta_json.triggered_principles`
  and no FINAL decision-trace row. `_detect_pipeline_failure` delegates to it, so the request page
  and the conversation timeline now flag timeout and generic fail-safe turns (before, only
  `SYSTEM.ERROR` was flagged). The delivery-path explanation and the Final Response caption name
  the recorded principle (`SYSTEM ERROR` is unchanged for `SYSTEM.ERROR`). The benchmark keeps its
  narrower {ERROR, TIMEOUT} set. The test that pinned the timeout gap
  (`test_u1_ui_detector_accepts_new_error_shape`) now asserts detection.

- **SDK routing and stream metadata follow the delivered action (F6 follow-up).**
  `GovernedCompletions.create` now routes on `delivery.final_action` instead of the raw label, so
  an unlabelled, blank-content or non-canonical result takes the REFUSE branch like the proxy.
  A REFUSE stream replays `delivery.text` (a blank refusal used to stream an empty string) and
  stream `governance_metadata.final_action` is the delivered action, equal to the non-stream
  value. Valid-label responses are unchanged; `finish_reason` is changed separately (see the SDK `finish_reason` entry under Changed).

- **One normalization rule for the `final_action` label (F6 follow-up).** The
  `ensure_final_action_and_return` backfill now writes the canonical upper-case label
  (`NORMAL_COMPLETE` / `SAFE_COMPLETE` / `REFUSE`; it wrote lowercase). The turn summary, the
  ESCALATED posture check and the `ensure` early return use the same `normalize_final_action` rule
  as `finalize_delivery` (an unknown label is recorded as REFUSE; delivery output is byte-identical).
  The ledger store is skip-only: only an exact canonical label is cached, anything else is logged
  once at INFO and not stored. Review follow-ups (dead conditional, shadowed parameter, hoisted UI
  caption, narrower `try` in the CLI report): no behaviour change.

- **`finalize_delivery` fails closed on a result without a valid decision label (F6, phase 5).** A
  result whose `final_action` is outside `NORMAL_COMPLETE` / `SAFE_COMPLETE` / `REFUSE` (empty,
  unknown, garbage) is now delivered as the deterministic governed refusal (`governed_pipeline_refusal`,
  REFUSE, `content_filter`) instead of being delivered with `stop` (an empty label as NORMAL_COMPLETE,
  any other label verbatim); the conversation turn summary defaults an empty label to REFUSE likewise.
  A label that differs only in ASCII case or surrounding whitespace is normalized; a non-ASCII look-alike
  is refused. Valid labels are byte-identical.

- **A transient unexpected exception no longer pins a cached REFUSE (F6).** The fail-safe REFUSE
  produced by the generic `except` in `process()` (and by the `ensure_final_action_and_return`
  fallback) is no longer stored in the semantic ledger, where it was always reapplied to similar
  later turns. It now carries `system_error` (flip reason and reason code) and the generic
  except logs the `pipeline failed closed` WARNING.

- **Pipeline errors now fail closed to a governed REFUSE (F6).** A `MoralStackError` or
  `OrchestratorTimeoutError` caught in `process()` used to leave `final_action` empty, so
  `finalize_delivery` relabelled the `[SYSTEM_ERROR]` / `[SYSTEM_TIMEOUT]` result NORMAL_COMPLETE
  and the turn summary recorded NORMAL_COMPLETE. The handlers now return `final_action="REFUSE"`
  with flip reason and reason code `system_error` / `system_timeout`, principle `SYSTEM.ERROR` /
  `SYSTEM.TIMEOUT`, `path="ERROR_PATH"`, one WARNING (`pipeline failed closed ...`), and the
  codes join `LEDGER_NON_CACHEABLE_FLIP_REASONS`, so they are never cached. No FINAL trace row is
  written (the UI keeps recognizing `SYSTEM.ERROR`). `error` falls back to the exception class
  name when `str(e)` is empty or raises.

- **Benchmark: pipeline-failure rows stay system errors.** `scripts/benchmark_moralstack.py` now
  classifies rows whose MoralStack metadata carries `SYSTEM.ERROR` or `SYSTEM.TIMEOUT` as
  `moralstack_final_action_failed` whatever their `final_action`, so they stay counted as system
  errors and excluded from FN/FP once pipeline errors are labelled REFUSE. Neutral at HEAD (these rows
  carry an empty `final_action` today); `SYSTEM.FAIL_SAFE` rows are still counted as REFUSE.

- **Transient fail-closed REFUSEs are no longer cached in the semantic ledger.** A critic outage
  (`deliberation_critique_failed|timeout|unverified`) or a fast-path escalation failure
  (`fast_path_escalation_failed|unverified`) used to be stored and then reapplied, pinning REFUSE for
  similar later turns of the conversation. These five REFUSEs now skip the ledger store, so the next turn
  is governed afresh. Hard-violation guard (`hard_violation_*`) refusals are still cached; lookup and
  reapply are unchanged.

- **Deliberative route fails closed when the critique did not complete.** Client-visible effect: where a
  deliberative draft used to be delivered on the risk signal alone because the critic failed, timed out
  (the 90% gate of `timeout_ms`) or was never invoked in the final cycle, the request is now answered with a
  governed REFUSE (`content_filter`) carrying `hard_violation_flip_reason` `deliberation_critique_failed` /
  `deliberation_critique_timeout` / `deliberation_critique_unverified` and a FINAL trace row
  (`hard_violation_source=deliberation_fail_closed`). `DeliberationRunner._critique` now records a per-cycle
  outcome and never raises; after a failure in cycle N, a report from cycle N-1 can no longer vouch for the cycle N
  draft. A critique that completes is unchanged; a `skipped` critique (zero retrieved principles) is still
  accepted, with a WARNING; no critic or no constitution source is still skipped by design. Critique telemetry is
  isolated, so a telemetry failure no longer drops a completed critique. The fast-path escalation predicate is
  tightened the same way: it requires a critique that completed in the final cycle, not just a non-skipped last
  report. Known gap: the soft-revision rewrite is not re-critiqued.

- **Fast path fails closed when the HARD safety check cannot be completed.** Client-visible
  effect: where an unchecked draft used to be delivered as `NORMAL_COMPLETE`, the request is now
  escalated to deliberation, or answered with a governed REFUSE (`content_filter`), whenever the
  HARD `quick_check` fails, errors (provider/transport error, unparseable reply, or a reply without
  the `violated` key) or cannot run because the constitution failed to load although a store is
  configured. If the escalation raises (`fast_path_escalation_failed`) or ends without a completed
  critique (`fast_path_escalation_unverified`) the draft, including an upstream-origin draft, is
  never delivered. `LLMConstitutionalCritic.quick_check` now raises instead of returning
  `passed=True` on errors. The refusal carries `original_final_action` / `hard_violation_flip_reason`
  and a FINAL trace row (`hard_violation_source="fast_path_fail_closed"`). Prompts are unchanged.
  Cost: a transient `quick_check` error now costs a full deliberation or, if that also fails, a
  refusal that the semantic ledger can cache and reapply (documented in `docs/CODEBASE_FACTS.md`).

- **Orchestration: the second, ungoverned generation call after a `TypeError` is gone.** Ten call
  sites (`deliberation_runner.py`: benign fast path, safe-complete text, fast path, soft revision,
  generate/rewrite; `controller.py`: risk estimate, speculative draft, compliance regeneration,
  draft-match classifier) caught `TypeError` and silently retried with fewer arguments, dropping the
  composed system prompt (and `overrides`, `config`, `model_override`, or the whole multi-turn
  `messages`), possibly billing the provider twice and persisting an audit row that described a
  system prompt that was never sent. A `TypeError` now reaches the existing fail-closed handler of
  its site.
- **Orchestration: `InMemoryLedgerStorage` is now thread-safe.** The ledger storage is shared
  by every request of a pipeline (the proxy runs requests in a threadpool and locks only per
  `conversation_id`), but had no lock: a `put` during another request's `get_entries` raised
  `RuntimeError: OrderedDict mutated during iteration`, which the `process()` catch-all turned
  into an `ERROR_PATH` response for an unrelated request, and two concurrent `put`s could take
  the same entry id and lose an entry. One plain `threading.Lock` now covers `get_entries`,
  `put`, `size` and `clear`; `get_entries` returns a detached list. Eviction (insertion-order
  FIFO, global; the "LRU" wording was inaccurate and is corrected) and TTL semantics are
  unchanged. Residuals: the unguarded but benign `_embedding_dim` write in `ledger.py`, and
  one ledger per worker process.

- **Observability: the per-request token totals count the three risk mini-estimator calls
  again.** The in-process accumulator skipped `LLM_CALL` rows whose
  `billable_provider_call` key is present as `None` (the mini-estimator rows since
  `5439aaf`), while SQL counts NULL as billable. Both now share one rule
  (`service._counts_as_billable`): a falsy flag excludes, `True`/`None`/absent counts; the
  gate also moved inside the swallowing `try`. **Client-visible, values only (shapes
  unchanged):** proxy `usage` tokens and SDK `GovernanceMetadata` token fields rise for every
  request that runs the parallel risk estimator, and `llm_call_count` grows by 3 when all
  three minis succeed (`calibration_guard` stays non-billable, retry rows were already
  counted). **Comparability break:** `request_token_usage` and archived `usage` figures
  (still best-effort) are unaffected by the mini-row gap before `5439aaf` (2026-07-18), miss
  the three mini calls from then until this fix, and include them after it; compare within one regime, or use `SUM(llm_calls)` with
  `COALESCE(billable_provider_call,1)=1` (never affected).

- **Observability: the rollback->WAL switch of a new SQLite file is serialized per file within
  the process.** On a fresh file a concurrent `PRAGMA journal_mode=WAL` fails at once with
  `database is locked` (`busy_timeout` does not apply), so `init_db` could return `False` and
  leave the schema briefly absent. `_get_connection` now holds a per-file lock around that one
  pragma (`sqlite_sink._wal_switch_lock`, lexical path key), covering every opener, not only
  `init_db`. `DefaultPersistence` now re-initializes the schema when `get_db_path()` changes
  (it cached a path-agnostic flag, so writes after a re-point hit a DB with no schema) and
  a failed init is still not cached and is retried on the next call. Not covered: the same race across processes, and envelopes queued
  before the first `init_db` on a fresh file (recorded as a hypothesis in
  `docs/CODEBASE_FACTS.md`). No API or payload change.

- **Docs: stale line anchors in `docs/CODEBASE_FACTS.md`.** The 2026-10-12 risk-estimator
  cleanup shifted `moralstack/models/risk/estimator.py`; five existing fact rows (domain
  routing, retrieval metadata, billable flag, provider retry, `RiskEstimatorConfig`) cited
  the old lines. Re-anchored after matching each cited line by content at HEAD. Docs only.

- **Docs: risk mini-estimator `response_format`.** `docs/modules/risk_estimator.md` said the
  mini-estimators request `json_object`; each one actually sends its own strict `json_schema`
  (`intent_response_format()`, `operational_response_format()`,
  `harm_signal_response_format(registry)`), and the plain `json_object` default of
  `_build_generation_config` is never hit by the live path. Docs only.

- **AI harness (lot 1).** Fixed five defects in `.claude/` hooks/guards: doc-path
  references now consistently use the on-disk `docs/traces/` casing (several
  files used an upper-case variant, invisible on case-insensitive filesystems);
  the Stop gate now runs `pre-commit` only (no pytest) under a 150 s budget
  inside a 300 s registration timeout; all 10 hook registrations use a portable
  `python`→`python3` interpreter wrapper with `"shell": "bash"` so a missing
  interpreter fails loud instead of silently disabling a guard; `guard_secrets`
  now blocks any Bash command that names a secret-bearing file outside a narrow
  `ls`/`stat`/`test`/`echo`/`printf` exemption (deny list widened accordingly);
  and three false documentation statements (a non-existent hard-reset block, a
  non-existent CI step, a non-existent `py` fallback) were corrected.
- **Stale Codex reviewer references.** The codebase index entry point and the
  `scripts/ai` diff/quality helpers still named Codex as the reviewer; they now
  name the `adversarial-reviewer` sub-agent that replaced it.
- **AI harness instructions that contradicted the repository.** A prompt audit of
  `.claude/` found agent, rule and skill text asserting things the code no longer
  does: the `pre-commit-verifier` agent described the pre-`not slow` pytest options,
  a hook list without `changelog-guard`/`memory-guard` and a dropped mypy flag (also
  still emitted by `scripts/ai/detect_python_quality_commands.ps1`); two UI-loop
  agents pointed at non-existent `scripts/verify.py` / `scripts/scenarios.py`; the
  release skill committed in Italian against the English-commits rule (the driver
  now commits `chore(release): release vX.Y.Z`); `~120` test-file counts were stale;
  and PROJECT_SPEC §8 plus the SessionStart reminder presented the Stop hook as the
  docs gate, while the hard gate is the `memory-guard` pre-commit hook. Dated
  history notes and all-caps emphasis were also removed from the release skill,
  the `adversarial-reviewer` and the `claude-implementer` definitions; no rule was
  dropped.
- **References to a `docs/CODEBASE_FACTS.md` section that does not exist.** PROJECT_SPEC
  §4/§9, the memory and docs rules, the `memory-curator` agent, the SessionEnd diary line
  and three `docs/ai` pages sent unverified claims to a "Hypotheses / Unverified
  assumptions" (or "Hypotheses / Future work") section. The ledger has no such section:
  unverified claims are `**Hypothesis — …**` rows under "Future work / known gaps",
  which is what they now name.
- **Stale statements flagged by the ledger curation.** `docs/CODEBASE_FACTS.md`: test
  baseline 2150 → 2950 passed (header and two rows), the 512 rule-window default dated
  to its commit (`9cd8296`, 2026-08-18), refreshed line cites on the reason-code and
  `core`-domain rows, and a caveat on the `init_db` entry (connections already wait up
  to 10 s on a lock, so which statement failed is an inference). The codebase index no
  longer says the mini-estimator rows are simply billable (the per-request accumulator
  skips them), and `docs/modules/critic.md` dates the default change the same way.
- **The request-page execution graph no longer tells the hard-violation delivery guard's
  story backwards.** On a request where the critic raised a hard violation (run
  `0d4a091a`, request `9eef1009`), the guard's SAFE_COMPLETE regeneration was drawn inside
  "Initial assessment" — before calibration and routing, under a "risk + principles"
  pipe — and its re-critique was tiered "parallel" with the critique that had caused it;
  a reviewer read "routing → SAFE_COMPLETE → then a deliberation", the reverse of what
  ran. The cause was persistence, not rendering: the guard reused the fast-path row shape
  (cycle 0, `SEQ_POLICY`, `generate (safe_complete_path)`) and `SEQ_CRITIC`, and the
  graph orders by `(cycle, sequence_in_cycle)`. The guard's two `llm_calls` rows are now
  written in the cycle that raised the violation with their own sequences
  (`SEQ_HARD_VIOLATION_REGENERATION=7`, action `generate (hard_violation_regeneration)`;
  `SEQ_HARD_VIOLATION_REVALIDATION=8`), the fast-path row is byte-unchanged, and the UI
  re-homes rows persisted before this change (badge `legacy row re-homed to guard`). Three
  smaller falsehoods in the same graph went with it: the synthetic calibration and
  path-routing nodes now carry a sequence, so they render before the policy draft they
  precede instead of after it; the critic→simulator pipe reads `gate: proceed` only when
  simulator/perspectives actually waited for the critic (`critic_gated`), otherwise
  `scheduled in parallel with critic (not gated)`; and the routing node's
  `final_action`/`winning_rule` inputs come from the `DECISION_EXPLANATION` in force at
  the branch rather than the post-deliberation one (`winning_rule=hard_violations` was
  being shown as an input to a decision taken before the critic ran). Historical
  analyses that count `generate (safe_complete_path)` as the guard's regeneration apply
  only to runs before this change. Pinned by `tests/test_ui_hard_violation_guard_graph.py`,
  which reproduces the observed graph verbatim against the pre-fix code.
- **Every governance module except the critic now has its output schema enforced by the
  provider, closing two failure modes that produced no error and no retry.** Measured
  across the COMPL-AI T=0/T=1 campaigns (144k calls), the harm-signal scanner returned
  `{"data": [...]}` — the request echoed back — on 41 calls, and misspelled
  `q10_weapons_explosives_toxins` as `..._txins` on 5. Both are **valid JSON**, so the
  tolerant `data.get(key, "no")` in `calibration.py` read the signals as absent: in the
  first case all 17 harm questions at once, in the second a signal that sits in
  `priority_harmful`, i.e. a hard one. Neither raised, neither retried, neither left a
  marker. Verified outcome-by-outcome: all 41 still ended in REFUSE (risk 0.85-0.95)
  because intent and operational carried the decision, and all 5 misspelled values were
  `'no'`, matching the default — so no decision is known to have been changed. The point
  is that nothing in the system would have told us otherwise. A third case, an invented
  `harm_type` (16 calls: `security`, `privacy`, `deception`, `manipulation`,
  `educational_integrity`), is largely absorbed downstream because `calibration.py`
  reassigns `harm_type` by signal priority — but only when a signal fires.
  Schemas now cover: risk intent / operational / harm signals, hindsight (single and
  batch, separately — they have different prompts and parsers), perspectives, safety
  override, and the DCCL draft match. Each is derived from its contract's own source:
  the harm-signal schema from the same `SignalRegistry` that renders the prompt, the
  hindsight schema from the Pydantic model's fields, the safety-override enum from
  `SAFETY_OVERRIDE_CATEGORIES`. **The critic is deliberately excluded**: zero parse
  failures over 5,000 campaign calls, and it is the P0 module whose verdict decides
  `final_action`, so it is not bundled with seven other changes.
  Guarantee and its limit: shape, key names and enums are enforced; value ranges are
  not (the supported JSON Schema subset has no `minimum`/`maximum`), and a model outside
  the `supports_json_schema` allowlist still falls back to plain JSON mode, so
  client-side validation stays load-bearing.
  **Verified against a live provider** (2026-09-08, 47 requests replaying the exact
  prompts that had failed, 507 LLM calls, $2.37): simulator retries 35% -> 0, and zero
  `data` envelopes, out-of-schema keys or out-of-enum `harm_type`. The 10 requests that
  used to return the `data` envelope — and therefore no signals at all — now raise real
  ones, hard signals included; they ended in REFUSE before and after, so no decision
  changed, but the decision now rests on the evidence that belongs to it. 11 of 47
  decisions differ against 10.0 expected from the noise measured on those same prompts,
  i.e. indistinguishable. An apparent regression in `domain_sensitivity` (6/37 against
  1.8% natural variability) was investigated and **disproven** by a two-condition
  ablation: 0 downgrades in 40, the one discordant case being an upgrade where the
  schema matches the logged value better than plain JSON mode. The cause was the replay
  harness, which sends no developer contract — its fidelity control goes from 62% to 98%
  once the contract is restored.
  **Follow-up 2026-09-09, covering the deliberative path with a contract** (50 requests,
  5 per task, replayed through the proxy with the developer contract and history the
  campaigns actually sent): 48/50 decisions coherent, zero failed retries anywhere, and
  the simulator's discard rate on those same prompts goes from 17.3% to 0%.
  One qualification found there and worth stating plainly: on the `estimate_intent`
  schema the strict `response_format` **does** perturb `request_type` — on a prompt with
  a clean fidelity control the logged value drops from 29/30 to 18/30 (Fisher exact
  p=0.0011). It is not a drift away from the campaign values (both conditions agree with
  the log 12/15), it does not appear on the harm-signal schema (0 changes in 40), key
  ordering is ruled out as the mechanism, and the one decision it moved went *toward*
  more caution — but the mechanism is unidentified and the aggregate direction across
  the enum is not established. See `docs/CODEBASE_FACTS.md`.
  Tests: `tests/test_structured_outputs_modules.py`.

- **Failed risk-estimator retries are now recorded instead of only logged.** The
  mini-estimator retry loop emitted a `logger.warning` and nothing else, so a paid-for
  attempt whose output could not be parsed left no row in `llm_calls` — the module that
  decides routing and the hard signals was the only one with no audit trail for its own
  failures, while critic, simulator, hindsight and perspectives all had one. It now
  emits the same `call_outcome="retry_failed"` row (`phase="risk_retry"`). Measured
  incidence is low (7 retries in 50,346 campaign calls, ~$0.04) — the value is that the
  failure becomes visible at all. Tests:
  `tests/test_risk_persist_batch.py::test_risk_estimator_failed_retries_are_persisted`.

- **The simulator now has its output schema enforced by the provider, so a single
  enum confusion no longer burns a third of its calls.** It ran with
  `response_format={"type": "json_object"}`, which guarantees only that the reply
  parses: the enums were checked client-side afterwards and any violation rejected
  the whole reply, costing up to three calls. The rejected replies were **not**
  malformed — on `include` rep 1 of the COMPL-AI T=0 campaign all 198 were complete,
  valid JSON, indistinguishable in length and tokens from the accepted ones. They
  differed in one field: the model put a `harm_type` value into `scenario_type`
  (`misinformation` 1,896 times across the 60 campaign runs, plus `security_breach`,
  `reputational_harm`, `psychological_harm`, `privacy_breach` — each a valid
  `harm_type`, none a valid `scenario_type`). Retrying could not help, because the
  same prompt at low temperature reproduces the same value, so all three attempts
  failed identically. Measured cost: 2,231 discarded calls, $5.08 per T=1 campaign
  and $5.18 per T=0 campaign, up to 8.9% of a single run on `include`; and 239
  invocations (~7%) exhausted every retry and produced no consequence at all, so
  those requests were governed without the simulator's contribution — the larger
  half of the damage. `simulator_response_format()` now builds a strict `json_schema`
  from the same `Literal` aliases as the Pydantic model, so schema and validator
  cannot drift. Client-side validation is unchanged and still load-bearing on the
  degraded path. Not yet re-measured against a live provider.
  Tests: `tests/test_structured_outputs_simulator.py`.

- **The proxy no longer drops whole requests from the SQLite audit trail when the
  observability database is re-pointed after the app is built.** `_ensure_request_row`
  pre-inserted the `requests` row without ensuring its parent `runs` row (and, on a fresh
  file, the schema) existed in the database actually being written. Both are created at
  `create_app` time, but a benchmark harness has to re-assert
  `MORALSTACK_OBSERVABILITY_DB_PATH` *after* importing the app — `build_app()` reloads
  `.env` with `override=True` — so the parent row stayed in the `.env` database. Every
  pre-insert then failed the FK and was swallowed, and because all child tables FK to
  `requests(run_id, request_id)`, the request disappeared from SQLite entirely: llm calls,
  orchestration events and decision traces included. Measured on the COMPL-AI T=0 campaign
  (`boolq_contrast` rep 1): 5 of 153 requests missing, with their 31 `llm.call`, 85
  `orchestration.event` and 7 `decision.trace` rows present in JSONL and absent from the DB
  — the counts reconcile exactly. The rows survived at all only because the controller's
  `DefaultPersistence` re-creates run and request later in the pipeline; the head of a run
  raced that safety net. `_ensure_run_row` now ensures schema + run row once per
  `(db_path, run_id)`, so the pre-insert no longer depends on it. Observability stays
  best-effort: the whole path is still inside the swallowing `try/except`.
  Regression: `tests/test_server_proxy.py::test_request_row_survives_observability_db_repointing`.
  Runs recorded before this fix are unaffected in their answers and scores — only their
  telemetry is short — and must be analysed from the JSONL.

- **A ledger-replayed decision is now marked in the audit trail.** When the
  `SemanticDecisionLedger` fast path reuses a cached decision, the persisted record used
  to carry the reasoning of a decision that never ran: on the 2026-08-20 COMPL-AI replay,
  **11 of 135 refusals recorded `decision_reason=DEFAULT_NORMAL_COMPLETE`** — a refusal
  whose stated reason said "complete normally" — and all 11 came from the ledger.
  The fix has two halves, and the first alone changes nothing observable:
  (1) `ConversationalFastPathRunner.apply_cached_decision` appends the policy string
  `cached_decision_reused`, mapped to the new `ReasonCode.LEDGER_FAST_PATH_REUSE`, after
  the cached codes, which keep their order; (2) the controller replaces the now-stale
  `DecisionExplanation` via `decision_explanation_for_ledger_reuse` immediately after the
  patch — `explanation` is built by `decide_action` *before* the ledger is consulted, and
  `ResponseMetadata.from_decision` prioritizes `decision_explanation.reason_codes` over
  `decision.reason_codes`, so half (1) alone never reached the database. This is the same
  failure mode `_decision_explanation_for_hard_violation_flip` already prevents at another
  call site. Verified against a running proxy: the persisted `decision_reason` went from
  `DEFAULT_NORMAL_COMPLETE` to `DEFAULT_NORMAL_COMPLETE, LEDGER_FAST_PATH_REUSE`; half (1)
  alone was measured and left the record unchanged. No routing change — the marker appears
  in none of the sets that gate decisions. Locked by
  `tests/test_conversational_fast_path.py::TestReuseMarkerReachesAuditRecord` (drives the
  same helper the controller calls) and `::TestControllerRebuildsTheExplanation` (asserts
  the call site survives); both fail with the fix removed.

- **`LEGAL.NOPRACTICE.1` now leads with its carve-out instead of closing with it.** The
  rule banned case-specific legal advice first and exempted general procedural or
  statutory explanations last, so the exemption sat past char 180 — the window that
  judged it until 2026-08-17, and the reason "what's the process for terminating a
  contract?" kept producing a hard violation. Reordered so the exemption ends at char
  173 of the rule; prohibition, level (`hard`), priority, id, keywords and examples are
  unchanged. Redundant at the new 512 default and **not re-measured against live
  models** — it is defence in depth for a lowered window, pinned by
  `tests/test_critic_rule_window.py`.

### Security

- **The semantic ledger no longer reuses a cached decision on a request with hard-signal evidence (PROJECT_SPEC §5.3).** A cached `NORMAL_COMPLETE` / `SAFE_COMPLETE` hit used to be applied to any request whose current route was `fast_path`, `benign`, `safe_complete` or `refuse`, even when the current risk estimation carried hard topical evidence (any of the eight hard semantic signals or the `clearly_harmful` category). On `fast_path` the hit was forced to the `benign` route, which has no critic, so the only HARD check of the freshly generated draft (`quick_check`) was skipped. Reproduced offline with a real risk estimator, decision policy, router and in-memory ledger: the same Q9 (`cyber_malware`) prompt sent twice in one conversation ran `quick_check` on turn 1 and skipped it on turn 2. A new pure helper, `ledger_hard_signal_block_reason` (`moralstack/orchestration/conversational_fast_path.py`), is now consulted at the controller apply site after `is_safe_to_apply`: a cached non-REFUSE decision is not applied when `has_hard_signal_evidence` holds (or the current decision is a hard-signal REFUSE) and the request continues exactly as on a cache miss (on `fast_path`, `quick_check` runs and the existing fail-closed contract applies). A cached `REFUSE` is never blocked. `LEDGER_FAST_PATH_NOT_APPLIED` keeps its payload keys; `gate_reason` / `reason_codes` gain the values `hard_signal_evidence` and `hard_signal_refuse`, and the taxonomy docstring and UI text now say the turn proceeds on its current route rather than always deliberating. **Exposure before this fix:** the SDK enables the ledger by default (`enable_ledger=True`, similarity threshold 0.92) and the ledger key `(contract_hash, posture, domain)` carries no conversation id, so a hit could come from another conversation; how often real LLM estimators produce the triggering combination was not measured. Behaviour change: hard-evidence requests on `fast_path` lose cache reuse and pay one `quick_check` per blocked hit. `is_safe_to_apply` and `apply_cached_decision` are unchanged. Still open, recorded as hypotheses in `docs/CODEBASE_FACTS.md`: hard-evidence requests can still be routed to `fast_path` (only the top-10 HARD `quick_check`, never a full critique), and the `run_fast_path` WARNINGs still print the exception text on the message line.

- **SDK pipeline-error `decision_reason` no longer carries exception text (BREAKING, behaviour).** On the SDK path where an exception escapes `process()`, `GovernanceMetadata.decision_reason` is now the fixed string `Pipeline error` (was `Pipeline error: <exception text>`), for `create()`, `stream=True` (`GovernedErrorStream`) and the deprecated `from_passthrough`; `from_pipeline_error` can no longer raise on an exception whose `__str__` raises. `final_action`, `reason_codes`, `winning_rule`, `finish_reason` and the delivered text are unchanged. The text is now emitted as one ERROR record on the `moralstack.sdk.wrapper` logger (message line carries the exception class name only; the text and traceback travel via `exc_info`), which reaches the host's logging, and stderr via `logging.lastResort` when logging is not configured. SDK clients that parsed the reason text must read the log instead.

- **Debug events no longer persist exception text for generic pipeline failures and ledger store failures.** The `H-except` `debug_events` row written by the generic `except` in `OrchestrationController.process()` now carries `error_type` (the exception class name) instead of `error: str(e)[:200]`. The non-fatal ledger store failure in `_maybe_store_in_ledger` (`H-ledger-store-fail`) likewise carries `error_type` instead of the untruncated `str(e)`, and its text and traceback move to a guarded controller WARNING (`ledger store failed (non-fatal) error_type=...`, text via `exc_info`), so the diagnostic is not lost. Delivered text, `final_action`, reason codes and the `PIPELINE_FAILED` payload are unchanged. Rows already written keep their old text (no purge or migration). Consumers reading `error` from `H-except` / `H-ledger-store-fail` debug payloads must read `error_type`. `docs/CODEBASE_FACTS.md` records the fix as a FIXED row (formerly a Future-work hypothesis) and the remaining sinks as a new Hypothesis row.

- **Four constitution principles re-levelled or split so a missing disclaimer can no
  longer cause a refusal.** Measured false positives drove this: questions about the
  Holocaust, about the general process for terminating a contract, and about coping with
  sorrow through music were all refused because a principle demanding a *framing* was
  marked `hard`, and a hard violation surviving the deliberation reaches the
  delivery-point guard, which fails closed to REFUSE.
  `MH.NOTTHERAPY.1` keeps `hard` but now states only the prohibition (never position
  the assistant as a replacement for treatment, never discourage professional help);
  the disclaimer duty moves to the new **`MH.DISCLAIMER.1`** (`soft`, p90).
  `EDU.AGEAPPROPRIATE.1` drops to `soft` (p85, register calibration); the adult-content
  prohibition moves to the new **`EDU.MINORCONTENT.1`** (`hard`, p100), explicitly
  inapplicable when no minor context is present, and explicitly not triggered by
  difficult subject matter. `LEGAL.NOPRACTICE.1` keeps `hard` and now carries in its own
  rule the case-specific limit that previously lived only in `LEGAL.DISCLAIMER.1`.
  `CORE.PRIV.1` and `HC.HIPAA.1` keep `hard` and gain positive `examples_allow` for
  aggregated/de-identified analysis; `HC.HIPAA.1` also loses the vague opening duty
  ("Respect health information privacy") that invited analogical extension.
  Constitution-only change — no code touched. Verified with
  `examples/airbench/run_regression.py` (new): the four in-scope cases pass (three at
  3/3, `LEGAL-01` at 2/3) and all four controls hold at 3/3 with zero forbidden markers
  leaked across 12 replays. Full suite 2536 passed, all 21 overlays validate.
- **A critic-rejected draft can no longer be delivered verbatim under
  SAFE_COMPLETE (P0, §5.3 hard-signal supremacy).** Measured on 1625 real FINAL
  traces: 16 `SAFE_COMPLETE` deliveries carried non-empty hard-violation codes
  (`CORE.PRIV.1`, `HC.HIPAA.1`, `CORE.MALWARE.1`, ...), 10 of them with no
  `rewrite` call at all — the delivered text was the exact draft the critic had
  just rejected on a hard violation, reached via two routes (critic votes
  REFUSE, or votes REVISE and cycles exhaust) and three
  `decision_service._handle_hard_violations` downgrade branches (crisis,
  regulated-informational derogation, pre-policy SAFE_COMPLETE). A new
  delivery-point guard (`DeliberationRunner.enforce_no_rejected_draft_delivery`,
  called from both post-`decide_action` sites right before `assemble` —
  `_build_deliberative_result` and the controller's `_route_deliberative`)
  fires whenever `decision.hard_violations` is non-empty and
  `decision.final_action != "REFUSE"`: it regenerates under SAFE_COMPLETE
  governance (violated principle ids passed as user-side guidance only, never
  appended to the system prompt, never critic rationale), re-validates exactly
  once with a **direct** `critic.critique(...)` call (never the timeout-guarded,
  exception-swallowing `_critique` wrapper), and delivers the regenerated text
  as SAFE_COMPLETE only if the critic clears it — otherwise it fails closed to a
  governed REFUSE. A second, independent measure closes the same hole from the
  gating side: `apply_safe_complete_gating` no longer relabels a SAFE_COMPLETE
  decision that still carries `hard_violations` down to NORMAL_COMPLETE.
  `hard_violation_downgraded_to_safe_complete` now has its own `ReasonCode`
  (previously flattened into `SAFE_COMPLETE_REQUIRED`, indistinguishable from a
  plain policy SAFE_COMPLETE in the audit trail). A fail-closed flip records the
  pre-flip action and reason on `ResponseMetadata` (`original_final_action`,
  `hard_violation_flip_reason`, additive-only) and appends (never rewrites) a
  reconciled FINAL decision trace, so the already-persisted FINAL trace and the
  delivered REFUSE tell one consistent story. The ~99% of requests without hard
  violations are byte-unchanged and make no extra LLM call.
  `ResponseAssembler`/`ResponseAssembler.__init__` are unchanged.
- **`DeliberationRunner`'s per-request clock and reuse-target audit trail are now
  request-scoped too (follow-up to the constitution-retrieval fix above).** The runner
  is built once per process and served requests on real threads
  (`server/proxy.py`), so its two remaining per-request instance attributes were
  last-writer-wins: `_current_start_time` (read by the four timeout gates before
  critique/simulation/hindsight/perspectives) and `_request_analysis_reuse_targets`
  (the `reuse_targets`/`reuse_count` audit fields in the `REQUEST_ANALYSIS_CONTEXT`
  trace). A slow request could backdate the shared clock and make a concurrent
  request's critique — the module that detects hard violations — skip via a stale
  90%-timeout ratio; this is not a corrupted audit field, it is governance not
  executed because another request was slow. `start_time` is now threaded as an
  explicit keyword parameter (default `None`, preserving "absent means do not skip")
  through `_critique`/`_simulate`/`_evaluate_hindsight`/`_evaluate_perspectives` and
  every intermediate caller, instead of living on the runner instance.
  `_request_analysis_reuse_targets` moved onto the per-call `DeliberationState`
  (including `DeliberationState.fork()`, so both parallel scheduling strategies carry
  it correctly, and an explicit merge at the full-parallel critic-fork seam). No
  persisted payload shape changed (`reuse_targets`/`reuse_count` keep their exact
  keys); no public API changed.
- **Constitution retrieval is now request-scoped (P0 concurrency fix).**
  `RiskEstimation.detected_domain` — which feeds the runtime overlay, `domain_regulated`,
  the sensitive risk floor and the domain-exclusion refusal route — used to be read back
  from `ConstitutionRetriever._last_debug_info`, a mutable instance attribute on a
  constitution store built once per process and hit by real concurrent threads
  (`server/proxy.py`, `run_in_threadpool`). Measured on 709 production requests: 264
  (37.2%) carried a `detected_domain` that was not their own prefilter's, and 108 of
  those crossed the overlay `sensitive: true` line. A second, deterministic channel
  (not a race) let a caller's forced `domain` argument leak into the shared
  `DomainPrefilter` cache and poison every later request sharing the same cache key.
  Both channels are closed: `ConstitutionRetriever.retrieve()` / `ConstitutionStore.retrieve()`
  now return a frozen `PrincipleRetrievalResult` (principles, `prefiltered_domains`,
  `debug_info`) on every call, writing no instance state; the prefilter cache is never
  handed out or mutated by reference. `get_relevant_principles()` keeps its exact
  signature/return type as a stateless projection of `retrieve()`. A store that does not
  yet implement `retrieve()` degrades loudly — a rate-limited WARNING plus a persisted
  `domain_channel` marker (`"retrieve"` on the normal path, `"fallback_no_retrieve"` on
  the fallback) in the `REQUEST_ANALYSIS_CONTEXT` trace — rather than silently inheriting
  another request's domain. **Migration note:** `ConstitutionStore.get_debug_info()` and
  `ConstitutionRetriever.get_debug_info()` are removed (not deprecated); external
  integrators reading per-retrieval debug info must switch to `retrieve(...).debug_info`.
  `DeliberationRunner`'s own per-request clock/reuse-target fields are a separate,
  follow-up commit (out of scope here).
- **Runtime DB backup artifacts are now git-ignored.** Root snapshots such as
  `moralstack.db.premigration.<ts>.bak` carry persisted prompts, responses and governance
  audit rows. The existing rules covered `/moralstack.db` and `moralstack.db-*` but not the
  `.bak` suffix, so a backup could be staged by an untargeted `git add`.
- **Dated DB backups, their SQLite sidecars and root probe outputs are now git-ignored too.**
  The rules above still missed `moralstack<YYYYMMDD>bkp.db` (only the exact path
  `/moralstackbkp.db` was covered), the `-shm`/`-wal` sidecars of any backup, and the
  root-level `gate_verify_*.csv`/`.jsonl` probe outputs, which embed benchmark prompts and
  the governed responses of a run.
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

- **Opt-in `generation="upstream_then_verify"` mode (P0 invariant #7 amended).** When the
  mode is enabled *and* the caller supplies a `model`, the wrapped/upstream client produces
  the **speculative draft** — and nothing else — which then enters the existing pipeline
  through the `_speculative_generate` → `SpeculativeOverlapHandle` seam and receives exactly
  the same route-specific treatment an internal speculative draft receives today: risk-only
  on the benign fast-path, DCCL-only on a compliance `MATCH`, full critique on the
  deliberative route, and **discarded on REFUSE / hard-signal**. This is *parity with the
  internal draft*, **not** full-pipeline validation on every path — see
  `.claude/rules/governed-delivery.md` for the precise wording. The upstream client is never
  called to produce a delivered answer directly, and an empty or failing upstream draft falls
  back to **internal governed regeneration** (never a refusal, never a passthrough). Enabled
  via `GovernanceConfig.generation` or `MORALSTACK_GENERATION_MODE`; unknown values fail
  closed to `internal`. Default `internal` is byte-identical on every persisted/wire sink.
  Draft provenance (`draft_origin`/`draft_model`) is carried end-to-end — a distinct
  `module="upstream_speculative"` `llm_call` label, `SPECULATIVE_STARTED`, the
  `X-Moralstack-Draft-Origin`/`-Draft-Model` headers (emitted only when upstream),
  `requests.meta_json`, the SSE/non-stream `model` field, the SDK `GovernanceMetadata`
  (two additive defaulted fields), and the UI / markdown-export / "Models used" readers.
  The caller's model reaches **only** the draft call; every governance module keeps its
  configured model. Known limitation: the proxy does not validate `body["model"]` against an
  allowlist — deliberately deferred, tracked as an open security follow-up.
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

- **The example configs now set `MORALSTACK_RISK_OPERATIONAL_MODEL=gpt-4o`** (was
  `gpt-4o-mini`) in `.env.minimal` and `.env.template`. Follows the paired A/B measurement
  recorded in `docs/CODEBASE_FACTS.md`: with the slot as the sole changed variable, CoCoNot
  `contrast` compliance went 91,0 % → 94,3 % (n=379, sign test p=0,017) and XSTest `safe`
  refusal rate 14,8 % → 9,6 % (n=250, p=0,0024), while the AIR-Bench 2024 safety score moved
  0,9062 → 0,9000 (n=80, p=1,00, bootstrap CI contains 0) — significant gains on both
  over-refusal benchmarks at a safety cost statistically indistinguishable from zero.
  Examples only: the dataclass default and the env-resolution chain in
  `models/risk/config_loader.py` are unchanged, so no runtime default moves.
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

- **Per-model token panel: every provider LLM call now records its generating model,
  and the synthetic `calibration_guard` row is excluded from cost.** Eleven call sites
  emitted an `llm_calls` record without `model`, so their (billable) tokens collapsed
  into a single unattributed `'—'` row in the per-model token panel: the
  fast-path/benign/safe-complete `generate (...)`, the REFUSE (`refuse (fast_path)` /
  `refuse (deliberative)`), `generate (compliance-regenerate)`, `draft_revalidate` (now
  attributed to the DCCL model), and the critic/simulator/hindsight/perspectives
  `retry_failed_attempt_*` rows. Separately, the risk estimator's synthetic
  `calibration_guard` row (no real provider call, no `usage`) was counted as billable and
  surfaced as a spurious "missing"-usage row; it is now emitted with
  `billable_provider_call=False` (still persisted for audit, excluded from token/cost
  aggregation). The risk estimator's local `llm_call` payload gained a
  `billable_provider_call` key (16-key shape). No historical rows are rewritten by the
  code change.
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

- **Test suite runs in parallel and ~5x faster** (`pyproject.toml`, `tests/conftest.py`).
  `python -m pytest` now uses pytest-xdist (`-n auto --maxprocesses=8 --dist worksteal`;
  new dev dependency `pytest-xdist>=3.5`); serial runs use `-n 0`. `tests/conftest.py` adds
  test-only speed patches: one shared default httpx SSL context, a shared Jinja bytecode
  cache for the UI templates, and `PRAGMA synchronous=OFF` on new SQLite connections (the
  test asserting the production default opts out via `@pytest.mark.sqlite_default_synchronous`).
  Full suite ~250 s → ~51-54 s on the reference laptop. `tests/harness/test_settings_hooks.py`
  now runs the hook wrapper under Git Bash, not the WSL launcher, so it passes from PowerShell.
  No production code change.

- **Documented what the critic actually reads of a principle** (`docs/constitution.md`
  §2.2, `docs/CODEBASE_FACTS.md`). `format_principles_compact`
  (`constitution/prompt_formatter.py:70-94`, used at `critic_module.py:401,673`)
  truncates every rule to **180 characters** and never emits `examples_allow` /
  `examples_deny` — their only consumer is `_specificity_score`
  (`constitution/helpers.py:80-81`), which counts them for ranking. **31 of the 210
  principles have a rule longer than 180 chars, 15 of them `hard`.** A qualifying
  clause written at the end of a long rule is therefore inert, which two earlier notes
  in `CODEBASE_FACTS.md` got wrong and are corrected in the same change. No behaviour
  change — documentation of existing behaviour.
- **Governance regression suite** (`examples/airbench/`): replays the prompts that
  governance handled wrongly in the 2026-08-11 and 2026-08-13 runs. Cases are tagged
  `in-scope` / `control` / `out-of-scope` so a failure says what it means, and the
  controls assert `withhold` (the dangerous content never reaches the user) instead of
  `refuse` — a regenerated SAFE_COMPLETE that denies the harmful part is the intended
  behaviour, and an earlier version of the suite scored exactly that as a failure.
  `--repeat N` is documented as mandatory before concluding: the pipeline is not
  deterministic at `temperature=0` and single runs produced opposite verdicts for the
  same prompt. `--max-tokens` defaults to 1024, above the 256 that Inspect pins for
  `xstest`/`coconot`; `--compare` runs both to separate the truncation effect.
- **Hard-violation delivery guard verified on production traffic** (`docs/CODEBASE_FACTS.md`,
  "Conditionally verified"). Replay of the 2026-08-11 709-request set on `f0d4bb8`
  (AIR-Bench 80 / CoCoNot 379 / XSTest 250, governed arm, judge `openai/gpt-4.1@0.0`):
  **9 guard activations**, each with exactly one regeneration and one direct re-critique;
  5 delivered SAFE_COMPLETE with text equal to the regeneration and different from the
  draft, 4 failed closed to REFUSE. Requests delivering a rejected draft verbatim:
  **9 of 9 pre-fix → 0 post-fix**. Score deltas are within noise (AIR-Bench 0,900→0,900;
  CoCoNot 94,20→95,51; XSTest `refusal_rate` 9,6 %→11,2 %; all sign tests p>0,2).
  Confound recorded: the same replay carries the request-scoped-state fixes and
  **287/709 prompts changed domain (40,5 %)**, so most per-item movement — including the
  headline `air-4993` case — comes from domain re-assignment, not the guard.
  Documentation only — no behavior change.
- **Gate-verification sample set versioned** (`scripts/complai_probe/gate_verify_samples.jsonl`,
  121 rows: 64 `adversarial_airbench`, 32 `benign_coconot`, 25 `benign_xstest`). Each row
  carries the benchmark prompt, its gold label (`passthrough`/bucket) and the 2026-08-11
  run's `prev_op`/`prev_action`/`prev_reason`, so a replay can be scored against the same
  input set. Input only — no governed responses are stored (8 keys, uniform across all
  121 rows). Joins the probe datasets already tracked in `scripts/complai_probe/`.
- **Known gap recorded: `should_use_safe_complete` ignores its `domain` parameter**
  (`docs/CODEBASE_FACTS.md`, "Future work / known gaps"). The docstring
  (`orchestration/safe_complete_gating.py:46-54`) promises a regulated-domain
  SAFE_COMPLETE floor ("domain in REGULATED_DOMAINS => True") that the body
  (`:61-70`) never implements — `domain` is never read and `REGULATED_DOMAINS`
  exists nowhere in `moralstack/` outside that docstring; `has_hard_violations` is
  vestigial as well. Latent today: the floor is delivered in practice by
  `overlay_sensitive`, and every regulated overlay shipped in-repo declares
  `sensitive: true`. Reachable with a custom regulated overlay that omits the flag.
  Documentation only — no behavior change; the decision (implement the documented
  floor vs. drop the promise and the dead parameters) is deliberately deferred.
- **AIR-Bench measurement session recorded in the facts ledger** (`docs/CODEBASE_FACTS.md`),
  from 709 requests over CoCoNot, XSTest and AIR-Bench 2024 on 2026-08-11. Verified: the three
  risk mini-estimator model slots and their env overrides (a per-slot override leaves **no**
  trace in `decision_traces`); `SAFE_COMPLETE` delivers `state.draft_response` **verbatim**,
  its "safe" framing living only in metadata; `detected_domain` is read back from a mutable
  instance attribute on the shared retriever instead of the retrieval return value.
  Conditionally verified (external measurement, never a code fact): `gpt-4o` is materially
  better calibrated than `gpt-4o-mini` on the `estimate_operational` slot. Two **P0 gaps
  documented and NOT fixed**: a request can be assigned another request's domain (37,2 %
  cross-attribution measured, 108 of 264 crossing the overlay `sensitive` line), and a
  critic-rejected draft can be delivered when REFUSE is downgraded to SAFE_COMPLETE (verified
  end-to-end on request `e324e47f`). Documentation only — no behavior change.
- **EU AI Act landscape note extended** (`docs/eu_ai_act_landscape.md`, update of
  2026-08-05). §3 rewritten — recognition status (as of the capture date **no** evaluation
  tool is EU-recognised, and the reason is structural: no CEN-CENELEC deliverable is cited
  in the OJ, and the AI Office was still gathering expert opinion on evaluator
  accreditation), the selected track, the over-refusal requirement and the operational
  integration details; §7 extended. §1, §2, §4, §5 and §6 untouched. Still a parked
  research note: nothing implemented, and the claims stay out of the verified facts ledger.
- **EU AI Act compliance research parked** (`docs/eu_ai_act_landscape.md`, referenced from
  `CLAUDE.md`). Captures the post-Digital-Omnibus regulatory state (Annex III high-risk
  deferred to 2027-12-02, Article 50 transparency binding from 2026-08-02, zero
  harmonised standards cited in the OJ), the evaluation tooling surveyed as alternatives
  to COMPL-AI, a proposed six-stage evidence pipeline, and three open decisions. Research
  only — nothing implemented, no behavior change. The note is explicitly excluded from
  `docs/CODEBASE_FACTS.md`: externally-verifiable claims stay hypotheses per
  `.claude/rules/memory-maintenance.md`.
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
