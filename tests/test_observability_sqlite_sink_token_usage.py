"""Tests for SQLite sink token usage schema and dispatch."""

from __future__ import annotations

import json
import sqlite3

from moralstack.observability.events import (
    EVENT_LLM_CALL,
    EVENT_PROXY_REQUEST_FINALIZED,
    EVENT_REQUEST_TOKEN_USAGE_FINALIZED,
    make_envelope,
)
from moralstack.observability.sinks.sqlite_sink import (
    _FK_ORDER,
    SqliteEventSink,
    _get_connection,
    create_run,
    init_db,
    upsert_request,
)


def _setup(tmp_path, monkeypatch):
    dbp = str(tmp_path / "token_sink.db")
    monkeypatch.setenv("MORALSTACK_DB_PATH", dbp)
    monkeypatch.setenv("MORALSTACK_OBSERVABILITY_MODE", "db_only")
    init_db(dbp)
    return dbp


def _usage_json(prompt: int, completion: int, total: int, source: str = "exact") -> str:
    return json.dumps(
        {
            "prompt_tokens": prompt,
            "completion_tokens": completion,
            "total_tokens": total,
            "source": source,
        }
    )


def test_sqlite_sink_write_llm_call_with_token_columns(tmp_path, monkeypatch):
    dbp = _setup(tmp_path, monkeypatch)
    create_run("run-1", run_type="test", meta={})
    upsert_request("run-1", "req-1", prompt="hi", domain="")
    sink = SqliteEventSink()
    sink.write_envelope(
        make_envelope(
            EVENT_LLM_CALL,
            run_id="run-1",
            request_id="req-1",
            payload={
                "phase": "deliberation",
                "module": "policy",
                "action": "generate",
                "prompt": "p",
                "raw_response": "{}",
                "token_usage_json": _usage_json(100, 50, 150),
            },
        )
    )
    conn = _get_connection(dbp)
    row = conn.execute(
        "SELECT input_tokens, output_tokens, total_tokens, token_usage_missing, token_usage_estimated "
        "FROM llm_calls WHERE run_id=? AND request_id=?",
        ("run-1", "req-1"),
    ).fetchone()
    conn.close()
    assert row["input_tokens"] == 100
    assert row["output_tokens"] == 50
    assert row["total_tokens"] == 150
    assert row["token_usage_missing"] == 0
    assert row["token_usage_estimated"] == 0


def test_sqlite_sink_write_llm_call_without_usage_leaves_columns_null(tmp_path, monkeypatch):
    dbp = _setup(tmp_path, monkeypatch)
    create_run("run-1", run_type="test", meta={})
    upsert_request("run-1", "req-1", prompt="hi", domain="")
    SqliteEventSink().write_envelope(
        make_envelope(
            EVENT_LLM_CALL,
            run_id="run-1",
            request_id="req-1",
            payload={
                "phase": "deliberation",
                "module": "policy",
                "action": "generate",
                "prompt": "p",
                "raw_response": "{}",
            },
        )
    )
    conn = _get_connection(dbp)
    row = conn.execute(
        "SELECT input_tokens, output_tokens, total_tokens, token_usage_json FROM llm_calls",
    ).fetchone()
    conn.close()
    assert row["input_tokens"] is None
    assert row["output_tokens"] is None
    assert row["total_tokens"] is None
    assert row["token_usage_json"] is None


def _emit_llm_call(usage_json: str | None) -> None:
    payload = {
        "phase": "deliberation",
        "module": "policy",
        "action": "generate",
        "prompt": "p",
        "raw_response": "{}",
    }
    if usage_json is not None:
        payload["token_usage_json"] = usage_json
    SqliteEventSink().write_envelope(make_envelope(EVENT_LLM_CALL, run_id="run-1", request_id="req-1", payload=payload))


def test_sqlite_sink_persists_cached_input_tokens(tmp_path, monkeypatch):
    dbp = _setup(tmp_path, monkeypatch)
    create_run("run-1", run_type="test", meta={})
    upsert_request("run-1", "req-1", prompt="hi", domain="")
    usage = json.dumps(
        {
            "prompt_tokens": 100,
            "completion_tokens": 50,
            "total_tokens": 150,
            "source": "exact",
            "cached_input_tokens": 64,
        }
    )
    _emit_llm_call(usage)
    conn = _get_connection(dbp)
    row = conn.execute("SELECT cached_input_tokens FROM llm_calls").fetchone()
    conn.close()
    assert row["cached_input_tokens"] == 64


def test_sqlite_sink_persists_measured_zero_cached_tokens(tmp_path, monkeypatch):
    """A measured cache miss (0) must not be stored as NULL/unknown."""
    dbp = _setup(tmp_path, monkeypatch)
    create_run("run-1", run_type="test", meta={})
    upsert_request("run-1", "req-1", prompt="hi", domain="")
    usage = json.dumps(
        {
            "prompt_tokens": 100,
            "completion_tokens": 50,
            "total_tokens": 150,
            "source": "exact",
            "cached_input_tokens": 0,
        }
    )
    _emit_llm_call(usage)
    conn = _get_connection(dbp)
    row = conn.execute("SELECT cached_input_tokens FROM llm_calls").fetchone()
    conn.close()
    assert row["cached_input_tokens"] == 0


def test_sqlite_sink_cached_tokens_null_when_provider_reported_nothing(tmp_path, monkeypatch):
    dbp = _setup(tmp_path, monkeypatch)
    create_run("run-1", run_type="test", meta={})
    upsert_request("run-1", "req-1", prompt="hi", domain="")
    _emit_llm_call(_usage_json(100, 50, 150))
    conn = _get_connection(dbp)
    row = conn.execute("SELECT cached_input_tokens FROM llm_calls").fetchone()
    conn.close()
    assert row["cached_input_tokens"] is None


def test_init_db_migration_idempotent_adds_new_llm_calls_columns(tmp_path, monkeypatch):
    dbp = _setup(tmp_path, monkeypatch)
    init_db(dbp)
    conn = _get_connection(dbp)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(llm_calls)").fetchall()}
    conn.close()
    for name in (
        "input_tokens",
        "output_tokens",
        "total_tokens",
        "token_usage_missing",
        "token_usage_estimated",
        "billable_provider_call",
        "cached_input_tokens",
    ):
        assert name in cols


def test_init_db_migration_on_preexisting_db_without_new_columns(tmp_path, monkeypatch):
    dbp = str(tmp_path / "legacy.db")
    monkeypatch.setenv("MORALSTACK_DB_PATH", dbp)
    monkeypatch.setenv("MORALSTACK_OBSERVABILITY_MODE", "db_only")
    conn = sqlite3.connect(dbp)
    conn.execute("""
        CREATE TABLE runs (run_id TEXT PRIMARY KEY, run_type TEXT, started_at INTEGER,
                           ended_at INTEGER, status TEXT, meta_json TEXT)
        """)
    conn.execute("""
        CREATE TABLE requests (run_id TEXT, request_id TEXT, prompt TEXT, domain TEXT,
                               created_at INTEGER, meta_json TEXT, final_response TEXT,
                               PRIMARY KEY (run_id, request_id))
        """)
    conn.execute("""
        CREATE TABLE llm_calls (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id TEXT, request_id TEXT, cycle INTEGER, phase TEXT, module TEXT,
            action TEXT, model TEXT, started_at INTEGER, duration_ms REAL,
            prompt TEXT, system_prompt TEXT, raw_response TEXT,
            parsed_json TEXT, parsed_summary_json TEXT, token_usage_json TEXT,
            attempts INTEGER, error TEXT
        )
        """)
    conn.commit()
    conn.close()
    init_db(dbp)
    conn = _get_connection(dbp)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(llm_calls)").fetchall()}
    conn.close()
    assert "input_tokens" in cols
    assert "billable_provider_call" in cols
    assert "cached_input_tokens" in cols


def test_new_index_idx_llm_calls_module_model_created(tmp_path, monkeypatch):
    dbp = _setup(tmp_path, monkeypatch)
    conn = _get_connection(dbp)
    rows = conn.execute("SELECT name FROM sqlite_master WHERE type='index' AND name='idx_llm_calls_module_model'").fetchall()
    conn.close()
    assert len(rows) == 1


def test_request_token_usage_table_created_with_fk_cascade(tmp_path, monkeypatch):
    dbp = _setup(tmp_path, monkeypatch)
    create_run("run-1", run_type="test", meta={})
    upsert_request("run-1", "req-1", prompt="hi", domain="")
    conn = _get_connection(dbp)
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute(
        """
        INSERT INTO request_token_usage (
            run_id, request_id, input_tokens, output_tokens, total_tokens,
            llm_call_count, missing_usage_count, estimated_usage_count,
            usage_may_be_incomplete, incomplete_reason, finalized_at
        ) VALUES (?, ?, 1, 2, 3, 1, 0, 0, 0, NULL, 1)
        """,
        ("run-1", "req-1"),
    )
    conn.commit()
    conn.close()


def test_insert_request_token_usage_is_idempotent(tmp_path, monkeypatch):
    dbp = _setup(tmp_path, monkeypatch)
    create_run("run-1", run_type="test", meta={})
    upsert_request("run-1", "req-1", prompt="hi", domain="")
    sink = SqliteEventSink()
    payload = {
        "input_tokens": 10,
        "output_tokens": 5,
        "total_tokens": 15,
        "llm_call_count": 1,
        "missing_usage_count": 0,
        "estimated_usage_count": 0,
    }
    for total in (15, 20):
        sink.write_envelope(
            make_envelope(
                EVENT_REQUEST_TOKEN_USAGE_FINALIZED,
                run_id="run-1",
                request_id="req-1",
                payload={**payload, "total_tokens": total},
            )
        )
    conn = _get_connection(dbp)
    row = conn.execute(
        "SELECT total_tokens FROM request_token_usage WHERE run_id=? AND request_id=?",
        ("run-1", "req-1"),
    ).fetchone()
    conn.close()
    assert row["total_tokens"] == 20


def test_dispatch_routes_request_token_usage_finalized_event(tmp_path, monkeypatch):
    dbp = _setup(tmp_path, monkeypatch)
    create_run("run-1", run_type="test", meta={})
    upsert_request("run-1", "req-1", prompt="hi", domain="")
    sink = SqliteEventSink()
    env = make_envelope(
        EVENT_REQUEST_TOKEN_USAGE_FINALIZED,
        run_id="run-1",
        request_id="req-1",
        payload={"input_tokens": 1, "output_tokens": 2, "total_tokens": 3, "llm_call_count": 1},
    )
    sink.write_envelope(env)
    sink.write_batch([env])
    conn = _get_connection(dbp)
    count = conn.execute("SELECT COUNT(*) AS c FROM request_token_usage").fetchone()["c"]
    conn.close()
    assert count >= 1


def test_fk_order_includes_request_token_usage():
    assert _FK_ORDER[EVENT_REQUEST_TOKEN_USAGE_FINALIZED] > _FK_ORDER[EVENT_PROXY_REQUEST_FINALIZED]
