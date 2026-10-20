"""Shared helpers for the A2 prompt-externalization tests: override directories that mark fragments.

A plain module (not a test file, not a conftest fixture): import it as ``from tests.prompt_markers import ...``.

``make_marker_dir`` writes a valid override tree that appends a unique marker to the text of packaged
fragments, so a test can prove which fragment ended up on the wire. ``make_when_override_dir`` writes an
override tree that changes only ``when``.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from pathlib import Path

from moralstack.prompts.catalog import default_prompt_set

DEFAULT_MARKER = "<<M:{id}>>"


def marker_for(fid: str, marker: str = DEFAULT_MARKER) -> str:
    """The marker text appended to fragment ``fid``."""
    return marker.format(id=fid)


def _new_dir(tmp_path: Path, stem: str) -> Path:
    candidate = tmp_path / stem
    counter = 1
    while candidate.exists():
        counter += 1
        candidate = tmp_path / f"{stem}{counter}"
    candidate.mkdir(parents=True)
    return candidate


def _write_override(directory: Path, fragments: Mapping[str, dict[str, str]]) -> Path:
    # JSON is valid YAML, and json.dumps escapes everything a double-quoted scalar needs.
    (directory / "overrides.yaml").write_text(json.dumps({"version": 1, "fragments": dict(fragments)}), encoding="utf-8")
    return directory


def make_marker_dir(tmp_path: Path, *, ids: Iterable[str] | None = None, marker: str = DEFAULT_MARKER) -> Path:
    """A new override directory appending ``marker_for(fid)`` after the text of every packaged fragment.

    ``ids`` restricts the override to those fragment IDs. The marker is appended on its own line after the
    original text, so placeholders are untouched and ``double_braces`` parts stay valid; ``marker`` must not
    contain ``{`` or ``}`` once formatted (a template fragment would reject them).
    """
    ds = default_prompt_set()
    wanted = list(ds.fragment_ids()) if ids is None else list(ids)
    fragments: dict[str, dict[str, str]] = {}
    for fid in wanted:
        text = marker_for(fid, marker)
        if "{" in text or "}" in text:
            raise ValueError(f"marker for {fid} must not contain braces: {text!r}")
        fragments[fid] = {"text": ds._fragments[fid].text + "\n" + text}
    return _write_override(_new_dir(tmp_path, "marker_prompts"), fragments)


def make_when_override_dir(tmp_path: Path, when_by_id: Mapping[str, str]) -> Path:
    """A new override directory that changes only ``when`` for the given fragment IDs."""
    return _write_override(_new_dir(tmp_path, "when_prompts"), {fid: {"when": w} for fid, w in when_by_id.items()})
