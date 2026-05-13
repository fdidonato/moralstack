"""
ReadStore: unique read contract for MoralStack observability data.

SqliteReadStore wraps the SQLite read functions from the original db.py,
now unified under a single Protocol so UI and reports depend on this
interface rather than on db.py directly.
"""

from __future__ import annotations

import logging
from typing import Any, Protocol

from moralstack.observability.config import get_db_path
from moralstack.observability.sinks.sqlite_sink import _get_connection

logger = logging.getLogger(__name__)


class ReadStore(Protocol):
    """Protocol: all query operations available to UI and reports."""

    def get_run(self, run_id: str) -> dict[str, Any] | None: ...

    def get_all_runs(self, limit: int = 100) -> list[dict[str, Any]]: ...

    def get_runs_page(
        self,
        page: int = 1,
        page_size: int = 20,
        domain: str | None = None,
        search_text: str | None = None,
    ) -> tuple[list[dict[str, Any]], int]: ...

    def get_request_domains(self) -> list[str]: ...

    def get_request(self, run_id: str, request_id: str) -> dict[str, Any] | None: ...

    def get_requests_for_run(self, run_id: str) -> list[dict[str, Any]]: ...

    def get_requests_for_conversation(self, conversation_id: str) -> list[dict[str, Any]]:
        """
        Return all requests bound to a given conversation_id, ordered by turn_index.

        Step 12 / design v1.3 §7 — audit conversation export.
        """
        ...

    def get_llm_calls_for_request(self, run_id: str, request_id: str) -> list[dict[str, Any]]: ...

    def get_decision_traces_for_request(self, run_id: str, request_id: str) -> list[dict[str, Any]]: ...

    def get_orchestration_events_for_request(self, run_id: str, request_id: str) -> list[dict[str, Any]]: ...

    def get_debug_events_for_request(self, run_id: str, request_id: str | None = None) -> list[dict[str, Any]]: ...

    def get_models_used_for_run(self, run_id: str) -> dict[str, str]: ...


class SqliteReadStore:
    """Reads from the MoralStack SQLite database."""

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        path = get_db_path()
        if not path:
            return None
        try:
            conn = _get_connection(path)
            row = conn.execute("SELECT * FROM runs WHERE run_id = ?", (run_id,)).fetchone()
            conn.close()
            return dict(row) if row else None
        except Exception as e:
            logger.warning("observability[read_store]: get_run failed: %s", e)
            return None

    def get_all_runs(self, limit: int = 100) -> list[dict[str, Any]]:
        path = get_db_path()
        if not path:
            return []
        try:
            conn = _get_connection(path)
            rows = conn.execute("SELECT * FROM runs ORDER BY started_at DESC LIMIT ?", (limit,)).fetchall()
            conn.close()
            return [dict(r) for r in rows]
        except Exception as e:
            logger.warning("observability[read_store]: get_all_runs failed: %s", e)
            return []

    def get_runs_page(
        self,
        page: int = 1,
        page_size: int = 20,
        domain: str | None = None,
        search_text: str | None = None,
    ) -> tuple[list[dict[str, Any]], int]:
        path = get_db_path()
        if not path:
            return [], 0
        safe_page = max(1, int(page))
        safe_page_size = max(1, int(page_size))
        offset = (safe_page - 1) * safe_page_size
        domain_filter = (domain or "").strip()
        text_filter = (search_text or "").strip()
        try:
            conn = _get_connection(path)
            if not domain_filter and not text_filter:
                total = conn.execute("SELECT COUNT(*) AS c FROM runs").fetchone()["c"]
                rows = conn.execute(
                    "SELECT * FROM runs ORDER BY started_at DESC LIMIT ? OFFSET ?",
                    (safe_page_size, offset),
                ).fetchall()
            else:
                where_clauses = []
                params: list[Any] = []
                if domain_filter:
                    where_clauses.append("LOWER(COALESCE(req.domain, '')) = LOWER(?)")
                    params.append(domain_filter)
                if text_filter:
                    where_clauses.append(
                        "(LOWER(COALESCE(req.prompt, '')) LIKE LOWER(?) "
                        "OR LOWER(COALESCE(req.final_response, '')) LIKE LOWER(?))"
                    )
                    like_text = f"%{text_filter}%"
                    params.extend([like_text, like_text])
                where_sql = " AND ".join(where_clauses)
                total = conn.execute(
                    f"""
                    SELECT COUNT(DISTINCT r.run_id) AS c
                    FROM runs r
                    JOIN requests req ON req.run_id = r.run_id
                    WHERE {where_sql}
                    """,
                    params,
                ).fetchone()["c"]
                rows = conn.execute(
                    f"""
                    SELECT r.*
                    FROM runs r
                    JOIN requests req ON req.run_id = r.run_id
                    WHERE {where_sql}
                    GROUP BY r.run_id
                    ORDER BY r.started_at DESC
                    LIMIT ? OFFSET ?
                    """,
                    [*params, safe_page_size, offset],
                ).fetchall()
            conn.close()
            return [dict(r) for r in rows], int(total or 0)
        except Exception as e:
            logger.warning("observability[read_store]: get_runs_page failed: %s", e)
            return [], 0

    def get_request_domains(self) -> list[str]:
        path = get_db_path()
        if not path:
            return []
        try:
            conn = _get_connection(path)
            rows = conn.execute("""
                SELECT DISTINCT TRIM(domain) AS domain
                FROM requests
                WHERE TRIM(COALESCE(domain, '')) != ''
                ORDER BY domain ASC
                """).fetchall()
            conn.close()
            return [str(r["domain"]) for r in rows if r["domain"]]
        except Exception as e:
            logger.warning("observability[read_store]: get_request_domains failed: %s", e)
            return []

    def get_request(self, run_id: str, request_id: str) -> dict[str, Any] | None:
        path = get_db_path()
        if not path:
            return None
        try:
            conn = _get_connection(path)
            row = conn.execute(
                "SELECT * FROM requests WHERE run_id = ? AND request_id = ?",
                (run_id, request_id),
            ).fetchone()
            conn.close()
            return dict(row) if row else None
        except Exception as e:
            logger.warning("observability[read_store]: get_request failed: %s", e)
            return None

    def get_requests_for_run(self, run_id: str) -> list[dict[str, Any]]:
        path = get_db_path()
        if not path:
            return []
        try:
            conn = _get_connection(path)
            rows = conn.execute(
                "SELECT * FROM requests WHERE run_id = ? ORDER BY created_at ASC",
                (run_id,),
            ).fetchall()
            conn.close()
            return [dict(r) for r in rows]
        except Exception as e:
            logger.warning("observability[read_store]: get_requests_for_run failed: %s", e)
            return []

    def get_requests_for_conversation(self, conversation_id: str) -> list[dict[str, Any]]:
        """
        Return all requests bound to a given conversation_id, ordered by turn_index.

        Uses the `idx_requests_conversation_turn` index added by the Step 6 migration
        on the `requests` table. Returns an empty list when conversation_id is not
        found, or when the db is unavailable.
        """
        if not conversation_id:
            return []
        path = get_db_path()
        if not path:
            return []
        try:
            conn = _get_connection(path)
            rows = conn.execute(
                "SELECT * FROM requests WHERE conversation_id = ? ORDER BY turn_index ASC, created_at ASC",
                (conversation_id,),
            ).fetchall()
            conn.close()
            return [dict(r) for r in rows]
        except Exception as e:
            logger.warning("observability[read_store]: get_requests_for_conversation failed: %s", e)
            return []

    def get_llm_calls_for_request(self, run_id: str, request_id: str) -> list[dict[str, Any]]:
        path = get_db_path()
        if not path:
            return []
        try:
            conn = _get_connection(path)
            rows = conn.execute(
                """
                SELECT * FROM llm_calls
                WHERE run_id = ? AND request_id = ?
                ORDER BY COALESCE(cycle, -1), COALESCE(sequence_in_cycle, 999), started_at, phase
                """,
                (run_id, request_id),
            ).fetchall()
            conn.close()
            return [dict(r) for r in rows]
        except Exception as e:
            logger.warning("observability[read_store]: get_llm_calls_for_request failed: %s", e)
            return []

    def get_decision_traces_for_request(self, run_id: str, request_id: str) -> list[dict[str, Any]]:
        path = get_db_path()
        if not path:
            return []
        try:
            conn = _get_connection(path)
            rows = conn.execute(
                """
                SELECT * FROM decision_traces
                WHERE run_id = ? AND request_id = ?
                ORDER BY created_at ASC, sequence ASC, id ASC
                """,
                (run_id, request_id),
            ).fetchall()
            conn.close()
            return [dict(r) for r in rows]
        except Exception as e:
            logger.warning("observability[read_store]: get_decision_traces_for_request failed: %s", e)
            return []

    def get_orchestration_events_for_request(self, run_id: str, request_id: str) -> list[dict[str, Any]]:
        path = get_db_path()
        if not path:
            return []
        try:
            conn = _get_connection(path)
            rows = conn.execute(
                """
                SELECT * FROM orchestration_events
                WHERE run_id = ? AND request_id = ?
                ORDER BY COALESCE(cycle, -1), COALESCE(sequence, 999999),
                         COALESCE(started_at, 0), id ASC
                """,
                (run_id, request_id),
            ).fetchall()
            conn.close()
            return [dict(r) for r in rows]
        except Exception as e:
            logger.warning("observability[read_store]: get_orchestration_events_for_request failed: %s", e)
            return []

    def get_debug_events_for_request(self, run_id: str, request_id: str | None = None) -> list[dict[str, Any]]:
        path = get_db_path()
        if not path:
            return []
        try:
            conn = _get_connection(path)
            if request_id:
                rows = conn.execute(
                    """
                    SELECT * FROM debug_events
                    WHERE run_id = ? AND (request_id = ? OR request_id IS NULL)
                    ORDER BY created_at
                    """,
                    (run_id, request_id),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM debug_events WHERE run_id = ? ORDER BY created_at",
                    (run_id,),
                ).fetchall()
            conn.close()
            return [dict(r) for r in rows]
        except Exception as e:
            logger.warning("observability[read_store]: get_debug_events_for_request failed: %s", e)
            return []

    def get_models_used_for_run(self, run_id: str) -> dict[str, str]:
        path = get_db_path()
        if not path:
            return {}
        try:
            conn = _get_connection(path)
            rows = conn.execute(
                """
                SELECT module, action, model FROM llm_calls
                WHERE run_id = ?
                  AND model IS NOT NULL AND model != ''
                  AND (
                    call_outcome IS NULL
                    OR LOWER(call_outcome) NOT IN ('skipped', 'cancelled', 'discarded')
                  )
                ORDER BY started_at
                """,
                (run_id,),
            ).fetchall()
            conn.close()
        except Exception as e:
            logger.warning("observability[read_store]: get_models_used_for_run failed: %s", e)
            return {}

        result: dict[str, str] = {}
        for r in rows:
            module = (r["module"] or "").strip()
            action = (r["action"] or "").strip().lower()
            model = (r["model"] or "").strip()
            if not model:
                continue
            if module == "policy":
                if "rewrite" in action:
                    result.setdefault("policy_rewrite", model)
                elif "generate" in action:
                    result.setdefault("policy_generate", model)
            elif module == "risk_estimator":
                result.setdefault("risk", model)
            elif module == "critic":
                result.setdefault("critic", model)
            elif module == "simulator":
                result.setdefault("simulator", model)
            elif module == "hindsight":
                result.setdefault("hindsight", model)
            elif module == "perspectives":
                result.setdefault("perspectives", model)
        return result
