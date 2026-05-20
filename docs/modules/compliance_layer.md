# Developer Contract Compliance Layer (DCCL)

> **Status:** Commit 1 of 4 (Foundation). Functional evaluation ships in Commit 2.
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

[diagram and detailed description — implementation in Commit 2]

## Public API

### `DeveloperContractComplianceLayer`

The main entry point. Instantiated once per orchestrator, configured via
`MORALSTACK_DCCL_*` env vars.

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

[Detailed examples — Commit 2]

## Configuration

The DCCL is configured via the following environment variables. See
`.env.template` for full documentation; defaults are sensible for most deployments.

| Variable | Default | Description |
|---|---|---|
| `MORALSTACK_DCCL_ENABLED` | `true` | Enable/disable the DCCL globally |
| `MORALSTACK_DCCL_EVALUATION_PATH` | `hybrid` | `structured` / `llm` / `hybrid` |
| `MORALSTACK_DCCL_LLM_MODEL` | `gpt-4o` | Model used by the LLM path |
| `MORALSTACK_DCCL_LLM_TIMEOUT_MS` | `5000` | LLM call timeout |
| `MORALSTACK_DCCL_LLM_MAX_TOKENS` | `512` | LLM response max tokens |
| `MORALSTACK_DCCL_CONFIDENCE_THRESHOLD` | `0.85` | Minimum confidence to accept MATCH |
| `MORALSTACK_DCCL_MAX_RULES_PER_CONTRACT` | `100` | Limit on structured rules per contract |
| `MORALSTACK_DCCL_SAFETY_OVERRIDE_STRICT` | `true` | Block at loading time on safety override |

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

[Detailed semantics — Commit 2]

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

[Implementation in Commit 3]

When the DCCL returns `MATCH`, downstream modules check for the
`ComplianceSignal` in the request context and return early with synthetic
results, emitting `MODULE_DEFERRED_TO_COMPLIANCE` for audit.

## Testing

Unit tests for the data structures and config loader are in
`tests/test_compliance_foundation.py` (Commit 1).
Full evaluation logic tests in `tests/test_compliance_evaluation.py` (Commit 2).
Pipeline integration tests in `tests/test_compliance_integration.py` (Commit 3).

## SDK compatibility

The DCCL is implemented inside the orchestrator, so it works identically when
MoralStack is consumed via:

- Proxy server (`moralstack-server`)
- SDK Python wrapper (`govern(OpenAI())`)
- Direct CLI / benchmark scripts

[Detailed SDK integration — Commit 3]
