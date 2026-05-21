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
        │
        ├── MATCH + speculative_draft_validated ──► COMPLIANCE_FAST_PATH
        │         (NORMAL_COMPLETE from speculative draft; modules deferred)
        │
        └── NO_MATCH / SAFETY_OVERRIDE / NO_CONTRACT ──► standard pipeline
        │
        ▼
Risk estimator (score still computed; may not drive final_action on MATCH)
        │
        ▼
decide_action → fast path / deliberation
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
- `speculative_draft_validated`: bool — whether the speculative draft delivers the
  authorized action (only meaningful on MATCH)
- `draft_match_method`: `""` | `"substring"` | `"semantic"` | `"none"` — how draft
  validation succeeded (or `"none"` when not validated)
- `draft_match_confidence`: float in [0.0, 1.0] — semantic draft-match confidence from
  the LLM verdict JSON (`draft_match_confidence`); `0.0` when validation used the
  substring pre-check or did not validate
- `degraded`: bool — `True` when the verdict is preserved but quality gates were not
  fully met (soft timeout exceeded or verdict confidence below threshold)
- `degraded_reason`: `""` | `llm_timeout` | `low_confidence`

### `ComplianceSignal`

Attached to the request context when DCCL returns a non-NO_CONTRACT verdict.
Commit 3 implements the routing effect via a controller-level compliance fast-path
rather than per-module early-return; downstream modules are not invoked on MATCH.
A `MODULE_DEFERRED_TO_COMPLIANCE` orchestration event is emitted for each skipped
module (audit).

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

### Speculative draft validation

Draft validation is centralized in `validate_draft_against_action()` (`dccl.py`).

| Path | Mechanism |
|---|---|
| **Structured** | Substring check via `validate_draft_against_action(action_payload, draft, None, threshold)`. On success, `draft_match_method="substring"`. |
| **LLM** | Same helper after the verdict call: substring pre-check on `action_excerpt`, then semantic fields `draft_matches_action` / `draft_match_confidence` from the verdict JSON when the excerpt is absent from the draft. |

### Controller routing (Cases 1–3)

On **MATCH**, the controller applies a three-case state machine before deliberation:

| Case | Condition | Action | Event |
|---|---|---|---|
| **1** | `speculative_draft_validated=True`, `degraded=False`, non-empty draft | Reuse draft → `COMPLIANCE_FAST_PATH` | `COMPLIANCE_DRAFT_REUSED` |
| **2** | Case 1 false (unvalidated draft, empty draft, or `degraded=True`) | `_regenerate_for_contract()` + `_revalidate_draft()`; on success → fast-path | `COMPLIANCE_DRAFT_REGENERATED` |
| **3** | Case 2 revalidation fails | Continue standard pipeline (deliberation) | `COMPLIANCE_MATCH_DOWNGRADED` |

Case 2 covers degraded MATCH (`llm_timeout`, `low_confidence`) and missing or wrong
speculative drafts (timing, paraphrase, or absent draft). Revalidation uses substring
first; a targeted semantic LLM call runs only when substring fails.

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
- Emit structured JSON with verdict, excerpts, rationale, and confidence
- When a speculative draft is present in the user prompt, also emit
  `draft_matches_action` and `draft_match_confidence` judging whether the draft
  semantically delivers `action_excerpt` (paraphrase allowed; not required to be
  verbatim). If no draft was provided, set `draft_matches_action=false` and
  `draft_match_confidence=0.0`
- Never authorize the seven safety-restricted categories

Post-LLM, keyword safety check runs again on `action_excerpt` (defense in depth).
Draft validation uses the substring pre-check first; semantic fields are consulted
only when the verbatim excerpt is absent from the draft.

## Confidence threshold

`MORALSTACK_DCCL_CONFIDENCE_THRESHOLD` (default `0.85`) applies to the LLM path in
two places:

1. **Verdict confidence** — an LLM `MATCH` with `confidence` below the threshold stays
   `MATCH` with `degraded=True` and `degraded_reason=low_confidence` (not invalidated
   to `NO_MATCH`). Downstream routing may treat degraded MATCH differently (see controller).
2. **Draft semantic match** — on MATCH, `draft_matches_action=true` is accepted only
   when `draft_match_confidence` is also at or above the threshold (substring matches
   bypass this gate).

Structured path always uses verdict confidence `1.0`.

## Configuration

The DCCL is configured via the following environment variables. See
`.env.template` for full documentation; defaults are sensible for most deployments.

| Variable | Default | Description |
|---|---|---|
| `MORALSTACK_DCCL_ENABLED` | `true` | Enable/disable the DCCL globally |
| `MORALSTACK_DCCL_EVALUATION_PATH` | `hybrid` | `structured` / `llm` / `hybrid` |
| `MORALSTACK_DCCL_LLM_MODEL` | `gpt-4o` | Model used by the LLM path |
| `MORALSTACK_DCCL_LLM_TIMEOUT_MS` | `5000` | Soft timeout: if the LLM response arrives later, the parsed verdict is preserved and marked `degraded` (`llm_timeout`) |
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
| `COMPLIANCE_DRAFT_REUSED` | Case 1: validated draft reused on fast-path |
| `COMPLIANCE_DRAFT_REGENERATED` | Case 2: regen + revalidation succeeded |
| `COMPLIANCE_MATCH_DOWNGRADED` | Case 3: MATCH fell through to deliberation |
| `CONTRACT_STRUCTURE_PROSE_CONFLICT` | Structured/prose mismatch |

All events flow through the standard observability infrastructure
(`moralstack/observability/sink.py`), so they appear in the same SQLite tables /
JSONL files as other module events, depending on `MORALSTACK_OBSERVABILITY_MODE`.

Verdict events (`COMPLIANCE_LAYER_VERDICT_*`) include, among other fields:

- `speculative_draft_validated`
- `draft_match_method`
- `draft_match_confidence`

The `COMPLIANCE_LAYER` decision trace `stage_payload` mirrors the same three fields
for audit export and UI.

## Pipeline integration

The controller calls `_run_dccl_evaluation()` after speculative overlap / risk entry.
The verdict is stored on `ProcessCallContext.compliance_verdict` and exposed on
`OrchestratorResult.compliance_verdict`.

When the DCCL returns **MATCH**, the controller applies the Case 1–3 state machine
(see above). Cases 1 and 2 call `_route_compliance_match()` with the reused or
regenerated draft, producing `NORMAL_COMPLETE` (`path=COMPLIANCE_FAST_PATH`),
abandoning remaining speculative work, emitting five `MODULE_DEFERRED_TO_COMPLIANCE`
events, and skipping `decide_action` routing. Case 3 and fast-path failures fall
through to the standard pipeline (non-fatal).

**NO_MATCH**, **SAFETY_OVERRIDE**, and **NO_CONTRACT** leave routing unchanged.

## Testing

Unit tests for the data structures and config loader are in
`tests/test_compliance_foundation.py` (Commit 1).
Safety classifier tests in `tests/test_compliance_safety_override.py` (Commit 2).
Full evaluation logic tests in `tests/test_compliance_evaluation.py` (Commit 2).
Orchestrator integration (isolation) in `tests/test_compliance_orchestrator_integration.py` (Commit 2).
Pipeline signal propagation tests in `tests/test_compliance_fast_path.py` and
`tests/test_sdk_dccl.py` (Commit 3).

## SDK compatibility

The DCCL is implemented inside the orchestrator, so it works identically when
MoralStack is consumed via:

- Proxy server (`moralstack-server`)
- SDK Python wrapper (`govern(OpenAI())`)
- Direct CLI / benchmark scripts

SDK/proxy contract validation at load time ships in Commit 3.
