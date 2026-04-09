"""
Report data model for MoralStack.

Single source of truth for request (deliberation) reports. Buildable from
CLI (trace + call_logger + result + prompt) or from persistence (request + llm_calls + traces).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class PhaseInfo:
    """Single phase for journey map and detailed phases."""

    phase_type: str
    cycle: int
    success: bool
    duration_ms: float
    decision: str | None = None
    decision_reason: str | None = None
    details: dict[str, Any] = field(default_factory=dict)
    input_summary: str = ""
    system_prompt: str = ""
    output_summary: str = ""
    full_input: str = ""
    full_output: str = ""
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


@dataclass
class RevisionEntry:
    """Single revision in draft history."""

    cycle: int
    draft_text: str
    guidance_used: str
    is_initial: bool = False


@dataclass
class CallLogEntry:
    """Single LLM call for the full call log section."""

    call_id: int | str
    module: str
    action: str
    duration_ms: float
    full_prompt: str
    full_response: str


@dataclass
class RequestReport:
    """
    Structured request (deliberation) report.

    Rendered by renderer_markdown.render_request_report() to produce
    the same markdown as CLI and UI export.
    """

    request_id: str
    generated_at: str
    path_badge: str
    risk_category: str
    risk_score: float
    total_cycles: int
    converged: bool
    response_type: str
    total_duration_ms: float
    prompt: str
    status: str
    decision_reason: str
    response_content: str
    domain: str = ""
    phases_by_cycle: list = field(default_factory=list)
    hindsight_score: float | None = None
    phase_durations: dict = field(default_factory=dict)
    module_stats: dict = field(default_factory=dict)
    policy_overlay: dict | None = None
    revision_history: list = field(default_factory=list)
    call_log: list = field(default_factory=list)
    benchmark_result: dict | None = None
    decision_traces: list = field(default_factory=list)
    debug_events: list = field(default_factory=list)
    soft_revision_applied: bool = False
    soft_revision_guidance_used: str = ""
    risk_rationale: str = ""  # Combined rationale from risk estimator (intent + operational)
    calibration_guard_info: str = ""  # Non-empty if the calibration guard was triggered
    orchestrator_observability: dict | None = None  # Path routing / debug-derived explanations (display only)
    policy_gating_observability: dict | None = None  # PRE_POLICY + SAFE_COMPLETE gating (display only)
    # Conversation linkage (multi-turn foundation; None when absent)
    conversation_id: str | None = None
    turn_index: int | None = None
    parent_request_id: str | None = None


def get_final_response_text(calls: list, final_action: str | None = None) -> str:
    """
    Determine the final response text from LLM calls for report display.

    Priority:
      1. Explicit refuse/refusal call (for REFUSE actions)
      2. Last generate/rewrite call (the policy draft)
      3. Empty string
    """
    fa = (final_action or "").strip().upper()
    if fa == "REFUSE":
        for call in reversed(calls):
            action = (call.get("action") or "").lower()
            if "refuse" in action or "refusal" in action:
                raw = call.get("raw_response") or ""
                if raw.strip():
                    return raw
    for call in reversed(calls):
        action = (call.get("action") or "").lower()
        if "generate" in action or "rewrite" in action:
            raw = call.get("raw_response") or ""
            if raw.strip():
                return raw
    return ""


def request_report_from_db(run_id: str, request_id: str) -> "RequestReport | None":
    """Build RequestReport from persistence. Returns None if DB not configured or request not found."""
    import json
    from collections import defaultdict
    from datetime import datetime

    from moralstack.observability import obs
    from moralstack.observability.config import get_db_path

    _rs = obs.read_store
    get_debug_events_for_request = _rs.get_debug_events_for_request
    get_decision_traces_for_request = _rs.get_decision_traces_for_request
    get_llm_calls_for_request = _rs.get_llm_calls_for_request
    get_request = _rs.get_request
    get_run = _rs.get_run
    from moralstack.reports.benchmark_report_loader import (
        get_benchmark_result_by_request_id,
        load_benchmark_report,
    )

    path = get_db_path()
    if not path:
        return None
    req = get_request(run_id, request_id)
    if not req:
        return None
    llm_calls = get_llm_calls_for_request(run_id, request_id)
    traces = get_decision_traces_for_request(run_id, request_id)
    debug_events = get_debug_events_for_request(run_id, request_id)
    benchmark_result = None
    run = get_run(run_id)
    if run and (run.get("run_type") or "").strip().lower() == "benchmark":
        report = load_benchmark_report(run_id)
        if report:
            br = get_benchmark_result_by_request_id(report, request_id)
            if br and not br.get("error"):
                benchmark_result = br

    def trace_dict(t):
        tj = t.get("trace_json", "{}")
        if isinstance(tj, str):
            try:
                return json.loads(tj)
            except Exception:
                return {}
        return tj or {}

    def derive_total_cycles(trace_list, calls, path_val):
        total = 0
        for t in trace_list:
            td = trace_dict(t)
            total = max(total, td.get("total_cycles", 0) or 0)
        if total > 0:
            return total
        if not calls:
            return 0
        total = max((c.get("cycle") or 0) for c in calls)
        if total > 0:
            return total
        # Only force 0 when path is FAST_PATH and there is no evidence of deliberation.
        if (path_val or "").strip().upper() == "FAST_PATH":
            return 0
        return total

    finals = [t for t in traces if (t.get("stage") or "").strip().upper() == "FINAL"]
    final_trace = finals[-1] if finals else (traces[-1] if traces else {})
    td = trace_dict(final_trace)
    risk_score = float(td.get("risk_score", 0.0))
    risk_category = (td.get("risk_category") or "").strip()
    path_val = (td.get("path") or "").strip()
    final_action = (td.get("final_action") or "").strip()
    total_cycles = derive_total_cycles(traces, llm_calls, path_val)
    # stop_reason may live in a different trace than the last one; scan all traces
    stop = (td.get("stop_reason") or "").strip().upper()
    if not stop:
        for t in reversed(traces):
            candidate = (trace_dict(t).get("stop_reason") or "").strip().upper()
            if candidate:
                stop = candidate
                break
    converged = stop == "CONVERGED"
    if not converged and debug_events:
        for ev in debug_events:
            payload = ev.get("payload_json")
            if isinstance(payload, str):
                try:
                    payload = json.loads(payload)
                except Exception:
                    payload = {}
            if not isinstance(payload, dict):
                payload = ev
            data = payload.get("data", payload) if isinstance(payload.get("data"), dict) else payload
            if data.get("converged") is True:
                converged = True
                break
    # Fast path with no deliberation and non-REFUSE outcome => converged for display.
    if total_cycles == 0 and final_action and (final_action.strip().upper() != "REFUSE"):
        converged = True
    # With max_deliberation_cycles=1, exit is CYCLES_EXHAUSTED; convergence was never tested.
    if total_cycles == 1 and final_action and stop == "CYCLES_EXHAUSTED":
        converged = True
    final_response_text = (req.get("final_response") or "").strip()
    if not final_response_text:
        final_response_text = get_final_response_text(llm_calls, final_action)
    if not final_response_text.strip() and final_action.strip().upper() == "REFUSE":
        # Fallback 1: decision traces may carry response_content even if llm_calls persistence is delayed.
        for t in reversed(traces):
            td_fb = trace_dict(t)
            rc = (td_fb.get("response_content") or "").strip()
            if rc:
                final_response_text = rc
                break
        # Fallback 2: benchmark report export may contain the served response
        if not final_response_text.strip() and benchmark_result:
            br_resp = (benchmark_result.get("response") or "").strip()
            if br_resp:
                final_response_text = br_resp
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    actual_path = path_val
    if debug_events:
        for ev in debug_events:
            payload = ev.get("payload_json")
            if isinstance(payload, str):
                try:
                    payload = json.loads(payload)
                except Exception:
                    payload = {}
            if not isinstance(payload, dict):
                payload = ev
            msg = (payload.get("message") or ev.get("message") or "").lower()
            if "taking _deliberative_path" in msg:
                actual_path = "DELIBERATIVE_PATH"
                break
            if "taking _fast_path" in msg:
                actual_path = "FAST_PATH"
                break
    path_badge = "⚡ **FAST PATH**" if actual_path.upper() == "FAST_PATH" else "🧭 **DELIBERATIVE PATH**"

    if final_action:
        fa_upper = final_action.upper()
        if fa_upper == "REFUSE":
            status = "🛑 **REFUSED** - Request refused due to policy."
        elif fa_upper == "SAFE_COMPLETE":
            status = "⚠ **APPROVED WITH CAVEATS** - Response includes disclaimers."
        elif fa_upper == "NORMAL_COMPLETE":
            status = "✅ **APPROVED** - Response delivered."
        else:
            status = "**" + final_action + "**"
    else:
        status = "📋 **COMPLETED** - Outcome from trace."

    by_cycle = defaultdict(list)
    for c in llm_calls:
        cy = c.get("cycle") if c.get("cycle") is not None else 0
        by_cycle[cy].append(c)
    phases_by_cycle = []
    for cycle_num in sorted(by_cycle.keys()):
        phase_infos = []
        for c in by_cycle[cycle_num]:
            mod = c.get("module", "")
            ph = c.get("phase", "")
            dur = c.get("duration_ms")
            duration_ms = float(dur) if dur is not None else 0.0
            phase_infos.append(
                PhaseInfo(
                    phase_type=mod + " / " + ph,
                    cycle=cycle_num,
                    success=True,
                    duration_ms=duration_ms,
                    decision=c.get("action"),
                    input_summary="",
                    output_summary="",
                    system_prompt=c.get("system_prompt") or "",
                    full_input=c.get("prompt") or "",
                    full_output=c.get("raw_response") or "",
                )
            )
        phases_by_cycle.append((cycle_num, phase_infos))

    total_ms = sum((c.get("duration_ms") or 0) for c in llm_calls)
    by_module = defaultdict(list)
    for c in llm_calls:
        mod = c.get("module", "unknown")
        dur = c.get("duration_ms")
        if dur is not None:
            by_module[mod].append(dur)
    module_stats = {}
    for mod in sorted(by_module.keys()):
        durs = by_module[mod]
        total = sum(durs)
        module_stats[mod] = {"calls": len(durs), "total_ms": total, "avg_ms": total / len(durs) if durs else 0.0}
    phase_durations = {}
    for _, pi_list in phases_by_cycle:
        for pi in pi_list:
            key = pi.phase_type
            phase_durations[key] = phase_durations.get(key, 0) + pi.duration_ms
    policy_overlay = None
    stop_reason = td.get("stop_reason", "").strip()
    principle_ids = td.get("policy_principle_ids") or []
    if stop_reason or principle_ids:
        policy_overlay = {"stop_reason": stop_reason, "principle_ids": principle_ids}
    rewrites = [
        c for c in llm_calls if "rewrite" in (c.get("action") or "").lower() or "revision" in (c.get("phase") or "").lower()
    ]
    revision_history = [
        RevisionEntry(cycle=c.get("cycle", 0), draft_text=c.get("raw_response") or "", guidance_used="", is_initial=False)
        for c in rewrites
    ]
    call_log = [
        CallLogEntry(
            call_id=i,
            module=c.get("module", "unknown"),
            action=c.get("action", ""),
            duration_ms=float(c.get("duration_ms") or 0),
            full_prompt=c.get("prompt") or "",
            full_response=c.get("raw_response") or "",
        )
        for i, c in enumerate(llm_calls, 1)
    ]
    soft_revision_calls = [
        c
        for c in llm_calls
        if (c.get("action") or "").strip().lower() == "soft_revision"
        or (c.get("phase") or "").strip().lower() == "soft_revision"
    ]
    soft_revision_applied = len(soft_revision_calls) > 0
    soft_revision_guidance_used = ""
    if soft_revision_calls:
        first_prompt = (soft_revision_calls[0].get("prompt") or "").strip()
        if "Guidance:" in first_prompt:
            idx = first_prompt.find("Guidance:")
            soft_revision_guidance_used = first_prompt[idx : idx + 500].strip()
        elif first_prompt:
            soft_revision_guidance_used = first_prompt[:500]
    response_content_trim = final_response_text[:2000] + ("..." if len(final_response_text) > 2000 else "")
    decision_reason_str = (td.get("decision_reason") or final_action or "").strip()

    # Extract risk rationale from risk estimation LLM call(s) raw_response
    risk_rationale = ""
    calibration_guard_info = ""
    risk_est_calls = [c for c in llm_calls if (c.get("module") or "").strip().lower() == "risk_estimator"]
    if risk_est_calls:
        # Parallel mode: merge intent + operational rationales
        intent_rat = ""
        op_rat = ""
        mono_rat = ""
        guard_rat = ""
        for c in risk_est_calls:
            action = (c.get("action") or "").strip().lower()
            raw = (c.get("raw_response") or "").strip()
            if not raw:
                continue
            try:
                # Strip markdown fences (```json ... ```) that some LLM responses include
                clean = raw
                if clean.startswith("```"):
                    clean = clean.split("\n", 1)[1] if "\n" in clean else clean[3:]
                if clean.endswith("```"):
                    clean = clean[: clean.rfind("```")]
                clean = clean.strip()
                parsed = json.loads(clean)
                rat = (parsed.get("rationale") or "").strip() if isinstance(parsed, dict) else ""
            except Exception:
                rat = ""
            if action == "estimate_intent":
                intent_rat = rat
            elif action == "estimate_operational":
                op_rat = rat
            elif action == "estimate":
                mono_rat = rat
            elif action == "calibration_guard":
                guard_rat = rat
                calibration_guard_info = rat
        if intent_rat and op_rat:
            risk_rationale = f"[intent] {intent_rat} | [op_risk] {op_rat}"
        elif intent_rat:
            risk_rationale = intent_rat
        elif op_rat:
            risk_rationale = op_rat
        elif mono_rat:
            risk_rationale = mono_rat
        if guard_rat:
            risk_rationale = f"{risk_rationale} | ⚡ {guard_rat}" if risk_rationale else f"⚡ {guard_rat}"

    from moralstack.reports.orchestrator_observability import build_orchestrator_observability
    from moralstack.reports.policy_gating_observability import build_policy_gating_observability

    orch_obs = build_orchestrator_observability(debug_events, traces)
    pg_obs = build_policy_gating_observability(traces, debug_events)

    return RequestReport(
        request_id=request_id,
        generated_at=ts,
        path_badge=path_badge,
        risk_category=risk_category,
        risk_score=risk_score,
        total_cycles=total_cycles,
        converged=converged,
        response_type=final_action or "unknown",
        total_duration_ms=total_ms,
        prompt=req.get("prompt", ""),
        status=status,
        decision_reason=decision_reason_str,
        response_content=response_content_trim,
        domain=(req.get("domain") or "").strip(),
        phases_by_cycle=phases_by_cycle,
        hindsight_score=None,
        phase_durations=phase_durations,
        module_stats=module_stats,
        policy_overlay=policy_overlay,
        revision_history=revision_history,
        call_log=call_log,
        benchmark_result=benchmark_result,
        decision_traces=traces,
        debug_events=debug_events,
        soft_revision_applied=soft_revision_applied,
        soft_revision_guidance_used=soft_revision_guidance_used,
        risk_rationale=risk_rationale,
        calibration_guard_info=calibration_guard_info,
        orchestrator_observability=orch_obs,
        policy_gating_observability=pg_obs,
        conversation_id=req.get("conversation_id"),
        turn_index=req.get("turn_index"),
        parent_request_id=req.get("parent_request_id"),
    )


def request_report_from_cli(trace, call_logger, result, prompt) -> RequestReport:
    """Build RequestReport from CLI (DeliberationTrace, CallLogger, result, prompt)."""
    from datetime import datetime

    from moralstack.cli.models import PhaseType

    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    path_badge = "⚡ **FAST PATH**" if (trace.path or "").strip() == "fast" else "ᾞ0 **DELIBERATIVE PATH**"
    response_content = result.response.content if result else "N/A"
    orch_trace = getattr(result, "trace", None)
    decision_reason = ""
    if orch_trace:
        fa = (getattr(orch_trace, "final_action", "") or "").strip()
        dp = (getattr(orch_trace, "decision_path", "") or "").strip()
        decision_reason = (fa + " (" + dp + ")") if fa else ""
    if trace.response_type == "full_refusal":
        status = "Ὢb **REFUSED** - Request was refused due to policy violations"
    elif trace.response_type == "with_caveat":
        status = "⚠ **APPROVED WITH CAVEATS** - Response includes disclaimers"
    elif trace.converged:
        status = "✅ **APPROVED** - All modules satisfied"
    else:
        status = "὾6 **COMPLETED** - Max cycles reached without full convergence"
    calls_by_module = {}
    if call_logger and call_logger.calls:
        for call in call_logger.calls:
            module = call.get("module", "unknown")
            if module not in calls_by_module:
                calls_by_module[module] = []
            calls_by_module[module].append(call)

    def get_full_data(phase, cycle):
        phase_to_module = {
            PhaseType.RISK_ESTIMATION: "risk_estimator",
            PhaseType.GENERATION: "policy",
            PhaseType.REVISION: "policy",
            PhaseType.CRITIQUE: "critic",
            PhaseType.SIMULATION: "simulator",
            PhaseType.HINDSIGHT: "hindsight",
            PhaseType.PERSPECTIVES: "perspectives",
            PhaseType.CONVERGENCE_CHECK: "orchestrator",
            PhaseType.PATH_DECISION: "orchestrator",
            PhaseType.ASSEMBLY: "orchestrator",
        }
        module_name = phase_to_module.get(phase.phase)
        if not module_name or module_name not in calls_by_module:
            return (phase.input_summary or "", phase.output_summary or "")
        module_calls = calls_by_module[module_name]
        if phase.phase == PhaseType.RISK_ESTIMATION:
            call_index = 0
        elif phase.phase == PhaseType.GENERATION:
            call_index = 0
            for idx, call in enumerate(module_calls):
                if "generate" in call.get("action", "").lower():
                    call_index = idx
                    break
        elif phase.phase == PhaseType.REVISION:
            rewrite_calls = [c for c in module_calls if "rewrite" in c.get("action", "").lower()]
            if cycle >= 2 and len(rewrite_calls) >= cycle - 1:
                call = rewrite_calls[cycle - 2] if cycle - 2 < len(rewrite_calls) else None
                if call:
                    return (
                        call.get("full_prompt", call.get("prompt", "")),
                        call.get("full_response", call.get("response", "")),
                    )
            return (phase.input_summary or "", phase.output_summary or "")
        else:
            call_index = max(0, cycle - 1)
        if call_index < len(module_calls):
            call = module_calls[call_index]
            return (
                call.get("full_prompt", call.get("prompt", "")),
                call.get("full_response", call.get("response", "")),
            )
        return (phase.input_summary or "", phase.output_summary or "")

    phases_by_cycle = []
    phase_durations = {}
    for cycle_num, phases in sorted(trace.get_phases_by_cycle().items()):
        phase_infos = []
        for phase in phases:
            full_in, full_out = get_full_data(phase, cycle_num)
            pt = phase.phase.value if hasattr(phase.phase, "value") else str(phase.phase)
            phase_infos.append(
                PhaseInfo(
                    phase_type=pt,
                    cycle=phase.cycle,
                    success=phase.success,
                    duration_ms=phase.duration_ms,
                    decision=phase.decision,
                    decision_reason=phase.decision_reason,
                    details=getattr(phase, "details", {}) or {},
                    input_summary=phase.input_summary or "",
                    output_summary=phase.output_summary or "",
                    full_input=full_in,
                    full_output=full_out,
                    errors=getattr(phase, "errors", []) or [],
                    warnings=getattr(phase, "warnings", []) or [],
                )
            )
            phase_durations[pt] = phase_durations.get(pt, 0) + phase.duration_ms
        phases_by_cycle.append((cycle_num, phase_infos))
    module_stats = {}
    for phase in trace.phases:
        name = phase.phase.value if hasattr(phase.phase, "value") else str(phase.phase)
        if name not in module_stats:
            module_stats[name] = {"calls": 0, "total_ms": 0.0, "avg_ms": 0.0}
        module_stats[name]["calls"] += 1
        module_stats[name]["total_ms"] += phase.duration_ms
    for m in module_stats:
        if module_stats[m]["calls"] > 0:
            module_stats[m]["avg_ms"] = module_stats[m]["total_ms"] / module_stats[m]["calls"]
    policy_overlay = None
    if result and hasattr(result, "response"):
        resp = result.response
        overlay = getattr(resp, "policy_overlay", None)
        meta = getattr(resp, "meta_analysis", None)
        if overlay or meta:
            policy_overlay = {
                "caveat_type": getattr(overlay, "caveat_type", "") if overlay else "",
                "principle_ids": getattr(overlay, "principle_ids", []) if overlay else [],
                "stop_reason": getattr(meta, "stop_reason", "") if meta else "",
                "hindsight_score": getattr(meta, "hindsight_score", 0.0) if meta else 0.0,
            }
    revision_history = [
        RevisionEntry(
            cycle=r.cycle,
            draft_text=r.draft_text,
            guidance_used=r.guidance_used or "",
            is_initial=getattr(r, "is_initial", False),
        )
        for r in (trace.draft_history or [])
    ]
    call_log = []
    if call_logger and call_logger.calls:
        for call in call_logger.calls:
            call_log.append(
                CallLogEntry(
                    call_id=call.get("id", 0),
                    module=call.get("module", "unknown"),
                    action=call.get("action", "unknown"),
                    duration_ms=float(call.get("duration_ms", 0)),
                    full_prompt=call.get("full_prompt", call.get("prompt", "")),
                    full_response=call.get("full_response", call.get("response", "")),
                )
            )
    hindsight_score = None
    if result and hasattr(result.response, "metadata"):
        hindsight_score = getattr(result.response.metadata, "hindsight_score", None)
    total_duration_ms = trace.total_duration_ms() if hasattr(trace, "total_duration_ms") else 0.0
    if total_duration_ms <= 0:
        total_duration_ms = sum(p.duration_ms for p in trace.phases)
    # Extract risk rationale from CLI call logger
    cli_risk_rationale = ""
    cli_calibration_guard_info = ""
    if call_logger and call_logger.calls:
        import json as _json

        _intent_rat = ""
        _op_rat = ""
        _guard_rat = ""
        for call in call_logger.calls:
            module = (call.get("module") or "").strip().lower()
            if module != "risk_estimator":
                continue
            action = (call.get("action") or "").strip().lower()
            raw = (call.get("full_response") or call.get("response") or "").strip()
            if not raw:
                continue
            try:
                # Strip markdown fences (```json ... ```) that some LLM responses include
                clean = raw
                if clean.startswith("```"):
                    clean = clean.split("\n", 1)[1] if "\n" in clean else clean[3:]
                if clean.endswith("```"):
                    clean = clean[: clean.rfind("```")]
                clean = clean.strip()
                parsed = _json.loads(clean)
                rat = (parsed.get("rationale") or "").strip() if isinstance(parsed, dict) else ""
            except Exception:
                rat = ""
            if action == "estimate_intent" and rat:
                _intent_rat = rat
            elif action == "estimate_operational" and rat:
                _op_rat = rat
            elif action == "estimate" and rat:
                cli_risk_rationale = cli_risk_rationale or rat
            elif action == "calibration_guard" and rat:
                _guard_rat = rat
                cli_calibration_guard_info = rat
        if _intent_rat and _op_rat:
            cli_risk_rationale = f"[intent] {_intent_rat} | [op_risk] {_op_rat}"
        elif _intent_rat:
            cli_risk_rationale = cli_risk_rationale or f"[intent] {_intent_rat}"
        elif _op_rat:
            cli_risk_rationale = cli_risk_rationale or f"[op_risk] {_op_rat}"
        if _guard_rat:
            cli_risk_rationale = f"{cli_risk_rationale} | ⚡ {_guard_rat}" if cli_risk_rationale else f"⚡ {_guard_rat}"
        cli_risk_rationale = cli_risk_rationale.strip().strip("|").strip()

    return RequestReport(
        request_id=trace.request_id or "",
        generated_at=ts,
        path_badge=path_badge,
        risk_category=trace.risk_category or "",
        risk_score=float(trace.risk_score),
        total_cycles=int(trace.total_cycles),
        converged=bool(trace.converged),
        response_type=trace.response_type or "",
        total_duration_ms=total_duration_ms,
        prompt=prompt,
        status=status,
        decision_reason=decision_reason,
        response_content=response_content,
        phases_by_cycle=phases_by_cycle,
        hindsight_score=hindsight_score,
        phase_durations=phase_durations,
        module_stats=module_stats,
        policy_overlay=policy_overlay,
        revision_history=revision_history,
        call_log=call_log,
        benchmark_result=None,
        decision_traces=[],
        debug_events=[],
        risk_rationale=cli_risk_rationale,
        calibration_guard_info=cli_calibration_guard_info,
    )
