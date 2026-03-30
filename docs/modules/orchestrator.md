# Orchestrator

> **Module**: `moralstack/runtime/orchestrator.py` (facade); `moralstack/orchestration/controller.py` (thin controller).

The Orchestrator is the central component of MoralStack that coordinates the entire deliberative processing flow. The runtime facade delegates to `OrchestrationController`, which uses injected **PersistencePort** (default: `NullPersistence`), **PathRouter**, **OverlayPolicyApplier** (overlay_policy), **TraceLifecycle**, and **DecisionLogger** for request-scoped persistence, routing, overlay sensitivity/risk floor, trace lifecycle, and decision explanation logging.

**For testers and stakeholders**: Every response produced by the system has an explicit **final action** (
`final_action`:
**NORMAL_COMPLETE** | **SAFE_COMPLETE** | **REFUSE**) and a **path** (**FAST_PATH** | **DELIBERATIVE_PATH**), exposed in
`response.metadata`. These fields are the reference for benchmarks, decision correctness metrics and audit.

---

## Overview

The Orchestrator handles:

- Request reception and preprocessing
- Initial risk estimation
- Routing between **Fast Path** and **Deliberative Path**
- Deliberative cycle coordination
- Guidance aggregation from modules
- Final response assembly

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                         ORCHESTRATOR                             │
│                                                                  │
│  Request ──► Risk Estimation ──► Path Selection                  │
│                                       │                          │
│              ┌────────────────────────┴────────────────────┐     │
│              ▼                                              ▼     │
│         FAST PATH                              DELIBERATIVE PATH │
│     (risk < 0.3)                                (risk ≥ 0.3)     │
│              │                                       │           │
│              ▼                                       ▼           │
│      Direct Generation              ┌──────────────────────┐     │
│              │                      │   DELIBERATIVE CYCLE  │     │
│              │                      │ ┌──────────────────┐ │     │
│              │                      │ │ Generate/Revise  │ │     │
│              │                      │ │ Critic           │ │     │
│              │                      │ │ Simulator        │ │     │
│              │                      │ │ Perspectives     │ │     │
│              │                      │ │ Hindsight        │ │     │
│              │                      │ │ Convergence      │ │     │
│              │                      │ └──────────────────┘ │     │
│              │                      └──────────────────────┘     │
│              └─────────────────────────────┬─────────────────────┘
│                                            ▼                     │
│                                   Response Assembly              │
└─────────────────────────────────────────────────────────────────┘
```

---

## Module result contracts

The deliberation runner and controller consume results from cognitive modules (critic, simulator, hindsight, perspectives) and from the policy LLM via **Protocol** types defined in `moralstack/orchestration/types.py`. These protocols provide a typed contract (structural subtyping) so that:

- Concrete module return types (e.g. `CriticReport`, `SimulationResult`, `HindsightResult`, `EnsembleResult`) satisfy the protocols without orchestration importing the runtime modules.
- Refactors that rename or change attributes are caught by type checkers where the runner uses direct attribute access.

Protocols include: `PolicyGenerationResultProtocol`, `CriticReportProtocol`, `QuickCheckResultProtocol`, `SimulationResultProtocol`, `HindsightResultProtocol`, `PerspectiveResultProtocol`, `EnsembleResultProtocol`, `RiskEstimationProtocol`, `LoggerProtocol`, `ConstitutionStoreProtocol`, and `OutputProtectorProtocol`. **DeliberationDependencies** (injected into `DeliberationRunner`) is a dataclass whose fields are typed with the corresponding protocols (`PolicyLLMProtocol`, `CriticProtocol`, `SimulatorProtocol`, `HindsightProtocol`, `PerspectiveEnsembleProtocol`, `ConstitutionStoreProtocol`, `OutputProtectorProtocol`) from `moralstack/core/types.py` and `moralstack/orchestration/types.py`. `DeliberationState` fields (`critiques`, `simulations`, `hindsight`, `perspectives`, `_perspectives_aggregation`) are typed with these protocols. The package `moralstack.orchestration` is checked with **mypy strict** (see `pyproject.toml`); some runtime module APIs may extend the base protocols with optional keyword arguments.

### Runner entry points and constitution

`DeliberationRunner.run_fast_path` and `DeliberationRunner.run_deliberative_path` accept an optional keyword argument `constitution`. When the controller passes a pre-loaded constitution (one load per request via `get_constitution_safe`), the runner reuses it for quick_check, assemble, and critique, avoiding multiple store lookups. If `constitution` is omitted, the runner loads from the store when needed (backward compatible).

### Supporting modules (orchestration)

`DeliberationRunner` delegates to dedicated modules to keep responsibilities separated:

- **guidance_builder** — Builds aggregated guidance string from critic, perspectives, hindsight, and simulator state (`build_aggregated_guidance(state)`).
- **convergence_evaluator** — Evaluates whether the deliberation has converged and which `DecisionType` to apply (`ConvergenceEvaluator(config).check_convergence(state)`, `determine_decision(state)`). Invariants and structured logging for the loop remain in **convergence.py** (`enforce_convergence_invariants`, `log_convergence_event`).
- **language_resolver** — Resolves explicit language and builds prompt with language prefix (`resolve_prompt_with_language(prompt, detected_iso, fallback_prompt)`), reusing logic from `safe_refusal_generator` and `_policy_helpers`.
- **persistence_helpers** — Centralizes optional diagnostics logging and LLM call persistence (`record_llm_call(logger, diagnostics_payload, persist_kwargs)`).

### Hindsight path diagnostics

`DeliberationRunner._apply_hindsight_if_needed` and `_evaluate_hindsight` emit structured events via `orch_debug_log` with `hypothesisId` `H-hindsight-path` and payload `component: hindsight_diagnostic`. The `outcome` field records the branch taken, for example: `disabled_by_config`, `skipped_no_module`, `gated_not_final_cycle`, `invoke_evaluate`, `evaluate_ok`, `evaluate_aborted_timeout_guard`, `evaluate_failed`, `evaluate_failed_orchestrator_timeout`.

Persistence matches **`MORALSTACK_PERSIST_MODE`** (see `moralstack/persistence/config.py`): **`db_only`** inserts into SQLite `debug_events`; **`dual`** writes both DB and `.debug/debug.log` (NDJSON); **`file_only`** writes the NDJSON file only. The same events appear in the root logger at **INFO** as `hindsight_diagnostic outcome=...`.

---

## Configuration

### OrchestratorConfig

```python
from moralstack.runtime.orchestrator import OrchestratorConfig, RiskThresholds

config = OrchestratorConfig(
    max_deliberation_cycles=2,  # Maximum number of deliberative cycles
    timeout_ms=600000,  # Total timeout (10 minutes)
    risk_thresholds=RiskThresholds(
        low=0.3,  # Below: fast path
        medium=0.7,  # Between low and medium: standard deliberation
    ),
    enable_simulation=True,  # Enable Consequence Simulator
    enable_hindsight=True,  # Enable Hindsight Evaluator
    enable_perspectives=True,  # Enable Perspective Ensemble
    min_hindsight_score=0.8,  # Minimum score for convergence
)
```

### Key Parameters

| Parameter                              | Default | Description                                                      |
|----------------------------------------|---------|------------------------------------------------------------------|
| `max_deliberation_cycles`              | 2       | Maximum deliberative cycle iterations                            |
| `timeout_ms`                           | 600000  | Total timeout in milliseconds                                    |
| `risk_thresholds.low`                  | 0.3     | Threshold for fast path                                          |
| `risk_thresholds.medium`               | 0.7     | Threshold for extended deliberation                              |
| `min_hindsight_score`                  | 0.8     | Minimum hindsight score for convergence                          |
| `borderline_refuse_upper`              | 0.95    | Upper bound for borderline REFUSE deliberation                   |
| `early_exit_perspectives_threshold`    | 0.85    | Weighted approval threshold for early exit (critic PROCEED path) |
| `parallel_critic_with_modules`         | `True`  | When `True` and `parallel_module_calls` is `True`, the critic runs in parallel with the simulator and perspectives instead of as a sequential gate. See [Latency-oriented parameters](#latency-oriented-parameters). |
| `enable_speculative_generation`        | `True`  | When `True`, risk estimation and speculative draft generation run in parallel before routing. The draft is reused on benign, fast, and deliberative routes when applicable. See [Latency-oriented parameters](#latency-oriented-parameters). |

### Latency-oriented parameters

These flags reduce wall-clock latency **without changing routing policy** (`decide_action`, `get_route`, overlay floors, or convergence invariants). They do not change how `final_action` is computed from risk and module outputs.

**`parallel_critic_with_modules` (default `True`)**

- **Requires** `parallel_module_calls=True`. When both are `True`, each deliberation cycle runs **critic**, **simulator**, and **perspectives** concurrently (three parallel LLM calls per cycle when those modules are enabled).
- **When `False`**: the runner uses a two-stage layout: critic runs first; only if there is no hard violation do simulator and perspectives run in parallel. This avoids paying for simulator/perspective calls when the critic would reject the draft, but adds sequential critic latency before sim/persp start.
- **Hard violations**: If the critic reports a hard violation, simulator and perspective results from that cycle are **discarded** and do not affect merged state. Convergence and refusal logic see the same critic outcome as in the gated layout; you may pay extra token cost in the rare hard-violation case.
- **Set to `false`** if you prioritize minimizing LLM spend on hard-violation paths over latency.

**`enable_speculative_generation` (default `True`)**

- **When `True`**: `OrchestrationController` starts **risk estimation** and a **speculative policy `generate`** (same base system prompt as normal first-pass generation) in parallel. After risk returns, routing proceeds as usual; the speculative draft is **not** used for policy routing decisions.
- **Reuse**: On benign fast path, fast path, and deliberative path, the draft is reused when it is still valid (skipping a duplicate first `generate` where implemented). On **REFUSE**, the speculative call is unused (wasted latency/token trade-off). **`SAFE_COMPLETE`** path does not reuse this draft (different system instructions).
- **Constrained generation** (`CLEARLY_HARMFUL` deliberation): the speculative draft is **not** applied as cycle-1 output; the constrained system prompt is used instead.
- **Note**: Speculative generation uses language resolution **before** the risk estimator’s `detected_language` is available (fallback path). Routing and safety decisions are unchanged; draft wording may differ slightly from a strictly sequential generate-after-risk for the same request.

### Borderline REFUSE Upper Bound

MoralStack supports a configurable parameter `borderline_refuse_upper` that defines the inclusive upper bound
for invoking the deliberative path on REFUSE decisions.

A REFUSE with `risk_score` in the interval:

```
risk_thresholds.medium <= risk_score <= borderline_refuse_upper
```

will enter the deliberative pipeline instead of being handled with early-fast refusal.

This parameter is useful when you want more contextual reasoning for borderline cases
that are not extreme.

- **Default**: `0.95`
- **Type**: `float` between `0.0` and `1.0`

Configuration example:

```python
OrchestratorConfig(
    risk_thresholds=RiskThresholds(low=0.3, medium=0.7),
    borderline_refuse_upper=0.90,  # more restrictive: deliberate up to 0.90
)
```

When `risk_score > borderline_refuse_upper`, the system applies early-fast refusal as before.

**Path and trace after borderline deliberation**: When REFUSE is in the borderline range and the
deliberative loop is run, the **execution path** is DELIBERATIVE_PATH. The FINAL decision trace and
`response.metadata.path` are set to **DELIBERATIVE_PATH** (not FAST_PATH), and `total_cycles` in
the report reflect the number of deliberative cycles actually executed.

See also: documentation on `risk_thresholds`.

---

## Processing Flows

### Overlay Sensitivity Floor

When a constitutional overlay is marked `sensitive: true`, the Controller applies a floor to `risk_score`:

- `OVERLAY_SENSITIVE_RISK_FLOOR = 0.35` (constant in `moralstack/orchestration/overlay_policy.py`, imported by controller)
- If `overlay.sensitive == True` and `risk_score < 0.35`, then `risk_score = 0.35`
- This forces routing toward the deliberative path for all sensitive domains, even when the risk estimator
  assigns a low score

### Fast Path (risk < 0.3)

For low-risk requests, the system uses an optimized path:

```
Request → Risk Estimation (< 0.3) → Direct Generation → Response
```

**Typical latency**: ~10-12s (benchmark, 84 questions)

### Deliberative Path (risk ≥ 0.3)

For significant-risk requests:

1. **Generation/Revision**: Generate or revise the draft
2. **Constitutional Critique**: Check constitutional violations
3. **Consequence Simulation**: Simulate future scenarios
4. **Perspective Ensemble**: Evaluate from multiple perspectives
5. **Hindsight Evaluation**: Retrospective evaluation
6. **Convergence Check**: Verify termination criteria

**Typical latency**: Deliberative path averages ~45-60s for standard queries,
~70-85s for sensitive-domain queries (1 cycle ~35s, 2 cycles ~65s).
Fast path averages ~10-12s.

---

## Convergence Criteria

The deliberative cycle terminates when:

1. **Max cycles reached**: Maximum number of iterations
2. **Early exit (critic PROCEED + perspectives)**: Critic has zero violations and weighted perspectives approval ≥ `early_exit_perspectives_threshold` (default 0.85). Does not require hindsight (which is skipped in cycle 1 when `enable_hindsight_gating=True`). Produces `CONVERGED` or `CONVERGED_WITH_SUGGESTIONS` if pending suggestions exist.
3. **Satisfactory scores**: `hindsight_score ≥ 0.8` with no critical violations
4. **All modules satisfied**: Critic without violations, Perspectives with high approval, Hindsight with "proceed"
5. **Stable quality**: After 2+ cycles with high and stable scores

### Report / log: "Cycles exhausted" vs "Converged"

In phase logs and reports, **"Cycles exhausted"** is displayed only when `state.cycle >= max_cycles` (display-only). It
is not derived from the internal convergence heuristic. Thus "Decision: converged" and "Cycles exhausted: True" do not
appear together when the run converged before reaching the cycle limit.

### CYCLES_EXHAUSTED Fallback

If deliberation exhausts cycles (`stop_reason == "CYCLES_EXHAUSTED"`) and the post-deliberation decision is
`NORMAL_COMPLETE`, the Controller checks whether the context is sensitive:

- `risk_category ∈ {SENSITIVE, MORALLY_NUANCED}` **or** overlay `sensitive: true`

If so, the decision is forced to `SAFE_COMPLETE` with reason code
`cycles_exhausted_sensitive_fallback`. This fallback never degrades a REFUSE.

---

## Guidance Aggregation

The Orchestrator aggregates feedback from all modules to guide revisions:

```python
# Aggregated guidance sources:
# 1. Critic → revision_guidance (violations and suggestions)
# 2. Perspectives → suggestions and concerns
# 3. Hindsight → feedback and suggestions
# 4. Simulator → negative consequences (if valence < 0)
```

The aggregated guidance is passed to the Policy for revision:

```
[CRITIC] Add medical disclaimer
[PERSPECTIVES - Suggestions] Vulnerable User: Acknowledge emotional impact
[PERSPECTIVES - Concerns] Vulnerable User: Tone too detached
[HINDSIGHT] Low score (0.45). Improve ethical balance
[SIMULATOR] Negative consequence: User might ignore warning signs
```

---

## Decision Model (Final Action)

The Orchestrator exposes a deterministic **final action**, independent of the generated text:

| final_action        | Meaning                                               | Typical response_type                         |
|---------------------|-------------------------------------------------------|-----------------------------------------------|
| **NORMAL_COMPLETE** | Authorized, direct response                           | `DIRECT`                                      |
| **SAFE_COMPLETE**   | Response with responsible framing and explicit limits | `WITH_CAVEAT`                                 |
| **REFUSE**          | Refusal with explanation and redirect                 | `FULL_REFUSAL`, `PARTIAL_REFUSAL`, `REDIRECT` |

The decision is made by `decide_action()` based on risk signals (constitution, hindsight, simulation,
perspectives) and is not overwritten during assembly.

## Response Types (ResponseType)

| Type              | Description                           |
|-------------------|---------------------------------------|
| `DIRECT`          | Direct response without modifications |
| `WITH_CAVEAT`     | Response with disclaimer/warnings     |
| `PARTIAL_REFUSAL` | Refuse part, respond to rest          |
| `FULL_REFUSAL`    | Full refusal with explanation         |
| `REDIRECT`        | Suggest alternatives                  |

### Relationship with final_action

- `final_action == "NORMAL_COMPLETE"` → `response_type == DIRECT`
- `final_action == "SAFE_COMPLETE"` → `response_type == WITH_CAVEAT`
- `final_action == "REFUSE"` → `response_type` in (`FULL_REFUSAL`, `PARTIAL_REFUSAL`, `REDIRECT`)

---

## Programmatic Usage

```python
from moralstack.runtime.orchestrator import Orchestrator, OrchestratorConfig

# Initialize with all modules
orchestrator = Orchestrator(
    config=config,
    policy=policy_llm,
    risk_estimator=risk_estimator,
    critic=critic,
    simulator=simulator,
    hindsight=hindsight,
    perspectives=perspectives,
    constitution_store=constitution_store,
)

# Process request
result = orchestrator.process("How can I manage stress?")

# Access results
print(f"Type: {result.response.response_type}")
print(f"Content: {result.response.content}")
print(f"Cycles: {result.total_cycles}")
print(f"Path: {result.path_taken}")
print(f"Converged: {result.converged}")
```

---

## Output Structure

### OrchestratorResult

```python
@dataclass
class OrchestratorResult:
    response: FinalResponse  # Final response
    request_id: str  # Request UUID
    path_taken: str  # "fast" or "deliberative"
    total_cycles: int  # Deliberative cycles executed
    converged: bool  # True if convergence reached
    error: str | None  # Optional error message
```

### FinalResponse

```python
@dataclass
class FinalResponse:
    content: str  # Generative output only; never critic rationale/guidance
    response_type: ResponseType  # Response type
    metadata: ResponseMetadata  # Detailed metadata
    policy_overlay: PolicyOverlay | None = None  # Normative structure (principle_ids, caveat_type)
    meta_analysis: MetaAnalysis | None = None   # For audit; never in content
```

**Invariants**: Content purity (no critic text in content); language-agnostic (no hardcoded strings).

### ResponseMetadata

```python
@dataclass
class ResponseMetadata:
    risk_score: float  # Risk score [0, 1]
    deliberation_cycles: int  # Number of cycles (0 for FAST_PATH)
    hindsight_score: float  # Final hindsight score
    triggered_principles: list[str]  # Triggered principles
    processing_time_ms: int  # Processing time
    # Policy-driven (for benchmark and audit)
    final_action: str  # "REFUSE" | "SAFE_COMPLETE" | "NORMAL_COMPLETE"
    path: str  # "FAST_PATH" | "DELIBERATIVE_PATH"
    cycles: int  # 0 for FAST_PATH, otherwise deliberative cycles
    intent_clarity: str  # LOW | MEDIUM | HIGH (semantic signals)
    misuse_plausibility: str  # LOW | MEDIUM | HIGH
    actionability_risk: str  # LOW | MEDIUM | HIGH
```

**Construction**: Always build metadata via factory methods for consistency across paths (fast, deliberative, safe_complete, domain_excluded, system error). Use `ResponseMetadata.from_decision(...)` for flows that have a `Decision` (and optional `DecisionExplanation`); use `ResponseMetadata.for_system_error(...)`, `for_domain_excluded(...)`, or `for_fail_safe(...)` for timeout, domain-excluded, and FAIL_SAFE fallback respectively. See `docs/architecture_spec.md` (ResponseMetadata Construction) for the full list.

---

## Convergence invariants and logging

The deliberative loop uses an **explicit state model** (`ConvergenceOutcome`) and a central function
**`enforce_convergence_invariants`**:

- **cycle ≥ max_cycles** → `should_continue=False`, `stop_reason=CYCLES_EXHAUSTED` (no "continue" when cycles
  exhausted).
- **CONVERGED decision** → `should_continue=False`, `stop_reason=CONVERGED`.
- The loop exits only based on the post-enforcement outcome; `converged` in the result is that of the outcome.
- **Fast path (0 cycles)**: For runs with no deliberative cycles and `final_action` other than REFUSE, the FINAL
  trace is written with `stop_reason=CONVERGED` so that reports and UI show converged=True consistently.

**Structured log (JSON)** with `request_id` and `event`:

- `CONVERGENCE_RAW` – raw outcome before enforcement.
- `CONVERGENCE_ENFORCED` – final outcome (should_continue, stop_reason, cycle, max_cycles).
- `CONVERGENCE_EXIT` – loop exit (stop_reason, total_cycles, converged).
- `SAFE_COMPLETE_DOWNGRADED` – downgrade to NORMAL_COMPLETE (domain, reason).

In benchmark, `execution_trace[request_id]["events"]` may contain `CONVERGENCE_ENFORCED` and `SAFE_COMPLETE_DOWNGRADED`
for verification.

Modules: `moralstack.orchestration.convergence`, `moralstack.orchestration.safe_complete_gating`.

---

## Error Handling

The Orchestrator implements a fail-safe system:

- **Timeout**: If it exceeds `timeout_ms`, returns a safe default response
- **Module errors**: Catches exceptions and continues with fallback values
- **Parsing failures**: Automatic retries with reformulated prompts

```python
# Safe default response on error
FinalResponse.safe_default(processing_time_ms=elapsed)
```

---

## Environment Variables

All orchestrator tuning can be overridden via `.env`. Variables are read when building the `OrchestratorConfig`; empty
or missing values use the defaults below. See `.env.template` for the full list. **In application runs (CLI and
benchmark), orchestrator configuration is the single source of configuration — no CLI override.**

There is no dedicated model for the orchestrator (it is not an LLM module).

### Deliberation control

#### MORALSTACK_ORCHESTRATOR_MAX_DELIBERATION_CYCLES

- **Default**: `2`
- **Type**: int (>= 1)
- **Description**: Maximum number of deliberative cycle iterations.

#### MORALSTACK_ORCHESTRATOR_TIMEOUT_MS

- **Default**: `600000`
- **Type**: int (>= 1)
- **Description**: Total timeout in milliseconds (default 10 minutes).

#### MORALSTACK_ORCHESTRATOR_PARALLEL_MODULE_CALLS

- **Default**: `true`
- **Type**: bool
- **Description**: When true, the deliberation runner uses parallel execution for module calls within each cycle (see
  also `MORALSTACK_ORCHESTRATOR_PARALLEL_CRITIC_WITH_MODULES`). LLM calls are persisted (with run/request/cycle context)
  and appear in moralstack-ui; `MORALSTACK_ORCHESTRATOR_ENABLE_PERSPECTIVES`,
  `MORALSTACK_ORCHESTRATOR_ENABLE_SIMULATION`, and `MORALSTACK_ORCHESTRATOR_ENABLE_HINDSIGHT` determine which modules run
  and thus which calls are recorded and visible in the UI.

#### MORALSTACK_ORCHESTRATOR_PARALLEL_CRITIC_WITH_MODULES

- **Default**: `true`
- **Type**: bool
- **Description**: When `true` and `MORALSTACK_ORCHESTRATOR_PARALLEL_MODULE_CALLS` is `true`, the critic runs in parallel
  with the simulator and perspectives (full parallel evaluation). When `false`, the critic runs first as a gate; simulator
  and perspectives run in parallel only after the critic reports no hard violation. See
  [Latency-oriented parameters](#latency-oriented-parameters).

#### MORALSTACK_ORCHESTRATOR_ENABLE_SPECULATIVE_GENERATION

- **Default**: `true`
- **Type**: bool
- **Description**: When `true`, risk estimation and speculative first-pass draft generation run in parallel at the start
  of `process()`. The draft may be reused on benign, fast, and deliberative routes; it is not used for routing. When
  `false`, risk estimation runs alone, then generation follows the previous sequential pattern. See
  [Latency-oriented parameters](#latency-oriented-parameters).

### Risk thresholds (path routing)

#### MORALSTACK_ORCHESTRATOR_RISK_LOW_THRESHOLD

- **Default**: `0.3`
- **Type**: float (0–1)
- **Description**: Risk score below this takes the Fast Path.

#### MORALSTACK_ORCHESTRATOR_RISK_MEDIUM_THRESHOLD

- **Default**: `0.7`
- **Type**: float (0–1)
- **Description**: Risk score at or above this triggers full deliberation.

### Module enable flags

#### MORALSTACK_ORCHESTRATOR_ENABLE_PERSPECTIVES

- **Default**: `true`
- **Type**: bool
- **Description**: Enable the Perspective Ensemble module.

#### MORALSTACK_ORCHESTRATOR_ENABLE_SIMULATION

- **Default**: `true`
- **Type**: bool
- **Description**: Enable the Consequence Simulator module.

#### MORALSTACK_ORCHESTRATOR_ENABLE_HINDSIGHT

- **Default**: `true`
- **Type**: bool
- **Description**: Enable the Hindsight Evaluator module.

### Convergence and scoring

#### MORALSTACK_ORCHESTRATOR_NUM_SIMULATIONS

- **Default**: `3`
- **Type**: int (>= 1)
- **Description**: Number of simulation scenarios per cycle.

#### MORALSTACK_ORCHESTRATOR_MIN_HINDSIGHT_SCORE

- **Default**: `0.8`
- **Type**: float (0–1)
- **Description**: Minimum hindsight score for the deliberative cycle to converge.

#### MORALSTACK_ORCHESTRATOR_MAX_CRITICAL_VIOLATIONS

- **Default**: `0`
- **Type**: int (>= 0)
- **Description**: Maximum critical violations allowed before forcing refusal.

#### MORALSTACK_ORCHESTRATOR_EARLY_EXIT_HINDSIGHT_THRESHOLD

- **Default**: `0.6`
- **Type**: float (0–1)
- **Description**: Hindsight threshold for early exit from deliberation.

### Safety and error handling

#### MORALSTACK_ORCHESTRATOR_SAFE_RESPONSE_ON_ERROR

- **Default**: `true`
- **Type**: bool
- **Description**: When true, return a safe default response on error instead of raising.

#### MORALSTACK_ORCHESTRATOR_BORDERLINE_REFUSE_UPPER

- **Default**: `0.95`
- **Type**: float (0–1)
- **Description**: Upper bound for borderline REFUSE deliberation. REFUSE decisions with risk_score between
  `risk_thresholds.medium` and this value enter the deliberative pipeline.

### Gating and optimization

#### MORALSTACK_ORCHESTRATOR_SKIP_OPTIONAL_MODULES_THRESHOLD

- **Default**: `0.95`
- **Type**: float (0–1)
- **Description**: Risk score above which optional modules are skipped.

#### MORALSTACK_ORCHESTRATOR_SOFT_TIMEOUT_THRESHOLD

- **Default**: `0.90`
- **Type**: float (0–1)
- **Description**: Fraction of timeout_ms at which soft timeout warnings are triggered.

#### MORALSTACK_ORCHESTRATOR_ENABLE_THIN_MODE

- **Default**: `false`
- **Type**: bool
- **Description**: When true, use thin (truncated) drafts to reduce token usage.

#### MORALSTACK_ORCHESTRATOR_ENABLE_SIMULATOR_GATING

- **Default**: `false`
- **Type**: bool
- **Description**: When true, skip simulator in cycle 2+ when previous cycle was safe.

#### MORALSTACK_ORCHESTRATOR_ENABLE_HINDSIGHT_GATING

- **Default**: `true`
- **Type**: bool
- **Description**: When true (default), skip hindsight except in the final cycle to reduce time and tokens. Set to false to run hindsight in every cycle (legacy behavior).

#### early_exit_perspectives_threshold (OrchestratorConfig field, non-env)

- **Default**: `0.85`
- **Type**: float (0–1)
- **Description**: Weighted perspectives approval threshold for early exit when critic returns PROCEED (zero violations). When approval ≥ this value, the cycle converges without waiting for hindsight. Set to `1.0` to effectively disable this early-exit path. Respects perspective weights (`vulnerable` ×1.2, `compliance` ×1.1, `adversary` ×0.8).
- **Configuration source**: This is currently a dataclass config field (`OrchestratorConfig.early_exit_perspectives_threshold`) and is **not** loaded from `MORALSTACK_ORCHESTRATOR_*` environment variables.

#### MORALSTACK_ORCHESTRATOR_SIMULATOR_GATE_SEMANTIC_HARM_THRESHOLD

- **Default**: `0.4`
- **Type**: float (0–1)
- **Description**: Run simulator if previous `semantic_expected_harm` >= this threshold.

#### MORALSTACK_ORCHESTRATOR_SIMULATOR_GATE_DELTA_CHARS_THRESHOLD

- **Default**: `100`
- **Type**: int (>= 0)
- **Description**: Run simulator if draft changed by >= this many characters.

---

## See Also

- [Risk Estimator](./risk_estimator.md) - Risk classification
- [Constitutional Critic](./critic.md) - Constitutional validation
- [Consequence Simulator](./simulator.md) - Consequence simulation
- [Hindsight Evaluator](./hindsight.md) - Retrospective evaluation
- [Perspective Ensemble](./perspectives.md) - Multi-perspective analysis
