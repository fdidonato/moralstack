"""
Default implementation of PersistencePort using context, config, and db.

Uses set_current_request_id, get_current_run_id, get_db_path, init_db, upsert_request.
Does not raise; logs on failure.
"""

from __future__ import annotations

import logging

from moralstack.persistence.config import get_db_path
from moralstack.persistence.context import get_current_run_id, set_current_request_id
from moralstack.persistence.db import create_run, init_db, update_request_domain, upsert_request

logger = logging.getLogger(__name__)


class DefaultPersistence:
    """
    PersistencePort implementation that uses context vars and SQLite.
    set_request_context sets the current request_id; ensure_run_and_upsert_request
    reads run_id from context and upserts the request when run_id and db_path are set.
    init_db is called once at construction time, not on every request.
    """

    def __init__(self) -> None:
        self._db_initialized = False

    def _ensure_db_initialized(self) -> bool:
        if self._db_initialized:
            return True
        db_path = get_db_path()
        if not db_path:
            return False
        if init_db(db_path):
            self._db_initialized = True
        return self._db_initialized

    def set_request_context(self, request_id: str) -> None:
        """Set the current request id in the persistence context."""
        set_current_request_id(request_id)

    def ensure_run_and_upsert_request(
        self,
        request_id: str,
        prompt: str,
        domain: str | None = None,
        *,
        conversation_id: str | None = None,
        turn_index: int | None = None,
        parent_request_id: str | None = None,
    ) -> None:
        """
        If a run_id is set in context and db_path is configured, ensure DB is initialized
        and upsert run/request. Does not raise; logs warning on failure.
        """
        run_id = get_current_run_id()
        if not run_id:
            return
        if not get_db_path():
            return
        try:
            self._ensure_db_initialized()
            # Ensure parent row in runs exists to satisfy FKs; INSERT OR IGNORE preserves
            # existing run and its cascade-linked data.
            create_run(run_id=run_id, run_type="session", meta={})
            upsert_request(
                run_id=run_id,
                request_id=request_id,
                prompt=prompt,
                domain=domain,
                conversation_id=conversation_id,
                turn_index=turn_index,
                parent_request_id=parent_request_id,
            )
        except Exception as e:
            logger.warning("persistence: ensure_run_and_upsert_request failed: %s", e)

    def update_request_domain(self, request_id: str, domain: str | None) -> None:
        """Update the request domain in the DB. No-op if no run_id or db_path."""
        run_id = get_current_run_id()
        if not run_id or not get_db_path():
            return
        try:
            self._ensure_db_initialized()
            update_request_domain(run_id=run_id, request_id=request_id, domain=domain)
        except Exception as e:
            logger.warning("persistence: update_request_domain failed: %s", e)
