# Architecture map (pointer)

This repository already maintains a full architecture map. To avoid a duplicate
that would drift, **this file is a pointer**, not a copy.

Authoritative sources (read these, in order):

1. **`docs/MORALSTACK_CODEBASE_INDEX.md`** — module & file map, flows, invariants.
   The starting index for any subsystem.
2. **`docs/CODEBASE_FACTS.md`** — verified facts ledger + a "Hypotheses /
   Unverified assumptions" section.
3. **`docs/TRACES/`** — end-to-end traces:
   - `governance_decision_flow.md`
   - `openai_compatible_multiturn.md`
   - `observability_db_to_ui.md`
   - `complai_llm_rules_flow.md`
4. **`docs/architecture_spec.md`**, **`docs/decision_policy.md`**,
   **`docs/constitution.md`**, **`docs/multiturn_design.md`**,
   **`docs/modules/*.md`** — long-form designs.

The **codebase-cartographer** agent (`.claude/agents/codebase-cartographer.md`)
produces a per-task map from these sources at planning time. The index is a
snapshot — the code is always authoritative (PROJECT_SPEC §1).
