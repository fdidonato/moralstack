# Cursor CLI Handoff -- Remove deprecated moralstack/persistence/ package

## Context

moralstack/persistence/ is a deprecated wrapper/re-export package
(moralstack/persistence/__init__.py:1-16 emits DeprecationWarning at
import time and says "use moralstack.observability instead"). Three of its
nine files (port.py, default.py, null.py) contain a real
Protocol+implementation dependency-injection pattern with no equivalent in
moralstack/observability/ and must be relocated, not deleted. Two files
(sink.py, write_queue.py) contain persist_*/async_persist_* helper
functions that build EventEnvelopes and call get_obs().emit() -- also with
no equivalent elsewhere -- and must be relocated into a new
moralstack/observability/emit_helpers.py. The remaining files
(config.py, context.py, db.py) are pure re-export aliases and are
deleted with no replacement (their one non-alias detail -- db.py's own
_SqliteReadStore() instance backing standalone get_* read functions -- has
zero production consumers, only tests, per the plan's verified evidence).

This is a pure import-path relocation. No behavior, no signature, no
try/except, no log text changes except where the plan explicitly says a
logger name changes as an accepted side effect of moving code
(moralstack.persistence.default -> moralstack.orchestration.default_persistence;
moralstack.persistence.sink -> moralstack.observability.emit_helpers).

- Approved plan (source of truth, read in full before starting):
  ai/plans/remove-deprecated-persistence-package.md
- Final Codex plan review (round 3, APPROVE_WITH_CHANGES, zero blocking
  issues -- all non-blocking fixes from all 3 rounds are already folded into
  the plan text; see the plan's own "## 14. Review history" section for the
  full log):
  ai/reviews/codex-plan-review-remove-deprecated-persistence-package-20260703-174806.md
- Prior (superseded) rounds, kept for audit trail only, do not re-litigate
  their already-resolved blocking issues:
  ai/reviews/codex-plan-review-remove-deprecated-persistence-package-20260703-171703.md (round 1, BLOCK)
  ai/reviews/codex-plan-review-remove-deprecated-persistence-package-20260703-174143.md (round 2, BLOCK)

## CRITICAL -- the working tree is NOT clean. Read this before touching anything.

git status right now shows ~40 already-modified tracked files and dozens of
untracked files, unrelated to this task -- they belong to a separate,
still-in-progress task ("token-accounting-p0-2b-p11") that has not been
committed yet. HEAD is dd381d2 ("refactoring AI Harness"); none of the
pending token-accounting work is committed.

This matters because some files this plan needs to edit already have
unrelated pending edits mixed in, specifically (verified against the
current working tree before this handoff was written):
moralstack/orchestration/controller.py, moralstack/server/proxy.py,
moralstack/orchestration/deliberation_runner.py,
moralstack/orchestration/final_revalidation.py,
moralstack/runtime/modules/critic_module.py,
moralstack/runtime/modules/hindsight_module.py,
moralstack/runtime/modules/perspective_module.py,
moralstack/runtime/modules/simulator_module.py,
moralstack/persistence/db.py, moralstack/persistence/write_queue.py,
docs/modules/persistence.md.

Rule: when editing any of the above files, touch ONLY the lines this plan
requires (import statements, or the specific persistence-related content the
plan calls out). Do NOT revert, "clean up", or otherwise touch the unrelated
pending token-accounting hunks already present in those files -- they are a
different task's in-flight work and are out of scope here. If you are
unsure whether a hunk in one of these files is related to this task, leave it
untouched and note the ambiguity in your final report instead of guessing.

Do not run git checkout --, git stash, git clean, or any command that
would discard the pending token-accounting changes.

## Objective

Single outcome: moralstack/persistence/ no longer exists in the repo.
PersistencePort/DefaultPersistence/NullPersistence live in
moralstack/orchestration/ as three new files mirroring the existing
EventEmitter/DefaultEventEmitter split. The persist_*/async_persist_*
helper functions live in one new file,
moralstack/observability/emit_helpers.py. Every caller (production,
scripts, tests) imports from the new locations. .claude/rules/observability.md
frontmatter covers the new files. Docs and CHANGELOG reflect the removal. Full
test suite passes.

## Approved plan (read this file in full first)

ai/plans/remove-deprecated-persistence-package.md -- in particular:
- Section 6 "Design" (Decisions 1 and 2, exact new-file contents, execution order)
- Section 7 "Tests to add / modify" (the 19-file table in 7.1, the new tests in
  7.2/7.3, edge cases in 7.4, exact commands in 7.6)
- Section 8 "Files to modify" (this handoff's allowed-file list is derived directly
  from it -- if in doubt, the plan's section 8/9 wording is authoritative over this
  handoff's summary)
- Section 9 "Docs to update"
- Section 10 "Risks"
- Section 11 "Acceptance criteria"
- Section 12 "Checklist implementazione (ordine)" -- follow this order

## Files allowed to modify

New files to create:
- moralstack/orchestration/persistence_port.py
- moralstack/orchestration/default_persistence.py
- moralstack/orchestration/null_persistence.py
- moralstack/observability/emit_helpers.py
- tests/test_orchestrator_default_persistence_e2e.py
- tests/test_orchestrator_default_persistence_failure_does_not_break_request.py
- tests/test_persistence_sink_contract.py
- tests/test_benchmark_moralstack_imports.py
- tests/test_orchestration_persistence_port.py
- tests/test_observability_config.py (this is test_persistence_config.py
  renamed -- content otherwise unchanged except the import path; delete the
  old file after the rename)

Files/directories to delete (only after every call site listed below is
updated and the targeted test run in section 7.6 is green):
- moralstack/persistence/ -- the entire directory (__init__.py,
  config.py, context.py, db.py, default.py, null.py, port.py,
  sink.py, write_queue.py)
- docs/modules/persistence.md
- tests/test_persistence_config.py (after rename above)

Existing files to edit -- import statement only, no logic change (several
of these already carry unrelated pending edits -- see the CRITICAL section
above, touch only the persistence-related lines):
- moralstack/orchestration/controller.py -- import at approx line 110-111 and the
  LAZY import inside a function at approx line 1136. Keep the import at
  approx 1136 lazy/local -- do NOT promote it to a module-level import, or the
  4 tests that patch moralstack.persistence.sink.persist_orchestration_event
  will silently stop intercepting the call (see plan section 7.1, section 10).
- moralstack/runtime/orchestrator.py (approx line 48, approx 181)
- moralstack/server/proxy.py (approx line 48)
- moralstack/constitution/retriever.py (approx line 24)
- moralstack/orchestration/final_revalidation.py (approx line 27)
- moralstack/orchestration/deliberation_runner.py (approx line 74)
- moralstack/orchestration/default_event_emitter.py (approx line 8)
- moralstack/orchestration/persistence_helpers.py (approx lines 12-15)
- moralstack/orchestration/diagnostics.py (approx line 121, lazy import -- keep lazy)
- moralstack/runtime/modules/critic_module.py (lazy import, approx line 527 -- keep lazy)
- moralstack/runtime/modules/hindsight_module.py (lazy import, approx line 802 -- keep lazy)
- moralstack/runtime/modules/perspective_module.py (lazy import, approx line 774 -- keep lazy)
- moralstack/runtime/modules/simulator_module.py (lazy imports, approx lines 477, 592 -- keep lazy)
- scripts/benchmark_moralstack.py (approx lines 2456-2458, 2615, 2634, 2751 -- these
  are inside lazy execution branches, keep lazy)
- moralstack/observability/context.py -- module docstring only (approx line 5,
  currently says "Migrated from moralstack.persistence.context"; update so it
  no longer references a module that will not exist)

Rule config file to edit (frontmatter only):
- .claude/rules/observability.md -- add to the paths: frontmatter list:
  moralstack/orchestration/default_persistence.py,
  moralstack/orchestration/persistence_port.py,
  moralstack/orchestration/null_persistence.py,
  moralstack/orchestration/persistence_helpers.py. Do not touch the rule
  body text below the frontmatter.

Existing test files -- import path only (19 files; do NOT change any
assertion unless explicitly listed below):
tests/test_persistence_load.py, tests/test_persistence_llm_calls.py,
tests/test_persistence_uow.py, tests/test_observability_write_queue.py,
tests/test_domain_prefilter_cache.py, tests/test_runtime_observability.py,
tests/test_reports.py, tests/test_report_journey_order.py,
tests/test_report_durations.py, tests/test_prompt_audit_fixes.py,
tests/test_controller_token_accounting_speculative.py,
tests/test_controller_speculative_lazy.py,
tests/test_runtime_modules_retry_token_accounting.py,
tests/test_compliance_fast_path.py,
tests/test_compliance_orchestrator_integration.py,
tests/test_observability_contract.py, tests/test_sdk_dccl.py
(test_persistence_config.py is handled via the rename above).

One test file with a content change beyond the import (plan section 7.1):
- tests/test_observability_read_store_token_usage.py -- remove the
  persistence_db import and the two callable(persistence_db.get_token_usage_totals/breakdown)
  assertions (lines approx 122-126 per the plan's evidence -- verify against the
  actual current file, which already has unrelated pending token-accounting
  edits, before assuming the exact line numbers). Leave the rest of the file
  (which tests SqliteReadStore directly) untouched.

For the 4 "coupled to lazy import" test files, verify the new patch target
explicitly with call_count, not just absence of exceptions (plan section 7.1,
section 10): tests/test_compliance_fast_path.py,
tests/test_compliance_orchestrator_integration.py,
tests/test_observability_contract.py, tests/test_sdk_dccl.py -- new patch
target is moralstack.observability.emit_helpers.persist_orchestration_event.

Docs to update (PROJECT_SPEC section 8 -- same change, not a follow-up):
- docs/modules/observability.md -- remove/historize "Migration from
  persistence" section (approx lines 445-459), fix the pre-existing self-referential
  wrong line 458, add emit_helpers.py to the module tree and responsibility
  table (approx lines 11-25, 31-40).
- docs/MORALSTACK_CODEBASE_INDEX.md -- remove the persistence/ module map
  entry (approx line 29) and the "### Persistence" section (approx lines 204-209);
  add the new locations of PersistencePort/DefaultPersistence/NullPersistence
  (orchestration section) and emit_helpers.py (observability section);
  update the test_persistence_*.py mention (approx line 563).
- README.md (approx line 119) -- remove the moralstack/persistence/ bullet from
  the package list.
- docs/modules/README.md (approx line 50) -- remove the table row linking to
  ./persistence.md.
- docs/modules/benchmark.md (approx line 110) -- the link
  [Persistence](./persistence.md) points at a file this plan deletes;
  repoint it to docs/modules/observability.md.
- docs/CODEBASE_FACTS.md -- update any fact that names
  moralstack/persistence/* as a living module; add to the
  hypotheses/known-defects section (do NOT fix the defect itself, out of
  scope): request.get_domain() == "core" is persisted unnormalized on the
  initial upsert (controller.py:2078-2086 -> default.py:68-75, or their new
  paths post-move) because _normalize_runtime_domain only runs in the
  post-risk-assessment block (controller.py:2267-2274), which skips the
  update_request_domain call entirely when the newly-detected domain is
  None -- so it never overwrites a "core" value already written by the
  initial upsert.
- docs/traces/observability_db_to_ui.md and
  docs/traces/governance_decision_flow.md (approx lines 7-8, 45-47) -- replace
  persistence.set_request_context(...) references with the new
  moralstack/orchestration/ path.
- docs/architecture_spec.md (approx line 1250) -- replace "from
  moralstack.persistence.context" with "from
  moralstack.observability.context".
- CHANGELOG.md -- add a breaking-change entry for the removal of the public
  deprecated moralstack.persistence package. Must explicitly cover: (a)
  PersistenceWriteQueue/get_write_queue removed with zero replacement
  (dead code, zero consumers, but were public API); (b) logger name changes
  (moralstack.persistence.default/moralstack.persistence.sink to new
  module names) for anyone with external log filters; (c) the entire package
  and all submodules are removed with NO per-symbol compatibility alias,
  including PersistMode (alias of ObservabilityMode) and the standalone
  read functions in old db.py (get_token_usage_totals,
  get_token_usage_breakdown, get_runs_page, get_request_domains,
  get_models_used_for_run) which have no production consumer and no
  replacement module -- anyone using them must instantiate SqliteReadStore()
  directly. State explicitly that the only supported import path for the
  relocated helpers is moralstack.observability.emit_helpers (submodule
  import) -- moralstack/observability/__init__.py is NOT being changed to
  re-export them (see "Files NOT to modify" below).

## Files NOT to modify

- docs/refactoring_diary.md -- historical, dated record of past
  refactorings; leave untouched even though it mentions old
  moralstack/persistence/* paths (explicit decision, plan section 14 round 3).
- moralstack/observability/__init__.py -- explicit decision (plan section 9, section 14
  round 3): the relocated helpers are NOT re-exported from the top-level
  observability package; the only supported path is the
  emit_helpers submodule. Do not add anything to this file's __all__.
- pyproject.toml -- no packaging change needed (wildcard include =
  ["moralstack*"] already covers everything; plan section 11 explicitly verifies
  this). Note this file already carries unrelated pending edits from another
  task -- do not touch it at all for this task.
- Any file not named above. In particular, do NOT touch these files that are
  part of the unrelated in-flight token-accounting work currently sitting
  uncommitted in the working tree: moralstack/models/base.py,
  moralstack/models/policy.py, moralstack/observability/events.py,
  moralstack/observability/read_store.py,
  moralstack/observability/service.py,
  moralstack/observability/sinks/sqlite_sink.py,
  moralstack/orchestration/embedder.py,
  moralstack/orchestration/refusal_handler.py,
  moralstack/orchestration/response_assembler.py,
  moralstack/orchestration/safe_refusal_generator.py,
  moralstack/orchestration/speculative_overlap.py,
  moralstack/orchestration/types.py, moralstack/sdk/response.py,
  requirements.txt, tests/test_constitution_retrieval_persistence.py,
  tests/test_local_embedder.py, tests/test_observability_envelope.py,
  tests/test_report_version_dynamic.py,
  tests/test_safe_refusal_generator.py, tests/test_sdk_bootstrap.py,
  tests/test_speculative_overlap.py, moralstack/observability/token_usage.py,
  moralstack/observability/request_token_accumulator.py, any
  tests/test_*token*.py file not listed above, any file under
  .claude/commands/, .claude/agents/, docs/ai/, scripts/ai/ (other
  than the harness script that launched you, which you should not touch
  either), and the untracked repo-root notes
  ANALISI_TECNICA_MORALSTACK.md, claude_upgrade_plan.md,
  codex_upgrade_plan.md.
- Do not git add, commit, push, stash, or discard any change -- yours or
  pre-existing.

## Invariants at stake (PROJECT_SPEC.md section 5)

1. Invariant 6 -- "Observability never breaks the request" (P0).
   .claude/rules/observability.md. DefaultPersistence.ensure_run_and_upsert_request/
   update_request_domain already wrap everything in try/except Exception
   with logger.warning, never raise. This must be preserved character
   for character when moved to orchestration/default_persistence.py. Same
   constraint for every persist_*/async_persist_* function moved into
   emit_helpers.py. Do NOT add a new try/except to the async helpers to
   "fix" the sync/async asymmetry described below -- that would be an
   out-of-scope behavior change.
   - Known, accepted, pre-existing asymmetry (do not "fix" it): sync
     helpers in old sink.py each have their own local try/except around
     get_obs().emit(); async helpers in old write_queue.py have NO
     local try/except -- their "never raise" behavior today depends entirely
     on ObservabilityService.emit() being internally safe
     (observability/service.py:44-64). Preserve this asymmetry exactly as
     the new characterization test (test_persistence_sink_contract.py)
     documents it.
2. Invariant 7 -- Governed delivery. .claude/rules/governed-delivery.md.
   moralstack/server/proxy.py emits governed-delivery audit markers via
   persist_orchestration_event (approx lines 48, 427-462). The import move must
   not change this logic. The existing end-to-end test
   tests/test_server_proxy.py::TestObservabilityPersistence::test_proxy_output_finalized_event_persisted
   (lines approx 1216-1270) is the regression gate for this -- it must still pass
   unmodified after the import relocation.
3. Constitution-domains (.claude/rules/constitution-domains.md) --
   relevant only as a read constraint: do NOT fix the pre-existing "core"
   domain persistence gap described above. It is out of scope; only document
   it in docs/CODEBASE_FACTS.md as instructed above.
4. No other section 5 invariant is touched: this is not decision policy (the
   persist_* functions are telemetry/audit-trail side effects, they never
   influence final_action), not prompt transparency, not hard-signal
   supremacy.

## Checklist (follow this order -- plan section 12)

1. Create the 4 new files (orchestration/persistence_port.py,
   orchestration/default_persistence.py, orchestration/null_persistence.py,
   observability/emit_helpers.py) and update .claude/rules/observability.md
   frontmatter. The old moralstack/persistence/ package stays intact at this
   step -- nothing should break yet.
2. Write the new characterization tests (section 7.2/7.3 of the plan) against the
   current code, still importing from moralstack.persistence.*, and get
   them passing before moving anything.
3. Update production call sites one at a time (section 8 list above), re-pointing the
   characterization tests to the new path as you go.
4. Run the targeted suite (commands below) to catch the lazy-import-coupled
   patch() targets; fix each one, verifying call_count explicitly.
5. Update the 19 existing test files (import only, except the one file with
   the 2-assertion removal noted above).
6. Rename tests/test_persistence_config.py to tests/test_observability_config.py.
7. Update scripts/benchmark_moralstack.py; validate with
   tests/test_benchmark_moralstack_imports.py.
8. Once the full suite is green: delete moralstack/persistence/ and
   docs/modules/persistence.md.
9. Update the documentation listed above.
10. Run the full suite again + report results.

## Required tests (exact commands -- plan section 7.6)

Command 1:
python -m pytest tests/test_persistence_config.py tests/test_persistence_load.py tests/test_persistence_llm_calls.py tests/test_persistence_uow.py tests/test_observability_write_queue.py tests/test_observability_read_store_token_usage.py tests/test_runtime_observability.py tests/test_domain_prefilter_cache.py -v

Command 2:
python -m pytest tests/test_compliance_fast_path.py tests/test_compliance_orchestrator_integration.py tests/test_observability_contract.py tests/test_sdk_dccl.py tests/test_runtime_modules_retry_token_accounting.py -v

Command 3:
python -m pytest "tests/test_server_proxy.py::TestObservabilityPersistence::test_proxy_output_finalized_event_persisted" -v

Command 4:
python -m pytest tests/test_orchestrator_default_persistence_e2e.py tests/test_orchestrator_default_persistence_failure_does_not_break_request.py tests/test_persistence_sink_contract.py tests/test_benchmark_moralstack_imports.py tests/test_orchestration_persistence_port.py -v

Command 5 (full suite):
python -m pytest -q

Note: commands 1-2 use the OLD tests/test_persistence_config.py path -- by
the time you reach the final full-suite run it will have been renamed to
tests/test_observability_config.py per checklist step 6; run the renamed
file if the old one no longer exists at that point.

Do not run "pre-commit run -a" (memory note: HEAD is not pre-commit-clean,
-a churns unrelated files). If you run pre-commit at all, scope it with
--files <list of files you actually touched>. This is optional -- not
required for this handoff's acceptance; the Cursor Implementation
Coordinator's post-run verification does not depend on it.

## Acceptance criteria (plan section 11)

- moralstack/persistence/ does not exist anywhere in the repository.
- Broadened grep (misses markdown links like ./persistence.md if using the
  narrower "moralstack.persistence" pattern alone) produces no hits except
  historical notes in CHANGELOG.md and docs/refactoring_diary.md. Run:
  grep -rE "moralstack[./]persistence|persistence\.md|PersistMode" moralstack/ scripts/ tests/ docs/
- PersistencePort, DefaultPersistence, NullPersistence exist in
  moralstack/orchestration/ with identical behavior (same try/except, same
  logging), verified by the new tests and the updated existing tests.
- emit_helpers.py in moralstack/observability/ exposes the same 11
  functions with identical signatures.
- tests/test_orchestrator_default_persistence_e2e.py and
  tests/test_orchestrator_default_persistence_failure_does_not_break_request.py
  pass.
- python -m pytest -q (full suite) passes -- zero tests skipped or deleted to
  work around the change, aside from the justified rename of
  test_persistence_config.py and the targeted 2-assertion removal in
  test_observability_read_store_token_usage.py.
- pyproject.toml requires no change (wildcard packaging already covers it --
  do not touch this file).
- Documentation no longer references moralstack/persistence/ as a living
  module, including README.md, docs/modules/README.md,
  docs/architecture_spec.md, docs/modules/benchmark.md.
- .claude/rules/observability.md frontmatter paths: covers the new
  default_persistence.py/persistence_port.py/null_persistence.py/
  persistence_helpers.py locations.
- The pre-existing "core" domain gap is documented in
  docs/CODEBASE_FACTS.md as a known defect, not fixed.
- No behavior change in the "observability never breaks the request"
  invariant -- the moved try/except blocks are byte-identical.

## Risks (plan section 10)

- Lazy-import-coupled test patches: 4 test files patch
  moralstack.persistence.sink.persist_orchestration_event relying on the
  lazy import inside controller.py. If the new patch target
  (moralstack.observability.emit_helpers.persist_orchestration_event) is not
  updated in lockstep, the mock silently stops intercepting (no error, just
  wrong call_count or an unpatched real call) -- verify call_count
  explicitly, not just "no exception raised".
- Untested script: scripts/benchmark_moralstack.py has no pytest
  coverage; a broken import there would not be caught by the suite -- mitigated
  by the new test_benchmark_moralstack_imports.py, which must actually
  execute the lazy branches containing the persistence imports, not just
  "import scripts.benchmark_moralstack" at module level.
- Blast radius: approx 14 production/script files + 19 existing test files + 5
  new test files + approx 10 doc files. High file count, low behavioral risk (pure
  import-path change) -- but an overlooked import is a real risk, catchable
  early with "python -m pytest --collect-only" before running anything.
- External consumers outside this repo: unverifiable from code; mitigated
  by the CHANGELOG entry, not by keeping any compatibility shim.
- PersistenceWriteQueue/get_write_queue are public API, not just dead
  internal code -- re-exported in the old __init__.py's __all__, and the
  wildcard packaging includes them in the distributed package. Zero internal
  consumers confirmed, but this is still a breaking change for any external
  importer -- must be called out explicitly in CHANGELOG.md.
- The working tree is dirty with unrelated pending work (see CRITICAL
  section above) -- the single highest risk for this handoff specifically is
  Cursor CLI touching or reverting hunks that belong to the other in-flight
  task while editing a shared file. Re-read the CRITICAL section before
  editing any file in that overlap list.

## Ready prompt for Cursor CLI

You are Cursor CLI (cursor-agent), running headless as the implementer for
MoralStack. Read this entire handoff file first, then read
ai/plans/remove-deprecated-persistence-package.md in full (it is the
authoritative source -- this handoff summarizes it but the plan's exact
wording wins on any discrepancy). Then read
ai/reviews/codex-plan-review-remove-deprecated-persistence-package-20260703-174806.md
for the final review context.

The working tree already contains unrelated uncommitted changes from a
different, still in-progress task. Do not touch, revert, or "clean up" any
hunk in a shared file that is not directly about the persistence-package
import relocation -- see the CRITICAL section above for the exact list of
affected files. When in doubt about whether a piece of code in a shared file
is related to your task, leave it alone.

Implement exactly and only what this handoff and the plan approve, following
the checklist order in "## Checklist" above (which mirrors plan section 12).
Modify only the files in "Files allowed to modify". Do not touch anything in
"Files NOT to modify". Do not weaken, skip, or delete any test -- the two
named exceptions (rename of test_persistence_config.py, and the 2-assertion
removal in test_observability_read_store_token_usage.py) are the only
sanctioned test-content changes; all other test edits are import-path only.
Preserve every invariant listed under "Invariants at stake" -- in particular,
the try/except blocks realizing "observability never breaks the request"
must be moved byte-for-byte identical, and the sync/async asymmetry in error
handling must be preserved, not "fixed".

Run the exact test commands under "Required tests" and report their real
output. If you hit an ambiguity in the plan, or a blocking architectural
problem, or you are unsure whether a hunk in a shared file belongs to this
task or the other in-flight one, STOP and report it instead of guessing.

Do not git add, commit, push, stash, or discard any file.

## Output required from Cursor CLI

At the end of the run, report:
- Files modified (full list, absolute or repo-relative paths) -- cross-checked
  against "Files allowed to modify" above.
- Tests added (list, with a one-line description of what each covers).
- Every command run, verbatim, with its real result (pass/fail counts, not
  paraphrased).
- Deviations from the plan (if any), with justification.
- Whether any hunk belonging to the unrelated in-flight token-accounting work
  was touched, and if so, exactly what and why.
- Residual problems / open questions / anything you could not verify.
