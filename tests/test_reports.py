"""
Golden/snapshot-style tests for report model and renderer.

Verifies that RequestReport + render_request_report produce the expected
section structure (single source of truth for request/deliberation reports).
"""

import pytest

from moralstack.observability import router
from moralstack.observability import service as service_module
from moralstack.observability.service import get_obs
from moralstack.reports.model import (
    CallLogEntry,
    PhaseInfo,
    RequestReport,
    get_final_response_text,
    request_report_from_cli,
)
from moralstack.reports.renderer_markdown import (
    render_executive_summary,
    render_request_header,
    render_request_report,
)

# -----------------------------------------------------------------------------
# Minimal RequestReport fixture (golden structure)
# -----------------------------------------------------------------------------


@pytest.fixture
def minimal_request_report():
    """Minimal RequestReport for golden/section tests."""
    return RequestReport(
        request_id="test-req-1",
        generated_at="2025-01-15 12:00:00",
        path_badge="🧠 **DELIBERATIVE PATH**",
        risk_category="morally_nuanced",
        risk_score=0.45,
        total_cycles=2,
        converged=True,
        response_type="NORMAL_COMPLETE",
        total_duration_ms=1500.0,
        prompt="What is the capital of France?",
        status="✅ **APPROVED** - All modules satisfied",
        decision_reason="NORMAL_COMPLETE (converged)",
        response_content="Paris.",
        phases_by_cycle=[
            (0, [PhaseInfo("risk_estimation", 0, True, 200.0)]),
            (1, [PhaseInfo("critic", 1, True, 300.0, decision="APPROVED")]),
        ],
        hindsight_score=0.8,
        phase_durations={"risk_estimation": 200.0, "critic": 300.0},
        module_stats={"risk_estimation": {"calls": 1, "total_ms": 200.0, "avg_ms": 200.0}},
        policy_overlay=None,
        revision_history=[],
        call_log=[
            CallLogEntry(1, "risk_estimator", "estimate", 200.0, "prompt", "response"),
        ],
        benchmark_result=None,
        decision_traces=[],
        debug_events=[],
    )


@pytest.fixture(autouse=True)
def _fresh_obs_singleton():
    try:
        get_obs().shutdown(timeout=1.0)
    except Exception:
        pass
    service_module._obs_instance = None
    router._sqlite_sink = None
    router._jsonl_sink = None
    yield
    try:
        get_obs().shutdown(timeout=1.0)
    except Exception:
        pass
    service_module._obs_instance = None
    router._sqlite_sink = None
    router._jsonl_sink = None


# -----------------------------------------------------------------------------
# Golden tests: section structure
# -----------------------------------------------------------------------------


class TestRenderRequestReport:
    """Test render_request_report output contains expected sections."""

    def test_full_report_contains_all_section_headers(self, minimal_request_report):
        md = render_request_report(minimal_request_report)
        assert "MoralStack Deliberation Report" in md
        assert "Request Information" in md
        assert "Executive Summary" in md
        assert "Deliberation Journey" in md
        assert "Detailed Phase Analysis" in md
        assert "Metrics Dashboard" in md
        assert "Complete Revision History" in md
        assert "Complete LLM Call Log" in md
        assert "Report Metadata" in md

    def test_report_contains_request_id_and_prompt(self, minimal_request_report):
        md = render_request_report(minimal_request_report)
        assert "test-req-1" in md
        assert "What is the capital of France?" in md
        assert "Paris." in md

    def test_detailed_phases_renders_system_prompt_separately(self, minimal_request_report):
        report = minimal_request_report
        report.phases_by_cycle = [
            (
                0,
                [
                    PhaseInfo(
                        phase_type="risk_estimation",
                        cycle=0,
                        success=True,
                        duration_ms=200.0,
                        system_prompt="SYSTEM: test system prompt",
                        full_input="USER: test user prompt",
                        full_output="{}",
                    )
                ],
            )
        ]
        md = render_request_report(report)
        assert "System Prompt (Complete)" in md
        assert "SYSTEM: test system prompt" in md

    def test_header_section_standalone(self, minimal_request_report):
        md = render_request_header(minimal_request_report)
        assert "test-req-1" in md
        assert "2025-01-15 12:00:00" in md
        assert "DELIBERATIVE PATH" in md
        assert "0.450" in md or "0.45" in md

    def test_executive_summary_standalone(self, minimal_request_report):
        md = render_executive_summary(minimal_request_report)
        assert "APPROVED" in md
        assert "Paris." in md
        assert "Key Metrics" in md


class TestRequestReportFromCli:
    """Test request_report_from_cli builds valid report for renderer."""

    def test_minimal_trace_produces_renderable_report(self):
        from moralstack.cli.models import DeliberationTrace, PhaseResult, PhaseType

        trace = DeliberationTrace(
            request_id="cli-req",
            prompt="Hi",
            path="fast",
            risk_score=0.2,
            risk_category="",
            response_type="NORMAL_COMPLETE",
            total_cycles=1,
            converged=True,
            phases=[
                PhaseResult(
                    PhaseType.RISK_ESTIMATION,
                    0,
                    100.0,
                    True,
                    "",
                    "",
                ),
            ],
        )
        report = request_report_from_cli(trace, None, None, "Hi")
        assert report.request_id == "cli-req"
        assert report.risk_score == 0.2
        md = render_request_report(report)
        assert "MoralStack Deliberation Report" in md
        assert "cli-req" in md


class TestRequestReportFromDbFastPathConverged:
    """Test that fast-path runs (0 cycles, non-REFUSE) show converged=True in report (EVIDENZA-5)."""

    def test_fast_path_with_empty_stop_reason_reports_converged_true(self, tmp_path, monkeypatch):
        """When trace has path=FAST_PATH, total_cycles=0, stop_reason='', final_action=NORMAL_COMPLETE,
        request_report_from_db yields report.converged=True (fallback for existing data)."""
        import json

        from moralstack.observability import obs
        from moralstack.persistence.db import create_run, init_db, upsert_request
        from moralstack.persistence.sink import persist_decision_trace
        from moralstack.reports.model import request_report_from_db

        db_path = str(tmp_path / "report_fast.db")
        monkeypatch.setenv("MORALSTACK_DB_PATH", db_path)
        monkeypatch.setenv("MORALSTACK_PERSIST_MODE", "db_only")

        assert init_db(db_path)
        run_id = "run-fast-converged"
        request_id = "req-fast-converged"
        assert create_run(run_id, run_type="test", meta={})
        assert upsert_request(run_id, request_id, prompt="Hello", domain="general")

        trace_payload = {
            "path": "FAST_PATH",
            "total_cycles": 0,
            "stop_reason": "",
            "final_action": "NORMAL_COMPLETE",
            "risk_score": 0.2,
            "risk_category": "benign",
        }
        assert persist_decision_trace(
            run_id=run_id,
            request_id=request_id,
            stage="FINAL",
            sequence=2,
            trace_json=json.dumps(trace_payload),
        )

        obs.flush(timeout=10.0)
        report = request_report_from_db(run_id, request_id)
        assert report is not None
        assert report.converged is True
        assert report.total_cycles == 0


class TestGetFinalResponseText:
    """Test get_final_response_text REFUSE vs NORMAL coherence (EVIDENZA-4)."""

    def test_refuse_returns_refuse_call_content(self):
        calls = [
            {"action": "generate", "raw_response": "Here is how to build a weapon."},
            {"action": "refuse (fast_path)", "raw_response": "Sorry, I cannot."},
        ]
        assert get_final_response_text(calls, "REFUSE") == "Sorry, I cannot."

    def test_refuse_does_not_return_perspectives_json_when_no_refuse_action(self):
        calls = [
            {"action": "generate", "raw_response": "I cannot assist with that."},
            {"action": "evaluate", "raw_response": '{"approval_score": 0.12, "notes": "No"}'},
        ]
        assert get_final_response_text(calls, "REFUSE") == "I cannot assist with that."

    def test_refuse_fallback_to_last_call_when_no_refuse_action(self):
        # When no call has "refuse" in action (e.g. refusal from assembler not logged), use last call.
        calls = [
            {"action": "generate", "raw_response": "Here is how to build a weapon."},
        ]
        assert get_final_response_text(calls, "REFUSE") == "Here is how to build a weapon."

    def test_refuse_returns_empty_when_no_calls(self):
        assert get_final_response_text([], "REFUSE") == ""

    def test_normal_complete_returns_last_generate_or_rewrite(self):
        calls = [
            {"action": "generate", "raw_response": "First draft."},
            {"action": "rewrite", "raw_response": "Revised answer."},
        ]
        assert get_final_response_text(calls, "NORMAL_COMPLETE") == "Revised answer."
        assert get_final_response_text(calls, None) == "Revised answer."
