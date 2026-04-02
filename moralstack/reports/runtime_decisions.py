"""
View-model builders for runtime / orchestration observability (display only).

Maps decision_traces, orchestration_events, and llm_calls into stable structures for UI and exports.
"""

from __future__ import annotations

import json
from typing import Any

from moralstack.orchestration.orchestration_event_taxonomy import (
    AGGREGATED_GUIDANCE_EVALUATED,
    CRITIC_SHORT_CIRCUIT_TRIGGERED,
    PARALLEL_STRATEGY_SELECTED,
    RELEVANT_PRINCIPLES_RETRIEVED,
    RELEVANT_PRINCIPLES_REUSED,
    SIMULATOR_EXECUTED,
    SIMULATOR_GATE_DECISION,
    SIMULATOR_SKIPPED,
    SPECULATIVE_JOIN_REQUIRED,
    SPECULATIVE_JOIN_SKIPPED,
    SPECULATIVE_RESULT_DISCARDED,
    SPECULATIVE_RESULT_USED,
    SPECULATIVE_STARTED,
)


def _parse_json_field(raw: Any) -> Any:
    if raw is None:
        return None
    if isinstance(raw, (dict, list)):
        return raw
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except Exception:
            return None
    return None


def _trace_payload(row: dict[str, Any]) -> dict[str, Any]:
    tj = row.get("trace_json")
    if isinstance(tj, str):
        try:
            return json.loads(tj)
        except Exception:
            return {}
    return tj if isinstance(tj, dict) else {}


def _risk_assessment_from_traces(traces: list[dict[str, Any]]) -> dict[str, Any]:
    for t in traces:
        if (t.get("stage") or "").strip().upper() == "RISK_ASSESSMENT":
            td = _trace_payload(t)
            sp = td.get("stage_payload") if isinstance(td.get("stage_payload"), dict) else {}
            return {
                "risk_score": td.get("risk_score"),
                "risk_category": td.get("risk_category"),
                "operational_risk": td.get("operational_risk"),
                "intent_to_harm": td.get("intent_to_harm"),
                "requested_instructions": td.get("requested_instructions"),
                "intent_operational": td.get("intent_operational"),
                "estimation_mode": td.get("estimation_mode"),
                "risk_policy_action": sp.get("risk_policy_action"),
                "detected_domain": sp.get("detected_domain"),
                "activated_signals": sp.get("activated_signals") or [],
            }
    return {}


def _request_context_from_traces(traces: list[dict[str, Any]]) -> dict[str, Any]:
    """Prefer the last REQUEST_ANALYSIS_CONTEXT trace (finalize pass includes reuse_targets)."""
    last_sp: dict[str, Any] | None = None
    for t in traces:
        if (t.get("stage") or "").strip().upper() == "REQUEST_ANALYSIS_CONTEXT":
            td = _trace_payload(t)
            sp = td.get("stage_payload") if isinstance(td.get("stage_payload"), dict) else {}
            if sp:
                last_sp = dict(sp)
    if last_sp:
        reuse_targets = last_sp.get("reuse_targets") or []
        reuse_count = last_sp.get("reuse_count")
        if reuse_count is None and isinstance(reuse_targets, list):
            reuse_count = len(reuse_targets)
        return {
            "relevant_principles_count": last_sp.get("retrieval_count"),
            "constitution_domain": last_sp.get("constitution_domain"),
            "reuse_targets": reuse_targets,
            "reuse_count": reuse_count,
            "prefilter_cache_status": last_sp.get("prefilter_cache_status"),
            "prefilter_cache_reason": last_sp.get("prefilter_cache_reason"),
            "prefilter_keywords_changed": last_sp.get("prefilter_keywords_changed"),
            "prefilter_keywords_fingerprint_prefix": last_sp.get("prefilter_keywords_fingerprint_prefix"),
            "parallel_retrieval": last_sp.get("parallel_retrieval"),
            "request_scoped": last_sp.get("request_scoped"),
            "retrieval_duration_ms": last_sp.get("retrieval_duration_ms"),
            "retrieval_top_k": last_sp.get("retrieval_top_k"),
        }
    return {}


def _simulator_gating_from_cycles(cycles: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Per-cycle simulator gate summary from CYCLE_SUMMARY (legacy traces may omit fields)."""
    rows: list[dict[str, Any]] = []
    for sp in cycles:
        ran = sp.get("simulator_ran_this_cycle")
        status = "unknown"
        if ran is True:
            status = "executed"
        elif ran is False:
            status = "skipped"
        rows.append(
            {
                "cycle": sp.get("cycle"),
                "gate_enabled": sp.get("simulator_gate_enabled"),
                "ran_this_cycle": ran,
                "status": status,
                "reason_codes": sp.get("simulator_gate_reason_codes") or [],
                "carry_forward": sp.get("simulator_carry_forward"),
            }
        )
    return rows


def _parallel_scheduler_rollups(cycles: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Per-cycle scheduler strategy from CYCLE_SUMMARY stage_payload (legacy requests may omit fields)."""
    rows: list[dict[str, Any]] = []
    for sp in cycles:
        rows.append(
            {
                "cycle": sp.get("cycle"),
                "strategy": sp.get("scheduler_strategy"),
                "reason_codes": sp.get("scheduler_reason_codes") or [],
                "critic_short_circuit": sp.get("critic_short_circuit"),
            }
        )
    return rows


def _convergence_from_cycle_summaries(cycles: list[dict[str, Any]]) -> dict[str, Any]:
    """Roll up cycle-1 early convergence fields from CYCLE_SUMMARY stage_payload."""
    c1: dict[str, Any] = {}
    for c in cycles:
        if int(c.get("cycle") or 0) == 1:
            c1 = dict(c)
            break
    last = cycles[-1] if cycles else {}
    return {
        "cycle1_early_convergence_considered": c1.get("early_convergence_considered"),
        "cycle1_early_convergence_accepted": c1.get("early_convergence_accepted"),
        "cycle1_convergence_reason_codes": c1.get("convergence_reason_codes") or [],
        "cycle1_deliberation_decision": c1.get("deliberation_decision"),
        "cycle1_perspectives_weighted_approval": c1.get("perspectives_weighted_approval"),
        "cycle1_semantic_expected_harm": c1.get("semantic_expected_harm"),
        "last_deliberation_decision": last.get("deliberation_decision"),
        "last_convergence_stop_reason": last.get("convergence_decision"),
    }


def _cycle_summaries_from_traces(traces: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for t in traces:
        if (t.get("stage") or "").strip().upper() != "CYCLE_SUMMARY":
            continue
        td = _trace_payload(t)
        sp = td.get("stage_payload") if isinstance(td.get("stage_payload"), dict) else {}
        if sp:
            out.append(dict(sp))
    out.sort(key=lambda x: int(x.get("cycle") or 0))
    return out


def _final_trace_dict(traces: list[dict[str, Any]]) -> dict[str, Any]:
    finals = [x for x in traces if (x.get("stage") or "").strip().upper() == "FINAL"]
    row = finals[-1] if finals else (traces[-1] if traces else {})
    return _trace_payload(row)


def _guidance_filter_card_fields(payload: dict[str, Any]) -> dict[str, Any]:
    """Readable fields for cycle cards from AGGREGATED_GUIDANCE_EVALUATED payload."""
    empty = payload.get("guidance_empty")
    parts: list[str] = []
    if "weighted_approval" in payload and isinstance(payload["weighted_approval"], (int, float)):
        parts.append(f"weighted_approval={float(payload['weighted_approval']):.3f}")
    if "semantic_expected_harm" in payload and isinstance(payload["semantic_expected_harm"], (int, float)):
        parts.append(f"semantic_expected_harm={float(payload['semantic_expected_harm']):.3f}")
    if payload.get("apply_signal_filter") is True:
        parts.append("signal_filter=on")
    detail = "; ".join(parts) if parts else ""
    summary = str(payload.get("short_summary") or "")
    if detail:
        summary = f"{summary} ({detail})" if summary else detail
    return {
        "guidance_filter_summary": summary or "—",
        "rewrite_skipped_for_empty_guidance": bool(empty is True),
    }


def _orchestration_event_payload(ev: dict[str, Any]) -> dict[str, Any]:
    raw = ev.get("payload_json")
    if raw is None:
        return {}
    if isinstance(raw, memoryview):
        raw = raw.tobytes().decode("utf-8", errors="replace")
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8", errors="replace")
    if isinstance(raw, str):
        return _parse_json_field(raw) or {}
    if isinstance(raw, dict):
        return raw
    return {}


def _speculative_summary_from_events_and_calls(
    evs: list[dict[str, Any]],
    llm_calls: list[dict[str, Any]] | None,
) -> dict[str, Any]:
    """Derive execution-strategy speculative fields from orchestration_events (+ optional llm_calls hints)."""
    started = False
    join_required = False
    join_skipped = False
    result_used = False
    last_discard_reason = ""
    join_wait_ms: float | None = None
    skip_elapsed_ms: float | None = None

    for e in evs:
        et = (e.get("event_type") or "").strip().upper()
        payload = _orchestration_event_payload(e)
        if et == SPECULATIVE_STARTED:
            started = True
        elif et == SPECULATIVE_JOIN_REQUIRED:
            join_required = True
        elif et == SPECULATIVE_JOIN_SKIPPED:
            join_skipped = True
            se = payload.get("elapsed_since_spec_start_ms")
            if isinstance(se, (int, float)):
                skip_elapsed_ms = float(se)
        elif et == SPECULATIVE_RESULT_USED:
            result_used = True
            jw = payload.get("join_wait_ms")
            if isinstance(jw, (int, float)):
                join_wait_ms = float(jw)
        elif et == SPECULATIVE_RESULT_DISCARDED:
            last_discard_reason = str(payload.get("reason") or "")

    outcome: str
    if result_used:
        outcome = "used"
    elif not started:
        outcome = "none"
    elif last_discard_reason == "speculative_failed":
        outcome = "unavailable"
    elif last_discard_reason == "speculative_empty_or_failed":
        outcome = "unavailable"
    else:
        outcome = "discarded"

    for c in llm_calls or []:
        if (c.get("call_kind") or "").strip().lower() != "speculative":
            continue
        started = True
        co = (c.get("call_outcome") or "").strip().lower()
        if co == "used":
            outcome = "used"
        elif co == "discarded" and outcome not in ("used", "unavailable"):
            outcome = "discarded"

    return {
        "speculative_started": started,
        "speculative_outcome": outcome,
        "join_required": join_required,
        "join_skipped": join_skipped,
        "join_wait_ms": join_wait_ms,
        "skip_elapsed_ms": skip_elapsed_ms,
        "last_discard_reason": last_discard_reason,
    }


def build_execution_strategy(
    traces: list[dict[str, Any]],
    llm_calls: list[dict[str, Any]] | None = None,
    orchestration_events: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """
    High-level execution strategy section: risk posture, retrieval context, cycle rollup placeholders.
    """
    risk = _risk_assessment_from_traces(traces)
    ctx = _request_context_from_traces(traces)
    cycles = _cycle_summaries_from_traces(traces)
    final_td = _final_trace_dict(traces)
    estimation_mode = risk.get("estimation_mode") or ""
    if not estimation_mode and llm_calls:
        for c in llm_calls:
            if (c.get("module") or "").strip().lower() != "risk_estimator":
                continue
            ps = c.get("parsed_summary_json")
            parsed = _parse_json_field(ps)
            if isinstance(parsed, dict) and parsed.get("estimation_mode"):
                estimation_mode = str(parsed.get("estimation_mode") or "")
                break
    speculative = _speculative_summary_from_events_and_calls(orchestration_events or [], llm_calls)
    orch = orchestration_events or []
    rp_retrieved = sum(
        1 for e in orch if (e.get("event_type") or "").strip().upper() == RELEVANT_PRINCIPLES_RETRIEVED
    )
    rp_reused = sum(1 for e in orch if (e.get("event_type") or "").strip().upper() == RELEVANT_PRINCIPLES_REUSED)
    strat_events = [
        e
        for e in orch
        if (e.get("event_type") or "").strip().upper()
        in (PARALLEL_STRATEGY_SELECTED, CRITIC_SHORT_CIRCUIT_TRIGGERED)
    ]
    sim_events = [
        e
        for e in orch
        if (e.get("event_type") or "").strip().upper()
        in (SIMULATOR_GATE_DECISION, SIMULATOR_EXECUTED, SIMULATOR_SKIPPED)
    ]
    return {
        "risk_assessment": risk,
        "request_analysis_context": ctx,
        "cycle_summaries": cycles,
        "convergence": _convergence_from_cycle_summaries(cycles),
        "parallel_scheduler_by_cycle": _parallel_scheduler_rollups(cycles),
        "simulator_gating_by_cycle": _simulator_gating_from_cycles(cycles),
        "total_cycles_observed": len(cycles),
        "final_action": final_td.get("final_action"),
        "stop_reason": final_td.get("stop_reason"),
        "estimation_mode": estimation_mode,
        "scheduler_events_count": len(strat_events),
        "simulator_gating_events_count": len(sim_events),
        "speculative": speculative,
        "relevant_principles_events": {
            "retrieved_count": rp_retrieved,
            "reused_count": rp_reused,
        },
    }


def orchestration_event_to_row(ev: dict[str, Any], sequence: int) -> dict[str, Any]:
    """Map a raw orchestration_events DB row to a UI table record."""
    reason = _parse_json_field(ev.get("reason_codes_json"))
    reason_s = ""
    if isinstance(reason, list):
        reason_s = ", ".join(str(x) for x in reason)
    elif reason is not None:
        reason_s = str(reason)
    badges: list[str] = []
    et = (ev.get("event_type") or "").strip().upper()
    if et:
        badges.append(et[:48])
    return {
        "id": ev.get("id"),
        "cycle": ev.get("cycle"),
        "stage": ev.get("stage") or "",
        "component": ev.get("component") or "",
        "event": ev.get("event_type") or "",
        "decision": ev.get("decision") or "",
        "reason": reason_s,
        "duration_ms": ev.get("duration_ms"),
        "status": ev.get("status") or "",
        "badges": badges,
        "_sequence": sequence,
    }


def build_runtime_decisions_table(orchestration_events: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    """Ordered rows for Runtime Decisions table."""
    evs = orchestration_events or []
    rows = [orchestration_event_to_row(ev, i) for i, ev in enumerate(evs)]
    return rows


def build_cycle_cards(
    traces: list[dict[str, Any]],
    orchestration_events: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """
    One card per deliberation cycle from CYCLE_SUMMARY traces.
    Sparse fields are allowed when data is not yet produced.
    """
    evs = orchestration_events or []
    reuse_by_cycle: dict[int, bool] = {}
    for e in evs:
        if (e.get("event_type") or "").strip().upper() != RELEVANT_PRINCIPLES_REUSED:
            continue
        payload = _orchestration_event_payload(e)
        ru = str(payload.get("reuse_target") or "").strip().lower()
        if ru != "critic":
            continue
        cyc = payload.get("cycle")
        if isinstance(cyc, int):
            reuse_by_cycle[cyc] = True
        elif isinstance(cyc, float):
            reuse_by_cycle[int(cyc)] = True
    guidance_filter_by_cycle: dict[int, dict[str, Any]] = {}
    for e in evs:
        if (e.get("event_type") or "").strip().upper() != AGGREGATED_GUIDANCE_EVALUATED:
            continue
        payload = _orchestration_event_payload(e)
        cyc = e.get("cycle")
        cnum: int | None = None
        if isinstance(cyc, int):
            cnum = cyc
        elif isinstance(cyc, float):
            cnum = int(cyc)
        if cnum is None:
            continue
        guidance_filter_by_cycle[cnum] = _guidance_filter_card_fields(payload)
    summaries = _cycle_summaries_from_traces(traces)
    cards: list[dict[str, Any]] = []
    for sp in summaries:
        cnum = int(sp.get("cycle") or 0)
        principles_note = ""
        if reuse_by_cycle.get(cnum):
            principles_note = "request-scoped reuse"
        gf = guidance_filter_by_cycle.get(cnum) or {}
        cards.append(
            {
                "cycle_label": f"Cycle {cnum}",
                "cycle": cnum,
                "strategy": sp.get("scheduler_strategy"),
                "scheduler_reason_codes": sp.get("scheduler_reason_codes") or [],
                "critic_short_circuit": sp.get("critic_short_circuit"),
                "modules_planned": sp.get("modules_planned") or [],
                "modules_executed": sp.get("modules_executed") or [],
                "modules_skipped": sp.get("modules_skipped") or [],
                "modules_cancelled": sp.get("modules_cancelled") or [],
                "critic_result": sp.get("critic_decision"),
                "simulator_result": sp.get("semantic_expected_harm"),
                "perspectives_result": sp.get("perspectives_weighted_approval"),
                "convergence_result": sp.get("convergence_decision"),
                "convergence_reason": sp.get("convergence_reason"),
                "deliberation_decision": sp.get("deliberation_decision"),
                "early_convergence_considered": sp.get("early_convergence_considered"),
                "early_convergence_accepted": sp.get("early_convergence_accepted"),
                "convergence_reason_codes": sp.get("convergence_reason_codes") or [],
                "principles_source_note": principles_note,
                "simulator_gate_enabled": sp.get("simulator_gate_enabled"),
                "simulator_ran_this_cycle": sp.get("simulator_ran_this_cycle"),
                "simulator_gate_reason_codes": sp.get("simulator_gate_reason_codes") or [],
                "simulator_carry_forward": sp.get("simulator_carry_forward"),
                "guidance_filter_summary": gf.get("guidance_filter_summary"),
                "rewrite_skipped_for_empty_guidance": gf.get("rewrite_skipped_for_empty_guidance"),
            }
        )
    return cards


def build_retrieval_reuse_summary(
    traces: list[dict[str, Any]],
    orchestration_events: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Compact retrieval / reuse summary from traces and orchestration events."""
    ctx = _request_context_from_traces(traces)
    evs = orchestration_events or []
    reuse_hits = sum(
        1
        for e in evs
        if (e.get("event_type") or "").strip().upper()
        in ("RELEVANT_PRINCIPLES_REUSED", "DOMAIN_PREFILTER_CACHE_HIT")
    )
    prefilter_invalidations = sum(
        1
        for e in evs
        if (e.get("event_type") or "").strip().upper() == "DOMAIN_PREFILTER_CACHE_INVALIDATED"
    )
    return {
        "context": ctx,
        "orchestration_reuse_events": reuse_hits,
        "prefilter_cache_invalidations": prefilter_invalidations,
        "prefilter_cache_status": ctx.get("prefilter_cache_status"),
        "prefilter_cache_reason": ctx.get("prefilter_cache_reason"),
        "prefilter_keywords_changed": ctx.get("prefilter_keywords_changed"),
    }


def build_runtime_decision_observability(
    *,
    traces: list[dict[str, Any]],
    orchestration_events: list[dict[str, Any]] | None,
    llm_calls: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """
    Aggregate view model for request detail: execution strategy, runtime decisions, cycle cards, retrieval.
    """
    orch = orchestration_events or []
    return {
        "execution_strategy": build_execution_strategy(traces, llm_calls, orchestration_events),
        "runtime_decisions": build_runtime_decisions_table(orch),
        "cycle_cards": build_cycle_cards(traces, orch),
        "retrieval_reuse_summary": build_retrieval_reuse_summary(traces, orch),
        "has_orchestration_events": len(orch) > 0,
    }


def enrich_llm_call_for_ui(call: dict[str, Any]) -> dict[str, Any]:
    """Attach badge hints for llm_calls rows (non-destructive copy of extra keys)."""
    out = dict(call)
    badges: list[str] = []
    ck = (call.get("call_kind") or "").strip().lower()
    co = (call.get("call_outcome") or "").strip().lower()
    cs = (call.get("cache_status") or "").strip().lower()
    if ck == "speculative":
        badges.append("speculative")
    if co == "discarded":
        badges.append("discarded")
    if co == "used":
        badges.append("used")
    if co == "skipped":
        badges.append("skipped")
    if co == "cancelled":
        badges.append("cancelled")
    if co == "cached" or cs == "reused":
        badges.append("reused")
    if cs == "hit":
        badges.append("cache hit")
    raw_ps = call.get("parsed_summary_json")
    if isinstance(raw_ps, str) and raw_ps.strip():
        try:
            d = json.loads(raw_ps)
        except Exception:
            d = None
        if isinstance(d, dict):
            pc = d.get("parse_contract")
            if isinstance(pc, dict):
                if pc.get("strict_json_requested"):
                    badges.append("strict json")
                rc = str(pc.get("response_contract") or "").lower()
                if rc == "json_object":
                    badges.append("json_object")
                ps = str(pc.get("parse_status") or "").lower()
                if ps == "fallback_ok":
                    badges.append("fallback parse")
                elif ps == "failed":
                    badges.append("parse failed")
                rct = pc.get("retry_count")
                if isinstance(rct, int) and rct > 0:
                    badges.append(f"retry x{rct}")
    out["semantic_badges"] = badges
    return out
