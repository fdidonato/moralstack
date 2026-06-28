# moralstack.persistence (deprecated)

> **Deprecated.** This module is kept as a backwards-compatible alias.
> Use [`moralstack.observability`](observability.md) instead.

Importing from `moralstack.persistence` will emit a `DeprecationWarning` at runtime.

All symbols are re-exported from `moralstack.observability` and its sub-packages:

- Configuration → `moralstack.observability.config`
- Context vars → `moralstack.observability.context`
- SQLite writes + schema → `moralstack.observability.sinks.sqlite_sink`
- Read queries → `obs.read_store` (`moralstack.observability.read_store.SqliteReadStore`)
- High-frequency persist helpers → `moralstack.persistence.sink` *(thin
  wrappers; still functional; they enqueue through `get_obs().emit*()`)*
- Async emit helpers → `obs.emit(make_envelope(...))` / `obs.emit_batch([...])`

Lifecycle helpers such as `create_run`, `upsert_request`,
`update_request_response`, and `update_request_domain` remain synchronous because
they anchor SQLite foreign keys and final responses. Decision-audit finalization
is also synchronous via `finalize_audit_sync`; ordinary `persist_*` telemetry is
best-effort queued and callers that immediately read the DB must call
`obs.flush()`.

See [observability.md](observability.md) for the full documentation, env vars, and migration guide.
