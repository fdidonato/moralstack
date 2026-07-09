# Plan — Cached-token observability (prompt caching hit-rate measurable)

> Status: **v2, REVISED after adversarial review (BLOCK)**. Three blockers were raised and
> each was independently re-verified against the code before being accepted:
>
> - **B1 (accepted).** The four deliberative modules never hand a `GenerationResult` to the
>   persistence path: they copy only `tokens_used/prompt_tokens/completion_tokens/
>   token_usage_source` into their own report objects (`critic_module.py:505-521`,
>   `hindsight_module.py:799-809`, `simulator_module.py:445-455,679-695`,
>   `perspective_module.py:786-793`), and `deliberation_runner._token_usage_json_from_result`
>   (`:379-384`) then reads *those*. The v1 four-file scope would therefore have persisted a
>   cached-token signal for everything **except** critic/simulator/hindsight/perspectives —
>   precisely the modules the prompt-caching commit `00c9e3e` targeted. Worse, the failed-retry
>   rows (which do use the real `GenerationResult`, e.g. `perspective_module.py:786,810`) would
>   have carried it while the successful rows did not. Scope expanded to the result types.
> - **B2 (accepted).** `cached_input_tokens: int = 0` + "emit only when > 0" collapses
>   "provider measured 0 cache hits" into "provider reported nothing". For a hit-rate
>   experiment that distinction *is* the signal. Field becomes `int | None = None`.
>   The reviewer also disproved v1's stated reason for the conditional emission: no test
>   asserts the exact key set of a generated `token_usage_json`.
> - **B3 (accepted).** Verified in the venv (openai **2.36.0**, pin `pyproject.toml:28`):
>   `PromptTokensDetails.cached_tokens` is `Optional[int]`, and `prompt_tokens_details` is
>   `None` on a usage built without it — so `int(...)` on it raises. Also confirmed
>   empirically: `int(Mock())` raises `TypeError` while `int(MagicMock()) == 1` (silent
>   garbage). Extraction must be type-checked (`isinstance(raw, int)`, rejecting `bool` and
>   any Mock) and wrapped.

## 1. Goal

Make OpenAI prompt-caching hits **observable**. Today `cached_tokens` is dropped before
reaching any sink, so the benefit of the prompt-caching work (commit `00c9e3e`,
"feat(prompt): riordina prompt per caching openai") cannot be measured — only inferred
from latency.

Success criteria:
- For every LLM call persisted in `llm_calls`, `token_usage_json` carries the number of
  cached (already-prefilled) input tokens reported by the provider.
- A COMPL-AI replay can report cache hit-rate = `cached_input_tokens / input_tokens`
  per module, and the implied cost saving.
- **Zero behavior change**: no decision, no routing, no prompt, no delivered text moves.

Explicit non-goals: request-level totals table, cost computation in-engine.

**Scope extension (post-implementation, on user request):** surface the hit rate in the UI
everywhere per-module / per-model token metrics already appear — the shared per-model panel
(4 scopes), the per-module rollup, the per-call badge, and the Domain retrieval table.
While verifying this against the real replay DB, the dashboard printed **30.9%** where the
run printed **65.9%**: the rate was dividing by *total* input, including calls the provider
never reported on. Fixed by adding `cached_input_base` (input tokens of the reported calls
only) to the SQL aggregations and dividing by that everywhere. Re-verified live: dashboard
67.5% across all runs, 65.9% on the new run, matching an independent SQL check.

## 2. Current behavior (verified this session, cited)

Two — and only two — places convert a provider response into a `TokenUsage`:

| Entry point | Sites | Notes |
|---|---|---|
| `TokenUsage.from_openai_usage(response.usage)` | `models/policy.py:240`; `constitution/retriever.py:624,879,1056`; `orchestration/embedder.py:274` | reads `prompt_tokens`, `completion_tokens`, `total_tokens` only (`observability/token_usage.py:37-39`) |
| `TokenUsage.from_generation_result(result)` | `orchestration/controller.py:1014`; `orchestration/deliberation_runner.py:383`; `orchestration/safe_refusal_generator.py:552`; `runtime/modules/{critic,hindsight,perspective,simulator}_module.py`; `models/base.py:129` | reads `GenerationResult.prompt_tokens/completion_tokens/tokens_used` (`observability/token_usage.py:77-87`) |

`GenerationResult` (`models/base.py:107-129`) is built only in `models/policy.py:338,394`,
fed by `_complete()` which returns a 6-tuple
`(text, tokens_used, finish_reason, prompt_tokens, completion_tokens, source)`
(`models/policy.py:181,247`). `_complete` has exactly 2 call sites (`policy.py:328,384`).

Risk-estimator usage reaches the DB via `result.token_usage_json()`
(`models/risk/estimator.py:927`), i.e. through `from_generation_result`.

Persistence: `sinks/sqlite_sink.py:648-667` re-parses `token_usage_json` via
`TokenUsage.from_json` and denormalizes into `llm_calls.input_tokens/output_tokens/
total_tokens/token_usage_missing/token_usage_estimated`. Additive column migrations
already exist (`sqlite_sink.py:789-793`).

Fact: `prompt_tokens_details` / `cached_tokens` appear **nowhere** in `moralstack/**.py`
(grep, this session). The provider returns them; we discard them.

## 3. Design

**A. `TokenUsage` gains one field** (`observability/token_usage.py`):
```python
cached_input_tokens: int | None = None  # None = provider reported nothing; 0 = measured miss
```
- `from_openai_usage`: read `usage.prompt_tokens_details.cached_tokens` through a
  never-raising helper: `getattr` → `Mapping.get` fallback (OpenAI-compatible proxies) →
  strict `isinstance(int)` and not `bool` (rejects Mock/MagicMock) → reject negatives →
  clamp to `input_tokens`. Wrapped in `try/except` (invariant §5.6).
- `to_json`: emit `cached_input_tokens` when `is not None` (**including 0**). Payloads from
  providers that report nothing stay byte-identical to today.
- `from_json`: absent ⇒ `None` (legacy rows read as "unknown", not "zero").
- `combine`: sum the known values; all-unknown ⇒ `None`.

**B. Result types gain `cached_prompt_tokens: int | None = None`**, placed next to the
existing `completion_tokens` field, and each copy site propagates it:
- `GenerationResult` (`models/base.py`), read by `from_generation_result`.
- `CriticReport` (`critic_module.py`), `HindsightResult` (`hindsight_module.py`),
  `SimulationResult` (`simulator_module.py`), `PerspectiveResult` + `EnsembleResult`
  (`perspective_module.py`).
- Aggregation: perspectives use the existing `_sum_optional_token_field`
  (`perspective_module.py:354-362`, already None-aware); the simulator seeded path sums with
  the same `has_*` guard it uses for prompt/completion tokens (`simulator_module.py:560-567`).

All fields are trailing keyword defaults, so positional `TokenUsage(0,0,0,"missing")` and
every existing construction site remain valid.

**C. `_complete` returns a 7-tuple**; the 2 call sites unpack the extra value and pass it
to `GenerationResult`. `rewrite()` and `refuse()` delegate to `generate()` so they inherit it.

**D. Persistence**: `sqlite_sink` writes `cached_input_tokens` into a new **nullable**
`llm_calls` column via the existing additive-migration list (`sqlite_sink.py:783-795`, run on
every `init_db`). Touches `_LLM_CALLS_INSERT`, `_derive_llm_call_token_columns`, and the
migration list; the `llm_calls` CREATE TABLE deliberately carries no token columns, so it is
not edited. No backfill: old rows read NULL ⇒ "unknown".

**E. `read_store.get_token_usage_breakdown`** adds `SUM(cached_input_tokens)` so per-module
hit-rate is computable in-repo rather than only from ad-hoc SQL.

**Deliberately unchanged** (stated so a reviewer does not rediscover it): the request-level
accumulator (`request_token_accumulator.py:32-43`) and `request_token_usage` do not carry
cached tokens — `cached ⊆ input`, so the existing totals stay correct. The proxy's
client-facing `usage` dict (`proxy.py:468-472`) does not expose `prompt_tokens_details`.
Both are follow-ups, not part of this change.

## 4. Invariants touched (PROJECT_SPEC §5)

- **§5.6 Observability never breaks the request** — the whole change lives on the
  telemetry path. The `cached_tokens` extraction must not raise for any provider shape.
  Defensive `getattr` + a `_safe_int` clamp; no `try/except` needed if no attribute
  access can throw, but the extraction is wrapped anyway.
- No other invariant is in scope: no prompt text, no decision field, no routing input is
  read or written. `TokenUsage` is never consumed by decision logic (verified: its only
  consumers are sinks, `request_token_accumulator`, `read_store`).

## 5. Files to modify

1. `moralstack/observability/token_usage.py` — field, extraction helper, (de)serialization, combine.
2. `moralstack/models/base.py` — `GenerationResult.cached_prompt_tokens`.
3. `moralstack/models/policy.py` — `_complete` 7-tuple + 2 unpack sites + 2 `GenerationResult(...)`.
4. `moralstack/runtime/modules/critic_module.py` — `CriticReport` field + copy site.
5. `moralstack/runtime/modules/hindsight_module.py` — `HindsightResult` field + copy site.
6. `moralstack/runtime/modules/simulator_module.py` — `SimulationResult` field + `_build_result`
   param + batch copy site + seeded-path summation.
7. `moralstack/runtime/modules/perspective_module.py` — `PerspectiveResult` + `EnsembleResult`
   fields + copy site + both ensemble aggregations.
8. `moralstack/observability/sinks/sqlite_sink.py` — column + insert + derive + migration entry.
9. `moralstack/observability/read_store.py` — `SUM(cached_input_tokens)` in the breakdown.

## 6. Tests

- `tests/test_models_base_token_usage.py` — `cached_prompt_tokens` round-trips; absent ⇒ key omitted.
- new: `from_openai_usage` with (a) pydantic-like `prompt_tokens_details.cached_tokens`,
  (b) dict-shaped details, (c) absent details, (d) `cached > prompt_tokens` (clamp),
  (e) embeddings (`is_embedding=True` ⇒ 0).
- `from_json` on a legacy payload without the key ⇒ `cached_input_tokens == 0`.
- `combine` sums the field.
- sqlite: a call with cached tokens persists the column; a legacy row reads NULL.
- Full suite must stay green (`python -m pytest`), especially the byte-equality and
  observability-contract tests.

## 7. Verification

1. `python -m pytest` full suite green.
2. `pre-commit run -a` clean.
3. Re-run the COMPL-AI replay (30 examples, scratchpad scripts) against a proxy on HEAD;
   aggregate `cached_input_tokens / input_tokens` per module; report hit-rate and the
   implied cost delta at published OpenAI cached-input pricing.
4. **A 0% hit-rate is ambiguous and must not be reported as "caching does not work."**
   OpenAI caching engages only at ≥1024 prompt tokens and only on an exact, stable prefix;
   the three risk minis fire concurrently (`estimator.py:956-961`) and cannot hit a cache
   still being written. The replay report must therefore carry, per module: mean
   `input_tokens` (is the prompt even eligible?), the unknown-vs-zero split, and call
   concurrency — not just the ratio.

## 7b. Verification — actual outcome (2026-07-09)

- `python -m pytest`: **2244 passed**, 1 deselected. `pre-commit run -a`: all hooks pass
  (ruff, black, mypy, changelog guard, memory guard).
- COMPL-AI replay (30 examples, 230 billable LLM calls) on the modified proxy:
  **229/230 calls now report cache details** (1 unknown). Overall hit-rate **63.0%**
  (337 152 cached of 534 773 input tokens) ⇒ **−31.5% on input cost** at gpt-4o published
  rates ($1.3369 → $0.9155). Per module: `risk_estimator` 76.3%, `critic` 59.0%,
  `policy` 50.4%, `constitution_retriever` 57.2%, `compliance_layer` 43.1%,
  and **`perspectives` 0.0%**, **`simulator` 0.0%**, `hindsight`/`orchestration` 0.0%.
- **Behavior unchanged (A/B verified).** Three samples flipped `REFUSE`↔`NORMAL_COMPLETE`
  between replays, all with a refusing delivered text. Verified by `git stash`-ing the
  source change and replaying the same prompts on HEAD: `llm_rules/1` → REFUSE 3/3 on
  pre-change code and REFUSE 4/4 on post-change code; `llm_rules_direct_request/1` →
  NORMAL_COMPLETE 3/3 on both. The flips are risk-estimator sampling noise, not an effect
  of this change (which is telemetry-only: `TokenUsage` reaches no decision logic, and no
  touched dataclass is constructed positionally or reflected over).

### Finding for follow-up (hypothesis, not verified)

`perspectives` and `simulator` — the two modules the A5a static-prefix reorder targeted —
show a **0% hit-rate despite eligible prompts**, while `critic` and `policy` hit reliably.
Their static system prompts are the smallest: `simulator` ≈708 tokens and `perspectives`
≈1048 tokens (chars/4 estimate; `tiktoken` is not installed), against `critic` ≈1418 and
`policy` ≈1324. OpenAI caches only a prefix of **≥1024 tokens**, in 128-token increments.
Hypothesis: the simulator's static prefix is below the threshold outright, and the
perspectives' sits on the boundary, so neither exposes a cacheable static prefix — the
reorder cannot pay off until the shared prefix clears 1024 real tokens. Measured cached
values are all multiples of 128 ≥ 1024, consistent with this. Verifying requires an exact
tokenizer, not a chars/4 estimate.

## 8. Risks

- **Tuple-arity change in `_complete`** is the only structural edit. Mitigated: private
  method, 2 call sites, both in the same file; mypy catches a miss.
- **Provider shape drift**: a non-OpenAI client returning a `usage` without
  `prompt_tokens_details` must yield 0, not raise. Covered by test (c).
- **Test churn**: any test asserting the exact key set of `token_usage_json` would break.
  Mitigated by emitting the key only when `> 0`. Must be confirmed by running the suite.
- Schema: additive nullable column only; existing DBs migrate in place via the existing
  helper. No destructive migration.

## 9. Rollback

Revert the 4 files. The added SQLite column is nullable and unread by old code, so an
older binary runs against a migrated DB without error.
