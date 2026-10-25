---
description: Hand the approved plan to a Claude Sonnet sub-agent for implementation; collect the diff
argument-hint: <path to approved ai/plans/plan.md>
allowed-tools: Read, Edit, Write, Grep, Glob, Bash, Agent
---

Approved plan: **$ARGUMENTS**.

Preconditions: the plan has been reviewed by the `adversarial-reviewer`
sub-agent and has **no unresolved BLOCKING items** (`/ai-review-plan` already
run). If not, stop and run the review first.

You are the **orchestrator** here — you prepare the handoff and verify the
result, but you do **not** write the feature code yourself. A separate Claude
**Sonnet** sub-agent (`claude-implementer`) is the implementer, running in an
isolated context so the plan→implement split stays honest (PROJECT_SPEC).

Steps:
1. Read the plan (`$ARGUMENTS`) and the matching plan review
   (`ai/reviews/plan-review-*.md`). If the review verdict is `BLOCK` or has
   unresolved BLOCKING items, stop and send it back to planning.
2. Write a complete handoff to `ai/handoffs/<slug>-handoff.md` (slug =
   `$ARGUMENTS` basename without extension), building on
   `ai/prompts/claude-implementation-template.md`. Fill in: context, objective,
   approved plan, **files allowed to modify**, **files NOT
   to modify**, invariants (PROJECT_SPEC §5 in play + how to keep them),
   checklist, required tests, acceptance criteria, risks, and the required output
   format.
   - **Self-contained:** copy the plan sections this slice needs **verbatim**
     (design steps, files, tests, acceptance criteria, the review findings folded
     into them) — never a paraphrase. Name the plan path plus the exact sections
     the handoff did not copy, so the implementer can consult the plan once if
     something is ambiguous instead of re-reading it in full.
   - **One coherent slice per handoff:** if the plan spans several independent
     steps (e.g. phases A1, A2a…), write one handoff per slice and run one
     implementer per slice. A slice is complete on its own: its tests, its docs,
     its verification.
3. Snapshot HEAD (`git rev-parse HEAD`) so you can detect any commit afterwards.
   Then launch the implementer sub-agent:
   `Agent(subagent_type: "claude-implementer", model: "sonnet", prompt: "Implement the handoff at ai/handoffs/<slug>-handoff.md. Read it in full first, implement only what it allows, and return your required-output report.")`.
   Save the sub-agent's returned report to
   `ai/handoffs/<slug>-implementation-report.md` for the audit trail.
   - **Worktree slices** (parallel implementers with `isolation: "worktree"`):
     the worktree is created from `origin/main`, not from the local HEAD
     (observed 2026-10-20), so unpushed local commits are missing from it. When
     HEAD is ahead of `origin/main`, start the prompt with the one authorized HEAD
     move: "if `git log -1 --format=%H` is not `<sha>`, run
     `git merge --ff-only <sha>`; no other HEAD move; if it fails, stop and
     report" (`<sha>` = the snapshot). Uncommitted state is never in the
     worktree, and `ai/**` is gitignored: pass the handoff by its absolute
     main-tree path; a slice that depends on uncommitted changes runs without
     `isolation`.
   - **No live background work:** once the report is saved, the implementer is
     done. If its notification says it "is waiting on its own background work",
     or its `Background commands` line is not "none", stop it with `TaskStop`
     (load it with ToolSearch `select:TaskStop`) and note it in the report —
     never wait on it or declare the step finished while it runs.
4. Collect the post-implementation diff to `ai/reviews/diff-after-<slug>-<ts>.md`
   via `scripts/ai/collect_git_diff.ps1 -OutPath <path>` (bash:
   `collect_git_diff.sh`; ts = `date +%Y%m%d-%H%M%S`). It never commits.
5. Verify only **allowed** files changed (`git status --short` + the saved diff).
   Flag loudly any do-not-modify file touched, any HEAD move (a commit — the
   sub-agent must not commit), or any out-of-scope change.
6. Report: files changed, deviations from plan, tests run + real results (from
   the sub-agent report), the handoff path, and the diff path.

**Fix passes** (after a diff review asks for changes): write
`ai/handoffs/<slug>-fix<N>-handoff.md` with the review findings to fix
**verbatim**, the files allowed, the files NOT to modify and the invariants
section copied verbatim from the original handoff, the original handoff and
implementation report paths, and the tests that must stay/become green; then
launch a **new**
`claude-implementer` on it. Resume the previous implementer (`SendMessage`)
only if its last activity is under 5 minutes old: sub-agent prompt cache lives
5 minutes, so a later resume re-writes its whole (often 200-400k) context, while
a fresh agent re-reads only what the fix needs.

Next step to tell the user: `/ai-review-diff $ARGUMENTS`.

You do **not** implement the feature code here — the `claude-implementer`
sub-agent does. Do not commit.
