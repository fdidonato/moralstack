# Implementation handoff — Bounded, tenant/principal-aware conversation correlation store (P3 / P0-3 / A3)

You are the **MoralStack Implementer** (`claude-implementer`, Claude Sonnet, isolated sub-agent context).
Implement ONLY what this handoff allows. Read the approved plan in full before touching code.

## Source of truth
- **Approved plan:** `ai/plans/bounded-tenant-aware-conversation-correlation.md` — READ IT IN FULL. It carries
  the design, per-`path:line` evidence, the full test list, risks, and acceptance criteria. This handoff is a
  scoping wrapper, not a replacement; where this handoff and the plan agree, the plan's detail governs.
- **Plan reviews (context, no action needed):**
  `ai/reviews/codex-plan-review-bounded-tenant-aware-conversation-correlation-20260709-100530.md` (round 1,
  APPROVE_WITH_CHANGES) and `...-20260709-103140.md` (round 2, **APPROVE**, no blockers). All review findings
  are already folded into the plan (see its "Review resolution" header). Do not re-litigate them.

## Objective
Make the proxy conversation correlation store (`ConversationCorrelationStore`) **bounded** (TTL + maxsize
eviction) and **tenant/principal-aware** by keying its internal lineage map on `(principal, history_hash)`.
Two byte-identical conversation histories from different principals must resolve to **different**
`conversation_id`s; with no principal (empty-string sentinel) behavior is identical to today.

**Critical design point (do not deviate):** the isolation lives ENTIRELY in the store's internal map key.
The hash functions `canonical_history_hash` and `canonical_parent_history_hash` are **UNCHANGED** — no `salt`
parameter, no envelope, no signature change. If you find yourself editing those two functions' bodies or
signatures, STOP — you are off-plan.

## Files ALLOWED to modify
- `moralstack/server/conversation_correlation.py` — add `principal` param to `resolve` and
  `observe_completed_turn`; replace the unbounded `dict[str, str]` with a bounded
  `OrderedDict[tuple[str, str], _Entry]` keyed by `(principal, hash)`; add `_Entry` dataclass, constructor
  params `ttl_seconds` / `max_entries` / `time_fn`, `size()`, `_get_id`/`_put_id` helpers, and module constants
  `DEFAULT_CORRELATION_TTL_SECONDS = 3600`, `DEFAULT_MAX_CORRELATION_ENTRIES = 20_000`.
  **Do NOT change the two hash functions.**
- `moralstack/server/proxy.py` — add `_extract_principal(request)` (A→B→C, per plan §4, uses `hmac`/`os`);
  thread `principal` through `_resolve_conversation_id_from_body_and_correlation`, `_handle_chat_completion_sync`,
  the `run_in_threadpool` call, and `observe_completed_turn`; extract principal in the `chat_completions` route;
  add optional keyword-only `correlation_store: ConversationCorrelationStore | None = None` param to `create_app`
  (mirror the existing `session_store=`); when not injected, read env `MORALSTACK_CORRELATION_TTL_SECONDS` /
  `MORALSTACK_CORRELATION_MAX_ENTRIES` (best-effort parse **and range-validate** ttl>0 / max_entries>=1, fall
  back to defaults, **never raise at create_app**); add a `# TODO(P3-followup): bound _locks via
  refcounted-waiter design ...` comment near `_locks` (`:83`) — DO NOT implement `_locks` bounding.
- `tests/test_conversation_correlation.py` — add the new unit tests (plan §Tests, items 1-12). Do not edit
  existing tests.
- `tests/test_server_proxy.py` — add the new integration + unit tests (plan §Tests, items 13-22). Do not edit
  existing tests. (Item 21/22 may live here or in a small new `tests/test_extract_principal.py` — your call,
  but prefer extending the existing file to minimize new surface.)
- `docs/MORALSTACK_CODEBASE_INDEX.md`, `docs/CODEBASE_FACTS.md`,
  `docs/TRACES/openai_compatible_multiturn.md` — per plan §Files (behavior docs MUST be updated in the same
  change; a Stop hook enforces this). In the trace, correct the stale ledger sentence (`:100-101`,
  `ledger.py:254`→ real key `(contract_hash,posture,domain)` at `ledger.py:262`) and refresh other drifted
  `proxy.py` citations in `:68-108`.
- The env-var catalogue doc (wherever `MORALSTACK_` env vars are documented — grep for an existing one like
  `MORALSTACK_SESSION_` to locate it) — add the three new vars: `MORALSTACK_CORRELATION_TTL_SECONDS`,
  `MORALSTACK_CORRELATION_MAX_ENTRIES`, `MORALSTACK_PRINCIPAL_HMAC_SECRET` (note the TTL-alignment caveat).

## Files NOT to modify (do-not-touch)
- `moralstack/sdk/session_store.py` — REFERENCE ONLY (mirror its pattern). Do NOT retrofit a `time_fn` seam or
  change it in any way.
- `moralstack/server/fingerprint.py` — verified-unaffected caller; leave it alone (it calls the unchanged hash).
- `moralstack/orchestration/ledger.py` and anything under `moralstack/orchestration/` — out of scope.
- `pyproject.toml` — no new dependency (do NOT add `cachetools`; use stdlib `OrderedDict`, `hmac`, `os`, `time`).
- The bodies/signatures of `canonical_history_hash` / `canonical_parent_history_hash`.
- Any existing test assertions.

## Invariants in play (PROJECT_SPEC §5) and how to keep them
- **#6 Observability never breaks the request (LOAD-BEARING).** All TTL/eviction paths best-effort in
  swallowing try/except (mirror `session_store.py:184-224`). CRUCIAL nuance: `resolve()` is on the **request
  path**, not telemetry — a swallowed eviction failure must still fall through and **return a valid freshly
  minted id**, never `None`/raise. `observe_completed_turn` is already wrapped at its call site
  (`proxy.py:418-425`); keep it best-effort.
- **No P0 decision invariant is touched** (decision/generation separation, hard-signal supremacy, prompt
  transparency, governed delivery). This change only affects `conversation_id` derivation and the correlation
  map lifetime. If your change starts affecting `final_action`, prompt bytes, or delivery, STOP — you are
  off-plan. (Prompt transparency here concerns system-prompt bytes, unrelated to these map-key hashes.)

## Security constraints (hard)
- HMAC (plan §4 path B): `hmac.new(secret.encode(), token.encode(), sha256).hexdigest()`. Read the secret
  **per-request** via `os.environ.get("MORALSTACK_PRINCIPAL_HMAC_SECRET")` inside `_extract_principal` (NOT
  captured once at create_app). If unset, skip B silently (no hardcoded fallback, no per-request log spam) and
  fall to C.
- **Never log or store the raw Authorization token, and never log the derived HMAC digest.** The token stays
  behind `_SENSITIVE_HEADER_MARKERS` (`proxy.py:138-144`); read it directly from `request.headers`, return only
  a principal string, and do not add either value to any log line.
- Only `Authorization: Bearer <token>` triggers B; non-Bearer (`Basic ...`) or malformed/empty → skip to C.
- Empty-string sentinel `""` for "no principal" (NOT `"anonymous"` or any literal).

## Implementation checklist (from plan)
1. `conversation_correlation.py`: add `principal` to `resolve`/`observe_completed_turn`; bounded
   `OrderedDict[tuple[str,str], _Entry]` + `_Entry` (pass `inserted_at=self._time_fn()` **explicitly** at every
   insert — a dataclass default cannot see the instance clock) + `ttl_seconds`/`max_entries`/`time_fn`/`size()` +
   `_get_id`/`_put_id`; strict `>` expiry, hit path does NOT refresh `inserted_at`; `popitem(last=False)` on
   overflow. Hash functions untouched.
2. `proxy.py`: `_extract_principal` (A→B→C); thread `principal` through resolver →
   `_handle_chat_completion_sync` → `observe_completed_turn`; `create_app(correlation_store=)` + env wiring
   (range-validated, never raise); `_locks` TODO comment only.
3. Add all new tests (plan items 1-22).
4. Update docs (INDEX, FACTS, TRACES incl. ledger-sentence correction + drifted citations, env catalogue).
5. Run verification; confirm behavior-locking set green.

## Required tests (must be added — see plan §Tests for exact arrange/act/assert)
Unit (`tests/test_conversation_correlation.py`): golden-digest unchanged (1), `[]`==root constant (2),
principal isolation (3), default==empty principal (4), lineage within principal (5), **no cross-principal
lineage leak (6 — primary P3 regression)**, TTL expiry via injected clock (7), TTL lineage survives inter-turn
delay (8), maxsize FIFO eviction (9), maxsize=1 observe-after-evict no crash (10), concurrent thread-safety
(11), concurrent mixed-principals (12).
Integration/unit (`tests/test_server_proxy.py`): tenant-header isolation (13), anonymous-when-bearer-no-secret
(14), HMAC distinct principal + no-leak (15), secret-unset-fallback (16), tenant precedence over auth (17),
explicit conv-id header still overrides (18), bound-does-not-break-request (19), explicit-header→correlation
chains under principal (20), `_extract_principal` layering unit (21), env-wiring fallback (22).

Behavior-locking tests that MUST stay green **unmodified** (do not edit):
`tests/test_conversation_correlation.py::{test_deterministic, test_resolve_new_conversation_id_format,
test_continuation_matches_parent_completed_hash, test_exact_request_replay_returns_same_id}`;
`tests/test_server_proxy.py::{test_conversation_id_stable_across_turns, test_separate_conversations_independent,
test_concurrent_same_conversation_serialized}`.

## Verification commands (run and report REAL output)
```
python -m pytest tests/test_conversation_correlation.py tests/test_server_proxy.py -q
python -m pytest
python -m pre_commit run -a
```
The full suite and pre-commit (ruff, black, trailing-whitespace, end-of-file, mypy strict on
`moralstack.orchestration.*` — note: the server module is not under orchestration strict, but keep types clean)
must be green before you declare done.

## Acceptance criteria (from plan — all must hold)
- Hash digests byte-identical to pre-change (golden-digest + inline-reference test); hash signatures gained no
  parameter.
- Different principals → different conversation_ids; same principal → same id.
- 3-turn lineage stable for a principal and for no-principal, including when only inter-turn delay (< TTL)
  elapsed.
- Store never exceeds max_entries; entries older than ttl_seconds not returned; hit path does not refresh TTL.
- All listed behavior-locking tests pass unmodified.
- Header > extra_body > correlation precedence unchanged with a principal set.
- A→B→C works; secret read per-request, unset disables B; non-Bearer skips to C; raw token + HMAC digest never
  logged.
- Env wiring range-validates and never raises at create_app on garbage/out-of-range values.
- No new runtime dependency.
- All eviction paths swallow exceptions and never raise into `_handle_chat_completion_sync`; swallowed eviction
  in `resolve()` still returns a valid minted id.
- `_locks` bounding deferred with a `TODO(P3-followup)` comment + FACTS note.
- Docs updated (INDEX, FACTS, TRACES incl. corrected ledger sentence, env catalogue).
- Full `pytest` green; `pre-commit run -a` green.

## Risks to watch (from plan)
- Accidentally changing a hash digest (breaks all existing `msconv-` lineage + tests) — the golden-digest test
  is your guard; run the two behavior-locking test files first.
- Fake-clock tests broken by a dataclass default that ignores `time_fn` — pass `inserted_at` explicitly.
- Env `-5`/`0` reaching the constructor and raising — range-validate in the wiring.
- Leaking the bearer token or HMAC digest into logs — assert against captured logs in test 15.

## Constraints on your run
- Do NOT commit, push, `git add`, amend, or tag. Do NOT use `--no-verify`. Do NOT delete/weaken tests. Do NOT
  touch do-not-modify files. If the plan is ambiguous or you hit a blocking architectural problem, STOP and
  report it rather than guessing.

## Required output at end of run
- files modified; tests added; commands run; results (REAL output, including any failures); deviations from the
  plan; residual problems / blockers.
