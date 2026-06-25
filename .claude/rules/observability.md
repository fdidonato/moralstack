---
paths:
  - "moralstack/observability/**"
  - "moralstack/persistence/**"
---
# Invariant — Observability never breaks the request (P0)

Load-bearing. If a change appears to require breaking this, stop and surface it to
the user rather than working around it.

**Observability never breaks the request.** All telemetry is best-effort and wrapped in
swallowing try/except. Never let an audit/log failure change a governance decision or
raise into the caller.
