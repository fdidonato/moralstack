# Codex Plan Review

## Verdict
BLOCK

## Blocking issues
- Speculative generation still has no token usage in the emitted `llm.call`. The speculative path performs a real policy call at `moralstack/orchestration/controller.py:941-955`, but the `persist_kwargs` it returns contains model/timing/prompt fields and no `token_usage_json` at `moralstack/orchestration/controller.py:973-1000`. `join_for_consumer()` and `abandon()` emit that metadata as-is at `moralstack/orchestration/speculative_overlap.py:103-108` and `moralstack/orchestration/speculative_overlap.py:156-164`, while persistence only records `token_usage_json` if it is present in kwargs at `moralstack/persistence/write_queue.py:51-71`. This blocks the plan’s “all internal LLM calls, including speculative discarded calls” accounting claim. Add token usage to speculative `persist_kwargs` for both used and discarded outcomes.

- The plan’s `TokenUsage.combine()` can erase known token counts whenever one component is `missing`. The plan defines `to_json()` as `None` whenever `source == "missing"` at `ai/plans/token-accounting-p0-2b-p11.md:319-320`, and `combine()` chooses the least-certain source, including `"missing"`, while still summing counts at `ai/plans/token-accounting-p0-2b-p11.md:820-832`; `_token_usage_json_from_result()` then returns `usage.to_json()` at `ai/plans/token-accounting-p0-2b-p11.md:857-859`. Current aggregate rows exist for multi-call modules, e.g. perspectives sums per-result tokens at `moralstack/runtime/modules/perspective_module.py:621-634` and `moralstack/runtime/modules/perspective_module.py:671-684`, then persists one aggregate row at `moralstack/orchestration/deliberation_runner.py:3242-3265`. A mixed exact+missing aggregate would become `NULL` and lose the exact counts. Separate “has missing component” from JSON nullability, or make combined records preserve numeric counts.

- The plan contradicts its own per-call audit goal. It says token input/output/total must be calculable per single LLM call at `ai/plans/token-accounting-p0-2b-p11.md:148-154` and that every token-consuming OpenAI call produces a record at `ai/plans/token-accounting-p0-2b-p11.md:219-223`, but later explicitly keeps one row per module invocation, not one row per provider call, for perspectives at `ai/plans/token-accounting-p0-2b-p11.md:1391-1400` and one row per refusal generation, not per retry attempt, at `ai/plans/token-accounting-p0-2b-p11.md:1038-1042`. Current code really does multiple provider calls for perspectives at `moralstack/runtime/modules/perspective_module.py:652-658` and `moralstack/runtime/modules/perspective_module.py:715-730`, and refusal can call once then retry at `moralstack/orchestration/safe_refusal_generator.py:607` and `moralstack/orchestration/safe_refusal_generator.py:634`. Either change the target semantics and rename `llm_call_count`, or persist one row per provider round trip.

- Newly planned retry/refusal rows do not carry the effective model, breaking the per-model goal. Persistence defaults missing model to `""` at `moralstack/persistence/write_queue.py:51-56`. The plan’s retry-failed payload omits `model` at `ai/plans/token-accounting-p0-2b-p11.md:941-947`, and the planned primary refusal payload also omits it at `ai/plans/token-accounting-p0-2b-p11.md:1048-1055`; current primary refusal and deliberative refusal rows likewise omit model at `moralstack/orchestration/refusal_handler.py:147-158` and `moralstack/orchestration/response_assembler.py:303-321`. Add model propagation for retry-failed rows and all refusal generation rows.

- `usage_may_be_incomplete` misses an implicit `abandon()` path that runs after finalization. The plan claims the six `abandon()` call sites are all before `_attach_trace_and_return` at `ai/plans/token-accounting-p0-2b-p11.md:1441-1452`. But `SpeculativeOverlapHandle.shutdown_executor()` also calls `abandon()` when a handle was neither joined nor abandoned at `moralstack/orchestration/speculative_overlap.py:51-54`, and the controller calls `shutdown_executor()` in `finally` after return expressions have already invoked `_attach_trace_and_return` at `moralstack/orchestration/controller.py:312-320` and `moralstack/orchestration/controller.py:2675-2677`. The flag can be set too late to reach metadata or `request_token_usage`. Cover this implicit path or document that the flag is not complete.

- The multi-PR schema sequence would strand existing `request_token_usage` tables without v4 columns. The initial table definition lacks `usage_may_be_incomplete` and `incomplete_reason` at `ai/plans/token-accounting-p0-2b-p11.md:365-377`; PR5 creates that table at `ai/plans/token-accounting-p0-2b-p11.md:1518-1520`; PR20 later adds the two columns at `ai/plans/token-accounting-p0-2b-p11.md:1570-1575` while saying no `ALTER` is needed at `ai/plans/token-accounting-p0-2b-p11.md:1484-1493`. Current SQLite migration practice uses additive `ALTER TABLE` loops at `moralstack/observability/sinks/sqlite_sink.py:643-709`. Add idempotent migrations for these columns or collapse the schema change into the original table-creation PR.

## Non-blocking issues
- `moralstack/observability/__init__.py` re-exports event constants and SQLite helpers at `moralstack/observability/__init__.py:34-77` and `moralstack/observability/__init__.py:92-155`; the plan does not list it for the new event/writer exports.
- The pre-commit command’s file list omits many later-scoped files such as runtime modules, refusal files, and `speculative_overlap.py`, even though those files are in the implementation checklist.
- Mutating cached result objects with `from_cache=True` is risky because cache hits return the same mutable result object in simulator/hindsight/perspectives at `moralstack/runtime/modules/simulator_module.py:347-355`, `moralstack/runtime/modules/hindsight_module.py:657-667`, and `moralstack/runtime/modules/perspective_module.py:487-495`. Prefer returning a copy or tracking cache status out-of-band.

## Missing tests
- A speculative used/discarded row must assert non-NULL `token_usage_json`; current speculative metadata lacks it at `moralstack/orchestration/controller.py:973-1000`.
- Mixed-source aggregates need tests for exact+missing and estimated+missing to ensure known counts are not serialized as `NULL`.
- Migration tests must simulate PR5-created `request_token_usage` without v4 columns, then verify idempotent `ALTER` adds them.
- Retry-failed and refusal rows need model assertions, not just token assertions.
- Add malformed `token_usage_json` tests: current SQLite stores opaque text at `moralstack/observability/sinks/sqlite_sink.py:1794-1797`; new parsing must not drop the whole `llm.call`.

## Risky assumptions
- “No result exists” in proxy fail-closed does not necessarily prove no internal LLM call completed. The proxy catches `orchestrator.process()` exceptions at `moralstack/server/proxy.py:331-364`, while internal calls can be emitted during processing through `record_llm_call()`/`async_persist_llm_call()` at `moralstack/orchestration/persistence_helpers.py:39-42`.
- The plan assumes the incomplete flag covers all synchronously known speculative discard cases, but `shutdown_executor()` is an uncounted discard source at `moralstack/orchestration/speculative_overlap.py:51-54`.
- The plan assumes aggregate rows are sufficient while still naming the result “per-call” accounting.

## Architecture concerns
- The plan mixes two incompatible grains: provider-call accounting and module-invocation accounting. That makes `llm_call_count`, per-call audit, and per-model breakdown ambiguous.
- Parsing token JSON in the sink changes `token_usage_json` from opaque telemetry to a schema-sensitive input. Current writers store it directly at `moralstack/observability/sinks/sqlite_sink.py:1794-1797` and `moralstack/observability/sinks/sqlite_sink.py:1968-1971`; the plan needs explicit non-throwing parse semantics.

## Security/performance concerns
- No direct authz or secret-handling issue found in the plan.
- The `emit_batch()` accumulator hook must filter and parse per envelope because risk mini-calls route batches through `ObservabilityService.emit_batch()` at `moralstack/models/risk/estimator.py:71-75` and `moralstack/models/risk/estimator.py:703-709`.

## Suggested plan changes
- Add `token_usage_json` to `_speculative_generate()` metadata.
- Decide one accounting grain: either one row per provider call, or rename/count rows as module attempts and stop claiming per-call audit.
- Redesign aggregate source/null semantics so partial-missing aggregates preserve known counts.
- Include `model` on retry-failed and refusal-generation rows.
- Add `ALTER TABLE` migrations for all later-added `request_token_usage` columns.
- Include `shutdown_executor()` implicit abandon in incomplete-usage handling.

## Questions for Claude/User
- Should `llm_call_count` mean provider round trips or audit rows?
- Is per-provider-call provenance required for refusal retries, perspectives, and seeded simulator, or are aggregate module rows acceptable if renamed?
- Should `usage` on proxy fail-closed responses include any already-emitted internal LLM calls when `orchestrator.process()` raises?
