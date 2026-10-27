#!/usr/bin/env python3
"""Read-only verifier of the prompt audit (prompt externalization, Phase A, step A4c).

Checks, offline and for free, that what the pipeline *recorded* about its prompts is consistent:

* per run: the ``prompt_config`` audit record (``runs.meta_json`` or ``run.started.jsonl``) is well-formed, its
  ``default_fingerprint`` matches the packaged defaults, and the effective prompt set rebuilt from the recorded
  overrides reproduces ``set_fingerprint`` (and every per-fragment sha256 / ``when``);
* per ``llm_calls`` row (or ``llm.call`` envelope): the persisted system prompt (and the system message of the
  recorded ``wire``) is one the effective set can produce for that ``(module, action)``, and the ``wire`` record
  decodes to messages whose sha256 equals the recorded digest;
* per request: ``requests.meta_json.prompt_set.ps`` equals the run's ``prompt_config.ps``.

Read-only by construction: SQLite is opened with ``file:<path>?mode=ro`` and ``PRAGMA query_only``, JSONL files are
only read, nothing is written next to them. No network, no LLM call: it imports only ``moralstack.prompts.catalog``,
``moralstack.observability.wire`` and the risk signal registry/renderer. The two packages that would drag the
runtime in with them (``moralstack.observability``, ``moralstack.models.risk``) are registered as empty placeholders
first, so their ``__init__`` never runs and only the leaf modules are loaded; the placeholders are removed from
``sys.modules`` right after those imports, so importing this script never leaves an empty package behind.

Usage::

    python scripts/verify_prompt_audit.py --db moralstack.db [--run-id ID ...] [--jsonl-dir DIR] [--json] [--strict]

Exit code 0, or 1 with ``--strict`` when any run (``mismatch``, ``malformed``, ``unrebuildable``), row or request is a
``mismatch`` or a wire-integrity error
(2 on a usage / unreadable-input error). Pre-feature data (no ``prompt_config``) is checked against the current
packaged defaults and flagged ``no prompt_config (pre-feature)``.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import logging
import re
import sqlite3
import string
import sys
import tempfile
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:  # allow `python scripts/verify_prompt_audit.py` from any cwd
    sys.path.insert(0, str(_REPO_ROOT))


_PLACEHOLDER_PACKAGES = (
    ("moralstack.observability", _REPO_ROOT / "moralstack" / "observability"),
    ("moralstack.models.risk", _REPO_ROOT / "moralstack" / "models" / "risk"),
    ("moralstack.models.risk.signals", _REPO_ROOT / "moralstack" / "models" / "risk" / "signals"),
)


def _register_placeholder_package(name: str, directory: Path) -> bool:
    """Make ``name`` importable as a package without running its ``__init__``; False when already imported."""
    if name in sys.modules:
        return False
    spec = importlib.util.spec_from_file_location(
        name, directory / "__init__.py", submodule_search_locations=[str(directory)]
    )
    if spec is None:  # pragma: no cover - the directory is part of this repository
        raise ImportError(f"cannot register {name}")
    sys.modules[name] = importlib.util.module_from_spec(spec)
    return True


def _unregister_placeholders(created: list[str]) -> None:
    """Drop the placeholders and the leaf modules loaded under them, so a later real import in the same process
    (the test suite, or anyone importing this script) runs the real package ``__init__`` instead of finding an
    empty package. This module keeps its own references to the imported functions."""
    for name in created:
        for key in [k for k in sys.modules if k == name or k.startswith(name + ".")]:
            del sys.modules[key]
        parent_name, _, child = name.rpartition(".")
        parent = sys.modules.get(parent_name)
        if parent is not None and child in vars(parent):
            delattr(parent, child)


# Only the leaf modules are needed: the package ``__init__`` files import the estimator, the orchestration types and
# the observability service (queue, sinks), none of which a read-only verifier should load.
_created_placeholders = [name for name, directory in _PLACEHOLDER_PACKAGES if _register_placeholder_package(name, directory)]
try:
    from moralstack.models.risk.signals.prompt_renderer import get_harm_signal_prompts  # noqa: E402
    from moralstack.models.risk.signals.registry import registry as _SIGNAL_REGISTRY  # noqa: E402
    from moralstack.observability.wire import REF_PROMPT, REF_SYSTEM, decode_wire, wire_sha256  # noqa: E402
    from moralstack.prompts.catalog import (  # noqa: E402
        PromptConfigError,
        PromptContext,
        PromptSet,
        default_prompt_set,
        load_prompt_set,
    )
finally:
    _unregister_placeholders(_created_placeholders)

logger = logging.getLogger("verify_prompt_audit")

STATUSES = ("ok", "missing", "mismatch", "unknown", "synthetic", "no_wire", "wire_integrity")
_OFFENDING = ("mismatch", "wire_integrity", "missing", "unknown", "no_wire")
_STRICT_ROW_STATUSES = ("mismatch", "wire_integrity")
_STRICT_RUN_STATUSES = ("mismatch", "malformed", "unrebuildable")

_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_FP = re.compile(r"^sha256:[0-9a-f]{64}$")
_WHEN_TOKENS = frozenset({"always", "never", "contract_present", "contract_absent", "history_present", "history_absent"})
_CONFIG_KEYS = (
    "schema",
    "set_fingerprint",
    "default_fingerprint",
    "ps",
    "is_default",
    "prompts_dir",
    "override_files",
    "safety_fragments_overridden",
    "fragments",
)

_CONTEXTS: tuple[PromptContext, ...] = tuple(PromptContext(c, h) for c in (False, True) for h in (False, True))


def _ctx_label(ctx: PromptContext) -> str:
    return f"c{int(ctx.contract_present)}h{int(ctx.history_present)}"


# ---------------------------------------------------------------------------------------
# Expected system prompts per (module, action)
# ---------------------------------------------------------------------------------------

# A matcher returns the set of context labels whose rendering equals the system prompt, or None when none does.
Matcher = Callable[[str], "set[str] | None"]

_SYNTHETIC_ACTION_RE = re.compile(
    r"^(leakage_detected|timeout_warning|pre_cycle_check|deliberation_cycle_|calibration_guard)"
    r"|\((SKIPPED|GATED|DISABLED|ERROR)[^)]*\)"
    r"|^generate \(speculative-reuse"
)
# Calls that are not a chat completion made by MoralStack (or have no caller): no wire by design.
_SYNTHETIC_PAIRS = frozenset(
    {
        ("embedder", "embed"),
        ("upstream_provider", "generate final response"),
        ("orchestration", "generate final response"),
        ("risk_estimator", "estimate"),
    }
)
_RETRY_FAILED_RE = re.compile(r"^retry_failed_attempt_\d+$")
# Rows that aggregate several calls (perspectives, simulator, hindsight) join the per-call pieces with this separator.
_SEGMENT_SEPARATOR = "\n---\n"


def _base_action(action: str) -> str:
    if _RETRY_FAILED_RE.match(action):
        return "retry_failed"
    return action.split(" (", 1)[0].strip()


def _variants(ps: PromptSet, fragment_id: str) -> dict[str, set[str]]:
    """``{rendered system prompt: {context labels}}`` of a composition (all 4 contexts) or a fixed text fragment."""
    if fragment_id in ps.composition_ids():
        out: dict[str, set[str]] = {}
        for ctx in _CONTEXTS:
            out.setdefault(ps.render(fragment_id, ctx), set()).add(_ctx_label(ctx))
        return out
    return {ps.text(fragment_id): {"any"}}


def _merge(*tables: dict[str, set[str]]) -> dict[str, set[str]]:
    out: dict[str, set[str]] = {}
    for table in tables:
        for text, labels in table.items():
            out.setdefault(text, set()).update(labels)
    return out


def _exact_matcher(table: dict[str, set[str]]) -> Matcher:
    def match(system: str) -> set[str] | None:
        return table.get(system)

    return match


def _template_pattern(template: str, fields: dict[str, str]) -> str:
    """Regex source for a ``str.format`` template: literals escaped, each field replaced by its pattern."""
    parts: list[str] = []
    for literal, name, _spec, _conv in string.Formatter().parse(template):
        parts.append(re.escape(literal))
        if name is not None:
            parts.append(fields.get(name, "(?:.*?)"))
    return "".join(parts)


def _prefilter_matcher(ps: PromptSet) -> Matcher:
    pattern = re.compile(_template_pattern(ps.template("retrieval.prefilter.system"), {}), re.DOTALL)

    def match(system: str) -> set[str] | None:
        return {"any"} if pattern.fullmatch(system) else None

    return match


def match_segmented(matcher: Matcher, system: str) -> set[str] | None:
    """``matcher`` on ``system``, or on every piece of an aggregated row (pieces joined by a newline, ``---``, newline).

    A piece may itself contain the separator, so the split is not by count: the first ``i`` parts are reachable
    when they can be tiled by pieces the matcher accepts.
    """
    whole = matcher(system)
    if whole is not None or _SEGMENT_SEPARATOR not in system:
        return whole
    parts = system.split(_SEGMENT_SEPARATOR)
    labels: list[set[str] | None] = [None] * (len(parts) + 1)
    labels[0] = set()
    for end in range(1, len(parts) + 1):
        for start in range(end):
            prior = labels[start]
            if prior is None:
                continue
            found = matcher(_SEGMENT_SEPARATOR.join(parts[start:end]))
            if found is not None:
                labels[end] = prior | found
                break
    return labels[len(parts)]


def _policy_matcher(ps: PromptSet, *, rewrite: bool) -> Matcher:
    """Structural check of a policy system prompt.

    ``[<contract>\\n\\n] policy.system \\n language_rules \\n anti_leak_rules <canary line>`` followed, for the
    ``rewrite`` / ``soft_revision`` actions only, by ``policy.rewrite.constraints`` (the rewrite default system plus the
    constraints is also accepted there). The canary line is ``policy.canary_instruction`` with ``§REF_<8 hex>§``;
    the runtime always builds it, so it is mandatory. ``generate`` actions never carry the constraints.
    """
    base = ps.text("policy.system") + "\n" + ps.text("policy.language_rules") + "\n" + ps.text("policy.anti_leak_rules")
    canary = _template_pattern(ps.template("policy.canary_instruction"), {"canary": "§REF_[0-9A-F]{8}§"})
    constraints = re.escape(ps.text("policy.rewrite.constraints"))
    tail = re.compile(canary + (constraints if rewrite else ""), re.DOTALL)
    rewrite_default = ps.text("policy.rewrite.default_system") + ps.text("policy.rewrite.constraints")

    def match(system: str) -> set[str] | None:
        if rewrite and system == rewrite_default:
            return {"any"}
        index = system.find(base)
        while index >= 0:
            prefix = system[:index]
            if (prefix == "" or prefix.endswith("\n\n")) and tail.fullmatch(system, index + len(base)):
                return {"any"}
            index = system.find(base, index + 1)
        return None

    return match


def _signals_variants(ps: PromptSet) -> dict[str, set[str]]:
    out: dict[str, set[str]] = {}
    for ctx in _CONTEXTS:
        system, _user = get_harm_signal_prompts(_SIGNAL_REGISTRY, prompt_set=ps, ctx=ctx)
        out.setdefault(system, set()).add(_ctx_label(ctx))
    return out


def build_matchers(ps: PromptSet) -> dict[tuple[str, str], Matcher]:
    """``{(module, base action): matcher}`` for the effective prompt set ``ps``."""
    intent = _variants(ps, "risk.intent.system")
    operational = _variants(ps, "risk.operational.system")
    signals = _signals_variants(ps)
    critic_full = _variants(ps, "critic.full.system")
    critic_quick = _variants(ps, "critic.quick.system")
    hindsight = _merge(_variants(ps, "hindsight.batch.system"), _variants(ps, "hindsight.single.system"))
    base = ps.text("perspectives.base") + "\n\n"
    perspectives = {base + text: labels for text, labels in _variants(ps, "perspectives.system_body").items()}
    simulator = _variants(ps, "simulator.batch.system")
    policy_generate = _policy_matcher(ps, rewrite=False)
    policy_rewrite = _policy_matcher(ps, rewrite=True)

    table: dict[tuple[str, str], Matcher] = {
        ("risk_estimator", "estimate_intent"): _exact_matcher(intent),
        ("risk_estimator", "estimate_operational"): _exact_matcher(operational),
        ("risk_estimator", "estimate_signals"): _exact_matcher(signals),
        ("risk_estimator", "retry_failed"): _exact_matcher(_merge(intent, operational, signals)),
        ("critic", "critique"): _exact_matcher(critic_full),
        ("critic", "quick_check"): _exact_matcher(critic_quick),
        ("critic", "retry_failed"): _exact_matcher(_merge(critic_full, critic_quick)),
        ("hindsight", "evaluate"): _exact_matcher(hindsight),
        ("hindsight", "retry_failed"): _exact_matcher(hindsight),
        ("perspectives", "evaluate"): _exact_matcher(perspectives),
        ("perspectives", "retry_failed"): _exact_matcher(perspectives),
        ("simulator", "simulate"): _exact_matcher(simulator),
        ("simulator", "retry_failed"): _exact_matcher(simulator),
        ("constitution_retriever", "domain_prefilter"): _prefilter_matcher(ps),
        ("constitution_retriever", "enhanced_domain_agent"): _exact_matcher(
            _variants(ps, "retrieval.agent.enhanced.system")
        ),
        ("constitution_retriever", "legacy_domain_agent"): _exact_matcher(_variants(ps, "retrieval.agent.legacy.system")),
        ("orchestration", "refuse"): _exact_matcher(_variants(ps, "refusal.system")),
        ("orchestration", "domain_excluded"): _exact_matcher(_variants(ps, "exclusion.system")),
        ("compliance_layer", "evaluate"): _exact_matcher(_variants(ps, "compliance.dccl.system")),
        ("compliance_layer", "safety_override"): _exact_matcher(_variants(ps, "compliance.safety_classifier.system")),
        ("compliance_layer", "draft_revalidate"): _exact_matcher(_variants(ps, "compliance.draft_match.system")),
    }
    table[("policy", "generate")] = policy_generate
    table[("upstream_speculative", "generate")] = policy_generate
    for action in ("rewrite", "soft_revision"):
        table[("policy", action)] = policy_rewrite
    return table


_DEFAULT_MATCHERS: dict[tuple[str, str], Matcher] | None = None


def _matchers_for(ps: PromptSet) -> dict[tuple[str, str], Matcher]:
    """``build_matchers(ps)``, built once per process for the packaged defaults (the common case)."""
    global _DEFAULT_MATCHERS
    default = default_prompt_set()
    if ps is not default:
        return build_matchers(ps)
    if _DEFAULT_MATCHERS is None:
        _DEFAULT_MATCHERS = build_matchers(default)
    return _DEFAULT_MATCHERS


# ---------------------------------------------------------------------------------------
# Audit record (prompt_config) checks
# ---------------------------------------------------------------------------------------


def _as_obj(value: Any) -> dict[str, Any] | None:
    """A JSON object stored either as text (SQLite / most envelopes) or already decoded; ``None`` when neither."""
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value.strip().startswith("{"):
        try:
            parsed = json.loads(value)
        except ValueError:
            return None
        return parsed if isinstance(parsed, dict) else None
    return None


def check_prompt_config(config: Any) -> list[str]:
    """Problems found in a ``prompt_config`` record's schema and hash formats (empty list = well-formed)."""
    if not isinstance(config, dict):
        return ["prompt_config is not an object"]
    problems = [f"missing key {key!r}" for key in _CONFIG_KEYS if key not in config]
    if problems:
        return problems
    if config["schema"] != 1 or isinstance(config["schema"], bool):
        problems.append(f"unsupported schema {config['schema']!r}")
    for key in ("set_fingerprint", "default_fingerprint"):
        if not isinstance(config[key], str) or not _FP.match(config[key]):
            problems.append(f"{key} is not 'sha256:<64 hex>'")
    if isinstance(config["set_fingerprint"], str) and config["ps"] != config["set_fingerprint"][7:23]:
        problems.append("ps is not the first 16 hex digits of set_fingerprint")
    if not isinstance(config["is_default"], bool):
        problems.append("is_default is not a boolean")
    if config["prompts_dir"] is not None and not isinstance(config["prompts_dir"], str):
        problems.append("prompts_dir is neither null nor a string")
    files = config["override_files"]
    if not isinstance(files, list) or not all(
        isinstance(f, dict) and isinstance(f.get("file"), str) and _HEX64.match(str(f.get("sha256", ""))) for f in files
    ):
        problems.append("override_files entries need 'file' and a 64-hex 'sha256'")
    safety = config["safety_fragments_overridden"]
    if not isinstance(safety, list) or not all(isinstance(s, str) for s in safety):
        problems.append("safety_fragments_overridden is not a list of strings")
    fragments = config["fragments"]
    if not isinstance(fragments, dict) or not fragments:
        problems.append("fragments is not a non-empty object")
        return problems
    for fid, entry in fragments.items():
        if not isinstance(entry, dict):
            problems.append(f"fragment {fid}: entry is not an object")
            continue
        if not _HEX64.match(str(entry.get("sha256", ""))):
            problems.append(f"fragment {fid}: sha256 is not 64 hex")
        if entry.get("when") not in _WHEN_TOKENS:
            problems.append(f"fragment {fid}: invalid 'when'")
        origin = entry.get("origin")
        if origin not in ("default", "override"):
            problems.append(f"fragment {fid}: invalid origin")
        elif origin == "override":
            overridden = entry.get("overridden")
            if not isinstance(overridden, list) or not overridden or not set(overridden) <= {"text", "when"}:
                problems.append(f"fragment {fid}: invalid 'overridden'")
            elif "text" in overridden and not isinstance(entry.get("text"), str):
                problems.append(f"fragment {fid}: text override without 'text'")
    return problems


def _write_overrides(config: dict[str, Any], directory: Path) -> bool:
    """Write the recorded overrides as a prompts_dir file. ``False`` when the record carries none."""
    from ruamel.yaml import YAML

    fragments: dict[str, dict[str, str]] = {}
    for fid, entry in config["fragments"].items():
        if entry.get("origin") != "override":
            continue
        item: dict[str, str] = {}
        if "text" in entry["overridden"]:
            item["text"] = entry["text"]
        if "when" in entry["overridden"]:
            item["when"] = entry["when"]
        fragments[fid] = item
    if not fragments:
        return False
    with (directory / "overrides.yaml").open("w", encoding="utf-8") as fh:
        YAML(typ="safe").dump({"version": 1, "fragments": fragments}, fh)
    return True


def rebuild_effective_set(config: dict[str, Any]) -> PromptSet:
    """The default set plus the per-fragment overridden ``text`` / ``when`` recorded in ``config``.

    Uses only the public loader (``load_prompt_set``) on a temporary ``prompts_dir``; raises ``PromptConfigError``
    when the recorded overrides do not form a valid set. The loader's WARNINGs (one per safety override) are
    muted: they were emitted when the audited run loaded the same set.
    """
    with tempfile.TemporaryDirectory(prefix="verify_prompt_audit_") as tmp:
        if not _write_overrides(config, Path(tmp)):
            return default_prompt_set()
        catalog_logger = logging.getLogger("moralstack.prompts.catalog")
        previous = catalog_logger.level
        catalog_logger.setLevel(logging.ERROR)
        try:
            return load_prompt_set(tmp)
        finally:
            catalog_logger.setLevel(previous)


@dataclass
class RunCheck:
    run_id: str
    config_status: str  # ok | pre-feature | malformed | defaults_differ | mismatch | unrebuildable
    problems: list[str] = field(default_factory=list)
    ps: str | None = None
    is_default: bool | None = None
    safety_fragments_overridden: list[str] = field(default_factory=list)
    fragment_diffs: list[str] = field(default_factory=list)
    prompt_set: PromptSet | None = None  # effective set rows are checked against (None: not checkable)
    matchers: dict[tuple[str, str], Matcher] | None = None

    @property
    def prefeature(self) -> bool:
        return self.config_status == "pre-feature"

    def to_json(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "config_status": self.config_status,
            "note": "no prompt_config (pre-feature)" if self.prefeature else None,
            "problems": self.problems,
            "ps": self.ps,
            "is_default": self.is_default,
            "safety_fragments_overridden": self.safety_fragments_overridden,
            "fragment_diffs": self.fragment_diffs,
        }


def check_run(run_id: str, config: Any) -> RunCheck:
    """Per-run checks 1-3 of plan section 14. ``config`` is ``None`` for a run without ``prompt_config``."""
    default = default_prompt_set()
    if config is None:
        # Pre-feature run: checked against the current packaged defaults.
        return RunCheck(run_id, "pre-feature", prompt_set=default, matchers=_matchers_for(default))
    problems = check_prompt_config(config)
    if problems:
        return RunCheck(run_id, "malformed", problems=problems)
    check = RunCheck(
        run_id,
        "ok",
        ps=config["ps"],
        is_default=config["is_default"],
        safety_fragments_overridden=sorted(config["safety_fragments_overridden"]),
    )
    if config["default_fingerprint"] != f"sha256:{default.default_fingerprint}":
        check.config_status = "defaults_differ"
        check.problems.append("unknown: defaults version differs (rows are not re-rendered)")
        return check
    try:
        rebuilt = rebuild_effective_set(config)
    except PromptConfigError as exc:
        check.config_status = "unrebuildable"
        check.problems.append(f"unknown: cannot rebuild the effective set ({exc.reason})")
        return check
    if f"sha256:{rebuilt.fingerprint}" != config["set_fingerprint"]:
        check.config_status = "mismatch"
        check.problems.append("rebuilt set fingerprint differs from set_fingerprint")
    recorded = config["fragments"]
    rebuilt_fragments = rebuilt.audit_summary()["fragments"]
    check.fragment_diffs = sorted(
        fid
        for fid in set(recorded) | set(rebuilt_fragments)
        if (recorded.get(fid) or {}).get("sha256") != (rebuilt_fragments.get(fid) or {}).get("sha256")
        or (recorded.get(fid) or {}).get("when") != (rebuilt_fragments.get(fid) or {}).get("when")
    )
    if check.fragment_diffs:
        check.config_status = "mismatch"
        check.problems.append(f"{len(check.fragment_diffs)} fragment(s) differ from the rebuilt set")
    check.prompt_set = rebuilt
    check.matchers = _matchers_for(rebuilt)
    return check


# ---------------------------------------------------------------------------------------
# Row checks
# ---------------------------------------------------------------------------------------


@dataclass
class RowResult:
    row_id: str
    module: str
    action: str
    status: str
    detail: str = ""
    wire: str = "none"  # none | verified | integrity_error
    branches: list[str] = field(default_factory=list)
    contexts: list[str] = field(default_factory=list)
    system_col_ne_wire: bool = False
    prompt_col_ne_wire: bool = False


def _is_synthetic(row: dict[str, Any]) -> bool:
    module, action = row["module"], row["action"]
    if (module, action) in _SYNTHETIC_PAIRS or _SYNTHETIC_ACTION_RE.search(action):
        return True
    if row.get("billable_provider_call") == 0:
        return True
    return module == "orchestration" and action.startswith("refuse") and row.get("attempts") == 0


def _wire_holder(row: dict[str, Any]) -> dict[str, Any] | None:
    """The object carrying the row's wire keys: the summary, or ``parsed_json`` (critic critique rows)."""
    summary = _as_obj(row.get("parsed_summary_json"))
    for holder in (summary, _as_obj(row.get("parsed_json"))):
        if holder and ("wire" in holder or "wire_calls" in holder):
            return holder
    return None


def _decode_records(
    holder: dict[str, Any], prompt_col: str, system_col: str
) -> tuple[list[tuple[str, list[dict[str, str]]]], list[str]]:
    """``([(branch, decoded messages)], integrity errors)`` for every record the row carries."""
    decoded: list[tuple[str, list[dict[str, str]]]] = []
    errors: list[str] = []
    if isinstance(holder.get("wire"), dict):
        records: list[Any] = [holder["wire"]]
        strings = None
    else:
        records = list(holder["wire_calls"]) if isinstance(holder.get("wire_calls"), list) else []
        strings = holder.get("wire_strings")
        if not isinstance(strings, list):
            errors.append("wire_calls without a wire_strings table")
            return decoded, errors
    if not records:
        errors.append("empty wire_calls")
    if strings is not None and not all(isinstance(item, str) for item in strings):
        errors.append("wire_strings holds a non-string entry")
        return decoded, errors
    for position, rec in enumerate(records):
        label = f"record {position}"
        if not isinstance(rec, dict):
            errors.append(f"{label}: not an object")
            continue
        try:
            messages = decode_wire(rec, prompt_col=prompt_col, system_prompt_col=system_col, strings=strings)
        except ValueError as exc:
            errors.append(f"{label}: {exc}")
            continue
        if not all(isinstance(m["content"], str) for m in messages):
            errors.append(f"{label}: a decoded message is not a string")
            continue
        columns = {REF_SYSTEM: system_col, REF_PROMPT: prompt_col}
        unresolved = [m["ref"] for m in rec["messages"] if "ref" in m and not columns.get(m["ref"])]
        if unresolved:
            errors.append(f"{label}: ref {unresolved[0]!r} resolves against an empty column")
        if rec.get("n") != len(messages):
            errors.append(f"{label}: n={rec.get('n')!r} but {len(messages)} messages")
        if wire_sha256(messages) != rec.get("sha256"):
            errors.append(f"{label}: sha256 does not match the decoded messages")
        decoded.append((str(rec.get("branch", "")), messages))
    return decoded, errors


def classify_row(row: dict[str, Any], run: RunCheck) -> RowResult:
    """Classify one ``llm_calls`` row. Never raises on malformed data: it is reported, not guessed."""
    module, action = row["module"], row["action"]
    result = RowResult(str(row["id"]), module, action, "ok")
    if _is_synthetic(row):
        result.status = "synthetic"
        return result

    prompt_col = row.get("prompt") or ""
    system_col = row.get("system_prompt") or ""
    holder = _wire_holder(row)
    decoded: list[tuple[str, list[dict[str, str]]]] = []
    if holder is not None:
        decoded, errors = _decode_records(holder, prompt_col, system_col)
        if errors:
            result.wire = "integrity_error"
            result.status = "wire_integrity"
            result.detail = "; ".join(errors)
            return result
        result.wire = "verified"
        result.branches = [branch for branch, _ in decoded]

    # Only the leading system message is MoralStack's own; later system messages are the conversation's (DCCL native).
    wire_systems = [messages[0]["content"] for _, messages in decoded if messages and messages[0]["role"] == "system"]
    if wire_systems and system_col and wire_systems[0] != system_col:
        result.system_col_ne_wire = True
    if (
        decoded
        and prompt_col
        and not any(m["content"] == prompt_col for _, ms in decoded for m in ms if m["role"] == "user")
    ):
        result.prompt_col_ne_wire = True

    if run.matchers is None:
        result.status = "unknown"
        result.detail = "; ".join(run.problems) or "run configuration not checkable"
        return result
    matcher = run.matchers.get((module, _base_action(action)))
    if matcher is None:
        result.status = "unknown"
        result.detail = "no expected-system matcher for this module/action"
        return result

    candidates = ([("system_prompt column", system_col)] if system_col else []) + [
        ("wire system message", text) for text in wire_systems
    ]
    if not candidates:
        result.status = "no_wire" if holder is None else "missing"
        result.detail = "no system prompt recorded" + ("" if holder is None else " (wire has no system message)")
        return result
    contexts: set[str] = set()
    for where, text in candidates:
        labels = match_segmented(matcher, text)
        if labels is None:
            result.status = "mismatch"
            result.detail = f"{where} is not an expected system prompt for {module}/{action}"
            return result
        contexts |= labels
    result.contexts = sorted(contexts)
    return result


# ---------------------------------------------------------------------------------------
# Sources
# ---------------------------------------------------------------------------------------


@dataclass
class RequestLink:
    run_id: str
    request_id: str
    link: Any  # the request meta's ``prompt_set`` value, or None


class Source:
    """What the checks need from a data source: runs, per-run rows and per-run request links."""

    name: str

    def run_ids(self) -> list[str]:
        raise NotImplementedError

    def run_config(self, run_id: str) -> Any:
        """The run's ``prompt_config`` record, or ``None``."""
        raise NotImplementedError

    def rows(self, run_id: str) -> Iterator[dict[str, Any]]:
        raise NotImplementedError

    def requests(self, run_id: str) -> list[RequestLink]:
        raise NotImplementedError

    def close(self) -> None:
        return None


def _config_from_meta(meta: Any) -> Any:
    obj = _as_obj(meta)
    return obj.get("prompt_config") if obj else None


class SqliteSource(Source):
    def __init__(self, path: Path, jsonl: JsonlStore | None = None) -> None:
        self.name = f"db:{path}"
        self._jsonl = jsonl
        uri = path.resolve().as_uri() + "?mode=ro"
        self._conn = sqlite3.connect(uri, uri=True)
        self._conn.execute("PRAGMA query_only = ON")
        self._conn.row_factory = sqlite3.Row
        self._llm_columns = {r["name"] for r in self._conn.execute("PRAGMA table_info(llm_calls)")}
        if not self._llm_columns:
            self._conn.close()
            raise ValueError("table llm_calls not found")

    def close(self) -> None:
        self._conn.close()

    def run_ids(self) -> list[str]:
        return [r["run_id"] for r in self._conn.execute("SELECT run_id FROM runs ORDER BY started_at, run_id")]

    def run_config(self, run_id: str) -> Any:
        row = self._conn.execute("SELECT meta_json FROM runs WHERE run_id = ?", (run_id,)).fetchone()
        config = _config_from_meta(row["meta_json"]) if row else None
        if config is None and self._jsonl is not None:
            config = self._jsonl.run_config(run_id)
        return config

    def rows(self, run_id: str) -> Iterator[dict[str, Any]]:
        wanted = (
            "id",
            "request_id",
            "module",
            "action",
            "prompt",
            "system_prompt",
            "parsed_json",
            "parsed_summary_json",
            "attempts",
            "billable_provider_call",
        )
        select = ", ".join(c if c in self._llm_columns else f"NULL AS {c}" for c in wanted)
        cursor = self._conn.execute(f"SELECT {select} FROM llm_calls WHERE run_id = ? ORDER BY id", (run_id,))
        for r in cursor:
            yield dict(r)

    def requests(self, run_id: str) -> list[RequestLink]:
        fallback = self._jsonl.request_links(run_id) if self._jsonl is not None else {}
        out: list[RequestLink] = []
        for r in self._conn.execute(
            "SELECT request_id, meta_json FROM requests WHERE run_id = ? ORDER BY created_at", (run_id,)
        ):
            meta = _as_obj(r["meta_json"]) or {}
            link = meta.get("prompt_set")
            if link is None:
                link = fallback.get(r["request_id"])
            out.append(RequestLink(run_id, r["request_id"], link))
        return out


_JSONL_EVENTS = ("llm.call", "run.started", "request.upserted", "request.meta_updated")


def _jsonl_files(directory: Path, event: str) -> list[Path]:
    """``<event>.jsonl`` and rotated ``<event>.*.jsonl`` files; other files (e.g. a benchmark_<run>.json) are ignored."""
    found = sorted(
        p for p in directory.glob(f"{event}*.jsonl") if p.name == f"{event}.jsonl" or p.name.startswith(f"{event}.")
    )
    return found


def _read_jsonl(paths: Iterable[Path]) -> Iterator[dict[str, Any]]:
    for path in paths:
        with path.open(encoding="utf-8") as fh:
            for line_no, line in enumerate(fh, 1):
                line = line.strip()
                if not line:
                    continue
                try:
                    env = json.loads(line)
                except ValueError:
                    logger.warning("malformed JSONL line %d in %s: skipped", line_no, path)
                    continue
                if isinstance(env, dict):
                    yield env


def _payload(env: dict[str, Any]) -> dict[str, Any]:
    payload = env.get("payload")
    return payload if isinstance(payload, dict) else {}


def _load_consolidate() -> Callable[[list[dict[str, Any]]], dict[str, dict[str, Any]]]:
    spec = importlib.util.spec_from_file_location(
        "_consolidate_jsonl_meta", Path(__file__).with_name("consolidate_jsonl_meta.py")
    )
    if spec is None or spec.loader is None:  # pragma: no cover - the file sits next to this script
        raise ImportError("consolidate_jsonl_meta.py not found next to verify_prompt_audit.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.consolidate  # type: ignore[no-any-return]


class JsonlStore:
    """The JSONL channel of a ``jsonl_dir``: ``run.started`` configs, consolidated request meta, ``llm.call`` rows."""

    def __init__(self, directory: Path) -> None:
        if not directory.is_dir():
            raise ValueError(f"not a directory: {directory}")
        self.directory = directory
        self._configs: dict[str, Any] = {}
        self._run_order: list[str] = []
        for env in _read_jsonl(_jsonl_files(directory, "run.started")):
            run_id = env.get("run_id") or (env.get("payload") or {}).get("run_id")
            if not run_id or run_id in self._configs:
                continue  # first line wins (a second line follows guard eviction)
            self._run_order.append(run_id)
            payload = _payload(env)
            self._configs[run_id] = _config_from_meta(payload.get("meta"))
        meta_envelopes = list(_read_jsonl(_jsonl_files(directory, "request.upserted"))) + list(
            _read_jsonl(_jsonl_files(directory, "request.meta_updated"))
        )
        self._llm_by_run: dict[str, list[dict[str, Any]]] | None = None
        self._request_meta = _load_consolidate()(meta_envelopes)
        self._requests: dict[str, list[str]] = {}
        for env in meta_envelopes:
            run_id, request_id = env.get("run_id"), env.get("request_id")
            if run_id and request_id and request_id not in self._requests.setdefault(run_id, []):
                self._requests[run_id].append(request_id)

    def run_ids(self) -> list[str]:
        return list(self._run_order)

    def run_config(self, run_id: str) -> Any:
        return self._configs.get(run_id)

    def request_links(self, run_id: str) -> dict[str, Any]:
        return {
            rid: (self._request_meta.get(f"{run_id}:{rid}") or {}).get("prompt_set")
            for rid in self._requests.get(run_id, [])
        }

    def _group_llm_rows(self) -> dict[str, list[dict[str, Any]]]:
        """One pass over every ``llm.call`` file, rows grouped by run (read lazily, on the first run checked)."""
        grouped: dict[str, list[dict[str, Any]]] = {}
        for env in _read_jsonl(_jsonl_files(self.directory, "llm.call")):
            run_id = env.get("run_id")
            if not isinstance(run_id, str):
                continue
            p = _payload(env)
            grouped.setdefault(run_id, []).append(
                {
                    "id": env.get("event_id", ""),
                    "request_id": env.get("request_id"),
                    "module": p.get("module", ""),
                    "action": p.get("action", ""),
                    "prompt": p.get("prompt", ""),
                    "system_prompt": p.get("system_prompt", ""),
                    "parsed_json": p.get("parsed_json"),
                    "parsed_summary_json": p.get("parsed_summary_json"),
                    "attempts": p.get("attempts"),
                    "billable_provider_call": (
                        None if p.get("billable_provider_call") is None else int(bool(p["billable_provider_call"]))
                    ),
                }
            )
        return grouped

    def llm_rows(self, run_id: str) -> Iterator[dict[str, Any]]:
        if self._llm_by_run is None:
            self._llm_by_run = self._group_llm_rows()
        yield from self._llm_by_run.get(run_id, [])


class JsonlSource(Source):
    def __init__(self, store: JsonlStore) -> None:
        self.name = f"jsonl:{store.directory}"
        self._store = store

    def run_ids(self) -> list[str]:
        return self._store.run_ids()

    def run_config(self, run_id: str) -> Any:
        return self._store.run_config(run_id)

    def rows(self, run_id: str) -> Iterator[dict[str, Any]]:
        return self._store.llm_rows(run_id)

    def requests(self, run_id: str) -> list[RequestLink]:
        return [RequestLink(run_id, rid, link) for rid, link in self._store.request_links(run_id).items()]


# ---------------------------------------------------------------------------------------
# Verification and report
# ---------------------------------------------------------------------------------------


@dataclass
class ModuleStats:
    counts: Counter[str] = field(default_factory=Counter)
    offending: dict[str, list[str]] = field(default_factory=dict)
    details: dict[str, str] = field(default_factory=dict)  # offending row id -> why
    with_wire: int = 0
    branches: Counter[str] = field(default_factory=Counter)
    system_col_ne_wire: int = 0
    prompt_col_ne_wire: int = 0

    def add(self, result: RowResult) -> None:
        self.counts[result.status] += 1
        if result.status in _OFFENDING:
            self.offending.setdefault(result.status, []).append(result.row_id)
            if result.detail:
                self.details[result.row_id] = result.detail
        if result.wire != "none":
            self.with_wire += 1
        self.branches.update(result.branches)
        self.system_col_ne_wire += int(result.system_col_ne_wire)
        self.prompt_col_ne_wire += int(result.prompt_col_ne_wire)


@dataclass
class RunReport:
    check: RunCheck
    totals: Counter[str] = field(default_factory=Counter)
    requests: Counter[str] = field(default_factory=Counter)  # ok | missing | mismatch | pre-feature
    request_offending: dict[str, list[str]] = field(default_factory=dict)


@dataclass
class SourceReport:
    name: str
    runs: list[RunReport] = field(default_factory=list)
    modules: dict[str, ModuleStats] = field(default_factory=dict)


def _verify_requests(report: RunReport, source: Source) -> None:
    for req in source.requests(report.check.run_id):
        if report.check.prefeature:
            status = "pre-feature"
        elif report.check.ps is None:
            status = "missing" if req.link is None else "ok"
        elif not isinstance(req.link, dict) or "ps" not in req.link:
            status = "missing"
        else:
            status = "ok" if req.link["ps"] == report.check.ps else "mismatch"
        report.requests[status] += 1
        if status in ("missing", "mismatch"):
            report.request_offending.setdefault(status, []).append(req.request_id)


def verify_source(source: Source, run_ids: list[str] | None = None) -> SourceReport:
    report = SourceReport(source.name)
    selected = source.run_ids()
    if run_ids:
        selected = [r for r in selected if r in set(run_ids)]
    for run_id in selected:
        check = check_run(run_id, source.run_config(run_id))
        run_report = RunReport(check)
        for row in source.rows(run_id):
            result = classify_row(row, check)
            run_report.totals[result.status] += 1
            report.modules.setdefault(result.module, ModuleStats()).add(result)
        _verify_requests(run_report, source)
        report.runs.append(run_report)
    return report


def strict_failures(reports: list[SourceReport]) -> int:
    """Number of mismatches and integrity errors (rows, runs, requests) that ``--strict`` turns into exit code 1."""
    total = 0
    for rep in reports:
        for run in rep.runs:
            total += int(run.check.config_status in _STRICT_RUN_STATUSES)
            total += sum(run.totals[s] for s in _STRICT_ROW_STATUSES)
            total += run.requests["mismatch"]
    return total


def report_to_json(reports: list[SourceReport], missing_run_ids: list[str]) -> dict[str, Any]:
    def module_json(stats: ModuleStats) -> dict[str, Any]:
        return {
            "counts": {s: stats.counts.get(s, 0) for s in STATUSES},
            "offending_rows": stats.offending,
            "offending_row_details": stats.details,
            "rows_with_wire": stats.with_wire,
            "branches": dict(stats.branches),
            "informational": {
                "system_column_differs_from_wire": stats.system_col_ne_wire,
                "prompt_column_not_in_wire": stats.prompt_col_ne_wire,
            },
        }

    return {
        "sources": [
            {
                "source": rep.name,
                "runs": [
                    {
                        **run.check.to_json(),
                        "rows": {s: run.totals.get(s, 0) for s in STATUSES},
                        "requests": dict(run.requests),
                        "offending_requests": run.request_offending,
                    }
                    for run in rep.runs
                ],
                "modules": {name: module_json(stats) for name, stats in sorted(rep.modules.items())},
            }
            for rep in reports
        ],
        "run_ids_not_found": missing_run_ids,
        "strict_failures": strict_failures(reports),
        "legend": _LEGEND,
    }


_LEGEND = {
    "ok": "the recorded system prompt (column and wire system message) is one the effective set renders for the action",
    "missing": "a wire record exists but carries no system message and the column is empty",
    "mismatch": "a recorded system prompt is not what the effective set renders (tampered, or a different set)",
    "unknown": "no matcher for the module/action, or the run's set cannot be rebuilt (defaults differ / unrebuildable; "
    "an unrebuildable run is itself a strict failure)",
    "synthetic": "not a chat completion (orchestration notes, reused draft, timeouts, calibration guard, "
    "skipped/gated/disabled/error markers, embedder, rows with billable_provider_call=0 or a refusal with "
    "attempts=0): no record by design",
    "no_wire": "no system prompt recorded and no wire record (pre-feature rows, retry-failed rows written before the wire)",
    "wire_integrity": "the wire record does not decode, a ref does not resolve against a non-empty column, "
    "or the sha256 differs",
    "notes": [
        "Row ids are llm_calls.id in --db mode and the envelope event_id in --jsonl-dir mode.",
        "branch=generate is only proven for OpenAIPolicy / UpstreamDraftGenerator; branch counts are reported, "
        "never failed on.",
        "rewrite / soft_revision wires are adapter-attested (taken from the adapter's own prompt_used / system_used).",
        "Column equality (system_prompt == decoded system, prompt == decoded user) is informational: it is false by design "
        "for G13 and retry-failed rows, dccl/generate, speculative/messages, and prefix/suffix refs.",
        "A row with a wire record counts under 'rows_with_wire'; a missing wire never fails a row by itself.",
    ],
}


def _cap(ids: list[str], limit: int = 12) -> str:
    shown = ", ".join(ids[:limit])
    return shown + (f" (+{len(ids) - limit} more)" if len(ids) > limit else "")


def _short(text: str, limit: int = 80) -> str:
    return text if len(text) <= limit else text[: limit - 3] + "..."


def render_text(reports: list[SourceReport], missing_run_ids: list[str]) -> str:
    lines: list[str] = []
    for rep in reports:
        lines.append(f"== {rep.name}")
        for run in rep.runs:
            check = run.check
            tag = "no prompt_config (pre-feature)" if check.prefeature else f"prompt_config {check.config_status}"
            lines.append(
                f"-- run {check.run_id}: {tag}" + (f", ps={check.ps}, is_default={check.is_default}" if check.ps else "")
            )
            for problem in check.problems:
                lines.append(f"     ! {problem}")
            if check.fragment_diffs:
                lines.append(f"     ! differing fragments: {_cap(check.fragment_diffs)}")
            if check.ps is not None:
                lines.append(
                    f"     safety_fragments_overridden ({len(check.safety_fragments_overridden)}): "
                    + (", ".join(check.safety_fragments_overridden) or "none")
                )
            lines.append("     rows: " + ", ".join(f"{s}={run.totals.get(s, 0)}" for s in STATUSES))
            lines.append("     requests: " + (", ".join(f"{k}={v}" for k, v in sorted(run.requests.items())) or "none"))
            for status, ids in sorted(run.request_offending.items()):
                lines.append(f"       {status} requests: {_cap(ids)}")
        lines.append("")
        header = f"{'module':<26}" + "".join(f"{s:>15}" for s in STATUSES) + f"{'with_wire':>11}"
        lines.append(header)
        for name, stats in sorted(rep.modules.items()):
            lines.append(
                f"{name:<26}" + "".join(f"{stats.counts.get(s, 0):>15}" for s in STATUSES) + f"{stats.with_wire:>11}"
            )
        for name, stats in sorted(rep.modules.items()):
            for status in _OFFENDING:
                if status in stats.offending:
                    labelled = [
                        f"{rid} ({_short(stats.details[rid])})" if rid in stats.details else rid
                        for rid in stats.offending[status]
                    ]
                    lines.append(f"  {name} {status} rows: {_cap(labelled)}")
        branch_totals: Counter[str] = Counter()
        for stats in rep.modules.values():
            branch_totals.update(stats.branches)
        if branch_totals:
            lines.append("  wire branches: " + ", ".join(f"{k}={v}" for k, v in sorted(branch_totals.items())))
        lines.append("")
    if missing_run_ids:
        lines.append("run ids not found: " + ", ".join(missing_run_ids))
    lines.append(
        "strict failures (mismatch + wire_integrity + run mismatch/malformed/unrebuildable + request mismatch): "
        f"{strict_failures(reports)}"
    )
    return "\n".join(lines)


# ---------------------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------------------


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Read-only verifier of the MoralStack prompt audit.")
    parser.add_argument("--db", type=Path, help="SQLite observability DB (opened read-only).")
    parser.add_argument("--run-id", action="append", default=[], help="Check only this run id (repeatable).")
    parser.add_argument(
        "--jsonl-dir",
        type=Path,
        help="Observability JSONL directory. Without --db it is the data source; with --db it supplies the run "
        "prompt_config and the request link when the DB lacks them.",
    )
    parser.add_argument("--json", action="store_true", help="Machine-readable output.")
    parser.add_argument("--strict", action="store_true", help="Exit 1 on any mismatch or wire-integrity error.")
    args = parser.parse_args(argv)
    if args.db is None and args.jsonl_dir is None:
        parser.error("one of --db / --jsonl-dir is required")
    return args


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")
    sources: list[Source] = []
    try:
        store = JsonlStore(args.jsonl_dir) if args.jsonl_dir is not None else None
        if args.db is not None:
            if not args.db.is_file():
                raise ValueError(f"not a file: {args.db}")
            sources.append(SqliteSource(args.db, store))
        elif store is not None:
            sources.append(JsonlSource(store))
        reports = [verify_source(src, args.run_id) for src in sources]
    except (ValueError, sqlite3.Error, OSError) as exc:
        print(f"verify_prompt_audit: cannot read input: {exc}", file=sys.stderr)
        return 2
    finally:
        for src in sources:
            src.close()

    found = {run.check.run_id for rep in reports for run in rep.runs}
    missing = [r for r in args.run_id if r not in found]
    if args.json:
        print(json.dumps(report_to_json(reports, missing), ensure_ascii=False, indent=2))
    else:
        text = render_text(reports, missing)
        sys.stdout.buffer.write((text + "\n").encode("utf-8", errors="replace"))
    if args.strict and strict_failures(reports):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
