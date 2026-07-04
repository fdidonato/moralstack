"""
SQLite event sink for MoralStack observability.

Contains the full SQLite schema (migrated from persistence/db.py), the
SqliteUnitOfWork context manager, low-level batch insert helpers, all lifecycle
write functions, and SqliteEventSink which maps EventEnvelope -> SQL rows.

Schema is compatible with the original persistence/db.py. Additive migrations
are applied idempotently in init_db().
"""

from __future__ import annotations

import json
import logging
import sqlite3
import time
from pathlib import Path
from typing import Any, Mapping, Sequence

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
    EVENT_REQUEST_TOKEN_USAGE_FINALIZED,
    EVENT_REQUEST_UPSERTED,
    EVENT_RUN_ENDED,
    EVENT_RUN_STARTED,
    EVENT_SESSION_STORE_GET,
    EVENT_SESSION_STORE_PUT,
    EventEnvelope,
)
from moralstack.observability.token_usage import TokenUsage

logger = logging.getLogger(__name__)

_FK_ORDER: dict[str, int] = {
    EVENT_RUN_STARTED: 0,
    EVENT_REQUEST_UPSERTED: 10,
    EVENT_REQUEST_DOMAIN_UPDATED: 20,
    EVENT_REQUEST_RESPONSE_UPDATED: 21,
    EVENT_REQUEST_META_UPDATED: 22,
    EVENT_ORCHESTRATION_EVENT: 30,
    EVENT_LLM_CALL: 31,
    EVENT_DECISION_TRACE: 32,
    EVENT_CONVERSATION_STATE_UPDATED: 33,
    EVENT_LEDGER_LOOKUP: 34,
    EVENT_LEDGER_STORE: 35,
    EVENT_SESSION_STORE_GET: 36,
    EVENT_SESSION_STORE_PUT: 37,
    EVENT_PROXY_REQUEST_FINALIZED: 38,
    EVENT_REQUEST_TOKEN_USAGE_FINALIZED: 39,
    EVENT_DEBUG_EVENT: 40,
    EVENT_RUN_ENDED: 100,
}
_LOCK_RETRY_DELAYS_S = (0.01, 0.025, 0.05, 0.1)


def _is_sqlite_locked(exc: BaseException) -> bool:
    message = str(exc).lower()
    return "database is locked" in message or "database table is locked" in message


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
              ON proxy_request_events(run_id, request_id);

          CREATE TABLE IF NOT EXISTS request_token_usage
          (
              run_id                  TEXT    NOT NULL,
              request_id              TEXT    NOT NULL,
              input_tokens            INTEGER NOT NULL DEFAULT 0,
              output_tokens           INTEGER NOT NULL DEFAULT 0,
              total_tokens            INTEGER NOT NULL DEFAULT 0,
              llm_call_count          INTEGER NOT NULL DEFAULT 0,
              missing_usage_count     INTEGER NOT NULL DEFAULT 0,
              estimated_usage_count   INTEGER NOT NULL DEFAULT 0,
              usage_may_be_incomplete INTEGER NOT NULL DEFAULT 0,
              incomplete_reason       TEXT,
              finalized_at            INTEGER NOT NULL,
              PRIMARY KEY (run_id, request_id),
              FOREIGN KEY (run_id, request_id)
                  REFERENCES requests(run_id, request_id) ON DELETE CASCADE
          ); \
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
                           call_kind, call_outcome, cache_status, related_event_id,
                           input_tokens, output_tokens, total_tokens,
                           token_usage_missing, token_usage_estimated, billable_provider_call)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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


def _text_or_json(value: Any) -> str | None:
    if value is None or isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False)


def _token_usage_json_str(value: Any) -> str | None:
    return _text_or_json(value)


def _derive_llm_call_token_columns(payload: Mapping[str, Any]) -> tuple[Any, ...]:
    usage = TokenUsage.from_json(_token_usage_json_str(payload.get("token_usage_json")))
    if usage.total_tokens == 0 and usage.source == "missing":
        input_tokens = output_tokens = total_tokens = None
    else:
        input_tokens = usage.input_tokens
        output_tokens = usage.output_tokens
        total_tokens = usage.total_tokens
    token_usage_missing = 1 if usage.source == "missing" else 0
    token_usage_estimated = 1 if usage.source == "estimated" else 0
    billable_raw = payload.get("billable_provider_call")
    if billable_raw is None:
        billable_provider_call = None
    else:
        billable_provider_call = 1 if billable_raw else 0
    return (
        input_tokens,
        output_tokens,
        total_tokens,
        token_usage_missing,
        token_usage_estimated,
        billable_provider_call,
    )


def _llm_call_row_from_envelope(ev: EventEnvelope, now: int) -> tuple[Any, ...] | None:
    run_id = ev.run_id
    request_id = ev.request_id
    if not run_id or not request_id:
        return None
    p = ev.payload
    token_cols = _derive_llm_call_token_columns(p)
    return (
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
        _text_or_json(p.get("parsed_json")),
        _text_or_json(p.get("parsed_summary_json")),
        _text_or_json(p.get("token_usage_json")),
        p.get("attempts"),
        p.get("error"),
        p.get("sequence_in_cycle"),
        p.get("call_kind"),
        p.get("call_outcome"),
        p.get("cache_status"),
        p.get("related_event_id"),
        *token_cols,
    )


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
        try:
            conn.execute("""
                DELETE FROM proxy_request_events
                WHERE id NOT IN (
                    SELECT MAX(id)
                    FROM proxy_request_events
                    GROUP BY run_id, request_id
                )
                """)
            conn.commit()
        except Exception:
            pass
        try:
            conn.execute("""
                CREATE UNIQUE INDEX IF NOT EXISTS idx_proxy_request_events_run_request_unique
                    ON proxy_request_events(run_id, request_id)
                """)
            conn.commit()
        except Exception:
            pass
        for _col, _sql in (
            ("sequence_in_cycle", "ALTER TABLE llm_calls ADD COLUMN sequence_in_cycle INTEGER"),
            ("call_kind", "ALTER TABLE llm_calls ADD COLUMN call_kind TEXT"),
            ("call_outcome", "ALTER TABLE llm_calls ADD COLUMN call_outcome TEXT"),
            ("cache_status", "ALTER TABLE llm_calls ADD COLUMN cache_status TEXT"),
            ("related_event_id", "ALTER TABLE llm_calls ADD COLUMN related_event_id INTEGER"),
            ("input_tokens", "ALTER TABLE llm_calls ADD COLUMN input_tokens INTEGER"),
            ("output_tokens", "ALTER TABLE llm_calls ADD COLUMN output_tokens INTEGER"),
            ("total_tokens", "ALTER TABLE llm_calls ADD COLUMN total_tokens INTEGER"),
            ("token_usage_missing", "ALTER TABLE llm_calls ADD COLUMN token_usage_missing INTEGER"),
            ("token_usage_estimated", "ALTER TABLE llm_calls ADD COLUMN token_usage_estimated INTEGER"),
            ("billable_provider_call", "ALTER TABLE llm_calls ADD COLUMN billable_provider_call INTEGER"),
        ):
            try:
                conn.execute(_sql)
                conn.commit()
            except Exception:
                pass
        for _idx_sql in (
            "CREATE INDEX IF NOT EXISTS idx_llm_calls_module_model "
            "ON llm_calls(run_id, request_id, module, phase, action, model)",
        ):
            try:
                conn.execute(_idx_sql)
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


def create_run(
    run_id: str,
    run_type: str,
    meta: dict[str, Any] | None = None,
    *,
    conn: sqlite3.Connection | None = None,
) -> bool:
    """Creates a run record. Returns True on success."""
    if conn is None and get_observability_mode() == "file_only":
        return False
    path = get_db_path()
    if conn is None and not path:
        return False
    owns_conn = conn is None
    try:
        if conn is None:
            conn = _get_connection(path or "")
        conn.execute(
            "INSERT OR IGNORE INTO runs (run_id, run_type, started_at, ended_at, status, meta_json)"
            " VALUES (?, ?, ?, NULL, 'running', ?)",
            (run_id, run_type, int(time.time() * 1000), json.dumps(meta or {})),
        )
        if owns_conn:
            conn.commit()
        return True
    except Exception as e:
        if not owns_conn:
            raise
        logger.warning("observability: create_run failed: %s", e)
        if conn is not None:
            try:
                conn.rollback()
            except Exception:
                pass
        return False
    finally:
        if owns_conn and conn is not None:
            conn.close()


def end_run(run_id: str, status: str = "ok", *, conn: sqlite3.Connection | None = None) -> bool:
    """Marks a run as ended. Returns True on success."""
    if conn is None and get_observability_mode() == "file_only":
        return False
    path = get_db_path()
    if conn is None and not path:
        return False
    owns_conn = conn is None
    try:
        if conn is None:
            conn = _get_connection(path or "")
        conn.execute(
            "UPDATE runs SET ended_at = ?, status = ? WHERE run_id = ?",
            (int(time.time() * 1000), status, run_id),
        )
        if owns_conn:
            conn.commit()
        return True
    except Exception as e:
        if not owns_conn:
            raise
        logger.warning("observability: end_run failed: %s", e)
        if conn is not None:
            try:
                conn.rollback()
            except Exception:
                pass
        return False
    finally:
        if owns_conn and conn is not None:
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
    conn: sqlite3.Connection | None = None,
) -> bool:
    """Inserts or ignores a request row. Returns True on success."""
    if conn is None and get_observability_mode() == "file_only":
        return False
    path = get_db_path()
    if conn is None and not path:
        return False
    owns_conn = conn is None
    try:
        if conn is None:
            conn = _get_connection(path or "")
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
        if owns_conn:
            conn.commit()
        return True
    except Exception as e:
        if not owns_conn:
            raise
        logger.warning("observability: upsert_request failed: %s", e)
        if conn is not None:
            try:
                conn.rollback()
            except Exception:
                pass
        return False
    finally:
        if owns_conn and conn is not None:
            conn.close()


def update_request_response(
    run_id: str,
    request_id: str,
    final_response: str,
    *,
    conn: sqlite3.Connection | None = None,
) -> bool:
    """Updates final_response of a request. Returns True on success."""
    if conn is None and get_observability_mode() == "file_only":
        return False
    path = get_db_path()
    if conn is None and not path:
        return False
    owns_conn = conn is None
    try:
        if conn is None:
            conn = _get_connection(path or "")
        cur = conn.execute(
            "UPDATE requests SET final_response = ? WHERE run_id = ? AND request_id = ?",
            (final_response, run_id, request_id),
        )
        if not owns_conn and cur.rowcount == 0:
            # Audit path (windowed/sync): a matched-zero UPDATE means the parent
            # requests row is missing, so the row was NOT persisted. Raise so the
            # caller counts a failure instead of recording a phantom write.
            raise RuntimeError(f"update_request_response: no requests row run_id={run_id} request_id={request_id}")
        if owns_conn:
            conn.commit()
        return True
    except Exception as e:
        if not owns_conn:
            raise
        logger.warning("observability: update_request_response failed: %s", e)
        if conn is not None:
            try:
                conn.rollback()
            except Exception:
                pass
        return False
    finally:
        if owns_conn and conn is not None:
            conn.close()


def update_request_domain(
    run_id: str,
    request_id: str,
    domain: str | None,
    *,
    conn: sqlite3.Connection | None = None,
) -> bool:
    """Updates domain of a request. Returns True on success."""
    if conn is None and get_observability_mode() == "file_only":
        return False
    path = get_db_path()
    if conn is None and not path:
        return False
    owns_conn = conn is None
    try:
        if conn is None:
            conn = _get_connection(path or "")
        cur = conn.execute(
            "UPDATE requests SET domain = ? WHERE run_id = ? AND request_id = ?",
            (domain or "", run_id, request_id),
        )
        if not owns_conn and cur.rowcount == 0:
            raise RuntimeError(f"update_request_domain: no requests row run_id={run_id} request_id={request_id}")
        if owns_conn:
            conn.commit()
        return True
    except Exception as e:
        if not owns_conn:
            raise
        logger.warning("observability: update_request_domain failed: %s", e)
        if conn is not None:
            try:
                conn.rollback()
            except Exception:
                pass
        return False
    finally:
        if owns_conn and conn is not None:
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
    conn: sqlite3.Connection | None = None,
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
    if conn is None and get_observability_mode() == "file_only":
        return False
    path = get_db_path()
    if conn is None and not path:
        return False
    if not run_id or not request_id:
        if conn is not None:
            raise ValueError("update_request_meta requires run_id and request_id")
        return False
    if not isinstance(meta, dict):
        if conn is not None:
            raise TypeError("update_request_meta meta must be a dict")
        return False
    owns_conn = conn is None
    try:
        if conn is None:
            conn = _get_connection(path or "")
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
        cur = conn.execute(
            "UPDATE requests SET meta_json = ? WHERE run_id = ? AND request_id = ?",
            (merged_json, run_id, request_id),
        )
        if not owns_conn and cur.rowcount == 0:
            # Audit path: final_action travels in meta_json; a matched-zero UPDATE
            # means the parent requests row is missing, so the decision was NOT
            # persisted. Raise so the caller counts a failure (never a phantom write).
            raise RuntimeError(f"update_request_meta: no requests row run_id={run_id} request_id={request_id}")
        if owns_conn:
            conn.commit()
        return True
    except Exception as e:
        if not owns_conn:
            raise
        logger.warning("observability: update_request_meta failed: %s", e)
        if conn is not None:
            try:
                conn.rollback()
            except Exception:
                pass
        return False
    finally:
        if owns_conn and conn is not None:
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
    ON CONFLICT(run_id, request_id) DO UPDATE SET
        conversation_id = excluded.conversation_id,
        turn_index = excluded.turn_index,
        created_at = excluded.created_at,
        final_action = excluded.final_action,
        risk_score = excluded.risk_score,
        path = excluded.path,
        domain = excluded.domain,
        posture_in = excluded.posture_in,
        posture_out = excluded.posture_out,
        state_provided = excluded.state_provided,
        state_updated = excluded.state_updated,
        was_cached = excluded.was_cached,
        cached_from_turn = excluded.cached_from_turn,
        final_response_length = excluded.final_response_length,
        headers_json = excluded.headers_json,
        metadata_json = excluded.metadata_json,
        state_in_json = excluded.state_in_json,
        state_out_json = excluded.state_out_json,
        payload_json = excluded.payload_json
"""

_REQUEST_TOKEN_USAGE_INSERT = """
    INSERT INTO request_token_usage (
        run_id, request_id,
        input_tokens, output_tokens, total_tokens,
        llm_call_count, missing_usage_count, estimated_usage_count,
        usage_may_be_incomplete, incomplete_reason, finalized_at
    )
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ON CONFLICT(run_id, request_id) DO UPDATE SET
        input_tokens = excluded.input_tokens,
        output_tokens = excluded.output_tokens,
        total_tokens = excluded.total_tokens,
        llm_call_count = excluded.llm_call_count,
        missing_usage_count = excluded.missing_usage_count,
        estimated_usage_count = excluded.estimated_usage_count,
        usage_may_be_incomplete = excluded.usage_may_be_incomplete,
        incomplete_reason = excluded.incomplete_reason,
        finalized_at = excluded.finalized_at
"""


def insert_conversation_state_event(payload: dict[str, Any], *, conn: sqlite3.Connection | None = None) -> bool:
    """Insert one row into `conversation_states`. Best-effort, never raises."""
    if conn is None and get_observability_mode() == "file_only":
        return False
    path = get_db_path()
    if conn is None and not path:
        return False
    run_id = (payload.get("run_id") or "").strip()
    request_id = (payload.get("request_id") or "").strip()
    conversation_id = (payload.get("conversation_id") or "").strip()
    if not run_id or not request_id or not conversation_id:
        if conn is not None:
            raise ValueError("conversation state event requires run_id, request_id, conversation_id")
        return False
    owns_conn = conn is None
    try:
        if conn is None:
            conn = _get_connection(path or "")
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
        if owns_conn:
            conn.commit()
        return True
    except Exception as e:
        if not owns_conn:
            raise
        logger.warning("observability: insert_conversation_state_event failed: %s", e)
        if conn is not None:
            try:
                conn.rollback()
            except Exception:
                pass
        return False
    finally:
        if owns_conn and conn is not None:
            conn.close()


def insert_ledger_event(payload: dict[str, Any], *, conn: sqlite3.Connection | None = None) -> bool:
    """Insert one row into `ledger_events`. Best-effort, never raises."""
    if conn is None and get_observability_mode() == "file_only":
        return False
    path = get_db_path()
    if conn is None and not path:
        return False
    run_id = (payload.get("run_id") or "").strip()
    operation = (payload.get("operation") or "").strip()
    outcome = (payload.get("outcome") or "").strip()
    if not run_id or not operation or not outcome:
        if conn is not None:
            raise ValueError("ledger event requires run_id, operation, outcome")
        return False
    owns_conn = conn is None
    try:
        if conn is None:
            conn = _get_connection(path or "")
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
        if owns_conn:
            conn.commit()
        return True
    except Exception as e:
        if not owns_conn:
            raise
        logger.warning("observability: insert_ledger_event failed: %s", e)
        if conn is not None:
            try:
                conn.rollback()
            except Exception:
                pass
        return False
    finally:
        if owns_conn and conn is not None:
            conn.close()


def insert_session_store_event(payload: dict[str, Any], *, conn: sqlite3.Connection | None = None) -> bool:
    """Insert one row into `session_store_events`. Best-effort, never raises."""
    if conn is None and get_observability_mode() == "file_only":
        return False
    path = get_db_path()
    if conn is None and not path:
        return False
    conversation_id = (payload.get("conversation_id") or "").strip()
    operation = (payload.get("operation") or "").strip()
    outcome = (payload.get("outcome") or "").strip()
    if not conversation_id or not operation or not outcome:
        if conn is not None:
            raise ValueError("session store event requires conversation_id, operation, outcome")
        return False
    owns_conn = conn is None
    try:
        if conn is None:
            conn = _get_connection(path or "")
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
        if owns_conn:
            conn.commit()
        return True
    except Exception as e:
        if not owns_conn:
            raise
        logger.warning("observability: insert_session_store_event failed: %s", e)
        if conn is not None:
            try:
                conn.rollback()
            except Exception:
                pass
        return False
    finally:
        if owns_conn and conn is not None:
            conn.close()


def insert_proxy_request_event(payload: dict[str, Any], *, conn: sqlite3.Connection | None = None) -> bool:
    """Insert one row into `proxy_request_events`. Best-effort, never raises."""
    if conn is None and get_observability_mode() == "file_only":
        return False
    path = get_db_path()
    if conn is None and not path:
        return False
    run_id = (payload.get("run_id") or "").strip()
    request_id = (payload.get("request_id") or "").strip()
    if not run_id or not request_id:
        if conn is not None:
            raise ValueError("proxy request event requires run_id and request_id")
        return False
    owns_conn = conn is None
    try:
        if conn is None:
            conn = _get_connection(path or "")
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
        if owns_conn:
            conn.commit()
        return True
    except Exception as e:
        if not owns_conn:
            raise
        logger.warning("observability: insert_proxy_request_event failed: %s", e)
        if conn is not None:
            try:
                conn.rollback()
            except Exception:
                pass
        return False
    finally:
        if owns_conn and conn is not None:
            conn.close()


def insert_request_token_usage(payload: dict[str, Any], *, conn: sqlite3.Connection | None = None) -> bool:
    """Insert or replace one row in `request_token_usage`. Best-effort, never raises."""
    if conn is None and get_observability_mode() == "file_only":
        return False
    path = get_db_path()
    if conn is None and not path:
        return False
    run_id = (payload.get("run_id") or "").strip()
    request_id = (payload.get("request_id") or "").strip()
    if not run_id or not request_id:
        if conn is not None:
            raise ValueError("request token usage requires run_id and request_id")
        return False
    owns_conn = conn is None
    try:
        if conn is None:
            conn = _get_connection(path or "")
        conn.execute(
            _REQUEST_TOKEN_USAGE_INSERT,
            (
                run_id,
                request_id,
                int(payload.get("input_tokens") or 0),
                int(payload.get("output_tokens") or 0),
                int(payload.get("total_tokens") or 0),
                int(payload.get("llm_call_count") or 0),
                int(payload.get("missing_usage_count") or 0),
                int(payload.get("estimated_usage_count") or 0),
                _coerce_bool_to_int(payload.get("usage_may_be_incomplete")) or 0,
                payload.get("incomplete_reason"),
                int(payload.get("finalized_at") or time.time() * 1000),
            ),
        )
        if owns_conn:
            conn.commit()
        return True
    except Exception as e:
        if not owns_conn:
            raise
        logger.warning("observability: insert_request_token_usage failed: %s", e)
        if conn is not None:
            try:
                conn.rollback()
            except Exception:
                pass
        return False
    finally:
        if owns_conn and conn is not None:
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

    def write_window(self, envelopes: Sequence[EventEnvelope], conn: sqlite3.Connection | None) -> Any:
        """Write an FK-ordered envelope window on a caller-owned connection."""
        from moralstack.observability.router import WindowResult

        if not envelopes:
            return WindowResult()
        if conn is None:
            return WindowResult(failed=len(envelopes), sqlite_failed=len(envelopes), error="missing sqlite connection")
        ordered = self._ordered_for_fk(envelopes)
        first_exc: Exception | None = None
        for attempt, delay_s in enumerate((0.0, *_LOCK_RETRY_DELAYS_S)):
            if delay_s:
                time.sleep(delay_s)
            try:
                conn.execute("BEGIN")
                for envelope in ordered:
                    self._dispatch_raising(envelope, conn)
                conn.commit()
                return WindowResult(written=len(ordered), sqlite_written=len(ordered))
            except Exception as exc:
                first_exc = exc
                try:
                    conn.rollback()
                except Exception:
                    pass
                if _is_sqlite_locked(exc) and attempt < len(_LOCK_RETRY_DELAYS_S):
                    continue
                break
        logger.warning("observability[sqlite]: write_window transaction failed: %s", first_exc)

        written = 0
        failed = 0
        first_error: str | None = None
        for envelope in ordered:
            envelope_written = False
            last_exc: Exception | None = None
            for attempt, delay_s in enumerate((0.0, *_LOCK_RETRY_DELAYS_S)):
                if delay_s:
                    time.sleep(delay_s)
                try:
                    conn.execute("BEGIN")
                    self._dispatch_raising(envelope, conn)
                    conn.commit()
                    written += 1
                    envelope_written = True
                    break
                except Exception as exc:
                    last_exc = exc
                    try:
                        conn.rollback()
                    except Exception:
                        pass
                    if _is_sqlite_locked(exc) and attempt < len(_LOCK_RETRY_DELAYS_S):
                        continue
                    break
            if not envelope_written:
                failed += 1
                if first_error is None:
                    first_error = str(last_exc)
                logger.warning(
                    "observability[sqlite]: write_window isolated failed event_type=%s: %s",
                    getattr(envelope, "event_type", "?"),
                    last_exc,
                )
        return WindowResult(
            written=written,
            failed=failed,
            sqlite_written=written,
            sqlite_failed=failed,
            error=first_error,
        )

    def flush(self, timeout: float = 30.0) -> None:
        """No-op: SQLite writes are synchronous within dispatch."""

    def close(self) -> None:
        """No-op: connections are opened/closed per-operation."""

    # ------------------------------------------------------------------
    # Internal dispatch
    # ------------------------------------------------------------------

    def _ordered_for_fk(self, envelopes: Sequence[EventEnvelope]) -> list[EventEnvelope]:
        return [
            ev
            for _, ev in sorted(
                enumerate(envelopes),
                key=lambda item: (_FK_ORDER.get(getattr(item[1], "event_type", ""), 50), item[0]),
            )
        ]

    def _dispatch_raising(self, envelope: EventEnvelope, conn: sqlite3.Connection) -> None:
        et = envelope.event_type
        p = envelope.payload

        if et == EVENT_RUN_STARTED:
            create_run(
                run_id=envelope.run_id or p.get("run_id", ""),
                run_type=p.get("run_type", "session"),
                meta=p.get("meta"),
                conn=conn,
            )
        elif et == EVENT_RUN_ENDED:
            end_run(
                run_id=envelope.run_id or p.get("run_id", ""),
                status=p.get("status", "ok"),
                conn=conn,
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
                conn=conn,
            )
        elif et == EVENT_REQUEST_DOMAIN_UPDATED:
            update_request_domain(
                run_id=envelope.run_id or p.get("run_id", ""),
                request_id=envelope.request_id or p.get("request_id", ""),
                domain=p.get("domain"),
                conn=conn,
            )
        elif et == EVENT_REQUEST_RESPONSE_UPDATED:
            update_request_response(
                run_id=envelope.run_id or p.get("run_id", ""),
                request_id=envelope.request_id or p.get("request_id", ""),
                final_response=p.get("final_response", ""),
                conn=conn,
            )
        elif et == EVENT_REQUEST_META_UPDATED:
            meta_payload = p.get("meta")
            if not isinstance(meta_payload, dict):
                raise TypeError("request.meta_updated payload.meta must be a dict")
            update_request_meta(
                run_id=envelope.run_id or p.get("run_id", ""),
                request_id=envelope.request_id or p.get("request_id", ""),
                meta=meta_payload,
                merge=bool(p.get("merge", True)),
                conn=conn,
            )
        elif et == EVENT_LLM_CALL:
            self._write_llm_call_batch([envelope], conn=conn)
        elif et == EVENT_ORCHESTRATION_EVENT:
            self._write_orch_event_batch([envelope], conn=conn)
        elif et == EVENT_DECISION_TRACE:
            self._write_decision_trace_batch([envelope], conn=conn)
        elif et == EVENT_DEBUG_EVENT:
            self._write_debug_event_batch([envelope], conn=conn)
        elif et == EVENT_CONVERSATION_STATE_UPDATED:
            insert_conversation_state_event(self._build_conversation_state_payload(envelope), conn=conn)
        elif et in (EVENT_LEDGER_LOOKUP, EVENT_LEDGER_STORE):
            insert_ledger_event(self._build_ledger_event_payload(envelope), conn=conn)
        elif et in (EVENT_SESSION_STORE_GET, EVENT_SESSION_STORE_PUT):
            insert_session_store_event(self._build_session_store_event_payload(envelope), conn=conn)
        elif et == EVENT_PROXY_REQUEST_FINALIZED:
            insert_proxy_request_event(self._build_proxy_request_event_payload(envelope), conn=conn)
        elif et == EVENT_REQUEST_TOKEN_USAGE_FINALIZED:
            insert_request_token_usage(self._build_request_token_usage_payload(envelope), conn=conn)
        else:
            raise ValueError(f"unsupported event_type for sqlite window: {et}")

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

        elif et == EVENT_REQUEST_TOKEN_USAGE_FINALIZED:
            insert_request_token_usage(self._build_request_token_usage_payload(envelope))

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

    def _build_request_token_usage_payload(self, ev: EventEnvelope) -> dict[str, Any]:
        p = dict(ev.payload or {})
        p.setdefault("run_id", ev.run_id or "")
        p.setdefault("request_id", ev.request_id or "")
        if "finalized_at" not in p:
            p["finalized_at"] = ev.timestamp_ms
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
        now = int(time.time() * 1000)
        row = _llm_call_row_from_envelope(ev, now)
        if row is None:
            return
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

    def _write_llm_call_batch(self, batch: list[EventEnvelope], conn: sqlite3.Connection | None = None) -> None:
        if conn is None and get_observability_mode() == "file_only":
            return
        path = get_db_path()
        if conn is None and not path:
            return
        now = int(time.time() * 1000)
        rows = []
        for ev in batch:
            row = _llm_call_row_from_envelope(ev, now)
            if row is None:
                if conn is not None:
                    raise ValueError("llm.call requires run_id and request_id")
                continue
            rows.append(row)
        if not rows:
            if conn is not None and batch:
                raise ValueError("llm.call window produced no rows")
            return
        owns_conn = conn is None
        try:
            if conn is None:
                conn = _get_connection(path or "")
            insert_llm_calls_batch(conn, rows)
            if owns_conn:
                conn.commit()
        except Exception as e:
            if not owns_conn:
                raise
            logger.warning("observability[sqlite]: llm_call batch insert failed: %s", e)
            if conn:
                try:
                    conn.rollback()
                except Exception:
                    pass
        finally:
            if owns_conn and conn:
                conn.close()

    def _write_orch_event_batch(self, batch: list[EventEnvelope], conn: sqlite3.Connection | None = None) -> None:
        if conn is None and get_observability_mode() == "file_only":
            return
        path = get_db_path()
        if conn is None and not path:
            return
        now = int(time.time() * 1000)
        rows = []
        for ev in batch:
            run_id = ev.run_id
            request_id = ev.request_id
            if not run_id or not request_id:
                if conn is not None:
                    raise ValueError("orchestration.event requires run_id and request_id")
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
            if conn is not None and batch:
                raise ValueError("orchestration.event window produced no rows")
            return
        owns_conn = conn is None
        try:
            if conn is None:
                conn = _get_connection(path or "")
            insert_orchestration_events_batch(conn, rows)
            if owns_conn:
                conn.commit()
        except Exception as e:
            if not owns_conn:
                raise
            logger.warning("observability[sqlite]: orch_event batch insert failed: %s", e)
            if conn:
                try:
                    conn.rollback()
                except Exception:
                    pass
        finally:
            if owns_conn and conn:
                conn.close()

    def _write_decision_trace_batch(self, batch: list[EventEnvelope], conn: sqlite3.Connection | None = None) -> None:
        if conn is None and get_observability_mode() == "file_only":
            return
        path = get_db_path()
        if conn is None and not path:
            return
        now = int(time.time() * 1000)
        rows = []
        for ev in batch:
            run_id = ev.run_id
            request_id = ev.request_id
            if not run_id or not request_id:
                if conn is not None:
                    raise ValueError("decision.trace requires run_id and request_id")
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
            if conn is not None and batch:
                raise ValueError("decision.trace window produced no rows")
            return
        owns_conn = conn is None
        try:
            if conn is None:
                conn = _get_connection(path or "")
            insert_decision_traces_batch(conn, rows)
            if owns_conn:
                conn.commit()
        except Exception as e:
            if not owns_conn:
                raise
            logger.warning("observability[sqlite]: decision_trace batch insert failed: %s", e)
            if conn:
                try:
                    conn.rollback()
                except Exception:
                    pass
        finally:
            if owns_conn and conn:
                conn.close()

    def _write_debug_event_batch(self, batch: list[EventEnvelope], conn: sqlite3.Connection | None = None) -> None:
        if conn is None and get_observability_mode() == "file_only":
            return
        path = get_db_path()
        if conn is None and not path:
            return
        now = int(time.time() * 1000)
        rows = []
        for ev in batch:
            run_id = ev.run_id
            if not run_id:
                if conn is not None:
                    raise ValueError("debug.event requires run_id")
                continue
            p = ev.payload
            payload_json = json.dumps(dict(p), ensure_ascii=False)
            rows.append((run_id, ev.request_id or "", now, payload_json))
        if not rows:
            if conn is not None and batch:
                raise ValueError("debug.event window produced no rows")
            return
        owns_conn = conn is None
        try:
            if conn is None:
                conn = _get_connection(path or "")
            insert_debug_events_batch(conn, rows)
            if owns_conn:
                conn.commit()
        except Exception as e:
            if not owns_conn:
                raise
            logger.warning("observability[sqlite]: debug_event batch insert failed: %s", e)
            if conn:
                try:
                    conn.rollback()
                except Exception:
                    pass
        finally:
            if owns_conn and conn:
                conn.close()
