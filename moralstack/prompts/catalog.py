"""Prompt catalog: packaged default fragments, deployer overrides, ``when`` conditions, audit summary.

Every LLM-facing MoralStack prompt text is a named *fragment* in ``moralstack/prompts/data/*.yaml``.
*Compositions* (defined only in the packaged defaults) rebuild today's concatenated prompts from
fragments. A deployer may override the ``text`` and/or the ``when`` condition of existing fragments
from a ``prompts_dir``; the structure (IDs, kinds, placeholders, compositions) is not overridable.

Design notes:

- Fail fast. A broken packaged file or a broken override raises ``PromptConfigError`` when the set is
  loaded (process startup / pipeline build), never at request time: placeholders and IDs are validated
  at load and every composition is pre-rendered for the four ``PromptContext`` values.
- Brace form is wire form for ``text`` fragments: what the YAML holds is what the model receives.
  ``template`` fragments are ``str.format`` strings (``{{``/``}}`` give literal braces).
- A composition's terminal whitespace always lives in its ``end``, never in the last fragment, so
  dropping the last part under ``when`` never loses the final newline.
- The set is immutable and there is no programmatic override API; the only way to build a
  non-default set is ``load_prompt_set(dir)``.
- This module imports nothing from ``moralstack.*`` (no import cycles; enforced by a test).
"""

from __future__ import annotations

import copy
import hashlib
import json
import logging
import os
import re
import string
import threading
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import TYPE_CHECKING, Any

logger = logging.getLogger(__name__)

ENV_PROMPTS_DIR = "MORALSTACK_PROMPTS_DIR"

_DATA_DIR = Path(__file__).parent / "data"

_ID_RE = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z0-9_]+)+$")
_WS_ONLY_RE = re.compile(r"[ \t\n]*")

_KINDS = frozenset({"text", "template", "lines"})
_WHEN_TOKENS = frozenset({"always", "never", "contract_present", "contract_absent", "history_present", "history_absent"})
_TRANSFORMS = frozenset({"double_braces"})

_DEFAULT_FILE_KEYS = frozenset({"version", "fragments", "compositions"})
_OVERRIDE_FILE_KEYS = frozenset({"version", "fragments"})
_DEFAULT_FRAGMENT_KEYS = frozenset({"text", "kind", "placeholders", "when"})
_OVERRIDE_FRAGMENT_KEYS = frozenset({"text", "when"})
_COMPOSITION_KEYS = frozenset({"context_aware", "parts", "end"})
_PART_KEYS = frozenset({"ref", "sep", "transform"})

# Packaged IDs under these prefixes make the models report (or act on) safety-relevant signals.
_SAFETY_PREFIXES = ("risk.", "critic.", "compliance.", "refusal.")
_SAFETY_EXPLICIT_IDS = frozenset(
    {
        "policy.safe_complete_instruction",
        "policy.constrained_instruction",
        "policy.hard_violation_guidance",
        "policy.hard_violation_guidance.unspecified",
        "policy.canary_instruction",
        "policy.anti_leak_rules",
        "retrieval.prefilter.system",  # SAFETY RECALL section
    }
)

_AUDIT_SCHEMA = 1


class PromptConfigError(Exception):
    """A prompt file or ``prompts_dir`` is invalid.

    Attributes:
        reason: what is wrong (never contains fragment text).
        path: the offending file or directory, when known.
        fragment_id: the offending fragment or composition ID, when known.
    """

    def __init__(self, reason: str, *, path: Path | str | None = None, fragment_id: str | None = None) -> None:
        self.reason = reason
        self.path = str(path) if path is not None else None
        self.fragment_id = fragment_id
        parts = [reason]
        if fragment_id:
            parts.append(f"fragment={fragment_id}")
        if self.path:
            parts.append(f"file={self.path}")
        super().__init__(" | ".join(parts))


# ---------------------------------------------------------------------------------------
# Evaluation context for ``when``
# ---------------------------------------------------------------------------------------


@dataclass(frozen=True)
class PromptContext:
    """Call-scoped facts a ``when`` condition can test.

    ``contract_present`` is text based: a developer contract whose text is non-empty after
    ``str.strip()`` (the same predicate as ``DeveloperContract.is_empty``). ``history_present`` is the
    plain truthiness of the conversation history the call site received.
    """

    contract_present: bool = False
    history_present: bool = False

    @classmethod
    def from_contract_text(cls, text: str | None, history: Any) -> PromptContext:
        return cls(
            contract_present=bool(isinstance(text, str) and text.strip()),
            history_present=bool(history),
        )

    @classmethod
    def from_contract(cls, contract: Any | None, history: Any) -> PromptContext:
        """Duck-typed on ``raw_text`` so this module needs no import from ``orchestration``."""
        raw = getattr(contract, "raw_text", None) if contract is not None else None
        return cls(
            contract_present=bool(isinstance(raw, str) and raw.strip()),
            history_present=bool(history),
        )


NO_CONTEXT = PromptContext()
_ALL_CONTEXTS: tuple[PromptContext, ...] = tuple(PromptContext(c, h) for c in (False, True) for h in (False, True))


def _when_holds(token: str, ctx: PromptContext) -> bool:
    if token == "always":
        return True
    if token == "never":
        return False
    if token == "contract_present":
        return ctx.contract_present
    if token == "contract_absent":
        return not ctx.contract_present
    if token == "history_present":
        return ctx.history_present
    return not ctx.history_present  # history_absent


# ---------------------------------------------------------------------------------------
# Internal model
# ---------------------------------------------------------------------------------------


@dataclass(frozen=True)
class _Fragment:
    id: str
    kind: str
    text: str
    placeholders: tuple[str, ...]
    when: str
    origin: str  # "default" | "override"
    override_file: str | None = None
    overridden: tuple[str, ...] = ()
    missing_placeholders: tuple[str, ...] = ()


@dataclass(frozen=True)
class _Part:
    ref: str
    sep: str = ""
    transform: str = ""


@dataclass(frozen=True)
class _Composition:
    id: str
    context_aware: bool
    parts: tuple[_Part, ...]
    end: str


def _is_safety_id(fragment_id: str) -> bool:
    return fragment_id.startswith(_SAFETY_PREFIXES) or fragment_id in _SAFETY_EXPLICIT_IDS


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _double_braces(text: str) -> str:
    return text.replace("{", "{{").replace("}", "}}")


def _render_composition(comp: _Composition, frags: Mapping[str, _Fragment], ctx: PromptContext) -> str:
    out: list[str] = []
    for part in comp.parts:
        frag = frags[part.ref]
        if not _when_holds(frag.when, ctx):
            continue
        text = _double_braces(frag.text) if part.transform == "double_braces" else frag.text
        if out:
            out.append(part.sep)
        out.append(text)
    if not out:
        return ""
    out.append(comp.end)
    return "".join(out)


def _fingerprint(frags: Mapping[str, _Fragment], comps: Mapping[str, _Composition]) -> str:
    payload = {
        "fragments": {fid: [f.kind, f.when, f.text] for fid, f in frags.items()},
        "compositions": {
            cid: [c.context_aware, [[p.ref, p.sep, p.transform] for p in c.parts], c.end] for cid, c in comps.items()
        },
    }
    blob = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------------------
# YAML reading and validation helpers
# ---------------------------------------------------------------------------------------


def _new_yaml() -> Any:
    """A safe-mode YAML parser (never executes tags). ruamel is imported lazily, on the first load."""
    from ruamel.yaml import YAML

    return YAML(typ="safe")


def _read_yaml_file(path: Path, yaml: Any = None) -> tuple[dict[str, Any], str]:
    """Return ``(mapping, sha256 of the LF-normalized text)``. Never echoes file content in errors.

    ``yaml`` is a parser from ``_new_yaml()``; one instance is shared by all files of a load.
    """
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise PromptConfigError(f"cannot read file ({type(exc).__name__})", path=path) from exc
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise PromptConfigError("file is not valid UTF-8", path=path) from exc
    # Same result as a text-mode (universal newlines) read, so CRLF checkouts load the same content.
    text = text.replace("\r\n", "\n").replace("\r", "\n")

    from ruamel.yaml.error import YAMLError

    if yaml is None:
        yaml = _new_yaml()
    try:
        data = yaml.load(text)
    except YAMLError as exc:
        mark = getattr(exc, "problem_mark", None)
        where = f" at line {mark.line + 1} column {mark.column + 1}" if mark is not None else ""
        raise PromptConfigError(f"invalid YAML ({type(exc).__name__}){where}", path=path) from exc
    if data is None:
        raise PromptConfigError("file is empty", path=path)
    if not isinstance(data, dict):
        raise PromptConfigError("top level must be a mapping", path=path)
    return data, _sha256_text(text)


def _check_version(data: Mapping[str, Any], path: Path) -> None:
    version = data.get("version")
    if type(version) is not int or version != 1:
        raise PromptConfigError("missing or unsupported 'version' (expected 1)", path=path)


def _check_keys(entry: Mapping[Any, Any], allowed: frozenset[str], *, what: str, path: Path, fid: str | None = None) -> None:
    for key in entry:
        if not isinstance(key, str) or key not in allowed:
            raise PromptConfigError(f"unknown key in {what}", path=path, fragment_id=fid)


def _check_fragment_id(fid: Any, path: Path) -> str:
    if not isinstance(fid, str) or not _ID_RE.match(fid):
        raise PromptConfigError(f"invalid fragment/composition ID {fid!r}", path=path)
    return fid


def _check_when(token: Any, *, path: Path, fid: str) -> str:
    if not isinstance(token, str) or token not in _WHEN_TOKENS:
        raise PromptConfigError("invalid 'when' token", path=path, fragment_id=fid)
    return token


def _template_fields(text: str, *, path: Path, fid: str) -> set[str]:
    """Fields used by a ``str.format`` template. Only bare ``{identifier}`` is allowed."""
    fields: set[str] = set()
    try:
        parsed = list(string.Formatter().parse(text))
    except ValueError as exc:
        raise PromptConfigError("malformed braces in template", path=path, fragment_id=fid) from exc
    for _literal, name, spec, conversion in parsed:
        if name is None:
            continue
        if not name.isidentifier() or conversion is not None or spec:
            raise PromptConfigError(
                "unsupported template field (only bare {identifier} replacement fields are allowed)",
                path=path,
                fragment_id=fid,
            )
        fields.add(name)
    return fields


def _check_lines(text: str, *, path: Path, fid: str) -> None:
    if text == "" or any(item == "" for item in text.split("\n")):
        raise PromptConfigError(
            "'lines' fragment has an empty item (one item per line; use '|-' so there is no trailing newline)",
            path=path,
            fragment_id=fid,
        )


def _parse_default_fragment(fid: str, entry: Any, path: Path) -> _Fragment:
    if not isinstance(entry, dict):
        raise PromptConfigError("fragment entry must be a mapping", path=path, fragment_id=fid)
    _check_keys(entry, _DEFAULT_FRAGMENT_KEYS, what="fragment", path=path, fid=fid)
    text = entry.get("text")
    if not isinstance(text, str):
        raise PromptConfigError("'text' is required and must be a string", path=path, fragment_id=fid)
    kind = entry.get("kind", "text")
    if kind not in _KINDS:
        raise PromptConfigError("invalid 'kind'", path=path, fragment_id=fid)
    when = _check_when(entry.get("when", "always"), path=path, fid=fid)
    raw_placeholders = entry.get("placeholders")
    placeholders: tuple[str, ...] = ()
    if kind == "template":
        if not isinstance(raw_placeholders, list) or not all(
            isinstance(p, str) and p.isidentifier() for p in raw_placeholders
        ):
            raise PromptConfigError(
                "template fragments must declare 'placeholders' as a list of identifiers", path=path, fragment_id=fid
            )
        placeholders = tuple(raw_placeholders)
        if len(set(placeholders)) != len(placeholders):
            raise PromptConfigError("duplicate entry in 'placeholders'", path=path, fragment_id=fid)
        used = _template_fields(text, path=path, fid=fid)
        if used != set(placeholders):
            raise PromptConfigError(
                "template fields do not match the declared 'placeholders' exactly", path=path, fragment_id=fid
            )
    else:
        if raw_placeholders is not None:
            raise PromptConfigError("'placeholders' is only valid on template fragments", path=path, fragment_id=fid)
        if kind == "lines":
            _check_lines(text, path=path, fid=fid)
    return _Fragment(fid, kind, text, placeholders, when, "default")


def _parse_composition(cid: str, entry: Any, path: Path) -> _Composition:
    if not isinstance(entry, dict):
        raise PromptConfigError("composition entry must be a mapping", path=path, fragment_id=cid)
    _check_keys(entry, _COMPOSITION_KEYS, what="composition", path=path, fid=cid)
    aware = entry.get("context_aware", False)
    if not isinstance(aware, bool):
        raise PromptConfigError("'context_aware' must be a boolean", path=path, fragment_id=cid)
    end = entry.get("end", "")
    if not isinstance(end, str) or not _WS_ONLY_RE.fullmatch(end):
        raise PromptConfigError("'end' must be whitespace only (spaces, tabs, newlines)", path=path, fragment_id=cid)
    raw_parts = entry.get("parts")
    if not isinstance(raw_parts, list) or not raw_parts:
        raise PromptConfigError("'parts' must be a non-empty list", path=path, fragment_id=cid)
    parts: list[_Part] = []
    for raw in raw_parts:
        if isinstance(raw, str):
            ref, sep, transform = raw, "", ""
        elif isinstance(raw, dict):
            _check_keys(raw, _PART_KEYS, what="composition part", path=path, fid=cid)
            raw_ref = raw.get("ref")
            sep = raw.get("sep", "")
            transform = raw.get("transform", "")
            if not isinstance(raw_ref, str):
                raise PromptConfigError("composition part needs a string 'ref'", path=path, fragment_id=cid)
            ref = raw_ref
            if not isinstance(sep, str) or not _WS_ONLY_RE.fullmatch(sep):
                raise PromptConfigError("'sep' must be whitespace only (spaces, tabs, newlines)", path=path, fragment_id=cid)
            if not isinstance(transform, str) or (transform and transform not in _TRANSFORMS):
                raise PromptConfigError("unknown 'transform'", path=path, fragment_id=cid)
        else:
            raise PromptConfigError("composition part must be a string or a mapping", path=path, fragment_id=cid)
        parts.append(_Part(ref, sep, transform))
    return _Composition(cid, aware, tuple(parts), end)


def _load_defaults(defaults_dir: Path, yaml: Any = None) -> tuple[dict[str, _Fragment], dict[str, _Composition]]:
    files = sorted(p for p in defaults_dir.glob("*.yaml") if p.is_file())
    if not files:
        raise PromptConfigError("no packaged default *.yaml files found", path=defaults_dir)
    if yaml is None:
        yaml = _new_yaml()
    frags: dict[str, _Fragment] = {}
    comps: dict[str, _Composition] = {}
    for path in files:
        data, _digest = _read_yaml_file(path, yaml)
        _check_keys(data, _DEFAULT_FILE_KEYS, what="file", path=path)
        _check_version(data, path)
        raw_frags = data.get("fragments") or {}
        raw_comps = data.get("compositions") or {}
        if not isinstance(raw_frags, dict) or not isinstance(raw_comps, dict):
            raise PromptConfigError("'fragments' and 'compositions' must be mappings", path=path)
        for fid, entry in raw_frags.items():
            fid = _check_fragment_id(fid, path)
            if fid in frags or fid in comps:
                raise PromptConfigError("duplicate ID", path=path, fragment_id=fid)
            frags[fid] = _parse_default_fragment(fid, entry, path)
        for cid, entry in raw_comps.items():
            cid = _check_fragment_id(cid, path)
            if cid in frags or cid in comps:
                raise PromptConfigError("duplicate ID", path=path, fragment_id=cid)
            comps[cid] = _parse_composition(cid, entry, path)
    # Composition refs are resolved after every file is read (a composition may use another file's fragment).
    for comp in comps.values():
        for part in comp.parts:
            ref = frags.get(part.ref)
            if ref is None:
                raise PromptConfigError("composition references an unknown fragment", fragment_id=comp.id)
            if ref.kind != "text":
                raise PromptConfigError(
                    f"composition part '{part.ref}' must reference a 'text' fragment", fragment_id=comp.id
                )
    return frags, comps


def _list_override_files(prompts_dir: Path) -> list[Path]:
    """The accepted override files; editor leftovers and wrong-case suffixes are load errors."""
    accepted: list[Path] = []
    for entry in sorted(prompts_dir.iterdir(), key=lambda p: p.name):
        if not entry.is_file():
            continue
        name = entry.name
        lowered = name.lower()
        if ".yaml" not in lowered and ".yml" not in lowered:
            continue  # README, notes, ... are none of our business
        if name.endswith(".yaml") and not name.startswith((".", "#")):
            accepted.append(entry)
            continue
        raise PromptConfigError(
            "unsupported file name: override files must be lowercase '*.yaml' and not hidden or editor "
            "backup files (use '.yaml', not '.yml'/'.YAML'; remove '.#*', '*~' and similar leftovers)",
            path=entry,
        )
    return accepted


def _apply_overrides(
    frags: dict[str, _Fragment], comps: Mapping[str, _Composition], prompts_dir: Path, yaml: Any = None
) -> tuple[dict[str, _Fragment], list[dict[str, str]]]:
    if not prompts_dir.exists():
        raise PromptConfigError("prompts_dir does not exist", path=prompts_dir)
    if not prompts_dir.is_dir():
        raise PromptConfigError("prompts_dir is not a directory", path=prompts_dir)
    files = _list_override_files(prompts_dir)
    if not files:
        raise PromptConfigError(
            f"prompts_dir {prompts_dir} contains no *.yaml override files; "
            "unset prompts_dir (or MORALSTACK_PROMPTS_DIR) to use the packaged defaults",
            path=prompts_dir,
        )
    if yaml is None:
        yaml = _new_yaml()
    merged = dict(frags)
    seen: dict[str, str] = {}
    audit_files: list[dict[str, str]] = []
    for path in files:
        data, digest = _read_yaml_file(path, yaml)
        _check_keys(data, _OVERRIDE_FILE_KEYS, what="override file", path=path)
        _check_version(data, path)
        raw_frags = data.get("fragments")
        if not isinstance(raw_frags, dict) or not raw_frags:
            raise PromptConfigError("override file must define a non-empty 'fragments' mapping", path=path)
        for fid, entry in raw_frags.items():
            if not isinstance(fid, str):
                raise PromptConfigError("fragment ID must be a string", path=path)
            if fid in comps:
                raise PromptConfigError(
                    "compositions cannot be overridden (only fragment text/when)", path=path, fragment_id=fid
                )
            base = frags.get(fid)
            if base is None:
                raise PromptConfigError("unknown fragment ID", path=path, fragment_id=fid)
            if fid in seen:
                raise PromptConfigError(f"fragment overridden twice (also in {seen[fid]})", path=path, fragment_id=fid)
            seen[fid] = path.name
            merged[fid] = _override_fragment(base, entry, path)
        audit_files.append({"file": path.name, "sha256": digest})
    return merged, audit_files


def _override_fragment(base: _Fragment, entry: Any, path: Path) -> _Fragment:
    fid = base.id
    if not isinstance(entry, dict):
        raise PromptConfigError("override entry must be a mapping with 'text' and/or 'when'", path=path, fragment_id=fid)
    _check_keys(entry, _OVERRIDE_FRAGMENT_KEYS, what="override entry", path=path, fid=fid)
    if not entry:
        raise PromptConfigError("override entry needs 'text' and/or 'when'", path=path, fragment_id=fid)
    text, when = base.text, base.when
    overridden: list[str] = []
    missing: tuple[str, ...] = ()
    if "text" in entry:
        text = entry["text"]
        if not isinstance(text, str):
            raise PromptConfigError("'text' must be a string", path=path, fragment_id=fid)
        overridden.append("text")
        if base.kind == "template":
            used = _template_fields(text, path=path, fid=fid)
            undeclared = used - set(base.placeholders)
            if undeclared:
                raise PromptConfigError(
                    "template uses undeclared placeholders: " + ", ".join(sorted(undeclared)),
                    path=path,
                    fragment_id=fid,
                )
            missing = tuple(p for p in base.placeholders if p not in used)
            if missing:
                logger.warning(
                    "prompt override of template %s omits declared placeholder(s): %s (file=%s)",
                    fid,
                    ", ".join(missing),
                    path.name,
                )
        elif base.kind == "lines":
            _check_lines(text, path=path, fid=fid)
    if "when" in entry:
        when = _check_when(entry["when"], path=path, fid=fid)
        overridden.append("when")
    return _Fragment(fid, base.kind, text, base.placeholders, when, "override", path.name, tuple(overridden), missing)


def _check_when_placement(frags: Mapping[str, _Fragment], comps: Mapping[str, _Composition]) -> None:
    """A non-``always`` ``when`` is only legal on fragments used solely by context-aware compositions."""
    users: dict[str, list[_Composition]] = {}
    for comp in comps.values():
        for part in comp.parts:
            users.setdefault(part.ref, []).append(comp)
    for fid, frag in frags.items():
        if frag.when == "always":
            continue
        containing = users.get(fid, [])
        if not containing:
            raise PromptConfigError(
                f"fragment has when={frag.when} but belongs to no composition (standalone fragments are always sent)",
                fragment_id=fid,
            )
        blocked = sorted(c.id for c in containing if not c.context_aware)
        if blocked:
            raise PromptConfigError(
                f"fragment has when={frag.when} but is also used by non-context-aware composition(s): " + ", ".join(blocked),
                fragment_id=fid,
            )


# ---------------------------------------------------------------------------------------
# PromptSet
# ---------------------------------------------------------------------------------------


@dataclass(frozen=True, eq=False)
class PromptSet:
    """Immutable, fully validated and pre-rendered set of prompt fragments and compositions."""

    fingerprint: str
    default_fingerprint: str
    is_default: bool
    ps: str
    _fragments: Mapping[str, _Fragment]
    _compositions: Mapping[str, _Composition]
    _renders: Mapping[tuple[str, PromptContext], str]
    _summary: Mapping[str, Any]

    # -- accessors ---------------------------------------------------------------------

    def fragment_ids(self) -> tuple[str, ...]:
        return tuple(self._fragments)

    def composition_ids(self) -> tuple[str, ...]:
        return tuple(self._compositions)

    def has(self, id: str) -> bool:  # noqa: A002 - mirrors the documented API
        return id in self._fragments or id in self._compositions

    def is_context_aware(self, id: str) -> bool:  # noqa: A002
        comp = self._compositions.get(id)
        if comp is None:
            raise KeyError(id)
        return comp.context_aware

    def text(self, id: str) -> str:  # noqa: A002
        """A ``text`` fragment, or a non-context-aware composition. Raises ``TypeError`` otherwise."""
        comp = self._compositions.get(id)
        if comp is not None:
            if comp.context_aware:
                raise TypeError(f"{id} is a context-aware composition: use render(id, ctx)")
            return self._renders[(id, NO_CONTEXT)]
        frag = self._fragment(id)
        if frag.kind != "text":
            raise TypeError(f"{id} is a '{frag.kind}' fragment: use template()/format()/lines()")
        return frag.text

    def render(self, id: str, ctx: PromptContext) -> str:  # noqa: A002
        """A composition rendered for ``ctx`` (non-aware compositions ignore ``ctx``)."""
        comp = self._compositions.get(id)
        if comp is None:
            if id in self._fragments:
                raise TypeError(f"{id} is a fragment, not a composition: use text()/template()/lines()")
            raise KeyError(id)
        return self._renders[(id, ctx if comp.context_aware else NO_CONTEXT)]

    def template(self, id: str) -> str:  # noqa: A002
        """The raw ``str.format`` template of a ``template`` fragment."""
        frag = self._fragment(id)
        if frag.kind != "template":
            raise TypeError(f"{id} is a '{frag.kind}' fragment, not a template")
        return frag.text

    def format(self, id: str, **values: Any) -> str:  # noqa: A002
        return self.template(id).format(**values)

    def lines(self, id: str) -> tuple[str, ...]:  # noqa: A002
        frag = self._fragment(id)
        if frag.kind != "lines":
            raise TypeError(f"{id} is a '{frag.kind}' fragment, not a 'lines' fragment")
        return tuple(frag.text.split("\n"))

    def cache_tag_for(self, ctx: PromptContext) -> str:
        """Cache-key part isolating non-default sets (and their ``when`` variants). Empty for the default set."""
        if self.is_default:
            return ""
        return f"ps:{self.ps}:{int(ctx.contract_present)}{int(ctx.history_present)}"

    def audit_summary(self) -> dict[str, Any]:
        """Deep copy of the precomputed ``prompt_config`` audit record."""
        return copy.deepcopy(dict(self._summary))

    def request_link(self) -> dict[str, Any]:
        return {"ps": self.ps, "is_default": self.is_default}

    # -- internals ---------------------------------------------------------------------

    def _fragment(self, id: str) -> _Fragment:  # noqa: A002
        frag = self._fragments.get(id)
        if frag is None:
            if id in self._compositions:
                raise TypeError(f"{id} is a composition, not a fragment: use text()/render()")
            raise KeyError(id)
        return frag


def _build_summary(
    *,
    frags: Mapping[str, _Fragment],
    fingerprint: str,
    default_fingerprint: str,
    prompts_dir: str | None,
    override_files: list[dict[str, str]],
    safety_overridden: list[str],
) -> dict[str, Any]:
    fragments: dict[str, Any] = {}
    for fid, f in frags.items():
        entry: dict[str, Any] = {"sha256": _sha256_text(f.text), "when": f.when, "origin": f.origin}
        if f.origin == "override":
            entry["override_file"] = f.override_file
            entry["overridden"] = list(f.overridden)
            if "text" in f.overridden:
                entry["text"] = f.text
            if f.missing_placeholders:
                entry["missing_placeholders"] = list(f.missing_placeholders)
        fragments[fid] = entry
    return {
        "schema": _AUDIT_SCHEMA,
        "set_fingerprint": f"sha256:{fingerprint}",
        "default_fingerprint": f"sha256:{default_fingerprint}",
        "ps": fingerprint[:16],
        "is_default": fingerprint == default_fingerprint,
        "prompts_dir": prompts_dir,
        "override_files": override_files,
        "safety_fragments_overridden": safety_overridden,
        "fragments": fragments,
    }


def _prerender(frags: Mapping[str, _Fragment], comps: Mapping[str, _Composition]) -> dict[tuple[str, PromptContext], str]:
    renders: dict[tuple[str, PromptContext], str] = {}
    for comp in comps.values():
        contexts = _ALL_CONTEXTS if comp.context_aware else (NO_CONTEXT,)
        for ctx in contexts:
            rendered = _render_composition(comp, frags, ctx)
            if rendered == "":
                logger.warning("prompt composition %s renders empty for %s", comp.id, ctx)
            renders[(comp.id, ctx)] = rendered
    return renders


_ParsedDefaults = tuple[dict[str, _Fragment], dict[str, _Composition], str]


def _parse_defaults(defaults_dir: Path, yaml: Any = None) -> _ParsedDefaults:
    """Parse and validate the default tree: ``(fragments, compositions, default fingerprint)``."""
    default_frags, comps = _load_defaults(defaults_dir, yaml)
    _check_when_placement(default_frags, comps)
    return default_frags, comps, _fingerprint(default_frags, comps)


def _load_from(defaults_dir: Path, prompts_dir: str | os.PathLike[str] | None) -> PromptSet:
    """Build a set from ``defaults_dir`` plus optional overrides. Also the hook the tests use."""
    defaults_dir = Path(defaults_dir)
    yaml = _new_yaml()
    parsed = _reusable_defaults(defaults_dir) if prompts_dir is not None else None
    if parsed is None:
        parsed = _parse_defaults(defaults_dir, yaml)
    return _assemble(parsed, prompts_dir, yaml)


def _assemble(parsed: _ParsedDefaults, prompts_dir: str | os.PathLike[str] | None, yaml: Any) -> PromptSet:
    default_frags, comps, default_fingerprint = parsed

    frags: dict[str, _Fragment] = default_frags
    override_files: list[dict[str, str]] = []
    configured_dir: str | None = None
    if prompts_dir is not None:
        configured_dir = os.fspath(prompts_dir)
        if not configured_dir.strip():
            raise PromptConfigError("prompts_dir is empty")
        frags, override_files = _apply_overrides(default_frags, comps, Path(configured_dir).expanduser(), yaml)
        _check_when_placement(frags, comps)

    renders = _prerender(frags, comps)
    if prompts_dir is None:
        for comp in comps.values():
            if comp.context_aware and len({renders[(comp.id, ctx)] for ctx in _ALL_CONTEXTS}) != 1:
                raise PromptConfigError(
                    "packaged default renders differently across contexts (every default 'when' must be 'always')",
                    fragment_id=comp.id,
                )

    fingerprint = _fingerprint(frags, comps)
    safety_overridden = sorted(fid for fid, f in frags.items() if f.origin == "override" and _is_safety_id(fid))
    summary = _build_summary(
        frags=frags,
        fingerprint=fingerprint,
        default_fingerprint=default_fingerprint,
        prompts_dir=configured_dir,
        override_files=override_files,
        safety_overridden=safety_overridden,
    )

    n_text = sum(1 for f in frags.values() if "text" in f.overridden)
    n_when = sum(1 for f in frags.values() if "when" in f.overridden)
    logger.info(
        "Prompt set loaded: fingerprint=%s default_fingerprint=%s override_files=%d text_overrides=%d when_overrides=%d",
        fingerprint[:16],
        default_fingerprint[:16],
        len(override_files),
        n_text,
        n_when,
    )
    is_default = fingerprint == default_fingerprint
    if not is_default:
        logger.warning("Prompt overrides active: governance-relevant prompt text differs from packaged defaults.")
    for fid in safety_overridden:
        kinds = "+".join(frags[fid].overridden)
        logger.warning(
            "prompt override touches safety-relevant fragment %s (%s); hard-signal reporting may be weakened; "
            "recorded in prompt_config.safety_fragments_overridden",
            fid,
            kinds,
        )

    return PromptSet(
        fingerprint=fingerprint,
        default_fingerprint=default_fingerprint,
        is_default=is_default,
        ps=fingerprint[:16],
        _fragments=MappingProxyType(dict(frags)),
        _compositions=MappingProxyType(dict(comps)),
        _renders=MappingProxyType(renders),
        _summary=MappingProxyType(summary),
    )


# ---------------------------------------------------------------------------------------
# Public loading API
# ---------------------------------------------------------------------------------------

_DEFAULT: PromptSet | None = None
_DEFAULT_PARSED: tuple[Path, _ParsedDefaults] | None = None  # parse result behind _DEFAULT, for reuse
_DEFAULT_LOCK = threading.Lock()


def _reusable_defaults(defaults_dir: Path) -> _ParsedDefaults | None:
    """The already-parsed packaged defaults, only when ``defaults_dir`` is the packaged dir they came from."""
    cached = _DEFAULT_PARSED
    default = _DEFAULT
    if default is None or cached is None or defaults_dir != _DATA_DIR or cached[0] != defaults_dir:
        return None
    frags, comps, fingerprint = cached[1]
    if fingerprint != default.default_fingerprint:  # _DEFAULT and its parse result must belong together
        return None
    return dict(frags), dict(comps), fingerprint  # fragments and compositions are frozen dataclasses


def default_prompt_set() -> PromptSet:
    """The packaged defaults: a lock-guarded process singleton. A broken packaged file raises, nothing is cached."""
    global _DEFAULT, _DEFAULT_PARSED
    current = _DEFAULT
    if current is not None:
        return current
    with _DEFAULT_LOCK:
        if _DEFAULT is None:
            yaml = _new_yaml()
            parsed = _parse_defaults(_DATA_DIR, yaml)
            _DEFAULT = _assemble(parsed, None, yaml)
            _DEFAULT_PARSED = (_DATA_DIR, parsed)
        return _DEFAULT


def load_prompt_set(prompts_dir: str | os.PathLike[str] | None) -> PromptSet:
    """``None`` gives the default singleton; a directory gives a new (not memoized) validated set."""
    if prompts_dir is None:
        return default_prompt_set()
    return _load_from(_DATA_DIR, prompts_dir)


def resolve_prompts_dir(explicit: str | None) -> str | None:
    """Explicit setting first, then a non-blank ``MORALSTACK_PROMPTS_DIR``, else ``None`` (packaged defaults)."""
    if explicit is not None and explicit.strip():
        return explicit
    from_env = os.environ.get(ENV_PROMPTS_DIR)
    if from_env is not None and from_env.strip():
        return from_env
    return None


def prompt_run_meta(obj: object) -> dict[str, Any]:
    """``{"prompt_config": <audit summary>}`` iff ``obj`` is a ``PromptSet``; ``{}`` otherwise. Never raises."""
    try:
        if isinstance(obj, PromptSet):
            return {"prompt_config": obj.audit_summary()}
    except Exception:  # noqa: BLE001 - audit metadata is best effort and must never break a run
        logger.debug("prompt_run_meta failed", exc_info=True)
    return {}


if TYPE_CHECKING:
    SAFETY_FRAGMENT_IDS: frozenset[str]  # served lazily by __getattr__ below


def __getattr__(name: str) -> Any:
    """PEP 562: ``SAFETY_FRAGMENT_IDS`` is derived from the packaged IDs on first access, never at import.

    Safety-relevant packaged fragments stay code-owned: the single rule is ``_is_safety_id`` (prefixes plus an
    explicit list), so a new fragment under a safety prefix is covered automatically. Importing this module
    stays cheap (no YAML parse); fail-fast on a corrupt packaged file happens at the first default load.

    The value is computed from the packaged set and written into the module globals: later reads bypass this
    function and ``dir()`` lists it. It is never invalidated (the packaged set is immutable per process).
    """
    if name == "SAFETY_FRAGMENT_IDS":
        value = frozenset(fid for fid in default_prompt_set().fragment_ids() if _is_safety_id(fid))
        globals()[name] = value
        return value
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
