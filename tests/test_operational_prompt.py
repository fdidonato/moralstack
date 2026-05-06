"""Regression tests for operational-risk mini-estimator prompt contracts."""

from __future__ import annotations

from moralstack.models.risk.prompts import OPERATIONAL_RISK_PROMPT_TEMPLATE


def test_operational_prompt_socio_behavioral_offensive_framing() -> None:
    assert "OFFENSIVE framing (socio-behavioral / interpersonal exploitation)" in OPERATIONAL_RISK_PROMPT_TEMPLATE


def test_operational_prompt_step2_parallel_technical_and_socio_high() -> None:
    assert "Socio-behavioral HIGH (parallel to technical HIGH):" in OPERATIONAL_RISK_PROMPT_TEMPLATE


def test_operational_prompt_step3_scope_note_decouples_checklist_from_grooming() -> None:
    assert "Scope — physical/chemical/cyber checklist vs interpersonal exploitation:" in (OPERATIONAL_RISK_PROMPT_TEMPLATE)


def test_operational_prompt_pre_output_rationale_rewrite_checkbox() -> None:
    assert "rewrite rationale" in OPERATIONAL_RISK_PROMPT_TEMPLATE
    assert "grooming, minor-contact tactics" in OPERATIONAL_RISK_PROMPT_TEMPLATE


def test_operational_prompt_rationale_requires_substantive_payload_not_vague_wrappers() -> None:
    assert "harmful behaviors" in OPERATIONAL_RISK_PROMPT_TEMPLATE
    assert "substantive semantic payload" in OPERATIONAL_RISK_PROMPT_TEMPLATE
    assert "wrapper-only summaries" in OPERATIONAL_RISK_PROMPT_TEMPLATE


def test_operational_prompt_step3_typography_fix() -> None:
    """hazard awareness must not run into next sentence."""
    assert "hazard awareness.\nClassify operational risk" in OPERATIONAL_RISK_PROMPT_TEMPLATE
