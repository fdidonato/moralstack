# Investigation — deliberation "wasted" revise cycles & simulator harm signal

- **Branch:** `investigate/deliberation-wasted-revise-cycles`
- **Date:** 2026-07-09
- **Scope:** read-only analysis of observability DBs + code. No behavior changed.
- **Relation to P3:** NONE. The P3 change (bounded, tenant-aware correlation store) touches only
  `moralstack/server/conversation_correlation.py` + `proxy.py`. Everything below lives in the
  deliberation/decision layer and is pre-existing behavior, identical on pre-P3 `main`.
- **Data sources:** `moralstack.db` (P3 smoke run, renamed from the throwaway smoke DB),
  `moralstackbkp.db` (project backup), and the 15 COMPL-AI run DBs under
  `C:/Users/fdidonato/Documents/progetti/compl-ai/logs/*/moralstack-logs/moralstack.db`.
- **Trigger:** the P3 smoke run showed a deliberative request whose cycle-2 rewrite reproduced the
  cycle-1 draft byte-for-byte ("revise cycles that don't converge / rewrite changes nothing").

## Question asked
Is there a bug where the speculative draft is not actually revised on critic/simulator guidance, and
where the loop runs extra cycles that cannot converge because the rewrite never changes the draft?

## Verdict
**No correctness/liveness bug.** Two real, pre-existing observations surfaced: (a) an *efficiency* cost
(soft-revision rewrites that produce no textual change), and (b) a *calibration* question (the simulator's
`semantic_expected_harm` appears decision-inert on the case examined).

## Verified facts (code read this session; cite path:line)

1. **Empty-guidance rewrites are already guarded — no billable LLM call is spent when aggregated guidance
   is empty.**
   - Soft-revision pass returns early: `deliberation_runner.py:2573-2575` (`guidance =
     build_aggregated_guidance(state); if not guidance.strip(): return state`).
   - Main cycle logs a non-billable marker and returns: `deliberation_runner.py:2678-2707`
     (`rewrite (SKIPPED_EMPTY_GUIDANCE)`, `billable_provider_call=False`).
   - Corpus: **162** such SKIPPED rows observed across the DBs (the guard firing correctly).

2. **The rewrite trigger uses *aggregated* guidance = critic + perspectives + simulator**, not the critic
   alone: `build_aggregated_guidance` (`guidance_builder.py:157`), consumed at
   `deliberation_runner.py:2573,2678`. The rewrite prompt embeds it as `REVISION FEEDBACK:\n{guidance}`
   (`models/policy.py:425`). The prompt is deliberately change-averse for soft feedback: enumerated answers
   with no hard requirement must be returned unchanged; "do not add caveats/length unless necessary"
   (`models/policy.py:425-428`).

3. **Corpus measurement (billable, non-skipped rewrites), ~13k requests:**
   - 543 real rewrites; **198 (36.5%) produced NO textual change** on soft-only guidance
     ("NOCHANGE_SOFTONLY"); 344 (63.5%) changed the draft; 1 kept text despite hard/critic guidance
     ("NOCHANGE_HARD").
   - One COMPL-AI run: **110/114** rewrites were no-op soft rewrites.
   - Analysis scripts kept in the session scratchpad (outside the repo): `analyze_revise.py`,
     `analyze_revise2.py`. (Not committed — re-derivable from the queries in this note.)

4. **Bounded, non-runaway loop.** `while state.cycle < max_cycles` (`deliberation_runner.py:1433`),
   `max_deliberation_cycles = 2` default (`orchestration/types.py:523`,
   `orchestration/config_loader.py:71`). `_effective_max_cycles` returns the cap for
   `risk_score >= low` or `risk_category ∈ {sensitive, morally_nuanced}`, else 1
   (`deliberation_runner.py:424-432`).

5. **Case `cfa2f88d` (P3 smoke, SDK single "is pain experienced before limb amputation?", domain=medical):**
   - Convergence events (from `moralstack.db`): cycle 1 `CONVERGENCE_EVALUATED converged=False,
     early_convergence_considered=True, early_convergence_accepted=False` → `EARLY_CONVERGENCE_REJECTED`;
     cycle 2 `CONVERGENCE_EVALUATED decision=CYCLES_EXHAUSTED, converged=False`. So the loop terminated by
     hitting the cycle cap, not by positive convergence — the conservative cycle-1 early-stop policy
     (`convergence_evaluator.py:168-216`, default reject) declined to stop after cycle 1 for a sensitive
     request. This is by design, not a hang.
   - Rewrite: billable, received `REVISION FEEDBACK = [BALANCE]…; [BALANCE]…; [SIMULATOR - Avoid] Potential
     emotional distress from discussing pain.; Misinterpretation leading to unnecessary fear of amputation.`
     Output was **byte-identical** to the draft (217 == 217 chars). The simulator guidance reached the
     model; the model no-op'd (vague negative "avoid" hint + change-averse prompt on an already-clinical
     draft).
   - **What set `final_action = SAFE_COMPLETE`:** the decision policy SENSITIVE branch
     (`runtime/decision/safe_complete_policy.py:192-215`): `risk_category=sensitive` →
     `REASON_RISK_SENSITIVE`; `ctx.overlay_sensitive=True` (medical = regulated overlay) →
     `REASON_DOMAIN_REGULATED`; the factual-query exemption at `:203` requires `not overlay_sensitive`, so it
     did NOT apply → `REASON_SAFE_COMPLETE_REQUIRED` → forced SAFE_COMPLETE. Decision trace reason_codes:
     `[RISK_SENSITIVE, DOMAIN_REGULATED, SAFE_COMPLETE_REQUIRED]`; `risk_score=0.35` (grey zone
     `[low=0.30, medium=0.70)`), `operational_risk=NONE`, `hard_violation_codes=[]`.
   - **The simulator harm did NOT drive the decision.** Decision-trace signals show `harm_type:none`;
     `sim_semantic_expected_harm=0.42` is recorded for observability only. So on this request the
     simulator's 0.42 harm influenced *neither* the delivered text (no-op rewrite) *nor* the final action
     (category+overlay drove it).

## Hypotheses / candidate follow-ups (UNVERIFIED)

- **[H1] `semantic_expected_harm` may be decision-inert.** On `cfa2f88d` a 0.42 semantic-harm reading with
  dominant types `[psychological_harm, misinformation]` affected nothing decision-relevant (the decision
  used `expected_valence=+0.02`, positive, and category/overlay). Open question: grep every consumer of
  `semantic_expected_harm` across the codebase to determine whether it is wired to any gate or is
  observability-only. If observability-only, decide whether that is intended.
- **[H2] Soft-revision effectiveness.** ~36.5% of billable rewrites are soft-only no-ops. The
  `[SIMULATOR - Avoid]` / `[BALANCE]` guidance format is negative/vague and, combined with the change-averse
  rewrite prompt, frequently yields zero edit. Candidate optimizations (design decision, not a bug fix):
  (A) more aggressive cycle-1 early-stop for sensitive-but-clearly-benign factual queries; (B) predictive
  skip of soft-only rewrites for enumerated/constrained answers; (C) make simulator guidance prescriptive
  ("change X to Y") rather than "avoid X". Any of these trades governance thoroughness for cost/latency and
  should be owner-approved.
- **[H3] The single `NOCHANGE_HARD` case** (a rewrite that kept the text despite hard/critic guidance,
  1/543) is the only potentially safety-relevant outlier and warrants individual inspection, separate from
  the efficiency theme.

## What is NOT wrong
- No infinite loop (bounded by `max_cycles`).
- No fail-open (the delivered answer is governed; `cfa2f88d` correctly delivered SAFE_COMPLETE).
- No empty-guidance billable rewrites (guarded).
- No P0 invariant impact; nothing here is caused by the P3 change.
