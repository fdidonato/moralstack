---
paths:
  - "moralstack/compliance/**"
  - "moralstack/orchestration/path_router.py"
---
# Invariant — Hard-signal supremacy (P0)

Load-bearing. If a change appears to require breaking this, stop and surface it to
the user rather than working around it.

**Hard-signal supremacy (P0).** Hard topical signals (self-harm, child safety,
weapons, physical harm) must not be overridable by a developer contract, a domain
overlay, or a cached ledger decision. See `path_router.is_hard_signal_refuse` and the
DCCL Safety Override (`moralstack/compliance/safety_override.py`).
