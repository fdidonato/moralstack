# MoralStack Module Documentation

This directory contains detailed documentation for each module of the MoralStack system.

**For readers**: The documentation is intended for developers, testers and stakeholders who need to understand *what*
each
component does, *how* it integrates into the flow and *how* to verify it. Each module is described with interfaces, data
structures and
usage examples.

---

## Module Index

### Core

| Module                                               | File                                        | Description                        |
|------------------------------------------------------|---------------------------------------------|------------------------------------|
| [Orchestrator](./orchestrator.md)                    | `moralstack/runtime/orchestrator.py`        | Deliberative flow coordination     |
| [Risk Estimator](./risk_estimator.md)                | `moralstack/models/risk/`                   | Semantic risk classification       |
| [Policy LLM](./policy.md)                            | `moralstack/models/policy.py`               | Text generation and revision       |
| [Decision Explainability](./decision_explanation.md) | `moralstack/models/decision_explanation.py` | Structured decision explainability |

### Cognitive Modules

| Module                                    | File                                               | Description                  |
|-------------------------------------------|----------------------------------------------------|------------------------------|
| [Constitutional Critic](./critic.md)      | `moralstack/runtime/modules/critic_module.py`      | Ethical principle validation |
| [Consequence Simulator](./simulator.md)   | `moralstack/runtime/modules/simulator_module.py`   | Future scenario simulation   |
| [Hindsight Evaluator](./hindsight.md)     | `moralstack/runtime/modules/hindsight_module.py`   | Retrospective evaluation     |
| [Perspective Ensemble](./perspectives.md) | `moralstack/runtime/modules/perspective_module.py` | Multi-perspective analysis   |

### Constitution

| Module                                        | File                               | Description                  |
|-----------------------------------------------|------------------------------------|------------------------------|
| [Constitution Store](./constitution_store.md) | `moralstack/constitution/store.py` | Ethical principle management |

### Utilities

| Module                              | File                                | Description                                                                    |
|-------------------------------------|-------------------------------------|--------------------------------------------------------------------------------|
| [Benchmark](./benchmark.md)         | `scripts/benchmark_moralstack.py`   | Policy-aware benchmark; judge model config, independence                       |
| [Persistence](./persistence.md)     | `moralstack/persistence/`           | SQLite storage, run/request context, llm_calls, decision_traces                |
| Web UI                              | `moralstack/ui/app.py`              | FastAPI dashboard; form login at `/login`, dashboard at `/runs`                |
| [Server proxy](./server_proxy.md)   | `moralstack/server/`                | OpenAI-compatible `POST /v1/chat/completions`; governance headers and fingerprint |
| [OpenAI Params](./openai_params.md) | `moralstack/utils/openai_params.py` | Model-specific API params (max_tokens vs max_completion_tokens)                |
| [env_loader](../../moralstack/utils/env_loader.py) | `moralstack/utils/env_loader.py`    | Loads .env from project root at startup (moralstack, moralstack-ui, benchmark) |
| [clean_start](../../moralstack/utils/clean_start.py) | `moralstack/utils/clean_start.py`   | Removes reports, benchmark_outputs, logs, debug artifacts (CLI)                |

---

## Processing Flow

```
Request
   │
   ▼
┌──────────────┐
│Risk Estimator│ ──► Classify risk, determine path
└──────────────┘
   │
   ├─── risk < 0.3 ───► FAST PATH ──► Direct Generation
   │
   └─── risk ≥ 0.3 ───► DELIBERATIVE PATH
                              │
                              ▼
                    ┌─────────────────┐
                    │   Orchestrator  │
                    └─────────────────┘
                              │
              ┌───────────────┼───────────────┬───────────────┐
              ▼               ▼               ▼               ▼
        ┌──────────┐   ┌──────────┐   ┌──────────┐   ┌──────────┐
        │  Critic  │   │Simulator │   │Hindsight │   │Perspect. │
        └──────────┘   └──────────┘   └──────────┘   └──────────┘
              │               │               │               │
              └───────────────┴───────────────┴───────────────┘
                              │
                              ▼
                    Aggregated Guidance
                              │
                              ▼
                    ┌─────────────────┐
                    │   Policy LLM    │ ──► Guided revision
                    └─────────────────┘
                              │
                              ▼
                    Convergence Check
                              │
                              ▼
                       Final Response
```

---

## How to Read the Documentation

Each module document includes:

1. **Overview** - Module purpose and functionality
2. **Output Structure** - Dataclass and output types
3. **Usage** - Code examples
4. **Integration** - How the module interacts with the Orchestrator
5. **See Also** - Links to related modules

---

## Back to Main README

[← README.md](../../README.md)
