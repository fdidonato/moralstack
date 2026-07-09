# Codex Diff Review

## Verdict
APPROVE_WITH_CHANGES

## Deviations from approved plan
No behavioral deviation found. The prompt split is implemented at `moralstack/constitution/retriever.py:494-498`, `_call_openai` uses the same `system_prompt` for API and persistence at `moralstack/constitution/retriever.py:608-610` and `moralstack/constitution/retriever.py:667-670`, and the six parallelism sources are covered.

Two documentation precision deviations need fixing: `moralstack/constitution/retriever.py:518-520` says prompt caching "engages," while the approved wording is cache-eligible only; `docs/CODEBASE_FACTS.md:119` paraphrases the cache key delimiter incorrectly versus the actual unchanged key at `moralstack/constitution/retriever.py:446`.

## Blocking issues
None.

Invariant check: `core` remains retrieval-only via `ALWAYS_EVALUATE` and exclusion from `domains_to_check` at `moralstack/constitution/retriever.py:279` and `moralstack/constitution/retriever.py:447`; hard-signal decision code is untouched and remains in `moralstack/orchestration/path_router.py:42`; observability persistence is still best-effort at `moralstack/constitution/retriever.py:81-108`; governed delivery code is untouched at `moralstack/orchestration/delivery.py:79`.

## Non-blocking issues
- `moralstack/constitution/retriever.py:518-520` overstates prompt caching as guaranteed and says prompt bytes change only with keyword/description mutation; `available_domains` also feeds `domain_list` at `moralstack/constitution/retriever.py:492`.
- `docs/CODEBASE_FACTS.md:119` describes the `_cache` key imprecisely; the actual unchanged code uses `f"{query}_{','.join(sorted(available_domains))}"` at `moralstack/constitution/retriever.py:446`.

## Missing/weak tests
Changed behavior is covered: persisted shape at `tests/test_domain_prefilter_cache.py:137`, parse ladder at `tests/test_domain_prefilter_cache.py:174`, core exclusion at `tests/test_static_prefix_stability.py:957`, defaults/help at `tests/test_constitution_max_parallel_agents_default.py:41`, and batching at `tests/test_constitution_retrieval_context_propagation.py:107`.

Weak pre-existing test remains: `tests/test_domain_prefilter_cache.py:330` defines `_mock_prefilter_openai(prompt)` and patches `_call_openai` at `tests/test_domain_prefilter_cache.py:336`, so it still exercises the swallowed fallback path rather than classifier success.

## Security issues
None found.

## Performance issues
None beyond the approved concurrency increase. The env override remains at `moralstack/pipeline/deliberation_stack.py:60-64`, and batching still uses configured batch size at `moralstack/constitution/retriever.py:1468` and `moralstack/constitution/retriever.py:1506`.

## Maintainability issues
The two documentation inaccuracies above are the only maintainability issues found. No dead code or needless runtime complexity in the touched implementation.

## Required fixes
- Change `moralstack/constitution/retriever.py:518-520` to say the prompt is cache-eligible, not that caching engages, and note `available_domains` also affects `domain_list`.
- Correct the `_cache` key description in `docs/CODEBASE_FACTS.md:119` to match `moralstack/constitution/retriever.py:446`.

## Suggested fixes
- Update `tests/test_domain_prefilter_cache.py:330` to accept the current `_call_openai` kwargs so `test_domain_selection_stable_pre_post_idempotence` no longer relies on fallback behavior.
