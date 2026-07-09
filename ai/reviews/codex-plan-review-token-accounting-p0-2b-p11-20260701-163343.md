# Codex Plan Review

## Verdict
BLOCK

## Blocking issues
- **Uncounted critic `quick_check()` LLM call.** The plan says all token-consuming OpenAI calls are covered, but `LLMCritic.quick_check()` calls the policy LLM and returns a `QuickCheckResult` with no token fields or persistence path. It is used in fast-path gating, so this is safety-relevant audit loss, not an edge case. Evidence: `moralstack/runtime/modules/critic_module.py:138-144`, `moralstack/runtime/modules/critic_module.py:650-654`, `moralstack/orchestration/deliberation_runner.py:974-977`. Must add token provenance/persistence for this path or explicitly prove it is never enabled.

- **Runtime module accounting still misses alternate provider-call paths and per-call granularity.** The plan covers four retry loops but misses simulator seeded generation and hindsight individual evaluation. Simulator seeded mode issues provider calls inside its own retry loop and silently continues on failures; hindsight defaults to individual evaluation for a single consequence and returns a `HindsightResult` with zero token totals. Evidence: `moralstack/runtime/modules/simulator_module.py:360-368`, `moralstack/runtime/modules/simulator_module.py:529-573`, `moralstack/runtime/modules/hindsight_module.py:669-679`, `moralstack/runtime/modules/hindsight_module.py:568-580`, `moralstack/runtime/modules/hindsight_module.py:837-843`. Also, perspectives can make multiple provider calls but deliberation persists one aggregate row, not one row per provider call. Evidence: `moralstack/runtime/modules/perspective_module.py:715-736`, `moralstack/runtime/modules/perspective_module.py:624-635`, `moralstack/orchestration/deliberation_runner.py:3242-3265`. Must either instrument these paths or narrow the stated goal from “single LLM call” to “module-level aggregate.”

- **`request_token_usage` is described as authoritative while the design knowingly omits late billable calls.** `SpeculativeOverlapHandle.abandon()` emits discarded speculative results from a daemon thread after the caller continues, so a finalizer in `_attach_trace_and_return()` can run before that billable call is emitted. Evidence: `moralstack/orchestration/speculative_overlap.py:156-170`, `moralstack/orchestration/controller.py:312-320`, `moralstack/orchestration/controller.py:2528`, `moralstack/orchestration/controller.py:2553`, `moralstack/orchestration/controller.py:2594`. Accepting partial HTTP `usage` may be a product choice, but an “authoritative” durable request total that excludes known billable calls is misleading. Must rename/redefine it as a synchronous summary with an explicit completeness caveat/flag, or design a non-blocking reconciliation path.

## Non-blocking issues
- The plan’s speculative blast-radius list is incomplete. `abandon()` is also called outside the three highlighted paths. Evidence: `moralstack/orchestration/controller.py:1378`, `moralstack/orchestration/controller.py:2213`, `moralstack/orchestration/controller.py:2258`.
- Streaming remains unresolved. The proxy has a separate SSE builder with no `usage` chunk today; the plan says not to add one unless already present, but OpenAI-compatible clients may expect explicit behavior. Evidence: `moralstack/server/proxy.py:205-245`.
- Documentation scope misses the observability trace document that maps DB tables/read store behavior. Evidence: `docs/traces/observability_db_to_ui.md:59-67`, `docs/traces/observability_db_to_ui.md:131-141`.

## Missing tests
- Tests for `LLMCritic.quick_check()` token accounting and persistence.
- Tests for simulator seeded retry accounting and failed seeded attempts.
- Tests for hindsight single-consequence / individual-evaluation token accounting.
- Tests proving whether successful perspective calls are stored per provider call or intentionally aggregated.
- Tests that `request_token_usage` is marked partial or reconciled when a discarded speculative call lands after finalization.
- Tests for proxy fail-closed after partial internal LLM work, not only “no result exists.”
- Tests that new read-store aggregate queries use `COALESCE` correctly for legacy `NULL` numeric token columns.

## Risky assumptions
- “Four retry loops verified” is incomplete: seeded simulator has its own retry loop, and hindsight individual evaluation bypasses the batch loop. Evidence: `moralstack/runtime/modules/simulator_module.py:529-573`, `moralstack/runtime/modules/hindsight_module.py:803-843`.
- “One common `EVENT_LLM_CALL` funnel” is true for emitted events, but it does not help paths that never emit a row or only emit an aggregate row. Evidence: `moralstack/observability/service.py:43-57`, `moralstack/orchestration/deliberation_runner.py:3018-3051`, `moralstack/orchestration/deliberation_runner.py:3242-3265`.
- The proxy fail-closed branch has no `result`, but that does not prove no internal LLM call completed before an exception escaped. Evidence: `moralstack/server/proxy.py:331-364`.

## Architecture concerns
- The plan mixes two facts: provider-call accounting and module-result accounting. Current runtime modules often return aggregated dataclass totals, while the new DB schema claims per-call audit. That coupling is especially visible in seeded simulator and perspective ensemble paths.
- The invariants are mostly preserved if implementation stays telemetry-only: decision policy remains outside text generation, hard signals are not weakened, and governed delivery still comes from MoralStack rather than upstream. Evidence: `moralstack/orchestration/delivery.py:79-129`, `moralstack/server/proxy.py:375-377`.
- Observability best-effort is correctly prioritized, but the plan should avoid calling best-effort summaries “authoritative.” Existing observability deliberately swallows failures. Evidence: `moralstack/observability/service.py:43-57`, `moralstack/observability/write_queue.py:280-290`.

## Security/performance concerns
- No direct authz/secret-handling regression found in the plan.
- The hot-path `emit_batch()` accumulator hook must keep JSON parsing and locking minimal; risk-estimator batches already flow through `emit_batch()`. Evidence: `moralstack/models/risk/estimator.py:64-75`, `moralstack/observability/service.py:50-57`.
- All new telemetry parsing/persistence must preserve §5.6 behavior: never raise into request handling. Current service behavior is best-effort; new hooks must match it. Evidence: `moralstack/observability/service.py:43-57`.

## Suggested plan changes
- Add critic `quick_check()` to scope, including token source, `billable_provider_call`, and tests.
- Add simulator seeded generation and hindsight individual evaluation to scope, or explicitly narrow target behavior away from full provider-call accounting.
- Decide whether perspective/simulator seeded rows are per provider call or aggregate rows, then align schema, queries, and acceptance criteria.
- Reword `request_token_usage` as a synchronous best-effort summary, or add a completeness/reconciliation mechanism for late speculative calls.
- Extend docs scope to `docs/traces/observability_db_to_ui.md`.
- Update acceptance criteria so SQL reconstruction is the canonical complete audit source when late calls exist.

## Questions for Claude/User
- Is the product requirement truly **per provider call**, or is **per module result** acceptable for simulator seeded mode and perspective ensembles?
- Should `request_token_usage` ever be presented as authoritative if late speculative calls are intentionally excluded?
- Should proxy streaming responses expose usage when clients request OpenAI-compatible streaming usage, or is non-streaming usage sufficient for this milestone?
- Should `quick_check()` token usage be categorized as `critic`, `fast_path`, or a separate module/action for audit queries?
