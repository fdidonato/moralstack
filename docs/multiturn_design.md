# MoralStack v0.4 Multi-Turn Design

> Implementation reference for the multi-turn governance layer introduced
> in MoralStack v0.4.

## Normative reference

The normative document is `MORALSTACK_MULTITURN_DESIGN.md` v1.3 (internal,
held in the team's design archive). This file is a short pointer to the
public surfaces of that design.

## Revision history

| Version | Date | Scope |
|---|---|---|
| v1.0 | 2026-03 | Initial draft: DeveloperContract + SemanticDecisionLedger |
| v1.1 | 2026-04 | Added ConversationGovernanceState, fast-path runner |
| v1.2 | 2026-04 | Cache governance hole fix (§6.7) |
| v1.3 | 2026-05 | RefusalContext 7-priority + caveat-as-extra-user-turn + server proxy |

## Public surfaces introduced in v0.4

| Surface | Module | Step |
|---|---|---|
| `DeveloperContract` | `moralstack.orchestration.contract` | 1, 2 |
| `ConversationGovernanceState` extension | `moralstack.orchestration.conversation_state` | 1 |
| `SemanticDecisionLedger` | `moralstack.orchestration.ledger` | 4 |
| `SessionState` / `InMemorySessionStore` | `moralstack.sdk.session*` | 5 |
| `ConversationalFastPathRunner` | `moralstack.orchestration.conversational_fast_path` | 7 |
| `effective_system_for_request` | `moralstack.orchestration.system_prompt_resolver` | 8 |
| Cache `build_context_fingerprint` | `moralstack.utils.cache` | 9 |
| `RefusalContext` extended | `moralstack.orchestration.refusal_context` | 10 |
| `_build_safe_complete_user_turn` | `moralstack.sdk.wrapper` | 10 |
| Server proxy (FastAPI) | `moralstack.server.*` | 11 |
| Audit conversation export | `moralstack.reports.conversation_export` | 12 |

## Key invariants

1. **Transparency (§1.3)**: the developer-declared system prompt is never modified
   by the governance layer. Caveats are injected as extra user turns.
2. **P0 invariant (§1.7)**: hard topical signals (self-harm, child safety,
   weapons, physical harm) MUST NOT be overridable by developer contracts
   or domain overlays.
3. **Byte-equality (§10)**: when no developer_contract and no conversation_history
   are provided, the pipeline behavior is byte-identical to v0.3.x single-turn.
4. **Audit completeness**: every governance decision (including those served
   from the SemanticDecisionLedger cache) appears in the audit trail.
