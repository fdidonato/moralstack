# Implementation Report — optimize-domain-prefilter-caching-and-parallelism

(Verbatim report returned by the `claude-implementer` Sonnet sub-agent.)

## Report

**Goal:** Implement `ai/handoffs/optimize-domain-prefilter-caching-and-parallelism-handoff.md` — split `DomainPrefilter`'s classifier prompt into a byte-stable SYSTEM prompt + query-only USER message (Intervention 1), and raise the effective `max_parallel_agents` default from 2→4 across all six sources (Intervention 2).

### Files modified
- `moralstack/constitution/retriever.py` — added `DomainPrefilter._build_prefilter_system_prompt(domain_list)` (class-scoped, f-string builder, verbatim static block); rewrote `filter_domains` to build `system_prompt` + `user_prompt = f"USER QUERY:\n{query}"` and call `_call_openai(user_prompt, system_prompt=system_prompt, retrieval_phase=...)`; `_call_openai` signature now takes keyword-only `system_prompt`, dropped the hardcoded 11-word `sys_msg`, threads `system_prompt` into both the OpenAI `messages` and `_persist_constitution_llm_call` (single source); bumped `ConstitutionRetrieverConfig.max_parallel_agents` 2→4. Parse/retry/fallback ladder and outer try/except left byte-for-byte unchanged (confirmed via diff).
- `moralstack/constitution/store.py` — `ConstitutionStoreConfig.max_parallel_agents` and `ConstitutionStore.__init__` kwarg default 2→4.
- `moralstack/pipeline/deliberation_stack.py` — `resolve_constitution_max_parallel_agents` env fallback 2→4.
- `moralstack/cli/models.py` — `CLIConfig.max_parallel_agents` default 2→4.
- `moralstack/cli/shell.py` — `--max-parallel-agents` help string "...or 2)" → "...or 4)" (argparse default stays `None`, unchanged).
- `docs/MORALSTACK_CODEBASE_INDEX.md` — §5.1 adds DomainPrefilter to the caching-split list (moved out of the A6 verify-only note); §6 documents the prefilter split and the new max_parallel_agents=4 default across sources.
- `docs/CODEBASE_FACTS.md` — new verified-fact row for this change (prefilter split, single-source persistence, `core`-exclusion invariant, new default=4 across sources, full-suite count).
- `docs/modules/constitution_store.md` — updated `=2` examples to `=4` and the resolver fallback note.

### Tests added / modified
- `tests/test_runtime_pooling.py` — MUST-UPDATE: added `system_prompt="s"` to the four direct `_call_openai` calls; added an assertion locking the outbound `messages[0]` system content.
- `tests/test_domain_prefilter_descriptions.py` — MUST-UPDATE: `_fake` stub now `def _fake(self, prompt, *, system_prompt, retrieval_phase=...)`, captures both; the 4 description-content assertions moved to `captured["system_prompt"]`.
- `tests/test_static_prefix_stability.py` — new `TestDomainPrefilterStaticPrefixStability` class (7 tests: byte-identity across queries, user-message-is-query-only/no-leak, config-change sensitivity + cache-clear timing, idempotent-set stability, no dangling placeholders, verbatim-phrase preservation, `core`-never-in-AVAILABLE-DOMAINS lock). `TestDcclAndRetrieverAlreadyStatic` untouched.
- `tests/test_domain_prefilter_cache.py` — added `test_call_openai_persists_system_prompt_as_built_block_and_prompt_as_query_only` (single-source lock) + 3 parse-ladder tests (`strict_json_parse_path`, `regex_fallback_on_malformed_json`, `fully_unparseable_returns_empty_and_failed_status`). `test_repeated_filter_domains_single_openai_call` unedited and still passes.
- `tests/test_constitution_retrieval_context_propagation.py` — added a counting `ThreadPoolExecutor` subclass and 4 batch-count tests (4→1 batch enhanced+legacy, negative control batch=2→2 batches, edge cases 1/4/5 agents→1/1/2 batches). Existing context-propagation tests unmodified.
- `tests/test_constitution_max_parallel_agents_default.py` — new file: 9 tests covering all five default sources, env-override-wins, explicit-wins-over-env, CLI omitted-flag resolution, and the CLI help-string text (kept out of `tests/test_mstack_cli.py` since that file was not in the handoff's allowed-to-modify list; ran it read-only to confirm no regression).

### Commands run (real output)
```
pytest tests/test_static_prefix_stability.py tests/test_domain_prefilter_cache.py tests/test_domain_prefilter_descriptions.py tests/test_runtime_pooling.py tests/test_constitution_retrieval_persistence.py tests/test_signals_mini_principle_free.py -v
→ 76 passed in 4.67s

pytest tests/test_constitution_retrieval_context_propagation.py tests/test_mstack_cli.py tests/test_constitution_max_parallel_agents_default.py -v
→ 49 passed in 1.59s

pytest tests/governance_invariants/ -v
→ 12 passed in 0.41s

pytest   (full suite)
→ 2173 passed, 1 deselected (slow), 101 warnings in 97.50s

pytest -q  (re-run after doc edits)
→ 2173 passed, 1 deselected, 101 warnings in 87.68s

pre_commit run -a
→ trim trailing whitespace: Passed
  fix end of files: Passed
  ruff check: Passed
  black: Passed
  mypy: Passed
```

### Deviations from the plan
- CLI help-string/default-resolution assertion was placed in the new `tests/test_constitution_max_parallel_agents_default.py` rather than `tests/test_mstack_cli.py`. Reason: `test_mstack_cli.py` is not listed in the handoff's "Files allowed to modify" (it's only listed as a scoped-run target), whereas the new file is explicitly allowed. This is the smallest-diff interpretation that still satisfies the plan's "add a test that the help string reads 'or 4' and/or resolves to 4" requirement.
- `git status` shows the file as new, not modified — confirmed no edits landed in `tests/test_mstack_cli.py`.
- Everything else implemented per plan (Option B, all six sources) with no other deviation.

### Residual problems / blockers
None. One pre-existing, out-of-scope gap noted (not fixed, per plan's explicit "OPTIONAL... not a blocker" instruction): `test_domain_selection_stable_pre_post_idempotence` in `tests/test_domain_prefilter_cache.py` (:201-219, pre-existing before this task) patches `_call_openai` via `patch(...side_effect=_mock_prefilter_openai)` at the class level, which is called without a bound `self`; combined with `_mock_prefilter_openai(prompt)` not accepting `retrieval_phase`/`system_prompt`, the call already silently hits the swallowed-exception fallback (core-only) rather than exercising the classifier branch. This existed before the split (plan's "Gap 3") and is unrelated to this change's scope — flagging per PROJECT_SPEC §9 rather than fixing it.

A6 (UI eyeball) was not interactively verified — no running UI in this session — but is expected to render sanely per the plan (raw string fields, token accounting reads `token_usage_json` not text location); no UI code was touched, consistent with the "Files NOT to modify" list.
