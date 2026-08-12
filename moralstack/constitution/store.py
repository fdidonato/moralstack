"""
Constitution Store - Gestione principi etici per MoralStack.

Responsabilità: Gestione principi etici, risoluzione conflitti, overlay.
Caricamento YAML: solo ruamel.yaml. Validazione: solo Pydantic. Fail-fast.
"""

from __future__ import annotations

import re
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pydantic import ValidationError

from moralstack.constitution.helpers import _STOPWORDS, resolve_conflict, tokenize
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

# ID principio core obbligatorio per validazione (fail-closed)
REQUIRED_CORE_PRINCIPLE_ID = "CORE.BALANCE.1"

# Alias per retrocompatibilità
ConstitutionValidationError = ConstitutionLoadError


def _extract_keywords_from_description(description: str) -> list[str]:
    """
    Estrae keyword compatte da una descrizione di dominio (deterministica, pure Python).

    Usato quando overlay.keywords è vuoto: lowercase, rimozione stopwords,
    parole len >= 4, deduplica, primi 12 token.

    Args:
        description: Descrizione testuale del dominio.

    Returns:
        Lista di keyword (max 12).
    """
    if not description or not description.strip():
        return []
    tokens = re.sub(r"[^\w\s]", " ", description.lower()).split()
    seen: set[str] = set()
    result: list[str] = []
    for t in tokens:
        w = t.strip()
        if len(w) >= 4 and w not in _STOPWORDS and w not in seen:
            seen.add(w)
            result.append(w)
            if len(result) >= 12:
                break
    return result


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
    # Usa SOLO LLM per rilevamento dominio
    if policy_llm is None or not domain_descriptions:
        return None

    try:
        return _detect_domain_llm(query, policy_llm, domain_descriptions, available_domains)
    except Exception as e:
        # Log errore ma non fallback - usa solo LLM
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
    Rileva dominio usando classificazione semantica LLM.

    Args:
        query: Query utente (qualsiasi lingua)
        policy_llm: Policy LLM per classificazione
        domain_descriptions: Dizionario delle descrizioni dei domini {nome: descrizione}
        available_domains: Lista di domini disponibili (se None, usa lista completa)

    Returns:
        Dominio rilevato o None se non rilevato.
    """
    # Se available_domains è specificato, filtra solo quelli
    if available_domains is not None:
        domain_descriptions = {k: v for k, v in domain_descriptions.items() if k in available_domains}

    # Costruisci lista domini per prompt
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
            temperature=0.1,  # Bassa temperatura per classificazione
            top_p=0.9,
        )

        result = policy_llm.generate(prompt, config=config)
        response_text = result.text.strip()

        # Estrai JSON dalla risposta
        import json
        import re

        # Cerca JSON nella risposta
        json_match = re.search(r"\{[^}]+\}", response_text, re.DOTALL)
        if json_match:
            data = json.loads(json_match.group())
            domain = data.get("domain")
            confidence = data.get("confidence", 0.0)

            # Ritorna dominio se confidence > 0.5 (soglia più bassa per maggiore sensibilità)
            # Log per debug se confidence è bassa ma dominio rilevato
            if isinstance(domain, str) and domain:
                if confidence > 0.5:
                    return domain
                else:
                    # Log per debug: dominio rilevato ma confidence bassa
                    import logging

                    logger = logging.getLogger(__name__)
                    logger.debug(f"Domain '{domain}' detected but low confidence ({confidence:.2f})")

        return None

    except Exception as e:
        # Log errore per debug
        import logging

        logger = logging.getLogger(__name__)
        logger.debug(f"LLM domain detection failed: {e}")
        return None


def _expand_query(query_tokens: set[str]) -> set[str]:
    """
    Espande query con sinonimi e termini correlati comuni.

    Query expansion base senza dipendenze esterne.
    """
    expanded = set(query_tokens)

    # Mappa sinonimi comuni (italiano e inglese)
    synonyms = {
        # Medical/Health
        "curare": {"trattare", "cura", "terapia", "cure", "treat", "treatment"},
        "depressione": {"depression", "tristezza", "malinconia", "sadness"},
        "medico": {"doctor", "dottore", "medico", "healthcare", "sanitario"},
        "malattia": {"disease", "illness", "sickness", "malattia"},
        "sintomi": {"symptoms", "sintomi", "segni"},
        # Legal
        "legale": {"legal", "law", "legge", "giuridico", "juridical"},
        "avvocato": {"lawyer", "attorney", "avvocato", "legale"},
        "contratto": {"contract", "contratto", "accordo", "agreement"},
        "causa": {"lawsuit", "case", "causa", "processo", "trial"},
        # Harm/Safety
        "danno": {"harm", "hurt", "damage", "danno", "ferita", "injury"},
        "uccidere": {"kill", "murder", "uccidere", "ammazzare"},
        "violenza": {"violence", "violent", "violenza", "aggressione"},
        "arma": {"weapon", "gun", "arma", "pistola"},
        # Privacy
        "privato": {"private", "privacy", "privato", "personale", "personal"},
        "spiare": {"spy", "spying", "spiare", "sorvegliare", "surveillance"},
        "dati": {"data", "information", "dati", "informazioni"},
        # Deception
        "ingannare": {"deceive", "trick", "ingannare", "imbrogliare", "fraud"},
        "falso": {"fake", "false", "falso", "falsificare", "forge"},
        "manipolare": {"manipulate", "manipulation", "manipolare"},
    }

    # Espandi con sinonimi
    for token in query_tokens:
        if token in synonyms:
            expanded.update(synonyms[token])

    return expanded


def _compute_relevance(
    principle: Principle,
    query_tokens: set[str],
    expanded_query: set[str] | None = None,
    keyword_freq: dict[str, int] | None = None,
) -> float:
    """
    Calcola relevance score tra principio e query.

    Miglioramenti:
    - Query expansion con sinonimi (passata come parametro per evitare ricalcoli)
    - Matching più intelligente (substring per keyword multi-parola)
    - Pesi dinamici basati su specificità del principio
    - Boost per principi di dominio specifico
    - Penalizzazione per keyword troppo comuni (TF-IDF-like)

    Args:
        principle: Principio da valutare
        query_tokens: Token originali della query
        expanded_query: Query espansa con sinonimi (opzionale, calcolata se None)
        keyword_freq: Frequenza delle keyword nei principi (per penalizzare keyword comuni)
    """
    # Espandi query con sinonimi se non fornita
    if expanded_query is None:
        expanded_query = _expand_query(query_tokens)

    score = 0.0

    # Keywords diretti (peso massimo, con matching intelligente)
    for kw in principle.keywords:
        kw_tokens = tokenize(kw)

        # Calcola peso base con penalizzazione per keyword comuni
        base_weight = 10.0
        if keyword_freq:
            # Penalizza keyword che appaiono in molti principi (meno specifiche)
            kw_freq = keyword_freq.get(kw.lower(), 1)
            total_principles = max(keyword_freq.values()) if keyword_freq.values() else 1
            # IDF-like: più rara la keyword, più importante
            specificity = 1.0 + (total_principles / max(kw_freq, 1)) * 0.3
            base_weight *= min(specificity, 2.0)  # Max 2x boost per keyword rare

        # Match esatto
        if kw_tokens & expanded_query:
            keyword_score = base_weight

            # Boost se keyword multi-parola matcha completamente
            if len(kw_tokens) > 1 and kw_tokens.issubset(expanded_query):
                keyword_score *= 1.5  # Boost 50% per match completo

            score += keyword_score

        # Match parziale (substring) per keyword multi-parola
        elif len(kw_tokens) > 1:
            # Controlla se almeno metà delle parole matchano
            overlap_ratio = len(kw_tokens & expanded_query) / len(kw_tokens)
            if overlap_ratio >= 0.5:
                score += base_weight * overlap_ratio  # Peso proporzionale con specificità

        # Match semantico: controlla se la keyword appare nella query (anche come substring)
        # Utile per keyword come "manipulation" che potrebbe non matchare esattamente
        kw_lower = kw.lower()
        query_lower = " ".join(expanded_query).lower() if expanded_query else ""
        if kw_lower in query_lower or any(kw_part in query_lower for kw_part in kw_lower.split()):
            semantic_weight = 8.0
            if keyword_freq:
                # Applica stessa logica di specificità
                kw_freq = keyword_freq.get(kw.lower(), 1)
                total_principles = max(keyword_freq.values()) if keyword_freq.values() else 1
                specificity = 1.0 + (total_principles / max(kw_freq, 1)) * 0.2
                semantic_weight *= min(specificity, 1.5)
            score += semantic_weight

    # Title match (peso medio-alto)
    title_tokens = tokenize(principle.title)
    title_overlap = len(title_tokens & expanded_query)
    if title_overlap > 0:
        # Peso base per ogni parola matchata
        title_score = title_overlap * 3.0
        # Boost se match completo del title
        if title_tokens.issubset(expanded_query):
            title_score *= 1.3
        score += title_score

    # Rule match (peso medio-alto, importante per contesto semantico)
    rule_tokens = tokenize(principle.rule)
    rule_overlap = len(rule_tokens & expanded_query)
    if rule_overlap > 0:
        # Peso base (aumentato da 1.0 a 2.0)
        rule_score = rule_overlap * 2.0
        # Boost se molte parole matchano (indica alta rilevanza)
        if rule_overlap >= 3:
            rule_score *= 1.5  # Boost maggiore
        score += rule_score

    # Rule substring match (per matching semantico più robusto)
    # Cerca parole chiave della rule nella query originale
    query_original_lower = " ".join(query_tokens).lower() if query_tokens else ""
    # Cerca parole significative della rule (es. "covert", "persuasion", "manipulation")
    significant_rule_words = [w for w in rule_tokens if len(w) > 4]  # Parole > 4 caratteri
    for word in significant_rule_words:
        if word in query_original_lower:
            score += 3.0  # Peso per match semantico su rule

    # Examples deny match (molto importante per detection)
    for example in principle.examples_deny:
        example_tokens = tokenize(example)
        example_overlap = example_tokens & expanded_query
        if example_overlap:
            # Peso alto per match in esempi deny
            example_score = 5.0
            # Boost se match completo
            if example_tokens.issubset(expanded_query):
                example_score *= 1.5
            score += example_score

    # Boost per principi di dominio specifico (se query contiene termini del dominio)
    if principle.domain is not None:
        domain_keywords = {
            "medical": {
                "medico",
                "salute",
                "cura",
                "malattia",
                "sintomi",
                "doctor",
                "health",
                "treatment",
            },
            "legal": {
                "legale",
                "avvocato",
                "contratto",
                "causa",
                "legal",
                "lawyer",
                "contract",
                "lawsuit",
            },
            "financial": {
                "finanza",
                "denaro",
                "soldi",
                "banca",
                "finance",
                "money",
                "bank",
                "financial",
            },
            "cybersecurity": {"sicurezza", "hack", "cyber", "security", "hacking", "vulnerability"},
        }

        domain_kw = domain_keywords.get(principle.domain, set())
        if domain_kw & expanded_query:
            score *= 1.2  # Boost 20% per principi di dominio rilevante

    return score


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


class ConstitutionStore:
    """
    Gestisce i principi etici della costituzione.

    Responsabilità:
    - Caricamento principi da YAML
    - Supporto overlay di dominio
    - Risoluzione conflitti tra principi
    - Retrieval principi rilevanti

    Uso:
        store = ConstitutionStore()  # uses bundled data by default
        constitution = store.get_constitution(domain="medical")
        relevant = store.get_relevant_principles(prompt, top_k=10)
    """

    # Soglia di confidenza per accettare principi (Fix 3)
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
        self._matching_cache: OrderedDict[str, list[str]] = OrderedDict()
        self._matching_cache_max_size: int = 500
        self._available_domains_cache: list[str] | None = None

        self._cost_tracker: Any = None

        retriever_config = ConstitutionRetrieverConfig(
            openai_config=self.openai_config,
            max_parallel_agents=cfg.max_parallel_agents,
            use_enhanced_retrieval=cfg.use_enhanced_retrieval,
            confidence_threshold=cfg.confidence_threshold,
            use_domain_prefilter=cfg.use_domain_prefilter,
            max_prefilter_domains=cfg.max_prefilter_domains,
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
        """Imposta il TokenCostTracker per tracciare i costi (domain agents e prefilter)."""
        self._cost_tracker = tracker
        self._retriever.set_cost_tracker(tracker)

    @property
    def config_dir(self) -> Path:
        """Directory di configurazione."""
        return self._config_dir

    def get_available_domains(self) -> list[str]:
        """
        Ottiene lista di domini disponibili (overlay esistenti).

        Returns:
            Lista di nomi di dominio disponibili.
        """
        if self._available_domains_cache is not None:
            return self._available_domains_cache

        overlays_path = self._config_dir / self._overlays_dir
        available = []

        if overlays_path.exists():
            # Cerca file .yaml nella directory overlays
            for overlay_file in overlays_path.glob("*.yaml"):
                domain = overlay_file.stem  # Nome file senza estensione
                available.append(domain)

        self._available_domains_cache = sorted(available)
        return self._available_domains_cache

    def get_domain_descriptions(self) -> dict[str, str]:
        """
        Ottiene le descrizioni semantiche di tutti i domini disponibili.

        Carica dinamicamente le descrizioni dai file YAML degli overlay.
        Include anche una descrizione per 'core'.

        Returns:
            Dict {domain_name: description}
        """
        descriptions: dict[str, str] = {}

        # Descrizione per core (principi universali)
        descriptions["core"] = (
            "Universal ethical principles: harm prevention, privacy protection, "
            "deception prevention, honesty, safety, illegal activity prevention, "
            "malware prevention, child safety, transparency, autonomy."
        )

        # Carica descrizioni dagli overlay
        for domain_name in self.get_available_domains():
            try:
                overlay = self.load_overlay(domain_name)
                if overlay.description:
                    descriptions[domain_name] = overlay.description
                else:
                    # Fallback: genera descrizione base dal nome
                    descriptions[domain_name] = f"Principles specific to {domain_name} domain."
            except FileNotFoundError:
                descriptions[domain_name] = f"Principles specific to {domain_name} domain."

        return descriptions

    def get_domain_keywords(self) -> dict[str, list[str]]:
        """
        Ottiene le keywords di tutti i domini disponibili.

        Se overlay.keywords è definito e non vuoto → usa quello.
        Altrimenti se overlay.description è presente → estrae keyword deterministicamente.
        Altrimenti → [domain_name].

        Returns:
            Dict {domain_name: [keywords]}
        """
        keywords_map: dict[str, list[str]] = {}

        # Keywords per core
        keywords_map["core"] = [
            "harm",
            "safety",
            "privacy",
            "deception",
            "illegal",
            "malware",
            "child",
            "honesty",
            "transparency",
        ]

        # Carica keywords dagli overlay
        for domain_name in self.get_available_domains():
            try:
                overlay = self.load_overlay(domain_name)
                if overlay.keywords:
                    keywords_map[domain_name] = list(overlay.keywords)
                elif overlay.description:
                    keywords_map[domain_name] = _extract_keywords_from_description(overlay.description)
                else:
                    keywords_map[domain_name] = [domain_name]
            except FileNotFoundError:
                keywords_map[domain_name] = [domain_name]

        return keywords_map

    @property
    def principles(self) -> list[Principle]:
        """
        Tutti i principi core ordinati per priorità.

        Shortcut per accesso rapido senza overlay.
        """
        return resolve_conflict(self.load_core())

    def load_core(self) -> list[Principle]:
        """
        Carica principi core da file YAML (ruamel.yaml) con validazione Pydantic.
        Fail-fast: YAML invalido o principio obbligatorio mancante → ConstitutionLoadError.

        Returns:
            Lista di principi core (oggetti Pydantic tipizzati).

        Raises:
            FileNotFoundError: Se il file core non esiste.
            ConstitutionLoadError: Se il caricamento o la validazione fallisce.
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
        Carica overlay di dominio da file YAML (ruamel.yaml) con validazione Pydantic.
        Fail-fast: YAML invalido → ConstitutionLoadError.

        Args:
            domain: Nome del dominio (es. "medical", "legal").

        Returns:
            Overlay per il dominio specificato (oggetti Pydantic tipizzati).

        Raises:
            FileNotFoundError: Se il file overlay non esiste.
            ConstitutionLoadError: Se il caricamento o la validazione fallisce.
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
            keywords=[str(k) for k in oy.keywords if k],
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
        Assembla costituzione completa.

        Args:
            domain: Dominio per overlay opzionale.

        Returns:
            Constitution con principi core e eventuale overlay.
        """
        core = self.load_core()
        overlay = None

        if domain is not None:
            try:
                overlay = self.load_overlay(domain)
            except FileNotFoundError:
                # Domain senza overlay, usa solo core
                pass

        # Fail-fast: se siamo qui, caricamento è andato a buon fine
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
        Retrieval con agenti paralleli per dominio.

        Delegates to ConstitutionRetriever.

        Args:
            query: Testo della richiesta/prompt (qualsiasi lingua).
            top_k: Numero massimo di principi da ritornare.
            domain: Dominio opzionale (forza valutazione di questo dominio).
            retrieval_phase: Observability qualifier for domain prefilter persistence
                (``risk_routing`` vs ``deliberation_retrieval``).

        Returns:
            Lista di principi ordinati per rilevanza (max top_k).
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
        Retrieval con agenti paralleli per dominio, canale decisionale tipizzato.

        Delegates to ConstitutionRetriever.retrieve(). Returns the principles, the
        raw ``prefiltered_domains`` (decision channel — the caller owns the `core`
        exclusion, §5.5) and ``debug_info`` (best-effort telemetry, same shape as
        the retired per-retrieval debug accessor this replaces). Writes no instance state.

        Args:
            query: Testo della richiesta/prompt (qualsiasi lingua).
            top_k: Numero massimo di principi da ritornare.
            domain: Dominio opzionale (forza valutazione di questo dominio).
            retrieval_phase: Observability qualifier for domain prefilter persistence
                (``risk_routing`` vs ``deliberation_retrieval``).

        Returns:
            PrincipleRetrievalResult frozen (principles, prefiltered_domains, debug_info).
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
        Ordina principi per risoluzione conflitti.

        Regole:
        1. Hard constraints > Soft norms (sempre)
        2. Tra stesso livello: priority più alta vince
        3. A parità di priority: principio più specifico vince
        4. Tie finale: ordine alfabetico ID (determinismo)

        Args:
            principles: Lista di principi da ordinare.

        Returns:
            Lista ordinata per priorità di applicazione.
        """
        return resolve_conflict(principles)

    def reload(self) -> None:
        """
        Ricarica configurazione da file.

        Utile per hot-reload senza riavvio.
        Invalida tutte le cache incluse quelle degli agenti.
        """
        self._core_principles = None
        self._overlays.clear()
        self._matching_cache.clear()  # Pulisce anche cache matching
        self._available_domains_cache = None  # Reset cache domini
        self._retriever.invalidate_cache()


# =============================================================================
# Factory & Convenience
# =============================================================================


def create_store(config_dir: Path | str | None = None) -> ConstitutionStore:
    """
    Factory function per creare uno store.

    Args:
        config_dir: Directory configurazione opzionale.

    Returns:
        ConstitutionStore configurato.
    """
    return ConstitutionStore(config_dir=config_dir)


def create_default_constitution() -> Constitution:
    """
    Crea una costituzione con principi di default hardcoded.

    Utile per testing o quando non ci sono file YAML.
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
            keywords=["bomb", "explosive", "weapon", "harm", "hurt", "kill"],
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
            keywords=["hack", "steal", "illegal", "drug", "fraud"],
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
            keywords=["spy", "stalk", "private", "personal data", "doxx"],
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
            keywords=["accurate", "true", "fact", "certain"],
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
            keywords=["respectful", "empathetic", "polite"],
        ),
    ]

    return Constitution(
        core_principles=default_principles,
        active_overlay=None,
    )
