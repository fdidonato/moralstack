# Trusted Baseline Policy

The adversarial documentation baseline is treated as the primary source for architectural intent, verified facts, invariants, known risks, and module maps.

The current codebase is treated as the primary source for current runtime behavior, exact file paths, current symbols, tests, and implementation state.

If documentation and code disagree, the issue must be marked as `DOC_CODE_CONFLICT` or `[DRIFT]`. A final plan must not silently choose one side.

Every important claim in plans, reviews, and synthesis should be tagged as one of:

- `[DOC]` — derived from trusted adversarial documentation.
- `[CODE]` — verified in the current repository.
- `[TEST]` — supported by existing tests or executable validation commands.
- `[DRIFT]` — derived from a documentation/code mismatch.
- `[ASSUMPTION]` — not yet verified and must be checked before implementation.

A final plan is acceptable only if it uses the baseline, handles drift, preserves documented invariants, identifies files and tests, includes rollback, and states required documentation updates.
