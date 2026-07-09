# Codex Diff Review

## Verdict
BLOCK

## Deviations from approved plan
- Blocking deviation: the approved plan requires TTL/eviction failures in `resolve()` to be swallowed and to still return a valid minted id (`ai/plans/bounded-tenant-aware-conversation-correlation.md:81`, `ai/plans/bounded-tenant-aware-conversation-correlation.md:451`). The implementation calls `_get_id()` / `_put_id()` unguarded from `resolve()` (`moralstack/server/conversation_correlation.py:146`, `moralstack/server/conversation_correlation.py:154`, `moralstack/server/conversation_correlation.py:158`).
- Non-blocking deviation: the handoff required adding the three new env vars to the env-var catalogue (`ai/handoffs/bounded-tenant-aware-conversation-correlation-handoff.md:52`). The main README configuration catalogue still lists other `MORALSTACK_*` env vars but not `MORALSTACK_CORRELATION_TTL_SECONDS`, `MORALSTACK_CORRELATION_MAX_ENTRIES`, or `MORALSTACK_PRINCIPAL_HMAC_SECRET` (`README.md:388`, `README.md:401`).
- Verified no deviation: `canonical_history_hash()` / `canonical_parent_history_hash()` signatures and bodies remain unchanged (`moralstack/server/conversation_correlation.py:77`, `moralstack/server/conversation_correlation.py:88`), and the new tests pin golden digests for `canonical_history_hash()` (`tests/test_conversation_correlation.py:62`).
- Verified no deviation: empty/absent principal uses the empty sentinel (`moralstack/server/conversation_correlation.py:140`, `moralstack/server/proxy.py:216`) and the existing behavior-locking tests were not edited; the test diff is additions-only.
- Verified no deviation: HMAC secret is read per request and non-Bearer/unset secret falls back to `""` (`moralstack/server/proxy.py:204`, `moralstack/server/proxy.py:208`, `moralstack/server/proxy.py:212`); safe header logging filters `Authorization` (`moralstack/server/proxy.py:155`, `moralstack/server/proxy.py:164`).
- Verified no deviation: `_locks` bounding is only a TODO; no prune code shipped (`moralstack/server/proxy.py:89`, `moralstack/server/proxy.py:97`, `moralstack/server/proxy.py:100`).
- Verified no P0 decision/governance invariant surface was changed: no decision-policy, hard-signal, prompt-composition, or constitution-domain files are touched; proxy delivery still uses `finalize_delivery()` and records governed-delivery markers (`moralstack/server/proxy.py:447`, `moralstack/server/proxy.py:507`).

## Blocking issues
- What: correlation-store TTL/eviction helper failures can abort the request path instead of falling through to a fresh `msconv-*` id.
  Why it blocks: this violates the approved plan and PROJECT_SPEC invariant #6, which requires best-effort swallowing for request-breaking side effects (`PROJECT_SPEC.md:78`). It also happens before the proxy's main `try`/fail-closed block, so an exception from correlation resolution propagates out of the endpoint instead of producing a governed response.
  Evidence: `resolve()` calls `_get_id()` and `_put_id()` without `try/except` (`moralstack/server/conversation_correlation.py:146`, `moralstack/server/conversation_correlation.py:154`, `moralstack/server/conversation_correlation.py:158`); `_get_id()` can raise from `self._time_fn()` / eviction (`moralstack/server/conversation_correlation.py:188`, `moralstack/server/conversation_correlation.py:189`); `_put_id()` can raise from `self._time_fn()` / `popitem()` (`moralstack/server/conversation_correlation.py:198`, `moralstack/server/conversation_correlation.py:202`); `_handle_chat_completion_sync()` resolves `conversation_id` before its outer `try` starts (`moralstack/server/proxy.py:336`, `moralstack/server/proxy.py:356`).
  Required fix: make `ConversationCorrelationStore.resolve()` best-effort around TTL/eviction helper paths. On helper failure, mint and return a valid fresh `msconv-*` id; if a best-effort insert fails, swallow it. Returning an id must not depend on `_get_id()` / `_put_id()` succeeding.

## Non-blocking issues
- The README/env catalogue omission is plan-incomplete but not a governance blocker because the vars are mentioned in the trace/index docs (`docs/traces/openai_compatible_multiturn.md:125`, `docs/MORALSTACK_CODEBASE_INDEX.md:298`).

## Missing/weak tests
- Missing blocking regression test: no test injects a failing TTL/eviction helper path and asserts `resolve()` still returns an `msconv-*` id. Existing eviction tests cover normal eviction only (`tests/test_conversation_correlation.py:263`, `tests/test_conversation_correlation.py:276`).
- Missing proxy regression test: `test_correlation_store_bound_does_not_break_request_on_eviction` only exercises successful FIFO eviction (`tests/test_server_proxy.py:1454`); it would not catch `_time_fn()` or eviction helper exceptions propagating before governance.
- Weak hash test: the golden test names both hash functions but only directly asserts `canonical_history_hash()` outputs; `canonical_parent_history_hash()` is not imported or directly pinned (`tests/test_conversation_correlation.py:11`, `tests/test_conversation_correlation.py:62`).

## Security issues
None found in the reviewed diff. The bearer-token path does not log the raw token or digest: `Authorization` is filtered from safe headers (`moralstack/server/proxy.py:155`, `moralstack/server/proxy.py:169`), the debug line logs only `safe_headers` (`moralstack/server/proxy.py:686`), and `_extract_principal()` reads the secret per request (`moralstack/server/proxy.py:212`).

## Performance issues
None found. The correlation map is bounded by TTL and max entries (`moralstack/server/conversation_correlation.py:125`, `moralstack/server/conversation_correlation.py:202`); `_locks` remains unbounded by approved deferral only (`moralstack/server/proxy.py:90`).

## Maintainability issues
- Add the new env vars to the README configuration table or the repo's chosen env catalogue; otherwise operators reading the main config docs will miss the new proxy controls (`README.md:388`, `README.md:401`).

## Required fixes
- Wrap best-effort TTL/eviction work in `ConversationCorrelationStore.resolve()` so helper failures never raise into the proxy and always return a valid minted conversation id.
- Add tests that force helper failure on the `resolve()` path and through `create_app(correlation_store=...)` / `/v1/chat/completions`.
- Add the three new env vars to the env-var catalogue required by the handoff.

## Suggested fixes
- Add direct golden/behavior assertions for `canonical_parent_history_hash()`.
- Add a `create_app`-level valid-env test that proves parsed TTL/max values reach the created default `ConversationCorrelationStore`, not only `_resolve_correlation_store_env_config()`.
