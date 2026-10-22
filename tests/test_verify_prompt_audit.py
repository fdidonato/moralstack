"""Tests for ``scripts/verify_prompt_audit.py`` (prompt externalization Phase A, step A4c). Tier 1: offline, deterministic.

Every database is a synthetic ``tmp_path`` SQLite file built with the production schema; rows are written with plain
``INSERT`` statements (no observability singleton, no queue). Expected system prompts come from the prompt catalog, so
no prompt text is hardcoded here.
"""

from __future__ import annotations

import gc
import importlib.util
import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from moralstack.observability.sinks.sqlite_sink import _SCHEMA
from moralstack.observability.wire import encode_wire, encode_wire_calls, generate_messages_for
from moralstack.prompts.catalog import NO_CONTEXT, PromptContext, PromptSet, default_prompt_set, load_prompt_set
from tests.prompt_markers import make_marker_dir, make_when_override_dir

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "verify_prompt_audit.py"
CANARY_LINE_ID = "policy.canary_instruction"


def _load_script() -> ModuleType:
    spec = importlib.util.spec_from_file_location("verify_prompt_audit_under_test", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


tool = _load_script()


# --------------------------------------------------------------------------------------
# Synthetic data
# --------------------------------------------------------------------------------------


def make_db(tmp_path: Path, name: str = "audit.db", *, billable_column: bool = True) -> Path:
    """The base schema; ``billable_provider_call`` is a later migration column, absent on older databases."""
    path = tmp_path / name
    conn = sqlite3.connect(path)
    conn.executescript(_SCHEMA)
    if billable_column:
        conn.execute("ALTER TABLE llm_calls ADD COLUMN billable_provider_call INTEGER")
    conn.commit()
    conn.close()
    return path


def add_run(db: Path, run_id: str, config: dict[str, Any] | None = None) -> None:
    meta = {"prompt_config": config} if config is not None else {}
    with sqlite3.connect(db) as conn:
        conn.execute(
            "INSERT INTO runs (run_id, run_type, started_at, status, meta_json) VALUES (?, 'test', 1, 'ok', ?)",
            (run_id, json.dumps(meta)),
        )


def add_request(db: Path, run_id: str, request_id: str, link: dict[str, Any] | None = None) -> None:
    meta = {"prompt_set": link} if link is not None else {}
    with sqlite3.connect(db) as conn:
        conn.execute(
            "INSERT INTO requests (run_id, request_id, prompt, created_at, meta_json) VALUES (?, ?, 'q', 1, ?)",
            (run_id, request_id, json.dumps(meta)),
        )


def add_row(
    db: Path,
    run_id: str,
    module: str,
    action: str,
    *,
    request_id: str = "r1",
    system: str = "",
    prompt: str = "q",
    summary: Any = None,
    parsed_json: Any = None,
    billable: int | None = None,
    attempts: int | None = 1,
) -> int:
    def as_text(value: Any) -> str | None:
        return value if value is None or isinstance(value, str) else json.dumps(value)

    with sqlite3.connect(db) as conn:
        cur = conn.execute(
            "INSERT INTO llm_calls (run_id, request_id, phase, module, action, started_at, prompt, system_prompt, "
            "raw_response, parsed_json, parsed_summary_json, attempts) VALUES (?, ?, 'p', ?, ?, 1, ?, ?, '', ?, ?, ?)",
            (run_id, request_id, module, action, prompt, system, as_text(parsed_json), as_text(summary), attempts),
        )
        row_id = int(cur.lastrowid or 0)
        if billable is not None:
            conn.execute("UPDATE llm_calls SET billable_provider_call = ? WHERE id = ?", (billable, row_id))
        return row_id


def policy_system(ps: PromptSet, *, canary: str | None = "§REF_0A1B2C3D§", contract: str = "", mode: str = "") -> str:
    base = ps.text("policy.system") + "\n" + ps.text("policy.language_rules") + "\n" + ps.text("policy.anti_leak_rules")
    if canary:
        base += ps.format(CANARY_LINE_ID, canary=canary)
    if contract:
        base = contract + "\n\n" + base
    if mode:
        base += "\n\n" + ps.text(mode)
    return base


def generate_wire(
    prompt: str, system: str, *, prompt_col: str | None = None, system_col: str | None = None
) -> dict[str, Any]:
    return encode_wire(
        generate_messages_for(prompt, system),
        prompt_col=prompt if prompt_col is None else prompt_col,
        system_prompt_col=system if system_col is None else system_col,
        branch="generate",
    )


def seed_standard_rows(db: Path, run_id: str, ps: PromptSet, *, with_wire: bool = True) -> dict[str, int]:
    """One row per verified family, rendered with ``ps``; returns ``{label: row id}``."""
    ids: dict[str, int] = {}
    ctx = PromptContext()

    intent = ps.render("risk.intent.system", ctx)
    ids["risk"] = add_row(
        db, run_id, "risk_estimator", "estimate_intent", system=intent,
        summary={"wire": generate_wire("q", intent)} if with_wire else None,
    )  # fmt: skip

    critic = ps.render("critic.full.system", ctx)
    ids["critic"] = add_row(
        db, run_id, "critic", "critique", system=critic,
        summary="Violations: 0, Guidance: none",
        parsed_json={"wire": generate_wire("q", critic)} if with_wire else None,
    )  # fmt: skip

    policy = policy_system(ps, contract="You are SupportBot.")
    ids["policy"] = add_row(
        db, run_id, "policy", "generate (fast_path)", system=policy,
        summary={"wire": generate_wire("q", policy)} if with_wire else None,
    )  # fmt: skip

    ids["prefilter"] = add_row(
        db, run_id, "constitution_retriever", "domain_prefilter",
        system=ps.format("retrieval.prefilter.system", domain_list="- medical\n- legal", max_domains=2),
    )  # fmt: skip

    persp = ps.text("perspectives.base") + "\n\n" + ps.render("perspectives.system_body", NO_CONTEXT)
    joined = "\n---\n".join([persp, persp])
    calls = [(generate_messages_for("p1", persp), "generate"), (generate_messages_for("p2", persp), "generate")]
    ids["perspectives"] = add_row(
        db, run_id, "perspectives", "evaluate", system=joined, prompt="p1\n---\np2",
        summary=encode_wire_calls(calls, prompt_col="", system_prompt_col=joined) if with_wire else None,
    )  # fmt: skip

    refusal = ps.text("refusal.system")
    refusal_calls = [(generate_messages_for("a", refusal), "generate"), (generate_messages_for("b", refusal), "generate")]
    ids["refusal"] = add_row(
        db, run_id, "orchestration", "refuse (deliberative, retried_no_leak)", system=refusal, prompt="b",
        summary=encode_wire_calls(refusal_calls, prompt_col="", system_prompt_col=refusal) if with_wire else None,
    )  # fmt: skip
    return ids


def run_tool(capsys: pytest.CaptureFixture[str], *argv: str) -> tuple[int, str]:
    capsys.readouterr()
    code = tool.main(list(argv))
    return code, capsys.readouterr().out


def tool_json(capsys: pytest.CaptureFixture[str], *argv: str) -> tuple[int, dict[str, Any]]:
    code, out = run_tool(capsys, *argv, "--json")
    return code, json.loads(out)


def module_counts(report: dict[str, Any], module: str, source: int = 0) -> dict[str, int]:
    return report["sources"][source]["modules"][module]["counts"]


def run_entry(report: dict[str, Any], run_id: str, source: int = 0) -> dict[str, Any]:
    return next(r for r in report["sources"][source]["runs"] if r["run_id"] == run_id)


# --------------------------------------------------------------------------------------
# Default run
# --------------------------------------------------------------------------------------


def test_default_run_is_all_ok_and_requests_are_linked(tmp_path, capsys):
    ps = default_prompt_set()
    db = make_db(tmp_path)
    add_run(db, "run-d", ps.audit_summary())
    add_request(db, "run-d", "r1", ps.request_link())
    seed_standard_rows(db, "run-d", ps)

    code, report = tool_json(capsys, "--db", str(db), "--strict")

    assert code == 0
    run = run_entry(report, "run-d")
    assert run["config_status"] == "ok" and run["ps"] == ps.ps and run["is_default"] is True
    assert run["rows"]["ok"] == 6
    assert all(run["rows"][s] == 0 for s in ("mismatch", "wire_integrity", "missing", "unknown", "no_wire"))
    assert run["requests"] == {"ok": 1}
    assert report["strict_failures"] == 0
    assert module_counts(report, "orchestration")["ok"] == 1
    assert report["sources"][0]["modules"]["policy"]["rows_with_wire"] == 1
    assert report["sources"][0]["modules"]["constitution_retriever"]["rows_with_wire"] == 0


def test_text_output_has_the_module_table_and_the_strict_line(tmp_path, capsys):
    ps = default_prompt_set()
    db = make_db(tmp_path)
    add_run(db, "run-d", ps.audit_summary())
    seed_standard_rows(db, "run-d", ps)

    code, out = run_tool(capsys, "--db", str(db))

    assert code == 0
    assert "module" in out and "wire_integrity" in out
    assert "critic" in out and "perspectives" in out
    assert "safety_fragments_overridden (0): none" in out
    assert "strict failures" in out


def test_json_legend_documents_the_statuses(tmp_path, capsys):
    db = make_db(tmp_path)
    add_run(db, "run-d", default_prompt_set().audit_summary())
    _, report = tool_json(capsys, "--db", str(db))
    assert set(tool.STATUSES) <= set(report["legend"])
    assert any("adapter-attested" in note for note in report["legend"]["notes"])


# --------------------------------------------------------------------------------------
# Override run (safety fragments included)
# --------------------------------------------------------------------------------------


@pytest.fixture()
def override_set(tmp_path: Path) -> PromptSet:
    ids = [
        "refusal.system",
        "critic.base",
        "policy.system",
        "perspectives.base",
        "risk.intent.head",
        "retrieval.prefilter.system",
    ]
    return load_prompt_set(make_marker_dir(tmp_path, ids=ids))


def test_override_run_is_rebuilt_checked_and_reports_safety_fragments(tmp_path, capsys, override_set):
    ps = override_set
    assert not ps.is_default
    db = make_db(tmp_path)
    add_run(db, "run-o", ps.audit_summary())
    add_request(db, "run-o", "r1", ps.request_link())
    seed_standard_rows(db, "run-o", ps)

    code, report = tool_json(capsys, "--db", str(db), "--strict")

    assert code == 0, report
    run = run_entry(report, "run-o")
    assert run["config_status"] == "ok" and run["is_default"] is False
    assert run["fragment_diffs"] == []
    assert run["rows"]["ok"] == 6 and run["rows"]["mismatch"] == 0
    # risk.* / critic.* / refusal.* overrides are safety-relevant; policy.system and perspectives.base are not
    assert run["safety_fragments_overridden"] == sorted(ps.audit_summary()["safety_fragments_overridden"])
    assert {"refusal.system", "critic.base", "risk.intent.head", "retrieval.prefilter.system"} <= set(
        run["safety_fragments_overridden"]
    )
    assert "policy.system" not in run["safety_fragments_overridden"]


def test_override_run_rows_rendered_with_the_default_set_are_mismatches(tmp_path, capsys, override_set):
    db = make_db(tmp_path)
    add_run(db, "run-o", override_set.audit_summary())
    # the audited run says the override was active, but the row carries the packaged default text
    row = add_row(db, "run-o", "orchestration", "refuse (fast_path)", system=default_prompt_set().text("refusal.system"))

    code, report = tool_json(capsys, "--db", str(db), "--strict")

    assert code == 1
    assert module_counts(report, "orchestration")["mismatch"] == 1
    assert report["sources"][0]["modules"]["orchestration"]["offending_rows"]["mismatch"] == [str(row)]


def test_text_output_lists_the_overridden_safety_fragments(tmp_path, capsys, override_set):
    db = make_db(tmp_path)
    add_run(db, "run-o", override_set.audit_summary())
    _, out = run_tool(capsys, "--db", str(db))
    assert "refusal.system" in out and "safety_fragments_overridden (" in out


def test_tampered_audit_fragment_hash_is_a_run_mismatch(tmp_path, capsys):
    config = default_prompt_set().audit_summary()
    config["fragments"]["policy.system"]["sha256"] = "0" * 64
    db = make_db(tmp_path)
    add_run(db, "run-t", config)

    code, report = tool_json(capsys, "--db", str(db), "--strict")

    run = run_entry(report, "run-t")
    assert run["config_status"] == "mismatch" and "policy.system" in run["fragment_diffs"]
    assert code == 1


def test_tampered_override_text_changes_the_rebuilt_fingerprint(tmp_path, capsys, override_set):
    config = override_set.audit_summary()
    config["fragments"]["refusal.system"]["text"] += " (edited after the fact)"
    db = make_db(tmp_path)
    add_run(db, "run-t", config)

    code, report = tool_json(capsys, "--db", str(db), "--strict")

    assert run_entry(report, "run-t")["config_status"] == "mismatch"
    assert code == 1


def test_defaults_version_differs_is_unknown_not_mismatch(tmp_path, capsys):
    config = default_prompt_set().audit_summary()
    config["default_fingerprint"] = "sha256:" + "a" * 64
    db = make_db(tmp_path)
    add_run(db, "run-v", config)
    add_row(db, "run-v", "orchestration", "refuse (fast_path)", system="whatever the older defaults said")

    code, report = tool_json(capsys, "--db", str(db), "--strict")

    run = run_entry(report, "run-v")
    assert run["config_status"] == "defaults_differ"
    assert any("defaults version differs" in p for p in run["problems"])
    assert run["rows"]["unknown"] == 1 and run["rows"]["mismatch"] == 0
    assert code == 0


def test_malformed_prompt_config_is_reported_and_fails_strict(tmp_path, capsys):
    db = make_db(tmp_path)
    add_run(db, "run-m", {"schema": 1, "ps": "x"})

    code, report = tool_json(capsys, "--db", str(db), "--strict")

    run = run_entry(report, "run-m")
    assert run["config_status"] == "malformed" and any("missing key" in p for p in run["problems"])
    assert code == 1


# --------------------------------------------------------------------------------------
# Pre-feature data
# --------------------------------------------------------------------------------------


def test_pre_feature_run_is_checked_against_the_current_defaults(tmp_path, capsys):
    ps = default_prompt_set()
    db = make_db(tmp_path)
    add_run(db, "run-old", None)
    add_request(db, "run-old", "r1", None)
    seed_standard_rows(db, "run-old", ps, with_wire=False)
    retry = add_row(db, "run-old", "critic", "retry_failed_attempt_1", system="")  # no system column, no wire (pre-feature)

    code, report = tool_json(capsys, "--db", str(db), "--strict")

    run = run_entry(report, "run-old")
    assert code == 0
    assert run["config_status"] == "pre-feature" and run["note"] == "no prompt_config (pre-feature)"
    assert run["rows"]["ok"] == 6 and run["rows"]["no_wire"] == 1
    assert report["sources"][0]["modules"]["critic"]["offending_rows"]["no_wire"] == [str(retry)]
    assert run["requests"] == {"pre-feature": 1}
    _, out = run_tool(capsys, "--db", str(db))
    assert "no prompt_config (pre-feature)" in out


def test_pre_feature_row_with_a_stale_system_prompt_is_a_mismatch(tmp_path, capsys):
    db = make_db(tmp_path)
    add_run(db, "run-old", None)
    add_row(db, "run-old", "critic", "critique", system="An older critic prompt")

    code, report = tool_json(capsys, "--db", str(db))

    assert code == 0  # not --strict
    assert module_counts(report, "critic")["mismatch"] == 1


# --------------------------------------------------------------------------------------
# Tampering
# --------------------------------------------------------------------------------------


def test_tampered_system_prompt_is_a_mismatch_and_strict_exits_one(tmp_path, capsys):
    ps = default_prompt_set()
    db = make_db(tmp_path)
    add_run(db, "run-x", ps.audit_summary())
    system = ps.render("critic.full.system", NO_CONTEXT)
    bad = add_row(db, "run-x", "critic", "critique", system=system.replace("violat", "VIOLAT", 1), summary="Violations: 0")
    assert system != db_value(db, bad, "system_prompt")

    code_lenient, report = tool_json(capsys, "--db", str(db))
    code_strict, _ = tool_json(capsys, "--db", str(db), "--strict")

    assert code_lenient == 0 and code_strict == 1
    assert module_counts(report, "critic")["mismatch"] == 1
    assert report["sources"][0]["modules"]["critic"]["offending_rows"]["mismatch"] == [str(bad)]


def db_value(db: Path, row_id: int, column: str) -> str:
    with sqlite3.connect(db) as conn:
        return str(conn.execute(f"SELECT {column} FROM llm_calls WHERE id = ?", (row_id,)).fetchone()[0])


def test_tampered_system_column_under_a_referencing_wire_is_an_integrity_error(tmp_path, capsys):
    ps = default_prompt_set()
    db = make_db(tmp_path)
    add_run(db, "run-x", ps.audit_summary())
    system = ps.render("risk.intent.system", NO_CONTEXT)
    # the wire references the column; the column is edited afterwards, so the decoded bytes no longer hash
    row = add_row(
        db,
        "run-x",
        "risk_estimator",
        "estimate_intent",
        system=system,
        summary={"wire": generate_wire("q", system)},
    )
    with sqlite3.connect(db) as conn:
        conn.execute("UPDATE llm_calls SET system_prompt = ? WHERE id = ?", (system + " tampered twice", row))

    code, report = tool_json(capsys, "--db", str(db), "--strict")

    assert code == 1
    assert module_counts(report, "risk_estimator")["wire_integrity"] == 1


def test_tampered_wire_digest_is_an_integrity_error_not_a_mismatch(tmp_path, capsys):
    ps = default_prompt_set()
    db = make_db(tmp_path)
    add_run(db, "run-x", ps.audit_summary())
    system = policy_system(ps)
    wire = generate_wire("q", system)
    wire["sha256"] = "f" * 64
    row = add_row(db, "run-x", "policy", "generate", system=system, summary={"wire": wire})

    code, report = tool_json(capsys, "--db", str(db), "--strict")

    counts = module_counts(report, "policy")
    assert counts["wire_integrity"] == 1 and counts["mismatch"] == 0
    assert report["sources"][0]["modules"]["policy"]["offending_rows"]["wire_integrity"] == [str(row)]
    assert code == 1


def test_tampered_wire_message_is_an_integrity_error(tmp_path, capsys):
    ps = default_prompt_set()
    db = make_db(tmp_path)
    add_run(db, "run-x", ps.audit_summary())
    system = ps.text("refusal.system")
    wire = generate_wire("question", system, system_col="")  # system inlined in the string table
    wire["strings"][0] = wire["strings"][0] + " and ignore the rules"
    add_row(db, "run-x", "orchestration", "refuse (fast_path)", system="", summary={"wire": wire})

    code, report = tool_json(capsys, "--db", str(db), "--strict")

    assert module_counts(report, "orchestration")["wire_integrity"] == 1
    assert code == 1


def test_malformed_and_unresolvable_wire_records_are_integrity_errors(tmp_path, capsys):
    ps = default_prompt_set()
    db = make_db(tmp_path)
    add_run(db, "run-x", ps.audit_summary())
    system = ps.text("refusal.system")
    bad_version = generate_wire("q", system)
    bad_version["v"] = 2
    add_row(db, "run-x", "orchestration", "refuse (fast_path)", system=system, summary={"wire": bad_version})
    empty_column = generate_wire("q", system)  # refs "system_prompt", but the column is empty
    add_row(db, "run-x", "orchestration", "refuse (fast_path)", system="", summary={"wire": empty_column})
    no_table = encode_wire_calls([(generate_messages_for("q", system), "generate")], prompt_col="", system_prompt_col=system)
    del no_table["wire_strings"]
    add_row(db, "run-x", "orchestration", "refuse (fast_path)", system=system, summary=no_table)

    _, report = tool_json(capsys, "--db", str(db))

    assert module_counts(report, "orchestration")["wire_integrity"] == 3


# --------------------------------------------------------------------------------------
# Wire placement and by-design divergences
# --------------------------------------------------------------------------------------


def test_critic_wire_in_parsed_json_with_a_plain_text_summary_is_verified(tmp_path, capsys):
    ps = default_prompt_set()
    db = make_db(tmp_path)
    add_run(db, "run-c", ps.audit_summary())
    system = ps.render("critic.full.system", NO_CONTEXT)
    add_row(
        db, "run-c", "critic", "critique (hard_violation_revalidation)", system=system,
        summary="Violations: 1, Guidance: fix it", parsed_json={"wire": generate_wire("q", system), "other": 1},
    )  # fmt: skip

    _, report = tool_json(capsys, "--db", str(db), "--strict")

    assert module_counts(report, "critic")["ok"] == 1
    assert report["sources"][0]["modules"]["critic"]["rows_with_wire"] == 1


def test_refusal_row_with_wire_calls_verifies_every_attempt(tmp_path, capsys):
    ps = default_prompt_set()
    db = make_db(tmp_path)
    add_run(db, "run-r", ps.audit_summary())
    system = ps.text("refusal.system")
    calls = [(generate_messages_for("first", system), "generate"), (generate_messages_for("second", system), "generate")]
    summary = encode_wire_calls(calls, prompt_col="second", system_prompt_col=system)
    add_row(
        db,
        "run-r",
        "orchestration",
        "refuse (deliberative, retried_no_leak)",
        system=system,
        prompt="second",
        summary=summary,
        attempts=2,
    )
    # break only the first attempt: each record is matched separately
    broken = json.loads(json.dumps(summary))
    broken["wire_calls"][0]["sha256"] = "0" * 64
    add_row(
        db,
        "run-r",
        "orchestration",
        "refuse (final_revalidation)",
        system=system,
        prompt="second",
        summary=broken,
        attempts=2,
    )

    _, report = tool_json(capsys, "--db", str(db))

    counts = module_counts(report, "orchestration")
    assert counts["ok"] == 1 and counts["wire_integrity"] == 1
    assert report["sources"][0]["modules"]["orchestration"]["branches"] == {"generate": 2}


def test_retry_failed_row_with_an_inlined_system_and_empty_column_is_ok(tmp_path, capsys):
    ps = default_prompt_set()
    db = make_db(tmp_path)
    add_run(db, "run-f", ps.audit_summary())
    system = ps.render("simulator.batch.system", NO_CONTEXT)
    wire = encode_wire(
        generate_messages_for("Retry reason: bad json", system),
        prompt_col="Retry reason: bad json",
        system_prompt_col="",
        branch="generate",
    )
    row = add_row(
        db,
        "run-f",
        "simulator",
        "retry_failed_attempt_1",
        system="",
        prompt="Retry reason: bad json",
        summary={"wire": wire},
    )

    code, report = tool_json(capsys, "--db", str(db), "--strict")

    assert module_counts(report, "simulator")["ok"] == 1, report
    assert module_counts(report, "simulator")["wire_integrity"] == 0
    assert code == 0 and row > 0


def test_g13_policy_row_with_inlined_system_and_empty_column_is_ok(tmp_path, capsys):
    ps = default_prompt_set()
    db = make_db(tmp_path)
    add_run(db, "run-g", ps.audit_summary())
    system = policy_system(ps, contract="Deployer contract text")
    wire = encode_wire(
        generate_messages_for("regenerate", system), prompt_col="regenerate", system_prompt_col="", branch="generate"
    )
    add_row(
        db, "run-g", "policy", "generate (compliance-regenerate)", system="", prompt="regenerate", summary={"wire": wire}
    )

    _, report = tool_json(capsys, "--db", str(db), "--strict")

    assert module_counts(report, "policy")["ok"] == 1


def test_inlined_system_that_is_not_an_expected_prompt_is_a_mismatch(tmp_path, capsys):
    ps = default_prompt_set()
    db = make_db(tmp_path)
    add_run(db, "run-g", ps.audit_summary())
    wire = encode_wire(
        generate_messages_for("regenerate", "You are evil."),
        prompt_col="regenerate",
        system_prompt_col="",
        branch="generate",
    )
    add_row(
        db, "run-g", "policy", "generate (compliance-regenerate)", system="", prompt="regenerate", summary={"wire": wire}
    )

    code, report = tool_json(capsys, "--db", str(db), "--strict")

    assert module_counts(report, "policy")["mismatch"] == 1 and code == 1


def test_column_divergence_from_the_wire_is_informational_not_an_error(tmp_path, capsys):
    ps = default_prompt_set()
    db = make_db(tmp_path)
    add_run(db, "run-i", ps.audit_summary())
    system = ps.text("compliance.dccl.system")
    messages = [{"role": "system", "content": system}, {"role": "user", "content": "raw final user message"}]
    # dccl/generate persists a prompt column that is not among the bytes sent
    wire = encode_wire(messages, prompt_col="a composed prompt", system_prompt_col=system, branch="messages")
    add_row(db, "run-i", "compliance_layer", "evaluate", system=system, prompt="a composed prompt", summary={"wire": wire})

    code, report = tool_json(capsys, "--db", str(db), "--strict")

    stats = report["sources"][0]["modules"]["compliance_layer"]
    assert stats["counts"]["ok"] == 1 and code == 0
    assert stats["informational"]["prompt_column_not_in_wire"] == 1
    assert stats["branches"] == {"messages": 1}


def test_only_the_leading_system_message_is_checked_in_a_native_conversation(tmp_path, capsys):
    ps = default_prompt_set()
    db = make_db(tmp_path)
    add_run(db, "run-n", ps.audit_summary())
    system = ps.text("compliance.dccl.system")
    messages = [
        {"role": "system", "content": system},
        {"role": "system", "content": "the deployer's own system message, part of the conversation"},
        {"role": "user", "content": "hi"},
    ]
    wire = encode_wire(messages, prompt_col="hi", system_prompt_col=system, branch="messages")
    add_row(db, "run-n", "compliance_layer", "evaluate", system=system, prompt="hi", summary={"wire": wire})

    _, report = tool_json(capsys, "--db", str(db))

    assert module_counts(report, "compliance_layer")["ok"] == 1


@pytest.mark.parametrize(
    ("module", "action", "kwargs"),
    [
        ("policy", "generate (speculative-reuse)", {}),
        ("orchestration", "pre_cycle_check", {}),
        ("orchestration", "leakage_detected (fast_path)", {}),
        ("critic", "critique (SKIPPED)", {}),
        ("simulator", "simulate (GATED)", {}),
        ("hindsight", "hindsight (DISABLED)", {}),
        ("policy", "rewrite (SKIPPED_EMPTY_GUIDANCE)", {}),
        ("risk_estimator", "calibration_guard", {"system": "[calibration_guard] Automatic recalibration of risk metrics"}),
        ("embedder", "embed", {}),
        ("upstream_provider", "generate final response", {}),
        ("orchestration", "refuse (fast_path)", {"billable": 0, "system": "anything"}),
        ("orchestration", "refuse (deliberative)", {"attempts": 0, "system": "anything"}),
    ],
)
def test_rows_without_a_record_by_design_are_synthetic(tmp_path, capsys, module, action, kwargs):
    db = make_db(tmp_path)
    add_run(db, "run-s", default_prompt_set().audit_summary())
    add_row(db, "run-s", module, action, **kwargs)

    code, report = tool_json(capsys, "--db", str(db), "--strict")

    counts = module_counts(report, module)
    assert counts["synthetic"] == 1 and counts["mismatch"] == 0 and counts["wire_integrity"] == 0
    assert code == 0


def test_a_module_without_a_matcher_is_unknown(tmp_path, capsys):
    db = make_db(tmp_path)
    add_run(db, "run-u", default_prompt_set().audit_summary())
    add_row(db, "run-u", "future_module", "do_something", system="text")

    code, report = tool_json(capsys, "--db", str(db), "--strict")

    assert module_counts(report, "future_module")["unknown"] == 1 and code == 0


def test_policy_system_requires_the_canary_and_never_carries_the_constraints_on_generate(tmp_path, capsys):
    ps = default_prompt_set()
    db = make_db(tmp_path)
    add_run(db, "run-p", ps.audit_summary())
    constraints = ps.text("policy.rewrite.constraints")
    ok = [
        add_row(db, "run-p", "policy", "generate (fast_path)", system=policy_system(ps)),
        add_row(db, "run-p", "policy", "generate", system=policy_system(ps, contract="You are SupportBot.")),
        add_row(db, "run-p", "upstream_speculative", "generate", system=policy_system(ps)),
        add_row(db, "run-p", "policy", "rewrite", system=ps.text("policy.rewrite.default_system") + constraints),
        add_row(db, "run-p", "policy", "rewrite", system=policy_system(ps) + constraints),
        add_row(db, "run-p", "policy", "soft_revision", system=policy_system(ps) + constraints),
    ]
    bad = [
        add_row(db, "run-p", "policy", "generate", system=policy_system(ps, canary=None)),  # no canary line
        add_row(db, "run-p", "policy", "generate", system=policy_system(ps, canary="not-a-canary")),
        add_row(db, "run-p", "policy", "generate", system=policy_system(ps) + "\n\nsomething appended"),
        # the runtime never puts a mode instruction in the system prompt
        add_row(db, "run-p", "policy", "generate", system=policy_system(ps, mode="policy.safe_complete_instruction")),
        add_row(db, "run-p", "policy", "generate", system=policy_system(ps, mode="policy.constrained_instruction")),
        # generate forbids the rewrite constraints; rewrite / soft_revision require them
        add_row(db, "run-p", "policy", "generate", system=policy_system(ps) + constraints),
        add_row(db, "run-p", "policy", "generate", system=ps.text("policy.rewrite.default_system") + constraints),
        add_row(db, "run-p", "policy", "rewrite", system=policy_system(ps)),
        add_row(db, "run-p", "policy", "soft_revision", system=policy_system(ps)),
        add_row(db, "run-p", "policy", "soft_revision", system=ps.text("policy.rewrite.default_system") + "tail"),
    ]

    _, report = tool_json(capsys, "--db", str(db))

    modules = report["sources"][0]["modules"]
    assert module_counts(report, "policy")["ok"] == 5 and module_counts(report, "policy")["mismatch"] == len(bad)
    assert module_counts(report, "upstream_speculative")["ok"] == 1
    assert modules["policy"]["offending_rows"]["mismatch"] == [str(r) for r in bad]
    assert len(ok) == 6


def test_a_speculative_generate_module_has_no_matcher():
    ps = default_prompt_set()
    matchers = tool.build_matchers(ps)
    assert ("speculative_generate", "generate") not in matchers
    assert ("upstream_speculative", "rewrite") not in matchers


# --------------------------------------------------------------------------------------
# Requests
# --------------------------------------------------------------------------------------


def test_request_without_link_and_with_a_different_set_are_reported(tmp_path, capsys):
    ps = default_prompt_set()
    db = make_db(tmp_path)
    add_run(db, "run-q", ps.audit_summary())
    add_request(db, "run-q", "linked", ps.request_link())
    add_request(db, "run-q", "unlinked", None)
    add_request(db, "run-q", "other-set", {"ps": "0" * 16, "is_default": False})

    code, report = tool_json(capsys, "--db", str(db), "--strict")

    run = run_entry(report, "run-q")
    assert run["requests"] == {"ok": 1, "missing": 1, "mismatch": 1}
    assert run["offending_requests"] == {"missing": ["unlinked"], "mismatch": ["other-set"]}
    assert code == 1  # the mismatch; a missing link alone does not fail --strict
    only_missing = make_db(tmp_path, "only_missing.db")
    add_run(only_missing, "run-q", ps.audit_summary())
    add_request(only_missing, "run-q", "unlinked", None)
    code, _ = tool_json(capsys, "--db", str(only_missing), "--strict")
    assert code == 0


# --------------------------------------------------------------------------------------
# JSONL directory
# --------------------------------------------------------------------------------------


def write_jsonl(directory: Path, name: str, envelopes: list[dict[str, Any]]) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / name).open("a", encoding="utf-8") as fh:
        for env in envelopes:
            fh.write(json.dumps(env, ensure_ascii=False) + "\n")


def llm_envelope(run_id: str, request_id: str, event_id: str, module: str, action: str, **payload: Any) -> dict[str, Any]:
    body = {"phase": "p", "module": module, "action": action, "prompt": "q", "system_prompt": "", **payload}
    return {"event_id": event_id, "event_type": "llm.call", "run_id": run_id, "request_id": request_id, "payload": body}


def seed_jsonl(directory: Path, ps: PromptSet, *, tamper: bool = False) -> None:
    config = ps.audit_summary()
    write_jsonl(
        directory,
        "run.started.jsonl",
        [{"event_type": "run.started", "run_id": "run-j", "payload": {"run_type": "t", "meta": {"prompt_config": config}}}],
    )
    write_jsonl(
        directory,
        "request.upserted.jsonl",
        [
            {
                "event_type": "request.upserted",
                "run_id": "run-j",
                "request_id": "r1",
                "payload": {"prompt": "q", "meta": {}},
            },
            {
                "event_type": "request.upserted",
                "run_id": "run-j",
                "request_id": "r2",
                "payload": {"prompt": "q", "meta": {}},
            },
        ],
    )
    write_jsonl(
        directory,
        "request.meta_updated.jsonl",
        [
            {
                "event_type": "request.meta_updated",
                "run_id": "run-j",
                "request_id": "r1",
                "payload": {"meta": {"prompt_set": ps.request_link()}, "merge": True},
            }
        ],
    )
    critic = ps.render("critic.full.system", NO_CONTEXT)
    system = critic + " tampered" if tamper else critic
    wire = {"wire": generate_wire("q", system)}
    write_jsonl(
        directory,
        "llm.call.jsonl",
        [
            llm_envelope(
                "run-j",
                "r1",
                "e1",
                "critic",
                "critique",
                system_prompt=system,
                parsed_json=wire,
                parsed_summary_json="Violations: 0",
            ),
            llm_envelope(
                "run-j",
                "r1",
                "e2",
                "policy",
                "generate",
                system_prompt=policy_system(ps),
                parsed_summary_json={"wire": generate_wire("q", policy_system(ps))},
            ),
            llm_envelope("run-j", "r1", "e3", "orchestration", "pre_cycle_check"),
            llm_envelope("other-run", "r9", "e4", "critic", "critique", system_prompt="not mine"),
        ],
    )


def test_jsonl_dir_variant_checks_runs_rows_and_requests(tmp_path, capsys):
    ps = default_prompt_set()
    jsonl = tmp_path / "jsonl"
    seed_jsonl(jsonl, ps)
    # the benchmark file_only orphan and rotated files sit in the same directory
    (jsonl / "benchmark_run-j.json").write_text("{}", encoding="utf-8")

    code, report = tool_json(capsys, "--jsonl-dir", str(jsonl), "--run-id", "run-j", "--strict")

    assert code == 0, report
    assert report["sources"][0]["source"].startswith("jsonl:")
    run = run_entry(report, "run-j")
    assert run["config_status"] == "ok"
    assert run["rows"]["ok"] == 2 and run["rows"]["synthetic"] == 1
    assert run["requests"] == {"ok": 1, "missing": 1}  # r1 linked via request.meta_updated, r2 never linked
    assert [r["run_id"] for r in report["sources"][0]["runs"]] == ["run-j"]


def test_jsonl_dir_variant_flags_a_tampered_system_prompt(tmp_path, capsys):
    jsonl = tmp_path / "jsonl"
    seed_jsonl(jsonl, default_prompt_set(), tamper=True)

    code, report = tool_json(capsys, "--jsonl-dir", str(jsonl), "--run-id", "run-j", "--strict")

    assert module_counts(report, "critic")["mismatch"] == 1 and code == 1
    assert report["sources"][0]["modules"]["critic"]["offending_rows"]["mismatch"] == ["e1"]


def test_first_run_started_line_wins_after_guard_eviction(tmp_path, capsys):
    ps = default_prompt_set()
    jsonl = tmp_path / "jsonl"
    seed_jsonl(jsonl, ps)
    evicted = ps.audit_summary()
    evicted["ps"] = "f" * 16  # a second line for the same run id, as written after the guard evicted the id
    write_jsonl(
        jsonl,
        "run.started.jsonl",
        [{"event_type": "run.started", "run_id": "run-j", "payload": {"run_type": "t", "meta": {"prompt_config": evicted}}}],
    )

    _, report = tool_json(capsys, "--jsonl-dir", str(jsonl), "--run-id", "run-j")

    run = run_entry(report, "run-j")
    assert run["config_status"] == "ok" and run["ps"] == ps.ps


def test_db_without_prompt_config_falls_back_to_the_jsonl_run_started_line(tmp_path, capsys):
    ps = default_prompt_set()
    jsonl = tmp_path / "jsonl"
    seed_jsonl(jsonl, ps)
    db = make_db(tmp_path)
    add_run(db, "run-j", None)  # the DB row has no prompt_config; the file channel does
    add_request(db, "run-j", "r1", None)  # ...and no link; request.meta_updated.jsonl has it
    add_row(db, "run-j", "critic", "critique", system=ps.render("critic.full.system", NO_CONTEXT))

    _, report = tool_json(capsys, "--db", str(db), "--jsonl-dir", str(jsonl))

    run = run_entry(report, "run-j")
    assert run["config_status"] == "ok" and run["ps"] == ps.ps
    assert run["requests"] == {"ok": 1}


# --------------------------------------------------------------------------------------
# CLI contract, read-only guarantee, imports
# --------------------------------------------------------------------------------------


def test_db_bytes_and_mtime_are_unchanged_after_every_mode(tmp_path, capsys):
    ps = default_prompt_set()
    db = make_db(tmp_path)
    add_run(db, "run-ro", ps.audit_summary())
    add_request(db, "run-ro", "r1", ps.request_link())
    seed_standard_rows(db, "run-ro", ps)
    os.utime(db, (1_700_000_000, 1_700_000_000))
    before_bytes = db.read_bytes()
    before_stat = db.stat()
    siblings_before = sorted(p.name for p in tmp_path.iterdir())

    run_tool(capsys, "--db", str(db))
    run_tool(capsys, "--db", str(db), "--json", "--strict")
    run_tool(capsys, "--db", str(db), "--run-id", "run-ro", "--run-id", "missing")

    assert db.read_bytes() == before_bytes
    after = db.stat()
    assert after.st_mtime_ns == before_stat.st_mtime_ns and after.st_size == before_stat.st_size
    assert sorted(p.name for p in tmp_path.iterdir()) == siblings_before  # no journal / wal / shm left behind


def test_database_is_opened_read_only(tmp_path):
    db = make_db(tmp_path)
    source = tool.SqliteSource(db)
    try:
        with pytest.raises(sqlite3.OperationalError):
            source._conn.execute("INSERT INTO runs (run_id, run_type, started_at, status) VALUES ('x', 't', 1, 'ok')")
    finally:
        source.close()


def test_run_id_filter_and_unknown_run_ids(tmp_path, capsys):
    ps = default_prompt_set()
    db = make_db(tmp_path)
    add_run(db, "run-1", ps.audit_summary())
    add_run(db, "run-2", ps.audit_summary())

    _, report = tool_json(capsys, "--db", str(db), "--run-id", "run-2", "--run-id", "nope")

    assert [r["run_id"] for r in report["sources"][0]["runs"]] == ["run-2"]
    assert report["run_ids_not_found"] == ["nope"]


def test_usage_and_input_errors(tmp_path, capsys):
    with pytest.raises(SystemExit) as no_source:
        tool.main([])
    assert no_source.value.code == 2
    assert tool.main(["--db", str(tmp_path / "does_not_exist.db")]) == 2
    not_a_db = tmp_path / "junk.db"
    not_a_db.write_text("this is not sqlite", encoding="utf-8")
    assert tool.main(["--db", str(not_a_db)]) == 2
    assert tool.main(["--jsonl-dir", str(tmp_path / "no_such_dir")]) == 2
    assert "cannot read input" in capsys.readouterr().err


def test_importing_the_tool_loads_no_runtime_client_thread_or_logging_handler():
    code = (
        "import importlib.util, json, logging, sys, threading;"
        f"spec = importlib.util.spec_from_file_location('vpa', r'{SCRIPT}');"
        "m = importlib.util.module_from_spec(spec); sys.modules['vpa'] = m; spec.loader.exec_module(m);"
        "http = ('openai', 'httpx', 'requests', 'urllib3', 'anthropic', 'aiohttp');"
        "print(json.dumps({"
        "'http': sorted(n for n in sys.modules if n.split('.')[0] in http),"
        "'runtime': sorted(n for n in sys.modules if n.startswith(('moralstack.runtime', 'moralstack.orchestration',"
        " 'moralstack.models.policy', 'moralstack.models.risk.estimator', 'moralstack.observability.service'))),"
        "'threads': [t.name for t in threading.enumerate() if t is not threading.main_thread()],"
        "'handlers': len(logging.getLogger().handlers),"
        "'has_catalog': 'moralstack.prompts.catalog' in sys.modules,"
        "'has_signals': callable(m.get_harm_signal_prompts) and m._SIGNAL_REGISTRY is not None,"
        "'has_wire': callable(m.decode_wire),"
        "'placeholders_left': sorted(n for n in sys.modules if n.startswith(('moralstack.models.risk',"
        " 'moralstack.observability')))}));"
        # The placeholders must not outlive the import: a later real import in the same process gets the real
        # packages (the test suite loads this script in-process).
        "from moralstack.models.risk import LLMBasedRiskEstimator;"
        "import moralstack.observability.service;"
        "print('REAL_IMPORTS_OK')"
    )
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    out = subprocess.run(
        [sys.executable, "-I", "-c", code], capture_output=True, text=True, timeout=120, env=env, cwd=SCRIPT.parents[1]
    )
    assert out.returncode == 0, out.stderr
    lines = out.stdout.strip().splitlines()
    seen = json.loads(lines[-2])
    assert seen["http"] == [] and seen["runtime"] == [] and seen["threads"] == [] and seen["handlers"] == 0
    assert seen["has_catalog"] and seen["has_signals"] and seen["has_wire"]
    assert seen["placeholders_left"] == []
    assert lines[-1] == "REAL_IMPORTS_OK"


def test_checked_prompt_config_schema_helper_accepts_the_real_audit_summary():
    assert tool.check_prompt_config(default_prompt_set().audit_summary()) == []
    broken = default_prompt_set().audit_summary()
    broken["fragments"]["policy.system"]["when"] = "sometimes"
    broken["ps"] = "short"
    problems = tool.check_prompt_config(broken)
    assert any("invalid 'when'" in p for p in problems) and any("ps is not" in p for p in problems)


# --------------------------------------------------------------------------------------
# A4c review fixes
# --------------------------------------------------------------------------------------


def _all_renders(ps: PromptSet, fragment_id: str) -> set[str]:
    return {ps.render(fragment_id, PromptContext(c, h)) for c in (False, True) for h in (False, True)}


def test_when_override_rows_are_checked_against_the_contexts_the_override_allows(tmp_path, capsys):
    default = default_prompt_set()
    ps = load_prompt_set(make_when_override_dir(tmp_path, {"critic.rules.head": "contract_present"}))
    with_contract = ps.render("critic.full.system", PromptContext(True, False))
    without_contract = ps.render("critic.full.system", PromptContext(False, False))
    assert with_contract != without_contract
    db = make_db(tmp_path)
    add_run(db, "run-w", ps.audit_summary())
    add_row(db, "run-w", "critic", "critique", system=with_contract, summary="x")
    add_row(db, "run-w", "critic", "critique", system=without_contract, summary="x")
    # the same override-only rendering is not a valid system prompt of a run that used the defaults
    add_run(db, "run-d", default.audit_summary())
    stale = add_row(db, "run-d", "critic", "critique", system=without_contract, summary="x")

    code, report = tool_json(capsys, "--db", str(db), "--strict")

    assert run_entry(report, "run-w")["config_status"] == "ok"
    assert run_entry(report, "run-w")["rows"]["ok"] == 2
    assert run_entry(report, "run-d")["rows"]["mismatch"] == 1
    assert report["sources"][0]["modules"]["critic"]["offending_rows"]["mismatch"] == [str(stale)]
    assert code == 1


def test_a_never_override_makes_the_default_render_a_mismatch(tmp_path, capsys):
    default = default_prompt_set()
    ps = load_prompt_set(make_when_override_dir(tmp_path, {"critic.rules.head": "never"}))
    assert not _all_renders(default, "critic.full.system") <= _all_renders(ps, "critic.full.system")
    db = make_db(tmp_path)
    add_run(db, "run-n", ps.audit_summary())
    add_row(db, "run-n", "critic", "critique", system=ps.render("critic.full.system", NO_CONTEXT), summary="x")
    add_row(db, "run-n", "critic", "critique", system=default.render("critic.full.system", NO_CONTEXT), summary="x")

    _, report = tool_json(capsys, "--db", str(db))

    assert module_counts(report, "critic")["ok"] == 1 and module_counts(report, "critic")["mismatch"] == 1


def test_wal_database_main_file_is_not_modified(tmp_path, capsys):
    ps = default_prompt_set()
    db = make_db(tmp_path)
    with sqlite3.connect(db) as conn:
        assert conn.execute("PRAGMA journal_mode=WAL").fetchone()[0].lower() == "wal"
    add_run(db, "run-wal", ps.audit_summary())
    seed_standard_rows(db, "run-wal", ps)
    # The seeding helpers never close their connections (`with sqlite3.connect(...)` only commits) and a Connection
    # sits in a reference cycle, so the cyclic GC closes them at an arbitrary moment; closing a WAL connection
    # checkpoints the -wal into the main file. Close them before the snapshot, or the checkpoint can land between
    # `before` and `after` (flaky under xdist).
    gc.collect()
    os.utime(db, (1_700_000_000, 1_700_000_000))
    before_bytes = db.read_bytes()
    before = db.stat()

    code, report = tool_json(capsys, "--db", str(db), "--strict")
    gc.collect()  # the tool's own connection objects, too, must leave the main file alone once collected

    assert code == 0 and run_entry(report, "run-wal")["rows"]["ok"] == 6
    after = db.stat()
    # -wal / -shm sidecars may appear (SQLite creates them on a read); the main file must not change
    assert db.read_bytes() == before_bytes
    assert after.st_size == before.st_size and after.st_mtime_ns == before.st_mtime_ns


def test_wire_count_tamper_is_an_integrity_error(tmp_path, capsys):
    ps = default_prompt_set()
    db = make_db(tmp_path)
    add_run(db, "run-x", ps.audit_summary())
    system = ps.render("critic.full.system", NO_CONTEXT)
    wire = generate_wire("q", system)
    wire["n"] = wire["n"] + 3  # the digest still matches the decoded messages
    row = add_row(db, "run-x", "critic", "critique", system=system, summary="x", parsed_json={"wire": wire})

    code, report = tool_json(capsys, "--db", str(db), "--strict")

    assert module_counts(report, "critic")["wire_integrity"] == 1 and code == 1
    assert "n=" in report["sources"][0]["modules"]["critic"]["offending_row_details"][str(row)]


def test_unrebuildable_run_is_a_strict_failure(tmp_path, capsys):
    config = default_prompt_set().audit_summary()
    config["fragments"]["no.such.fragment"] = {
        "sha256": "0" * 64,
        "when": "always",
        "origin": "override",
        "overridden": ["text"],
        "text": "x",
    }
    db = make_db(tmp_path)
    add_run(db, "run-u", config)
    add_row(db, "run-u", "critic", "critique", system="anything", summary="x")

    code_lenient, report = tool_json(capsys, "--db", str(db))
    code_strict, _ = tool_json(capsys, "--db", str(db), "--strict")

    run = run_entry(report, "run-u")
    assert run["config_status"] == "unrebuildable" and run["rows"]["unknown"] == 1
    assert report["strict_failures"] == 1
    assert code_lenient == 0 and code_strict == 1
    assert "unrebuildable" in report["legend"]["unknown"]


def test_rotated_jsonl_files_are_read_and_a_malformed_line_is_tolerated(tmp_path, capsys):
    ps = default_prompt_set()
    jsonl = tmp_path / "jsonl"
    seed_jsonl(jsonl, ps)
    with (jsonl / "llm.call.jsonl").open("a", encoding="utf-8") as fh:
        fh.write("{ this is not json\n")
    system = ps.render("critic.full.system", NO_CONTEXT)
    write_jsonl(
        jsonl,
        "llm.call.1.jsonl",
        [llm_envelope("run-j", "r1", "e-rot", "critic", "critique", system_prompt=system + " tampered")],
    )

    code, report = tool_json(capsys, "--jsonl-dir", str(jsonl), "--run-id", "run-j", "--strict")

    assert run_entry(report, "run-j")["rows"]["ok"] == 2
    assert module_counts(report, "critic")["mismatch"] == 1
    assert report["sources"][0]["modules"]["critic"]["offending_rows"]["mismatch"] == ["e-rot"]
    assert code == 1


def test_offending_row_reasons_are_reported_in_json_and_text(tmp_path, capsys):
    db = make_db(tmp_path)
    add_run(db, "run-r", default_prompt_set().audit_summary())
    row = add_row(db, "run-r", "critic", "critique", system="An older critic prompt")

    _, report = tool_json(capsys, "--db", str(db))
    _, out = run_tool(capsys, "--db", str(db))

    detail = report["sources"][0]["modules"]["critic"]["offending_row_details"][str(row)]
    assert "not an expected system prompt" in detail
    assert f"critic mismatch rows: {row} (" in out and "not an expected" in out


def test_missing_llm_calls_table_releases_the_connection(tmp_path):
    path = tmp_path / "empty.db"
    sqlite3.connect(path).close()
    with pytest.raises(ValueError, match="llm_calls"):
        tool.SqliteSource(path)
    path.unlink()  # fails on Windows while a handle is still open
    assert not path.exists()


def test_non_string_wire_strings_are_an_integrity_error_not_a_crash(tmp_path, capsys):
    ps = default_prompt_set()
    db = make_db(tmp_path)
    add_run(db, "run-x", ps.audit_summary())
    system = ps.text("refusal.system")
    calls = [(generate_messages_for("q", system), "generate")]
    unhashable = encode_wire_calls(calls, prompt_col="", system_prompt_col="")
    unhashable["wire_strings"][0] = {"not": "a string"}
    number = encode_wire_calls(calls, prompt_col="", system_prompt_col="")
    number["wire_strings"][0] = 7
    single = generate_wire("question", system, system_col="")
    single["strings"][0] = ["x"]
    for summary in (unhashable, number, {"wire": single}):
        add_row(db, "run-x", "orchestration", "refuse (fast_path)", system="", summary=summary)

    code, report = tool_json(capsys, "--db", str(db), "--strict")

    assert module_counts(report, "orchestration")["wire_integrity"] == 3 and code == 1


def test_default_matchers_are_built_once_per_process():
    ps = default_prompt_set()
    assert tool._matchers_for(ps) is tool._matchers_for(default_prompt_set())
