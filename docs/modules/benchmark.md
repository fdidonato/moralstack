# Benchmark Module

## Overview

The MoralStack benchmark suite (`scripts/benchmark_moralstack.py`) compares Baseline (plain GPT) and MoralStack on a
curated dataset of ethical dilemmas. It evaluates **decision correctness** (policy-driven, language-agnostic) and *
*response quality** via an LLM judge.

The judge evaluates both Baseline and MoralStack responses with two distinct signals: (1) `decision_correctness` (
CORRECT/DEBATABLE/WRONG), (2) `response_quality_score` (0–10 conditioned on decision). The judge does **not** decide
REFUSE/SAFE_COMPLETE/NORMAL_COMPLETE; that is determined by the policy layer.

## Language

The benchmark dataset questions are in **English**. The judge is **language-aware**: it evaluates and provides reasoning
in the same language as the original question and system response. If the question/response is in English, the judge
evaluates in English; if in Italian or another language, evaluate and respond in that language.

## Judge Independence

The judge model can be configured separately from the generation model (Baseline and MoralStack). This affects
evaluation validity:

- **Judge independent**: When the judge uses a **different** model than Baseline and MoralStack, the evaluation is
  independent. The judge has no shared parameters with the systems under test.
- **Judge not independent**: When the judge uses the **same** model as Baseline and MoralStack, the judge is not
  independent. Results may be biased because the same model evaluates its own (or a sibling model's) outputs.

Reports (markdown export) and the UI explicitly display this distinction:

- If `judge_model == model`: "the judge **is not independent** from the model that generates responses."
- If `judge_model != model`: "The judge is **independent** from Baseline and MoralStack (judge model: {judge_model})."

## Environment Variables

All benchmark configuration can be overridden via `.env`. Variables are read when the benchmark script starts (after
`load_env()`).

| Variable                              | Default             | Description                                                                                                                                                                                                                                                                                                                             |
|---------------------------------------|---------------------|-----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| `MORALSTACK_BENCHMARK_OUTPUTS`        | `benchmark_outputs` | Directory for benchmark report files (`benchmark_{run_id}.json`). Used by the benchmark script for persistence and by moralstack-ui for loading run details.                                                                                                                                                                            |
| `MORALSTACK_BENCHMARK_BASELINE_MODEL` | `gpt-4o`            | Model for **baseline only** (raw GPT, no MoralStack). **Single source of truth** for baseline: env > CLI `--model` > default. MoralStack modules use their own env vars (MORALSTACK_RISK_MODEL, MORALSTACK_CRITIC_MODEL, etc.).                                                                                                         |
| `MORALSTACK_BENCHMARK_JUDGE_MODEL`    | -                   | Model for the judge (evaluation). **Priority**: env > CLI `--judge-model` > default (same as generation model). When set, always used; when not set, the judge uses the same model as Baseline and MoralStack. When different from the generation model, the judge is independent. Example: `gpt-4o-mini` for a smaller, cheaper judge. |

## Model Resolution

**Baseline** (single source of truth for baseline only):

1. `MORALSTACK_BENCHMARK_BASELINE_MODEL` (if set in `.env`)
2. `--model` / `-m` (CLI)
3. `gpt-4o` (default)

**MoralStack policy** (CLI only; modules use their own env):

1. `--model` / `-m` (CLI)
2. `gpt-4o` (default)

**MoralStack policy rewrite** (env only; not set via CLI): `MORALSTACK_POLICY_REWRITE_MODEL` selects the model for
`rewrite()` in deliberation (cycle 2+). If unset, the rewrite uses the same model as primary policy generation. Report
`models_config.moralstack.policy_rewrite` reflects the effective value.

**Judge**:

1. `MORALSTACK_BENCHMARK_JUDGE_MODEL` (if set in `.env`)
2. `--judge-model` / `-j` (CLI)
3. Same model as Baseline and MoralStack (default)

When env variables are set, they **override** CLI options. This ensures reproducible runs when `.env` is committed or
shared.

**Model compatibility**: Newer models (gpt-5.x, o1, o3, o4) require `max_completion_tokens` instead of `max_tokens`. The
benchmark uses [OpenAI Params](./openai_params.md) to select the correct parameter automatically for both baseline and
judge.

## Usage

```bash
python scripts/benchmark_moralstack.py                    # All tests
python scripts/benchmark_moralstack.py --questions 5      # First 5 questions
python scripts/benchmark_moralstack.py --question-id 42  # Run only question 42
python scripts/benchmark_moralstack.py -j gpt-4o-mini    # Use gpt-4o-mini as judge (overridden by env if set)
```

With `MORALSTACK_BENCHMARK_BASELINE_MODEL=gpt-4o` in `.env`, the baseline always uses that model regardless of
`--model`. With `MORALSTACK_BENCHMARK_JUDGE_MODEL=gpt-4o-mini`, the judge always uses `gpt-4o-mini` regardless of
`--judge-model`.

## Report and UI

The benchmark report JSON (`benchmark_{run_id}.json`) stores `model`, `judge_model`, and `models_config` (baseline,
judge, MoralStack modules: policy, policy_rewrite, risk, critic, simulator, hindsight, perspectives). The markdown
export and the UI display these models clearly in the report header and on the run detail page.

**UI and export requirement**: The file `benchmark_{run_id}.json` in `MORALSTACK_BENCHMARK_OUTPUTS` is **required** for
the moralstack-ui to display the full benchmark summary (Executive Summary, FP/FN, per-question baseline/moralstack
responses, judge evaluations). When the file is missing, the run detail page shows a minimal view and the Export
Benchmark returns an explicit error. Run the benchmark from CLI to generate the report. Relative paths in
`MORALSTACK_BENCHMARK_OUTPUTS` are resolved against the project root so the UI finds reports regardless of the current
working directory.

## See Also

- [Persistence](./persistence.md) — Run/request storage; benchmark reports loaded from `MORALSTACK_BENCHMARK_OUTPUTS`
- [INSTALL.md](../../INSTALL.md) — Benchmark env vars summary
