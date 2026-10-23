"""F1 (docs/traces casing hygiene) and F5(b) (testing-rule wording) checks.

These tests read the *real* repository (via the ``repo_root`` fixture), not a
throwaway ``project`` fixture — a check against a fake project would pass
vacuously and miss exactly the class of bug F1 was.
"""

from __future__ import annotations

import importlib.util
import os
import re
import subprocess
from pathlib import Path
from types import ModuleType

import pytest

_DOC_REF_RE = re.compile(r"`(docs/[A-Za-z0-9_./-]*)`")


def _load_module(path: Path, name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader, f"cannot load {path}"
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _case_correct_relative(base: Path, rel: str) -> bool:
    """True iff every component of ``rel`` matches the on-disk casing exactly,
    walked from the (assumed case-correct) ``base``. ``Path.exists()`` alone is
    NOT sufficient here — it is precisely how F1 (``docs/TRACES/`` vs the real
    ``docs/traces/``) shipped unnoticed on a case-insensitive filesystem."""
    cur = base
    for part in Path(rel).parts:
        try:
            listing = os.listdir(cur)
        except OSError:
            return False
        if part not in listing:
            return False
        cur = cur / part
    return True


# Documented alternative mechanism (not used here): ``p.resolve(strict=True)``
# then compare the resolved string's casing to the declared one — verified this
# session to also return the true on-disk casing on this NTFS box, and to
# distinguish "missing" from "wrong case" via the ``strict=True`` OSError.


def test_docs_targets_exist_on_disk_case_sensitive(repo_root):
    stop_gate = _load_module(repo_root / ".claude" / "hooks" / "stop_gate.py", "_docs_check_stop_gate")
    check_memory = _load_module(repo_root / "scripts" / "check_memory_updated.py", "_docs_check_memory_updated")

    files: set[str] = set(stop_gate._DOCS_ALWAYS)
    for _, hints in stop_gate._DOCS_HINTS:
        files.update(hints)
    dirs: set[str] = set()
    for prefix in stop_gate.MEMORY_DOC_PREFIXES:
        if prefix.endswith("/"):
            dirs.add(prefix.rstrip("/"))
        else:
            files.add(prefix)
    for _, docs in check_memory.BEHAVIOR_DOC_MAP:
        files.update(docs)

    bad: list[str] = []
    for rel in sorted(files):
        target = repo_root / rel
        if not target.is_file() or not _case_correct_relative(repo_root, rel):
            bad.append(rel)
    for rel in sorted(dirs):
        target = repo_root / rel
        if not target.is_dir() or not _case_correct_relative(repo_root, rel):
            bad.append(rel)

    assert not bad, f"doc targets missing or wrong-case on disk: {bad}"


def test_harness_docs_reference_existing_paths_case_sensitively(repo_root):
    targets = [
        repo_root / ".claude" / "hooks" / "README.md",
        repo_root / "PROJECT_SPEC.md",
        repo_root / "CLAUDE.md",
        repo_root / "docs" / "ai" / "ARCHITECTURE_MAP.md",
    ]
    targets.extend(sorted((repo_root / ".claude" / "rules").glob("*.md")))
    targets.extend(sorted((repo_root / ".claude" / "agents").glob("*.md")))

    bad: list[tuple[str, str]] = []
    for doc in targets:
        text = doc.read_text(encoding="utf-8")
        for match in _DOC_REF_RE.finditer(text):
            ref = match.group(1)
            if "*" in ref or "<" in ref or ">" in ref:
                continue
            target = repo_root / ref
            if not target.exists() or not _case_correct_relative(repo_root, ref):
                bad.append((str(doc.relative_to(repo_root)).replace("\\", "/"), ref))

    assert not bad, f"stale/wrong-case docs/ references: {bad}"


def test_no_stale_uppercase_docs_traces_references(repo_root):
    """No tracked file outside CHANGELOG.md (historical entries) spells the
    uppercase ``docs/TRACES`` casing. The needle is built at runtime — never
    spelled as one literal in this file — so this test itself, once staged,
    does not trip on its own text (review round 1, BLOCKING 2)."""
    needle = "docs/" + "traces".upper()
    proc = subprocess.run(
        [
            "git",
            "grep",
            "-lI",
            needle,
            "--",
            ".",
            ":!CHANGELOG.md",
            ":!tests/harness/test_harness_docs.py",
        ],
        cwd=str(repo_root),
        capture_output=True,
        text=True,
    )
    if proc.returncode not in (0, 1):
        pytest.skip(f"git grep unavailable/errored: {proc.stderr.strip()}")
    assert proc.returncode == 1, f"stale uppercase references found:\n{proc.stdout}"


def test_testing_rule_points_to_manual_slow_run(repo_root):
    text = (repo_root / ".claude" / "rules" / "testing.md").read_text(encoding="utf-8")
    assert "-m slow" in text
    assert "CI runs" not in text
    assert not (repo_root / ".github" / "workflows").exists()


_FULL_SUITE_TIMEOUT_FILES = [
    ".claude/agents/pre-commit-verifier.md",
    ".claude/agents/claude-implementer.md",
    ".claude/agents/final-integrator.md",
    ".claude/agents/test-strategist.md",
    ".claude/commands/ai-finalize.md",
    ".claude/rules/testing.md",
    ".claude/skills/improve-moralstack-ui/SKILL.md",
    "PROJECT_SPEC.md",
]


@pytest.mark.parametrize("rel", _FULL_SUITE_TIMEOUT_FILES)
def test_full_suite_instructions_carry_bash_timeout(repo_root, rel):
    """S3: every instruction that launches the full suite names the Bash tool timeout
    (the default 120 s kills a 3-5 min run and pushes it into background polling)."""
    text = (repo_root / rel).read_text(encoding="utf-8")
    assert "timeout: 600000" in text, rel


@pytest.mark.parametrize("rel", [r for r in _FULL_SUITE_TIMEOUT_FILES if "SKILL" not in r])
def test_full_suite_instructions_point_to_suite_record(repo_root, rel):
    text = (repo_root / rel).read_text(encoding="utf-8")
    assert "suite_record.py" in text, rel


def test_reviewer_is_read_only_on_the_suite_record(repo_root):
    text = (repo_root / ".claude" / "agents" / "adversarial-reviewer.md").read_text(encoding="utf-8")
    assert "suite_record.py check" in text
    assert "Never run the full suite" in text
    assert "suite_record.py run" not in text


def test_project_spec_keeps_the_no_weakening_rule_and_hooks_readme_lists_the_marker(repo_root):
    assert "Do **not** weaken or delete a test" in (repo_root / "PROJECT_SPEC.md").read_text(encoding="utf-8")
    readme = (repo_root / ".claude" / "hooks" / "README.md").read_text(encoding="utf-8")
    row = next(line for line in readme.splitlines() if ".suite-result.json" in line)
    assert "never values" in row
    assert "file-mode" in row


def test_testing_rule_leads_with_suite_record_and_documents_slow_and_mode_residual(repo_root):
    text = (repo_root / ".claude" / "rules" / "testing.md").read_text(encoding="utf-8")
    first_bullet = text.split("\n- ", 2)[1]
    assert "suite_record.py" in first_bullet and "timeout: 600000" in first_bullet
    assert "ONE record" in text
    assert "file-mode" in text
    assert "large directory selection" in text


def _frontmatter(text: str) -> dict[str, str]:
    head = text.split("---", 2)[1]
    return dict(line.split(":", 1) for line in head.strip().splitlines() if ":" in line and not line.startswith(" "))


def test_reviewer_pins_effort_in_frontmatter(repo_root):
    """E5: the reviewer's effort is pinned, not inherited from the global setting.
    Claude Code silently ignores unknown frontmatter keys, so the key name itself is
    locked here."""
    text = (repo_root / ".claude" / "agents" / "adversarial-reviewer.md").read_text(encoding="utf-8")
    fm = {k.strip(): v.strip() for k, v in _frontmatter(text).items()}
    assert fm.get("effort") == "xhigh"
    assert fm.get("model") == "claude-fable-5-1"


def _flat(text: str) -> str:
    return " ".join(text.split())


@pytest.mark.parametrize("rel", [".claude/commands/ai-review-plan.md", ".claude/commands/ai-review-diff.md"])
def test_review_commands_lower_effort_only_on_a_checked_lean_lane(repo_root, rel):
    text = _flat((repo_root / rel).read_text(encoding="utf-8"))
    assert 'lean** → pass `effort: "high"` only if the plan says `Lane: lean` **and** every' in text
    assert "on the lean allowlist" in text
    assert "any path off the allowlist" in text or "any file off the allowlist" in text
    assert "→ pass nothing, the frontmatter's `xhigh` applies" in text
    assert "never drop or downgrade a blocker" in text.lower()


_LEAN_ALLOWLIST = {
    ".claude/agents/**",
    ".claude/commands/**",
    ".claude/hooks/**",
    "scripts/ai/**",
    "scripts/setup_env.ps1",
    "tests/harness/**",
    "docs/ai/**",
    "ai/prompts/*-template.md",
    "CHANGELOG.md",
}


def test_lean_allowlist_is_locked_and_excludes_governance_paths(repo_root):
    """The lean lane lowers the reviewer's effort and skips mapping/test design, so its
    allowlist is locked here: widening it must be a deliberate, reviewed test change."""
    workflow = (repo_root / "docs" / "ai" / "AGENTIC_WORKFLOW.md").read_text(encoding="utf-8")
    para = workflow.split("**Lean allowlist**", 1)[1].split("\n\n", 1)[0]
    allowed, excluded = para.split("Anything else", 1)
    listed = set(re.findall(r"`([^`]+)`", allowed))
    assert listed == _LEAN_ALLOWLIST
    assert "`.claude/hooks/**` except the guards" in allowed
    for entry in listed:
        assert not entry.startswith(("moralstack", ".claude/rules", "examples")), entry
        assert entry == "tests/harness/**" or not entry.startswith("tests/"), entry
        static = entry.split("*", 1)[0].rstrip("/")
        assert static and (repo_root / static).exists(), entry
    assert "scripts/**" not in listed and "docs/**" not in listed and ".claude/**" not in listed
    # Harness enforcement and secret/release handling stay full (guards, hook wiring, commit gates, skills).
    for full in (
        ".claude/hooks/guard_*.py",
        ".claude/settings*.json",
        ".claude/skills/**",
        "scripts/check_*_updated.py",
    ):
        assert f"`{full}`" in excluded, full
    for guard in (".claude/hooks/guard_dangerous_git.py", ".claude/hooks/guard_secrets.py"):
        assert (repo_root / guard).is_file(), guard


def test_lane_rules_keep_every_gate_and_force_full_on_governance(repo_root):
    plan_cmd = _flat((repo_root / ".claude" / "commands" / "ai-plan.md").read_text(encoding="utf-8"))
    assert "only if **every** file the change touches is on the lean allowlist" in plan_cmd
    assert "always when the change touches `moralstack/**`, any other `tests/**`" in plan_cmd
    assert "When in doubt, full." in plan_cmd
    workflow = _flat((repo_root / "docs" / "ai" / "AGENTIC_WORKFLOW.md").read_text(encoding="utf-8"))
    assert "Both lanes keep every quality gate" in workflow
    assert "The lane is checked, not trusted" in workflow
    for rel in ("ai/prompts/plan-review-template.md", "ai/prompts/diff-review-template.md"):
        assert "is BLOCKING" in _flat((repo_root / rel).read_text(encoding="utf-8")).split("a wrong lane", 1)[1], rel


def test_direct_lane_keeps_the_diff_review_and_forces_plan_on_governance(repo_root):
    """The direct lane drops the planner and the implementer, never the independent diff
    review or the verifier gate, and never applies to governance code or invariant text."""
    workflow = (repo_root / "docs" / "ai" / "AGENTIC_WORKFLOW.md").read_text(encoding="utf-8")
    section = _flat(workflow.split("### Direct lane", 1)[1].split("\n## ", 1)[0])
    never = section.split("**Never direct**", 1)[1].split("**Steps**", 1)[0]
    for path in (
        "`moralstack/**`",
        "`tests/harness/**`",
        "`.claude/rules/**`",
        "`PROJECT_SPEC.md`",
        "`CLAUDE.md`",
        "`.claude/hooks/guard_*.py`",
        "`.claude/settings*.json`",
        "`scripts/check_*_updated.py`",
    ):
        assert path in never, path
    assert "When in doubt, `/ai-plan`." in never
    assert "`/ai-review-diff ai/plans/<slug>.md`" in section
    assert "pre-commit-verifier gate" in section
    assert "never skipped" in section
    assert "is BLOCKING" in section
    review_cmd = _flat((repo_root / ".claude" / "commands" / "ai-review-diff.md").read_text(encoding="utf-8"))
    assert "the plan says `Lane: direct`" in review_cmd
    template = _flat((repo_root / "ai" / "prompts" / "diff-review-template.md").read_text(encoding="utf-8"))
    assert "`Lane: direct`" in template and "never-direct path" in template


def test_parallel_verifier_edits_force_a_re_review(repo_root):
    text = _flat((repo_root / ".claude" / "commands" / "ai-review-diff.md").read_text(encoding="utf-8"))
    assert "re-collect the diff and re-run this review on the delta before finalize" in text


def test_finalize_runs_the_verifier_in_both_lanes(repo_root):
    text = _flat((repo_root / ".claude" / "commands" / "ai-finalize.md").read_text(encoding="utf-8"))
    assert "ensure verification actually happened, in both lanes" in text


def test_implementer_context_economy_keeps_the_evidence_rules(repo_root):
    """E2 must not erode PROJECT_SPEC §1/§10: the full-region read and the failure
    traceback stay mandatory next to the context-economy guidance."""
    text = _flat((repo_root / ".claude" / "agents" / "claude-implementer.md").read_text(encoding="utf-8"))
    assert "Read a file in full (or the complete relevant region) before editing it" in text
    assert "never hide a failure" in text
    assert "re-run the failing node(s) with the full traceback" in text


def test_cartographer_uses_lsp_and_the_plugin_is_enabled(repo_root):
    """The cartographer's callers come from LSP `incomingCalls`; without `LSP` + `ToolSearch`
    in its tools (LSP is a deferred tool) or with the plugin disabled it silently falls back
    to Grep — measured: Haiku skips LSP unless loading it is step 0."""
    import json

    settings = json.loads((repo_root / ".claude" / "settings.json").read_text(encoding="utf-8"))
    assert settings["enabledPlugins"]["pyright-lsp@claude-plugins-official"] is True
    text = (repo_root / ".claude" / "agents" / "codebase-cartographer.md").read_text(encoding="utf-8")
    tools = {t.strip() for t in _frontmatter(text)["tools"].split(",")}
    assert {"LSP", "ToolSearch", "Read", "Grep", "Glob"} <= tools
    assert {"Edit", "Write"}.isdisjoint(tools)
    assert _frontmatter(text)["model"].strip() == "haiku"  # Sonnet: same recall at ~17x cost (A/B 2026-10-22)
    for section in (
        "## Reading list",
        "## Persisted / emitted side effects",
        "## Invariants in play",
        "## Open questions for the planner",
    ):
        assert section in text, section
    body = _flat(text)
    assert "Your **first** tool call is ToolSearch with query `select:LSP`" in body
    assert "**`incomingCalls`**" in body
    assert "**Bash** only for read-only `git`" in body
    assert "Run `python scripts/ai/impact_for_paths.py <every production file on the reading list>`" in body
    assert "**regression surface**" in body
    assert "`Impact command: python scripts/ai/impact_for_paths.py <paths>`" in body
    plan_cmd = _flat((repo_root / ".claude" / "commands" / "ai-plan.md").read_text(encoding="utf-8"))
    assert "save its full output verbatim to `ai/evidence/<slug>-impact.md`" in plan_cmd
    assert "Bash(python scripts/ai/impact_for_paths.py:*)" in settings["permissions"]["allow"]
    assert "Executable not found in $PATH" in (repo_root / "docs" / "ai" / "AGENTIC_WORKFLOW.md").read_text(encoding="utf-8")
