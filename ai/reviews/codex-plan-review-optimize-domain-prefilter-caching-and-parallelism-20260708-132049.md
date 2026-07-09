# Codex Plan Review

## Verdict

APPROVE_WITH_CHANGES

## Blocking issues (Each: what, why it blocks, the path:line evidence, and what must change.)

None.

## Non-blocking issues

- The plan misses a user-facing default reference in CLI help. Runtime parsing resolves through `resolve_constitution_max_parallel_agents` and returns it in `CLIConfig` at `moralstack/cli/shell.py:1158-1167`, but the help still says fallback default `2` at `moralstack/cli/shell.py:1120-1124`. Add `moralstack/cli/shell.py` to files to modify and test/help expectations.

- The parallelism blast radius is wider than "core + up to 3 domains." With enhanced retrieval and prefilter disabled, `relevant_domains = available_domains` at `moralstack/constitution/retriever.py:1174-1188`; with legacy retrieval, `_create_domain_agents()` builds core plus every overlay at `moralstack/constitution/retriever.py:1270-1285` and `moralstack/constitution/retriever.py:1381-1408`. Both runners use the same batch size at `moralstack/constitution/retriever.py:1457-1461` and `moralstack/constitution/retriever.py:1495-1499`. The plan should call out no-prefilter and legacy paths explicitly.

- The plan's "rate-limits, not correctness" risk framing is too weak. Agent exceptions are converted to empty results at `moralstack/constitution/retriever.py:1479-1483` and `moralstack/constitution/retriever.py:1514-1516`; prefilter API failures return `{}` at `moralstack/constitution/retriever.py:672-674`. Higher default concurrency can therefore change retrieved principles under provider throttling, not just latency.

- The proposed builder placement is ambiguous. `_domain_line` is a local closure inside `filter_domains` at `moralstack/constitution/retriever.py:485-492`, and the current prompt starts immediately after at `moralstack/constitution/retriever.py:494-555`; a real `_build_prefilter_system_prompt` method must be class-scoped, not inserted inside that local region.

- The UI assumption is mostly cosmetic but should be resolved before merge. The Domain retrieval table uses token fields only at `moralstack/ui/app.py:2216-2250` and `moralstack/ui/templates/request.html:297-345`, but the request page also renders each call's `prompt` and `system_prompt` at `moralstack/ui/templates/request.html:1252-1256`, `moralstack/ui/templates/request.html:1507-1511`, and `moralstack/ui/templates/request.html:1590-1594`.

## Missing tests

- Add or update a CLI help/default test for `--max-parallel-agents`; current parser tests only assert explicit `4` at `tests/test_mstack_cli.py:360-365`, while default parsing does not assert this field at `tests/test_mstack_cli.py:323-331`.

- Add batch-count coverage for enhanced prefilter-disabled and legacy retrieval paths, not only the fake direct runner. Current context propagation tests only use fake 3-agent lists at `tests/test_constitution_retrieval_context_propagation.py:45-69`.

- Include `tests/test_signals_mini_principle_free.py` in invariant reruns. It locks that only the intent mini receives constitution context and the signals mini remains principle-free at `tests/test_signals_mini_principle_free.py:76-105`; the plan currently names only the hard-signal route invariant.

- Fix the proposed cache-clearing test shape: `set_domain_keywords` clears cache at `moralstack/constitution/retriever.py:341`, but a subsequent successful `filter_domains` repopulates it at `moralstack/constitution/retriever.py:566`. Assert emptiness immediately after mutation or inside the fake before return.

- The direct `_call_openai` test updates are correctly identified: `tests/test_runtime_pooling.py:38-60` will need `system_prompt=...`, and `tests/test_domain_prefilter_descriptions.py:25-31` must accept/capture `system_prompt` once `filter_domains` passes it.

## Risky assumptions

- "Byte-identical system prompt" makes the call cache-eligible, but does not guarantee a cache hit. OpenAI documents exact-prefix matching and static-first prompt structure, and caching requires prompts of at least 1024 tokens; under 1024 tokens `cached_tokens` is zero. See OpenAI prompt caching docs (lines 727, 732, 791-793 per Codex's citation).

- "Env override still wins" is true for resolver-mediated SDK/CLI paths: `resolve_constitution_max_parallel_agents` uses explicit value first, then env fallback at `moralstack/pipeline/deliberation_stack.py:60-64`, and `build_deliberation_modules` passes the resolved value into the store at `moralstack/pipeline/deliberation_stack.py:95-105`. Direct `ConstitutionStore()` construction still uses constructor/dataclass defaults at `moralstack/constitution/store.py:462` and `moralstack/constitution/store.py:498`.

- The plan assumes no persisted-shape consumers break. Persistence forwards exactly `prompt` and `system_prompt` into `persist_llm_call` at `moralstack/constitution/retriever.py:91-105`; the existing persistence tests only assert cycle/sequence/retrieval phase/token usage at `tests/test_constitution_retrieval_persistence.py:24-39` and `tests/test_constitution_retrieval_persistence.py:69-90`.

## Architecture concerns

- The critical invariants look intact if the implementation stays scoped. Final action remains structured policy output, not prompt text: `.claude/rules/decision-policy.md:12-20` and `moralstack/runtime/decision/safe_complete_policy.py:158-285`.

- Hard-signal supremacy is structurally independent of retrieval output: `is_hard_signal_refuse` consumes decision/risk signals at `moralstack/orchestration/path_router.py:42-66`, and the invariant test locks hard-signal routing at `tests/governance_invariants/test_hard_signal_not_overridable_by_retrieval_wave.py:59-77`.

- `core` remains retrieval-only if the plan preserves current filtering: `DomainPrefilter.ALWAYS_EVALUATE = {"core"}` at `moralstack/constitution/retriever.py:279`, `domains_to_check` excludes it at `moralstack/constitution/retriever.py:447`, and runtime normalization rejects `"core"` at `moralstack/orchestration/controller.py:130-143`.

- Prompt transparency is not directly touched because the changed prompt is an internal classifier prompt, while developer-system byte equality is enforced through `effective_system_for_request` at `moralstack/orchestration/system_prompt_resolver.py:26-77` and tests at `tests/test_system_prompt_byte_equality.py:36-100`.

## Security/performance concerns

- Raising default concurrency from 2 to 4 increases burst pressure on the provider. Because per-agent failures are swallowed into empty retrieval results at `moralstack/constitution/retriever.py:1479-1483` and `moralstack/constitution/retriever.py:1514-1516`, this is a safety-relevant performance change.

- Prompt caching does not add a new provider disclosure path because the same domain text is already sent in the user prompt at `moralstack/constitution/retriever.py:494-555`, but custom/proprietary domain descriptions will now become the stable cached prefix. Operators may care about cache retention behavior.

## Suggested plan changes

- Add `moralstack/cli/shell.py` to the file list and update the help text from fallback `2` to `4`.

- Rephrase "caching engages" to "cache-eligible when prompt length and routing conditions are met," and add optional verification around `usage.prompt_tokens_details.cached_tokens` if measuring the optimization.

- Expand the concurrency risk section to cover prefilter-disabled and legacy retrieval paths, and acknowledge provider errors can change retrieved-principle sets.

- Put `_build_prefilter_system_prompt` at class scope and make tests call it only after the implementation exposes it there.

- Add tests/reruns for `tests/test_signals_mini_principle_free.py`, CLI help/defaults, and batch counts for no-prefilter and legacy paths.

## Questions for Claude/User

- Is the production default of 4 still approved for deployments that disable the prefilter or use legacy retrieval, where the wave can include all overlays?

- Should custom deployments set `MORALSTACK_CONSTITUTION_MAX_PARALLEL_AGENTS=2` until rate-limit behavior is observed?

- Do any domain descriptions contain proprietary or regulated policy text where prompt-cache retention should be configured explicitly?
