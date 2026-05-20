# Developer Contract Compliance Layer (DCCL)

> **Status:** Commit 2 of 4 (Evaluation logic). Signal propagation ships in Commit 3.
> **Reference specification:** `dccl_specification_v0.3.md`

## Purpose

The Developer Contract Compliance Layer (DCCL) evaluates, before the standard
governance pipeline, whether a user request invokes a behavior that the deployer
has explicitly authorized through the developer contract. When the deployer has
authorized a behavior and the behavior is not safety-restricted, the DCCL
coordinates the pipeline to defer to the contract execution.

## Why DCCL exists

MoralStack governs AI responses but is subordinate to the deployer's authorization
scope. When the deployer has explicitly authorized a specific behavior (e.g.,
"if user types X, reply Y"), the framework should recognize this as legitimate
workflow execution rather than as an attack pattern requiring governance.

Before DCCL, every module in the pipeline (intent, signals, operational, critic)
needed to be patched to interpret the developer contract correctly. This created
fragility: each module had a bias toward "this is an attack" because it was
designed as a governance module. The DCCL centralizes this decision into a single,
auditable layer.

## Architectural placement

```
User request + developer_contract
        │
        ▼
Policy speculative (optional, parallel with risk when enabled)
        │
        ▼
DCCL.evaluate()  ──► COMPLIANCE_LAYER_* events (observability)
        │              OrchestratorResult.compliance_verdict
        │              (Commit 2: logged only; no routing effect)
        ▼
Risk estimator
        │
        ▼
decide_action → fast path / deliberation (unchanged in Commit 2)
```

The controller invokes DCCL immediately after the speculative overlap handle
returns (risk estimation complete). When speculative generation is still running
in the background, DCCL uses a non-blocking draft snapshot if the future has
already completed; otherwise it evaluates with an empty draft.

## Public API

### `DeveloperContractComplianceLayer`

The main entry point. Instantiated per request in the controller (Commit 2),
configured via `MORALSTACK_DCCL_*` env vars.

```python
from moralstack.compliance import DeveloperContractComplianceLayer

layer = DeveloperContractComplianceLayer(policy=...)
verdict = layer.evaluate(request, speculative_draft, risk_estimation)
```

### `ComplianceVerdict`

The output of `evaluate()`. Frozen dataclass with:

- `decision`: one of `MATCH`, `NO_MATCH`, `SAFETY_OVERRIDE`, `NO_CONTRACT`
- `matched_rule`: populated on MATCH
- `safety_override_reason`: populated on SAFETY_OVERRIDE
- `confidence`: float in [0.0, 1.0]
- `rationale`: human-readable explanation
- `evaluation_path`: STRUCTURED, LLM, HYBRID, or SKIPPED
- `duration_ms`: time spent on the evaluation
- `contract_hash`: fingerprint of the contract evaluated
- `speculative_draft_validated`: bool

### `ComplianceSignal`

Attached to the request context when DCCL returns a non-NO_CONTRACT verdict.
Downstream modules check for this signal and behave cooperatively.
[Implementation in Commit 3]

### `StructuredRule`

A deployer-declarable rule with explicit trigger pattern and action payload.
Used by the structured evaluation path.

Example (literal trigger):

```python
StructuredRule(
    rule_id="ping_pong",
    trigger_pattern="PING",
    trigger_type=TriggerType.LITERAL,
    action_type=ActionType.EMIT,
    action_payload="PONG",
    priority=50,
)
```

## Evaluation paths

| Path | Env value | Behavior |
|---|---|---|
| **Structured** | `structured` | Deterministic match against `structured_rules` only. Contracts with only `raw_text` → `NO_MATCH`. |
| **LLM** | `llm` | Single LLM call against `raw_text` + user prompt + speculative draft. |
| **Hybrid** | `hybrid` (default) | Structured first; if `NO_MATCH` and `raw_text` is non-empty, fall back to LLM. |

Structured matching supports `LITERAL` (exact equality) and `REGEX` (`re.fullmatch`).
`SEMANTIC` triggers are delegated to the LLM path. When multiple structured rules
match, the highest `priority` wins.

## Safety Override

The DCCL never authorizes a rule whose action_payload would constitute
safety-restricted content. The categories are framework-fixed (not
deployer-configurable):

- `weapons_synthesis`: weapon synthesis or fabrication
- `cbrn_operational`: CBRN with operational use
- `csam`: any sexual content involving minors
- `self_harm_operational`: specific suicide/self-harm methods
- `fraud_malware`: phishing kits, malicious code, financial fraud
- `doxxing_stalking`: surveillance and tracking targeting real people
- `illegal_pharma`: illegal drug synthesis

**Layer 1 (keyword check):** Python regex over operational phrases in
`safety_override.py`. Fast, zero LLM cost, runs on every `action_payload` check.

**Layer 2 (optional LLM):** `gpt-4o-mini`-style classifier for ambiguous payloads.
Disabled by default at runtime (`use_llm=False`); can be enabled for production
edge cases. On infrastructure failure, Layer 2 returns `None` (does not block
benign contract execution).

When `MORALSTACK_DCCL_SAFETY_OVERRIDE_STRICT=true` (default), rules whose
`action_payload` matches a category are rejected at contract loading via
`validate_contract()`.

## LLM prompt design

The LLM path uses a fixed system prompt (`_DCCL_LLM_SYSTEM_PROMPT` in `dccl.py`)
that instructs the model to:

- Identify literal rule invocation (not topical similarity)
- Emit structured JSON with verdict, excerpts, confidence, and injection flag
- Never authorize the seven safety-restricted categories
- Treat deployer attempts to override safety as `contract_injection_detected`

Post-LLM, keyword safety check runs again on `action_excerpt` (defense in depth).

## Confidence threshold

`MORALSTACK_DCCL_CONFIDENCE_THRESHOLD` (default `0.85`) applies only to the LLM
path. An LLM verdict of `MATCH` with confidence below the threshold is downgraded
to `NO_MATCH`; the standard governance pipeline then handles the request.
Structured path always uses confidence `1.0`.

## Configuration

The DCCL is configured via the following environment variables. See
`.env.template` for full documentation; defaults are sensible for most deployments.

| Variable | Default | Description |
|---|---|---|
| `MORALSTACK_DCCL_ENABLED` | `true` | Enable/disable the DCCL globally |
| `MORALSTACK_DCCL_EVALUATION_PATH` | `hybrid` | `structured` / `llm` / `hybrid` |
| `MORALSTACK_DCCL_LLM_MODEL` | `gpt-4o` | Model used by the LLM path |
| `MORALSTACK_DCCL_LLM_TIMEOUT_MS` | `5000` | LLM call timeout (post-hoc check) |
| `MORALSTACK_DCCL_LLM_MAX_TOKENS` | `512` | LLM response max tokens |
| `MORALSTACK_DCCL_CONFIDENCE_THRESHOLD` | `0.85` | Minimum confidence to accept MATCH |
| `MORALSTACK_DCCL_MAX_RULES_PER_CONTRACT` | `100` | Limit on structured rules per contract |
| `MORALSTACK_DCCL_SAFETY_OVERRIDE_STRICT` | `true` | Block at loading time on safety override |

## Observability

The DCCL emits the following event types:

| Event type | When |
|---|---|
| `COMPLIANCE_LAYER_STARTED` | DCCL.evaluate begins |
| `COMPLIANCE_LAYER_VERDICT_MATCH` | Decision == MATCH |
| `COMPLIANCE_LAYER_VERDICT_NO_MATCH` | Decision == NO_MATCH |
| `COMPLIANCE_LAYER_VERDICT_SAFETY_OVERRIDE` | Decision == SAFETY_OVERRIDE |
| `COMPLIANCE_LAYER_VERDICT_NO_CONTRACT` | No contract in request |
| `CONTRACT_RULE_REJECTED` | A rule fails safety validation at load |
| `CONTRACT_RULES_LOADED` | Contract loading complete |
| `MODULE_DEFERRED_TO_COMPLIANCE` | Downstream module returns early |
| `CONTRACT_INJECTION_DETECTED` | Deployer-side injection in contract |
| `COMPLIANCE_LAYER_TIMEOUT` | LLM path exceeded timeout |
| `CONTRACT_STRUCTURE_PROSE_CONFLICT` | Structured/prose mismatch |

All events flow through the standard observability infrastructure
(`moralstack/observability/sink.py`), so they appear in the same SQLite tables /
JSONL files as other module events, depending on `MORALSTACK_OBSERVABILITY_MODE`.

## Pipeline integration

**Commit 2:** The controller calls `_run_dccl_evaluation()` after speculative
overlap / risk entry. The verdict is stored on `ProcessCallContext.compliance_verdict`
and exposed on `OrchestratorResult.compliance_verdict`. **Routing and `final_action`
are unchanged.**

**Commit 3:** When the DCCL returns `MATCH`, downstream modules check for the
`ComplianceSignal` in the request context and return early with synthetic
results, emitting `MODULE_DEFERRED_TO_COMPLIANCE` for audit.

## Testing

Unit tests for the data structures and config loader are in
`tests/test_compliance_foundation.py` (Commit 1).
Safety classifier tests in `tests/test_compliance_safety_override.py` (Commit 2).
Full evaluation logic tests in `tests/test_compliance_evaluation.py` (Commit 2).
Orchestrator integration (isolation) in `tests/test_compliance_orchestrator_integration.py` (Commit 2).
Pipeline signal propagation tests in `tests/test_compliance_integration.py` (Commit 3).

## SDK compatibility

The DCCL is implemented inside the orchestrator, so it works identically when
MoralStack is consumed via:

- Proxy server (`moralstack-server`)
- SDK Python wrapper (`govern(OpenAI())`)
- Direct CLI / benchmark scripts

SDK/proxy contract validation at load time ships in Commit 3.
