# Persistence Module

## Overview

The persistence layer stores the full deliberative process in SQLite for audit, replay, and on-demand export. It is
configurable via environment variables and supports three modes: `db_only`, `dual`, and `file_only`.

## Configuration

| Variable                  | Description                           |
|---------------------------|---------------------------------------|
| `MORALSTACK_DB_PATH`      | Path to SQLite database file          |
| `MORALSTACK_PERSIST_MODE` | `db_only` \| `dual` \| `file_only`    |
| `MORALSTACK_UI_USERNAME`  | Basic Auth username for UI (optional) |
| `MORALSTACK_UI_PASSWORD`  | Basic Auth password for UI (optional) |

**Default**: If `MORALSTACK_DB_PATH` is set and `MORALSTACK_PERSIST_MODE` is not set, mode defaults to `db_only`.

- **db_only**: All data in DB; no file artifacts (reports, decision_trace.jsonl, debug.log)
- **dual**: DB + legacy file writes (transition mode)
- **file_only**: Legacy behavior; no DB writes

## Package Structure

| File                                | Responsibility                                                                         |
|-------------------------------------|----------------------------------------------------------------------------------------|
| `moralstack/persistence/config.py`  | Load env: DB_PATH, PERSIST_MODE, UI creds                                              |
| `moralstack/persistence/context.py` | Contextvars: run_id, request_id, cycle                                                 |
| `moralstack/persistence/db.py`      | SQLite schema, init_db, SqliteUnitOfWork, batch insert helpers                         |
| `moralstack/persistence/sink.py`    | Safe insert: persist_llm_call, persist_decision_trace, persist_debug_event, batch APIs |

## Schema

- **runs** — Run metadata (run_id, run_type, started_at, ended_at, status)
- **requests** — Per-request data (run_id, request_id, prompt, domain)
- **llm_calls** — Full LLM call log (prompt, system_prompt, raw_response, phase, module, cycle,
  **sequence_in_cycle**). The optional `sequence_in_cycle` (integer) encodes the logical order within a
  deliberation cycle (1=policy, 2=critic, 3=simulator, 4=perspectives, 5=hindsight, 6=refusal/finalize). It is used by
  the UI and report to display the Deliberation Journey in execution order (policy → critic/simulator/perspectives →
  hindsight) instead of completion order when modules run in parallel.
- **decision_traces** — Decision audit trail (stage, sequence, trace_json)
- **debug_events** — Debug payloads (location, message, data)
- **exports_cache** — Cached markdown exports (optional)

## Unit of Work and batching

- **SqliteUnitOfWork**: Context manager in `db.py` that holds a single connection for a logical unit of work. Use
  `with SqliteUnitOfWork() as uow:` (or `SqliteUnitOfWork(db_path=...)` to pass a path). On exit: commit on success,
  rollback on exception; the connection is always closed. When `get_persist_mode()` is `file_only` or `get_db_path()`
  is not set, `uow.conn` is `None` (no-op).
- **Optional `uow` on sink**: `persist_llm_call`, `persist_decision_trace`, and `persist_debug_event` accept an optional
  `uow: SqliteUnitOfWork | None = None`. If provided and `uow.conn` is not None, they use that connection and do not
  commit or close (the UoW commits on exit). Callers can thus group multiple writes in one transaction.
- **Batch APIs**: `persist_llm_calls_batch(entries, uow=None)`, `persist_decision_traces_batch(entries, uow=None)`,
  and `persist_debug_events_batch(entries, uow=None)` insert multiple rows in one go. Each entry is a dict with the
  same keys as the single-persist kwargs (context supplies run_id/request_id/cycle when missing). Low-level
  `insert_llm_calls_batch(conn, rows)`, `insert_decision_traces_batch(conn, rows)`, and `insert_debug_events_batch(conn,
  rows)` in `db.py` accept a connection and a list of tuples for direct use with an existing UoW.
- **Thread-safety**: Use one UoW per thread or per request; do not share the same UoW instance across threads.
  SQLite in WAL mode with one connection per UoW is safe for typical single-thread or per-request usage.

## Usage

- **Context**: `set_current_run_id`, `set_current_request_id`, `set_current_cycle` are set by CLI/benchmark/controller
  before processing.
- **Sink**: `persist_llm_call`, `persist_decision_trace`, `persist_debug_event` are called from deliberation_runner,
  decision_service, diagnostics, risk estimator. They do not raise; failures are logged. Pass `uow` when grouping
  writes in a single transaction.
- **Export**: `moralstack/reports/markdown_export.py` provides `export_request_markdown` and
  `export_run_benchmark_markdown` for on-demand report generation from DB. Single-request markdown is produced
  via `request_report_from_db` plus `render_request_report` (renderer_markdown).

## Web UI

The FastAPI dashboard (`moralstack-ui`) exposes runs and requests. After starting `moralstack-ui`, open *
*http://localhost:8765/** in your browser. You will be redirected to the login page; use `MORALSTACK_UI_USERNAME` and
`MORALSTACK_UI_PASSWORD` from `.env`. After login, the dashboard is at `/runs`.

## See Also

- [Orchestrator](./orchestrator.md) — Sets request context
- [Decision Explainability](./decision_explanation.md) — Trace structure
- [INSTALL.md](../../INSTALL.md) — UI optional dependencies
