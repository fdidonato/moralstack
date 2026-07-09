You are the **MoralStack Implementer** (`claude-implementer`, Claude Sonnet, isolated sub-agent context).
This is a SMALL FOLLOW-UP FIX PASS on an already-implemented, Codex-reviewed change. Apply ONLY the fixes
listed here. No scope creep.

Rules — non-negotiable:
- Implement ONLY the fixes below. Modify ONLY the allowed files. Do NOT touch do-not-modify files.
- Do NOT weaken/skip/xfail/delete tests. Do NOT change `response_format` (stays `{"type":"json_object"}`),
  retry loops, parsers, or any governance control flow.
- Honor PROJECT_SPEC §5 invariants. Run the verification commands and report REAL output.
- Do NOT git add/commit/push. If a fix is ambiguous or you hit a blocker, STOP and report.

## Context
The Part-A prompt-caching reorder (plan `ai/plans/prompt-caching-strict-json.md`, implemented in the prior
pass) passed Codex diff review with verdict **APPROVE_WITH_CHANGES** — no blockers. Codex found one real
content-preservation drift plus two test gaps plus two nits. This pass closes them. Full context: the diff
review is `ai/reviews/codex-diff-review-prompt-caching-strict-json-20260706-125903.md`.

## Fixes to apply

### REQUIRED 1 — Restore Hindsight batch base framing (content-preservation regression)
Before the reorder, the batch hindsight path sent `HINDSIGHT_SYSTEM_PROMPT` (the base role framing) as its
system message. After the reorder, the batch path sends `HINDSIGHT_BATCH_SYSTEM_PROMPT`
(`moralstack/prompts/hindsight_prompt.py:27`) which starts directly with the batch rubric and DROPPED the base
framing — the text: `"You are a hindsight evaluator for an AI safety system. ... Be rigorous and objective in
your assessments. Always respond with valid JSON only. No additional text or explanation outside the JSON."`
(`moralstack/runtime/modules/hindsight_module.py:310-314`). The single path preserved it by prepending
(`HINDSIGHT_SINGLE_SYSTEM_PROMPT = HINDSIGHT_SYSTEM_PROMPT + "..."`, `hindsight_module.py:323`).
**Fix:** make `HINDSIGHT_BATCH_SYSTEM_PROMPT` prepend the same `HINDSIGHT_SYSTEM_PROMPT` base framing (mirror
the single path), keeping the `"evaluations"`-rooted schema intact. Import/compose so the base text is present
exactly once (verify the batch USER prompt `build_hindsight_prompt` does NOT already contain that base text —
no duplication). The batch and single system prompts must remain DISTINCT (batch has the `"evaluations"` root,
single has the root-object schema) — do not collapse them.

### REQUIRED 2 — Hindsight batch content-preservation test
Add a test (in `tests/test_static_prefix_stability.py`) that captures the batch path's `concat(system, user)`
and asserts the base `HINDSIGHT_SYSTEM_PROMPT` framing is present — specifically the literal substrings
`"You are a hindsight evaluator for an AI safety system."` AND `"Be rigorous and objective in your assessments."`
AND `"Always respond with valid JSON only."`. This test must FAIL against the current (pre-fix) code and PASS
after REQUIRED 1. Keep the existing batch/single schema-separation assertions intact.

### REQUIRED 3 — Pin the critic quick-check as a LITERAL byte snapshot
`tests/test_static_prefix_stability.py:300-317` currently asserts `call.system == CRITIC_SYSTEM_PROMPT` (the
LIVE constant) — this would not catch an in-place edit to the quick-check's sent bytes. Strengthen it: pin the
EXPECTED quick-check `system` AND `prompt` as literal string snapshots captured from today's actual sent bytes
(the `_CapturingPolicy` already records `call.system`/`call.prompt`). Assert both equal the pinned literals,
that `call.system` contains NO `"decision"`/`"violations"` full-critique schema, and that the USER `prompt`
still carries the `{"violated"}` contract. Do not remove the existing hard-violation-still-fails test
(`test_quick_check_hard_violation_still_fails`, `:319`).

### SUGGESTED 4 — Fix stale perspectives docstring
`moralstack/prompts/perspectives_prompt.py:1-6` still says `OPT-2: Shared system prompt (REQUEST+RESPONSE+common
instructions)...`. That contradicts the new A5a split (static system / dynamic user). Update the module
docstring to describe the current design: static ctx-independent system prompt + per-perspective user prompt
carrying REQUEST/RESPONSE/risk context. Docstring text only — no code change.

### SUGGESTED 5 — Observability split assertions
Add assertions (in `tests/test_static_prefix_stability.py` or a small integration test) that the runner-persisted
`prompt`/`system_prompt` fields reflect the new split: the persisted `system_prompt` carries the moved static
prefix actually sent and `prompt` is dynamic-only, for critic/simulator/hindsight (batch + single/individual)/
perspectives — the runner reads these at `moralstack/orchestration/deliberation_runner.py:2904` (critic),
`:3017` (simulator), `:3131` (hindsight), `:3239` (perspectives). Read that file (do NOT edit it) to see the
exact field names. If wiring a full runner cycle is disproportionate, assert at the module-result level
(`CriticReport.system_prompt`, hindsight `HindsightResult.system_prompt`, etc.) that the value equals the
path-specific constant actually sent — the same guarantee, cheaper.

## Files ALLOWED to modify
- `moralstack/prompts/hindsight_prompt.py` (REQUIRED 1)
- `moralstack/prompts/perspectives_prompt.py` (SUGGESTED 4 — docstring only)
- `tests/test_static_prefix_stability.py` (REQUIRED 2, 3; SUGGESTED 5)
- (optional) a new `tests/test_*.py` for SUGGESTED 5 if you prefer a separate integration test file.

## Files NOT to modify (do-not-touch)
- Everything else. In particular: `moralstack/runtime/modules/hindsight_module.py` should NOT need changes —
  REQUIRED 1 is achieved by composing `HINDSIGHT_BATCH_SYSTEM_PROMPT` in `hindsight_prompt.py`; if you believe
  a `hindsight_module.py` change is required, STOP and report why rather than editing it.
- `moralstack/models/policy.py`, `base.py`, `utils/structured_output.py`, `utils/llm_parse_contract.py`,
  `compliance/dccl.py`, `constitution/retriever.py`, `orchestration/deliberation_runner.py` (read-only).
- Protected tests: `tests/test_llm_parse_contract.py`, `tests/test_system_prompt_byte_equality.py`,
  `tests/test_runtime_modules_retry_token_accounting.py`, the `*_config_loader` tests. Any `response_format`
  value, any retry/parser logic.

## Invariants (PROJECT_SPEC §5)
- #3 hard-signal supremacy: REQUIRED 1 restores (does not remove) safety-relevant framing; the batch/single
  schema separation must stay intact (no contract collision). Quick-check must still return `passed=False` on
  a hard violation.
- #6 observability: SUGGESTED 5 strengthens audit-trail fidelity.
- #1/#2/#4: untouched (no policy.py, no response_format, no decision-from-text).

## Verification commands (run and report REAL output)
```
.\venv\Scripts\python.exe -m pytest tests/test_static_prefix_stability.py -v
.\venv\Scripts\python.exe -m pytest tests/test_llm_parse_contract.py tests/test_system_prompt_byte_equality.py tests/test_runtime_modules_retry_token_accounting.py -v
.\venv\Scripts\python.exe -m pytest
.\venv\Scripts\python.exe -m pre_commit run --files <the files you changed>
```
(Per memory `precommit-head-drift`: scope `pre-commit` to your changed files if `-a` churns unrelated files.)

## Acceptance criteria
- Batch hindsight `concat(system,user)` again contains the base `HINDSIGHT_SYSTEM_PROMPT` framing (REQUIRED 2
  test passes; would have failed before REQUIRED 1). Batch/single schemas still distinct.
- Quick-check test pins literal system+user snapshots; hard-violation-still-fails intact.
- Perspectives docstring matches the new design.
- Observability split asserted. `response_format` unchanged everywhere. Full `pytest` green; `pre-commit` clean.
- Do-not-modify files untouched; HEAD not moved (no commit).

## Required output
files modified; tests added/changed; commands run; REAL results; deviations; residual blockers.
