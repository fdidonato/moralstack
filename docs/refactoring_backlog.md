# Refactoring Backlog — MoralStack

> Generated: 2026-02-24 · Branch: `massive-refactoring`
>
> Prioritized by **impact × ease** (high-impact + low-risk first).

---

## Legend

| Column       | Meaning                                                                                                      |
|--------------|--------------------------------------------------------------------------------------------------------------|
| **Priority** | P0 = do first … P3 = nice-to-have                                                                            |
| **Smell**    | Long Method, God Class, Duplication, Data Clump, Dead Code, Language Violation                               |
| **Risk**     | 🟢 LOW (no safety logic touched) · 🟡 MEDIUM (touches flow but well-tested) · 🔴 HIGH (safety-critical path) |
| **Tests**    | Existing test coverage for the area                                                                          |

---

## P0 — High Impact, Low Risk

### 1. Config Loader Duplication (6 files × 4 identical functions)

|               |                                                                                                                                                                                                                                                                                                               |
|---------------|---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| **Files**     | `runtime/modules/simulator_config_loader.py` (L25-76), `runtime/modules/critic_config_loader.py` (L24-74), `runtime/modules/hindsight_config_loader.py` (L29-79), `runtime/modules/perspective_config_loader.py` (L28-78), `models/risk/config_loader.py` (L34-84), `orchestration/config_loader.py` (L38-88) |
| **Smell**     | **Duplication** — `get_*_env_float`, `get_*_env_int`, `get_*_env_str`, `get_*_env_bool` are byte-identical across all 6 files (only the function-name prefix differs). ~240 LOC of pure copy-paste.                                                                                                           |
| **Transform** | **Extract Module** → Create `moralstack/utils/env_helpers.py` with generic `get_env_float(key, default, min_val, max_val)`, `get_env_int(…)`, `get_env_str(…)`, `get_env_bool(…)`. Each config loader imports from there.                                                                                     |
| **Tests**     | `test_simulator_config_loader.py` (149), `test_critic_config_loader.py` (147), `test_hindsight_config_loader.py` (173), `test_perspective_config_loader.py` (153), `test_risk_config_loader.py` (130), `test_orchestrator_config_loader.py` (211) — all cover the `load_*_config_from_env()` end-to-end.      |
| **Risk**      | 🟢 LOW — Pure utility functions, no decision logic. All 6 loaders have dedicated tests.                                                                                                                                                                                                                       |

---

### 2. Dead Code: `_detect_domain_keyword` (never called)

|               |                                                                                                                                                                                        |
|---------------|----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| **File**      | `constitution/store.py` → `_detect_domain_keyword()` (L188-244)                                                                                                                        |
| **Smell**     | **Dead Code** — The function contains hardcoded Italian/English keyword lists but `_detect_domain()` (L247) uses **only** the LLM path and never calls `_detect_domain_keyword`.       |
| **Transform** | **Remove Function** — Delete `_detect_domain_keyword` and its associated keyword sets. Also removes a Language-Agnostic invariant violation (hardcoded Italian words in runtime code). |
| **Tests**     | `test_constitution_validation.py` (93) — does not call `_detect_domain_keyword` directly.                                                                                              |
| **Risk**      | 🟢 LOW — Unreachable code; removal cannot change behavior.                                                                                                                             |

---

### 3. OpenAI Config Data Clump (4 classes)

|               |                                                                                                                                                                                            |
|---------------|--------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| **Files**     | `constitution/store.py` → `ConstitutionStore.__init__` (L1344-1360), `DomainPrefilter.__init__` (L623-632), `EnhancedDomainAgent.__init__` (L869-903), `DomainAgent.__init__` (L1112-1145) |
| **Smell**     | **Data Clump** — The triple `(use_openai, openai_api_key, openai_model)` is passed together through 4 constructors and stored as 3 separate fields in each class.                          |
| **Transform** | **Introduce Dataclass** → `@dataclass class OpenAIClientConfig: use_openai: bool; api_key: str                                                                                             | None; model: str`. Pass a single config object instead of 3 params. |
| **Tests**     | `test_constitution_validation.py` covers store creation.                                                                                                                                   |
| **Risk**      | 🟢 LOW — Internal refactor, no behavior change.                                                                                                                                            |

---

### 4. `_call_openai` Duplication (3 classes)

|               |                                                                                                                                                                                                 |
|---------------|-------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| **File**      | `constitution/retriever.py` → `DomainPrefilter._call_openai`, `EnhancedDomainAgent._call_openai`, `DomainAgent._call_openai`.                                                                   |
| **Smell**     | **Duplication** — Three near-identical implementations of OpenAI call + JSON parse + cost tracking. `_call_local_llm` variants removed (OpenAI-only).                                           |
| **Transform** | **Extract Class** → Create a `ConstitutionLLMClient` (or helper module) with `call(prompt) -> dict` that handles OpenAI call, JSON extraction, and cost tracking. All 3 classes delegate to it. |
| **Tests**     | Tests cover the higher-level `get_relevant_principles` and `filter_domains`; the internal call methods are not directly tested.                                                                 |
| **Risk**      | 🟡 MEDIUM — Multiple callers; ensure JSON extraction semantics remain identical.                                                                                                                |

---

### 5. ~~`_format_principles` Duplication (3 locations)~~ ✅ DONE

|               |                                                                                                                                                                                    |
|---------------|------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| **File**      | `constitution/store.py` → `DomainAgent._format_principles` (L1309), `EnhancedDomainAgent._format_principles` (L1088), `ConstitutionStore._format_principles_for_matching` (L2279)  |
| **Smell**     | **Duplication** — Three methods that format `list[dict]` of principles into prompt-ready text, with minor formatting variations.                                                   |
| **Transform** | **Extract Function** → `format_principles_for_prompt(principles, include_level, style, max_rule_len)` in `prompt_formatter.py`. Replaced 3 call sites; removed duplicated methods. |
| **Tests**     | `test_prompt_formatter.py` — added 6 tests for compact/verbose styles, truncation, empty, include_level.                                                                           |
| **Risk**      | 🟢 LOW — Pure formatting, no decision logic.                                                                                                                                       |

---

### 6. `ModuleLoader._load_real_modules` — Repeated Load Pattern

|               |                                                                                                                                                                                                        |
|---------------|--------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| **File**      | `cli/run.py` → `ModuleLoader._load_real_modules()` (L839-1002)                                                                                                                                         |
| **Smell**     | **Long Method / Duplication** — 165 lines with 4× near-identical blocks: import config_loader, read env model, create policy, create module, handle exception with mock fallback.                      |
| **Transform** | **Extract Method** → Factor out `_load_optional_module(name, config_loader_path, module_class, mock_class, api_key)` that encapsulates the try/except/fallback pattern. Reduces to ~4 one-liner calls. |
| **Tests**     | `test_mstack_cli.py` (412) covers CLI initialization.                                                                                                                                                  |
| **Risk**      | 🟢 LOW — CLI layer, not safety-critical.                                                                                                                                                               |

---

## P1 — High Impact, Medium Risk

### 7. `MoralStackCLI._process_prompt` — Monster Method (~280 LOC)

|               |                                                                                                                                                                                           |
|---------------|-------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| **File**      | `cli/run.py` → `MoralStackCLI._process_prompt()` (L2090-2372)                                                                                                                             |
| **Smell**     | **Long Method** — Mixes: persistence setup, domain detection, principle retrieval, verbose debug output, orchestrator call, trace update, result display, cost tracking.                  |
| **Transform** | **Extract Method** (multiple): `_setup_run_context()`, `_display_relevant_principles(prompt)`, `_call_orchestrator(prompt)`, `_display_result(result, elapsed)`, `_update_trace(result)`. |
| **Tests**     | `test_mstack_cli.py` (412).                                                                                                                                                               |
| **Risk**      | 🟡 MEDIUM — Complex method but in CLI layer; no safety logic. Risk is in accidentally breaking verbose output flow.                                                                       |

---

### 8. `MoralStackCLI._build_trace_from_calls` — Massive Switch (~320 LOC)

|               |                                                                                                                                                                                                                                                                                             |
|---------------|---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| **File**      | `cli/run.py` → `MoralStackCLI._build_trace_from_calls()` (L2400-2722)                                                                                                                                                                                                                       |
| **Smell**     | **Long Method / Switch Statements** — A giant if/elif chain parsing string responses from different modules (`risk_estimator`, `policy`, `critic`, `simulator`, `hindsight`, `perspectives`). Each branch does fragile string splitting (`"Risk:".split(...)`, `"Violations:".split(...)`). |
| **Transform** | **Replace Conditional with Polymorphism** or **Extract Method** → Create per-module trace parsers: `_parse_risk_trace(call)`, `_parse_critic_trace(call)`, etc. Consider a `TraceParser` protocol with module-specific implementations.                                                     |
| **Tests**     | `test_mstack_cli.py` tests end-to-end but not trace building specifically.                                                                                                                                                                                                                  |
| **Risk**      | 🟡 MEDIUM — String parsing is brittle; extracting methods improves testability.                                                                                                                                                                                                             |

---

### 9. `ConstitutionStore` — God Class (~1000 LOC in class body)

|               |                                                                                                                                                                                                                                                                       |
|---------------|-----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| **File**      | `constitution/store.py` → `ConstitutionStore` (L1325-2410)                                                                                                                                                                                                            |
| **Smell**     | **God Class** — Combines: YAML loading, overlay management, domain detection, agent creation/orchestration, parallel execution, LLM semantic matching, caching, conflict resolution, debug info. 14-param constructor.                                                |
| **Transform** | **Extract Class** (staged): (a) Extract `ConstitutionRetriever` for `get_relevant_principles` + agent management + parallel execution. (b) Extract LLM matching to `ConstitutionLLMClient` (see item 4). (c) Keep `ConstitutionStore` as facade for load/get/resolve. |
| **Tests**     | `test_constitution_validation.py` (93) — moderate coverage.                                                                                                                                                                                                           |
| **Risk**      | 🟡 MEDIUM — Store is used across the system but behind a stable API (`get_constitution`, `get_relevant_principles`). Extract internal mechanics while keeping the public API unchanged.                                                                               |

---

### 10. `ConstitutionStore.__init__` — Long Parameter List (14 params)

|               |                                                                                                                                                                                                                                                                                                          |
|---------------|----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| **File**      | `constitution/store.py` → `ConstitutionStore.__init__` (L1344-1360)                                                                                                                                                                                                                                      |
| **Smell**     | **Long Parameter List** — 14 parameters including `config_dir`, `core_file`, `overlays_dir`, `policy_llm`, `use_llm_matching`, `use_openai`, `openai_api_key`, `openai_model`, `max_parallel_agents`, `use_enhanced_retrieval`, `confidence_threshold`, `use_domain_prefilter`, `max_prefilter_domains`. |
| **Transform** | **Introduce Parameter Object** → Group into `ConstitutionStoreConfig` dataclass. Combine with item 3 (`OpenAIClientConfig`).                                                                                                                                                                             |
| **Tests**     | `test_constitution_validation.py`.                                                                                                                                                                                                                                                                       |
| **Risk**      | 🟡 MEDIUM — Requires updating all call sites (CLI, orchestrator, tests).                                                                                                                                                                                                                                 |

---

### 11. Stopwords Duplication in `constitution/store.py` — DONE

|               |                                                                                                                                                                          |
|---------------|--------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| **File**      | `constitution/helpers.py` → `tokenize()` and `_STOPWORDS`; `constitution/store.py` → `_extract_keywords_from_description()`                                              |
| **Smell**     | ~~Duplication / Language Violation~~ — Resolved: single `_STOPWORDS` in helpers.py (English-only), used by both `tokenize()` and `_extract_keywords_from_description()`. |
| **Transform** | ~~Consolidate~~ — Done. Single `_STOPWORDS` constant in `helpers.py`; store imports and uses it.                                                                         |
| **Tests**     | `tests/test_constitution_retrieval.py` — tokenize, _extract_keywords_from_description, _compute_relevance, retrieval behavioral.                                         |
| **Risk**      | 🟡 MEDIUM — Addressed with behavioral regression tests.                                                                                                                  |

---

## P2 — Medium Impact, Higher Risk

### 12. `OrchestrationController.process` — Monster Method (~450 LOC)

|               |                                                                                                                                                                                                                                                         |
|---------------|---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| **File**      | `orchestration/controller.py` → `OrchestrationController.process()` (L229-680)                                                                                                                                                                          |
| **Smell**     | **Long Method** — A single method doing: persistence setup, risk estimation, overlay sensitivity check, decision routing (REFUSE/benign/SAFE_COMPLETE/fast/deliberative), trace management, result assembly, cycles-exhausted fallback, error handling. |
| **Transform** | **Extract Method** (staged): `_route_refuse(...)`, `_route_benign(...)`, `_route_safe_complete(...)`, `_route_fast_path(...)`, `_route_deliberative(...)`. Keep `process()` as a thin dispatcher.                                                       |
| **Tests**     | `test_orchestrator.py` (1044) — extensive coverage.                                                                                                                                                                                                     |
| **Risk**      | 🔴 HIGH — This is **safety-critical** routing logic. Each extraction must preserve exact branching semantics. Strong test suite mitigates risk.                                                                                                         |

---

### 13. `DeliberationRunner.__init__` — Long Parameter List (11 params)

|               |                                                                                                                                                                                               |
|---------------|-----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| **File**      | `orchestration/deliberation_runner.py` → `DeliberationRunner.__init__` (L53-66)                                                                                                               |
| **Smell**     | **Long Parameter List** — 11 dependencies injected via constructor.                                                                                                                           |
| **Transform** | **Introduce Parameter Object** → Create `DeliberationDependencies` dataclass grouping `policy`, `critic`, `simulator`, `hindsight`, `perspectives`, `constitution_store`, `output_protector`. |
| **Tests**     | `test_orchestrator.py` covers deliberation paths end-to-end.                                                                                                                                  |
| **Risk**      | 🟡 MEDIUM — Constructor change requires updating `Orchestrator.__init__`.                                                                                                                     |

---

### 14. `DeliberationRunner._deliberation_cycle` — Complexity

|               |                                                                                                                                                                                                                             |
|---------------|-----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| **File**      | `orchestration/deliberation_runner.py` → `_deliberation_cycle()` (L499-602)                                                                                                                                                 |
| **Smell**     | **Long Method / Feature Envy** — 100 lines mixing generation, context building, module dispatch, override application, hindsight gating, decision determination, logging. Heavy use of `self.config.*` and `self.logger.*`. |
| **Transform** | **Extract Method** → `_build_delib_context(state, request, risk_estimation)`, `_apply_hindsight_if_needed(state, request, delib_context)`, `_finalize_cycle(state)`.                                                        |
| **Tests**     | `test_orchestrator.py` (1044).                                                                                                                                                                                              |
| **Risk**      | 🔴 HIGH — Core deliberation loop; must preserve exact cycle semantics.                                                                                                                                                      |

---

### 15. `decision_service.py` → `_handle_informational_recovery` — Long Parameter List

|               |                                                                                                                                                                                                                                                                     |
|---------------|---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| **File**      | `orchestration/decision_service.py` → `_handle_informational_recovery()` (L529-580)                                                                                                                                                                                 |
| **Smell**     | **Long Parameter List** — 10 parameters: `inputs`, `trace`, `risk_category`, `risk_score`, `intent_operational`, `domain_regulated`, `domain`, `intent_type_val`, `has_ambiguity_or_dual_use`, `pre_final_action`.                                                  |
| **Transform** | **Introduce Parameter Object** → Group `risk_category`, `risk_score`, `intent_operational`, `domain_regulated`, `domain`, `intent_type_val`, `has_ambiguity_or_dual_use` into a `PolicyContext` dataclass (partially exists as `_build_policy_context_pre` output). |
| **Tests**     | `test_decide_action.py` (385).                                                                                                                                                                                                                                      |
| **Risk**      | 🟡 MEDIUM — Decision logic function; well-tested.                                                                                                                                                                                                                   |

---

## P3 — Lower Priority / Nice-to-Have

### 16. `cli/run.py` — File-level God Module (2600 LOC, 14 classes)

|               |                                                                                                                                                                                                                                                                                                                    |
|---------------|--------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| **File**      | `cli/run.py` (2600 lines)                                                                                                                                                                                                                                                                                          |
| **Smell**     | **God Module** — Contains 14 classes: `PhaseType`, `DecisionReason`, `PhaseResult`, `DraftRevision`, `DeliberationTrace`, `DeliberationVisualizer`, `CLIConfig`, Mock classes (6), `ModuleLoader`, `CallLogger`, `MarkdownReportGenerator`, `MoralStackCLI`.                                                       |
| **Transform** | **Split Module** → Move to `moralstack/cli/` package: `cli/models.py` (PhaseType, DecisionReason, PhaseResult, etc.), `cli/mocks.py` (Mock*), `cli/visualizer.py` (DeliberationVisualizer), `cli/report.py` (MarkdownReportGenerator, CallLogger), `cli/loader.py` (ModuleLoader), `cli/shell.py` (MoralStackCLI). |
| **Tests**     | `test_mstack_cli.py` (412).                                                                                                                                                                                                                                                                                        |
| **Risk**      | 🟢 LOW — CLI-only; no runtime impact. Large change surface but safe.                                                                                                                                                                                                                                               |

---

### 17. `constitution/store.py` — File-level God Module (2255 LOC, 5 classes)

|               |                                                                                                                                                                                                                                             |
|---------------|---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| **File**      | `constitution/store.py` (2255 lines)                                                                                                                                                                                                        |
| **Smell**     | **God Module** — Contains: `AgentResult`, `DomainPrefilter`, `EnhancedDomainAgent`, `DomainAgent`, `ConstitutionStore` plus 10+ module-level helper functions.                                                                              |
| **Transform** | **Split Module** → `constitution/store.py` (ConstitutionStore facade only), `constitution/retrieval.py` (agents, prefilter, retrieval logic), `constitution/relevance.py` (tokenize, expand_query, compute_relevance, specificity helpers). |
| **Tests**     | `test_constitution_validation.py` (93). Need more tests before splitting.                                                                                                                                                                   |
| **Risk**      | 🟡 MEDIUM — Widely imported; must keep backward-compatible re-exports.                                                                                                                                                                      |

---

### 18. `cli/run.py` → `MarkdownReportGenerator` — Large Class (~740 LOC)

|               |                                                                                                                                                                                         |
|---------------|-----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| **File**      | `cli/run.py` → `MarkdownReportGenerator` (L1103-1878)                                                                                                                                   |
| **Smell**     | **Large Class** — 775 lines, 20+ methods for generating markdown reports. Single class doing header, summary, journey map, phase details, metrics, revision history, call logs, footer. |
| **Transform** | **Extract Class** → Split into `ReportSections` (header, summary, journey, metrics) and `ReportRenderer` (orchestrates sections into full report). Move to `cli/report.py`.             |
| **Tests**     | No dedicated tests.                                                                                                                                                                     |
| **Risk**      | 🟢 LOW — Report generation only; no safety impact.                                                                                                                                      |

---

### 19. `constitution/helpers.py` → `tokenize` Stopwords Were Language-Specific — DONE

|               |                                                                                                                                                      |
|---------------|------------------------------------------------------------------------------------------------------------------------------------------------------|
| **File**      | `constitution/helpers.py` → `tokenize()`                                                                                                             |
| **Smell**     | ~~Language Violation~~ — Resolved: Italian stopwords removed; accent normalization via `unicodedata.normalize('NFD')` (language-agnostic).           |
| **Transform** | ~~Replace with language-neutral tokenization~~ — Done. Uses `_STOPWORDS` (English-only) and Unicode NFD for accents.                                 |
| **Tests**     | `tests/test_constitution_retrieval.py` — tokenize tests cover accent normalization, no Italian stopwords, stability.                                 |
| **Risk**      | 🟡 MEDIUM — Addressed with behavioral regression tests. Optional follow-up: add `language` param to retrieval for configurable stopwords per locale. |

---

### 20. `constitution/store.py` → `_expand_query` Contains Hardcoded Synonyms

|               |                                                                                                                   |
|---------------|-------------------------------------------------------------------------------------------------------------------|
| **File**      | `constitution/store.py` → `_expand_query()` (L401-448)                                                            |
| **Smell**     | **Language Violation** — Likely contains hardcoded English synonym maps. Should be configurable or LLM-delegated. |
| **Transform** | **Move to config** or **Remove** if LLM-based retrieval supersedes keyword expansion.                             |
| **Tests**     | No direct tests.                                                                                                  |
| **Risk**      | 🟡 MEDIUM — Affects keyword-based principle matching.                                                             |

---

## Suggested Attack Order

```
Phase 1: "Quick wins" (P0, items 1-6)
  ├── #1  Config loader dedup         → 1 new file, 6 updated
  ├── #2  Remove dead code            → 1 file
  ├── #3  OpenAI config dataclass     → 1 new dataclass, 4 updated ctors
  ├── #5  Format principles dedup     → 1 updated file
  └── #6  ModuleLoader extract        → 1 file

Phase 2: "Store cleanup" (P0-P1, items 4, 9, 10, 11)
  ├── #4  LLM client extraction       → 1 new class, 3 updated
  ├── #10 ConstitutionStoreConfig     → 1 new dataclass
  ├── #9  Extract ConstitutionRetriever → 1 new class
  └── #11 Stopwords consolidation     → DONE (helpers + store)

Phase 3: "CLI decomposition" (P1, items 7, 8, 16, 18)
  ├── #7  _process_prompt split       → extract 4-5 methods
  ├── #8  _build_trace_from_calls     → extract 6 parsers
  ├── #16 Split cli/run.py into package
  └── #18 MarkdownReportGenerator split

Phase 4: "Safety-critical refactors" (P2, items 12-15)
  ├── #12 Controller.process split    → 5 route methods (CAREFUL)
  ├── #14 _deliberation_cycle split   → 3 methods (CAREFUL)
  ├── #13 DeliberationDependencies    → 1 dataclass
  └── #15 PolicyContext dataclass     → 1 dataclass
```

---

## Summary Statistics

| Metric                   | Value                          |
|--------------------------|--------------------------------|
| Total items              | 20                             |
| P0 (do first)            | 6                              |
| P1 (next batch)          | 5                              |
| P2 (careful)             | 4                              |
| P3 (nice-to-have)        | 5                              |
| 🟢 LOW risk              | 9                              |
| 🟡 MEDIUM risk           | 9                              |
| 🔴 HIGH risk             | 2                              |
| Estimated duplicated LOC | ~600+                          |
| Estimated removable LOC  | ~200+ (dead code, duplication) |
