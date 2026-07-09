# Plan — Bounded, tenant/principal-aware conversation correlation store (P3 / P0-3 / A3)

## Review resolution (2026-07-09)
This plan was independently reviewed (verdict `APPROVE_WITH_CHANGES`, 1 blocking issue) — see
`ai/reviews/codex-plan-review-bounded-tenant-aware-conversation-correlation-20260709-100530.md`.
Owner decisions taken after the review:
- **Isolation mechanism → composite map key `(principal, history_hash)`**, NOT a salt folded into the
  digest. The hash functions (`canonical_history_hash`, `canonical_parent_history_hash`) stay
  **byte-for-byte unchanged**; only the correlation store's internal map keys change. This removes the
  entire byte-equality risk class, the salted empty-history-root special case, and the salt tests that
  the salt design required (reviewer's recommended simpler alternative; PROJECT_SPEC §6 smallest change).
- **Principal derivation A+B+C ships in this cut** (trusted `X-Moralstack-Tenant-Id` header → HMAC of the
  bearer token → empty-string sentinel).
- **`ConversationLockManager._locks` bounding is DEFERRED** with a documented TODO — the reviewer proved
  the sketched idle-prune has a real race (see §5). The correlation-store bound already removes the
  dominant growth source.
- **Config surface: ship `create_app(correlation_store=)` + 3 env vars** (`MORALSTACK_CORRELATION_TTL_SECONDS`,
  `MORALSTACK_CORRELATION_MAX_ENTRIES`, `MORALSTACK_PRINCIPAL_HMAC_SECRET`).
Non-blocking review fixes folded in: correct the stale trace ledger sentence, list `fingerprint.py` as a
verified-unaffected caller, golden-digest lock, positive TTL lineage-survival test, hit-path TTL semantics,
and the extra security notes.

**Second review (2026-07-09, verdict `APPROVE`, no blockers)** —
`ai/reviews/codex-plan-review-bounded-tenant-aware-conversation-correlation-20260709-103140.md`. Six minor
spec gaps folded into this revision (owner defaults adopted): HMAC secret read **per-request** via
`os.environ.get` + `.encode()` to bytes; env wiring **range-validates** (ttl>0, max_entries>=1) and falls back
to defaults so `create_app` never raises even on `-5`; `_Entry.inserted_at` passed **explicitly**
(`inserted_at=self._time_fn()`) at each insert site (a dataclass default cannot see the instance clock);
non-Bearer/malformed `Authorization` → skip to C; the trace pass refreshes **all** drifted `proxy.py`
citations in `:68-108`, not only the ledger sentence; added env-wiring + `_extract_principal` unit tests and
an explicit-header→correlation cross-path lineage test under a non-empty principal; test 14 strengthened.
`_locks` follow-up stays a TODO + FACTS note (no separate plan yet); tenant-id length not capped (bounded by
server header limits). Plan is **APPROVED** and ready for `/ai-implement`.

## Goal
Make the proxy conversation correlation store bounded (TTL + maxsize eviction) and key its lineage map by
`(principal, history_hash)` so byte-identical histories from different tenants no longer collide onto one
`conversation_id` — without changing the history-hash digests at all.

## Current behavior
- `ConversationCorrelationStore` backs lineage mapping with an unbounded plain dict:
  `self._history_to_conversation: dict[str, str] = {}` (`moralstack/server/conversation_correlation.py:97`).
  Entries are added on every resolve miss (`:113`), lineage inherit (`:109`), and `observe_completed_turn`
  (`:129`); nothing is ever removed → unbounded growth / OOM under long-running benchmarks.
- `canonical_history_hash(messages)` (`conversation_correlation.py:61-69`) is exactly
  `sha256(json.dumps(normalized, sort_keys=True, ensure_ascii=False, separators=(",", ":")))`
  (`:67-68`); `canonical_parent_history_hash(messages)` hashes `messages[:-1]` when the last message is a
  user turn, else returns `_EMPTY_HISTORY_CANONICAL_HASH` (`:72-85`, root constant `:26`). The store keys
  the map purely by this digest, so two tenants with identical histories map to the **same**
  `conversation_id` (verified: `resolve()` keys on `canonical_history_hash(messages)` alone, no caller
  identity involved — `:99-114`).
- The resolved `conversation_id` keys the per-conversation lock (`ConversationLockManager`,
  `proxy.py:71-117`) and the `SessionStore` entry (`store.get` `proxy.py:318`, `store.put` `:374-375`).
  So collisions cause cross-tenant lock serialization and governance-state sharing. **The ledger is NOT
  keyed by `conversation_id`** — its key is `(contract_hash, posture, domain)`
  (`moralstack/orchestration/ledger.py:50`, `:262`; `ledger.py` contains no `conversation_id` at all), so
  it is unaffected. NOTE: `docs/TRACES/openai_compatible_multiturn.md:100-101` stalely lists
  "the ledger key (`ledger.py:254`)" as a collision-shared surface — that is wrong (`ledger.py:254` is a
  `LedgerResult` return, not a key); this docs revision must correct it per PROJECT_SPEC §9 (code wins).
- `ConversationLockManager._locks: dict[str, threading.Lock] = {}` (`proxy.py:83`) is also unbounded — one
  lock created per distinct `conversation_id` (`:97-100`), never removed.
- Auth headers are treated as sensitive and stripped from debug logs, and are NOT used for routing:
  `_SENSITIVE_HEADER_MARKERS` includes authorization/api-key (`proxy.py:138-144`), filtered in
  `_collect_safe_headers` (`:147-158`). NOTE: `X-Moralstack-Tenant-Id` starts with `x-` and therefore
  passes `_collect_safe_headers` (`:154`) and appears in the correlation debug line (`:580-588`).
- Resolution precedence today: header `X-Moralstack-Conversation-Id` (`proxy.py:275-276`, read via
  `Header(alias=...)` at `:553`) > `extra_body.moralstack_conversation_id` (`:131-134`, `:578`) >
  `correlation_store.resolve(messages)` (`:135`).
- Diagnostics-only caller: `moralstack/server/fingerprint.py:23,52` (`compute_conversation_fingerprint`,
  `msf-` fingerprint) calls `canonical_history_hash` with no extra args — **verified unaffected** because
  the hash function is unchanged and gains no required parameter.
- `create_app` builds one process-singleton `ConversationCorrelationStore()` (`proxy.py:507`) and one
  `ConversationLockManager()` (`:506`).

## Target behavior
1. The correlation store is bounded: entries expire after a TTL and the store never exceeds a max size
   (oldest evicted first). Lineage chaining across turns still works within the TTL window.
2. The lineage map is keyed by `(principal, history_hash)`. Identical histories from different principals
   resolve to different `conversation_id`s. With no principal (the empty-string sentinel) the map key is
   `("", history_hash)` and all resolution behavior is identical to today. **The digests are untouched.**
3. Eviction/expiry is best-effort and never raises into the request handler; a swallowed eviction failure
   inside `resolve()` (which is on the request path, not telemetry) must still fall through to minting a
   valid fresh id (PROJECT_SPEC §5 invariant #6).
4. Header/extra_body precedence over correlation is unchanged.

## Assumptions
- Every citation above was re-verified this session against the current files (see path:line), including by
  the independent review.
- The four `test_conversation_correlation.py` cases and the three `test_server_proxy.py`
  multi-turn/concurrency cases send no auth/tenant header, so principal resolves to the empty sentinel and
  the `("", hash)` keying reproduces today's behavior; they must stay green unchanged
  (`tests/test_conversation_correlation.py:12-42`; `tests/test_server_proxy.py:779-853`).
- Because the hash functions are unchanged, `test_deterministic` and every digest-shape test pass trivially
  — there is no salt to break them.
- `InMemorySessionStore` is the reference pattern to mirror: RLock + OrderedDict, TTL via
  `_SessionEntry.inserted_at`, lazy expiry on read, `popitem(last=False)` on overflow, defaults
  ttl_seconds=3600 / max_sessions=10_000 (`moralstack/sdk/session_store.py:65-178`).
- cachetools is NOT a project dependency (verified absent from pyproject.toml).
- The proxy is documented single-worker for multi-turn (`docs/TRACES/openai_compatible_multiturn.md:113`),
  so a process-local store is the correct scope; no cross-process principal registry is in scope. Bounding
  and principal isolation are per-process; a future multi-worker deployment changes semantics (keep this in
  the trace update).

## Constraints
- Invariant #6 (Observability never breaks the request): all TTL/eviction paths wrapped in swallowing
  try/except, mirroring `InMemorySessionStore._emit_*` (`session_store.py:184-224`). On the request path
  (`resolve()`), a swallowed failure must still return a valid minted id. No new raise reaches
  `_handle_chat_completion_sync`.
- No P0 decision invariant touched. final_action computation, prompt transparency (this concerns map keys,
  not prompt composition), hard-signal supremacy, governed delivery are untouched — this change only affects
  `conversation_id` derivation and the lifetime of the correlation map. Confirmed by the map and the review.
- Backward compatibility (dominant): empty/absent principal ⇒ `("", hash)` keying ⇒ identical resolution to
  today. Digests are byte-identical because the hash functions do not change. Listed behavior-locking tests
  stay green with no modification.
- Precedence unchanged: header > extra_body > correlation. Principal only affects the third (correlation)
  path's map key.
- TTL > inter-turn delay so lineage survives real (slow) conversations. TTL only needs to exceed the
  inter-turn delay, not the whole conversation duration (each turn writes fresh request/completed entries).
- Scope: smallest change (PROJECT_SPEC §6). No new dependency. No change to the hash algorithm.
- Security posture: composite keying prevents accidental collision only; it is not an auth boundary. A
  hostile tenant forging the principal header still requires the trusted-fronting-layer assumption.
  Documented, not enforced here.

## Proposed design

### 1. Composite map key `(principal, history_hash)` — hash functions UNCHANGED
No change to `canonical_history_hash` (`:61-69`) or `canonical_parent_history_hash` (`:72-85`): no salt
param, no envelope, digests identical to today. Isolation is achieved entirely inside the store by keying
the internal map on the tuple `(principal, history_hash)` instead of the bare `history_hash`.
- `resolve(messages, *, principal: str = "")` computes `request_hash = canonical_history_hash(messages)` and
  `parent_hash = canonical_parent_history_hash(messages)` (both unchanged), then does all map reads/writes
  under the composite key `(principal, request_hash)` / `(principal, parent_hash)`.
- With `principal=""` the keys are `("", request_hash)` etc., so the keyspace and behavior match today for
  the no-principal path. Two principals with identical histories occupy disjoint key tuples → no collision.
- The empty-history root (`_EMPTY_HISTORY_CANONICAL_HASH`, `:26`, used by `canonical_parent_history_hash` at
  `:84`) is unchanged; different principals still don't collide on the parent path because the *map key*
  carries the principal. No salted-root special case is needed.

### 2. Thread principal through the store
- `resolve(messages, *, principal: str = "")`: as above; the rest of the resolution algorithm (`:103-114`)
  is unchanged except that lookups/inserts go through the composite-key helpers (§3).
- `observe_completed_turn(*, messages, assistant_content, conversation_id, principal: str = "")`: compute
  `completed_hash = canonical_history_hash(completed_messages)` (unchanged, `:127`) and write under
  `(principal, completed_hash)`.
- Default principal empty ⇒ current behavior.

### 3. Bound the correlation store (mirror InMemorySessionStore)
Replace the plain `dict[str, str]` with a bounded, RLock-protected `OrderedDict` keyed by
`tuple[str, str]` (principal, hash) inside `ConversationCorrelationStore`:
- Entry wrapper carrying `conversation_id` + `inserted_at` (dataclass), mirroring `_SessionEntry`
  (`session_store.py:57-62`). NOTE: unlike `_SessionEntry` (which hardcodes `time.time` in a
  `default_factory`), a dataclass default cannot see `self._time_fn`; every insert site must pass
  `inserted_at=self._time_fn()` explicitly, otherwise the fake-clock TTL tests break.
- Constructor `__init__(self, *, ttl_seconds=DEFAULT_CORRELATION_TTL_SECONDS,
  max_entries=DEFAULT_MAX_CORRELATION_ENTRIES, time_fn: Callable[[], float] = time.time)`: validate
  ttl_seconds > 0 and max_entries >= 1 (mirror `session_store.py:89-92`). **`time_fn` is an accepted seam**
  (test dependency #1) so TTL expiry is testable with a fake monotonic clock without real sleeps; do NOT
  retrofit `InMemorySessionStore`.
- `size() -> int` helper (accepted, test dependency #2), mirroring `InMemorySessionStore.size()`
  (`session_store.py:166-169`), so eviction tests can assert `store.size() <= max_entries`.
- Private `_get_id(key)`: read under the RLock, check expiry lazily (`self._time_fn() - inserted_at >
  ttl_seconds`, strict `>` so `age == ttl` is NOT expired, matching `session_store.py:176-178`),
  pop-and-return-None on expiry, else return the id. **Hit path does NOT refresh `inserted_at`** (matches
  `InMemorySessionStore.get`, `session_store.py:99-110`), so expiry is well-defined for tests.
- Private `_put_id(key, cid)`: under the RLock, delete-then-reinsert (refresh FIFO order like
  `session_store.py:133-135`) and, while `len > max_entries`, `popitem(last=False)`
  (`session_store.py:137-140`). Used by the write sites in resolve (`:109`, `:113`) and
  `observe_completed_turn` (`:129`).
- In the lineage-inherit branch (`:107-110`), only insert the new `(principal, request_hash)`; do not touch
  parent-entry timing (keep the diff small).
- Keep the RLock (already `threading.RLock()` at `:96`); it must stay reentrant because `resolve()` may
  recurse into locked eviction.

Recommended defaults and justification:
- `ttl_seconds = 3600` (1h), matching `InMemorySessionStore` (`session_store.py:53`). Far exceeds realistic
  inter-turn delays; aligning with the session-store TTL means a lineage entry and its session state expire
  on the same horizon, avoiding a dangling lineage pointing at an already-evicted session. (If ops override
  the correlation TTL without the session TTL this alignment degrades — noted in env docs.)
- `max_entries = 20_000`. The store holds ~2 hashes per turn (request + completed), roughly twice the
  session-store growth per conversation; doubling the session-store cap keeps headroom while staying a hard
  safety bound. TTL is the primary reclaim mechanism (same philosophy as `session_store.py:71-73`).
- All principals share one `max_entries` pool: a noisy tenant can evict another tenant's lineage entries
  (cross-tenant **availability** effect — mid-conversation id split, not a confidentiality leak). Documented;
  a per-principal cap is a reasonable follow-up.
- Configuration: constructor defaults, overridable via env at create_app wiring (see §4/Files). The env
  wiring must **range-validate** (ttl_seconds > 0, max_entries >= 1) and fall back to defaults, not only catch
  parse errors: the constructor mirrors `session_store.py:89-92` which `raise ValueError` on out-of-range
  values, so a value like `MORALSTACK_CORRELATION_TTL_SECONDS=-5` parses as a valid int and would raise at
  `create_app` unless the wiring clamps/rejects it first (or catches the `ValueError`). "Never raise at
  create_app" must hold for out-of-range values too.

### 4. Principal extraction (A + B + C, shipping in this cut)
Add a module-level helper `_extract_principal(request) -> str` and call it in the async route
`chat_completions` (`proxy.py:551-588`), passing the result into `_handle_chat_completion_sync` as a new
`principal` kwarg (added to the signature at `:254-267` and to the `run_in_threadpool` call at `:590-604`).
Inside the sync handler, thread principal into `_resolve_conversation_id_from_body_and_correlation` (add
param, forward to `correlation_store.resolve(messages, principal=principal)` at `proxy.py:135`) and into
`correlation_store.observe_completed_turn` (`:419-423`).

Layered derivation A → B → C:
- (A) Trusted internal header `X-Moralstack-Tenant-Id` — if present and non-empty, use it verbatim as the
  principal. Set by a trusted fronting layer. NOTE: this header passes `_collect_safe_headers` and appears in
  the debug line (`:154`, `:580-588`) — acceptable for an identifier, but operators must NOT derive it from a
  secret.
- (B) HMAC of the bearer token — if no tenant header but an `Authorization: Bearer <token>` is present,
  `principal = hmac.new(secret.encode(), token.encode(), sha256).hexdigest()`, `secret` from env
  `MORALSTACK_PRINCIPAL_HMAC_SECRET`. Use `hmac.new(...)`, never `sha256(secret+token)`; the env secret is a
  `str` and `hmac.new` needs `bytes`, so `.encode()` both. **Read the secret per-request** inside
  `_extract_principal` via `os.environ.get` (rotation-friendly, and monkeypatch-testable without app-build
  ordering constraints) — do NOT capture it once at `create_app`. Never log or store the raw token (it stays
  filtered by `_SENSITIVE_HEADER_MARKERS`, `proxy.py:138-144`; the helper reads `request.headers` directly and
  returns only the digest). The derived digest must ALSO never be added to any log line. If the secret env var
  is unset, skip B (no hardcoded fallback) and go to C — do this silently per-request (no per-request INFO log
  spam; if a "B disabled" signal is wanted, emit it once at create_app based on the env presence, not on the
  request path). Only `Authorization: Bearer <token>` triggers B; a non-Bearer scheme (`Basic ...`) or a
  malformed/empty value skips to C.
- (C) No principal ⇒ empty-string sentinel `""` — the `("", hash)` keyspace reproduces today's behavior
  exactly. Use the empty string (not `"anonymous"` or any literal); a non-empty literal would occupy a
  different keyspace than the existing no-principal tests expect. This is the path all existing tests take.

Security note (documented, not enforced): composite keying prevents accidental collision; it is not an
authentication/authorization boundary. Under (A) a hostile tenant can forge `X-Moralstack-Tenant-Id` unless
the fronting layer strips/overwrites client-supplied copies — that trust assumption must hold. Under (B)
forgery requires a valid bearer token; note that **token rotation mid-conversation changes the principal and
silently splits lineage into a new `conversation_id`** (expected behavior). Call this out in the trace doc.

### 5. `ConversationLockManager._locks` — DEFERRED (documented TODO)
`_locks` shares the OOM risk (`proxy.py:83`), but bounding it safely is non-trivial and is **deferred** to a
follow-up. The sketched idle-prune (acquire-nonblocking-then-delete under `_meta_lock`) has a concrete race:
`acquire` fetches the lock reference under `_meta_lock` and then blocks on `lock.acquire(timeout=...)`
*outside* it (`proxy.py:97-101`). In the window between releasing `_meta_lock` and calling `lock.acquire()`,
a pruner would see the lock as unlocked, delete it, and a subsequent request for the same `conversation_id`
would mint a *new* lock (`:98-99`) — two live locks for one conversation, breaking serialization. The canary
`test_concurrent_same_conversation_serialized` (`tests/test_server_proxy.py:602-637`) would not reliably
catch this (timing-dependent, only above the soft cap). Action in this cut: add a
`# TODO(P3-followup): bound _locks via refcounted-waiter design (increment waiters under _meta_lock BEFORE
the blocking acquire; prune only entries with waiters==0 AND unlocked)` at `proxy.py:83` and note the
follow-up in FACTS. Rationale for deferral: the correlation-store TTL bound already removes the dominant
growth source (~2 entries/turn vs 1 lock/conversation).

### 6. Telemetry (out of scope, follow-up)
No correlation-store telemetry exists today (only `session_store.get/put` and `proxy.request_finalized`).
Adding an eviction/size event is out of scope to keep the diff minimal; note as a follow-up. If added, it
MUST mirror the best-effort try/except pattern of `session_store.py:184-224`.

## Alternatives considered
- **Salt folded into the digest (was the original design; rejected in favor of composite key).** Would add a
  `salt` param to `canonical_history_hash`/`canonical_parent_history_hash` and a salted envelope. Rejected:
  introduces a byte-equality risk class (the digest must be provably unchanged for empty salt), a salted
  empty-history-root special case, and touches every caller of the hash (incl. `fingerprint.py`). Composite
  keying achieves identical isolation with zero hash changes. The only thing salting would add is protection
  if *future* code hashes histories outside the store — but the digests are process-local map inputs with no
  persistence or cross-component use (verified), so that benefit is hypothetical and does not justify the
  extra risk under PROJECT_SPEC §6.
- Add `cachetools.TTLCache`. Rejected: new runtime dependency (absent from pyproject.toml) for a pattern the
  team already maintains (`InMemorySessionStore`). Hand-rolled OrderedDict + inserted_at + `popitem(last=False)`
  is consistent, zero new deps, trivially auditable.
- Prefix the *conversation_id* with the principal (instead of keying the map). Rejected: the collision
  happens at the map-key level (two histories map to the same key); prefixing the produced id does not stop
  them sharing a map entry. Must scope the map key.
- Background eviction thread. Rejected: `InMemorySessionStore` deliberately uses lazy expiry with no thread
  (`session_store.py:69`); a thread diverges from the pattern and adds lifecycle/shutdown complexity.
- Use the Authorization header directly as the principal. Rejected: puts a raw secret into key input and risks
  leaking via any future debug path; HMAC digest is the safe equivalent.
- Global cross-process principal/tenant registry. Rejected: out of scope; the proxy is single-worker for
  multi-turn, process-local is correct.

## Files to modify
- `moralstack/server/conversation_correlation.py` — hash functions UNCHANGED. Add `principal` param to
  `resolve` (`:99`) and `observe_completed_turn` (`:116`); replace the unbounded `dict[str, str]` (`:97`) with
  a bounded `OrderedDict[tuple[str, str], _Entry]` + `_Entry` dataclass + `ttl_seconds`/`max_entries`/`time_fn`
  constructor params + `size()` + `_get_id`/`_put_id` helpers keyed by `(principal, hash)`; add module
  constants `DEFAULT_CORRELATION_TTL_SECONDS = 3600`, `DEFAULT_MAX_CORRELATION_ENTRIES = 20_000`.
- `moralstack/server/proxy.py` — add `_extract_principal(request)` helper (A→B→C, incl. `hmac`); add `principal`
  param to `_resolve_conversation_id_from_body_and_correlation` (`:120`) and forward to `resolve` (`:135`); add
  `principal` kwarg to `_handle_chat_completion_sync` (`:254`) and forward to the resolver (`:276`) and
  `observe_completed_turn` (`:419-423`); extract principal in `chat_completions` (`:551-588`) and pass through
  `run_in_threadpool` (`:590-604`); add optional `correlation_store: ConversationCorrelationStore | None = None`
  param to `create_app` (`:482-488`, mirror `session_store=`) and, when not injected, read env TTL/max_entries
  (best-effort parse **and range-validate** ttl>0/max_entries>=1, fallback to defaults, never raise) at `:507`;
  add the `# TODO(P3-followup)` deferral comment for `_locks` at `:83`; import `hmac`/`os` as needed for
  `_extract_principal`.
- `moralstack/server/fingerprint.py` — NO change; listed only as a verified-unaffected caller
  (`:52` calls the unchanged hash).
- `docs/MORALSTACK_CODEBASE_INDEX.md` — note bounded correlation store + composite `(principal, hash)` keying.
- `docs/CODEBASE_FACTS.md` — record: correlation store now TTL+max_entries bounded; lineage map keyed by
  `(principal, hash)`; hash digests unchanged; principal A→B→C derivation; `_locks` bounding deferred (TODO).
- `docs/TRACES/openai_compatible_multiturn.md` — update the conversation_id resolution section (`:68-108`):
  composite-key isolation, principal derivation A→B→C, the security-posture caveat, per-process/single-worker
  scope; **correct the stale Collision-risks sentence** (`:100-101`) that wrongly lists `ledger.py:254` as a
  collision-shared key (code wins: ledger key is `(contract_hash, posture, domain)`, `ledger.py:262`); and
  while rewriting `:68-108` **refresh all other drifted `proxy.py` citations** in that section (e.g. the
  resolver is `proxy.py:120-135`, lock manager `:71-117`, `store.get`/`put` `:318`/`:374-375`) rather than
  only the ledger sentence.
- Env docs — document `MORALSTACK_CORRELATION_TTL_SECONDS`, `MORALSTACK_CORRELATION_MAX_ENTRIES`,
  `MORALSTACK_PRINCIPAL_HMAC_SECRET` where MORALSTACK_ env vars are catalogued (note the TTL-alignment caveat).

## Tests to add / modify
Do NOT weaken or edit the existing behavior-locking tests below — only add new test functions/classes. If any
of them fails after the change, that is a real regression, not a test to "fix".

### Accepted design seams (needed for deterministic tests)
1. **Clock injection** — `time_fn: Callable[[], float] = time.time` on the `ConversationCorrelationStore`
   constructor, so TTL expiry is tested with a fake monotonic clock (no real sleeps). Do NOT retrofit
   `InMemorySessionStore` (it keeps its `sleep()`-based tests).
2. **`size()`/`__len__`** on the store (mirror `session_store.py:166-169`) for eviction assertions.
3. **`create_app(correlation_store=...)`** injection param (mirror `session_store=`) for tiny-bound
   integration tests.

### Backward-compat (run unmodified, must stay GREEN)
- `tests/test_conversation_correlation.py:12-42`: `test_deterministic`, `test_resolve_new_conversation_id_format`,
  `test_continuation_matches_parent_completed_hash`, `test_exact_request_replay_returns_same_id` — all use the
  empty-principal path; digests unchanged so they hold trivially.
- `tests/test_server_proxy.py`: `test_conversation_id_stable_across_turns` (`:779-830`, 3-turn COMPL-AI
  lineage, correlation-only path), `test_separate_conversations_independent` (`:832-853`),
  `test_concurrent_same_conversation_serialized` (`:602-637`).

### New unit tests — `tests/test_conversation_correlation.py`
1. `test_hash_functions_unchanged_golden_digest` (extend `TestCanonicalHistoryHash`) — pin **one hard-coded
   pre-change hex digest literal** for a fixed input, plus an inline reimplementation of the full algorithm
   **including normalization** (`_canonical_message_record`/content-normalization logic reproduced, not
   called) → assert `canonical_history_hash(msgs) == literal == reference`. Cover empty list, single user
   msg, multi-turn unicode, and the `_MAX_CONTENT_PER_MESSAGE` truncation boundary (`:22`). This is the
   cheapest lock proving the digest did not move (composite keying must not tempt an incidental hash change).
   Style mirrors `tests/test_system_prompt_byte_equality.py:36-40`.
2. `test_empty_list_equals_root_constant` — `canonical_history_hash([]) == _EMPTY_HISTORY_CANONICAL_HASH`
   (`:26`) — the parent-lookup root path depends on this identity (verified true: `json.dumps([]) == "[]"`).
3. `test_resolve_isolates_principals` (new `TestPrincipalIsolation`) — same messages: `principal="A"` vs
   `"B"` → different conversation_ids; `"A"` twice → same id. **Core bug fix.**
4. `test_resolve_default_principal_matches_pre_change_behavior` — `resolve(msgs)` == `resolve(msgs, principal="")`;
   the `("", hash)` keyspace matches the no-principal path. Keep `test_exact_request_replay_returns_same_id`
   green as the untouched-default proof.
5. `test_observe_completed_turn_lineage_within_principal` — resolve t0 with `principal="A"` → cid0;
   `observe_completed_turn(..., principal="A")`; follow-up resolve with `principal="A"` chains to cid0.
6. **`test_observe_completed_turn_does_not_leak_lineage_across_principal`** — same setup, follow-up resolved
   with `principal="B"` → **different** id (mints new; does not chain into A's lineage despite byte-identical
   content). **Primary P3/P0-3 regression lock — name and comment it as such.**
7. `test_ttl_expiry_mints_new_id` (new `TestBoundedStoreTTL`) — construct with an injected `time_fn`; resolve
   → cid0; advance the fake clock past TTL; resolve again → new id (stale entry evicted-on-read). Assert the
   boundary `age == ttl` is NOT expired (strict `>`), matching `session_store.py:176-178`. Assert the hit path
   does not refresh `inserted_at`.
8. `test_ttl_lineage_survives_when_only_inter_turn_delay_elapses` — 3-turn conversation where turn-1 entries
   have expired but chaining still works because each turn writes fresh request/completed entries → one stable
   conversation_id. Locks the "TTL only needs to exceed inter-turn delay" claim.
9. `test_maxsize_eviction_oldest_first` — `max_entries=2`, resolve 3 distinct histories; assert
   `store.size() <= 2`, oldest evicted (`popitem(last=False)`), re-resolving the evicted history mints a new
   id. Mirror `tests/test_session_store.py::TestCapacityCap.test_fifo_eviction_on_overflow` (`:132-141`).
10. `test_maxsize_one_observe_after_eviction_no_crash` — `max_entries=1`; every new history evicts the prior;
    `observe_completed_turn` must not crash when its target was already evicted (mints fresh, no `KeyError`).
11. `test_concurrent_resolve_thread_safety` — 20 threads × 50 distinct histories, `max_entries=200`; collect
    exceptions, assert none and `store.size() <= 200`. Mirror `tests/test_session_store.py::TestThreadSafety`
    (`:179-204`). Guards against `dictionary changed size during iteration` under concurrent eviction.
12. `test_concurrent_mixed_principals_identical_history` — threads racing with **different** principals on
    **identical** histories; assert no exceptions and that ids partition cleanly by principal (isolation holds
    under concurrency).

### New integration tests — `tests/test_server_proxy.py` (reuse `client_factory` `:75-94`)
13. `test_tenant_header_isolates_conversation_id` — identical body, no `X-Moralstack-Conversation-Id`; POST with
    `X-Moralstack-Tenant-Id: tenant-A` then `tenant-B`; assert the two `X-Moralstack-Conversation-Id` response
    headers differ.
14. `test_anonymous_when_bearer_present_but_no_tenant_and_no_secret` — strengthened from a bare re-run of
    `test_conversation_id_stable_across_turns` (which is already required green): send an `Authorization:
    Bearer key-X` with `MORALSTACK_PRINCIPAL_HMAC_SECRET` unset and no tenant header, across two turns → stable
    id via the anonymous `("", hash)` path (asserts B is skipped, not that the header is simply ignored).
15. `test_authorization_hmac_derives_distinct_principal` (path B) — identical history, `Authorization: Bearer
    key-A` vs `key-B`, no tenant header, `MORALSTACK_PRINCIPAL_HMAC_SECRET` set via `monkeypatch.setenv`
    (dummy) → different conversation_ids. Assert the raw Authorization value **and** the derived HMAC digest
    never appear in `_collect_safe_headers` output or captured log records (security regression guard).
16. `test_hmac_secret_unset_falls_back_to_anonymous` (path B/C) — no tenant header, bearer present, secret
    unset → behaves as anonymous (`("", hash)`); two different bearer tokens with identical history collide to
    the same id (B disabled). Locks the skip-B-no-hardcoded-fallback rule.
17. `test_tenant_header_precedence_over_authorization` — both `X-Moralstack-Tenant-Id` and `Authorization`
    present → same id as tenant-header-only (A wins over B).
18. `test_conversation_id_header_still_overrides_correlation` — explicit `X-Moralstack-Conversation-Id` +
    differing tenant headers across two calls → same id both times (explicit header wins; principal keying does
    not touch the header-precedence path `proxy.py:120-135`).
19. `test_correlation_store_bound_does_not_break_request_on_eviction` (invariant #6) — inject a tiny-bound store
    via `create_app(correlation_store=...)`, force eviction mid-run; assert every request still returns 200
    (eviction never raises into the handler).
20. `test_explicit_header_then_correlation_chains_under_principal` — turn 1 uses `X-Moralstack-Conversation-Id`
    + tenant A; turn 2 drops the explicit header (same tenant A, extended history) → chains to the
    header-supplied id (locks the cross-path lineage — `observe_completed_turn` records `(principal,
    completed_hash)→cid` even when the cid came from the explicit header, `proxy.py:275-276` then `:419-423` —
    for a non-empty principal, not just `principal=""`).

### New unit tests — `_extract_principal` and env wiring (`tests/test_server_proxy.py` or a small new module)
21. `test_extract_principal_layering` — direct unit test of the helper (build a minimal request-like object
    with headers): (a) `X-Moralstack-Tenant-Id: t` → `"t"`; (b) whitespace-only tenant header → treated as
    absent (define: strip → empty → not "present and non-empty"); (c) no tenant + `Authorization: Bearer k` +
    secret set → HMAC digest; (d) same but secret unset → `""`; (e) non-Bearer `Authorization: Basic ...` →
    `""`; (f) both absent → `""`. Set/unset the secret via `monkeypatch.setenv`/`delenv`.
22. `test_env_wiring_fallback` — `create_app` with `MORALSTACK_CORRELATION_TTL_SECONDS`/`MAX_ENTRIES`: valid
    values reach the store (assert via the injected/created store's config); garbage (`"abc"`) and
    out-of-range (`"-5"`, `"0"`) fall back to defaults **without raising** at `create_app`.

### Edge cases to cover
- Explicit `principal=""` == omitted kwarg (backward-compat).
- Principal containing odd characters (e.g. `"}"`, `":"`) — with tuple keying there is no serialization to
  spoof, but assert isolation/stability still hold (guards against any accidental string-concat keying).
- TTL boundary `age == ttl_seconds` → not expired (strict `>`).
- Concurrent `resolve()` + `observe_completed_turn()` racing eviction of the same entry → at worst a fresh id,
  never an exception.

### Commands
- Scoped (while iterating): `python -m pytest tests/test_conversation_correlation.py tests/test_server_proxy.py -q`
- Full suite before done: `python -m pytest`
- Quality gate: `python -m pre_commit run -a`

### Fixtures / mocks
- Clock: injected `time_fn` (seam #1) — a mutable counter fake clock; no real sleeps.
- `create_app(correlation_store=...)` injection (seam #3) for tiny-bound integration tests.
- `client_factory` (`tests/test_server_proxy.py:75-94`) reused as-is unless it needs a `correlation_store=`
  override.
- HMAC secret via `monkeypatch.setenv` (never a real secret).
- All tests offline/mocked per `.claude/rules/testing.md`; no DB/network beyond `client_factory`.

## Risks
- TTL too short breaks real multi-turn (lineage evicted between slow turns → new conversation_id
  mid-conversation). Mitigation: 3600s default matched to session store; document that TTL must exceed max
  inter-turn delay; positive lineage-survival test (test 8).
- Principal header forgery (a tenant reads another tenant's history). Mitigation/limitation: documented as
  trust-boundary-dependent (A requires the fronting layer to strip client-supplied `X-Moralstack-Tenant-Id`);
  composite keying is anti-collision, not authz. Surfaced explicitly, not silently fixed.
- Cross-tenant availability via shared eviction budget (noisy tenant evicts another's lineage). Mitigation:
  document; per-principal cap is a follow-up.
- Secret/log leakage on path B. Mitigation: `hmac.new`, secret from env only, skip-B when unset, raw token and
  derived digest never logged; test 15 asserts no-leak.
- Env parsing errors (bad `MORALSTACK_CORRELATION_*` value). Mitigation: best-effort parse with fallback to
  defaults; never raise at create_app.
- `_locks` growth remains unbounded (deferred). Mitigation: correlation-store bound removes the dominant
  growth; documented TODO with the refcount design for the follow-up.
- Multi-worker deployment silently changes semantics (per-process store). Mitigation: documented in the trace;
  out of scope here.

## Acceptance criteria
- [ ] Hash digests are byte-identical to pre-change (golden-digest + inline-reference test), and the hash
      function signatures gained no required parameter.
- [ ] Two identical histories with different principals resolve to different conversation_ids; same principal
      → same id.
- [ ] Lineage chaining across 3 turns still yields one stable conversation_id for a single principal and for
      the no-principal path, including when only inter-turn delay (< TTL) has elapsed.
- [ ] Correlation store never exceeds max_entries; entries older than ttl_seconds are not returned; hit path
      does not refresh TTL.
- [ ] All listed behavior-locking tests pass unmodified.
- [ ] Header > extra_body > correlation precedence unchanged, verified with a principal set.
- [ ] Principal derivation A→B→C works; secret read per-request and unset disables B (anonymous); non-Bearer
      auth skips to C; raw token and HMAC digest never logged.
- [ ] Env wiring range-validates (ttl>0, max_entries>=1) and falls back to defaults; `create_app` never raises
      on garbage or out-of-range `MORALSTACK_CORRELATION_*` values.
- [ ] No new runtime dependency added.
- [ ] All TTL/eviction paths swallow exceptions and never raise into `_handle_chat_completion_sync`; a
      swallowed eviction in `resolve()` still returns a valid minted id (invariant #6).
- [ ] `_locks` bounding deferred with a `TODO(P3-followup)` comment + FACTS note.
- [ ] Docs updated: INDEX, FACTS, TRACES (incl. the corrected stale ledger sentence), env catalogue.
- [ ] Full `python -m pytest` green; `pre-commit run -a` green.

## Implementation checklist
1. Add `principal` param to `resolve`/`observe_completed_turn`; replace the unbounded dict with the bounded
   `OrderedDict[tuple[str,str], _Entry]` + `_Entry` + `ttl_seconds`/`max_entries`/`time_fn`/`size()` +
   `_get_id`/`_put_id` keyed by `(principal, hash)`. Hash functions untouched. Add the golden-digest test;
   run `pytest tests/test_conversation_correlation.py`.
2. Add `_extract_principal(request)` (A→B→C, `hmac`) to proxy.py; thread principal through the resolver,
   `_handle_chat_completion_sync`, and `observe_completed_turn`; add `create_app(correlation_store=)` param and
   env TTL/max_entries wiring; add the `_locks` deferral TODO.
3. Add new unit + integration tests (principal isolation, TTL/max_entries eviction, proxy tenant/HMAC wiring,
   precedence, no-leak, bound-does-not-break-request).
4. Update docs/MORALSTACK_CODEBASE_INDEX.md, docs/CODEBASE_FACTS.md, docs/TRACES/openai_compatible_multiturn.md
   (incl. correcting the stale ledger sentence), and the env catalogue.
5. Run full `python -m pytest` and `pre-commit run -a`; confirm behavior-locking set green.

## Rollback plan
- All new params default to backward-compatible values (principal empty ⇒ `("", hash)` keyspace = today).
  To disable principal isolation operationally without a revert: stop sending `X-Moralstack-Tenant-Id` and
  leave `MORALSTACK_PRINCIPAL_HMAC_SECRET` unset so the principal is empty and behavior is exactly as before.
- To disable the bound operationally: set `MORALSTACK_CORRELATION_MAX_ENTRIES` and TTL very high (approximates
  the old unbounded dict).
- Full revert: `git revert` the single feature commit; the correlation store is process-local in-memory, no
  persisted schema/format changed, so no data migration is required.
