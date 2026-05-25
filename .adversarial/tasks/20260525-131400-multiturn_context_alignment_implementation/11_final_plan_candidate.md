The `Write` tool is disabled in this context (as both prior planning agents also found), so I cannot persist the plan file. This workflow is planning-only anyway — the synthesized plan is the deliverable. I verified every contested claim against source this session, so the resolutions below are evidence-backed rather than assumed. Here is the final synthesized plan.

---

# Final Adversarial Plan

## 1. Executive Summary

Introduce one additive, dormant-when-absent `ConversationContext` built by a single builder shared by the SDK wrapper and the HTTP proxy, consumed first by the two canary-critical modules: **DCCL** and **speculative generation**. DCCL gains a role-ordered, budgeted transcript block in its LLM prompt and must never assert a prior turn is absent when context was truncated/omitted. Speculative generation uses the role-serialized transcript when prior turns exist (Strategy A) so the validated draft — which the proxy can return directly on `COMPLIANCE_FAST_PATH` — reflects prior authorization. Risk/deliberative modules keep their last-3 optimization but must **declare** their context mode. A delivery/governance mismatch guard plus safe fallback operationalizes the core invariant. The cumulative-vs-single-final divergence is root-caused (stateful ledger fast-path, not parsing) and pinned by tests; the DCCL fix makes both paths reach `COMPLIANCE_FAST_PATH`, which short-circuits the ledger. Tests are written first; `INSUFFICIENT_CONTEXT`, raw-transcript persistence, policy-aware summaries, and SDK/proxy fast-path normalization are deferred.

## 2. Scope

### In Scope
- Additive `ConversationContext` + one shared builder (SDK + proxy).
- DCCL LLM path: role-ordered budgeted transcript; honest truncation; no false absence; canary → `MATCH`.
- Speculative generation: role-serialized full-transcript prefix when prior turns exist (Strategy A); single-turn byte-equality preserved otherwise.
- Per-LLM-module context-shape observability incl. `delivery_context_broader_than_governance`.
- Delivery/governance mismatch guard + safe fallback.
- Explicit `history_source` semantics for calling Cases 1–4 (incl. `none`).
- Root-cause + regression test for cumulative-vs-single-final divergence.
- Regression Tests A–G (offline, deterministic, canary strings only in fixtures).
- Documentation updates.

### Out of Scope (deferred follow-ups)
- `INSUFFICIENT_CONTEXT` DCCL verdict (touches headers/SDK metadata/UI/export/tests).
- Raw-transcript persistence in proxy/SDK stores.
- Policy-aware summarization for risk/deliberative (last-3 kept, declared).
- Normalizing SDK-vs-proxy `COMPLIANCE_FAST_PATH` final-source divergence (documented + tested, not changed).
- Proxy streaming; broad refactors of `controller.process()`.

## 3. Baseline Documents Used
`CLAUDE.md` (§5 invariants, §6–§8); `docs/MORALSTACK_CODEBASE_INDEX.md`; `docs/CODEBASE_FACTS.md`; `docs/traces/{openai_compatible_multiturn,governance_decision_flow,complai_llm_rules_flow,observability_db_to_ui}.md`; `docs/decision_policy.md`; `docs/modules/{orchestrator,risk_estimator,compliance_layer,server_proxy}.md`; `docs/architecture_spec.md`; `final_investigation_report.md` + run artifacts. [DOC]

## 4. Evidence Base
- `ProcessedRequest` has no raw-messages/context field — `orchestration/types.py:192-209`. [CODE]
- SDK parsing: `prompt`/`history`/`developer_contract` only — `sdk/wrapper.py:35-93,285-303`. Proxy mirrors it, no native messages retained — `server/proxy.py:244-252`. [CODE]
- DCCL `evaluate(request, speculative_draft, risk_estimation)` reads `request.prompt` + contract only; never `conversation_history` — `dccl.py:214-247,307,419`. [CODE]
- **C1**: `_build_llm_user_prompt` truncates `raw_text[:3000]`, `draft[:1000]`, single `USER REQUEST`, no history block — `dccl.py:493-512`. [CODE]
- **C3**: structured `_rule_matches_prompt` matches only `user_prompt`, `SEMANTIC→False`; default path `hybrid` → canary uses the LLM path — `dccl.py:252-269,365-377`; `.env.template:236`. [CODE][DOC]
- DCCL MATCH still passes `classify_safety_override` — `dccl.py:328-340,544-553`; P0 preserved (`controller.py:2209`). [CODE]
- Speculative = `policy.generate(prompt=request.prompt, system=…)`, no transcript — `controller.py:847-872`; proxy returns governed draft on `COMPLIANCE_FAST_PATH` — `proxy.py:338-350`. [CODE]
- **C5**: `policy.generate(prompt, system, config, prediction, model_override)`, no message-list form; speculative call already swallows `TypeError` — `policy.py:234-284`, `controller.py:868-874`. [CODE]
- **C4**: risk truncation site = `estimator._format_context_block` (`[-3:]`, `[:200]`); controller passes full history, estimator truncates — `estimator.py:122-150`, `controller.py:794-823`. [CODE]
- **C2** mechanism: DCCL MATCH → `_route_compliance_match` early return (`controller.py:1936-2040`) **before** ledger lookup (`controller.py:2149-2306`); ledger key incl. `conversation_id`-scoped posture/turn (`2162-2170`); `is_safe_to_apply` applies cached non-REFUSE on non-deliberative routes (`conversational_fast_path.py:84-151`). Divergence is **stateful, not parsing**, and short-circuited once DCCL matches. [CODE]
- SDK has no governed-draft branch on `NORMAL_COMPLETE` → SDK/proxy diverge in final-text source — `wrapper.py:380-403` vs `proxy.py:338-350`. [DRIFT][CODE]
- `record_llm_call` already persists `prompt`/`system_prompt` (`dccl.py:447-465`); JSONL one file per event-type. [CODE][DOC]

## 5. Baseline Compliance
| Baseline Source | Constraint | How The Plan Preserves It |
|---|---|---|
| §5.1 | Decision/generation separation | No module derives action from text; DCCL stays structured; guard sets flag/fallback, not text-inferred action. |
| §5.2 | System-prompt transparency | `_build_safe_complete_user_turn` untouched; byte-equality test kept green. |
| §5.3 | Hard-signal / Safety Override supremacy | DCCL MATCH still passes `classify_safety_override`; `is_hard_signal_refuse` re-evaluated after ledger patch. |
| §5.4 | Single-turn byte-equality | Additive dormant field; DCCL/speculative changes gate on `prior turns > 0`. |
| §5.6 | Observability never breaks request | All context-shape logging in swallowing try/except. |
| §5.7 | REFUSE no upstream | Routing branches unchanged. |
| §6 | Smallest change | Reuse existing extract helpers, `record_llm_call`, routes; no rename/reorg. |

## 6. Documentation / Code Drift Resolution
| Drift Item | Severity | Resolution |
|---|---|---|
| Trace: "full history passed into governance" vs DCCL/speculative ignore it | High | Code wins; distinguish "present in body / to risk" from "used by DCCL/speculative"; document `ConversationContext`. |
| README/spec: "full conversational governance" | Medium | Code wins; reword to exact contract + per-module policy. |
| `complai_llm_rules_flow.md`: DCCL fast-path "contract-aware over history" | Medium | Code wins (canary NO_MATCH); update after fix + canary note. |
| `docs/TRACES/*` vs renamed `docs/traces/*` in `CLAUDE.md`/docs | Low | Normalize lowercase in touched files. |

No unresolved DOC_CODE_CONFLICT blocks the plan (all claims re-verified this session).

## 7. Critic Response Matrix
| Issue | Source | Decision | Resolution |
|---|---|---|---|
| BI-001: invariant only observed, not enforced | Codex/A | Accept | Step 7: compute `delivery_context_broader_than_governance` **and** safe fallback (no unaligned draft as final; flag mismatch). Test G asserts it fires. |
| BI-002: Case 1/3 (transcript-absent) unspecified | Codex/A | Accept | Step 6: `history_source=none` for current-question-only; no raw reconstruction v1; state≠transcript; tested. |
| C1: DCCL truncation re-introduces "claims absence" | Claude/B | Accept | Step 3: budget transcript, keep rule-relevant turns, honest `history_truncation`, forbid bare absence `NO_MATCH` when trimmed. Test F (long transcript). |
| C2: divergence root cause unlocated | Claude/B | Accept | Root-caused: stateful ledger, short-circuited by DCCL MATCH. Test E asserts `COMPLIANCE_FAST_PATH` + ledger fast-path **not applied**. |
| C3: structured path can't express history | Claude/B | Accept | Fix targets LLM path; Test B drives LLM path with fake policy and asserts prior turn present untruncated. |
| C4: risk truncation site unpinned | Claude/B | Accept | Pinned `estimator._format_context_block:140-145`; Step 5 declares `role_serialized_truncated`. |
| C5: `generate_messages` broad surface | Claude/B | Accept (narrower) | Step 4 reuses `policy.generate` with role-serialized prompt; native `messages=` deferred. |
| Doc gaps: `compliance_layer.md`, `server_proxy.md` | Codex/A | Accept | Added to §15. |
| "DCCL MATCH with fake = circular" | Claude/B | Accept | Binding assertion is structural (prior turn present untruncated), not the fake's verdict. |
| Strategy A cost / poisoning | Both | Preserve as risk | §13. |

## 8. Final Architecture Understanding
`controller.process` order: risk ∥ speculative → **DCCL** (`1936`) → on MATCH+validated draft `_route_compliance_match` early return (`1941-2040`, skips routing/ledger/deliberation) → else `decide_action` → routing → ledger fast-path (`2149-2306`) → dispatch → assembly → entry routes by `final_action`. Canary fails because DCCL sees only `prompt="proceed"`+contract → NO_MATCH → falls through → prior-turn ledger state can apply cached `SAFE_COMPLETE`. Single-final "correct" output is correctness-by-accident (governance NO_MATCH, upstream saw transcript). Fix gives DCCL + the draft it validates the rule-relevant transcript so both paths reach `COMPLIANCE_FAST_PATH`/`NORMAL_COMPLETE` with governed draft `HISTORY_SECRET_42`; the compliance fast-path short-circuits the ledger, removing divergence. Other modules keep reduced context but declare it; the guard prevents an unaligned draft becoming final.

## 9. Implementation Plan

**Step 0 — Tests first (red).** Goal: A–G fail today. Constraint: offline/deterministic; canary strings only in fixtures. Inspect: `test_multiturn_context_propagation.py`, `test_sdk_dccl.py`, `test_server_proxy.py`, `test_controller_speculative_lazy.py`, `test_controller_risk_context_propagation.py`, `test_deliberative_modules_context_propagation.py`, `history_dependent_rule_canary.json`. Add: `tests/test_multiturn_context_alignment.py`. Validate: `./venv/Scripts/python.exe -m pytest tests/test_multiturn_context_alignment.py -q`. Rollback: delete.

**Step 1 — `ConversationContext` + builder.** Constraint: additive/dormant; never mutate `messages`. Add `moralstack/orchestration/conversation_context.py` (`ChatMessage`, `ConversationContext` per task fields, `build_conversation_context(...)`, helpers `role_serialized_transcript(budget_chars)`, `developer_plus_last_user()`, `context_shape_metadata()`); add `conversation_context: ConversationContext | None = None` to `ProcessedRequest`. Reuse existing `_extract_*`/`_messages_to_turns`. Validate: Test A + `test_system_prompt_byte_equality.py`. Rollback: remove module+field.

**Step 2 — SDK/proxy attach context.** Change `wrapper.py:285-303`, `proxy.py:244-256` to set `processed.conversation_context`; keep `conversation_id`/`state_in` separate. Validate: Test D + `test_sdk_wrapper.py` + `test_server_proxy.py`. Rollback: drop assignments.

**Step 3 — DCCL budgeted transcript (canary-critical; C1, C3).** Constraint: MATCH still passes Safety Override. Change `dccl.py` `_build_llm_user_prompt`/`_evaluate_llm`: insert role-ordered CONVERSATION TRANSCRIPT block from `conversation_context`, explicit budget, **keep rule-relevant prior turns** on trim, honest `history_truncation`; minimal `_DCCL_LLM_SYSTEM_PROMPT` edit so prior turns may satisfy a rule and absence is not claimed when context omitted; pass context from controller DCCL call. Decisions: role-serialized full transcript; no `INSUFFICIENT_CONTEXT` v1. Validate: Tests B,F + `test_sdk_dccl.py` + `test_compliance_evaluation.py` + `test_compliance_orchestrator_integration.py` + `test_compliance_fast_path.py`. Rollback: revert builder/invocation.

**Step 4 — Speculative Strategy A (canary-critical; C5).** Constraint: byte-equal single-turn. Change `controller._speculative_generate`: when prior turns exist, build prompt from `conversation_context.role_serialized_transcript(...)` reusing `policy.generate(...)`; else byte-identical. No new protocol method. Validate: Test C + `test_controller_speculative_lazy.py` + `test_speculative_overlap.py` + byte-equality. Rollback: ignore context.

**Step 5 — Risk/deliberative declare context (C4).** Change `estimator.py:122-150` + four `*_module.py` + `persistence_helpers.py`: emit `context_mode=role_serialized_truncated`, `history_truncation=last_n`, n=3, used/available counts. Behavior unchanged. Validate: Test F + `test_controller_risk_context_propagation.py` + `test_deliberative_modules_context_propagation.py`. Rollback: remove metadata.

**Step 6 — `history_source` semantics (BI-002).** Change `wrapper.py`/`proxy.py`/context metadata: cumulative `messages` → `history_source=request_transcript` (authoritative); current-question-only → `history_source=none` (no raw reconstruction v1); keep `state_in` as governance_state; emit `stored_transcript_loaded`, `stored_transcript_reconciliation`, `governance_state_loaded`. Validate: Test E + `test_sdk_session*.py` + `test_conversation_correlation.py`. Rollback: revert tagging.

**Step 7 — Mismatch guard + fallback (BI-001).** Constraint: SAFE_COMPLETE byte-equality. Change controller compliance fast-path + assembly + flag surfacing: compute `delivery_context_broader_than_governance`; if true on multi-turn, do not reuse incomplete draft / take fast-path on unaligned draft, fall through to standard governed path, record flag. Validate: Test G + `test_safe_complete_user_turn.py` + byte-equality + `test_sdk_response.py`. Rollback: flag-only (documented follow-up).

**Step 8 — Ledger non-divergence verification + docs (C2 + §15).** Confirm DCCL MATCH short-circuits before ledger; Test E asserts fast-path taken + ledger not applied; if residual divergence when DCCL ≠ MATCH, document as stateful policy with exact fields (no speculative guard). Then §15 doc updates. Validate: full suite.

## 10. Test Plan
- **A** parser/context preservation (SDK & proxy). **B** DCCL canary (LLM path, fake policy): prior auth turn present untruncated, verdict `MATCH`, no false absence. **C** speculative alignment (full context multi-turn, byte-equal single-turn; poisoning case). **D** SDK/proxy equivalence (compliance-fast-path final-source divergence asserted as documented). **E** cumulative-vs-single: both reach `COMPLIANCE_FAST_PATH`, ledger fast-path **not applied**, output `HISTORY_SECRET_42`. **F** long transcript / older-than-3: DCCL still sees rule-relevant turn; modules log truncation. **G** mismatch guard fallback fires.
- Behavior-locking kept green: byte-equality, `governance_invariants/`, `test_compliance_fast_path.py`, `test_sdk_dccl.py`, `test_compliance_evaluation.py`, `test_server_proxy.py`, `test_conversation_correlation.py`, `test_multiturn_context_propagation.py`, `test_deliberative_modules_context_propagation.py`, `test_observability_*`.
- COMPL-AI benign (#9): Q74/Q248 still `MATCH`/`COMPLIANCE_FAST_PATH` (`q74_full.json`).
- Full: `./venv/Scripts/python.exe -m pytest -q`.

## 11. Observability and Logging Requirements
Per LLM module via existing `record_llm_call`/events (best-effort): `context_mode ∈ {full_native, role_serialized_full, role_serialized_truncated, system_last_user, last_user_only, policy_summary, none}`, raw/system/developer counts, prior user/assistant available+used, `history_truncation`+n, `contains_full_native_messages`, `developer_contract_included`, `final_user_included`, `delivery_context_broader_than_governance`. Proxy/SDK finalize: `history_source`, `stored_transcript_loaded`, `stored_transcript_reconciliation`, `governance_state_loaded`, `request_transcript_message_count`. JSONL minimum; SQLite/UI follow-up. Must answer "Did this module see the prior turn that legitimized the final request?" without source.

## 12. Backward Compatibility Requirements
Additive fields default `None`/empty → single-turn byte-identical; `policy.generate` signature unchanged; DCCL enum unchanged; observability additive (JSONL shape preserved); SDK/proxy fast-path divergence preserved + documented.

## 13. Concurrency and State Risks
New context off `self` (use `ProcessCallContext`); Strategy A raises tokens/latency and feeds transcript to internal LLM even on REFUSE paths (accepted, documented, log counts not bodies); poisoning mitigated by Safety Override + risk + draft validation (Test C); DCCL prompt bounded by budget, never drops rule-relevant prior user turns; lineage `conversation_id` collisions unchanged (noted).

## 14. Rollback Plan
Each step independently revertible; full rollback = remove `conversation_context.py` + field and revert per-module reads → baseline. No `--no-verify`, no deleted tests.

## 15. Documentation Maintenance Plan
| Document | Update | Reason |
|---|---|---|
| `README.md` | Exact SDK/proxy transcript contract + `ConversationContext` | Drift §6 |
| `docs/traces/openai_compatible_multiturn.md` | request transcript vs stored vs state; SDK+proxy `messages→ConversationContext→policy→module messages→decision→delivery` diagram | Drift §6 |
| `docs/traces/complai_llm_rules_flow.md` | DCCL history-aware + canary note | Drift §6 |
| `docs/traces/governance_decision_flow.md` | DCCL transcript input; guard; ledger short-circuit | Behavior change |
| `docs/traces/observability_db_to_ui.md` | New context-shape fields; JSONL minimum | New observability |
| `docs/MORALSTACK_CODEBASE_INDEX.md` | New module + parsing path | New module |
| `docs/CODEBASE_FACTS.md` | Correct "DCCL ignores history"; add verified facts; move cumulative-divergence out of Hypotheses only after Test E | Facts ledger |
| `docs/modules/compliance_layer.md` | DCCL context policy + budgeting | Module contract |
| `docs/modules/server_proxy.md` | `history_source`, transcript-vs-state, unsupported raw persistence | Calling patterns |
| `docs/modules/{risk_estimator,critic,simulator,perspectives,hindsight,orchestrator}.md` | Per-module context policy + declared truncation | Module contracts |
| `CLAUDE.md` | Normalize `docs/TRACES/`→`docs/traces/` | Drift §6 |

Acceptance: no claim of full multi-turn governance beyond tests; distinguish body-available vs module-used, native vs serialized, state vs transcript.

## 16. Open Questions
1. Free-form column to avoid a SQLite migration (JSONL-only acceptable v1). [ASSUMPTION]
2. Adopt SDK governed-draft return now, or keep documented divergence? (Plan keeps documented.) [DRIFT]
3. If Test E shows residual ledger/posture divergence when DCCL ≠ MATCH: documented stateful policy vs minimal guard — decide from the test.
4. Redaction posture if transcript previews are ever persisted (v1: counts/modes + redacted previews/hashes). [ASSUMPTION]

## 17. Go/No-Go Criteria
Go when: (1) A–G first then green; (2) canary → `HISTORY_SECRET_42` via SDK+proxy; (3) DCCL `MATCH`; (4) no false absence (B/F); (5) speculative aligned (C); (6) modules declare context (F); (7) cumulative≡single + ledger not the cause (E); (8) SDK/proxy equivalence (D); (9) COMPL-AI benign no-regression; (10) no hard-coded canary strings outside fixtures; (11) README/docs/traces updated; (12) prompt-vs-message separation documented; (13) full suite green.

## 18. Final Recommendation
Proceed. Tests A–G red→green. Fix DCCL (budgeted role-ordered transcript, never claim absence, LLM path) and speculative generation (Strategy A via role-serialized prompt reusing `policy.generate`) first — together they make both cumulative and single-final paths reach `COMPLIANCE_FAST_PATH` and short-circuit the stateful ledger divergence (C2). Make risk/deliberative modules **declare** reduced context, add the delivery/governance **mismatch guard + safe fallback** (BI-001), specify+test **`history_source`** for all calling patterns (BI-002), and verify the ledger isn't the residual cause (Test E). Defer `INSUFFICIENT_CONTEXT`, raw-transcript persistence, policy-aware summaries, and fast-path normalization. Update docs in the same change. Satisfies all 12 acceptance criteria with the smallest surface while preserving every CLAUDE.md §5 invariant.

---

Note: this is a planning-only synthesizer deliverable — no files were edited and no implementation performed. The `Write` tool is disabled in this context, so the plan could not be saved to `shared-rules-for-splendid-reef.md`; it is delivered inline above. I am not calling `ExitPlanMode`, since this workflow's output is the plan document itself, not approval to implement. If you want me to retry persisting the file (e.g., via an available mechanism) or to proceed to implementation outside the adversarial-planning constraints, tell me and I will.
