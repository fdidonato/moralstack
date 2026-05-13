"""
ReadStore: unique read contract for MoralStack observability data.

SqliteReadStore wraps the SQLite read functions from the original db.py,
now unified under a single Protocol so UI and reports depend on this
interface rather than on db.py directly.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from typing import Any, Protocol

from moralstack.observability.config import get_db_path
from moralstack.observability.sinks.sqlite_sink import _get_connection

logger = logging.getLogger(__name__)


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    """Return True when the given table exists in the connected SQLite DB."""
    try:
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
            (table,),
        ).fetchone()
        return row is not None
    except Exception:
        return False


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

    # ---------------------------------------------------------------
    # Step 13 — multi-turn observability reads
    # ---------------------------------------------------------------

    def get_conversation_states(self, conversation_id: str) -> list[dict[str, Any]]:
        """Return all conversation_states rows for the given conversation_id."""
        ...

    def get_ledger_events_for_conversation(self, conversation_id: str) -> list[dict[str, Any]]:
        """Return all ledger_events rows for the given conversation_id."""
        ...

    def get_session_store_events_for_conversation(self, conversation_id: str) -> list[dict[str, Any]]:
        """Return all session_store_events rows for the given conversation_id."""
        ...

    def get_proxy_request_events_for_conversation(self, conversation_id: str) -> list[dict[str, Any]]:
        """Return all proxy_request_events rows for the given conversation_id."""
        ...

    def get_conversation_ids_for_run(self, run_id: str) -> list[dict[str, Any]]:
        """Return one row per conversation that has requests in the given run."""
        ...

    def get_conversation_overview(self, conversation_id: str) -> dict[str, Any]:
        """Return aggregate metrics for a conversation (turn count, max risk, etc.)."""
        ...


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

    # ------------------------------------------------------------------
    # Step 13 — multi-turn observability reads (queryable conversation data)
    # ------------------------------------------------------------------

    _CONV_ORDERING = "ORDER BY COALESCE(turn_index, 0) ASC, created_at ASC, id ASC"

    def get_conversation_states(self, conversation_id: str) -> list[dict[str, Any]]:
        """Return ``conversation_states`` rows for a conversation, ordered by turn."""
        if not conversation_id:
            return []
        path = get_db_path()
        if not path:
            return []
        try:
            conn = _get_connection(path)
            if not _table_exists(conn, "conversation_states"):
                conn.close()
                return []
            rows = conn.execute(
                f"SELECT * FROM conversation_states WHERE conversation_id = ? {self._CONV_ORDERING}",
                (conversation_id,),
            ).fetchall()
            conn.close()
            return [dict(r) for r in rows]
        except Exception as e:
            logger.warning("observability[read_store]: get_conversation_states failed: %s", e)
            return []

    def get_ledger_events_for_conversation(self, conversation_id: str) -> list[dict[str, Any]]:
        """Return ``ledger_events`` rows for a conversation, ordered by turn."""
        if not conversation_id:
            return []
        path = get_db_path()
        if not path:
            return []
        try:
            conn = _get_connection(path)
            if not _table_exists(conn, "ledger_events"):
                conn.close()
                return []
            rows = conn.execute(
                f"SELECT * FROM ledger_events WHERE conversation_id = ? {self._CONV_ORDERING}",
                (conversation_id,),
            ).fetchall()
            conn.close()
            return [dict(r) for r in rows]
        except Exception as e:
            logger.warning("observability[read_store]: get_ledger_events_for_conversation failed: %s", e)
            return []

    def get_session_store_events_for_conversation(self, conversation_id: str) -> list[dict[str, Any]]:
        """Return ``session_store_events`` rows for a conversation, ordered by turn."""
        if not conversation_id:
            return []
        path = get_db_path()
        if not path:
            return []
        try:
            conn = _get_connection(path)
            if not _table_exists(conn, "session_store_events"):
                conn.close()
                return []
            rows = conn.execute(
                f"SELECT * FROM session_store_events WHERE conversation_id = ? {self._CONV_ORDERING}",
                (conversation_id,),
            ).fetchall()
            conn.close()
            return [dict(r) for r in rows]
        except Exception as e:
            logger.warning(
                "observability[read_store]: get_session_store_events_for_conversation failed: %s",
                e,
            )
            return []

    def get_proxy_request_events_for_conversation(self, conversation_id: str) -> list[dict[str, Any]]:
        """Return ``proxy_request_events`` rows for a conversation, ordered by turn."""
        if not conversation_id:
            return []
        path = get_db_path()
        if not path:
            return []
        try:
            conn = _get_connection(path)
            if not _table_exists(conn, "proxy_request_events"):
                conn.close()
                return []
            rows = conn.execute(
                f"SELECT * FROM proxy_request_events WHERE conversation_id = ? {self._CONV_ORDERING}",
                (conversation_id,),
            ).fetchall()
            conn.close()
            return [dict(r) for r in rows]
        except Exception as e:
            logger.warning(
                "observability[read_store]: get_proxy_request_events_for_conversation failed: %s",
                e,
            )
            return []

    def get_conversation_ids_for_run(self, run_id: str) -> list[dict[str, Any]]:
        """
        Return one row per conversation that has at least one request in the
        given run, with aggregate metadata derived from `requests` and the
        Step 13 multi-turn tables when available.
        """
        if not run_id:
            return []
        path = get_db_path()
        if not path:
            return []
        try:
            conn = _get_connection(path)
            rows = conn.execute(
                """
                SELECT
                    conversation_id,
                    COUNT(*) AS turn_count,
                    MIN(turn_index) AS first_turn_index,
                    MAX(turn_index) AS last_turn_index,
                    MIN(created_at) AS first_created_at,
                    MAX(created_at) AS last_created_at
                FROM requests
                WHERE run_id = ? AND conversation_id IS NOT NULL AND TRIM(conversation_id) <> ''
                GROUP BY conversation_id
                ORDER BY MIN(created_at) ASC
                """,
                (run_id,),
            ).fetchall()
            conversations: list[dict[str, Any]] = []
            has_proxy_table = _table_exists(conn, "proxy_request_events")
            for r in rows:
                conv_id = r["conversation_id"]
                final_actions: list[str] = []
                max_risk: float | None = None
                cached_count = 0
                req_rows = conn.execute(
                    "SELECT meta_json FROM requests WHERE run_id = ? AND conversation_id = ?",
                    (run_id, conv_id),
                ).fetchall()
                for req_row in req_rows:
                    raw = req_row["meta_json"]
                    if not raw:
                        continue
                    try:
                        parsed = json.loads(raw) if isinstance(raw, str) else raw
                    except (ValueError, TypeError):
                        continue
                    if not isinstance(parsed, dict):
                        continue
                    fa = parsed.get("final_action")
                    if isinstance(fa, str) and fa:
                        final_actions.append(fa)
                    rs = parsed.get("risk_score")
                    try:
                        rs_val = float(rs) if rs is not None else None
                    except (ValueError, TypeError):
                        rs_val = None
                    if rs_val is not None:
                        max_risk = rs_val if max_risk is None else max(max_risk, rs_val)
                    if bool(parsed.get("was_cached")):
                        cached_count += 1
                last_posture: str | None = None
                if has_proxy_table:
                    proxy_rows = conn.execute(
                        """
                        SELECT final_action, risk_score, posture_out, was_cached
                        FROM proxy_request_events
                        WHERE conversation_id = ?
                        ORDER BY COALESCE(turn_index, 0) ASC, created_at ASC, id ASC
                        """,
                        (conv_id,),
                    ).fetchall()
                    for pr in proxy_rows:
                        if pr["final_action"]:
                            final_actions.append(pr["final_action"])
                        rs2 = pr["risk_score"]
                        try:
                            rs2_val = float(rs2) if rs2 is not None else None
                        except (ValueError, TypeError):
                            rs2_val = None
                        if rs2_val is not None:
                            max_risk = rs2_val if max_risk is None else max(max_risk, rs2_val)
                        if pr["posture_out"]:
                            last_posture = pr["posture_out"]
                        if pr["was_cached"]:
                            cached_count += 1
                conversations.append(
                    {
                        "conversation_id": conv_id,
                        "turn_count": int(r["turn_count"] or 0),
                        "first_turn_index": r["first_turn_index"],
                        "last_turn_index": r["last_turn_index"],
                        "first_created_at": r["first_created_at"],
                        "last_created_at": r["last_created_at"],
                        "final_actions": final_actions,
                        "max_risk_score": max_risk,
                        "last_posture": last_posture,
                        "cached_turn_count": cached_count,
                    }
                )
            conn.close()
            return conversations
        except Exception as e:
            logger.warning("observability[read_store]: get_conversation_ids_for_run failed: %s", e)
            return []

    def get_conversation_overview(self, conversation_id: str) -> dict[str, Any]:
        """
        Return aggregate metrics for a conversation across multi-turn tables.

        Provides total turns, first/last timestamps, final-action distribution,
        max risk score, last posture, and ledger / session-store hit counts.
        Missing tables yield zeros / empty structures rather than errors.
        """
        if not conversation_id:
            return {}
        path = get_db_path()
        if not path:
            return {}
        try:
            conn = _get_connection(path)
            requests_rows = conn.execute(
                """
                SELECT request_id, turn_index, created_at, meta_json
                FROM requests
                WHERE conversation_id = ?
                ORDER BY COALESCE(turn_index, 0) ASC, created_at ASC
                """,
                (conversation_id,),
            ).fetchall()
            turn_count = len(requests_rows)
            first_ts = requests_rows[0]["created_at"] if requests_rows else None
            last_ts = requests_rows[-1]["created_at"] if requests_rows else None

            action_distribution: dict[str, int] = {}
            max_risk: float | None = None
            cached_turn_count = 0
            for row in requests_rows:
                raw = row["meta_json"]
                if not raw:
                    continue
                try:
                    parsed = json.loads(raw) if isinstance(raw, str) else raw
                except (ValueError, TypeError):
                    continue
                if not isinstance(parsed, dict):
                    continue
                fa = parsed.get("final_action")
                if isinstance(fa, str) and fa:
                    action_distribution[fa] = action_distribution.get(fa, 0) + 1
                rs = parsed.get("risk_score")
                try:
                    rs_val = float(rs) if rs is not None else None
                except (ValueError, TypeError):
                    rs_val = None
                if rs_val is not None:
                    max_risk = rs_val if max_risk is None else max(max_risk, rs_val)
                if bool(parsed.get("was_cached")):
                    cached_turn_count += 1

            ledger_hits = 0
            ledger_misses = 0
            store_stored = 0
            store_skipped = 0
            if _table_exists(conn, "ledger_events"):
                lrows = conn.execute(
                    "SELECT operation, outcome FROM ledger_events WHERE conversation_id = ?",
                    (conversation_id,),
                ).fetchall()
                for lr in lrows:
                    op = (lr["operation"] or "").strip()
                    out = (lr["outcome"] or "").strip()
                    if op == "lookup" and out == "hit":
                        ledger_hits += 1
                    elif op == "lookup" and out == "miss":
                        ledger_misses += 1
                    elif op == "store" and out == "stored":
                        store_stored += 1
                    elif op == "store" and out == "skipped":
                        store_skipped += 1

            session_get_hits = 0
            session_get_misses = 0
            session_put_count = 0
            session_expired = 0
            if _table_exists(conn, "session_store_events"):
                srows = conn.execute(
                    "SELECT operation, outcome FROM session_store_events WHERE conversation_id = ?",
                    (conversation_id,),
                ).fetchall()
                for sr in srows:
                    op = (sr["operation"] or "").strip()
                    out = (sr["outcome"] or "").strip()
                    if op == "get" and out == "hit":
                        session_get_hits += 1
                    elif op == "get" and out == "miss":
                        session_get_misses += 1
                    elif op == "get" and out == "expired":
                        session_expired += 1
                    elif op == "put":
                        session_put_count += 1

            state_snapshots = 0
            last_posture: str | None = None
            if _table_exists(conn, "conversation_states"):
                cs_rows = conn.execute(
                    """
                    SELECT posture FROM conversation_states
                    WHERE conversation_id = ?
                    ORDER BY COALESCE(turn_index, 0) DESC, created_at DESC, id DESC
                    """,
                    (conversation_id,),
                ).fetchall()
                state_snapshots = len(cs_rows)
                if cs_rows:
                    last_posture = cs_rows[0]["posture"]

            conn.close()

            return {
                "conversation_id": conversation_id,
                "turn_count": turn_count,
                "first_created_at": first_ts,
                "last_created_at": last_ts,
                "final_actions": action_distribution,
                "max_risk_score": max_risk,
                "last_posture": last_posture,
                "ledger_hits": ledger_hits,
                "ledger_misses": ledger_misses,
                "ledger_stored": store_stored,
                "ledger_skipped": store_skipped,
                "session_store_hits": session_get_hits,
                "session_store_misses": session_get_misses,
                "session_store_expired": session_expired,
                "session_store_puts": session_put_count,
                "state_snapshots": state_snapshots,
                "any_turn_cached": cached_turn_count > 0,
                "cached_turn_count": cached_turn_count,
            }
        except Exception as e:
            logger.warning("observability[read_store]: get_conversation_overview failed: %s", e)
            return {}

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
