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
        from moralstack.observability.emit_helpers import persist_decision_trace
        from moralstack.observability.sinks.sqlite_sink import create_run, init_db, upsert_request
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


class TestRiskRationaleFromDb:
    """Characterization pins (risk-estimator-dual-config C0) for the risk rationale built from
    ``risk_estimator`` rows by ``request_report_from_db`` and ``request_report_from_cli``."""

    @staticmethod
    def _report_for_rows(tmp_path, monkeypatch, rows):
        import json

        from moralstack.observability import obs
        from moralstack.observability.emit_helpers import persist_decision_trace, persist_llm_call
        from moralstack.observability.sinks.sqlite_sink import create_run, init_db, upsert_request
        from moralstack.reports.model import request_report_from_db

        db_path = str(tmp_path / "report_rationale.db")
        monkeypatch.setenv("MORALSTACK_DB_PATH", db_path)
        monkeypatch.setenv("MORALSTACK_PERSIST_MODE", "db_only")
        assert init_db(db_path)
        run_id, request_id = "run-rationale", "req-rationale"
        assert create_run(run_id, run_type="test", meta={})
        assert upsert_request(run_id, request_id, prompt="Hello", domain="general")
        trace_payload = {"path": "FAST_PATH", "total_cycles": 0, "final_action": "NORMAL_COMPLETE", "risk_score": 0.2}
        assert persist_decision_trace(
            run_id=run_id, request_id=request_id, stage="FINAL", sequence=2, trace_json=json.dumps(trace_payload)
        )
        for started_at, (action, raw) in enumerate(rows, start=1000):
            assert persist_llm_call(
                run_id=run_id,
                request_id=request_id,
                cycle=0,
                phase="risk_estimation",
                module="risk_estimator",
                action=action,
                model="test",
                started_at=started_at,
                duration_ms=1.0,
                prompt="p",
                system_prompt="s",
                raw_response=raw,
                sequence_in_cycle=None,
            )
        obs.flush(timeout=10.0)
        report = request_report_from_db(run_id, request_id)
        assert report is not None
        return report

    def test_intent_operational_and_guard_rows_build_the_merged_rationale(self, tmp_path, monkeypatch):
        """Stays green through every phase: the three live row kinds feed the rationale."""
        import json

        report = self._report_for_rows(
            tmp_path,
            monkeypatch,
            [
                ("estimate_intent", json.dumps({"rationale": "asks for a recipe"})),
                ("estimate_operational", json.dumps({"rationale": "low actionability"})),
                ("calibration_guard", json.dumps({"rationale": "capped to 0.45"})),
            ],
        )

        assert report.risk_rationale == "[intent] asks for a recipe | [op_risk] low actionability | ⚡ capped to 0.45"
        assert report.calibration_guard_info == "capped to 0.45"

    def test_legacy_estimate_row_no_longer_feeds_the_rationale(self, tmp_path, monkeypatch):
        """Flipped in C1: the legacy single-estimator ``estimate`` row no longer feeds the rationale (-> "").

        Justification: the mono estimator was removed in 178b8cf and 0 such rows were found in 114 DBs.
        """
        import json

        report = self._report_for_rows(
            tmp_path, monkeypatch, [("estimate", json.dumps({"rationale": "legacy single estimate"}))]
        )

        assert report.risk_rationale == ""

    def test_cli_text_risk_row_yields_no_rationale(self):
        """Stays green: the only CLI ``risk_estimator`` log_call writes non-JSON text ("Risk: ...")."""
        from types import SimpleNamespace

        from moralstack.cli.models import DeliberationTrace

        trace = DeliberationTrace(
            request_id="cli-risk-row",
            prompt="Hi",
            path="fast",
            risk_score=0.3,
            risk_category="",
            response_type="NORMAL_COMPLETE",
            total_cycles=1,
            converged=True,
            phases=[],
        )
        call_logger = SimpleNamespace(
            calls=[
                {
                    "module": "risk_estimator",
                    "action": "estimate",
                    "prompt": "Hi",
                    "response": "Risk: 0.30, Category: sensitive",
                    "duration_ms": 1.0,
                }
            ]
        )

        report = request_report_from_cli(trace, call_logger, None, "Hi")

        assert report.risk_rationale == ""


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


# -----------------------------------------------------------------------------
# F6 follow-up C6: report status for pipeline failures (shared predicate)
# -----------------------------------------------------------------------------

_FAILURE_PRINCIPLES = ["SYSTEM.ERROR", "SYSTEM.TIMEOUT", "SYSTEM.FAIL_SAFE"]
_PRE_FINAL_ROWS = {
    "s1": [],
    "s2": [
        ("RISK_ASSESSMENT", {"risk_score": 0.6, "risk_category": "moderate"}),
        ("PRE_POLICY", {"final_action": "SAFE_COMPLETE", "path": "DELIBERATIVE_PATH"}),
    ],
    "s3": [
        ("RISK_ASSESSMENT", {"risk_score": 0.6, "risk_category": "moderate"}),
        ("PRE_POLICY", {"final_action": "SAFE_COMPLETE", "path": "DELIBERATIVE_PATH"}),
        ("RELEVANT_PRINCIPLES", {"relevant_principle_ids": ["p1", "p2"]}),
    ],
}
_UNSET = object()


def _seeded_report(tmp_path, monkeypatch, *, rows, meta, raw_meta=_UNSET):
    """Seed one request (meta dict, or the raw meta_json text) plus decision-trace rows and build the DB report."""
    import json
    import sqlite3

    from moralstack.observability import obs
    from moralstack.observability.emit_helpers import persist_decision_trace
    from moralstack.observability.sinks.sqlite_sink import (
        create_run,
        init_db,
        update_request_meta,
        update_request_response,
        upsert_request,
    )
    from moralstack.reports.model import request_report_from_db

    db_path = str(tmp_path / "report_status.db")
    monkeypatch.setenv("MORALSTACK_DB_PATH", db_path)
    monkeypatch.setenv("MORALSTACK_PERSIST_MODE", "db_only")
    assert init_db(db_path)
    run_id, request_id = "run-status", "req-status"
    assert create_run(run_id, run_type="test", meta={})
    assert upsert_request(run_id, request_id, prompt="do something", domain="general")
    if meta is not None:
        assert update_request_meta(run_id, request_id, meta)
    assert update_request_response(run_id, request_id, "[SYSTEM_ERROR]")
    for sequence, (stage, payload) in enumerate(rows, start=1):
        assert persist_decision_trace(
            run_id=run_id, request_id=request_id, stage=stage, sequence=sequence, trace_json=json.dumps(payload)
        )
    obs.flush(timeout=10.0)
    if raw_meta is not _UNSET:
        conn = sqlite3.connect(db_path)
        try:
            conn.execute(
                "UPDATE requests SET meta_json = ? WHERE run_id = ? AND request_id = ?", (raw_meta, run_id, request_id)
            )
            conn.commit()
        finally:
            conn.close()
    report = request_report_from_db(run_id, request_id)
    assert report is not None
    return report


class TestPipelineFailureStatus:
    """request_report_from_db flags a pipeline failure from structured signals only (never from text)."""

    @pytest.mark.parametrize("principle", _FAILURE_PRINCIPLES)
    @pytest.mark.parametrize("shape", ["s1", "s2", "s3"])
    def test_status_flags_pipeline_failure(self, tmp_path, monkeypatch, shape, principle):
        meta = {"final_action": "REFUSE", "triggered_principles": [principle]}
        report = _seeded_report(tmp_path, monkeypatch, rows=_PRE_FINAL_ROWS[shape], meta=meta)

        assert "PIPELINE FAILURE" in report.status
        assert principle in report.status
        assert "REFUSED" not in report.status
        assert "APPROVED" not in report.status

    @pytest.mark.parametrize(
        "action,expected",
        [
            ("NORMAL_COMPLETE", "✅ **APPROVED** - Response delivered."),
            ("SAFE_COMPLETE", "⚠ **APPROVED WITH CAVEATS** - Response includes disclaimers."),
            ("REFUSE", "🛑 **REFUSED** - Request refused due to policy."),
        ],
    )
    def test_status_unchanged_for_final_rows(self, tmp_path, monkeypatch, action, expected):
        rows = [("FINAL", {"final_action": action, "path": "FAST_PATH"})]
        report = _seeded_report(tmp_path, monkeypatch, rows=rows, meta={"final_action": action})

        assert report.status == expected

    def test_final_row_present_with_system_principle_unchanged(self, tmp_path, monkeypatch):
        rows = [("FINAL", {"final_action": "NORMAL_COMPLETE", "path": "FAST_PATH"})]
        meta = {"final_action": "NORMAL_COMPLETE", "triggered_principles": ["SYSTEM.ERROR"]}
        report = _seeded_report(tmp_path, monkeypatch, rows=rows, meta=meta)

        assert report.status == "✅ **APPROVED** - Response delivered."

    def test_no_final_no_principle_unchanged(self, tmp_path, monkeypatch):
        report = _seeded_report(tmp_path, monkeypatch, rows=[], meta={"final_action": "REFUSE"})

        assert report.status == "📋 **COMPLETED** - Outcome from trace."

    @pytest.mark.parametrize(
        "raw_meta",
        [None, "not json", "[]", "null", '{"triggered_principles":"SYSTEM.ERROR"}'],
    )
    def test_malformed_meta_json_never_raises(self, tmp_path, monkeypatch, raw_meta):
        report = _seeded_report(tmp_path, monkeypatch, rows=[], meta=None, raw_meta=raw_meta)

        assert report.status == "📋 **COMPLETED** - Outcome from trace."

    def test_markdown_renders_failure_status(self, tmp_path, monkeypatch):
        meta = {"final_action": "REFUSE", "triggered_principles": ["SYSTEM.TIMEOUT"]}
        report = _seeded_report(tmp_path, monkeypatch, rows=[], meta=meta)

        assert "PIPELINE FAILURE" in render_executive_summary(report)
        assert "PIPELINE FAILURE" in render_request_report(report)


class TestCliReportPipelineFailure:
    """request_report_from_cli: a SYSTEM.* failure principle is not reported as a policy refusal."""

    @staticmethod
    def _cli_trace(response_type):
        from moralstack.cli.models import DeliberationTrace

        return DeliberationTrace(
            request_id="cli-failure",
            prompt="Hi",
            path="fast",
            risk_score=0.3,
            risk_category="",
            response_type=response_type,
            total_cycles=0,
            converged=False,
            phases=[],
        )

    @pytest.mark.parametrize(
        "exc_name,principle",
        [
            ("GenerationError", "SYSTEM.ERROR"),
            ("OrchestratorTimeoutError", "SYSTEM.TIMEOUT"),
            ("ValueError", "SYSTEM.FAIL_SAFE"),
        ],
    )
    def test_cli_report_flags_pipeline_failure(self, request, monkeypatch, exc_name, principle):
        from moralstack.orchestration import types as orch_types
        from tests.test_pipeline_error_fail_closed import _ctrl, _fail

        exc_cls = getattr(orch_types, exc_name, None) or ValueError
        result = _fail(_ctrl(request=request), monkeypatch, exc_cls("x"))
        assert result.response.metadata.triggered_principles == [principle]

        report = request_report_from_cli(self._cli_trace("full_refusal"), None, result, "Hi")

        assert "PIPELINE FAILURE" in report.status
        assert principle in report.status
        assert "policy violations" not in report.status

    def test_cli_report_policy_refusal_unchanged(self):
        from types import SimpleNamespace

        result = SimpleNamespace(
            response=SimpleNamespace(content="No.", metadata=SimpleNamespace(triggered_principles=["SAFETY.HARM.1"])),
            trace=None,
        )
        report = request_report_from_cli(self._cli_trace("full_refusal"), None, result, "Hi")

        assert "REFUSED" in report.status
        assert "policy violations" in report.status
        assert "PIPELINE FAILURE" not in report.status
