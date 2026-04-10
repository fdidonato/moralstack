"""
Trace lifecycle: request-scoped trace creation, parser diagnostic handler, and trace fill from result.

Extracted from the controller to centralize trace setup/teardown and the repeated
pattern of filling trace from OrchestratorResult in route handlers.
"""

from __future__ import annotations

import json
import logging
import threading
import uuid
from typing import Any

from moralstack.orchestration.trace import Trace
from moralstack.orchestration.types import OrchestratorResult

_LOG = logging.getLogger(__name__)


def log_trace_event(event: str, location: str, trace: Trace, data: dict[str, Any]) -> None:
    """
    Log a structured JSON event (one line) with request_id and trace_id for parallel execution.
    Does not raise; logs warning on failure.
    """
    try:
        payload = {
            "event": event,
            "location": location,
            "request_id": trace.request_id,
            "trace_id": trace.trace_id,
            "data": data,
        }
        _LOG.info("%s", json.dumps(payload, ensure_ascii=False))
    except Exception as e:
        _LOG.warning("log_trace_event failed: %s", e)


def fill_trace_from_result(
    trace: Trace,
    result: OrchestratorResult,
    *,
    modules_called: set[str] | None = None,
) -> None:
    """
    Fill trace from an OrchestratorResult (response_type, cycles, modules_called, converged).
    Caller must set result.trace = trace after this.
    """
    trace.response_type = getattr(result.response.response_type, "value", str(result.response.response_type))
    trace.deliberation_cycles_actual = getattr(result, "total_cycles", 0)
    trace.modules_called = modules_called if modules_called is not None else set()
    trace.converged = getattr(result, "converged", True)


class TraceLifecycle:
    """
    Manages trace creation, parser diagnostic handler install/remove, and trace event logging.
    Holds references to the controller's trace_lock, execution_trace, and handler registry.
    """

    def __init__(
        self,
        trace_lock: threading.Lock,
        execution_trace: dict[str, dict[str, Any]],
        parser_diagnostic_handlers: dict[str, logging.Handler],
    ):
        self._trace_lock = trace_lock
        self._execution_trace = execution_trace
        self._parser_diagnostic_handlers = parser_diagnostic_handlers

    def start_trace(self, request_id: str) -> Trace:
        """
        Initialize execution_trace slot for request_id, install parser diagnostic handler,
        create and return a new Trace. Call remove_parser_diagnostic_handler in finally.
        """
        with self._trace_lock:
            self._execution_trace[request_id] = {
                "parser_logs": [],
                "path": "",
                "final_action": "",
            }
        self._install_parser_diagnostic_handler(request_id)
        trace = Trace(request_id=request_id, trace_id=str(uuid.uuid4()))
        log_trace_event("process_start", "orchestrator.process", trace, {"request_id": request_id})
        return trace

    def remove_parser_diagnostic_handler(self, request_id: str) -> None:
        """Remove and unregister the parser diagnostic handler for request_id."""
        with self._trace_lock:
            handler = self._parser_diagnostic_handlers.pop(request_id, None)
        if handler is None:
            return
        try:
            from moralstack.utils import structured_output as so_module

            logging.getLogger(so_module.__name__).removeHandler(handler)
        except Exception as e:
            _LOG.warning("remove_parser_diagnostic_handler failed: %s", e)

    def _install_parser_diagnostic_handler(self, request_id: str) -> None:
        from moralstack.utils import structured_output as so_module

        log = logging.getLogger(so_module.__name__)
        execution_trace = self._execution_trace
        trace_lock = self._trace_lock
        handler_ref = self._parser_diagnostic_handlers

        class ParserDiagnosticHandler(logging.Handler):
            def emit(self, record: logging.LogRecord) -> None:
                if not getattr(record, "moralstack_parser_diagnostic", False):
                    return
                rid = getattr(record, "request_id", "") or request_id
                with trace_lock:
                    if rid not in execution_trace:
                        execution_trace[rid] = {
                            "parser_logs": [],
                            "path": "",
                            "final_action": "",
                        }
                    execution_trace[rid]["parser_logs"].append(
                        {
                            "parser_status": getattr(record, "parser_status", ""),
                            "raw_output_keys": getattr(record, "raw_output_keys", []),
                            "parsed_output_keys": getattr(record, "parsed_output_keys", []),
                            "final_action": getattr(record, "final_action", ""),
                            "path_decision": getattr(record, "path_decision", ""),
                            "risk_level": getattr(record, "risk_level", None),
                        }
                    )

        handler = ParserDiagnosticHandler()
        with self._trace_lock:
            handler_ref[request_id] = handler
        log.addHandler(handler)
