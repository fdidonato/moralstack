"""
Constitution Retriever - Agent-based retrieval of relevant principles.

Encapsulates: domain prefilter, domain agents, enhanced agents,
parallel execution, and get_relevant_principles internals.
"""

from __future__ import annotations

import concurrent.futures
import contextvars
import functools
import hashlib
import json
import logging
import math
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol

from moralstack.constitution.helpers import resolve_conflict, tokenize
from moralstack.constitution.openai_config import OpenAIClientConfig
from moralstack.constitution.prompt_formatter import format_principles_for_prompt
from moralstack.constitution.retrieval_result import PrincipleRetrievalResult
from moralstack.constitution.schema import Overlay, Principle
from moralstack.observability.emit_helpers import persist_llm_call, persist_orchestration_event
from moralstack.observability.token_usage import TokenUsage
from moralstack.prompts.catalog import PromptSet, default_prompt_set
from moralstack.utils.llm_parse_contract import (
    merge_parse_contract_into_summary,
    parse_dict_with_contract,
    parse_principle_id_list_with_contract,
)
from moralstack.utils.openai_params import completion_tokens_param, supports_json_schema

if TYPE_CHECKING:
    from openai.types.chat import ChatCompletionMessageParam
    from openai.types.shared_params import ResponseFormatJSONObject, ResponseFormatJSONSchema

logger = logging.getLogger(__name__)

RETRIEVAL_PHASE_RISK_ROUTING = "risk_routing"
RETRIEVAL_PHASE_DELIBERATION = "deliberation_retrieval"
_JSON_OBJECT_RESPONSE_FORMAT: ResponseFormatJSONObject = {"type": "json_object"}
_DOMAIN_AGENT_TEMPERATURE = 0.1
_ENHANCED_DOMAIN_AGENT_MAX_OUTPUT_TOKENS = 300
_LEGACY_DOMAIN_AGENT_MAX_OUTPUT_TOKENS = 256
# Default re-exports of the packaged prompt set: runtime code reads the instance's ``_prompt_set``, never these.
_ENHANCED_DOMAIN_AGENT_SYSTEM_PROMPT = default_prompt_set().text("retrieval.agent.enhanced.system")
_LEGACY_DOMAIN_AGENT_SYSTEM_PROMPT = default_prompt_set().text("retrieval.agent.legacy.system")

_RETRIEVAL_PHASE_PERSISTENCE: dict[str, tuple[int, int]] = {
    RETRIEVAL_PHASE_RISK_ROUTING: (0, -10),
    RETRIEVAL_PHASE_DELIBERATION: (0, -1),
}


def _persist_constitution_llm_call(
    *,
    action: str,
    system_prompt: str,
    prompt: str,
    raw_response: str,
    duration_ms: float,
    started_at: int | None,
    parse_contract: dict[str, Any],
    model: str | None,
    token_usage_json: str | None = None,
    retrieval_phase: str = RETRIEVAL_PHASE_RISK_ROUTING,
    cycle: int | None = 0,
    sequence_in_cycle: int | None = None,
    domain: str | None = None,
) -> None:
    """
    Best-effort persistence for constitution retrieval LLM calls (parse metadata in parsed_summary_json).

    ``domain`` names the domain agent that made the call (per-domain agents); it is
    omitted for the shared prefilter call. Skips silently when no DB context.
    """
    try:
        if sequence_in_cycle is None:
            _, sequence_in_cycle = _RETRIEVAL_PHASE_PERSISTENCE.get(
                retrieval_phase,
                _RETRIEVAL_PHASE_PERSISTENCE[RETRIEVAL_PHASE_RISK_ROUTING],
            )
        summary_base: dict[str, Any] = {"module": "constitution_retriever", "retrieval_phase": retrieval_phase}
        if domain:
            summary_base["domain"] = domain
        summary = merge_parse_contract_into_summary(summary_base, parse_contract)
        persist_llm_call(
            phase="constitution_retrieval",
            module="constitution_retriever",
            action=action,
            model=model or "",
            started_at=started_at,
            duration_ms=duration_ms,
            prompt=prompt,
            system_prompt=system_prompt,
            raw_response=raw_response,
            parsed_summary_json=summary,
            token_usage_json=token_usage_json,
            attempts=1,
            cycle=cycle,
            sequence_in_cycle=sequence_in_cycle,
        )
    except Exception:
        logger.debug("constitution retrieval llm_call persist skipped", exc_info=True)


def _normalize_domain_descriptions(descriptions: dict[str, str]) -> tuple[tuple[str, str], ...]:
    """Canonical form for fingerprinting: sorted by domain key, string values."""
    return tuple((str(k), str(descriptions[k] or "")) for k in sorted(descriptions.keys()))


def _fingerprint_domain_descriptions(descriptions: dict[str, str]) -> str:
    """Stable SHA-256 hex digest over the normalized descriptions map."""
    norm = _normalize_domain_descriptions(descriptions)
    blob = json.dumps(norm, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


def _snapshot_domain_descriptions(descriptions: dict[str, str]) -> dict[str, str]:
    """Shallow copy of description strings for cache-fingerprint stability."""
    return {str(k): str(v or "") for k, v in (descriptions or {}).items()}


_NOT_FOR_RE = re.compile(r"\bNOT\s+for\s*:\s*", re.IGNORECASE)


def _split_scope_notfor(description: str) -> tuple[str, str]:
    """Split a YAML domain description into (positive scope, exclusion clause).

    Divides at the first ``NOT for:`` marker — a convention the deployer already
    writes inside the descriptions. When absent, the exclusion is empty and the
    scope is the whole description (no loss, no sync broken: the prompt still
    recompiles from the same YAML the deployer edits). Used to render the
    precision catalog, which surfaces exclusions on their own ``NOT:`` line."""
    parts = _NOT_FOR_RE.split(description, maxsplit=1)
    scope = parts[0].strip().rstrip(".")
    notfor = parts[1].strip() if len(parts) > 1 else ""
    return scope, notfor


def _domain_agent_messages(system_prompt: str, user_prompt: str) -> list[ChatCompletionMessageParam]:
    return [{"role": "system", "content": system_prompt}, {"role": "user", "content": user_prompt}]


def _json_object_response_format() -> ResponseFormatJSONObject:
    return {"type": "json_object"}


def _domain_agent_cache_key(
    *,
    model: str | None,
    system_prompt: str,
    user_prompt: str,
    max_output_tokens: int,
) -> str:
    """
    Cache on the exact OpenAI-relevant request material.

    Compact principle rendering omits titles by design, so unrendered title-only
    changes intentionally do not invalidate the cache.
    """
    completion_params = completion_tokens_param(model, max_output_tokens)
    key_material = json.dumps(
        {
            "model": model or "",
            "messages": _domain_agent_messages(system_prompt, user_prompt),
            "temperature": _DOMAIN_AGENT_TEMPERATURE,
            "response_format": _JSON_OBJECT_RESPONSE_FORMAT,
            "completion_params": completion_params,
        },
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(key_material.encode("utf-8")).hexdigest()


def _emit_domain_prefilter_orchestration_event(
    event_type: str, payload: dict[str, Any], *, reason_codes: list[str] | None = None
) -> None:
    """Best-effort orchestration_events row; no-op when persistence context or DB is unavailable."""
    try:
        persist_orchestration_event(
            stage="retrieval",
            component="domain_prefilter",
            event_type=event_type,
            decision=str(payload.get("decision") or ""),
            status="ok",
            payload=payload,
            reason_codes=reason_codes,
        )
    except Exception:
        logger.debug("domain prefilter orchestration event emission failed", exc_info=True)


def _prefilter_combined_cache_status(descriptions_changed: bool, cache_hit: bool | None) -> str:
    if cache_hit is None:
        return "unknown"
    if descriptions_changed and cache_hit:
        return "invalidated_then_hit"
    if descriptions_changed and not cache_hit:
        return "invalidated_then_miss"
    if cache_hit:
        return "hit"
    return "miss"


# Parse status of the last prefilter LLM call in the current context. Write-only audit side channel: it is
# set by ``DomainPrefilter._call_openai`` and read only by ``_audit_rejected_domains``; routing never reads it.
_PREFILTER_PARSE_STATUS: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "moralstack_domain_prefilter_parse_status", default=None
)

_REJECTION_AUDIT_MAX_ITEMS = 16
_REJECTION_AUDIT_MAX_LABEL_CHARS = 64
_REJECT_UNKNOWN_DOMAIN = "unknown_domain"
_REJECT_LOW_CONFIDENCE = "low_confidence"
_REJECT_OVER_CAP = "over_cap"
_REJECT_PARSE_FAILED = "parse_failed"


def _record_prefilter_parse_status(p_contract: dict[str, Any] | None) -> None:
    """Best-effort: store the parse status of the prefilter LLM output (None when unknown)."""
    try:
        status = p_contract.get("parse_status") if isinstance(p_contract, dict) else None
        _PREFILTER_PARSE_STATUS.set(status if isinstance(status, str) else None)
    except Exception:
        logger.debug("domain prefilter parse status capture failed", exc_info=True)


def _audit_label(value: Any) -> tuple[str, bool]:
    """Bounded display label for a model-proposed value; the second item is True when it was cut."""
    label = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    return label[:_REJECTION_AUDIT_MAX_LABEL_CHARS], len(label) > _REJECTION_AUDIT_MAX_LABEL_CHARS


def _audit_value_key(value: Any) -> str:
    """Exact, untruncated, type-aware dedup key (never persisted)."""
    return f"{type(value).__name__}:" + json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def _proposal_items(raw_domains: Any) -> list[Any]:
    """Items the model proposed, mirroring what the routing comprehension iterates (a str is ONE item)."""
    if isinstance(raw_domains, list):
        return list(raw_domains)
    if isinstance(raw_domains, dict):
        return list(raw_domains.keys())
    if raw_domains is None or raw_domains == "":
        return []
    return [raw_domains]


def _audit_confidence(result: Any) -> tuple[float | None, str]:
    """(finite numeric confidence or None, type name or "missing") for the audit payload."""
    if not isinstance(result, dict) or "confidence" not in result:
        return None, "missing"
    raw = result["confidence"]
    value: float | None = None
    if isinstance(raw, (int, float)) and not isinstance(raw, bool):
        try:
            candidate = float(raw)
        except OverflowError:
            candidate = float("nan")
        value = candidate if math.isfinite(candidate) else None
    return value, type(raw).__name__


def _parse_selection(sel: Any) -> tuple[str, float] | None:
    """(domain, finite confidence) of one ``selections`` entry, or None when the per-domain gate cannot score it.

    Single parser shared by the routing gate (``DomainPrefilter._selected_from_selections``) and the
    rejection audit, so the audit's per-domain ``low_confidence`` verdict is the routing verdict.
    """
    if not isinstance(sel, dict):
        return None
    domain = sel.get("domain")
    try:
        conf = float(sel.get("confidence", 0))
    except (TypeError, ValueError):
        return None
    if not isinstance(domain, str) or not math.isfinite(conf):
        return None
    return domain, conf


def _selection_confidences(selections: list[Any]) -> dict[str, float]:
    """Best scorable confidence per domain over a ``selections`` array (routing keeps a domain if ANY entry clears)."""
    best: dict[str, float] = {}
    for sel in selections:
        parsed = _parse_selection(sel)
        if parsed is not None:
            domain, conf = parsed
            best[domain] = max(conf, best.get(domain, conf))
    return best


def _build_prefilter_rejection_record(
    result: Any,
    *,
    parse_status: str | None,
    available_domains: list[str],
    applied_domains: list[str],
    max_domains: int,
    threshold: float,
    routing_fallback: bool,
    domain_confidences: dict[str, float] | None = None,
) -> dict[str, Any] | None:
    """
    Pure audit builder: which proposed values were NOT applied, and why (first gate in routing order wins).

    ``domain_confidences`` (from ``_selection_confidences``) is passed only for the per-domain ``selections``
    shape: routing then gates each domain on its OWN confidence, so a proposed value with no scorable
    confidence at or above ``threshold`` is ``low_confidence`` even when the projected global ``confidence``
    (the best per-domain score) clears it. ``None`` keeps the legacy single-global-gate attribution unchanged.

    Never mutates its inputs and does no I/O. Returns None when there is nothing to record.
    """
    confidence, confidence_type = _audit_confidence(result)
    record: dict[str, Any] = {
        "decision": "rejected",
        "reason_codes": [],
        "parse_status": parse_status,
        "confidence": confidence,
        "confidence_type": confidence_type,
        "confidence_threshold": threshold,
        "max_domains": max_domains,
        "routing_fallback": bool(routing_fallback),
        "proposed": [],
        "proposed_total": 0,
        "applied_domains": list(applied_domains),
        "rejected": [],
        "rejected_total": 0,
        "truncated": False,
    }

    def _fill_proposed(proposed: list[Any]) -> bool:
        labels = [_audit_label(p) for p in proposed[:_REJECTION_AUDIT_MAX_ITEMS]]
        record["proposed"] = [lbl for lbl, _ in labels]
        record["proposed_total"] = len(proposed)
        return len(proposed) > _REJECTION_AUDIT_MAX_ITEMS or any(cut for _, cut in labels)

    def _parse_failed_record(proposed: list[Any]) -> dict[str, Any]:
        record["decision"] = "parse_failed"
        record["reason_codes"] = [_REJECT_PARSE_FAILED]
        record["truncated"] = _fill_proposed(proposed)
        return record

    if parse_status == "failed":
        return _parse_failed_record([])
    if not isinstance(result, dict) or not result:
        return None

    raw_domains = result.get("domains", [])
    proposed = _proposal_items(raw_domains)
    if not proposed:
        return _parse_failed_record([]) if routing_fallback else None

    # Same expressions, in the same order, as the routing gate and filter comprehension.
    gate_raised = False
    try:
        gate_ok = bool(result.get("confidence", 0) >= threshold)
    except Exception:
        gate_ok = False
        gate_raised = True
    iter_raised = False
    if gate_ok:
        try:
            [d for d in raw_domains if d in available_domains]
        except Exception:
            iter_raised = True

    rejected: list[tuple[str, str]] = []
    cut_label = False
    seen: set[str] = set()
    for item in proposed:
        key = _audit_value_key(item)
        if key in seen:
            continue
        seen.add(key)
        if item in applied_domains:
            continue
        if gate_raised:
            reason = _REJECT_PARSE_FAILED
        elif not gate_ok:
            reason = _REJECT_LOW_CONFIDENCE
        elif domain_confidences is not None and not (
            isinstance(item, str) and item in domain_confidences and domain_confidences[item] >= threshold
        ):
            reason = _REJECT_LOW_CONFIDENCE
        elif iter_raised or isinstance(raw_domains, str):
            reason = _REJECT_PARSE_FAILED
        elif item not in available_domains:
            reason = _REJECT_UNKNOWN_DOMAIN
        else:
            reason = _REJECT_OVER_CAP
        label, cut = _audit_label(item)
        cut_label = cut_label or cut
        rejected.append((label, reason))

    if not rejected:
        # Routing fell back to core-only (uncached) although every proposed value counts as applied (e.g. only "core").
        return _parse_failed_record(proposed) if routing_fallback else None

    record["reason_codes"] = sorted({reason for _, reason in rejected})
    record["rejected"] = [{"domain": lbl, "reason": reason} for lbl, reason in rejected[:_REJECTION_AUDIT_MAX_ITEMS]]
    record["rejected_total"] = len(rejected)
    record["truncated"] = _fill_proposed(proposed) or len(rejected) > _REJECTION_AUDIT_MAX_ITEMS or cut_label
    return record


# =============================================================================
# Data Provider Protocol
# =============================================================================


class ConstitutionDataProvider(Protocol):
    """Protocol for constitution data (core, overlays, domain metadata)."""

    def load_core(self) -> list[Principle]: ...

    def load_overlay(self, domain: str) -> Overlay: ...

    def _get_available_domains(self) -> list[str]: ...

    def get_domain_descriptions(self) -> dict[str, str]: ...


# =============================================================================
# Agent Result
# =============================================================================


@dataclass
class AgentResult:
    """
    Domain agent result with confidence score.

    Fix 3: Adds confidence score to filter low-confidence principles.
    """

    principle_ids: list[str]
    confidence: float  # 0.0-1.0
    domain_match: bool  # True if domain is relevant to query
    reasoning: str = ""

    def __post_init__(self) -> None:
        self.confidence = max(0.0, min(1.0, self.confidence))


# =============================================================================
# Domain Prefilter
# =============================================================================


@dataclass(frozen=True)
class _PrefilterOutcome:
    """Local, non-shared result of one ``_filter_domains_scoped`` call.

    ``domains`` is always a fresh list (never the cache entry by reference) so a
    caller mutating it (e.g. appending a forced domain) can never poison
    ``DomainPrefilter._cache``.
    """

    domains: list[str]
    cache_lookup_hit: bool | None


class DomainPrefilter:
    """
    Pre-filter relevant domains before running agents.

    Two-stage retrieval: first identify relevant domains, then run only those agents.
    """

    ALWAYS_EVALUATE = {"core"}
    DOMAIN_CONFIDENCE_THRESHOLD = 0.5
    # Queries shorter than this (after stripping surrounding whitespace) carry
    # too little signal for the LLM classifier to pick a domain reliably and
    # were observed to (a) waste a full classifier round-trip on noise inputs
    # like "51", "63312" in COMPL-AI direct_request and (b) occasionally
    # hallucinate a domain match. We bypass the classifier and return an empty
    # list; the caller's existing fallback (all core principles) applies.
    MIN_QUERY_LEN_FOR_CLASSIFICATION = 10
    # Output-token budget for the classifier reply. The per-domain schema carries
    # one short evidence span per selection instead of a free-text ``reason``, so
    # this bounds cost while leaving headroom (the old 200 was hit by ~0.6% of
    # replies). A cut reply is usually repaired by the parser (``fallback_ok``),
    # so its effect depends on where it falls: inside ``payload`` routing is
    # core-only, inside ``selections`` the cut domain is silently dropped.
    # ``finish_reason`` is not read.
    PREFILTER_MAX_OUTPUT_TOKENS = 400

    def __init__(
        self,
        openai_config: OpenAIClientConfig | None = None,
        max_domains: int = 3,
        cost_tracker: Any | None = None,
        domain_descriptions: dict[str, str] | None = None,
        prompt_set: PromptSet | None = None,
    ) -> None:
        self.openai_config = openai_config or OpenAIClientConfig.default()
        self.max_domains = max_domains
        self._prompt_set = prompt_set or default_prompt_set()
        raw_desc = domain_descriptions or {}
        self._domain_descriptions = _snapshot_domain_descriptions(raw_desc)
        self._descriptions_fingerprint = _fingerprint_domain_descriptions(raw_desc)
        self._cache: dict[str, list[str]] = {}
        self._cost_tracker = cost_tracker
        # Instance-scoped OpenAI HTTP client: stateless; per-call args stay on chat.completions.create.
        self._openai_http_client: Any | None = None
        self._openai_http_client_key: str | None = None
        self._openai_client_creates: int = 0
        self._openai_client_reuses_after_cache: int = 0

    def set_cost_tracker(self, tracker: Any | None) -> None:
        """Set TokenCostTracker for OpenAI call cost tracking."""
        self._cost_tracker = tracker

    def set_domain_descriptions(
        self,
        descriptions: dict[str, str],
        *,
        invalidation_reason: str = "effective_descriptions_changed",
    ) -> bool:
        """
        Update domain descriptions when the effective map changes. Idempotent: same map does not clear cache.

        Returns:
            True if descriptions changed and cache was invalidated; False if state was equivalent.
        """
        from moralstack.orchestration.orchestration_event_taxonomy import DOMAIN_PREFILTER_CACHE_INVALIDATED

        fp_new = _fingerprint_domain_descriptions(descriptions or {})
        if fp_new == self._descriptions_fingerprint:
            return False

        fp_before = self._descriptions_fingerprint
        self._descriptions_fingerprint = fp_new
        self._domain_descriptions = _snapshot_domain_descriptions(descriptions or {})
        self._cache.clear()

        _emit_domain_prefilter_orchestration_event(
            DOMAIN_PREFILTER_CACHE_INVALIDATED,
            {
                "reason": invalidation_reason,
                "descriptions_fingerprint_before": fp_before,
                "descriptions_fingerprint_after": fp_new,
                "domain_count": len(descriptions or {}),
                "decision": "invalidated",
            },
        )
        return True

    def clear_cache(self, *, reason: str = "forced_refresh") -> None:
        """
        Clear prefilter entries without requiring a description change (e.g. full retriever refresh).
        Emits INVALIDATED only when entries were present.
        """
        from moralstack.orchestration.orchestration_event_taxonomy import DOMAIN_PREFILTER_CACHE_INVALIDATED

        if not self._cache:
            return
        self._cache.clear()
        fp = self._descriptions_fingerprint
        _emit_domain_prefilter_orchestration_event(
            DOMAIN_PREFILTER_CACHE_INVALIDATED,
            {
                "reason": reason,
                "descriptions_fingerprint_before": fp,
                "descriptions_fingerprint_after": fp,
                "domain_count": len(self._domain_descriptions),
                "decision": "invalidated",
            },
        )

    def filter_domains(
        self,
        query: str,
        available_domains: list[str],
        *,
        retrieval_phase: str = RETRIEVAL_PHASE_RISK_ROUTING,
    ) -> list[str]:
        """Identify domains most relevant to the query. Always returns a fresh list."""
        return self._filter_domains_scoped(query, available_domains, retrieval_phase=retrieval_phase).domains

    def _filter_domains_scoped(
        self,
        query: str,
        available_domains: list[str],
        *,
        retrieval_phase: str = RETRIEVAL_PHASE_RISK_ROUTING,
    ) -> _PrefilterOutcome:
        """Identify domains most relevant to the query.

        Returns a local ``_PrefilterOutcome`` (never a reference into ``self._cache``)
        so a caller can freely mutate ``.domains`` without poisoning the cache entry —
        the previous behavior, where ``filter_domains`` handed out the cached list
        object itself, let a caller's in-place domain append leak into every later
        request sharing the same cache key.
        """
        from moralstack.orchestration.orchestration_event_taxonomy import (
            DOMAIN_PREFILTER_CACHE_HIT,
            DOMAIN_PREFILTER_CACHE_MISS,
            DOMAIN_PREFILTER_QUERY_TOO_SHORT,
        )

        stripped_query_len = len(query.strip())
        if stripped_query_len < self.MIN_QUERY_LEN_FOR_CLASSIFICATION:
            _emit_domain_prefilter_orchestration_event(
                DOMAIN_PREFILTER_QUERY_TOO_SHORT,
                {
                    "decision": "bypass",
                    "rationale": "query too short to identify a domain",
                    "query_length": stripped_query_len,
                    "threshold": self.MIN_QUERY_LEN_FOR_CLASSIFICATION,
                    "available_domain_count": len(available_domains),
                },
            )
            return _PrefilterOutcome(domains=[], cache_lookup_hit=None)

        cache_key = hashlib.md5(f"{query}_{','.join(sorted(available_domains))}".encode()).hexdigest()
        domains_to_check = [d for d in available_domains if d not in self.ALWAYS_EVALUATE]
        candidate_domain_count = len(domains_to_check)

        if cache_key in self._cache:
            cached = self._cache[cache_key]
            _emit_domain_prefilter_orchestration_event(
                DOMAIN_PREFILTER_CACHE_HIT,
                {
                    "decision": "hit",
                    "cache_key_digest": cache_key,
                    "matched_domains": list(cached),
                    "candidate_domain_count": candidate_domain_count,
                    "descriptions_fingerprint": self._descriptions_fingerprint,
                },
            )
            return _PrefilterOutcome(domains=list(cached), cache_lookup_hit=True)

        _emit_domain_prefilter_orchestration_event(
            DOMAIN_PREFILTER_CACHE_MISS,
            {
                "decision": "miss",
                "cache_key_digest": cache_key,
                "candidate_domain_count": candidate_domain_count,
                "descriptions_fingerprint": self._descriptions_fingerprint,
            },
        )

        relevant = list(self.ALWAYS_EVALUATE & set(available_domains))

        if not domains_to_check:
            self._cache[cache_key] = relevant
            return _PrefilterOutcome(domains=list(relevant), cache_lookup_hit=False)

        # Precision catalog: the deployer's YAML description split into a positive
        # scope line and an explicit ``NOT:`` exclusion line (from the description's
        # own ``NOT for:`` convention). Scope+NOT keeps the deployer's intent while
        # shortening the prompt (less attention dilution) and sharpening boundaries.
        # A domain without a description is listed by name only (the store always
        # supplies one, so this applies only to a hand-built prefilter).
        def _domain_line(domain: str) -> str:
            desc = (self._domain_descriptions.get(domain) or "").strip()
            if not desc:
                return f"- {domain}"
            scope, notfor = _split_scope_notfor(desc)
            line = f"- {domain}: {scope}."
            if notfor:
                line += f"\n  NOT: {notfor}"
            return line

        domain_list = "\n".join([_domain_line(domain) for domain in sorted(domains_to_check)])

        system_prompt = self._build_prefilter_system_prompt(domain_list)
        user_prompt = self._prompt_set.format("retrieval.prefilter.user", query=query)

        # Strict Structured Outputs when the model supports it: ``domain`` is
        # enum-constrained to this request's candidate set, so an out-of-catalog
        # name is impossible at decode time (not merely filtered after the fact),
        # and the JSON is always well-formed. Unsupported models fall back to the
        # existing json_object mode inside _call_openai.
        response_format = (
            self._build_prefilter_response_format(domains_to_check)
            if supports_json_schema(self.openai_config.model)
            else None
        )

        _record_prefilter_parse_status(None)
        # Dead store on purpose: visible to the except branch (routing fallback audit).
        result: dict[str, Any] = {}
        try:
            result = self._call_openai(
                user_prompt,
                system_prompt=system_prompt,
                response_format=response_format,
                retrieval_phase=retrieval_phase,
            )

            # Per-domain gate (new schema): each selection clears the threshold on
            # its OWN confidence, so a weak secondary no longer rides a strong
            # primary's global score. Legacy single-global-confidence shape is
            # handled unchanged in the elif — the routing-invariance contract for
            # {domains, confidence} replies must not move.
            if result and isinstance(result.get("selections"), list):
                selected = self._selected_from_selections(result)
                valid_selected = [d for d in selected if d in available_domains][: self.max_domains]
                relevant.extend(valid_selected)
            elif result and result.get("confidence", 0) >= self.DOMAIN_CONFIDENCE_THRESHOLD:
                selected = result.get("domains", [])
                valid_selected = [d for d in selected if d in available_domains][: self.max_domains]
                relevant.extend(valid_selected)

            relevant = list(dict.fromkeys(relevant))
            self._cache[cache_key] = relevant
            self._audit_rejected_domains(
                result,
                available_domains=available_domains,
                applied_domains=relevant,
                retrieval_phase=retrieval_phase,
                cache_key=cache_key,
            )
            return _PrefilterOutcome(domains=list(relevant), cache_lookup_hit=False)

        except Exception as e:
            logger.warning(f"DomainPrefilter failed: {e}, returning core only")
            self._audit_rejected_domains(
                result,
                available_domains=available_domains,
                applied_domains=None,
                retrieval_phase=retrieval_phase,
                cache_key=cache_key,
            )
            return _PrefilterOutcome(
                domains=list(self.ALWAYS_EVALUATE & set(available_domains)),
                cache_lookup_hit=False,
            )

    def _audit_rejected_domains(
        self,
        result: Any,
        *,
        available_domains: list[str],
        applied_domains: list[str] | None,
        retrieval_phase: str,
        cache_key: str,
    ) -> None:
        """Best-effort, write-only: emit DOMAIN_PREFILTER_DOMAINS_REJECTED for proposed-but-not-applied domains.

        ``applied_domains=None`` means routing raised and fell back to core-only (not cached).
        Never raises and never influences the returned domains.
        """
        try:
            from moralstack.orchestration.orchestration_event_taxonomy import DOMAIN_PREFILTER_DOMAINS_REJECTED

            status = _PREFILTER_PARSE_STATUS.get()
            if applied_domains is not None:
                applied = list(applied_domains)
            else:
                applied = list(self.ALWAYS_EVALUATE & set(available_domains))
            # Same condition as the routing branch choice: per-domain attribution only for ``selections``.
            selections = result.get("selections") if isinstance(result, dict) else None
            record = _build_prefilter_rejection_record(
                result,
                parse_status=status,
                available_domains=available_domains,
                applied_domains=applied,
                max_domains=self.max_domains,
                threshold=self.DOMAIN_CONFIDENCE_THRESHOLD,
                routing_fallback=applied_domains is None,
                domain_confidences=_selection_confidences(selections) if isinstance(selections, list) else None,
            )
            if record is not None:
                record["retrieval_phase"] = retrieval_phase
                record["cache_key_digest"] = cache_key
                _emit_domain_prefilter_orchestration_event(
                    DOMAIN_PREFILTER_DOMAINS_REJECTED, record, reason_codes=record["reason_codes"]
                )
        except Exception:
            logger.debug("domain prefilter rejection audit failed", exc_info=True)

    def _selected_from_selections(self, result: dict[str, Any]) -> list[str]:
        """Per-domain gate over the ``selections`` array: keep each domain whose
        OWN confidence clears the threshold, ordered by confidence descending and
        de-duplicated. The caller applies ``available_domains`` membership and the
        ``max_domains`` cap, matching the legacy path's post-filter."""
        scored: list[tuple[str, float]] = []
        for sel in result.get("selections") or []:
            parsed = _parse_selection(sel)
            if parsed is not None and parsed[1] >= self.DOMAIN_CONFIDENCE_THRESHOLD:
                scored.append(parsed)
        scored.sort(key=lambda dc: -dc[1])
        ordered: list[str] = []
        for domain, _ in scored:
            if domain not in ordered:
                ordered.append(domain)
        return ordered

    def _build_prefilter_response_format(self, domains_to_check: list[str]) -> ResponseFormatJSONSchema:
        """Strict Structured Outputs schema for the classifier reply.

        ``selections[].domain`` is enum-constrained to ``domains_to_check`` (this
        request's candidates, core already excluded), so the provider cannot emit
        an out-of-catalog name and the reply always parses. Byte-stable for a
        fixed candidate set, so it does not disturb prompt-prefix caching."""
        enum = sorted(domains_to_check)
        return {
            "type": "json_schema",
            "json_schema": {
                "name": "domain_prefilter_selection",
                "strict": True,
                "schema": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["payload", "selections", "wrapper_ignored"],
                    "properties": {
                        "payload": {
                            "type": "string",
                            "description": "One-line paraphrase of the real request, decoded when applicable.",
                        },
                        "selections": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "additionalProperties": False,
                                "required": ["domain", "evidence", "confidence"],
                                "properties": {
                                    "domain": {"type": "string", "enum": enum},
                                    "evidence": {
                                        "type": "string",
                                        "description": "<=15-word span of the real request this domain covers.",
                                    },
                                    "confidence": {"type": "number"},
                                },
                            },
                        },
                        "wrapper_ignored": {"type": "array", "items": {"type": "string"}},
                    },
                },
            },
        }

    def _build_prefilter_system_prompt(self, domain_list: str) -> str:
        """Compose the byte-stable prefilter SYSTEM prompt for the current domain config.

        The content is the prompt-set fragment ``retrieval.prefilter.system`` with the AVAILABLE
        DOMAINS list (rendered from the current descriptions) substituted in. Byte-identical across requests while the
        domain config and available_domains are unchanged, so the prefix is cache-eligible for
        OpenAI automatic prompt caching (an actual cache hit still depends on prompt length /
        provider conditions - byte-stability is necessary, not sufficient). The rendered
        domain_list changes when set_domain_descriptions mutates the
        effective map (which also clears self._cache, keeping local cache and cached prefix
        consistent) or the per-request available_domains differs.
        """
        # The text lives in the prompt set (``retrieval.prefilter.system``, a safety fragment: SAFETY RECALL);
        # the reply shape is enforced by the schema from _build_prefilter_response_format.
        return self._prompt_set.format("retrieval.prefilter.system", domain_list=domain_list, max_domains=self.max_domains)

    def _call_openai(
        self,
        prompt: str,
        *,
        system_prompt: str,
        response_format: ResponseFormatJSONSchema | None = None,
        retrieval_phase: str = RETRIEVAL_PHASE_RISK_ROUTING,
    ) -> dict[str, Any]:
        import time

        from moralstack.utils.json_utils import JSONParseError

        try:
            import openai

            if not self.openai_config.api_key:
                return {}

            key = self.openai_config.api_key
            if self._openai_http_client is None or self._openai_http_client_key != key:
                self._openai_http_client = openai.OpenAI(api_key=key)
                self._openai_http_client_key = key
                self._openai_client_creates += 1
            else:
                self._openai_client_reuses_after_cache += 1
            client = self._openai_http_client
            t0 = time.time()
            started_ms = int(t0 * 1000)
            response = client.chat.completions.create(
                model=self.openai_config.model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.1,
                response_format=response_format or _json_object_response_format(),
                **completion_tokens_param(self.openai_config.model, self.PREFILTER_MAX_OUTPUT_TOKENS),
            )

            raw_usage = response.usage
            token_usage = TokenUsage.from_openai_usage(raw_usage)
            tracker = self._cost_tracker
            if tracker is not None and raw_usage is not None and hasattr(tracker, "add_call"):
                tracker.add_call(self.openai_config.model, token_usage.input_tokens, token_usage.output_tokens)

            text = (response.choices[0].message.content or "").strip()
            elapsed_ms = (time.time() - t0) * 1000
            data: dict[str, Any]
            p_contract: dict[str, Any]
            try:
                data, p_contract = parse_dict_with_contract(text, strict_json_requested=True)
            except JSONParseError:
                json_match = re.search(r"\{[\s\S]*\}", text)
                if json_match:
                    try:
                        raw_obj = json.loads(json_match.group())
                        data = raw_obj if isinstance(raw_obj, dict) else {}
                        p_contract = {
                            "response_contract": "json_object",
                            "strict_json_requested": True,
                            "parse_status": "fallback_ok",
                            "fallback_used": True,
                            "parse_attempts": 1,
                            "retry_count": 0,
                        }
                    except json.JSONDecodeError:
                        data = {}
                        p_contract = {
                            "response_contract": "json_object",
                            "strict_json_requested": True,
                            "parse_status": "failed",
                            "fallback_used": True,
                            "parse_attempts": 1,
                            "retry_count": 0,
                        }
                else:
                    data = {}
                    p_contract = {
                        "response_contract": "json_object",
                        "strict_json_requested": True,
                        "parse_status": "failed",
                        "fallback_used": False,
                        "parse_attempts": 1,
                        "retry_count": 0,
                    }
            # Mirror the per-domain ``selections`` shape onto the legacy
            # ``domains``/``confidence`` fields the rejection audit reads, so the
            # whole audit subsystem keeps working unchanged: ``domains`` = every
            # proposed domain in confidence order (the audit's "proposed"),
            # ``confidence`` = the best per-domain score (keeps the global-gate
            # audit meaningful). The gate itself reads ``selections`` directly.
            if isinstance(data, dict) and isinstance(data.get("selections"), list) and "domains" not in data:
                _sels = [s for s in data["selections"] if isinstance(s, dict)]
                _confs: list[float] = []
                for _s in _sels:
                    try:
                        _c = float(_s.get("confidence", 0))
                    except (TypeError, ValueError):
                        _c = 0.0
                    _s["_conf"] = _c if math.isfinite(_c) else 0.0
                    _confs.append(_s["_conf"])
                _ordered = sorted(_sels, key=lambda s: -s["_conf"])
                data["domains"] = [s.get("domain") for s in _ordered if isinstance(s.get("domain"), str)]
                data["confidence"] = max(_confs) if _confs else 0.0
                for _s in _sels:
                    _s.pop("_conf", None)
            _record_prefilter_parse_status(p_contract)
            cycle_val, seq_val = _RETRIEVAL_PHASE_PERSISTENCE.get(
                retrieval_phase,
                _RETRIEVAL_PHASE_PERSISTENCE[RETRIEVAL_PHASE_RISK_ROUTING],
            )
            _persist_constitution_llm_call(
                action="domain_prefilter",
                system_prompt=system_prompt,
                prompt=prompt,
                raw_response=text,
                duration_ms=elapsed_ms,
                started_at=started_ms,
                parse_contract=p_contract,
                model=self.openai_config.model,
                token_usage_json=token_usage.to_json(),
                retrieval_phase=retrieval_phase,
                cycle=cycle_val,
                sequence_in_cycle=seq_val,
            )
            return data

        except Exception as e:
            logger.debug(f"OpenAI prefilter call failed: {e}")
            return {}


# =============================================================================
# Enhanced Domain Agent
# =============================================================================


class EnhancedDomainAgent:
    """
    Domain agent with improved prompts and confidence scoring.
    """

    # Default re-export of ``retrieval.agent.negative_examples.<domain>``; runtime reads the instance's prompt set.
    DOMAIN_NEGATIVE_EXAMPLES: dict[str, list[str]] = {
        _domain: list(default_prompt_set().lines(f"retrieval.agent.negative_examples.{_domain}"))
        for _domain in ("financial", "research", "journalism", "enterprise", "mental_health", "medical")
    }

    def __init__(
        self,
        domain_name: str,
        principles: list[Principle],
        openai_config: OpenAIClientConfig | None = None,
        domain_description: str = "",
        cost_tracker: Any | None = None,
        prompt_set: PromptSet | None = None,
    ) -> None:
        self.domain_name = domain_name
        self.principles = principles
        self._prompt_set = prompt_set or default_prompt_set()
        self.openai_config = openai_config or OpenAIClientConfig.default()
        self._domain_description = domain_description or f"Principles specific to {domain_name} domain"
        self._cache: dict[str, AgentResult] = {}
        self._cost_tracker = cost_tracker
        self._openai_http_client: Any | None = None
        self._openai_http_client_key: str | None = None
        self._openai_client_creates: int = 0
        self._openai_client_reuses_after_cache: int = 0

    def evaluate(self, query: str, *, retrieval_phase: str = RETRIEVAL_PHASE_RISK_ROUTING) -> AgentResult:
        """Evaluate query and return AgentResult with principles and confidence."""
        if not self.principles:
            return AgentResult(principle_ids=[], confidence=0.0, domain_match=False)

        principles_summary = [{"id": p.id, "title": p.title, "rule": p.rule, "level": p.level} for p in self.principles]
        principles_text = format_principles_for_prompt(
            principles_summary, include_level=True, style="compact", max_rule_len=233
        )

        domain_desc = self._domain_description
        ps = self._prompt_set
        negative_id = f"retrieval.agent.negative_examples.{self.domain_name}"
        negative_examples = ps.lines(negative_id) if ps.has(negative_id) else ()
        negative_section = ""
        if negative_examples:
            negative_section = ps.format(
                "retrieval.agent.negative_section", examples="\n".join(f"- {ex}" for ex in negative_examples)
            )

        prompt = ps.format(
            "retrieval.agent.enhanced.user",
            domain_upper=self.domain_name.upper(),
            domain_desc=domain_desc,
            query=query,
            principles_text=principles_text,
            domain_name=self.domain_name,
            negative_section=negative_section,
        )

        cache_key = _domain_agent_cache_key(
            model=self.openai_config.model,
            system_prompt=ps.text("retrieval.agent.enhanced.system"),
            user_prompt=prompt,
            max_output_tokens=_ENHANCED_DOMAIN_AGENT_MAX_OUTPUT_TOKENS,
        )
        if cache_key in self._cache:
            return self._cache[cache_key]

        try:
            result_data = self._call_openai(prompt, retrieval_phase=retrieval_phase)

            domain_match = result_data.get("domain_match", False)
            confidence = float(result_data.get("confidence", 0.0))
            principle_ids = result_data.get("principle_ids", [])
            reasoning = result_data.get("reasoning", "")

            valid_ids = [pid for pid in principle_ids if any(p.id == pid for p in self.principles)]

            result = AgentResult(
                principle_ids=valid_ids,
                confidence=confidence,
                domain_match=domain_match,
                reasoning=reasoning,
            )

            self._cache[cache_key] = result
            return result

        except Exception as e:
            logger.warning(f"EnhancedDomainAgent {self.domain_name} evaluation failed: {e}")
            return AgentResult(principle_ids=[], confidence=0.0, domain_match=False, reasoning=str(e))

    def _call_openai(self, prompt: str, *, retrieval_phase: str = RETRIEVAL_PHASE_RISK_ROUTING) -> dict[str, Any]:
        import time

        from moralstack.utils.json_utils import JSONParseError

        try:
            import openai

            if not self.openai_config.api_key:
                return {}

            key = self.openai_config.api_key
            if self._openai_http_client is None or self._openai_http_client_key != key:
                self._openai_http_client = openai.OpenAI(api_key=key)
                self._openai_http_client_key = key
                self._openai_client_creates += 1
            else:
                self._openai_client_reuses_after_cache += 1
            client = self._openai_http_client
            sys_msg = self._prompt_set.text("retrieval.agent.enhanced.system")
            t0 = time.time()
            started_ms = int(t0 * 1000)
            response = client.chat.completions.create(
                model=self.openai_config.model,
                messages=_domain_agent_messages(sys_msg, prompt),
                temperature=_DOMAIN_AGENT_TEMPERATURE,
                response_format=_json_object_response_format(),
                **completion_tokens_param(self.openai_config.model, _ENHANCED_DOMAIN_AGENT_MAX_OUTPUT_TOKENS),
            )

            raw_usage = response.usage
            token_usage = TokenUsage.from_openai_usage(raw_usage)
            tracker = self._cost_tracker
            if tracker is not None and raw_usage is not None and hasattr(tracker, "add_call"):
                tracker.add_call(self.openai_config.model, token_usage.input_tokens, token_usage.output_tokens)

            text = (response.choices[0].message.content or "").strip()
            elapsed_ms = (time.time() - t0) * 1000
            data: dict[str, Any]
            p_contract: dict[str, Any]
            try:
                data, p_contract = parse_dict_with_contract(text, strict_json_requested=True)
            except JSONParseError:
                json_match = re.search(r"\{[\s\S]*\}", text)
                if json_match:
                    try:
                        raw_obj = json.loads(json_match.group())
                        data = raw_obj if isinstance(raw_obj, dict) else {}
                        p_contract = {
                            "response_contract": "json_object",
                            "strict_json_requested": True,
                            "parse_status": "fallback_ok",
                            "fallback_used": True,
                            "parse_attempts": 1,
                            "retry_count": 0,
                        }
                    except json.JSONDecodeError:
                        data = {}
                        p_contract = {
                            "response_contract": "json_object",
                            "strict_json_requested": True,
                            "parse_status": "failed",
                            "fallback_used": True,
                            "parse_attempts": 1,
                            "retry_count": 0,
                        }
                else:
                    data = {}
                    p_contract = {
                        "response_contract": "json_object",
                        "strict_json_requested": True,
                        "parse_status": "failed",
                        "fallback_used": False,
                        "parse_attempts": 1,
                        "retry_count": 0,
                    }
            _persist_constitution_llm_call(
                action="enhanced_domain_agent",
                system_prompt=sys_msg,
                prompt=prompt,
                raw_response=text,
                duration_ms=elapsed_ms,
                started_at=started_ms,
                parse_contract=p_contract,
                model=self.openai_config.model,
                token_usage_json=token_usage.to_json(),
                retrieval_phase=retrieval_phase,
                domain=self.domain_name,
            )
            return data

        except Exception as e:
            logger.debug(f"OpenAI agent call failed: {e}")
            return {}


# =============================================================================
# Legacy Domain Agent
# =============================================================================


class DomainAgent:
    """
    Legacy domain agent for principle evaluation.
    """

    def __init__(
        self,
        domain_name: str,
        principles: list[Principle],
        openai_config: OpenAIClientConfig | None = None,
        cost_tracker: Any | None = None,
        prompt_set: PromptSet | None = None,
    ) -> None:
        self.domain_name = domain_name
        self.principles = principles
        self._prompt_set = prompt_set or default_prompt_set()
        self.openai_config = openai_config or OpenAIClientConfig.default()
        self._cost_tracker = cost_tracker
        self._cache: dict[str, list[str]] = {}
        self._openai_http_client: Any | None = None
        self._openai_http_client_key: str | None = None
        self._openai_client_creates: int = 0
        self._openai_client_reuses_after_cache: int = 0

    def evaluate(self, query: str, *, retrieval_phase: str = RETRIEVAL_PHASE_RISK_ROUTING) -> list[str]:
        """Evaluate query and return relevant principle IDs."""
        if not self.principles:
            return []

        principles_summary = [{"id": p.id, "title": p.title, "rule": p.rule, "level": p.level} for p in self.principles]
        principles_text = format_principles_for_prompt(
            principles_summary, include_level=True, style="compact", max_rule_len=233
        )

        prompt = self._prompt_set.format(
            "retrieval.agent.legacy.user",
            domain_upper=self.domain_name.upper(),
            query=query,
            principles_text=principles_text,
        )

        cache_key = _domain_agent_cache_key(
            model=self.openai_config.model,
            system_prompt=self._prompt_set.text("retrieval.agent.legacy.system"),
            user_prompt=prompt,
            max_output_tokens=_LEGACY_DOMAIN_AGENT_MAX_OUTPUT_TOKENS,
        )
        if cache_key in self._cache:
            return self._cache[cache_key]

        try:
            result_ids = self._call_openai(prompt, retrieval_phase=retrieval_phase)

            valid_ids = [pid for pid in result_ids if any(p.id == pid for p in self.principles)]
            self._cache[cache_key] = valid_ids
            return valid_ids

        except Exception as e:
            logger.warning(f"DomainAgent {self.domain_name} evaluation failed: {e}")
            return []

    def _call_openai(self, prompt: str, *, retrieval_phase: str = RETRIEVAL_PHASE_RISK_ROUTING) -> list[str]:
        import time

        from moralstack.utils.json_utils import JSONParseError

        try:
            import openai

            if not self.openai_config.api_key:
                return []

            key = self.openai_config.api_key
            if self._openai_http_client is None or self._openai_http_client_key != key:
                self._openai_http_client = openai.OpenAI(api_key=key)
                self._openai_http_client_key = key
                self._openai_client_creates += 1
            else:
                self._openai_client_reuses_after_cache += 1
            client = self._openai_http_client
            sys_msg = self._prompt_set.text("retrieval.agent.legacy.system")
            t0 = time.time()
            started_ms = int(t0 * 1000)
            response = client.chat.completions.create(
                model=self.openai_config.model,
                messages=_domain_agent_messages(sys_msg, prompt),
                temperature=_DOMAIN_AGENT_TEMPERATURE,
                response_format=_json_object_response_format(),
                **completion_tokens_param(self.openai_config.model, _LEGACY_DOMAIN_AGENT_MAX_OUTPUT_TOKENS),
            )

            raw_usage = response.usage
            token_usage = TokenUsage.from_openai_usage(raw_usage)
            tracker = self._cost_tracker
            if tracker is not None and raw_usage is not None and hasattr(tracker, "add_call"):
                tracker.add_call(self.openai_config.model, token_usage.input_tokens, token_usage.output_tokens)

            text = (response.choices[0].message.content or "").strip()
            elapsed_ms = (time.time() - t0) * 1000
            try:
                ids, p_contract = parse_principle_id_list_with_contract(text, strict_json_requested=True)
            except JSONParseError:
                ids = []
                p_contract = {
                    "response_contract": "json_object",
                    "strict_json_requested": True,
                    "parse_status": "failed",
                    "fallback_used": True,
                    "parse_attempts": 1,
                    "retry_count": 0,
                }
            _persist_constitution_llm_call(
                action="legacy_domain_agent",
                system_prompt=sys_msg,
                prompt=prompt,
                raw_response=text,
                duration_ms=elapsed_ms,
                started_at=started_ms,
                parse_contract=p_contract,
                model=self.openai_config.model,
                token_usage_json=token_usage.to_json(),
                retrieval_phase=retrieval_phase,
                domain=self.domain_name,
            )
            return ids

        except Exception as e:
            logger.debug(f"OpenAI agent call failed: {e}")
            return []


# =============================================================================
# Constitution Retriever Config
# =============================================================================


@dataclass
class ConstitutionRetrieverConfig:
    """Configuration for ConstitutionRetriever."""

    openai_config: OpenAIClientConfig | None = None
    max_parallel_agents: int = 4
    use_enhanced_retrieval: bool = True
    confidence_threshold: float = 0.6
    use_domain_prefilter: bool = True
    max_prefilter_domains: int = 3
    # A PromptSet, or None for the packaged defaults.
    prompt_set: PromptSet | None = None


# =============================================================================
# Constitution Retriever
# =============================================================================


class ConstitutionRetriever:
    """
    Encapsulates agent-based retrieval of relevant principles.

    Delegates to DomainPrefilter, DomainAgent, EnhancedDomainAgent.
    Uses parallel execution with configurable batch size.
    """

    DEFAULT_CONFIDENCE_THRESHOLD = 0.6

    def __init__(
        self,
        config: ConstitutionRetrieverConfig,
        data_provider: ConstitutionDataProvider,
        cost_tracker: Any | None = None,
    ) -> None:
        self._config = config
        self._provider = data_provider
        self._cost_tracker = cost_tracker

        self._domain_agents: dict[str, DomainAgent] = {}
        self._enhanced_agents: dict[str, EnhancedDomainAgent] = {}
        self._domain_prefilter: DomainPrefilter | None = None

        if config.use_domain_prefilter:
            self._domain_prefilter = DomainPrefilter(
                openai_config=config.openai_config or OpenAIClientConfig.default(),
                max_domains=config.max_prefilter_domains,
                cost_tracker=cost_tracker,
                domain_descriptions=data_provider.get_domain_descriptions(),
                prompt_set=config.prompt_set,
            )

    def set_cost_tracker(self, tracker: Any | None) -> None:
        """Set TokenCostTracker for cost tracking."""
        self._cost_tracker = tracker
        self._enhanced_agents.clear()
        self._domain_agents.clear()
        prefilter = self._domain_prefilter
        if prefilter is not None and hasattr(prefilter, "set_cost_tracker"):
            prefilter.set_cost_tracker(tracker)

    def invalidate_cache(self) -> None:
        """Invalidate all caches (agents, prefilter)."""
        self._domain_agents.clear()
        self._enhanced_agents.clear()
        if self._domain_prefilter is not None and hasattr(self._domain_prefilter, "clear_cache"):
            self._domain_prefilter.clear_cache(reason="forced_refresh")

    def retrieve(
        self,
        query: str,
        top_k: int = 10,
        domain: str | None = None,
        *,
        retrieval_phase: str = RETRIEVAL_PHASE_RISK_ROUTING,
    ) -> PrincipleRetrievalResult:
        """
        Retrieve relevant principles via parallel domain agents.

        Returns a frozen ``PrincipleRetrievalResult``: ``principles`` ordered by
        relevance (max top_k), ``prefiltered_domains`` (the decision channel — the
        raw prefilter output, including ``"core"``; the caller owns the ``core``
        exclusion) and ``debug_info`` (best-effort telemetry, same shape as the
        retired per-retrieval debug accessor this replaces). Writes no instance
        state — every per-request value travels on this return value only.
        """
        query_tokens = tokenize(query)

        if not query_tokens:
            core = self._provider.load_core()
            principles = sorted(core, key=lambda p: -p.priority)[:top_k]
            # Returns before the local `debug` dict below is built — its own
            # dict, so this exit path is marked too (plan §6 point 5): an
            # unmarked empty-query result would be indistinguishable from a
            # legacy store on the audit trail.
            return PrincipleRetrievalResult(
                principles=tuple(principles),
                debug_info={"domain_channel": "retrieve"},
            )

        available_domains = ["core"] + self._provider._get_available_domains()

        prefilter_desc_changed = False
        prefilter_cache_hit: bool | None = None
        if self._config.use_enhanced_retrieval and self._config.use_domain_prefilter and self._domain_prefilter:
            assert self._domain_prefilter is not None
            prefilter_desc_changed = self._domain_prefilter.set_domain_descriptions(self._provider.get_domain_descriptions())
            outcome = self._domain_prefilter._filter_domains_scoped(
                query,
                available_domains,
                retrieval_phase=retrieval_phase,
            )
            # Local copy, never the prefilter's cache entry: appending `domain`
            # below must never mutate what a concurrent request's cache lookup
            # could return (retriever.py cache-alias channel, T4/T8).
            relevant_domains = list(outcome.domains)
            prefilter_cache_hit = outcome.cache_lookup_hit
            if domain and domain not in relevant_domains:
                relevant_domains.append(domain)
        else:
            relevant_domains = list(available_domains)

        prefilter_status = (
            _prefilter_combined_cache_status(prefilter_desc_changed, prefilter_cache_hit)
            if self._config.use_enhanced_retrieval and self._config.use_domain_prefilter and self._domain_prefilter
            else "n/a"
        )
        inv_reason = (
            "effective_descriptions_changed" if prefilter_desc_changed and self._domain_prefilter is not None else None
        )

        debug: dict[str, Any] = {
            # Single source of truth (plan §6 point 5): stamped once here, so the
            # two no-agents fallback returns and the normal return below all
            # inherit it from this one dict instead of each needing its own line.
            "domain_channel": "retrieve",
            "use_enhanced_retrieval": self._config.use_enhanced_retrieval,
            "use_domain_prefilter": self._config.use_domain_prefilter,
            "available_domains": available_domains,
            "prefiltered_domains": relevant_domains,
            "confidence_threshold": self._config.confidence_threshold,
            "prefilter_cache_status": prefilter_status,
            "prefilter_descriptions_changed": (
                bool(prefilter_desc_changed)
                if self._config.use_enhanced_retrieval and self._config.use_domain_prefilter and self._domain_prefilter
                else None
            ),
            "prefilter_cache_invalidation_reason": inv_reason,
            "prefilter_cache_lookup_hit": prefilter_cache_hit,
            "prefilter_descriptions_fingerprint_prefix": (
                (self._domain_prefilter._descriptions_fingerprint[:16] if self._domain_prefilter else "")
                if self._config.use_enhanced_retrieval and self._config.use_domain_prefilter
                else ""
            ),
        }

        all_principle_ids: set[str] = set()

        if self._config.use_enhanced_retrieval:
            agents = self._create_enhanced_agents(relevant_domains)

            debug.update(
                {
                    "agents_created": len(agents),
                    "agent_domains": [a.domain_name for a in agents],
                    "agent_principles_count": {a.domain_name: len(a.principles) for a in agents},
                }
            )

            if not agents:
                core = self._provider.load_core()
                debug["fallback"] = True
                principles = sorted(core, key=lambda p: -p.priority)[:top_k]
                return PrincipleRetrievalResult(
                    principles=tuple(principles),
                    prefiltered_domains=tuple(relevant_domains),
                    debug_info=debug,
                )

            agent_results = self._run_enhanced_agents_parallel(agents, query, retrieval_phase=retrieval_phase)

            filtered_results: dict[str, AgentResult] = {}
            rejected_results: dict[str, dict[str, Any]] = {}

            for domain_name, result in agent_results.items():
                if result.domain_match and result.confidence >= self._config.confidence_threshold:
                    all_principle_ids.update(result.principle_ids)
                    filtered_results[domain_name] = result
                else:
                    rejected_results[domain_name] = {
                        "confidence": result.confidence,
                        "domain_match": result.domain_match,
                        "reasoning": result.reasoning,
                        "principle_count": len(result.principle_ids),
                    }

            debug.update(
                {
                    "agent_results": {
                        d: {
                            "confidence": r.confidence,
                            "domain_match": r.domain_match,
                            "principles_count": len(r.principle_ids),
                        }
                        for d, r in agent_results.items()
                    },
                    "accepted_domains": list(filtered_results.keys()),
                    "rejected_domains": rejected_results,
                    "total_principles_found": len(all_principle_ids),
                }
            )

        else:
            legacy_agents = self._create_domain_agents()

            debug.update(
                {
                    "agents_created": len(legacy_agents),
                    "agent_domains": [a.domain_name for a in legacy_agents],
                    "agent_principles_count": {a.domain_name: len(a.principles) for a in legacy_agents},
                }
            )

            if not legacy_agents:
                core = self._provider.load_core()
                debug["fallback"] = True
                principles = sorted(core, key=lambda p: -p.priority)[:top_k]
                return PrincipleRetrievalResult(
                    principles=tuple(principles),
                    prefiltered_domains=tuple(relevant_domains),
                    debug_info=debug,
                )

            legacy_results = self._run_agents_parallel(legacy_agents, query, retrieval_phase=retrieval_phase)

            for domain_name, principle_ids in legacy_results.items():
                all_principle_ids.update(principle_ids)

            debug.update(
                {
                    "agent_results": {d: len(ids) for d, ids in legacy_results.items()},
                    "total_principles_found": len(all_principle_ids),
                }
            )

        all_principles_map: dict[str, Principle] = {}

        for p in self._provider.load_core():
            all_principles_map[p.id] = p

        for domain_name in self._provider._get_available_domains():
            try:
                overlay = self._provider.load_overlay(domain_name)
                for p in overlay.additional_principles:
                    all_principles_map[p.id] = p
            except FileNotFoundError:
                continue

        relevant_principles = [all_principles_map[pid] for pid in all_principle_ids if pid in all_principles_map]

        for domain_name in self._provider._get_available_domains():
            try:
                overlay = self._provider.load_overlay(domain_name)
                priority_map = overlay.priority_overrides
                for i, p in enumerate(relevant_principles):
                    if p.id in priority_map:
                        relevant_principles[i] = p.model_copy(update={"priority": priority_map[p.id]})
            except FileNotFoundError:
                continue

        relevant_principles = resolve_conflict(relevant_principles)

        debug.update(
            {
                "final_principles_count": len(relevant_principles),
                "principles_by_domain": self._get_principles_by_domain(relevant_principles),
                "retrieval_openai_client_pooling": self._snapshot_retrieval_openai_pooling(),
            }
        )

        return PrincipleRetrievalResult(
            principles=tuple(relevant_principles[:top_k]),
            prefiltered_domains=tuple(relevant_domains),
            debug_info=debug,
        )

    def get_relevant_principles(
        self,
        query: str,
        top_k: int = 10,
        domain: str | None = None,
        *,
        retrieval_phase: str = RETRIEVAL_PHASE_RISK_ROUTING,
    ) -> list[Principle]:
        """
        Retrieve relevant principles via parallel domain agents.

        Returns list of principles ordered by relevance (max top_k). Pure
        projection of ``retrieve()`` — writes no instance state.
        """
        return list(self.retrieve(query, top_k=top_k, domain=domain, retrieval_phase=retrieval_phase).principles)

    def _get_principles_by_domain(self, principles: list[Principle]) -> dict[str, int]:
        by_domain: dict[str, int] = {}
        for p in principles:
            domain = p.domain or "core"
            by_domain[domain] = by_domain.get(domain, 0) + 1
        return by_domain

    def _snapshot_retrieval_openai_pooling(self) -> dict[str, Any]:
        """
        Low-noise diagnostics: aggregate OpenAI HTTP client reuse across prefilter and agents.

        Instance-scoped clients; counts are creates vs. subsequent uses of the same client.
        """
        total_creates = 0
        total_reuses = 0
        if self._domain_prefilter is not None:
            pf = self._domain_prefilter
            total_creates += int(getattr(pf, "_openai_client_creates", 0))
            total_reuses += int(getattr(pf, "_openai_client_reuses_after_cache", 0))
        for ag in self._domain_agents.values():
            total_creates += int(getattr(ag, "_openai_client_creates", 0))
            total_reuses += int(getattr(ag, "_openai_client_reuses_after_cache", 0))
        for enhanced_ag in self._enhanced_agents.values():
            total_creates += int(getattr(enhanced_ag, "_openai_client_creates", 0))
            total_reuses += int(getattr(enhanced_ag, "_openai_client_reuses_after_cache", 0))
        return {
            "retrieval_openai_client_creates": total_creates,
            "retrieval_openai_client_reuses_after_cache": total_reuses,
            "retrieval_client_reused": total_reuses > 0,
        }

    def detect_relevant_domains(self, query: str) -> list[str]:
        """Return domains relevant to the query, ordered by relevance."""
        try:
            available = ["core"] + self._provider._get_available_domains()
            if self._domain_prefilter is not None:
                _ = self._domain_prefilter.set_domain_descriptions(self._provider.get_domain_descriptions())
                return self._domain_prefilter.filter_domains(query, available)
            return []
        except Exception:
            return []

    def _create_domain_agents(self) -> list[DomainAgent]:
        agents = []
        core_principles = self._provider.load_core()
        openai_cfg = self._config.openai_config or OpenAIClientConfig.default()

        if core_principles:
            if "core" not in self._domain_agents:
                self._domain_agents["core"] = DomainAgent(
                    domain_name="core",
                    principles=core_principles,
                    openai_config=openai_cfg,
                    cost_tracker=self._cost_tracker,
                    prompt_set=self._config.prompt_set,
                )
            agents.append(self._domain_agents["core"])

        for domain_name in self._provider._get_available_domains():
            try:
                overlay = self._provider.load_overlay(domain_name)
                if overlay.additional_principles:
                    if domain_name not in self._domain_agents:
                        self._domain_agents[domain_name] = DomainAgent(
                            domain_name=domain_name,
                            principles=overlay.additional_principles,
                            openai_config=openai_cfg,
                            cost_tracker=self._cost_tracker,
                            prompt_set=self._config.prompt_set,
                        )
                    agents.append(self._domain_agents[domain_name])
            except FileNotFoundError:
                continue

        return agents

    def _create_enhanced_agents(self, domains: list[str]) -> list[EnhancedDomainAgent]:
        agents = []
        domain_descriptions = self._provider.get_domain_descriptions()
        openai_cfg = self._config.openai_config or OpenAIClientConfig.default()

        for domain_name in domains:
            if domain_name == "core":
                core_principles = self._provider.load_core()
                if core_principles:
                    if "core" not in self._enhanced_agents:
                        self._enhanced_agents["core"] = EnhancedDomainAgent(
                            domain_name="core",
                            principles=core_principles,
                            openai_config=openai_cfg,
                            domain_description=domain_descriptions.get("core", ""),
                            cost_tracker=self._cost_tracker,
                            prompt_set=self._config.prompt_set,
                        )
                    agents.append(self._enhanced_agents["core"])
            else:
                try:
                    overlay = self._provider.load_overlay(domain_name)
                    if overlay.additional_principles:
                        if domain_name not in self._enhanced_agents:
                            self._enhanced_agents[domain_name] = EnhancedDomainAgent(
                                domain_name=domain_name,
                                principles=overlay.additional_principles,
                                openai_config=openai_cfg,
                                domain_description=overlay.description or domain_descriptions.get(domain_name, ""),
                                cost_tracker=self._cost_tracker,
                                prompt_set=self._config.prompt_set,
                            )
                        agents.append(self._enhanced_agents[domain_name])
                except FileNotFoundError:
                    continue

        return agents

    def _run_enhanced_agents_parallel(
        self,
        agents: list[EnhancedDomainAgent],
        query: str,
        *,
        retrieval_phase: str = RETRIEVAL_PHASE_RISK_ROUTING,
    ) -> dict[str, AgentResult]:
        results: dict[str, AgentResult] = {}
        batch_size = self._config.max_parallel_agents

        for i in range(0, len(agents), batch_size):
            batch = agents[i : i + batch_size]
            with concurrent.futures.ThreadPoolExecutor(max_workers=len(batch)) as executor:
                # copy_context() runs in this (main) thread, so each worker inherits the
                # observability context (run_id/request_id/cycle/session/turn). Without it
                # the per-domain llm_calls persist orphaned (no run/request id) and their
                # tokens are never attributed to the request. One snapshot per submit — a
                # Context object cannot be entered concurrently by multiple threads.
                future_to_agent = {
                    executor.submit(
                        contextvars.copy_context().run,
                        functools.partial(agent.evaluate, query, retrieval_phase=retrieval_phase),
                    ): agent
                    for agent in batch
                }
                for future in concurrent.futures.as_completed(future_to_agent):
                    agent = future_to_agent[future]
                    try:
                        agent_result = future.result()
                        results[agent.domain_name] = agent_result
                    except Exception as e:
                        logger.warning(f"EnhancedAgent {agent.domain_name} failed: {e}")
                        results[agent.domain_name] = AgentResult(
                            principle_ids=[], confidence=0.0, domain_match=False, reasoning=str(e)
                        )

        return results

    def _run_agents_parallel(
        self,
        agents: list[DomainAgent],
        query: str,
        *,
        retrieval_phase: str = RETRIEVAL_PHASE_RISK_ROUTING,
    ) -> dict[str, list[str]]:
        results: dict[str, list[str]] = {}
        batch_size = self._config.max_parallel_agents

        for i in range(0, len(agents), batch_size):
            batch = agents[i : i + batch_size]
            with concurrent.futures.ThreadPoolExecutor(max_workers=len(batch)) as executor:
                # See _run_enhanced_agents_parallel: propagate observability context into
                # worker threads so legacy per-domain llm_calls carry run/request ids.
                future_to_agent = {
                    executor.submit(
                        contextvars.copy_context().run,
                        functools.partial(agent.evaluate, query, retrieval_phase=retrieval_phase),
                    ): agent
                    for agent in batch
                }
                for future in concurrent.futures.as_completed(future_to_agent):
                    agent = future_to_agent[future]
                    try:
                        principle_ids = future.result()
                        results[agent.domain_name] = principle_ids
                    except Exception as e:
                        logger.warning(f"Agent {agent.domain_name} failed: {e}")
                        results[agent.domain_name] = []

        return results
