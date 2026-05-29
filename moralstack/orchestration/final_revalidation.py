"""Final output revalidation for contract-constrained upstream responses."""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Any, Literal

from moralstack.orchestration.orchestration_event_taxonomy import (
    PROXY_FINAL_REVALIDATION_BLOCKED,
    PROXY_FINAL_REVALIDATION_ERROR,
    PROXY_FINAL_REVALIDATION_PASSED,
    PROXY_FINAL_REVALIDATION_SKIPPED,
    PROXY_FINAL_REVALIDATION_STARTED,
)
from moralstack.orchestration.persistence_helpers import record_llm_call
from moralstack.orchestration.safe_refusal_generator import (
    REFUSAL_FALLBACK_MARKER,
    _detect_language_fallback,
    generate_llm_safe_refusal_detailed,
    resolve_refusal_domain_and_redirection,
)
from moralstack.orchestration.types import ResponseType
from moralstack.persistence.sink import persist_orchestration_event

logger = logging.getLogger(__name__)

UPSTREAM_FINAL_TEXT_SOURCES = frozenset({"safe_complete_upstream", "upstream_regen"})
CONTRACT_HARD_VIOLATION_IDS = frozenset({"CORE.DEVCONTRACT.1"})
DEFAULT_POST_REVALIDATION_REFUSAL = "I cannot provide that content."
CONTRACT_LITERAL_MIN_LENGTH = 8
RESPONSE_FRAGMENT_MIN_LENGTH = 7
NEAR_MATCH_THRESHOLD = 0.82


@dataclass(frozen=True)
class FinalRevalidationOutcome:
    """Result of contract-aware final-output validation."""

    status: Literal["pass", "block", "error", "skipped"]
    final_text: str
    final_text_source: str
    final_action: str | None = None
    fallback_source: str = ""
    violated_hard: bool = False
    violated_principles: list[str] = field(default_factory=list)
    skip_reason: str = ""
    error: str = ""


def has_developer_contract(developer_contract: Any | None) -> bool:
    """Return True when a non-empty developer contract constrains the request."""
    if developer_contract is None:
        return False
    raw = getattr(developer_contract, "raw_text", None)
    if raw is not None:
        return bool(str(raw).strip())
    return True


def emit_final_revalidation_skipped(
    *,
    run_id: str,
    request_id: str,
    final_text_source: str,
    developer_contract: Any | None,
    skip_reason: Literal["streaming", "no_developer_contract", "final_text_source_not_upstream"],
    final_response_length: int = 0,
    final_text: str = "",
) -> None:
    """Emit an explicit skip event so missing validation is auditable."""
    _emit_event(
        run_id=run_id,
        request_id=request_id,
        event_type=PROXY_FINAL_REVALIDATION_SKIPPED,
        decision="skipped",
        status="ok",
        payload={
            "final_text_source_original": final_text_source,
            "final_text_source_after_revalidation": final_text_source,
            "developer_contract_present": has_developer_contract(developer_contract),
            "skip_reason": skip_reason,
            "violated_hard": False,
            "violated_principles": [],
            "fallback_source": "",
            "candidate_final_text_before": final_text or "",
            "final_text_after_revalidation": final_text or "",
            "final_response_length_before": final_response_length,
            "final_response_length_after": final_response_length,
        },
    )


def record_upstream_final_generation(
    *,
    run_id: str,
    request_id: str,
    final_text_source: str,
    messages: list[dict[str, Any]],
    response_text: str,
    model: str = "",
    started_at: int | None = None,
    duration_ms: float | None = None,
    finish_reason: str = "",
    reason: str = "",
) -> None:
    """Persist the final upstream provider call as an inspectable execution node."""
    if not run_id or not request_id:
        return
    sections = _message_sections_from_messages(messages)
    prompt = sections.get("final_user_message") or ""
    system_prompt = "\n\n---\n\n".join(sections.get("system_messages") or [])
    if not prompt:
        prompt = "\n\n".join(
            f"{str(m.get('role') or 'unknown').upper()}:\n{str(m.get('content') or '')}" for m in messages
        )
    parsed_summary = {
        "message_sections": sections,
        "final_text_source": final_text_source,
        "finish_reason": finish_reason,
        "reason": reason,
        "is_delivery_generation": True,
    }
    record_llm_call(
        None,
        None,
        {
            "run_id": run_id,
            "request_id": request_id,
            "cycle": None,
            "phase": final_text_source,
            "module": "upstream_provider",
            "action": "generate final response",
            "model": model,
            "started_at": started_at or int(time.time() * 1000),
            "duration_ms": duration_ms,
            "prompt": prompt,
            "system_prompt": system_prompt,
            "raw_response": response_text or "",
            "parsed_summary_json": _json_dumps(parsed_summary),
            "sequence_in_cycle": 7,
            "call_kind": "final_delivery_generation",
        },
    )


def revalidate_final_output(
    *,
    orchestrator: Any,
    request: Any,
    result: Any,
    final_text: str,
    final_text_source: str,
    run_id: str = "",
) -> FinalRevalidationOutcome:
    """
    Revalidate upstream-generated final text against the request developer contract.

    Active only for non-empty developer contracts and upstream text sources. Technical
    failures fail closed for contract-bearing requests.
    """
    request_id = getattr(request, "request_id", "") or getattr(result, "request_id", "") or ""
    developer_contract = getattr(request, "developer_contract", None)
    length_before = len(final_text or "")

    if not has_developer_contract(developer_contract):
        emit_final_revalidation_skipped(
            run_id=run_id,
            request_id=request_id,
            final_text_source=final_text_source,
            developer_contract=developer_contract,
            skip_reason="no_developer_contract",
            final_response_length=length_before,
            final_text=final_text or "",
        )
        return FinalRevalidationOutcome(
            status="skipped",
            final_text=final_text,
            final_text_source=final_text_source,
            skip_reason="no_developer_contract",
        )

    if final_text_source not in UPSTREAM_FINAL_TEXT_SOURCES:
        emit_final_revalidation_skipped(
            run_id=run_id,
            request_id=request_id,
            final_text_source=final_text_source,
            developer_contract=developer_contract,
            skip_reason="final_text_source_not_upstream",
            final_response_length=length_before,
            final_text=final_text or "",
        )
        return FinalRevalidationOutcome(
            status="skipped",
            final_text=final_text,
            final_text_source=final_text_source,
            skip_reason="final_text_source_not_upstream",
        )

    _emit_event(
        run_id=run_id,
        request_id=request_id,
        event_type=PROXY_FINAL_REVALIDATION_STARTED,
        decision="started",
        status="ok",
        payload={
            "final_text_source_original": final_text_source,
            "final_text_source_after_revalidation": final_text_source,
            "developer_contract_present": True,
            "violated_hard": False,
            "violated_principles": [],
            "fallback_source": "",
            "candidate_final_text_before": final_text or "",
            "final_text_after_revalidation": final_text or "",
            "final_response_length_before": length_before,
            "final_response_length_after": length_before,
        },
    )

    literal_disclosure = _detect_contract_literal_disclosure(developer_contract, final_text or "")
    if literal_disclosure is not None:
        refusal, fallback_source = _generate_post_revalidation_refusal(
            orchestrator=orchestrator,
            request=request,
            result=result,
            violated_principles=["CORE.DEVCONTRACT.1"],
            block_reason="contract_literal_disclosure",
            run_id=run_id,
        )
        _emit_event(
            run_id=run_id,
            request_id=request_id,
            event_type=PROXY_FINAL_REVALIDATION_BLOCKED,
            decision="blocked",
            status="ok",
            payload={
                "final_text_source_original": final_text_source,
                "final_text_source_after_revalidation": "refusal_post_revalidation",
                "developer_contract_present": True,
                "violated_hard": True,
                "violated_principles": ["CORE.DEVCONTRACT.1"],
                "fallback_source": fallback_source,
                "candidate_final_text_before": final_text or "",
                "final_text_after_revalidation": refusal,
                "final_response_length_before": length_before,
                "final_response_length_after": len(refusal),
                "block_reason": "contract_literal_disclosure",
                **literal_disclosure,
            },
        )
        return FinalRevalidationOutcome(
            status="block",
            final_text=refusal,
            final_text_source="refusal_post_revalidation",
            final_action="REFUSE",
            fallback_source=fallback_source,
            violated_hard=True,
            violated_principles=["CORE.DEVCONTRACT.1"],
        )

    try:
        critic = getattr(orchestrator, "critic", None)
        store = getattr(orchestrator, "constitution_store", None)
        if critic is None or store is None:
            raise RuntimeError("final revalidation requires critic and constitution_store")

        domain = request.get_domain() if hasattr(request, "get_domain") else None
        constitution = store.get_constitution(domain)
        try:
            principles = list(
                store.get_relevant_principles(
                    query=getattr(request, "prompt", "") or "",
                    top_k=10,
                    domain=domain,
                    retrieval_phase="final_revalidation",
                )
            )
        except TypeError:
            principles = list(store.get_relevant_principles(getattr(request, "prompt", "") or "", top_k=10, domain=domain))

        report = critic.critique(
            getattr(request, "prompt", "") or "",
            final_text or "",
            constitution,
            principles=principles,
            request_id=request_id,
            developer_contract=developer_contract,
            conversation_history=list(getattr(request, "conversation_history", []) or []),
        )
        violated_principles = _violation_ids(report)
        violated_hard = getattr(report, "violated_hard", False) is True or _has_hard_contract_violation(report)
        if violated_hard:
            refusal, fallback_source = _generate_post_revalidation_refusal(
                orchestrator=orchestrator,
                request=request,
                result=result,
                violated_principles=violated_principles,
                block_reason="critic_hard_violation",
                run_id=run_id,
            )
            _emit_event(
                run_id=run_id,
                request_id=request_id,
                event_type=PROXY_FINAL_REVALIDATION_BLOCKED,
                decision="blocked",
                status="ok",
                payload={
                    "final_text_source_original": final_text_source,
                    "final_text_source_after_revalidation": "refusal_post_revalidation",
                    "developer_contract_present": True,
                    "violated_hard": True,
                    "violated_principles": violated_principles,
                    "fallback_source": fallback_source,
                    "candidate_final_text_before": final_text or "",
                    "final_text_after_revalidation": refusal,
                    "final_response_length_before": length_before,
                    "final_response_length_after": len(refusal),
                },
            )
            return FinalRevalidationOutcome(
                status="block",
                final_text=refusal,
                final_text_source="refusal_post_revalidation",
                final_action="REFUSE",
                fallback_source=fallback_source,
                violated_hard=True,
                violated_principles=violated_principles,
            )

        _emit_event(
            run_id=run_id,
            request_id=request_id,
            event_type=PROXY_FINAL_REVALIDATION_PASSED,
            decision="passed",
            status="ok",
            payload={
                "final_text_source_original": final_text_source,
                "final_text_source_after_revalidation": final_text_source,
                "developer_contract_present": True,
                "violated_hard": False,
                "violated_principles": violated_principles,
                "fallback_source": "",
                "candidate_final_text_before": final_text or "",
                "final_text_after_revalidation": final_text or "",
                "final_response_length_before": length_before,
                "final_response_length_after": length_before,
            },
        )
        return FinalRevalidationOutcome(
            status="pass",
            final_text=final_text,
            final_text_source=final_text_source,
            final_action=getattr(getattr(getattr(result, "response", None), "metadata", None), "final_action", None),
            violated_principles=violated_principles,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("final output revalidation failed closed: %s", exc)
        refusal, fallback_source = _generate_post_revalidation_refusal(
            orchestrator=orchestrator,
            request=request,
            result=result,
            violated_principles=[],
            block_reason="final_revalidation_error",
            run_id=run_id,
        )
        _emit_event(
            run_id=run_id,
            request_id=request_id,
            event_type=PROXY_FINAL_REVALIDATION_ERROR,
            decision="error",
            status="error",
            payload={
                "final_text_source_original": final_text_source,
                "final_text_source_after_revalidation": "refusal_post_revalidation",
                "developer_contract_present": True,
                "violated_hard": False,
                "violated_principles": [],
                "fallback_source": fallback_source,
                "candidate_final_text_before": final_text or "",
                "final_text_after_revalidation": refusal,
                "final_response_length_before": length_before,
                "final_response_length_after": len(refusal),
                "error": str(exc)[:500],
            },
        )
        return FinalRevalidationOutcome(
            status="error",
            final_text=refusal,
            final_text_source="refusal_post_revalidation",
            final_action="REFUSE",
            fallback_source=fallback_source,
            error=str(exc),
    )


def _message_sections_from_messages(messages: list[dict[str, Any]]) -> dict[str, Any]:
    sections: dict[str, Any] = {
        "system_messages": [],
        "developer_messages": [],
        "history_messages": [],
        "final_user_message": "",
    }
    for msg in messages or []:
        role = str(msg.get("role") or "").strip().lower()
        content = str(msg.get("content") or "")
        if role == "system":
            sections["system_messages"].append(content)
        elif role == "developer":
            sections["developer_messages"].append(content)
        elif role == "user":
            if sections["final_user_message"]:
                sections["history_messages"].append({"role": "user", "content": sections["final_user_message"]})
            sections["final_user_message"] = content
        elif role == "assistant":
            sections["history_messages"].append({"role": "assistant", "content": content})
        elif role:
            sections["history_messages"].append({"role": role, "content": content})
    return sections


def _json_dumps(payload: dict[str, Any]) -> str:
    import json

    return json.dumps(payload, ensure_ascii=False)


def _violation_ids(report: Any) -> list[str]:
    ids: list[str] = []
    for violation in getattr(report, "violations", []) or []:
        pid = getattr(violation, "principle_id", "") or getattr(violation, "id", "")
        if pid:
            ids.append(str(pid))
    return list(dict.fromkeys(ids))


def _has_hard_contract_violation(report: Any) -> bool:
    for violation in getattr(report, "violations", []) or []:
        pid = getattr(violation, "principle_id", "") or getattr(violation, "id", "")
        if pid in CONTRACT_HARD_VIOLATION_IDS and getattr(violation, "constraint_type", "") == "hard":
            return True
    return False


def _detect_contract_literal_disclosure(developer_contract: Any | None, response_text: str) -> dict[str, Any] | None:
    """
    Detect exact or near disclosure of protected literal values quoted in a contract.

    The payload intentionally records only lengths and match metadata, never the
    protected value or response fragment.
    """
    protected_literals = _extract_protected_contract_literals(developer_contract)
    if not protected_literals:
        return None

    response_norm = _normalize_alnum(response_text)
    if not response_norm:
        return None

    for literal in protected_literals:
        literal_norm = _normalize_alnum(literal)
        if len(literal_norm) < CONTRACT_LITERAL_MIN_LENGTH:
            continue
        if literal_norm in response_norm:
            return {
                "match_kind": "exact_normalized_literal",
                "protected_literal_length": len(literal_norm),
                "matched_fragment_length": len(literal_norm),
                "similarity": 1.0,
            }

        substring_len = max(RESPONSE_FRAGMENT_MIN_LENGTH, min(12, int(len(literal_norm) * 0.55)))
        for size in range(len(literal_norm), substring_len - 1, -1):
            for start in range(0, len(literal_norm) - size + 1):
                if literal_norm[start : start + size] in response_norm:
                    return {
                        "match_kind": "protected_literal_substring",
                        "protected_literal_length": len(literal_norm),
                        "matched_fragment_length": size,
                        "similarity": 1.0,
                    }

        near_match = _find_near_literal_fragment(literal_norm, response_norm)
        if near_match is not None:
            return {
                "match_kind": "protected_literal_near_match",
                "protected_literal_length": len(literal_norm),
                **near_match,
            }

    return None


def _extract_protected_contract_literals(developer_contract: Any | None) -> list[str]:
    raw_text = getattr(developer_contract, "raw_text", "") if developer_contract is not None else ""
    if not raw_text:
        return []
    literals: list[str] = []
    for match in re.finditer(r"""(['"`])([^'"`]{%d,})\1""" % CONTRACT_LITERAL_MIN_LENGTH, str(raw_text)):
        literal = match.group(2).strip()
        if literal and any(ch.isalnum() for ch in literal):
            literals.append(literal)
    return list(dict.fromkeys(literals))


def _find_near_literal_fragment(literal_norm: str, response_norm: str) -> dict[str, Any] | None:
    min_fragment_len = max(RESPONSE_FRAGMENT_MIN_LENGTH, int(len(literal_norm) * 0.4))
    max_fragment_len = min(len(literal_norm), 32)
    for fragment_len in range(min_fragment_len, max_fragment_len + 1):
        for response_start in range(0, len(response_norm) - fragment_len + 1):
            fragment = response_norm[response_start : response_start + fragment_len]
            min_window = max(min_fragment_len, fragment_len - 2)
            max_window = min(len(literal_norm), fragment_len + 2)
            for size in range(min_window, max_window + 1):
                for start in range(0, len(literal_norm) - size + 1):
                    ratio = SequenceMatcher(None, fragment, literal_norm[start : start + size]).ratio()
                    if ratio >= NEAR_MATCH_THRESHOLD:
                        return {
                            "matched_fragment_length": fragment_len,
                            "similarity": round(ratio, 3),
                        }
    return None


def _normalize_alnum(text: str) -> str:
    return "".join(ch.lower() for ch in text if ch.isalnum())


def _generate_post_revalidation_refusal(
    *,
    orchestrator: Any,
    request: Any,
    result: Any,
    violated_principles: list[str],
    block_reason: str,
    run_id: str = "",
) -> tuple[str, str]:
    policy = getattr(orchestrator, "policy", None)
    generate = getattr(policy, "generate", None) if policy is not None else None
    llm_client = policy if callable(generate) else None
    language = _detect_language_fallback(getattr(request, "prompt", "") or "")
    domain, refusal_redirection = resolve_refusal_domain_and_redirection(
        request_prompt=getattr(request, "prompt", "") or "",
        request_domain=request.get_domain() if hasattr(request, "get_domain") else None,
        detected_domain=None,
        risk_signals=[],
        constitution_store=getattr(orchestrator, "constitution_store", None),
    )
    reason_codes = list(dict.fromkeys(["FINAL_REVALIDATION_BLOCKED", block_reason, *violated_principles]))
    rationale = (
        "The candidate final answer failed the final developer-contract validation. "
        "Refuse without quoting protected content, internal detector details, or policy identifiers."
    )

    start = time.perf_counter()
    wall = int(time.time() * 1000)
    refusal_result = generate_llm_safe_refusal_detailed(
        user_prompt=getattr(request, "prompt", "") or "",
        risk_category="developer_contract_violation",
        policy_reason_codes=reason_codes,
        language=language,
        domain=domain,
        llm_client=llm_client,
        rationale=rationale,
        refusal_redirection=refusal_redirection,
        refusal_context=None,
    )
    duration_ms = (time.perf_counter() - start) * 1000
    refusal = refusal_result.text
    fallback_source = (
        "refusal_module_llm"
        if refusal_result.attempts > 0 and refusal != REFUSAL_FALLBACK_MARKER
        else "refusal_module_fallback"
    )
    request_id = getattr(request, "request_id", "") or getattr(result, "request_id", "") or ""
    try:
        record_llm_call(
            None,
            None,
            {
                "run_id": run_id,
                "request_id": request_id,
                "cycle": None,
                "phase": "refusal",
                "module": "orchestration",
                "action": "refuse (final_revalidation)",
                "model": str(getattr(policy, "model", "") or ""),
                "started_at": wall,
                "duration_ms": duration_ms,
                "prompt": refusal_result.user_prompt,
                "system_prompt": refusal_result.system_prompt,
                "raw_response": refusal,
                "attempts": refusal_result.attempts,
                "sequence_in_cycle": 6,
                "call_kind": "final_revalidation_refusal",
            },
        )
    except Exception:
        logger.debug("persist final revalidation refusal llm call failed", exc_info=True)

    _mutate_result_to_revalidation_refusal(
        result,
        refusal=refusal,
        violated_principles=violated_principles,
        refusal_reason=block_reason,
    )
    return refusal, fallback_source


def _mutate_result_to_revalidation_refusal(
    result: Any,
    *,
    refusal: str,
    violated_principles: list[str],
    refusal_reason: str,
) -> None:
    try:
        result.response.content = refusal
        result.response.response_type = ResponseType.FULL_REFUSAL
        meta = result.response.metadata
        meta.final_action = "REFUSE"
        meta.must_refuse = True
        meta.refusal_reason = refusal_reason
        meta.reason_codes = list(
            dict.fromkeys(list(getattr(meta, "reason_codes", []) or []) + ["FINAL_REVALIDATION_BLOCKED"])
        )
        meta.hard_violations = list(dict.fromkeys(list(getattr(meta, "hard_violations", []) or []) + violated_principles))
    except Exception:
        pass


def _emit_event(
    *,
    run_id: str,
    request_id: str,
    event_type: str,
    decision: str,
    status: str,
    payload: dict[str, Any],
) -> None:
    if not run_id or not request_id:
        return
    try:
        persist_orchestration_event(
            run_id=run_id,
            request_id=request_id,
            stage="proxy",
            component="final_revalidation",
            event_type=event_type,
            decision=decision,
            status=status,
            payload=payload,
        )
    except Exception:
        logger.debug("persist %s failed (non-fatal)", event_type, exc_info=True)
