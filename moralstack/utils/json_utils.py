"""
Utilities for robust JSON parsing in MoralStack.

Handles common cases of malformed LLM output:
- Truncated JSON (unclosed strings/arrays/objects)
- Text before/after the JSON
- Problematic escape sequences
- Markdown blocks
"""

from __future__ import annotations

import json
import re
from typing import Any, cast


class JSONParseError(Exception):
    """JSON parsing error."""

    pass


def _loads_json_object(text: str) -> dict[str, Any]:
    """Parse JSON object from text. Call sites assume the root value is an object."""
    return cast(dict[str, Any], json.loads(text))


def _preprocess_text(text: str) -> str:
    """Pre-processes the text to remove common problems."""
    # Remove trailing commas in objects and arrays
    # Pattern: , followed by } or ] with optional whitespace in between
    text = re.sub(r",\s*\}", "}", text)
    text = re.sub(r",\s*\]", "]", text)

    # Remove stray backslashes before underscore (common LLM error)
    text = text.replace("\\_", "_")

    # Remove problematic control characters (except newline and tab)
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", text)

    # Normalize newlines
    text = text.replace("\r\n", "\n").replace("\r", "\n")

    return text


def _try_complete_truncated_json(text: str) -> str | None:
    """
    Attempts to complete a truncated JSON.

    Handled cases:
    - Unclosed strings: "value → "value"
    - Unclosed arrays: ["a", "b → ["a", "b"]
    - Unclosed objects: {"key": "value → {"key": "value"}
    """
    if not text.strip().startswith("{"):
        return None

    # Count open/closed brackets
    open_braces = text.count("{")
    close_braces = text.count("}")
    open_brackets = text.count("[")
    close_brackets = text.count("]")

    # If already balanced, no completion needed
    if open_braces == close_braces and open_brackets == close_brackets:
        return text

    completed = text.rstrip()

    # Remove trailing comma if present
    completed = re.sub(r",\s*$", "", completed)

    # If it ends with an unclosed string, close it
    # Pattern: "...[^"] at the end without a closing "
    if re.search(r':\s*"[^"]*$', completed):
        completed += '"'

    # Close open arrays
    while open_brackets > close_brackets:
        completed += "]"
        close_brackets += 1

    # Close open objects
    while open_braces > close_braces:
        completed += "}"
        close_braces += 1

    return completed


def _extract_json_object(text: str) -> str | None:
    """Extracts the first complete JSON object from the text."""
    start_idx = text.find("{")
    if start_idx == -1:
        return None

    # Look for the matching closing bracket
    depth = 0
    in_string = False
    escape_next = False

    for i in range(start_idx, len(text)):
        char = text[i]

        if escape_next:
            escape_next = False
            continue

        if char == "\\":
            escape_next = True
            continue

        if char == '"' and not escape_next:
            in_string = not in_string
            continue

        if in_string:
            continue

        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[start_idx : i + 1]

    # If we get here, the object is truncated
    return text[start_idx:]


def extract_json(text: str) -> dict[str, Any]:
    """
    Extracts and parses JSON from text robustly.
    Supports markdown blocks, text before/after, truncated JSON, problematic escapes.
    text: string that may contain JSON.
    Returns: parsed dictionary.
    Raises JSONParseError if it cannot extract valid JSON.
    """
    original_text = text
    text = _preprocess_text(text.strip())

    # Attempt 1: Direct parsing (ideal case)
    try:
        return _loads_json_object(text)
    except json.JSONDecodeError:
        pass

    # Attempt 2: Extraction from markdown blocks ```json ... ``` or ``` ... ```
    code_patterns = [
        r"```json\s*(.*?)\s*```",
        r"```\s*(.*?)\s*```",
    ]
    for pattern in code_patterns:
        match = re.search(pattern, text, re.DOTALL)
        if match:
            try:
                return _loads_json_object(match.group(1))
            except json.JSONDecodeError:
                # Try to repair the content
                pass

    # Attempt 3: Extract the JSON object from the text
    json_text = _extract_json_object(text)
    if json_text:
        try:
            return _loads_json_object(json_text)
        except json.JSONDecodeError:
            pass

        # Attempt 4: Try to complete truncated JSON
        completed = _try_complete_truncated_json(json_text)
        if completed:
            try:
                return _loads_json_object(completed)
            except json.JSONDecodeError:
                pass

    # Attempt 5: Look for JSON between the first { and the last }
    start_idx = text.find("{")
    end_idx = text.rfind("}")

    if start_idx != -1 and end_idx > start_idx:
        json_content = text[start_idx : end_idx + 1]
        try:
            return _loads_json_object(json_content)
        except json.JSONDecodeError:
            # Attempt 6: Repair newlines in strings
            try:
                repaired = re.sub(
                    r':\s*"([^"]*)"',
                    lambda m: ': "' + m.group(1).replace("\n", " ").replace("\r", "") + '"',
                    json_content,
                )
                return _loads_json_object(repaired)
            except Exception:
                pass

            # Attempt 7: Complete if truncated
            completed = _try_complete_truncated_json(json_content)
            if completed and completed != json_content:
                try:
                    return _loads_json_object(completed)
                except json.JSONDecodeError:
                    pass

    # Attempt 8: If there is only { without }, try to complete
    if "{" in text and "}" not in text:
        start_idx = text.find("{")
        truncated = text[start_idx:]
        completed = _try_complete_truncated_json(truncated)
        if completed:
            try:
                return _loads_json_object(completed)
            except json.JSONDecodeError:
                pass

    raise JSONParseError(f"Impossibile estrarre JSON valido dal testo: {original_text[:150]}...")
