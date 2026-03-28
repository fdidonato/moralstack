<p align="center">
  <img src="assets/banner.png" alt="MoralStack" width="512"/>
</p>

![License](https://img.shields.io/badge/license-Apache%202.0-orange)
![Python](https://img.shields.io/badge/python-3.11+-blue)
![Status](https://img.shields.io/badge/status-research--stage-yellow)
![Compliance](https://img.shields.io/badge/benchmark-98.8%25%20compliance-brightgreen)
![Model](https://img.shields.io/badge/model-GPT--4o-412991)

MoralStack is a governance layer that decides **whether**, **how**, and **under what constraints** a response should be
generated before text generation starts.

## Table of Contents

- [Core Idea](#core-idea)
- [Decision Model](#decision-model)
- [Architecture](#architecture)
- [Benchmark Results](#benchmark-results)
- [Quickstart](#quickstart)
- [Configuration](#configuration)
- [Running the Benchmark](#running-the-benchmark)
- [Web UI](#web-ui)
- [Documentation](#documentation)
- [Limitations & Trade-offs](#limitations--trade-offs)

## Core Idea

Traditional LLM pipelines optimize for helpfulness first. MoralStack adds an explicit policy layer that separates:

- **Decision**: `NORMAL_COMPLETE`, `SAFE_COMPLETE`, or `REFUSE`
- **Generation**: produce text consistent with the selected decision

This keeps decision logic auditable and minimizes unsafe false negatives in sensitive contexts.

## Decision Model

Every request produces an explicit `final_action`:

| Action | Meaning |
|---|---|
| `NORMAL_COMPLETE` | Direct response |
| `SAFE_COMPLETE` | Responsible response with safeguards |
| `REFUSE` | Refusal with safe redirection |

Single source of truth for bounds and action selection:

- `moralstack/runtime/decision/safe_complete_policy.py`
- API: `compute_action_bounds(...)`, `decide_final_action(...)`

`SAFE_COMPLETE` is a first-class policy action and is not inferred from text disclaimers.

## Architecture

High-level flow:

1. Risk and context analysis (parallel mini-estimators: intent, operational risk, signal detection)
2. Policy bounds computation + domain overlay application
3. Routing (`FAST_PATH` or `DELIBERATIVE_PATH`)
4. Deliberation (critic → simulator → perspectives → hindsight) when needed
5. Response assembly aligned with `final_action`

Main packages:

- `moralstack/runtime/` — orchestration runtime
- `moralstack/orchestration/` — controller, routing, deliberation services
- `moralstack/models/risk/` — risk estimation and calibration
- `moralstack/constitution/` — constitution schema, loader, store (YAML-driven)
- `moralstack/persistence/` — DB and file persistence modes
- `moralstack/ui/` — FastAPI dashboard (`moralstack-ui`)

## Benchmark Results

Evaluated on 84 questions spanning adversarial prompts, dual-use domains, regulated topics (legal, medical, financial), and false-positive torture tests. Judge model (GPT-5.2) is independent from both baseline and MoralStack generation.

### Safety & Utility

| Metric | Baseline (GPT-4o) | MoralStack |
|---|---|---|
| **False Negatives** (no refusal when needed) | 13 | **0** |
| **Information Leakage** | 14 (16.7%) | **0 (0%)** |
| **False Positives** (refusal on legitimate queries) | 0 | **0** |
| **Utility Preservation** (legitimate queries answered) | 62/62 | **62/62** |
| **Safe Redirection on Refusal** | 1/22 (4.5%) | **22/22 (100%)** |

### Head-to-Head (judged by GPT-5.2)

| | Baseline | MoralStack | Tie |
|---|---|---|---|
| **Wins** | 4 | **54** | 26 |
| **Avg Safety Score** | 7.92/10 | **9.27/10** | — |

### Decision Accuracy

```
             Predicted
Expected      NC    SC    REFUSE
───────────────────────────────
NC             9     1     0
SC             0    52     0
REFUSE         0     0    22
```

98.8% compliance rate. Zero system errors. The single off-diagonal cell (1 NC→SC) is a health-domain query where MoralStack adds a professional-consultation disclaimer — a reasonable policy choice for regulated content.

> **Note**: This benchmark demonstrates proof-of-concept effectiveness on 84 curated questions. It is not a claim of production-grade coverage across all possible inputs. We encourage independent evaluation.

### Avg Response Time

| | Baseline | MoralStack |
|---|---|---|
| **Avg Latency** | ~10s | ~70s |

Deliberative paths add latency by design (see [Limitations](#limitations--trade-offs)).

## Quickstart

### Prerequisites

- Python 3.11+
- OpenAI API key

## Installation

Before installing, please create a virtual environment and activate it.

```bash
python -m venv venv
source venv/bin/activate
```

**One-command (recommended):**

```bash
python scripts/install.py
```

Installs the package in editable mode with all extras (dev, ui). Registers `moralstack` and `moralstack-ui` CLI entry
points.

**Manual (equivalent to install.py):**

```bash
pip install -e ".[dev,ui]"
```

### Configure

```bash
cp .env.minimal .env
```

Set at least:

```env
OPENAI_API_KEY=sk-...
```

### Run

```bash
moralstack
```

Useful commands:

- `moralstack --help`
- `moralstack --verbose`
- `moralstack --mock`

Legacy wrapper (same runtime entrypoint): `python scripts/mstack_run.py`

## Configuration

Environment is loaded via `moralstack/utils/env_loader.py`.

- `.env` values are loaded with `override=True` (non-empty `.env` values override existing env vars)
- optional empty values are purged after load to avoid invalid client configuration

Key variables:

- `OPENAI_MODEL` (default `gpt-4o`)
- `MORALSTACK_POLICY_REWRITE_MODEL` (optional; model for deliberative `rewrite()` at cycle 2+; if unset, same as `OPENAI_MODEL`. `.env.template` suggests `gpt-4o-mini` for lower rewrite latency.)
- `OPENAI_TIMEOUT_MS` (default `60000`)
- `OPENAI_MAX_RETRIES` (default `3`)
- `OPENAI_TEMPERATURE` (code fallback default `0.7`; `.env.template` starter value `0.1`)
- `OPENAI_TOP_P` (code fallback default `0.9`; `.env.template` starter value `0.8`)
- `MORALSTACK_DB_PATH` (enable SQLite persistence)
- `MORALSTACK_PERSIST_MODE` (`db_only`, `dual`, `file_only`)
- `MORALSTACK_ORCHESTRATOR_BORDERLINE_REFUSE_UPPER` (default `0.95`)

For full variable reference see [INSTALL.md](INSTALL.md) and `docs/modules/*.md`.

Default models by component (each can be overridden via its env var; see `INSTALL.md` and module docs):

| Component | Default model | Env variable |
|-----------|---------------|--------------|
| Policy (generation) | gpt-4o | `OPENAI_MODEL` |
| Policy (rewrite) | same as primary, or `gpt-4o-mini` in `.env.template` | `MORALSTACK_POLICY_REWRITE_MODEL` |
| Risk estimator | follows `OPENAI_MODEL` unless set | `MORALSTACK_RISK_MODEL` |
| Critic | follows `OPENAI_MODEL` unless set | `MORALSTACK_CRITIC_MODEL` |
| Simulator | follows `OPENAI_MODEL` unless set | `MORALSTACK_SIMULATOR_MODEL` |
| Perspectives | follows `OPENAI_MODEL` unless set | `MORALSTACK_PERSPECTIVES_MODEL` |
| Hindsight | follows `OPENAI_MODEL` unless set | `MORALSTACK_HINDSIGHT_MODEL` |

## Running the Benchmark

```bash
python scripts/benchmark_moralstack.py
```

Benchmark supports separate baseline and judge models via:

- `MORALSTACK_BENCHMARK_BASELINE_MODEL`
- `MORALSTACK_BENCHMARK_JUDGE_MODEL`

When judge model differs from generation model, the judge is treated as independent.

## Web UI

Install UI extras and configure DB path:

```bash
pip install -e .[ui]
```

Set:

- `MORALSTACK_DB_PATH`
- `MORALSTACK_UI_USERNAME`
- `MORALSTACK_UI_PASSWORD`

Start:

```bash
moralstack-ui
```

Open [http://localhost:8765/](http://localhost:8765/) (or `MORALSTACK_UI_PORT`).

## Documentation

- [INSTALL.md](INSTALL.md)
- [Architecture spec](docs/architecture_spec.md)
- [Decision policy](docs/decision_policy.md)
- [Constitution design](docs/constitution.md)
- [Module docs](docs/modules/README.md)
- [Development guide](docs/DEVELOPMENT.md)
- [Limitations and trade-offs](docs/limitations_and_tradeoffs.md)

## Limitations & Trade-offs

MoralStack makes deliberate trade-offs:

- **Latency over speed**: deliberative paths run multiple LLM calls (risk → critic → simulator → perspectives → hindsight). Average response time is ~70s vs ~10s for raw GPT-4o. This is a design choice — governance takes time.
- **Multi-model cost**: a single deliberative request makes 7-9 LLM calls. We use `gpt-4o-mini` for lower-stakes modules (simulator, perspectives) to reduce cost.
- **Benchmark scope**: 84 curated questions demonstrate the approach but do not cover all edge cases. We recommend running your own evaluations on domain-specific inputs.
- **LLM non-determinism**: despite low temperature settings across all modules, LLM outputs can vary between runs. The system includes deterministic guardrails in code to bound this variance, but perfect reproducibility is not guaranteed.

We are actively working on reducing latency through early-exit optimizations, context-mode switching, speculative decoding (predicted outputs for draft revisions), and JSON response format enforcement for evaluation modules.

See full discussion in [docs/limitations_and_tradeoffs.md](docs/limitations_and_tradeoffs.md).
