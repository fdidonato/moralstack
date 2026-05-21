"""
Stable event_type identifiers for orchestration_events persistence.

Runtime code may emit subsets; the taxonomy is fixed so reports and UI can rely on names.
"""

from __future__ import annotations

# Speculative execution (future scheduler hooks)
SPECULATIVE_STARTED = "SPECULATIVE_STARTED"
SPECULATIVE_JOIN_REQUIRED = "SPECULATIVE_JOIN_REQUIRED"
SPECULATIVE_JOIN_SKIPPED = "SPECULATIVE_JOIN_SKIPPED"
SPECULATIVE_RESULT_USED = "SPECULATIVE_RESULT_USED"
SPECULATIVE_RESULT_DISCARDED = "SPECULATIVE_RESULT_DISCARDED"

# Parallel / short-circuit orchestration
PARALLEL_STRATEGY_SELECTED = "PARALLEL_STRATEGY_SELECTED"
CRITIC_SHORT_CIRCUIT_TRIGGERED = "CRITIC_SHORT_CIRCUIT_TRIGGERED"
PARALLEL_MODULE_CANCEL_ATTEMPTED = "PARALLEL_MODULE_CANCEL_ATTEMPTED"
PARALLEL_MODULE_CANCELLED = "PARALLEL_MODULE_CANCELLED"
PARALLEL_MODULE_COMPLETED_AFTER_SHORT_CIRCUIT = "PARALLEL_MODULE_COMPLETED_AFTER_SHORT_CIRCUIT"

# Principle retrieval / domain cache
RELEVANT_PRINCIPLES_RETRIEVED = "RELEVANT_PRINCIPLES_RETRIEVED"
CRITIC_SKIPPED = "CRITIC_SKIPPED"
RELEVANT_PRINCIPLES_REUSED = "RELEVANT_PRINCIPLES_REUSED"
DOMAIN_PREFILTER_CACHE_HIT = "DOMAIN_PREFILTER_CACHE_HIT"
DOMAIN_PREFILTER_CACHE_MISS = "DOMAIN_PREFILTER_CACHE_MISS"
DOMAIN_PREFILTER_CACHE_INVALIDATED = "DOMAIN_PREFILTER_CACHE_INVALIDATED"

# Simulator gating / execution
SIMULATOR_GATE_DECISION = "SIMULATOR_GATE_DECISION"
SIMULATOR_EXECUTED = "SIMULATOR_EXECUTED"
SIMULATOR_SKIPPED = "SIMULATOR_SKIPPED"

# Aggregated guidance (rewrite / soft revision)
AGGREGATED_GUIDANCE_EVALUATED = "AGGREGATED_GUIDANCE_EVALUATED"

# Convergence
CONVERGENCE_EVALUATED = "CONVERGENCE_EVALUATED"
EARLY_CONVERGENCE_ACCEPTED = "EARLY_CONVERGENCE_ACCEPTED"
EARLY_CONVERGENCE_REJECTED = "EARLY_CONVERGENCE_REJECTED"

# Ledger fast-path (Step 14.4)
LEDGER_FAST_PATH_APPLIED = "LEDGER_FAST_PATH_APPLIED"
"""
Emitted when a SemanticDecisionLedger cache hit is applied: the cached
decision overrides the current deliberation, and critic / simulator /
perspectives / hindsight are NOT executed for this turn. Payload includes
``from_turn``, ``similarity``, ``cached_action``, ``modules_skipped``.
"""

LEDGER_FAST_PATH_NOT_APPLIED = "LEDGER_FAST_PATH_NOT_APPLIED"
"""
Emitted when a SemanticDecisionLedger cache hit is FOUND but the safety gate
(``ConversationalFastPathRunner.is_safe_to_apply``) refused to apply it. The
turn proceeds with full deliberation. Payload includes ``from_turn``,
``similarity``, ``cached_action``, ``current_route``, ``gate_reason``.
"""

# Conversation (multi-turn foundation; emit only when context is explicitly provided)
CONVERSATION_CONTEXT_ATTACHED = "CONVERSATION_CONTEXT_ATTACHED"
CONVERSATION_STATE_UPDATED = "CONVERSATION_STATE_UPDATED"

# =============================================================================
# Developer Contract Compliance Layer (DCCL)
# =============================================================================
# Emitted by the DCCL component. Reference: dccl_specification_v0.3.md §8.2.
# Commit 1 (Foundation) declares the constants; emission ships in Commit 2-3.

COMPLIANCE_LAYER_STARTED = "COMPLIANCE_LAYER_STARTED"
"""DCCL.evaluate() invoked.
Payload: has_contract, has_structured_rules, evaluation_path_preference."""

COMPLIANCE_LAYER_VERDICT_MATCH = "COMPLIANCE_LAYER_VERDICT_MATCH"
"""DCCL returned MATCH.
Payload: matched_rule_id, evaluation_path, confidence, duration_ms."""

COMPLIANCE_LAYER_VERDICT_NO_MATCH = "COMPLIANCE_LAYER_VERDICT_NO_MATCH"
"""DCCL returned NO_MATCH.
Payload: rationale_excerpt, evaluation_path, duration_ms, confidence."""

COMPLIANCE_LAYER_VERDICT_SAFETY_OVERRIDE = "COMPLIANCE_LAYER_VERDICT_SAFETY_OVERRIDE"
"""DCCL returned SAFETY_OVERRIDE.
Payload: safety_override_reason, rule_excerpt, duration_ms."""

COMPLIANCE_LAYER_VERDICT_NO_CONTRACT = "COMPLIANCE_LAYER_VERDICT_NO_CONTRACT"
"""Request has no developer contract.
Payload: empty."""

CONTRACT_RULE_REJECTED = "CONTRACT_RULE_REJECTED"
"""A structured rule failed safety validation at contract loading.
Payload: rule_id, reason, rejected_action_payload_excerpt."""

CONTRACT_RULES_LOADED = "CONTRACT_RULES_LOADED"
"""Contract loading complete.
Payload: contract_hash, total_rules, accepted_rules, rejected_rules."""

MODULE_DEFERRED_TO_COMPLIANCE = "MODULE_DEFERRED_TO_COMPLIANCE"
"""A downstream module returned early due to ComplianceSignal(MATCH).
Payload: module, reason, cycle, deferred_outcome_summary."""

CONTRACT_INJECTION_DETECTED = "CONTRACT_INJECTION_DETECTED"
"""DEPRECATED (Commit 3-fix): the contract-injection concept was removed because it
conflated legitimate authorization patterns (password-gated secret reveal, workflow
execution) with attacks. The correct safety boundary is Safety Override on the
rule OUTPUT category, not a separate injection signal. This constant is retained
only to avoid breaking any external consumers that may reference the name; it is
never emitted. Do not use in new code."""

COMPLIANCE_LAYER_TIMEOUT = "COMPLIANCE_LAYER_TIMEOUT"
"""DCCL LLM call exceeded timeout. Payload: timeout_ms, evaluation_path."""

COMPLIANCE_DRAFT_REUSED = "COMPLIANCE_DRAFT_REUSED"
"""Validated speculative draft reused on compliance fast-path (Case 1).
Payload: matched_rule_id, draft_match_method, draft_match_confidence, action_excerpt, degraded."""

COMPLIANCE_DRAFT_REGENERATED = "COMPLIANCE_DRAFT_REGENERATED"
"""Contract-authorized draft regenerated and validated (Case 2 success).
Payload: validated, draft_match_method, draft_match_confidence, reason."""

COMPLIANCE_MATCH_DOWNGRADED = "COMPLIANCE_MATCH_DOWNGRADED"
"""DCCL MATCH could not be fast-pathed after regen/revalidation (Case 3).
Payload: reason, matched_rule_id, action_excerpt, degraded, degraded_reason."""

CONTRACT_STRUCTURE_PROSE_CONFLICT = "CONTRACT_STRUCTURE_PROSE_CONFLICT"
"""structured_rules conflict with raw_text in the same contract.
Payload: contract_hash, conflict_description."""

# Proxy output routing (server)
PROXY_OUTPUT_FINALIZED = "PROXY_OUTPUT_FINALIZED"
"""Proxy finalized the HTTP response body.
Payload: final_action, final_text_source, reused_governed_content,
final_response_length, finish_reason, model."""

ALL_EVENT_TYPES: frozenset[str] = frozenset(
    {
        AGGREGATED_GUIDANCE_EVALUATED,
        SPECULATIVE_STARTED,
        SPECULATIVE_JOIN_REQUIRED,
        SPECULATIVE_JOIN_SKIPPED,
        SPECULATIVE_RESULT_USED,
        SPECULATIVE_RESULT_DISCARDED,
        PARALLEL_STRATEGY_SELECTED,
        CRITIC_SHORT_CIRCUIT_TRIGGERED,
        PARALLEL_MODULE_CANCEL_ATTEMPTED,
        PARALLEL_MODULE_CANCELLED,
        PARALLEL_MODULE_COMPLETED_AFTER_SHORT_CIRCUIT,
        RELEVANT_PRINCIPLES_RETRIEVED,
        CRITIC_SKIPPED,
        RELEVANT_PRINCIPLES_REUSED,
        DOMAIN_PREFILTER_CACHE_HIT,
        DOMAIN_PREFILTER_CACHE_MISS,
        DOMAIN_PREFILTER_CACHE_INVALIDATED,
        SIMULATOR_GATE_DECISION,
        SIMULATOR_EXECUTED,
        SIMULATOR_SKIPPED,
        CONVERGENCE_EVALUATED,
        EARLY_CONVERGENCE_ACCEPTED,
        EARLY_CONVERGENCE_REJECTED,
        CONVERSATION_CONTEXT_ATTACHED,
        CONVERSATION_STATE_UPDATED,
        LEDGER_FAST_PATH_APPLIED,
        LEDGER_FAST_PATH_NOT_APPLIED,
    }
)
