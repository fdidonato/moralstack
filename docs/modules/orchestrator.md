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
- Developer Contract Compliance Layer (DCCL) evaluation and optional compliance fast-path
- Initial risk estimation (parallel with speculative generation when enabled)
- Routing between **Fast Path**, **Compliance Fast Path**, and **Deliberative Path**
- Deliberative cycle coordination
- Guidance aggregation from modules
- Final response assembly

**Conversation linkage (optional, foundation only)**: `OrchestrationController.process` / `Orchestrator.process` accept
optional keyword-only arguments `conversation_id`, `turn_index`, `parent_request_id`, and `conversation_state`
(`ConversationGovernanceState`). When provided, metadata is persisted on the request row and echoed on
`OrchestratorResult`; they do not change routing or `decide_action` when omitted. See `moralstack/orchestration/conversation_state.py`.

**Request transcript context**: SDK and proxy attach
`moralstack.orchestration.conversation_context.ConversationContext` to
`ProcessedRequest`. It is additive and dormant when absent. DCCL and speculative
generation use its role-serialized transcript when prior turns exist; risk keeps
its smaller declared last-3 context. The result records context-shape fields such
as `history_source`, `prior_turn_count`, `governance_context_mode`,
`candidate_context_mode`, and the delivery mismatch guard outcome.

**Per-call context under concurrency:** `OrchestrationController.process()` constructs a stack-local `ProcessCallContext` (`moralstack/orchestration/process_context.py`) and passes it to internal helpers (`_apply_conversation_metadata_to_result`, ledger follow-up, canonical conversation events). A single controller instance may handle overlapping `process()` calls (for example from the HTTP proxy threadpool); conversation linkage and ledger intent scratch fields must never live on `self` for that reason.

When `OrchestrationController` is constructed with a non-`None` `SemanticDecisionLedger` and `process(..., conversation_id=...)`
is used, the controller consults the ledger after `decide_action` and initial routing. On a semantic cache hit,
`ConversationalFastPathRunner` (`moralstack/orchestration/conversational_fast_path.py`) may patch `Decision` and the
string route to skip deliberation only when a conservative gate allows (cached `REFUSE`, or current route already
non-deliberative). Response text is never read from the ledger (DAF-4); only governance metadata is reused.

`moralstack/orchestration/system_prompt_resolver.py` exposes `effective_system_for_request(...)`, composing the policy system prompt per request from the protected base, optional non-empty `DeveloperContract.raw_text`, and an optional mode suffix (`normal`, `safe_complete`, `constrained`). When no contract text is present, output matches the legacy single-turn byte strings. The suffix modes remain available for other call sites; **`DeliberationRunner` does not use `safe_complete` or `constrained` resolver modes for policy generation** (see below).

**Governed delivery (Plan 1)**: `moralstack/orchestration/delivery.py`
(`finalize_delivery` → `GovernedDelivery`) is the pure finalizer that turns an
already-governed `OrchestratorResult` into the exact text to deliver. SDK and
proxy serialize that text directly; the wrapped/upstream client is never called
to generate a delivered answer. Blank/invalid governed content fails closed to a
deterministic governed refusal (`governed_pipeline_refusal`). The finalizer makes
no model call and performs no I/O.

`moralstack/orchestration/final_revalidation.py` was the pre-Plan-1 shared
final-output gate that validated **upstream-generated** text against the
developer contract. It is **no longer invoked on the active delivery paths**
(delivery is governed-only) and is retained for historical UI/report readers.

**Compliance delivery guard**: On DCCL `MATCH`, a validated speculative draft can
be reused only if its candidate context is aligned with the governance context.
If prior turns exist and governance saw a full role-serialized/native transcript
but the candidate draft came from last-user-only context, the controller forces
Case 2 regeneration or downgrades to the standard pipeline. This guard is
strictly conservative: it can prevent fast-path draft reuse, but it cannot loosen
routing or override Safety Override / hard-signal refusal.

**Governance prompt placement (v0.4 Step 10)**:

- **Internal policy (`DeliberationRunner`)**: `SAFE_COMPLETE_GENERATION_INSTRUCTION` and `CONSTRAINED_GENERATION_INSTRUCTION` from `moralstack/orchestration/_policy_helpers.py` are **prefixed onto the user-facing prompt** passed to `policy.generate` / `policy.rewrite`. The system string passed to the policy LLM is still composed with `effective_system_for_request(..., mode="normal")` (contract overlay only, no governance suffix from those constants).
- **Python SDK (`moralstack/sdk/wrapper.py`)**: Plan 1 governed delivery — the SDK delivers the governed pipeline text for every `final_action` and never calls the wrapped client to generate the answer. The SAFE_COMPLETE governance guidance is composed inside the governed pipeline (via the policy prompt above), not appended to a wrapped-client call. (`_build_safe_complete_user_turn` is retained as a pure helper but is no longer applied to delivery.)

**Refusal context** (`moralstack/orchestration/refusal_context.py`):

- `build_refusal_context(...)` produces a frozen `RefusalContext` for refusal LLM calls. Optional keyword arguments `developer_contract` and `conversation_history` populate bounded snippets `developer_contract_summary` and `conversation_history_snippet`.
- `classify_refusal_focus` implements priority order **P0–P6** (documented in the source docstring): **P0** hard topical signals are never overridden by deployer contract hints; **P1** applies only when `DeveloperContract.mode == "structured"` and redirection keywords appear in `raw_text`; domain overlay redirection continues to integrate via `refusal_redirection` as before.

In v0.4 foundations, `ConversationGovernanceState` includes additive fields for future multi-turn routing:
`last_developer_contract_hash`, `last_governance_posture`, and `turn_decisions_summary` (tuple of
`TurnDecisionSummary`). `should_full_refresh(*, current_turn: TurnContext | None = None)` preserves legacy behavior
(`should_full_refresh()` without arguments remains conservative and returns `True`) and enables forward-compatible
rule evaluation when `current_turn` is provided.

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

Both entry points also accept an optional `request_analysis:
RequestAnalysisContext | None` (unify-constitution-retrieval-single-pass): the
controller's risk-owned single constitution-retrieval wave, lifted from
`RiskEstimation` and passed down so the runner never retrieves a second time.
It is **authoritative even when `relevant_principles` is empty** — a
successful zero-principle retrieval is a valid result, not a degraded one; only
when `request_analysis is None` (the risk-owned retrieval was unavailable —
no risk estimator / no store / retrieval raised) does the runner fall back to
its own retrieval (`_try_build_request_analysis_context`) and emit
`RELEVANT_PRINCIPLES_RETRIEVED` itself (the controller already emitted it on
the success path, so the two emit sites are mutually exclusive). The critic
reuse gate (`_critique`) checks `request_analysis is not None`, never
`len(relevant_principles) > 0`, and slices the reused list to
`retrieval_top_k_for_request()` (the critic's own configured top_k) so it is
never widened beyond its bound even when the unified retrieval top_k
(`max(risk_top_k, critic_top_k)`) was larger. `run_fast_path` forwards the same
context to `critic.quick_check(..., pre_retrieved_principles=...)` and to the
quick-check-failed `run_deliberative_path` escalation, so FAST_PATH and its
deliberative fallback also reuse the single wave (see
`docs/modules/critic.md` and `docs/TRACES/governance_decision_flow.md` §3).

### Supporting modules (orchestration)

`DeliberationRunner` delegates to dedicated modules to keep responsibilities separated:

- **guidance_builder** — Builds aggregated guidance string from critic, perspectives, hindsight, and simulator state (`build_aggregated_guidance(state, *, filter_marginal=True, telemetry=None)`). Applies **signal-strength filtering** by default (`filter_marginal=True`): critic guidance is included only when violations exist or critic decision is `REVISE`/`REFUSE`; perspective suggestions only from dissatisfied perspectives (`approval_score` < 0.75) and only when weighted approval < 0.85; hindsight only when a real hindsight signal exists and `hindsight_score` < 0.7; simulator only when `semantic_expected_harm` ≥ 0.35. Hard violations bypass all filters. When all modules are satisfied, guidance returns empty and the rewrite is skipped (draft from the previous cycle is preserved). Emits `rewrite (SKIPPED_EMPTY_GUIDANCE)` in persisted LLM call rows when the rewrite is skipped; logs `guidance_filter:` lines at INFO. Also emits `AGGREGATED_GUIDANCE_EVALUATED` orchestration events for the request detail UI.
- **convergence_evaluator** — Evaluates whether the deliberation has converged and which `DecisionType` to apply (`ConvergenceEvaluator(config).determine_decision(state, risk_estimation=None)`). **Cycle 1** may stop early only via a conservative `_evaluate_cycle1_early_convergence` gate (stricter than the legacy cycle>=2 weighted-perspectives path); rejection is explicit and observable. Invariants and structured logging for the loop remain in **convergence.py** (`enforce_convergence_invariants`, `log_convergence_event`). Observability: `CONVERGENCE_EVALUATED`, `EARLY_CONVERGENCE_ACCEPTED`, `EARLY_CONVERGENCE_REJECTED`; `CYCLE_SUMMARY` includes `early_convergence_considered`, `early_convergence_accepted`, `convergence_reason_codes`, `deliberation_decision`.
- **language_resolver** — Resolves explicit language and builds prompt with language prefix (`resolve_prompt_with_language(prompt, detected_iso, fallback_prompt)`), reusing logic from `safe_refusal_generator` and `_policy_helpers`.
- **persistence_helpers** — Centralizes optional diagnostics logging and LLM call persistence (`record_llm_call(logger, diagnostics_payload, persist_kwargs)`). Every `llm_calls` row that represents a **real provider call** must carry its generating `model` (the fast-path/benign/safe-complete `generate (...)`, the REFUSE `refuse (fast_path)`/`(deliberative)`, `generate (compliance-regenerate)`, `draft_revalidate` with the DCCL model, and the module `retry_failed_attempt_*` rows all pass it explicitly). An unset `model` collapses into the unattributed `'—'` row of the per-model token panel. Synthetic/diagnostic rows emitted here that are **not** provider calls (module `SKIPPED`/`GATED`/`DISABLED`/`ERROR` markers, `timeout_warning`, `output_protection` leakage, speculative reuse) carry `billable_provider_call=False` and intentionally leave `model` empty.

### Hindsight path diagnostics

`DeliberationRunner._apply_hindsight_if_needed` and `_evaluate_hindsight` emit structured events via `orch_debug_log` with `hypothesisId` `H-hindsight-path` and payload `component: hindsight_diagnostic`. The `outcome` field records the branch taken, for example: `disabled_by_config`, `skipped_no_module`, `gated_not_final_cycle`, `invoke_evaluate`, `evaluate_ok`, `evaluate_aborted_timeout_guard`, `evaluate_failed`, `evaluate_failed_orchestrator_timeout`.

Observability routing follows **`MORALSTACK_OBSERVABILITY_MODE`** (see `moralstack/observability/config.py`): **`db_only`** inserts into SQLite `debug_events`; **`dual`** writes both DB and `logs/observability/debug.event.jsonl`; **`file_only`** writes the JSONL file only. The legacy alias `MORALSTACK_PERSIST_MODE` is still accepted with a deprecation warning. The same events appear in the root logger at **INFO** as `hindsight_diagnostic outcome=...`.

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
| `cycle1_early_convergence_min_weighted_approval` | 0.78 | Minimum weighted perspectives approval for cycle-1 early convergence. Perspectives with weights: vulnerable ×1.2, compliance ×1.1, adversary ×0.8, user/observer ×1.0. |
| `cycle1_early_convergence_max_semantic_harm` | 0.35 | Maximum simulator `semantic_expected_harm` for cycle-1 early convergence (when simulation is enabled). Queries with harm above this threshold always get cycle 2. |
| `cycle1_early_convergence_min_per_perspective_approval` | 0.70 | Minimum `approval_score` for each individual perspective. If any single perspective scores below this, cycle 2 is forced. |
| `parallel_critic_with_modules`         | `True`  | Legacy static fork when `enable_dynamic_parallel_scheduler` is `False` or risk estimation is unavailable: when `True` and `parallel_module_calls` is `True`, critic runs in parallel with simulator and perspectives. See [Latency-oriented parameters](#latency-oriented-parameters). |
| `enable_dynamic_parallel_scheduler`   | `True`  | When `True` and `parallel_module_calls` is `True`, each cycle selects `critic_gated` vs `full_parallel` from risk posture (execution only; governance unchanged). See [Latency-oriented parameters](#latency-oriented-parameters). |
| `enable_speculative_generation`        | `True`  | When `True`, risk estimation and speculative draft generation run in parallel before routing. The draft is reused on benign, fast, and deliberative routes when applicable. See [Latency-oriented parameters](#latency-oriented-parameters). |

### Latency-oriented parameters

These flags reduce wall-clock latency **without changing routing policy** (`decide_action`, `get_route`, overlay floors, or convergence invariants). They do not change how `final_action` is computed from risk and module outputs.

**`enable_dynamic_parallel_scheduler` (default `True`)**

- **Requires** `parallel_module_calls=True`. When both are `True`, `DeliberationRunner` chooses **`critic_gated`** (critic first; simulator and perspectives only if no hard violation) vs **`full_parallel`** (critic, simulator, perspectives concurrently) **per cycle** using existing risk fields (`risk_category`, `operational_risk`, `intent_to_harm`, `requested_instructions`, `risk_policy_action`, prior-cycle hard critiques). Emits `PARALLEL_STRATEGY_SELECTED` and enriches `CYCLE_SUMMARY` traces (`scheduler_reason_codes`, `critic_short_circuit`).
- **When `critic_gated` short-circuits** (hard violation after critic): simulator and perspectives are not invoked; emits `CRITIC_SHORT_CIRCUIT_TRIGGERED`.
- **When `False`**: the runner uses the legacy **`parallel_critic_with_modules`** boolean alone to choose the fork (same behavior as before dynamic scheduling).

**`parallel_critic_with_modules` (default `True`)**

- **Requires** `parallel_module_calls=True`. When both are `True`, each deliberation cycle runs **critic**, **simulator**, and **perspectives** concurrently (three parallel LLM calls per cycle when those modules are enabled).
- **When `False`**: the runner uses a two-stage layout: critic runs first; only if there is no hard violation do simulator and perspectives run in parallel. This avoids paying for simulator/perspective calls when the critic would reject the draft, but adds sequential critic latency before sim/persp start.
- **Hard violations**: If the critic reports a hard violation, simulator and perspective results from that cycle are **discarded** and do not affect merged state. Convergence and refusal logic see the same critic outcome as in the gated layout; you may pay extra token cost in the rare hard-violation case.
- **Set to `false`** if you prioritize minimizing LLM spend on hard-violation paths over latency.

**`enable_speculative_generation` (default `True`)**

- **When `True`**: `OrchestrationController` starts **risk estimation** and a **speculative policy `generate`** (same base system prompt as normal first-pass generation) in parallel. After risk returns, routing proceeds as usual; the speculative draft is **not** used for policy routing decisions.
- **Reuse**: On benign fast path, fast path, and deliberative path, the draft is reused when it is still valid (skipping a duplicate first `generate` where implemented). On **REFUSE**, the speculative call is unused (wasted latency/token trade-off). **`SAFE_COMPLETE`** path does not reuse this draft (separate policy call with SAFE_COMPLETE governance prefixed on the user prompt).
- **Constrained generation** (`CLEARLY_HARMFUL` deliberation): the speculative draft is **not** applied as cycle-1 output; constrained governance text is supplied as a **prompt prefix** (not via `mode="constrained"` on the system resolver).
- **Note**: Speculative generation uses language resolution **before** the risk estimator’s `detected_language` is available (fallback path). Routing and safety decisions are unchanged; draft wording may differ slightly from a strictly sequential generate-after-risk for the same request.

**`enable_simulator_gating` (default `false`)**

- **Requires** `enable_simulation=True` and an available simulator module. When `true`, each cycle evaluates a conservative
  simulator gate (see env vars `MORALSTACK_ORCHESTRATOR_SIMULATOR_GATE_*`). Cycle 1 always runs the simulator; cycle 2+
  may skip only when prior harm is very low, the draft has not materially changed, risk posture is not elevated, and
  (when the critic has already run) the critic is clean. **Full parallel** scheduling uses a stricter prior-only
  check before the critic runs because the critic is not visible to the gate yet.
- **Observability**: `SIMULATOR_GATE_DECISION`, `SIMULATOR_EXECUTED`, `SIMULATOR_SKIPPED`; `CYCLE_SUMMARY` includes
  `simulator_ran_this_cycle`, `simulator_gate_reason_codes`, and `simulator_carry_forward`.

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

**Typical latency** (84-question benchmark, run 12): overall deliberative workload
**mean ~36s**, **median ~26s**; SAFE_COMPLETE **one-cycle** subset **~23s** mean;
two-cycle SAFE_COMPLETE is higher per query. Sensitive-domain queries often sit at
the upper end of the range. Fast path averages ~12s (~37% of queries, including REFUSE).

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
    decision_correctness: dict[str, Any] | None  # optional DCF payload from diagnostics
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

#### MORALSTACK_ORCHESTRATOR_ENABLE_DYNAMIC_PARALLEL_SCHEDULER

- **Default**: `true`
- **Type**: bool
- **Description**: When `true` and `MORALSTACK_ORCHESTRATOR_PARALLEL_MODULE_CALLS` is `true`, each deliberation cycle
  selects `critic_gated` vs `full_parallel` from existing risk posture signals (execution scheduling only). When `false`,
  the static fork uses only `MORALSTACK_ORCHESTRATOR_PARALLEL_CRITIC_WITH_MODULES`. See
  [Latency-oriented parameters](#latency-oriented-parameters).

#### MORALSTACK_ORCHESTRATOR_PARALLEL_CRITIC_WITH_MODULES

- **Default**: `true`
- **Type**: bool
- **Description**: Legacy static fork when `MORALSTACK_ORCHESTRATOR_ENABLE_DYNAMIC_PARALLEL_SCHEDULER` is `false` or risk
  estimation is unavailable: when `true` and `MORALSTACK_ORCHESTRATOR_PARALLEL_MODULE_CALLS` is `true`, the critic runs in parallel
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

#### MORALSTACK_ORCHESTRATOR_ENABLE_SIMULATOR_GATING

- **Default**: `false`
- **Type**: bool
- **Description**: When `true`, `DeliberationRunner` may **skip** re-running the simulator in cycle 2+ only when conservative
  evidence supports it (low prior `semantic_expected_harm`, stable draft, clean critic when available, low risk posture).
  Emits `SIMULATOR_GATE_DECISION`, `SIMULATOR_EXECUTED`, and `SIMULATOR_SKIPPED` and enriches `CYCLE_SUMMARY` traces.
  When `false`, the simulator always runs (unless disabled or unavailable).

#### MORALSTACK_ORCHESTRATOR_ENABLE_HINDSIGHT_GATING

- **Default**: `true`
- **Type**: bool
- **Description**: When true (default), skip hindsight except in the final cycle to reduce time and tokens. Set to false to run hindsight in every cycle (legacy behavior).

#### early_exit_perspectives_threshold (OrchestratorConfig field, non-env)

- **Default**: `0.85`
- **Type**: float (0–1)
- **Description**: Weighted perspectives approval threshold for early exit when critic returns PROCEED (zero violations). When approval ≥ this value, the cycle converges without waiting for hindsight. Set to `1.0` to effectively disable this early-exit path. Respects perspective weights (`vulnerable` ×1.2, `compliance` ×1.1, `adversary` ×0.8).
- **Configuration source**: This is currently a dataclass config field (`OrchestratorConfig.early_exit_perspectives_threshold`) and is **not** loaded from `MORALSTACK_ORCHESTRATOR_*` environment variables.

#### MORALSTACK_ORCHESTRATOR_CYCLE1_EARLY_CONVERGENCE_MIN_WEIGHTED_APPROVAL

- **Default**: `0.78`
- **Type**: float (0–1)
- **Description**: Minimum weighted perspectives approval for cycle-1 early convergence. When all cycle-1 gates pass
  (critic clean, no hard violations, risk posture not elevated, perspectives aligned, simulator harm low), the
  deliberation stops after 1 cycle. This threshold is stricter than `early_exit_perspectives_threshold` (0.85) because
  it applies without hindsight confirmation. Set to `1.0` to effectively disable cycle-1 early convergence.

#### MORALSTACK_ORCHESTRATOR_CYCLE1_EARLY_CONVERGENCE_MAX_SEMANTIC_HARM

- **Default**: `0.35`
- **Type**: float (0–1)
- **Description**: Maximum simulator `semantic_expected_harm` allowed for cycle-1 early convergence. Queries where the
  simulator reports harm above this threshold always proceed to cycle 2 for additional review. Only evaluated when
  `enable_simulation=True` and the simulator produced results. Set to `0.0` to require zero simulated harm for early
  convergence.

#### MORALSTACK_ORCHESTRATOR_CYCLE1_EARLY_CONVERGENCE_MIN_PER_PERSPECTIVE_APPROVAL

- **Default**: `0.70`
- **Type**: float (0–1)
- **Description**: Minimum `approval_score` required from each individual perspective for cycle-1 early convergence.
  If any single perspective (user, vulnerable, observer, adversary, compliance) scores below this, cycle 2 is forced.
  This prevents early convergence when one perspective is dissatisfied even if the weighted average is high.

#### MORALSTACK_ORCHESTRATOR_REGULATED_INFORMATIONAL_NORMAL_COMPLETE

- **Default**: `false`
- **Type**: bool
- **Description**: Opt-in. When `true`, a clearly benign, non-operational informational request in a sensitive
  overlay (e.g. `financial`, `legal`, `research`) recovers to `NORMAL_COMPLETE` in `decision_service._handle_informational_recovery`
  instead of being floored to `SAFE_COMPLETE`. Applies the same benignity guards as the unregulated branch
  (no ambiguity/dual-use, no `requested_instructions`, no `intent_to_harm`, no operational intent, and pre-policy
  action ≠ `SAFE_COMPLETE`); any positive signal keeps `SAFE_COMPLETE` (`regulated_but_informational`). Does not
  affect `SENSITIVE`/`MORALLY_NUANCED`/`POTENTIALLY_HARMFUL`/`CLEARLY_HARMFUL` categories or hard-signal REFUSE.
  Default `false` preserves the current regulated → `SAFE_COMPLETE` behavior.

#### MORALSTACK_ORCHESTRATOR_SIMULATOR_GATE_SEMANTIC_HARM_THRESHOLD

- **Default**: `0.4`
- **Type**: float (0–1)
- **Description**: Run simulator if previous `semantic_expected_harm` >= this threshold.

#### MORALSTACK_ORCHESTRATOR_SIMULATOR_GATE_DELTA_CHARS_THRESHOLD

- **Default**: `100`
- **Type**: int (>= 0)
- **Description**: Run simulator if draft changed by >= this many characters.

#### MORALSTACK_ORCHESTRATOR_SIMULATOR_GATE_SKIP_MAX_PRIOR_SEMANTIC_HARM

- **Default**: `0.25`
- **Type**: float (0–1)
- **Description**: Conservative skip is allowed only when the previous cycle’s `semantic_expected_harm` is **strictly
  below** this value (and other gates pass). Values in `[skip_max, simulator_gate_semantic_harm_threshold)` still
  require a simulator run (borderline band).

---

## See Also

- `moralstack/orchestration/refusal_context.py` — `RefusalContext`, `build_refusal_context`, `classify_refusal_focus` (summarized under **Refusal context** in Overview above)
- [Risk Estimator](./risk_estimator.md) - Risk classification
- [Constitutional Critic](./critic.md) - Constitutional validation
- [Consequence Simulator](./simulator.md) - Consequence simulation
- [Hindsight Evaluator](./hindsight.md) - Retrospective evaluation
- [Perspective Ensemble](./perspectives.md) - Multi-perspective analysis
