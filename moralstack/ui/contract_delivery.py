"""Developer-contract delivery for the request page (read-only, pure).

Which ``llm_calls`` rows actually *received* the developer contract, through which channel, and how much of it. The
answer is data-driven: each row's wire record (``observability/wire.py``) holds the exact messages sent to the model, so
the contract is looked for in them. Nothing here is a static per-module map.

Two steps:

* :func:`resolve_request_contract` finds the request's canonical contract text. It is verified by hash: the
  ``contract_hash`` of the compliance-layer verdict event must equal ``DeveloperContract.from_text(text, mode)
  .contract_hash`` for a candidate text taken from the recorded request message sections. Without a verified match, a
  single distinct risk-estimator developer message is used and flagged ``verified=False``; otherwise nothing is claimed.
* :func:`contract_delivery_for_call` classifies one row against that text (native developer/system message, embedded in
  a system or user prompt, or recorded in the row's message sections).

Never raises into the page: malformed or missing data resolves to "no contract section" (PROJECT_SPEC §5.6).
No DB access, no writes: the UI only calls ``decode_wire`` and ``DeveloperContract.from_text`` (both pure).
"""

from __future__ import annotations

import json
from typing import Any

from moralstack.observability.wire import decode_wire
from moralstack.orchestration.contract import ContractMode, DeveloperContract

#: A partial match (the call got only a prefix of the contract) counts only from this many characters. The smallest
#: truncation limit applied to the contract by a module is 1500; rule-action excerpts are 120-500 chars
#: (``dccl.py`` evidence, ``controller.py`` draft revalidation), so they never reach it.
MIN_PARTIAL_CONTRACT_CHARS = 1000

_MODES: tuple[ContractMode, ...] = ("opaque", "structured")
_DENIED_COMPLIANCE_ACTIONS = frozenset({"safety_override", "draft_revalidate"})

_LABELS = {
    "native_developer": "native developer message (role=developer)",
    "native_system": "native message (role=system)",
    "embedded_system": "embedded in the system prompt",
    "embedded_developer": "embedded in a developer message",
    "embedded_user": "embedded in the user prompt",
    "recorded_sections": "recorded in this call's message sections (role sent not recorded)",
}
_SHORT_LABELS = {
    "native_developer": "native · developer message",
    "native_system": "native · system message",
    "embedded_system": "embedded · system prompt",
    "embedded_developer": "embedded · developer message",
    "embedded_user": "embedded · user prompt",
    "recorded_sections": "recorded · message sections",
}
_CLIENT_ROLE_SYSTEM_NOTE = " — sent by the client as role=system"
# Lower value wins when one call carries the contract through several channels.
_CHANNEL_PRIORITY = {
    "native_developer": 0,
    "native_system": 0,
    "embedded_system": 1,
    "embedded_developer": 2,
    "embedded_user": 3,
}


def _as_dict(value: Any) -> dict[str, Any]:
    """``value`` as a dict when it is one or a JSON object text; ``{}`` otherwise (never raises)."""
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
        except (ValueError, TypeError):
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _str(value: Any) -> str:
    return value if isinstance(value, str) else ""


def _str_list(value: Any) -> list[str]:
    return [item for item in value if isinstance(item, str)] if isinstance(value, list) else []


def _sections_of(data: dict[str, Any]) -> dict[str, Any]:
    """``message_sections`` of a summary / payload, top level or under ``context_shape`` (same shapes as the page)."""
    sections = data.get("message_sections")
    if not isinstance(sections, dict) and isinstance(data.get("context_shape"), dict):
        sections = data["context_shape"].get("message_sections")
    return sections if isinstance(sections, dict) else {}


def _event_payload(event: Any) -> dict[str, Any]:
    if not isinstance(event, dict):
        return {}
    return _as_dict(event.get("payload_json")) or _as_dict(event.get("payload"))


def resolve_request_contract(
    llm_calls: list[dict[str, Any]], orchestration_events: list[dict[str, Any]]
) -> dict[str, Any] | None:
    """Canonical developer contract of the request, or ``None`` when nothing can be claimed.

    Returns ``{"text", "verified", "client_role_system"}``. ``text`` is stripped (display form); the identity check
    hashes the raw and the stripped text under both contract modes against the verdict events' ``contract_hash``.
    """
    hashes: set[str] = set()
    candidates: list[str] = []
    system_texts: set[str] = set()
    risk_developer_texts: list[str] = []

    def add_sections(sections: dict[str, Any], *, risk_row: bool) -> None:
        system = _str_list(sections.get("system_messages"))
        developer = _str_list(sections.get("developer_messages"))
        system_texts.update(s.strip() for s in system)
        for text in (*system, *developer):
            if text.strip() and text not in candidates:
                candidates.append(text)
        if risk_row:
            risk_developer_texts.extend(d.strip() for d in developer if d.strip())

    for event in orchestration_events or []:
        if not isinstance(event, dict):
            continue
        payload = _event_payload(event)
        if str(event.get("event_type") or "").startswith("COMPLIANCE_LAYER_VERDICT"):
            value = payload.get("contract_hash")
            if isinstance(value, str) and value:
                hashes.add(value)
        add_sections(_sections_of(payload) or payload, risk_row=False)

    for call in llm_calls or []:
        if not isinstance(call, dict):
            continue
        summary = _as_dict(call.get("parsed_summary_json"))
        add_sections(_sections_of(summary), risk_row="risk" in _str(call.get("module")).lower())

    for text in candidates:
        for variant in (text, text.strip()):
            for mode in _MODES:
                if variant.strip() and DeveloperContract.from_text(variant, mode).contract_hash in hashes:
                    canonical = text.strip()
                    return {"text": canonical, "verified": True, "client_role_system": canonical in system_texts}

    distinct = list(dict.fromkeys(risk_developer_texts))
    if len(distinct) == 1:
        return {"text": distinct[0], "verified": False, "client_role_system": distinct[0] in system_texts}
    return None


def _longest_prefix_in(contract: str, body: str) -> int:
    """Length of the longest prefix of ``contract`` contained in ``body`` (containment is monotone in the length)."""
    if not contract or not body:
        return 0
    if contract in body:
        return len(contract)
    low, high = 0, len(contract)  # invariant: contract[:low] in body, contract[:high + 1] not in body
    while low < high:
        mid = (low + high + 1) // 2
        if contract[:mid] in body:
            low = mid
        else:
            high = mid - 1
    return low


def _classify_message(role: str, content: str, contract: str) -> tuple[str, int] | None:
    """``(channel, chars_received)`` when this message carries the contract, else ``None``."""
    stripped = content.strip()
    if role in ("system", "developer"):
        if stripped and (
            stripped == contract or (len(stripped) >= MIN_PARTIAL_CONTRACT_CHARS and contract.startswith(stripped))
        ):
            return ("native_developer" if role == "developer" else "native_system", len(stripped))
        received = _longest_prefix_in(contract, content)
        if received == len(contract) or received >= MIN_PARTIAL_CONTRACT_CHARS:
            return ("embedded_system" if role == "system" else "embedded_developer", received)
        return None
    if role == "user":
        received = _longest_prefix_in(contract, content)
        if received == len(contract) or received >= MIN_PARTIAL_CONTRACT_CHARS:
            return ("embedded_user", received)
    return None


def _better(candidate: tuple[str, int, str], current: tuple[str, int, str] | None) -> bool:
    if current is None:
        return True
    rank, current_rank = _CHANNEL_PRIORITY[candidate[0]], _CHANNEL_PRIORITY[current[0]]
    return rank < current_rank or (rank == current_rank and candidate[1] > current[1])


def _pick_best(hits: list[tuple[str, int, str]]) -> tuple[str, int, str]:
    """Best hit of a non-empty list (native over embedded, then the larger excerpt)."""
    best = hits[0]
    for hit in hits[1:]:
        if _better(hit, best):
            best = hit
    return best


def _best_of_messages(messages: list[Any], contract: str) -> tuple[str, int, str] | None:
    """Best ``(channel, chars_received, role)`` among the messages of ONE call."""
    best: tuple[str, int, str] | None = None
    for message in messages:
        if (
            not isinstance(message, dict)
            or not isinstance(message.get("role"), str)
            or not isinstance(message.get("content"), str)
        ):
            continue
        role = message["role"].strip().lower()
        hit = _classify_message(role, message["content"], contract)
        if hit is not None and _better((hit[0], hit[1], role), best):
            best = (hit[0], hit[1], role)
    return best


def _decode(record: Any, call: dict[str, Any], strings: list[str] | None = None) -> list[Any] | None:
    try:
        return decode_wire(
            record,
            prompt_col=_str(call.get("prompt")),
            system_prompt_col=_str(call.get("system_prompt")),
            strings=strings,
        )
    except (ValueError, TypeError, AttributeError):
        return None


def _wire_calls_of(call: dict[str, Any], summary: dict[str, Any]) -> list[list[Any] | None] | None:
    """Decoded message lists, one per recorded call (``None`` = malformed record); ``None`` when the row has no wire."""
    if "wire" in summary:
        return [_decode(summary["wire"], call)]
    if "wire_calls" in summary:
        records, strings = summary["wire_calls"], summary.get("wire_strings")
        if not isinstance(records, list) or not isinstance(strings, list):
            return [None]
        return [_decode(record, call, strings) for record in records] or [None]
    # The critic's wire sits in ``parsed_json`` because its summary column is plain text.
    if "critic" in _str(call.get("module")).lower():
        parsed = _as_dict(call.get("parsed_json"))
        if "wire" in parsed:
            return [_decode(parsed["wire"], call)]
    return None


def _build_delivery(
    contract: dict[str, Any],
    channel: str,
    received: int,
    role: str | None,
    *,
    source: str,
    with_contract: int,
    total: int,
) -> dict[str, Any]:
    text = contract["text"]
    chars_total = len(text)
    received = min(received, chars_total)
    truncated = received < chars_total
    label = _LABELS[channel]
    if channel == "native_system" and contract.get("client_role_system"):
        label += _CLIENT_ROLE_SYSTEM_NOTE
    return {
        "channel": channel,
        "label": label,
        "short_label": _SHORT_LABELS[channel],
        "role": role,
        "chars_received": received,
        "chars_total": chars_total,
        "truncated": truncated,
        "coverage": (f"truncated: first {received} of {chars_total} chars" if truncated else f"full ({chars_total} chars)"),
        "excerpt": text[:received],
        "verified": bool(contract.get("verified")),
        "source": source,
        "calls_with_contract": with_contract,
        "calls_total": total,
    }


def contract_delivery_for_call(call: dict[str, Any], contract: dict[str, Any] | None) -> dict[str, Any] | None:
    """How ``call`` received the developer contract, or ``None`` when it did not (or nothing can be claimed).

    Never claims for synthetic rows, hydrated speculative-reuse rows, or the compliance ``safety_override`` /
    ``draft_revalidate`` rows: their prompts carry rule-action excerpts or the draft by construction, so a text match
    there would be a false claim.
    """
    if not contract or not _str(contract.get("text")) or not isinstance(call, dict):
        return None
    if call.get("is_synthetic") or call.get("_hydrated_from_speculative_generate"):
        return None
    module, action = _str(call.get("module")).lower(), _str(call.get("action")).lower()
    if "compliance" in module and action in _DENIED_COMPLIANCE_ACTIONS:
        return None

    text = contract["text"]
    summary = _as_dict(call.get("parsed_summary_json"))
    decoded = _wire_calls_of(call, summary)

    if decoded is not None:
        # The wire is the exact message list: it answers alone, a miss is never second-guessed by recorded sections.
        hits = [_best_of_messages(messages, text) for messages in decoded if messages is not None]
        found = [hit for hit in hits if hit is not None]
        if not found:
            return None
        best = _pick_best(found)
        return _build_delivery(
            contract, best[0], best[1], best[2], source="wire", with_contract=len(found), total=len(decoded)
        )

    column_hits = [
        hit
        for hit in (
            _best_of_messages([{"role": "system", "content": _str(call.get("system_prompt"))}], text),
            _best_of_messages([{"role": "user", "content": _str(call.get("prompt"))}], text),
        )
        if hit is not None
    ]
    if column_hits:
        best = _pick_best(column_hits)
        return _build_delivery(contract, best[0], best[1], best[2], source="columns", with_contract=1, total=1)

    sections = _sections_of(summary)
    recorded = [*_str_list(sections.get("system_messages")), *_str_list(sections.get("developer_messages"))]
    if any(item.strip() == text for item in recorded):
        return _build_delivery(contract, "recorded_sections", len(text), None, source="sections", with_contract=1, total=1)
    return None
