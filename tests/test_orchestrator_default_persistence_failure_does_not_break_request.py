"""DefaultPersistence swallows DB failures and never breaks process()."""

from __future__ import annotations

from unittest.mock import patch

from moralstack.observability.context import set_current_run_id
from moralstack.orchestration.default_persistence import DefaultPersistence
from tests.test_orchestrator import MockCritic, MockPolicyLLM, MockRiskEstimator, create_orchestrator


def test_ensure_run_and_upsert_failure_does_not_break_process(tmp_path, monkeypatch):
    db_path = str(tmp_path / "fail.db")
    monkeypatch.setenv("MORALSTACK_DB_PATH", db_path)
    monkeypatch.setenv("MORALSTACK_PERSIST_MODE", "db_only")
    set_current_run_id("run-fail-1")

    orch = create_orchestrator(
        policy=MockPolicyLLM(),
        risk_estimator=MockRiskEstimator(default_score=0.2),
        critic=MockCritic(),
    )
    orch._controller._persistence = DefaultPersistence()

    with patch("moralstack.orchestration.default_persistence.upsert_request", side_effect=RuntimeError("db down")):
        result = orch.process("Hello")

    assert result.response is not None
    assert result.request_id
