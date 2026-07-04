---
description: Collect the current diff and have Codex review it against the approved plan
argument-hint: <path to approved ai/plans/plan.md>
allowed-tools: Bash, Read, Write, Grep, Glob, Skill
---

Approved plan the diff must satisfy: **$ARGUMENTS**.

This uses the official OpenAI Codex Claude Code plugin's own `codex:rescue`
entry point (`github.com/openai/codex-plugin-cc`) — not a hand-rolled script.
If it is not installed, tell the user to run `/plugin install codex@openai-codex`
and stop; do not fabricate a review.

Steps:
1. Collect the current diff via `scripts/ai/collect_git_diff.ps1` (bash:
   `collect_git_diff.sh`) unless one was already collected by the Cursor
   implementation step — reuse that file instead of collecting again. This is
   a plain git-diff snapshot helper, not a Codex call.
2. Read `ai/prompts/codex-diff-review-template.md` (the review rubric and
   required output structure), `$ARGUMENTS` (the approved plan), and the
   matching Cursor handoff at `ai/handoffs/<slug>-cursor-cli-handoff.md` if it
   exists.
3. Compose the review request: the template's content verbatim, followed by:
   - "This is a READ-ONLY review. Do not modify, create, or delete any file."
   - The diff file path, the plan path, and the handoff path, with instructions
     to read all three itself before judging (do not paste large content
     inline — Codex has read access to this repo).
   - Repo-root framing: this is the MoralStack governance engine; verify the
     diff does not break the invariants in `PROJECT_SPEC.md` section 5 /
     `.claude/rules/` (decision/generation separation, hard-signal supremacy,
     prompt transparency, governed delivery, observability best-effort). A
     change that makes governance fail **open** is always BLOCKING.
4. Invoke `Skill(skill: "codex:rescue", args: "--wait --fresh <composed request>")`.
   - `--wait` runs it synchronously; `--fresh` skips the resumable-thread
     prompt since this is a one-shot review.
   - Do not add `--write` — the read-only phrasing above is what keeps the
     Codex sandbox read-only per the plugin's own `codex-cli-runtime` skill.
   - Return exactly what that Skill call produces; do not paraphrase it away.
5. Save Codex's verbatim response to
   `ai/reviews/codex-diff-review-<slug>-<timestamp>.md` and the exact composed
   request to `ai/prompts/generated-codex-diff-review-<slug>-<timestamp>.md`
   (slug = `$ARGUMENTS` basename without extension; timestamp = `date +%Y%m%d-%H%M%S`).
6. Present the verdict (`APPROVE` / `APPROVE_WITH_CHANGES` / `BLOCK`) and
   classify findings **BLOCKING / NON_BLOCKING / SUGGESTION** per
   `docs/ai/REVIEW_POLICY.md`. Surface deviations from the plan explicitly.
7. If there are BLOCKING items: state that implementation must be fixed (back
   to `/ai-implement-with-cursor` with an updated handoff) before finalize.

Next step to tell the user: `/ai-finalize $ARGUMENTS`.

Do not modify code here. Do not commit.
