---
description: Collect the current diff and have the independent adversarial-reviewer sub-agent review it against the approved plan
argument-hint: <path to approved ai/plans/plan.md>
allowed-tools: Bash, Read, Write, Grep, Glob, Agent
---

Approved plan the diff must satisfy: **$ARGUMENTS**.

The reviewer is the **adversarial-reviewer** sub-agent
(`.claude/agents/adversarial-reviewer.md`): read-only tools, a model pinned in
its own frontmatter that differs from the implementer's, and a fresh isolated
context — it did not write this code. Do not review the diff yourself and
present it as the sub-agent's. If the sub-agent cannot be launched (e.g. `Agent
type 'adversarial-reviewer' not found`), use the headless path in
`docs/ai/REVIEW_GUIDE.md` (`claude -p --agent adversarial-reviewer ...`) with the
same saved request, verbatim save and `Reviewer model:` check — otherwise say so
and stop; never fabricate a review.

Steps:
1. Collect the current diff via `scripts/ai/collect_git_diff.ps1` (bash:
   `collect_git_diff.sh`) unless one was already collected by the implementation
   step — reuse that file instead of collecting again, unless `git status` has
   changed since it was collected (then re-collect: a stale snapshot makes the
   reviewer judge code that is not what gets committed). This is a plain
   git-diff snapshot helper, not a review call.
2. Read `ai/prompts/diff-review-template.md` (the review rubric and required
   output structure), `$ARGUMENTS` (the approved plan), and the matching
   handoff at `ai/handoffs/<slug>-handoff.md` if it exists.
3. Compose the review request: the template's content verbatim, followed by:
   - "This is a READ-ONLY review. Do not modify, create, or delete any file."
   - The diff file path, the plan path, and the handoff path, with instructions
     to read all three itself before judging (do not paste large content
     inline — the sub-agent has read access to this repo, and must also read
     the changed files in the working tree, not only the diff).
   - Repo-root framing: this is the MoralStack governance engine; verify the
     diff does not break the invariants in `PROJECT_SPEC.md` section 5 /
     `.claude/rules/` (decision/generation separation, hard-signal supremacy,
     prompt transparency, governed delivery, observability best-effort). A
     change that makes governance fail **open** is always BLOCKING.
4. Save the exact composed request to
   `ai/prompts/generated-diff-review-<slug>-<timestamp>.md` (slug =
   `$ARGUMENTS` basename without extension; timestamp = `date +%Y%m%d-%H%M%S`)
   **before** launching, so the prompt stays traceable even if the run fails.
   Then invoke the reviewer:
   `Agent(subagent_type: "adversarial-reviewer", description: "Review diff <slug>", prompt: "<composed request>")`.
   - Do **not** pass `model:` — the agent definition pins the reviewer model;
     overriding it here would collapse the model separation.
   - Wait for it to complete and return exactly what it produces; do not
     paraphrase it away.
5. Save the sub-agent's verbatim response to
   `ai/reviews/diff-review-<slug>-<timestamp>.md` (same slug and timestamp as
   the prompt file). Its first line must be `Reviewer model: <id>`; if it is
   missing, or names the same model as the implementer, say so loudly — the
   independence claim is void.
6. Present the verdict (`APPROVE` / `APPROVE_WITH_CHANGES` / `BLOCK`) and
   classify findings **BLOCKING / NON_BLOCKING / SUGGESTION / QUESTION** per
   `docs/ai/REVIEW_POLICY.md`. Surface deviations from the plan explicitly.
7. If there are BLOCKING items: state that implementation must be fixed (back
   to `/ai-implement` with an updated handoff) before finalize.

Next step to tell the user: `/ai-finalize $ARGUMENTS`.

Do not modify code here. Do not commit.
