---
paths:
  - "moralstack/constitution/**"
  - "moralstack/orchestration/controller.py"
---
# Invariant — `core` is retrieval-only (P0)

Load-bearing. If a change appears to require breaking this, stop and surface it to
the user rather than working around it.

**`core` is retrieval-only.** The `core` constitution is never a runtime domain overlay
(`_normalize_runtime_domain` in `moralstack/orchestration/controller.py`).
