"""
Orchestrator routing observability for reports and UI.

Extracts structured explanations from persisted debug events (orch_debug_log) and
optional decision traces. Does not implement or alter routing logic — display only.
"""

from __future__ import annotations

import json
from typing import Any


def _parse_event_payload(ev: dict[str, Any]) -> dict[str, Any]:
    """Normalize payload_json from a debug_events row into a dict."""
    pj = ev.get("payload_json")
    if pj is None:
        pj = ev.get("payload")
    if isinstance(pj, str):
        try:
            return json.loads(pj)
        except Exception:
            return {}
    return pj if isinstance(pj, dict) else {}


def _final_trace_dict(traces: list | None) -> dict[str, Any]:
    if not traces:
        return {}
    final_trace = None
    for t in traces:
        stage = (t.get("stage") or "").strip().upper()
        if stage == "FINAL":
            final_trace = t
    if final_trace is None:
        final_trace = traces[-1]
    tj = final_trace.get("trace_json", "{}")
    if isinstance(tj, str):
        try:
            return json.loads(tj)
        except Exception:
            return {}
    return tj if isinstance(tj, dict) else {}


def _explain_event(message: str, data: dict[str, Any]) -> str | None:
    """One English sentence per known orchestrator debug message (display only)."""
    m = (message or "").strip()
    if not m:
        return None
    if m == "DECISION_EXPLANATION":
        fa = data.get("final_action", "")
        wr = data.get("winning_rule", "")
        rc = data.get("reason_codes") or []
        rs = data.get("risk_score", "")
        oa = data.get("overlay_applied", "")
        parts = [f"Policy decision after risk assessment: final_action={fa!r}, winning_rule={wr!r}, " f"risk_score={rs!r}."]
        if rc:
            parts.append(f" Reason codes: {', '.join(str(x) for x in rc)}.")
        if oa:
            parts.append(f" Overlay context: {oa!r}.")
        return "".join(parts).replace("  ", " ")
    if "overlay sensitive" in m.lower():
        orig = data.get("original_risk_score")
        floored = data.get("floored_to")
        eff = data.get("per_overlay_floor")
        return (
            f"Sensitive-domain overlay: risk_score was raised from {orig!r} to at least {floored!r} "
            f"(configured overlay floor={eff!r}) before routing."
        )
    if "borderline" in m.lower() and "refuse" in m.lower():
        lo = data.get("lower_bound")
        hi = data.get("upper_bound")
        rs = data.get("risk_score")
        return (
            f"Borderline REFUSE band: risk_score={rs!r} in [{lo!r}, {hi!r}] — orchestrator forced the "
            "deliberative loop instead of an immediate REFUSE."
        )
    if "branch risk_policy vs deliberative" in m:
        rpa = data.get("risk_policy_action")
        rs = data.get("risk_score")
        tl = data.get("threshold_low")
        dp = data.get("decision.path")
        return (
            f"path_router branch: resolved risk_policy_action={rpa!r} at risk_score={rs!r} "
            f"(threshold_low={tl!r}); policy pre-path was {dp!r} — used to choose fast vs deliberative execution."
        )
    if "taking _fast_path" in m:
        return "Orchestrator selected FAST execution: single-shot generation without a deliberation cycle."
    if "taking _deliberative_path" in m:
        return "Orchestrator selected DELIBERATIVE execution: critic / simulator / perspectives / hindsight may run."
    if "early return REFUSE" in m:
        dp = data.get("decision.path")
        return f"Early REFUSE path: immediate refusal with decision.path={dp!r} (no deliberation)."
    if "early return benign_fast_path" in m:
        dp = data.get("decision.path")
        return f"Early benign path: minimal-risk fast completion with decision.path={dp!r}."
    if "early return safe_complete_path" in m:
        dp = data.get("decision.path")
        return f"Early SAFE_COMPLETE path: governed response without full deliberation, decision.path={dp!r}."
    return f"{m} — {json.dumps(data, ensure_ascii=False)}" if data else m


def build_orchestrator_observability(
    debug_events: list[dict[str, Any]] | None,
    traces: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """
    Build a structured summary for UI and markdown from persisted debug events.

    Returns keys:
      - decision_explanation: payload dict or None (from DECISION_EXPLANATION)
      - events_chronological: filtered events with message, hypothesis_id, data
      - narrative_bullets: short English lines (why routing behaved as observed)
      - routing_signals: extracted fields (path_taken, branch, overlay_floor, etc.)
      - trace_bounds: policy_min_action / policy_max_action from FINAL trace when present
      - has_routing_data: True if any narrative or decision_explanation exists
    """
    out: dict[str, Any] = {
        "decision_explanation": None,
        "events_chronological": [],
        "narrative_bullets": [],
        "routing_signals": {},
        "trace_bounds": {},
        "has_routing_data": False,
    }
    td_final = _final_trace_dict(traces)
    if td_final:
        pmin = (td_final.get("policy_min_action") or "").strip()
        pmax = (td_final.get("policy_max_action") or "").strip()
        if pmin or pmax:
            out["trace_bounds"] = {"policy_min_action": pmin, "policy_max_action": pmax}
            out["narrative_bullets"].append(
                f"Constitution policy bounds on this request: min_required={pmin or '—'}, "
                f"max_allowed={pmax or '—'} (from FINAL trace)."
            )

    interesting: list[dict[str, Any]] = []
    for ev in sorted(debug_events or [], key=lambda e: e.get("created_at") or 0):
        payload = _parse_event_payload(ev)
        message = (payload.get("message") or "").strip()
        data = payload.get("data")
        if not isinstance(data, dict):
            data = {}
        hid = (payload.get("hypothesisId") or payload.get("hypothesis_id") or "").strip()

        if message == "DECISION_EXPLANATION":
            # Third arg to orch_debug_log is the full explanation dict (nested under data).
            merged = dict(data) if data else {}
            out["decision_explanation"] = merged
            interesting.append({"message": message, "hypothesis_id": hid, "data": merged})
            continue

        if any(
            x in message
            for x in (
                "taking _fast_path",
                "taking _deliberative_path",
                "REFUSE borderline",
                "branch risk_policy vs deliberative",
                "overlay sensitive",
                "early return REFUSE",
                "early return benign_fast_path",
                "early return safe_complete_path",
            )
        ):
            interesting.append({"message": message, "hypothesis_id": hid, "data": data})

    out["events_chronological"] = interesting

    signals: dict[str, Any] = {}
    for ev in interesting:
        msg = ev["message"]
        data = ev.get("data") or {}
        if msg == "branch risk_policy vs deliberative":
            signals["risk_policy_action"] = data.get("risk_policy_action")
            signals["threshold_low"] = data.get("threshold_low")
            signals["decision_path_at_branch"] = data.get("decision.path")
            signals["risk_score_at_branch"] = data.get("risk_score")
        if "overlay sensitive" in msg.lower():
            signals["overlay_risk_floor_applied"] = True
            signals["original_risk_score"] = data.get("original_risk_score")
            signals["floored_to"] = data.get("floored_to")
        if "taking _fast_path" in msg:
            signals["path_taken"] = "fast"
        if "taking _deliberative_path" in msg:
            signals["path_taken"] = "deliberative"
        if "borderline" in msg.lower():
            signals["borderline_refuse_forced_deliberation"] = True
    out["routing_signals"] = signals

    seen: set[str] = set()
    for ev in interesting:
        line = _explain_event(ev["message"], ev.get("data") or {})
        if line and line not in seen:
            seen.add(line)
            out["narrative_bullets"].append(line)

    if out["decision_explanation"]:
        de = out["decision_explanation"]
        wnr = (de.get("why_not_refuse") or "").strip()
        wns = (de.get("why_not_safe_complete") or "").strip()
        wnn = (de.get("why_not_normal_complete") or "").strip()
        if wnr:
            out["narrative_bullets"].append(f"Why not REFUSE (policy explanation): {wnr}")
        if wns:
            out["narrative_bullets"].append(f"Why not SAFE_COMPLETE: {wns}")
        if wnn:
            out["narrative_bullets"].append(f"Why not NORMAL_COMPLETE: {wnn}")

    # Fallback when debug events are missing (older DB): use FINAL trace explainability fields.
    if td_final:
        if not out["decision_explanation"]:
            wnr = (td_final.get("why_not_refuse") or "").strip()
            wns = (td_final.get("why_not_safe_complete") or "").strip()
            wnn = (td_final.get("why_not_normal_complete") or "").strip()
            if wnr and not any("Why not REFUSE" in x for x in out["narrative_bullets"]):
                out["narrative_bullets"].append(f"Why not REFUSE (FINAL trace): {wnr}")
            if wns and not any("Why not SAFE_COMPLETE" in x for x in out["narrative_bullets"]):
                out["narrative_bullets"].append(f"Why not SAFE_COMPLETE (FINAL trace): {wns}")
            if wnn and not any("Why not NORMAL_COMPLETE" in x for x in out["narrative_bullets"]):
                out["narrative_bullets"].append(f"Why not NORMAL_COMPLETE (FINAL trace): {wnn}")
        wr = (td_final.get("winning_rule") or "").strip()
        if wr and not any("winning_rule=" in x for x in out["narrative_bullets"]):
            out["narrative_bullets"].append(f"Winning rule (FINAL trace): {wr}")

    out["has_routing_data"] = bool(out["narrative_bullets"] or out["decision_explanation"])
    return out


def orchestrator_observability_to_io_annotations(obs: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    """Build flow-graph style I/O rows from observability dict (labels are English, values are structural)."""
    inputs: list[dict[str, Any]] = []
    outputs: list[dict[str, Any]] = []
    de = obs.get("decision_explanation")
    if isinstance(de, dict):
        fa = de.get("final_action")
        wr = de.get("winning_rule")
        if fa is not None:
            inputs.append({"label": "final_action (policy)", "source": str(fa)})
        if wr:
            inputs.append({"label": "winning_rule", "source": str(wr)})
    sig = obs.get("routing_signals") or {}
    if sig.get("risk_policy_action") is not None:
        outputs.append({"label": "risk_policy_action", "value": str(sig.get("risk_policy_action"))})
    if sig.get("threshold_low") is not None:
        outputs.append({"label": "threshold_low", "value": sig.get("threshold_low")})
    if sig.get("risk_score_at_branch") is not None:
        outputs.append({"label": "risk_score@branch", "value": sig.get("risk_score_at_branch")})
    if sig.get("decision_path_at_branch"):
        outputs.append({"label": "decision.path@branch", "value": str(sig.get("decision_path_at_branch"))})
    if sig.get("path_taken"):
        outputs.append({"label": "orchestrator_path_taken", "value": str(sig.get("path_taken"))})
    if sig.get("overlay_risk_floor_applied"):
        outputs.append(
            {
                "label": "sensitive_overlay_floor",
                "value": f"{sig.get('original_risk_score')} → {sig.get('floored_to')}",
            }
        )
    tb = obs.get("trace_bounds") or {}
    if tb.get("policy_min_action") or tb.get("policy_max_action"):
        outputs.append(
            {
                "label": "policy_bounds",
                "value": f"min={tb.get('policy_min_action') or '—'} max={tb.get('policy_max_action') or '—'}",
            }
        )
    return {"inputs": inputs, "outputs": outputs}


def render_orchestrator_observability_markdown(obs: dict[str, Any]) -> str:
    """Markdown section for export (English, mirrors calibration-style transparency)."""
    if not obs.get("has_routing_data"):
        return ""

    lines = [
        "---",
        "",
        "## Path routing and risk governance (runtime logs)",
        "",
        "> Derived from orchestrator debug events and the FINAL decision trace. "
        "This explains **which branch** ran after risk assessment (fast, deliberative, early exit) "
        "and **why** caps/thresholds mattered — without changing runtime behavior.",
        "",
    ]
    bullets = obs.get("narrative_bullets") or []
    if bullets:
        lines.append("### Summary")
        lines.append("")
        for b in bullets:
            lines.append(f"- {b}")
        lines.append("")

    de = obs.get("decision_explanation")
    if isinstance(de, dict) and de:
        lines.append("### DECISION_EXPLANATION payload")
        lines.append("")
        lines.append("```json")
        lines.append(json.dumps(de, indent=2, ensure_ascii=False))
        lines.append("```")
        lines.append("")

    evs = obs.get("events_chronological") or []
    if evs:
        lines.append("### Orchestrator events (chronological)")
        lines.append("")
        for i, ev in enumerate(evs, 1):
            msg = ev.get("message", "")
            hid = ev.get("hypothesis_id", "")
            lines.append(f"{i}. **{msg}**" + (f" `{hid}`" if hid else ""))
            dat = ev.get("data")
            if isinstance(dat, dict) and dat:
                lines.append("")
                lines.append("```json")
                lines.append(json.dumps(dat, indent=2, ensure_ascii=False))
                lines.append("```")
            lines.append("")

    return "\n".join(lines).rstrip() + "\n"
