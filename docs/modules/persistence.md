# moralstack.persistence (deprecated)

> **Deprecated.** This module is kept as a backwards-compatible alias.
> Use [`moralstack.observability`](observability.md) instead.

Importing from `moralstack.persistence` will emit a `DeprecationWarning` at runtime.

All symbols are re-exported from `moralstack.observability` and its sub-packages:

- Configuration → `moralstack.observability.config`
- Context vars → `moralstack.observability.context`
- SQLite writes + schema → `moralstack.observability.sinks.sqlite_sink`
- Read queries → `obs.read_store` (`moralstack.observability.read_store.SqliteReadStore`)
- Sync persist helpers → `moralstack.persistence.sink` *(thin wrappers; still functional)*
- Async persist helpers → `obs.emit(make_envelope(…))`

See [observability.md](observability.md) for the full documentation, env vars, and migration guide.
