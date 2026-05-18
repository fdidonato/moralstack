"""
Consolidate append-only JSONL deltas (request.meta_updated style) into a
self-contained JSON snapshot equivalent to the SQLite ``requests.meta_json``
column.

This utility is for offline consumers who only have the JSONL stream (no
SQLite DB) but need the consolidated state — for example when copying logs
between machines, or auditing after DB rotation, or feeding analytics that
expect self-contained JSON.

Semantics (matches sqlite_sink.update_request_meta with merge=True):
- Group envelopes by ``(run_id, request_id)``.
- Process each group in chronological order (envelope ``created_at``, then
  position in file as tiebreaker for stability).
- For each envelope, take ``payload.meta`` (must be a dict) and merge it
  into the accumulator for that key. Last-write-wins per individual meta
  field.
- Output: ``{"<run_id>:<request_id>": {<consolidated meta>}, ...}``.

The script handles both ``request.meta_updated`` (the primary use case) and
any other event_type with a ``payload.meta`` field by name compatibility.
Other event_types are skipped silently.

Usage:
    python scripts/consolidate_jsonl_meta.py \\
        --input logs/observability/request.meta_updated.jsonl \\
        --output requests_meta_consolidated.json

    # Multiple input files merged in sequence:
    python scripts/consolidate_jsonl_meta.py \\
        --input logs/observability/request.meta_updated.jsonl \\
        --input logs/observability/request.meta_updated.20260514.jsonl \\
        --output consolidated.json
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from collections import OrderedDict
from pathlib import Path
from typing import Any

_LOG = logging.getLogger(__name__)


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Consolidate request.meta_updated JSONL deltas into a single JSON snapshot.",
    )
    parser.add_argument(
        "--input",
        action="append",
        required=True,
        help="Input JSONL file (can be repeated to merge multiple files).",
    )
    parser.add_argument(
        "--output",
        required=True,
        help="Output JSON file path.",
    )
    parser.add_argument(
        "--pretty",
        action="store_true",
        help="Pretty-print the output JSON with 2-space indent (default: compact).",
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="Verbose logging.")
    return parser.parse_args(argv)


def _load_envelopes(paths: list[Path]) -> list[dict[str, Any]]:
    """
    Read all JSONL files and return parsed envelopes with their source order
    preserved. Each envelope is annotated with ``_source_index`` (file order)
    and ``_line_index`` (line within the file) for stable sorting.
    """
    envelopes: list[dict[str, Any]] = []
    for src_idx, path in enumerate(paths):
        if not path.exists():
            _LOG.warning("Input file not found: %s — skipping", path)
            continue
        with path.open(encoding="utf-8") as fh:
            for line_idx, raw in enumerate(fh):
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    env = json.loads(raw)
                except json.JSONDecodeError as e:
                    _LOG.warning("Malformed JSONL line %d in %s: %s", line_idx, path, e)
                    continue
                env["_source_index"] = src_idx
                env["_line_index"] = line_idx
                envelopes.append(env)
    return envelopes


def _sort_key(envelope: dict[str, Any]) -> tuple:
    """
    Sort key for chronological merge:
      primary: created_at (envelope timestamp);
      tiebreaker: source file index, then line index in that file.
    """
    created_at = envelope.get("created_at") or envelope.get("timestamp") or ""
    return (
        str(created_at),
        envelope.get("_source_index", 0),
        envelope.get("_line_index", 0),
    )


def consolidate(envelopes: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """
    Apply progressive merge to grouped envelopes.

    Returns a dict mapping ``"<run_id>:<request_id>"`` to the consolidated
    meta dict. Envelopes without a meta field, or without identifiable
    run_id/request_id, are skipped silently.
    """
    sorted_envs = sorted(envelopes, key=_sort_key)
    result: dict[str, dict[str, Any]] = OrderedDict()

    for env in sorted_envs:
        run_id = env.get("run_id") or ""
        request_id = env.get("request_id") or ""
        payload = env.get("payload") or {}
        if not isinstance(payload, dict):
            continue
        meta = payload.get("meta")
        if not isinstance(meta, dict):
            continue
        if not run_id or not request_id:
            continue

        key = f"{run_id}:{request_id}"
        accumulator = result.setdefault(key, {})
        # Last-write-wins per field. Matches sqlite_sink merge semantics.
        accumulator.update(meta)

    return result


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )

    input_paths = [Path(p) for p in args.input]
    output_path = Path(args.output)

    envelopes = _load_envelopes(input_paths)
    _LOG.info("Loaded %d envelopes from %d file(s)", len(envelopes), len(input_paths))

    consolidated = consolidate(envelopes)
    _LOG.info("Consolidated to %d unique (run_id, request_id) keys", len(consolidated))

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as fh:
        if args.pretty:
            json.dump(consolidated, fh, indent=2, ensure_ascii=False, default=str)
        else:
            json.dump(consolidated, fh, ensure_ascii=False, default=str)
        fh.write("\n")

    _LOG.info("Wrote consolidated output to %s", output_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
