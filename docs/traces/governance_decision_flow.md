# TRACE — Governance decision flow (end to end)

Path of a single request from input to response, with side effects.
Claims are grounded in the cited source. Path-specific caveats and
unverified branches are noted inline.

Primary code: `moralstack/orchestration/controller.py` (`process`, line 1885),
`moralstack/sdk/wrapper.py`, `moralstack/runtime/decision/safe_complete_policy.py`.

---

## 0. Entry

- **SDK**: `client.chat.completions.create(**kwargs)` →
  `GovernedCompletions._create_inner` (`wrapper.py:285`).
- **Proxy**: `POST /v1/chat/completions` → `_handle_chat_completion_sync`
  (`server/proxy.py:197`), run inside a threadpool.

Both build a `ProcessedRequest` and call `orchestrator.process(...)`.

## 1. Input request & message parsing

`wrapper.py:285-303`:
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
(`wrapper.py:305-314`). A snapshot `state_in` is captured *before*
`session.update_from_result` overwrites it.

Proxy equivalent: `conversation_id` resolution + stateless `turn_index`
(`proxy.py:218-256`), `conv_state = store.get(conversation_id)`.

## 2. Controller setup (`controller.py:1900-1925`)

- Coerce `str` → `ProcessedRequest`; build `ProcessCallContext`.
- Set context vars: `set_current_session_id`, `set_current_turn_number`.
- `persistence.set_request_context(request_id)` and
  `ensure_run_and_upsert_request(...)` — **side effect**: pre-inserts the
  `requests` row so later FK-bound events succeed.
- `trace = self._trace_lifecycle.start_trace(request_id)`.

## 3. Risk estimation (`controller.py:1928-1935`)

- If `enable_speculative_generation` and a policy is set: `_run_speculative_overlap`
  runs **risk estimation and a speculative draft in parallel** (two-worker
  `ThreadPoolExecutor`, contextvars copied). The method blocks only for risk;
  the draft continues in the background (`controller.py:906-964`). The
  speculative draft calls the internal **policy LLM** (`self.policy.generate`),
  not the wrapped/upstream client. This means an internal LLM call may already
  be in-flight before any routing decision — including on paths that will
  ultimately REFUSE.
- Else: `risk_estimation = self._estimate_risk(request)` (`controller.py:788`).

`_estimate_risk` forwards the developer-contract text and conversation history to
the estimator (`controller.py:797-823`) and emits `CONTEXT_SHAPE_RECORDED`. The
risk estimator declares its reduced history mode as `role_serialized_truncated`
when only the last-3 window is used. The estimator runs three parallel
mini-estimators (intent / signals q1–q17 / operational) and calibrates them into
a `RiskEstimation` (`models/risk/estimator.py:541-735`).

## 4. DCCL (developer-contract) evaluation (`controller.py:1936-2040`)

- `speculative_draft_for_dccl = self._nonblocking_speculative_draft(spec_handle)`
  (only if the background draft already finished).
- `_run_dccl_evaluation(...)` → `call_ctx.compliance_verdict`
  (`controller.py:980-1062`). Emits `COMPLIANCE_LAYER_STARTED` and a verdict event.
- The DCCL LLM prompt includes a budgeted role-ordered transcript from
  `ConversationContext`, not only the final user request. If budget trimming
  occurs, the prompt explicitly says not to claim prior turns are absent.
- If verdict is `MATCH`:
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
  (`controller.py:1205-1297`).

## 5. Domain overlay & risk floor (`controller.py:2042-2116`)

- Extract risk score / category / op_risk; record on `trace`.
- Persist domain (`update_request_domain`) after `_normalize_runtime_domain`
  (drops `core`).
- Domain-exclusion check: if the active overlay excludes the detected domain →
  `_route_domain_excluded` (`controller.py:2074-2081`).
- `overlay_sensitive = is_overlay_sensitive(...)`; if sensitive, raise the score
  via `apply_risk_floor_if_sensitive` (per-overlay floor or
  `OVERLAY_SENSITIVE_RISK_FLOOR`). The floored score is propagated into a
  replaced `risk_proto` (`controller.py:2110-2116`).

## 6. Decision (`controller.py:2117-2141`)

- `_emit_risk_assessment_trace(...)` — **side effect**: `RISK_ASSESSMENT`
  decision trace.
- `decision, explanation = decide_action(request, risk_proto,
  overlay_sensitive=…, risk_thresholds=…)` (`orchestration/decision_service.py`).
- `decision = apply_safe_complete_gating(decision, request, risk_proto, …)`.
- The decision encodes `final_action` and `path` derived from
  `safe_complete_policy.compute_action_bounds` / `decide_final_action`.

## 7. Route resolution (`controller.py:2143-2144`)

- `route, borderline_refuse, risk_policy_action = get_route(decision, risk_proto,
  risk_score, config, op_risk)` — route ∈ `{refuse, benign, safe_complete,
  fast_path, deliberative}`.
- `hard_signal_refuse = is_hard_signal_refuse(decision, risk_proto, op_risk)`.

## 8. Ledger lookup (multi-turn only) (`controller.py:2149-2306`)

When a `SemanticDecisionLedger` is configured and `conversation_id` is set:
- Compute posture (`_compute_governance_posture`: ESCALATED if hard-signal REFUSE,
  ELEVATED if sensitive overlay, else NORMAL), contract hash, intent_clarity,
  request_type, turn index.
- `_lookup_cached_decision(...)` → `LedgerResult` recorded on `call_ctx`.
  `LedgerResult.query_embedding` carries the vector computed during lookup (None
  on early-skip paths where `embed()` is never called).
- On a hit, `ConversationalFastPathRunner.is_safe_to_apply(...)` gates reuse. If
  safe: `apply_cached_decision(...)` patches `decision` and `route`, re-evaluates
  `hard_signal_refuse`, sets `ledger_hit_applied=True`, emits
  `LEDGER_FAST_PATH_APPLIED`. If not safe: emits `LEDGER_FAST_PATH_NOT_APPLIED`
  with a gate reason and deliberation proceeds. On a hit the controller does NOT
  call `_maybe_store_in_ledger()` (deliberation is bypassed).
- On a miss, the embedding computed during lookup is reused in `_maybe_store_in_ledger()`
  via `prompt_embedding=call_ctx.ledger_lookup.query_embedding`, avoiding a redundant
  `embed()` call. On `no_candidates` misses, `query_embedding` is None and `store()`
  calls `embed()` normally.

**Embedder provider** (as of v0.6.1): `GovernanceConfig.embedder_provider` selects
the implementation (`"local"` default → `LocalEmbedder` with fastembed or
`HashingEmbedder` fallback; `"openai"` → `OpenAIEmbedder`). `OPENAI_API_KEY` is not
required for the embedder when `embedder_provider="local"`.

## 9. Dispatch (`controller.py:2345-end`)

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
`final_action`, `finish_reason`). Its only transformation is the **fail-closed**
substitution of a deterministic safe refusal when the governed content is
blank/whitespace (`final_action` downgraded to `REFUSE`,
`finish_reason="content_filter"`).

- **NORMAL_COMPLETE / SAFE_COMPLETE**: the SDK returns the governed text via
  `GovernedResponse.from_governed_text(...)` (`wrapper.py:372-415`); the proxy
  returns it as a synthetic `chat.completion` (`proxy.py:_handle_chat_completion_sync`).
  No wrapped-client / upstream call is made. SAFE_COMPLETE governance guidance is
  composed *inside* the governed pipeline (policy prompt), not appended to a
  wrapped-client call; the developer system prompt is unchanged. The stale
  COMPLIANCE_FAST_PATH "mismatch guard" fields are retained as audit-only
  metadata and no longer route delivery to an upstream call.
- **REFUSE**: SDK returns refusal text without calling the wrapped client
  (`wrapper.py:394-403`); proxy returns a synthetic `chat.completion` with
  `finish_reason="content_filter"` and **no upstream call**.
- **Streaming (`stream=True`)**: the governed text is replayed as OpenAI-compatible
  synthetic SSE (`GovernedSyntheticStream` in the SDK, `_build_synthetic_sse_response`
  in the proxy); live upstream tokens are never forwarded.
- **Pipeline failure** fails closed to a governed refusal — there is no
  passthrough to the wrapped/upstream client (`wrapper.py:_handle_pipeline_failure`).

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
`X-Moralstack-*` headers via `build_governance_headers` (`server/headers.py:40-54`):
always `Decision`, `Risk-Score`, `Posture`, `Path`, `Conversation-Id`,
`Internal-Draft-Reused`; conditionally `Cached-From`, `Compliance-Decision`,
`Compliance-Rule`.

## 12. Logging side effects (best-effort, never raise)

Emitted across the flow (DB rows + JSONL envelopes per observability mode):
- `requests` row pre-insert (step 2) and finalize (step 12) with
  `final_response`, `domain`, merged `meta_json`.
- `RISK_ASSESSMENT`, `COMPLIANCE_LAYER`, `DELIBERATION_AGGREGATE`, `FINAL`
  decision traces.
- `orchestration_events`: `SPECULATIVE_STARTED`, `COMPLIANCE_LAYER_*`,
  `MODULE_DEFERRED_TO_COMPLIANCE`, `LEDGER_FAST_PATH_*`,
  `CONVERSATION_CONTEXT_ATTACHED`, `CONVERSATION_STATE_UPDATED`,
  `CONTEXT_SHAPE_RECORDED`, `PROXY_OUTPUT_FINALIZED` (proxy).
- `conversation_states`, `ledger_events`, `session_store_events`,
  `proxy_request_events` for multi-turn.
- SDK flushes observability synchronously after each call (`wrapper.py:275-283`);
  the proxy flushes in `_finalize_request` (`proxy.py:702-703`).
- `_apply_conversation_metadata_to_result` (controller.py:319-413) builds
  `conversation_governance_state_out` and stores the decision in the ledger via
  `_maybe_store_in_ledger` (reuses `call_ctx.ledger_lookup.query_embedding` when
  available to skip the second `embed()` call).
