<p align="center">
  <img src="assets/banner.png" alt="MoralStack" width="512"/>
</p>

# MoralStack

### Your LLM thinks. MoralStack judges.

A deliberative governance engine that decides *whether*, *how*, and *under what constraints* an LLM should respond — before a single token is generated.

![License](https://img.shields.io/badge/license-Apache%202.0-orange)
![Python](https://img.shields.io/badge/python-3.11+-blue)
![Status](https://img.shields.io/badge/status-research--stage-yellow)
![Compliance](https://img.shields.io/badge/benchmark-98.8%25%20compliance-brightgreen)
![Model](https://img.shields.io/badge/model-GPT--4o-412991)
[![CI](https://github.com/fdidonato/moralstack/actions/workflows/ci.yml/badge.svg)](https://github.com/fdidonato/moralstack/actions/workflows/ci.yml)
[![codecov](https://codecov.io/gh/fdidonato/moralstack/graph/badge.svg)](https://codecov.io/gh/fdidonato/moralstack)

---

> **Most AI safety tools are filters. MoralStack is a judge.**
>
> It runs a full deliberative pipeline — risk estimation, constitutional critique, consequence simulation,
> multi-perspective reasoning — and issues an explicit, auditable decision *before* your LLM generates anything.

---

## Table of Contents

- [What it does](#what-it-does)
- [Decision Model](#decision-model)
- [Architecture](#architecture)
- [Benchmark Results](#benchmark-results)
- [Installation](#installation)
- [30-Second Quickstart](#30-second-quickstart)
- [SDK Usage](#sdk-usage)
- [Configuration](#configuration)
- [Running the Benchmark](#running-the-benchmark)
- [Web UI](#web-ui)
- [Why not just use a filter?](#why-not-just-use-a-filter)
- [Documentation](#documentation)
- [Limitations & Trade-offs](#limitations--trade-offs)

---

## What it does

```
Traditional pipeline:   prompt ──► generate ──► (maybe filter)

MoralStack:             prompt ──► deliberate ──► decide ──► generate within bounds
```

Traditional LLM pipelines optimize for helpfulness first. MoralStack adds an explicit policy layer that separates:

- **Decision**: `NORMAL_COMPLETE`, `SAFE_COMPLETE`, or `REFUSE`
- **Generation**: produce text consistent with the selected decision

This keeps decision logic auditable and minimizes unsafe false negatives in sensitive contexts.

---

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

---

## Architecture

High-level flow:

```
Request
  │
  ▼
[Risk Estimator] ─────────── parallel mini-estimators:
  │                          intent · operational risk · signal detection
  ▼
[Policy Router] ──────────── applies domain overlay, computes action bounds
  │
  ├── FAST_PATH ──────────────────────────────────────────────────────────┐
  │   (clearly benign or clearly harmful — deliberation skipped)          │
  │                                                                       │
  └── DELIBERATIVE_PATH                                                   │
        │                                                                 │
        ├── [Constitutional Critic]    checks principle violations        │
        ├── [Consequence Simulator]    projects downstream harm           │
        ├── [Perspectives Ensemble]    multi-stakeholder reasoning        │
        └── [Hindsight Evaluator]      retrospective quality check        │
                    │                                                     │
                    ▼                                                     │
             [Convergence Engine] ──── issues final_action ◄─────────────┘
                    │
                    ▼
             [Response Assembler] ─── generates within the decided bounds
```

Main packages:

- `moralstack/sdk/` — Python SDK (`govern()`, `GovernedClient`, `GovernanceConfig`)
- `moralstack/runtime/` — orchestration runtime
- `moralstack/orchestration/` — controller, routing, deliberation services
- `moralstack/models/risk/` — risk estimation and calibration
- `moralstack/constitution/` — constitution schema, loader, store (YAML-driven)
- `moralstack/persistence/` — DB and file persistence modes
- `moralstack/ui/` — FastAPI dashboard (`moralstack-ui`)

---

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
| **Wins** | 6 | **54** | 24 |
| **Avg Safety Score** | 7.83/10 | **9.27/10** | — |

*(Latest full run: benchmark 12, same 84-question suite and judge.)*

### Decision Accuracy

**98.8% compliance rate. Zero system errors.**

```
             Predicted
Expected      NC    SC    REFUSE
───────────────────────────────
NC             9     1     0
SC             0    52     0
REFUSE         0     0    22
```

The single off-diagonal cell (1 NC→SC) is a health-domain query where MoralStack adds a professional-consultation disclaimer — a reasonable policy choice for regulated content.

> **Note**: This benchmark demonstrates proof-of-concept effectiveness on 84 curated questions. It is not a claim of production-grade coverage across all possible inputs. We encourage independent evaluation.

### Avg Response Time

| | Baseline | MoralStack |
|---|---|---|
| **Mean wall-clock** | ~6s | **~36s** |
| **Median wall-clock** | — | **~26s** |

*(Benchmark 12, 84 questions; mean ~51% lower than the original benchmark configuration ~73s mean. Fast path rate ~37% vs ~11% previously, due to REFUSE queries now routed through fast path.)*

Deliberative paths add latency by design. See [Limitations & Trade-offs](#limitations--trade-offs).

---

## Installation

### From PyPI (recommended for users)

```bash
pip install moralstack
```

For the optional admin UI:

```bash
pip install "moralstack[ui]"
```

### From source (for contributors)

```bash
git clone https://github.com/fdidonato/moralstack.git
cd moralstack
python -m venv venv
source venv/bin/activate
pip install -e ".[dev,ui]"
```

### Configure

```bash
cp .env.minimal .env
# edit .env and set OPENAI_API_KEY=sk-...
```

## 30-Second Quickstart

```python
from moralstack import govern
from openai import OpenAI

# Wrap any OpenAI-compatible client with MoralStack governance.
client = govern(OpenAI())

response = client.chat.completions.create(
    model="gpt-4o",
    messages=[{"role": "user", "content": "What are the symptoms of vitamin D deficiency?"}],
)

# Full OpenAI API compatibility:
print(response.choices[0].message.content)

# Plus governance metadata on every call:
meta = response.governance_metadata
print(f"Decision: {meta.final_action}")
print(f"Risk: {meta.risk_score:.2f} ({meta.risk_category})")
print(f"Overlay: {meta.domain_overlay}")
```

More patterns in [`examples/`](./examples/).

---

## SDK Usage

Use MoralStack as a governance wrapper around your existing OpenAI client — no server, no HTTP, no separate process.

```python
from moralstack import govern
from openai import OpenAI

client = govern(OpenAI())

response = client.chat.completions.create(
    model="gpt-4o",
    messages=[{"role": "user", "content": "How do I pick a lock?"}],
)

print(response.content)
# I'm unable to assist with that request.

print(response.governance_metadata.final_action)
# REFUSE

print(response.governance_metadata.risk_score)
# 0.87
```

`govern()` wraps any OpenAI-compatible client. All non-`chat.completions.create()` calls pass through transparently (`client.models.list()`, `client.files.*`, etc.).

### Decision routing

| `final_action` | What happens |
|---|---|
| `NORMAL_COMPLETE` | Request passes unchanged to your OpenAI client |
| `SAFE_COMPLETE` | Governance constraints injected into system prompt, then calls your client |
| `REFUSE` | OpenAI is **not called** — refusal text returned directly |

### Governance metadata

Every response carries `response.governance_metadata`:

```python
meta = response.governance_metadata

meta.final_action           # NORMAL_COMPLETE | SAFE_COMPLETE | REFUSE
meta.risk_score             # 0.0 (benign) — 1.0 (harmful)
meta.risk_category          # CLEARLY_BENIGN | SENSITIVE | CLEARLY_HARMFUL
meta.path                   # FAST_PATH | DELIBERATIVE_PATH
meta.reason_codes           # ["DUAL_USE", "SENSITIVE_DOMAIN", ...]
meta.triggered_principles   # constitution principles activated
meta.decision_reason        # human-readable explanation
meta.conversation_id        # session tracking (multi-turn)
meta.turn_index             # turn counter within session
```

### GovernanceConfig

```python
from moralstack import govern, GovernanceConfig
from openai import OpenAI

client = govern(
    OpenAI(),
    config=GovernanceConfig(
        domain_overlay="healthcare",     # enforce a specific domain overlay
        failure_policy="passthrough",    # on pipeline error: call OpenAI directly (unsafe)
        observability_mode="file_only",  # write JSONL audit trail
        jsonl_dir="logs/audit",
    ),
)
```

All parameters default to sensible values. Minimum required: `OPENAI_API_KEY` in environment.

### SDK model resolution

When you use `govern()`, MoralStack runs two separate model planes:

1. **Governance plane (internal)**: risk estimation, deliberation modules, speculative draft, policy rewrite, and refusal text.
2. **Generation plane (your client)**: the final user-visible response when `final_action` is `NORMAL_COMPLETE` or `SAFE_COMPLETE`.

The model passed in `client.chat.completions.create(model="...")` controls only the final response. `OPENAI_MODEL` and `MORALSTACK_*_MODEL` variables control only the internal governance pipeline. Neither side overrides the other.

| Stage | Model source |
|---|---|
| Final response (`NORMAL_COMPLETE`) | `model=` passed to `chat.completions.create(...)` |
| Final response (`SAFE_COMPLETE`) | same `model=`, with governance constraints injected into system message |
| `REFUSE` response text | internal policy model (`OPENAI_MODEL`) |
| Speculative overlap draft | internal policy model (`OPENAI_MODEL`) |
| Policy `rewrite()` (cycle 2+) | `MORALSTACK_POLICY_REWRITE_MODEL` (fallback: `OPENAI_MODEL`) |
| Risk / Critic / Simulator / Perspectives / Hindsight | `MORALSTACK_*_MODEL` per module, fallback to `OPENAI_MODEL` |

When `final_action` is `REFUSE`, your wrapped client is not called for generation.

### Streaming

```python
for chunk in client.chat.completions.create(
    model="gpt-4o",
    messages=[{"role": "user", "content": "..."}],
    stream=True,
):
    print(chunk.choices[0].delta.content or "", end="", flush=True)
```

Governance deliberation happens **before** streaming starts. If `REFUSE`, a single synthetic chunk is yielded with the refusal text.

---

## Configuration

Environment is loaded via `moralstack/utils/env_loader.py`.

- `.env` values are loaded with `override=True` (non-empty `.env` values override existing env vars)
- Optional empty values are purged after load to avoid invalid client configuration

### Key variables

| Variable | Default | Description |
|---|---|---|
| `OPENAI_API_KEY` | *(required)* | Your OpenAI API key |
| `OPENAI_MODEL` | `gpt-4o` | Model used by all pipeline modules |
| `MORALSTACK_POLICY_REWRITE_MODEL` | same as `OPENAI_MODEL` | Model for deliberative `rewrite()` at cycle 2+. `.env.template` sets `gpt-4.1-nano` for lower rewrite latency |
| `OPENAI_TIMEOUT_MS` | `60000` | Per-call timeout in milliseconds |
| `OPENAI_MAX_RETRIES` | `3` | Retry count on transient errors |
| `OPENAI_TEMPERATURE` | `0.1` | Temperature for all modules |
| `OPENAI_TOP_P` | `0.8` | Top-p sampling parameter |
| `MORALSTACK_OBSERVABILITY_DB_PATH` | *(unset)* | Enables SQLite persistence |
| `MORALSTACK_OBSERVABILITY_MODE` | `file_only` | `db_only` · `dual` · `file_only` |
| `MORALSTACK_OBSERVABILITY_JSONL_DIR` | `logs/observability` | JSONL output directory |
| `MORALSTACK_DB_PATH` / `MORALSTACK_PERSIST_MODE` | — | Deprecated aliases — still work |
| `MORALSTACK_ORCHESTRATOR_BORDERLINE_REFUSE_UPPER` | `0.95` | Upper boundary for the borderline-refuse zone |

### Default models by component

| Component | Default model | Override variable |
|---|---|---|
| Policy (generation) | `gpt-4o` | `OPENAI_MODEL` |
| Policy (rewrite) | same as primary, or `gpt-4.1-nano` in `.env.template` | `MORALSTACK_POLICY_REWRITE_MODEL` |
| Risk estimator | follows `OPENAI_MODEL` | `MORALSTACK_RISK_MODEL` |
| Critic | follows `OPENAI_MODEL` | `MORALSTACK_CRITIC_MODEL` |
| Simulator | follows `OPENAI_MODEL` | `MORALSTACK_SIMULATOR_MODEL` |
| Perspectives | follows `OPENAI_MODEL` | `MORALSTACK_PERSPECTIVES_MODEL` |
| Hindsight | follows `OPENAI_MODEL` | `MORALSTACK_HINDSIGHT_MODEL` |

For the full variable reference see [INSTALL.md](INSTALL.md) and `docs/modules/*.md`.

---

## Running the Benchmark

```bash
python scripts/benchmark_moralstack.py
```

Override baseline and judge models independently:

```bash
MORALSTACK_BENCHMARK_BASELINE_MODEL=gpt-4o \
MORALSTACK_BENCHMARK_JUDGE_MODEL=gpt-5.2 \
python scripts/benchmark_moralstack.py
```

When the judge model differs from the generation model, it is treated as independent.

---

## Web UI

Install UI extras:

```bash
pip install -e ".[ui]"
```

Configure persistence and credentials:

```bash
# .env
MORALSTACK_DB_PATH=moralstack.db
MORALSTACK_UI_USERNAME=admin
MORALSTACK_UI_PASSWORD=your_password
```

Start:

```bash
moralstack-ui
# → http://localhost:8765  (override with MORALSTACK_UI_PORT)
```

Inspect every decision: LLM calls, critic scores, risk traces, decision explanation, convergence steps, and benchmark comparisons.

---

## Why not just use a filter?

| | Regex / keywords | Moderation APIs | **MoralStack** |
|---|:---:|:---:|:---:|
| Understands context | ✗ | Partial | ✓ |
| Auditable decisions | ✗ | ✗ | ✓ |
| Domain-configurable | ✗ | ✗ | ✓ |
| Handles dual-use | ✗ | Partial | ✓ |
| Safe redirection | ✗ | ✗ | ✓ |
| Counterfactual reasoning | ✗ | ✗ | ✓ |
| Zero false negatives* | ✗ | ✗ | ✓ |

*On our benchmark set — see full methodology above.*

- **Deliberative, not reactive** — runs a multi-module reasoning pipeline, not a classifier
- **First-class `SAFE_COMPLETE`** — "respond with safeguards" is an explicit policy action, not a post-hoc disclaimer
- **Full audit trail** — every decision is explainable, logged, and queryable
- **Domain overlay system** — YAML-configurable per sector, no code changes required

---

## Documentation

- [INSTALL.md](./INSTALL.md) — detailed installation guide
- [examples/](./examples/) — runnable code examples
- [docs/architecture_spec.md](./docs/architecture_spec.md)
- [docs/decision_policy.md](./docs/decision_policy.md)
- [docs/constitution.md](./docs/constitution.md)
- [docs/creating_overlays.md](./docs/creating_overlays.md)
- [docs/modules/](./docs/modules/README.md)
- [docs/DEVELOPMENT.md](./docs/DEVELOPMENT.md)
- [docs/limitations_and_tradeoffs.md](./docs/limitations_and_tradeoffs.md)

---

## Limitations & Trade-offs

MoralStack makes deliberate trade-offs:

**Latency over speed**: Deliberative paths run multiple LLM calls (risk → critic → simulator → perspectives → hindsight). On the latest benchmark run, mean wall-clock is ~36s (median ~26s) vs ~6s for raw GPT-4o. This is a design choice — governance takes time. Latency has been reduced through speculative decoding, parallel risk estimation, lighter models for simulator and rewrite (`gpt-4.1-nano`), structured JSON output enforcement, and soft-revision prompt constraints. Further optimizations (early-exit on low-risk queries, context-mode switching) are planned.

**Multi-model cost**: A single deliberative request makes 7–9 LLM calls. Example profiles: `.env.minimal` uses `gpt-4.1-nano` for policy rewrite and simulator, and `gpt-4o-mini` for perspectives — all overridable via env.

**LLM non-determinism**: Despite low temperature settings across all modules, LLM outputs can vary between runs. The system includes deterministic guardrails in code to bound this variance, but perfect reproducibility is not guaranteed.

**Benchmark scope**: 84 curated questions demonstrate the approach but do not cover all edge cases. We recommend running your own evaluations on domain-specific inputs.

See full discussion in [docs/limitations_and_tradeoffs.md](docs/limitations_and_tradeoffs.md).

---

<p align="center">Apache 2.0 · Built with deliberation, not just parameters.</p>
