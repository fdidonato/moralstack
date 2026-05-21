"""
MoralStack UI - FastAPI web dashboard.

Requires: pip install moralstack[ui]
Auth: MORALSTACK_UI_USERNAME, MORALSTACK_UI_PASSWORD (form-based login)
"""

import json
import os
import secrets
import sys
import time
from collections import defaultdict
from pathlib import Path
from typing import Any, cast

from dotenv import load_dotenv
from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.responses import Response
from starlette.templating import Jinja2Templates

from moralstack.observability.config import get_db_path
from moralstack.observability.service import get_obs
from moralstack.observability.sinks.sqlite_sink import delete_request, delete_run
from moralstack.orchestration.orchestration_event_taxonomy import (
    COMPLIANCE_DRAFT_REGENERATED,
    COMPLIANCE_DRAFT_REUSED,
    COMPLIANCE_MATCH_DOWNGRADED,
    MODULE_DEFERRED_TO_COMPLIANCE,
    PROXY_OUTPUT_FINALIZED,
)
from moralstack.reports.benchmark_report_loader import (
    get_benchmark_result_by_request_id,
    get_questions_by_category,
    load_benchmark_report,
)
from moralstack.reports.conversation_export import export_conversation_to_markdown
from moralstack.reports.markdown_export import (
    build_benchmark_report_markdown,
    export_request_markdown,
    export_run_benchmark_markdown,
)
from moralstack.reports.orchestrator_observability import (
    build_orchestrator_observability,
    orchestrator_observability_to_io_annotations,
)
from moralstack.reports.runtime_decisions import (
    build_runtime_decision_observability,
    enrich_llm_call_for_ui,
)
from moralstack.utils.env_loader import _purge_empty_env_vars


class _ReadStoreProxy:
    """
    Resolve SqliteReadStore accessors at call time via get_obs().

    The observability singleton (and its read_store) can be replaced in tests;
    module-level aliases must not capture a stale read_store from import time.
    """

    __slots__ = ()

    def __getattr__(self, name: str) -> Any:
        def _forward(*args: Any, **kwargs: Any) -> Any:
            return getattr(get_obs().read_store, name)(*args, **kwargs)

        return _forward


_rs = _ReadStoreProxy()
get_all_runs = _rs.get_all_runs
get_request_domains = _rs.get_request_domains
get_runs_page = _rs.get_runs_page
get_debug_events_for_request = _rs.get_debug_events_for_request
get_decision_traces_for_request = _rs.get_decision_traces_for_request
get_llm_calls_for_request = _rs.get_llm_calls_for_request
get_orchestration_events_for_request = _rs.get_orchestration_events_for_request
get_request = _rs.get_request
get_requests_for_run = _rs.get_requests_for_run
get_run = _rs.get_run

# Step 13: multi-turn conversation observability accessors.
get_requests_for_conversation = _rs.get_requests_for_conversation
get_conversation_states = _rs.get_conversation_states
get_ledger_events_for_conversation = _rs.get_ledger_events_for_conversation
get_session_store_events_for_conversation = _rs.get_session_store_events_for_conversation
get_proxy_request_events_for_conversation = _rs.get_proxy_request_events_for_conversation
get_conversation_ids_for_run = _rs.get_conversation_ids_for_run
get_conversation_overview = _rs.get_conversation_overview

_root = Path(__file__).resolve().parent.parent.parent
_env_path = _root / ".env"

_dotenv_loaded = False
try:
    if _env_path.is_file():
        load_dotenv(str(_env_path), override=True)
        _dotenv_loaded = True
    else:
        print(f"moralstack-ui: WARNING — .env not found at {_env_path}", file=sys.stderr)
except ImportError:
    if _env_path.is_file():
        for line in _env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            key, val = key.strip(), val.strip()
            if len(val) >= 2 and val[0] == val[-1] and val[0] in ('"', "'"):
                val = val[1:-1]
            if val:
                os.environ[key] = val
        _dotenv_loaded = True
        print(
            "moralstack-ui: python-dotenv not installed, used manual .env parser",
            file=sys.stderr,
        )
    else:
        print(
            f"moralstack-ui: WARNING — python-dotenv not installed and .env " f"not found at {_env_path}",
            file=sys.stderr,
        )

_purge_empty_env_vars()


def _sanitize(val: str | None) -> str:
    out = (val or "").replace("\ufeff", "").strip().replace("\r", "").replace("\n", "")
    if len(out) >= 2 and out[0] == out[-1] and out[0] in ('"', "'"):
        out = out[1:-1]
    return out


_UI_USERNAME = _sanitize(os.getenv("MORALSTACK_UI_USERNAME"))
_UI_PASSWORD = _sanitize(os.getenv("MORALSTACK_UI_PASSWORD"))

if _UI_USERNAME and _UI_PASSWORD:
    print(f"moralstack-ui: credentials loaded (user={_UI_USERNAME!r})", file=sys.stderr)
else:
    print(
        "moralstack-ui: WARNING — UI credentials not configured, login will fail",
        file=sys.stderr,
    )

# In-memory session store: token -> expiry_time (simple, no server restart persistence)
_SESSIONS: dict[str, float] = {}
_SESSION_TTL = 86400  # 24 hours
_SESSION_COOKIE = "moralstack_session"


def _parse_trace_json(trace_record: dict[str, Any]) -> dict[str, Any]:
    """Parse trace_json from a decision_traces row into a dict."""
    tj = trace_record.get("trace_json")
    if tj is None:
        return {}
    if isinstance(tj, str):
        try:
            parsed: Any = json.loads(tj)
            return cast(dict[str, Any], parsed) if isinstance(parsed, dict) else {}
        except Exception:
            return {}
    if isinstance(tj, dict):
        return cast(dict[str, Any], tj)
    return {}


def _parse_json_field(value: Any) -> Any:
    """Best-effort JSON parsing; returns ``None`` when input is unparseable."""
    if value is None:
        return None
    if isinstance(value, (dict, list)):
        return value
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except (ValueError, TypeError):
        return None


def _enrich_event_row(row: dict[str, Any], json_keys: tuple[str, ...]) -> dict[str, Any]:
    """Return a shallow copy of ``row`` with the listed JSON columns pre-parsed."""
    out = dict(row)
    for key in json_keys:
        if key in out:
            out[f"{key}__parsed"] = _parse_json_field(out.get(key))
    return out


def _relevant_principles_from_traces(traces: list[dict[str, Any]]) -> dict[str, Any]:
    """Extract relevant principles (at start: id, title, level), triggered
    (same shape), and policy context."""
    out: dict[str, Any] = {
        "relevant_at_start": [],  # List of {"id", "title", "level"} for principles at start
        "triggered": [],  # List of {"id", "title", "level"} for principles in final decision
        "overlay_applied": "",
        "reason_codes": [],
        "winning_rule": "",
        "hard_violation_codes": [],
    }
    if not traces:
        return out
    # Relevant principles identified at the start (stage RELEVANT_PRINCIPLES)
    relevant_detail: list[dict[str, Any]] = []
    for t in traces:
        stage = (t.get("stage") or "").strip().upper()
        if stage == "RELEVANT_PRINCIPLES":
            td = _parse_trace_json(t)
            raw_list = td.get("relevant_principles")
            if raw_list and isinstance(raw_list, list):
                for p in raw_list:
                    if isinstance(p, dict):
                        relevant_detail.append(
                            {
                                "id": (p.get("id") or "").strip(),
                                "title": (p.get("title") or "").strip(),
                                "level": (p.get("level") or "soft").strip().lower(),
                            }
                        )
                    else:
                        relevant_detail.append({"id": str(p), "title": "", "level": ""})
            else:
                for pid in td.get("relevant_principle_ids") or []:
                    relevant_detail.append({"id": str(pid), "title": "", "level": ""})
            break
    out["relevant_at_start"] = relevant_detail
    # Build lookup by id for title/level
    by_id = {p["id"]: p for p in relevant_detail if p.get("id")}

    # Final trace: triggered principle IDs and policy context (prefer last FINAL stage row)
    final_trace = None
    for t in traces:
        stage = (t.get("stage") or "").strip().upper()
        if stage == "FINAL":
            final_trace = t
    if final_trace is None:
        final_trace = traces[-1] if traces else {}
    td = _parse_trace_json(final_trace)
    triggered_ids = list(td.get("policy_principle_ids") or [])
    out["triggered"] = [by_id.get(pid, {"id": pid, "title": "", "level": ""}) for pid in triggered_ids]
    out["overlay_applied"] = (td.get("overlay_applied") or "").strip()
    out["reason_codes"] = list(td.get("reason_codes") or [])
    out["winning_rule"] = (td.get("winning_rule") or "").strip()
    out["hard_violation_codes"] = list(td.get("hard_violation_codes") or [])
    return out


def _build_final_decision_card(traces: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Extract final decision details from the FINAL trace."""
    final_trace = None
    risk_trace = None
    for t in traces:
        stage = (t.get("stage") or "").strip().upper()
        if stage == "FINAL":
            final_trace = t
        elif stage == "RISK_ASSESSMENT":
            risk_trace = t

    if not final_trace:
        return None

    td = _parse_trace_json(final_trace)
    rd = _parse_trace_json(risk_trace) if risk_trace else {}

    return {
        "final_action": td.get("final_action"),
        "stop_reason": td.get("stop_reason"),
        "path": td.get("path"),
        "total_cycles": td.get("total_cycles"),
        "reason_codes": td.get("reason_codes") or [],
        "winning_rule": td.get("winning_rule"),
        "risk_score": td.get("risk_score") if td.get("risk_score") is not None else rd.get("risk_score"),
        "risk_category": td.get("risk_category") or rd.get("risk_category"),
        "activated_signals": td.get("activated_signals") or rd.get("activated_signals") or [],
        "hard_violation_codes": td.get("hard_violation_codes") or [],
        "sim_semantic_expected_harm": td.get("sim_semantic_expected_harm"),
        "sim_expected_valence": td.get("sim_expected_valence"),
        "why_not_refuse": td.get("why_not_refuse"),
        "why_not_safe_complete": td.get("why_not_safe_complete"),
        "why_not_normal_complete": td.get("why_not_normal_complete"),
        "overlay_applied": td.get("overlay_applied"),
        "modules_skipped": td.get("modules_skipped") or [],
        "duration_ms": td.get("duration_ms"),
        "policy_min_action": (td.get("policy_min_action") or "").strip(),
        "policy_max_action": (td.get("policy_max_action") or "").strip(),
    }


def _build_module_io_annotations(call: dict[str, Any]) -> dict[str, Any]:
    """Build input/output annotations for a module call."""
    module = (call.get("module") or "").lower()
    cycle = call.get("cycle") or 0
    summary = call.get("parsed_summary_json")
    if isinstance(summary, str):
        try:
            summary = json.loads(summary)
        except Exception:
            summary = {}
    if not isinstance(summary, dict):
        summary = {}

    inputs: list[dict[str, Any]] = []
    outputs: list[dict[str, Any]] = []

    # Inputs logic
    if "risk" in module:
        inputs.append({"label": "prompt", "source": "user"})
    elif "policy" in module:
        if cycle == 0:
            inputs.append({"label": "risk", "source": "risk_estimator"})
            inputs.append({"label": "principles", "source": "constitution"})
        else:
            inputs.append({"label": "revision_guidance", "source": "critic"})
            inputs.append({"label": "risk", "source": "risk_estimator"})
    elif "critic" in module:
        inputs.append({"label": "draft", "source": "policy"})
        inputs.append({"label": "principles", "source": "constitution"})
        inputs.append({"label": "risk_context", "source": "risk_estimator"})
    elif "simulator" in module:
        inputs.append({"label": "draft", "source": "policy"})
        inputs.append({"label": "risk_context", "source": "risk_estimator"})
    elif "perspectives" in module:
        inputs.append({"label": "draft", "source": "policy"})
        inputs.append({"label": "risk_context", "source": "risk_estimator"})
    elif "hindsight" in module:
        inputs.append({"label": "draft", "source": "policy"})
        inputs.append({"label": "consequences", "source": "simulator"})
    elif "compliance" in module:
        inputs.append({"label": "developer_contract", "source": "user"})
        inputs.append({"label": "draft", "source": "policy"})
        inputs.append({"label": "prompt", "source": "user"})
    elif "constitution" in module:
        inputs.append({"label": "domain", "source": "risk_estimator"})

    # Outputs logic
    action = (call.get("action") or "").lower()
    if "risk" in module and action == "calibration_guard":
        # Calibration guard synthetic call — show caps applied
        inputs.clear()
        raw = call.get("raw_response") or ""
        try:
            guard_data = json.loads(raw) if raw else {}
        except Exception:
            guard_data = {}
        guard_notes = guard_data.get("_calibration_guard_notes") or guard_data.get("notes") or []
        req_type = guard_data.get("_calibration_guard_request_type") or guard_data.get("request_type") or ""
        if req_type:
            inputs.append({"label": "trigger", "source": req_type})
        if isinstance(guard_notes, list):
            for note in guard_notes:
                outputs.append({"label": "cap", "value": str(note)})
        if not guard_notes:
            # Fallback: parse rationale for calibration info
            rationale = guard_data.get("rationale") or ""
            if "[calibration_guard]" in rationale:
                cap_text = rationale.split("[calibration_guard]")[-1].strip()
                if cap_text:
                    outputs.append({"label": "cap", "value": cap_text[:80]})
    elif "risk" in module:
        # For mini-estimators, parsed_summary_json only has {mini_estimator, estimation_mode}.
        # The actual LLM output is in raw_response — parse it for richer annotations.
        raw_text = call.get("raw_response") or ""
        try:
            raw_data = json.loads(raw_text) if raw_text else {}
        except Exception:
            raw_data = {}
        # Merge: use summary fields if present, then raw_data for mini-estimator details
        risk_data = {**raw_data, **{k: v for k, v in summary.items() if k not in ("mini_estimator", "estimation_mode")}}

        if "risk_score" in risk_data:
            outputs.append({"label": "score", "value": risk_data["risk_score"]})
        if "risk_category" in risk_data:
            outputs.append({"label": "cat", "value": risk_data["risk_category"]})
        # Show key calibration-relevant fields from mini-estimators
        if action == "estimate_intent":
            for key in ("request_type", "intent_to_harm", "requested_instructions", "intent_operational"):
                if key in risk_data:
                    outputs.append({"label": key.replace("_", " "), "value": risk_data[key]})
        elif action == "estimate_operational":
            for key in ("operational_risk", "risk_score", "risk_policy_action"):
                if key in risk_data:
                    outputs.append({"label": key.replace("_", " "), "value": risk_data[key]})
        elif action == "estimate_signals":
            hc_keys = _positive_calibration_signal_keys(risk_data, _CALIBRATION_HARMFUL_COUNT_KEYS)
            q13_on = _positive_calibration_signal_keys(risk_data, (_CALIBRATION_Q13_TOPIC_KEY,))
            rq_on = _positive_calibration_signal_keys(risk_data, _CALIBRATION_REPUTATIONAL_TOPIC_KEYS)
            outputs.append(
                {
                    "label": "harm signals",
                    "value": f"{len(hc_keys)} positive (harmful_count keys)",
                }
            )
            topic_bits: list[str] = []
            if q13_on:
                topic_bits.append("q13")
            if rq_on:
                topic_bits.append(f"q14–q16:{len(rq_on)}")
            topic_summary = ", ".join(topic_bits) if topic_bits else "none"
            outputs.append(
                {
                    "label": "topic signals",
                    "value": f"{topic_summary} (q13–q16; excluded from harmful_count)",
                }
            )
    elif "policy" in module:
        outputs.append({"label": "draft", "value": "text"})
    elif "critic" in module:
        if "violations" in summary:
            outputs.append({"label": "violations", "value": summary["violations"]})
        if "decision" in summary:
            outputs.append({"label": "decision", "value": summary["decision"]})
    elif "simulator" in module:
        if "semantic_expected_harm" in summary:
            outputs.append({"label": "harm", "value": summary["semantic_expected_harm"]})
        if "expected_valence" in summary:
            outputs.append({"label": "valence", "value": summary["expected_valence"]})
    elif "perspectives" in module:
        if "approval_scores" in summary:
            avgs = summary.get("approval_scores", {})
            if isinstance(avgs, dict) and avgs:
                # just show avg of avgs or count
                vals = [v for v in avgs.values() if isinstance(v, (int, float))]
                if vals:
                    avg = sum(vals) / len(vals)
                    outputs.append({"label": "avg_approval", "value": str(round(avg, 2))})
        if "recommendation" in summary:
            outputs.append({"label": "rec", "value": summary["recommendation"]})
    elif "hindsight" in module:
        if "score" in summary:
            outputs.append({"label": "score", "value": summary["score"]})
    elif "compliance" in module:
        raw = call.get("raw_response") or ""
        try:
            verdict_data = json.loads(raw) if raw else {}
        except Exception:
            verdict_data = {}
        verdict = verdict_data.get("verdict") or summary.get("verdict") or "—"
        outputs.append({"label": "verdict", "value": verdict})
    elif "constitution" in module:
        if "count" in summary:
            outputs.append({"label": "principles", "value": summary["count"]})

    result: dict[str, Any] = {"inputs": inputs, "outputs": outputs}
    if "constitution" in module and call.get("_constitution_phase"):
        result["phase_hint"] = call["_constitution_phase"]
    return result


# Deliberation-cycle visual tiers (cycle >= 1).  SEQ_SIMULATOR=3 and SEQ_PERSPECTIVES=4
# share a tier because they run in parallel via executor.submit.
_SEQ_TO_VISUAL_TIER: dict[int, int] = {
    1: 1,  # policy
    2: 2,  # critic
    3: 3,  # simulator  } parallel
    4: 3,  # perspectives}
    5: 4,  # hindsight
    6: 5,  # refusal/finalize
}

# Cycle-0 pipeline sequences (constitution -10, risk -9, calibration -8, compliance -5, …).
_CYCLE0_SEQ_TO_VISUAL_TIER: dict[int, int] = {
    -10: 0,  # constitution domain prefilter
    -9: 1,  # risk mini-estimators (parallel)
    -8: 2,  # calibration guard
    -5: 3,  # DCCL evaluate
    -4: 4,  # draft revalidation (Case 2)
    0: 1,  # speculative policy (parallel with risk)
    1: 5,  # compliance-regenerate policy
}


def _visual_tier_for_call(call: dict[str, Any]) -> int:
    """Map a call to a visual tier for grouping parallel modules."""
    seq_raw = call.get("sequence_in_cycle")
    if seq_raw is None:
        return 9999
    seq = int(seq_raw)
    cycle = int(call.get("cycle") or 0)
    if cycle == 0:
        vt = _CYCLE0_SEQ_TO_VISUAL_TIER.get(seq)
        return vt if vt is not None else seq
    vt = _SEQ_TO_VISUAL_TIER.get(seq)
    return vt if vt is not None else seq


def _call_tier_sort_key(call: dict[str, Any]) -> tuple[Any, ...]:
    """Primary sort: sequence_in_cycle, then id, then started_at (tie-break only)."""
    seq = call.get("sequence_in_cycle")
    if seq is None:
        seq = 9999
    return (seq, call.get("id") or 0, call.get("started_at") or 0)


def _group_calls_into_tiers_and_enrich(calls: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    """Group calls into tiers reflecting architectural execution order.

    Primary ordering uses ``(sequence_in_cycle, id, started_at)`` — never
    ``started_at`` alone.  Calls sharing a visual tier (e.g. simulator +
    perspectives, or risk + speculative policy) are grouped as parallel.

    Calls without ``sequence_in_cycle`` fall back to time-overlap grouping
    (legacy data only) and are appended after sequenced tiers.

    Legacy data fix: when ``sequence_in_cycle`` is NULL for deliberation
    calls (cycle >= 1), infer it from the module name.
    """
    if not calls:
        return []

    _MODULE_TO_INFERRED_SEQ: dict[str, int] = {
        "policy": 1,
        "critic": 2,
        "simulator": 3,
        "perspectives": 4,
        "hindsight": 5,
    }
    for c in calls:
        if c.get("sequence_in_cycle") is None and (c.get("cycle") or 0) >= 1:
            mod = (c.get("module") or "").lower()
            inferred = _MODULE_TO_INFERRED_SEQ.get(mod)
            if inferred is not None:
                c["sequence_in_cycle"] = inferred

    sequenced = [c for c in calls if c.get("sequence_in_cycle") is not None]
    unsequenced = [c for c in calls if c.get("sequence_in_cycle") is None]

    by_vtier: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for c in sequenced:
        by_vtier[_visual_tier_for_call(c)].append(c)

    merged_seq_tiers: list[list[dict[str, Any]]] = []
    for vtier in sorted(by_vtier.keys()):
        tier_calls = sorted(
            by_vtier[vtier],
            key=lambda c: (c.get("id") or 0, c.get("started_at") or 0),
        )
        merged_seq_tiers.append(tier_calls)

    # Rare fallback: legacy rows without sequence_in_cycle.
    unsorted = sorted(unsequenced, key=lambda c: (c.get("started_at") or 0, c.get("id") or 0))
    time_tiers: list[list[dict[str, Any]]] = []
    current_tier: list[dict[str, Any]] = []
    current_max_end = 0
    for call in unsorted:
        s = call.get("started_at") or 0
        e = s + (call.get("duration_ms") or 0)
        if not current_tier:
            current_tier.append(call)
            current_max_end = e
        elif s < current_max_end:
            current_tier.append(call)
            current_max_end = max(current_max_end, e)
        else:
            time_tiers.append(current_tier)
            current_tier = [call]
            current_max_end = e
    if current_tier:
        time_tiers.append(current_tier)

    all_tiers = merged_seq_tiers + time_tiers

    # ── Enrich with timing info ──────────────────────────────────────────
    processed: list[list[dict[str, Any]]] = []
    for tier in all_tiers:
        if len(tier) > 1:
            min_start = min((c.get("started_at") or 0) for c in tier)
            max_end = max((c.get("started_at") or 0) + (c.get("duration_ms") or 0) for c in tier)
            tier_duration = max(max_end - min_start, 1)
            for call in tier:
                start = call.get("started_at") or 0
                dur = call.get("duration_ms") or 0
                call["tier_relative_start_pct"] = (start - min_start) / tier_duration * 100
                call["tier_relative_width_pct"] = dur / tier_duration * 100
                call["tier_total_ms"] = tier_duration
                call["is_parallel_tier"] = True
        else:
            for call in tier:
                call["is_parallel_tier"] = False
        processed.append(tier)

    return processed


def _compute_connector_labels(tiers: list[list[dict[str, Any]]]) -> list[str | None]:
    """Return a human-readable label for the pipe AFTER each tier.

    len(result) == len(tiers) - 1.  ``None`` means no label for that pipe.
    """
    labels: list[str | None] = []
    for i in range(len(tiers) - 1):
        src_modules = {(c.get("module") or "").lower() for c in tiers[i]}
        dst_modules = {(c.get("module") or "").lower() for c in tiers[i + 1]}
        dst_actions = {(c.get("action") or "").lower() for c in tiers[i + 1]}
        parts: list[str] = []
        if "policy" in src_modules:
            if dst_modules & {"critic", "simulator", "perspectives", "hindsight"}:
                parts.append("draft")
        if "risk_estimator" in src_modules or "constitution" in src_modules:
            if "calibrate" in dst_actions:
                parts.append("merged signals")
            elif dst_modules & {"policy"}:
                parts.append("risk + principles")
            elif dst_modules & {"critic", "simulator", "perspectives"}:
                parts.append("risk context")
        if "critic" in src_modules:
            if dst_modules & {"simulator", "perspectives"}:
                parts.append("gate: proceed")
        if any("compliance" in m for m in dst_modules):
            parts.append("DCCL")
        labels.append(" · ".join(parts) if parts else None)
    return labels


def _tag_constitution_phases(calls: list[dict[str, Any]]) -> None:
    """Tag constitution prefilter calls so the UI can distinguish routing vs deliberation."""
    prefilter_calls = sorted(
        [
            c
            for c in calls
            if "constitution" in (c.get("module") or "").lower() and "domain_prefilter" in (c.get("action") or "").lower()
        ],
        key=lambda c: c.get("started_at") or 0,
    )
    for idx, call in enumerate(prefilter_calls):
        if idx == 0:
            call["_constitution_phase"] = "domain prefilter (risk routing)"
        else:
            call["_constitution_phase"] = "domain prefilter (deliberation retrieval)"


def _build_compliance_card(orchestration_events: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Build the DCCL compliance card from orchestration events for the request page."""
    compliance_events = [
        e for e in orchestration_events if (e.get("event_type") or "").startswith("COMPLIANCE_LAYER_VERDICT")
    ]
    if not compliance_events:
        return None

    verdict_event = compliance_events[0]
    payload = _parse_json_field(verdict_event.get("payload_json")) or _parse_json_field(verdict_event.get("payload"))
    if not isinstance(payload, dict):
        payload = {}
    decision = verdict_event.get("decision") or payload.get("decision") or "—"

    if decision == "NO_CONTRACT":
        return None

    deferred = [e for e in orchestration_events if (e.get("event_type") or "") == MODULE_DEFERRED_TO_COMPLIANCE]
    deferred_modules: list[dict[str, str]] = []
    for d in deferred:
        dp = _parse_json_field(d.get("payload_json")) or _parse_json_field(d.get("payload")) or {}
        mod = (dp.get("module") or "").strip()
        if mod:
            deferred_modules.append({"module": mod, "skip_reason": "contract_authorized_rule_execution"})

    action_excerpt = ""
    for event_type in (COMPLIANCE_DRAFT_REUSED, COMPLIANCE_DRAFT_REGENERATED):
        for e in orchestration_events:
            if (e.get("event_type") or "") == event_type:
                ep = _parse_json_field(e.get("payload_json")) or _parse_json_field(e.get("payload")) or {}
                action_excerpt = (ep.get("action_excerpt") or "").strip()
                if action_excerpt:
                    break
        if action_excerpt:
            break

    return {
        "decision": decision,
        "evaluation_path": payload.get("evaluation_path", "—"),
        "matched_rule_id": payload.get("matched_rule_id"),
        "matched_rule_summary": payload.get("matched_rule_summary"),
        "action_payload_summary": action_excerpt or payload.get("matched_rule_summary"),
        "safety_override_reason": payload.get("safety_override_reason"),
        "confidence": payload.get("confidence"),
        "speculative_draft_validated": payload.get("speculative_draft_validated"),
        "draft_match_method": payload.get("draft_match_method"),
        "draft_match_confidence": payload.get("draft_match_confidence"),
        "degraded": payload.get("degraded"),
        "degraded_reason": payload.get("degraded_reason"),
        "contract_hash": payload.get("contract_hash"),
        "rationale": payload.get("rationale_excerpt"),
        "modules_deferred": [m["module"] for m in deferred_modules],
        "modules_deferred_detail": deferred_modules,
    }


def _compliance_stage_payload_from_traces(traces: list[dict[str, Any]]) -> dict[str, Any]:
    """Return stage_payload from the COMPLIANCE_LAYER decision trace, if present."""
    for t in traces:
        if (t.get("stage") or "").strip().upper() != "COMPLIANCE_LAYER":
            continue
        td = _parse_trace_json(t)
        sp = td.get("stage_payload")
        if isinstance(sp, dict) and sp:
            return sp
    return {}


def _build_compliance_fast_path_panel(
    traces: list[dict[str, Any]],
    orchestration_events: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """Panel fields for COMPLIANCE_FAST_PATH from decision trace stage_payload."""
    sp = _compliance_stage_payload_from_traces(traces)
    if not sp:
        return None

    action_summary = (sp.get("action_payload_summary") or "").strip()
    if not action_summary:
        card = _build_compliance_card(orchestration_events)
        if card:
            action_summary = (card.get("action_payload_summary") or "").strip()

    return {
        "compliance_decision": sp.get("compliance_decision"),
        "matched_rule_id": sp.get("matched_rule_id"),
        "matched_rule_summary": sp.get("matched_rule_summary"),
        "action_payload_summary": action_summary,
        "evaluation_path": sp.get("evaluation_path"),
        "confidence": sp.get("confidence"),
        "contract_hash": sp.get("contract_hash"),
        "speculative_draft_validated": sp.get("speculative_draft_validated"),
        "draft_match_method": sp.get("draft_match_method"),
        "draft_match_confidence": sp.get("draft_match_confidence"),
        "degraded": sp.get("degraded"),
        "degraded_reason": sp.get("degraded_reason"),
    }


def _build_path_badge_info(orchestration_events: list[dict[str, Any]]) -> dict[str, Any]:
    """Human-readable pipeline path badge from compliance fast-path orchestration events."""
    event_types = {(e.get("event_type") or "") for e in orchestration_events}

    if COMPLIANCE_DRAFT_REUSED in event_types:
        return {
            "label": "Contract MATCH · draft riusato · moduli bypassati",
            "kind": "compliance_reused",
        }

    if COMPLIANCE_DRAFT_REGENERATED in event_types:
        degraded = False
        for e in orchestration_events:
            if (e.get("event_type") or "") != COMPLIANCE_DRAFT_REGENERATED:
                continue
            ep = _parse_json_field(e.get("payload_json")) or _parse_json_field(e.get("payload")) or {}
            reason = (ep.get("reason") or "").strip()
            if reason.startswith("degraded:"):
                degraded = True
                break
        label = "Contract MATCH · rigenerato"
        if degraded:
            label += " (degradato)"
        label += " · moduli bypassati"
        return {"label": label, "kind": "compliance_regenerated", "degraded": degraded}

    if COMPLIANCE_MATCH_DOWNGRADED in event_types:
        return {
            "label": "MATCH declassato → pipeline standard",
            "kind": "compliance_downgraded",
        }

    return {"label": "Pipeline deliberativa standard", "kind": "deliberative"}


def _build_proxy_output_info(orchestration_events: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Extract final_text_source from PROXY_OUTPUT_FINALIZED, if recorded."""
    for e in orchestration_events:
        if (e.get("event_type") or "") != PROXY_OUTPUT_FINALIZED:
            continue
        payload = _parse_json_field(e.get("payload_json")) or _parse_json_field(e.get("payload"))
        if not isinstance(payload, dict):
            payload = {}
        source = (payload.get("final_text_source") or "").strip()
        if source:
            return {
                "final_text_source": source,
                "final_action": payload.get("final_action") or e.get("decision"),
                "reused_governed_content": payload.get("reused_governed_content"),
                "final_response_length": payload.get("final_response_length"),
            }
    return None


def _pick_final_trace_row(traces: list[dict[str, Any]]) -> dict[str, Any]:
    """Prefer the last row with stage FINAL (not necessarily the last row by insert order)."""
    finals = [t for t in traces if (t.get("stage") or "").strip().upper() == "FINAL"]
    if finals:
        return finals[-1]
    return traces[-1] if traces else {}


def _execution_summary_from_request(
    traces: list[dict[str, Any]],
    llm_calls: list[dict[str, Any]],
) -> dict[str, Any]:
    """Build execution summary (path, total_cycles, converged) from traces and llm_calls."""
    path_val = ""
    total_cycles = 0
    converged = False
    final_trace = _pick_final_trace_row(traces)
    trace_json = final_trace.get("trace_json")
    if trace_json:
        try:
            td = json.loads(trace_json) if isinstance(trace_json, str) else trace_json
            path_val = (td.get("path") or "").strip()
            total_cycles = td.get("total_cycles") or 0
            stop = (td.get("stop_reason") or "").strip().upper()
            converged = stop == "CONVERGED"
            # Fast path: no deliberation and non-REFUSE outcome => converged for display.
            if total_cycles == 0:
                fa = (td.get("final_action") or "").strip().upper()
                if fa and fa != "REFUSE":
                    converged = True
        except Exception:
            pass
    if (path_val or "").upper() == "FAST_PATH":
        total_cycles = 0
    elif total_cycles <= 0 and llm_calls:
        total_cycles = max((c.get("cycle") or 0) for c in llm_calls)

    excluded_trace = None
    estimation_mode = ""
    for t in traces:
        tj = t.get("trace_json")
        if not tj:
            continue
        try:
            td = json.loads(tj) if isinstance(tj, str) else tj
            if (td.get("stage") or "").strip().upper() == "DOMAIN_EXCLUDED":
                excluded_trace = td
            if not estimation_mode and td.get("estimation_mode"):
                estimation_mode = td["estimation_mode"]
        except Exception:
            pass

    path_upper = (path_val or "").strip().upper()
    if path_upper == "FAST_PATH":
        path_badge = "FAST_PATH"
    elif path_upper == "COMPLIANCE_FAST_PATH":
        path_badge = "COMPLIANCE_FAST_PATH"
    else:
        path_badge = "DELIBERATIVE_PATH"

    return {
        "path": path_val or "—",
        "path_badge": path_badge,
        "total_cycles": total_cycles,
        "converged": converged,
        "domain_excluded": bool(excluded_trace),
        "excluded_domain": (excluded_trace.get("excluded_domain", "") or "") if excluded_trace else "",
        "estimation_mode": estimation_mode,
    }


def _llm_calls_by_cycle(
    llm_calls: list[dict[str, Any]],
) -> list[tuple[int | None, list[dict[str, Any]]]]:
    """Group llm_calls by cycle; return list of (cycle_num, calls) sorted by cycle."""
    by_cycle: dict[int | None, list[dict[str, Any]]] = defaultdict(list)
    for c in llm_calls:
        cy = c.get("cycle")
        by_cycle[cy].append(c)
    return sorted(by_cycle.items(), key=lambda x: x[0] if x[0] is not None else 0)


def _extract_mini_estimator_data(
    llm_calls: list[dict[str, Any]],
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], int]:
    """Extract raw_response data from the 3 mini-estimator calls.

    Returns (intent_data, signal_data, operational_data, last_risk_end_ms).
    """
    intent_data: dict[str, Any] = {}
    signal_data: dict[str, Any] = {}
    operational_data: dict[str, Any] = {}
    last_risk_end: int = 0
    for c in llm_calls:
        if (c.get("module") or "").lower() != "risk_estimator":
            continue
        c_end = (c.get("started_at") or 0) + int(c.get("duration_ms") or 0)
        if c_end > last_risk_end:
            last_risk_end = c_end
        action = (c.get("action") or "").lower()
        raw_text = c.get("raw_response") or ""
        try:
            parsed = json.loads(raw_text) if raw_text else {}
        except Exception:
            parsed = {}
        if action == "estimate_intent":
            intent_data = parsed
        elif action == "estimate_signals":
            signal_data = parsed
        elif action == "estimate_operational":
            operational_data = parsed
    return intent_data, signal_data, operational_data, last_risk_end


def _is_yes_ui(value: Any) -> bool:
    """Check if a value represents yes/true (for UI calibration analysis)."""
    if isinstance(value, bool):
        return value
    return str(value).lower().strip() in ("yes", "true", "1", "sì", "si")


# Keys whose affirmative answers increment calibration harmful_count (see
# moralstack.models.risk.calibration._parse_verification_questions).
_CALIBRATION_HARMFUL_COUNT_KEYS: tuple[str, ...] = (
    "q1_confidential",
    "q2_unauthorized",
    "q3_breach",
    "q4_fraud",
    "q5_physical",
    "q6_illegal_items",
    "q8_self_harm_suicide",
    "q9_cyber_malware",
    "q10_weapons_explosives_toxins",
    "q11_privacy_doxxing_stalking",
    "q12_medical_harmful",
    "q17_minor_exploitation",
)

# q13–q16 do not increment harmful_count. q14–q16 still enter the harm-signals
# calibration branch (reputational cluster).
_CALIBRATION_Q13_TOPIC_KEY = "q13_protected_class_targeting"
_CALIBRATION_REPUTATIONAL_TOPIC_KEYS: tuple[str, ...] = (
    "q14_reputational_harm",
    "q15_deceptive_online_campaign",
    "q16_harassment_smear_campaign",
)


def _positive_calibration_signal_keys(signal_data: dict[str, Any], keys: tuple[str, ...]) -> list[str]:
    return [k for k in keys if _is_yes_ui(signal_data.get(k, "no"))]


def _describe_calibration_path(
    intent_data: dict[str, Any],
    signal_data: dict[str, Any],
    operational_data: dict[str, Any],
    raw_score: float,
    final_score: float,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], str]:
    """Analyze which calibration rules fired and build structured explanations.

    Returns (inputs, outputs, raw_response_json).
    """
    request_type = str(intent_data.get("request_type", "")).lower().strip()
    intent_to_harm = _is_yes_ui(intent_data.get("intent_to_harm", "no"))
    requested_instructions = _is_yes_ui(intent_data.get("requested_instructions", "no"))
    intent_operational = _is_yes_ui(intent_data.get("intent_operational", "no"))
    op_risk = str(operational_data.get("operational_risk", "")).upper().strip()

    positive_signals = _positive_calibration_signal_keys(signal_data, _CALIBRATION_HARMFUL_COUNT_KEYS)
    harmful_count = len(positive_signals)
    q13_positive = _positive_calibration_signal_keys(signal_data, (_CALIBRATION_Q13_TOPIC_KEY,))
    reputational_positive = _positive_calibration_signal_keys(signal_data, _CALIBRATION_REPUTATIONAL_TOPIC_KEYS)
    has_reputational_cluster = len(reputational_positive) > 0
    q7 = _is_yes_ui(signal_data.get("q7_only_emotional", "no"))

    topic_signal_summary_parts: list[str] = []
    if q13_positive:
        topic_signal_summary_parts.append("q13=yes")
    if reputational_positive:
        topic_signal_summary_parts.append(f"q14–q16={len(reputational_positive)} ({', '.join(reputational_positive)})")
    topic_signal_summary = (
        "; ".join(topic_signal_summary_parts)
        + " — excluded from harmful_count"
        + ("; q14–q16 still open harm calibration branch" if has_reputational_cluster else "")
        if topic_signal_summary_parts
        else "0 affirmative (q13–q16)"
    )

    inputs: list[dict[str, Any]] = [
        {"label": "request_type", "source": request_type or "unknown"},
        {"label": "op_risk", "source": op_risk or "NONE"},
        {
            "label": "harm_signals",
            "source": f"{harmful_count} positive (q1–q6, q8–q12, q17 = calibration harmful_count)",
        },
        {"label": "topic_signals", "source": topic_signal_summary},
    ]
    if intent_to_harm:
        inputs.append({"label": "intent_to_harm", "source": "yes"})
    if requested_instructions:
        inputs.append({"label": "requested_instructions", "source": "yes"})
    if intent_operational:
        inputs.append({"label": "intent_operational", "source": "yes"})

    outputs: list[dict[str, Any]] = []
    rules_applied: list[str] = []
    calibration_path = ""

    if harmful_count > 0 or has_reputational_cluster:
        calibration_path = "harm_signals_present"
        all_intent_false = not intent_to_harm and not requested_instructions and not intent_operational
        is_defensive = harmful_count >= 1 and all_intent_false and op_risk in ("NONE", "LOW", "")
        if is_defensive:
            rules_applied.append(
                f"defensive_override: {harmful_count} harm signal(s) "
                f"but all intent=False, op_risk={op_risk or 'NONE'} "
                f"-> cap score to 0.30, category=SENSITIVE"
            )
        elif harmful_count >= 3 or raw_score >= 0.85:
            rules_applied.append(
                f"harm_escalation: {harmful_count} signals, score={raw_score:.2f} " f"-> CLEARLY_HARMFUL, floor 0.85"
            )
        elif harmful_count > 0:
            rules_applied.append(f"harm_present: {harmful_count} signal(s) ({', '.join(positive_signals)})")
        elif has_reputational_cluster:
            rules_applied.append(
                "reputational_cluster: q14–q16 affirmative routes through harm_signals_present "
                "without increasing harmful_count (calibration._apply_calibration)"
            )
    elif q7:
        calibration_path = "emotional_only"
        rules_applied.append(f"emotional_only: q7_only_emotional=yes, " f"request_type={request_type}")
    else:
        calibration_path = "no_harm"
        rules_applied.append(f"no_harm_routing: request_type={request_type}")

    all_intent_false = not intent_to_harm and not requested_instructions and not intent_operational
    if op_risk == "NONE" and all_intent_false:
        rules_applied.append(
            "non_operational_clamp: op_risk=NONE + all intent=False " "-> cap score 0.30, CLEARLY_HARMFUL->SENSITIVE"
        )
    elif op_risk == "HIGH" and all_intent_false and harmful_count == 0 and not has_reputational_cluster:
        rules_applied.append(
            "intent_contradiction: op_risk=HIGH but all intent=False "
            "-> CLEARLY_HARMFUL->SENSITIVE, cap 0.55, DENY->DELIBERATE"
        )

    outputs.append(
        {
            "label": "path",
            "value": calibration_path,
        }
    )
    if abs(raw_score - final_score) > 0.01:
        outputs.append(
            {
                "label": "score",
                "value": f"{raw_score:.2f} -> {final_score:.2f}",
            }
        )
    for rule in rules_applied:
        outputs.append({"label": "rule", "value": rule})

    raw_response = json.dumps(
        {
            "calibration_path": calibration_path,
            "raw_score": raw_score,
            "final_score": final_score,
            "request_type": request_type,
            "operational_risk": op_risk,
            "intent_to_harm": intent_to_harm,
            "requested_instructions": requested_instructions,
            "intent_operational": intent_operational,
            "harmful_count": harmful_count,
            "positive_harm_signals": positive_signals,
            "positive_topic_signal_q13": q13_positive,
            "positive_topic_signals_q14_q16": reputational_positive,
            "has_reputational_cluster": has_reputational_cluster,
            "q7_emotional": q7,
            "rules_applied": rules_applied,
        },
        indent=2,
        ensure_ascii=False,
    )
    return inputs, outputs, raw_response


def _build_synthetic_calibration_node(
    llm_calls: list[dict[str, Any]], final_decision_card: dict[str, Any] | None
) -> dict[str, Any] | None:
    """Build a synthetic node showing how calibration adjusted the risk score.

    Extracts signals from the 3 mini-estimator raw responses, determines
    which calibration rules fired (defensive override, harm escalation,
    non-operational clamp, calibration guard), and explains the WHY behind
    each score adjustment.

    Returns ``None`` if no meaningful delta is detected or data is missing.
    """
    if not final_decision_card:
        return None
    final_score = final_decision_card.get("risk_score")
    if final_score is None:
        return None
    try:
        final_score = float(final_score)
    except (ValueError, TypeError):
        return None

    intent_data, signal_data, operational_data, last_risk_end = _extract_mini_estimator_data(llm_calls)

    raw_score: float | None = None
    try:
        raw_score = float(operational_data.get("risk_score", operational_data.get("score", -1)))
    except (ValueError, TypeError):
        pass
    if raw_score is None or raw_score < 0:
        return None

    inputs, outputs, raw_response = _describe_calibration_path(
        intent_data, signal_data, operational_data, raw_score, final_score
    )

    # Append calibration_guard caps if present
    for c in llm_calls:
        if (c.get("action") or "").lower() == "calibration_guard":
            raw_resp = c.get("raw_response") or ""
            try:
                gd = json.loads(raw_resp) if raw_resp else {}
                for note in gd.get("notes") or gd.get("_calibration_guard_notes", []):
                    outputs.append({"label": "guard", "value": str(note)})
            except Exception:
                pass

    if not outputs:
        return None

    prompt_text = (
        "Cross-signal calibration pipeline. Merges 3 mini-estimator "
        "outputs (intent, signals, operational) and applies: "
        "defensive override, harm escalation, non-operational clamp, "
        "calibration guard. The rules below explain WHY the score "
        "was adjusted."
    )
    return {
        "module": "risk_estimator",
        "phase": "calibration",
        "action": "calibrate",
        "cycle": 0,
        "started_at": last_risk_end,
        "duration_ms": 0,
        "is_synthetic": True,
        "prompt": prompt_text,
        "system_prompt": "[calibration] Post-merge score adjustment",
        "raw_response": raw_response,
        "io_annotations": {"inputs": inputs, "outputs": outputs},
    }


def _build_synthetic_path_routing_node(
    debug_events: list[dict[str, Any]],
    traces: list[dict[str, Any]],
    calibration_node: dict[str, Any] | None,
    llm_calls: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """Synthetic flow node: path_router / controller branch, from persisted debug (no extra LLM)."""
    obs = build_orchestrator_observability(debug_events, traces)
    if not obs.get("has_routing_data"):
        return None
    io = orchestrator_observability_to_io_annotations(obs)
    if not io["inputs"] and not io["outputs"]:
        bullets_raw = obs.get("narrative_bullets") or []
        bullets = bullets_raw[:12] if isinstance(bullets_raw, list) else []
        for i, b in enumerate(bullets):
            io["outputs"].append({"label": f"detail_{i + 1}", "value": str(b)[:800]})
    _, _, _, last_risk_end = _extract_mini_estimator_data(llm_calls)
    anchor = int(calibration_node["started_at"]) if calibration_node else int(last_risk_end)
    started_at = anchor + 1
    raw_payload = {
        "narrative_bullets": obs.get("narrative_bullets"),
        "routing_signals": obs.get("routing_signals"),
        "events_chronological": obs.get("events_chronological"),
    }
    prompt_text = (
        "Controller routing after risk assessment (path_router.get_route): which branch executes "
        "(early REFUSE / benign / SAFE_COMPLETE / fast_path / deliberative) and how "
        "risk_policy_action, thresholds, and sensitive-overlay floors interact."
    )
    return {
        "module": "orchestrator",
        "phase": "path_routing",
        "action": "route_resolution",
        "cycle": 0,
        "started_at": started_at,
        "duration_ms": 0,
        "is_synthetic": True,
        "prompt": prompt_text,
        "system_prompt": "[orchestrator] Path routing observability (structured logs, not an LLM call)",
        "raw_response": json.dumps(raw_payload, indent=2, ensure_ascii=False),
        "io_annotations": io,
    }


def _synthetic_constitution_call_from_traces(traces: list[dict[str, Any]]) -> dict[str, Any] | None:
    """If RELEVANT_PRINCIPLES trace has started_at and duration_ms, return a
    synthetic 'call' for metro/journey."""
    for t in traces:
        if (t.get("stage") or "").strip().upper() != "RELEVANT_PRINCIPLES":
            continue
        td = _parse_trace_json(t)
        started_at = td.get("started_at")
        duration_ms = td.get("duration_ms")
        if duration_ms is None:
            continue
        if started_at is None:
            continue
        principle_ids = td.get("relevant_principle_ids") or []
        principles_detail = td.get("relevant_principles") or []
        if not principles_detail and principle_ids:
            principles_detail = [{"id": pid, "title": "", "level": ""} for pid in principle_ids]
        domain = td.get("domain") or ""
        prompt_text = (
            "Parallel retrieval of relevant constitutional principles for this request. "
            "Multiple domain agents run in parallel (constitution store). "
            f"Domain hint: {domain or 'auto'}."
        )
        raw_response = json.dumps(
            {
                "relevant_principles": principles_detail,
                "domain": domain,
                "parallel_retrieval": td.get("parallel_retrieval", True),
                "count": len(principle_ids or principles_detail),
            },
            indent=2,
        )
        return {
            "module": "constitution",
            "phase": "relevant_principles",
            "action": "retrieve",
            "cycle": 0,
            "started_at": int(started_at),
            "duration_ms": float(duration_ms),
            "is_synthetic": True,
            "parallel_retrieval": td.get("parallel_retrieval", True),
            "prompt": prompt_text,
            "system_prompt": (
                "Constitution store: parallel domain agents evaluate query " "relevance and return principle IDs per domain."
            ),
            "raw_response": raw_response,
        }
    return None


def _journey_sort_key(c: dict[str, Any]) -> tuple[Any, Any, Any, Any, Any]:
    """Sort key: cycle, sequence_in_cycle, id, started_at (tie-break), phase."""
    cycle = c.get("cycle") if c.get("cycle") is not None else -1
    seq = c.get("sequence_in_cycle") if c.get("sequence_in_cycle") is not None else 999
    return (cycle, seq, c.get("id") or 0, c.get("started_at") or 0, c.get("phase") or "")


def _journey_steps(llm_calls: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return llm_calls sorted by logical order (cycle, sequence_in_cycle) then started_at for journey."""
    return sorted(llm_calls, key=_journey_sort_key)


def _enrich_journey_with_timing_and_parallel(
    journey_steps: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """
    Enrich each step with ended_at (ms), left_pct, width_pct for timeline,
    and is_parallel (True if this call overlaps in time with another in the same cycle).
    """
    if not journey_steps:
        return []
    t_min = min((c.get("started_at") or 0) for c in journey_steps)
    ended_list = [(c.get("started_at") or 0) + (c.get("duration_ms") or 0) for c in journey_steps]
    t_max = max(ended_list) if ended_list else t_min
    span = max(t_max - t_min, 1)

    out: list[dict[str, Any]] = []
    for i, c in enumerate(journey_steps):
        start_ms = c.get("started_at") or 0
        dur = c.get("duration_ms") or 0
        end_ms = start_ms + dur
        left_pct = (start_ms - t_min) / span * 100.0
        width_pct = dur / span * 100.0
        cycle = c.get("cycle")
        # Overlaps with any other call in same cycle?
        is_parallel = False
        for j, other in enumerate(journey_steps):
            if i == j:
                continue
            if other.get("cycle") != cycle:
                continue
            o_start = other.get("started_at") or 0
            o_dur = other.get("duration_ms") or 0
            o_end = o_start + o_dur
            if start_ms < o_end and o_start < end_ms:
                is_parallel = True
                break
        entry: dict[str, Any] = dict(c)
        entry["ended_at_ms"] = end_ms
        entry["left_pct"] = round(left_pct, 2)
        entry["width_pct"] = round(width_pct, 2)
        entry["is_parallel"] = is_parallel
        out.append(entry)
    return out


# Display order of modules for timeline rows (metro map)
_TIMELINE_MODULE_ORDER = (
    "risk_estimator",
    "orchestrator",
    "constitution",
    "policy",
    "critic",
    "simulator",
    "perspectives",
    "hindsight",
)


def _build_execution_timeline(llm_calls: list[dict[str, Any]]) -> dict[str, Any]:
    """
    Build Gantt-style timeline: t_min_ms, t_max_ms, total_duration_ms,
    and rows = list of { module, calls: [ { phase, cycle, left_pct, width_pct,
    duration_ms, started_at } ] } in fixed module order.
    started_at/ended_at are in ms (DB stores started_at as ms).
    """
    if not llm_calls:
        return {"t_min_ms": 0, "t_max_ms": 0, "total_duration_ms": 0, "rows": []}
    t_min = min((c.get("started_at") or 0) for c in llm_calls)
    ended = [(c.get("started_at") or 0) + (c.get("duration_ms") or 0) for c in llm_calls]
    t_max = max(ended) if ended else t_min
    span = max(t_max - t_min, 1)

    by_module: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for c in llm_calls:
        mod = (c.get("module") or "unknown").strip() or "unknown"
        by_module[mod].append(c)

    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for mod in _TIMELINE_MODULE_ORDER:
        if mod not in by_module:
            continue
        seen.add(mod)
        calls = sorted(
            by_module[mod],
            key=lambda x: (
                x.get("sequence_in_cycle") if x.get("sequence_in_cycle") is not None else 9999,
                x.get("id") or 0,
                x.get("started_at") or 0,
                x.get("phase") or "",
            ),
        )
        bar_list: list[dict[str, Any]] = []
        for c in calls:
            start_ms = c.get("started_at") or 0
            dur = c.get("duration_ms") or 0
            left_pct = (start_ms - t_min) / span * 100.0
            width_pct = dur / span * 100.0
            bar_list.append(
                {
                    "phase": c.get("phase") or "",
                    "cycle": c.get("cycle"),
                    "left_pct": round(left_pct, 2),
                    "width_pct": round(max(width_pct, 0.5), 2),
                    "duration_ms": round(dur, 0),
                    "started_at": start_ms,
                }
            )
        rows.append({"module": mod, "calls": bar_list})
    for mod in sorted(by_module.keys()):
        if mod in seen:
            continue
        calls = sorted(
            by_module[mod],
            key=lambda x: (
                x.get("sequence_in_cycle") if x.get("sequence_in_cycle") is not None else 9999,
                x.get("id") or 0,
                x.get("started_at") or 0,
                x.get("phase") or "",
            ),
        )
        bar_list_else: list[dict[str, Any]] = []
        for c in calls:
            start_ms = c.get("started_at") or 0
            dur = c.get("duration_ms") or 0
            left_pct = (start_ms - t_min) / span * 100.0
            width_pct = dur / span * 100.0
            bar_list_else.append(
                {
                    "phase": c.get("phase") or "",
                    "cycle": c.get("cycle"),
                    "left_pct": round(left_pct, 2),
                    "width_pct": round(max(width_pct, 0.5), 2),
                    "duration_ms": round(dur, 0),
                    "started_at": start_ms,
                }
            )
        rows.append({"module": mod, "calls": bar_list_else})

    return {
        "t_min_ms": t_min,
        "t_max_ms": t_max,
        "total_duration_ms": t_max - t_min,
        "rows": rows,
    }


def _module_summaries(llm_calls: list[dict[str, Any]]) -> dict[str, Any]:
    """Build per-module summary: count, total_ms, and a short summary
    from last parsed_summary_json."""
    by_module: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for c in llm_calls:
        mod = (c.get("module") or "unknown").strip() or "unknown"
        by_module[mod].append(c)
    summaries: dict[str, Any] = {}
    for mod, calls in sorted(by_module.items()):
        total_ms = sum((x.get("duration_ms") or 0) for x in calls)
        last_parsed = None
        for x in reversed(calls):
            ps = x.get("parsed_summary_json")
            if ps:
                last_parsed = ps
                break
        summaries[mod] = {
            "count": len(calls),
            "total_ms": round(total_ms, 0),
            "last_summary": last_parsed,
        }
    return summaries


def _call_result_preview(parsed_summary_json: str | None, module: str) -> str:
    """Format parsed_summary_json as a short readable result for the module (for UI card)."""
    if not parsed_summary_json or not str(parsed_summary_json).strip():
        return ""
    raw = str(parsed_summary_json).strip()
    try:
        data = json.loads(raw) if isinstance(parsed_summary_json, str) else parsed_summary_json
        if not isinstance(data, dict):
            return raw[:300] + ("..." if len(raw) > 300 else "")
        parts = []
        mod = (module or "").lower()
        if "risk" in mod:
            if "risk_score" in data:
                parts.append(f"Score: {data['risk_score']}")
            if "risk_category" in data:
                parts.append(f"Cat: {data['risk_category']}")
            if "intent_type" in data:
                parts.append(f"Intent: {data['intent_type']}")
            if "harm_type" in data:
                parts.append(f"Harm: {data['harm_type']}")
            if "operational_risk" in data:
                parts.append(f"Op: {data['operational_risk']}")
            if "domain" in data:
                parts.append(f"Domain: {data['domain']}")
            mini = data.get("mini_estimator")
            mode = data.get("estimation_mode")
            if mini:
                parts.append(f"mini: {mini}")
            if mode:
                parts.append(f"mode: {mode}")

        if "critic" in mod:
            if "violations" in data:
                parts.append(f"Violations: {data.get('violations', data.get('violation_count', '—'))}")
            if "violated_hard" in data:
                parts.append(f"Hard: {data['violated_hard']}")
            if "decision" in data:
                parts.append(f"Decision: {data['decision']}")
            if "revision_guidance" in data:
                g = data["revision_guidance"]
                parts.append(f"Guidance: {(g[:80] + '…') if isinstance(g, str) and len(g) > 80 else g}")

        if "simulator" in mod:
            if "consequences_count" in data:
                parts.append(f"Consequences: {data['consequences_count']}")
            if "semantic_expected_harm" in data:
                parts.append(f"Harm: {data['semantic_expected_harm']}")
            if "expected_valence" in data:
                parts.append(f"Valence: {data['expected_valence']}")
            if "dominant_harm_types" in data:
                dht = data["dominant_harm_types"]
                if isinstance(dht, list):
                    dht = ", ".join(dht)
                parts.append(f"HarmTypes: {dht}")

        if "hindsight" in mod:
            if "score" in data:
                parts.append(f"Score: {data['score']}")
            if "hindsight_score" in data:
                parts.append(f"Score: {data['hindsight_score']}")
            if "recommendation" in data:
                parts.append(f"Rec: {data['recommendation']}")

        if "perspective" in mod:
            if "perspectives_evaluated" in data:
                parts.append(f"Perspectives: {data['perspectives_evaluated']}")
            if "recommendation" in data:
                parts.append(f"Rec: {data['recommendation']}")
            if "approval_scores" in data:
                avgs = data.get("approval_scores", {})
                if isinstance(avgs, dict) and avgs:
                    vals = [v for v in avgs.values() if isinstance(v, (int, float))]
                    if vals:
                        avg = sum(vals) / len(vals)
                        parts.append(f"Avg Approval: {round(avg, 2)}")

        if not parts:
            for k, v in list(data.items())[:5]:
                if v is not None and str(v).strip():
                    parts.append(f"{k}: {v}")
        return " | ".join(parts) if parts else raw[:200] + ("..." if len(raw) > 200 else "")
    except (json.JSONDecodeError, TypeError):
        return raw[:300] + ("..." if len(raw) > 300 else "")


_CONVERSATION_STATE_JSON_KEYS = ("state_in_json", "state_out_json", "state_summary_json")
_LEDGER_EVENT_JSON_KEYS = ("payload_json",)
_SESSION_STORE_EVENT_JSON_KEYS = ("state_summary_json", "payload_json")
_PROXY_REQUEST_EVENT_JSON_KEYS = ("headers_json", "metadata_json")


def _build_conversation_timeline(conversation_id: str) -> dict[str, Any]:
    """
    Assemble a fully-resolved timeline for a conversation.

    Aggregates:
      * the ``requests`` rows ordered by ``turn_index`` (with parsed ``meta_json``);
      * the per-request ``conversation_states`` snapshot;
      * the per-request ledger / session-store / proxy-finalization events;
      * an overview document (totals, postures, hit/miss counters).

    Best-effort: when individual lookups fail or tables are missing, the
    corresponding sections degrade to empty lists / ``None`` instead of
    raising.  The result is consumed by the conversation timeline template.
    """
    if not conversation_id:
        return {
            "conversation_id": "",
            "requests": [],
            "overview": {},
            "states_by_request": {},
            "ledger_by_request": {},
            "session_by_request": {},
            "proxy_by_request": {},
            "run_id": None,
        }

    requests_rows = get_requests_for_conversation(conversation_id) or []
    states_rows = get_conversation_states(conversation_id) or []
    ledger_rows = get_ledger_events_for_conversation(conversation_id) or []
    session_rows = get_session_store_events_for_conversation(conversation_id) or []
    proxy_rows = get_proxy_request_events_for_conversation(conversation_id) or []
    overview = get_conversation_overview(conversation_id) or {}

    states_by_request: dict[str, dict[str, Any]] = {}
    for row in states_rows:
        rid = row.get("request_id")
        if rid:
            states_by_request[str(rid)] = _enrich_event_row(row, _CONVERSATION_STATE_JSON_KEYS)

    ledger_by_request: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in ledger_rows:
        rid = row.get("request_id")
        if rid:
            ledger_by_request[str(rid)].append(_enrich_event_row(row, _LEDGER_EVENT_JSON_KEYS))

    session_by_request: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in session_rows:
        rid = row.get("request_id")
        if rid:
            session_by_request[str(rid)].append(_enrich_event_row(row, _SESSION_STORE_EVENT_JSON_KEYS))

    proxy_by_request: dict[str, dict[str, Any]] = {}
    for row in proxy_rows:
        rid = row.get("request_id")
        if rid:
            proxy_by_request[str(rid)] = _enrich_event_row(row, _PROXY_REQUEST_EVENT_JSON_KEYS)

    run_id: str | None = None
    enriched_requests: list[dict[str, Any]] = []
    for req in requests_rows:
        rid = str(req.get("request_id") or "")
        rrid = req.get("run_id")
        if rrid and not run_id:
            run_id = str(rrid)
        item = dict(req)
        item["meta_json__parsed"] = _parse_json_field(item.get("meta_json"))
        item["state"] = states_by_request.get(rid)
        item["ledger_events"] = ledger_by_request.get(rid, [])
        item["session_events"] = session_by_request.get(rid, [])
        item["proxy_event"] = proxy_by_request.get(rid)
        enriched_requests.append(item)

    return {
        "conversation_id": conversation_id,
        "requests": enriched_requests,
        "overview": overview,
        "states_by_request": states_by_request,
        "ledger_by_request": dict(ledger_by_request),
        "session_by_request": dict(session_by_request),
        "proxy_by_request": proxy_by_request,
        "run_id": run_id,
    }


def _check_credentials(username: str, password: str) -> bool:
    if not _UI_USERNAME or not _UI_PASSWORD:
        return False
    u = _sanitize(username)
    p = _sanitize(password)
    try:
        u_ok = secrets.compare_digest(u.encode("utf-8"), _UI_USERNAME.encode("utf-8"))
        p_ok = secrets.compare_digest(p.encode("utf-8"), _UI_PASSWORD.encode("utf-8"))
        return u_ok and p_ok
    except (UnicodeEncodeError, UnicodeDecodeError):
        return False


def _create_session() -> str:
    token = secrets.token_urlsafe(32)
    _SESSIONS[token] = time.time() + _SESSION_TTL
    return token


def _validate_session(token: str | None) -> bool:
    if not token:
        return False
    now = time.time()
    if token in _SESSIONS and _SESSIONS[token] > now:
        return True
    if token in _SESSIONS:
        del _SESSIONS[token]
    return False


def main() -> None:
    """Entry point for moralstack-ui command."""
    import uvicorn

    app = create_app()
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=int(os.getenv("MORALSTACK_UI_PORT", "8765")),
    )


def create_app() -> FastAPI:
    """Creates the FastAPI app (for uvicorn factory)."""
    app = FastAPI(title="MoralStack Dashboard")

    @app.get("/")
    def root_redirect(request: Request) -> RedirectResponse:
        """Redirect / to /runs (or /login if not authenticated)."""
        token = request.cookies.get(_SESSION_COOKIE)
        if _validate_session(token):
            return RedirectResponse(url="/runs", status_code=303)
        return RedirectResponse(url="/login", status_code=303)

    @app.get("/auth-status")
    def auth_status() -> dict[str, bool]:
        """Diagnostic: whether UI credentials are configured (no auth required)."""
        return {"credentials_configured": bool(_UI_USERNAME and _UI_PASSWORD)}

    _LOGIN_HTML = """<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>MoralStack — Login</title>
<style>
:root{--bg:#0b0b0f;--surface:#16161c;--border:#2a2a35;--text:#e4e4e7;--muted:#71717a;--accent:#60a5fa;--red:#f87171}
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:'Inter',system-ui,sans-serif;background:var(--bg);color:var(--text);
min-height:100vh;display:flex;align-items:center;justify-content:center}
.login-card{background:var(--surface);border:1px solid var(--border);
border-radius:12px;padding:2.5rem;width:100%;max-width:380px}
h1{font-size:1.3rem;font-weight:700;margin-bottom:0.25rem}
.sub{color:var(--muted);font-size:0.85rem;margin-bottom:1.5rem}
label{display:block;font-size:0.8rem;color:var(--muted);margin-bottom:0.25rem;
text-transform:uppercase;letter-spacing:0.05em}
input{width:100%;padding:0.6rem 0.75rem;border:1px solid var(--border);
border-radius:6px;background:var(--bg);color:var(--text);font-size:0.9rem;margin-bottom:1rem;outline:none}
input:focus{border-color:var(--accent)}
button{width:100%;padding:0.65rem;border:none;border-radius:6px;background:var(--accent);color:#0b0b0f;font-size:0.9rem;font-weight:600;cursor:pointer}
button:hover{opacity:0.9}
.error{color:var(--red);font-size:0.85rem;margin-bottom:1rem;text-align:center}
</style></head>
<body><div class="login-card">
<h1>MoralStack</h1>
<p class="sub">Enter credentials to access the dashboard.</p>
<form method="post" action="/login">
  <label for="username">Username</label>
  <input type="text" id="username" name="username" required autofocus>
  <label for="password">Password</label>
  <input type="password" id="password" name="password" required>
  <button type="submit">Sign in</button>
</form>
</div></body></html>"""

    @app.get("/login", response_class=HTMLResponse, response_model=None)
    def login_page(request: Request) -> Response:
        """Login form (redirect to /runs if already authenticated)."""
        token = request.cookies.get(_SESSION_COOKIE)
        if _validate_session(token):
            return RedirectResponse(url="/runs", status_code=303)
        return HTMLResponse(_LOGIN_HTML)

    @app.post("/login", response_model=None)
    def login_post(
        username: str = Form(...),
        password: str = Form(...),
    ) -> Response:
        """Process login form; set session cookie and redirect to /runs."""
        if not _check_credentials(username, password):
            return HTMLResponse(
                _LOGIN_HTML.replace("</form>", '<p class="error">Invalid credentials</p></form>'),
                status_code=401,
            )
        token = _create_session()
        resp = RedirectResponse(url="/runs", status_code=303)
        resp.set_cookie(_SESSION_COOKIE, token, httponly=True, max_age=_SESSION_TTL, samesite="lax")
        return resp

    @app.get("/logout")
    def logout(request: Request) -> RedirectResponse:
        """Clear session and redirect to login."""
        token = request.cookies.get(_SESSION_COOKIE)
        if token and token in _SESSIONS:
            del _SESSIONS[token]
        resp = RedirectResponse(url="/login", status_code=303)
        resp.delete_cookie(_SESSION_COOKIE)
        return resp

    def _require_session(request: Request) -> None:
        """Raise 302 to /login if no valid session."""
        token = request.cookies.get(_SESSION_COOKIE)
        if not _validate_session(token):
            raise HTTPException(status_code=302, headers={"Location": "/login"})

    static_dir = Path(__file__).parent / "static"
    if static_dir.exists():
        app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    templates_dir = Path(__file__).parent / "templates"
    templates = Jinja2Templates(directory=str(templates_dir)) if templates_dir.exists() else None

    if templates:
        from datetime import datetime, timezone

        def _format_ts(value: int | float | None, fmt: str = "%Y-%m-%d %H:%M:%S") -> str:
            if value is None:
                return "—"
            try:
                ts = float(value)
                if ts > 1e12:
                    ts /= 1000.0
                return datetime.fromtimestamp(ts, tz=timezone.utc).strftime(fmt)
            except (ValueError, TypeError, OSError):
                return str(value)

        templates.env.filters["fmtdate"] = _format_ts

        def _filter_module_result(parsed_summary_json: Any, module: str) -> str:
            return _call_result_preview(parsed_summary_json, module)

        templates.env.filters["module_result"] = _filter_module_result

    @app.get("/runs", response_class=HTMLResponse)
    def list_runs(
        request: Request,
        page: int = 1,
        domain: str = "",
        search_text: str = "",
    ) -> Response:
        _require_session(request)
        if not get_db_path():
            raise HTTPException(500, "No database configured (MORALSTACK_DB_PATH)")
        page_size = 20
        safe_page = max(1, int(page))
        runs, total_runs = get_runs_page(
            page=safe_page,
            page_size=page_size,
            domain=domain,
            search_text=search_text,
        )
        total_pages = max(1, (total_runs + page_size - 1) // page_size) if total_runs else 1
        if safe_page > total_pages:
            safe_page = total_pages
            runs, total_runs = get_runs_page(
                page=safe_page,
                page_size=page_size,
                domain=domain,
                search_text=search_text,
            )
        available_domains = get_request_domains()
        if templates:
            return templates.TemplateResponse(
                request,
                "runs.html",
                {
                    "runs": runs,
                    "page": safe_page,
                    "page_size": page_size,
                    "total_runs": total_runs,
                    "total_pages": total_pages,
                    "domain": domain.strip(),
                    "search_text": search_text.strip(),
                    "available_domains": available_domains,
                },
            )
        return HTMLResponse(f"<html><body><h1>Runs</h1><pre>{runs}</pre></body></html>")

    @app.get("/runs/{run_id}", response_class=HTMLResponse)
    def run_detail(run_id: str, request: Request) -> Response:
        _require_session(request)
        if not get_db_path():
            raise HTTPException(500, "No database configured")
        run = get_run(run_id)
        if not run:
            raise HTTPException(404, "Run not found")
        requests_list = get_requests_for_run(run_id)
        benchmark_report = None
        questions_by_category = {}
        benchmark_summary_md = ""
        if (run.get("run_type") or "").strip().lower() == "benchmark":
            benchmark_report = load_benchmark_report(run_id)
            if benchmark_report:
                questions_by_category = get_questions_by_category(benchmark_report)
                benchmark_summary_md = build_benchmark_report_markdown(benchmark_report)
        conversations = get_conversation_ids_for_run(run_id) or []
        if templates:
            return templates.TemplateResponse(
                request,
                "run.html",
                {
                    "run": run,
                    "requests": requests_list,
                    "benchmark_report": benchmark_report,
                    "questions_by_category": questions_by_category,
                    "benchmark_summary_md": benchmark_summary_md,
                    "conversations": conversations,
                },
            )
        return HTMLResponse(f"<html><body><h1>Run {run_id}</h1></body></html>")

    @app.get("/runs/{run_id}/requests/{request_id}", response_class=HTMLResponse)
    def request_detail(run_id: str, request_id: str, request: Request) -> Response:
        _require_session(request)
        if not get_db_path():
            raise HTTPException(500, "No database configured")
        run = get_run(run_id)
        req_data = get_request(run_id, request_id)
        if not req_data:
            raise HTTPException(404, "Request not found")
        llm_calls = get_llm_calls_for_request(run_id, request_id)
        traces = get_decision_traces_for_request(run_id, request_id)
        debug_events = get_debug_events_for_request(run_id, request_id)
        orchestration_events = get_orchestration_events_for_request(run_id, request_id)
        runtime_decision_obs = build_runtime_decision_observability(
            traces=traces,
            orchestration_events=orchestration_events,
            llm_calls=llm_calls,
        )
        execution_summary = _execution_summary_from_request(traces, llm_calls)

        # Build final decision card
        final_decision_card = _build_final_decision_card(traces)

        # Include constitution relevant-principles phase in journey and timeline
        # (parallel domain agents)
        synthetic_constitution = _synthetic_constitution_call_from_traces(traces)

        # Enrich calls with I/O annotations and semantic badges (call_kind / cache_status)
        _tag_constitution_phases(llm_calls)
        for call in llm_calls:
            call["io_annotations"] = _build_module_io_annotations(call)
            enriched = enrich_llm_call_for_ui(call)
            call["semantic_badges"] = enriched.get("semantic_badges") or []

        all_flow_calls = list(llm_calls)
        if synthetic_constitution is not None:
            synthetic_constitution["_constitution_phase"] = "relevant principles (deliberation retrieval)"
            synthetic_constitution["io_annotations"] = _build_module_io_annotations(synthetic_constitution)
            all_flow_calls.append(synthetic_constitution)

        # Build a synthetic "calibration" node showing pre→post score delta.
        calibration_node = _build_synthetic_calibration_node(llm_calls, final_decision_card)
        if calibration_node is not None:
            all_flow_calls.append(calibration_node)

        path_routing_node = _build_synthetic_path_routing_node(debug_events, traces, calibration_node, llm_calls)
        if path_routing_node is not None:
            all_flow_calls.append(path_routing_node)

        orchestrator_observability = build_orchestrator_observability(debug_events, traces)

        # Group by cycle for flow graph
        by_cycle_flow: defaultdict[int | None, list[dict[str, Any]]] = defaultdict(list)
        for c in all_flow_calls:
            cy = c.get("cycle")
            by_cycle_flow[cy].append(c)

        flow_data_cycles: list[dict[str, Any]] = []
        for cycle_num, cycle_calls in sorted(by_cycle_flow.items(), key=lambda x: x[0] if x[0] is not None else 0):
            tiers = _group_calls_into_tiers_and_enrich(cycle_calls)
            connector_labels = _compute_connector_labels(tiers)
            flow_data_cycles.append(
                {
                    "cycle_num": cycle_num,
                    "tiers": tiers,
                    "total_calls": len(cycle_calls),
                    "connector_labels": connector_labels,
                }
            )

        llm_calls_for_journey = list(all_flow_calls)
        llm_calls_for_journey.sort(key=_journey_sort_key)

        journey_steps = _journey_steps(llm_calls_for_journey)
        journey_steps_enriched = _enrich_journey_with_timing_and_parallel(journey_steps)
        llm_calls_chronological = journey_steps
        module_summaries = _module_summaries(llm_calls)
        relevant_principles = _relevant_principles_from_traces(traces)
        execution_timeline = _build_execution_timeline(llm_calls_for_journey)
        benchmark_result = None
        if run and (run.get("run_type") or "").strip().lower() == "benchmark":
            report = load_benchmark_report(run_id)
            if report:
                benchmark_result = get_benchmark_result_by_request_id(report, request_id)

        # Step 13: conversation context for this request (if part of a multi-turn).
        conversation_context: dict[str, Any] | None = None
        conversation_id_for_req = (req_data.get("conversation_id") or "").strip() if isinstance(req_data, dict) else ""
        if conversation_id_for_req:
            sibling_requests = get_requests_for_conversation(conversation_id_for_req) or []
            state_rows = get_conversation_states(conversation_id_for_req) or []
            this_state = None
            for row in state_rows:
                if str(row.get("request_id")) == str(request_id):
                    this_state = _enrich_event_row(row, _CONVERSATION_STATE_JSON_KEYS)
                    break
            this_ledger = [
                _enrich_event_row(r, _LEDGER_EVENT_JSON_KEYS)
                for r in (get_ledger_events_for_conversation(conversation_id_for_req) or [])
                if str(r.get("request_id")) == str(request_id)
            ]
            this_session = [
                _enrich_event_row(r, _SESSION_STORE_EVENT_JSON_KEYS)
                for r in (get_session_store_events_for_conversation(conversation_id_for_req) or [])
                if str(r.get("request_id")) == str(request_id)
            ]
            this_proxy = None
            for row in get_proxy_request_events_for_conversation(conversation_id_for_req) or []:
                if str(row.get("request_id")) == str(request_id):
                    this_proxy = _enrich_event_row(row, _PROXY_REQUEST_EVENT_JSON_KEYS)
                    break
            conversation_context = {
                "conversation_id": conversation_id_for_req,
                "turn_count": len(sibling_requests),
                "turn_index": req_data.get("turn_index"),
                "siblings": [
                    {
                        "request_id": r.get("request_id"),
                        "turn_index": r.get("turn_index"),
                        "domain": r.get("domain"),
                        "is_current": str(r.get("request_id")) == str(request_id),
                    }
                    for r in sibling_requests
                ],
                "state": this_state,
                "ledger_events": this_ledger,
                "session_events": this_session,
                "proxy_event": this_proxy,
                "meta_json__parsed": _parse_json_field(req_data.get("meta_json")) if isinstance(req_data, dict) else None,
            }

        path_badge_info = _build_path_badge_info(orchestration_events)
        proxy_output_info = _build_proxy_output_info(orchestration_events)
        compliance_fast_path_panel = None
        if (execution_summary.get("path") or "").strip().upper() == "COMPLIANCE_FAST_PATH":
            compliance_fast_path_panel = _build_compliance_fast_path_panel(traces, orchestration_events)

        if templates:
            return templates.TemplateResponse(
                request,
                "request.html",
                {
                    "run_id": run_id,
                    "run": run or {},
                    "req": req_data,
                    "llm_calls": llm_calls,
                    "flow_data_cycles": flow_data_cycles,
                    "final_decision_card": final_decision_card,
                    "llm_calls_chronological": llm_calls_chronological,
                    "journey_steps": journey_steps_enriched,
                    "execution_summary": execution_summary,
                    "module_summaries": module_summaries,
                    "relevant_principles": relevant_principles,
                    "execution_timeline": execution_timeline,
                    "traces": traces,
                    "debug_events": debug_events,
                    "benchmark_result": benchmark_result,
                    "orchestrator_observability": orchestrator_observability,
                    "orchestration_events": orchestration_events,
                    "runtime_decision_obs": runtime_decision_obs,
                    "compliance_card": _build_compliance_card(orchestration_events),
                    "compliance_fast_path_panel": compliance_fast_path_panel,
                    "path_badge_info": path_badge_info,
                    "proxy_output_info": proxy_output_info,
                    "conversation_context": conversation_context,
                },
            )
        return HTMLResponse(f"<html><body><h1>Request {request_id}</h1></body></html>")

    @app.post("/runs/{run_id}/delete")
    def do_delete_run(run_id: str, request: Request) -> dict[str, str]:
        _require_session(request)
        if not get_db_path():
            raise HTTPException(500, "No database configured")
        if delete_run(run_id):
            return {"status": "ok"}
        raise HTTPException(500, "Delete failed")

    @app.post("/runs/{run_id}/requests/{request_id}/delete")
    def do_delete_request(run_id: str, request_id: str, request: Request) -> dict[str, str]:
        _require_session(request)
        if not get_db_path():
            raise HTTPException(500, "No database configured")
        if delete_request(run_id, request_id):
            return {"status": "ok"}
        raise HTTPException(500, "Delete failed")

    @app.get("/runs/{run_id}/requests/{request_id}/export.md", response_class=PlainTextResponse)
    def export_request_md(run_id: str, request_id: str, request: Request) -> PlainTextResponse:
        _require_session(request)
        if not get_db_path():
            raise HTTPException(500, "No database configured")
        content = export_request_markdown(run_id, request_id)
        return PlainTextResponse(content, media_type="text/markdown")

    @app.get("/runs/{run_id}/export_benchmark.md", response_class=PlainTextResponse)
    def export_benchmark_md(run_id: str, request: Request) -> PlainTextResponse:
        _require_session(request)
        if not get_db_path():
            raise HTTPException(500, "No database configured")
        content = export_run_benchmark_markdown(run_id)
        return PlainTextResponse(content, media_type="text/markdown")

    # ------------------------------------------------------------------
    # Step 13 — multi-turn conversation timeline views and export
    # ------------------------------------------------------------------

    @app.get("/conversations/{conversation_id}", response_class=HTMLResponse)
    def conversation_detail(conversation_id: str, request: Request) -> Response:
        """Render the full multi-turn timeline for a conversation_id."""
        _require_session(request)
        if not get_db_path():
            raise HTTPException(500, "No database configured")
        timeline = _build_conversation_timeline(conversation_id)
        if not timeline.get("requests"):
            raise HTTPException(404, f"Conversation {conversation_id} not found")
        if templates:
            return templates.TemplateResponse(
                request,
                "conversation.html",
                {
                    "conversation_id": conversation_id,
                    "timeline": timeline,
                },
            )
        return HTMLResponse(
            f"<html><body><h1>Conversation {conversation_id}</h1>"
            f"<pre>{json.dumps(timeline, indent=2, default=str)}</pre>"
            "</body></html>"
        )

    @app.get("/conversations/{conversation_id}/export.md", response_class=PlainTextResponse)
    def export_conversation_md(conversation_id: str, request: Request) -> PlainTextResponse:
        """Export a conversation audit trail as markdown (AI Act art. 12)."""
        _require_session(request)
        if not get_db_path():
            raise HTTPException(500, "No database configured")
        content = export_conversation_to_markdown(conversation_id)
        return PlainTextResponse(content, media_type="text/markdown")

    @app.get("/conversations", response_class=HTMLResponse)
    def conversations_search(request: Request, q: str = "") -> Response:
        """Direct conversation_id lookup (Step 13.17)."""
        _require_session(request)
        if not get_db_path():
            raise HTTPException(500, "No database configured")
        query = (q or "").strip()
        if query:
            return RedirectResponse(url=f"/conversations/{query}", status_code=303)
        return HTMLResponse("<html><body><meta http-equiv='refresh' content='0;url=/runs'></body></html>")

    return app
