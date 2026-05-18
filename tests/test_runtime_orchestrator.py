"""Tests for moralstack.runtime.orchestrator facade."""

from unittest.mock import MagicMock

from moralstack.runtime.orchestrator import Orchestrator


class TestOrchestratorLedgerWiring:
    """Verify the ledger parameter is propagated to the underlying controller."""

    def test_default_ledger_is_none(self) -> None:
        """By default, no ledger is wired."""
        orch = Orchestrator()
        assert orch.ledger is None
        assert orch._controller._ledger is None

    def test_explicit_ledger_is_propagated(self) -> None:
        """When a ledger is passed, it reaches the OrchestrationController."""
        fake_ledger = MagicMock(name="FakeLedger")
        orch = Orchestrator(ledger=fake_ledger)
        assert orch.ledger is fake_ledger
        assert orch._controller._ledger is fake_ledger
