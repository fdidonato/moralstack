# Refactoring Diary

Log of refactoring sessions: baseline state and per-change decisions. Used to keep refactors reversible and auditable.

## Baseline

Fill this section at the start of the first refactoring session (or when resetting the baseline).

| Field      | Value                                    |
|------------|------------------------------------------|
| **Date**   | 2025-02-24                               |
| **Branch** | massive-refactoring                      |
| **Commit** | 97890e70d3d33a240ded7f75fc931ee6370f8b84 |

### Commands and expected outcome

| Command                 | Purpose                                                            | Result                                                    |
|-------------------------|--------------------------------------------------------------------|-----------------------------------------------------------|
| `pytest -q`             | Run tests (or `pytest --maxfail=1 --disable-warnings -q` as in CI) | 576 passed, 3 warnings                                    |
| `ruff check .`          | Lint                                                               | 1142 errors (126 fixable with `--fix`)                    |
| `ruff format --check .` | Format check (CI-style)                                            | 80 files would be reformatted, 42 already formatted       |
| `black --check .`       | Black format check (CI-style)                                      | 83 files would be reformatted, 42 would be left unchanged |
| `mypy moralstack`       | Type check (gradual; package only)                                 | 100 errors in 12 files (84 source files checked)          |

**Baseline failure notes (pre-existing; do not attribute to refactoring):**

- **Tests:** All 576 tests passed. Three PytestCollectionWarnings (test classes with `__init__`: `TestRunner` in `test_perspective_standalone.py`, `TestTask`/`TestResult` in `test_risk_estimator.py`).
- **Ruff check:** 1142 lint errors at baseline; 126 fixable with `--fix`. Run `ruff check .` for full list.
- **Ruff format:** 80 files would be reformatted, 42 already formatted; run `ruff format --check .` for list.
- **Black check:** 83 files would be reformatted, 42 would be left unchanged; run `black --check .` for full list.

**Ruff check — conteggio per categoria (`ruff check . --statistics`):**

| Categoria | Count | Regola | Descrizione                      |
|-----------|-------|--------|----------------------------------|
| E         | 950   | E501   | line-too-long                    |
| E         | 44    | E402   | module-import-not-at-top-of-file |
| E         | 13    | E722   | bare-except                      |
| E         | 1     | E741   | ambiguous-variable-name          |
| F         | 29    | F541   | f-string-missing-placeholders    |
| F         | 19    | F401   | unused-import                    |
| F         | 7     | F841   | unused-variable                  |
| F         | 1     | F601   | multi-value-repeated-key-literal |
| I         | 78    | I001   | unsorted-imports                 |

**Ruff check — top 10 file più problematici:**

| #  | File                                            | Errori |
|----|-------------------------------------------------|--------|
| 1  | scripts/benchmark_moralstack.py                 | 308    |
| 2  | moralstack/orchestration/deliberation_runner.py | 119    |
| 3  | moralstack/cli/run.py                           | 113    |
| 4  | moralstack/orchestration/controller.py          | 58     |
| 5  | moralstack/constitution/store.py                | 47     |
| 6  | tests/test_orchestrator.py                      | 29     |
| 7  | tests/test_perspective_module.py                | 26     |
| 8  | tests/test_perspective_standalone.py            | 24     |
| 9  | moralstack/ui/app.py                            | 22     |
| 10 | tests/test_risk_estimator.py                    | 22     |

| 10 | tests/test_risk_estimator.py                    | 22     |

**mypy baseline (2025-02-24):**

Command: `mypy moralstack` (from repo root). Target: package `moralstack` only (tests/ and scripts/ excluded).

| Metric               | Value |
|----------------------|-------|
| Total errors         | 100   |
| Files with errors    | 12    |
| Source files checked | 84    |

**mypy — count by error category:**

| Category         | Code             | Count | Description                                           |
|------------------|------------------|-------|-------------------------------------------------------|
| assignment       | assignment       | 18    | Incompatible types in assignment                      |
| attr-defined     | attr-defined     | 21    | Object has no attribute                               |
| arg-type         | arg-type         | 14    | Argument has incompatible type (e.g. str vs Literal)  |
| return-value     | return-value     | 10    | Incompatible return value type                        |
| misc             | misc             | 17    | Cannot assign to type, object not iterable, etc.      |
| has-type         | has-type         | 10    | Cannot determine type of variable                     |
| var-annotated    | var-annotated    | 5     | Need type annotation for variable                     |
| import-not-found | import-not-found | 7     | Missing stubs (datasets, fastapi, uvicorn, starlette) |
| typeddict-item   | typeddict-item   | 2     | Missing keys for TypedDict                            |
| union-attr       | union-attr       | 1     | Item "None" has no attribute                          |
| call-arg         | call-arg         | 1     | Unexpected keyword argument                           |

**mypy — top 10 files by error count:**

| #  | File                                            | Errors |
|----|-------------------------------------------------|--------|
| 1  | moralstack/cli/run.py                           | 32     |
| 2  | moralstack/orchestration/decision_service.py    | 28     |
| 3  | moralstack/constitution/store.py                | 13     |
| 4  | moralstack/orchestration/deliberation_runner.py | 6      |
| 5  | moralstack/ui/app.py                            | 7      |
| 6  | moralstack/data/builders/sft_builder.py         | 5      |
| 7  | moralstack/runtime/trace/decision_trace.py      | 4      |
| 8  | moralstack/orchestration/controller.py          | 2      |
| 9  | moralstack/runtime/modules/simulator_module.py  | 2      |
| 10 | moralstack/runtime/modules/hindsight_module.py  | 2      |

Note: Seven `import-not-found` errors are for optional dependencies (datasets in sft_builder; fastapi, uvicorn, starlette in ui/app). They can be silenced by installing optional extras or adding per-module overrides.

**Interfacce candidate a Protocol/typing (solo analisi):**

The following interfaces are good candidates for Protocol or improved typing in a later step. No code changes in this commit.

1. **LLM / OpenAI client**
   - **Files:** `moralstack/constitution/store.py`, `moralstack/models/policy.py`
   - **Issue:** Concrete `openai.OpenAI()` and `client.chat.completions.create` usage; no abstraction. Refactoring and mocking are harder.
   - **Candidate:** A Protocol (e.g. `LLMClient`) with a method such as `create_chat_completion(...)` and typed parameters/return, to be adopted in store and policy later.

2. **Persistence / DB**
   - **Files:** `moralstack/persistence/db.py`, `moralstack/persistence/sink.py`
   - **Issue:** Direct use of `sqlite3` and `_get_connection(path)`; no typed abstraction for connection or sink.
   - **Candidate:** A Protocol for the connection (execute/commit/context manager) and/or for the sink (e.g. `persist_llm_call`, `persist_request`), to be defined in a later step.

**Black check — baseline (2025-02-24):**

| Metric                         | Value |
|--------------------------------|-------|
| Files with violations          | 83    |
| Files already compliant        | 42    |

Sample of files needing reformat (first 10 from `black --check .` output):

| #  | File                                        |
|----|---------------------------------------------|
| 1  | moralstack/__init__.py                      |
| 2  | moralstack/data/builders/__init__.py        |
| 3  | moralstack/models/base.py                   |
| 4  | moralstack/constitution/__init__.py         |
| 5  | moralstack/constitution/loader.py           |
| 6  | moralstack/models/risk/categories.py        |
| 7  | moralstack/constitution/prompt_formatter.py |
| 8  | moralstack/models/risk/utils.py             |
| 9  | moralstack/orchestration/__init__.py        |
| 10 | moralstack/models/risk/schema.py            |

---

## Decision log

One row or block per refactoring step. Add rows as you go.

| Date       | What (scope/files)                     | Why                                                                   | Risk | Tests run                      | Commit (hash or message)                  |
|------------|----------------------------------------|-----------------------------------------------------------------------|------|--------------------------------|-------------------------------------------|
| 2025-02-24 | Add Black config + check (no reformat) | Standardize formatting with Black, prepare CI                         | low  | `pytest -q`, `black --check .` | `chore: add black config (check-only)`    |
| 2025-02-24 | Add mypy (gradual) configuration       | Introduce gradual type checking; baseline errors; Protocol candidates | low  | `pytest -q`, `mypy moralstack` | `chore: add mypy (gradual) configuration` |

#### Entry: 2025-02-24 — Black config (check-only)

- **What:** `pyproject.toml` (dev deps + `[tool.black]`), CI step, `docs/refactoring_diary.md`, `docs/DEVELOPMENT.md`
- **Why:** Standardize formatting with Black; check executable in CI; no reformat in this step
- **Risk:** low
- **Tests run:** `pytest -q`, `black --check .`
- **Commit:** `chore: add black config (check-only)`

#### Entry: 2025-02-24 — mypy (gradual) configuration

- **What:** `pyproject.toml` (`[tool.mypy]` + overrides, mypy in dev deps), `docs/refactoring_diary.md` (mypy baseline + Protocol candidates), `docs/DEVELOPMENT.md` (mypy command)
- **Why:** Gradual type checking; runnable `mypy moralstack` without blocking; baseline and interface analysis documented
- **Risk:** low
- **Tests run:** `pytest -q`, `mypy moralstack`
- **Commit:** `chore: add mypy (gradual) configuration`

| 2026-02-24   | Add pre-commit hooks (ruff/black/whitespace) | Automate cheap checks before commit | low | `pre-commit run --all-files` | `chore: add pre-commit hooks (ruff/black)` |
| 2026-07-11   | mypy strict on `moralstack.server.*`; drop stale lenient overrides | Network surface fully type-checked; remove dead config | low | `mypy moralstack --ignore-missing-imports` (clean-cache), `pytest -q`, `pre-commit run -a` | `chore(typing): enable mypy strict on server package` |
| _YYYY-MM-DD_ | _e.g. rename X to Y in module Z_ | _e.g. clarity, consistency_ | low / medium / high | _e.g. pytest -q, tests/unit/test_z.py_ | _hash or `refactor: ... (no behavior change)`_ |

#### Entry: 2026-02-24 — Pre-commit hooks

- **What:** `.pre-commit-config.yaml` (new), `pyproject.toml` (pre-commit in dev deps), `docs/DEVELOPMENT.md` (pre-commit section), `docs/refactoring_diary.md`, `.cursor/rules/dependency-management.mdc`
- **Why:** Automate cheap checks (format, lint, whitespace) before every commit to prevent mechanical regressions
- **Risk:** low
- **Tests run:** `pre-commit run --all-files`
- **Commit:** `chore: add pre-commit hooks (ruff/black)`

**Hooks activated:**

| Hook                | Repo                               | Mode                           |
|---------------------|------------------------------------|--------------------------------|
| trailing-whitespace | pre-commit/pre-commit-hooks v6.0.0 | auto-fix                       |
| end-of-file-fixer   | pre-commit/pre-commit-hooks v6.0.0 | auto-fix                       |
| ruff-check          | astral-sh/ruff-pre-commit v0.15.2  | `--fix --exit-non-zero-on-fix` |
| black               | psf/black 26.1.0                   | auto-format                    |

**First run results (`pre-commit run --all-files`):**

| Hook                | Result               | Files fixed                                          |
|---------------------|----------------------|------------------------------------------------------|
| trailing-whitespace | Failed (fixed)       | 24 files                                             |
| end-of-file-fixer   | Failed (fixed)       | ~40 files (py, yaml, md)                             |
| ruff-check          | Failed               | ~1000 E501/E402/E722 remaining; I001/F401 auto-fixed |
| black               | Failed (reformatted) | 79 files reformatted, 46 unchanged                   |

**Notes:** First full-repo run fails due to pre-existing baseline issues (1142 ruff errors, 83 black violations). On incremental commits (staged files only), hooks are fast (<2s for ruff/black on typical changesets). The `--exit-non-zero-on-fix` flag on ruff ensures the developer sees and re-stages auto-fixed files.

#### Entry: 2026-07-11 — mypy strict on the server package

- **What:** `pyproject.toml` only — `[[tool.mypy.overrides]]` for `moralstack.server.*` changed from lenient (`ignore_missing_imports` + `untyped-decorator` disabled) to `strict = true` (same pattern as `moralstack.orchestration.*`); the `moralstack.ui.app` lenient override removed entirely (proven stale — zero errors without it).
- **Why:** The proxy is the network-facing surface; it should be at least as type-safe as the orchestration core. Both removed overrides were dead weight: the server package was already strict-clean, and `untyped-decorator` never fires at the default strictness level anyway.
- **Risk:** low (tooling config only, no runtime code touched). Strictness verified with a canary (temporary untyped def in `server/headers.py` → `no-untyped-def` fired, then reverted).
- **Tests run:** `mypy moralstack --ignore-missing-imports` clean-cache (0 errors, 174 files); standalone `mypy moralstack/server` without the flag also clean; full `pytest -q`; `pre-commit run -a`.
- **Note:** removing the *global* `--ignore-missing-imports` from CI/pre-commit is blocked by the undeclared PyYAML dependency (`models/risk/signals/registry.py:14` — see `docs/CODEBASE_FACTS.md`, Future work / known gaps). *Update 2026-07-13: unblocked by the ruamel migration below.*
- **Commit:** `chore(typing): enable mypy strict on server package`

#### Entry: 2026-07-13 — migrate signal registry from PyYAML to ruamel.yaml

- **What:** `moralstack/models/risk/signals/registry.py` — the single `import yaml` / `yaml.safe_load` in the codebase replaced with the already-declared `ruamel.yaml` (`YAML(typ="safe").load`). No other file imported PyYAML.
- **Why:** PyYAML was an **undeclared** runtime dependency (worked locally only because the dev toolchain pulls it transitively) — a clean `pip install moralstack` would likely fail at import of the risk estimator. Migrating (rather than declaring `pyyaml`) keeps one YAML library in the package, matches the design intent in `constitution/loader.py` ("only ruamel reads YAML"), and unblocks dropping the global `--ignore-missing-imports` mypy flag without adding `types-PyYAML`.
- **Risk:** low. ruamel `typ="safe"` speaks YAML 1.2 vs PyYAML's 1.1; `signals.yaml` was checked for 1.1-only tokens (unquoted `yes/no/on/off` booleans) — none present, so parsing is semantics-preserving. Registry loads the same 17 signals at import.
- **Tests run:** scoped signals/prefix/fast-path tests (57 passed); `mypy moralstack` **without** `--ignore-missing-imports` clean (174 files); full `pytest -q` + `pre-commit run -a` before commit.
- **Commit:** `fix(deps): migrate signal registry from pyyaml to ruamel.yaml`

#### Entry: 2026-07-13 — drop global --ignore-missing-imports from mypy invocations

- **What:** `.github/workflows/ci.yml` (Type Check step) and `.pre-commit-config.yaml` (local mypy hook) — both now run plain `mypy moralstack`. No pyproject change needed: the targeted per-module overrides (`ruamel.yaml.*`, `langdetect.*`, `fastembed`/`numpy`) already cover the third-party packages without stubs.
- **Why:** the global flag silently suppressed *every* missing-stub error, including for first-party imports — it is what masked the undeclared PyYAML dependency in the first place. With the ruamel migration landed, the blanket suppression is dead weight; stub gaps should be declared per-module, visibly, in `pyproject.toml`.
- **Risk:** low. Config-only; makes mypy strictly more sensitive, not less. Residual: the CI env (`pip install -e ".[dev,ui]"`) could differ from local on transitively installed packages — first CI run after push is the real proof.
- **Tests run:** `.mypy_cache` removed, `pre-commit run mypy -a` → Passed (clean-cache `mypy moralstack` with no flag); full `pre-commit run -a` before commit.
- **Commit:** `chore(typing): drop global ignore-missing-imports from mypy`

#### Entry: 2026-07-13 — mypy strict on the ui package

- **What:** `pyproject.toml` only — added `[[tool.mypy.overrides]]` `module = "moralstack.ui.*"` with `strict = true`. The package is just `__init__.py` + `app.py`, but the wildcard matches the established pattern (`server.*`, `orchestration.*`) and covers future ui modules automatically.
- **Why:** completes the strict rollout across the three user-facing packages; `ui/app.py` (~3300 lines) was the last major surface at default strictness after its stale lenient override was removed on 2026-07-11.
- **Risk:** none observed. `moralstack.ui.*` was **already strict-clean** (0 errors, 174 files); strictness proven active with a canary on the final wildcard config (temporary untyped def in `app.py` → `no-untyped-def` fired at app.py:3339, then reverted via `git checkout --`).
- **Tests run:** `mypy moralstack` clean; canary check; full `pytest -q` + `pre-commit run -a` before commit.
- **Commit:** `chore(typing): enable mypy strict on ui package`

### Template for a single entry (copy as needed)

#### Entry: 2026-05-21 — DCCL Commit 3 (compliance fast-path)

- **What:** `OrchestrationController._route_compliance_match`, SDK `GovernanceMetadata` DCCL fields, proxy compliance headers, markdown export DCCL section, tests `test_compliance_fast_path.py` / `test_sdk_dccl.py`, module docs.
- **Why:** When the Developer Contract Compliance Layer emits MATCH with a validated speculative draft, deployer-authorized rule execution must produce `NORMAL_COMPLETE` without running risk routing or deliberation (q51–q58, q74, q75 authentication samples). Per-module early-return was replaced by a single controller fast-path (functionally equivalent, less invasive).
- **Risk:** medium (safety-critical routing); guarded by `speculative_draft_validated`, failsafe try/except fallback, and unchanged pipeline for NO_MATCH / SAFETY_OVERRIDE / NO_CONTRACT.
- **Tests run:** `pytest tests/test_compliance_fast_path.py tests/test_sdk_dccl.py`; full suite `pytest tests/` (1644 passed).
- **Commit:** (pending user commit)

```markdown
#### Entry: YYYY-MM-DD — &lt;short title&gt;

- **What:** (files / scope)
- **Why:** (rationale)
- **Risk:** low | medium | high
- **Tests run:** (commands)
- **Commit:** (hash or message)
```

#### Entry: 2026-07-07 — AI harness hooks (context survival + verify dedup)

- **What:** `.claude/hooks/` — `stop_gate.py` (verify skipped on `stop_hook_active`
  or unchanged content-fingerprint via `.last-verified.json`; docs-gate nudge cap
  via `.nudge-count.json`; docs-update stub `.docs-stub.md`; emits context only on a
  fresh verify to avoid a re-wake loop); new `precompact_snapshot.py` (PreCompact,
  `async`) → `.context-snapshot.md`, reloaded by `session_start.py` on
  `source∈{compact,resume}`; new `session_end.py` → UNVERIFIED digest in
  `.claude/session-diary.md` (staging only, §4); new `user_prompt_submit.py`
  (keyword-gated context injection). Registered in `.claude/settings.json`. New
  `.claude/hooks/README.md`; `.gitignore` for the new markers + generated `ai/`
  artifacts. Tests in `tests/harness/`. Docs: `PROJECT_SPEC.md` §8,
  `.claude/rules/docs-maintenance.md`, `docs/ai/HARNESS_SESSION_LEARNINGS.md`.
- **Why:** Fase-1 reconstruction found redundant Stop-verify runs, docs-nudge repeated
  across chains, and silent loss of the in-flight plan on auto-compaction. All fixes are
  harness-only; no `moralstack/`/`tests/` product code touched.
- **Risk:** low. Every hook is fail-open (malformed/empty stdin → exit 0); no governance
  behavior changed → 84-question benchmark invariant by construction.
- **Tests run:** `pytest tests/harness/` (63 passed); full suite `python -m pytest -q`
  (2150 passed / 0 failed, 2026-07-07); `pre-commit run --files` on the changed set
  (clean; black reformatted `session_start.py`).
- **Commit:** (pending user commit)

---

_See `.cursor/rules/refactoring.md` for the refactoring constraints and workflow._
