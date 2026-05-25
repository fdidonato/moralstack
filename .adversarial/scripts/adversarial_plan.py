#!/usr/bin/env python
from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from build_baseline_manifest import snapshot_baseline  # noqa: E402
from build_context_pack import build_context_pack  # noqa: E402
from build_doc_digest import build_digest  # noqa: E402
from check_doc_code_drift import build_drift_report  # noqa: E402
from common import (  # noqa: E402
    AdversarialError,
    adversarial_root,
    command_exists,
    find_repo_root,
    git_output,
    load_json,
    load_model_json,
    now_run_id,
    read_text,
    run_command,
    safe_command_text,
    write_json,
    write_run_report,
    write_text,
)


def make_run_dir(adv_root: Path, task_path: Path, run_name: str | None) -> Path:
    name = run_name or task_path.stem
    run_id = now_run_id(name)
    run_dir = adv_root / "runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=False)
    return run_dir


def write_prompt(run_dir: Path, filename: str, prompt: str) -> Path:
    path = run_dir / "raw_prompts" / filename
    write_text(path, prompt)
    return path


def artifact_exists(path: Path, *, json_expected: bool = False) -> bool:
    if not path.exists() or path.stat().st_size <= 0:
        return False
    if json_expected:
        try:
            load_model_json(path)
        except Exception:
            return False
    return True


def skip_or_call(label: str, out_path: Path, callback, *, json_expected: bool = False) -> None:
    if artifact_exists(out_path, json_expected=json_expected):
        print(f"[resume] skipping {label}: {out_path.name} already exists")
        return
    callback()


def resolve_resume_run(repo_root: Path, resume_run: str) -> Path:
    run_dir = Path(resume_run)
    if not run_dir.is_absolute():
        run_dir = repo_root / run_dir
    run_dir = run_dir.resolve()
    if not run_dir.exists() or not run_dir.is_dir():
        raise AdversarialError(f"Resume run directory not found: {run_dir}")
    if not (run_dir / "00_task.md").exists():
        raise AdversarialError(f"Cannot resume {run_dir}: missing 00_task.md")
    return run_dir


def claude_base_command(config: dict[str, Any], role_model: str) -> list[str]:
    commands = config.get("commands", {})
    safety = config.get("safety", {})
    binary = commands.get("claude_binary", "claude")
    model = config.get("models", {}).get(role_model, "sonnet")
    cmd = [
        binary,
        "-p",
        "--model",
        model,
        "--permission-mode",
        safety.get("claude_permission_mode", "plan"),
    ]
    for tool in safety.get("disallowed_claude_tools", []):
        cmd.extend(["--disallowedTools", tool])
    cmd.extend(["--output-format", "text"])
    return cmd


def call_claude(
    repo_root: Path,
    run_dir: Path,
    config: dict[str, Any],
    role_model: str,
    prompt_name: str,
    prompt: str,
    out_path: Path,
    max_turns: int = 8,
) -> None:
    print(f"Calling Claude with prompt: {prompt_name} and model: {role_model}")

    prompt_file = write_prompt(run_dir, prompt_name, prompt)
    cmd = claude_base_command(config, role_model)
    cmd.extend(["--max-turns", str(max_turns)])
    mode = config.get("commands", {}).get("claude_prompt_mode", "stdin")
    timeout = int(config.get("limits", {}).get("max_command_seconds", 900))

    if mode == "stdin":
        result = run_command(cmd, cwd=repo_root, timeout=timeout, stdin_text=prompt, check=False)
    elif mode == "arg":
        result = run_command([*cmd, prompt], cwd=repo_root, timeout=timeout, check=False)
    elif mode == "file_arg":
        result = run_command([*cmd, str(prompt_file)], cwd=repo_root, timeout=timeout, check=False)
    else:
        raise AdversarialError(f"Unsupported claude_prompt_mode: {mode}")

    write_text(out_path.with_suffix(out_path.suffix + ".stdout.log"), result.stdout)
    write_text(out_path.with_suffix(out_path.suffix + ".stderr.log"), result.stderr)
    if result.returncode != 0:
        raise AdversarialError(
            f"Claude command failed for {prompt_name}: {safe_command_text(result.command)}\n\nSTDERR:\n{result.stderr}"
        )
    write_text(out_path, result.stdout)


def codex_base_command(config: dict[str, Any], role_model: str) -> list[str]:
    commands = config.get("commands", {})
    safety = config.get("safety", {})
    binary = commands.get("codex_binary", "codex")
    sandbox = safety.get("codex_sandbox", "read-only")
    cmd = [binary, "exec"]
    model = config.get("models", {}).get(role_model)
    model_flag = commands.get("codex_model_flag", "--model")
    if model and model_flag:
        cmd.extend([model_flag, model])
    cmd.extend(["--sandbox", sandbox])
    return cmd


def call_codex(
    repo_root: Path,
    run_dir: Path,
    config: dict[str, Any],
    role_model: str,
    prompt_name: str,
    prompt: str,
    out_path: Path,
    schema_path: Path | None = None,
) -> None:
    print(f"Calling Codex with prompt: {prompt_name} and model: {role_model}")
    prompt_file = write_prompt(run_dir, prompt_name, prompt)
    commands = config.get("commands", {})
    cmd = codex_base_command(config, role_model)
    output_flag = commands.get("codex_output_flag", "-o")
    schema_flag = commands.get("codex_schema_flag", "--output-schema")
    if schema_path is not None:
        cmd.extend([schema_flag, str(schema_path)])
    if output_flag:
        cmd.extend([output_flag, str(out_path)])

    mode = commands.get("codex_prompt_mode", "arg")
    timeout = int(config.get("limits", {}).get("max_command_seconds", 900))

    if mode == "stdin":
        result = run_command(cmd, cwd=repo_root, timeout=timeout, stdin_text=prompt, check=False)
    elif mode == "arg":
        result = run_command([*cmd, prompt], cwd=repo_root, timeout=timeout, check=False)
    elif mode == "file_arg":
        result = run_command([*cmd, str(prompt_file)], cwd=repo_root, timeout=timeout, check=False)
    else:
        raise AdversarialError(f"Unsupported codex_prompt_mode: {mode}")

    write_text(out_path.with_suffix(out_path.suffix + ".stdout.log"), result.stdout)
    write_text(out_path.with_suffix(out_path.suffix + ".stderr.log"), result.stderr)
    if result.returncode != 0:
        raise AdversarialError(
            f"Codex command failed for {prompt_name}: {safe_command_text(result.command)}\n\nSTDERR:\n{result.stderr}"
        )
    if not out_path.exists() or out_path.stat().st_size == 0:
        # Some local setups may not support -o. Fall back to stdout.
        write_text(out_path, result.stdout)


def make_common_input(task_text: str, context_pack: str) -> str:
    return "\n\n".join(
        [
            "# User Task",
            task_text,
            "# Documentation-Grounded Context Pack",
            context_pack,
        ]
    )


def final_gate_passed(gate: dict[str, Any], config: dict[str, Any]) -> bool:
    threshold = float(config.get("limits", {}).get("min_final_confidence", 0.82))
    return (
        gate.get("verdict") == "ACCEPT"
        and float(gate.get("confidence", 0)) >= threshold
        and gate.get("baseline_consistency") is True
        and gate.get("used_required_baseline_documents") is True
        and gate.get("drift_report_handled") is True
        and len(gate.get("unresolved_doc_code_conflicts", [])) == 0
        and len(gate.get("blocking_issues", [])) == 0
        and gate.get("has_sufficient_tests") is True
        and gate.get("has_rollback_strategy") is True
        and gate.get("is_implementable_by_fresh_agent") is True
    )


def preflight_tools(config: dict[str, Any], skip_tool_check: bool) -> None:
    if skip_tool_check:
        return
    missing = []
    for binary in [
        config.get("commands", {}).get("claude_binary", "claude"),
        config.get("commands", {}).get("codex_binary", "codex"),
    ]:
        if not command_exists(binary):
            missing.append(binary)
    if missing:
        raise AdversarialError(
            "Missing required CLI tools: "
            + ", ".join(missing)
            + "\nInstall/configure them or rerun with --dry-run to generate only baseline/context artifacts."
        )


def run_pipeline(args: argparse.Namespace) -> Path:
    repo_root = find_repo_root(Path(args.repo_root))
    adv = adversarial_root(repo_root)
    config_path = Path(args.config) if args.config else adv / "config.json"
    if not config_path.is_absolute():
        config_path = repo_root / config_path
    config = load_json(config_path)
    print(f"Using config from: {config_path}")

    manifest_path = Path(args.baseline) if args.baseline else adv / "baseline" / "manifest.json"
    if not manifest_path.is_absolute():
        manifest_path = repo_root / manifest_path
    print(f"Using baseline manifest from: {manifest_path}")

    if args.resume_run:
        run_dir = resolve_resume_run(repo_root, args.resume_run)
        task_path = run_dir / "00_task.md"
        print(f"Resuming existing run: {run_dir}")
    else:
        if not args.task:
            raise AdversarialError("--task is required unless --resume-run is provided.")
        task_path = Path(args.task)
        if not task_path.is_absolute():
            task_path = repo_root / task_path
        if not task_path.exists():
            raise AdversarialError(f"Task file not found: {task_path}")

        run_dir = make_run_dir(adv, task_path, args.run_name)
        write_text(run_dir / "00_task.md", read_text(task_path))
        write_json(
            run_dir / "00_run_metadata.json",
            {
                "run_dir": str(run_dir.relative_to(repo_root)),
                "task_file": (
                    str(task_path.relative_to(repo_root)) if task_path.is_relative_to(repo_root) else str(task_path)
                ),
                "git_branch": git_output(repo_root, ["branch", "--show-current"]),
                "git_commit": git_output(repo_root, ["rev-parse", "HEAD"]),
                "mode": config.get("mode"),
                "planning_only": config.get("safety", {}).get("planning_only", True),
            },
        )
        print(f"Run metadata saved to: {run_dir / '00_run_metadata.json'}")

    if not artifact_exists(run_dir / "01_baseline_manifest.json", json_expected=True):
        snapshot_baseline(repo_root, run_dir, manifest_path)
    else:
        print("[resume] skipping baseline snapshot: 01_baseline_manifest.json already exists")

    if not artifact_exists(run_dir / "03_baseline_digest.md"):
        build_digest(run_dir, run_dir / "00_task.md", config)
    else:
        print("[resume] skipping baseline digest: 03_baseline_digest.md already exists")

    if not artifact_exists(run_dir / "04_doc_code_drift_report.md"):
        build_drift_report(repo_root, run_dir, run_dir / "00_task.md", config)
    else:
        print("[resume] skipping drift report: 04_doc_code_drift_report.md already exists")

    if not artifact_exists(run_dir / "05_context_pack.md"):
        build_context_pack(repo_root, run_dir, run_dir / "00_task.md", config)
    else:
        print("[resume] skipping context pack: 05_context_pack.md already exists")

    if args.dry_run:
        write_run_report(
            run_dir / "run_report.md",
            "Run Report",
            [
                "Verdict: DRY_RUN_COMPLETED",
                "Baseline snapshot, digest, drift report and context pack were generated or already present.",
                "No Claude/Codex calls were executed.",
            ],
        )
        print(run_dir)
        return run_dir

    preflight_tools(config, args.skip_tool_check)
    print("Tools preflight completed successfully")

    prompts = adv / "prompts"
    schemas = adv / "schemas"
    shared_rules = read_text(prompts / "shared_rules.md")
    task_text = read_text(run_dir / "00_task.md")
    context_pack = read_text(run_dir / "05_context_pack.md")
    common_input = make_common_input(task_text, context_pack)

    claude_plan = run_dir / "06_claude_plan.md"
    codex_plan = run_dir / "07_codex_plan.md"
    codex_review = run_dir / "08_codex_reviews_claude.json"
    claude_review = run_dir / "09_claude_reviews_codex.md"
    synthesis_input_path = run_dir / "10_synthesis_input.md"
    final_candidate = run_dir / "11_final_plan_candidate.md"
    final_gate = run_dir / "12_codex_final_gate.json"
    final_plan = run_dir / "final_plan.md"

    print(f"Prompts and schemas loaded from: {prompts} and {schemas}")
    print("Common input generated successfully")

    skip_or_call(
        "Claude Plan A",
        claude_plan,
        lambda: call_claude(
            repo_root,
            run_dir,
            config,
            "claude_planner",
            "06_claude_plan.prompt.md",
            "\n\n".join([shared_rules, read_text(prompts / "01_planner_claude.md"), common_input]),
            claude_plan,
            max_turns=8,
        ),
    )

    skip_or_call(
        "Codex Plan B",
        codex_plan,
        lambda: call_codex(
            repo_root,
            run_dir,
            config,
            "codex_planner",
            "07_codex_plan.prompt.md",
            "\n\n".join([shared_rules, read_text(prompts / "02_planner_codex.md"), common_input]),
            codex_plan,
            schema_path=None,
        ),
    )

    skip_or_call(
        "Codex review of Claude Plan A",
        codex_review,
        lambda: call_codex(
            repo_root,
            run_dir,
            config,
            "codex_reviewer",
            "08_codex_reviews_claude.prompt.md",
            "\n\n".join(
                [
                    shared_rules,
                    read_text(prompts / "03_reviewer_codex.md"),
                    common_input,
                    "# Plan To Review: Claude Plan A",
                    read_text(claude_plan),
                ]
            ),
            codex_review,
            schema_path=schemas / "review.schema.json",
        ),
        json_expected=True,
    )

    skip_or_call(
        "Claude review of Codex Plan B",
        claude_review,
        lambda: call_claude(
            repo_root,
            run_dir,
            config,
            "claude_reviewer",
            "09_claude_reviews_codex.prompt.md",
            "\n\n".join(
                [
                    shared_rules,
                    read_text(prompts / "04_reviewer_claude.md"),
                    common_input,
                    "# Plan To Review: Codex Plan B",
                    read_text(codex_plan),
                ]
            ),
            claude_review,
            max_turns=8,
        ),
    )

    if not artifact_exists(final_candidate):
        synthesis_input = "\n\n".join(
            [
                shared_rules,
                read_text(prompts / "05_synthesizer.md"),
                common_input,
                "# Claude Plan A",
                read_text(claude_plan),
                "# Codex Plan B",
                read_text(codex_plan),
                "# Codex Review of Claude Plan A",
                read_text(codex_review),
                "# Claude Review of Codex Plan B",
                read_text(claude_review),
            ]
        )
        write_text(synthesis_input_path, synthesis_input)
        call_claude(
            repo_root,
            run_dir,
            config,
            "claude_synthesizer",
            "11_final_plan_candidate.prompt.md",
            synthesis_input,
            final_candidate,
            max_turns=10,
        )
    else:
        print("[resume] skipping final synthesis: 11_final_plan_candidate.md already exists")

    skip_or_call(
        "Codex final gate",
        final_gate,
        lambda: call_codex(
            repo_root,
            run_dir,
            config,
            "codex_final_gate",
            "12_codex_final_gate.prompt.md",
            "\n\n".join(
                [
                    shared_rules,
                    read_text(prompts / "06_final_gate_codex.md"),
                    common_input,
                    "# Final Plan Candidate",
                    read_text(final_candidate),
                ]
            ),
            final_gate,
            schema_path=schemas / "final_gate.schema.json",
        ),
        json_expected=True,
    )

    gate = load_model_json(final_gate)
    max_rounds = int(
        args.max_rounds if args.max_rounds is not None else config.get("limits", {}).get("max_review_rounds", 1)
    )
    round_index = 0
    while not final_gate_passed(gate, config) and round_index < max_rounds:
        round_index += 1
        revised_candidate = run_dir / f"11_final_plan_candidate_r{round_index}.md"
        revision_gate = run_dir / f"12_codex_final_gate_r{round_index}.json"

        if artifact_exists(revision_gate, json_expected=True):
            print(f"[resume] skipping revision round {round_index}: gate already exists")
            final_candidate = revised_candidate if artifact_exists(revised_candidate) else final_candidate
            final_gate = revision_gate
            gate = load_model_json(final_gate)
            continue

        revision_prompt = "\n\n".join(
            [
                shared_rules,
                read_text(prompts / "07_revision_synthesizer.md"),
                common_input,
                "# Previous Final Plan Candidate",
                read_text(final_candidate),
                "# Final Gate Blocking Issues",
                read_text(final_gate),
            ]
        )

        if not artifact_exists(revised_candidate):
            call_claude(
                repo_root,
                run_dir,
                config,
                "claude_synthesizer",
                f"11_final_plan_candidate_r{round_index}.prompt.md",
                revision_prompt,
                revised_candidate,
                max_turns=10,
            )
        else:
            print(f"[resume] skipping revision synthesis round {round_index}: {revised_candidate.name} already exists")

        final_candidate = revised_candidate
        final_gate = revision_gate
        call_codex(
            repo_root,
            run_dir,
            config,
            "codex_final_gate",
            f"12_codex_final_gate_r{round_index}.prompt.md",
            "\n\n".join(
                [
                    shared_rules,
                    read_text(prompts / "06_final_gate_codex.md"),
                    common_input,
                    "# Final Plan Candidate",
                    read_text(final_candidate),
                ]
            ),
            final_gate,
            schema_path=schemas / "final_gate.schema.json",
        )
        gate = load_model_json(final_gate)

    if final_gate_passed(gate, config):
        shutil.copyfile(final_candidate, final_plan)
        write_run_report(
            run_dir / "run_report.md",
            "Run Report",
            [
                "Verdict: ACCEPTED",
                f"Final plan: `{final_plan.name}`",
                f"Final gate: `{final_gate.name}`",
                f"Confidence: {gate.get('confidence')}",
            ],
        )
        print(f"ACCEPTED: {final_plan}")
    else:
        write_run_report(
            run_dir / "run_report.md",
            "Run Report",
            [
                "Verdict: FAILED_TO_CONVERGE",
                f"Latest final gate: `{final_gate.name}`",
                "Blocking issues remain. Do not implement this plan as approved.",
            ],
        )
        print(f"FAILED_TO_CONVERGE: {run_dir}")
        if not args.no_fail:
            raise SystemExit(2)

    return run_dir


def main() -> None:
    parser = argparse.ArgumentParser(description="Run documentation-grounded dual adversarial planning.")
    parser.add_argument(
        "--task", required=False, help="Path to the task markdown file. Required unless --resume-run is provided."
    )
    parser.add_argument("--repo-root", default=".", help="Repository root. Defaults to current directory.")
    parser.add_argument("--config", default=None, help="Path to .adversarial/config.json.")
    parser.add_argument("--baseline", default=None, help="Path to .adversarial/baseline/manifest.json.")
    parser.add_argument("--run-name", default=None, help="Optional run slug.")
    parser.add_argument(
        "--resume-run",
        default=None,
        help="Resume an existing .adversarial/runs/<run_id> directory without regenerating completed artifacts.",
    )
    parser.add_argument("--max-rounds", type=int, default=None, help="Maximum revision rounds after final gate failure.")
    parser.add_argument(
        "--dry-run", action="store_true", help="Generate baseline/context artifacts only; do not call Claude/Codex."
    )
    parser.add_argument("--skip-tool-check", action="store_true", help="Skip local CLI binary existence check.")
    parser.add_argument("--no-fail", action="store_true", help="Return zero even if final gate fails.")
    args = parser.parse_args()

    try:
        run_pipeline(args)
    except AdversarialError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
