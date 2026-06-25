# Operating rules for AI agents working in MoralStack

This file governs how any AI assistant (Claude or otherwise) must behave when
working in this repository. It is **operating discipline only** — architecture
lives in the documents linked at the bottom.

MoralStack is a *governance engine* for LLMs. Its decisions decide whether a
model is allowed to answer. Bugs here are not cosmetic: they change refusal
behavior, leak information, or corrupt audit trails used for AI Act compliance.
Treat every change as safety-relevant until proven otherwise.

---

## 1. Read before you write

- **Never edit a file you have not read in full** (or read the complete
  relevant region — large files like `moralstack/orchestration/controller.py`
  and `moralstack/ui/app.py` are >2000 lines and are paged by the Read tool;
  read every page that touches your change).
- Before changing behavior, read the **call sites** and the **tests** that
  exercise it. The `tests/` directory is large and behavior-locking — assume a
  test pins the behavior you are about to change.
- For any subsystem, start from the index: `docs/MORALSTACK_CODEBASE_INDEX.md`.
  Confirm the file/function still exists before relying on it — the index is a
  snapshot and the code is authoritative.

## 2. Audit before you patch

- Reproduce or precisely locate the problem first. Quote the exact file and
  line that causes it. Do not patch a symptom in a different layer than the
  cause.
- Trace the data path end to end before editing. The relevant traces are in
  `docs/TRACES/`. If your change touches governance routing, multi-turn, or
  observability, re-read the matching trace document.
- Identify every caller and every persisted side effect (DB rows, JSONL
  envelopes, emitted events) before changing a function signature or a payload
  shape.

## 3. Evidence discipline

- Every claim you make about how the code behaves must be backed by a file and
  function you actually inspected in this session. Cite as `path:line`.
- Do not generalize from a file name, a docstring, or a comment. Docstrings in
  this repo are detailed but can lag the code — verify against the
  implementation.
- When you run something, report what actually happened, not what you expected.

## 4. Facts vs. hypotheses (keep them separate)

- State **facts** only when you have read the supporting code. Everything else
  is a **hypothesis** and must be labelled as such.
- `docs/CODEBASE_FACTS.md` is the verified ledger. Anything not yet verified
  belongs in its "Hypotheses / Unverified assumptions" section, never in the
  facts table.
- If you discover that a documented fact is wrong, fix the document in the same
  change and note it (see §9).

## 5. Critical MoralStack invariants (do not break)

These are load-bearing. If a change appears to require breaking one, stop and
surface it to the user rather than working around it. Each invariant's **full
text** lives in a path-scoped rule under `.claude/rules/` that loads automatically
when you open the relevant files — the headline below is only the reminder.

1. **Decision/generation separation (P0).** `final_action ∈ {NORMAL_COMPLETE,
   SAFE_COMPLETE, REFUSE}` is computed from structured signals, **never inferred
   from response text or disclaimers**. → `.claude/rules/decision-policy.md`
2. **System-prompt transparency.** Governance never mutates the developer-declared
   system prompt. → `.claude/rules/prompt-transparency.md`
3. **Hard-signal supremacy (P0).** Hard topical signals (self-harm, child safety,
   weapons, physical harm) are never overridable by a developer contract, a domain
   overlay, or a cached ledger decision. → `.claude/rules/hard-signal-safety.md`
4. **Single-turn prompt transparency.** No `developer_contract` + no
   `conversation_history` ⇒ byte-identical prompt/system composition to the
   single-turn baseline. → `.claude/rules/prompt-transparency.md`
5. **`core` is retrieval-only.** The `core` constitution is never a runtime domain
   overlay. → `.claude/rules/constitution-domains.md`
6. **Observability never breaks the request.** Telemetry is best-effort, wrapped in
   swallowing try/except. → `.claude/rules/observability.md`
7. **Governed delivery only (Plan 1).** Every user-visible answer is produced inside
   the governed pipeline; the wrapped/upstream client never generates a delivered
   answer, and a pipeline failure fails closed to a governed refusal.
   → `.claude/rules/governed-delivery.md`

## 6. No broad refactoring unless explicitly requested

- Make the smallest change that fixes the task. Do not rename, reorganize, or
  "tidy" adjacent code.
- Do not introduce abstractions for hypothetical future needs.
- The codebase uses mixed Italian/English in older comments and docstrings.
  Do **not** mass-translate or reformat. New comments/docs must be English
  (per `.cursor/rules/`), but leave existing text alone unless it is in scope.

## 7. Testing expectations

- Run the relevant subset for any change, and the full suite before declaring a
  task done: `python -m pytest` (or a scoped `python -m pytest tests/test_<area>.py`).
  Tests are offline/mocked; keep new tests deterministic.
- Do **not** weaken or delete a test to make a change pass. If a test must change,
  justify why in the PR/commit message (see §8).
- Full detail — the behavior-locking test map (byte-equality, governance invariants,
  decision policy, observability, proxy/correlation, ledger) — lives in
  `.claude/rules/testing.md` (loads automatically when you open `tests/**`).

## 8. Documentation update expectations

When you change behavior, update the docs in the **same** change:
`docs/MORALSTACK_CODEBASE_INDEX.md` (module/flow/invariant), `docs/CODEBASE_FACTS.md`
(verified/disproven facts), `docs/TRACES/` (governance, multi-turn, observability,
COMPL-AI), `docs/modules/*.md` (module contract). Full mapping →
`.claude/rules/docs-maintenance.md` (loads when you open `docs/**`). A `Stop` hook
gates this: editing behavior code without touching the matching docs blocks the turn.

## 9. Error-correction protocol

- If you made a wrong edit, **revert or correct it explicitly** and say so.
  Do not silently layer a second fix on top.
- If you find a defect outside your task scope, note it (and add it to the
  hypotheses section of `docs/CODEBASE_FACTS.md` if unverified) rather than
  fixing it without being asked.
- If a documented statement contradicts the code, the **code wins**. Correct
  the document and flag the discrepancy in your summary.
- Never use destructive shortcuts to make an obstacle disappear (no
  `--no-verify`, no deleting failing tests, no force-push). Find the root cause.
  These are enforced by `.claude/hooks/guard_dangerous_git.py` (a `PreToolUse`
  hook that blocks the command outright), not just by this guidance.

## 10. Expected response format for future sessions

When working a task in this repo, structure your reply so a reviewer can audit
it cold:

1. **Goal** — one line: what you were asked to do.
2. **Evidence** — the specific files/functions you read, cited `path:line`,
   and what they told you. Separate **facts** (verified) from **hypotheses**.
3. **Change** — what you edited and why, smallest-diff first. Note any
   invariant from §5 that the change touches and how it stays intact.
4. **Verification** — exact tests/commands run and their real outcome. If you
   could not verify something (e.g. no API key, no UI), say so explicitly.
5. **Docs** — which of the §8 documents you updated.
6. **Open questions / risks** — anything unverified, plus follow-ups.

Keep it terse. Do not claim success you did not observe.

---

## Reference documents

- `docs/MORALSTACK_CODEBASE_INDEX.md` — architecture & file map.
- `docs/CODEBASE_FACTS.md` — verified facts ledger + hypotheses.
- `docs/TRACES/governance_decision_flow.md` — end-to-end decision flow.
- `docs/TRACES/openai_compatible_multiturn.md` — OpenAI-compatible bridge & multi-turn.
- `docs/TRACES/observability_db_to_ui.md` — logging → DB/JSONL → UI.
- `docs/TRACES/complai_llm_rules_flow.md` — COMPL-AI / llm_rules benchmark path & risks.
- Existing long-form docs: `docs/architecture_spec.md`, `docs/decision_policy.md`,
  `docs/constitution.md`, `docs/multiturn_design.md`, `docs/modules/*.md`.
