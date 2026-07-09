<!--
SECOND review (revised plan) of ai/plans/bounded-tenant-aware-conversation-correlation.md.
Reviewer: independent plan review via the fable model (claude-fable-5), read-only.
Context: re-review requested after the plan was revised to the composite-key design and
the prior blocker (lock idle-prune race) was deferred. The Codex CLI runtime had stalled
in the first round and was cleaned; per the user's instruction both reviews were run with
fable using the same rubric that would have gone to Codex. The heading is kept as
"Codex Plan Review" because the review template mandates that exact structure.
Prior review: codex-plan-review-bounded-tenant-aware-conversation-correlation-20260709-100530.md
-->

# Codex Plan Review

## Verdict
`APPROVE`

## Blocking issues

None. The prior review's single blocking issue is genuinely resolved:

- **Prior blocker (`_locks` idle-prune race) — resolved by deferral.** The revised §5 ships NO prune code, only a `# TODO(P3-followup)` comment at `proxy.py:83` plus a FACTS note. The race the prior review proved is real and still present in the code (lock reference fetched under `_meta_lock` at `moralstack/server/proxy.py:97-100`, blocking `lock.acquire(timeout=...)` at `:101` *outside* it), so deferring is the correct call: no pruner ships, hence no window in which a waiter's about-to-acquire reference can be orphaned. The plan's §5 restates the race accurately and embeds the refcounted-waiter design in the TODO for the follow-up. The remaining unbounded growth is honestly characterized: one `threading.Lock` per distinct `conversation_id` vs ~2 map entries per *turn* in the correlation store (`conversation_correlation.py:109,113,129`), so the correlation-store bound does remove the dominant growth source. Acceptable.

## Non-blocking issues

- **HMAC-secret read timing is unspecified, and it affects tests 15/16.** §4(B) says the secret comes from `MORALSTACK_PRINCIPAL_HMAC_SECRET` and "log once at INFO that B is disabled", but not *when* the env is read: per-request in `_extract_principal`, or once at `create_app` (`proxy.py:482-507`). The `client_factory` fixture (`tests/test_server_proxy.py:75-94`) builds the app inside `_build()`; if the secret is captured at `create_app` time, `monkeypatch.setenv` must run before `client_factory(...)` is called, otherwise tests 15/16 silently test path C. Also "log once" implies module/app-level state — per-request INFO would be log spam. One sentence pinning "read per-request via `os.environ.get` (rotation-friendly)" or "captured at create_app" removes the ambiguity.
- **"Never raise at create_app" can be violated by the store's own constructor validation.** §3 mirrors `session_store.py:89-92`, which `raise ValueError` on `ttl_seconds <= 0` / `max_entries < 1`. The env-wiring risk item says "best-effort parse with fallback to defaults; never raise" — but `MORALSTACK_CORRELATION_TTL_SECONDS=-5` parses as a valid int and then the constructor raises. The plan should state that the env wiring range-validates (or catches `ValueError`) and falls back to defaults, not just catches parse errors.
- **`_Entry` "default via injected clock" is not implementable as a dataclass field default.** A `field(default_factory=...)` cannot see `self._time_fn` (contrast `_SessionEntry`, `session_store.py:57-62`, which hardcodes `time.time`). The implementer must pass `inserted_at=self._time_fn()` explicitly at every insert site. The plan's intent is clear but the wording invites copying the `_SessionEntry` pattern verbatim, which would break the fake-clock tests.
- **Path B only defines `Authorization: Bearer <token>`.** Behavior for a non-Bearer scheme (`Basic ...`) or a malformed value is unstated — presumably skip to C. One line avoids implementer divergence and makes a cheap unit-test case.
- **Trace doc has more stale line refs than the one sentence the plan corrects.** `docs/TRACES/openai_compatible_multiturn.md:68` cites `proxy.py:218-219, 121-136` and `:98-100` cites `proxy.py:87-110`/`:256,303-304` — several are drifted (resolver is `proxy.py:120-135`, lock manager `:71-117`, `store.get`/`put` `:318`/`:374-375`). Since the plan rewrites `:68-108` anyway, note that the pass should refresh all citations in that section, not only the ledger sentence.
- Test 14 ("no tenant header matches current behavior") as described is a re-run of the existing `test_conversation_id_stable_across_turns` (`tests/test_server_proxy.py:779-830`), which is already required green under "Backward-compat". Harmless, but it adds no coverage; either drop it or make it assert something the existing test doesn't (e.g. presence of an `Authorization` header with secret unset).

Verified resolutions of the prior review's material findings (all confirmed against code this session):
- Composite key backward compat: **verified.** `resolve()` keys purely on the bare digest today (`conversation_correlation.py:100-113`), with no caller identity; `("", hash)` is a bijective relabeling of today's keyspace, so the four unit tests (`tests/test_conversation_correlation.py:12-42`, exactly four, no auth/tenant headers) and the three proxy tests (`tests/test_server_proxy.py:602-637` explicit-header lock path, `:779-830` correlation-only, `:832-853` explicit headers) stay green unmodified.
- Hash functions genuinely need no change: `canonical_history_hash` (`conversation_correlation.py:61-69`) and `canonical_parent_history_hash` (`:72-85`) take only `messages`; the composite key lives entirely in the store's map, so no salt param, no envelope, no signature change. `fingerprint.py:52` calls `canonical_history_hash(stem)` with no extra args — verified unaffected as the plan claims.
- Stale ledger sentence: **verified stale.** `docs/TRACES/openai_compatible_multiturn.md:100-101` lists "the ledger key (`ledger.py:254`)"; `ledger.py:254` is a `LedgerResult` return, the real key is `LedgerKey(contract_hash, posture, domain)` at `moralstack/orchestration/ledger.py:262` (contract documented `:50-51`), and `ledger.py` contains zero occurrences of `conversation_id` (grepped). Plan's correction step (§Files) is right.
- `canonical_history_hash([])`: normalized `[]` → blob `"[]"` → `sha256(b"[]")` = `_EMPTY_HISTORY_CANONICAL_HASH` (`:26`) — identity holds; test 2 locks it.
- Hit-path no-TTL-refresh, strict-`>` expiry, `size()`, delete-then-reinsert, `popitem(last=False)`: all correctly mirror `session_store.py:99-110,131-140,166-169,176-178`.
- `observe_completed_turn` call site already wrapped in swallowing try/except (`proxy.py:418-425`); invariant #6 request-path clause ("swallowed eviction in `resolve()` still returns a minted id") is explicit in Target behavior #3 and acceptance criteria.

## Missing tests

- **Env-wiring tests.** No test covers `MORALSTACK_CORRELATION_TTL_SECONDS`/`MAX_ENTRIES`: (a) valid values reach the store; (b) garbage or out-of-range values fall back to defaults without raising at `create_app` (this is the plan's own stated risk, and interacts with the constructor-ValueError gap above).
- **Unit tests for `_extract_principal` itself.** Tests 13-17 are integration-level; a direct unit test of the helper is cheap and should cover: empty-string tenant header value (whitespace-only → does it count as "present and non-empty"?), non-Bearer `Authorization`, tenant header + no secret, both absent.
- **Explicit-header turn followed by correlation turn under a principal.** `observe_completed_turn` records `(principal, completed_hash) → cid` even when the cid came from the explicit header (`proxy.py:275-276` then `:419-423`). A test that turn 1 uses `X-Moralstack-Conversation-Id` + tenant A and turn 2 drops the explicit header (same tenant) should chain to the header-supplied id — this cross-path lineage exists today for `principal=""` and should be locked for non-empty principals.

## Risky assumptions

- **Single-worker scope** (trace `:113-116`) — correct today and honestly documented; multi-worker deployment silently changes both bounding and isolation semantics. Kept in the trace update; fine.
- **Trusted fronting layer for path A** — the plan is explicit that composite keying is anti-collision, not authz, and that the fronting layer must strip client-supplied `X-Moralstack-Tenant-Id`. Correct posture, correctly not "fixed" here.
- **"TTL only needs to exceed inter-turn delay"** — true given each turn writes fresh request/completed entries (`conversation_correlation.py:109,113,129`), and test 8 locks it. Sound.
- All spot-checked citations in the revised plan are accurate against the current tree, including `proxy.py:83,97-101,120-135,138-144,147-158,254-268,275-276,318,374-375,419-423,482-488,507,551-588,590-604`, `session_store.py` lines listed above, and `cachetools` absent from `pyproject.toml`.

## Architecture concerns

- **The composite-key pivot is sound and strictly smaller than the salted design.** Nothing outside `conversation_correlation.py` reads or iterates `_history_to_conversation` (grepped: only the module itself plus docs/plans), nothing serializes the map, and tuple keys are plain hashable Python — no persistence format or cross-component contract is touched. The prior review's recommended alternative was adopted correctly.
- **`create_app(correlation_store=)` is safe additive surface.** The signature is keyword-only (`proxy.py:482-488`) and every caller in the repo uses keywords (grepped: `examples/server_quickstart.py:63`, ~20 test sites; `moralstack/ui/app.py:2479` is an unrelated same-named function). No collision with the env-wiring path: the singleton at `proxy.py:507` is only built when the param is None, mirroring `session_store` at `:505`. API sign-off is recorded as an owner decision in the Review-resolution header.
- **Invariants: none touched.** The change is confined to `conversation_id` derivation and map lifetime. Decision/generation separation, hard-signal supremacy, prompt transparency (map keys, not prompt composition — the byte-equality suite concerns system-prompt bytes), and governed delivery (`proxy.py:381-414` untouched) are unaffected. Invariant #6 is correctly split: `observe_completed_turn` is already best-effort (`:418-425`); `resolve()` is request-path, and the plan explicitly requires fall-through to a minted id.
- The `time_fn` seam divergence from `InMemorySessionStore` is acknowledged and scoped (no retrofit) — acceptable.

## Security/performance concerns

- **Tenant header in debug logs:** confirmed — `X-Moralstack-Tenant-Id` passes `_collect_safe_headers` (starts with `x-`, `proxy.py:154`) and lands in the correlation debug line (`:580-588`). The plan documents it and warns operators not to derive the id from a secret. Adequate. Optional hardening: cap/validate the tenant header length before using it verbatim as a map-key component (server header limits bound it anyway, so low priority).
- **HMAC handling:** `hmac.new` mandated, secret env-only, skip-B when unset (no hardcoded fallback), raw token stays behind `_SENSITIVE_HEADER_MARKERS` (`proxy.py:138-144`), and the derived digest is also barred from logs with test 15 asserting both. Sound. Implementation nit: env secret is `str`, `hmac.new` needs `bytes` — `.encode()` required.
- **Performance:** unchanged digest count, O(1) OrderedDict ops, one HMAC per request — negligible. The 20k `max_entries` rationale (2 hashes/turn vs 1 session/conversation) is coherent; shared eviction budget across principals is a documented availability (not confidentiality) effect with a per-principal-cap follow-up noted.

## Suggested plan changes

1. §4(B): specify the HMAC-secret read point (recommend per-request `os.environ.get`) and the mechanism for "log once"; state that tests 15/16 must set the env before app construction if the read is create_app-time.
2. §3/§Files: state that env wiring range-validates (ttl > 0, max_entries >= 1) or catches `ValueError` from the constructor, so "never raise at create_app" actually holds for out-of-range values, not just unparseable ones.
3. §3 `_Entry`: replace "default via injected clock" with "insert sites pass `inserted_at=self._time_fn()` explicitly (a dataclass default cannot reference the instance clock)".
4. §4(B): define behavior for non-Bearer/malformed `Authorization` (skip to C) and add it to the unit-test list.
5. Docs step: extend the trace correction to refresh all drifted `proxy.py` line citations in `docs/TRACES/openai_compatible_multiturn.md:68-108`, not only the ledger sentence.
6. Tests: add the env-wiring fallback test and the explicit-header→correlation cross-path lineage test under a non-empty principal; drop or strengthen test 14.

## Questions for Claude/User

1. Secret read timing: per-request env read (rotation-friendly, trivially testable with monkeypatch) or captured at `create_app` (fewer env reads, needs test ordering care)? Recommend per-request.
2. Should path A verbatim tenant ids be length-capped/sanitized before use as a key component, or is the server's header-size limit considered sufficient?
3. For the `_locks` follow-up, do you want the refcounted-waiter design tracked as its own `ai/plans/` entry now, or is the TODO + FACTS note enough until it's scheduled?
