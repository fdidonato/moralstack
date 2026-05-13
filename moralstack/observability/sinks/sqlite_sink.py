"""
SQLite event sink for MoralStack observability.

Contains the full SQLite schema (migrated from persistence/db.py), the
SqliteUnitOfWork context manager, low-level batch insert helpers, all lifecycle
write functions, and SqliteEventSink which maps EventEnvelope -> SQL rows.

Schema is identical to the original persistence/db.py — existing databases are
fully compatible with no migrations needed.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import time
from pathlib import Path
from typing import Any, Sequence

from moralstack.observability.config import get_db_path, get_observability_mode
from moralstack.observability.events import (
    EVENT_CONVERSATION_STATE_UPDATED,
    EVENT_DEBUG_EVENT,
    EVENT_DECISION_TRACE,
    EVENT_LEDGER_LOOKUP,
    EVENT_LEDGER_STORE,
    EVENT_LLM_CALL,
    EVENT_ORCHESTRATION_EVENT,
    EVENT_PROXY_REQUEST_FINALIZED,
    EVENT_REQUEST_DOMAIN_UPDATED,
    EVENT_REQUEST_META_UPDATED,
    EVENT_REQUEST_RESPONSE_UPDATED,
    EVENT_REQUEST_UPSERTED,
    EVENT_RUN_ENDED,
    EVENT_RUN_STARTED,
    EVENT_SESSION_STORE_GET,
    EVENT_SESSION_STORE_PUT,
    EventEnvelope,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Schema (unchanged from persistence/db.py)
# ---------------------------------------------------------------------------

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
              call_kind
              TEXT,
              call_outcome
              TEXT,
              cache_status
              TEXT,
              related_event_id
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

          CREATE TABLE IF NOT EXISTS orchestration_events
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
              stage
              TEXT
              NOT
              NULL,
              component
              TEXT
              NOT
              NULL,
              event_type
              TEXT
              NOT
              NULL,
              decision
              TEXT,
              status
              TEXT,
              sequence
              INTEGER,
              started_at
              INTEGER,
              duration_ms
              REAL,
              reason_codes_json
              TEXT,
              inputs_json
              TEXT,
              outputs_json
              TEXT,
              payload_json
              TEXT,
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

          CREATE INDEX IF NOT EXISTS idx_orch_events_run_req_cycle_seq
              ON orchestration_events(run_id, request_id, cycle, sequence);

          CREATE INDEX IF NOT EXISTS idx_orch_events_run_req_type
              ON orchestration_events(run_id, request_id, event_type);

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
              );

          -- Step 13 multi-turn observability: conversation state snapshots per request/turn.
          CREATE TABLE IF NOT EXISTS conversation_states
          (
              id                  INTEGER PRIMARY KEY AUTOINCREMENT,
              run_id              TEXT    NOT NULL,
              request_id          TEXT    NOT NULL,
              conversation_id     TEXT    NOT NULL,
              turn_index          INTEGER,
              created_at          INTEGER NOT NULL,
              state_in_json       TEXT,
              state_out_json      TEXT    NOT NULL,
              state_summary_json  TEXT,
              final_action        TEXT,
              risk_score          REAL,
              posture             TEXT,
              was_cached          INTEGER,
              cached_from_turn    INTEGER,
              refresh_required    INTEGER,
              refresh_reason      TEXT,
              FOREIGN KEY (run_id, request_id)
                  REFERENCES requests(run_id, request_id) ON DELETE CASCADE
          );

          CREATE INDEX IF NOT EXISTS idx_conversation_states_conv_turn
              ON conversation_states(conversation_id, turn_index);

          CREATE INDEX IF NOT EXISTS idx_conversation_states_request
              ON conversation_states(run_id, request_id);

          -- Step 13 multi-turn observability: SemanticDecisionLedger lookup/store events.
          CREATE TABLE IF NOT EXISTS ledger_events
          (
              id                  INTEGER PRIMARY KEY AUTOINCREMENT,
              run_id              TEXT    NOT NULL,
              request_id          TEXT,
              conversation_id     TEXT,
              turn_index          INTEGER,
              created_at          INTEGER NOT NULL,
              operation           TEXT    NOT NULL,
              outcome             TEXT    NOT NULL,
              reason              TEXT,
              similarity          REAL,
              from_turn           INTEGER,
              contract_hash       TEXT,
              posture             TEXT,
              domain              TEXT,
              intent_clarity      TEXT,
              request_type        TEXT,
              final_action        TEXT,
              risk_score          REAL,
              payload_json        TEXT,
              FOREIGN KEY (run_id, request_id)
                  REFERENCES requests(run_id, request_id) ON DELETE CASCADE
          );

          CREATE INDEX IF NOT EXISTS idx_ledger_events_conv_turn
              ON ledger_events(conversation_id, turn_index);

          CREATE INDEX IF NOT EXISTS idx_ledger_events_request
              ON ledger_events(run_id, request_id);

          CREATE INDEX IF NOT EXISTS idx_ledger_events_operation
              ON ledger_events(operation, outcome);

          -- Step 13 multi-turn observability: SessionStore get/put events.
          CREATE TABLE IF NOT EXISTS session_store_events
          (
              id                  INTEGER PRIMARY KEY AUTOINCREMENT,
              run_id              TEXT,
              request_id          TEXT,
              conversation_id     TEXT    NOT NULL,
              turn_index          INTEGER,
              created_at          INTEGER NOT NULL,
              operation           TEXT    NOT NULL,
              outcome             TEXT    NOT NULL,
              state_summary_json  TEXT,
              payload_json        TEXT
          );

          CREATE INDEX IF NOT EXISTS idx_session_store_events_conv_turn
              ON session_store_events(conversation_id, turn_index);

          CREATE INDEX IF NOT EXISTS idx_session_store_events_request
              ON session_store_events(run_id, request_id);

          -- Step 13 multi-turn observability: per-request proxy finalization summary.
          CREATE TABLE IF NOT EXISTS proxy_request_events
          (
              id                    INTEGER PRIMARY KEY AUTOINCREMENT,
              run_id                TEXT    NOT NULL,
              request_id            TEXT    NOT NULL,
              conversation_id       TEXT,
              turn_index            INTEGER,
              created_at            INTEGER NOT NULL,
              final_action          TEXT,
              risk_score            REAL,
              path                  TEXT,
              domain                TEXT,
              posture_in            TEXT,
              posture_out           TEXT,
              state_provided        INTEGER,
              state_updated         INTEGER,
              was_cached            INTEGER,
              cached_from_turn      INTEGER,
              final_response_length INTEGER,
              headers_json          TEXT,
              metadata_json         TEXT,
              state_in_json         TEXT,
              state_out_json        TEXT,
              payload_json          TEXT,
              FOREIGN KEY (run_id, request_id)
                  REFERENCES requests(run_id, request_id) ON DELETE CASCADE
          );

          CREATE INDEX IF NOT EXISTS idx_proxy_request_events_conv_turn
              ON proxy_request_events(conversation_id, turn_index);

          CREATE INDEX IF NOT EXISTS idx_proxy_request_events_request
              ON proxy_request_events(run_id, request_id); \
          """


# ---------------------------------------------------------------------------
# Connection helpers
# ---------------------------------------------------------------------------


def _get_connection(db_path: str) -> sqlite3.Connection:
    """Returns a connection with WAL and foreign_keys enabled."""
    conn = sqlite3.connect(db_path, timeout=10.0)
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
        if get_observability_mode() == "file_only":
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


# ---------------------------------------------------------------------------
# Batch insert helpers (low-level; used internally and by sink dispatch)
# ---------------------------------------------------------------------------

_LLM_CALLS_INSERT = """
    INSERT INTO llm_calls (run_id, request_id, cycle, phase, module, action, model,
                           started_at, duration_ms, prompt, system_prompt, raw_response,
                           parsed_json, parsed_summary_json, token_usage_json,
                           attempts, error, sequence_in_cycle,
                           call_kind, call_outcome, cache_status, related_event_id)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
"""

_DECISION_TRACES_INSERT = """
    INSERT INTO decision_traces (run_id, request_id, stage, sequence, trace_json, created_at)
    VALUES (?, ?, ?, ?, ?, ?)
"""

_DEBUG_EVENTS_INSERT = """
    INSERT INTO debug_events (run_id, request_id, created_at, payload_json)
    VALUES (?, ?, ?, ?)
"""

_ORCH_EVENTS_INSERT = """
    INSERT INTO orchestration_events (
        run_id, request_id, cycle, stage, component, event_type,
        decision, status, sequence, started_at, duration_ms,
        reason_codes_json, inputs_json, outputs_json, payload_json
    )
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
"""


def insert_llm_calls_batch(conn: sqlite3.Connection, rows: list[tuple[Any, ...]]) -> None:
    if not rows:
        return
    conn.executemany(_LLM_CALLS_INSERT, rows)


def insert_decision_traces_batch(conn: sqlite3.Connection, rows: list[tuple[Any, ...]]) -> None:
    if not rows:
        return
    conn.executemany(_DECISION_TRACES_INSERT, rows)


def insert_orchestration_events_batch(conn: sqlite3.Connection, rows: list[tuple[Any, ...]]) -> None:
    if not rows:
        return
    conn.executemany(_ORCH_EVENTS_INSERT, rows)


def insert_debug_events_batch(conn: sqlite3.Connection, rows: list[tuple[Any, ...]]) -> None:
    if not rows:
        return
    conn.executemany(_DEBUG_EVENTS_INSERT, rows)


# ---------------------------------------------------------------------------
# DB initialisation
# ---------------------------------------------------------------------------


def init_db(db_path: str | None = None) -> bool:
    """Initializes the database schema. Returns True on success."""
    mode = get_observability_mode()
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
        # Migrations: safe no-op if columns already exist
        for _col, _sql in (
            ("final_response", "ALTER TABLE requests ADD COLUMN final_response TEXT"),
            ("conversation_id", "ALTER TABLE requests ADD COLUMN conversation_id TEXT"),
            ("turn_index", "ALTER TABLE requests ADD COLUMN turn_index INTEGER"),
            ("parent_request_id", "ALTER TABLE requests ADD COLUMN parent_request_id TEXT"),
        ):
            try:
                conn.execute(_sql)
                conn.commit()
            except Exception:
                pass
        for _idx_sql in (
            "CREATE INDEX IF NOT EXISTS idx_requests_conversation_id ON requests(conversation_id)",
            "CREATE INDEX IF NOT EXISTS idx_requests_conversation_turn ON requests(conversation_id, turn_index)",
        ):
            try:
                conn.execute(_idx_sql)
                conn.commit()
            except Exception:
                pass
        for _col, _sql in (
            ("sequence_in_cycle", "ALTER TABLE llm_calls ADD COLUMN sequence_in_cycle INTEGER"),
            ("call_kind", "ALTER TABLE llm_calls ADD COLUMN call_kind TEXT"),
            ("call_outcome", "ALTER TABLE llm_calls ADD COLUMN call_outcome TEXT"),
            ("cache_status", "ALTER TABLE llm_calls ADD COLUMN cache_status TEXT"),
            ("related_event_id", "ALTER TABLE llm_calls ADD COLUMN related_event_id INTEGER"),
        ):
            try:
                conn.execute(_sql)
                conn.commit()
            except Exception:
                pass
        conn.close()
        return True
    except Exception as e:
        logger.warning("observability: init_db failed: %s", e)
        return False


# ---------------------------------------------------------------------------
# Lifecycle write functions (used by DefaultPersistence and SqliteEventSink)
# ---------------------------------------------------------------------------


def create_run(run_id: str, run_type: str, meta: dict[str, Any] | None = None) -> bool:
    """Creates a run record. Returns True on success."""
    if get_observability_mode() == "file_only":
        return False
    path = get_db_path()
    if not path:
        return False
    conn = None
    try:
        conn = _get_connection(path)
        conn.execute(
            "INSERT OR IGNORE INTO runs (run_id, run_type, started_at, ended_at, status, meta_json)"
            " VALUES (?, ?, ?, NULL, 'running', ?)",
            (run_id, run_type, int(time.time() * 1000), json.dumps(meta or {})),
        )
        conn.commit()
        return True
    except Exception as e:
        logger.warning("observability: create_run failed: %s", e)
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
    if get_observability_mode() == "file_only":
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
        logger.warning("observability: end_run failed: %s", e)
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
    *,
    conversation_id: str | None = None,
    turn_index: int | None = None,
    parent_request_id: str | None = None,
) -> bool:
    """Inserts or ignores a request row. Returns True on success."""
    if get_observability_mode() == "file_only":
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
                run_id, request_id, prompt, domain, created_at, meta_json,
                conversation_id, turn_index, parent_request_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                request_id,
                prompt,
                domain or "",
                int(time.time() * 1000),
                json.dumps(meta or {}),
                conversation_id,
                turn_index,
                parent_request_id,
            ),
        )
        conn.commit()
        return True
    except Exception as e:
        logger.warning("observability: upsert_request failed: %s", e)
        if conn is not None:
            try:
                conn.rollback()
            except Exception:
                pass
        return False
    finally:
        if conn is not None:
            conn.close()


def update_request_response(run_id: str, request_id: str, final_response: str) -> bool:
    """Updates final_response of a request. Returns True on success."""
    if get_observability_mode() == "file_only":
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
        logger.warning("observability: update_request_response failed: %s", e)
        if conn is not None:
            try:
                conn.rollback()
            except Exception:
                pass
        return False
    finally:
        if conn is not None:
            conn.close()


def update_request_domain(run_id: str, request_id: str, domain: str | None) -> bool:
    """Updates domain of a request. Returns True on success."""
    if get_observability_mode() == "file_only":
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
        logger.warning("observability: update_request_domain failed: %s", e)
        if conn is not None:
            try:
                conn.rollback()
            except Exception:
                pass
        return False
    finally:
        if conn is not None:
            conn.close()


# ---------------------------------------------------------------------------
# Step 13 — multi-turn observability writers
# ---------------------------------------------------------------------------


def _coerce_bool_to_int(value: Any) -> int | None:
    """Coerce Python truthy/falsy/None into INTEGER or NULL for SQLite."""
    if value is None:
        return None
    if isinstance(value, bool):
        return 1 if value else 0
    try:
        return 1 if int(value) != 0 else 0
    except (ValueError, TypeError):
        return None


def update_request_meta(
    run_id: str,
    request_id: str,
    meta: dict[str, Any],
    *,
    merge: bool = True,
) -> bool:
    """
    Merge or replace governance metadata on the requests.meta_json column.

    Behaviour:
        - merge=True (default): read current meta_json, parse it as a JSON
          object when possible, update with `meta`, write back.
        - merge=False: replace meta_json entirely.
        - Malformed / empty / non-object meta_json is treated as `{}`.
        - Always best-effort: returns False on any failure, never raises.
    """
    if get_observability_mode() == "file_only":
        return False
    path = get_db_path()
    if not path:
        return False
    if not run_id or not request_id:
        return False
    if not isinstance(meta, dict):
        return False
    conn: sqlite3.Connection | None = None
    try:
        conn = _get_connection(path)
        if merge:
            row = conn.execute(
                "SELECT meta_json FROM requests WHERE run_id = ? AND request_id = ?",
                (run_id, request_id),
            ).fetchone()
            current: dict[str, Any] = {}
            if row is not None:
                raw = row["meta_json"]
                if isinstance(raw, str) and raw.strip():
                    try:
                        parsed = json.loads(raw)
                        if isinstance(parsed, dict):
                            current = parsed
                    except (ValueError, TypeError):
                        current = {}
            current.update(meta)
            merged_json = json.dumps(current, ensure_ascii=False, default=str)
        else:
            merged_json = json.dumps(meta, ensure_ascii=False, default=str)
        conn.execute(
            "UPDATE requests SET meta_json = ? WHERE run_id = ? AND request_id = ?",
            (merged_json, run_id, request_id),
        )
        conn.commit()
        return True
    except Exception as e:
        logger.warning("observability: update_request_meta failed: %s", e)
        if conn is not None:
            try:
                conn.rollback()
            except Exception:
                pass
        return False
    finally:
        if conn is not None:
            conn.close()


_CONVERSATION_STATES_INSERT = """
    INSERT INTO conversation_states (
        run_id, request_id, conversation_id, turn_index, created_at,
        state_in_json, state_out_json, state_summary_json,
        final_action, risk_score, posture,
        was_cached, cached_from_turn, refresh_required, refresh_reason
    )
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
"""

_LEDGER_EVENTS_INSERT = """
    INSERT INTO ledger_events (
        run_id, request_id, conversation_id, turn_index, created_at,
        operation, outcome, reason, similarity, from_turn,
        contract_hash, posture, domain, intent_clarity, request_type,
        final_action, risk_score, payload_json
    )
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
"""

_SESSION_STORE_EVENTS_INSERT = """
    INSERT INTO session_store_events (
        run_id, request_id, conversation_id, turn_index, created_at,
        operation, outcome, state_summary_json, payload_json
    )
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
"""

_PROXY_REQUEST_EVENTS_INSERT = """
    INSERT INTO proxy_request_events (
        run_id, request_id, conversation_id, turn_index, created_at,
        final_action, risk_score, path, domain,
        posture_in, posture_out,
        state_provided, state_updated, was_cached, cached_from_turn,
        final_response_length, headers_json, metadata_json,
        state_in_json, state_out_json, payload_json
    )
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
"""


def insert_conversation_state_event(payload: dict[str, Any]) -> bool:
    """Insert one row into `conversation_states`. Best-effort, never raises."""
    if get_observability_mode() == "file_only":
        return False
    path = get_db_path()
    if not path:
        return False
    run_id = (payload.get("run_id") or "").strip()
    request_id = (payload.get("request_id") or "").strip()
    conversation_id = (payload.get("conversation_id") or "").strip()
    if not run_id or not request_id or not conversation_id:
        return False
    conn: sqlite3.Connection | None = None
    try:
        conn = _get_connection(path)
        conn.execute(
            _CONVERSATION_STATES_INSERT,
            (
                run_id,
                request_id,
                conversation_id,
                payload.get("turn_index"),
                int(payload.get("created_at") or time.time() * 1000),
                _json_or_none(payload.get("state_in")),
                _json_or_none(payload.get("state_out")) or "{}",
                _json_or_none(payload.get("state_summary")),
                payload.get("final_action"),
                payload.get("risk_score"),
                payload.get("posture"),
                _coerce_bool_to_int(payload.get("was_cached")),
                payload.get("cached_from_turn"),
                _coerce_bool_to_int(payload.get("refresh_required")),
                payload.get("refresh_reason"),
            ),
        )
        conn.commit()
        return True
    except Exception as e:
        logger.warning("observability: insert_conversation_state_event failed: %s", e)
        if conn is not None:
            try:
                conn.rollback()
            except Exception:
                pass
        return False
    finally:
        if conn is not None:
            conn.close()


def insert_ledger_event(payload: dict[str, Any]) -> bool:
    """Insert one row into `ledger_events`. Best-effort, never raises."""
    if get_observability_mode() == "file_only":
        return False
    path = get_db_path()
    if not path:
        return False
    run_id = (payload.get("run_id") or "").strip()
    operation = (payload.get("operation") or "").strip()
    outcome = (payload.get("outcome") or "").strip()
    if not run_id or not operation or not outcome:
        return False
    conn: sqlite3.Connection | None = None
    try:
        conn = _get_connection(path)
        conn.execute(
            _LEDGER_EVENTS_INSERT,
            (
                run_id,
                payload.get("request_id"),
                payload.get("conversation_id"),
                payload.get("turn_index"),
                int(payload.get("created_at") or time.time() * 1000),
                operation,
                outcome,
                payload.get("reason"),
                payload.get("similarity"),
                payload.get("from_turn"),
                payload.get("contract_hash"),
                payload.get("posture"),
                payload.get("domain"),
                payload.get("intent_clarity"),
                payload.get("request_type"),
                payload.get("final_action"),
                payload.get("risk_score"),
                _json_or_none(payload.get("payload")),
            ),
        )
        conn.commit()
        return True
    except Exception as e:
        logger.warning("observability: insert_ledger_event failed: %s", e)
        if conn is not None:
            try:
                conn.rollback()
            except Exception:
                pass
        return False
    finally:
        if conn is not None:
            conn.close()


def insert_session_store_event(payload: dict[str, Any]) -> bool:
    """Insert one row into `session_store_events`. Best-effort, never raises."""
    if get_observability_mode() == "file_only":
        return False
    path = get_db_path()
    if not path:
        return False
    conversation_id = (payload.get("conversation_id") or "").strip()
    operation = (payload.get("operation") or "").strip()
    outcome = (payload.get("outcome") or "").strip()
    if not conversation_id or not operation or not outcome:
        return False
    conn: sqlite3.Connection | None = None
    try:
        conn = _get_connection(path)
        conn.execute(
            _SESSION_STORE_EVENTS_INSERT,
            (
                payload.get("run_id"),
                payload.get("request_id"),
                conversation_id,
                payload.get("turn_index"),
                int(payload.get("created_at") or time.time() * 1000),
                operation,
                outcome,
                _json_or_none(payload.get("state_summary")),
                _json_or_none(payload.get("payload")),
            ),
        )
        conn.commit()
        return True
    except Exception as e:
        logger.warning("observability: insert_session_store_event failed: %s", e)
        if conn is not None:
            try:
                conn.rollback()
            except Exception:
                pass
        return False
    finally:
        if conn is not None:
            conn.close()


def insert_proxy_request_event(payload: dict[str, Any]) -> bool:
    """Insert one row into `proxy_request_events`. Best-effort, never raises."""
    if get_observability_mode() == "file_only":
        return False
    path = get_db_path()
    if not path:
        return False
    run_id = (payload.get("run_id") or "").strip()
    request_id = (payload.get("request_id") or "").strip()
    if not run_id or not request_id:
        return False
    conn: sqlite3.Connection | None = None
    try:
        conn = _get_connection(path)
        conn.execute(
            _PROXY_REQUEST_EVENTS_INSERT,
            (
                run_id,
                request_id,
                payload.get("conversation_id"),
                payload.get("turn_index"),
                int(payload.get("created_at") or time.time() * 1000),
                payload.get("final_action"),
                payload.get("risk_score"),
                payload.get("path"),
                payload.get("domain"),
                payload.get("posture_in"),
                payload.get("posture_out"),
                _coerce_bool_to_int(payload.get("state_provided")),
                _coerce_bool_to_int(payload.get("state_updated")),
                _coerce_bool_to_int(payload.get("was_cached")),
                payload.get("cached_from_turn"),
                payload.get("final_response_length"),
                _json_or_none(payload.get("headers")),
                _json_or_none(payload.get("metadata")),
                _json_or_none(payload.get("state_in")),
                _json_or_none(payload.get("state_out")),
                _json_or_none(payload.get("payload")),
            ),
        )
        conn.commit()
        return True
    except Exception as e:
        logger.warning("observability: insert_proxy_request_event failed: %s", e)
        if conn is not None:
            try:
                conn.rollback()
            except Exception:
                pass
        return False
    finally:
        if conn is not None:
            conn.close()


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
        logger.warning("observability: delete_run failed: %s", e)
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
        logger.warning("observability: delete_request failed: %s", e)
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
    """Invalidates exports cache for a run or specific request."""
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
        logger.warning("observability: invalidate_exports_cache failed: %s", e)
        if conn is not None:
            try:
                conn.rollback()
            except Exception:
                pass
    finally:
        if conn is not None:
            conn.close()


# ---------------------------------------------------------------------------
# SqliteEventSink — EventEnvelope dispatch
# ---------------------------------------------------------------------------


def _json_or_none(obj: Any) -> str | None:
    if obj is None:
        return None
    try:
        return json.dumps(obj, ensure_ascii=False)
    except Exception:
        return None


class SqliteEventSink:
    """
    Implements EventSink by mapping each EventEnvelope to the appropriate
    SQLite table using the existing batch insert helpers.

    Lifecycle events (run.started, request.upserted, …) call the corresponding
    lifecycle write functions directly.
    """

    def write_envelope(self, envelope: EventEnvelope) -> None:
        """Write a single envelope. Does not raise."""
        try:
            self._dispatch(envelope)
        except Exception as e:
            logger.warning("observability[sqlite]: write_envelope failed event_type=%s: %s", envelope.event_type, e)

    def write_batch(self, envelopes: Sequence[EventEnvelope]) -> None:
        """Write multiple envelopes, grouping same-type writes into batch inserts."""
        by_type: dict[str, list[EventEnvelope]] = {}
        for ev in envelopes:
            by_type.setdefault(ev.event_type, []).append(ev)
        for event_type, batch in by_type.items():
            try:
                self._dispatch_batch(event_type, batch)
            except Exception as e:
                logger.warning(
                    "observability[sqlite]: write_batch failed event_type=%s count=%d: %s",
                    event_type,
                    len(batch),
                    e,
                )

    def flush(self, timeout: float = 30.0) -> None:
        """No-op: SQLite writes are synchronous within dispatch."""

    def close(self) -> None:
        """No-op: connections are opened/closed per-operation."""

    # ------------------------------------------------------------------
    # Internal dispatch
    # ------------------------------------------------------------------

    def _dispatch(self, envelope: EventEnvelope) -> None:
        et = envelope.event_type
        p = envelope.payload

        if et == EVENT_RUN_STARTED:
            create_run(
                run_id=envelope.run_id or p.get("run_id", ""),
                run_type=p.get("run_type", "session"),
                meta=p.get("meta"),
            )

        elif et == EVENT_RUN_ENDED:
            end_run(
                run_id=envelope.run_id or p.get("run_id", ""),
                status=p.get("status", "ok"),
            )

        elif et == EVENT_REQUEST_UPSERTED:
            upsert_request(
                run_id=envelope.run_id or p.get("run_id", ""),
                request_id=envelope.request_id or p.get("request_id", ""),
                prompt=p.get("prompt", ""),
                domain=p.get("domain"),
                meta=p.get("meta"),
                conversation_id=envelope.session_id or p.get("conversation_id"),
                turn_index=envelope.turn_number if envelope.turn_number is not None else p.get("turn_index"),
                parent_request_id=envelope.parent_event_id or p.get("parent_request_id"),
            )

        elif et == EVENT_REQUEST_DOMAIN_UPDATED:
            update_request_domain(
                run_id=envelope.run_id or p.get("run_id", ""),
                request_id=envelope.request_id or p.get("request_id", ""),
                domain=p.get("domain"),
            )

        elif et == EVENT_REQUEST_RESPONSE_UPDATED:
            update_request_response(
                run_id=envelope.run_id or p.get("run_id", ""),
                request_id=envelope.request_id or p.get("request_id", ""),
                final_response=p.get("final_response", ""),
            )

        elif et == EVENT_LLM_CALL:
            self._write_llm_call_single(envelope)

        elif et == EVENT_ORCHESTRATION_EVENT:
            self._write_orch_event_single(envelope)

        elif et == EVENT_DECISION_TRACE:
            self._write_decision_trace_single(envelope)

        elif et == EVENT_DEBUG_EVENT:
            self._write_debug_event_single(envelope)

        # Step 13 multi-turn observability dispatch
        elif et == EVENT_REQUEST_META_UPDATED:
            run_id = envelope.run_id or p.get("run_id", "")
            request_id = envelope.request_id or p.get("request_id", "")
            meta_payload = p.get("meta")
            if isinstance(meta_payload, dict):
                update_request_meta(
                    run_id=run_id,
                    request_id=request_id,
                    meta=meta_payload,
                    merge=bool(p.get("merge", True)),
                )

        elif et == EVENT_CONVERSATION_STATE_UPDATED:
            insert_conversation_state_event(self._build_conversation_state_payload(envelope))

        elif et in (EVENT_LEDGER_LOOKUP, EVENT_LEDGER_STORE):
            insert_ledger_event(self._build_ledger_event_payload(envelope))

        elif et in (EVENT_SESSION_STORE_GET, EVENT_SESSION_STORE_PUT):
            insert_session_store_event(self._build_session_store_event_payload(envelope))

        elif et == EVENT_PROXY_REQUEST_FINALIZED:
            insert_proxy_request_event(self._build_proxy_request_event_payload(envelope))

        # Lifecycle events that don't map to a table are silently ignored.

    def _dispatch_batch(self, event_type: str, batch: list[EventEnvelope]) -> None:
        if event_type == EVENT_LLM_CALL:
            self._write_llm_call_batch(batch)
        elif event_type == EVENT_ORCHESTRATION_EVENT:
            self._write_orch_event_batch(batch)
        elif event_type == EVENT_DECISION_TRACE:
            self._write_decision_trace_batch(batch)
        elif event_type == EVENT_DEBUG_EVENT:
            self._write_debug_event_batch(batch)
        else:
            # Step 13 multi-turn events are looped individually; the inserts
            # are small and infrequent, so per-event commits keep the schema
            # simple and side-effects predictable.
            for ev in batch:
                self._dispatch(ev)

    # ------------------------------------------------------------------
    # Step 13 multi-turn payload builders (EventEnvelope -> writer dict)
    # ------------------------------------------------------------------

    def _build_conversation_state_payload(self, ev: EventEnvelope) -> dict[str, Any]:
        p = dict(ev.payload or {})
        p.setdefault("run_id", ev.run_id or "")
        p.setdefault("request_id", ev.request_id or "")
        if "conversation_id" not in p:
            p["conversation_id"] = ev.session_id or ""
        if "turn_index" not in p:
            p["turn_index"] = ev.turn_number
        if "created_at" not in p:
            p["created_at"] = ev.timestamp_ms
        return p

    def _build_ledger_event_payload(self, ev: EventEnvelope) -> dict[str, Any]:
        p = dict(ev.payload or {})
        p.setdefault("run_id", ev.run_id or "")
        if "request_id" not in p:
            p["request_id"] = ev.request_id
        if "conversation_id" not in p:
            p["conversation_id"] = ev.session_id
        if "turn_index" not in p:
            p["turn_index"] = ev.turn_number
        if "created_at" not in p:
            p["created_at"] = ev.timestamp_ms
        return p

    def _build_session_store_event_payload(self, ev: EventEnvelope) -> dict[str, Any]:
        p = dict(ev.payload or {})
        if "run_id" not in p:
            p["run_id"] = ev.run_id
        if "request_id" not in p:
            p["request_id"] = ev.request_id
        if "conversation_id" not in p:
            p["conversation_id"] = ev.session_id or ""
        if "turn_index" not in p:
            p["turn_index"] = ev.turn_number
        if "created_at" not in p:
            p["created_at"] = ev.timestamp_ms
        return p

    def _build_proxy_request_event_payload(self, ev: EventEnvelope) -> dict[str, Any]:
        p = dict(ev.payload or {})
        p.setdefault("run_id", ev.run_id or "")
        p.setdefault("request_id", ev.request_id or "")
        if "conversation_id" not in p:
            p["conversation_id"] = ev.session_id
        if "turn_index" not in p:
            p["turn_index"] = ev.turn_number
        if "created_at" not in p:
            p["created_at"] = ev.timestamp_ms
        return p

    # ------------------------------------------------------------------
    # Single-row writers
    # ------------------------------------------------------------------

    def _write_llm_call_single(self, ev: EventEnvelope) -> None:
        if get_observability_mode() == "file_only":
            return
        path = get_db_path()
        if not path:
            return
        run_id = ev.run_id
        request_id = ev.request_id
        if not run_id or not request_id:
            return
        p = ev.payload
        now = int(time.time() * 1000)
        row = (
            run_id,
            request_id,
            ev.cycle,
            p.get("phase", ""),
            p.get("module", ""),
            p.get("action", ""),
            p.get("model", ""),
            p.get("started_at", now),
            p.get("duration_ms"),
            p.get("prompt", ""),
            p.get("system_prompt", ""),
            p.get("raw_response", ""),
            p.get("parsed_json"),
            p.get("parsed_summary_json"),
            p.get("token_usage_json"),
            p.get("attempts"),
            p.get("error"),
            p.get("sequence_in_cycle"),
            p.get("call_kind"),
            p.get("call_outcome"),
            p.get("cache_status"),
            p.get("related_event_id"),
        )
        conn = None
        try:
            conn = _get_connection(path)
            insert_llm_calls_batch(conn, [row])
            conn.commit()
        except Exception as e:
            logger.warning("observability[sqlite]: llm_call insert failed: %s", e)
            if conn:
                try:
                    conn.rollback()
                except Exception:
                    pass
        finally:
            if conn:
                conn.close()

    def _write_orch_event_single(self, ev: EventEnvelope) -> None:
        if get_observability_mode() == "file_only":
            return
        path = get_db_path()
        if not path:
            return
        run_id = ev.run_id
        request_id = ev.request_id
        if not run_id or not request_id:
            return
        p = ev.payload
        now = int(time.time() * 1000)
        row = (
            run_id,
            request_id,
            ev.cycle,
            p.get("stage", ""),
            p.get("component", ""),
            p.get("event_type", ""),
            p.get("decision"),
            p.get("status"),
            p.get("sequence"),
            p.get("started_at", now),
            p.get("duration_ms"),
            _json_or_none(p.get("reason_codes")),
            _json_or_none(p.get("inputs")),
            _json_or_none(p.get("outputs")),
            _json_or_none(p.get("payload")),
        )
        conn = None
        try:
            conn = _get_connection(path)
            insert_orchestration_events_batch(conn, [row])
            conn.commit()
        except Exception as e:
            logger.warning("observability[sqlite]: orch_event insert failed: %s", e)
            if conn:
                try:
                    conn.rollback()
                except Exception:
                    pass
        finally:
            if conn:
                conn.close()

    def _write_decision_trace_single(self, ev: EventEnvelope) -> None:
        if get_observability_mode() == "file_only":
            return
        path = get_db_path()
        if not path:
            return
        run_id = ev.run_id
        request_id = ev.request_id
        if not run_id or not request_id:
            return
        p = ev.payload
        row = (
            run_id,
            request_id,
            p.get("stage", ""),
            p.get("sequence", 0),
            p.get("trace_json", ""),
            p.get("created_at", int(time.time() * 1000)),
        )
        conn = None
        try:
            conn = _get_connection(path)
            insert_decision_traces_batch(conn, [row])
            conn.commit()
        except Exception as e:
            logger.warning("observability[sqlite]: decision_trace insert failed: %s", e)
            if conn:
                try:
                    conn.rollback()
                except Exception:
                    pass
        finally:
            if conn:
                conn.close()

    def _write_debug_event_single(self, ev: EventEnvelope) -> None:
        if get_observability_mode() == "file_only":
            return
        path = get_db_path()
        if not path:
            return
        run_id = ev.run_id
        if not run_id:
            return
        p = ev.payload
        payload_json = json.dumps(dict(p), ensure_ascii=False)
        row = (
            run_id,
            ev.request_id or "",
            int(time.time() * 1000),
            payload_json,
        )
        conn = None
        try:
            conn = _get_connection(path)
            insert_debug_events_batch(conn, [row])
            conn.commit()
        except Exception as e:
            logger.warning("observability[sqlite]: debug_event insert failed: %s", e)
            if conn:
                try:
                    conn.rollback()
                except Exception:
                    pass
        finally:
            if conn:
                conn.close()

    # ------------------------------------------------------------------
    # Batch writers
    # ------------------------------------------------------------------

    def _write_llm_call_batch(self, batch: list[EventEnvelope]) -> None:
        if get_observability_mode() == "file_only":
            return
        path = get_db_path()
        if not path:
            return
        now = int(time.time() * 1000)
        rows = []
        for ev in batch:
            run_id = ev.run_id
            request_id = ev.request_id
            if not run_id or not request_id:
                continue
            p = ev.payload
            rows.append(
                (
                    run_id,
                    request_id,
                    ev.cycle,
                    p.get("phase", ""),
                    p.get("module", ""),
                    p.get("action", ""),
                    p.get("model", ""),
                    p.get("started_at", now),
                    p.get("duration_ms"),
                    p.get("prompt", ""),
                    p.get("system_prompt", ""),
                    p.get("raw_response", ""),
                    p.get("parsed_json"),
                    p.get("parsed_summary_json"),
                    p.get("token_usage_json"),
                    p.get("attempts"),
                    p.get("error"),
                    p.get("sequence_in_cycle"),
                    p.get("call_kind"),
                    p.get("call_outcome"),
                    p.get("cache_status"),
                    p.get("related_event_id"),
                )
            )
        if not rows:
            return
        conn = None
        try:
            conn = _get_connection(path)
            insert_llm_calls_batch(conn, rows)
            conn.commit()
        except Exception as e:
            logger.warning("observability[sqlite]: llm_call batch insert failed: %s", e)
            if conn:
                try:
                    conn.rollback()
                except Exception:
                    pass
        finally:
            if conn:
                conn.close()

    def _write_orch_event_batch(self, batch: list[EventEnvelope]) -> None:
        if get_observability_mode() == "file_only":
            return
        path = get_db_path()
        if not path:
            return
        now = int(time.time() * 1000)
        rows = []
        for ev in batch:
            run_id = ev.run_id
            request_id = ev.request_id
            if not run_id or not request_id:
                continue
            p = ev.payload
            rows.append(
                (
                    run_id,
                    request_id,
                    ev.cycle,
                    p.get("stage", ""),
                    p.get("component", ""),
                    p.get("event_type", ""),
                    p.get("decision"),
                    p.get("status"),
                    p.get("sequence"),
                    p.get("started_at", now),
                    p.get("duration_ms"),
                    _json_or_none(p.get("reason_codes")),
                    _json_or_none(p.get("inputs")),
                    _json_or_none(p.get("outputs")),
                    _json_or_none(p.get("payload")),
                )
            )
        if not rows:
            return
        conn = None
        try:
            conn = _get_connection(path)
            insert_orchestration_events_batch(conn, rows)
            conn.commit()
        except Exception as e:
            logger.warning("observability[sqlite]: orch_event batch insert failed: %s", e)
            if conn:
                try:
                    conn.rollback()
                except Exception:
                    pass
        finally:
            if conn:
                conn.close()

    def _write_decision_trace_batch(self, batch: list[EventEnvelope]) -> None:
        if get_observability_mode() == "file_only":
            return
        path = get_db_path()
        if not path:
            return
        now = int(time.time() * 1000)
        rows = []
        for ev in batch:
            run_id = ev.run_id
            request_id = ev.request_id
            if not run_id or not request_id:
                continue
            p = ev.payload
            rows.append(
                (
                    run_id,
                    request_id,
                    p.get("stage", ""),
                    p.get("sequence", 0),
                    p.get("trace_json", ""),
                    p.get("created_at", now),
                )
            )
        if not rows:
            return
        conn = None
        try:
            conn = _get_connection(path)
            insert_decision_traces_batch(conn, rows)
            conn.commit()
        except Exception as e:
            logger.warning("observability[sqlite]: decision_trace batch insert failed: %s", e)
            if conn:
                try:
                    conn.rollback()
                except Exception:
                    pass
        finally:
            if conn:
                conn.close()

    def _write_debug_event_batch(self, batch: list[EventEnvelope]) -> None:
        if get_observability_mode() == "file_only":
            return
        path = get_db_path()
        if not path:
            return
        now = int(time.time() * 1000)
        rows = []
        for ev in batch:
            run_id = ev.run_id
            if not run_id:
                continue
            p = ev.payload
            payload_json = json.dumps(dict(p), ensure_ascii=False)
            rows.append((run_id, ev.request_id or "", now, payload_json))
        if not rows:
            return
        conn = None
        try:
            conn = _get_connection(path)
            insert_debug_events_batch(conn, rows)
            conn.commit()
        except Exception as e:
            logger.warning("observability[sqlite]: debug_event batch insert failed: %s", e)
            if conn:
                try:
                    conn.rollback()
                except Exception:
                    pass
        finally:
            if conn:
                conn.close()
