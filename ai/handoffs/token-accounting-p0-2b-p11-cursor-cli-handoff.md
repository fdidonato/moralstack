# Cursor CLI Implementation Handoff — token-accounting-p0-2b-p11

Built on `ai/prompts/cursor-cli-implementation-template.md`. Read that file's
rules first — non-negotiable (allowed-files only, no scope creep, no
opportunistic refactors, no weakening/skipping tests, honor PROJECT_SPEC §5
invariants, report real command output only, stop and report on ambiguity or
blocking problems, never git add/commit/push).

---

## IMPORTANT PROCESS NOTICE — read before implementing

The standard precondition for this command ("no unresolved Codex BLOCKING
items") is **not formally satisfied**. History:

- This plan went through **7 full rounds** of Codex plan review
  (`ai/reviews/codex-plan-review-token-accounting-p0-2b-p11-*.md`, from
  `20260701-144844` to `20260702-120013`), **all verdict BLOCK**, resolved
  progressively (see the plan's "Revision log", plan lines 12-196).
- The **7th review** (`ai/reviews/codex-plan-review-token-accounting-p0-2b-p11-20260702-120013.md`)
  found **2 BLOCKING issues caused by an implementation error in the
  planner's own v7 snippet** for the systemic fix to
  `_policy_llm_model_for_action()`/`_module_model()`
  (`moralstack/orchestration/deliberation_runner.py:200-226`): the v7
  snippet **replaced the entire body** of those 2 functions instead of
  normalizing only the "no model available" boundary, **losing real
  business logic**: the `rewrite_model` branch (`action == "rewrite"`) in
  `_policy_llm_model_for_action`, and the two-level lookup
  `module.policy.model` -> `module.model` in `_module_model`.
- This was corrected in the plan as **"v8"** (plan Revision log entry `v8`,
  lines 176-194, and the dedicated section "Correzione dopo il settimo
  verdetto BLOCK (v8, PROJECT_SPEC §9)", lines 3360-3379) with a surgical
  fix verified against the real code (plan lines 2211-2301): **only** the 2
  final `return None` per function become `return ""`; signature, `action`
  parameter, `rewrite_model` branch, and the two-level lookup are all
  **preserved unchanged**.
- **An 8th Codex review was attempted but failed due to a Codex account
  rate limit** (not a BLOCK verdict) — no verdict was ever produced for v8.
  Only two generated request prompts exist
  (`ai/prompts/generated-codex-plan-review-token-accounting-p0-2b-p11-20260702-121626.md`,
  `...-121931.md`, 4278 lines each, matching the full plan length) with
  **no** corresponding output in `ai/reviews/` — confirming the review never
  ran.
- **The user has explicitly chosen to proceed to implementation now**,
  without waiting for the 8th review.

**Consequence for you (Cursor CLI)**: the v8 design change (fix to
`_policy_llm_model_for_action`/`_module_model`) has **no independent Codex
confirmation**. Give it extra care in implementation and testing —
specifically the two acceptance criteria dedicated to it (plan lines
3360-3379), which protect the `rewrite_model` branch and the
`module.policy.model` lookup. The exact required diff is reproduced below in
"Design — the v8-corrected helper functions". If the real current body of
these two functions no longer matches what's shown below, **STOP and report
the discrepancy** — re-read the real function bodies first and adapt the
mechanical diff (only the 2 `return None` -> `return ""` per function) to
whatever the real code actually contains; do not guess.

---

## Context

MoralStack's token accounting is incomplete and non-aggregatable
(`codex_upgrade_plan.md` P0-2b, `claude_upgrade_plan.md` P11). The proxy
hardcodes `usage: {0,0,0}`; several LLM call sites (refusal generation,
`constitution_retriever`, embedder, retry attempts, quick-check fast paths,
seeded simulator runs, individual hindsight evaluations) never persist token
usage; cache-hit results get re-billed as new calls; there is no canonical
`TokenUsage` type or per-request/per-module aggregation.

- Approved plan (READ IN FULL, 4219 lines): `ai/plans/token-accounting-p0-2b-p11.md` (v8).
- Latest Codex review with an actual verdict (7th round, confirms all prior
  BLOCKING items resolved except the 2 fixed in v8):
  `ai/reviews/codex-plan-review-token-accounting-p0-2b-p11-20260702-120013.md`.
- Revision history / product decisions: plan lines 12-311.

## Objective

Implement the full plan: canonical `TokenUsage` type (exact/estimated/
missing/unknown), numeric token columns on `llm_calls` plus a new
best-effort `request_token_usage` summary table, a `billable_provider_call`
discriminator, complete token capture across every LLM call site, a real
`usage` field in the OpenAI-compatible proxy response, and mirrored
`GovernanceMetadata` SDK fields — all while respecting PROJECT_SPEC §5.6
(observability never breaks the request) and touching no other P0
invariant.

## Approved plan — section map (plan file itself is authoritative; this is
just a navigation aid)

- Lines 1-311: Revision log (v1->v8) + Product decisions — read first.
- Lines 314-490: Goal / Current behavior (file:line evidence) / Target
  behavior / Assumptions / Constraints.
- Lines 491-2431: Design, Decisions 1-8 (technical design + Decision 8 =
  every BLOCKING fix from all 7 review rounds, v8 correction at 2211-2301).
- Lines 2305-2431: Decisione 7 — recommended PR sequence (PR1..PR21) + exact
  target SQL queries.
- Lines 2433-2461: Alternatives considered and rejected — do not reintroduce.
- Lines 2462-2776: Files to modify (base + deltas per BLOCK round v2-v8).
- Lines 2777-3094: Risks (R1-R25ish).
- Lines 3095-3380: Acceptance criteria (base + deltas per round; v8 criteria
  at 3360-3379).
- Lines 3381-4037: Tests to add/modify — exact test names + exact pytest
  commands + explicit non-regression guardrails.
- Lines 4038-4109: Implementation checklist — the order to follow (mirrors
  PR1-PR21).
- Lines 4110-4151: Rollback plan (informational).
- Lines 4154-4163: PROJECT_SPEC §5 invariants touched (only §5.6).

### Design — the v8-corrected helper functions (do not re-derive, use this)

Reproduced verbatim so there is no ambiguity. Current real code,
`moralstack/orchestration/deliberation_runner.py:200-226` (**verify this is
still accurate against the actual file before editing** — if it has
drifted, stop and report):

```python
def _policy_llm_model_for_action(policy: Any, action: str) -> str | None:
    """Effective OpenAI model name for policy generate vs rewrite (rewrite may use MORALSTACK_POLICY_REWRITE_MODEL)."""
    if policy is None:
        return None
    if action == "rewrite":
        rw = getattr(policy, "rewrite_model", None)
        if rw is not None:
            return str(rw)
    m = getattr(policy, "model", None)
    return str(m) if m is not None else None


def _module_model(module: Any) -> str | None:
    """Return the OpenAI model name used by a cognitive module (critic, simulator, ...).

    Each module stores its inner ``OpenAIPolicy`` as ``self.policy``; fall back
    to ``module.model`` if present.
    """
    if module is None:
        return None
    inner = getattr(module, "policy", None)
    if inner is not None:
        m = getattr(inner, "model", None)
        if m is not None:
            return str(m)
    m = getattr(module, "model", None)
    return str(m) if m is not None else None
```

Required fix — **only** change the 2 `return None` per function to
`return ""`, and the return-type annotation from `str | None` to `str`.
Everything else (signature, `action` param, `rewrite_model` branch,
two-level `module.policy.model` -> `module.model` lookup) stays
**byte-identical**:

```python
def _policy_llm_model_for_action(policy: Any, action: str) -> str:
    """Effective OpenAI model name for policy generate vs rewrite (rewrite may use MORALSTACK_POLICY_REWRITE_MODEL)."""
    if policy is None:
        return ""  # era: return None
    if action == "rewrite":
        rw = getattr(policy, "rewrite_model", None)
        if rw is not None:
            return str(rw)
    m = getattr(policy, "model", None)
    return str(m) if m is not None else ""  # era: else None


def _module_model(module: Any) -> str:
    """Return the OpenAI model name used by a cognitive module (critic, simulator, ...).

    Each module stores its inner ``OpenAIPolicy`` as ``self.policy``; fall back
    to ``module.model`` if present.
    """
    if module is None:
        return ""  # era: return None
    inner = getattr(module, "policy", None)
    if inner is not None:
        m = getattr(inner, "model", None)
        if m is not None:
            return str(m)
    m = getattr(module, "model", None)
    return str(m) if m is not None else ""  # era: else None
```

No call site among the ~13 billable call sites consuming these functions'
return value needs its own edit — they already pass the return value
through as `model` in the persisted payload. After changing the return
annotation from `str | None` to `str`, verify with `mypy --strict` that no
downstream variable typed `str | None` needs adjusting
(`moralstack.orchestration.*` is under strict mypy,
`pyproject.toml:140-141`).

## Files allowed to modify

Full authoritative list: plan lines 2462-2776 ("Files to modify", base list
+ every delta added after BLOCK rounds v2-v8). Consolidated here:

### Application code

- `moralstack/observability/token_usage.py` — **NEW**
- `moralstack/observability/request_token_accumulator.py` — **NEW**
- `moralstack/observability/service.py`
- `moralstack/observability/events.py`
- `moralstack/observability/sinks/sqlite_sink.py`
- `moralstack/observability/read_store.py`
- `moralstack/models/base.py`
- `moralstack/models/policy.py`
- `moralstack/orchestration/deliberation_runner.py`
- `moralstack/constitution/retriever.py`
- `moralstack/orchestration/embedder.py`
- `moralstack/orchestration/types.py`
- `moralstack/orchestration/controller.py`
- `moralstack/server/proxy.py`
- `moralstack/sdk/response.py`
- `moralstack/orchestration/speculative_overlap.py`
- `moralstack/persistence/write_queue.py`
- `moralstack/orchestration/safe_refusal_generator.py`
- `moralstack/orchestration/response_assembler.py`
- `moralstack/orchestration/final_revalidation.py`
- `moralstack/orchestration/refusal_handler.py`
- `moralstack/runtime/modules/critic_module.py`
- `moralstack/runtime/modules/simulator_module.py`
- `moralstack/runtime/modules/hindsight_module.py`
- `moralstack/runtime/modules/perspective_module.py`
- `moralstack/persistence/db.py` (mirror new `read_store.py` methods only,
  same pattern as the existing `get_llm_calls_for_request` mirror at
  `db.py:76-77`)

### Documentation (update in the SAME change as the behavior described,
PROJECT_SPEC §8 — do not batch docs at the end)

- `docs/MORALSTACK_CODEBASE_INDEX.md`
- `docs/CODEBASE_FACTS.md`
- `docs/modules/observability.md`
- `docs/modules/persistence.md`
- `docs/TRACES/observability_db_to_ui.md`

### Tests

New test files (exact names from plan "Tests to add / modify",
lines 3381-4037):

- `tests/test_token_usage.py`
- `tests/test_request_token_accumulator.py`
- `tests/test_models_base_token_usage.py`
- `tests/test_models_policy_token_usage.py`
- `tests/test_deliberation_runner_token_usage.py`
- `tests/test_observability_sqlite_sink_token_usage.py`
- `tests/test_observability_read_store_token_usage.py`
- `tests/test_orchestrator_embedder_token_usage.py`
- `tests/test_sdk_response_token_usage.py`
- `tests/test_server_proxy_token_usage.py`
- `tests/test_token_accounting_e2e.py`
- `tests/test_refusal_handler.py`
- `tests/test_deliberation_runner_billable_provider_call.py`
- `tests/test_module_result_cache_billing.py`
- `tests/test_runtime_modules_retry_token_accounting.py`
- `tests/test_runtime_modules_token_usage_source.py`
- `tests/test_controller_token_accounting_speculative.py`

Existing test files you may **extend** (add tests only, never remove/weaken
an existing assertion — see "Existing coverage to protect" below):

- `tests/test_generation_overrides.py`
- `tests/test_orchestrator.py` (near line 1630)
- `tests/test_observability_sqlite_sink.py`
- `tests/test_observability_read_store.py`
- `tests/test_observability_service.py`
- `tests/test_embedder.py`
- `tests/test_local_embedder.py`
- `tests/test_constitution_retrieval_persistence.py`
- `tests/test_server_proxy.py`
- `tests/test_sdk_response.py`
- `tests/test_speculative_overlap.py`
- `tests/test_controller_speculative_lazy.py` (only if a genuinely new
  assertion is required — do not touch its existing assertions)
- `tests/test_perspective_module.py`
- `tests/test_safe_refusal_generator.py`
- `tests/test_response_assembler.py`
- `tests/test_final_revalidation.py`
- `tests/test_observability_contract.py`

Where the plan says "(o estendere X)" (new file OR extend existing file X),
either choice is in scope — prefer extending if it keeps the change minimal.

## Files NOT to modify

- Anything under `moralstack/` not listed above — in particular:
  wherever `final_action` is computed (decision policy), any DCCL/
  hard-signal detection code, `orchestration/delivery.py`,
  `constitution/domains*.py` — none are touched by this plan.
- `tests/test_risk_persist_batch.py` — **explicit guardrail** (plan lines
  1073-1078, 3807-3809): must break correctly if `billable_provider_call`
  leaks into the risk-estimator's local payload (out of scope). Do not
  touch it; if it fails, the field was added somewhere it shouldn't be.
- `tests/test_system_prompt_byte_equality.py`
- `tests/governance_invariants/**`
- `tests/test_decide_action.py`
- `tests/test_safe_complete_policy.py`, `tests/test_safe_complete_gating.py`,
  `tests/test_safe_complete_user_turn.py`
- `tests/test_governed_delivery.py`
- `tests/test_observability_write_queue.py`
- `tests/test_ledger.py`, `tests/test_ledger_storage.py`,
  `tests/test_ledger_fast_path_events.py`,
  `tests/test_ledger_fast_path_gate_rejected_e2e.py`,
  `tests/test_ledger_posture_symmetry.py`
- `tests/test_conversation_correlation.py`
- `PROJECT_SPEC.md`, `CLAUDE.md`, any `.claude/**`, any `.cursor/**`
- `ai/plans/**`, `ai/reviews/**`, `ai/prompts/**`, `ai/handoffs/**`
  (planning artifacts — not implementation scope; if you think you've found
  an error in the plan, STOP and report it, do not edit the plan)
- `requirements.txt` — currently modified in the working tree for an
  unrelated, uncommitted reason; do not touch or commit it.
- `ANALISI_TECNICA_MORALSTACK.md`, `claude_upgrade_plan.md`,
  `codex_upgrade_plan.md` — unrelated pre-existing untracked files.

The non-regression guardrail tests listed above exist to prove this change
did **not** touch governance decision logic. They must stay green with
**zero** modifications. If any fails after your change, the defect is in
your change (PROJECT_SPEC §7/§9) — fix the change, never the test.

## Invariants (PROJECT_SPEC §5) in play

- **§5.6 — Observability never breaks the request.** The only P0 invariant
  this plan touches, and central to the design: every new hook (`TokenUsage`
  parsing, the per-request accumulator, the `ObservabilityService.emit()`/
  `emit_batch()` hook, `_finalize_token_accounting` in `controller.py`,
  every new self-persisting call in the 4 runtime modules and the embedder)
  MUST be wrapped in a non-propagating `try/except Exception:
  logger.debug(..., exc_info=True)` (or equivalent), with no synchronous
  blocking I/O in the generation/proxy hot path. The plan gives the exact
  pattern at every site — follow it, do not broaden the except or use bare
  except.
- All other P0 invariants (§5.1 decision/generation separation, §5.2
  system-prompt transparency, §5.3 hard-signal supremacy, §5.4 single-turn
  byte-equality, §5.5 `core` retrieval-only, §5.7 governed delivery) are
  **not** touched by design — the proxy change only populates the `usage`
  field of an already-existing synthetic response payload with real numbers
  instead of a hardcoded `{0,0,0}`. If implementing this ever seems to
  require touching `final_action` computation, prompt composition,
  hard-signal handling, or the `core` overlay — **STOP**, that means you've
  misread the design.

## Checklist part A (steps 1-10)

1. Create moralstack/observability/token_usage.py with TokenUsage (Decision
   1, plan lines 493-539) plus from_generation_result/combine (Decision
   8/BLOCKING 3, lines 1087-1116) plus unit tests
   (tests/test_token_usage.py). Get to_json() nullability rule right from
   the start: result is None if and only if total_tokens == 0 AND source ==
   "missing" (BLOCKING v5-2, lines 297-311).
2. models/base.py (add token_usage_source field, rewrite
   token_usage_json()), models/policy.py (_complete() to 6-tuple, 2 call
   sites), deliberation_runner.py _token_usage_json_from_result rewritten
   to delegate to TokenUsage (Decision 8/BLOCKING 3, lines 1136-1157) plus
   matching tests. Non-regression: keep tests/test_orchestrator.py lines
   1630-1642 green without weakening it.
3. constitution/retriever.py: replace the 3 duplicated fallback blocks with
   TokenUsage.from_openai_usage(), wire token_usage_json into
   _persist_constitution_llm_call and its 3 call sites. Add regression
   test.
4. orchestration/embedder.py OpenAIEmbedder.embed(): self-contained
   instrumentation in its own try/except after a successful embed (Decision
   3, lines 651-674). LocalEmbedder/HashingEmbedder stay uninstrumented.
5. SQLite migration: numeric columns on llm_calls, new table
   request_token_usage (with usage_may_be_incomplete/incomplete_reason in
   the initial CREATE TABLE, not a later ALTER TABLE), new index, writer,
   dispatch, FK order, new event EVENT_REQUEST_TOKEN_USAGE_FINALIZED.
6. read_store.py: get_token_usage_totals/get_token_usage_breakdown
   (Protocol plus impl), filtering billable_provider_call. Mirror into
   persistence/db.py.
7. Create moralstack/observability/request_token_accumulator.py (Decision
   4, lines 676-734); hook into ObservabilityService emit/emit_batch --
   filter on billable_provider_call per envelope from the start. Key the
   accumulator by the tuple run_id and request_id, never request_id alone
   (BLOCKING 5, lines 387-424).
8. orchestration/types.py ResponseMetadata plus 8 fields; implement
   _finalize_token_accounting in controller.py, wire into
   _attach_trace_and_return. Verify byte-equality tests are not regressed.
9. server/proxy.py _build_synthetic_chat_completion plus its 2 call sites
   (Decision 5, lines 831-846): real usage on success, zeroed usage on the
   fail-closed path where no result exists.
10. sdk/response.py GovernanceMetadata plus 8 fields, mirroring
    ResponseMetadata. Use SimpleNamespace or explicit spec in test mocks,
    not bare MagicMock.

## Checklist part B (steps 11-15)

11. End-to-end integration tests (tests/test_token_accounting_e2e.py, lines
    3583-3611): full process to DB to breakdown query to proxy usage
    chain.
12. Update docs (MORALSTACK_CODEBASE_INDEX.md, CODEBASE_FACTS.md,
    docs/modules/observability.md, docs/modules/persistence.md,
    docs/TRACES/observability_db_to_ui.md). Describe request_token_usage as
    a synchronous, best-effort, potentially partial summary, never an
    authoritative total; describe the SUM llm_calls offline reconstruction
    query as the most complete source available among rows actually
    persisted, never canonical or a complete guarantee (plan lines
    391-431, 676-696, 2394-2431).
13. BLOCKING 5 checkpoint: confirm the accumulator is keyed on the tuple
    run_id and request_id (should already be true from step 7).
14. billable_provider_call discriminator (Decision 8, BLOCKING 2, inventory
    at lines 977-1078, table lines 994-1002, cache-hit paragraph lines
    1008-1046): new nullable column on llm_calls; async_persist_llm_call
    propagates it with default True; set billable_provider_call to False
    explicitly at deliberation_runner.py lines 866-887, 909-937, 2616-2639,
    2651-2674, 2718-2742, and 2907-2939 (conditional on is_skipped). Every
    query that aggregates tokens must filter on billable_provider_call in
    all 3 places. Do not modify models/risk/estimator.py local payload --
    guardrail test tests/test_risk_persist_batch.py lines 168-169 must stay
    untouched.
15. Cache-hit double-billing fix (risk R12, lines 1008-1046): add
    from_cache boolean field to SimulationResult, HindsightResult,
    EnsembleResult; set True on cache-hit return paths; read it in
    _simulate, _evaluate_hindsight, _evaluate_perspectives. Read the 7th
    Codex review non-blocking concern first in the review file dated
    20260702-120013: the cache returns the exact stored object, so
    mutating from_cache in place mutates the cached instance itself, an
    explicitly unresolved question in the plan. If in-place mutation
    causes a real bug, prefer returning a shallow copy of the cached
    dataclass instead of mutating the cached instance, and report this as
    a deviation.

## Checklist part C (steps 16-20)

16. Token provenance on runtime dataclasses (Decision 8, BLOCKING 3, lines
    1080-1157): add a token_usage_source field defaulting to "unknown" to
    CriticReport, SimulationResult (via _build_result), HindsightResult,
    PerspectiveResult, EnsembleResult (2 construction sites: parallel
    around lines 624-634 and sequential around lines 674-684 in
    perspective_module.py -- cover both, risk R10).
17. Retry-failed accounting (Product decision 6, lines 1158-1272): uniform
    pattern across the 4 retry loops (critic, simulator, hindsight,
    perspective) -- a separate llm_calls row per failed attempt, guarded so
    that only attempts where the provider actually responded produce a
    row. Fix the existing partial precedent in simulator_module.py lines
    466-481: remove the condition that skips the first failed attempt, add
    the missing token_usage_json. Do NOT make the final dataclass numeric
    fields cumulative across attempts (lines 1178-1204). Add a SQL-parity
    test and a non-regression test on final numeric fields.
18. BLOCKING 4, refusal generation on all 3 REFUSE paths (lines 1274-1345,
    table lines 1298-1302): _llm_refusal_call returns a tuple of text and
    TokenUsage; RefusalGenerationResult gains a token_usage field with a
    safe default from the start (2 existing tests construct this dataclass
    without the field and must stay green: test_controller_speculative_lazy.py
    line 101, test_refusal_handler_duration.py line 111); accumulate one
    TokenUsage per real call in generate_llm_safe_refusal_detailed, combine
    on all 3 return points; wire token_usage_json into
    response_assembler.py lines 304-322, final_revalidation.py lines
    577-597, AND refusal_handler.py lines 147-158 (the actual primary
    REFUSE path). Add billable_provider_call keyed on attempts greater than
    zero at refusal_handler.py and final_revalidation.py -- NOT at
    response_assembler.py, which is already unconditionally gated on
    self.policy being available; verify before skipping.
19. BLOCKING 1, speculative-discard context propagation, simplified in v3
    (lines 890-975): fix SpeculativeOverlapHandle.abandon() in
    speculative_overlap.py lines 127-170 to capture run_id, request_id,
    session_id, turn_number, cycle in the calling thread before starting
    the daemon thread, then inject them via setdefault into the merged
    payload (exact code at lines 912-947). This is a genuine pre-existing
    bug fix. Do NOT add any pending-counter or deferred-finalize or timer
    mechanism -- that was tried in v2 and removed in v3 because Codex
    confirmed it did not close the race. It is accepted that usage in the
    HTTP response may miss a late-resolving discarded speculative call
    (Product decision 5, lines 241-252). _finalize_token_accounting stays
    unconditional and synchronous, exactly as built in step 8.
20. Run the full non-regression gate, then the full suite, then mypy
    strict, then scoped pre-commit (exact commands in the Required tests
    section below).

## Required tests

Run these exact commands (plan lines 3632-3662, plus deltas at lines
3771-3778 and 4009-4028). Report the REAL output of every command.

```bash
# 1. New isolated modules
python -m pytest tests/test_token_usage.py tests/test_request_token_accumulator.py -q

# 2. Modified modules
python -m pytest tests/test_models_base_token_usage.py tests/test_models_policy_token_usage.py tests/test_generation_overrides.py -q
python -m pytest tests/test_deliberation_runner_token_usage.py tests/test_orchestrator.py -k "token_usage" -q
python -m pytest tests/test_constitution_retrieval_persistence.py -q
python -m pytest tests/test_embedder.py tests/test_local_embedder.py tests/test_orchestrator_embedder_token_usage.py -q
python -m pytest tests/test_observability_sqlite_sink.py tests/test_observability_sqlite_sink_token_usage.py tests/test_observability_read_store.py tests/test_observability_read_store_token_usage.py -q
python -m pytest tests/test_sdk_response.py tests/test_sdk_response_token_usage.py -q
python -m pytest tests/test_server_proxy.py tests/test_server_proxy_token_usage.py -q

# 2b. billable_provider_call, cache-hit, retry, refusal, speculative fixes
python -m pytest tests/test_speculative_overlap.py tests/test_request_token_accumulator.py -q
python -m pytest tests/test_controller_token_accounting_speculative.py tests/test_controller_speculative_lazy.py -q
python -m pytest tests/test_deliberation_runner_billable_provider_call.py tests/test_observability_contract.py tests/test_risk_persist_batch.py -q
python -m pytest tests/test_observability_service.py tests/test_observability_sqlite_sink.py tests/test_observability_read_store.py -q
python -m pytest tests/test_module_result_cache_billing.py -q
python -m pytest tests/test_runtime_modules_token_usage_source.py tests/test_perspective_module.py -q
python -m pytest tests/test_runtime_modules_retry_token_accounting.py -q
python -m pytest tests/test_refusal_handler.py tests/test_safe_refusal_generator.py tests/test_response_assembler.py tests/test_final_revalidation.py -q

# 3. Integration end-to-end
python -m pytest tests/test_token_accounting_e2e.py -q

# 4. Targeted non-regression gate (must be untouched and green)
python -m pytest tests/test_system_prompt_byte_equality.py -q
python -m pytest tests/governance_invariants/ -q
python -m pytest tests/test_decide_action.py tests/test_safe_complete_policy.py tests/test_safe_complete_gating.py tests/test_safe_complete_user_turn.py -q
python -m pytest tests/test_observability_write_queue.py tests/test_observability_service.py tests/test_observability_contract.py -q
python -m pytest tests/test_server_proxy.py tests/test_conversation_correlation.py -q
python -m pytest tests/test_ledger.py tests/test_ledger_storage.py tests/test_ledger_fast_path_events.py tests/test_ledger_fast_path_gate_rejected_e2e.py tests/test_ledger_posture_symmetry.py -q
python -m pytest tests/test_governed_delivery.py -q

# 5. Full suite
python -m pytest

# 6. mypy strict on orchestration modules touched
python -m mypy moralstack.orchestration.controller moralstack.orchestration.types moralstack.orchestration.embedder moralstack.orchestration.persistence_helpers moralstack.orchestration.speculative_overlap moralstack.orchestration.deliberation_runner moralstack.orchestration.safe_refusal_generator moralstack.orchestration.response_assembler moralstack.orchestration.final_revalidation moralstack.orchestration.refusal_handler --strict
python -m mypy moralstack --ignore-missing-imports

# 7. pre-commit scoped on touched files ONLY (HEAD is NOT pre-commit-clean,
#    use --files, never -a, per repo memory precommit-head-drift)
python -m pre_commit run --files moralstack/observability/token_usage.py moralstack/observability/request_token_accumulator.py moralstack/observability/service.py moralstack/observability/events.py moralstack/observability/sinks/sqlite_sink.py moralstack/observability/read_store.py moralstack/models/base.py moralstack/models/policy.py moralstack/orchestration/deliberation_runner.py moralstack/constitution/retriever.py moralstack/orchestration/embedder.py moralstack/orchestration/types.py moralstack/orchestration/controller.py moralstack/server/proxy.py moralstack/sdk/response.py moralstack/orchestration/speculative_overlap.py moralstack/persistence/write_queue.py moralstack/orchestration/safe_refusal_generator.py moralstack/orchestration/response_assembler.py moralstack/orchestration/final_revalidation.py moralstack/orchestration/refusal_handler.py moralstack/runtime/modules/critic_module.py moralstack/runtime/modules/simulator_module.py moralstack/runtime/modules/hindsight_module.py moralstack/runtime/modules/perspective_module.py moralstack/persistence/db.py
```

### Existing coverage to protect (behavior-locking, do not weaken)

- tests/test_orchestrator.py lines 1630-1642: mock without a source
  attribute must not raise AttributeError.
- tests/test_risk_persist_batch.py lines 168-169: must break if
  billable_provider_call leaks into the risk-estimator local payload.
- tests/test_controller_speculative_lazy.py line 101,
  tests/test_refusal_handler_duration.py line 111: construct
  RefusalGenerationResult without token_usage; must stay green via the
  dataclass default.
- tests/test_perspective_module.py lines 52-59: MockGenerationResult
  without token_usage_source is already the attribute-absent case required
  by BLOCKING 3; do not add the attribute there just to complete the mock.
- tests/test_observability_contract.py lines 86-90: payload assertions
  including speculative-reuse; verify the additive billable_provider_call
  field does not break it.

## Acceptance criteria

The authoritative, complete checklist is plan lines 3095-3380 (base
criteria plus one delta block per BLOCK round v2 through v8). You must
satisfy every item there. Categories, for orientation:

- Base (lines 3097-3121): llm_calls numeric columns populated;
  request_token_usage has one row per run_id/request_id; totals and
  breakdown queries correct end to end; policy.py marks source estimated
  correctly; constitution_retriever appears in the breakdown; embedder
  produces a row only for OpenAIEmbedder; proxy usage is real and non-zero
  when generation happened; GovernanceMetadata exposes the new fields; no
  regression on decision-policy, byte-equality, observability, ledger
  tests; mypy strict clean; docs updated in the same commit.
- After round 1 / Decisione 8 base (lines 3125-3162): speculative discard
  persisted correctly though not necessarily reflected in synchronous
  usage; diagnostic rows marked non-billable and excluded from breakdown;
  module breakdown reports correct token_usage_source; refusal generation
  produces non-NULL token_usage_json including retry sums; explicit SQL
  parity test between billable rows and synchronous total; historical NULL
  billable_provider_call rows counted as billable; two run_ids with the
  same caller-supplied request_id never mix totals.
- After round 2 / v3 (lines 3166-3198): critic-skip site correctly
  conditional; cache-hit produces non-billable rows with no double count;
  failed attempts persist their own billable rows; SQL parity for one
  failed plus one succeeded attempt; total retry exhaustion still persists
  every failed attempt; final dataclass numeric fields unchanged
  (last-successful-attempt only); refuse fast-path row present.
- After round 3 / v4 (lines 3202-3243): quick_check success and failure
  paths persist correctly; simulator seeded retries persist failures and
  combine sources correctly; HindsightResult.tokens_used is greater than
  zero on the individual-evaluation default path; hindsight retry failures
  persist; perspectives aggregated row carries a per-perspective token
  breakdown that sums correctly; usage_may_be_incomplete flag set correctly
  on every abandon() call site and reflected consistently in
  ResponseMetadata and GovernanceMetadata; no residual "authoritative"
  wording.
- After round 4 / v5 (lines 3247-3285): speculative used and discarded
  outcomes both carry non-NULL token_usage_json; mixed aggregated rows with
  a missing component and a non-missing component with tokens still
  produce non-NULL token_usage_json; no residual "one row per HTTP
  round-trip" wording; every retry_failed row has a non-empty model when
  available; refusal rows have a non-empty model; the exception-before-route
  edge case still persists a discarded row without requiring
  usage_may_be_incomplete to propagate; request_token_usage has the v4
  columns from its very first CREATE TABLE.
- After round 5 / v6 (lines 3289-3311): refusal rows with no policy
  available are non-billable (attempts equal to zero); refusal rows with a
  policy available are billable regardless of fallback text used;
  response_assembler.py never produces a row with self.policy None; no
  residual "canonical/complete guarantee" wording for the offline
  reconstruction query.
- After round 6 / v7 (lines 3315-3358): numeric columns on mixed aggregated
  rows match the JSON-decoded values, not just the JSON column; SQL sum
  parity with manually decoded JSON; breakdown query excludes non-billable
  rows with the same predicate as the reconstruction query; llm_call_count
  and the two usage-count fields in request_token_usage count only billable
  rows; the 4 named fast-path/regenerate call sites and _revalidate_draft
  and _speculative_generate all carry a correct, never-null model value;
  RefusalGenerationResult default token_usage does not break the 2 named
  existing tests; critic/simulator/hindsight/perspectives rows via the
  helper functions carry a correct model value.

**After round 7 (v8, PROJECT_SPEC section 9) -- reproduced here in full
because these 2 criteria have NO independent Codex confirmation and
directly protect the v8 correction described in the process notice at the
top of this handoff:**

- [ ] _module_model() in deliberation_runner.py (lines 212-226) returns
      module.policy.model, NOT module.model, for critic, simulator,
      hindsight and perspectives when module.policy.model is present;
      verified with a dedicated test that sets module.policy.model and
      module.model to different values and checks that the former is
      returned. This protects the Goal of accounting per effective model --
      the buggy reading would blank out model attribution for the 4 core
      modules.
- [ ] _policy_llm_model_for_action(policy, "rewrite") in
      deliberation_runner.py (lines 200-209) returns policy.rewrite_model,
      NOT policy.model, when the two differ (see models/policy.py lines
      143, 162, 489, where OpenAIPolicy.rewrite() calls generate with a
      model_override of the rewrite model); verified with a dedicated test
      that sets rewrite_model different from model and compares the
      returned value for action equal to "rewrite" versus action equal to
      "generate". This protects the Goal of accounting per effective model
      -- the buggy reading would misattribute every rewrite call to the
      primary generation model.

Do not consider the task done until every checkbox in plan lines 3095-3380
is genuinely satisfied and, where the plan requires it, covered by a
dedicated test.

## Risks (full list at plan lines 2777-3094, highlights below)

- R1/R21, false sense of completeness: both request_token_usage and the SUM
  llm_calls reconstruction query share the same lossy best-effort
  observability queue (write_queue.py, section 5.6, pre-existing P2 risk).
  Neither is an absolute completeness guarantee -- reflect this in wording
  everywhere.
- R8, speculative-discard race, accepted limit: usage in the HTTP response
  may not include a late-resolving discarded speculative call. Explicitly
  accepted (Product decision v3). Do not try to fully fix this by
  reintroducing a pending-counter or timer mechanism -- that was tried and
  rejected by a prior Codex review.
- R9, billable and non-billable parity: if the billable_provider_call
  filter is applied in only one of {accumulator hook, SQL queries}, the
  proxy synchronous total silently diverges from the offline-reconstructed
  total. Same predicate, same PR, explicit parity test.
- R10, incomplete token_usage_source propagation: a fifth construction site
  of one of the 5 dataclasses could remain uncovered. Mitigate by grepping
  for tokens_used access across the 4 runtime module files before declaring
  this step done.
- R12, cache-hit double-billing, pre-existing independent bug: cache hits
  on simulator and hindsight (caching on by default) and perspectives
  (caching off by default, dormant) get re-billed as new calls unless
  from_cache is threaded through. See checklist step 15 note on in-place
  mutation of cached objects -- decide conservatively (copy, do not mutate)
  if a real bug is found there.
- R14/R16, retry accounting must not double- or under-count: SQL-parity
  test required; final dataclass numeric fields must stay last-successful-
  attempt-only, never cumulative.
- Section 5.6 risk for every new hook: every new persistence or
  accumulation call site must be wrapped in a non-propagating try/except --
  this is the plan central safety property, verify it at every site
  touched, not just the ones explicitly shown with a try/except in the
  plan code samples.

## Deviations policy

If you must deviate from the plan (for example the cache-hit mutation issue
in checklist step 15, or if the real code no longer matches a path:line
reference in the plan), report it explicitly as a deviation, with the
reason, rather than silently adapting. Do not deviate on anything touching
the section 5.6 invariant or the acceptance criteria above without stopping
and reporting first.

## Ready prompt for Cursor CLI

You are implementing a large, already-approved, 8-times-revised plan for
MoralStack token accounting subsystem. Read this handoff in full, then read
ai/plans/token-accounting-p0-2b-p11.md in full (it is long, 4219 lines,
budget for a full read, not a skim; the Revision log at the top explains why
the design looks the way it does, read it before the Design section so the
many small guardrails make sense instead of looking arbitrary).

This is a large plan, roughly 21 PR-sized changes across about 26
application files plus around 30 test files. Implement it in the order
given in the Checklist section above (steps 1-20), which mirrors the plan
own Implementation checklist (plan lines 4038-4109). After each step, the
affected tests should pass before moving to the next step -- do not pile up
broken intermediate states.

If you cannot complete every step in this run, stop at a clean step
boundary (all tests for the steps completed so far passing) and report
exactly which steps (1-20) were completed versus not, rather than leaving a
partially-edited, inconsistent set of files. Do not claim the task is done
if it is not.

Pay special attention to the IMPORTANT PROCESS NOTICE at the top of this
handoff and to the 2 acceptance criteria under "After round 7 (v8...)" --
those protect a design correction that has not yet been independently
reviewed by Codex.

Follow every rule in ai/prompts/cursor-cli-implementation-template.md
(allowed-files only, no scope creep, no refactors, no weakened or skipped
tests, honor PROJECT_SPEC section 5.6, report real command output, stop on
ambiguity or blockers, never git add, commit, or push).

## Output required from Cursor CLI

At the end of your run, report:
- Files modified (full list, repo-relative paths).
- Tests added (full list of new test files plus new test functions in
  extended files).
- Commands run plus their REAL results (paste actual pytest, mypy, and
  pre-commit output summaries, not paraphrases).
- Which of the 20 checklist steps were completed, and which, if any, were
  not, with the reason.
- Deviations from the plan (including anything decided per the Deviations
  policy above), or "none".
- Residual problems and blockers, or "none".
