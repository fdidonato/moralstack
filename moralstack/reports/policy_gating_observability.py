"""
PRE_POLICY bounds (safe_complete_policy) and SAFE_COMPLETE gating observability.

Display-only: explains policy reason codes and gating events from traces + debug events.
Does not implement policy logic.
"""

from __future__ import annotations

import json
from typing import Any

from moralstack.runtime.decision.safe_complete_policy import (
    REASON_DOMAIN_REGULATED,
    REASON_HARD_VIOLATIONS,
    REASON_NORMAL_COMPLETE_REQUIRED,
    REASON_RISK_BENIGN,
    REASON_RISK_CLEARLY_HARMFUL,
    REASON_RISK_MORALLY_NUANCED,
    REASON_RISK_POTENTIALLY_HARMFUL,
    REASON_RISK_SENSITIVE,
    REASON_SAFE_COMPLETE_ALLOWED,
    REASON_SAFE_COMPLETE_REQUIRED,
    REASON_SENSITIVE_ALLOWED,
)

# English audit strings for policy-layer reason codes (reports / UI).
_POLICY_REASON_DISPLAY: dict[str, str] = {
    REASON_HARD_VIOLATIONS: "Hard violations present: bounds require REFUSE.",
    REASON_RISK_CLEARLY_HARMFUL: "Risk category clearly_harmful or operational risk HIGH: REFUSE required.",
    REASON_RISK_POTENTIALLY_HARMFUL: "Risk category potentially_harmful: gray zone (min NORMAL, max may be SAFE_COMPLETE).",
    REASON_RISK_SENSITIVE: "Risk category sensitive: governed framing may be required.",
    REASON_RISK_MORALLY_NUANCED: "Risk category morally_nuanced: governed framing may be required.",
    REASON_RISK_BENIGN: "Risk category benign: only NORMAL_COMPLETE within bounds.",
    REASON_DOMAIN_REGULATED: "Active domain overlay is sensitive/regulated: stricter bounds apply.",
    REASON_SAFE_COMPLETE_REQUIRED: "Policy requires SAFE_COMPLETE as minimum action.",
    REASON_SAFE_COMPLETE_ALLOWED: "SAFE_COMPLETE is allowed but not required (default may be NORMAL_COMPLETE).",
    REASON_NORMAL_COMPLETE_REQUIRED: "Only NORMAL_COMPLETE satisfies upper bound.",
    REASON_SENSITIVE_ALLOWED: "Factual intent without ambiguity: NORMAL_COMPLETE allowed within sensitive/moral band.",
    "safe_complete_required_high_actionability": (
        "Actionability axis HIGH: epistemic escalation — SAFE_COMPLETE required (responsible framing)."
    ),
    "hard_violation_downgraded_to_safe_complete": (
        "Hard violation present but bounded conditions preserved SAFE_COMPLETE instead of REFUSE "
        "(decision_service._handle_hard_violations branch 3). The hard-violation delivery guard "
        "(DeliberationRunner.enforce_no_rejected_draft_delivery) still regenerates/re-validates the "
        "delivered text before it ships — this code marks the branch, not the delivered content."
    ),
}


def _trace_dict(t: dict[str, Any]) -> dict[str, Any]:
    tj = t.get("trace_json", "{}")
    if isinstance(tj, str):
        try:
            parsed = json.loads(tj)
            return parsed if isinstance(parsed, dict) else {}
        except Exception:
            return {}
    return tj if isinstance(tj, dict) else {}


def _parse_debug_payload(ev: dict[str, Any]) -> dict[str, Any]:
    pj = ev.get("payload_json")
    if pj is None:
        pj = ev.get("payload")
    if isinstance(pj, str):
        try:
            parsed = json.loads(pj)
            return parsed if isinstance(parsed, dict) else {}
        except Exception:
            return {}
    return pj if isinstance(pj, dict) else {}


def _explain_policy_codes(codes: list[str]) -> list[str]:
    out: list[str] = []
    for c in codes:
        if not c:
            continue
        disp = _POLICY_REASON_DISPLAY.get(c)
        if disp:
            out.append(f"{c}: {disp}")
        else:
            out.append(f"{c}: (see safe_complete_policy.compute_action_bounds)")
    return out


def extract_pre_policy_from_traces(traces: list[dict[str, Any]] | None) -> dict[str, Any] | None:
    """Return structured PRE_POLICY snapshot from persisted decision_traces, or None."""
    if not traces:
        return None
    for t in traces:
        if (t.get("stage") or "").strip().upper() != "PRE_POLICY":
            continue
        td = _trace_dict(t)
        pmin = (td.get("policy_min_action") or "").strip()
        pmax = (td.get("policy_max_action") or "").strip()
        prc = list(td.get("policy_reason_codes") or [])
        fa = (td.get("final_action") or "").strip()
        path = (td.get("path") or "").strip()
        rc = (td.get("risk_category") or "").strip()
        rs = td.get("risk_score")
        return {
            "policy_min_action": pmin,
            "policy_max_action": pmax,
            "policy_reason_codes": prc,
            "pre_policy_final_action": fa,
            "pre_policy_path": path,
            "risk_category": rc,
            "risk_score": rs,
            "decision_reason": (td.get("decision_reason") or "").strip(),
            "mapped_reason_codes": list(td.get("reason_codes") or []),
            "winning_rule": (td.get("winning_rule") or "").strip(),
        }
    return None


def _gating_events_from_debug(debug_events: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    if not debug_events:
        return out
    for ev in sorted(debug_events, key=lambda e: e.get("created_at") or 0):
        payload = _parse_debug_payload(ev)
        msg = (payload.get("message") or "").strip()
        if msg not in ("SAFE_COMPLETE_GATING_PASSED", "SAFE_COMPLETE_DOWNGRADED"):
            continue
        data = payload.get("data")
        if not isinstance(data, dict):
            data = {}
        out.append(
            {
                "message": msg,
                "hypothesis_id": (payload.get("hypothesisId") or payload.get("hypothesis_id") or "").strip(),
                "data": data,
            }
        )
    return out


def _narrative_for_gating_event(ev: dict[str, Any]) -> str:
    msg = ev.get("message", "")
    data = ev.get("data") or {}
    if msg == "SAFE_COMPLETE_GATING_PASSED":
        rsn = (data.get("reason") or "").strip()
        if rsn == "overlay_sensitive":
            return (
                "SAFE_COMPLETE gating: **kept** SAFE_COMPLETE because the active overlay is sensitive/regulated "
                "(governed framing required)."
            )
        if rsn == "signals_misuse_and_actionability_medium_plus":
            return (
                "SAFE_COMPLETE gating: **kept** SAFE_COMPLETE because misuse_plausibility and actionability_risk "
                "are both at least MEDIUM (structured signals)."
            )
        return f"SAFE_COMPLETE gating: **kept** SAFE_COMPLETE ({json.dumps(data, ensure_ascii=False)})."
    if msg == "SAFE_COMPLETE_DOWNGRADED":
        return (
            "SAFE_COMPLETE gating: **downgraded** SAFE_COMPLETE → NORMAL_COMPLETE because the domain is not "
            "regulated under this rule, structured misuse/actionability signals are below MEDIUM, and "
            "risk_policy_action is ALLOW; policy prefers NORMAL_COMPLETE to avoid over-governance."
        )
    return f"{msg}: {json.dumps(data, ensure_ascii=False)}"


def build_policy_gating_observability(
    traces: list[dict[str, Any]] | None,
    debug_events: list[dict[str, Any]] | None,
) -> dict[str, Any]:
    """
    Build a structured summary for UI and markdown.

    Returns:
      - pre_policy: PRE_POLICY snapshot or None
      - policy_code_explanations: expanded lines for policy_reason_codes
      - gating_events: parsed SAFE_COMPLETE gating debug events
      - narrative_bullets: combined English lines
      - has_policy_gating_data: True if anything useful is present
    """
    out: dict[str, Any] = {
        "pre_policy": None,
        "policy_code_explanations": [],
        "gating_events": [],
        "narrative_bullets": [],
        "has_policy_gating_data": False,
    }
    pre = extract_pre_policy_from_traces(traces)
    if pre:
        out["pre_policy"] = pre
        prc = list(pre.get("policy_reason_codes") or [])
        out["policy_code_explanations"] = _explain_policy_codes(prc)
        pfa = pre.get("pre_policy_final_action") or ""
        pmin = pre.get("policy_min_action") or ""
        pmax = pre.get("policy_max_action") or ""
        ppath = pre.get("pre_policy_path") or ""
        rc = pre.get("risk_category") or ""
        rs = pre.get("risk_score")
        out["narrative_bullets"].append(
            f"PRE_POLICY (safe_complete_policy.decide_final_action): "
            f"risk_category={rc!r}, risk_score={rs!r} → bounds min={pmin!r}, max={pmax!r}; "
            f"derived pre-action={pfa!r}, pre-path={ppath!r}."
        )
        for line in out["policy_code_explanations"]:
            out["narrative_bullets"].append(f"Policy reason: {line}")

    gating = _gating_events_from_debug(debug_events or [])
    out["gating_events"] = gating
    for ge in gating:
        out["narrative_bullets"].append(_narrative_for_gating_event(ge))

    out["has_policy_gating_data"] = bool(out["pre_policy"] or out["gating_events"])
    return out


def render_policy_gating_observability_markdown(obs: dict[str, Any]) -> str:
    """Markdown section for export."""
    if not obs.get("has_policy_gating_data"):
        return ""
    lines = [
        "---",
        "",
        "## Policy bounds (PRE_POLICY) and SAFE_COMPLETE gating",
        "",
        "> From `safe_complete_policy` (bounds + `decide_final_action`) and `safe_complete_gating` "
        "(optional downgrade of SAFE_COMPLETE). No inference from user text.",
        "",
    ]
    pre = obs.get("pre_policy")
    if isinstance(pre, dict) and pre:
        lines.append("### PRE_POLICY snapshot")
        lines.append("")
        lines.append("| Field | Value |")
        lines.append("|-------|-------|")
        for k in (
            "risk_category",
            "risk_score",
            "policy_min_action",
            "policy_max_action",
            "pre_policy_final_action",
            "pre_policy_path",
            "winning_rule",
        ):
            if k in pre and pre[k] not in (None, ""):
                lines.append(f"| `{k}` | `{pre[k]}` |")
        lines.append("")
        prc = pre.get("policy_reason_codes") or []
        if prc:
            lines.append("**policy_reason_codes:** " + ", ".join(str(x) for x in prc))
            lines.append("")
        for ex in obs.get("policy_code_explanations") or []:
            lines.append(f"- {ex}")
        lines.append("")

    if obs.get("gating_events"):
        lines.append("### SAFE_COMPLETE gating (runtime)")
        lines.append("")
        for i, ge in enumerate(obs["gating_events"], 1):
            msg = ge.get("message", "")
            hid = ge.get("hypothesis_id", "")
            lines.append(f"{i}. **{msg}**" + (f" `{hid}`" if hid else ""))
            dat = ge.get("data")
            if isinstance(dat, dict) and dat:
                lines.append("")
                lines.append("```json")
                lines.append(json.dumps(dat, indent=2, ensure_ascii=False))
                lines.append("```")
            lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def policy_gating_to_io_annotations(obs: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    """Flow-graph style I/O rows for a synthetic node."""
    inputs: list[dict[str, Any]] = []
    outputs: list[dict[str, Any]] = []
    pre = obs.get("pre_policy")
    if isinstance(pre, dict) and pre:
        inputs.append({"label": "risk_category", "source": str(pre.get("risk_category") or "")})
        inputs.append(
            {"label": "bounds", "source": f"min={pre.get('policy_min_action')} max={pre.get('policy_max_action')}"}
        )
        outputs.append({"label": "pre_action", "value": str(pre.get("pre_policy_final_action") or "")})
        outputs.append({"label": "pre_path", "value": str(pre.get("pre_policy_path") or "")})
    for ge in obs.get("gating_events") or []:
        msg = ge.get("message", "")
        data = ge.get("data") or {}
        if msg == "SAFE_COMPLETE_GATING_PASSED":
            outputs.append({"label": "gating", "value": f"kept SAFE_COMPLETE ({data.get('reason', '')})"})
        elif msg == "SAFE_COMPLETE_DOWNGRADED":
            outputs.append({"label": "gating", "value": "downgraded SAFE_COMPLETE → NORMAL_COMPLETE"})
    return {"inputs": inputs, "outputs": outputs}
