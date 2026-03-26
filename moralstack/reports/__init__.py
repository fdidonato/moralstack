"""
MoralStack Reports - Markdown export and rendering.

Unified model (RequestReport) and renderer (render_request_report) for
request/deliberation reports; benchmark report markdown via markdown_export.
"""

from moralstack.reports.benchmark_report_loader import (
    get_benchmark_result_by_request_id,
    get_questions_by_category,
    load_benchmark_report,
)
from moralstack.reports.markdown_export import (
    build_benchmark_report_markdown,
    export_request_markdown,
    export_run_benchmark_markdown,
)
from moralstack.reports.model import (
    CallLogEntry,
    PhaseInfo,
    RequestReport,
    RevisionEntry,
    request_report_from_cli,
    request_report_from_db,
)
from moralstack.reports.renderer_markdown import (
    render_request_report,
)

__all__ = [
    "build_benchmark_report_markdown",
    "export_request_markdown",
    "export_run_benchmark_markdown",
    "load_benchmark_report",
    "get_benchmark_result_by_request_id",
    "get_questions_by_category",
    "RequestReport",
    "PhaseInfo",
    "RevisionEntry",
    "CallLogEntry",
    "request_report_from_db",
    "request_report_from_cli",
    "render_request_report",
]
