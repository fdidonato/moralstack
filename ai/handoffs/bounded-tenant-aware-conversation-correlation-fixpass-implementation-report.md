# Fix-pass implementation report — Bounded, tenant/principal-aware conversation correlation store (P3/P0-3/A3)

Implementer: `claude-implementer` (Claude Sonnet), isolated sub-agent context.
Fix-pass handoff: `ai/handoffs/bounded-tenant-aware-conversation-correlation-fixpass-handoff.md`.
Resolves the BLOCK in `ai/reviews/codex-diff-review-bounded-tenant-aware-conversation-correlation-20260709-115102.md`.

## Files modified
- `moralstack/server/conversation_correlation.py` — FIX 1: added `logging`/`_LOG`; rewrote `resolve()` to wrap
  the hit-lookup/parent-inherit branch and the mint-insert branch each in a swallowing `try/except Exception`
  (debug-logged, no content/secrets) so a `_get_id`/`_put_id` failure never escapes `resolve()`; a valid
  `msconv-<uuid16>` id is always returned. `observe_completed_turn`, the hash functions, and `_get_id`/`_put_id`
  themselves untouched (helper unit tests still see real behavior).
- `tests/test_conversation_correlation.py` — FIX 2 + FIX 4 tests (additions only).
- `tests/test_server_proxy.py` — FIX 2 + FIX 4 tests (additions only, plus a test-local helper).
- `README.md` — FIX 3: three env-var rows in the "Key variables" table.
- `docs/CODEBASE_FACTS.md` — one verified-facts row documenting `resolve()` best-effort behavior.

`moralstack/server/proxy.py` was NOT modified in this fix-pass (its `M` status predates this pass — prior
implementation left it uncommitted).

## Tests added
- `test_conversation_correlation.py`: `TestResolveBestEffort::{test_resolve_survives_lookup_failure_and_mints_fresh_id,
  test_resolve_survives_insert_failure_and_still_returns_id, test_resolve_survives_time_fn_failure_during_eviction}`;
  `TestCanonicalHistoryHash::test_canonical_parent_history_hash_golden_digest`.
- `test_server_proxy.py`: `TestPrincipalAwareCorrelation::test_correlation_store_raising_helper_does_not_break_request`;
  `TestCorrelationEnvWiring::test_env_wiring_valid_values_reach_default_store`.

## Commands run (real output)
- `python -m pytest tests/test_conversation_correlation.py -q` → 24 passed
- `python -m pytest tests/test_server_proxy.py -q -k "CorrelationEnvWiring"` → 4 passed, 54 deselected
- `python -m pytest tests/test_conversation_correlation.py tests/test_server_proxy.py -q` → 82 passed
- `python -m pytest` → **2214 passed, 1 deselected** in 88.92s
- `python -m pre_commit run -a` → all hooks Passed (trailing whitespace, end-of-files, ruff, black, mypy,
  changelog guard, memory guard)

## Deviations
None on substance. Note: FIX 4's env-reaching test reads the default `ConversationCorrelationStore` built inside
`create_app` (not exposed on `app.state`, and `proxy.py` was off-limits) via the `chat_completions` closure
(`route.endpoint.__closure__`) — pure test-side introspection, zero production change; satisfies the acceptance
criterion as written.

## Residual problems / blockers
None. `git status` confirms only allowed files changed; `proxy.py`, `session_store.py`, `fingerprint.py`,
`orchestration/`, `pyproject.toml`, and INDEX/trace docs untouched. Both test files are additions-only; the
behavior-locking tests are unmodified and green.
