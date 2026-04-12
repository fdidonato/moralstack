# MoralStack Installation (OpenAI-only)

## Prerequisites

- Python 3.11+
- **OpenAI API key** (required)

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

**Development only (pytest, pytest-cov, ruff, black, mypy):**

```bash
pip install -e .[dev]
```

**With Web UI (FastAPI, uvicorn, jinja2):**

```bash
pip install -e .[ui]
```

**Alternative (legacy):**

```bash
pip install -r requirements.txt
```

Note: `requirements.txt` installs dependencies only; it does not register the `moralstack` CLI. Use `pip install -e .`
or `install.py` for that.

## SDK Installation

The Python SDK (`govern()`) is included in the base package — no extra install step needed. All required dependencies (`openai`, `pydantic`, `ruamel.yaml`, etc.) are already in `[project.dependencies]`.

```bash
pip install -e .
```

The `[ui]` extra is only needed for the web dashboard (`moralstack-ui`). The `[dev]` extra is only needed for running tests and linting.

### SDK Quickstart

```python
from moralstack import govern
from openai import OpenAI

# Minimum: OPENAI_API_KEY must be set in the environment
client = govern(OpenAI())

response = client.chat.completions.create(
    model="gpt-4o",
    messages=[{"role": "user", "content": "Explain recursion in programming."}],
)

print(response.content)                               # generated text (or refusal)
print(response.governance_metadata.final_action)     # NORMAL_COMPLETE | SAFE_COMPLETE | REFUSE
print(response.governance_metadata.risk_score)       # 0.0–1.0
```

### SDK Configuration via GovernanceConfig

All SDK parameters are optional and can be overridden programmatically:

```python
from moralstack import govern, GovernanceConfig
from openai import OpenAI

client = govern(
    OpenAI(),
    config=GovernanceConfig(
        # Pipeline credentials (default: from env)
        api_key="sk-...",            # OPENAI_API_KEY
        model="gpt-4o",             # OPENAI_MODEL
        base_url=None,              # OPENAI_BASE_URL

        # Domain-specific governance
        domain_overlay="healthcare",  # enforce a named domain overlay

        # Pipeline tuning
        max_deliberation_cycles=2,
        timeout_ms=600_000,

        # Failure handling
        failure_policy="refuse",     # "refuse" (default) | "passthrough"

        # Observability
        observability_mode="file_only",  # "off" | "file_only" | "db_only" | "dual"
        jsonl_dir="logs/audit",

        # Session tracking (multi-turn)
        enable_session_tracking=True,
    ),
)
```

The pipeline uses its **own** internal LLM client for deliberation (configured via `GovernanceConfig`). The `OpenAI()` client you pass to `govern()` is used only for final text generation.

## Configuration

All configuration can be managed via a `.env` file in the project root.

### Quick Setup

Copy .env.minimal to .env end configure your API key in OPENAI_API_KEY

### Customized Setup

1. Copy the template: `cp .env.template .env` (Linux/macOS) or `copy .env.template .env` (Windows)
2. Edit `.env` and set your values (at minimum `OPENAI_API_KEY`)
3. Run `moralstack`, `moralstack-ui`, or `python scripts/benchmark_moralstack.py` — they load `.env` automatically at
   startup

Non-empty values from `.env` **override** pre-existing environment variables (`load_dotenv(..., override=True)`).
Optional variables left empty are purged after loading to avoid invalid empty-string settings in third-party libraries.

### Model compatibility

Newer OpenAI models (gpt-5.x, o1, o3, o4) require `max_completion_tokens` instead of `max_tokens`. MoralStack
automatically selects the correct parameter based on the model name — no configuration needed.
See [docs/modules/openai_params.md](docs/modules/openai_params.md) for details and how to add support for future models.

### Variables

| Variable                       | Default                   | Description                                                    |
|--------------------------------|---------------------------|----------------------------------------------------------------|
| OPENAI_API_KEY                 | -                         | OpenAI API key (required)                                      |
| OPENAI_MODEL                   | gpt-4o                    | OpenAI model (see [Model compatibility](#model-compatibility)) |
| MORALSTACK_POLICY_REWRITE_MODEL | - (same as OPENAI_MODEL) | Policy `rewrite()` at cycle 2+; `.env.template` uses `gpt-4.1-nano`; set any lighter model to reduce latency (see [policy.md](docs/modules/policy.md)) |
| OPENAI_BASE_URL                | -                         | Base URL (proxy/enterprise)                                    |
| OPENAI_TIMEOUT_MS              | 60000                     | Timeout in milliseconds                                        |
| OPENAI_MAX_RETRIES             | 3                         | Retries on 429/503                                             |
| OPENAI_TEMPERATURE             | 0.7 (fallback)            | Generation temperature (`.env.template` starter: 0.1)         |
| OPENAI_TOP_P                   | 0.9 (fallback)            | Nucleus sampling parameter (`.env.template` starter: 0.8)     |
| MORALSTACK_OBSERVABILITY_DB_PATH    | -                      | SQLite DB path (enables persistence)                           |
| MORALSTACK_OBSERVABILITY_MODE       | db_only if DB set      | db_only \| dual \| file_only                                   |
| MORALSTACK_OBSERVABILITY_JSONL_DIR  | logs/observability     | JSONL output directory (file_only and dual modes)              |
| MORALSTACK_DB_PATH             | -                         | Deprecated alias for MORALSTACK_OBSERVABILITY_DB_PATH          |
| MORALSTACK_PERSIST_MODE        | -                         | Deprecated alias for MORALSTACK_OBSERVABILITY_MODE             |
| MORALSTACK_UI_PORT             | 8765                      | Web UI port                                                    |
| MORALSTACK_UI_USERNAME         | -                         | Basic Auth for UI (required when running moralstack-ui)        |
| MORALSTACK_UI_PASSWORD         | -                         | Basic Auth for UI                                              |
| MORALSTACK_VERBOSE             | -                         | Set to 1 for verbose output                                    |

**Risk Estimator**: Optional overrides (e.g. `MORALSTACK_RISK_MODEL`, `MORALSTACK_RISK_LOW_THRESHOLD`,
`MORALSTACK_RISK_MEDIUM_THRESHOLD`, `MORALSTACK_RISK_MAX_RETRIES`, …) are listed in `.env.template` and fully documented
in [docs/modules/risk_estimator.md](docs/modules/risk_estimator.md#environment-variables). Leave them commented to use
built-in defaults (risk estimator uses the same model as `OPENAI_MODEL` when `MORALSTACK_RISK_MODEL` is not set). **In
both CLI run and benchmark, risk configuration is read only from the environment (`.env`); there is no CLI override —
env is the single source of configuration.**

**Perspective**: Optional overrides (e.g. `MORALSTACK_PERSPECTIVES_MODEL`, `MORALSTACK_PERSPECTIVES_MAX_RETRIES`,
`MORALSTACK_PERSPECTIVES_MAX_TOKENS`, …) are listed in `.env.template` and fully documented
in [docs/modules/perspectives.md](docs/modules/perspectives.md#environment-variables). Leave them commented to use
built-in defaults (perspectives use the same model as `OPENAI_MODEL` when `MORALSTACK_PERSPECTIVES_MODEL` is not set). *
*In both CLI run and benchmark, perspective configuration is read only from the environment (`.env`); there is no CLI
override — env is the single source of configuration.**

**Critic**: Optional overrides (e.g. `MORALSTACK_CRITIC_MODEL`, `MORALSTACK_CRITIC_MAX_RETRIES`,
`MORALSTACK_CRITIC_MAX_TOKENS`, …) are listed in `.env.template` and fully documented
in [docs/modules/critic.md](docs/modules/critic.md#environment-variables). Leave them commented to use built-in
defaults (critic uses the same model as `OPENAI_MODEL` when `MORALSTACK_CRITIC_MODEL` is not set). **In both CLI run and
benchmark, critic configuration and model are read only from the environment (`.env`); there is no CLI override — env is
the single source of configuration.**

**Simulator**: Optional overrides (e.g. `MORALSTACK_SIMULATOR_MODEL`, `MORALSTACK_SIMULATOR_MAX_RETRIES`,
`MORALSTACK_SIMULATOR_MAX_TOKENS`, …) are listed in `.env.template` and fully documented
in [docs/modules/simulator.md](docs/modules/simulator.md#environment-variables). Leave them commented to use built-in
defaults (simulator uses the same model as `OPENAI_MODEL` when `MORALSTACK_SIMULATOR_MODEL` is not set). **In both CLI
run and benchmark, simulator configuration and model are read only from the environment (`.env`); there is no CLI
override — env is the single source of configuration.**

**Hindsight**: Optional overrides (e.g. `MORALSTACK_HINDSIGHT_MODEL`, `MORALSTACK_HINDSIGHT_MAX_RETRIES`,
`MORALSTACK_HINDSIGHT_MAX_TOKENS`, …) are listed in `.env.template` and fully documented
in [docs/modules/hindsight.md](docs/modules/hindsight.md#environment-variables). Leave them commented to use built-in
defaults (hindsight evaluator uses the same model as `OPENAI_MODEL` when `MORALSTACK_HINDSIGHT_MODEL` is not set). **In
both CLI run and benchmark, hindsight configuration and model are read only from the environment (`.env`); there is no
CLI override — env is the single source of configuration.**

**Orchestrator**: Optional overrides (e.g. `MORALSTACK_ORCHESTRATOR_MAX_DELIBERATION_CYCLES`,
`MORALSTACK_ORCHESTRATOR_TIMEOUT_MS`, `MORALSTACK_ORCHESTRATOR_ENABLE_PERSPECTIVES`,
`MORALSTACK_ORCHESTRATOR_PARALLEL_MODULE_CALLS`, `MORALSTACK_ORCHESTRATOR_PARALLEL_CRITIC_WITH_MODULES`,
`MORALSTACK_ORCHESTRATOR_ENABLE_DYNAMIC_PARALLEL_SCHEDULER`, `MORALSTACK_ORCHESTRATOR_ENABLE_SPECULATIVE_GENERATION`,
`MORALSTACK_ORCHESTRATOR_CYCLE1_EARLY_CONVERGENCE_MIN_WEIGHTED_APPROVAL`,
`MORALSTACK_ORCHESTRATOR_CYCLE1_EARLY_CONVERGENCE_MAX_SEMANTIC_HARM`,
`MORALSTACK_ORCHESTRATOR_CYCLE1_EARLY_CONVERGENCE_MIN_PER_PERSPECTIVE_APPROVAL`,
`MORALSTACK_ORCHESTRATOR_ENABLE_SIMULATOR_GATING`, `MORALSTACK_ORCHESTRATOR_SIMULATOR_GATE_SKIP_MAX_PRIOR_SEMANTIC_HARM`, …)
are listed in `.env.template` and fully documented in
[docs/modules/orchestrator.md](docs/modules/orchestrator.md#environment-variables). Leave them commented to use built-in
defaults. **In both CLI run and benchmark, orchestrator configuration is read only from the environment (`.env`); there
is no CLI override — env is the single source of configuration.**

**Benchmark**: Optional overrides (e.g. `MORALSTACK_BENCHMARK_OUTPUTS`, `MORALSTACK_BENCHMARK_BASELINE_MODEL`,
`MORALSTACK_BENCHMARK_JUDGE_MODEL`) are listed in `.env.template` and fully documented
in [docs/modules/benchmark.md](docs/modules/benchmark.md#environment-variables). `MORALSTACK_BENCHMARK_BASELINE_MODEL`
is the single source for the **baseline only** (raw GPT): when set, it overrides CLI `--model` for baseline; when unset,
uses gpt-4o. MoralStack modules use their own env vars. `MORALSTACK_BENCHMARK_JUDGE_MODEL` configures the LLM used for
evaluation: when set, it overrides the CLI `--judge-model` option; when not set, the judge uses the same model as
MoralStack policy. When the judge model differs from the generation model, the judge is **independent**; otherwise the
judge is **not independent**. Reports and the UI display this distinction explicitly.

### Alternative: environment variables

You can still set variables directly:

```bash
# Linux / macOS
export OPENAI_API_KEY=sk-...

# Windows (PowerShell)
$env:OPENAI_API_KEY = "sk-..."
```

## Verification

```bash
# With .env configured:
moralstack --mock   # Test with mock modules (no API)
moralstack          # Real launch (requires OPENAI_API_KEY in .env)

# Or with env var:
export OPENAI_API_KEY=sk-...
moralstack
```

## Quickstart

```bash
cp .env.template .env
# Edit .env and set OPENAI_API_KEY=sk-...
moralstack
```

Type a prompt in the interactive shell; the system will evaluate the risk and respond accordingly.

> **Note**: You can also use `python scripts/mstack_run.py` as a legacy wrapper, but the preferred method is
> `moralstack`. Run `moralstack --verbose` for detailed deliberation output. With `MORALSTACK_OBSERVABILITY_DB_PATH` set, use
> `moralstack-ui` to browse runs and export markdown reports on demand.

## Web UI (moralstack-ui)

With `pip install -e .[ui]` and `MORALSTACK_OBSERVABILITY_DB_PATH` set, run:

```bash
moralstack-ui
```

Then open in your browser:

**http://localhost:8765/**

You will be redirected to the login page. Enter `MORALSTACK_UI_USERNAME` and `MORALSTACK_UI_PASSWORD` from your `.env`.
After login you will access the dashboard at `/runs`.

**Troubleshooting 401:** Ensure `.env` is in the project root and `moralstack-ui` is run from the project directory (or
the package-based fallback will load it). If the password contains `#` or `=`, wrap it in quotes:
`MORALSTACK_UI_PASSWORD="pass#word"`.
