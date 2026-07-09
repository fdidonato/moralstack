<!--
RE-REVIEW of the diff after the fix-pass. Reviewer: OpenAI Codex via codex:codex-rescue (--wait --fresh),
read-only. Prior diff review (BLOCK): codex-diff-review-...-20260709-115102.md.
Cumulative diff reviewed: ai/reviews/diff-after-bounded-tenant-aware-conversation-correlation-fixpass-20260709-120500.md.
-->

# Codex Diff Review

## Verdict
APPROVE_WITH_CHANGES

## Deviations from approved plan
None affecting behavior. The prior BLOCK is resolved: `resolve()` now catches helper-path `Exception`s with debug logging and still returns a minted `msconv-*` id (`moralstack/server/conversation_correlation.py:159`, `:171`, `:178`, `:182`, `:188`). The helpers themselves still raise normally and are not internally swallowed (`moralstack/server/conversation_correlation.py:211`, `:222`). The cumulative diff still includes `proxy.py` from the original approved implementation; the fix-pass report states `proxy.py` was not modified in the fix-pass (`ai/handoffs/bounded-tenant-aware-conversation-correlation-fixpass-implementation-report.md:18`).

## Blocking issues
None.

## Non-blocking issues
- `docs/CODEBASE_FACTS.md:120` still records "Full suite: 2208 passed / 1 deselected" for this change, while the fix-pass report records the post-fix-pass full suite as `2214 passed, 1 deselected` (`ai/handoffs/bounded-tenant-aware-conversation-correlation-fixpass-implementation-report.md:32`). Documentation drift, not a runtime defect.

## Missing/weak tests
None found. Helper-failure regressions are covered at unit level for lookup, insert, and `time_fn` failures (`tests/test_conversation_correlation.py:382,391,400`), and at endpoint level with forced `_get_id`/`_put_id` failures plus `200`/`msconv-*` assertions (`tests/test_server_proxy.py:1484,1492,1500`). `canonical_parent_history_hash()` is directly golden-pinned (`tests/test_conversation_correlation.py:105`).

## Security issues
None found. `Authorization` remains filtered from safe debug headers (`moralstack/server/proxy.py:155,169`); HMAC principal derivation reads the secret per request without logging the token or digest (`moralstack/server/proxy.py:204,212`); no-leak regression test checks logs plus safe headers (`tests/test_server_proxy.py:1387,1409,1416`).

## Performance issues
None found. Store is bounded by TTL and max entries (`moralstack/server/conversation_correlation.py:128,129,217,231`).

## Maintainability issues
- `_extract_principal()`'s docstring says the derived HMAC digest is never "stored" (`moralstack/server/proxy.py:200`), but the returned principal is used in the correlation key (`moralstack/server/conversation_correlation.py:157`) and stored in the in-memory `OrderedDict` key (`moralstack/server/conversation_correlation.py:227`). Implementation is consistent with the plan; wording should clarify the raw token is never stored/logged and the digest is not logged or persisted.

## Required fixes
- Update the stale `docs/CODEBASE_FACTS.md:120` suite-count text (or remove the exact count) to avoid drift.
- Clarify the `_extract_principal()` docstring at `moralstack/server/proxy.py:200` per above.

## Suggested fixes
None beyond the required documentation wording updates.
