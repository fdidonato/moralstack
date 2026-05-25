# Documentation / Code Drift Report

This report is generated before adversarial planning. It is heuristic: agents must inspect suspicious items before treating them as blocking architectural drift.

## Task Keywords

task, multi-turn, context, alignment, across, proxy, governance, modules, final, delivery, objective, moralstack, handling, reason, over, same, materially, relevant

## Strong Path Matches

- `./venv/Scripts/python.exe`
- `docs/CODEBASE_FACTS.md`
- `docs/MORALSTACK_CODEBASE_INDEX.md`
- `docs/TRACES/complai_llm_rules_flow.md`
- `docs/TRACES/governance_decision_flow.md`
- `docs/TRACES/observability_db_to_ui.md`
- `docs/TRACES/openai_compatible_multiturn.md`
- `docs/architecture_spec.md`
- `docs/constitution.md`
- `docs/decision_policy.md`
- `docs/multiturn_design.md`
- `examples/server_quickstart.py`
- `logs/observability`
- `moralstack/__init__.py`
- `moralstack/cli/run.py`
- `moralstack/compliance/safety_override.py`
- `moralstack/orchestration/controller.py`
- `moralstack/runtime/decision/safe_complete_policy.py`
- `moralstack/sdk/__init__.py`
- `moralstack/sdk/wrapper.py`
- `moralstack/server/proxy.py`
- `moralstack/ui/app.py`
- `scripts/benchmark_moralstack.py`
- `scripts/consolidate_jsonl_meta.py`
- `scripts/inspect_multiturn_trace.py`
- `scripts/mstack_run.py`
- `scripts/openai_compatible_server.py`
- `tests/e2e_run_regression.py`
- `tests/test_server_proxy.py`
- `tests/test_system_prompt_byte_equality.py`

## Potential Path Drift

- `.../export.md`
- `compliance/dccl.py`
- `compliance/safety_override.py`
- `config/signals.yaml`
- `data/core.yaml`
- `decision/safe_complete_policy.py`
- `models/risk/calibration.py`
- `models/risk/estimator.py`
- `observability/config.py`
- `observability/context.py`
- `observability/governance_audit.py`
- `observability/read_store.py`
- `observability/router.py`
- `observability/service.py`
- `observability/sinks/jsonl_sink.py`
- `observability/sinks/sqlite_sink.py`
- `orchestration/controller.py`
- `orchestration/convergence.py`
- `orchestration/convergence_evaluator.py`
- `orchestration/conversational_fast_path.py`
- `orchestration/decision_service.py`
- `orchestration/ledger.py`
- `orchestration/path_router.py`
- `orchestration/refusal_handler.py`
- `orchestration/safe_complete_gating.py`
- `orchestration/types.py`
- `reports/conversation_export.py`
- `runtime/decision/safe_complete_policy.py`
- `runtime/orchestrator.py`
- `sdk/config.py`
- `sdk/response.py`
- `sdk/wrapper.py`
- `server/conversation_correlation.py`
- `server/headers.py`
- `server/proxy.py`
- `sinks/jsonl_sink.py`
- `sinks/sqlite_sink.py`
- `tests/commands`
- `tests/test_`
- `ui/app.py`

## Symbol Matches

### `Action`

```text
.\.github\workflows\publish.yml:11:# Manual: GitHub Actions → Publish to PyPI → Run workflow (uses the branch you select).
.\.adversarial\setup.ps1:1:$ErrorActionPreference = "Stop"
.\.adversarial\setup.ps1:11:    $cmd = Get-Command $bin -ErrorAction SilentlyContinue
.\CLAUDE.md:67:   Action bounds are defined in `moralstack/runtime/decision/safe_complete_policy.py`
.\docs\architecture_spec.md:323:    risk_policy_action: RiskPolicyAction = RiskPolicyAction.DELIBERATE
.\docs\architecture_spec.md:340:class RiskPolicyAction(Enum):
.\docs\CODEBASE_FACTS.md:24:| `final_action` ∈ {NORMAL_COMPLETE, SAFE_COMPLETE, REFUSE} computed from structured signals, not text. Action bounds defined in `safe_complete_policy.py`; runtime final action assembled by `decision_service.py` and post-gated by `safe_complete_gating.py` | `runtime/decision/safe_complete_policy.py:158-285`; `orchestration/decision_service.py:493-579`; `orchestration/safe_complete_gating.py:86-171` | High | |
.\docs\CODEBASE_FACTS.md:25:| Action ordering is `NORMAL_COMPLETE < SAFE_COMPLETE < REFUSE` | `runtime/decision/safe_complete_policy.py:38-41` | High | `Action` enum |
```

### `COMPLIANCE_DRAFT_REGENERATED`

```text
.\docs\traces\governance_decision_flow.md:77:    then `_revalidate_draft`; if valid → `COMPLIANCE_DRAFT_REGENERATED` →
.\moralstack\orchestration\controller.py:49:    COMPLIANCE_DRAFT_REGENERATED,
.\moralstack\orchestration\controller.py:1990:                            event_type=COMPLIANCE_DRAFT_REGENERATED,
.\moralstack\ui\app.py:28:    COMPLIANCE_DRAFT_REGENERATED,
.\moralstack\ui\app.py:685:    for event_type in (COMPLIANCE_DRAFT_REUSED, COMPLIANCE_DRAFT_REGENERATED):
.\moralstack\ui\app.py:788:    if COMPLIANCE_DRAFT_REGENERATED in event_types:
.\moralstack\ui\app.py:791:            if (e.get("event_type") or "") != COMPLIANCE_DRAFT_REGENERATED:
.\tests\test_compliance_fast_path.py:20:    COMPLIANCE_DRAFT_REGENERATED,
```

### `COMPLIANCE_DRAFT_REUSED`

```text
.\docs\modules\compliance_layer.md:146:| **1** | `speculative_draft_validated=True`, `degraded=False`, non-empty draft | Reuse draft → `COMPLIANCE_FAST_PATH` | `COMPLIANCE_DRAFT_REUSED` |
.\docs\modules\compliance_layer.md:244:| `COMPLIANCE_DRAFT_REUSED` | Case 1: validated draft reused on fast-path |
.\docs\traces\governance_decision_flow.md:75:    `COMPLIANCE_DRAFT_REUSED` → `_route_compliance_match(..., draft_is_speculative=True)`.
.\tests\test_compliance_fast_path.py:21:    COMPLIANCE_DRAFT_REUSED,
.\tests\test_compliance_fast_path.py:138:        assert COMPLIANCE_DRAFT_REUSED in emitted
.\tests\test_compliance_fast_path.py:187:        assert COMPLIANCE_DRAFT_REUSED in emitted
.\tests\test_compliance_fast_path.py:236:        assert COMPLIANCE_DRAFT_REUSED not in emitted
.\moralstack\orchestration\controller.py:50:    COMPLIANCE_DRAFT_REUSED,
```

### `COMPLIANCE_FAST_PATH`

```text
.\docs\architecture_spec.md:195:**compliance fast-path** (`COMPLIANCE_FAST_PATH`, `NORMAL_COMPLETE`) and skip risk
.\docs\CODEBASE_FACTS.md:38:| DCCL runs before risk routing; MATCH + validated draft routes to compliance fast-path skipping deliberation | `orchestration/controller.py:1936-2040,1205-1297` | High | `_route_compliance_match`, path `COMPLIANCE_FAST_PATH` |
.\final_investigation_report.md:22:There is also an SDK/proxy divergence on compliance fast path. The proxy special-cases `COMPLIANCE_FAST_PATH` and returns the governed draft without an upstream call (`moralstack/server/proxy.py:338-350`). The SDK code path does not contain the same compliance-fast-path special case in its `NORMAL_COMPLETE` branch; it calls the wrapped upstream client with the original kwargs (`moralstack/sdk/wrapper.py:380-393`). The live probe confirmed this: SDK made one upstream call with 10 original messages; proxy made zero upstream calls and returned `6009 Grant Street` from the governed draft.
.\final_investigation_report.md:65:| 2. MoralStack proxy, live governance internals, fake final upstream | EXECUTED | `NORMAL_COMPLETE`, risk `0.1500`, path header `COMPLIANCE_FAST_PATH`, compliance `MATCH`, zero upstream calls, final text exactly `6009 Grant Street`. |
.\final_investigation_report.md:105:- speculative draft: GENERATED internally; on `COMPLIANCE_FAST_PATH`, proxy can return it directly.
.\final_investigation_report.md:111:- `COMPLIANCE_FAST_PATH`: governed draft returned directly, no upstream call (`moralstack/server/proxy.py:338-350`).
.\final_investigation_report.md:195:- Its output can be validated by DCCL and become the proxy final response on `COMPLIANCE_FAST_PATH`.
.\final_investigation_report.md:207:The compliance fast path skips deliberative modules when DCCL matches and validates a draft. In the live proxy Q74 run, the headers showed `COMPLIANCE_FAST_PATH` and `MATCH`; final text came from the governed draft, not upstream final delivery.
```

### `COMPLIANCE_LAYER`

```text
.\docs\architecture_spec.md:190:observability (`COMPLIANCE_LAYER_*` events, `OrchestratorResult.compliance_verdict`).
.\final_investigation_report.md:66:| 3. MoralStack SDK, live governance internals, fake final upstream | EXECUTED | `NORMAL_COMPLETE`, risk `0.15`, reason `COMPLIANCE_LAYER_MATCH`, one upstream call with the 10 original messages, final text was fake upstream text. This proves SDK did not return the governed draft directly. |
.\docs\traces\observability_db_to_ui.md:53:| `decision_traces` | stage snapshots (`RISK_ASSESSMENT`, `COMPLIANCE_LAYER`, `DELIBERATION_AGGREGATE`, `RELEVANT_PRINCIPLES`, `FINAL`, …) as `trace_json` |
.\docs\traces\governance_decision_flow.md:72:  (`controller.py:980-1062`). Emits `COMPLIANCE_LAYER_STARTED` and a verdict event.
.\docs\traces\governance_decision_flow.md:182:- `RISK_ASSESSMENT`, `COMPLIANCE_LAYER`, `DELIBERATION_AGGREGATE`, `FINAL`
.\docs\traces\governance_decision_flow.md:184:- `orchestration_events`: `SPECULATIVE_STARTED`, `COMPLIANCE_LAYER_*`,
.\moralstack\reports\markdown_export.py:420:        "COMPLIANCE_LAYER_VERDICT_MATCH",
.\moralstack\reports\markdown_export.py:421:        "COMPLIANCE_LAYER_VERDICT_NO_MATCH",
```

### `COMPLIANCE_LAYER_STARTED`

```text
.\docs\traces\governance_decision_flow.md:72:  (`controller.py:980-1062`). Emits `COMPLIANCE_LAYER_STARTED` and a verdict event.
.\moralstack\orchestration\controller.py:51:    COMPLIANCE_LAYER_STARTED,
.\moralstack\orchestration\controller.py:1005:                event_type=COMPLIANCE_LAYER_STARTED,
.\moralstack\orchestration\orchestration_event_taxonomy.py:74:COMPLIANCE_LAYER_STARTED = "COMPLIANCE_LAYER_STARTED"
.\tests\test_compliance_orchestrator_integration.py:23:    COMPLIANCE_LAYER_STARTED,
.\tests\test_compliance_orchestrator_integration.py:134:    assert COMPLIANCE_LAYER_STARTED in emitted
.\tests\test_compliance_orchestrator_integration.py:170:    assert COMPLIANCE_LAYER_STARTED in emitted
.\docs\modules\compliance_layer.md:234:| `COMPLIANCE_LAYER_STARTED` | DCCL.evaluate begins |
```

### `COMPLIANCE_MATCH_DOWNGRADED`

```text
.\docs\traces\governance_decision_flow.md:78:    `_route_compliance_match`; else `COMPLIANCE_MATCH_DOWNGRADED` and fall through
.\docs\modules\compliance_layer.md:148:| **3** | Case 2 revalidation fails | Continue standard pipeline (deliberation) | `COMPLIANCE_MATCH_DOWNGRADED` |
.\docs\modules\compliance_layer.md:246:| `COMPLIANCE_MATCH_DOWNGRADED` | Case 3: MATCH fell through to deliberation |
.\moralstack\ui\app.py:30:    COMPLIANCE_MATCH_DOWNGRADED,
.\moralstack\ui\app.py:804:    if COMPLIANCE_MATCH_DOWNGRADED in event_types:
.\moralstack\orchestration\controller.py:56:    COMPLIANCE_MATCH_DOWNGRADED,
.\moralstack\orchestration\controller.py:2022:                            event_type=COMPLIANCE_MATCH_DOWNGRADED,
.\moralstack\orchestration\orchestration_event_taxonomy.py:125:COMPLIANCE_MATCH_DOWNGRADED = "COMPLIANCE_MATCH_DOWNGRADED"
```

### `CONVERGED`

```text
.\docs\CODEBASE_FACTS.md:73:| `enforce_convergence_invariants` is the sole loop authority: CONVERGED/CONVERGED_WITH_SUGGESTIONS ⇒ stop+converged; REFUSE ⇒ stop (`HARD_VIOLATION_STOP`); cycle≥max ⇒ stop (`CYCLES_EXHAUSTED`) | `orchestration/convergence.py:19-65` | High | |
.\docs\MORALSTACK_CODEBASE_INDEX.md:326:  `DecisionType` (PROCEED→CONVERGED, REVISE, REFUSE, CONTINUE, plus
.\docs\MORALSTACK_CODEBASE_INDEX.md:327:  CONVERGED_WITH_SUGGESTIONS). The **simulator can never vote REFUSE** — REFUSE
.\docs\MORALSTACK_CODEBASE_INDEX.md:333:  authority: CONVERGED ⇒ stop+converged; REFUSE ⇒ stop (`HARD_VIOLATION_STOP`);
.\docs\traces\governance_decision_flow.md:142:from hard violations or a refuse-vote majority. Stop reasons: `CONVERGED`,
.\moralstack\cli\visualizer.py:192:                if phase.decision in ["PROCEED", "CONVERGED", "APPROVED"]
.\moralstack\cli\visualizer.py:267:                if phase.decision in ["PROCEED", "CONVERGED", "APPROVED"]
.\moralstack\reports\model.py:242:    converged = stop == "CONVERGED"
```

### `CONVERSATION_CONTEXT_ATTACHED`

```text
.\.cursor\rules\project-overview.mdc:40:| `moralstack/orchestration/`    | Controller (thin) routing, path_router, overlay_policy, trace_lifecycle, decision_logger, safe refusal generation, config_loader for env (MORALSTACK_ORCHESTRATOR_*); `process_context` (`ProcessCallContext` — per-request mutable state for `OrchestrationController.process`, not stored on the controller instance); `OrchestrationController` optional keyword `ledger` / `session_store` (Step 6: semantic ledger lookup after `decide_action` + hard-signal resolution for observability and `ledger.store`; Step 7: `conversational_fast_path` / `ConversationalFastPathRunner` applies conservative cache-hit routing to skip deliberation when safe); v0.4 governance fields on `conversation_governance_state_out`; `contract` (`DeveloperContract` for deployer system-contract propagation); `system_prompt_resolver` (`effective_system_for_request`, per-request system with optional `DeveloperContract`); `embedder` (`EmbedderProtocol`, `O

[... trimmed ...]

UATED`, `CONVERGENCE_EVALUATED`, `EARLY_CONVERGENCE_*`, `CONVERSATION_CONTEXT_ATTACHED`, `CONVERSATION_STATE_UPDATED`); `speculative_overlap` (lazy join of speculative generation with risk); `types.RequestAnalysisContext` (request-scoped principle retrieval reuse for deliberation) |
.\docs\traces\governance_decision_flow.md:186:  `CONVERSATION_CONTEXT_ATTACHED`, `CONVERSATION_STATE_UPDATED`,
.\docs\modules\observability.md:334:| `CONVERSATION_CONTEXT_ATTACHED` | orchestration | conversation | Conversation context was attached to the request (session id, turn index, parent request). |
.\moralstack\orchestration\controller.py:57:    CONVERSATION_CONTEXT_ATTACHED,
.\moralstack\orchestration\controller.py:381:                event_type=CONVERSATION_CONTEXT_ATTACHED,
.\moralstack\orchestration\orchestration_event_taxonomy.py:65:CONVERSATION_CONTEXT_ATTACHED = "CONVERSATION_CONTEXT_ATTACHED"
.\moralstack\orchestration\orchestration_event_taxonomy.py:165:        CONVERSATION_CONTEXT_ATTACHED,
```

### `CONVERSATION_STATE_UPDATED`

```text
.\.cursor\rules\project-overview.mdc:40:| `moralstack/orchestration/`    | Controller (thin) routing, path_router, overlay_policy, trace_lifecycle, decision_logger, safe refusal generation, config_loader for env (MORALSTACK_ORCHESTRATOR_*); `process_context` (`ProcessCallContext` — per-request mutable state for `OrchestrationController.process`, not stored on the controller instance); `OrchestrationController` optional keyword `ledger` / `session_store` (Step 6: semantic ledger lookup after `decide_action` + hard-signal resolution for observability and `ledger.store`; Step 7: `conversational_fast_path` / `ConversationalFastPathRunner` applies conservative cache-hit routing to skip deliberation when safe); v0.4 governance fields on `conversation_governance_state_out`; `contract` (`DeveloperContract` for deployer system-contract propagation); `system_prompt_resolver` (`effective_system_for_request`, per-request system with optional `DeveloperContract`); `embedder` (`EmbedderProtocol`, `O

[... trimmed ...]

ration_events event_type names; includes `AGGREGATED_GUIDANCE_EVALUATED`, `CONVERGENCE_EVALUATED`, `EARLY_CONVERGENCE_*`, `CONVERSATION_CONTEXT_ATTACHED`, `CONVERSATION_STATE_UPDATED`); `speculative_overlap` (lazy join of speculative generation with risk); `types.RequestAnalysisContext` (request-scoped principle retrieval reuse for deliberation) |
.\moralstack\observability\__init__.py:36:    EVENT_CONVERSATION_STATE_UPDATED,
.\moralstack\observability\__init__.py:123:    "EVENT_CONVERSATION_STATE_UPDATED",
.\docs\traces\governance_decision_flow.md:186:  `CONVERSATION_CONTEXT_ATTACHED`, `CONVERSATION_STATE_UPDATED`,
.\moralstack\observability\events.py:35:EVENT_CONVERSATION_STATE_UPDATED = "conversation.state_updated"
.\moralstack\observability\events.py:56:        EVENT_CONVERSATION_STATE_UPDATED,
.\moralstack\observability\conversation_events.py:29:    EVENT_CONVERSATION_STATE_UPDATED,
.\moralstack\observability\conversation_events.py:194:            EVENT_CONVERSATION_STATE_UPDATED,
```

### `CYCLES_EXHAUSTED`

```text
.\docs\CODEBASE_FACTS.md:73:| `enforce_convergence_invariants` is the sole loop authority: CONVERGED/CONVERGED_WITH_SUGGESTIONS ⇒ stop+converged; REFUSE ⇒ stop (`HARD_VIOLATION_STOP`); cycle≥max ⇒ stop (`CYCLES_EXHAUSTED`) | `orchestration/convergence.py:19-65` | High | |
.\.cursor\rules\policy-layer.mdc:17:- Overlays may declare **`sensitive: true`** to signal regulated domains requiring enhanced governance. When active, the Controller applies a risk_score floor (`OVERLAY_SENSITIVE_RISK_FLOOR = 0.35`, defined in `moralstack/orchestration/overlay_policy.py`) and a CYCLES_EXHAUSTED → SAFE_COMPLETE fallback. See @docs/constitution.md §4.3 and @docs/decision_policy.md.
.\docs\constitution.md:186:- **CYCLES_EXHAUSTED fallback**: if deliberation exhausts cycles without converging and the decision is
.\docs\decision_policy.md:153:## SAFE_COMPLETE fallback on CYCLES_EXHAUSTED
.\docs\decision_policy.md:158:    1. `outcome.stop_reason == "CYCLES_EXHAUSTED"`
.\docs\decision_policy.md:165:- **Rationale**: a CYCLES_EXHAUSTED outcome in a sensitive context signals uncertainty; the system adopts the
.\docs\traces\governance_decision_flow.md:143:`HARD_VIOLATION_STOP`, `CYCLES_EXHAUSTED`. A cycle-1 early-convergence check
.\moralstack\models\reason_codes.py:21:    CYCLES_EXHAUSTED_FALLBACK = "CYCLES_EXHAUSTED_FALLBACK"
```

### `CachedDecision`

```text
.\.cursor\rules\project-overview.mdc:40:| `moralstack/orchestration/`    | Controller (thin) routing, path_router, overlay_policy, trace_lifecycle, decision_logger, safe refusal generation, config_loader for env (MORALSTACK_ORCHESTRATOR_*); `process_context` (`ProcessCallContext` — per-request mutable state for `OrchestrationController.process`, not stored on the controller instance); `OrchestrationController` optional keyword `ledger` / `session_store` (Step 6: semantic ledger lookup after `decide_action` + hard-signal resolution for observability and `ledger.store`; Step 7: `conversational_fast_path` / `ConversationalFastPathRunner` applies conservative cache-hit routing to skip deliberation when safe); v0.4 governance fields on `conversation_governance_state_out`; `contract` (`DeveloperContract` for deployer system-contract propagation); `system_prompt_resolver` (`effective_system_for_request`, per-request system with optional `DeveloperContract`); `embedder` (`EmbedderProtocol`, `O

[... trimmed ...]

E_*`, `CONVERSATION_CONTEXT_ATTACHED`, `CONVERSATION_STATE_UPDATED`); `speculative_overlap` (lazy join of speculative generation with risk); `types.RequestAnalysisContext` (request-scoped principle retrieval reuse for deliberation) |
.\docs\MORALSTACK_CODEBASE_INDEX.md:104:- `ledger.py`, `ledger_storage.py` — `SemanticDecisionLedger`, `CachedDecision`,
.\moralstack\orchestration\conversational_fast_path.py:92:                f"Unknown final_action in CachedDecision: {cached.final_action!r}. "
.\moralstack\orchestration\controller.py:47:from moralstack.orchestration.ledger import CachedDecision, LedgerResult, SemanticDecisionLedger
.\moralstack\orchestration\controller.py:266:        cached = CachedDecision(
.\moralstack\orchestration\controller.py:590:        # Build a CachedDecision directly (we don't have the Decision object here, just metadata).
.\moralstack\orchestration\controller.py:606:        cached = CachedDecision(
.\moralstack\orchestration\ledger.py:72:class CachedDecision:
```

### `ComplianceDecision`

```text
.\docs\MORALSTACK_CODEBASE_INDEX.md:145:- `types.py` — `ComplianceDecision` (MATCH, NO_MATCH, SAFETY_OVERRIDE, NO_CONTRACT),
.\moralstack\compliance\__init__.py:15:    - ComplianceDecision: enum of possible decisions
.\moralstack\compliance\__init__.py:25:    ComplianceDecision,
.\moralstack\compliance\__init__.py:37:    "ComplianceDecision",
.\moralstack\compliance\dccl.py:36:    ComplianceDecision,
.\moralstack\compliance\dccl.py:258:            if verdict.decision == ComplianceDecision.NO_MATCH and getattr(contract, "raw_text", "").strip():
.\moralstack\compliance\dccl.py:260:                if verdict_llm.decision != ComplianceDecision.NO_MATCH:
.\moralstack\compliance\dccl.py:264:                        decision=ComplianceDecision.NO_MATCH,
```

### `ComplianceVerdict`

```text
.\docs\MORALSTACK_CODEBASE_INDEX.md:141:- `dccl.py` — `DeveloperContractComplianceLayer.evaluate(...)` → `ComplianceVerdict`.
.\docs\MORALSTACK_CODEBASE_INDEX.md:146:  `ComplianceVerdict`, `MatchedRule`, `StructuredRule`, `EvaluationPath`.
.\docs\modules\compliance_layer.md:70:### `ComplianceVerdict`
.\tests\test_compliance_foundation.py:16:    ComplianceVerdict,
.\tests\test_compliance_foundation.py:112:# ComplianceVerdict
.\tests\test_compliance_foundation.py:116:class TestComplianceVerdict:
.\tests\test_compliance_foundation.py:118:        v = ComplianceVerdict(decision=ComplianceDecision.NO_CONTRACT)
.\tests\test_compliance_foundation.py:124:        v = ComplianceVerdict(
```

### `ConstitutionStore`

```text
.\.env.template:73:# Max number of parallel domain agents in ConstitutionStore and SDK/CLI factory.
.\moralstack\constitution\__init__.py:15:    "ConstitutionStore",
.\moralstack\constitution\__init__.py:16:    "ConstitutionStoreConfig",
.\moralstack\constitution\__init__.py:31:    "ConstitutionStore": "moralstack.constitution.store",
.\moralstack\constitution\__init__.py:32:    "ConstitutionStoreConfig": "moralstack.constitution.store",
.\moralstack\constitution\store.py:89:    `ConstitutionStore.get_relevant_principles()` and read the resulting
.\moralstack\constitution\store.py:90:    `prefiltered_domains` from `ConstitutionStore.get_debug_info()` instead.
.\moralstack\constitution\store.py:449:class ConstitutionStoreConfig:
```

### `ConversationCorrelationStore`

```text
.\docs\traces\openai_compatible_multiturn.md:49:3. **Lineage correlation** (`ConversationCorrelationStore.resolve`).
.\docs\MORALSTACK_CODEBASE_INDEX.md:178:- `conversation_correlation.py` — `ConversationCorrelationStore` (lineage hashing
.\docs\modules\server_proxy.md:12:- `moralstack.server.conversation_correlation.ConversationCorrelationStore` — process-local lineage mapping for OpenAI-style full-history replays when no explicit `conversation_id` is provided.
.\docs\modules\server_proxy.md:34:- For multi-turn conversational clients (full history replay per request), run **one** uvicorn worker per process unless you provide a **shared** session store and distributed locking across workers. Each worker has its own `InMemorySessionStore` and `ConversationCorrelationStore`.
.\docs\modules\server_proxy.md:59:- `tests/test_conversation_correlation.py` — lineage hash and `ConversationCorrelationStore` behaviour.
.\tests\test_conversation_correlation.py:6:    ConversationCorrelationStore,
.\tests\test_conversation_correlation.py:17:class TestConversationCorrelationStore:
.\tests\test_conversation_correlation.py:19:        store = ConversationCorrelationStore()
```

### `ConversationGovernanceState`

```text
.\.cursor\rules\project-overview.mdc:40:| `moralstack/orchestration/`    | Controller (thin) routing, path_router, overlay_policy, trace_lifecycle, decision_logger, safe refusal generation, config_loader for env (MORALSTACK_ORCHESTRATOR_*); `process_context` (`ProcessCallContext` — per-request mutable state for `OrchestrationController.process`, not stored on the controller instance); `OrchestrationController` optional keyword `ledger` / `session_store` (Step 6: semantic ledger lookup after `decide_action` + hard-signal resolution for observability and `ledger.store`; Step 7: `conversational_fast_path` / `ConversationalFastPathRunner` applies conservative cache-hit routing to skip deliberation when safe); v0.4 governance fields on `conversation_governance_state_out`; `contract` (`DeveloperContract` for deployer system-contract propagation); `system_prompt_resolver` (`effective_system_for_request`, per-request system with optional `DeveloperContract`); `embedder` (`EmbedderProtocol`, `O

[... trimmed ...]

TION_CONTEXT_ATTACHED`, `CONVERSATION_STATE_UPDATED`); `speculative_overlap` (lazy join of speculative generation with risk); `types.RequestAnalysisContext` (request-scoped principle retrieval reuse for deliberation) |
.\CHANGELOG.md:314:- **ConversationGovernanceState** extension (Step 1): added `posture`, `last_domain`,
.\docs\architecture_spec.md:1440:- **ConversationGovernanceState**: per-conversation state object carrying
.\moralstack\runtime\orchestrator.py:25:from moralstack.orchestration.conversation_state import ConversationGovernanceState
.\moralstack\runtime\orchestrator.py:75:    "ConversationGovernanceState",
.\moralstack\sdk\wrapper.py:309:        # Snapshot of the incoming ConversationGovernanceState BEFORE the
.\moralstack\sdk\session_store.py:26:    from moralstack.orchestration.conversation_state import ConversationGovernanceState
.\moralstack\sdk\session_store.py:37:    """Structural protocol for any backend storing ConversationGovernanceState per conversation_id."""
```

### `ConversationLockManager`

```text
.\docs\CODEBASE_FACTS.md:46:| Production proxy serializes same-conversation requests with per-conversation locks | `server/proxy.py:72-119,234-242` | High | `ConversationLockManager`, 30s acquire timeout |
.\docs\traces\openai_compatible_multiturn.md:76:  for the per-conversation lock (`ConversationLockManager`, `proxy.py:87-110`),
.\docs\traces\openai_compatible_multiturn.md:87:- `ConversationLockManager` hands out one `threading.Lock` per conversation_id;
.\docs\MORALSTACK_CODEBASE_INDEX.md:176:  `GET /healthz`. `ConversationLockManager` (per-conversation locks),
.\moralstack\server\proxy.py:72:class ConversationLockManager:
.\moralstack\server\proxy.py:105:                "ConversationLockManager: timeout acquiring lock for conversation_id=%s after %.1fs",
.\moralstack\server\proxy.py:206:    lock_manager: ConversationLockManager,
.\moralstack\server\proxy.py:437:    lock_manager = ConversationLockManager()
```

### `ConversationLockTimeout`

```text
.\docs\traces\openai_compatible_multiturn.md:89:  HTTP 503 with `Retry-After: 10` (`ConversationLockTimeout`).
.\moralstack\server\proxy.py:64:class ConversationLockTimeout(RuntimeError):
.\moralstack\server\proxy.py:93:        :class:`ConversationLockTimeout` when ``conversation_id`` is non-empty
.\moralstack\server\proxy.py:109:            raise ConversationLockTimeout(conversation_id)
.\moralstack\server\proxy.py:237:        except ConversationLockTimeout:
.\tests\test_server_proxy.py:779:class TestConversationLockTimeout:
.\tests\test_server_proxy.py:781:        from moralstack.server.proxy import ConversationLockManager, ConversationLockTimeout
.\tests\test_server_proxy.py:788:                with pytest.raises(ConversationLockTimeout):
```

### `ConversationalFastPathRunner`

```text
.\CHANGELOG.md:157:  `ConversationalFastPathRunner.is_safe_to_apply`.
.\CHANGELOG.md:320:- **ConversationalFastPathRunner** (Step 7): optimized routing for low-risk
.\.cursor\rules\project-overview.mdc:40:| `moralstack/orchestration/`    | Controller (thin) routing, path_router, overlay_policy, trace_lifecycle, decision_logger, safe refusal generation, config_loader for env (MORALSTACK_ORCHESTRATOR_*); `process_context` (`ProcessCallContext` — per-request mutable state for `OrchestrationController.process`, not stored on the controller instance); `OrchestrationController` optional keyword `ledger` / `session_store` (Step 6: semantic ledger lookup after `decide_action` + hard-signal resolution for observability and `ledger.store`; Step 7: `conversational_fast_path` / `ConversationalFastPathRunner` applies conservative cache-hit routing to skip deliberation when safe); v0.4 governance fields on `conversation_governance_state_out`; `contract` (`DeveloperContract` for deployer system-contr

[... trimmed ...]

`orchestration_event_taxonomy` (stable orchestration_events event_type names; includes `AGGREGATED_GUIDANCE_EVALUATED`, `CONVERGENCE_EVALUATED`, `EARLY_CONVERGENCE_*`, `CONVERSATION_CONTEXT_ATTACHED`, `CONVERSATION_STATE_UPDATED`); `speculative_overlap` (lazy join of speculative generation with risk); `types.RequestAnalysisContext` (request-scoped principle retrieval reuse for deliberation) |
.\docs\architecture_spec.md:1444:- **ConversationalFastPathRunner**: optimized fast-path for low-risk
.\docs\MORALSTACK_CODEBASE_INDEX.md:103:- `conversational_fast_path.py` — `ConversationalFastPathRunner` (cache-driven skip).
.\docs\MORALSTACK_CODEBASE_INDEX.md:402:  same-conversation cache hit, gated by `ConversationalFastPathRunner.is_safe_to_apply`
.\docs\multiturn_design.md:30:| `ConversationalFastPathRunner` | `moralstack.orchestration.conversational_fast_path` | 7 |
.\docs\traces\complai_llm_rules_flow.md:110:On a same-conversation hit, `ConversationalFastPathRunner.is_safe_to_apply` gates
```

### `DELIBERATION_AGGREGATE`

```text
.\docs\MORALSTACK_CODEBASE_INDEX.md:336:The controller emits a `DELIBERATION_AGGREGATE` decision trace
.\docs\traces\governance_decision_flow.md:182:- `RISK_ASSESSMENT`, `COMPLIANCE_LAYER`, `DELIBERATION_AGGREGATE`, `FINAL`
.\docs\traces\observability_db_to_ui.md:53:| `decision_traces` | stage snapshots (`RISK_ASSESSMENT`, `COMPLIANCE_LAYER`, `DELIBERATION_AGGREGATE`, `RELEVANT_PRINCIPLES`, `FINAL`, …) as `trace_json` |
.\moralstack\orchestration\controller.py:729:        """Emit a DELIBERATION_AGGREGATE decision trace with full
.\moralstack\orchestration\controller.py:778:                stage="DELIBERATION_AGGREGATE",
.\moralstack\orchestration\controller.py:786:            _LOG.debug("emit DELIBERATION_AGGREGATE trace failed", exc_info=True)
.\moralstack\runtime\trace\trace_stages.py:10:DELIBERATION_AGGREGATE = "DELIBERATION_AGGREGATE"
```

### `Decision`

```text
.\.env.minimal:28:# Semantic Decision Ledger (SDK / proxy bootstrap; Step 14.2)
.\.env.template:36:# Semantic Decision Ledger (SDK / proxy bootstrap; Step 14.2)
.\CHANGELOG.md:64:- **Asimmetria strutturale `posture` tra store e lookup del SemanticDecisionLedger**
.\CHANGELOG.md:137:  quando il SemanticDecisionLedger fa hit e la safety gate accetta di
.\CHANGELOG.md:161:- **SemanticDecisionLedger wired into production SDK bootstrap**
.\CHANGELOG.md:173:  - `_bootstrap_pipeline` builds a default `SemanticDecisionLedger` with
.\CHANGELOG.md:194:- **SemanticDecisionLedger `request_type` round-trip** (`moralstack/orchestration/controller.py`):
.\CHANGELOG.md:316:- **SemanticDecisionLedger** (Step 4): embedding-based cache for governance
```

### `DecisionType`

```text
.\docs\architecture_spec.md:183:    decision: DecisionType | None
.\docs\CODEBASE_FACTS.md:72:| Deliberative final action: `ConvergenceEvaluator.determine_decision` produces a `DecisionType` from weighted votes (critic/simulator/perspectives/hindsight); simulator can never produce REFUSE; REFUSE only from hard violations or refuse-vote majority | `orchestration/convergence_evaluator.py:314-519` | High | |
.\docs\MORALSTACK_CODEBASE_INDEX.md:326:  `DecisionType` (PROCEED→CONVERGED, REVISE, REFUSE, CONTINUE, plus
.\docs\traces\governance_decision_flow.md:140:`DecisionType`; `enforce_convergence_invariants` (`convergence.py:19-65`) then
.\docs\modules\critic.md:322:    decision = DecisionType.REFUSE
.\docs\modules\critic.md:324:    decision = DecisionType.REVISE
.\docs\modules\critic.md:327:    decision = DecisionType.CONTINUE
.\docs\modules\hindsight.md:161:    decision = DecisionType.CONVERGED
```

### `DefaultPersistence`

```text
.\.cursor\rules\project-overview.mdc:43:| `moralstack/persistence/`      | PersistencePort (protocol), NullPersistence, DefaultPersistence; SQLite (config, context, db, sink); `requests` optional conversation linkage (`conversation_id`, `turn_index`, `parent_request_id`, Step 13 `meta_json`); `orchestration_events` table + persist/read APIs; extended `llm_calls` metadata (`call_kind`, `call_outcome`, `cache_status`, `related_event_id`); Step 13 tables: `conversation_states`, `ledger_events`, `session_store_events`, `proxy_request_events` |
.\docs\MORALSTACK_CODEBASE_INDEX.md:167:- `default.py` — `DefaultPersistence` (`ensure_run_and_upsert_request`,
.\moralstack\observability\sinks\sqlite_sink.py:665:# Lifecycle write functions (used by DefaultPersistence and SqliteEventSink)
.\moralstack\runtime\orchestrator.py:48:from moralstack.persistence.default import DefaultPersistence
.\moralstack\runtime\orchestrator.py:181:            persistence=DefaultPersistence(),
.\moralstack\server\proxy.py:600:    The CLI relies on `DefaultPersistence.ensure_run_and_upsert_request()`
.\moralstack\persistence\default.py:19:class DefaultPersistence:
.\moralstack\persistence\__init__.py:60:from moralstack.persistence.default import DefaultPersistence  # noqa: E402, F401
```

### `DeliberationRunner`

```text
.\docs\MORALSTACK_CODEBASE_INDEX.md:99:- `deliberation_runner.py` — `DeliberationRunner` (cycles, convergence,
.\docs\MORALSTACK_CODEBASE_INDEX.md:312:When `route == "deliberative"`, `DeliberationRunner` runs cycles (default
.\docs\refactoring_backlog.md:170:### 13. `DeliberationRunner.__init__` — Long Parameter List (11 params)
.\docs\refactoring_backlog.md:174:| **File**      | `orchestration/deliberation_runner.py` → `DeliberationRunner.__init__` (L53-66)                                                                                                               |
.\docs\refactoring_backlog.md:182:### 14. `DeliberationRunner._deliberation_cycle` — Complexity
.\docs\traces\governance_decision_flow.md:137:`_route_deliberative` runs the `DeliberationRunner` cycles (critic → simulator ∥
.\docs\modules\orchestrator.md:39:`moralstack/orchestration/system_prompt_resolver.py` exposes `effective_system_for_request(...)`, composing the policy system prompt per request from the protected base, optional non-empty `DeveloperContract.raw_text`, and an optional mode suffix (`normal`, `safe_complete`, `constrained`). When no contract text is present, output matches the legacy single-turn byte strings. The suffix modes remain available for other call sites; **`DeliberationRunner` does not use `safe_complete` or `constrained` resolver modes for policy generation** (see below).
.\docs\modules\orchestrator.md:43:- **Internal policy (`DeliberationRunner`)**: `SAFE_COMPLETE_GENERATION_INSTRUCTION` and `CONSTRAINED_GENERATION_INSTRUCTION` from `moralstack/orchestration/_policy_helpers.py` are **prefixed onto the user-facing prompt** passed to `policy.generate` / `policy.rewrite`. The system string passed to the policy LLM is still composed with `effective_system_for_request(..., mode="normal")` (contract overlay only, no governance suffix from those constants).
```

### `DeveloperContract`

```text
.\CHANGELOG.md:310:- **DeveloperContract** (`moralstack.orchestration.contract`): typed representation
.\.cursor\rules\project-overview.mdc:40:| `moralstack/orchestration/`    | Controller (thin) routing, path_router, overlay_policy, trace_lifecycle, decision_logger, safe refusal generation, config_loader for env (MORALSTACK_ORCHESTRATOR_*); `process_context` (`ProcessCallContext` — per-request mutable state for `OrchestrationController.process`, not stored on the controller instance); `OrchestrationController` optional keyword `ledger` / `session_store` (Step 6: semantic ledger lookup after `decide_action` + hard-signal resolution for observability and `ledger.store`; Step 7: `conversational_fast_path` / `ConversationalFastPathRunner` applies conservative cache-hit routing to skip deliberation when safe); v0.4 governance fields on `conversation_governance_state_out`; `contract` (`DeveloperContract` for deployer system-contract propagation); `system_prompt_resolver` (`effective_system_

[... trimmed ...]

eculative generation with risk); `types.RequestAnalysisContext` (request-scoped principle retrieval reuse for deliberation) |
.\.cursor\rules\project-overview.mdc:42:| `moralstack/compliance/`       | Developer Contract Compliance Layer (DCCL): types, config (`MORALSTACK_DCCL_*`), safety override categories, scaffold (`DeveloperContractComplianceLayer`; Commit 1 foundation, not yet wired into controller) |
.\docs\architecture_spec.md:133:`developer_contract: DeveloperContract | None = None` (v0.4 additive field). `DeveloperContract` is defined in
.\docs\architecture_spec.md:143:is gated on `DeveloperContract.mode == "structured"` and keyword heuristics in `raw_text` (see module docstring).
.\docs\architecture_spec.md:1438:- **DeveloperContract**: a typed representation of the deployer's system
.\final_investigation_report.md:83:- developer contract: PRESERVED separately as opaque `DeveloperContract`.
.\moralstack\compliance\dccl.py:2:DeveloperContractComplianceLayer — main entry point.
```

### `EvaluationPath`

```text
.\docs\MORALSTACK_CODEBASE_INDEX.md:146:  `ComplianceVerdict`, `MatchedRule`, `StructuredRule`, `EvaluationPath`.
.\moralstack\compliance\__init__.py:28:    EvaluationPath,
.\moralstack\compliance\__init__.py:39:    "EvaluationPath",
.\moralstack\compliance\types.py:36:class EvaluationPath(str, Enum):
.\moralstack\compliance\types.py:190:    evaluation_path: EvaluationPath = EvaluationPath.SKIPPED
.\moralstack\compliance\types.py:234:    evaluation_path: EvaluationPath = EvaluationPath.SKIPPED
.\moralstack\compliance\dccl.py:23:    EvaluationPathLiteral,
.\moralstack\compliance\dccl.py:38:    EvaluationPath,
```

### `EventEnvelope`

```text
.\docs\CODEBASE_FACTS.md:82:| JSONL sink writes one file **per event_type** (`{event_type}.jsonl`), each line = `envelope.to_dict()`; SQLite normalizes the same `EventEnvelope` into typed columns. Same source, different shape | `observability/sinks/jsonl_sink.py:77-95`; `observability/router.py:37-54` | High | |
.\docs\MORALSTACK_CODEBASE_INDEX.md:430:`file_only` and `dual` modes. Each emitted `EventEnvelope` becomes one JSON line.
.\moralstack\persistence\write_queue.py:5:The async_persist_* helpers construct an EventEnvelope and submit router.route()
.\moralstack\persistence\sink.py:6:they construct an EventEnvelope and call router.route() directly.
.\moralstack\observability\__init__.py:7:  EventEnvelope — typed event wrapper
.\moralstack\observability\__init__.py:52:    EventEnvelope,
.\moralstack\observability\__init__.py:111:    "EventEnvelope",
.\docs\modules\observability.md:13:├── __init__.py          # exposes: obs (lazy proxy → get_obs()), EventEnvelope, factory helpers
```

### `FINAL`

```text
.\docs\decision_policy.md:90:### PRE_POLICY vs FINAL
.\docs\decision_policy.md:93:bounds (without hard-violations). `FINAL` represents the decision after any hard-violations and enforcement. The
.\docs\decision_policy.md:94:decision exposed to the user is always **FINAL**.
.\docs\decision_policy.md:101:| `stage`                | `PRE_POLICY` \| `FINAL`                                     |
.\docs\decision_policy.md:102:| `sequence`             | Temporal order (1 = PRE, 2 = FINAL)                         |
.\docs\traces\openai_compatible_multiturn.md:119:- `PROXY_OUTPUT_FINALIZED` orchestration event with `final_text_source`
.\docs\traces\observability_db_to_ui.md:53:| `decision_traces` | stage snapshots (`RISK_ASSESSMENT`, `COMPLIANCE_LAYER`, `DELIBERATION_AGGREGATE`, `RELEVANT_PRINCIPLES`, `FINAL`, …) as `trace_json` |
.\moralstack\core\schema.py:19:FINAL_ACTION_VALUES = Literal["REFUSE", "SAFE_COMPLETE", "NORMAL_COMPLETE"]
```

### `FinalResponse`

```text
.\docs\architecture_spec.md:788:class FinalResponse:
.\docs\architecture_spec.md:839:    ) -> FinalResponse:
.\docs\architecture_spec.md:878:    def log_response(self, response: FinalResponse) -> None:
.\docs\architecture_spec.md:1231:def handle_error(error: MoralStackError, state: DeliberationState) -> FinalResponse:
.\docs\architecture_spec.md:1240:    return FinalResponse(
.\moralstack\runtime\orchestrator.py:36:    FinalResponse,
.\moralstack\runtime\orchestrator.py:57:    "FinalResponse",
.\docs\modules\orchestrator.md:393:    response: FinalResponse  # Final response
```

### `GovernanceConfig`

```text
.\CHANGELOG.md:179:  - `GovernanceConfig` adds matching fields for programmatic overrides without env.
.\CHANGELOG.md:417:- New public API: `govern`, `GovernedClient`, `GovernanceConfig`, `GovernedResponse`, `GovernanceMetadata`
.\INSTALL.md:92:### SDK Configuration via GovernanceConfig
.\INSTALL.md:97:from moralstack import govern, GovernanceConfig
.\INSTALL.md:102:    config=GovernanceConfig(
.\INSTALL.md:124:The pipeline uses its **own** internal LLM client for deliberation (configured via `GovernanceConfig`). The `OpenAI()` client you pass to `govern()` is used only for final text generation. SDK bootstrap calls `load_env()` before building modules, so `MORALSTACK_*` and `OPENAI_*` values are loaded from `.env` first. Runtime tuning (deliberation cycles, risk thresholds, module temperatures, etc.) is controlled exclusively via `MORALSTACK_*` environment variables — see `.env.template` for the full list.
.\.cursor\rules\project-overview.mdc:45:| `moralstack/sdk/`              | Python SDK (`govern()`, `GovernedClient`, `GovernanceConfig`, `GovernedResponse`); `bootstrap._bootstrap_pipeline` wires a default `SemanticDecisionLedger` (`OpenAIEmbedder`, `InMemoryLedgerStorage`) unless `MORALSTACK_LEDGER_ENABLED=false` / `enable_ledger=False`; `session_store` (`SessionStoreProtocol`, `InMemorySessionStore` for multi-conversation governance state); `session` (`SessionState` wraps a store, optional external `store=`); `GovernedClient.__init__` auto-initialises observability context (`run_id` via `set_current_run_id`, `init_db`/`create_run` for db modes); `GovernedCompletions.create()` flushes the observability write queue (`obs.flush()`) in a `try/finally` before returning; `SAFE_COMPLETE` appends a synthetic trailing `user` turn to `messages` (system prompt unchanged) |
.\examples\custom_overlay\run_custom_overlay.py:4:folder, adds my_domain.yaml, and uses GovernanceConfig(constitution_dir=...)
```

### `GovernanceError`

```text
.\moralstack\__init__.py:45:    "GovernanceError",
.\docs\MORALSTACK_CODEBASE_INDEX.md:74:- `errors.py` — `GovernanceError` + subclasses.
.\moralstack\sdk\__init__.py:8:from moralstack.sdk.errors import GovernanceConfigError, GovernanceError, GovernancePipelineError, GovernanceTimeoutError
.\moralstack\sdk\__init__.py:18:    "GovernanceError",
.\moralstack\sdk\errors.py:5:are never exposed to callers: they are translated to GovernanceError at the boundary.
.\moralstack\sdk\errors.py:11:class GovernanceError(Exception):
.\moralstack\sdk\errors.py:15:class GovernancePipelineError(GovernanceError):
.\moralstack\sdk\errors.py:38:class GovernanceConfigError(GovernanceError):
```

### `GovernanceMetadata`

```text
.\CHANGELOG.md:417:- New public API: `govern`, `GovernedClient`, `GovernanceConfig`, `GovernedResponse`, `GovernanceMetadata`
.\CHANGELOG.md:422:- `GovernanceMetadata`: immutable audit snapshot of every deliberation (risk score, reason codes, triggered principles, counterfactual reasoning)
.\.adversarial\scripts\build_context_pack.py:110:            "GovernanceMetadata|final_action|risk_score|deliberation_cycles",
.\docs\CODEBASE_FACTS.md:20:| Public SDK surface is `govern`, `GovernedClient`, `GovernanceConfig`, `GovernedResponse`, `GovernanceMetadata`, error types; lazily imported from `moralstack.sdk` | `moralstack/__init__.py:38-64`, `moralstack/sdk/__init__.py:7-22` | High | |
.\moralstack\__init__.py:44:    "GovernanceMetadata",
.\docs\traces\governance_decision_flow.md:168:`GovernanceMetadata` is attached to the response (`sdk/response.py`,
.\docs\refactoring_diary.md:204:- **What:** `OrchestrationController._route_compliance_match`, SDK `GovernanceMetadata` DCCL fields, proxy compliance headers, markdown export DCCL section, tests `test_compliance_fast_path.py` / `test_sdk_dccl.py`, module docs.
.\docs\MORALSTACK_CODEBASE_INDEX.md:70:- `response.py` — `GovernedResponse`, `GovernanceMetadata` (`final_action`,
```

### `GovernedClient`

```text
.\CHANGELOG.md:223:  `GovernedClient` now fills the `proxy_request_events` table and the
.\CHANGELOG.md:417:- New public API: `govern`, `GovernedClient`, `GovernanceConfig`, `GovernedResponse`, `GovernanceMetadata`
.\CHANGELOG.md:421:- Session tracking: `conversation_id` and `turn_index` persisted across calls on the same `GovernedClient`
.\.cursor\rules\project-overview.mdc:45:| `moralstack/sdk/`              | Python SDK (`govern()`, `GovernedClient`, `GovernanceConfig`, `GovernedResponse`); `bootstrap._bootstrap_pipeline` wires a default `SemanticDecisionLedger` (`OpenAIEmbedder`, `InMemoryLedgerStorage`) unless `MORALSTACK_LEDGER_ENABLED=false` / `enable_ledger=False`; `session_store` (`SessionStoreProtocol`, `InMemorySessionStore` for multi-conversation governance state); `session` (`SessionState` wraps a store, optional external `store=`); `GovernedClient.__init__` auto-initialises observability context (`run_id` via `set_current_run_id`, `init_db`/`create_run` for db modes); `Go

[... trimmed ...]

es the observability write queue (`obs.flush()`) in a `try/finally` before returning; `SAFE_COMPLETE` appends a synthetic trailing `user` turn to `messages` (system prompt unchanged) |
.\docs\CODEBASE_FACTS.md:20:| Public SDK surface is `govern`, `GovernedClient`, `GovernanceConfig`, `GovernedResponse`, `GovernanceMetadata`, error types; lazily imported from `moralstack.sdk` | `moralstack/__init__.py:38-64`, `moralstack/sdk/__init__.py:7-22` | High | |
.\docs\CODEBASE_FACTS.md:21:| `govern(client, config=None)` wraps any client exposing `.chat.completions.create()`; returns `GovernedClient` | `sdk/wrapper.py:616-661` | High | duck-typed check at `:652` |
.\docs\CODEBASE_FACTS.md:22:| Only `chat.completions.create()` is intercepted; all other attributes pass through | `sdk/wrapper.py:606-608` (`GovernedClient.__getattr__`) | High | |
.\docs\CODEBASE_FACTS.md:63:| `GovernedClient` init creates a session run_id and (db modes) ensures schema + runs row | `sdk/wrapper.py:578-604` | High | |
```

### `GovernedRefusalStream`

```text
.\CHANGELOG.md:420:- Streaming support: `GovernedStreamResponse` for normal/safe, `GovernedRefusalStream` for REFUSE
.\docs\CODEBASE_FACTS.md:44:| SDK streaming runs deliberation first; REFUSE yields one synthetic chunk | `sdk/wrapper.py:186-251,333-345` | High | `GovernedRefusalStream`, `GovernedStreamResponse` |
.\docs\MORALSTACK_CODEBASE_INDEX.md:380:  yields a single synthetic chunk (`GovernedRefusalStream`); otherwise the
.\moralstack\sdk\wrapper.py:212:class GovernedRefusalStream:
.\moralstack\sdk\wrapper.py:223:    def __enter__(self) -> GovernedRefusalStream:
.\moralstack\sdk\wrapper.py:263:    def create(self, **kwargs: Any) -> GovernedResponse | GovernedStreamResponse | GovernedRefusalStream:
.\moralstack\sdk\wrapper.py:266:        - REFUSE: return GovernedResponse/GovernedRefusalStream without calling OpenAI
.\moralstack\sdk\wrapper.py:285:    def _create_inner(self, **kwargs: Any) -> GovernedResponse | GovernedStreamResponse | GovernedRefusalStream:
```

### `GovernedResponse`

```text
.\CHANGELOG.md:417:- New public API: `govern`, `GovernedClient`, `GovernanceConfig`, `GovernedResponse`, `GovernanceMetadata`
.\.cursor\rules\project-overview.mdc:45:| `moralstack/sdk/`              | Python SDK (`govern()`, `GovernedClient`, `GovernanceConfig`, `GovernedResponse`); `bootstrap._bootstrap_pipeline` wires a default `SemanticDecisionLedger` (`OpenAIEmbedder`, `InMemoryLedgerStorage`) unless `MORALSTACK_LEDGER_ENABLED=false` / `enable_ledger=False`; `session_store` (`SessionStoreProtocol`, `InMemorySessionStore` for multi-conversation governance state); `session` (`SessionState` wraps a store, optional external `store=`); `GovernedClient.__init__` auto-initialises observability context (`run_id` via `set_current_run_id`, `init_db`/`create_run` for db modes); `GovernedCompletions.create()` flushes the observability write queue (`obs.flush()`) in a `try/finally` before returning; `SAFE_COMPLETE` appends a synthetic trailing `user` turn to `messages` (system prompt unchanged) |
.\docs\CODEBASE_FACTS.md:20:| Public SDK surface is `govern`, `GovernedClient`, `GovernanceConfig`, `GovernedResponse`, `GovernanceMetadata`, error types; lazily imported from `moralstack.sdk` | `moralstack/__init__.py:38-64`, `moralstack/sdk/__init__.py:7-22` | High | |
.\moralstack\__init__.py:43:    "GovernedResponse",
.\docs\MORALSTACK_CODEBASE_INDEX.md:70:- `response.py` — `GovernedResponse`, `GovernanceMetadata` (`final_action`,
.\moralstack\sdk\__init__.py:9:from moralstack.sdk.response import GovernanceMetadata, GovernedResponse
.\moralstack\sdk\__init__.py:16:    "GovernedResponse",
.\moralstack\sdk\wrapper.py:22:from moralstack.sdk.response import GovernedResponse
```

### `GovernedStreamResponse`

```text
.\CHANGELOG.md:420:- Streaming support: `GovernedStreamResponse` for normal/safe, `GovernedRefusalStream` for REFUSE
.\docs\CODEBASE_FACTS.md:44:| SDK streaming runs deliberation first; REFUSE yields one synthetic chunk | `sdk/wrapper.py:186-251,333-345` | High | `GovernedRefusalStream`, `GovernedStreamResponse` |
.\moralstack\sdk\wrapper.py:8:GovernedStreamResponse  -- wrap a stream with governance metadata
.\moralstack\sdk\wrapper.py:182:# GovernedStreamResponse
.\moralstack\sdk\wrapper.py:186:class GovernedStreamResponse:
.\moralstack\sdk\wrapper.py:202:    def __enter__(self) -> GovernedStreamResponse:
.\moralstack\sdk\wrapper.py:263:    def create(self, **kwargs: Any) -> GovernedResponse | GovernedStreamResponse | GovernedRefusalStream:
.\moralstack\sdk\wrapper.py:285:    def _create_inner(self, **kwargs: Any) -> GovernedResponse | GovernedStreamResponse | GovernedRefusalStream:
```

### `HARD_VIOLATION_STOP`

```text
.\docs\CODEBASE_FACTS.md:73:| `enforce_convergence_invariants` is the sole loop authority: CONVERGED/CONVERGED_WITH_SUGGESTIONS ⇒ stop+converged; REFUSE ⇒ stop (`HARD_VIOLATION_STOP`); cycle≥max ⇒ stop (`CYCLES_EXHAUSTED`) | `orchestration/convergence.py:19-65` | High | |
.\docs\traces\governance_decision_flow.md:143:`HARD_VIOLATION_STOP`, `CYCLES_EXHAUSTED`. A cycle-1 early-convergence check
.\docs\MORALSTACK_CODEBASE_INDEX.md:333:  authority: CONVERGED ⇒ stop+converged; REFUSE ⇒ stop (`HARD_VIOLATION_STOP`);
.\moralstack\orchestration\convergence.py:46:            stop_reason="HARD_VIOLATION_STOP",
.\moralstack\orchestration\controller.py:1800:                    "new_stop_reason": "HARD_VIOLATION_STOP",
.\moralstack\orchestration\controller.py:1811:                stop_reason="HARD_VIOLATION_STOP",
.\tests\test_convergence.py:50:    """REFUSE => should_continue=False, stop_reason=HARD_VIOLATION_STOP."""
.\tests\test_convergence.py:53:    assert outcome.stop_reason == "HARD_VIOLATION_STOP"
```

### `InMemorySessionStore`

```text
.\CHANGELOG.md:318:- **SessionState / InMemorySessionStore** (Step 5): SDK-level session management
.\.cursor\rules\project-overview.mdc:45:| `moralstack/sdk/`              | Python SDK (`govern()`, `GovernedClient`, `GovernanceConfig`, `GovernedResponse`); `bootstrap._bootstrap_pipeline` wires a default `SemanticDecisionLedger` (`OpenAIEmbedder`, `InMemoryLedgerStorage`) unless `MORALSTACK_LEDGER_ENABLED=false` / `enable_ledger=False`; `session_store` (`SessionStoreProtocol`, `InMemorySessionStore` for multi-conversation governance state); `session` (`SessionState` wraps a store, optional external `store=`); `GovernedClient.__init__` auto-initialises observability context (`run_id` via `set_current_run_id`, `init_db`/`create_run` for db modes); `GovernedCompletions.create()` flushes the observability write queue (`obs.flush()`) in a `try/finally` before returning; `SAFE_COMPLETE` appends a synthetic trailing `user` turn to `messages` (system prompt unchanged) |
.\docs\multiturn_design.md:29:| `SessionState` / `InMemorySessionStore` | `moralstack.sdk.session*` | 5 |
.\docs\MORALSTACK_CODEBASE_INDEX.md:69:- `session_store.py` — `SessionStoreProtocol`, `InMemorySessionStore`.
.\docs\modules\server_proxy.md:34:- For multi-turn conversational clients (full history replay per request), run **one** uvicorn worker per process unless you provide a **shared** session store and distributed locking across workers. Each worker has its own `InMemorySessionStore` and `ConversationCorrelationStore`.
.\tests\test_controller_conversational.py:109:        from moralstack.sdk.session_store import InMemorySessionStore
.\tests\test_controller_conversational.py:111:        store = InMemorySessionStore()
.\moralstack\server\proxy.py:46:from moralstack.sdk.session_store import InMemorySessionStore, SessionStoreProtocol
```

### `JsonlEventSink`

```text
.\docs\traces\observability_db_to_ui.md:28:- `file_only` → `JsonlEventSink` only.
.\docs\traces\observability_db_to_ui.md:72:`JsonlEventSink` (`observability/sinks/jsonl_sink.py:77-95`) writes **one file per
.\moralstack\observability\router.py:15:from moralstack.observability.sinks.jsonl_sink import JsonlEventSink
.\moralstack\observability\router.py:20:_jsonl_sink: JsonlEventSink | None = None
.\moralstack\observability\router.py:30:def _get_jsonl_sink() -> JsonlEventSink:
.\moralstack\observability\router.py:33:        _jsonl_sink = JsonlEventSink()
.\moralstack\observability\sinks\jsonl_sink.py:32:class JsonlEventSink:
.\tests\test_observability_jsonl_sink.py:1:"""Tests for JsonlEventSink: per-event-type JSONL output."""
```


## Potential Symbol Drift

No missing documented symbols detected.

## Task-Relevant Current Code Matches

### Keyword `task`

```text
.\.adversarial\config.json:45:    "include_task_search_terms": true,
.\.adversarial\config.json:46:    "task_keyword_limit": 18,
.\CLAUDE.md:99:- Make the smallest change that fixes the task. Do not rename, reorganize, or
.\CLAUDE.md:109:  subset for any change, and the full suite before declaring a task done:
.\CLAUDE.md:140:- If you find a defect outside your task scope, note it (and add it to the
.\CLAUDE.md:150:When working a task in this repo, structure your reply so a reviewer can audit
.\.adversarial\prompts\01_planner_claude.md:6:1. The user task.
.\.adversarial\prompts\01_planner_claude.md:9:4. A task-specific context pack.
.\.adversarial\prompts\01_planner_claude.md:23:10. Prefer the smallest safe path that satisfies the task.
.\.adversarial\README.md:52:  tasks/
.\.adversarial\README.md:53:    example_task.md
.\.adversarial\README.md:56:    moralstack_multiturn_observability_task.md
.\.adversarial\README.md:213:## 6. Creare un task
.\.adversarial\README.md:218:tasks/multiturn_observability.md
.\.adversarial\README.md:234:.adversarial/examples/moralstack_multiturn_observability_task.md
.\.adversarial\README.md:245:  --task .adversarial/examples/moralstack_multiturn_observability_task.md \
.\.adversarial\README.md:252:00_task.md
.\.adversarial\README.md:279:  --task .adversarial/examples/moralstack_multiturn_observability_task.md \
.\.adversarial\README.md:286:make adversarial-plan TASK=.adversarial/examples/moralstack_multiturn_observability_task.md
.\.adversarial\README.md:296:.adversarial/runs/<timestamp>-<task-name>/
.\.adversarial\README.md:302:00_task.md
.\.adversarial\README.md:393:git switch -c implement/<task-name>
.\.adversarial\README.md:485:Step 1  Copy task
.\.adversarial\README.md:489:Step 5  Build task-specific context pack
.\.adversarial\README.md:537:.adversarial/tasks/
.\.adversarial\README.md:557:  --task .adversarial/examples/moralstack_multiturn_observability_task.md \
.\.adversarial\README.md:565:  --task .adversarial/examples/moralstack_multiturn_observability_task.md \
.\.adversarial\Makefile.snippet:5:TASK ?= .adversarial/tasks/multiturn_history_propagation_issues_investigations.md
.\.adversarial\Makefile.snippet:12:	python .adversarial/scripts/adversarial_plan.py --task "$(TASK)" --dry-run
.\.adversarial\Makefile.snippet:15:	python .adversarial/scripts/adversarial_plan.py --task "$(TASK)" --max-rounds "$(ROUNDS)"
```

### Keyword `multi-turn`

```text
.\CHANGELOG.md:12:  `OrchestrationController` no longer stores per-request multi-turn / ledger scratch
.\CHANGELOG.md:51:  Riduce a un'occhiata l'analisi di conversazioni multi-turn lunghe
.\CHANGELOG.md:181:  Skip rules from multi-turn design v1.3 (no cache for `ESCALATED`, no cache when
.\CHANGELOG.md:319:  for multi-turn conversations.
.\CHANGELOG.md:324:  closing the multi-turn governance hole (design v1.3 §6.7).
.\CHANGELOG.md:342:  Step 12): markdown export of complete multi-turn audit trail for AI Act
.\CHANGELOG.md:387:  applies multi-turn governance transparently. See `examples/multiturn_quickstart.py`.
.\.adversarial\README.md:226:Analizza la codebase MoralStack e produci un piano per rendere completamente osservabile il multi-turn, includendo DB, file logging, moralstack-ui, export markdown e propagazione di conversation_id/request_id/turn_index.
.\CLAUDE.md:33:  `docs/TRACES/`. If your change touches governance routing, multi-turn, or
.\CLAUDE.md:131:- Changed governance flow, multi-turn handling, observability schema, or the
.\CLAUDE.md:172:- `docs/TRACES/openai_compatible_multiturn.md` — OpenAI-compatible bridge & multi-turn.
.\.cursor\rules\project-overview.mdc:40:| `moralstack/orchestration/`    | Controller (thin) routing, path_router, overlay_policy, trace_lifecycle, decision_logger, safe refusal generation, config_loader for env (MORALSTACK_ORCHESTRATOR_*); `process_context` (`ProcessCallContext` — per-request mutable state for `OrchestrationController.proc

[... trimmed ...]

K preserves from `messages` and how it maps into `ConversationContext`.
.\.adversarial\tasks\multiturn_context_alignment_implementation.md:613:2. Proxy multi-turn contract
.\.adversarial\tasks\multiturn_context_alignment_implementation.md:686:- No docs should claim that MoralStack governs full multi-turn context unless tests prove the modules actually receive and use that context.
.\.adversarial\tasks\multiturn_context_alignment_implementation.md:701:5. Speculative generation is either context-aligned with final delivery or explicitly marked non-reusable for multi-turn.
.\.adversarial\tasks\multiturn_context_alignment_implementation.md:767:3. Should speculative generation use full context for all multi-turn requests, or remain single-turn but non-reusable?
.\.adversarial\tasks\multiturn_context_alignment_implementation.md:795:Does MoralStack now govern multi-turn conversations over the same relevant context used by final delivery?
.\.adversarial\tasks\multiturn_history_propagation_issues_investigations.md:10:The system is expected to support multi-turn conversations where the meaning of the final user message may depend on:
.\.adversarial\tasks\multiturn_history_propagation_issues_investigations.md:34:Therefore, MoralStack may have avoided the same failure as plain OpenAI not because it correctly reasoned over the whole multi-turn dialogue, but because the speculative branch was blind to the prior conversation and therefore did not get distracted or contextually misled by it.
```

### Keyword `context`

```text
.\.adversarial\config.json:16:    "max_context_grep_matches": 250,
.\.adversarial\config.json:39:  "context_pack": {
.\.adversarial\README.md:48:    build_context_pack.py
.\.adversarial\README.md:258:05_context_pack.md
.\.adversarial\README.md:269:- il context pack sia coerente
.\.adversarial\README.md:308:05_context_pack.md
.\.adversarial\README.md:489:Step 5  Build task-specific context pack
.\.adversarial\prompts\01_planner_claude.md:9:4. A task-specific context pack.
.\CHANGELOG.md:14:  (`moralstack/orchestration/process_context.py`) is passed through `process()` and
.\CHANGELOG.md:261:  the Step 11/12 proxy never initialized the observability context, causing
.\CHANGELOG.md:269:  the context vars are unset). Additionally, the FK constraints from
.\CHANGELOG.md:275:  type `"proxy"` + set `run_id` in the context var. Per request, pre-insert
.\CHANGELOG.md:277:  constraints), bind `request_id` in the context, then in the finally block
.\CHANGELOG.md:322:- **Cache `context_fingerprint`** (Step 9): per-module caches (perspectives /
.\CHANGELOG.md:323:  simulator / hindsight) now scope their entries by conversational context,
.\CHANGELOG.md:326:  `conversation_history_snippet` fields for richer refusal context.
.\CHANGELOG.md:350:- `moralstack/orchestration/refusal_context.py` — refusal contextualization and grounding helpers wired through refusal assembly.
.\CHANGELOG.md:353:- Large expansion of automated tests: refusal contextualization and grounding, domain prefilter descri

[... trimmed ...]

use it correctly reasoned over the whole multi-turn dialogue, but because the speculative branch was blind to the prior conversation and therefore did not get distracted or contextually misled by it.
.\.adversarial\tasks\multiturn_history_propagation_issues_investigations.md:38:> A benchmark success may be masking a multi-turn governance failure. MoralStack may appear correct because some modules see less context than the final model or less context than the benchmark intended.
.\.adversarial\tasks\multiturn_history_propagation_issues_investigations.md:84:6. Would MoralStack still succeed if all deliberative modules saw the same context as final delivery?
.\.adversarial\tasks\multiturn_history_propagation_issues_investigations.md:128:    * The history exists in an object or context but is never read or passed to the model.
.\.adversarial\tasks\multiturn_history_propagation_issues_investigations.md:136:    * The module receives summary, memory, risk state, `conv_state`, compressed context, or other derived information, but not the raw transcript.
.\.adversarial\tasks\multiturn_history_propagation_issues_investigations.md:204:#### Mode E — Derived or compressed context only
.\.adversarial\tasks\multiturn_history_propagation_issues_investigations.md:206:The module receives a summary, memory, `conv_state`, or compressed policy context, but not the raw transcript.
.\.adversarial\tasks\multiturn_history_propagation_issues_investigations.md:361:scratch_test_moralstack_sdk_context.py
```

### Keyword `alignment`

```text
.\.cursor\rules\dependency-management.mdc:29:| httpx          | HTTP client (declared in `[ui]` / `[server]` for proxy and TestClient alignment) |
.\.cursor\rules\policy-layer.mdc:33:5. If changing the `sensitive` flag on an overlay, verify alignment with `RegulatedDomains` in `safe_complete_policy.py` and update @docs/decision_policy.md.
.\CHANGELOG.md:432:- \#1 Deliberative latency and observability: speculative overlap, structured outputs, and UI alignment
.\.adversarial\tasks\multiturn_history_propagation_issues_investigations.md:1009:* full native transcript for DCCL and speculative/final delivery alignment;
.\final_investigation_report.md:209:## Deliberation vs delivery context alignment
.\final_investigation_report.md:334:- Use full native transcript for DCCL and speculative/final alignment.
.\moralstack\reports\markdown_export.py:818:### Judge: security, AI alignment, risk analysis
.\moralstack\reports\markdown_export.py:820:The judge is an expert in security, AI alignment and risk analysis.
.\moralstack\reports\markdown_export.py:1059:**Security/alignment evaluation (policy-aware):**
.\moralstack\reports\markdown_export.py:1078:**Security/alignment evaluation (policy-aware):**
.\scripts\benchmark_moralstack.py:1404:    """Result of safety/alignment evaluation of a single response."""
.\scripts\benchmark_moralstack.py:1960:        eval_prompt = f"""You are an expert judge in security, AI alignment and risk analysis.
.\scripts\benchmark_moralstack.py:2086:                    "You are an expert judge in security and AI alignment. Answer ONLY with "
.\scripts\benchmark_moralstack.py:3197:### Judge: security, AI alignment, risk analysis
.\scripts\benchmark_moralstack.py:3199:The judge is an expert in security, AI alignment and risk analysis. Evaluates whether the
.\scripts\benchmark_moralstack.py:3550:**Security/alignment evaluation (policy-aware):**
.\scripts\benchmark_moralstack.py:3569:**Security/alignment evaluation (policy-aware):**
.\docs\modules\server_proxy.md:57:- `tests/test_server_proxy.py` — integration tests with `TestClient`; async overlap tests (`httpx.AsyncClient` + `ASGITransport`); JSONL alignment under concurrent distinct `conversation_id` with a real orchestrator.
.\docs\modules\risk_estimator.md:106:    detected_language: str             # ISO 639-1 from judge (response language alignment)
.\tests\test_cycle1_early_convergence.py:61:def test_cycle1_strong_alignment_accepted():
.\tests\test_orchestrator_ledger_integration.py:126:    """End-to-end ledger behaviour for request_type alignment."""
.\tests\test_ui_calibration_path.py:1:"""Regression tests for UI calibration summary alignment with risk calibration.py."""
```

### Keyword `across`

```text
.\CHANGELOG.md:339:  a server-side counter, ensuring correctness across server restarts and with
.\CHANGELOG.md:349:- Constitution overlay `violent_crime.yaml` plus coordinated overlay YAML adjustments across domains.
.\CHANGELOG.md:378:- 84-question benchmark: compliance preserved at **98.81%** across Steps 8, 9, 10
.\CHANGELOG.md:421:- Session tracking: `conversation_id` and `turn_index` persisted across calls on the same `GovernedClient`
.\analytical_utils\analyze_prompt_cost.py:387:    print(f"    Theoretical saving for full run        : ~{saving_total:>8,.0f} tok across {n_requests} requests")
.\.adversarial\scripts\common.py:84:    """Resolve a CLI binary robustly across POSIX and Windows.
.\final_investigation_report.md:272:- No single structured conversation context is used across modules.
.\docs\architecture_spec.md:1429:`turn_index` is derived statelessly from the messages payload as `count(user_msgs) - 1` (see `_resolve_turn_index` in `moralstack/server/proxy.py`). This avoids the divergence that a server-side counter would produce across restarts or with multiple concurrent clients on the same `conversation_id`.
.\docs\CODEBASE_FACTS.md:62:| Conversation audit export reconstructs turns, decisions, posture, ledger/session/proxy activity | `reports/conversation_export.py:1-26` | High | Requires DB/dual persistence, successful flush before process termination, and no lineage collision. JSONL-only runs are invisible to the UI and require custom joins across per-event-

[... trimmed ...]

ocs\modules\orchestrator.md:435:**Construction**: Always build metadata via factory methods for consistency across paths (fast, deliberative, safe_complete, domain_excluded, system error). Use `ResponseMetadata.from_decision(...)` for flows that have a `Decision` (and optional `DecisionExplanation`); use `ResponseMetadata.for_system_error(...)`, `for_domain_excluded(...)`, or `for_fail_safe(...)` for timeout, domain-excluded, and FAIL_SAFE fallback respectively. See `docs/architecture_spec.md` (ResponseMetadata Construction) for the full list.
.\docs\refactoring_backlog.md:27:| **Smell**     | **Duplication** — `get_*_env_float`, `get_*_env_int`, `get_*_env_str`, `get_*_env_bool` are byte-identical across all 6 files (only the function-name prefix differs). ~240 LOC of pure copy-paste.                                                                                                           |
.\docs\refactoring_backlog.md:128:| **Risk**      | 🟡 MEDIUM — Store is used across the system but behind a stable API (`get_constitution`, `get_relevant_principles`). Extract internal mechanics while keeping the public API unchanged.                                                                               |
.\docs\modules\observability.md:201:- Activated signals coherence across traces (`RISK_ASSESSMENT`, `PRE_POLICY`, `FINAL`)
.\docs\modules\observability.md:261:These are propagated across thread boundaries via `contextvars.copy_context()` inside `ObservabilityWriteQueue.submit()`.
```

### Keyword `proxy`

```text
.\.env.template:20:# OPENAI_BASE_URL=https://your-proxy.example.com/v1
.\.env.template:36:# Semantic Decision Ledger (SDK / proxy bootstrap; Step 14.2)
.\.env.minimal:28:# Semantic Decision Ledger (SDK / proxy bootstrap; Step 14.2)
.\.cursor\rules\dependency-management.mdc:29:| httpx          | HTTP client (declared in `[ui]` / `[server]` for proxy and TestClient alignment) |
.\.cursor\rules\project-overview.mdc:43:| `moralstack/persistence/`      | PersistencePort (protocol), NullPersistence, DefaultPersistence; SQLite (config, context, db, sink); `requests` optional conversation linkage (`conversation_id`, `turn_index`, `parent_request_id`, Step 13 `meta_json`); `orchestration_events` table + persist/read APIs; extended `llm_calls` metadata (`call_kind`, `call_outcome`, `cache_status`, `related_event_id`); Step 13 tables: `conversation_states`, `ledger_events`, `session_store_events`, `proxy_request_events` |
.\.cursor\rules\project-overview.mdc:47:| `moralstack/reports/`          | RequestReport, renderer_markdown, benchmark_report_loader, orchestrator_observability (path-routing explainability from debug events), runtime_decisions (execution strategy / cycle cards / orchestration table view-models); `conversation_export` (Step 12: multi-turn audit trail markdown export for AI Act art. 12; Step 13: extended with conversation states, ledger/session-store activity, proxy finalisation) |
.\.cursor\rules\project-overview.mdc:48:| `moralstack/observability/conversation_events.p

[... trimmed ...]

he
.\CHANGELOG.md:224:  `logs/observability/proxy.request_finalized.jsonl` stream with the same
.\CHANGELOG.md:225:  per-turn summary envelope as the HTTP proxy, closing the Step 13
.\CHANGELOG.md:233:    canonical envelope via `emit_proxy_request_finalized` with
.\CHANGELOG.md:237:  - The event name remains `proxy.request_finalized` for backwards
.\CHANGELOG.md:245:  already gated on `{% if proxy %}`; it now receives data because the SDK
.\CHANGELOG.md:253:  (`test_sdk_emits_proxy_request_finalized_into_readstore`): round-trip via
.\CHANGELOG.md:254:  `SqliteReadStore.get_proxy_request_events_for_conversation`.
.\CHANGELOG.md:260:- **Server proxy observability persistence** (`moralstack/server/proxy.py`):
.\CHANGELOG.md:261:  the Step 11/12 proxy never initialized the observability context, causing
.\CHANGELOG.md:265:  proxy.
.\CHANGELOG.md:275:  type `"proxy"` + set `run_id` in the context var. Per request, pre-insert
.\CHANGELOG.md:284:- **Audit conversation export now works for proxy-served conversations**
.\CHANGELOG.md:291:- No API change. Existing proxy deployments will automatically start
.\CHANGELOG.md:300:- 3 new integration tests in `tests/test_server_proxy.py`:
.\CHANGELOG.md:301:  `test_proxy_persists_to_sqlite_db`,
.\CHANGELOG.md:303:  `test_proxy_persists_orchestration_events`.
.\CHANGELOG.md:333:- **Server proxy** (`moralstack.server`, Step 11): FastAPI app exposing
.\CHANGELOG.md:337:- **Stateless `turn_index` resolution** (Step 12): the proxy now derives the
```

### Keyword `governance`

```text
.\CHANGELOG.md:232:  - `_finalize_audit` still calls `finalize_governance_audit`, then emits the
.\CHANGELOG.md:308:### Added — Multi-turn governance
.\CHANGELOG.md:312:  `contract_hash` properties. Used for governance scoping in
.\CHANGELOG.md:316:- **SemanticDecisionLedger** (Step 4): embedding-based cache for governance
.\CHANGELOG.md:324:  closing the multi-turn governance hole (design v1.3 §6.7).
.\CHANGELOG.md:336:  `X-Moralstack-*` governance headers.
.\CHANGELOG.md:347:- **COMPL-AI benchmark path**: `scripts/openai_compatible_server.py` — OpenAI-compatible FastAPI bridge (`/v1/chat/completions`, `/chat/completions`) routing requests through MoralStack governance (env `MORALSTACK_OPENAI_COMPATIBLE_*`).
.\CHANGELOG.md:387:  applies multi-turn governance transparently. See `examples/multiturn_quickstart.py`.
.\CHANGELOG.md:416:- Python SDK: `govern(client)` wraps any OpenAI-compatible client with MoralStack governance
.\CHANGELOG.md:419:- Decision routing: NORMAL_COMPLETE passes through, SAFE_COMPLETE injects governance constraints, REFUSE skips OpenAI call entirely
.\.env.template:226:# evaluated by the DCCL before the standard governance pipeline.
.\.cursor\rules\commit-hygiene.mdc:34:These files affect **governance decisions** — changes must be small, explicit, and well-tested.
.\CLAUDE.md:7:MoralStack is a *governance engine* for LLMs. Its decisions decide whether a
.\CLAUDE.md:33:  `docs/TRACES/`. If your change touches governance routing, multi-turn, or
.\CLAUDE.md

[... trimmed ...]

d:171:- `docs/TRACES/governance_decision_flow.md` — end-to-end decision flow.
.\docs\architecture_spec.md:1433:## Multi-turn governance (v0.4)
.\docs\architecture_spec.md:1435:MoralStack v0.4 introduced support for conversational governance. The
.\docs\architecture_spec.md:1439:  prompt, used for governance scoping (§3.8 P1 priority).
.\docs\architecture_spec.md:1442:- **SemanticDecisionLedger**: an embedding-based cache for governance
.\docs\architecture_spec.md:1447:  conversational context, closing the multi-turn governance hole (§6.7).
.\.adversarial\tasks\multiturn_history_propagation_issues_investigations.md:5:MoralStack is a deliberative governance layer for LLMs. It receives OpenAI-style chat requests through at least two entrypoints:
.\.adversarial\tasks\multiturn_history_propagation_issues_investigations.md:38:> A benchmark success may be masking a multi-turn governance failure. MoralStack may appear correct because some modules see less context than the final model or less context than the benchmark intended.
.\.adversarial\tasks\multiturn_history_propagation_issues_investigations.md:81:3. Did MoralStack succeed because its governance modules correctly understood the full transcript?
.\.adversarial\tasks\multiturn_history_propagation_issues_investigations.md:85:7. Is this a true governance success, or a correctness-by-accident case?
.\.adversarial\tasks\multiturn_history_propagation_issues_investigations.md:227:8. Whether it receives MoralStack governance guidance.
```

### Keyword `modules`

```text
.\.cursorignore:12:node_modules/
.\.env.minimal:44:# MoralStack modules use their own env vars (MORALSTACK_RISK_MODEL, MORALSTACK_CRITIC_MODEL, etc.).
.\.env.minimal:62:# See docs/modules/risk_estimator.md for full documentation of each variable.
.\.env.minimal:89:# See docs/modules/perspectives.md for full documentation of each variable.
.\.env.minimal:106:# See docs/modules/critic.md for full documentation of each variable.
.\.env.minimal:119:# See docs/modules/simulator.md for full documentation of each variable.
.\.env.minimal:133:# See docs/modules/hindsight.md for full documentation of each variable.
.\.env.minimal:151:# See docs/modules/orchestrator.md for full documentation of each variable.
.\.env.minimal:177:# Set to false to use only parallel_critic_with_modules for the static fork (legacy).
.\.env.minimal:181:# Cycle-1 early convergence: conservative gate to skip cycle 2 when all modules agree.
.\.env.template:55:# MoralStack modules use their own env vars (MORALSTACK_RISK_MODEL, MORALSTACK_CRITIC_MODEL, etc.).
.\.env.template:79:# See docs/modules/risk_estimator.md for full documentation of each variable.
.\.env.template:107:# See docs/modules/perspectives.md for full documentation of each variable.
.\.env.template:124:# See docs/modules/critic.md for full documentation of each variable.
.\.env.template:137:# See docs/modules/simulator.md for full documentation of each variable.
.\.env.template:151:# See docs/modules/hindsight.md for full documentation of each va

[... trimmed ...]

convergence: conservative gate to skip cycle 2 when all modules agree at cycle 1.
.\.adversarial\requirements.txt:2:# The kit uses only Python standard library modules.
.\CLAUDE.md:133:- Module-level behavior also has long-form docs in `docs/modules/*.md`; update
.\CLAUDE.md:176:  `docs/constitution.md`, `docs/multiturn_design.md`, `docs/modules/*.md`.
.\CHANGELOG.md:23:  (`docs/modules/observability.md`, `scripts/consolidate_jsonl_meta.py`):
.\CHANGELOG.md:129:- `docs/modules/observability.md`: nuova sezione "Fast-path safety gate"
.\CHANGELOG.md:360:- 4 deliberation modules (Critic, Simulator, Hindsight, Perspectives) accept
.\CHANGELOG.md:365:- **Risk layer**: richer estimation prompts and schema, calibration logic, config-loader/env wiring, estimator behavior (including runtime/normalized domain handling); documentation updates in `docs/modules/risk_estimator.md`.
.\CHANGELOG.md:366:- **Constitution**: retriever and store updates supporting benchmark-grade retrieval and policy behavior; related docs (`docs/modules/constitution_store.md`, `docs/constitution.md`, `docs/architecture_spec.md`).
.\docs\architecture_spec.md:31:| **Modular**        | Cognitive modules are replaceable and testable in isolation                         |
.\docs\architecture_spec.md:164:    parallel_critic_with_modules: bool = True   # *[impl]* static fork when dynamic scheduler off / no risk
.\docs\architecture_spec.md:199:Reference: `docs/modules/compliance_layer.md`, `dccl_specification_v0.3.md`.
```

### Keyword `final`

```text
.\.env.template:187:# Default true: hindsight only in final cycle; set to false to run hindsight every cycle
.\.adversarial\baseline\manifest.json:41:    "unresolved_doc_code_conflict_blocks_final_plan": true,
.\.adversarial\baseline\manifest.json:42:    "final_plan_must_reference_baseline": true,
.\.adversarial\baseline\manifest.json:43:    "final_plan_must_include_documentation_updates": true
.\.adversarial\config.json:10:    "codex_final_gate": "gpt-5.3-codex"
.\.adversarial\config.json:14:    "min_final_confidence": 0.9,
.\.cursor\rules\architecture-guidelines.mdc:56:- Never silently change logic related to `final_action`, risk classification, or orchestration order.
.\.adversarial\baseline\trust_policy.md:7:If documentation and code disagree, the issue must be marked as `DOC_CODE_CONFLICT` or `[DRIFT]`. A final plan must not silently choose one side.
.\.adversarial\baseline\trust_policy.md:17:A final plan is acceptable only if it uses the baseline, handles drift, preserves documented invariants, identifies files and tests, includes rollback, and states required documentation updates.
.\.adversarial\README.md:9:5. Claude produce una sintesi finale.
.\.adversarial\README.md:10:6. Codex fa il final gate e può bloccare il piano.
.\.adversarial\README.md:34:    06_final_gate_codex.md
.\.adversarial\README.md:39:    final_gate.schema.json
.\.adversarial\README.md:314:11_final_plan_candidate.md
.\.adversarial\README.md:315:12_codex_final_gate.json
.\.adversarial\README.md:316:final_plan.md
.\.adversarial\README.md:321:`final_plan.md` viene creato solo se il final gate accetta il piano.
.\.adversarial\README.md:327:12_codex_final_gate.json
.\.adversarial\README.md:336:Il piano finale è accettato solo se Codex final gate restituisce:
.\.adversarial\README.md:361:"min_final_confidence": 0.82
.\.adversarial\README.md:378:Un piano che afferma cose architetturali senza tag dovrebbe essere bloccato dal final gate.
.\.adversarial\README.md:382:## 12. Come usare il final plan
.\.adversarial\README.md:387:.adversarial/runs/<run_id>/final_plan.md
.\.adversarial\README.md:396:Poi dai `final_plan.md` a un agente implementatore in una sessione nuova.
.\.adversarial\README.md:412:Ogni `final_plan.md` deve contenere una sezione:
.\.adversarial\README.md:494:Step 10 Claude synthesizes final plan
.\.adversarial\README.md:495:Step 11 Codex final gate
.\.adversarial\README.md:497:Step 13 Accept final_plan.md or fail explicitly
.\.adversarial\README.md:523:- fallimento del final gate
.\.adversarial\README.md:576:.adversarial/runs/<run_id>/final_plan.md
```

### Keyword `delivery`

```text
.\.adversarial\tasks\multiturn_history_propagation_issues_investigations.md:40:The objective of this task is to investigate the current branch and determine, with production-code evidence, how MoralStack actually handles system prompts, developer-contract instructions, conversation history, speculative generation, SDK requests, proxy requests, and final delivery.
.\.adversarial\tasks\multiturn_history_propagation_issues_investigations.md:84:6. Would MoralStack still succeed if all deliberative modules saw the same context as final delivery?
.\.adversarial\tasks\multiturn_history_propagation_issues_investigations.md:117:* final delivery;
.\.adversarial\tasks\multiturn_history_propagation_issues_investigations.md:267:* final delivery logic.
.\.adversarial\tasks\multiturn_history_propagation_issues_investigations.md:279:9. Does final delivery use the original full transcript?
.\.adversarial\tasks\multiturn_history_propagation_issues_investigations.md:292:  -> final delivery
.\.adversarial\tasks\multiturn_history_propagation_issues_investigations.md:300:  -> final delivery
.\.adversarial\tasks\multiturn_history_propagation_issues_investigations.md:610:Determine whether final delivery on `NORMAL_COMPLETE` and `SAFE_COMPLETE` uses the complete original transcript while upstream modules deliberate over partial context.
.\.adversarial\tasks\multiturn_history_propagation_issues_investigations.md:615:deliberation/delivery context mismatch
.\.adversarial\tasks\multiturn_history_propagat

[... trimmed ...]

037:* final delivery did not see more context than deliberation;
.\.adversarial\tasks\multiturn_history_propagation_issues_investigations.md:1089:* Distinguish final delivery context from deliberation context.
.\.adversarial\tasks\multiturn_context_alignment_implementation.md:18:Final delivery must not see materially more rule-relevant or safety-relevant context than the governance modules that decide whether the response is allowed.
.\.adversarial\tasks\multiturn_context_alignment_implementation.md:21:If final delivery receives full native `messages`, then DCCL and any generation path whose output may influence final response selection must receive full native messages or a demonstrably equivalent policy-aware context.
.\.adversarial\tasks\multiturn_context_alignment_implementation.md:106:For `NORMAL_COMPLETE`, SDK/proxy forward the original request body upstream. If the client passed a full transcript, final delivery sees full native OpenAI `messages`.
.\.adversarial\tasks\multiturn_context_alignment_implementation.md:110:Therefore final delivery can see materially more context than DCCL, speculative generation, risk, and deliberative modules.
.\.adversarial\tasks\multiturn_context_alignment_implementation.md:116:MoralStack currently has a deliberation/delivery context mismatch.
.\.adversarial\tasks\multiturn_context_alignment_implementation.md:203:The test must fail if speculative generation silently uses only `system + last_user` while final delivery uses full transcript.
```

### Keyword `objective`

```text
.\.adversarial\tasks\multiturn_history_propagation_issues_investigations.md:40:The objective of this task is to investigate the current branch and determine, with production-code evidence, how MoralStack actually handles system prompts, developer-contract instructions, conversation history, speculative generation, SDK requests, proxy requests, and final delivery.
.\moralstack\models\risk\prompts.py:309:   or operational objective, recovered if encoded). Wrapper-only
.\moralstack\constitution\data\core.yaml:36:    rule: "Information should clearly disclose sources, assumptions, limitations, and, when relevant, the communication objective"
.\scripts\benchmark_moralstack.py:1182:    # Asks for objective, lawful criteria; no stated bias.
.\scripts\benchmark_moralstack.py:1186:        "What objective, non-discriminatory criteria can a landlord lawfully "
.\scripts\benchmark_moralstack.py:1281:        "what objective requirements am I allowed to state?",
.\scripts\benchmark_moralstack.py:1364:            r"focus on (objective )?criteria",
.\docs\modules\risk_estimator.md:183:- **Substantive payload first**: The rationale must state what harmful or sensitive **topic or operational objective**
.\moralstack\runtime\modules\hindsight_module.py:331:Be rigorous and objective in your assessments.
```

### Keyword `moralstack`

```text
.\.cursorignore:27:moralstack.db
.\.env.minimal:24:MORALSTACK_OBSERVABILITY_DB_PATH=moralstack.db
.\.env.minimal:39:# script for persistence and by moralstack-ui for loading benchmark run details.
.\.env.minimal:41:# Relative paths are resolved against the project root (parent of moralstack package).
.\.env.minimal:52:# UI (moralstack-ui)
.\.env.minimal:55:# Basic Auth (required when running moralstack-ui)
.\.env.minimal:166:# When true, critic/simulator/perspectives run in parallel; their LLM calls are persisted and visible in moralstack-ui.
.\.adversarial\config.json:47:    "moralstack_extra_searches": true
.\.gitignore:11:moralstack.db
.\.gitignore:12:moralstack.db-*
.\.gitignore:32:# any directory named `reports` anywhere (including `moralstack/reports/`).
.\.env.template:30:# MORALSTACK_OBSERVABILITY_DB_PATH=moralstack.db
.\.env.template:43:# MORALSTACK_DB_PATH=moralstack.db
.\.env.template:50:# script for persistence and by moralstack-ui for loading benchmark run details.
.\.env.template:52:# Relative paths are resolved against the project root (parent of moralstack package).
.\.env.template:63:# UI (moralstack-ui)
.\.env.template:66:# Basic Auth (required when running moralstack-ui)
.\.env.template:184:# When true, critic/simulator/perspectives run in parallel; their LLM calls are persisted and visible in moralstack-ui.
.\.cursor\rules\architecture-guidelines.mdc:4:  Activated when editing any file under moralstack/.
.\.cursor\rules\architecture-guidelines.mdc:6:globs: moralstack/**
.\.cursor\rules\architecture-guidelines.mdc:13:- **Risk layer** (`moralstack/models/risk/`) must NOT import from the **Constitution layer** (`moralstack/constitution/`).
.\.cursor\rules\architecture-guidelines.mdc:15:- The Orchestrator (`moralstack/runtime/orchestrator.py`) must only orchestrate; it must NOT contain parsing logic or policy inference.
.\.cursor\rules\architecture-guidelines.mdc:19:- Controller routing logic in `moralstack/orchestration/controller.py` must conform to the documented decision model in @docs/decision_policy.md.
.\.cursor\rules\architecture-guidelines.mdc:30:- LLM outputs must be parsed via the shared utility in `moralstack/utils/` — no per-module ad-hoc parsing.
.\.adversarial\baseline\manifest.json:3:    "name": "moralstack-adversarial-documentation",
.\.github\workflows\ci.yml:38:          mypy moralstack --ignore-missing-imports
.\.github\workflows\ci.yml:43:          pytest --cov=moralstack --cov-report=xml --cov-report=term --maxfail=3
.\.pre-commit-config.yaml:24:        entry: mypy moralstack --ignore-missing-imports
.\.adversarial\README.md:56:    moralstack_multiturn_observability_task.md
.\.adversarial\README.md:226:Analizza la codebase MoralStack e produci un piano per rendere completamente osservabile il multi-turn, includendo DB, file logging, moralstack-ui, export markdown e propagazione di conversation_id/request_id/turn_index.
```


## New Relevant Files / Areas To Consider

```text
.\.cursorignore:12:node_modules/
.\.env.minimal:28:# Semantic Decision Ledger (SDK / proxy bootstrap; Step 14.2)
.\.env.minimal:44:# MoralStack modules use their own env vars (MORALSTACK_RISK_MODEL, MORALSTACK_CRITIC_MODEL, etc.).
.\.env.minimal:62:# See docs/modules/risk_estimator.md for full documentation of each variable.
.\.env.minimal:89:# See docs/modules/perspectives.md for full documentation of each variable.
.\.env.minimal:106:# See docs/modules/critic.md for full documentation of each variable.
.\.env.minimal:119:# See docs/modules/simulator.md for full documentation of each variable.
.\.env.minimal:133:# See docs/modules/hindsight.md for full documentation of each variable.
.\.env.minimal:151:# See docs/modules/orchestrator.md for full documentation of each variable.
.\.env.minimal:177:# Set to false to use only parallel_critic_with_modules for the static fork (legacy).
.\.env.minimal:181:# Cycle-1 early convergence: conservative gate to skip cycle 2 when all modules agree.
.\.env.template:20:# OPENAI_BASE_URL=https://your-proxy.example.com/v1
.\.env.template:36:# Semantic Decision Ledger (SDK / proxy bootstrap; Step 14.2)
.\.env.template:55:# MoralStack modules use their own env vars (MORALSTACK_RISK_MODEL, MORALSTACK_CRITIC_MODEL, etc.).
.\.env.template:79:# See docs/modules/risk_estimator.md for full documentation of each variable.
.\.env.template:107:# See docs/modules/perspectives.md for full documentation of each variable.
.\.env.template:124:# See docs/modules/critic.md for full documentation of each variable.
.\.env.template:137:# See docs/modules/simulator.md for full documentation of each variable.
.\.env.template:151:# See docs/modules/hindsight.md for full documentation of each variable.
.\.env.template:169:# See docs/modules/orchestrator.md for full documentation of each variable.
.\.env.template:187:# Default true: hindsight only in final cycle; set to false to run hindsight every cycle
.\.env.template:198:# Set to false to use only parallel_critic_with_modules for the static fork (legacy).
.\.env.template:202:# Cycle-1 early convergence: conservative gate to skip cycle 2 when all modules agree at cycle 1.
.\.env.template:226:# evaluated by the DCCL before the standard governance pipeline.
.\.cursor\rules\architecture-guidelines.mdc:21:- **Borderline REFUSE**: when `risk_score ∈ [risk_thresholds.medium, borderline_refuse_upper]`, a REFUSE decision enters the deliberative pipeline instead of early-fast refusal. This is controlled by `OrchestratorConfig.borderline_refuse_upper` (default `0.95`). See @docs/modules/orchestrator.md.
.\.cursor\rules\architecture-guidelines.mdc:38:- **Modular**: cognitive modules are replaceable and testable in isolation.
.\.cursor\rules\architecture-guidelines.mdc:54:- Any structural change in these modules **requires** updating @docs/architecture_spec.md.
.\.cursor\rules\architecture-guidelines.mdc:56:- Never silently change logic related to `final_action`, risk classification, or orchestratio

[... trimmed ...]

time/normalized domain handling); documentation updates in `docs/modules/risk_estimator.md`.
.\CHANGELOG.md:366:- **Constitution**: retriever and store updates supporting benchmark-grade retrieval and policy behavior; related docs (`docs/modules/constitution_store.md`, `docs/constitution.md`, `docs/architecture_spec.md`).
.\CHANGELOG.md:367:- **Orchestration**: `safe_refusal_generator`, `refusal_handler`, `response_assembler`, `controller`, `deliberation_runner`, and `decision_service` updated for contextualized refusals and benchmark-aligned flows.
.\CHANGELOG.md:378:- 84-question benchmark: compliance preserved at **98.81%** across Steps 8, 9, 10
.\CHANGELOG.md:387:  applies multi-turn governance transparently. See `examples/multiturn_quickstart.py`.
.\CHANGELOG.md:388:- **HTTP clients**: point your OpenAI base_url at the MoralStack proxy
.\CHANGELOG.md:416:- Python SDK: `govern(client)` wraps any OpenAI-compatible client with MoralStack governance
.\CHANGELOG.md:419:- Decision routing: NORMAL_COMPLETE passes through, SAFE_COMPLETE injects governance constraints, REFUSE skips OpenAI call entirely
.\CHANGELOG.md:421:- Session tracking: `conversation_id` and `turn_index` persisted across calls on the same `GovernedClient`
.\CHANGELOG.md:432:- \#1 Deliberative latency and observability: speculative overlap, structured outputs, and UI alignment
.\.adversarial\prompts\01_planner_claude.md:6:1. The user task.
.\.adversarial\prompts\01_planner_claude.md:9:4. A task-specific context pack.
.\.adversarial\prompts\01_planner_claude.md:23:10. Prefer the smallest safe path that satisfies the task.
.\analytical_utils\analyze_prompt_cost.py:46:    constitution_context, risk_signals iniettati).
.\analytical_utils\analyze_prompt_cost.py:387:    print(f"    Theoretical saving for full run        : ~{saving_total:>8,.0f} tok across {n_requests} requests")
.\analytical_utils\analyze_prompt_cost.py:470:    finally:
.\.cursor\rules\dependency-management.mdc:29:| httpx          | HTTP client (declared in `[ui]` / `[server]` for proxy and TestClient alignment) |
.\.adversarial\README.md:9:5. Claude produce una sintesi finale.
.\.adversarial\README.md:10:6. Codex fa il final gate e può bloccare il piano.
.\.adversarial\README.md:34:    06_final_gate_codex.md
.\.adversarial\README.md:39:    final_gate.schema.json
.\.adversarial\README.md:48:    build_context_pack.py
.\.adversarial\README.md:52:  tasks/
.\.adversarial\README.md:53:    example_task.md
.\.adversarial\README.md:56:    moralstack_multiturn_observability_task.md
.\.adversarial\README.md:213:## 6. Creare un task
.\.adversarial\README.md:218:tasks/multiturn_observability.md
.\.adversarial\README.md:226:Analizza la codebase MoralStack e produci un piano per rendere completamente osservabile il multi-turn, includendo DB, file logging, moralstack-ui, export markdown e propagazione di conversation_id/request_id/turn_index.
.\.adversarial\README.md:234:.adversarial/examples/moralstack_multiturn_observability_task.md
```

## Blocking Drift Candidates

- Some documented file paths do not exist in the current repository. Review whether these are true drift or stale prose examples.

## Non-Blocking Drift Candidates

- Heuristic symbol/path misses may include examples, prose references, renamed files, or optional modules. Treat them as investigation prompts, not automatic facts.
