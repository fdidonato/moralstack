# Plan — optimize-domain-prefilter-caching-and-parallelism

> **Status: APPROVED_WITH_CHANGES** (Codex review 2026-07-08, verdict APPROVE_WITH_CHANGES, no
> blockers; review at `ai/reviews/codex-plan-review-optimize-domain-prefilter-caching-and-parallelism-20260708-132049.md`).
> All non-blocking findings folded in: 6th default source (shell.py:1123 help), cache-eligible
> wording, wider concurrency blast radius (no-prefilter/legacy paths, provider-throttling can
> change retrieved-principle set), class-scoped builder, A6 UI resolved (cosmetic), and the extra
> tests (CLI help/default, signals-mini rerun, batch-count edge cases, corrected cache-clear assert).

## Goal
Two surgical retrieval optimizations in moralstack/constitution/retriever.py: (1) split
DomainPrefilter into a byte-stable SYSTEM prompt + query-only USER message so OpenAI prompt
caching engages; (2) raise the effective max_parallel_agents default from 2 to 4 so all
prefilter agents (core + up to 3 domains) run in a single parallel batch.

## Current behavior
- DomainPrefilter.filter_domains builds ONE user prompt f-string (retriever.py:494-555)
  that contains BOTH the dynamic "USER QUERY: {query}" (:495-496) AND all static text:
  "AVAILABLE DOMAINS:" + rendered domain_list (:498-499), classifier instructions,
  wrapper/encoding rules, classification procedure, falsification checks, confidence scale,
  and the JSON schema (:501-554). domain_list is rendered from _domain_keywords /
  _domain_descriptions via the local _domain_line closure (:485-492).
- _call_openai (retriever.py:573-674) hardcodes an 11-word system message
  sys_msg = "You are a strict domain classifier. Always respond with valid JSON only."
  (:592) and sends messages=[{system: sys_msg}, {user: prompt}] (:597-600) with
  temperature=0.1, response_format={"type":"json_object"} (:601-602),
  max_output_tokens=200 (:603). It persists system_prompt=sys_msg, prompt=prompt via
  _persist_constitution_llm_call(...) (:656-669). Net effect: the ~21-domain static block
  sits in the USER message and is re-sent uncached on every call; the cached prefix
  (system) is only 11 words.
- Local _cache key is md5(f"{query}_{','.join(sorted(available_domains))}") (retriever.py:446)
  keyed on query + domain NAMES, independent of message layout and of keyword/description
  CONTENT. set_domain_keywords/set_domain_descriptions mutate the fingerprint and
  self._cache.clear() when the effective map changes (retriever.py:319-391; clears at :341
  and :379).
- max_parallel_agents default is 2 in FOUR places and is consumed as batch_size in both
  parallel runners:
  - ConstitutionRetrieverConfig.max_parallel_agents = 2 (retriever.py:1088) — consumed at
    _run_enhanced_agents_parallel (retriever.py:1457) and _run_agents_parallel
    (retriever.py:1495) as batch_size.
  - ConstitutionStoreConfig.max_parallel_agents = 2 (store.py:462) and the
    ConstitutionStore.__init__ kwarg default = 2 (store.py:498). The store passes
    cfg.max_parallel_agents straight into the ConstitutionRetrieverConfig it builds
    (store.py:578), so the store default OVERRIDES retriever.py:1088 on every store-mediated
    path.
  - resolve_constitution_max_parallel_agents(...) env fallback default = 2
    (deliberation_stack.py:64; env var MORALSTACK_CONSTITUTION_MAX_PARALLEL_AGENTS, :32).
    This is what SDK/CLI runtime wiring uses: build_deliberation_modules
    (deliberation_stack.py:95) and parse_args (shell.py:1158; argparse default None at
    shell.py:1122) both resolve through it, then feed the store (shell.py:120).
  - CLIConfig.max_parallel_agents = 2 (cli/models.py:492) — overridden by the resolver in
    parse_args (shell.py:1158-1167).
- With batch=2 and 4 agents, range(0, 4, 2) runs TWO sequential ThreadPoolExecutor batches
  (retriever.py:1459-1461, 1497-1499).

## Target behavior
- Prefilter SYSTEM message carries the full static classifier block + AVAILABLE DOMAINS list
  (rendered from the current domain config); USER message carries only "USER QUERY:\n{query}".
  In the common case (domain config and available_domains unchanged) the SYSTEM message is
  byte-identical across requests -> the large prefix becomes CACHE-ELIGIBLE for OpenAI
  automatic prompt caching. NOTE (Codex review): byte-stability is necessary but not
  sufficient — OpenAI caches only exact static prefixes and only when the prompt is >=1024
  tokens; below that `usage.prompt_tokens_details.cached_tokens` stays 0. The split makes the
  call cache-eligible; an actual hit depends on prompt length/provider conditions. response_format,
  temperature, max_output_tokens=200, and the parser/retry/fallback
  in _call_openai are unchanged. Persisted system_prompt becomes the large static block,
  persisted prompt becomes the query-only message (expected/desired shape change).
- The effective max_parallel_agents default becomes 4 on all runtime paths, so the up-to-4
  prefilter agents run in ONE ThreadPoolExecutor batch. Env override
  MORALSTACK_CONSTITUTION_MAX_PARALLEL_AGENTS still wins when set. No token, semantics, or
  contract change.

## Assumptions
- A1 (verified): the static block text (retriever.py:501-554) contains no per-request data
  other than {query} (moved out), {domain_list} (per-config), and {self.max_domains}
  (instance-fixed). Verified by reading :494-555.
- A2 (verified): _domain_keywords/_domain_descriptions only change via
  set_domain_keywords/set_domain_descriptions/clear_cache, each of which clears _cache when
  the effective map changes (retriever.py:341,379,402). So a changed system prompt always
  coincides with a cleared local cache (no stale-cache/hot-prompt mismatch). Verified by
  reading :319-415.
- A3 (verified): _cache key never hashed keyword/description CONTENT — only query + sorted
  domain NAMES (retriever.py:446). This was already true before the split (domain_list lived
  in the user prompt but was never part of the key). So the split introduces no cache
  hit/miss regression.
- A4 (verified): test_constitution_retrieval_persistence.py calls _persist_constitution_llm_call
  directly with system_prompt="s", prompt="p" and asserts ONLY cycle, sequence_in_cycle,
  retrieval_phase, and token_usage_json — never the text location/shape. So the persisted
  payload shape change does not break it. Verified by reading the whole file.
- A5 (verified): no test pins max_parallel_agents == 2 or asserts the resolver default;
  test_mstack_cli.py:360-365 passes --max-parallel-agents 4 explicitly and asserts 4;
  test_constitution_retrieval_context_propagation.py:45 already uses
  _fake_retriever(max_parallel=4) with 3 agents and is batch-size-independent. Verified by
  grep (no "max_parallel_agents == 2" in tests/**) and by reading both files.
- A6 (RESOLVED by Codex review — cosmetic, non-blocking): the UI Domain-retrieval TABLE uses
  token fields only (ui/app.py:2216-2250, request.html:297-345), so token accounting is
  unaffected. HOWEVER the request page ALSO renders each call's raw `prompt` and `system_prompt`
  text (request.html:1252-1256, :1507-1511, :1590-1594). After the split the domain_prefilter
  row will show the large static block under "system_prompt" and only the query under "prompt".
  This is a display shape change, NOT a governance change — acceptable/expected. Implementer:
  eyeball the request-detail page once after the change to confirm it renders sanely (no
  truncation/escaping bug); no code change expected.

## Constraints
- PROJECT_SPEC section 5 invariants — explicit non-impact:
  - 5.3 Hard-signal supremacy (P0): UNTOUCHED. The prefilter only NARROWS the principle pool
    by selecting domain overlays; it never sets final_action and never overrides hard topical
    signals. Hard signals are computed downstream by the risk minis, not here.
  - 5.5 core is retrieval-only (P0): UNTOUCHED. ALWAYS_EVALUATE = {"core"} (retriever.py:279)
    keeps core always in relevant (:476), independent of both changes. core is excluded from
    domains_to_check and thus from domain_list (:447), so the system-prompt rewrite cannot
    turn core into an overlay.
  - 5.6 Observability best-effort: UNTOUCHED. Persistence stays wrapped as today (_call_openai
    outer try/except, retriever.py:672-674); only the field VALUES change.
  - 5.1/5.2/5.4/5.7: not on this path.
- Surgical: no broad refactor, no rename of _call_openai/filter_domains, no reorganization of
  the parallel runners. New comments/docstrings in English.
- Prompt-caching parity: mirror the system/user split already applied to risk-minis, critic,
  simulator, hindsight, perspectives (INDEX 5.1, docs/MORALSTACK_CODEBASE_INDEX.md:422-449).
- Backward-compat: the MORALSTACK_CONSTITUTION_MAX_PARALLEL_AGENTS env override must keep
  working; only the fallback default value changes.

## Proposed design

### Intervention 1 — DomainPrefilter system/user split
Because the AVAILABLE DOMAINS list is per-domain-config dynamic (rendered from
_domain_keywords/_domain_descriptions and the per-request available_domains), the system
prompt is NOT a module-level constant like the other modules; it must be composed per request
by a builder. Use an f-string builder (NOT a .format() template) so the literal double-brace
JSON-schema braces need no escaping — matching the mechanism already in filter_domains.

Steps:
1. Add a private builder method at CLASS scope on DomainPrefilter (Codex review: `_domain_line`
   at :485-492 is a LOCAL closure inside filter_domains — the builder must NOT be nested there;
   declare `_build_prefilter_system_prompt` as a proper class method, e.g. right after
   filter_domains or _call_openai). It takes the already-rendered `domain_list` string as its
   argument (the caller keeps computing domain_list via the existing _domain_line closure at
   :485-492):

       def _build_prefilter_system_prompt(self, domain_list: str) -> str:
           """Compose the byte-stable prefilter SYSTEM prompt for the current domain config.

           Contains the static classifier instructions, the AVAILABLE DOMAINS list (rendered
           from the current keywords/descriptions), the classification procedure, falsification
           checks, confidence scale and JSON schema. Byte-identical across requests while the
           domain config and available_domains are unchanged (prompt caching engages). Changes
           only when the rendered domain_list changes — which happens exactly when
           set_domain_keywords / set_domain_descriptions mutate the effective map, and those
           also clear self._cache, keeping local cache and cached prefix consistent.
           """
           return f"""You are a strict semantic domain classifier.

           AVAILABLE DOMAINS:
           {domain_list}

           Your task is to select up to {self.max_domains} domains from AVAILABLE DOMAINS.
           ... <verbatim move of current retriever.py:505-554 static text> ...
           """

   The body is the current static block (retriever.py:501-554) with the USER QUERY lines
   (:495-496) removed and AVAILABLE DOMAINS promoted to the top after the role line. Preserve
   every instruction/phrase verbatim (wrapper rules, encoding rules, procedure, falsification
   checks referencing medical/children/cybersecurity/violent_crime/creative, confidence scale,
   JSON schema with literal double braces).

2. In filter_domains, after domain_list is computed (retriever.py:492), replace the
   prompt = f"""...""" block (:494-555) with:

       system_prompt = self._build_prefilter_system_prompt(domain_list)
       user_prompt = f"USER QUERY:\n{query}"
       ...
       result = self._call_openai(user_prompt, system_prompt=system_prompt, retrieval_phase=retrieval_phase)

3. Change _call_openai signature (retriever.py:573) to accept the system prompt as a
   keyword-only param and thread it into BOTH the API call and persistence (single source):

       def _call_openai(self, prompt: str, *, system_prompt: str,
                        retrieval_phase: str = RETRIEVAL_PHASE_RISK_ROUTING) -> dict[str, Any]:

   - Remove the hardcoded sys_msg = "You are a strict domain classifier. ..." (:592).
   - Use system_prompt at messages=[{system: system_prompt}, {user: prompt}] (:597-600).
   - Use system_prompt=system_prompt in _persist_constitution_llm_call(...) (:658).
   - Everything else in _call_openai (client reuse, token usage, parse/retry/fallback,
     cycle/seq resolution, outer try/except) stays byte-for-byte unchanged.

4. Decisions resolved:
   - Byte-stability per config (task decision 1): the builder renders the static template +
     domain_list. Same config + same available_domains => identical domain_list => identical
     system prompt across requests => caching engages. When config changes,
     set_domain_keywords/set_domain_descriptions change the fingerprint AND clear _cache
     (:341,379); the next request re-renders domain_list (new bytes) and the local cache is
     empty — both change together, no stale-cache/hot-prompt mismatch.
   - Local cache semantics (task decision 2): UNCHANGED. Key stays
     md5(query + ',' sorted(available_domains)) (:446); the split does not touch it. The key
     never hashed keyword/description content — already true pre-split — so no regression;
     called out explicitly here.
   - Persistence shape (task decision 3): after the split, persisted system_prompt = large
     static block, prompt = "USER QUERY:\n{query}". Expected/desired. No test/UI asserts the
     old shape (A4 verified; A6 to verify).
   - Single source (task decision 4): the builder output is used for both the OpenAI system
     message and the persisted system_prompt; _call_openai no longer hardcodes sys_msg. The
     old 11-word system sentence is intentionally dropped (superseded — the new block already
     opens with the classifier role line and ends with "Return JSON only:" + schema;
     response_format=json_object still enforces JSON).

### Intervention 2 — effective max_parallel_agents default 2 -> 4
Critical: bumping ONLY retriever.py:1088 does NOT change runtime behavior — the store overrides
it (store.py:578) and the runtime default flows from resolve_constitution_max_parallel_agents
(deliberation_stack.py:64) and ConstitutionStoreConfig (store.py:462). To actually put 4
agents in one batch on production paths, bump the effective default sources (RECOMMENDED —
Option B):
- deliberation_stack.py:64 — env fallback get_env_int(ENV_..., 2, 1) -> (..., 4, 1).
- store.py:462 — ConstitutionStoreConfig.max_parallel_agents: int = 2 -> 4.
- store.py:498 — ConstitutionStore.__init__(..., max_parallel_agents: int = 2, ...) -> 4.
- cli/models.py:492 — CLIConfig.max_parallel_agents: int = 2 -> 4 (kept consistent; in
  practice overridden by the resolver in parse_args).
- retriever.py:1088 — ConstitutionRetrieverConfig.max_parallel_agents: int = 2 -> 4 (task's
  explicit ask; the fallback for direct-retriever instantiation, keeps all layers consistent).

The env override MORALSTACK_CONSTITUTION_MAX_PARALLEL_AGENTS continues to win when set; only
the fallback changes. No change to the runners (retriever.py:1449-1485,1487-1516):
batch_size = self._config.max_parallel_agents, range(0, len(agents), batch_size),
ThreadPoolExecutor(max_workers=len(batch)), and the contextvars.copy_context().run(...)
propagation (:1467-1473, :1502-1508) are all already correct at any batch size.

## Alternatives considered (rejected)
- Int.1 — module-level constant + .format() (mirroring other modules literally): rejected.
  The JSON schema uses literal double braces and {self.max_domains}/{domain_list} are
  per-config; a .format() template invites brace-escaping bugs and cannot capture per-config
  domain_list. An instance builder is the correct analogue here.
- Int.1 — keep the 11-word system AND move the block into a second system message: rejected.
  Two system messages complicate caching and diverge from the single-system-prefix pattern in
  5.1; one composed system prefix is cleaner and faithful.
- Int.2 Option A — bump ONLY retriever.py:1088 (task's literal wording): rejected as
  insufficient. The store overrides it (store.py:578); production/SDK/CLI paths would still run
  batch=2 -> the stated goal (one batch) is NOT met. Surfaced rather than designed around.
- Int.2 Option C — bump only the resolver + store defaults, leave retriever.py:1088 at 2:
  workable but leaves direct-retriever instantiation (tests, embedders) diverging from
  production. Rejected for consistency; Option B bumps all layers to 4.
- Int.2 — dynamic batch_size = len(agents) (always one batch regardless of config): rejected —
  broader change, removes the operator's ability to cap concurrency, out of scope.

## Files to modify
- moralstack/constitution/retriever.py
  - Add DomainPrefilter._build_prefilter_system_prompt(self, domain_list) -> str.
  - filter_domains (:494-558): replace the combined prompt f-string with system_prompt
    (builder) + user_prompt (query only); update the _call_openai call.
  - _call_openai (:573,592,597-600,656-669): add keyword-only system_prompt param, drop
    hardcoded sys_msg, thread system_prompt into messages + persistence.
  - ConstitutionRetrieverConfig.max_parallel_agents (:1088): 2 -> 4.
- moralstack/constitution/store.py
  - ConstitutionStoreConfig.max_parallel_agents (:462): 2 -> 4.
  - ConstitutionStore.__init__ kwarg default (:498): 2 -> 4.
- moralstack/pipeline/deliberation_stack.py
  - resolve_constitution_max_parallel_agents env fallback (:64): 2 -> 4.
- moralstack/cli/models.py
  - CLIConfig.max_parallel_agents (:492): 2 -> 4.
- moralstack/cli/shell.py (6th source, from Codex review)
  - --max-parallel-agents help text (:1123): "... or 2)" -> "... or 4)". User-facing default
    reference only; runtime already resolves via resolve_constitution_max_parallel_agents
    (shell.py:1158-1167). Argparse default stays None (:1122).
- Tests — MUST-UPDATE with Int.1 (signature change breaks these; see Tests section):
  - tests/test_runtime_pooling.py — add system_prompt="s" to the direct _call_openai calls
    (HARD FAIL otherwise).
  - tests/test_domain_prefilter_descriptions.py — update the _fake stub signature + move
    description-text assertions to system_prompt (SILENT DEGRADE → real failure otherwise).
- Tests — new/extended (see Tests section for detail):
  - tests/test_static_prefix_stability.py (new TestDomainPrefilterStaticPrefixStability class).
  - tests/test_domain_prefilter_cache.py (persistence single-source + parse-ladder tests).
  - tests/test_constitution_retrieval_context_propagation.py (batching-count tests).
  - tests/test_constitution_max_parallel_agents_default.py (new file — default/env-override asserts).
- Docs (section 8, same change):
  - docs/MORALSTACK_CODEBASE_INDEX.md 5.1 (:422-449): add DomainPrefilter to the caching-split
    list; move it out of the A6 "verify-only, no code change" note (:446-447). Update 6
    (:453-462) retrieval section for the prefilter split + new parallelism default.
  - docs/CODEBASE_FACTS.md: add/adjust verified facts — prefilter system/user split,
    persisted-shape change, effective max_parallel_agents default now 4 across
    resolver/store/retriever/CLI.
  - docs/modules/constitution_store.md (examples at :136,159,171 show =2): update the stated
    default to 4 (and note the resolver env fallback).

## Tests to add / modify

### Two MUST-UPDATE call sites (will hard-fail without a fix — land in the SAME commit as Int.1)
These are the highest-value findings from test design; both stem from the new keyword-only
`system_prompt` param on `_call_openai` and must be fixed together with Intervention 1, not
left as "should still pass":
1. `tests/test_runtime_pooling.py` — HARD FAIL. `test_domain_prefilter_reuses_openai_client_across_calls`
   and `test_domain_prefilter_new_client_when_api_key_changes` call `pre._call_openai("q1")` /
   `("q2")` / `("x")` / `("y")` POSITIONALLY with no `system_prompt` (direct call, not through
   `filter_domains`), so after the signature change they raise `TypeError: missing 1 required
   keyword-only argument: 'system_prompt'`. Fix: pass `system_prompt="s"` at each call site.
   Optional add: assert `mock_client.chat.completions.create.call_args_list[0].kwargs["messages"][0]
   == {"role": "system", "content": "s"}` to lock kwarg → wire payload.
2. `tests/test_domain_prefilter_descriptions.py` — SILENT DEGRADE to real failure. Its hand-written
   `_fake` stub is method-bound (self + kwargs forwarded, verified empirically); after the split it
   raises `TypeError: unexpected kwarg 'system_prompt'` INSIDE `filter_domains`'s `except Exception`
   (:569-571), gets swallowed → falls back to core-only → `captured["prompt"]` never set → the 4
   description-content assertions fail. Fix: update `_fake` signature to
   `def _fake(self, prompt, *, system_prompt, retrieval_phase="risk_routing")`, capture BOTH
   `system_prompt` and `prompt`, and move the description/keyword-text assertions
   (`test_prefilter_prompt_includes_descriptions_when_provided`,
   `test_prefilter_falls_back_when_descriptions_missing`, `test_prefilter_partial_descriptions`,
   `test_prefilter_prompt_surfaces_multiple_not_for_scopes`) to check `captured["system_prompt"]`
   (that's where the domain list now lives). The two `set_domain_descriptions` cache tests need no
   prompt assertion change.

### Intervention 1 — new tests
- Extend `tests/test_static_prefix_stability.py` with a NEW class
  `TestDomainPrefilterStaticPrefixStability` (an ADDITION — do NOT touch
  `TestDcclAndRetrieverAlreadyStatic:680-712`, which stays as the domain-AGENT-constant verify-only
  case; DomainPrefilter is genuinely new here, not a move). Fixture:
  `patch.object(DomainPrefilter, "_call_openai", <fake accepting (self, prompt, *, system_prompt, retrieval_phase=...)>)`.
  - `test_system_prompt_byte_identical_across_different_queries_same_config`: two `filter_domains`
    calls, different queries, fixed config → captured `system_prompt` byte-identical; captured
    `prompt` differs and equals exactly `f"USER QUERY:\n{query}"`.
  - `test_user_message_is_query_only_no_static_text_leak`: `"AVAILABLE DOMAINS"`, procedure text,
    falsification-check domain names (medical/children/cybersecurity/violent_crime), and JSON-schema
    keys (`"substantive_payload"`, `"wrapper_cues_ignored"`) are NOT in `prompt` and ARE in
    `system_prompt` (catches a partial split that byte-equality alone misses).
  - `test_system_prompt_changes_when_domain_keywords_change_and_cache_cleared_together`: capture
    `system_prompt` for call 1; call `set_domain_keywords(<different map>)` → returns True AND
    assert `len(pf._cache) == 0` IMMEDIATELY after this mutation (Codex review: a subsequent
    successful `filter_domains` REPOPULATES `_cache` at :566, so the emptiness assertion must be
    right after `set_domain_keywords`, NOT after the next filter_domains call); then capture
    call 2 → `system_prompt` differs (config-change sensitivity). This proves A2:
    hot-prompt/cold-cache never diverge.
  - `test_system_prompt_unchanged_when_config_unchanged_via_idempotent_set`:
    `set_domain_keywords(<same map, different order>)` returns False; `system_prompt` byte-equal
    before/after (fingerprint idempotence at the prompt-bytes level).
  - `test_no_dangling_placeholders_or_double_braces`: neither message contains literal `{query}`,
    `{domain_list}`, `{self.max_domains}`, or stray unescaped `{{`/`}}` outside the JSON schema
    (f-string builder bug guard).
  - `test_static_block_phrases_preserved_verbatim`: load-bearing phrases survive the verbatim move —
    `"Core principle:"`, `"Encoded or obfuscated content:"`, `"Classification procedure:"`,
    `"Falsification checks:"`, `"Use confidence:"`, and each of the 5 falsification sentences — all
    present in `system_prompt` (guards the "semantic drift from reordering" risk).
  - `test_core_never_in_available_domains_section` (invariant lock, §5.5): with `available_domains`
    including `"core"`, `"core"` must NOT appear in the AVAILABLE DOMAINS section of `system_prompt`
    (core is excluded from `domains_to_check` at :447 → never in `domain_list`).
- Extend `tests/test_domain_prefilter_cache.py`:
  - CONFIRM `test_repeated_filter_domains_single_openai_call` (:115-122) passes unchanged — patches
    `_call_openai` via `return_value=` (MagicMock, signature-agnostic). No edit.
  - ADD `test_call_openai_persists_system_prompt_as_built_block_and_prompt_as_query_only`: patch
    `openai.OpenAI` (à la test_runtime_pooling.py:33-36) to return fixed JSON; patch
    `moralstack.constitution.retriever.persist_llm_call` to capture kwargs; call `filter_domains`;
    assert `captured["system_prompt"]` == builder output (recompute `pf._build_prefilter_system_prompt(domain_list)`
    or substring-check the static markers) and `captured["prompt"] == f"USER QUERY:\n{query}"`
    (task decision 4: single source).
  - OPTIONAL (pre-existing Gap 3, flag to user — cheap fix while in-file): loosen
    `_mock_prefilter_openai` in `test_domain_selection_stable_pre_post_idempotence` (:201-219) to
    `def _mock_prefilter_openai(prompt, **_kw)` so it exercises the classifier-response branch
    instead of the exception fallback it silently hits TODAY (independent of this change; not a
    blocker).
- ADD `_call_openai` parse-ladder unit tests (new region of `tests/test_domain_prefilter_cache.py`
  or a small new file) — this code region is being edited (signature at :573) and is not directly
  unit-tested today:
  - `test_call_openai_strict_json_parse_path`: valid JSON content → `data` == parsed dict,
    `fallback_used is False`.
  - `test_call_openai_regex_fallback_on_malformed_json`: JSON wrapped in prose →
    `parse_status == "fallback_ok"`, `fallback_used is True`, `data` == recovered dict.
  - `test_call_openai_fully_unparseable_returns_empty_and_failed_status`: non-JSON text → `data == {}`,
    `parse_status == "failed"`.

### Intervention 2 — new tests
- NEW file `tests/test_constitution_max_parallel_agents_default.py` (no existing home spans all
  layers; grep confirmed zero tests for `ConstitutionStoreConfig(` / `resolve_constitution_max_parallel_agents`).
  Use `monkeypatch.delenv("MORALSTACK_CONSTITUTION_MAX_PARALLEL_AGENTS", raising=False)` in the
  default tests (dev/CI `.env` may set it):
  - `test_constitution_retriever_config_default_is_4`: `ConstitutionRetrieverConfig().max_parallel_agents == 4`.
  - `test_constitution_store_config_default_is_4`: `ConstitutionStoreConfig().max_parallel_agents == 4`.
  - `test_constitution_store_init_kwarg_default_is_4`: store built against real data → retriever
    config `max_parallel_agents == 4` when kwarg omitted.
  - `test_cli_config_default_is_4`: `CLIConfig().max_parallel_agents == 4`.
  - `test_resolve_..._default_is_4_when_env_unset`: `resolve_constitution_max_parallel_agents(None) == 4`.
  - `test_resolve_..._env_override_still_wins` (`setenv(..., "2")`): resolver returns 2.
  - `test_resolve_..._explicit_still_wins_over_env` (`setenv(..., "2")`): `resolve(7) == 7`.
- CLI help/default (Codex review): current parser tests assert only explicit `4`
  (test_mstack_cli.py:360-365); default parsing does NOT assert this field
  (test_mstack_cli.py:323-331). Add a test that the `--max-parallel-agents` help string reads
  "... or 4)" (after the shell.py:1123 edit) and/or that omitting the flag resolves to 4 via the
  resolver with env unset.
- Extend `tests/test_constitution_retrieval_context_propagation.py` (owns `_fake_retriever` /
  fake agents; add a one-line docstring note that it now also locks batching). Use a counting
  `ThreadPoolExecutor` subclass (overrides `__init__` to increment a counter, delegates to
  `super().__init__`) — do NOT mock away real execution (keeps contextvars propagation exercised):
  - `test_four_agents_run_in_single_threadpool_batch_at_max_parallel_4`: 4 fake agents,
    `max_parallel=4` → exactly ONE ThreadPoolExecutor constructed (assert construction count, not
    thread count). Repeat for `_run_agents_parallel`.
  - `test_regression_two_agents_batch_size_2_still_needs_two_batches` (negative control proving the
    counter is meaningful): `max_parallel=2`, 4 agents → TWO constructions. This is the test that
    would catch rejected "Option A".
  - `test_batch_count_1_1_2_for_1_4_5_agents` (edge cases): 1 agent → 1, 4 → 1, 5 → 2 constructions.
    Covers the WIDER blast radius Codex flagged (5-agent case ≈ no-prefilter/legacy paths where
    the wave can include core + every overlay, not just core + 3): confirms batch=4 still splits
    correctly above 4 agents so those paths don't silently run one giant batch.
  - RE-RUN `test_enhanced_agents_inherit_observability_context` /
    `test_legacy_agents_inherit_observability_context` (:49-73) unchanged — confirm contextvar
    propagation holds identically at batch=4 (run_id/request_id attribution on llm_calls rows).

### Regression / invariant guards (re-run unmodified)
- `tests/governance_invariants/test_hard_signal_not_overridable_by_retrieval_wave.py` — structurally
  independent (`path_router.py` consumes only `risk_signals`, never principles/prompts). No change.
- `tests/test_signals_mini_principle_free.py` (Codex review) — re-run unmodified. Locks that ONLY
  the intent mini receives constitution context and the signals mini stays principle-free
  (:76-105). Int.1 touches only the prefilter classifier prompt, not the risk-mini wiring, so this
  must stay green — add it to the Int.1 scoped command below.
- core-retrieval-only: locked additionally by `test_core_never_in_available_domains_section` above.

### Fixtures / mocks
- OpenAI: never call the real SDK. Two patterns, both already in the suite —
  `patch.object(DomainPrefilter, "_call_openai", ...)` (cache/stability tests) and
  `patch("openai.OpenAI")` returning a MagicMock with `chat.completions.create` configured
  (parser + single-source persistence tests, mirrors test_runtime_pooling.py:15-23,33-36).
- Observability/DB: reuse the `_fresh_obs_singleton` autouse fixture pattern
  (test_domain_prefilter_cache.py:42-65) + `tmp_path` + `MORALSTACK_DB_PATH` /
  `MORALSTACK_PERSIST_MODE=db_only` for any test that flushes through `get_obs()`.
- No frozen clock needed (duration_ms/started_at never asserted by value).
- ThreadPoolExecutor counting double as described above (subclass, delegate to super).

### Commands
- Scoped (Int.1):
  `.\venv\Scripts\python.exe -m pytest tests/test_static_prefix_stability.py tests/test_domain_prefilter_cache.py tests/test_domain_prefilter_descriptions.py tests/test_runtime_pooling.py tests/test_constitution_retrieval_persistence.py tests/test_signals_mini_principle_free.py -v`
- Scoped (Int.2):
  `.\venv\Scripts\python.exe -m pytest tests/test_constitution_retrieval_context_propagation.py tests/test_mstack_cli.py tests/test_constitution_max_parallel_agents_default.py -v`
- Invariant guard: `.\venv\Scripts\python.exe -m pytest tests/governance_invariants/ -v`
- Full suite (required, §7): `.\venv\Scripts\python.exe -m pytest`
- Gate: `.\venv\Scripts\python.exe -m pre_commit run -a`

## Risks
- Int.1 semantic drift from reordering: promoting AVAILABLE DOMAINS above the role line /
  moving USER QUERY out changes the exact bytes the classifier sees. Blast radius: domain
  selection could shift on edge queries -> different overlay pool (never hard-signal behavior;
  5.3 intact). Mitigation: move text verbatim, keep all instructions; add the
  content-preservation assertions above; the local _cache and downstream decision policy are
  unaffected.
- Int.1 persistence/UI shape (A6 unverified): if the UI request-detail view assumes the static
  block lives in prompt, the display could look odd. Blast radius: UI cosmetics only, no
  governance impact. Mitigation: grep ui/app.py for domain_prefilter before merge; the token
  accounting reads token_usage_json, not text location.
- Int.2 concurrency: 4 threads instead of 2 -> up to 2x concurrent OpenAI calls per prefilter
  round, higher burst/rate-limit exposure. Blast radius WIDER than "core + up to 3 domains"
  (Codex review): with prefilter DISABLED, relevant_domains = available_domains (:1174-1188);
  with LEGACY retrieval, _create_domain_agents builds core + EVERY overlay (:1270-1285,
  :1381-1408). Both runners share batch_size (:1457-1461, :1495-1499), so batch=4 raises
  concurrency on those paths too (up to ~22 agents in 4-wide batches).
  CORRECTNESS caveat (Codex review): per-agent exceptions are swallowed to empty results
  (:1479-1483, :1514-1516) and prefilter API failure returns {} (:672-674). Under provider
  THROTTLING, higher concurrency can therefore change the retrieved-principle SET (more agents
  failing empty), not just latency — a safety-relevant performance change, never a hard-signal
  change (5.3 intact; hard signals are computed by the risk minis, not from retrieval).
  Mitigation: env override MORALSTACK_CONSTITUTION_MAX_PARALLEL_AGENTS lets operators cap it
  back to 2 until rate-limit behavior is observed.
- Int.2 scope creep: touching store/resolver/CLI defaults is broader than the task's literal
  "bump retriever.py:1088". This is deliberate and surfaced (Option B) because the
  retriever-only bump does not meet the goal. Needs user sign-off on changing the production
  default (see Open decision).

## Acceptance criteria
- [ ] DomainPrefilter sends a SYSTEM message containing the full static block + AVAILABLE
      DOMAINS; the USER message is exactly "USER QUERY:\n{query}".
- [ ] _call_openai uses the passed system_prompt for both the API system message and the
      persisted system_prompt; no hardcoded 11-word sys_msg remains.
- [ ] SYSTEM message is byte-identical across two requests with different queries, same
      config/available_domains; it changes after set_domain_keywords/set_domain_descriptions.
- [ ] response_format=json_object, temperature=0.1, max_output_tokens=200, and the
      parse/retry/fallback path are unchanged (diff shows no change in :601-654,672-674).
- [ ] Local _cache key at retriever.py:446 is unchanged; hit/miss semantics identical.
- [ ] ConstitutionStoreConfig().max_parallel_agents == 4,
      ConstitutionRetrieverConfig().max_parallel_agents == 4,
      resolve_constitution_max_parallel_agents(None) == 4 (env unset), CLIConfig default 4.
- [ ] With 4 agents, exactly one ThreadPoolExecutor batch runs (no second batch).
- [ ] MORALSTACK_CONSTITUTION_MAX_PARALLEL_AGENTS=2 still forces batch=2.
- [ ] pytest for test_static_prefix_stability, test_constitution_retrieval_persistence,
      test_constitution_retrieval_context_propagation, test_mstack_cli green; full pytest green.
- [ ] Docs 5.1, 6, CODEBASE_FACTS, constitution_store.md updated in the same change.

## Implementation checklist
1. Read retriever.py:417-674 in full (done in planning) before editing.
2. Add _build_prefilter_system_prompt; verbatim-move static text out of the combined prompt.
3. Rewrite filter_domains :494-558 to build system + query-only user; update the call.
4. Change _call_openai signature/body: add system_prompt kwarg, drop sys_msg, thread into
   messages + persistence.
5. A6 resolved (Codex): after the change, eyeball the request-detail page once to confirm the
   domain_prefilter row renders sanely with the static block under system_prompt (no code change
   expected; token table already unaffected).
6. Bump defaults to 4 in all SIX places: retriever.py:1088, store.py:462, store.py:498,
   deliberation_stack.py:64, cli/models.py:492, and the shell.py:1123 help string ("or 2"->"or 4").
7. FIX the two MUST-UPDATE call sites in the SAME commit as step 4: tests/test_runtime_pooling.py
   (add system_prompt=) and tests/test_domain_prefilter_descriptions.py (_fake signature + assert on
   system_prompt). Both hard/silent-fail without this.
8. Add/extend tests (Int.1 stability + user-only + config-change + verbatim + persistence single-source
   + parse-ladder; Int.2 default/env asserts + batching-count).
9. Update docs (INDEX 5.1/6, CODEBASE_FACTS, constitution_store.md).
10. Run the scoped suites, then the full suite + pre-commit. Report real outcomes.

## Rollback plan
- Both interventions are independent; revert per commit.
- Int.1: restore the combined prompt f-string in filter_domains and the hardcoded sys_msg in
  _call_openai (revert the retriever.py hunk). No persisted-data migration needed — only new
  rows carry the new shape; old rows are untouched.
- Int.2: revert the five 4 -> 2 default edits. Operators can also mitigate live without a
  revert by setting MORALSTACK_CONSTITUTION_MAX_PARALLEL_AGENTS=2.

## Open decision — RESOLVED (user sign-off 2026-07-08: Option B)
Intervention 2 changes a PRODUCTION runtime default (agent concurrency). The task text scoped
it to retriever.py:1088 only, but that bump is inert on store-mediated paths. **DECISION: Option B**
— bump all five effective sources (resolver env-fallback deliberation_stack.py:64,
ConstitutionStoreConfig store.py:462, ConstitutionStore.__init__ store.py:498, CLIConfig
cli/models.py:492, ConstitutionRetrieverConfig retriever.py:1088) to 4; env override
MORALSTACK_CONSTITUTION_MAX_PARALLEL_AGENTS preserved. Option A (retriever-only, goal unmet) and
Option C (resolver+store only, direct-retriever paths diverge) rejected.

Codex caveat acknowledged (does NOT reopen the decision): the DEFAULT runtime path uses enhanced
retrieval + prefilter enabled (use_enhanced_retrieval=True/use_domain_prefilter=True), so it runs
core + up to 3 prefiltered domains. On NON-default paths (prefilter disabled, or legacy retrieval)
the wave can include core + every overlay (~22 agents), and batch=4 raises concurrency there too.
Operators on those configs who hit provider throttling should set
MORALSTACK_CONSTITUTION_MAX_PARALLEL_AGENTS=2. This is documented in Risks/Int.2 and CODEBASE_FACTS.
