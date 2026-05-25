# Codex Plan B

## 1. Objective

Fix MoralStack’s multi-turn context alignment so SDK/proxy governance modules use the same rule-relevant transcript context that final delivery can see, with regression tests and observability proving the effective context per module. [DOC]

Primary invariant: final delivery must not receive materially broader rule/safety-relevant context than DCCL or any generation path whose output can influence final response selection. [DOC]

## 2. Baseline Interpretation

MoralStack’s documented invariants require decision/generation separation, byte-identical preservation of user/developer system prompts, hard-signal supremacy, single-turn byte equality when no contract/history exists, and best-effort observability that cannot change decisions. [DOC]

The documented OpenAI-compatible proxy path treats OpenAI `messages` as the client-sent history and derives `developer_contract`, `conversation_history`, `conversation_id`, and stateless `turn_index`. [DOC]

The trusted investigation changes the priority: prior “history used” documentation is insufficient because the raw native transcript is not retained in `ProcessedRequest`, DCCL does not consume history, speculative generation sees only system plus final user, and deliberative/risk modules truncate or serialize history. [DOC][DRIFT]

## 3. Repository Evidence

SDK parsing currently extracts the last user prompt, developer contract, and simplified prior turns, but does not retain raw `messages` in `ProcessedRequest`. Relevant files: `moralstack/sdk/wrapper.py`, `moralstack/orchestration/types.py`. [CODE]

Proxy parsing mirrors SDK extraction and forwards the original body upstream for final delivery, so upstream final generation may see full native `messages` while governance sees reduced fields. Relevant files: `moralstack/server/proxy.py`, `moralstack/orchestration/types.py`. [CODE]

DCCL is invoked before routing and can drive `COMPLIANCE_FAST_PATH`; current evidence says it evaluates developer contract plus current prompt and ignores `conversation_history`, causing `NO_MATCH` for the canary despite prior auth being present. [CODE][DOC]

Speculative overlap can produce a draft before final routing; for proxy `COMPLIANCE_FAST_PATH`, that draft may become the final returned content. Current evidence says speculative generation uses `policy.generate(prompt=..., system=...)`, so it is not context-aligned for multi-turn requests. [CODE][DOC]

Existing test areas already cover multi-turn, SDK, proxy, DCCL, speculative overlap, risk context, and observability: `tests/test_multiturn_context_propagation.py`, `tests/test_sdk_dccl.py`, `tests/test_server_proxy.py`, `tests/test_controller_speculative_lazy.py`, `tests/test_controller_risk_context_propagation.py`, `tests/test_deliberative_modules_context_propagation.py`, and observability suites. [TEST]

## 4. Drift Findings Relevant To This Task

DOC_CODE_CONFLICT: `docs/traces/openai_compatible_multiturn.md` says full history is passed into governance via `ProcessedRequest`, but current evidence shows raw native roles are dropped and DCCL/speculative generation do not consume full history. [DRIFT]

DOC_CODE_CONFLICT: README/docs claim “full support for conversational governance,” while current behavior allows final delivery to see full native transcript and governance modules to see no history, truncated snippets, or `system + last_user`. [DRIFT]

DOC_CODE_CONFLICT: docs describe COMPL-AI `llm_rules` DCCL fast-path as contract-aware over multi-turn history, but the canary shows DCCL returns `NO_MATCH` while claiming prior auth is absent. [DRIFT]

Non-blocking path drift: baseline references `docs/TRACES/*`, current repository has renamed lowercase `docs/traces/*`. Update docs using current lowercase paths. [DRIFT][CODE]

## 5. Candidate Solution

Introduce a shared `ConversationContext` and context builder used by both SDK and proxy. Keep existing `ProcessedRequest.prompt`, `developer_contract`, and `conversation_history` for compatibility, but add `conversation_context` carrying native-role raw messages, separated system/developer messages, prior user/assistant turns, final user, context source, state metadata, and per-module context-policy metadata. [DOC][CODE]

Use `request_transcript` as authoritative whenever OpenAI-style cumulative `messages` are supplied. Keep `ConversationGovernanceState` separate from transcript context; do not treat `conversation_id` alone as history. [DOC]

DCCL should receive a role-preserving full transcript representation derived from `ConversationContext` for multi-turn contract evaluation. Add `INSUFFICIENT_CONTEXT` to DCCL only if the orchestrator treats it as no compliance fast-path and continues normal governance safely; otherwise return `NO_MATCH` only when full relevant context was actually inspected. Recommendation: add the enum and safe orchestrator handling now, because it makes missing/truncated history explicit. [DOC][ASSUMPTION]

Speculative generation should use Strategy A for multi-turn requests: full native transcript or role-preserving equivalent. If `PolicyModel` cannot accept chat messages today, add a minimal `generate_messages(...)` path or a role-serialized full-context fallback with `context_mode=role_serialized_full`, then block draft reuse when context is incomplete. [DOC][ASSUMPTION]

Risk and deliberative modules can remain serialized for the first patch, but must receive full role-serialized context or explicit `role_serialized_truncated` / `policy_summary` metadata. Last-3 truncation must be visible and must not be represented as full-context reasoning. [DOC]

## 6. Step-by-Step Plan

### Step 1: Add failing regression tests first

- Goal: Lock the known bug before implementation. [DOC]
- Baseline constraint: Tests must be offline/deterministic and must not weaken existing behavior-locking tests. [DOC]
- Files to inspect:
  - `tests/test_multiturn_context_propagation.py`
  - `tests/test_sdk_dccl.py`
  - `tests/test_server_proxy.py`
  - `tests/test_controller_speculative_lazy.py`
  - `tests/test_controller_risk_context_propagation.py`
  - `tests/test_deliberative_modules_context_propagation.py`
  - `.adversarial/runs/20260525-102814-multiturn_history_propagation_issues_investigations/history_dependent_rule_canary.json`
- Files likely to change:
  - `tests/test_multiturn_context_alignment.py` or existing nearest test modules
  - `tests/conftest.py` only if shared deterministic fakes are needed
- Exact expected change:
  - Add parser preservation test for system/developer, prior user, prior assistant, final user, raw role order.
  - Add DCCL canary test asserting `MATCH`, no rationale claiming prior auth absent, and prompt/context includes prior auth turn.
  - Add speculative context test asserting multi-turn speculative generation receives full/equivalent context or is marked non-reusable.
  - Add SDK/proxy equivalence tests for full transcript, cumulative iterative vs single final full transcript, and older-than-last-3 authorization turn.
- Validation command:
  - `.\venv\Scripts\python.exe -m pytest tests/test_multiturn_context_alignment.py -q`
  - Expected before implementation: targeted failures proving current behavior is broken.
- Rollback note:
  - If tests are too broad or flaky, keep the canary parser/DCCL/speculative tests and move SDK/proxy e2e equivalence into narrower fake-orchestrator tests.

### Step 2: Add shared `ConversationContext` and builder

- Goal: Preserve native OpenAI transcript once and make SDK/proxy use the same parser. [DOC]
- Baseline constraint: Preserve single-turn byte equality and system-prompt transparency; do not mutate original `messages`. [DOC]
- Files to inspect:
  - `moralstack/orchestration/types.py`
  - `moralstack/sdk/wrapper.py`
  - `moralstack/server/proxy.py`
  - `moralstack/orchestration/contract.py`
  - `moralstack/pipeline/context_builder.py`
- Files likely to change:
  - `moralstack/orchestration/types.py`
  - New `moralstack/orchestration/conversation_context.py` or extend `moralstack/pipeline/context_builder.py`
  - `moralstack/sdk/wrapper.py`
  - `moralstack/server/proxy.py`
- Exact expected change:
  - Define `ChatMessage` and `ConversationContext` dataclasses, or use existing structured message shape if present.
  - Add `build_conversation_context(messages, conversation_id=None, turn_index=None, state_in=None, history_source="request_transcript")`.
  - Add `ProcessedRequest.conversation_context: ConversationContext | None = None` as an additive field.
  - SDK/proxy build `ProcessedRequest` from the shared builder instead of independent ad hoc slicing.
  - Preserve current `prompt`, `developer_contract`, and `conversation_history` from context-derived fields for backward compatibility.
- Validation command:
  - `.\venv\Scripts\python.exe -m pytest tests/test_sdk_wrapper.py tests/test_server_proxy.py tests/test_system_prompt_byte_equality.py -q`
- Rollback note:
  - Because this is additive, rollback is removing the new field and reverting SDK/proxy to existing helper paths; keep tests to document the gap if implementation is delayed.

### Step 3: Wire DCCL to full context

- Goal: Make DCCL evaluate history-dependent developer contracts over prior turns. [DOC]
- Baseline constraint: DCCL `MATCH` must still be subject to safety override and must not bypass P0 hard-signal rules. [DOC]
- Files to inspect:
  - `moralstack/compliance/dccl.py`
  - `moralstack/compliance/types.py`
  - `moralstack/compliance/safety_override.py`
  - `moralstack/orchestration/controller.py`
  - `tests/test_compliance_evaluation.py`
  - `tests/test_compliance_orchestrator_integration.py`
- Files likely to change:
  - `moralstack/compliance/types.py`
  - `moralstack/compliance/dccl.py`
  - `moralstack/orchestration/controller.py`
  - DCCL tests
- Exact expected change:
  - Extend DCCL `evaluate(...)` to accept `conversation_context` or structured/serialized transcript evidence.
  - Include a native-like ordered transcript block with roles, separated from the DCCL module system/task prompt.
  - Add `INSUFFICIENT_CONTEXT` if supported; orchestrator treats it as no compliance fast-path and continues normal routing with metadata.
  - Ensure `NO_MATCH` rationale may only claim prior evidence absent when full relevant context was included and inspected.
- Validation command:
  - `.\venv\Scripts\python.exe -m pytest tests/test_compliance_evaluation.py tests/test_compliance_orchestrator_integration.py tests/test_sdk_dccl.py tests/test_multiturn_context_alignment.py -q`
- Rollback note:
  - If enum expansion creates too much surface area, keep full-context DCCL input and defer `INSUFFICIENT_CONTEXT`, but retain metadata proving context availability.

### Step 4: Align speculative generation with multi-turn context

- Goal: Prevent a `system + last_user` speculative draft from influencing multi-turn final response selection. [DOC]
- Baseline constraint: Internal speculative calls may occur before routing, but returned final action must still come from structured governance decisions. [DOC]
- Files to inspect:
  - `moralstack/orchestration/controller.py`
  - `moralstack/orchestration/speculative_overlap.py`
  - `moralstack/models/policy.py`
  - `tests/test_controller_speculative_lazy.py`
  - `tests/test_speculative_overlap.py`
  - `tests/test_compliance_fast_path.py`
- Files likely to change:
  - `moralstack/models/policy.py`
  - `moralstack/orchestration/controller.py`
  - `moralstack/orchestration/speculative_overlap.py`
  - Speculative tests
- Exact expected change:
  - Preferred: add `policy.generate_messages(messages=..., system=module_system, context_mode="full_native" or "role_serialized_full")` and use it when `ConversationContext` has prior turns.
  - If native messages are not feasible, role-serialize the full transcript and mark `context_mode=role_serialized_full`.
  - If context is incomplete, set `context_mode=system_last_user_only`, `reusable=False`, and prevent compliance fast-path reuse/final draft reuse for multi-turn.
- Validation command:
  - `.\venv\Scripts\python.exe -m pytest tests/test_controller_speculative_lazy.py tests/test_speculative_overlap.py tests/test_compliance_fast_path.py tests/test_multiturn_context_alignment.py -q`
- Rollback note:
  - Roll back to non-reusable single-turn speculative mode for multi-turn if native message generation proves too invasive.

### Step 5: Add module context policies and metadata

- Goal: Make every LLM-using governance module declare context used vs available. [DOC]
- Baseline constraint: Observability must be best-effort and never change request outcome. [DOC]
- Files to inspect:
  - `moralstack/models/risk/estimator.py`
  - `moralstack/runtime/modules/critic_module.py`
  - `moralstack/runtime/modules/simulator_module.py`
  - `moralstack/runtime/modules/perspective_module.py`
  - `moralstack/runtime/modules/hindsight_module.py`
  - `moralstack/orchestration/deliberation_runner.py`
  - `moralstack/orchestration/persistence_helpers.py`
  - `moralstack/observability/sinks/jsonl_sink.py`
  - `moralstack/observability/sinks/sqlite_sink.py`
- Files likely to change:
  - Module prompt builders/call sites above
  - Shared context metadata helper
  - Observability event payload construction
- Exact expected change:
  - Add per-module context metadata fields: `context_mode`, raw/system/developer counts, prior available/used counts, truncation mode/n, full-native flag, developer-contract/final-user included flags, delivery-broader flag.
  - Risk and deliberative modules should receive full role-serialized context for the first patch unless a tested truncation/summary policy is explicitly encoded.
  - If any module still uses last-3, log `role_serialized_truncated`, `history_truncation=last_n`, and used/available counts.
- Validation command:
  - `.\venv\Scripts\python.exe -m pytest tests/test_controller_risk_context_propagation.py tests/test_deliberative_modules_context_propagation.py tests/test_observability_jsonl_sink.py tests/test_observability_sqlite_sink.py -q`
- Rollback note:
  - If DB schema migration is too broad, keep metadata in JSONL/event `metadata_json` first and document UI/SQLite follow-up, but acceptance requires JSONL at minimum.

### Step 6: Separate request transcript from governance state in SDK/proxy

- Goal: Prevent conversation state from replacing or erasing explicit request transcript evidence. [DOC]
- Baseline constraint: `conversation_id` is identity/state linkage, not raw transcript reconstruction unless persistence explicitly stores raw transcript. [DOC]
- Files to inspect:
  - `moralstack/sdk/session.py`
  - `moralstack/sdk/session_store.py`
  - `moralstack/sdk/wrapper.py`
  - `moralstack/server/proxy.py`
  - `moralstack/server/conversation_correlation.py`
  - `moralstack/orchestration/conversation_state.py`
  - `moralstack/orchestration/controller.py`
- Files likely to change:
  - `moralstack/sdk/wrapper.py`
  - `moralstack/server/proxy.py`
  - `moralstack/orchestration/types.py` or context builder metadata
- Exact expected change:
  - Set `history_source=request_transcript` when `messages` contains cumulative history.
  - Set `history_source=none` for single-current-question calls unless explicit transcript persistence exists.
  - Keep `state_in` separately as `governance_state`, not transcript.
  - Add reconciliation metadata placeholders: `stored_transcript_loaded=false`, `stored_transcript_reconciliation=not_applicable|missing`, `governance_state_loaded`.
- Validation command:
  - `.\venv\Scripts\python.exe -m pytest tests/test_sdk_session.py tests/test_sdk_session_with_store.py tests/test_server_proxy.py tests/test_conversation_correlation.py tests/test_multiturn_context_alignment.py -q`
- Rollback note:
  - If session persistence support is out of scope, explicitly document unsupported raw transcript persistence and emit `history_source=none` for current-question-only calls.

### Step 7: Enforce delivery/governance context mismatch guard

- Goal: Detect and safely handle cases where final delivery has broader relevant context than governance. [DOC]
- Baseline constraint: Do not change `SAFE_COMPLETE` system prompt byte equality; append guidance only as trailing user message. [DOC]
- Files to inspect:
  - `moralstack/sdk/wrapper.py`
  - `moralstack/server/proxy.py`
  - `moralstack/orchestration/controller.py`
  - `moralstack/sdk/response.py`
  - `moralstack/server/headers.py`
- Files likely to change:
  - SDK/proxy finalization metadata
  - `ResponseMetadata` if exposed to SDK metadata
  - Proxy headers only if needed and backward compatible
- Exact expected change:
  - Compute `delivery_context_broader_than_governance`.
  - For normal full-transcript calls after fixes, this should be false for DCCL and reusable speculative generation.
  - If true, record a mismatch flag and avoid using incomplete speculative drafts for final selection.
- Validation command:
  - `.\venv\Scripts\python.exe -m pytest tests/test_safe_complete_user_turn.py tests/test_system_prompt_byte_equality.py tests/test_sdk_response.py tests/test_server_proxy.py tests/test_multiturn_context_alignment.py -q`
- Rollback note:
  - If public metadata expansion is risky, keep the flag in observability metadata first and add public fields later.

### Step 8: Update docs and trace diagrams

- Goal: Make documented semantics match implemented behavior. [DOC]
- Baseline constraint: Behavior changes require same-change docs updates. [DOC]
- Files to inspect:
  - `README.md`
  - `docs/MORALSTACK_CODEBASE_INDEX.md`
  - `docs/CODEBASE_FACTS.md`
  - `docs/multiturn_design.md`
  - `docs/modules/compliance_layer.md`
  - `docs/modules/risk_estimator.md`
  - `docs/modules/critic.md`
  - `docs/modules/simulator.md`
  - `docs/modules/perspectives.md`
  - `docs/modules/hindsight.md`
  - `docs/modules/orchestrator.md`
  - `docs/modules/observability.md`
  - `docs/traces/openai_compatible_multiturn.md`
  - `docs/traces/complai_llm_rules_flow.md`
  - `docs/traces/governance_decision_flow.md`
  - `docs/traces/observability_db_to_ui.md`
- Files likely to change:
  - All docs listed above as applicable
- Exact expected change:
  - Document `ConversationContext`, request transcript vs stored transcript vs governance state.
  - Document SDK/proxy multi-turn contracts and unsupported raw transcript persistence if not implemented.
  - Document per-module context policy and prompt/message separation.
  - Update trace diagrams to show `OpenAI messages -> ConversationContext -> module context policy -> module model messages -> governance decision -> final delivery`.
  - Add history-dependent canary regression section and test locations.
- Validation command:
  - `.\venv\Scripts\python.exe -m pytest tests/test_reports.py tests/test_conversation_export.py tests/test_observability_read_store.py -q`
  - Optional docs grep: `rg "docs/TRACES|full support for conversational governance|history used" README.md docs`
- Rollback note:
  - If module docs are too extensive for the first patch, update README, trace docs, codebase index, facts ledger, and directly touched module docs before merge.

### Step 9: Run acceptance regression suite

- Goal: Prove no regression in governance invariants, COMPL-AI benign path, SDK/proxy behavior, and observability. [DOC]
- Baseline constraint: Full suite should pass before declaring done. [DOC]
- Files to inspect:
  - Failure output only
- Files likely to change:
  - Only targeted fixes for failures caused by this patch
- Exact expected change:
  - No hard-coded canary strings.
  - Canary final turn returns `HISTORY_SECRET_42` through SDK/proxy using fakes or deterministic harness.
  - DCCL returns `MATCH`.
  - COMPL-AI `llm_rules benign` behavior remains stable.
- Validation command:
  - `.\venv\Scripts\python.exe -m pytest tests/test_multiturn_context_alignment.py tests/test_multiturn_context_propagation.py tests/test_prompt8_contract_priority.py tests/e2e_run_regression.py -q`
  - `.\venv\Scripts\python.exe -m pytest -q`
- Rollback note:
  - If full suite exposes unrelated dirty-worktree failures, isolate with targeted tests and report unrelated failures without reverting user changes.

## 7. Tests and Verification

Minimum new/updated tests:

- Parser preservation: SDK and proxy both preserve raw native roles, separated system/developer messages, prior user/assistant turns, and final user. [TEST]
- DCCL canary: full transcript rule produces `MATCH`; rationale does not claim prior auth absent. [TEST]
- Speculative alignment: multi-turn speculative generation uses full/equivalent context or is non-reusable. [TEST]
- SDK/proxy equivalence: same full transcript produces same context metadata and no unexplained action divergence. [TEST]
- Cumulative iterative vs single final: request transcript wins over stored governance state; final cumulative and single final have equivalent governance semantics. [TEST]
- Last-3 guard: older rule-relevant turn remains visible to DCCL; truncated modules log truncation explicitly. [TEST]
- Invariant regression: `tests/test_system_prompt_byte_equality.py`, `tests/test_safe_complete_user_turn.py`, governance invariant tests, DCCL safety override tests. [TEST]

## 8. Risks and Compatibility

Adding raw transcript context increases token use and may expose more raw message content to internal governance LLM calls; observability should log counts and modes by default, not full raw message bodies unless existing LLM-call logging already captures prompts. [DOC][ASSUMPTION]

Changing DCCL verdict enum can affect headers, SDK metadata, UI cards, reports, and tests. Treat `INSUFFICIENT_CONTEXT` as non-match for fast-path purposes unless and until a stricter policy is designed. [CODE][ASSUMPTION]

Speculative full-context generation may alter latency and cost. Strategy A is preferred for correctness; fallback non-reusable mode is acceptable only if tests prove drafts cannot influence final selection. [DOC]

Single-turn byte equality is fragile. The context field must be additive and dormant for no-contract/no-history cases. [DOC]

Proxy compliance fast-path and SDK normal completion are already behaviorally different in final response source. This task should not silently normalize that difference unless explicitly tested and documented. [DRIFT]

## 9. Documentation Updates Required

Update `README.md` to replace broad “full conversational governance” claims with exact SDK/proxy transcript requirements and `ConversationContext` semantics. [DRIFT]

Update `docs/CODEBASE_FACTS.md` with verified facts after implementation: shared context builder, DCCL context mode, speculative context mode, and any remaining truncation. [DOC]

Update `docs/MORALSTACK_CODEBASE_INDEX.md` to add the new context builder/module and revised request parsing path. [DOC]

Update `docs/traces/openai_compatible_multiturn.md`, `docs/traces/complai_llm_rules_flow.md`, `docs/traces/governance_decision_flow.md`, and `docs/traces/observability_db_to_ui.md` to distinguish request transcript, stored transcript, governance state, module prompts, module context, and final delivery messages. [DOC]

Update module docs for compliance, risk, critic, simulator, perspectives, hindsight, orchestrator, policy/speculative generation, and observability to state context policy and metadata fields. [DOC]

Add regression documentation for the history-dependent authorization canary: expected behavior, prior failure mode, proof tests, and test file paths. [DOC]

## 10. Recommendation

Proceed with a minimal additive implementation: shared `ConversationContext`, SDK/proxy shared builder, DCCL full role-preserving transcript, speculative Strategy A for multi-turn or non-reusable fallback, and per-module context metadata. This path preserves existing architecture while directly closing the context mismatch proven by the investigation. [DOC][CODE]

Do not implement raw transcript persistence as part of the first patch unless it already exists behind a clear API. For current-question-only SDK/proxy calls, document and log `history_source=none`; for OpenAI-style cumulative calls, treat the request transcript as authoritative. [DOC]

Do not accept the patch until the canary passes through SDK and proxy, DCCL returns `MATCH`, cumulative and single-final proxy semantics align, and docs no longer claim history is governed merely because it was present in the request body. [TEST][DRIFT]
