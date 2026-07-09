# Implementation Handoff — optimize-domain-prefilter-caching-and-parallelism

You are the **MoralStack Implementer** (`claude-implementer`, Claude Sonnet, isolated
sub-agent context). Implement the approved plan below. Follow the non-negotiable rules from
`ai/prompts/claude-implementation-template.md`: implement ONLY the approved plan, modify ONLY
the allowed files, no opportunistic refactoring, do NOT weaken/skip/delete tests, honor every
§5 invariant, run the verification commands and report REAL output, STOP and report if
anything is ambiguous or blocking. Do NOT git add/commit/push.

## Context
MoralStack is a governance engine for LLMs. This task optimizes the **domain-identification**
stage of the constitution retrieval (the `DomainPrefilter` classifier + per-domain agents in
`moralstack/constitution/retriever.py`). Two surgical, independent optimizations. The plan was
reviewed by Codex (verdict **APPROVE_WITH_CHANGES**, no blockers; all non-blocking findings
already folded into the plan).

- Approved plan: `ai/plans/optimize-domain-prefilter-caching-and-parallelism.md` (READ IT IN
  FULL — it is authoritative; this handoff summarizes but the plan governs).
- Codex review: `ai/reviews/codex-plan-review-optimize-domain-prefilter-caching-and-parallelism-20260708-132049.md`.

## Objective
1. **Intervention 1** — split the `DomainPrefilter` classifier prompt into a byte-stable SYSTEM
   prompt (all static text: classifier instructions + `AVAILABLE DOMAINS` list + procedure +
   falsification checks + confidence scale + JSON schema) and a query-only USER message
   (`USER QUERY:\n{query}`), so the large static prefix becomes cache-eligible for OpenAI
   automatic prompt caching. Mirrors the §5.1 system/user split already applied elsewhere.
2. **Intervention 2** — raise the effective `max_parallel_agents` default from 2 to 4 across ALL
   SIX sources so up-to-4 prefilter agents run in one parallel batch. Env override preserved.

## Approved design (authoritative detail in the plan — "Proposed design" section)

### Intervention 1 — DomainPrefilter system/user split (`moralstack/constitution/retriever.py`)
- Add a **class-scoped** method `DomainPrefilter._build_prefilter_system_prompt(self, domain_list: str) -> str`
  (NOT nested inside `filter_domains`; `_domain_line` at :485-492 is a local closure — do not
  put the builder there). Use an **f-string** (NOT `.format()`) so the literal JSON-schema double
  braces need no escaping. Body = the current static block (retriever.py:501-554) with the
  `USER QUERY:` lines removed and `AVAILABLE DOMAINS:` promoted to the top after the classifier
  role line. **Move every instruction/phrase verbatim** — do not drop or reword (wrapper rules,
  encoding rules, classification procedure, the 5 falsification checks referencing
  medical/children/cybersecurity/violent_crime/creative-wrapper, confidence scale, JSON schema).
- In `filter_domains` (retriever.py:494-558): after `domain_list` is computed (:492), replace the
  combined `prompt = f"""..."""` block (:494-555) with:
  ```python
  system_prompt = self._build_prefilter_system_prompt(domain_list)
  user_prompt = f"USER QUERY:\n{query}"
  ...
  result = self._call_openai(user_prompt, system_prompt=system_prompt, retrieval_phase=retrieval_phase)
  ```
- Change `_call_openai` signature (retriever.py:573) to keyword-only `system_prompt`:
  ```python
  def _call_openai(self, prompt: str, *, system_prompt: str,
                   retrieval_phase: str = RETRIEVAL_PHASE_RISK_ROUTING) -> dict[str, Any]:
  ```
  - Remove the hardcoded `sys_msg = "You are a strict domain classifier. ..."` (:592).
  - Use `system_prompt` in `messages=[{system: system_prompt}, {user: prompt}]` (:597-600).
  - Use `system_prompt=system_prompt` in `_persist_constitution_llm_call(...)` (:658) — SINGLE
    SOURCE: the builder output feeds BOTH the API call and persistence.
  - Everything else in `_call_openai` (client reuse, token usage, parse/retry/regex fallback,
    cycle/seq resolution, outer try/except at :672-674) stays BYTE-FOR-BYTE unchanged.
- Persisted-shape change is EXPECTED/desired: `system_prompt` = large static block, `prompt` =
  query only. Do not add migration; only new rows carry the new shape.

### Intervention 2 — effective max_parallel_agents default 2 → 4 (SIX places)
Bump to 4:
1. `moralstack/constitution/retriever.py:1088` — `ConstitutionRetrieverConfig.max_parallel_agents`.
2. `moralstack/constitution/store.py:462` — `ConstitutionStoreConfig.max_parallel_agents`.
3. `moralstack/constitution/store.py:498` — `ConstitutionStore.__init__` kwarg default.
4. `moralstack/pipeline/deliberation_stack.py:64` — `resolve_constitution_max_parallel_agents`
   env fallback `get_env_int(ENV_..., 2, 1)` → `(..., 4, 1)`.
5. `moralstack/cli/models.py:492` — `CLIConfig.max_parallel_agents`.
6. `moralstack/cli/shell.py:1123` — `--max-parallel-agents` help string: "... or 2)" → "... or 4)".
   (Argparse default stays `None` at :1122 — do NOT change that; runtime resolves via the
   resolver at shell.py:1158-1167.)
Do NOT change the runners (retriever.py:1449-1518): `batch_size = self._config.max_parallel_agents`,
`range(0, len(agents), batch_size)`, `ThreadPoolExecutor`, and the `contextvars.copy_context().run(...)`
propagation (:1467-1473, :1502-1508) are already correct at any batch size.
The env override `MORALSTACK_CONSTITUTION_MAX_PARALLEL_AGENTS` must keep winning — only the
fallback default changes.

**VERIFY the exact line numbers before editing** — read each region in full first; the code is
authoritative over these snapshots.

## Files ALLOWED to modify
Source:
- `moralstack/constitution/retriever.py`
- `moralstack/constitution/store.py`
- `moralstack/pipeline/deliberation_stack.py`
- `moralstack/cli/models.py`
- `moralstack/cli/shell.py`
Tests (MUST-UPDATE — signature change breaks these; fix in the same change):
- `tests/test_runtime_pooling.py` — the two direct `pre._call_openai("q1")` / `("q2")` / `("x")` /
  `("y")` calls need `system_prompt="s"` (else `TypeError`). Optionally assert the passed
  `system_prompt` reaches `messages[0]`.
- `tests/test_domain_prefilter_descriptions.py` — update the `_fake` stub signature to
  `def _fake(self, prompt, *, system_prompt, retrieval_phase="risk_routing")`, capture BOTH
  `system_prompt` and `prompt`, and move the description/keyword-text assertions
  (`test_prefilter_prompt_includes_descriptions_when_provided`,
  `test_prefilter_falls_back_when_descriptions_missing`, `test_prefilter_partial_descriptions`,
  `test_prefilter_prompt_surfaces_multiple_not_for_scopes`) to check `captured["system_prompt"]`
  (that's where the domain list now lives).
Tests (new / extended):
- `tests/test_static_prefix_stability.py` — new class `TestDomainPrefilterStaticPrefixStability`
  (ADDITION; do NOT touch `TestDcclAndRetrieverAlreadyStatic:680-712`).
- `tests/test_domain_prefilter_cache.py` — add single-source persistence test + `_call_openai`
  parse-ladder tests; confirm `test_repeated_filter_domains_single_openai_call` still passes
  (no edit); corrected cache-clear assert (see below).
- `tests/test_constitution_retrieval_context_propagation.py` — add batch-count tests (counting
  `ThreadPoolExecutor` subclass); re-run existing context-propagation tests unchanged.
- `tests/test_constitution_max_parallel_agents_default.py` — NEW file (default/env-override asserts).
Docs (same change, §8):
- `docs/MORALSTACK_CODEBASE_INDEX.md` (§5.1 add DomainPrefilter to caching-split list & move it out
  of the A6 "verify-only" note; §6 retrieval — prefilter split + new parallelism default).
- `docs/CODEBASE_FACTS.md` (verified facts: prefilter system/user split, persisted-shape change,
  effective max_parallel_agents default now 4 across resolver/store/retriever/CLI/help).
- `docs/modules/constitution_store.md` (examples showing `=2` → 4; note the resolver env fallback).

## Files NOT to modify
- Any governance/decision code: `moralstack/orchestration/**`, `moralstack/runtime/**`,
  `moralstack/models/risk/**`, `moralstack/compliance/**` (retrieval feeds these but this change
  must not touch them).
- The parallel runners' bodies in `retriever.py` (`_run_enhanced_agents_parallel`,
  `_run_agents_parallel`) beyond reading — only the DEFAULT config value changes, not the runner
  logic.
- `_domain_agent_cache_key`, `_persist_constitution_llm_call` signatures, the local `_cache` key
  computation at `retriever.py:446` (must stay `md5(query + ',' sorted(available_domains))`).
- `moralstack/ui/**` — A6 is cosmetic/resolved; do NOT change UI code. Just eyeball-note is optional.
- Any test not listed above; do not weaken/skip/delete any test.
- Do NOT touch `.claude/**`, hooks, or unrelated docs.

## Invariants in play (PROJECT_SPEC §5) — how to keep them
- **§5.5 core is retrieval-only (P0):** `ALWAYS_EVALUATE = {"core"}` (retriever.py:279) and the
  exclusion of `core` from `domains_to_check` (:447) MUST remain — `core` must never appear in the
  new SYSTEM prompt's `AVAILABLE DOMAINS` section. Add the lock test
  `test_core_never_in_available_domains_section`.
- **§5.3 hard-signal supremacy (P0):** UNTOUCHED. The prefilter only narrows the overlay pool;
  hard signals are computed by the risk minis / decision policy downstream, never from retrieval.
  Do not alter any routing/decision path.
- **§5.6 observability best-effort:** the `_call_openai` outer try/except (:672-674) and
  `filter_domains`'s own `except Exception` (:569-571) stay unchanged; only field VALUES change.
- **§5.1 prompt-caching parity:** faithful system/user split, single system prefix (drop the old
  11-word sentence — the new block opens with the classifier role line; `response_format=json_object`
  still enforces JSON).

## Checklist
1. Read `retriever.py:417-674`, `:1084-1092`, `:1449-1518` in full before editing.
2. Add class-scoped `_build_prefilter_system_prompt`; verbatim-move the static text out.
3. Rewrite `filter_domains` :494-558: build `system_prompt` + query-only `user_prompt`; update call.
4. Change `_call_openai` signature/body: add keyword-only `system_prompt`, drop hardcoded sys_msg,
   thread into messages + persistence. Keep parse/retry/fallback byte-identical.
5. FIX the two MUST-UPDATE test files (test_runtime_pooling.py, test_domain_prefilter_descriptions.py)
   in the SAME change.
6. Bump the default to 4 in all SIX sources (retriever, store×2, deliberation_stack, cli/models,
   shell help string).
7. Add/extend tests (Int.1 stability/user-only/config-change/verbatim/persistence-single-source/
   parse-ladder + core-lock; Int.2 default/env asserts + batch-count edge cases).
8. Update docs (INDEX §5.1/§6, CODEBASE_FACTS, constitution_store.md).
9. Run the scoped suites, then the full suite + pre-commit. Report REAL outcomes.

## Required tests (see plan "Tests to add / modify" for full detail)
Scoped (Int.1):
```
.\venv\Scripts\python.exe -m pytest tests/test_static_prefix_stability.py tests/test_domain_prefilter_cache.py tests/test_domain_prefilter_descriptions.py tests/test_runtime_pooling.py tests/test_constitution_retrieval_persistence.py tests/test_signals_mini_principle_free.py -v
```
Scoped (Int.2):
```
.\venv\Scripts\python.exe -m pytest tests/test_constitution_retrieval_context_propagation.py tests/test_mstack_cli.py tests/test_constitution_max_parallel_agents_default.py -v
```
Invariant guard:
```
.\venv\Scripts\python.exe -m pytest tests/governance_invariants/ -v
```
Full suite (REQUIRED before declaring done, §7):
```
.\venv\Scripts\python.exe -m pytest
```
Lint/type/format gate:
```
.\venv\Scripts\python.exe -m pre_commit run -a
```
Key test-shape notes:
- Byte-stability: SYSTEM prompt byte-identical across different queries same config; USER message ==
  exactly `f"USER QUERY:\n{query}"`; static text NOT in user, IS in system.
- Cache-clear (corrected): assert `len(pf._cache) == 0` IMMEDIATELY after `set_domain_keywords(...)`
  (a subsequent `filter_domains` REPOPULATES `_cache` at :566 — do NOT assert emptiness after it).
- Batch-count: counting `ThreadPoolExecutor` subclass; 1 agent→1, 4→1, 5→2 constructions; negative
  control `max_parallel=2`, 4 agents → 2 constructions.
- Default asserts: use `monkeypatch.delenv("MORALSTACK_CONSTITUTION_MAX_PARALLEL_AGENTS", raising=False)`;
  `ConstitutionRetrieverConfig()/ConstitutionStoreConfig()/CLIConfig().max_parallel_agents == 4`;
  `resolve_constitution_max_parallel_agents(None) == 4`; env `"2"` → resolver returns 2; `resolve(7) == 7`.

## Acceptance criteria (from the plan)
- [ ] DomainPrefilter sends a SYSTEM message with the full static block + AVAILABLE DOMAINS; USER
      message is exactly `USER QUERY:\n{query}`.
- [ ] `_call_openai` uses the passed `system_prompt` for both the API system message and persistence;
      no hardcoded 11-word sys_msg remains.
- [ ] SYSTEM message byte-identical across two requests (different queries, same config); changes
      after `set_domain_keywords`/`set_domain_descriptions`.
- [ ] `response_format=json_object`, `temperature=0.1`, `max_output_tokens=200`, and the parse/retry/
      fallback path are unchanged (diff shows no change in :601-654,672-674).
- [ ] Local `_cache` key at retriever.py:446 unchanged; hit/miss semantics identical.
- [ ] `ConstitutionStoreConfig()/ConstitutionRetrieverConfig()/CLIConfig().max_parallel_agents == 4`;
      `resolve_constitution_max_parallel_agents(None) == 4` (env unset); shell help says "or 4".
- [ ] With 4 agents, exactly one ThreadPoolExecutor batch runs; `...=2` still forces batch=2.
- [ ] Scoped suites + full pytest + pre-commit all green (report REAL output).
- [ ] Docs §5.1/§6, CODEBASE_FACTS, constitution_store.md updated in the same change.

## Risks (mitigations in the plan)
- Int.1 semantic drift from reordering → move text VERBATIM; the content-preservation tests guard it.
- Int.1 persistence/UI shape change → expected; A6 UI is cosmetic (do not modify UI).
- Int.2 concurrency under provider throttling can change the retrieved-principle set on
  no-prefilter/legacy paths (swallowed agent errors) → env override caps it back to 2; never a
  hard-signal change.

## Required output (end of your run)
- files modified (list);
- tests added/updated (list);
- commands run;
- results (REAL output — paste the pytest + pre-commit summary lines);
- deviations from the plan (with justification);
- residual problems / blockers.
Do NOT commit. Do NOT touch files outside the allowed list.
