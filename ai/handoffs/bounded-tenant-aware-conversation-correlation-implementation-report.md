# Implementation report — Bounded, tenant/principal-aware conversation correlation store (P3 / P0-3 / A3)

Implementer: `claude-implementer` (Claude Sonnet), isolated sub-agent context.
Handoff: `ai/handoffs/bounded-tenant-aware-conversation-correlation-handoff.md`.
Plan: `ai/plans/bounded-tenant-aware-conversation-correlation.md`.

## Summary
Made `ConversationCorrelationStore` bounded (TTL + max-entries eviction) and tenant/principal-aware, keying
the internal lineage map on `(principal, history_hash)` without touching the two hash functions.

## Files modified
- `moralstack/server/conversation_correlation.py` — `canonical_history_hash`/`canonical_parent_history_hash`
  untouched. Added `_Entry` dataclass, `DEFAULT_CORRELATION_TTL_SECONDS=3600`,
  `DEFAULT_MAX_CORRELATION_ENTRIES=20_000`, replaced `dict[str,str]` with `OrderedDict[tuple[str,str],_Entry]`,
  added `ttl_seconds`/`max_entries`/`time_fn` constructor params, `size()`, `_get_id`/`_put_id`;
  `resolve`/`observe_completed_turn` gained keyword-only `principal: str = ""`.
- `moralstack/server/proxy.py` — added `_extract_principal(request)` (A: `X-Moralstack-Tenant-Id` → B:
  HMAC-SHA256 of Bearer token via per-request `os.environ.get("MORALSTACK_PRINCIPAL_HMAC_SECRET")` → C: `""`);
  threaded `principal` through `_resolve_conversation_id_from_body_and_correlation`,
  `_handle_chat_completion_sync`, `observe_completed_turn`; added `_resolve_correlation_store_env_config()`
  (range-validated, never raises) and `create_app(correlation_store=...)`; added the `# TODO(P3-followup)`
  deferral comment on `ConversationLockManager._locks` (`:90-96`).
- `tests/test_conversation_correlation.py` — added golden-digest lock, principal isolation/no-leak, TTL
  (expiry, hit-path-no-refresh, lineage-survives-delay), maxsize FIFO eviction, concurrency, constructor
  validation tests (all 12 plan items). Existing 3 tests untouched, all pass.
- `tests/test_server_proxy.py` — added tenant-header isolation, anonymous-fallback,
  HMAC-derivation-with-no-leak (log + `_collect_safe_headers` check), secret-unset fallback, precedence
  (tenant>auth, header>correlation), bound-does-not-break-request, cross-path lineage chaining,
  `_extract_principal` unit tests, env-wiring tests (all 10 plan items, 13-22). Existing tests untouched.
- `docs/MORALSTACK_CODEBASE_INDEX.md`, `docs/CODEBASE_FACTS.md`, `docs/traces/openai_compatible_multiturn.md`
  — updated per plan §Files, including the ledger-key correction (`ledger.py:262`, not `:254`) and refreshed
  `proxy.py` citations in the affected section.

## Commands run / results (real output, as reported by the sub-agent)
- `python -m pytest tests/test_conversation_correlation.py tests/test_server_proxy.py -q` → `76 passed, 94 warnings`
- `python -m pytest -q` (full suite) → `2208 passed, 1 deselected, 119 warnings in 142.72s`
- `python -m pre_commit run -a` → all hooks Passed (trailing whitespace, end-of-files, ruff, black, mypy,
  changelog guard, memory guard)

## Deviations from the plan
None in code/tests/invariants. One doc-scope note: the handoff's "env-var catalogue doc (grep for
`MORALSTACK_SESSION_`)" pointer did not resolve — no such catalogue exists in the repo (session store has no
env vars), and there is no dedicated env-var catalogue file. The three new vars
(`MORALSTACK_CORRELATION_TTL_SECONDS`, `MORALSTACK_CORRELATION_MAX_ENTRIES`, `MORALSTACK_PRINCIPAL_HMAC_SECRET`,
incl. the TTL-alignment caveat) were documented inline in `docs/MORALSTACK_CODEBASE_INDEX.md` and
`docs/traces/openai_compatible_multiturn.md` instead.

## Residual problems / blockers
None. `docs/modules/server_proxy.md` exists and PROJECT_SPEC §8 generically suggests updating module-contract
docs, but it is not in the handoff's allowed-files list, so it was left untouched (flagged as optional
follow-up). `ConversationLockManager._locks` unbounded growth remains deferred exactly as the plan specifies
(TODO comment + FACTS note, no implementation).
