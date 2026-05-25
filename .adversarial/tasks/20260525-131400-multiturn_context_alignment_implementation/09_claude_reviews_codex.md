The plan file isn't writable in this context, so I'll deliver the review directly. Here is my completed adversarial review.

# Claude Review of Codex Plan B

## 1. Verdict

**REVISE.** Plan B is directionally correct, baseline-aware, and respects the load-bearing invariants (additive `ConversationContext`, single-turn byte equality, P0 safety supremacy, observability-never-breaks-request). But it has **two HIGH gaps** that would let an implementer ship a fix that passes the canary "by accident" while leaving the bug class open.

## 2. Baseline Usage Assessment

Good. Documentation used for intent, code for runtime behavior, claims tagged. I independently verified the load-bearing `[CODE]` claims:
- `ProcessedRequest` has no raw-messages/context field — `types.py:192-209`. [CODE]
- DCCL reads only contract + `request.prompt`, never history — `dccl.py:307,419,493-512`. [CODE]
- Speculative = `system + last user` — `controller.py:869-872`. [CODE]
- `policy.generate(prompt, system, config, prediction, model_override)` — no message-list form, so `generate_messages` is genuinely new — `policy.py:234`. [CODE]
- Invariants preserved: additive dormant field (#4); DCCL `MATCH` still passes `classify_safety_override` (#3, `dccl.py:328-340,544-553`); full-context speculative still hits internal policy LLM not upstream (#7). [CODE][DOC]

## 3. Drift Handling Assessment

Adequate, one miss. The three DOC_CODE_CONFLICTs (trace "full history into governance," README "full conversational governance," COMPL-AI DCCL multi-turn claim) are real and confirmed. The `docs/TRACES`→`docs/traces` rename is handled. **Miss:** `CLAUDE.md` (§5 invariant 3, §8, references) still cites uppercase `docs/TRACES/...` post-rename and is omitted from the update set. [DRIFT] No conflict is silently resolved one-sided.

## 4. Critical Issues

**C1 — DCCL prompt truncation re-introduces "claims absence" bug. Severity: high.** Step 3 adds a transcript block but ignores `dccl.py:500-501`, which truncates `raw_text[:3000]` and `draft[:1000]`. A long transcript can push `HISTORY_AUTH_CANARY_ALPHA` past the cutoff → LLM legitimately reports it absent → violates acceptance #4. [CODE] **Fix:** specify transcript placement + length budget, emit honest `history_truncation` metadata, and forbid a bare absence-claiming `NO_MATCH` when trimmed; test with a long transcript.

**C2 — Cumulative vs single-final divergence root cause not located. Severity: high.** Both final requests carry the full transcript, yet cumulative → `SAFE_COMPLETE`/refuse and single-final → `NORMAL_COMPLETE`. The cause is stateful machinery (ledger fast-path / posture at `controller.py:2149-2306`, `conversational_fast_path.is_safe_to_apply`, keyed by `conversation_id`), which Steps 2/6 never touch. [CODE][DOC] **Fix:** investigate and pin the mechanism before asserting equivalence; Test E must assert the ledger/state path, not just matching final actions, or it passes for the wrong reason.

**C3 — Structured DCCL path can't express history rules. Severity: medium.** `_rule_matches_prompt` (`dccl.py:365-377`) matches only the current prompt; SEMANTIC returns False. With default `hybrid` the canary necessarily uses the LLM path. [CODE] **Fix:** state the fix targets `_evaluate_llm`/`_build_llm_user_prompt`; Test B must drive the LLM path with a fake policy.

**C4 — "Risk truncates last-3" asserted but unpinned. Severity: medium.** Controller passes the **full** history to the estimator (`controller.py:804-823`); any last-3 truncation lives inside the estimator, which the plan never cites. [CODE] **Fix:** locate the real truncation site before labeling `history_truncation=last_n`.

**C5 — `generate_messages` is broad new surface. Severity: medium.** Protocol + every mock/double only expose `generate(prompt, system, …)`; call sites swallow `TypeError` and fall back to single-turn (`controller.py:868-874`). [CODE] **Fix:** enumerate protocol/mock updates, or prefer the role-serialized fallback (reuses `generate`) for the first patch.

## 5. Missing Investigations
1. Ledger/conversation-state divergence path (C2) — most important.
2. Risk estimator truncation site (C4).
3. Proxy `COMPLIANCE_FAST_PATH` governed-draft return (`proxy.py:338-361`) under full-context speculative; SDK vs proxy already diverge here.
4. Full-context speculative vs `effective_system_for_request` single-turn/SAFE_COMPLETE byte equality (#2, #4).

## 6. Unsafe Assumptions
- "DCCL returns MATCH for canary" with a fake policy is circular — the binding assertion must be that the prior turn is present untruncated in the DCCL prompt (ties to C1).
- "Request transcript authoritative resolves divergence" — unproven until C2; parsing is already authoritative yet divergence persists.
- `INSUFFICIENT_CONTEXT` "safely now" understates blast radius (headers, SDK metadata, UI, export, tests); deferral is the lower-risk first patch.

## 7. Test Gaps
- No test that the DCCL transcript block isn't silently truncated (C1).
- No test pinning ledger/state reuse for the cumulative canary (C2).
- No multi-turn speculative byte-equality guard (#4).
- COMPL-AI non-regression (acceptance #9) not made concrete (assert Q74/Q248 still `MATCH`/`COMPLIANCE_FAST_PATH`).
- Confirmed valid: listed existing tests exist; `test_multiturn_context_alignment.py` does not — sequencing is sound. [TEST]

## 8. Documentation Update Gaps
- `CLAUDE.md` omitted but holds stale `docs/TRACES/` paths. [DRIFT]
- `CODEBASE_FACTS.md`: move the cumulative-divergence item out of Hypotheses only after C2 is root-caused.
- Otherwise doc coverage is complete.

## 9. Non-Blocking Warnings
- Strategy A raises tokens and feeds full transcript to the internal LLM even on REFUSE; answer privacy/redaction (open Q6) before persisting raw bodies — default to counts/modes.
- Proxy streaming stays unsupported; trace diagrams shouldn't imply parity.
- Verify `validate_draft_against_action` thresholds (`dccl.py:57-74`) still hold when the draft is full-context, so the fast-path doesn't needlessly regenerate.

## 10. Final Judgment

REVISE. Sound, minimal, additive, invariant-respecting plan that correctly localizes the parsing/DCCL/speculative defects. Before execution it must add: C1 (DCCL transcript budgeting + honest truncation) and C2 (root-cause the ledger/state divergence, make Test E assert that path), plus C3–C5, the §5 investigations, the §7 test gaps, and the `CLAUDE.md`/`CODEBASE_FACTS.md` doc corrections. With those it is executable and acceptance-complete; without C1 and C2 it risks a green canary over an unfixed bug class.

---

This review is the deliverable for the planning-only adversarial workflow — no files were edited and no implementation was performed, per the reviewer role. The plan file was not writable in this context, so the review is delivered inline above.
