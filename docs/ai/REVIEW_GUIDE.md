# Review guide — the `adversarial-reviewer` sub-agent

The `adversarial-reviewer` sub-agent (`.claude/agents/adversarial-reviewer.md`)
is the **independent reviewer** of plans (before implementation) and diffs
(after). It is **read-only** by construction — it inspects the repo but cannot
modify, commit, or push — and it runs in an **isolated context** on a model
**different from the implementer's**. The value of the review is that
isolation: the reviewer did not see the plan being written and did not write
the code.

## Invocation mechanism (verified on Claude Code 2.1.272, 2026-09-15)

- `/ai-review-plan` and `/ai-review-diff` (`.claude/commands/`) run **inline**
  in the main conversation to compose the review request and save the
  artifacts, then launch the reviewer through the `Agent` tool:

      Agent(subagent_type: "adversarial-reviewer", description: "Review <plan|diff> <slug>", prompt: "<review request>")

  The tool is named `Agent` (renamed from `Task` in Claude Code 2.1.63 per the
  sub-agent docs, code.claude.com/docs/en/sub-agents); every `allowed-tools:`
  line under `.claude/commands/` and the tracked `settings*.json` allowlists say
  `Agent`. In the 2.1.272 binary a `Task` entry still resolves to `Agent` through
  a legacy alias table (`{Task:"Agent", KillShell:"TaskStop", ...}`) applied when
  tool-name rules are parsed, so it is not broken today — but it is not the
  canonical name and the alias is compatibility, not a contract.
- The commands never pass `model:` to the `Agent` call. The reviewer's model is
  pinned in the agent's own frontmatter (`model: claude-fable-5-1`, preferred;
  `opus` is the documented fallback if that ID stops resolving). Overriding it
  per call would let the review run on the implementer's model.
- The reviewer's first output line is `Reviewer model: <id>`, quoted from its
  own system prompt. The commands check it: missing, or equal to the
  implementer's model (`claude-implementer` runs on `sonnet`), voids the
  independence claim and must be reported.
- We hand the reviewer the same rubric we always did
  (`ai/prompts/plan-review-template.md` / `ai/prompts/diff-review-template.md`,
  the MoralStack invariant framing, and the `docs/ai/REVIEW_POLICY.md`
  taxonomy) as the request text. The reviewer reads the plan / diff / handoff
  files itself — and, for a diff, the changed files in the working tree — so
  the commands do not paste their content into the prompt, and the reviewer
  must not trust any summary the prompt offers.
- The command saves the reviewer's verbatim response to
  `ai/reviews/{plan,diff}-review-<slug>-<timestamp>.md` and the exact composed
  request to `ai/prompts/generated-{plan,diff}-review-<slug>-<timestamp>.md`
  (the request is saved **before** the launch, so a failed run still leaves
  its prompt behind). Both locations are gitignored (`ai/**`,
  `ai/prompts/generated-*.md`); only the templates and the `.gitkeep` files are
  tracked.

- A reviewer file created or renamed mid-session is not necessarily spawnable
  through `Agent` right away (observed 2026-09-15: `Agent type
  'adversarial-reviewer' not found` minutes after creating the file; the type
  appeared later in the same session). The headless path loads the definition
  from disk on every run (verified: the first review of this harness change ran
  through it and reported `Reviewer model: claude-fable-5-1`) and is the
  out-of-process fallback the commands point to when the launch fails:

      claude -p --agent adversarial-reviewer --output-format json < ai/prompts/generated-<kind>-review-<slug>-<ts>.md

  Its JSON result carries `modelUsage` keyed by the model ID that actually ran —
  a second, tool-independent witness of the reviewer model.

## Read-only: one structural layer, one instructional

- Frontmatter: `tools: Read, Grep, Glob, Bash` — no `Edit`, no `Write`. File
  edits through the file tools are impossible, not merely forbidden.
- Body: `Bash` is write-capable, so it is constrained by **instruction**:
  reading only (`git diff/show/log/status`, `rg`, `ls`, running
  `python -m pytest ...` for evidence); any mutating command —
  `git add/commit/checkout/stash/reset/restore/mv/rm`, `pre-commit run` (it
  auto-fixes files), redirections, `sed -i` — is forbidden, and a check that
  would need a write must be raised as a `QUESTION` instead. The only
  mechanical guards on that path are the repo's `PreToolUse` hooks
  (`guard_dangerous_git.py`, `guard_secrets.py`); they do not block `rm` or
  `sed -i`. This is weaker than the sandbox the Codex path had — accepted
  because a review leaves a checkable trace: `git status` before and after it
  must match.

## What the reviewer must check

See the templates and `docs/ai/REVIEW_POLICY.md`. In short: wrong assumptions,
blast radius, missing files/tests, regressions, architecture/coupling, security,
performance — and, for this repo specifically, each MoralStack invariant
(`docs/ai/INVARIANTS.md`, full text under `.claude/rules/`), with the outcome
stated per invariant. A governance change that fails **open** is BLOCKING.
Findings are classified `BLOCKING` / `NON_BLOCKING` / `SUGGESTION` /
`QUESTION`; the verdict is `APPROVE` / `APPROVE_WITH_CHANGES` / `BLOCK`.

## If the sub-agent cannot be launched

`/ai-review-plan` / `/ai-review-diff` must say so and stop — never fabricate a
review, and never fall back to the orchestrator's own judgment presented as the
reviewer's. The orchestrator reviewing its own plan is exactly the failure the
isolation exists to prevent.
