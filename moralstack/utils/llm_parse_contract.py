"""
Shared parse-contract metadata for LLM JSON outputs (risk, constitution retrieval).

Records whether strict json_object mode was requested, whether parsing used a direct
json.loads path vs tolerant extract_json, and retry hints. Does not change governance logic.
"""

from __future__ import annotations

import json
from typing import Any

from moralstack.utils.json_utils import JSONParseError, extract_json

# Stored in parsed_summary_json under key "parse_contract" (flat merge also supported).
RESPONSE_CONTRACT_JSON_OBJECT = "json_object"
RESPONSE_CONTRACT_PROMPT_ONLY = "prompt_only_json"
RESPONSE_CONTRACT_UNKNOWN = "unknown"

PARSE_STATUS_OK = "ok"
PARSE_STATUS_FALLBACK_OK = "fallback_ok"
PARSE_STATUS_FAILED = "failed"


def _contract_base(
    *,
    strict_json_requested: bool,
    response_contract: str,
    parse_status: str,
    fallback_used: bool,
    parse_attempts: int = 1,
    retry_count: int = 0,
    retry_reason: str | list[str] | None = None,
) -> dict[str, Any]:
    out: dict[str, Any] = {
        "response_contract": response_contract,
        "strict_json_requested": strict_json_requested,
        "parse_status": parse_status,
        "fallback_used": fallback_used,
        "parse_attempts": parse_attempts,
        "retry_count": retry_count,
    }
    if retry_reason is not None:
        out["retry_reason"] = retry_reason
    return out


def merge_parse_contract_into_summary(
    existing: dict[str, Any] | None,
    parse_contract: dict[str, Any],
) -> str:
    """Merge parse_contract into a summary dict and return JSON string for persist_llm_call."""
    base = dict(existing) if existing else {}
    base["parse_contract"] = parse_contract
    return json.dumps(base, ensure_ascii=False)


def build_failed_parse_contract(*, strict_json_requested: bool, message: str) -> dict[str, Any]:
    """Parse metadata when extract_json / validation fails (for observability only)."""
    c = _contract_base(
        strict_json_requested=strict_json_requested,
        response_contract=RESPONSE_CONTRACT_JSON_OBJECT,
        parse_status=PARSE_STATUS_FAILED,
        fallback_used=False,
    )
    c["retry_reason"] = message[:500]
    return c


def parse_dict_with_contract(
    raw: str,
    *,
    strict_json_requested: bool,
    response_contract: str = RESPONSE_CONTRACT_JSON_OBJECT,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """
    Parse a JSON object from model output.

    First tries json.loads on stripped text (deterministic path when the model obeys json_object).
    Falls back to extract_json() for tolerant recovery (markdown, noise, truncation repair).

    Returns:
        (data_dict, parse_contract dict suitable for persistence)

    Raises:
        JSONParseError: If no valid object could be extracted.
    """
    if not (raw or "").strip():
        raise JSONParseError("empty model output")

    stripped = raw.strip()
    try:
        first = json.loads(stripped)
        if isinstance(first, dict):
            contract = _contract_base(
                strict_json_requested=strict_json_requested,
                response_contract=response_contract,
                parse_status=PARSE_STATUS_OK,
                fallback_used=False,
            )
            return first, contract
    except json.JSONDecodeError:
        pass

    try:
        data = extract_json(raw)
        if not isinstance(data, dict):
            raise JSONParseError("extract_json did not return a JSON object")
        contract = _contract_base(
            strict_json_requested=strict_json_requested,
            response_contract=response_contract,
            parse_status=PARSE_STATUS_FALLBACK_OK,
            fallback_used=True,
        )
        return data, contract
    except JSONParseError:
        raise


def parse_principle_id_list_with_contract(
    raw: str,
    *,
    strict_json_requested: bool,
) -> tuple[list[str], dict[str, Any]]:
    """
    Parse {"principle_ids": [...]} from json_object output.

    Falls back to legacy array extraction if the object path fails but extract_json returns a list
    (prompt_only / tolerant paths only).
    """
    try:
        d, c = parse_dict_with_contract(raw, strict_json_requested=strict_json_requested)
        ids = d.get("principle_ids", [])
        if isinstance(ids, list):
            return [str(x) for x in ids], c
        return [], c
    except JSONParseError:
        pass
    try:
        data = extract_json(raw)
        if isinstance(data, list):
            c = _contract_base(
                strict_json_requested=strict_json_requested,
                response_contract=RESPONSE_CONTRACT_PROMPT_ONLY,
                parse_status=PARSE_STATUS_FALLBACK_OK,
                fallback_used=True,
            )
            return [str(x) for x in data], c
    except JSONParseError:
        pass
    # Last resort: regex array (legacy DomainAgent behavior)
    import re

    m = re.search(r"\[[\s\S]*?\]", raw)
    if m:
        try:
            arr = json.loads(m.group())
            if isinstance(arr, list):
                c = _contract_base(
                    strict_json_requested=strict_json_requested,
                    response_contract=RESPONSE_CONTRACT_PROMPT_ONLY,
                    parse_status=PARSE_STATUS_FALLBACK_OK,
                    fallback_used=True,
                )
                return [str(x) for x in arr], c
        except json.JSONDecodeError:
            pass
    raise JSONParseError("Could not parse principle_ids list from model output")
