"""
SQLite database layer for MoralStack persistence.

Schema: runs, requests, llm_calls, decision_traces, debug_events, exports_cache.
Uses WAL mode and foreign_keys=ON.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import time
from pathlib import Path
from typing import Any

from moralstack.persistence.config import get_db_path, get_persist_mode

logger = logging.getLogger(__name__)

_SCHEMA = """
          CREATE TABLE IF NOT EXISTS runs
          (
              run_id
              TEXT
              PRIMARY
              KEY,
              run_type
              TEXT
              NOT
              NULL,
              started_at
              INTEGER
              NOT
              NULL,
              ended_at
              INTEGER,
              status
              TEXT
              NOT
              NULL,
              meta_json
              TEXT
          );

          CREATE TABLE IF NOT EXISTS requests
          (
              run_id
              TEXT
              NOT
              NULL,
              request_id
              TEXT
              NOT
              NULL,
              prompt
              TEXT
              NOT
              NULL,
              domain
              TEXT,
              created_at
              INTEGER
              NOT
              NULL,
              meta_json
              TEXT,
              final_response
              TEXT,
              PRIMARY
              KEY
          (
              run_id,
              request_id
          ),
              FOREIGN KEY
          (
              run_id
          ) REFERENCES runs
          (
              run_id
          ) ON DELETE CASCADE
              );

          CREATE TABLE IF NOT EXISTS llm_calls
          (
              id
              INTEGER
              PRIMARY
              KEY
              AUTOINCREMENT,
              run_id
              TEXT
              NOT
              NULL,
              request_id
              TEXT
              NOT
              NULL,
              cycle
              INTEGER,
              phase
              TEXT
              NOT
              NULL,
              module
              TEXT
              NOT
              NULL,
              action
              TEXT
              NOT
              NULL,
              model
              TEXT,
              started_at
              INTEGER
              NOT
              NULL,
              duration_ms
              REAL,
              prompt
              TEXT
              NOT
              NULL,
              system_prompt
              TEXT
              NOT
              NULL,
              raw_response
              TEXT
              NOT
              NULL,
              parsed_json
              TEXT,
              parsed_summary_json
              TEXT,
              token_usage_json
              TEXT,
              attempts
              INTEGER,
              error
              TEXT,
              sequence_in_cycle
              INTEGER,
              FOREIGN
              KEY
          (
              run_id,
              request_id
          ) REFERENCES requests
          (
              run_id,
              request_id
          ) ON DELETE CASCADE
              );

          CREATE INDEX IF NOT EXISTS idx_llm_calls_request
              ON llm_calls(run_id, request_id, cycle, phase);

          CREATE TABLE IF NOT EXISTS decision_traces
          (
              id
              INTEGER
              PRIMARY
              KEY
              AUTOINCREMENT,
              run_id
              TEXT
              NOT
              NULL,
              request_id
              TEXT
              NOT
              NULL,
              stage
              TEXT
              NOT
              NULL,
              sequence
              INTEGER
              NOT
              NULL,
              trace_json
              TEXT
              NOT
              NULL,
              created_at
              INTEGER
              NOT
              NULL,
              FOREIGN
              KEY
          (
              run_id,
              request_id
          ) REFERENCES requests
          (
              run_id,
              request_id
          ) ON DELETE CASCADE
              );

          CREATE TABLE IF NOT EXISTS debug_events
          (
              id
              INTEGER
              PRIMARY
              KEY
              AUTOINCREMENT,
              run_id
              TEXT
              NOT
              NULL,
              request_id
              TEXT,
              created_at
              INTEGER
              NOT
              NULL,
              payload_json
              TEXT
              NOT
              NULL,
              FOREIGN
              KEY
          (
              run_id
          ) REFERENCES runs
          (
              run_id
          ) ON DELETE CASCADE
              );

          CREATE INDEX IF NOT EXISTS idx_debug_events_request ON debug_events(run_id, request_id);

          CREATE TABLE IF NOT EXISTS exports_cache
          (
              id
              INTEGER
              PRIMARY
              KEY
              AUTOINCREMENT,
              run_id
              TEXT
              NOT
              NULL,
              request_id
              TEXT,
              export_type
              TEXT
              NOT
              NULL,
              created_at
              INTEGER
              NOT
              NULL,
              content
              TEXT
              NOT
              NULL,
              UNIQUE
          (
              run_id,
              request_id,
              export_type
          )
              ); \
          """


def _get_connection(db_path: str) -> sqlite3.Connection:
    """Returns a connection with WAL and foreign_keys enabled."""
    conn = sqlite3.connect(db_path, timeout=10.0)
    # Busy timeout to mitigate 'database is locked' under concurrent writers
    conn.execute("PRAGMA busy_timeout=10000")
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.row_factory = sqlite3.Row
    return conn


class SqliteUnitOfWork:
    """
    Context manager: single connection for a logical unit of work.
    Commit on success, rollback on exception. One UoW per thread/request.
    """

    def __init__(self, db_path: str | None = None) -> None:
        self._db_path = db_path
        self._conn: sqlite3.Connection | None = None

    @property
    def conn(self) -> sqlite3.Connection | None:
        """Active connection, or None if no-op (file_only or no path)."""
        return self._conn

    def __enter__(self) -> SqliteUnitOfWork:
        if get_persist_mode() == "file_only":
            return self
        path = self._db_path or get_db_path()
        if not path:
            return self
        self._conn = _get_connection(path)
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: Any,
    ) -> None:
        if self._conn is None:
            return
        try:
            if exc_type is not None:
                self._conn.rollback()
            else:
                self._conn.commit()
        finally:
            self._conn.close()
            self._conn = None


_LLM_CALLS_INSERT = """
    INSERT INTO llm_calls (run_id, request_id, cycle, phase, module, action, model,
                           started_at, duration_ms, prompt, system_prompt, raw_response,
                           parsed_json, parsed_summary_json, token_usage_json,
                           attempts, error, sequence_in_cycle)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
"""

_DECISION_TRACES_INSERT = """
    INSERT INTO decision_traces (run_id, request_id, stage, sequence, trace_json, created_at)
    VALUES (?, ?, ?, ?, ?, ?)
"""

_DEBUG_EVENTS_INSERT = """
    INSERT INTO debug_events (run_id, request_id, created_at, payload_json)
    VALUES (?, ?, ?, ?)
"""


def insert_llm_calls_batch(conn: sqlite3.Connection, rows: list[tuple[Any, ...]]) -> None:
    """
    Batch insert into llm_calls. Each row: (run_id, request_id, cycle, phase, module,
    action, model, started_at, duration_ms, prompt, system_prompt, raw_response,
    parsed_json, parsed_summary_json, token_usage_json, attempts, error,
    sequence_in_cycle).
    """
    if not rows:
        return
    conn.executemany(_LLM_CALLS_INSERT, rows)


def insert_decision_traces_batch(conn: sqlite3.Connection, rows: list[tuple[Any, ...]]) -> None:
    """
    Batch insert into decision_traces. Each row: (run_id, request_id, stage,
    sequence, trace_json, created_at).
    """
    if not rows:
        return
    conn.executemany(_DECISION_TRACES_INSERT, rows)


def insert_debug_events_batch(conn: sqlite3.Connection, rows: list[tuple[Any, ...]]) -> None:
    """
    Batch insert into debug_events. Each row: (run_id, request_id, created_at,
    payload_json).
    """
    if not rows:
        return
    conn.executemany(_DEBUG_EVENTS_INSERT, rows)


def init_db(db_path: str | None = None) -> bool:
    """
    Initializes the database schema. Creates tables if not exist.

    Returns True if successful, False if db_path is None or file_only mode.
    """
    mode = get_persist_mode()
    if mode == "file_only":
        return False
    path = db_path or get_db_path()
    if not path:
        return False
    try:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        conn = _get_connection(path)
        conn.executescript(_SCHEMA)
        conn.commit()
        # Migration: add final_response column to existing DBs (safe no-op if already present)
        try:
            conn.execute("ALTER TABLE requests ADD COLUMN final_response TEXT")
            conn.commit()
        except Exception:
            pass
        # Migration: add sequence_in_cycle for logical journey order (safe no-op if already present)
        try:
            conn.execute("ALTER TABLE llm_calls ADD COLUMN sequence_in_cycle INTEGER")
            conn.commit()
        except Exception:
            pass
        conn.close()
        return True
    except Exception as e:
        logger.warning("persistence: init_db failed: %s", e)
        return False


def create_run(
    run_id: str,
    run_type: str,
    meta: dict[str, Any] | None = None,
) -> bool:
    """Creates a run record. Returns True on success."""
    mode = get_persist_mode()
    if mode == "file_only":
        return False
    path = get_db_path()
    if not path:
        return False
    conn = None
    try:
        conn = _get_connection(path)
        conn.execute(
            """
            INSERT OR IGNORE INTO runs (run_id, run_type, started_at, ended_at, status, meta_json)
            VALUES (?, ?, ?, NULL, 'running', ?)
            """,
            (run_id, run_type, int(time.time() * 1000), json.dumps(meta or {})),
        )
        conn.commit()
        return True
    except Exception as e:
        logger.warning("persistence: create_run failed: %s", e)
        if conn is not None:
            try:
                conn.rollback()
            except Exception:
                pass
        return False
    finally:
        if conn is not None:
            conn.close()


def end_run(run_id: str, status: str = "ok") -> bool:
    """Marks a run as ended. Returns True on success."""
    mode = get_persist_mode()
    if mode == "file_only":
        return False
    path = get_db_path()
    if not path:
        return False
    conn = None
    try:
        conn = _get_connection(path)
        conn.execute(
            "UPDATE runs SET ended_at = ?, status = ? WHERE run_id = ?",
            (int(time.time() * 1000), status, run_id),
        )
        conn.commit()
        return True
    except Exception as e:
        logger.warning("persistence: end_run failed: %s", e)
        if conn is not None:
            try:
                conn.rollback()
            except Exception:
                pass
        return False
    finally:
        if conn is not None:
            conn.close()


def upsert_request(
    run_id: str,
    request_id: str,
    prompt: str,
    domain: str | None = None,
    meta: dict[str, Any] | None = None,
) -> bool:
    """Inserts or replaces a request. Returns True on success."""
    mode = get_persist_mode()
    if mode == "file_only":
        return False
    path = get_db_path()
    if not path:
        return False
    conn = None
    try:
        conn = _get_connection(path)
        conn.execute(
            """
            INSERT OR IGNORE INTO requests (
                run_id, request_id, prompt, domain, created_at, meta_json
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                request_id,
                prompt,
                domain or "",
                int(time.time() * 1000),
                json.dumps(meta or {}),
            ),
        )
        conn.commit()
        return True
    except Exception as e:
        logger.warning("persistence: upsert_request failed: %s", e)
        if conn is not None:
            try:
                conn.rollback()
            except Exception:
                pass
        return False
    finally:
        if conn is not None:
            conn.close()


def update_request_response(
    run_id: str,
    request_id: str,
    final_response: str,
) -> bool:
    """Updates the final_response of a request. Returns True on success."""
    mode = get_persist_mode()
    if mode == "file_only":
        return False
    path = get_db_path()
    if not path:
        return False
    conn = None
    try:
        conn = _get_connection(path)
        conn.execute(
            "UPDATE requests SET final_response = ? WHERE run_id = ? AND request_id = ?",
            (final_response, run_id, request_id),
        )
        conn.commit()
        return True
    except Exception as e:
        logger.warning("persistence: update_request_response failed: %s", e)
        if conn is not None:
            try:
                conn.rollback()
            except Exception:
                pass
        return False
    finally:
        if conn is not None:
            conn.close()


def update_request_domain(
    run_id: str,
    request_id: str,
    domain: str | None,
) -> bool:
    """Updates the domain of a request. Returns True on success."""
    mode = get_persist_mode()
    if mode == "file_only":
        return False
    path = get_db_path()
    if not path:
        return False
    conn = None
    try:
        conn = _get_connection(path)
        conn.execute(
            "UPDATE requests SET domain = ? WHERE run_id = ? AND request_id = ?",
            (domain or "", run_id, request_id),
        )
        conn.commit()
        return True
    except Exception as e:
        logger.warning("persistence: update_request_domain failed: %s", e)
        if conn is not None:
            try:
                conn.rollback()
            except Exception:
                pass
        return False
    finally:
        if conn is not None:
            conn.close()


def get_run(run_id: str) -> dict[str, Any] | None:
    """Returns run record or None."""
    path = get_db_path()
    if not path:
        return None
    try:
        conn = _get_connection(path)
        row = conn.execute("SELECT * FROM runs WHERE run_id = ?", (run_id,)).fetchone()
        conn.close()
        if row is None:
            return None
        return dict(row)
    except Exception as e:
        logger.warning("persistence: get_run failed: %s", e)
        return None


def get_request(run_id: str, request_id: str) -> dict[str, Any] | None:
    """Returns a single request record or None."""
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
        logger.warning("persistence: get_request failed: %s", e)
        return None


def get_requests_for_run(run_id: str) -> list[dict[str, Any]]:
    """Returns all requests for a run, ordered by created_at."""
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
        logger.warning("persistence: get_requests_for_run failed: %s", e)
        return []


def get_llm_calls_for_request(
    run_id: str,
    request_id: str,
) -> list[dict[str, Any]]:
    """Returns llm_calls for a request, ordered by cycle, sequence_in_cycle, started_at."""
    path = get_db_path()
    if not path:
        return []
    try:
        conn = _get_connection(path)
        rows = conn.execute(
            """
            SELECT *
            FROM llm_calls
            WHERE run_id = ?
              AND request_id = ?
            ORDER BY COALESCE(cycle, -1), COALESCE(sequence_in_cycle, 999), started_at, phase
            """,
            (run_id, request_id),
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]
    except Exception as e:
        logger.warning("persistence: get_llm_calls_for_request failed: %s", e)
        return []


def get_decision_traces_for_request(
    run_id: str,
    request_id: str,
) -> list[dict[str, Any]]:
    """Returns decision traces for a request, ordered by sequence."""
    path = get_db_path()
    if not path:
        return []
    try:
        conn = _get_connection(path)
        rows = conn.execute(
            """
            SELECT *
            FROM decision_traces
            WHERE run_id = ?
              AND request_id = ?
            ORDER BY sequence
            """,
            (run_id, request_id),
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]
    except Exception as e:
        logger.warning("persistence: get_decision_traces_for_request failed: %s", e)
        return []


def get_debug_events_for_request(
    run_id: str,
    request_id: str | None = None,
) -> list[dict[str, Any]]:
    """Returns debug events for a run (optionally filtered by request_id)."""
    path = get_db_path()
    if not path:
        return []
    try:
        conn = _get_connection(path)
        if request_id:
            rows = conn.execute(
                """
                SELECT *
                FROM debug_events
                WHERE run_id = ?
                  AND (request_id = ? OR request_id IS NULL)
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
        logger.warning("persistence: get_debug_events_for_request failed: %s", e)
        return []


def get_all_runs(limit: int = 100) -> list[dict[str, Any]]:
    """Returns recent runs, ordered by started_at DESC."""
    path = get_db_path()
    if not path:
        return []
    try:
        conn = _get_connection(path)
        rows = conn.execute(
            "SELECT * FROM runs ORDER BY started_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]
    except Exception as e:
        logger.warning("persistence: get_all_runs failed: %s", e)
        return []


def delete_run(run_id: str) -> bool:
    """Deletes a run and all related data (CASCADE). Returns True on success."""
    path = get_db_path()
    if not path:
        return False
    conn = None
    try:
        conn = _get_connection(path)
        conn.execute("DELETE FROM runs WHERE run_id = ?", (run_id,))
        conn.commit()
        return True
    except Exception as e:
        logger.warning("persistence: delete_run failed: %s", e)
        if conn is not None:
            try:
                conn.rollback()
            except Exception:
                pass
        return False
    finally:
        if conn is not None:
            conn.close()


def delete_request(run_id: str, request_id: str) -> bool:
    """Deletes a request and all related data (CASCADE). Returns True on success."""
    path = get_db_path()
    if not path:
        return False
    conn = None
    try:
        conn = _get_connection(path)
        conn.execute(
            "DELETE FROM requests WHERE run_id = ? AND request_id = ?",
            (run_id, request_id),
        )
        conn.commit()
        return True
    except Exception as e:
        logger.warning("persistence: delete_request failed: %s", e)
        if conn is not None:
            try:
                conn.rollback()
            except Exception:
                pass
        return False
    finally:
        if conn is not None:
            conn.close()


def invalidate_exports_cache(run_id: str, request_id: str | None = None) -> None:
    """Invalidates exports cache for a run or a specific request."""
    path = get_db_path()
    if not path:
        return
    conn = None
    try:
        conn = _get_connection(path)
        if request_id:
            conn.execute(
                "DELETE FROM exports_cache WHERE run_id = ? AND request_id = ?",
                (run_id, request_id),
            )
        else:
            conn.execute("DELETE FROM exports_cache WHERE run_id = ?", (run_id,))
        conn.commit()
    except Exception as e:
        logger.warning("persistence: invalidate_exports_cache failed: %s", e)
        if conn is not None:
            try:
                conn.rollback()
            except Exception:
                pass
    finally:
        if conn is not None:
            conn.close()
