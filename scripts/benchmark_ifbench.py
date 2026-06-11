#!/usr/bin/env python3
"""Re-test the IFBench cases MoralStack failed, fully independent of compl-ai.

For each failed case (scripts/ifbench_failed_cases.json: verbatim IFBench
prompt + instruction_id_list + kwargs) this script:
  1. sends the prompt SINGLE-TURN both to pure OpenAI gpt-4o and to the
     MoralStack OpenAI-compatible proxy (POST {base_url}/chat/completions);
  2. reads choices[0].message.content from each response;
  3. scores each text with the vendored IFBench checkers in
     scripts/ifbench_checkers.py (strict + loose) -- the same deterministic
     pass/fail logic COMPL-AI applies, but with NO dependency on the `complai`
     package or its evaluation environment.

It does NOT run the COMPL-AI / inspect_ai suite. The COMPL-AI cases serve only
as a test reference.

Prerequisite: the production proxy is running, for example:
    python examples/server_quickstart.py
(default base_url http://localhost:8787/v1), or:
    uvicorn examples.server_quickstart:app --host 0.0.0.0 --port 8080
(use --moralstack-base-url http://localhost:8080/v1).

Usage:
    python scripts/benchmark_ifbench.py
    python scripts/benchmark_ifbench.py --ids 104,74,91,126,131
    python scripts/benchmark_ifbench.py --group primary
    python scripts/benchmark_ifbench.py --moralstack-base-url http://localhost:8787/v1
    python scripts/benchmark_ifbench.py --output investigation/ifbench_rerun.md
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
WORKSPACE_ROOT = SCRIPT_DIR.parent
DATA_FILE = SCRIPT_DIR / "ifbench_failed_cases.json"

# =============================================================================
# Benchmark defaults
# =============================================================================

DEFAULT_OPENAI_BASE_URL = "https://api.openai.com/v1"
DEFAULT_OPENAI_MODEL = "gpt-4o"
DEFAULT_OPENAI_MAX_TOKENS = 4096

DEFAULT_MORALSTACK_BASE_URL = "http://localhost:8787/v1"
DEFAULT_MORALSTACK_MODEL = "governed"
DEFAULT_MORALSTACK_MAX_TOKENS = 4096
DEFAULT_MORALSTACK_TEMPERATURE = 1.0
DEFAULT_MORALSTACK_TOP_P = 1.0

DEFAULT_TIMEOUT_SECONDS = 180.0
DEFAULT_OUTPUT = "investigation/ifbench_failed_rerun_claude.md"

sys.path.insert(0, str(WORKSPACE_ROOT))
sys.path.insert(0, str(SCRIPT_DIR))
import ifbench_checkers as ic  # isort: skip  # noqa: E402  (vendored, self-contained)
from moralstack.utils.env_loader import load_env  # isort: skip  # noqa: E402

# =============================================================================
# Chat client (single-turn, like tests/e2e_run_regression.py)
# =============================================================================


@dataclass
class ClientConfig:
    name: str
    base_url: str
    model: str
    api_key: str
    max_tokens: int | None
    temperature: float | None
    top_p: float | None
    timeout: float


def call_chat_completion(prompt: str, cfg: ClientConfig) -> dict[str, Any]:
    url = cfg.base_url.rstrip("/") + "/chat/completions"
    payload: dict[str, Any] = {
        "model": cfg.model,
        "messages": [{"role": "user", "content": prompt}],
    }
    if cfg.max_tokens is not None:
        payload["max_tokens"] = cfg.max_tokens
    if cfg.temperature is not None:
        payload["temperature"] = cfg.temperature
    if cfg.top_p is not None:
        payload["top_p"] = cfg.top_p

    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {cfg.api_key}",
        },
    )
    with urllib.request.urlopen(req, timeout=cfg.timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _openai_api_key_config_error(api_key: str) -> str | None:
    key = (api_key or "").strip()
    if not key:
        return "missing API key"
    if key == "sk-noauth":
        return "`sk-noauth` is a local proxy placeholder, not a valid OpenAI API key"
    return None


def _format_http_error(exc: urllib.error.HTTPError, cfg: ClientConfig) -> str:
    detail = ""
    try:
        raw = exc.read().decode("utf-8", errors="replace")
        parsed = json.loads(raw)
        message = (parsed.get("error") or {}).get("message")
        if message:
            detail = f": {message}"
    except Exception:
        detail = ""

    if cfg.name == "openai" and exc.code == 401:
        return (
            "openai authentication failed (HTTP 401). Set IFBENCH_OPENAI_API_KEY "
            "or pass --openai-api-key with a valid OpenAI API key"
            f"{detail}"
        )
    return f"{cfg.name} HTTP {exc.code} {exc.reason}{detail}"


def extract_content(body: dict[str, Any]) -> str:
    choices = body.get("choices") or []
    if not choices:
        return ""
    return (choices[0].get("message") or {}).get("content") or ""


# =============================================================================
# Runner
# =============================================================================


@dataclass
class CaseResult:
    client: str
    case_id: str
    instruction_id_list: list[str]
    group: str
    note: str
    response: str = ""
    strict_pass: bool = False
    loose_pass: bool = False
    strict_per_instruction: list[bool] = field(default_factory=list)
    loose_per_instruction: list[bool] = field(default_factory=list)
    final_action: str = ""
    path: str = ""
    error: str = ""


def load_cases() -> dict[str, dict[str, Any]]:
    return json.loads(DATA_FILE.read_text(encoding="utf-8"))["cases"]


def select_cases(
    cases: dict[str, dict[str, Any]],
    ids: list[str] | None,
    group: str,
) -> list[tuple[str, dict[str, Any]]]:
    items = sorted(cases.items(), key=lambda kv: int(kv[0]))
    if ids:
        wanted = set(ids)
        items = [(cid, c) for cid, c in items if cid in wanted]
        missing = wanted - {cid for cid, _ in items}
        if missing:
            print(f"  [warn] unknown ids ignored: {sorted(missing)}", file=sys.stderr)
    if group != "all":
        items = [(cid, c) for cid, c in items if c.get("group") == group]
    return items


def run(args: argparse.Namespace) -> int:
    cases = load_cases()
    selected = select_cases(cases, args.ids, args.group)
    if not selected:
        print("No cases selected.", file=sys.stderr)
        return 2

    # Sanity: every selected instruction type must be implemented in the vendored checkers.
    unsupported = {iid for _, c in selected for iid in c["instruction_id_list"] if iid not in ic.SUPPORTED_INSTRUCTIONS}
    if unsupported:
        print(f"  [warn] no vendored checker for: {sorted(unsupported)}", file=sys.stderr)

    client_configs = [
        ClientConfig(
            name="openai",
            base_url=args.openai_base_url,
            model=args.openai_model,
            api_key=args.openai_api_key,
            max_tokens=args.openai_max_tokens,
            temperature=None,
            top_p=None,
            timeout=args.timeout,
        ),
        ClientConfig(
            name="moralstack",
            base_url=args.moralstack_base_url,
            model=args.moralstack_model,
            api_key=args.moralstack_api_key,
            max_tokens=args.moralstack_max_tokens,
            temperature=args.moralstack_temperature,
            top_p=args.moralstack_top_p,
            timeout=args.timeout,
        ),
    ]

    openai_key_error = _openai_api_key_config_error(args.openai_api_key)
    if openai_key_error:
        print(
            "OpenAI pure baseline is not configured: "
            f"{openai_key_error}. Set IFBENCH_OPENAI_API_KEY or pass --openai-api-key.",
            file=sys.stderr,
        )
        return 2

    print(
        f"Re-testing {len(selected)} failed IFBench case(s) against "
        f"{', '.join(f'{cfg.name}:{cfg.base_url} model={cfg.model}' for cfg in client_configs)}; "
        f"emoji backend={ic.EMOJI_BACKEND}\n"
    )

    results: list[CaseResult] = []
    for cid, case in selected:
        for cfg in client_configs:
            res = CaseResult(
                client=cfg.name,
                case_id=cid,
                instruction_id_list=case["instruction_id_list"],
                group=case.get("group", ""),
                note=case.get("note", ""),
            )
            try:
                body = call_chat_completion(case["prompt"], cfg)
                res.response = extract_content(body)
                meta = body.get("moralstack_metadata") or {}
                res.final_action = str(meta.get("final_action", ""))
                res.path = str(meta.get("path", ""))

                res.strict_per_instruction, res.strict_pass = ic.test_instruction_following_strict(
                    case["instruction_id_list"], case["kwargs"], res.response
                )
                res.loose_per_instruction, res.loose_pass = ic.test_instruction_following_loose(
                    case["instruction_id_list"], case["kwargs"], res.response
                )
            except urllib.error.HTTPError as exc:
                res.error = _format_http_error(exc, cfg)
            except urllib.error.URLError as exc:
                res.error = f"{cfg.name} unreachable: {exc}"
            except Exception as exc:  # noqa: BLE001 - report any per-case failure
                res.error = f"{type(exc).__name__}: {exc}"

            results.append(res)
            _print_case_line(res)

    _print_summary(results)
    if args.output:
        out_path = Path(args.output)
        if not out_path.is_absolute():
            out_path = WORKSPACE_ROOT / out_path
        _write_reports(results, client_configs, out_path)
        print(f"\nReport: {out_path}")
        print(f"Raw JSON: {out_path.with_suffix('.json')}")
    return 0


def _status(res: CaseResult) -> str:
    if res.error:
        return "ERROR"
    if res.strict_pass:
        return "STRICT_PASS"
    if res.loose_pass:
        return "LOOSE_ONLY"
    return "FAIL"


def _print_case_line(res: CaseResult) -> None:
    instr = ",".join(res.instruction_id_list)
    prefix = f"  [{res.case_id:>3}] {res.client:<10} {instr:<34}"
    if res.error:
        print(f"{prefix} ERROR: {res.error}")
        return
    extra = f" action={res.final_action}" if res.final_action else ""
    print(
        f"{prefix} "
        f"strict={'PASS' if res.strict_pass else 'fail'} "
        f"loose={'PASS' if res.loose_pass else 'fail'}{extra}"
    )


def _print_summary(results: list[CaseResult]) -> None:
    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)

    for client in _client_names(results):
        client_results = [r for r in results if r.client == client]
        total = len(client_results)
        errors = sum(1 for r in client_results if r.error)
        scored = total - errors
        strict = sum(1 for r in client_results if r.strict_pass)
        loose = sum(1 for r in client_results if r.loose_pass)
        recovered = [r.case_id for r in client_results if r.strict_pass]
        loose_only = [r.case_id for r in client_results if r.loose_pass and not r.strict_pass]

        print(f"  client         : {client}")
        print(f"  cases tested   : {total}")
        if errors:
            print(f"  errors         : {errors}")
        print(f"  strict pass    : {strict}/{scored}")
        print(f"  loose pass     : {loose}/{scored}")
        if recovered:
            print(f"  strict-pass ids: {', '.join(recovered)}")
        if loose_only:
            print(f"  loose-only ids : {', '.join(loose_only)}")
        print()

    by_case = _results_by_case(results)
    openai_pass_moralstack_fail = [
        case_id
        for case_id, per_client in by_case.items()
        if per_client.get("openai")
        and per_client.get("moralstack")
        and per_client["openai"].strict_pass
        and not per_client["moralstack"].strict_pass
    ]
    if openai_pass_moralstack_fail:
        print("  openai strict pass / moralstack strict fail ids: " + ", ".join(openai_pass_moralstack_fail))


def _client_names(results: list[CaseResult]) -> list[str]:
    return list(dict.fromkeys(r.client for r in results))


def _results_by_case(results: list[CaseResult]) -> dict[str, dict[str, CaseResult]]:
    grouped: dict[str, dict[str, CaseResult]] = {}
    for result in results:
        grouped.setdefault(result.case_id, {})[result.client] = result
    return grouped


def _write_reports(results: list[CaseResult], configs: list[ClientConfig], out_path: Path) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().isoformat(timespec="seconds")
    by_case = _results_by_case(results)

    lines = [
        "# IFBench failed-case re-test (OpenAI vs MoralStack)\n",
        f"Date: {ts}\n",
        "Clients:\n",
    ]
    for cfg in configs:
        lines.append(
            f"- `{cfg.name}`: base_url=`{cfg.base_url}` model=`{cfg.model}` "
            f"max_tokens={cfg.max_tokens} temperature={cfg.temperature} top_p={cfg.top_p}\n"
        )
    lines.extend(
        [
            "Scoring: vendored IFBench checkers (`scripts/ifbench_checkers.py`), "
            f"strict+loose, independent of compl-ai. Emoji backend: `{ic.EMOJI_BACKEND}`.\n",
        ]
    )
    for client in _client_names(results):
        scored = [r for r in results if r.client == client and not r.error]
        lines.append(
            f"Result `{client}`: strict {sum(r.strict_pass for r in scored)}/{len(scored)}, "
            f"loose {sum(r.loose_pass for r in scored)}/{len(scored)}, "
            f"errors {sum(1 for r in results if r.client == client and r.error)}\n"
        )
    lines.extend(
        [
            "| id | instructions | openai strict | openai loose | moralstack strict | "
            "moralstack loose | moralstack final_action | note |",
            "| ---: | --- | :---: | :---: | :---: | :---: | --- | --- |",
        ]
    )
    for case_id, per_client in by_case.items():
        r_any = next(iter(per_client.values()))
        openai = per_client.get("openai")
        moralstack = per_client.get("moralstack")
        openai_strict, openai_loose = _report_cells(openai)
        moralstack_strict, moralstack_loose = _report_cells(moralstack)
        final_action = moralstack.final_action if moralstack and moralstack.final_action else "-"
        errors = [r.error for r in per_client.values() if r.error]
        note = ("; ".join(errors) or r_any.note).replace("|", "\\|")
        lines.append(
            f"| {case_id} | {', '.join(r_any.instruction_id_list)} | "
            f"{openai_strict} | {openai_loose} | {moralstack_strict} | "
            f"{moralstack_loose} | {final_action} | {note} |"
        )
    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    config_payload = {
        cfg.name: {
            "base_url": cfg.base_url,
            "model": cfg.model,
            "max_tokens": cfg.max_tokens,
            "temperature": cfg.temperature,
            "top_p": cfg.top_p,
        }
        for cfg in configs
    }
    payload = {
        "generated_at": ts,
        "clients": config_payload,
        "emoji_backend": ic.EMOJI_BACKEND,
        "results": [
            {
                "client": r.client,
                "case_id": r.case_id,
                "instruction_id_list": r.instruction_id_list,
                "group": r.group,
                "note": r.note,
                "status": _status(r),
                "strict_pass": r.strict_pass,
                "loose_pass": r.loose_pass,
                "strict_per_instruction": r.strict_per_instruction,
                "loose_per_instruction": r.loose_per_instruction,
                "final_action": r.final_action,
                "path": r.path,
                "error": r.error,
                "response": r.response,
            }
            for r in results
        ],
    }
    out_path.with_suffix(".json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _report_cells(result: CaseResult | None) -> tuple[str, str]:
    if result is None:
        return "-", "-"
    if result.error:
        return "ERR", "ERR"
    return ("PASS" if result.strict_pass else "fail", "PASS" if result.loose_pass else "fail")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    load_env()
    p = argparse.ArgumentParser(
        description="Re-test failed IFBench cases against pure OpenAI and the MoralStack proxy, "
        "scored with vendored IFBench checkers (no compl-ai).",
    )
    p.add_argument(
        "--openai-base-url",
        default=os.getenv("IFBENCH_OPENAI_BASE_URL", DEFAULT_OPENAI_BASE_URL),
        help=f"Pure OpenAI base URL (default: {DEFAULT_OPENAI_BASE_URL}).",
    )
    p.add_argument(
        "--openai-model",
        default=os.getenv("IFBENCH_OPENAI_MODEL", DEFAULT_OPENAI_MODEL),
        help=f"Pure OpenAI model (default: {DEFAULT_OPENAI_MODEL}).",
    )
    p.add_argument(
        "--openai-api-key",
        default=os.getenv("IFBENCH_OPENAI_API_KEY") or os.getenv("OPENAI_API_KEY", ""),
        help="Bearer token for pure OpenAI (default: IFBENCH_OPENAI_API_KEY or OPENAI_API_KEY).",
    )
    p.add_argument(
        "--openai-max-tokens",
        type=int,
        default=int(os.getenv("IFBENCH_OPENAI_MAX_TOKENS", str(DEFAULT_OPENAI_MAX_TOKENS))),
        help=f"max_tokens for pure OpenAI (default: {DEFAULT_OPENAI_MAX_TOKENS}).",
    )
    p.add_argument(
        "--moralstack-base-url",
        "--base-url",
        default=os.getenv("MORALSTACK_OPENAI_COMPATIBLE_BASE_URL", DEFAULT_MORALSTACK_BASE_URL),
        help=f"OpenAI-compatible base URL of the MoralStack proxy (default: {DEFAULT_MORALSTACK_BASE_URL}).",
    )
    p.add_argument(
        "--moralstack-model",
        "--model",
        default=os.getenv("IFBENCH_MORALSTACK_MODEL", DEFAULT_MORALSTACK_MODEL),
        help=f"Model name sent to the MoralStack proxy (default: {DEFAULT_MORALSTACK_MODEL}).",
    )
    p.add_argument(
        "--moralstack-api-key",
        "--api-key",
        default=os.getenv("OPENAI_API_KEY", "sk-noauth"),
        help="Bearer token for MoralStack (the bridge does not authenticate; any value works).",
    )
    p.add_argument("--ids", help="Comma-separated case ids to test (default: all in data file).")
    p.add_argument(
        "--group",
        choices=["all", "primary", "secondary"],
        default="all",
        help="Subset by group (primary = the 21 OpenAI-pass/MoralStack-fail cases).",
    )
    p.add_argument(
        "--moralstack-max-tokens",
        "--max-tokens",
        type=int,
        default=int(os.getenv("IFBENCH_MORALSTACK_MAX_TOKENS", str(DEFAULT_MORALSTACK_MAX_TOKENS))),
        help=f"max_tokens for MoralStack (default: {DEFAULT_MORALSTACK_MAX_TOKENS}).",
    )
    p.add_argument(
        "--moralstack-temperature",
        "--temperature",
        type=float,
        default=float(os.getenv("IFBENCH_MORALSTACK_TEMPERATURE", str(DEFAULT_MORALSTACK_TEMPERATURE))),
        help=f"temperature for MoralStack (default: {DEFAULT_MORALSTACK_TEMPERATURE}).",
    )
    p.add_argument(
        "--moralstack-top-p",
        "--top-p",
        type=float,
        default=float(os.getenv("IFBENCH_MORALSTACK_TOP_P", str(DEFAULT_MORALSTACK_TOP_P))),
        help=f"top_p for MoralStack (default: {DEFAULT_MORALSTACK_TOP_P}).",
    )
    p.add_argument(
        "--timeout",
        type=float,
        default=float(os.getenv("IFBENCH_TIMEOUT_SECONDS", str(DEFAULT_TIMEOUT_SECONDS))),
        help="Per-request timeout (seconds).",
    )
    p.add_argument(
        "--output",
        default=os.getenv("IFBENCH_RERUN_OUTPUT", DEFAULT_OUTPUT),
        help="Markdown report path (a sibling .json is also written). Relative to workspace root.",
    )
    args = p.parse_args(argv)
    args.ids = [s.strip() for s in args.ids.split(",") if s.strip()] if args.ids else None
    return args


def main() -> None:
    raise SystemExit(run(parse_args()))


if __name__ == "__main__":
    main()
