"""E2E: OrchestrationController + DefaultPersistence writes real requests rows."""

from __future__ import annotations

import sqlite3

from moralstack.models.risk import OperationalRisk, RiskCategory
from moralstack.models.risk.schema import RiskEstimation
from moralstack.observability.context import set_current_run_id
from moralstack.orchestration.default_persistence import DefaultPersistence
from tests.test_orchestrator import MockCritic, MockPolicyLLM, create_orchestrator


def test_default_persistence_e2e_writes_request_row(tmp_path, monkeypatch):
    db_path = str(tmp_path / "e2e.db")
    monkeypatch.setenv("MORALSTACK_DB_PATH", db_path)
    monkeypatch.setenv("MORALSTACK_PERSIST_MODE", "db_only")
    run_id = "run-e2e-1"
    set_current_run_id(run_id)

    class _RiskWithDomain:
        def estimate(self, prompt: str, **kwargs):
            return RiskEstimation(
                score=0.1,
                confidence=0.9,
                risk_category=RiskCategory.BENIGN,
                operational_risk=OperationalRisk.NONE,
                detected_domain="general",
            )

    orch = create_orchestrator(
        policy=MockPolicyLLM(),
        risk_estimator=_RiskWithDomain(),
        critic=MockCritic(),
    )
    orch._controller._persistence = DefaultPersistence()

    result = orch.process("Hello")
    assert result.request_id

    conn = sqlite3.connect(db_path)
    row = conn.execute(
        "SELECT prompt, domain FROM requests WHERE request_id = ?",
        (result.request_id,),
    ).fetchone()
    conn.close()
    assert row is not None
    assert row[0] == "Hello"
    assert row[1] == "general"


def test_detected_domain_core_skips_update_request_domain(tmp_path, monkeypatch):
    db_path = str(tmp_path / "core.db")
    monkeypatch.setenv("MORALSTACK_DB_PATH", db_path)
    monkeypatch.setenv("MORALSTACK_PERSIST_MODE", "db_only")
    run_id = "run-core-1"
    set_current_run_id(run_id)

    class _RiskCore:
        def estimate(self, prompt: str, **kwargs):
            return RiskEstimation(
                score=0.1,
                confidence=0.9,
                risk_category=RiskCategory.BENIGN,
                operational_risk=OperationalRisk.NONE,
                detected_domain="core",
            )

    persistence = DefaultPersistence()
    update_calls: list[tuple[str, str | None]] = []
    original_update = persistence.update_request_domain

    def _spy_update(request_id: str, domain: str | None) -> None:
        update_calls.append((request_id, domain))
        original_update(request_id, domain)

    persistence.update_request_domain = _spy_update  # type: ignore[method-assign]

    orch = create_orchestrator(
        policy=MockPolicyLLM(),
        risk_estimator=_RiskCore(),
        critic=MockCritic(),
    )
    orch._controller._persistence = persistence

    result = orch.process("Hello")
    assert result.request_id
    assert update_calls == []

    conn = sqlite3.connect(db_path)
    domain = conn.execute(
        "SELECT domain FROM requests WHERE request_id = ?",
        (result.request_id,),
    ).fetchone()[0]
    conn.close()
    assert domain in (None, "")
