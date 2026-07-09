<!--
Plan reviewed: ai/plans/bounded-tenant-aware-conversation-correlation.md
Reviewer: independent plan review via the fable model (claude-fable-5), read-only.
Context: the Codex CLI runtime stalled server-side (~07:29Z) without emitting a
verdict and was reconciled/cleaned; on the user's instruction the review was
re-run with fable using the same review rubric/prompt that would have gone to Codex.
The heading below is kept as "Codex Plan Review" because the review template
mandates that exact structure; the reviewing model was fable, not Codex.
-->

# Codex Plan Review

## Verdict
`APPROVE_WITH_CHANGES`

## Blocking issues

1. **The recommended §5 lock idle-prune mechanism has a concrete race that breaks the serialization invariant.** `ConversationLockManager.acquire` fetches the lock reference under `_meta_lock` and then blocks on `lock.acquire(timeout=...)` *outside* it (`moralstack/server/proxy.py:97-101`). In the window between releasing `_meta_lock` (after `proxy.py:100`) and calling `lock.acquire()` (`proxy.py:101`), the proposed pruner would observe the lock as "currently unlocked", non-blocking-acquire it, and delete it from `_locks`. The waiting thread then acquires the orphaned lock object it still references, while a subsequent request for the same `conversation_id` mints a *new* lock at `proxy.py:98-99` — two live locks for one conversation, i.e. serialization broken. "Only prune locks acquired non-blocking under `_meta_lock`" (plan :84, :163) does **not** close this window, because the about-to-acquire waiter holds a reference invisible to the pruner. The canary test `test_concurrent_same_conversation_serialized` (`tests/test_server_proxy.py:602-637`) would not reliably catch it (prune only fires above the soft cap; the race is timing-dependent). **Required change:** flip the §5 recommendation — make deferral of `_locks` bounding (with TODO) the primary path, or specify a refcounted design (waiter count incremented under `_meta_lock` *before* the blocking acquire; prune only entries with `waiters == 0` and unlocked). Do not ship the sketched unlocked-check prune. The plan's own escape hatch (":84 — defer if review flags any race") is hereby triggered.

## Non-blocking issues

- **Plan mischaracterizes the trace doc it cites, and the trace doc itself is stale.** Plan :9 says the trace ":98-104 lists the lock and SessionStore as the shared surfaces". In fact `docs/TRACES/openai_compatible_multiturn.md:100-101` *also* lists "the ledger key (`ledger.py:254`)" as a collision-shared surface. The code wins: `ledger.py` contains no occurrence of `conversation_id` at all, and the ledger key is `(contract_hash, posture, domain)` (`moralstack/orchestration/ledger.py:50`, `:262`); `ledger.py:254` is actually a `LedgerResult` return, not a key. The plan's *conclusion* (ledger unaffected) is correct, but the scheduled revision of the Collision-risks section (plan :102) must explicitly correct the trace's stale ledger sentence per PROJECT_SPEC §9 (code wins), not just add the salt/principal narrative.
- **Unlisted caller of `canonical_history_hash`:** `moralstack/server/fingerprint.py:23,52` (`compute_conversation_fingerprint`, diagnostics-only `msf-` fingerprint). Verified unaffected — the call at `:52` passes no salt, so the keyword-default empty salt keeps its digest byte-identical. The plan should list it as a verified-unaffected caller in the blast-radius section so the implementer doesn't "fix" it.
- **`X-Moralstack-Tenant-Id` will appear in debug logs.** It starts with `x-`, so it passes `_collect_safe_headers` (`proxy.py:154`) and is logged in the correlation diagnostics debug line (`proxy.py:580-588`). Acceptable for a tenant *identifier*, but the plan should state it explicitly — if operators derive tenant ids from secrets, this leaks. Path-B principals (HMAC digests) should likewise never be added to any log line.
- **Shared eviction budget across principals.** All tenants share one `max_entries` pool; a noisy tenant can evict another tenant's lineage entries (cross-tenant availability effect, mid-conversation id split). Not a confidentiality issue, but document it; a per-principal cap is a reasonable follow-up note.
- **Bearer-token rotation mid-conversation (path B) changes the principal** → lineage silently splits into a new `conversation_id`. Document as expected behavior.
- **`resolve()` hit-path TTL semantics unspecified.** Today the hit path returns without touching the entry (`conversation_correlation.py:104-105`); the plan's `_put_id` refresh applies only to writes. Decide and state whether a read refreshes `inserted_at` (recommend: no, matching `InMemorySessionStore.get`, `session_store.py:99-110`) so test 8's expiry expectation is well-defined.
- **Env-skew risk in the TTL-alignment argument.** Plan :67 relies on correlation TTL == session TTL (3600). If ops override `MORALSTACK_CORRELATION_TTL_SECONDS` without the session-store TTL, the dangling-lineage/orphaned-state protection silently degrades. One sentence in the env docs suffices.
- Trivial: §1 pseudocode computes the unsalted blob and then discards it when salt is set — compute once per branch.

## Missing tests

- **A frozen golden digest.** Test 1 reimplements the algorithm inline, which is sound *only if* the reimplementation includes the normalization (`_canonical_message_record`/`_normalize_content` logic), not calls into it — otherwise a normalization change passes tautologically. The plan should say so explicitly, and additionally pin **one hard-coded hex digest literal** (computed pre-change) for a fixed input. That is immune to any accidental re-derivation of a shared bug and is the cheapest possible lock on "every production `msconv-` lineage".
- **Positive lineage survival under TTL:** a 3-turn conversation where turn-1 entries have already expired but chaining still works because each turn writes fresh request/completed hashes — this locks the plan's own claim (:33, :161) that TTL only needs to exceed *inter-turn* delay, not conversation duration.
- **Empty-list equivalence lock:** assert `canonical_history_hash([]) == _EMPTY_HISTORY_CANONICAL_HASH` (`conversation_correlation.py:26`) with empty salt — the salted-root design at plan :50 silently depends on this identity (verified true this session: `json.dumps([]) == "[]"`).
- **Mixed-principal concurrency:** threads racing with *different* principals on identical histories (test 10 uses distinct histories only).
- If path B ships: assert the derived principal digest never appears in captured log output (test 14 covers the raw token; extend to the digest).

## Risky assumptions

- **The core byte-compat claim is VERIFIED TRUE.** Current blob is exactly `json.dumps(normalized, sort_keys=True, ensure_ascii=False, separators=(",", ":"))` (`conversation_correlation.py:67-68`); the plan's empty-salt path reproduces it literally, so the digest is byte-identical. Envelope domain separation is sound: unsalted blobs always start with `[`, salted with `{` — no cross-domain collision; the JSON dict envelope defeats the `principal="}"` crafting edge (plan :141). The salted empty-history root at `:84` and the salt-threading through `canonical_parent_history_hash(messages[:-1])` at `:85` are coherent as designed.
- All spot-checked `path:line` citations are accurate: dict `:97`, resolve `:99-114`, observe `:116-129`, resolver `proxy.py:120-135`, sensitive markers `:138-144`, sync handler `:254`/`:275-276`, `store.get`/`put` `:318`/`:374-375`, observe call `:419-423`, `create_app` `:482-488`/`:507`, route `:551-588`/`:553`/`:578`, threadpool `:590-604`, `_locks` `:83`/`:97-100`; session store `sdk/session_store.py:53,57-62,89-92,133-140,166-169,176-178,184-224`; tests `test_conversation_correlation.py:12-42` (exactly 4 cases, no auth headers), `test_server_proxy.py:75-94,602-637,779-853`, `test_session_store.py:114-129,132-141,179-204`; `cachetools` absent from `pyproject.toml`; single-worker note at trace `:113`.
- **Single-worker assumption** (plan :26): correct per the trace, but bounding and principal isolation are per-process; a future multi-worker deployment silently changes semantics. Already documented — keep it in the trace update.
- Minor: `test_concurrent_same_conversation_serialized` uses an explicit `X-Moralstack-Conversation-Id` header (`test_server_proxy.py:624`), so it exercises the lock manager but not the correlation path — fine for the plan's use of it as the `_locks` canary, weak as a prune-race detector (see blocking issue 1).

## Architecture concerns

- **A materially simpler alternative was not considered: composite map key `(principal, hash)`.** The plan's rejection at :92 addresses a strawman (prefixing the *conversation_id*), not keying the *map* by `(principal, existing_hash)`. Composite keying achieves identical isolation with **zero** change to the hash functions — eliminating the entire byte-equality risk class (the plan's own top risk, :160), tests 1-3, the salted-root special case, and the `fingerprint.py` blast-radius question. The digests are process-local map keys only (verified: no persistence or cross-component use), so nothing needs the salt to live inside the digest. Trade-off in salting's favor: protection if future code hashes without going through the store. Under PROJECT_SPEC §6 (smallest change), composite keying looks strictly smaller and safer; the plan should either adopt it or record a concrete reason to prefer digest salting.
- `create_app(correlation_store=)` plus 3 new env vars is an additive public API/config surface change; the plan flags the env vars for review but should flag the constructor param too (user rules require sign-off on public API surface).
- The `time_fn` seam diverges from the mirrored `InMemorySessionStore` pattern (which uses real `sleep()` tests). Justified for determinism, but do it knowingly; do not retrofit the session store in this change.
- Invariant check: no P0 invariant is touched. Decision/generation separation, hard-signal supremacy, prompt transparency (byte-equality here concerns hash blobs, not prompt composition), and governed delivery are unaffected; invariant #6 is respected provided eviction failures inside `resolve()` still fall through to minting a fresh id (state this explicitly — eviction is on the request path, not telemetry, so "swallow" must still return a valid id), and the `observe_completed_turn` call site is already wrapped (`proxy.py:418-425`).

## Security/performance concerns

- **HMAC handling is sound as specified:** secret from `MORALSTACK_PRINCIPAL_HMAC_SECRET`, skip-B (never a hardcoded fallback) when unset, raw token never logged (stays behind `_SENSITIVE_HEADER_MARKERS`, `proxy.py:138-144`), digest-only output. No constant-time comparison needed (no secret comparison occurs). Use `hmac.new(...)`, not plain `sha256(secret+token)`.
- **Header forgery is correctly characterized** as a trust-boundary limitation, not a fix — path A is only as strong as the fronting layer stripping client-supplied `X-Moralstack-Tenant-Id`. The plan surfaces this honestly (:35, :81, :162). Correct posture.
- **Empty-string sentinel is rigorously justified** (:79, :91): any non-empty literal would change every digest and break the behavior-locking tests. Correct — and it doubles as the operational rollback path (:191).
- Performance: negligible. Two SHA-256s per request (unchanged), O(1) OrderedDict ops, amortized eviction, one HMAC per request in the async route (microseconds). The 20k `max_entries` sizing rationale (2 hashes/turn vs 1 session/conversation) is coherent.

## Suggested plan changes

1. §5: make **deferral** (or the refcounted-waiter design) the primary recommendation for `_locks`; strike the unlocked-check prune as written (blocking issue 1).
2. Alternatives section: add and explicitly decide the **composite `(principal, hash)` map key** option; if salting is kept, record why.
3. Blast radius: list `moralstack/server/fingerprint.py:52` as a verified-unaffected caller.
4. Docs step (:102): explicitly correct the trace's stale "ledger key (`ledger.py:254`)" sentence, citing `ledger.py:262`.
5. Test 1: require the inline reference to reimplement normalization too, and add one frozen golden-digest literal.
6. Add the positive TTL lineage-survival test and the `canonical_history_hash([]) == _EMPTY_HISTORY_CANONICAL_HASH` lock.
7. Design note: define hit-path TTL semantics (recommend no refresh on read) and state that a swallowed eviction failure in `resolve()` must still return a freshly minted id (invariant #6 on the request path).
8. Security notes: mention that `X-Moralstack-Tenant-Id` appears in debug `safe_headers`, that B-path digests must not be logged, and that token rotation splits lineage.

## Questions for Claude/User

1. Composite `(principal, hash)` map key vs digest salting — the former removes the byte-equality risk entirely and shrinks the diff; do you want the plan revised to it, or is there a forward-looking reason (future out-of-store hashing) to keep the salt in the digest?
2. Sign-off needed on the additive API/config surface: `create_app(correlation_store=)` param and the three env vars (`MORALSTACK_CORRELATION_TTL_SECONDS`, `MORALSTACK_CORRELATION_MAX_ENTRIES`, `MORALSTACK_PRINCIPAL_HMAC_SECRET`) — ship in this cut or defer env wiring?
3. `_locks` bounding: defer entirely (correlation bound already removes the dominant growth, ~2 entries/turn vs 1 lock/conversation) or invest in the refcounted design now?
4. Should path B (HMAC of bearer) ship in the first cut at all, or is A+C sufficient for the benchmark scenario driving P3?
