I couldn't persist to the plan file (the `Write` tool is disabled in this context), so here is the planning deliverable inline. This is the **planning-only** output required by the documentation-grounded adversarial workflow — no files were edited, no implementation performed.

---

# Claude Plan A

## 1. Objective

Make MoralStack's governance modules reason over the **same materially-relevant conversational context** that final delivery sees when an OpenAI-style request carries a full cumulative transcript. Concretely:

- Introduce one shared `ConversationContext` built by a single builder used by both the SDK wrapper and the HTTP proxy.
- Feed DCCL (highest priority), speculative generation, the risk estimator, and the deliberative modules a context sufficient for history-dependent rules — or have each module **explicitly declare** (in observability) the reduced mode it used.
- Enforce the invariant: *final delivery must not see materially more rule/safety-relevant context than governance.*
- Prove it with Tests A–F; make the `history_dependent_rule_canary` produce `HISTORY_SECRET_42` through SDK and proxy with DCCL returning `MATCH` (not false `NO_MATCH`).

Implementation task starting from a completed investigation; the investigation is not re-planned.

## 2. Baseline Documents Used

- `CLAUDE.md` (§5 invariants, §7 testing, §8 docs) — high authority.
- `docs/MORALSTACK_CODEBASE_INDEX.md`, `docs/CODEBASE_FACTS.md`.
- `docs/traces/openai_compatible_multiturn.md`, `docs/traces/governance_decision_flow.md`, `docs/traces/complai_llm_rules_flow.md`, `docs/traces/observability_db_to_ui.md`.
- `docs/decision_policy.md`, `docs/modules/orchestrator.md`, `docs/modules/risk_estimator.md`, `docs/architecture_spec.md`.
- Investigation evidence: `final_investigation_report.md` + named run artifacts.

## 3. Relevant Verified Facts

**Request parsing / context shape**
- `ProcessedRequest` has only `prompt`, `conversation_history: list[Turn]`, `user_context`, `developer_contract`; **no raw native `messages` field**. [CODE] `moralstack/orchestration/types.py:192-209`.
- `Turn` is user/assistant only; system/developer excluded from history. [CODE] `moralstack/sdk/wrapper.py:96-111`.
- SDK reduces to `prompt=_extract_last_user_message`, `history=_messages_to_turns(messages[:-1])`, `developer_contract=_extract_developer_contract` (last system wins, opaque). [CODE] `wrapper.py:35-93,290-303`.
- Proxy builds the same reduced request. [CODE] `moralstack/server/proxy.py:244-252`.

**Module context modes**
- DCCL `evaluate()` reads contract + `request.prompt` only; **no history**. Structured path matches the **last user prompt**; LLM prompt = contract + user request + speculative draft — history absent. [CODE] `dccl.py:239-247,307-320,365-377,419-421,493-512`.
- DCCL invoked `(request, speculative_draft, risk_estimation=None)`; no transcript added. [CODE] `controller.py:1017-1021`.
- `OpenAIPolicy.generate()` builds `[system?, user]` only — cannot carry a transcript. [CODE] `policy.py:251-254`.
- Speculative generation: `policy.generate(prompt=request.prompt, system=effective_system_for_request(...))` — no history. [CODE] `controller.py:847-872`.
- Risk estimator truncates history to **last 3 turns**, `[role]: content`, 200-char cap. [CODE] `estimator.py:122-150` (`recent = list(history)[-3:]`).
- Critic/simulator/hindsight/perspective serialize last-3 snippets, not native roles. [DOC] investigation matrix; [ASSUMPTION] exact module line numbers (re-verify before editing).

**Routing / final delivery**
- SDK: REFUSE→no upstream; SAFE_COMPLETE→append synthetic user turn; NORMAL→forward original kwargs (full native messages). [CODE] `wrapper.py:333-403`.
- Proxy: REFUSE→synthetic; SAFE→append+forward; `COMPLIANCE_FAST_PATH`→return governed draft, zero upstream; other NORMAL→forward body. [CODE] `proxy.py:312-355`.
- **SDK/proxy divergence**: proxy returns governed draft on `COMPLIANCE_FAST_PATH`; SDK has no such branch. [CODE] `wrapper.py:380-403` vs `proxy.py:338-350`.

**Constraining invariants (CLAUDE.md §5)**: decision/generation separation; system-prompt transparency (SAFE_COMPLETE = extra trailing user turn); hard-signal/Safety-Override supremacy; single-turn byte-equality; best-effort observability; REFUSE never calls upstream.

## 4. Documentation / Code Drift Impact

- `docs/TRACES/*` → `docs/traces/*` rename in working tree; treat lowercase as authoritative. [DRIFT] non-blocking.
- No `ChatMessage` type exists; the task's `ConversationContext` references `list[ChatMessage]` — plan introduces a minimal `ChatMessage` dataclass. [CODE] (absence).
- `moralstack-server` entrypoint is a stub; tests must use `create_app` + `TestClient`. [DOC].
- No `DOC_CODE_CONFLICT` blocks the plan; all behavioral claims re-verified this session.

## 5. Architecture Understanding

Verified flow: build `ProcessedRequest` → `process()` → speculative-overlap (risk ∥ draft) → **DCCL** → on `MATCH`+validated draft → **compliance fast-path** returns governed draft (skips deliberation) → else decide_action → routing → deliberation → assembly → entry layer routes by `final_action`.

Canary fails because, on the cumulative path, **DCCL never sees the prior `HISTORY_AUTH_CANARY_ALPHA` turn** (reads prompt+contract only) → `NO_MATCH` → no fast-path → falls through → `SAFE_COMPLETE` refusal. Even when the single-call answer is correct, it is correct only because upstream saw the transcript, not governance (correctness-by-accident). Fix: one shared context object carrying native role-ordered messages + derived views; DCCL and speculative generation consume the rule-relevant transcript; other modules log their reduced mode; `policy.generate` gains an optional `messages=` path that defaults to today's behavior (byte-equality preserved).

## 6. Proposed Investigation Plan

Read-only pre-implementation verification only:
1. Confirm per-module context-block builders/line ranges in the four `*_module.py` (resolve §3 [ASSUMPTION]).
2. Read controller compliance fast-path handlers (`_route_compliance_match`, `_regenerate_for_contract`, ~1064-2040).
3. Read `sqlite_sink.py` `llm_calls` schema + `orchestration_events` writers to place context-shape fields without a migration if possible.
4. Read existing tests/fixtures (`test_multiturn_context_propagation.py`, `test_sdk_dccl.py`, `test_compliance_fast_path.py`, `test_system_prompt_byte_equality.py`).

## 7. Proposed Implementation Plan

**Step 0 — Tests first (red).** Goal: Tests A–F failing today. Baseline: offline/deterministic fake policy; canary strings only in fixtures (acceptance #10). Add `tests/test_multiturn_context_alignment.py`. Validate: `python -m pytest tests/test_multiturn_context_alignment.py -q`. Rollback: delete file.

**Step 1 — Shared `ConversationContext` + builder.** Goal: structured context (native order + derived views). Baseline: additive, dormant when absent (byte-equality §5.4). Add `moralstack/orchestration/conversation_context.py` (`ChatMessage`, `ConversationContext` per task fields, `build_conversation_context(...)`, view helpers `full_native_messages()`, `developer_plus_last_user()`, `role_serialized_transcript()`, `context_shape_metadata()`); add `conversation_context: ConversationContext | None = None` to `ProcessedRequest`. Reuse existing extract helpers as single source of truth. Validate: TestA + `test_system_prompt_byte_equality.py`. Rollback: remove module + field.

**Step 2 — SDK + proxy use the shared builder.** Change `wrapper.py:285-303`, `proxy.py:244-256` to attach `conversation_context` (keep `conversation_id`/`state_in` separate from transcript). Validate: TestD + `test_sdk_wrapper.py` + `test_server_proxy.py`. Rollback: drop two lines.

**Step 3 — DCCL consumes rule-relevant transcript (canary-critical).** Change `dccl.py:392-512`: `evaluate()` reads `request.conversation_context`; `_build_llm_user_prompt` includes a role-ordered transcript block (system/developer separated from prior turns + final user); minimal `_DCCL_LLM_SYSTEM_PROMPT` edit so prior turns may satisfy the rule and DCCL must not assert a prior turn is absent unless the transcript was provided and lacks it; structured path evaluates prior-turn triggers against `prior_user_messages`. **Decisions (OQ1,2):** role-serialized full transcript; **no `INSUFFICIENT_CONTEXT`** verdict in v1 (too broad for routing), but never claim absence when context wasn't supplied. Validate: TestB + `test_sdk_dccl.py` + `test_compliance_fast_path.py` + `test_compliance_evaluation.py`. Rollback: revert prompt builder.

**Step 4 — Speculative generation aligned (canary-critical).** Add optional `messages` to `policy.generate` (`policy.py:234-284`) — verbatim when provided, else byte-identical `[system?,user]`. In `_speculative_generate` (`controller.py:847-872`), when `conversation_context` has prior turns pass `messages=context.full_native_messages()`, else unchanged. **Decision (OQ3): Strategy A** for multi-turn (the canary answer needs the draft to reflect the prior auth turn; Strategy B alone cannot pass it); single-turn unchanged. Validate: TestC + `test_controller_speculative_lazy.py` + `test_speculative_overlap.py` + byte-equality. Rollback: ignore `messages`.

**Step 5 — Risk/deliberative modules declare context mode.** Change `estimator.py:122-150` + four `*_module.py` builders to emit `context_mode`, `history_truncation=last_n`, `history_truncation_n`, available-vs-used turn counts. **Decision (OQ4):** keep fixed last-3 as a logged optimization in v1 (canary passes via DCCL+speculative); defer policy-aware summary. Validate: TestF + `test_deliberative_modules_context_propagation.py` + `test_controller_risk_context_propagation.py`. Rollback: remove metadata lines.

**Step 6 — Observability fields.** Thread context-shape dict through `persistence_helpers.record_llm_call` into `sqlite_sink`/`jsonl_sink` (prefer existing `meta_json`/payload to avoid migration). Fields per task block (`context_mode`, message counts, truncation, `contains_full_native_messages`, `delivery_context_broader_than_governance`, etc.) + proxy/SDK finalize `history_source`/reconciliation. **Decisions (OQ5,6):** request-body transcript authoritative; **no** raw-transcript persistence in state store v1 (keep governance_state separate; document `conversation_id ≠ transcript`); privacy = counts + redacted previews + hashes, reuse existing redaction. Baseline: best-effort, never alter decision (§5.6). Validate: `test_observability_contract.py` + `test_observability_sqlite_sink.py` + `test_persistence_llm_calls.py`.

**Step 7 — SDK/proxy compliance-fast-path divergence.** **Decision:** document + test the intentional divergence (Test D asserts identical governance action + context metadata, not identical final-text source). Changing SDK to reuse the governed draft = larger behavior change → flag as follow-up. Validate: TestD.

**Step 8 — Documentation (same change).** See §11.

## 8. Test Plan

- Tests A–F (offline, deterministic fake policy). Canary → `HISTORY_SECRET_42` via SDK+proxy; DCCL `MATCH`; rationale no false absence (acceptance #2,3,4); strings only in fixtures (#10).
- Keep green: `test_system_prompt_byte_equality.py`, `tests/governance_invariants/`, `test_compliance_fast_path.py`, `test_sdk_dccl.py`, `test_compliance_evaluation.py`, `test_server_proxy.py`, `test_conversation_correlation.py`, `test_multiturn_context_propagation.py`, `test_deliberative_modules_context_propagation.py`, `test_observability_*`.
- Full suite before done: `python -m pytest -q` (`./venv/Scripts/python.exe -m pytest -q` on Windows); no weakened/deleted tests.
- COMPL-AI benign no-regression (#9): `test_multiturn_context_propagation.py` + Q74 e2e payload.

## 9. Risks

- Byte-equality regression → mitigated by optional fields defaulting to `None` and divergence gated on `prior_turns > 0`.
- Assistant-message poisoning of full-context speculative → Safety Override + risk still in force; draft becomes final only via DCCL MATCH+validation; add poisoning test.
- DCCL prompt-size growth → cap serialized transcript (reuse 3000-char pattern), log truncation, never drop the rule-relevant turn.
- Observability schema → prefer `meta_json`/payload to avoid migration.
- Latency/cost of full-context speculative on multi-turn (accepted for Strategy A; document).
- Deliberative-module line ranges are [ASSUMPTION] until re-read (Step 6/§6.1).

## 10. Open Questions

Answered in-plan: DCCL→role-serialized full transcript; no `INSUFFICIENT_CONTEXT` v1; speculative→Strategy A multi-turn; risk/deliberative→last-3 logged; proxy state→request transcript authoritative, no raw persistence v1; privacy→counts/redacted/hashes.

For reviewer/implementer: (1) does an existing free-form column avoid a SQLite migration? (2) adopt SDK governed-draft return now or keep documented divergence? (3) `messages=`-capable `generate` vs role-serialized prompt as the alignment mechanism?

## 11. Documentation Maintenance Plan

Same-change updates (CLAUDE.md §8): `README.md`; `docs/traces/openai_compatible_multiturn.md`, `.../complai_llm_rules_flow.md`, `.../governance_decision_flow.md`, `.../observability_db_to_ui.md`; `docs/MORALSTACK_CODEBASE_INDEX.md` (new module + field); `docs/CODEBASE_FACTS.md` (move verified DCCL-history/context-shape facts into the table; correct the "DCCL ignores history" entry); relevant `docs/modules/*.md` (per-module context-policy table, prompt/message separation, canary regression section). Trace diagrams: OpenAI messages → shared `ConversationContext` → per-module policy → module model messages → decision → final delivery, with SDK/proxy paths and state read/write marked.

## 12. Final Recommendation

Proceed with the smallest safe path: one additive shared `ConversationContext` (dormant when absent → byte-equality preserved), then fix the two canary-critical consumers first — **DCCL** (role-serialized full transcript; never claim absence) and **speculative generation** (Strategy A via optional `policy.generate(messages=…)` when prior turns exist). Keep risk/deliberative behavior unchanged but make them **declare** their reduced context mode. Document+test the SDK/proxy compliance-fast-path divergence rather than re-route. Land Tests A–F red→green, keep all behavior-locking suites green, update docs in the same change. Defer `INSUFFICIENT_CONTEXT`, raw-transcript persistence, and policy-aware summaries to follow-ups. This meets all 12 acceptance criteria with minimum surface and preserves every CLAUDE.md §5 invariant.

---

Note: the `Write` tool was unavailable, so the plan above is delivered inline rather than saved to `shared-rules-for-humble-pie.md`. Since this is a planning-only deliverable (no code changes are authorized in this workflow), I am not calling ExitPlanMode to request implementation approval — the plan itself is the requested artifact. If you want me to proceed to implementation (outside the adversarial planning constraints) or to retry persisting the file, say so.
