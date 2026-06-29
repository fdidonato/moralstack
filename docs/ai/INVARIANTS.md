# Invariants (pointer)

MoralStack's load-bearing invariants already have an authoritative home. **This
file is a pointer**, not a second copy that could drift.

- **Headlines:** `PROJECT_SPEC.md` section 5 ("Critical MoralStack invariants").
- **Full text, path-scoped (auto-loaded when you open the relevant files):**
  `.claude/rules/`
  - `decision-policy.md` — decision/generation separation (P0).
  - `prompt-transparency.md` — system-prompt transparency; single-turn byte parity.
  - `hard-signal-safety.md` — hard-signal supremacy (P0).
  - `constitution-domains.md` — `core` is retrieval-only.
  - `observability.md` — telemetry never breaks the request.
  - `governed-delivery.md` — governed delivery only; fail closed.

Every agent and review template in this workflow references these. The rule when
a change appears to require breaking one: **stop and surface it to the user**
rather than working around it (PROJECT_SPEC §5). The Codex review templates
explicitly check a plan/diff against these invariants, and a governance change
that fails *open* is always treated as BLOCKING.
