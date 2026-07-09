# Fix-pass handoff — Bounded, tenant/principal-aware conversation correlation store (P3 / P0-3 / A3)

You are the **MoralStack Implementer** (`claude-implementer`, Claude Sonnet, isolated sub-agent context).
This is a **FIX-PASS** on top of an already-implemented change that a diff review **BLOCKED**. The prior
implementation is already in the working tree (uncommitted). Apply ONLY the fixes below. Do not re-do the
whole feature, do not revert existing work, do not commit.

## Context
- Approved plan: `ai/plans/bounded-tenant-aware-conversation-correlation.md`.
- Prior implementation report: `ai/handoffs/bounded-tenant-aware-conversation-correlation-implementation-report.md`.
- **Diff review that BLOCKED it:** `ai/reviews/codex-diff-review-bounded-tenant-aware-conversation-correlation-20260709-115102.md`.
  Read it. The verdict was `BLOCK` on ONE issue, plus non-blocking/suggested items. This fix-pass resolves them.
- The plan is correct; this is an **implementation defect**, not a design change. Do not change the design.

## Objective (exactly these fixes, nothing else)

### FIX 1 — BLOCKING: make `ConversationCorrelationStore.resolve()` best-effort (invariant #6)
Today `resolve()` (`moralstack/server/conversation_correlation.py:140-159`) calls `_get_id()` / `_put_id()`
with no exception guard, and the proxy resolves `conversation_id` at `proxy.py:337-339` **before** its outer
fail-closed `try:` (`proxy.py:356`). So an exception from `_get_id`/`_put_id` (e.g. from `self._time_fn()` at
`:188`/`:200` or `popitem()` at `:203`) propagates out of the endpoint instead of yielding a governed response
— violating PROJECT_SPEC §5 invariant #6 and the plan's Target-behavior #3 / acceptance criterion ("a
swallowed eviction failure inside `resolve()` must still fall through to minting a valid fresh id").

Required change (keep it surgical, inside `conversation_correlation.py`):
- Make `resolve()` never raise from the TTL/eviction helper paths. Wrap the lookup/insert work in a swallowing
  `try/except Exception` that logs at debug (use the module `logging`, Italian message per repo convention;
  do NOT log message content or any secret) and **falls through to minting and returning a fresh
  `msconv-<uuid16>` id**. Returning a valid id must NOT depend on `_get_id()`/`_put_id()` succeeding.
- Recommended shape (adapt to the actual code): compute the two hashes (these do not raise); then attempt the
  hit-lookup, parent-inherit, and inserts inside best-effort guards; if any helper raises, log-debug and
  proceed to mint a new id (and best-effort attempt to store it, swallowing a failure there too). The minted
  id is always returned.
- Do NOT broaden this to a blanket bare `except:`; catch `Exception`, log with context, and continue. Do NOT
  swallow inside `_get_id`/`_put_id` themselves in a way that hides bugs from unit tests — the swallowing lives
  in `resolve()` so the helper unit tests still see real behavior. (You may keep `observe_completed_turn` as is:
  its call site is already wrapped at `proxy.py:418-425`.)
- Do NOT change `canonical_history_hash` / `canonical_parent_history_hash` (still byte-for-byte frozen).
- Do NOT touch `proxy.py` for this — the fix belongs in `resolve()`. If you believe a proxy-side guard is truly
  required, STOP and report instead of editing proxy.py.

### FIX 2 — BLOCKING test coverage: prove `resolve()` stays best-effort
- In `tests/test_conversation_correlation.py`: add a test that injects a **failing helper path** and asserts
  `resolve()` still returns a valid `msconv-*` id. Cleanest injection: construct the store with a `time_fn`
  that raises (e.g. `time_fn=Mock(side_effect=RuntimeError)`) after a first successful call, or monkeypatch
  `_get_id`/`_put_id` to raise; assert `resolve(...)` returns a string starting with `msconv-` and does not
  raise. Cover both the lookup-failure and the insert-failure branches.
- In `tests/test_server_proxy.py`: add a test that injects a correlation store whose helper raises (via
  `create_app(correlation_store=...)`), sends a `/v1/chat/completions` request with NO explicit
  `X-Moralstack-Conversation-Id`, and asserts the endpoint still returns 200 with a governed response (the
  exception does not escape before the fail-closed block). This complements the existing
  `test_correlation_store_bound_does_not_break_request_on_eviction` (which only covers successful eviction).

### FIX 3 — NON-BLOCKING: add the three new env vars to the README config table
The env-var catalogue is `README.md` → "Configuration → Key variables" table (`README.md:397-409`), which the
prior pass missed. Add rows for `MORALSTACK_CORRELATION_TTL_SECONDS` (default `3600`),
`MORALSTACK_CORRELATION_MAX_ENTRIES` (default `20000`), and `MORALSTACK_PRINCIPAL_HMAC_SECRET` (default
*(unset)*; when unset the HMAC principal path is disabled). Match the table's existing style; note the
TTL-alignment caveat briefly in the Description cell for the TTL var. Do not restructure the table.

### FIX 4 — SUGGESTED (do it, cheap): strengthen the hash golden test + env-reaches-store test
- In `tests/test_conversation_correlation.py`: extend the golden-digest test to ALSO pin
  `canonical_parent_history_hash()` directly (import it, assert a hard-coded pre-change hex digest for a fixed
  multi-turn input, and assert the empty-history-root case). Currently only `canonical_history_hash` is pinned.
- In `tests/test_server_proxy.py`: add a `create_app`-level test proving parsed valid env values
  (`MORALSTACK_CORRELATION_TTL_SECONDS` / `MORALSTACK_CORRELATION_MAX_ENTRIES`) actually reach the created
  default `ConversationCorrelationStore` (e.g. assert the app's store `_ttl_seconds`/`_max_entries`), not only
  that `_resolve_correlation_store_env_config()` parses them.

## Files ALLOWED to modify
- `moralstack/server/conversation_correlation.py` — FIX 1 only (best-effort `resolve()`; add `logging` if not
  already imported).
- `tests/test_conversation_correlation.py` — FIX 2 + FIX 4 tests.
- `tests/test_server_proxy.py` — FIX 2 + FIX 4 tests.
- `README.md` — FIX 3 config-table rows.
- `docs/CODEBASE_FACTS.md` — one-line note that `resolve()` is best-effort (never raises into the request; mints
  a fresh id on helper failure), to keep FACTS accurate and satisfy the docs Stop gate.

## Files NOT to modify
- `moralstack/server/proxy.py` — NO change in this fix-pass (the fix is in `resolve()`; if you think otherwise,
  STOP and report).
- `moralstack/sdk/session_store.py`, `moralstack/server/fingerprint.py`, anything under
  `moralstack/orchestration/`, `pyproject.toml`.
- The bodies/signatures of `canonical_history_hash` / `canonical_parent_history_hash`.
- Any existing test assertions; the 7 behavior-locking tests must stay green unmodified.
- `docs/MORALSTACK_CODEBASE_INDEX.md` / `docs/traces/openai_compatible_multiturn.md` — already updated in the
  prior pass; leave them.

## Invariants in play (PROJECT_SPEC §5)
- **#6 Observability never breaks the request** — this fix-pass is precisely about honoring it on the
  `resolve()` request path: best-effort, swallow-and-continue, always return a valid minted id, never raise
  into `_handle_chat_completion_sync`.
- No P0 decision/governance invariant is touched. If your change starts affecting `final_action`, prompt bytes,
  or delivery, STOP — you are off-plan.

## Verification commands (run and report REAL output)
```
python -m pytest tests/test_conversation_correlation.py tests/test_server_proxy.py -q
python -m pytest
python -m pre_commit run -a
```
All must be green before declaring done.

## Acceptance criteria
- `resolve()` never raises from TTL/eviction helpers; on helper failure it returns a valid `msconv-*` id
  (unit test proves it; proxy endpoint test proves the request still returns 200 governed).
- The three env vars appear in the README config table.
- `canonical_parent_history_hash()` is directly pinned by a golden digest; a `create_app`-level test proves
  valid env values reach the default store.
- The 7 behavior-locking tests still pass unmodified; full `pytest` green; `pre-commit run -a` green.
- Only the allowed files changed; `proxy.py` untouched; hash functions untouched.

## Constraints on your run
- Do NOT commit, push, `git add`, amend, tag, or use `--no-verify`. Do NOT delete/weaken tests. Do NOT touch
  do-not-modify files. If the fix is ambiguous or you hit a blocking problem, STOP and report.

## Required output at end of run
- files modified; tests added; commands run; results (REAL output, incl. any failures); deviations; residual
  problems / blockers.
