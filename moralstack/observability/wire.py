"""Wire codec: reconstruct the exact messages sent to a model from a persisted ``llm_calls`` row.

Pure functions, no I/O. The record lives at ``parsed_summary_json["wire"]`` (one call) or, for rows that
aggregate several calls, at ``parsed_summary_json["wire_calls"]`` with one shared string table at
``parsed_summary_json["wire_strings"]``.

Record v1::

    {"v": 1, "branch": "messages|generate", "sha256": "...", "n": 4,
     "messages": [{"role": "system", "ref": "system_prompt"},
                  {"role": "developer", "s": 0},
                  {"role": "user", "s": 1},
                  {"role": "user", "ref": "prompt", "prefix": "<preface>", "suffix": ""}],
     "strings": ["<contract as sent>", "<history turn as sent>"]}

Each message content is described in one of three ways:

* ``ref``: byte-equal to a column of the row (``"system_prompt"`` or ``"prompt"``);
* ``ref`` + ``prefix``/``suffix``: the column occurs exactly once inside the content (the context
  preface and the retry suffix wrap the persisted prompt);
* ``s``: an index into the string table, which deduplicates identical contents.

Only what the row's columns lack is stored. Building a record is telemetry: callers wrap it (PROJECT_SPEC
§5.6), a failure must never reach the module call or change a result.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

WIRE_VERSION = 1
REF_SYSTEM = "system_prompt"
REF_PROMPT = "prompt"


def generate_messages_for(prompt: str, system: str) -> list[dict[str, str]]:
    """Messages ``OpenAIPolicy.generate`` builds from ``(prompt, system)``: the system message is omitted when empty."""
    messages: list[dict[str, str]] = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})
    return messages


def wire_sha256(messages: list[dict[str, str]]) -> str:
    """sha256 of the compact JSON of ``[{"role", "content"}, ...]`` (non-ASCII kept as is)."""
    canonical = json.dumps(
        [{"role": m["role"], "content": m["content"]} for m in messages],
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8", errors="surrogatepass")).hexdigest()


def _intern(strings: list[str], value: str) -> int:
    try:
        return strings.index(value)
    except ValueError:
        strings.append(value)
        return len(strings) - 1


def _describe(content: str, columns: tuple[tuple[str, str], ...], strings: list[str]) -> dict[str, Any]:
    for ref, column in columns:
        if column and content == column:
            return {"ref": ref}
    for ref, column in columns:
        if column and content.count(column) == 1:
            head, _, tail = content.partition(column)
            return {"ref": ref, "prefix": head, "suffix": tail}
    return {"s": _intern(strings, content)}


def encode_wire(
    messages: list[dict[str, str]],
    *,
    prompt_col: str,
    system_prompt_col: str,
    branch: str,
    strings: list[str] | None = None,
) -> dict[str, Any]:
    """Encode the exact ``messages`` sent to the model against the columns persisted with the row.

    ``strings`` is an optional shared string table (aggregated rows): when given it is extended in place and
    the returned record carries no ``strings`` key of its own.
    """
    table: list[str] = [] if strings is None else strings
    columns = ((REF_SYSTEM, system_prompt_col), (REF_PROMPT, prompt_col))
    described = [{"role": m["role"], **_describe(m["content"], columns, table)} for m in messages]
    record: dict[str, Any] = {
        "v": WIRE_VERSION,
        "branch": branch,
        "sha256": wire_sha256(messages),
        "n": len(messages),
        "messages": described,
    }
    if strings is None:
        record["strings"] = table
    return record


def encode_wire_calls(
    calls: list[tuple[list[dict[str, str]], str]],
    *,
    prompt_col: str,
    system_prompt_col: str,
) -> dict[str, Any]:
    """Encode several calls of one aggregated row: ``{"wire_calls": [...], "wire_strings": [...]}``.

    ``calls`` is ``[(messages, branch), ...]`` in call order; all records share one string table, so a system
    prompt common to every call is stored once.
    """
    shared: list[str] = []
    records = [
        encode_wire(
            messages,
            prompt_col=prompt_col,
            system_prompt_col=system_prompt_col,
            branch=branch,
            strings=shared,
        )
        for messages, branch in calls
    ]
    return {"wire_calls": records, "wire_strings": shared}


def decode_wire(
    record: dict[str, Any],
    *,
    prompt_col: str,
    system_prompt_col: str,
    strings: list[str] | None = None,
) -> list[dict[str, str]]:
    """Rebuild the exact messages from a v1 record and the row's columns.

    ``strings`` overrides the record's own table (aggregated rows share ``wire_strings``). Raises ``ValueError``
    on a malformed record: a verifier must report it, never guess.
    """
    try:
        return _decode(record, prompt_col, system_prompt_col, strings)
    except (KeyError, IndexError, TypeError, AttributeError) as exc:
        raise ValueError(f"malformed wire record: {type(exc).__name__}: {exc}") from exc


def _decode(
    record: dict[str, Any], prompt_col: str, system_prompt_col: str, strings: list[str] | None
) -> list[dict[str, str]]:
    if record.get("v") != WIRE_VERSION:
        raise ValueError(f"unsupported wire record version: {record.get('v')!r}")
    table = strings if strings is not None else record.get("strings", [])
    columns = {REF_SYSTEM: system_prompt_col, REF_PROMPT: prompt_col}
    out: list[dict[str, str]] = []
    for item in record["messages"]:
        if "s" in item:
            index = item["s"]
            if not isinstance(index, int) or isinstance(index, bool) or not 0 <= index < len(table):
                raise ValueError(f"wire string index out of range: {index!r}")
            content = table[index]
        elif item.get("ref") in columns:
            content = item.get("prefix", "") + columns[item["ref"]] + item.get("suffix", "")
        else:
            raise ValueError(f"malformed wire message: {item!r}")
        out.append({"role": item["role"], "content": content})
    return out
