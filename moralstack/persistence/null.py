"""
Null implementation of PersistencePort: no-op for all operations.

Used when persistence is disabled or when the orchestration layer
should not depend on the persistence module at runtime.
"""

from __future__ import annotations


class NullPersistence:
    """No-op implementation of PersistencePort. All methods do nothing."""

    def set_request_context(self, request_id: str) -> None:
        """No-op."""
        pass

    def ensure_run_and_upsert_request(
        self,
        request_id: str,
        prompt: str,
        domain: str | None = None,
    ) -> None:
        """No-op."""
        pass

    def update_request_domain(self, request_id: str, domain: str | None) -> None:
        """No-op."""
        pass
