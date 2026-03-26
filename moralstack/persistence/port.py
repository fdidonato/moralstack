"""
Persistence port: protocol for request-scoped persistence (context + upsert).

Allows the orchestration layer to depend on an abstraction so that
persistence can be disabled (NullPersistence) or implemented with real DB.
"""

from __future__ import annotations

from typing import Protocol


class PersistencePort(Protocol):
    """Protocol for setting request context and ensuring request is persisted in the current run."""

    def set_request_context(self, request_id: str) -> None:
        """Set the current request id in the persistence context (e.g. for correlation)."""
        ...

    def ensure_run_and_upsert_request(
        self,
        request_id: str,
        prompt: str,
        domain: str | None = None,
    ) -> None:
        """
        Ensure the current run exists and upsert the request.
        No-op if no run_id in context or no db path configured.
        Does not raise; failures are logged internally.
        """
        ...

    def update_request_domain(self, request_id: str, domain: str | None) -> None:
        """
        Update the stored domain for the current request (e.g. after risk detection).
        No-op if no run_id in context or no db path. Does not raise.
        """
        ...
