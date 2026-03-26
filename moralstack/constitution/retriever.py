"""
Constitution Retriever - Agent-based retrieval of relevant principles.

Encapsulates: domain prefilter, domain agents, enhanced agents,
parallel execution, and get_relevant_principles internals.
"""

from __future__ import annotations

import concurrent.futures
import hashlib
import json
import logging
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol

from moralstack.constitution.helpers import resolve_conflict, tokenize
from moralstack.constitution.openai_config import OpenAIClientConfig
from moralstack.constitution.prompt_formatter import format_principles_for_prompt
from moralstack.constitution.schema import Overlay, Principle
from moralstack.utils.openai_params import completion_tokens_param

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


# =============================================================================
# Data Provider Protocol
# =============================================================================


class ConstitutionDataProvider(Protocol):
    """Protocol for constitution data (core, overlays, domain metadata)."""

    def load_core(self) -> list[Principle]: ...

    def load_overlay(self, domain: str) -> Overlay: ...

    def _get_available_domains(self) -> list[str]: ...

    def get_domain_keywords(self) -> dict[str, list[str]]: ...

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


class DomainPrefilter:
    """
    Pre-filter relevant domains before running agents.

    Two-stage retrieval: first identify relevant domains, then run only those agents.
    """

    ALWAYS_EVALUATE = {"core"}
    DOMAIN_CONFIDENCE_THRESHOLD = 0.5

    def __init__(
        self,
        openai_config: OpenAIClientConfig | None = None,
        max_domains: int = 3,
        domain_keywords: dict[str, list[str]] | None = None,
        cost_tracker: Any | None = None,
    ) -> None:
        self.openai_config = openai_config or OpenAIClientConfig.default()
        self.max_domains = max_domains
        self._domain_keywords = domain_keywords or {}
        self._cache: dict[str, list[str]] = {}
        self._cost_tracker = cost_tracker

    def set_cost_tracker(self, tracker: Any | None) -> None:
        """Set TokenCostTracker for OpenAI call cost tracking."""
        self._cost_tracker = tracker

    def set_domain_keywords(self, keywords: dict[str, list[str]]) -> None:
        """Update domain keywords. Invalidates cache."""
        self._domain_keywords = keywords
        self._cache.clear()

    def filter_domains(self, query: str, available_domains: list[str]) -> list[str]:
        """Identify domains most relevant to the query."""
        cache_key = hashlib.md5(f"{query}_{','.join(sorted(available_domains))}".encode()).hexdigest()
        if cache_key in self._cache:
            return self._cache[cache_key]

        relevant = list(self.ALWAYS_EVALUATE & set(available_domains))
        domains_to_check = [d for d in available_domains if d not in self.ALWAYS_EVALUATE]

        if not domains_to_check:
            return relevant

        domain_list = "\n".join(
            [f"- {domain}: {', '.join(self._domain_keywords.get(domain, []))}" for domain in sorted(domains_to_check)]
        )

        prompt = f"""
USER QUERY:
"{query}"

AVAILABLE DOMAINS:
{domain_list}

TASK:
Select up to {self.max_domains} domains directly relevant to the query.

Rules:
- Select ONLY direct semantic matches.
- If uncertain, omit.
- Relationships/personal communication/dating/friendship → include "relationships".
- Include "financial" only for money/investing/financial products.
- Include "journalism" only for news/media.
- Include "research" only for academic/scientific research.
- Include "enterprise" only for corporate/business context.

Return JSON ONLY:
{{"domains": ["..."], "confidence": 0.0-1.0}}
"""

        try:
            result = self._call_openai(prompt)

            if result and result.get("confidence", 0) >= self.DOMAIN_CONFIDENCE_THRESHOLD:
                selected = result.get("domains", [])
                valid_selected = [d for d in selected if d in available_domains][: self.max_domains]
                relevant.extend(valid_selected)

            relevant = list(dict.fromkeys(relevant))
            self._cache[cache_key] = relevant
            return relevant

        except Exception as e:
            logger.warning(f"DomainPrefilter failed: {e}, returning core only")
            return list(self.ALWAYS_EVALUATE & set(available_domains))

    def _call_openai(self, prompt: str) -> dict[str, Any]:
        try:
            import openai

            if not self.openai_config.api_key:
                return {}

            client = openai.OpenAI(api_key=self.openai_config.api_key)

            response = client.chat.completions.create(
                model=self.openai_config.model,
                messages=[
                    {
                        "role": "system",
                        "content": ("You are a strict domain classifier. " "Always respond with valid JSON only."),
                    },
                    {"role": "user", "content": prompt},
                ],
                temperature=0.1,
                **completion_tokens_param(self.openai_config.model, 200),
            )

            usage = response.usage
            has_tracker = self._cost_tracker is not None and usage and hasattr(self._cost_tracker, "add_call")
            if has_tracker:
                pt = getattr(usage, "prompt_tokens", None)
                ct = getattr(usage, "completion_tokens", None)
                total = getattr(usage, "total_tokens", 0) or 0
                if pt is None or ct is None:
                    pt = int(total * 0.7) if total else 0
                    ct = total - pt if total else 0
                self._cost_tracker.add_call(self.openai_config.model, pt, ct)

            text = response.choices[0].message.content.strip()
            json_match = re.search(r"\{[\s\S]*\}", text)
            if json_match:
                return json.loads(json_match.group())
            return {}

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

    DOMAIN_NEGATIVE_EXAMPLES: dict[str, list[str]] = {
        "financial": [
            "Personal honesty/communication → NOT financial",
            "Emotional topics → NOT financial",
            "Relationships → NOT financial",
        ],
        "research": [
            "Personal questions → NOT research",
            "Emotional/relationship topics → NOT research",
            "General knowledge questions → NOT research",
        ],
        "journalism": [
            "Personal communication → NOT journalism",
            "Creative writing (fiction) → NOT journalism",
            "Personal opinions → NOT journalism",
        ],
        "enterprise": [
            "Personal relationships → NOT enterprise",
            "Family matters → NOT enterprise",
            "Personal finance → NOT enterprise",
        ],
        "mental_health": [
            "General happiness/sadness → check if clinical",
            "Relationship advice without distress → NOT mental_health",
        ],
        "medical": [
            "Emotional well-being → mental_health, NOT medical",
            "Relationship stress → NOT medical",
        ],
    }

    def __init__(
        self,
        domain_name: str,
        principles: list[Principle],
        openai_config: OpenAIClientConfig | None = None,
        domain_description: str = "",
        cost_tracker: Any | None = None,
    ) -> None:
        self.domain_name = domain_name
        self.principles = principles
        self.openai_config = openai_config or OpenAIClientConfig.default()
        self._domain_description = domain_description or f"Principles specific to {domain_name} domain"
        self._cache: dict[str, AgentResult] = {}
        self._cost_tracker = cost_tracker

    def evaluate(self, query: str) -> AgentResult:
        """Evaluate query and return AgentResult with principles and confidence."""
        cache_key = hashlib.md5(f"{query}_{len(self.principles)}".encode()).hexdigest()
        if cache_key in self._cache:
            return self._cache[cache_key]

        if not self.principles:
            return AgentResult(principle_ids=[], confidence=0.0, domain_match=False)

        principles_summary = [{"id": p.id, "title": p.title, "rule": p.rule, "level": p.level} for p in self.principles]
        principles_text = format_principles_for_prompt(
            principles_summary, include_level=True, style="compact", max_rule_len=233
        )

        domain_desc = self._domain_description
        negative_examples = self.DOMAIN_NEGATIVE_EXAMPLES.get(self.domain_name, [])
        negative_section = ""
        if negative_examples:
            negative_section = "\n\nNEGATIVE EXAMPLES (DO NOT MATCH):\n" + "\n".join(f"- {ex}" for ex in negative_examples)

        prompt = f"""You are a STRICT domain-specific ethical principle evaluator.

DOMAIN: {self.domain_name.upper()}
DOMAIN DESCRIPTION: {domain_desc}

USER QUERY (in any language): "{query}"

ETHICAL PRINCIPLES FOR THIS DOMAIN:
{principles_text}

CRITICAL EVALUATION RULES:

STEP 1 - DOMAIN RELEVANCE CHECK:
First, determine if this query is ACTUALLY about {self.domain_name}:
- If the query topic does NOT match this domain → set domain_match=false, return empty list
- If UNCERTAIN whether query matches domain → set domain_match=false, return empty list
- ONLY proceed if query CLEARLY relates to {self.domain_name}
{negative_section}

STEP 2 - PRINCIPLE SELECTION (only if domain_match=true):
- Select ONLY principles that DIRECTLY address the query's core intent
- Prioritize HARD constraints [H] over SOFT norms [S]
- Order by relevance, HARD constraints first
- Maximum 5 principles per domain

STEP 3 - CONFIDENCE SCORING:
- 0.9-1.0: Query perfectly matches domain and principles
- 0.7-0.9: Query clearly matches domain
- 0.5-0.7: Query somewhat matches domain
- 0.0-0.5: Query does not match domain well → return empty list

Return JSON ONLY:
{{
    "domain_match": true/false,
    "confidence": 0.0-1.0,
    "principle_ids": ["ID1", "ID2", ...],
    "reasoning": "brief explanation"
}}

If domain does not match, return:
{{"domain_match": false, "confidence": 0.0, "principle_ids": [],
 "reasoning": "query not about this domain"}}

Output valid JSON only:"""

        try:
            result_data = self._call_openai(prompt)

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

    def _call_openai(self, prompt: str) -> dict[str, Any]:
        try:
            import openai

            if not self.openai_config.api_key:
                return {}

            client = openai.OpenAI(api_key=self.openai_config.api_key)

            response = client.chat.completions.create(
                model=self.openai_config.model,
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "You are a STRICT semantic matching system. "
                            "Be conservative - when uncertain, return empty results. "
                            "Always respond with valid JSON only."
                        ),
                    },
                    {"role": "user", "content": prompt},
                ],
                temperature=0.1,
                **completion_tokens_param(self.openai_config.model, 300),
            )

            usage = response.usage
            has_tracker = self._cost_tracker is not None and usage and hasattr(self._cost_tracker, "add_call")
            if has_tracker:
                pt = getattr(usage, "prompt_tokens", None)
                ct = getattr(usage, "completion_tokens", None)
                total = getattr(usage, "total_tokens", 0) or 0
                if pt is None or ct is None:
                    pt = int(total * 0.7) if total else 0
                    ct = total - pt if total else 0
                self._cost_tracker.add_call(self.openai_config.model, pt, ct)

            text = response.choices[0].message.content.strip()
            json_match = re.search(r"\{[\s\S]*\}", text)
            if json_match:
                return json.loads(json_match.group())
            return {}

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
    ) -> None:
        self.domain_name = domain_name
        self.principles = principles
        self.openai_config = openai_config or OpenAIClientConfig.default()
        self._cost_tracker = cost_tracker
        self._cache: dict[str, list[str]] = {}

    def evaluate(self, query: str) -> list[str]:
        """Evaluate query and return relevant principle IDs."""
        cache_key = hashlib.md5(f"{query}_{len(self.principles)}".encode()).hexdigest()
        if cache_key in self._cache:
            return self._cache[cache_key]

        if not self.principles:
            return []

        principles_summary = [{"id": p.id, "title": p.title, "rule": p.rule, "level": p.level} for p in self.principles]
        principles_text = format_principles_for_prompt(
            principles_summary, include_level=True, style="compact", max_rule_len=233
        )

        prompt = f"""You are a domain-specific ethical principle evaluator.

DOMAIN: {self.domain_name.upper()}

USER QUERY (in any language): "{query}"

ETHICAL PRINCIPLES FOR THIS DOMAIN:
{principles_text}

Task: Identify which principles from THIS DOMAIN are semantically relevant to the user query.

CRITICAL RULES:
1. **ALWAYS prioritize HARD constraints [H] over SOFT norms [S]**
2. **Semantic analysis**: Analyze the MEANING and INTENT of the query
3. **Domain relevance**: Only return principles that are relevant to THIS specific domain
4. **Relevance ordering**: Order by semantic relevance, HARD constraints first

Return ONLY a JSON list of principle IDs that are relevant, ordered by relevance:
["PRINCIPLE.ID.1", "PRINCIPLE.ID.2", ...]

If no principles from this domain are relevant, return empty list [].

Output ONLY valid JSON, nothing else:"""

        try:
            result_ids = self._call_openai(prompt)

            valid_ids = [pid for pid in result_ids if any(p.id == pid for p in self.principles)]
            self._cache[cache_key] = valid_ids
            return valid_ids

        except Exception as e:
            logger.warning(f"DomainAgent {self.domain_name} evaluation failed: {e}")
            return []

    def _call_openai(self, prompt: str) -> list[str]:
        try:
            import openai

            if not self.openai_config.api_key:
                return []

            client = openai.OpenAI(api_key=self.openai_config.api_key)

            response = client.chat.completions.create(
                model=self.openai_config.model,
                messages=[
                    {
                        "role": "system",
                        "content": ("You are a precise semantic matching system. " "Always respond with valid JSON only."),
                    },
                    {"role": "user", "content": prompt},
                ],
                temperature=0.1,
                **completion_tokens_param(self.openai_config.model, 256),
            )

            usage = response.usage
            has_tracker = self._cost_tracker is not None and usage and hasattr(self._cost_tracker, "add_call")
            if has_tracker:
                pt = getattr(usage, "prompt_tokens", None)
                ct = getattr(usage, "completion_tokens", None)
                total = getattr(usage, "total_tokens", 0) or 0
                if pt is None or ct is None:
                    pt = int(total * 0.7) if total else 0
                    ct = total - pt if total else 0
                self._cost_tracker.add_call(self.openai_config.model, pt, ct)

            text = response.choices[0].message.content.strip()
            json_match = re.search(r"\[[\s\S]*?\]", text)
            if json_match:
                return json.loads(json_match.group())

            return []

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
    max_parallel_agents: int = 2
    use_enhanced_retrieval: bool = True
    confidence_threshold: float = 0.6
    use_domain_prefilter: bool = True
    max_prefilter_domains: int = 3


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
                domain_keywords=data_provider.get_domain_keywords(),
                cost_tracker=cost_tracker,
            )

        self._last_debug_info: dict[str, Any] = {}

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
        if self._domain_prefilter is not None and hasattr(self._domain_prefilter, "_cache"):
            self._domain_prefilter._cache.clear()

    def get_relevant_principles(
        self,
        query: str,
        top_k: int = 10,
        domain: str | None = None,
    ) -> list[Principle]:
        """
        Retrieve relevant principles via parallel domain agents.

        Returns list of principles ordered by relevance (max top_k).
        """
        query_tokens = tokenize(query)

        if not query_tokens:
            core = self._provider.load_core()
            return sorted(core, key=lambda p: -p.priority)[:top_k]

        available_domains = ["core"] + self._provider._get_available_domains()

        if self._config.use_enhanced_retrieval and self._config.use_domain_prefilter and self._domain_prefilter:
            self._domain_prefilter.set_domain_keywords(self._provider.get_domain_keywords())
            relevant_domains = self._domain_prefilter.filter_domains(query, available_domains)
            if domain and domain not in relevant_domains:
                relevant_domains.append(domain)
        else:
            relevant_domains = available_domains

        self._last_debug_info = {
            "use_enhanced_retrieval": self._config.use_enhanced_retrieval,
            "use_domain_prefilter": self._config.use_domain_prefilter,
            "available_domains": available_domains,
            "prefiltered_domains": relevant_domains,
            "confidence_threshold": self._config.confidence_threshold,
        }

        all_principle_ids: set[str] = set()

        if self._config.use_enhanced_retrieval:
            agents = self._create_enhanced_agents(relevant_domains)

            self._last_debug_info.update(
                {
                    "agents_created": len(agents),
                    "agent_domains": [a.domain_name for a in agents],
                    "agent_principles_count": {a.domain_name: len(a.principles) for a in agents},
                }
            )

            if not agents:
                core = self._provider.load_core()
                self._last_debug_info["fallback"] = True
                return sorted(core, key=lambda p: -p.priority)[:top_k]

            agent_results = self._run_enhanced_agents_parallel(agents, query)

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

            self._last_debug_info.update(
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

            self._last_debug_info.update(
                {
                    "agents_created": len(legacy_agents),
                    "agent_domains": [a.domain_name for a in legacy_agents],
                    "agent_principles_count": {a.domain_name: len(a.principles) for a in legacy_agents},
                }
            )

            if not legacy_agents:
                core = self._provider.load_core()
                self._last_debug_info["fallback"] = True
                return sorted(core, key=lambda p: -p.priority)[:top_k]

            legacy_results = self._run_agents_parallel(legacy_agents, query)

            for domain_name, principle_ids in legacy_results.items():
                all_principle_ids.update(principle_ids)

            self._last_debug_info.update(
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

        self._last_debug_info.update(
            {
                "final_principles_count": len(relevant_principles),
                "principles_by_domain": self._get_principles_by_domain(relevant_principles),
            }
        )

        return relevant_principles[:top_k]

    def _get_principles_by_domain(self, principles: list[Principle]) -> dict[str, int]:
        by_domain: dict[str, int] = {}
        for p in principles:
            domain = p.domain or "core"
            by_domain[domain] = by_domain.get(domain, 0) + 1
        return by_domain

    def detect_relevant_domains(self, query: str) -> list[str]:
        """Return domains relevant to the query, ordered by relevance."""
        try:
            available = ["core"] + self._provider._get_available_domains()
            if self._domain_prefilter is not None:
                self._domain_prefilter.set_domain_keywords(self._provider.get_domain_keywords())
                return self._domain_prefilter.filter_domains(query, available)
            return []
        except Exception:
            return []

    def get_debug_info(self) -> dict[str, Any]:
        """Return debug info from last retrieval."""
        return self._last_debug_info.copy()

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
                            )
                        agents.append(self._enhanced_agents[domain_name])
                except FileNotFoundError:
                    continue

        return agents

    def _run_enhanced_agents_parallel(self, agents: list[EnhancedDomainAgent], query: str) -> dict[str, AgentResult]:
        results: dict[str, AgentResult] = {}
        batch_size = self._config.max_parallel_agents

        for i in range(0, len(agents), batch_size):
            batch = agents[i : i + batch_size]
            with concurrent.futures.ThreadPoolExecutor(max_workers=len(batch)) as executor:
                future_to_agent = {executor.submit(agent.evaluate, query): agent for agent in batch}
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

    def _run_agents_parallel(self, agents: list[DomainAgent], query: str) -> dict[str, list[str]]:
        results: dict[str, list[str]] = {}
        batch_size = self._config.max_parallel_agents

        for i in range(0, len(agents), batch_size):
            batch = agents[i : i + batch_size]
            with concurrent.futures.ThreadPoolExecutor(max_workers=len(batch)) as executor:
                future_to_agent = {executor.submit(agent.evaluate, query): agent for agent in batch}
                for future in concurrent.futures.as_completed(future_to_agent):
                    agent = future_to_agent[future]
                    try:
                        principle_ids = future.result()
                        results[agent.domain_name] = principle_ids
                    except Exception as e:
                        logger.warning(f"Agent {agent.domain_name} failed: {e}")
                        results[agent.domain_name] = []

        return results
