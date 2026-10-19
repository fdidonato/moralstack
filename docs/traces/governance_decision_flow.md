# TRACE — Governance decision flow (end to end)

Path of a single request from input to response, with side effects.
Claims are grounded in the cited source. Path-specific caveats and
unverified branches are noted inline.

Primary code: `moralstack/orchestration/controller.py` (`process`, line 1885),
`moralstack/sdk/wrapper.py`, `moralstack/runtime/decision/safe_complete_policy.py`.

---

## 0. Entry

- **SDK**: `client.chat.completions.create(**kwargs)` →
  `GovernedCompletions._create_inner` (`wrapper.py:310`).
- **Proxy**: `POST /v1/chat/completions` → `_handle_chat_completion_sync`
  (`server/proxy.py:197`), run inside a threadpool.

Both build a `ProcessedRequest` and call `orchestrator.process(...)`.

## 1. Input request & message parsing

`wrapper.py:310-329`:
- `conversation_context = build_conversation_context(messages)` parses the full
  OpenAI message list once.
- `user_message` is `conversation_context.final_user_message`.
- `conversation_history = context_to_turns(conversation_context)` contains prior
  `user`/`assistant` turns before the final user message.
- `developer_contract = conversation_context.developer_contract`, derived from
  the last non-empty `system`/`developer` message with `mode="opaque"`.
- `ProcessedRequest(prompt, conversation_history, user_context(domain_overlay),
  developer_contract, conversation_context)`.

Session/turn (SDK): `conv_id = session.conversation_id`,
`turn_idx = session.next_turn_index()`, `conv_state = session.current_state`
(`wrapper.py:331-340`). A snapshot `state_in` is captured *before*
`session.update_from_result` overwrites it.

Proxy equivalent: `conversation_id` resolution + stateless `turn_index`
(`proxy.py:218-256`), `conv_state = store.get(conversation_id)`.

## 2. Controller setup (`controller.py:1962-1987`)

- Coerce `str` → `ProcessedRequest`; build `ProcessCallContext`.
- Set context vars: `set_current_session_id`, `set_current_turn_number`.
- `DefaultPersistence.set_request_context(request_id)` and
  `ensure_run_and_upsert_request(...)` — **side effect**: pre-inserts the
  `requests` row so later FK-bound events succeed. The schema is ensured lazily,
  once per DB path (`_db_initialized_for`): a changed `get_db_path()` triggers a new `init_db`.
- `trace = self._trace_lifecycle.start_trace(request_id)`.

## 3. Risk estimation (`controller.py:1990-1997`)

- If `enable_speculative_generation` and a policy is set: `_run_speculative_overlap`
  runs **risk estimation and a speculative draft in parallel** (two-worker
  `ThreadPoolExecutor`, contextvars copied). The method blocks only for risk;
  the draft continues in the background (`controller.py:930-988`). The
  speculative draft calls the internal **policy LLM** (`self.policy.generate`),
  not the wrapped/upstream client. This means an internal LLM call may already
  be in-flight before any routing decision — including on paths that will
  ultimately REFUSE.
  - **Opt-in exception (`generation="upstream_then_verify"`, default off):**
    `_speculative_generate` selects `gen = request.upstream_draft_generator or
    self.policy`. When the SDK/proxy wired an `UpstreamDraftGenerator` (client
    `model` supplied), the *speculative draft only* is produced by the caller's
    model — the same hard-signal/REFUSE discard and route-specific validation
    below still apply unchanged; the draft is never a direct delivered answer.
    An empty/whitespace upstream draft is "no draft" → internal regeneration
    with `self.policy` (never a passthrough, never a refusal for that reason).
    The same holds for any exception from the selected generator, `TypeError`
    included: there is no retry (a retry would be a second, ungoverned upstream
    call without system prompt or history); `_speculative_generate` returns
    `(None, None)` and the route regenerates internally.
    See `moralstack/orchestration/upstream_draft.py` and
    `.claude/rules/governed-delivery.md`.
- Else: `risk_estimation = self._estimate_risk(request)` (`controller.py:803`).

`_estimate_risk` forwards the developer-contract text and conversation history to
the estimator (`controller.py:812-838`) and emits `CONTEXT_SHAPE_RECORDED`. The
risk estimator declares its reduced history mode as `role_serialized_truncated`
when only the last-3 window is used. The estimator runs three parallel
mini-estimators (intent / signals q1–q17 / operational) and calibrates them into
a `RiskEstimation` (`models/risk/estimator.py:794-1155`).

**Single upstream constitution-retrieval wave** (unify-constitution-retrieval-
single-pass). `_estimate_risk` also computes the retrieval query policy (RAW
prompt iff no meaningful developer-contract text AND no conversation history,
else the enriched contract+history+prompt query) and the unified
`retrieval_top_k = max(risk_top_k, critic_top_k)`, passing both into
`estimate(..., retrieval_query=, retrieval_top_k=)`. Inside the risk thread,
`_get_principles_context` makes the ONE `store.retrieve(...)` call for the
whole request (`domain=None`, unchanged from before) and returns the full
retrieved principle list, the domain-prefilter debug snapshot, and
retrieval-status flags, all carried on `RiskEstimation`
(`relevant_principles`, `retrieval_metadata`, `retrieval_count`,
`retrieval_succeeded`, ...). **Domain provenance (retrieval-request-scoped-state,
P0 fix):** `detected_domain` travels on `retrieve()`'s typed
`PrincipleRetrievalResult.prefiltered_domains` return value — never through the
removed `get_debug_info()` shared-instance-attribute channel, which a concurrent
request on the same process-wide constitution store could overwrite between
another request's write and read (measured 37.2% cross-attribution on 709
production requests before the fix; see `docs/CODEBASE_FACTS.md`). A
`constitution_store` without `retrieve()` degrades loudly to
`detected_domain=None` (rate-limited WARNING, `retrieval_metadata["domain_channel"]
= "fallback_no_retrieve"`) instead of silently inheriting a foreign domain; the
normal path stamps `domain_channel = "retrieve"`. The marker reaches the audit
trail via the `REQUEST_ANALYSIS_CONTEXT` `stage_payload` (§ below). Back in the controller,
`_build_request_analysis_from_risk` lifts this into a `RequestAnalysisContext`
— authoritative even when `relevant_principles == ()` — gated on
`retrieval_succeeded` (never on emptiness), and emits the single
`RELEVANT_PRINCIPLES_RETRIEVED` event. `_route_fast_path`/`_route_deliberative`
each build this context and pass it into `run_fast_path`/`run_deliberative_path`,
so the deliberation critic, the FAST_PATH `quick_check` (filtered to HARD), and
the quick-check-failed deliberative fallback all reuse the same retrieval —
exactly one `store.retrieve(...)` call per request across routes. Only when
the risk-owned retrieval did not succeed (no risk estimator / no store /
retrieval raised) does the runner fall back to its own retrieval
(`_try_build_request_analysis_context`, `retrieval_phase="deliberation_retrieval"`)
and emit the event itself — controller-emit and runner-emit are mutually
exclusive per request. `COMPLIANCE_FAST_PATH` (§4) consumes no principles and
adds no retrieval.

## 4. DCCL (developer-contract) evaluation (`controller.py:1998-2102`)

- `speculative_draft_for_dccl = self._nonblocking_speculative_draft(spec_handle)`
  (only if the background draft already finished).
- `_run_dccl_evaluation(...)` → `call_ctx.compliance_verdict`
  (`controller.py:1004-1086`). Emits `COMPLIANCE_LAYER_STARTED` and a verdict event.
- The DCCL LLM prompt includes a budgeted role-ordered transcript from
  `ConversationContext`, not only the final user request. If budget trimming
  occurs, the prompt explicitly says not to claim prior turns are absent.
- **Hard-signal gate (P0 invariant #3, `controller.py` before the MATCH block).**
  Because `DCCL.evaluate` discards `risk_estimation`, a `MATCH` would otherwise be
  delivered on the fast-path before any deterministic hard-signal check runs. If
  `path_router.has_hard_signal_evidence(risk_estimation)` is true (a hard q-signal or
  a `clearly_harmful` category), the `MATCH` is invalidated (`cv = None`),
  `COMPLIANCE_MATCH_DOWNGRADED` is emitted with `reason="hard_signal_evidence"`, and the
  request falls through to the standard governed pipeline. A developer contract can
  never authorize a hard-signal request by short-circuiting the pipeline.
- If verdict is `MATCH` (and the hard-signal gate did not fire):
  - The delivery/governance mismatch guard records `governance_context_mode`,
    `candidate_context_mode`, `prior_turn_count`,
    `delivery_context_broader_than_governance`, and `mismatch_guard_action`.
    It only blocks draft reuse when prior turns exist, governance used a full
    role-serialized/native context, and the candidate draft was generated from
    last-user-only context.
  - **Case 1** (validated speculative draft, not low-confidence): emit
    `COMPLIANCE_DRAFT_REUSED` → `_route_compliance_match(..., draft_is_speculative=True)`.
  - **Case 2** (missing/invalid/low-confidence draft): `_regenerate_for_contract`
    then `_revalidate_draft`; if valid → `COMPLIANCE_DRAFT_REGENERATED` →
    `_route_compliance_match`; else `COMPLIANCE_MATCH_DOWNGRADED` and fall through
    to the standard pipeline.
- The compliance fast-path produces `NORMAL_COMPLETE` with path
  `COMPLIANCE_FAST_PATH`, skipping risk routing, critic, simulator, perspectives,
  hindsight; emits `MODULE_DEFERRED_TO_COMPLIANCE` per skipped module
  (`controller.py:1229-1321`).

## 5. Domain overlay & risk floor (`controller.py:2104-2178`)

- Extract risk score / category / op_risk; record on `trace`.
- Persist domain (`update_request_domain`) after `_normalize_runtime_domain`
  (drops `core`).
- Domain-exclusion check: if the active overlay excludes the detected domain →
  `_route_domain_excluded` (`controller.py:2136-2143`).
- `overlay_sensitive = is_overlay_sensitive(...)`; if sensitive, raise the score
  via `apply_risk_floor_if_sensitive` (per-overlay floor or
  `OVERLAY_SENSITIVE_RISK_FLOOR`). The floored score is propagated into a
  replaced `risk_proto` (`controller.py:2172-2178`).

## 6. Decision (`controller.py:2179-2203`)

- `_emit_risk_assessment_trace(...)` — **side effect**: `RISK_ASSESSMENT`
  decision trace.
- `decision, explanation = decide_action(request, risk_proto,
  overlay_sensitive=…, risk_thresholds=…)` (`orchestration/decision_service.py`).
- `decision = apply_safe_complete_gating(decision, request, risk_proto, …)`.
- The decision encodes `final_action` and `path` derived from
  `safe_complete_policy.compute_action_bounds` / `decide_final_action`.

## 7. Route resolution (`controller.py:2205-2206`)

- `route, borderline_refuse, risk_policy_action = get_route(decision, risk_proto,
  risk_score, config, op_risk)` — route ∈ `{refuse, benign, safe_complete,
  fast_path, deliberative}`.
- `hard_signal_refuse = is_hard_signal_refuse(decision, risk_proto, op_risk)`.

## 8. Ledger lookup (multi-turn only) (`controller.py:2824-3006`)

When a `SemanticDecisionLedger` is configured and `conversation_id` is set:
- Compute posture (`_compute_governance_posture`: ESCALATED if hard-signal REFUSE,
  ELEVATED if sensitive overlay, else NORMAL), contract hash, intent_clarity,
  request_type, turn index.
- `_lookup_cached_decision(...)` → `LedgerResult` recorded on `call_ctx`.
  `LedgerResult.query_embedding` carries the vector computed during lookup (None
  on early-skip paths where `embed()` is never called).
- On a hit, `ConversationalFastPathRunner.is_safe_to_apply(...)` gates reuse, and
  then `ledger_hard_signal_block_reason(...)` (`conversational_fast_path.py:170`, PROJECT_SPEC
  §5.3) applies the hard-signal guard: a cached non-REFUSE decision is never reused when
  `has_hard_signal_evidence(risk_proto)` (`"hard_signal_evidence"`) or `hard_signal_refuse`
  (`"hard_signal_refuse"`, defense in depth) holds; a cached REFUSE is never blocked. The
  gate alone is risk-blind and is not the §5.3 guarantee. If safe and not blocked:
  `apply_cached_decision(...)` patches `decision` and `route`, re-evaluates
  `hard_signal_refuse`, sets `ledger_hit_applied=True`, emits
  `LEDGER_FAST_PATH_APPLIED`. Otherwise it emits `LEDGER_FAST_PATH_NOT_APPLIED` with a
  `gate_reason` (`current_route_requires_deliberation`, `hard_signal_evidence` or
  `hard_signal_refuse`) and the request continues on its current route exactly as on a
  cache miss: deliberation on a deliberative route, `quick_check` on `fast_path`. There is no ledger-hit gate on the
  store: `_attach_trace_and_return` -> `_apply_conversation_metadata_to_result` calls
  `_maybe_store_in_ledger()` for every result, which skips transient fail-closed
  REFUSEs and pipeline failures (`hard_violation_flip_reason` in
  `LEDGER_NON_CACHEABLE_FLIP_REASONS`, including `system_error` / `system_timeout`).
- On a miss, the embedding computed during lookup is reused in `_maybe_store_in_ledger()`
  via `prompt_embedding=call_ctx.ledger_lookup.query_embedding`, avoiding a redundant
  `embed()` call. On `no_candidates` misses, `query_embedding` is None and `store()`
  calls `embed()` normally.

**Embedder provider** (as of v0.6.1): `GovernanceConfig.embedder_provider` selects
the implementation (`"local"` default → `LocalEmbedder` with fastembed or
`HashingEmbedder` fallback; `"openai"` → `OpenAIEmbedder`). `OPENAI_API_KEY` is not
required for the embedder when `embedder_provider="local"`.

## 9. Dispatch (`controller.py:2407-end`)

| route | handler | speculative draft |
|---|---|---|
| `refuse` | `_route_refuse` | abandoned |
| `benign` | `_route_benign` | joined (`join_for_consumer("benign")`) |
| `safe_complete` | `_route_safe_complete` | abandoned |
| `fast_path` | `_route_fast_path` | joined |
| `deliberative` | `_route_deliberative` | (consumed in the loop) |

`_route_deliberative` runs the `DeliberationRunner` cycles (critic → simulator ∥
perspectives → hindsight). Each cycle: `ConvergenceEvaluator.determine_decision`
(`convergence_evaluator.py:314-519`) turns the modules' weighted votes into a
`DecisionType`; `enforce_convergence_invariants` (`convergence.py:19-65`) then
decides whether to stop. The simulator never votes REFUSE — REFUSE arises only
from hard violations or a refuse-vote majority. Stop reasons: `CONVERGED`,
`HARD_VIOLATION_STOP`, `CYCLES_EXHAUSTED`. A cycle-1 early-convergence check
(`_evaluate_cycle1_early_convergence`) can stop after one cycle when critic is
clean, perspectives are strongly aligned, and simulated harm is low.

**Enumerated-output gate (Tier-1).** Before the critic's verdict feeds the
convergence vote, `critique()` checks whether the output is a single enumerated
answer (e.g. `answer exactly 'TRUE' or 'FALSE'`) via
`pipeline/output_contract.py:detect_enumerated_output`. When the draft is such a
token and the only violations are SOFT (`violated_hard == False`), a `REVISE`
is downgraded to `PROCEED` and the soft `violations`/`guidance` are cleared —
because the convergence evaluator votes `revise` on the *presence of
violations* (`convergence_evaluator.py:345-350`), not on `decision`. This stops
the revision loop from flipping a correct binary answer (observed on
`boolq_contrast`). HARD violations are never affected. Each activation emits a
best-effort `governance.enumerated_output_gate` diagnostic to
`debug.event.jsonl`, the `debug_events` table, and the UI "Debug Events" panel.

**Hard-violation delivery guard (§5.3, P0).** After the deliberation loop
finishes, `_route_deliberative` calls `decide_action` again (post-cycle),
applies the deliberation-override / cycles-exhausted-fallback checks, then
`apply_safe_complete_gating` — and only then, right before `assemble`, calls
`self._runner.enforce_no_rejected_draft_delivery(...)`. This is the delivery
point where a decision that still carries `hard_violations` (any of
`decision_service._handle_hard_violations`'s three downgrade branches —
crisis, regulated-informational derogation, or "pre-policy SAFE_COMPLETE must
not be overridden" — or, defensively, a NORMAL_COMPLETE it should no longer be
possible to reach given `apply_safe_complete_gating`'s own no-op above) is
stopped from delivering `state.draft_response` verbatim: that text is the
draft the critic already rejected on a hard violation, reached either because
the critic voted REFUSE (the REVISE/rewrite branch is skipped entirely) or
because it voted REVISE and cycles exhausted before a rewrite happened. The
guard regenerates under SAFE_COMPLETE governance and re-validates once with a
direct `critic.critique(...)` call (never the exception-swallowing,
timeout-guarded `_critique` wrapper used inside the cycle loop above);
delivers the regenerated text as SAFE_COMPLETE if the critic clears it, or
fails closed to REFUSE. The same guard runs at the fast-path→deliberative
escalation site (`DeliberationRunner._build_deliberative_result`, used when
`run_fast_path`'s `quick_check` fails). No-op (no LLM call) when
`decision.hard_violations` is empty or `decision.final_action == "REFUSE"` —
the ~99% no-hard-violation path is unaffected. In `llm_calls` the guard leaves
two rows in the cycle that raised the violation (`state.cycle`), sequenced after
every module of that cycle: `generate (hard_violation_regeneration)`
(`SEQ_HARD_VIOLATION_REGENERATION=7`) and `critique (hard_violation_revalidation)`
(`SEQ_HARD_VIOLATION_REVALIDATION=8`). The fast-path SAFE_COMPLETE row stays
cycle 0 / `SEQ_POLICY` / `generate (safe_complete_path)`, so the two can never be
confused in a trace: a `generate (safe_complete_path)` row means no deliberation
ran (rows persisted before 2026-09-18 are the exception — the guard's
regeneration was then written with the fast-path coordinates; the UI re-homes
them, see `docs/traces/observability_db_to_ui.md`).

**Fast-path HARD check fails closed (2026-10-13).** `DeliberationRunner.run_fast_path`
delivers the fast draft only if `critic.quick_check` ran and passed, or the check is
skipped by design (`critic is None`, or no constitution store configured). Four
triggers escalate to `run_deliberative_path` (`escalation_trigger` in the audit):
`quick_check_violation` (`passed=False`), `quick_check_error` (`quick_check` raised:
provider error, unparseable reply, or a reply with no `violated` key -- see
`docs/modules/critic.md`), `no_hard_principles` (`quick_check` raised
`NoHardPrinciplesError`: no HARD principle to check against, no LLM call; D10, a
dedicated trigger so operators can tell a misconfigured constitution from a provider
error), and `constitution_unavailable` (F3: a store is configured
but `get_constitution_safe` returned `None` after the retry). The escalation must end
with a **critique that completed in the final cycle**
(`_escalation_has_completed_critique`: `state._critique_outcome == "completed"`, cycle-scoped, a skipped
critique does not count); then the deliberative result is returned as
before. If it raises (any exception, `OrchestratorTimeoutError` included) the result is a
governed REFUSE with reason `fast_path_escalation_failed`; if it returns without a
completed critique (no critique, `CriticReport.empty_skipped`, a failed critique, or an
escalation that starts between 90% and 100% of `timeout_ms`, where the `_critique` guard
records outcome `timeout`) the reason is `fast_path_escalation_unverified`; an escalation that starts past
100% of `timeout_ms` is stopped earlier by the `while` guard in `run_deliberative_path`
(`deliberation_runner.py:2302-2305`), which raises, so the reason is
`fast_path_escalation_failed`. The REFUSE is built from structured signals only
(`_decision_as_refuse` keeps `hard_violations`), a rebuilt `DecisionExplanation`
(`winning_rule="fast_path_fail_closed"`), and the REFUSE branch of
`ResponseAssembler.assemble`, so `finalize_delivery` reports REFUSE/`content_filter`; the
refusal LLM receives a neutral rationale ("The safety check could not be completed.")
instead of the benign risk rationale. A FINAL trace row (sequence 2, appended after the
controller-side FINAL row; readers take the last FINAL row) carries
`hard_violation_source="fast_path_fail_closed"` and `stage_payload`
`{original_final_action, flip_reason, escalation_trigger}`;
`ResponseMetadata.original_final_action` / `hard_violation_flip_reason` carry the
same values, `internal_draft_reused` is `False` and an upstream-origin draft is not
claimed (`draft_origin="internal"`). `path_taken="fast"` / `path="FAST_PATH"` /
`total_cycles=0` are kept. Known gaps (see `docs/CODEBASE_FACTS.md`): the soft-revision rewrite is
not re-critiqued (D9), and the ledger caches and reapplies the fail-closed REFUSE (D6).

**Deliberative critique coverage fails closed (2026-10-14).** The general deliberative route
(`OrchestrationController._route_deliberative`) applies the same discipline to the critic.
`DeliberationRunner._critique` records what happened to the critique of the CURRENT cycle in
`DeliberationState._critique_outcome`: `""` (critic not invoked this cycle), `completed`, `skipped`
(report with `skipped is True`, i.e. zero retrieved principles), `failed` (the critic raised or
returned a non-conforming report) or `timeout` (the 90% gate of `timeout_ms` fired). The field is
reset at the start of every cycle and carried by `fork()` and the full-parallel critic merge.
`DeliberationRunner.enforce_verified_draft_delivery` is called once, after the deliberation override,
the CYCLES_EXHAUSTED fallback and the SAFE_COMPLETE gating, right before the hard-violation delivery
guard and `assemble`. With the critic gate open (a critic is configured and a store or a constitution
exists) and a non-REFUSE decision: `failed` -> governed REFUSE `deliberation_critique_failed`,
`timeout` -> `deliberation_critique_timeout`, `""` -> `deliberation_critique_unverified`; `completed` is
unchanged (no new row); `skipped` is accepted (D2) with a WARNING `deliberation critique skipped,
delivered under S2`. A closed gate (`critic is None`, or no store and no constitution) is the by-design
skip and is unchanged. The decision is flipped from the structured outcome only (never from response
text) with `_decision_as_refuse` (keeps `hard_violations`), a rebuilt `DecisionExplanation`
(`winning_rule="deliberation_fail_closed"`), the neutral refusal rationale, and a best-effort FINAL
trace row (sequence 2) with `hard_violation_source="deliberation_fail_closed"` and `stage_payload`
`{original_final_action, flip_reason, critique_outcome, cycle}` (`critique_outcome` is `not_invoked`
for `""`). `ResponseMetadata.original_final_action` / `hard_violation_flip_reason` carry the same
values; `finalize_delivery` reports REFUSE/`content_filter`. If the check itself raises, the controller
falls back to REFUSE `deliberation_critique_unverified` (explanation `None`) so the draft is still never
delivered. Critique telemetry is best-effort and isolated from the outcome. Note on the trace:
`CYCLE_SUMMARY` can mark the critic as "executed" (a critique object exists) in the same cycle whose FINAL
row reports `critique_outcome=failed`; these are two different notions of "executed", not a
contradiction. Known gaps (see `docs/CODEBASE_FACTS.md`): the soft-revision rewrite after the final
cycle is not re-critiqued (D9), and under S2 a degraded retriever that returns zero principles would
deliver every deliberative draft with only the WARNING.

## 10. Final action → governed delivery (Plan 1)

Back in the entry layer, delivery is **governed-only**: the delivered text is
*always* the text produced inside the MoralStack governed pipeline, and the
wrapped/upstream client is **never** called to generate the delivered answer for
any `final_action`. The governed answer is the validated speculative draft when
one was produced and reused (§3, §9), a compliance regeneration, a policy
generate/rewrite, or a governed refusal — it is never (re)generated by the
wrapped SDK client or the proxy upstream client.

The pure finalizer `finalize_delivery(result)`
(`orchestration/delivery.py`) turns the already-governed `OrchestratorResult`
into a transport-neutral `GovernedDelivery` (`text`, `final_text_source`,
`final_action`, `finish_reason`). It has exactly two **fail-closed**
substitutions of the deterministic safe refusal (`final_action` `REFUSE`,
`finish_reason="content_filter"`, `final_text_source="governed_pipeline_refusal"`):
(1) the governed content is blank/whitespace (checked first); (2) the result carries
no valid decision label (`final_action` outside `NORMAL_COMPLETE` / `SAFE_COMPLETE` /
`REFUSE`, F6 phase 5), which was previously delivered with `stop` (an empty label as
`NORMAL_COMPLETE`, any other label verbatim). A label differing only in ASCII case or
surrounding whitespace is normalized to the canonical form; a non-ASCII look-alike is
refused (`original_final_action` keeps the raw label). The label decides, the text is never
inspected. The rule is one shared helper, `normalize_final_action` (`orchestration/types.py`),
also used by the `ensure_final_action_and_return` early return, the conversation turn summary
and posture (an empty or unknown label is recorded as `REFUSE`; a lowercase `refuse` counts
for the ESCALATED check), and the ledger store check. The `ensure` backfill of an empty label
writes the canonical upper-case name (`NORMAL_COMPLETE` / `SAFE_COMPLETE` / `REFUSE`). The
ledger store is strict skip-only: only an exact canonical label is cached; anything else logs
one INFO line and is not stored (a stored non-canonical label would make a later hit raise).

- **NORMAL_COMPLETE / SAFE_COMPLETE**: the SDK returns the governed text via
  `GovernedResponse.from_governed_text(...)` (`wrapper.py:454-480`); the proxy
  returns it as a synthetic `chat.completion` (`proxy.py:_handle_chat_completion_sync`).
  No wrapped-client / upstream call is made. SAFE_COMPLETE governance guidance is
  composed *inside* the governed pipeline (policy prompt), not appended to a
  wrapped-client call; the developer system prompt is unchanged. The stale
  COMPLIANCE_FAST_PATH "mismatch guard" fields are retained as audit-only
  metadata and no longer route delivery to an upstream call.
- **REFUSE**: the SDK routes on the delivered action (`delivery.final_action`, so
  unlabelled / blank results are REFUSE), returns `delivery.text` without calling the
  wrapped client, and streams it through `GovernedRefusalStream(result, delivery=delivery)`
  with metadata reporting the delivered action (`wrapper.py:454-480`). Every SDK-delivered
  REFUSE carries `finish_reason="content_filter"` (non-stream `GovernedResponse.choices`
  derives it from `governance_metadata.final_action == "REFUSE"`; `GovernedRefusalStream` and
  `GovernedErrorStream` chunks use it too), aligned with the proxy, which returns a synthetic
  `chat.completion` with `finish_reason="content_filter"` and **no upstream call**. NORMAL /
  SAFE keep `stop`.
- **Pipeline failure inside `process()`** (a `MoralStackError` or
  `OrchestratorTimeoutError` caught by the controller): `_handle_error` /
  `_handle_timeout` (via `_pipeline_failure_result`) return a governed REFUSE with
  principle `SYSTEM.ERROR` / `SYSTEM.TIMEOUT`, `hard_violation_flip_reason` and
  `reason_codes` = `system_error` / `system_timeout`, `path="ERROR_PATH"` /
  `path_taken="error"`, and fixed sentinel content `[SYSTEM_ERROR]` /
  `[SYSTEM_TIMEOUT]` (never the exception text, in any mode). No LLM call and no
  FINAL decision-trace row (the UI detects the failure by a `SYSTEM.ERROR` /
  `SYSTEM.TIMEOUT` / `SYSTEM.FAIL_SAFE` principle plus no FINAL row, through
  `reports/pipeline_failure.py::pipeline_failure_principle`; the request page names
  the recorded principle); one WARNING `pipeline failed closed ...`. The codes are in
  `LEDGER_NON_CACHEABLE_FLIP_REASONS`, so the ledger never stores them.
  `finalize_delivery` then delivers REFUSE / `content_filter`. The handlers and the generic
  `except` also emit one best-effort `PIPELINE_FAILED` orchestration event
  (`_emit_pipeline_failed_event`: `fail_closed_reason`, `triggered_principle`, `error_type`,
  `path`; never the exception text); the `ensure` fallback does not emit. The generic
  `except` also writes a `debug_events` row (`H-except`) whose payload carries `error_type`
  only, not the exception text (the text stays in the WARNING). A ledger store failure
  (`_maybe_store_in_ledger`, non-fatal) likewise logs a guarded WARNING with the text in
  `exc_info` and writes `H-ledger-store-fail` with `error_type` only.
- **Streaming (`stream=True`)**: the governed text is replayed as OpenAI-compatible
  synthetic SSE (`GovernedSyntheticStream` in the SDK, `_build_synthetic_sse_response`
  in the proxy); live upstream tokens are never forwarded.
- **Pipeline failure** fails closed to a governed refusal — there is no
  passthrough to the wrapped/upstream client (`wrapper.py:_handle_pipeline_failure`).
  On the SDK exception-escape path `GovernedResponse.from_pipeline_error` sets the fixed
  `decision_reason="Pipeline error"` (the exception is never rendered into public
  metadata); the exception text and traceback go only to one ERROR record on the
  `moralstack.sdk.wrapper` logger (`exc_info`; message line = `error_type` only).

Internal MoralStack LLM calls are still the allowed generation sources: risk
mini-estimators, the speculative draft (which may already be running or complete,
see §3), policy generate/rewrite, and `RefusalHandler.handle` →
`generate_llm_safe_refusal_detailed` for refusal wording
(`orchestration/refusal_handler.py:94-104`). The proxy records the invariant on
the `PROXY_OUTPUT_FINALIZED` envelope (`governed_delivery=True`,
`wrapped_client_delivery_call=False`).

## 11. Response metadata

`GovernanceMetadata` is attached to the response (`sdk/response.py`,
`GovernedResponse.from_*`). Fields: `final_action`, `risk_score`,
`risk_category`, `path`, `reason_codes`, `triggered_principles`,
`decision_reason`, `conversation_id`, `turn_index`. The proxy attaches these
`X-Moralstack-*` headers via `build_governance_headers` (`server/headers.py:49-64`).
`Decision` is the delivered action: the proxy passes `final_action=delivery.final_action`,
after `finalize_delivery`. The headers are:
always `Decision`, `Risk-Score`, `Posture`, `Path`, `Conversation-Id`,
`Internal-Draft-Reused`; conditionally `Cached-From`, `Compliance-Decision`,
`Compliance-Rule`.

## 12. Logging side effects (best-effort, never raise)

Emitted across the flow (DB rows + JSONL envelopes per observability mode):
- `requests` row pre-insert (step 2) and finalize (step 12) with
  `final_response`, `domain`, merged `meta_json`.
- `RISK_ASSESSMENT`, `COMPLIANCE_LAYER`, `DELIBERATION_AGGREGATE`, `FINAL`
  decision traces. The `FINAL` trace's `sim_*` fields are populated by
  `_populate_trace_from_sim` / copied in `_log_final_trace`
  (`orchestration/decision_service.py`); `sim_metrics_measured` (tri-state
  `bool | None`) records whether those metrics are a retained measurement vs
  defaults — observability only, it gates nothing (see
  `docs/traces/observability_db_to_ui.md` and `docs/CODEBASE_FACTS.md`).
- `orchestration_events`: `SPECULATIVE_STARTED`, `COMPLIANCE_LAYER_*`,
  `MODULE_DEFERRED_TO_COMPLIANCE`, `LEDGER_FAST_PATH_*`,
  `CONVERSATION_CONTEXT_ATTACHED`, `CONVERSATION_STATE_UPDATED`,
  `CONTEXT_SHAPE_RECORDED`, `PROXY_OUTPUT_FINALIZED` (proxy),
  `DOMAIN_PREFILTER_DOMAINS_REJECTED` (retrieval; write-only audit of domains the
  prefilter LLM proposed but routing did not apply, with reasons `unknown_domain` /
  `low_confidence` / `over_cap` / `parse_failed`; never read for routing).
- `conversation_states`, `ledger_events`, `session_store_events`,
  `proxy_request_events` for multi-turn.
- SDK flushes observability synchronously after each call (`wrapper.py:300-308`);
  the proxy flushes in `_finalize_request` (`proxy.py:707-708`).
- `_apply_conversation_metadata_to_result` (controller.py:322-416) builds
  `conversation_governance_state_out` and stores the decision in the ledger via
  `_maybe_store_in_ledger` (reuses `call_ctx.ledger_lookup.query_embedding` when
  available to skip the second `embed()` call).
