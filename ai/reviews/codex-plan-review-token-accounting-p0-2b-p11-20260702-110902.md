# Codex Plan Review

## Verdict
`BLOCK`

## Blocking issues
- Mixed `TokenUsage.combine()` aggregates can still disappear from SQL totals. The plan correctly says mixed aggregates may have positive totals while `source=="missing"` (`ai/plans/token-accounting-p0-2b-p11.md:439`, `ai/plans/token-accounting-p0-2b-p11.md:970`), but SQLite derivation says numeric columns are `NULL` whenever `source == "missing"` (`ai/plans/token-accounting-p0-2b-p11.md:490`). Since the new reconstruction queries sum numeric columns, not JSON (`ai/plans/token-accounting-p0-2b-p11.md:2084`), this would undercount the exact mixed-source rows the v5 fix was meant to preserve. Current code has only `token_usage_json TEXT` and no numeric columns (`moralstack/observability/sinks/sqlite_sink.py:191`, `moralstack/observability/sinks/sqlite_sink.py:1796`). Must change numeric-column derivation to match `to_json()` nullability: only omit numeric values when `total_tokens == 0 AND source == "missing"`.

- The target breakdown query conflicts with the billable/non-billable design. The query shown for breakdown has no `COALESCE(billable_provider_call, 1)=1` filter (`ai/plans/token-accounting-p0-2b-p11.md:2066`), while the plan elsewhere requires non-billable rows to be excluded from breakdowns (`ai/plans/token-accounting-p0-2b-p11.md:2172`). Current code already has audit rows that are diagnostic/non-provider paths the plan will keep in `llm_calls`, including speculative reuse (`moralstack/orchestration/deliberation_runner.py:866`), fast-path leakage detection (`moralstack/orchestration/deliberation_runner.py:909`), skipped rewrite (`moralstack/orchestration/deliberation_runner.py:2661`), and output-protection leakage detection (`moralstack/orchestration/deliberation_runner.py:2718`). Must make every target/read-store breakdown query use the same billable predicate as the accumulator and offline total.

- Per-model accounting remains incomplete. The plan goal requires token totals by effective model (`ai/plans/token-accounting-p0-2b-p11.md:262`), but v5 only adds `model` to retry/refusal payloads (`ai/plans/token-accounting-p0-2b-p11.md:1741`). Existing billable rows with `token_usage_json` omit `model`: benign fast-path generation (`moralstack/orchestration/deliberation_runner.py:667`), safe-complete generation (`moralstack/orchestration/deliberation_runner.py:772`), fast-path generation (`moralstack/orchestration/deliberation_runner.py:947`), compliance regeneration (`moralstack/orchestration/controller.py:1227`), and draft revalidation (`moralstack/orchestration/controller.py:1309`). Speculative generation can pass explicit `None` as model (`moralstack/orchestration/controller.py:971`, `moralstack/orchestration/controller.py:978`), and the writer preserves explicit `None` because it uses `kwargs.get("model", "")` (`moralstack/persistence/write_queue.py:55`). Must normalize/populate `model` on all billable token rows, not only new retry/refusal rows.

## Non-blocking issues
- Existing tests construct `RefusalGenerationResult` without the planned `token_usage` field (`tests/test_controller_speculative_lazy.py:100`, `tests/test_refusal_handler_duration.py:111`). The plan should either give the new field a safe default or explicitly update those tests.
- The planned `quick_check()` parse-failure row uses an empty `raw_response`, but current `quick_check()` has the provider text before JSON extraction can fail (`moralstack/runtime/modules/critic_module.py:650`, `moralstack/runtime/modules/critic_module.py:688`). Persisting the raw response would improve audit utility.
- The text still says `_token_usage_json_from_result` returns `None` only when `source=="missing"` (`ai/plans/token-accounting-p0-2b-p11.md:1009`), which conflicts with the corrected v5 rule based on `total_tokens==0 AND source=="missing"`.

## Missing tests
- Add a SQLite sink test for a mixed aggregate: one positive exact/estimated component plus one missing component must produce non-NULL numeric token columns and a correct SQL `SUM`.
- Add tests proving breakdown queries exclude `billable_provider_call=0` rows and that `COUNT(*)` semantics match the chosen `llm_call_count` meaning.
- Add model-completeness tests for benign fast-path, safe-complete, fast-path, compliance-regenerate, draft-revalidate, speculative used/discarded, refusal, and retry rows.
- Add/update tests for direct `RefusalGenerationResult` construction after adding `token_usage`.

## Risky assumptions
- The plan assumes JSON token preservation and numeric SQL preservation are equivalent, but the planned `source=="missing"` numeric-null rule breaks that equivalence.
- The plan assumes fixing model only on retry/refusal payloads is enough for per-model accounting; current core billable policy/DCCL/speculative rows show otherwise.
- The plan assumes `llm_call_count` is clear, but the accumulator skips non-billable rows while `llm_calls` contains both provider and diagnostic rows. The plan must define whether this count is billable rows or all audit rows.

## Architecture concerns
- The `ObservabilityService.emit()` hook is the right common interception point: current producers converge there through `emit()`/`emit_batch()` (`moralstack/observability/service.py:43`, `moralstack/models/risk/estimator.py:71`, `moralstack/persistence/sink.py:115`).
- The architecture needs one shared billable predicate for accumulator, read-store, docs, and SQL examples. Duplicating it in prose and queries is already drifting.
- Runtime-module self-persistence is layering leakage, but it is already established in simulator retry persistence (`moralstack/runtime/modules/simulator_module.py:466`), so it is acceptable if kept best-effort and narrow.

## Security/performance concerns
- I found no direct violation of decision/generation separation, hard-signal supremacy, prompt transparency, `core` retrieval-only, or governed delivery. The proposed changes are telemetry and proxy payload changes; proxy success still builds a synthetic response from governed output (`moralstack/server/proxy.py:396`), and SSE replay is synthetic (`moralstack/server/proxy.py:205`).
- Observability best-effort remains correctly acknowledged: the current write queue drops events on `queue.Full` (`moralstack/observability/write_queue.py:175`).
- Performance risk is contained but real for `emit_batch()`: the current service accepts batches in one call (`moralstack/observability/service.py:50`), so per-envelope JSON parsing should be tested under mixed large batches.

## Suggested plan changes
- Change Decision 2 so numeric token columns are populated whenever positive totals are known, even if aggregate `source=="missing"`.
- Add the billable predicate to the published breakdown query and all read-store aggregation methods.
- Expand the `model` normalization audit to every billable row with `token_usage_json`, using existing model helpers where available.
- Define `llm_call_count` precisely as billable provider/audit rows or all audit rows, then align schema comments, accumulator behavior, read-store output, and tests.

## Questions for Claude/User
- Should `request_token_usage.llm_call_count` count only billable provider rows, or all `llm_calls` audit rows including non-billable diagnostics/cache hits?
- For mixed aggregates with positive known tokens plus missing components, should `token_usage_missing=1` mean “partial/possibly incomplete total” while numeric totals still store the known subtotal?
- When a policy object lacks `.model`, is empty string acceptable in per-model billing breakdowns, or should the implementation derive a stronger effective-model fallback?
