"""
DiagnosticsLayer: structured logging, DCF.
Never influences flow; observability and final_action assertion only.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from typing import Any, Callable

from moralstack.orchestration.types import (
    FinalAction,
    FinalResponse,
    OrchestratorResult,
    ProcessedRequest,
    response_type_to_final_action,
)
from moralstack.runtime.decision_correctness import (
    DecisionCorrectnessResult,
    DecisionSignals,
    evaluate_correctness,
)

_ORCH_MODULE_LOG = logging.getLogger(__name__)

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_DEBUG_LOG_PATH = os.path.join(_PROJECT_ROOT, ".debug", "debug.log")
_DEBUG_LOG_LOCK = threading.Lock()


def log_deliberation_inconsistency(
    request_id: str,
    path: str,
    final_action: str,
    return_point: str,
    modules_executed: str = "none",
) -> None:
    """Log WARNING when path=DELIBERATIVE_PATH but no deliberative module
    executed. Does not block."""
    try:
        _ORCH_MODULE_LOG.warning(
            "[Orchestrator] Inconsistency: path=DELIBERATIVE_PATH with deliberation_cycles=0 "
            "(no deliberative module). request_id=%s path=%s final_action=%s "
            "return_point=%s modules_executed=%s",
            request_id,
            path,
            final_action,
            return_point,
            modules_executed,
        )
    except Exception as e:
        _ORCH_MODULE_LOG.warning(
            "log_deliberation_inconsistency failed request_id=%s error_type=%s error=%s",
            request_id,
            type(e).__name__,
            e,
        )


def orch_debug_log(location: str, message: str, data: dict, hypothesis_id: str = "", request_id: str = "") -> None:
    """Writes one NDJSON line to the debug file and/or DB; does not raise exceptions.

    Thread-safe: concurrent calls are serialized via _DEBUG_LOG_LOCK to prevent
    interleaved writes that would corrupt JSON lines.

    Persist mode: db_only -> DB only; dual -> DB + file; file_only -> file only.

    Persist mode: db_only -> DB only; dual -> DB + file; file_only -> file only.
    """
    try:
        import json

        payload = {
            "location": location,
            "message": message,
            "data": data,
            "timestamp": int(time.time() * 1000),
        }
        if request_id:
            payload["request_id"] = request_id
        if hypothesis_id:
            payload["hypothesisId"] = hypothesis_id

        # Persist to DB when db_only or dual
        try:
            from moralstack.persistence.config import get_persist_mode
            from moralstack.persistence.context import get_current_run_id
            from moralstack.persistence.write_queue import async_persist_debug_event

            mode = get_persist_mode()
            if mode in ("db_only", "dual"):
                async_persist_debug_event(
                    run_id=get_current_run_id(),
                    request_id=request_id or None,
                    payload=payload,
                )
        except ImportError as e:
            _ORCH_MODULE_LOG.debug("orch_debug_log: persistence unavailable (optional): %s", e)
        except Exception as e:
            _ORCH_MODULE_LOG.warning(
                "orch_debug_log: persist_debug_event failed request_id=%s error_type=%s error=%s",
                request_id or "",
                type(e).__name__,
                e,
            )

        # Skip file write when db_only
        try:
            from moralstack.persistence.config import get_persist_mode

            if get_persist_mode() == "db_only":
                return
        except ImportError as e:
            _ORCH_MODULE_LOG.debug("orch_debug_log: get_persist_mode unavailable (optional): %s", e)
        except Exception as e:
            _ORCH_MODULE_LOG.warning(
                "orch_debug_log: get_persist_mode failed request_id=%s error_type=%s error=%s",
                request_id or "",
                type(e).__name__,
                e,
            )

        line = json.dumps(payload, ensure_ascii=False) + "\n"
        try:
            with _DEBUG_LOG_LOCK:
                log_dir = os.path.dirname(_DEBUG_LOG_PATH)
                if not os.path.exists(log_dir):
                    os.makedirs(log_dir, exist_ok=True)
                with open(_DEBUG_LOG_PATH, "a", encoding="utf-8") as f:
                    f.write(line)
        except Exception as e:
            _ORCH_MODULE_LOG.error(
                "orch_debug_log: file write failed request_id=%s path=%s error_type=%s error=%s",
                request_id or "",
                _DEBUG_LOG_PATH,
                type(e).__name__,
                e,
            )
    except Exception as e:
        _ORCH_MODULE_LOG.warning(
            "orch_debug_log failed location=%s request_id=%s error_type=%s error=%s",
            location,
            request_id or "",
            type(e).__name__,
            e,
        )


class DiagnosticsLayer:
    """
    Structured logging, DCF.
    Never modifies the decision result; diagnostics and final_action assertion only.
    """

    def __init__(
        self,
        trace_lock: threading.Lock,
        execution_trace: dict[str, dict[str, Any]],
    ) -> None:
        self._trace_lock = trace_lock
        self._execution_trace = execution_trace

    def attach_decision_correctness(self, result: OrchestratorResult) -> None:
        """Builds DecisionSignals from metadata, evaluates correctness (DCF)
        and saves to metadata.decision_correctness."""
        meta = getattr(result.response, "metadata", None)
        if meta is None:
            return
        signals = DecisionSignals(
            risk_category=(getattr(meta, "risk_category", "") or "").strip() or None,
            hard_violations=list(getattr(meta, "hard_violations", None) or []),
            intent_clarity=((getattr(meta, "intent_clarity", "") or "").strip().upper() or None),
            misuse_plausibility=((getattr(meta, "misuse_plausibility", "") or "").strip().upper() or None),
            actionability_risk=((getattr(meta, "actionability_risk", "") or "").strip().upper() or None),
            intent_type=(getattr(meta, "intent_type", "") or "").strip().lower() or None,
            domain_overlay=(getattr(meta, "domain_overlay", "") or "").strip().lower() or None,
            operational_risk=((getattr(meta, "operational_risk", "") or "").strip().upper() or None),
            requested_instructions=bool(getattr(meta, "requested_instructions", False)),
            intent_to_harm=bool(getattr(meta, "intent_to_harm", False)),
            intent_operational=bool(getattr(meta, "intent_operational", False)),
        )
        chosen = getattr(meta, "final_action", "") or ""
        dcf_result: DecisionCorrectnessResult = evaluate_correctness(chosen, signals)
        routing_codes = list(getattr(meta, "routing_reason_codes", None) or [])
        merged_reason_codes = routing_codes + list(dcf_result.reason_codes)
        result.response.metadata.decision_correctness = {
            "verdict": dcf_result.verdict.value,
            "min_required": dcf_result.min_required.value if dcf_result.min_required else None,
            "max_allowed": dcf_result.max_allowed.value if dcf_result.max_allowed else None,
            "reason_codes": merged_reason_codes,
        }

    def attach_trace_and_return(
        self,
        result: OrchestratorResult,
        request: ProcessedRequest,
        execution_trace: dict[str, dict[str, Any]],
    ) -> OrchestratorResult:
        """Updates execution_trace with path/final_action and attaches to
        result (for diagnostics)."""
        rid = getattr(request, "request_id", "") or ""
        with self._trace_lock:
            if rid and rid in execution_trace:
                path_val = getattr(result, "path", "") or ""
                meta_resp = getattr(result.response, "metadata", None)
                final_action_val = (getattr(meta_resp, "final_action", "") if meta_resp else "") or ""
                execution_trace[rid]["path"] = path_val
                execution_trace[rid]["final_action"] = final_action_val
                if not execution_trace[rid].get("parser_logs"):
                    has_error = getattr(result, "error", None)
                    status = "ERROR" if has_error else "OK"
                    execution_trace[rid].setdefault("parser_logs", []).append(
                        {
                            "parser_status": status,
                            "raw_output_keys": [],
                            "parsed_output_keys": [],
                            "final_action": final_action_val,
                            "path_decision": path_val,
                            "risk_level": None,
                            "module_name": "orchestrator",
                        }
                    )
                result.execution_trace = dict(execution_trace[rid])
        return result

    def ensure_final_action_and_return(
        self,
        result: OrchestratorResult,
        request: ProcessedRequest,
        start_time: float,
        return_point: str | None,
        attach_trace_fn: Callable[[OrchestratorResult, ProcessedRequest], OrchestratorResult],
    ) -> OrchestratorResult:
        """
        Asserts final_action is set and valid before returning.
        FAIL_SAFE only for undefined or invalid final_action (last-resort).
        DCF: after ensuring final_action, evaluates correctness and saves to
        metadata.decision_correctness.
        attach_trace_fn(result, request) must return OrchestratorResult with trace attached.
        """
        meta_fa = (result.response.metadata.final_action or "").strip().upper()
        if meta_fa in ("REFUSE", "SAFE_COMPLETE", "NORMAL_COMPLETE"):
            res_path = getattr(result, "path", "") or ""
            res_cycles = getattr(result, "total_cycles", 0)
            if res_path == "DELIBERATIVE_PATH" and res_cycles == 0:
                log_deliberation_inconsistency(
                    getattr(request, "request_id", "") or "",
                    res_path,
                    meta_fa,
                    return_point or "_ensure_final_action_and_return",
                    "none",
                )
            self.attach_decision_correctness(result)
            return attach_trace_fn(result, request)

        final_action = response_type_to_final_action(result.response.response_type)
        if final_action is None or final_action not in (
            FinalAction.NORMAL_COMPLETE,
            FinalAction.SAFE_COMPLETE,
            FinalAction.REFUSE,
        ):
            processing_time = int((time.time() - start_time) * 1000)
            return attach_trace_fn(
                OrchestratorResult(
                    response=FinalResponse.safe_default(processing_time),
                    request_id=request.request_id,
                    path_taken=getattr(result, "path_taken", "fast") or "fast",
                    path="DELIBERATIVE_PATH",
                    total_cycles=getattr(result, "total_cycles", 0),
                    converged=False,
                    error="final_action_undefined",
                ),
                request,
            )
        if not result.response.metadata.final_action and final_action is not None:
            result.response.metadata.final_action = (
                final_action.value if hasattr(final_action, "value") else str(final_action)
            )
        res_path = getattr(result, "path", "") or ""
        res_cycles = getattr(result, "total_cycles", 0)
        if res_path == "DELIBERATIVE_PATH" and res_cycles == 0:
            log_deliberation_inconsistency(
                getattr(request, "request_id", "") or "",
                res_path,
                (
                    meta_fa
                    if meta_fa in ("REFUSE", "SAFE_COMPLETE", "NORMAL_COMPLETE")
                    else (final_action.value if hasattr(final_action, "value") else str(final_action))
                ),
                return_point or "_ensure_final_action_and_return",
                "none",
            )
        self.attach_decision_correctness(result)
        return attach_trace_fn(result, request)
