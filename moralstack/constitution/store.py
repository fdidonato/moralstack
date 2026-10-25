"""
Constitution Store - Ethical principles management for MoralStack.

Responsibilities: ethical principle management, conflict resolution, overlays.
YAML loading: ruamel.yaml only. Validation: Pydantic only. Fail-fast.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pydantic import ValidationError

from moralstack.constitution.helpers import resolve_conflict
from moralstack.constitution.loader import ConstitutionLoadError, load_yaml_file
from moralstack.constitution.openai_config import OpenAIClientConfig
from moralstack.constitution.retrieval_result import PrincipleRetrievalResult
from moralstack.constitution.retriever import (
    ConstitutionRetriever,
    ConstitutionRetrieverConfig,
)
from moralstack.constitution.schema import (
    Constitution,
    CoreYAML,
    Overlay,
    OverlayYAML,
    Principle,
)

# Optional imports for LLM matching (type: ignore for fallback assignment)
if TYPE_CHECKING:
    from moralstack.models.base import GenerationConfig
else:
    try:
        from moralstack.core.types import PolicyLLMProtocol
        from moralstack.models.base import GenerationConfig
    except ImportError:
        PolicyLLMProtocol = None  # type: ignore[misc, assignment]
        GenerationConfig = None  # type: ignore[misc, assignment]

# Mandatory core principle ID for validation (fail-closed)
REQUIRED_CORE_PRINCIPLE_ID = "CORE.BALANCE.1"

# Alias for backward compatibility
ConstitutionValidationError = ConstitutionLoadError


def detect_domain(
    query: str,
    policy_llm: Any | None = None,
    domain_descriptions: dict[str, str] | None = None,
    available_domains: list[str] | None = None,
) -> str | None:
    """
    DEPRECATED for runtime domain selection.

    Runtime code MUST use ConstitutionRetriever / DomainPrefilter through
    `ConstitutionStore.retrieve()` and read the resulting `prefiltered_domains`
    from its return value instead.

    This function uses a legacy standalone LLM classifier whose prompt does not
    enforce the same domain-exclusion contract as the DomainPrefilter (e.g. it
    can classify weapon/violent requests as `legal`). Calling it from the
    runtime pipeline causes a duplicate, silent, contract-divergent LLM call
    whose output then propagates as `request.user_context.domain_overlay` /
    `overlay_applied` / refusal redirection. Do not use it to set request
    `domain_overlay`, `overlay_applied`, or `refusal_domain`.

    Kept for backward compatibility with external callers and tests only.

    Args:
        query: User query.
        policy_llm: Policy LLM for semantic classification (required).
        domain_descriptions: Mapping {domain_name: description}.
        available_domains: Optional restriction over the candidate domain set.

    Returns:
        Detected domain or None if not detected or LLM unavailable.
    """
    # Use ONLY the LLM for domain detection
    if policy_llm is None or not domain_descriptions:
        return None

    try:
        return _detect_domain_llm(query, policy_llm, domain_descriptions, available_domains)
    except Exception as e:
        # Log the error but no fallback - use only the LLM
        import logging

        logger = logging.getLogger(__name__)
        logger.debug(f"LLM domain detection failed: {e}")
        return None


def _detect_domain_llm(
    query: str,
    policy_llm: Any,
    domain_descriptions: dict[str, str],
    available_domains: list[str] | None = None,
) -> str | None:
    """
    Detects the domain using LLM semantic classification.

    Args:
        query: User query (any language)
        policy_llm: Policy LLM for classification
        domain_descriptions: Dictionary of domain descriptions {name: description}
        available_domains: List of available domains (if None, uses the full list)

    Returns:
        Detected domain, or None if not detected.
    """
    # If available_domains is specified, filter to those only
    if available_domains is not None:
        domain_descriptions = {k: v for k, v in domain_descriptions.items() if k in available_domains}

    # Build the domain list for the prompt
    domains_list = "\n".join([f"- {domain}: {desc}" for domain, desc in sorted(domain_descriptions.items())])

    domains_enum = " | ".join([f'"{d}"' for d in sorted(domain_descriptions.keys())]) + " | null"

    prompt = f"""Classify the following user query into one of these domains
based on semantic meaning and intent.

IMPORTANT: Be sensitive to medical/health queries. If the query mentions:
- Pain, symptoms, physical discomfort, body parts, injuries
- Health problems, medical conditions, remedies, treatments
- Asking for medical advice, health suggestions, wellness
Then classify as "medical" domain.

Available domains:
{domains_list}

USER QUERY (in any language): "{query}"

Analyze the semantic meaning and intent of the query. Consider:
- What is the user trying to achieve?
- What domain does the query naturally belong to?
- Is this a health/medical question? (pain, symptoms, remedies, treatments, body parts)

Return ONLY a JSON object with:
{{
  "domain": {domains_enum},
  "confidence": 0.0-1.0,
  "reasoning": "brief explanation"
}}

Examples:
- "ho un dolore alla schiena" → medical (confidence: 0.9)
- "suggeriscimi dei rimedi" → medical (confidence: 0.4)
- "mal di testa cosa fare" → medical (confidence: 0.9)
- "dolore lombare rimedi" → medical (confidence: 0.95)

If the query doesn't clearly belong to any of these domains, set domain to null.
Output ONLY valid JSON, nothing else:"""

    try:
        from moralstack.models.policy import GenerationConfig

        config = GenerationConfig(
            max_tokens=150,
            temperature=0.1,  # Low temperature for classification
            top_p=0.9,
        )

        result = policy_llm.generate(prompt, config=config)
        response_text = result.text.strip()

        # Extract JSON from the response
        import json
        import re

        # Look for JSON in the response
        json_match = re.search(r"\{[^}]+\}", response_text, re.DOTALL)
        if json_match:
            data = json.loads(json_match.group())
            domain = data.get("domain")
            confidence = data.get("confidence", 0.0)

            # Return the domain if confidence > 0.5 (lower threshold for higher sensitivity)
            # Debug log if confidence is low but a domain was detected
            if isinstance(domain, str) and domain:
                if confidence > 0.5:
                    return domain
                else:
                    # Debug log: domain detected but confidence low
                    import logging

                    logger = logging.getLogger(__name__)
                    logger.debug(f"Domain '{domain}' detected but low confidence ({confidence:.2f})")

        return None

    except Exception as e:
        # Log the error for debugging
        import logging

        logger = logging.getLogger(__name__)
        logger.debug(f"LLM domain detection failed: {e}")
        return None


# =============================================================================
# Constitution Store (facade: load/overlay + delegation to retriever)
# =============================================================================
# AgentResult, DomainPrefilter, DomainAgent, EnhancedDomainAgent moved to retriever.py


# =============================================================================
# Constitution Store
# =============================================================================


@dataclass
class ConstitutionStoreConfig:
    """
    Configuration for ConstitutionStore. Use this to avoid long parameter lists.

    All fields have defaults consistent with ConstitutionStore's previous defaults.
    """

    config_dir: Path | str | None = None
    core_file: str = "core.yaml"
    overlays_dir: str = "overlays"
    policy_llm: Any | None = None
    use_llm_matching: bool = True
    openai_config: OpenAIClientConfig | None = None
    max_parallel_agents: int = 4
    use_enhanced_retrieval: bool = True
    confidence_threshold: float = 0.6
    use_domain_prefilter: bool = True
    max_prefilter_domains: int = 3
    # A PromptSet, or None for the packaged defaults (typed Any: see ConstitutionRetrieverConfig).
    prompt_set: Any | None = None


class ConstitutionStore:
    """
    Manages the ethical principles of the constitution.

    Responsibilities:
    - Loading principles from YAML
    - Domain overlay support
    - Conflict resolution between principles
    - Relevant principle retrieval

    Usage:
        store = ConstitutionStore()  # uses bundled data by default
        constitution = store.get_constitution(domain="medical")
        relevant = store.get_relevant_principles(prompt, top_k=10)
    """

    # Confidence threshold to accept principles (Fix 3)
    DEFAULT_CONFIDENCE_THRESHOLD = 0.6

    def __init__(
        self,
        config: ConstitutionStoreConfig | None = None,
        *,
        config_dir: Path | str | None = None,
        core_file: str = "core.yaml",
        overlays_dir: str = "overlays",
        policy_llm: Any | None = None,
        use_llm_matching: bool = True,
        openai_config: OpenAIClientConfig | None = None,
        max_parallel_agents: int = 4,
        use_enhanced_retrieval: bool = True,
        confidence_threshold: float = 0.6,
        use_domain_prefilter: bool = True,
        max_prefilter_domains: int = 3,
        prompt_set: Any | None = None,
    ) -> None:
        """
        Initialize the store.

        Accepts either a single `config: ConstitutionStoreConfig` or keyword
        arguments (backward compatible). When both are provided, `config` wins.

        Args:
            config: Optional full configuration; if given, other kwargs are ignored.
            config_dir: Directory containing YAML files. If None, uses project-relative path.
            core_file: Core principles filename.
            overlays_dir: Subdirectory for domain overlays.
            policy_llm: Optional policy LLM for semantic matching.
            use_llm_matching: If True and policy_llm set, use LLM for matching.
            openai_config: OpenAI client config (api_key, model).
            max_parallel_agents: Max concurrent domain agents.
            use_enhanced_retrieval: Use EnhancedDomainAgent with confidence.
            confidence_threshold: Min confidence to accept principles.
            use_domain_prefilter: Pre-filter domains before running agents.
            max_prefilter_domains: Max domains selected by pre-filter.
            prompt_set: PromptSet for the prefilter and domain-agent prompts; None = packaged defaults.
        """
        if config is not None:
            cfg = config
        else:
            cfg = ConstitutionStoreConfig(
                config_dir=config_dir,
                core_file=core_file,
                overlays_dir=overlays_dir,
                policy_llm=policy_llm,
                use_llm_matching=use_llm_matching,
                openai_config=openai_config,
                max_parallel_agents=max_parallel_agents,
                use_enhanced_retrieval=use_enhanced_retrieval,
                confidence_threshold=confidence_threshold,
                use_domain_prefilter=use_domain_prefilter,
                max_prefilter_domains=max_prefilter_domains,
                prompt_set=prompt_set,
            )

        if cfg.config_dir is None:
            self._config_dir = Path(__file__).parent / "data"
        else:
            self._config_dir = Path(cfg.config_dir)

        self._core_file = cfg.core_file
        self._overlays_dir = cfg.overlays_dir

        # LLM matching
        self.policy_llm = cfg.policy_llm
        self.use_llm_matching = cfg.use_llm_matching and cfg.policy_llm is not None

        # OpenAI support
        resolved_openai = cfg.openai_config or OpenAIClientConfig.default()
        self.openai_config = OpenAIClientConfig.with_env_fallback(
            resolved_openai.api_key,
            resolved_openai.model,
        )
        self.max_parallel_agents = cfg.max_parallel_agents

        # Enhanced retrieval settings
        self.use_enhanced_retrieval = cfg.use_enhanced_retrieval
        self.confidence_threshold = cfg.confidence_threshold
        self.use_domain_prefilter = cfg.use_domain_prefilter
        self.max_prefilter_domains = cfg.max_prefilter_domains

        # Cache
        self._core_principles: list[Principle] | None = None
        self._overlays: dict[str, Overlay] = {}
        self._available_domains_cache: list[str] | None = None

        self._cost_tracker: Any = None

        retriever_config = ConstitutionRetrieverConfig(
            openai_config=self.openai_config,
            max_parallel_agents=cfg.max_parallel_agents,
            use_enhanced_retrieval=cfg.use_enhanced_retrieval,
            confidence_threshold=cfg.confidence_threshold,
            use_domain_prefilter=cfg.use_domain_prefilter,
            max_prefilter_domains=cfg.max_prefilter_domains,
            prompt_set=cfg.prompt_set,
        )
        self._retriever = ConstitutionRetriever(
            config=retriever_config,
            data_provider=self,
            cost_tracker=self._cost_tracker,
        )

    def _get_available_domains(self) -> list[str]:
        """
        Internal provider hook for ConstitutionRetriever protocol compatibility.
        Mirrors public get_available_domains().
        """
        return self.get_available_domains()

    def set_cost_tracker(self, tracker: Any | None) -> None:
        """Sets the TokenCostTracker to track costs (domain agents and prefilter)."""
        self._cost_tracker = tracker
        self._retriever.set_cost_tracker(tracker)

    @property
    def config_dir(self) -> Path:
        """Configuration directory."""
        return self._config_dir

    def get_available_domains(self) -> list[str]:
        """
        Gets the list of available domains (existing overlays).

        Returns:
            List of available domain names.
        """
        if self._available_domains_cache is not None:
            return self._available_domains_cache

        overlays_path = self._config_dir / self._overlays_dir
        available = []

        if overlays_path.exists():
            # Look for .yaml files in the overlays directory
            for overlay_file in overlays_path.glob("*.yaml"):
                domain = overlay_file.stem  # File name without extension
                available.append(domain)

        self._available_domains_cache = sorted(available)
        return self._available_domains_cache

    def get_domain_descriptions(self) -> dict[str, str]:
        """
        Gets the semantic descriptions of all available domains.

        Dynamically loads the descriptions from the overlay YAML files.
        Also includes a description for 'core'.

        Returns:
            Dict {domain_name: description}
        """
        descriptions: dict[str, str] = {}

        # Description for core (universal principles)
        descriptions["core"] = (
            "Universal ethical principles: harm prevention, privacy protection, "
            "deception prevention, honesty, safety, illegal activity prevention, "
            "malware prevention, child safety, transparency, autonomy."
        )

        # Load descriptions from the overlays
        for domain_name in self.get_available_domains():
            try:
                overlay = self.load_overlay(domain_name)
                if overlay.description:
                    descriptions[domain_name] = overlay.description
                else:
                    # Fallback: generate a basic description from the name
                    descriptions[domain_name] = f"Principles specific to {domain_name} domain."
            except FileNotFoundError:
                descriptions[domain_name] = f"Principles specific to {domain_name} domain."

        return descriptions

    @property
    def principles(self) -> list[Principle]:
        """
        All core principles ordered by priority.

        Shortcut for quick access without overlays.
        """
        return resolve_conflict(self.load_core())

    def load_core(self) -> list[Principle]:
        """
        Loads core principles from a YAML file (ruamel.yaml) with Pydantic validation.
        Fail-fast: invalid YAML or missing mandatory principle → ConstitutionLoadError.

        Returns:
            List of core principles (typed Pydantic objects).

        Raises:
            FileNotFoundError: If the core file does not exist.
            ConstitutionLoadError: If loading or validation fails.
        """
        if self._core_principles is not None:
            return list(self._core_principles)

        core_path = self._config_dir / self._core_file

        if not core_path.exists():
            raise FileNotFoundError(f"Constitution core file not found: {core_path}")

        try:
            data = load_yaml_file(core_path)
        except ConstitutionLoadError:
            raise
        except Exception as e:
            raise ConstitutionLoadError(
                str(e),
                path=core_path,
                field="(root)",
                reason="Parsing YAML fallito",
            ) from e

        try:
            core = CoreYAML.model_validate(data)
        except ValidationError as e:
            first_error: Any = e.errors()[0] if e.errors() else None
            loc = ".".join(str(x) for x in (first_error.get("loc", []) if first_error else []))
            raise ConstitutionLoadError(
                str(first_error.get("msg")) if first_error else str(e),
                path=core_path,
                field=loc or "principles",
                reason="Validazione Pydantic fallita",
            ) from e

        valid_principles: list[Principle] = [py.to_principle() for py in core.principles]

        if not any(pr.id == REQUIRED_CORE_PRINCIPLE_ID for pr in valid_principles):
            raise ConstitutionLoadError(
                f"Required principle {REQUIRED_CORE_PRINCIPLE_ID} not found or invalid",
                path=core_path,
                field="principles",
                reason=f"Principio obbligatorio {REQUIRED_CORE_PRINCIPLE_ID} mancante",
            )

        self._core_principles = valid_principles
        return list(self._core_principles)

    def load_overlay(self, domain: str) -> Overlay:
        """
        Loads a domain overlay from a YAML file (ruamel.yaml) with Pydantic validation.
        Fail-fast: invalid YAML → ConstitutionLoadError.

        Args:
            domain: Domain name (e.g. "medical", "legal").

        Returns:
            Overlay for the specified domain (typed Pydantic objects).

        Raises:
            FileNotFoundError: If the overlay file does not exist.
            ConstitutionLoadError: If loading or validation fails.
        """
        if domain in self._overlays:
            return self._overlays[domain]

        overlay_path = self._config_dir / self._overlays_dir / f"{domain}.yaml"

        if not overlay_path.exists():
            raise FileNotFoundError(f"Overlay file not found for domain '{domain}': {overlay_path}")

        try:
            data = load_yaml_file(overlay_path)
        except ConstitutionLoadError:
            raise
        except Exception as e:
            raise ConstitutionLoadError(
                str(e),
                path=overlay_path,
                field="(root)",
                reason="Parsing YAML fallito",
            ) from e

        try:
            oy = OverlayYAML.model_validate(data)
        except ValidationError as e:
            first_error: Any = e.errors()[0] if e.errors() else None
            loc = ".".join(str(x) for x in (first_error.get("loc", []) if first_error else []))
            raise ConstitutionLoadError(
                str(first_error.get("msg")) if first_error else str(e),
                path=overlay_path,
                field=loc or "additional_principles",
                reason="Validazione Pydantic fallita",
            ) from e

        additional: list[Principle] = []
        for py in oy.additional_principles:
            pr = py.to_principle()
            if pr.domain is None:
                pr = pr.model_copy(update={"domain": domain})
            additional.append(pr)

        overlay = Overlay(
            domain=domain,
            additional_principles=additional,
            priority_overrides=dict(oy.priority_overrides),
            description=oy.description.strip(),
            sensitive=oy.sensitive,
            excluded=oy.excluded,
            refusal_redirection=oy.refusal_redirection.strip(),
            simulator_domain_guidance=oy.simulator_domain_guidance.strip(),
            sensitive_risk_floor=oy.sensitive_risk_floor,
        )

        self._overlays[domain] = overlay
        return overlay

    def get_constitution(self, domain: str | None = None) -> Constitution:
        """
        Assembles the complete constitution.

        Args:
            domain: Domain for the optional overlay.

        Returns:
            Constitution with core principles and optional overlay.
        """
        core = self.load_core()
        overlay = None

        if domain is not None:
            try:
                overlay = self.load_overlay(domain)
            except FileNotFoundError:
                # Domain without overlay, use core only
                pass

        # Fail-fast: if we are here, loading succeeded
        return Constitution(
            core_principles=core,
            active_overlay=overlay,
            constitution_loaded_ok=True,
            constitution_corrupted=False,
        )

    def get_relevant_principles(
        self,
        query: str,
        top_k: int = 10,
        domain: str | None = None,
        *,
        retrieval_phase: str = "risk_routing",
    ) -> list[Principle]:
        """
        Retrieval with parallel per-domain agents.

        Delegates to ConstitutionRetriever.

        Args:
            query: Request/prompt text (any language).
            top_k: Maximum number of principles to return.
            domain: Optional domain (forces evaluation of this domain).
            retrieval_phase: Observability qualifier for domain prefilter persistence
                (``risk_routing`` vs ``deliberation_retrieval``).

        Returns:
            List of principles ordered by relevance (max top_k).
        """
        return self._retriever.get_relevant_principles(
            query,
            top_k=top_k,
            domain=domain,
            retrieval_phase=retrieval_phase,
        )

    def retrieve(
        self,
        query: str,
        top_k: int = 10,
        domain: str | None = None,
        *,
        retrieval_phase: str = "risk_routing",
    ) -> PrincipleRetrievalResult:
        """
        Retrieval with parallel per-domain agents, typed decision channel.

        Delegates to ConstitutionRetriever.retrieve(). Returns the principles, the
        raw ``prefiltered_domains`` (decision channel — the caller owns the `core`
        exclusion, §5.5) and ``debug_info`` (best-effort telemetry, same shape as
        the retired per-retrieval debug accessor this replaces). Writes no instance state.

        Args:
            query: Request/prompt text (any language).
            top_k: Maximum number of principles to return.
            domain: Optional domain (forces evaluation of this domain).
            retrieval_phase: Observability qualifier for domain prefilter persistence
                (``risk_routing`` vs ``deliberation_retrieval``).

        Returns:
            Frozen PrincipleRetrievalResult (principles, prefiltered_domains, debug_info).
        """
        return self._retriever.retrieve(
            query,
            top_k=top_k,
            domain=domain,
            retrieval_phase=retrieval_phase,
        )

    def detect_relevant_domains(self, query: str) -> list[str]:
        """
        Return a list of constitution domains relevant to the query, ordered by relevance.

        Delegates to ConstitutionRetriever.

        Returns:
            List of domain names (may include "core").  Empty list on error or
            if the prefilter is not configured.
        """
        return self._retriever.detect_relevant_domains(query)

    def has_excluded_domains(self) -> bool:
        """True if at least one overlay has excluded=true. [NO LLM] Pure Python gate."""
        for d in self.get_available_domains():
            ov = self.load_overlay(d)
            if getattr(ov, "excluded", False):
                return True
        return False

    def get_excluded_domains(self) -> list[str]:
        """List of domain names with excluded=true. [NO LLM] For CLI startup display."""
        return [d for d in self.get_available_domains() if getattr(self.load_overlay(d), "excluded", False)]

    def resolve_conflict(self, principles: list[Principle]) -> list[Principle]:
        """
        Sorts principles for conflict resolution.

        Rules:
        1. Hard constraints > Soft norms (always)
        2. Within the same level: higher priority wins
        3. On equal priority: the more specific principle wins
        4. Final tie: alphabetical ID order (determinism)

        Args:
            principles: List of principles to sort.

        Returns:
            List ordered by application priority.
        """
        return resolve_conflict(principles)


# =============================================================================
# Factory & Convenience
# =============================================================================


def create_store(config_dir: Path | str | None = None) -> ConstitutionStore:
    """
    Factory function to create a store.

    Args:
        config_dir: Optional configuration directory.

    Returns:
        Configured ConstitutionStore.
    """
    return ConstitutionStore(config_dir=config_dir)


def create_default_constitution() -> Constitution:
    """
    Creates a constitution with hardcoded default principles.

    Useful for testing or when there are no YAML files.
    """
    default_principles = [
        Principle(
            id="CORE.NM.1",
            level="hard",
            priority=100,
            title="Non-Maleficence - Physical Harm",
            rule=("Never provide instructions that could directly facilitate " "physical harm to self or others"),
            examples_allow=[],
            examples_deny=[
                "How to build an explosive device",
                "Best methods to harm someone",
            ],
            remediation="Refuse clearly, explain why, suggest safe alternatives",
        ),
        Principle(
            id="CORE.NM.2",
            level="hard",
            priority=95,
            title="Non-Maleficence - Illegal Activity",
            rule="Never provide assistance with clearly illegal activities",
            examples_allow=[],
            examples_deny=[
                "How to hack into someone's account",
                "How to make illegal drugs",
            ],
            remediation="Refuse and explain legal alternatives",
        ),
        Principle(
            id="CORE.PRIV.1",
            level="hard",
            priority=90,
            title="Privacy Protection",
            rule=("Never assist in violating individual privacy or obtaining " "private information without consent"),
            examples_allow=[],
            examples_deny=[
                "How to spy on someone's phone",
                "Find someone's personal address",
            ],
            remediation="Refuse and explain privacy rights",
        ),
        Principle(
            id="SOFT.HONEST.1",
            level="soft",
            priority=70,
            title="Honesty",
            rule="Provide accurate information and acknowledge uncertainty when present",
            examples_allow=[
                "I'm not certain, but...",
                "Based on my knowledge...",
            ],
            examples_deny=[
                "Making up false statistics",
                "Claiming certainty when uncertain",
            ],
            remediation="Add appropriate caveats and cite limitations",
        ),
        Principle(
            id="SOFT.STYLE.1",
            level="soft",
            priority=30,
            title="Communication Tone",
            rule="Maintain respectful, empathetic tone even when refusing",
            examples_allow=[
                "I understand your question, but...",
                "I appreciate you asking...",
            ],
            examples_deny=[
                "That's a stupid question",
                "I won't help you with that, period.",
            ],
            remediation="Reframe response with empathy",
        ),
    ]

    return Constitution(
        core_principles=default_principles,
        active_overlay=None,
    )
