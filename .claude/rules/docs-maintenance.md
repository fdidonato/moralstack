---
paths:
  - "docs/**"
---
# Documentation update expectations

When you change behavior, update the docs in the **same** change:

- New/changed module, flow, or invariant → update `docs/MORALSTACK_CODEBASE_INDEX.md`.
- New verified fact, or a fact you proved wrong → update `docs/CODEBASE_FACTS.md` (and
  move items out of the hypotheses section as you verify them).
- Changed governance flow, multi-turn handling, observability schema, or the COMPL-AI
  bridge path → update the matching file in `docs/TRACES/`.
- Module-level behavior also has long-form docs in `docs/modules/*.md`; update the
  relevant one if you touch that module's contract.

When the Stop docs-gate blocks, it stages `.claude/.docs-stub.md` with the symbols you
touched (parsed from `git diff HEAD`) mapped to the docs above. Treat it as a starting
point, not an authority: verify each symbol against the code, update the real docs, then
delete the stub.
