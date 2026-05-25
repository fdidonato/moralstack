The `Write` tool is disabled in this context — the same constraint both prior planning agents hit. This is a planning-only workflow, so the synthesized plan is itself the deliverable. I verified every contested claim behind the three blocking issues against source this session (cited inline), so the resolutions are evidence-backed. Here is the revised plan.

---

# Final Adversarial Plan

> Revision Synthesizer output, revised to clear the three final-gate blocking issues (FG-001, FG-002, FG-003) while preserving the baseline-grounded structure. Planning-only: no files edited, no implementation. Contested code claims re-verified against source this session.

## 1. Executive Summary
Add one additive, dormant-when-absent `ConversationContext` built by a single builder shared by SDK wrapper and HTTP proxy. Fix the two canary-critical modules first: **DCCL** (role-ordered budgeted transcript in the LLM prompt; never claim a prior turn is absent when context was trimmed/omitted) and **speculative generation** (Strategy A: role-serialized transcript prefix when prior turns exist, so the validated draft the proxy can return on `COMPLIANCE_FAST_PATH` reflects prior authorization). Risk/deliberative modules keep last-3 but **declare** their context mode. A precisely-specified **mismatch guard** (FG-002) prevents an unaligned reused draft from becoming final. The **SDK/proxy fast-path final-source divergence** (FG-001) is kept (no acceptance criterion requires identical text source) but backed by an explicit **proof obligation** with pass/fail criteria, reinforced by the guard. **Observability** sink targets are now mandatory and explicit (FG-003): JSONL + `CONTEXT_SHAPE_RECORDED` orchestration events (SQLite `orchestration_events`, JSON payload, no migration) + folding into existing `llm_calls`/proxy-finalized payloads; UI deferred, docs scoped accordingly. Tests first. `INSUFFICIENT_CONTEXT`, raw-transcript persistence, policy-aware summaries, and fast-path normalization deferred with reasons.

## 2. Scope
**In scope:** additive `ConversationContext` + shared builder; DCCL budgeted role-ordered transcript (canary → `MATCH`, honest truncation, no false absence); speculative Strategy A; **fully-specified mismatch guard (FG-002)**; **FG-001 proof obligation + Test D**; **mandatory FG-003 sink contract**; per-module context-shape observability incl. `delivery_context_broader_than_governance`; `history_source` semantics for Cases 1–4 (incl. `none`); cumulative-vs-single root-cause + Test E; Tests A–G (offline, deterministic, canary strings only in fixtures); docs.

**Out of scope (deferred, with reasons):** `INSUFFICIENT_CONTEXT` verdict (touches headers/SDK/UI/export/tests; not needed for `MATCH`; DCCL still forbids false-absence as the permitted partial measure); raw-transcript persistence (v1 returns `history_source=none`, documented unsupported); policy-aware summaries (last-3 retained but declared); **normalizing the SDK/proxy fast-path final-source divergence** (behavior change touching headers/SDK metadata/`test_server_proxy.py`/`test_sdk_dccl.py`; FG-001 instead discharged by proof + guard); proxy streaming; `controller.process()` refactor.

## 3. Baseline Documents Used
`CLAUDE.md` (§5 invariants, §6–8); `docs/MORALSTACK_CODEBASE_INDEX.md`; `docs/CODEBASE_FACTS.md`; `docs/traces/{openai_compatible_multiturn,governance_decision_flow,complai_llm_rules_flow,observability_db_to_ui}.md`; `docs/decision_policy.md`; `docs/modules/{orchestrator,risk_estimator,compliance_layer,server_proxy}.md`; `docs/architecture_spec.md`; `final_investigation_report.md` + run artifacts. [DOC]

## 4. Evidence Base (re-verified this session)
- `ProcessedRequest` has no raw-messages field — `orchestration/types.py:192-209`. [CODE]
- SDK parsing prompt/history/contract only — `sdk/wrapper.py:285-303`; proxy mirrors, no native messages — `server/proxy.py:244-252`. [CODE]
- DCCL `evaluate(request, speculative_draft, risk_estimation)` discards `risk_estimation` (`_ = risk_estimation`), reads contract + `request.prompt`, never `conversation_history` — `dccl.py:214-269`, `_evaluate_llm:392-491` (`user_prompt = request.prompt`, `:419`). [CODE]
- **C1**: `_build_llm_user_prompt` truncates `raw_text[:3000]`, `draft[:1000]`, single `USER REQUEST` line, **no transcript block** — `dccl.py:493-512`. [CODE]
- Hybrid default; structured matches only `request.prompt`; canary uses LLM path — `dccl.py:252-269`; `.env.template:236`. [CODE][DOC]
- DCCL MATCH → `_route_compliance_match` early return `controller.py:1966`(Case1)/`2001`(Case2), **before** risk routing and ledger lookup (`2149-2306`); `case1` gated by `force_case2` at `1941-1948` (guard insertion point). [CODE]
- `_route_compliance_match` (`1205-1297`) turns draft into final via `run_benign_fast_path(..., speculative_draft=...)`; regen-fail emits `COMPLIANCE_MATCH_DOWNGRADED` + `spec_handle.abandon` (`2017-2040`). [CODE]
- Speculative = `policy.generate(prompt=request.prompt, system=…)`, no transcript — `controller.py:847-872`; `policy.generate` has no message-list form — `policy.py:234-284`. [CODE]
- **FG-001 divergence**: proxy returns governed draft on `path=="COMPLIANCE_FAST_PATH"` + non-empty content, **no upstream call** — `proxy.py:338-361`; SDK NORMAL_COMPLETE always calls wrapped client with original kwargs — `wrapper.py:380-403`. Both run **after** `process()` returns, consulting `result.path`/`final_action` — downstream of finalized governance. [CODE][DRIFT]
- Risk truncation site `estimator._format_context_block` (`[-3:]`,`[:200]`) — `estimator.py:122-150`; controller passes full history — `controller.py:794-823`. [CODE]
- **FG-003 sinks**: `record_llm_call`→`async_persist_llm_call` (llm_calls + JSONL) — `persistence_helpers.py:25-47`; DCCL persists prompt/system/raw into llm_calls — `dccl.py:447-465`; `persist_orchestration_event(..., payload={...})` writes `orchestration_events` (SQLite) + JSONL with free-form JSON payload — `proxy.py:376-394`. No migration needed. [CODE]

## 5. Baseline Compliance
| Source | Constraint | Preservation |
|---|---|---|
| §5.1 | Decision/generation separation | Guard sets a flag and reroutes (regen/downgrade), never infers action from text; DCCL stays structured. |
| §5.2 | System-prompt transparency | `_build_safe_complete_user_turn` untouched; byte-equality green. |
| §5.3 | Hard-signal/Safety-Override supremacy | DCCL MATCH still passes `classify_safety_override`; `is_hard_signal_refuse` re-evaluated after ledger patch; guard never bypasses it. |
| §5.4 | Single-turn byte-equality | Additive dormant context; DCCL/speculative/guard gate on `prior_turn_count > 0`. |
| §5.6 | Observability never breaks request | All emits best-effort in swallowing try/except. |
| §5.7 | REFUSE no upstream | Routing unchanged; guard only affects compliance-fast-path draft reuse. |
| §6 | Smallest change | Guard reuses existing `force_case2`→Case-2/downgrade machinery; divergence kept (proof, not rewrite); observability reuses existing channels (no migration). |

## 6. Doc/Code Drift Resolution
| Item | Severity | Resolution |
|---|---|---|
| Trace "full history passed into governance" vs DCCL/speculative ignore it | High | Code wins; distinguish body-present/risk-passed vs DCCL/speculative-used; document context object. |
| README/spec "full conversational governance" | Medium | Code wins; reword to exact contract + per-module policy. |
| `complai_llm_rules_flow.md` DCCL "contract-aware over history" | Medium | Code wins (canary NO_MATCH); update after fix + canary note. |
| `docs/TRACES/*` vs `docs/traces/*` | Low | Normalize lowercase in touched files. |
| **SDK/proxy fast-path final-source divergence** | High (FG-001) | **Resolved, not silently chosen**: documented as intentional in `server_proxy.md`+`CODEBASE_FACTS.md`, bounded by §7 proof obligation + §7 guard; normalization tracked-deferred in §2. |

The one gate-flagged conflict (`SDK_PROXY_COMPLIANCE_FAST_PATH_FINAL_SOURCE_DIVERGENCE`) is resolved by proof obligation + guard + doc reconciliation above.

## 7. Final-Gate Blocking-Issue Resolution

### FG-001 — SDK/proxy `COMPLIANCE_FAST_PATH` divergence: proof obligation
**Decision:** keep divergence in v1 (normalization deferred); discharge via proof it cannot change governance outcome, reinforced by the FG-002 guard.

**Argument:** `final_action`, `path`, `compliance_verdict`, `reason_codes`, `risk_category` are computed entirely inside `process()` and are identical for SDK/proxy given the same `ConversationContext` (Test D asserts). The SDK↔proxy difference lives **only** in the post-`process()` text-delivery branch (`proxy.py:338-361` vs `wrapper.py:380-403`) — proxy reuses the governed draft; SDK calls the wrapped client with full original `messages`. Neither mutates the decision. For the canary both yield `HISTORY_SECRET_42`: proxy's draft is, after Strategy A, generated with the role-serialized transcript (aligned with DCCL), and the FG-002 guard guarantees an *unaligned* draft is never the reused source; SDK's upstream sees the full transcript (a **superset** — the allowed direction).

**Pass/Fail (Test D):** PASS iff (1) SDK & proxy emit identical `final_action`/`path=COMPLIANCE_FAST_PATH`/`compliance_decision=MATCH`/`reason_codes`/`risk_category` for the canary; (2) both final contents == `HISTORY_SECRET_42`; (3) `final_text_source` recorded and differs (`governed_draft` vs normal upstream) — observable, not silent; (4) proxy-reused draft has `context_mode == role_serialized_full`. FAIL if proxy reuses a `system_last_user_only` draft while `prior_turn_count > 0`. This is the in-scope proof the remaining divergence is governance-neutral for the target task.

### FG-002 — Delivery/governance mismatch guard: exact algorithm
Pure fn `evaluate_delivery_context_guard(ctx, candidate)` in the new context module, called from the controller.

**Inputs:** `prior_turn_count = len(ctx.prior_user_messages)+len(ctx.prior_assistant_messages)`; `governance_context_mode` (gating module mode — DCCL `role_serialized_full` post-fix); `candidate_context_mode` (mode of the artifact about to become final — the draft on the fast path); `is_draft_reused_as_final` (True only on `_route_compliance_match` draft reuse and the proxy `governed_draft` branch).

**Boolean `delivery_context_broader_than_governance`:** True iff `prior_turn_count > 0` **and** `is_draft_reused_as_final and candidate_context_mode == "system_last_user_only" and governance_context_mode in {"role_serialized_full","full_native"}`. For non-draft-reuse paths the flag is **False** by construction (final delivery sees the full transcript, a superset, while post-fix DCCL also saw it) → flag **recorded only**, never blocking.

**Fallback when True (blocking, draft-reuse path only):** (1) set `force_case2=True` at `controller.py:1941-1948` → existing `_regenerate_for_contract`+`_revalidate_draft` produce a draft from `ctx.role_serialized_transcript(...)`; (2) if validated → reuse (Case 2, `COMPLIANCE_DRAFT_REGENERATED`), `mismatch_guard_action="regenerated_aligned"`; (3) if regen unavailable/invalid → emit `COMPLIANCE_MATCH_DOWNGRADED`, `spec_handle.abandon("context_mismatch_guard","DELIBERATIVE_PATH")`, fall through to the standard governed pipeline, `mismatch_guard_action="downgraded_to_pipeline"`.

**Metadata set (every path):** `delivery_context_broader_than_governance`, `mismatch_guard_action ∈ {none,regenerated_aligned,downgraded_to_pipeline}`, `governance_context_mode`, `candidate_context_mode`, `prior_turn_count`.

**Ordering/precedence:** (1) risk∥speculative `1929-1935`; (2) DCCL `1936`→verdict; (3) **guard** between `1941` and the `case1` decision at `1948`, only ever converting `case1`→Case-2/downgrade (strictly more conservative); (4) `_route_compliance_match` returns `1966`/`2001` **before** risk routing and ledger lookup `2149` (so the ledger cannot reintroduce an unaligned reuse); (5) entry layer: proxy reuses `governed_draft` **only** when `path==COMPLIANCE_FAST_PATH and delivery_context_broader_than_governance is False`, else upstream regen (full transcript = safe superset); SDK unaffected.

### FG-003 — Observability persistence: mandatory sink targets
**Mandatory v1 (no SQLite migration):** per-module context-shape fields (`context_mode`, raw/system/developer counts, prior user/assistant available+used, `history_truncation`+n, `contains_full_native_messages`, `developer_contract_included`, `final_user_included`, `delivery_context_broader_than_governance`, `mismatch_guard_action`) → best-effort `CONTEXT_SHAPE_RECORDED` orchestration event per LLM-using module via `persist_orchestration_event(..., payload={...})` → **SQLite `orchestration_events` + per-event-type JSONL** (free-form JSON payload, no migration); also folded into the existing `llm_calls` summary JSON where a module already persists a call (e.g. DCCL `dccl.py:447-465`). Request-level fields (`history_source`, `stored_transcript_loaded`, `stored_transcript_reconciliation`, `governance_state_loaded`, `request_transcript_message_count`) → folded into existing `PROXY_OUTPUT_FINALIZED`/`proxy.request_finalized` payload and SDK `_finalize_audit` → **SQLite `proxy_request_events`/`orchestration_events` + JSONL**.

**Deferred (documented non-goal):** dedicated typed SQLite columns; UI rendering. `docs/traces/observability_db_to_ui.md` states these fields are queryable from event JSON payloads + JSONL in v1, UI a follow-up; no doc claims unimplemented UI surfacing. Test asserts presence in JSONL **and** SQLite event payloads (round-trip via `SqliteReadStore.get_orchestration_events_for_request`).

## 8. Critic Response Matrix (condensed)
BI-001 → FG-002 guard fully specified (§7), Test G. BI-002 → `history_source=none`, no raw reconstruction v1, tested. C1 → budgeted transcript keeps rule-relevant turns, honest truncation, forbid bare absence. C2 → stateful ledger short-circuited by DCCL MATCH, Test E. C3 → fix targets LLM path. C4 → pinned `estimator._format_context_block:122-150`, declared `role_serialized_truncated`. C5 → reuse `policy.generate`, native `messages=` deferred.

## 9. Implementation Plan
- **Step 0** Tests A–G first (red); add `tests/test_multiturn_context_alignment.py`. Validate scoped pytest.
- **Step 1** `moralstack/orchestration/conversation_context.py` (`ChatMessage`, `ConversationContext`, `build_conversation_context`, `role_serialized_transcript(budget)`, `developer_plus_last_user`, `context_shape_metadata`, `evaluate_delivery_context_guard`); add `conversation_context` field to `ProcessedRequest`. Reuse `_extract_*`/`_messages_to_turns`. Validate Test A + byte-equality.
- **Step 2** SDK/proxy attach context (`wrapper.py:285-303`, `proxy.py:244-256`); keep `conversation_id`/`state_in` separate. Validate Test D + wrapper/proxy tests.
- **Step 3** DCCL budgeted transcript (`dccl.py:_build_llm_user_prompt`/`_evaluate_llm`, `419`,`493-512`); minimal `_DCCL_LLM_SYSTEM_PROMPT` edit; MATCH still passes Safety Override. Validate Tests B,F + compliance suites.
- **Step 4** Speculative Strategy A (`controller._speculative_generate:847-872`); record `context_mode`. Validate Test C + speculative tests + byte-equality.
- **Step 5** Guard wiring: call guard at `1941-1948`, OR into `force_case2`, set §7 metadata; proxy `governed_draft` branch also requires `delivery_context_broader_than_governance is False`. Validate Test G + fast-path/proxy tests.
- **Step 6** Risk/deliberative declare context (`estimator.py:122-150` + 4 modules + `persistence_helpers`). Validate Test F + context-propagation tests.
- **Step 7** `history_source` semantics. Validate Test E + session/correlation tests.
- **Step 8** Observability emit per §7-FG-003. Validate round-trip + JSONL + `test_observability_*`.
- **Step 9** Ledger non-divergence verification (Test E) + §15 docs. Validate full suite.

## 10. Test Plan
A parser preservation; B DCCL canary (LLM path, fake policy: prior turn present untruncated, `MATCH`, no false-absence); C speculative alignment + poisoning case + byte-equal single-turn; **D SDK/proxy equivalence + FG-001 proof obligation (criteria 1–4)**; E cumulative≡single + ledger not applied; F long-transcript/older-than-3; **G guard fires → `regenerated_aligned`/`downgraded_to_pipeline`, proxy doesn't return unaligned draft**; observability: fields in JSONL **and** SQLite event payloads. Behavior-locking kept green (byte-equality, `governance_invariants/`, `test_compliance_fast_path`, `test_sdk_dccl`, `test_compliance_evaluation`, `test_server_proxy`, `test_conversation_correlation`, `test_multiturn_context_propagation`, `test_deliberative_modules_context_propagation`, `test_observability_*`). COMPL-AI benign Q74/Q248 no-regression. Full: `./venv/Scripts/python.exe -m pytest -q`.

## 11. Observability/Logging
Per-module `CONTEXT_SHAPE_RECORDED` event + folded `llm_calls` summary; request-level fields in proxy/SDK finalize. **Mandatory sinks (FG-003): JSONL + SQLite `orchestration_events`/`proxy_request_events` JSON payloads; UI deferred.** Logs answer "Did this module see the prior turn that legitimized the final request?".

## 12. Backward Compatibility
Additive fields default None/empty → single-turn byte-identical; `policy.generate` unchanged; DCCL enum unchanged; observability additive (no migration, JSONL shape preserved); divergence preserved + proven governance-neutral.

## 13. Concurrency/State Risks
Context off `ProcessCallContext` not `self`; Strategy A token/latency cost and transcript-to-internal-LLM even on REFUSE (accepted, log counts not bodies); poisoning mitigated by Safety Override + risk + validation (Test C); DCCL prompt budget never drops rule-relevant user turns; lineage collisions unchanged; guard strictly conservative (cannot loosen under concurrency).

## 14. Rollback
Each step independently revertible; full rollback = remove `conversation_context.py` + field, revert per-module reads. Guard wiring is one OR + one proxy condition. No `--no-verify`, no deleted tests.

## 15. Documentation Maintenance
`README.md` (transcript contract + context object); `docs/traces/openai_compatible_multiturn.md` (transcript vs stored vs state; SDK+proxy diagram; guard + ledger short-circuit); `complai_llm_rules_flow.md` (DCCL history-aware + canary); **`governance_decision_flow.md` (deterministic guard flow + precedence — FG-002)**; **`observability_db_to_ui.md` (mandatory sink targets per field — FG-003; UI deferred)**; `MORALSTACK_CODEBASE_INDEX.md` (new module + guard); `CODEBASE_FACTS.md` (correct DCCL-ignores-history; document divergence as intentional+bounded; move cumulative-divergence out of Hypotheses only after Test E); **`modules/compliance_layer.md` (DCCL context contract, truncation, rationale constraints)**; **`modules/server_proxy.md` (request-transcript authority vs state, reconciliation, governed_draft vs SDK upstream divergence + guard precondition)**; `modules/{risk_estimator,critic,simulator,perspectives,hindsight,orchestrator}.md` (per-module context policy + declared truncation); `CLAUDE.md` (`docs/TRACES/`→`docs/traces/`). Acceptance: distinguish body-available vs module-used, native vs serialized, state vs transcript; no claim of unimplemented UI.

## 16. Open Questions
1. Full SDK governed-draft normalization later vs proven-neutral divergence (plan keeps it). [DRIFT] 2. Redaction posture if transcript previews persisted (v1: counts/modes + redacted/hashes). [ASSUMPTION] 3. Residual ledger divergence when DCCL≠MATCH → decide from Test E. [ASSUMPTION]

## 17. Go/No-Go
(1) A–G first then green; (2) canary→`HISTORY_SECRET_42` via SDK+proxy; (3) DCCL `MATCH`; (4) no false-absence (B/F); (5) speculative aligned (C); (6) modules declare context (F); (7) cumulative≡single + ledger not the cause (E); (8) SDK/proxy decision equivalence + FG-001 proof (D); (9) guard reroutes deterministically (G); (10) FG-003 fields in JSONL **and** SQLite payloads; (11) COMPL-AI benign no-regression; (12) no hard-coded canary strings outside fixtures; (13) docs incl. guard flow + sink targets + prompt/message separation; (14) full suite green.

## 18. Final Recommendation
Proceed. FG-001 resolved by an explicit proof obligation (decision computed in shared `process()`; divergence downstream; proxy's reused draft guard-aligned) with pass/fail Test D. FG-002 resolved by a fully-specified deterministic guard (inputs, boolean, fallback sequence, metadata, exact ordering before ledger/final delivery) reusing existing Case-2/downgrade machinery. FG-003 resolved by mandatory migration-free sink targets (JSONL + `orchestration_events`/`proxy_request_events` JSON) with UI deferred and docs scoped. Fix DCCL and speculative first so cumulative and single-final both reach `COMPLIANCE_FAST_PATH` and short-circuit the stateful ledger; declare reduced context elsewhere; specify+test `history_source`. Smallest surface; all §5 invariants preserved; satisfies the 12 acceptance criteria.

---

**Note on persistence:** I attempted to write this to `shared-rules-for-sunny-bumblebee.md` but the `Write`/edit tools are disabled in this planning-only context, so the plan is delivered inline above (as the prior synthesizer also had to do). I am not calling `ExitPlanMode` — this adversarial-workflow's deliverable is the plan document itself, not approval to implement, and no plan file could be created. If you want me to retry persisting it via a different mechanism, or to proceed to implementation outside the planning constraints, say so and I will.
