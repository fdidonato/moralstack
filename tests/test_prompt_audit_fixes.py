"""
Targeted tests for prompt audit fixes (Findings 1, 3, 4, 5, 6).

Verifies: perspectives RISK CONTEXT in full mode, simulator domain guidance,
intent constitution ordering, report converged for 1-cycle, get_final_response_text fallback.
"""

import json

from moralstack.models.delib_context import DelibContext
from moralstack.models.risk.prompts import INTENT_CONTEXT_PROMPT_TEMPLATE

# Persistence imports for report test
from moralstack.persistence.db import create_run, init_db, upsert_request
from moralstack.persistence.sink import persist_decision_trace
from moralstack.prompts.perspectives_prompt import build_perspectives_system_prompt
from moralstack.prompts.simulator_prompt import (
    DEFAULT_DOMAIN_GUIDANCE,
    DOMAIN_GUIDANCE,
    build_simulator_prompt,
)
from moralstack.reports.model import get_final_response_text, request_report_from_db

# -----------------------------------------------------------------------------
# Finding 3: Perspectives OPT-2 full mode includes RISK CONTEXT
# -----------------------------------------------------------------------------


class TestPerspectivesFullModeRiskContext:
    """RISK CONTEXT must appear in shared system prompt in full mode (Finding 3)."""

    def test_full_mode_includes_risk_context_section(self):
        ctx = DelibContext(
            user_prompt="User request.",
            draft_text_full="Draft response.",
            risk_score=0.6,
            risk_category="clearly_harmful",
            intent_to_harm=True,
        )
        out = build_perspectives_system_prompt(ctx)
        assert "RISK CONTEXT:" in out
        assert "risk_score=0.60" in out
        assert "risk_category=clearly_harmful" in out
        assert "intent_to_harm=true" in out


# -----------------------------------------------------------------------------
# Finding 4: Simulator domain-specific guidance by domain
# -----------------------------------------------------------------------------


class TestSimulatorDomainGuidance:
    """Domain guidance must be injected by domain, not hardcoded cybersecurity (Finding 4)."""

    def test_cybersecurity_domain_gets_cybersecurity_guidance(self):
        ctx = DelibContext(user_prompt="Request", draft_text_full="Response")
        ctx.domain = "cybersecurity"
        prompt = build_simulator_prompt(ctx, num_scenarios=3)
        assert "CYBERSECURITY" in prompt
        assert "DEFENSIVE intent" in prompt
        assert "OFFENSIVE intent" in prompt

    def test_general_domain_gets_default_guidance(self):
        ctx = DelibContext(user_prompt="Request", draft_text_full="Response")
        prompt = build_simulator_prompt(ctx, num_scenarios=3)
        assert DEFAULT_DOMAIN_GUIDANCE in prompt
        assert "common security mistakes" not in prompt

    def test_domain_guidance_dict_has_cybersecurity(self):
        assert "cybersecurity" in DOMAIN_GUIDANCE
        assert "DEFENSIVE" in DOMAIN_GUIDANCE["cybersecurity"]


# -----------------------------------------------------------------------------
# Finding 1: Constitution context before PRE-OUTPUT COHERENCE CHECK
# -----------------------------------------------------------------------------


class TestIntentConstitutionOrdering:
    """Constitution context must appear before coherence check, not after JSON instruction."""

    def test_constitution_context_placeholder_before_coherence_check(self):
        context_block = "RELEVANT ETHICAL PRINCIPLES FROM CONSTITUTION (for context):\n\nHARD: P1"
        prompt = INTENT_CONTEXT_PROMPT_TEMPLATE.format(
            request="Tell me how to hack",
            constitution_context=context_block,
        )
        idx_coherence = prompt.find("PRE-OUTPUT COHERENCE CHECK")
        idx_constitution = prompt.find("RELEVANT ETHICAL PRINCIPLES")
        idx_return_json = prompt.find("Return ONLY valid JSON")
        assert idx_coherence != -1
        assert idx_constitution != -1
        assert idx_return_json != -1
        # Constitution block must appear before coherence check
        assert idx_constitution < idx_coherence
        # Coherence check must appear before "Return ONLY valid JSON"
        assert idx_coherence < idx_return_json

    def test_empty_constitution_context_does_not_break_format(self):
        prompt = INTENT_CONTEXT_PROMPT_TEMPLATE.format(
            request="Hello",
            constitution_context="",
        )
        assert "PRE-OUTPUT COHERENCE CHECK" in prompt
        assert "Return ONLY valid JSON" in prompt


# -----------------------------------------------------------------------------
# Finding 6: Report converged=True when total_cycles=1 and CYCLES_EXHAUSTED
# -----------------------------------------------------------------------------


class TestReportOneCycleConverged:
    """With max_deliberation_cycles=1, CYCLES_EXHAUSTED + non-REFUSE => converged (Finding 6)."""

    def test_one_cycle_cycles_exhausted_non_refuse_reports_converged_true(self, tmp_path, monkeypatch):
        db_path = str(tmp_path / "report_one_cycle.db")
        monkeypatch.setenv("MORALSTACK_DB_PATH", db_path)
        monkeypatch.setenv("MORALSTACK_PERSIST_MODE", "db_only")

        assert init_db(db_path)
        run_id = "run-one-cycle"
        request_id = "req-one-cycle"
        assert create_run(run_id, run_type="test", meta={})
        assert upsert_request(run_id, request_id, prompt="Hello", domain="general")

        trace_payload = {
            "path": "DELIBERATIVE_PATH",
            "total_cycles": 1,
            "stop_reason": "CYCLES_EXHAUSTED",
            "final_action": "NORMAL_COMPLETE",
            "risk_score": 0.3,
            "risk_category": "benign",
        }
        assert persist_decision_trace(
            run_id=run_id,
            request_id=request_id,
            stage="FINAL",
            sequence=1,
            trace_json=json.dumps(trace_payload),
        )

        report = request_report_from_db(run_id, request_id)
        assert report is not None
        assert report.total_cycles == 1
        assert report.converged is True

    def test_one_cycle_cycles_exhausted_refuse_reports_converged_true(self, tmp_path, monkeypatch):
        db_path = str(tmp_path / "report_one_cycle_refuse.db")
        monkeypatch.setenv("MORALSTACK_DB_PATH", db_path)
        monkeypatch.setenv("MORALSTACK_PERSIST_MODE", "db_only")

        assert init_db(db_path)
        run_id = "run-one-cycle-refuse"
        request_id = "req-one-cycle-refuse"
        assert create_run(run_id, run_type="test", meta={})
        assert upsert_request(run_id, request_id, prompt="Hello", domain="general")

        trace_payload = {
            "path": "DELIBERATIVE_PATH",
            "total_cycles": 1,
            "stop_reason": "CYCLES_EXHAUSTED",
            "final_action": "REFUSE",
            "risk_score": 0.9,
            "risk_category": "clearly_harmful",
        }
        assert persist_decision_trace(
            run_id=run_id,
            request_id=request_id,
            stage="FINAL",
            sequence=1,
            trace_json=json.dumps(trace_payload),
        )

        report = request_report_from_db(run_id, request_id)
        assert report is not None
        assert report.total_cycles == 1
        assert report.converged is True


# -----------------------------------------------------------------------------
# Finding 5: get_final_response_text REFUSE fallback
# -----------------------------------------------------------------------------


class TestGetFinalResponseTextRefuseFallback:
    """When REFUSE and no call with 'refuse' in action, fallback to last call (Finding 5)."""

    def test_refuse_uses_last_call_raw_response_when_no_refuse_action(self):
        calls = [
            {"action": "critic", "raw_response": "Critic output."},
            {"action": "generate", "raw_response": "I cannot assist with that."},
        ]
        assert get_final_response_text(calls, "REFUSE") == "I cannot assist with that."

    def test_refuse_returns_empty_when_calls_empty(self):
        assert get_final_response_text([], "REFUSE") == ""


class TestRequestReportRefuseResponseFromTrace:
    """When REFUSE and llm_calls are missing/delayed, report should recover final response from decision traces."""

    def test_report_uses_response_content_from_decision_trace(self, tmp_path, monkeypatch):
        db_path = str(tmp_path / "report_refuse_response_trace.db")
        monkeypatch.setenv("MORALSTACK_DB_PATH", db_path)
        monkeypatch.setenv("MORALSTACK_PERSIST_MODE", "db_only")

        assert init_db(db_path)
        run_id = "run-refuse-trace"
        request_id = "req-refuse-trace"
        assert create_run(run_id, run_type="test", meta={})
        assert upsert_request(run_id, request_id, prompt="Hi", domain="general")

        final_trace = {
            "path": "DELIBERATIVE_PATH",
            "total_cycles": 1,
            "stop_reason": "CYCLES_EXHAUSTED",
            "final_action": "REFUSE",
            "risk_score": 0.9,
            "risk_category": "clearly_harmful",
        }
        response_trace = {
            "path": "DELIBERATIVE_PATH",
            "final_action": "REFUSE",
            "total_cycles": 1,
            "response_content": "I cannot help with that request. Here are safe alternatives.",
        }
        assert persist_decision_trace(
            run_id=run_id,
            request_id=request_id,
            stage="FINAL",
            sequence=1,
            trace_json=json.dumps(final_trace),
        )
        assert persist_decision_trace(
            run_id=run_id,
            request_id=request_id,
            stage="RESPONSE",
            sequence=3,
            trace_json=json.dumps(response_trace),
        )

        report = request_report_from_db(run_id, request_id)
        assert report is not None
        assert "I cannot help with that request" in report.response_content
