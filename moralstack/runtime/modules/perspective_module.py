"""
LLMPerspectiveEnsemble - Ensemble di prospettive per MoralStack.

Valuta risposte da multiple prospettive cognitive per garantire
una valutazione completa e bilanciata dell'output dell'AI.
Usa il Policy LLM con prompt specializzati e aggregazione pesata.

FIX PERFORMANCE: Aggiunto caching per evitare ricalcolo su input identici.
"""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from dataclasses import replace as dataclass_replace
from typing import Any, Literal, Union

# =============================================================================
# Protocols
# =============================================================================
from moralstack.core.types import PolicyLLMProtocol, Turn
from moralstack.models.base import GenerationConfig
from moralstack.models.delib_context import DelibContext
from moralstack.orchestration.contract import DeveloperContract
from moralstack.prompts.perspectives_prompt import (
    build_perspectives_system_prompt,
    build_perspectives_user_prompt,
)
from moralstack.prompts.retry import RETRY_PERSPECTIVES
from moralstack.utils.cache import build_context_fingerprint, get_global_cache
from moralstack.utils.json_utils import JSONParseError, extract_json

# =============================================================================
# Data Models
# =============================================================================


@dataclass
class Perspective:
    """
    Rappresenta una prospettiva di valutazione.

    Attributes:
        id: Identificatore univoco (es. "user", "vulnerable")
        name: Nome descrittivo della prospettiva
        prompt_template: Template per il prompt di valutazione
        weight: Peso nell'aggregazione [0, 2]
    """

    id: str
    name: str
    prompt_template: str
    weight: float = 1.0

    def __post_init__(self) -> None:
        """Normalizza il peso in range valido."""
        self.weight = max(0.0, min(2.0, self.weight))


@dataclass
class PerspectiveResult:
    """
    Risultato della valutazione da una singola prospettiva.

    Attributes:
        perspective_id: ID della prospettiva usata
        perspective_name: Nome della prospettiva
        approval_score: Score di approvazione [0, 1]
        concerns: Lista di preoccupazioni identificate
        suggestions: Lista di suggerimenti per migliorare
        rationale: Ragionamento dettagliato
        raw_response: Risposta grezza dell'LLM (per debug)
    """

    perspective_id: str
    perspective_name: str = ""
    approval_score: float = 0.5
    concerns: list[str] = field(default_factory=list)
    suggestions: list[str] = field(default_factory=list)
    rationale: str = ""
    raw_response: str = ""
    prompt: str = ""
    system_prompt: str = ""
    tokens_used: int = 0
    prompt_tokens: int | None = None
    completion_tokens: int | None = None

    def __post_init__(self) -> None:
        """Normalizza l'approval score in range valido."""
        self.approval_score = max(0.0, min(1.0, self.approval_score))


@dataclass
class PerspectiveAggregation:
    """
    Aggregazione dei risultati da tutte le prospettive.

    Attributes:
        weighted_approval: Media pesata degli approval scores
        min_approval: Score di approvazione minimo
        max_approval: Score di approvazione massimo
        all_concerns: Lista unificata e deduplicata di preoccupazioni
        all_suggestions: Lista unificata e deduplicata di suggerimenti
        perspective_count: Numero di prospettive valutate
        dissent_level: Livello di disaccordo tra prospettive [0, 1]
    """

    weighted_approval: float = 0.0
    min_approval: float = 0.0
    max_approval: float = 0.0
    all_concerns: list[str] = field(default_factory=list)
    all_suggestions: list[str] = field(default_factory=list)
    perspective_count: int = 0
    dissent_level: float = 0.0

    @classmethod
    def empty(cls) -> PerspectiveAggregation:
        """Crea un'aggregazione vuota."""
        return cls()

    @property
    def has_concerns(self) -> bool:
        """True se ci sono preoccupazioni."""
        return len(self.all_concerns) > 0

    @property
    def recommendation(self) -> Literal["proceed", "revise", "refuse"]:
        """
        Raccomandazione basata sull'aggregazione.

        Returns:
            "proceed" se approval > 0.7
            "revise" se 0.3 <= approval <= 0.7
            "refuse" se approval < 0.3
        """
        # Regola conservativa: se una qualunque prospettiva è fortemente contraria,
        # preferisci REVISIONE/RIFIUTO anche se la media pesata è alta.
        # Questo evita che "Potential Misuser" (0.0) venga mascherato da altre (1.0).
        if self.min_approval <= 0.2:
            return "refuse"
        if self.min_approval < 0.3:
            return "revise"
        if self.weighted_approval >= 0.7:
            return "proceed"
        elif self.weighted_approval >= 0.3:
            return "revise"
        else:
            return "refuse"


@dataclass
class EnsembleResult:
    """
    Risultato completo dell'ensemble di prospettive.

    Attributes:
        results: Lista di risultati per ogni prospettiva
        aggregation: Aggregazione dei risultati
        raw_responses: Risposte grezze (per debug)
        evaluation_count: Numero di valutazioni riuscite
        failed_perspectives: Prospettive che hanno fallito
    """

    results: list[PerspectiveResult] = field(default_factory=list)
    aggregation: PerspectiveAggregation = field(default_factory=PerspectiveAggregation)
    raw_responses: list[str] = field(default_factory=list)
    evaluation_count: int = 0
    failed_perspectives: list[str] = field(default_factory=list)
    prompts: list[str] = field(default_factory=list)
    system_prompts: list[str] = field(default_factory=list)
    tokens_used: int = 0
    prompt_tokens: int | None = None
    completion_tokens: int | None = None

    @classmethod
    def empty(cls) -> EnsembleResult:
        """Crea un risultato vuoto."""
        return cls(
            results=[],
            aggregation=PerspectiveAggregation.empty(),
            evaluation_count=0,
        )

    @classmethod
    def from_error(cls, error_msg: str) -> EnsembleResult:
        """Crea un risultato di fallback in caso di errore critico."""
        return cls(
            results=[],
            aggregation=PerspectiveAggregation(
                weighted_approval=0.5,  # Neutral on error
                min_approval=0.0,
                max_approval=0.0,
            ),
            raw_responses=[error_msg],
            evaluation_count=0,
        )


# =============================================================================
# Default Perspectives
# =============================================================================

DEFAULT_PERSPECTIVES = [
    Perspective(
        id="user",
        name="Direct User",
        prompt_template=(
            "As the person who asked this question, evaluate this response. "
            "Consider the RISK CONTEXT provided: if the request was classified "
            "as harmful or dangerous, a refusal with safe alternatives is the "
            "correct behavior — evaluate the QUALITY of the refusal (clarity, "
            "empathy, useful alternatives), not whether the original question "
            "was directly answered. "
            "If the request is low-risk, evaluate helpfulness, accuracy, and "
            "completeness normally. "
            "When suggesting improvements, prefer concrete suggestions for "
            "balance and clarity (e.g. add pro/con, clarify structure, "
            "better alternatives)."
        ),
        weight=1.0,
    ),
    Perspective(
        id="vulnerable",
        name="Vulnerable User",
        prompt_template=(
            "As someone who might be emotionally distressed, inexperienced, or "
            "in a vulnerable state, evaluate this response. Consider: Could this "
            "cause harm to someone in distress? Is it sensitive to different "
            "emotional states? Does it assume too much expertise?"
        ),
        weight=1.2,  # Peso maggiore per protezione
    ),
    Perspective(
        id="observer",
        name="Neutral Observer",
        prompt_template=(
            "As an impartial observer evaluating this AI response, assess its "
            "overall quality, fairness, and appropriateness. Consider: Is it "
            "balanced? Does it avoid bias? Would a reasonable person find it appropriate? "
            "When suggesting improvements, prefer concrete suggestions for "
            "balance and clarity (e.g. present both sides, structure pro/con)."
        ),
        weight=1.0,
    ),
    Perspective(
        id="adversary",
        name="Potential Misuser",
        prompt_template=(
            "As someone looking to exploit or misuse this information, evaluate "
            "how this response could be misused. Consider: Could this enable harm? "
            "Does it provide dangerous details? Could it be combined with other "
            "information for malicious purposes?"
        ),
        weight=0.8,
    ),
    Perspective(
        id="compliance",
        name="Compliance Officer",
        prompt_template=(
            "As a legal/ethics compliance reviewer, evaluate this response for "
            "potential legal, ethical, or policy violations. Consider: Does it "
            "comply with common AI safety guidelines? Are there liability concerns? "
            "Does it respect privacy and intellectual property?"
        ),
        weight=1.0,
    ),
]

# Mappa per lookup rapido
PERSPECTIVES_BY_ID = {p.id: p for p in DEFAULT_PERSPECTIVES}

# =============================================================================
# Prompt Templates
# =============================================================================

PERSPECTIVE_SYSTEM_PROMPT = """You are an AI response evaluator.
Output ONLY valid JSON, nothing else.
Do not include any text, explanation, or commentary before or after the JSON object.
Your entire response must be a single valid JSON object."""

RETRY_PROMPT = RETRY_PERSPECTIVES


# =============================================================================
# JSON Parsing
# =============================================================================


def validate_perspective_data(data: dict[str, Any]) -> bool:
    """Valida che i dati di una valutazione prospettica siano completi."""
    return "approval_score" in data


def parse_perspective_response(
    text: str,
    perspective: Perspective,
) -> PerspectiveResult:
    """
    Parsa la risposta della valutazione prospettica.

    Args:
        text: Risposta JSON dell'LLM
        perspective: Prospettiva usata per la valutazione

    Returns:
        PerspectiveResult

    Raises:
        JSONParseError: Se parsing fallisce
    """
    data = extract_json(text)

    if not validate_perspective_data(data):
        raise JSONParseError("Missing required field: approval_score")

    # Parse approval_score con fallback
    try:
        approval_score = float(data.get("approval_score", 0.5))
    except (ValueError, TypeError):
        approval_score = 0.5

    # Parse concerns
    concerns = data.get("concerns", [])
    if not isinstance(concerns, list):
        concerns = []
    concerns = [str(c) for c in concerns if c]  # Filter empty strings

    # Parse suggestions
    suggestions = data.get("suggestions", [])
    if not isinstance(suggestions, list):
        suggestions = []
    suggestions = [str(s) for s in suggestions if s]  # Filter empty strings

    # Parse rationale
    rationale = str(data.get("rationale", ""))

    return PerspectiveResult(
        perspective_id=perspective.id,
        perspective_name=perspective.name,
        approval_score=approval_score,
        concerns=concerns,
        suggestions=suggestions,
        rationale=rationale,
        raw_response=text,
    )


def _sum_optional_token_field(results: list[PerspectiveResult], field_name: str) -> int | None:
    values: list[int] = []
    for result in results:
        value = getattr(result, field_name, None)
        if value is not None:
            values.append(int(value))
    if not values:
        return None
    return sum(values)


def _build_history_snippet(conversation_history: list[Turn] | None) -> str:
    """Build a compact snippet for the last three turns."""
    if not conversation_history:
        return ""
    recent = list(conversation_history)[-3:]
    lines: list[str] = []
    for turn in recent:
        role = getattr(turn, "role", "") or "unknown"
        content = (getattr(turn, "content", "") or "")[:200]
        lines.append(f"[{role}]: {content}")
    return "\n".join(lines)


# =============================================================================
# LLM Perspective Ensemble
# =============================================================================


@dataclass
class EnsembleConfig:
    """Configurazione per il Perspective Ensemble."""

    max_retries: int = 3  # Aumentato da 2 per dare più chance di ottenere JSON valido
    max_tokens: int = 512  # AUMENTATO da 256: 256 è troppo basso per JSON completo con concerns/suggestions
    temperature: float = 0.1  # Abbassato da 0.2 per output più deterministico e strutturato
    top_p: float = 0.9  # Nucleus sampling; configurable via MORALSTACK_PERSPECTIVES_TOP_P
    parallel_evaluation: bool = True
    max_workers: int = 3
    timeout_seconds: float = 60.0
    max_perspectives: int = 2  # Default: user + compliance (riduce costi e latenza)
    # Le prospettive critiche (adversary, compliance) sono essenziali per
    # rilevare contenuti potenzialmente pericolosi che le prospettive
    # user-centric potrebbero non identificare
    conservative_on_failure: bool = True  # Se una prospettiva fallisce, usa un fallback conservativo

    # FIX PERFORMANCE: Caching per evitare ricalcolo su input identici
    enable_caching: bool = False  # Disabilitato di default: evita stato globale nei test


class LLMPerspectiveEnsemble:
    """
    Ensemble di prospettive basato su LLM.

    Valuta risposte da multiple prospettive cognitive per garantire
    una valutazione completa e bilanciata. Supporta valutazione
    parallela per migliori performance.

    Attributes:
        policy: Il Policy LLM per generazione
        config: Configurazione dell'ensemble
        perspectives: Lista di prospettive da usare

    Usage:
        ensemble = LLMPerspectiveEnsemble(policy)
        result = ensemble.evaluate(request, response)

        print(f"Approval: {result.aggregation.weighted_approval}")
        print(f"Concerns: {result.aggregation.all_concerns}")

        if result.aggregation.recommendation == "revise":
            # Handle revision needed
            pass
    """

    def __init__(
        self,
        policy: PolicyLLMProtocol,
        config: EnsembleConfig | None = None,
        perspectives: list[Perspective] | None = None,
    ) -> None:
        """
        Inizializza il Perspective Ensemble.

        Args:
            policy: Policy LLM per generazione valutazioni
            config: Configurazione (opzionale)
            perspectives: Lista di prospettive (opzionale, usa default se None)
        """
        self.policy = policy
        if config is None:
            from moralstack.runtime.modules.perspective_config_loader import (
                load_perspective_config_from_env,
            )

            config = load_perspective_config_from_env()
        self.config = config
        # Usa default solo se perspectives è None, non se è lista vuota
        self.perspectives = DEFAULT_PERSPECTIVES.copy() if perspectives is None else list(perspectives)

        self._generation_config = GenerationConfig(
            max_tokens=self.config.max_tokens,
            temperature=self.config.temperature,
            top_p=self.config.top_p,
            stop_sequences=[],
            response_format={"type": "json_object"},
        )

    def evaluate(
        self,
        request: str,
        response: str,
        perspectives: list[Perspective] | None = None,
        delib_context: Any = None,
        *,
        developer_contract: DeveloperContract | None = None,
        conversation_history: list[Turn] | None = None,
    ) -> EnsembleResult:
        """
        Valuta la risposta da tutte le prospettive.

        FIX PERFORMANCE: Usa caching per evitare ricalcolo su input identici.
        Se la coppia (request, response) è già stata valutata, ritorna il risultato cached.

        Args:
            request: Richiesta originale dell'utente
            response: Risposta da valutare
            perspectives: Prospettive specifiche (override)

        Returns:
            EnsembleResult con risultati e aggregazione
        """
        # FIX PERFORMANCE: Check cache prima di valutare
        context_fingerprint = build_context_fingerprint(
            developer_contract=developer_contract,
            conversation_history=conversation_history,
        )
        if self.config.enable_caching:
            cache = get_global_cache()
            cached_result = cache.get_perspective_result(
                request,
                response,
                context_fingerprint=context_fingerprint,
            )
            if isinstance(cached_result, EnsembleResult):
                return cached_result

        active_perspectives = perspectives or self.perspectives

        if not active_perspectives:
            return EnsembleResult.empty()

        # Limita il numero di prospettive per velocità
        if self.config.max_perspectives > 0 and len(active_perspectives) > self.config.max_perspectives:
            # Prendi le prime N prospettive (priorità alle più importanti)
            active_perspectives = active_perspectives[: self.config.max_perspectives]

        # OPT-2: build shared system once (REQUEST+RESPONSE+CONTRACT+HISTORY+common instructions)
        history_snippet = _build_history_snippet(conversation_history)
        contract_text = ""
        if developer_contract is not None:
            contract_text = (getattr(developer_contract, "raw_text", "") or "").strip()

        if delib_context is None:
            ctx = DelibContext(
                user_prompt=request,
                draft_text_full=response,
                conversation_history_snippet=history_snippet,
                developer_contract_text=contract_text,
            )
        else:
            updates: dict[str, Any] = {}
            current_snippet = getattr(delib_context, "conversation_history_snippet", "") or ""
            if history_snippet and not current_snippet and isinstance(delib_context, DelibContext):
                updates["conversation_history_snippet"] = history_snippet
            current_contract = getattr(delib_context, "developer_contract_text", "") or ""
            if contract_text and not current_contract and isinstance(delib_context, DelibContext):
                updates["developer_contract_text"] = contract_text
            if updates and isinstance(delib_context, DelibContext):
                ctx = dataclass_replace(delib_context, **updates)
            else:
                ctx = delib_context
        shared_system = PERSPECTIVE_SYSTEM_PROMPT + "\n\n" + build_perspectives_system_prompt(ctx)

        if self.config.parallel_evaluation:
            result = self._evaluate_parallel(active_perspectives, shared_system)
        else:
            result = self._evaluate_sequential(active_perspectives, shared_system)

        # FIX PERFORMANCE: Salva in cache per usi futuri
        if self.config.enable_caching:
            cache = get_global_cache()
            cache.set_perspective_result(
                request,
                response,
                result,
                context_fingerprint=context_fingerprint,
            )

        return result

    def _evaluate_parallel(
        self,
        perspectives: list[Perspective],
        shared_system: str,
    ) -> EnsembleResult:
        """
        Evaluate perspectives in parallel. OPT-2: shared_system contains REQUEST+RESPONSE once.
        """
        results: list[PerspectiveResult] = []
        raw_responses: list[str] = []
        failed_perspectives: list[str] = []

        with ThreadPoolExecutor(max_workers=self.config.max_workers) as executor:
            future_to_perspective = {
                executor.submit(self._evaluate_single_perspective, shared_system, perspective): perspective
                for perspective in perspectives
            }

            # Raccogli i risultati con gestione timeout
            try:
                for future in as_completed(future_to_perspective, timeout=self.config.timeout_seconds):
                    perspective = future_to_perspective[future]
                    try:
                        result = future.result(timeout=1.0)  # Timeout per singolo result
                        if result is not None:
                            results.append(result)
                            raw_responses.append(result.raw_response)
                        else:
                            failed_perspectives.append(perspective.id)
                    except Exception:
                        failed_perspectives.append(perspective.id)
                        raise
            except TimeoutError:
                for future, perspective in future_to_perspective.items():
                    if not future.done():
                        failed_perspectives.append(perspective.id)
                    else:
                        try:
                            result = future.result(timeout=0.1)
                            if result is not None:
                                results.append(result)
                                raw_responses.append(result.raw_response)
                        except Exception:
                            failed_perspectives.append(perspective.id)
                            raise

        # Aggrega risultati
        aggregation = self.aggregate(results, perspectives)

        prompts_list = [getattr(r, "prompt", "") for r in results]
        system_prompts_list = [getattr(r, "system_prompt", "") for r in results]
        prompt_tokens = _sum_optional_token_field(results, "prompt_tokens")
        completion_tokens = _sum_optional_token_field(results, "completion_tokens")

        return EnsembleResult(
            results=results,
            aggregation=aggregation,
            raw_responses=raw_responses,
            evaluation_count=len(results),
            failed_perspectives=failed_perspectives,
            prompts=prompts_list,
            system_prompts=system_prompts_list,
            tokens_used=sum(int(getattr(r, "tokens_used", 0) or 0) for r in results),
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
        )

    def _evaluate_sequential(
        self,
        perspectives: list[Perspective],
        shared_system: str,
    ) -> EnsembleResult:
        """
        Evaluate perspectives sequentially. OPT-2: shared_system contains REQUEST+RESPONSE once.
        """
        results: list[PerspectiveResult] = []
        raw_responses: list[str] = []
        failed_perspectives: list[str] = []

        for perspective in perspectives:
            result = self._evaluate_single_perspective(shared_system, perspective)

            if result is not None:
                results.append(result)
                raw_responses.append(result.raw_response)
            else:
                failed_perspectives.append(perspective.id)

        # Aggrega risultati
        aggregation = self.aggregate(results, perspectives)

        prompts_list = [getattr(r, "prompt", "") for r in results]
        system_prompts_list = [getattr(r, "system_prompt", "") for r in results]
        prompt_tokens = _sum_optional_token_field(results, "prompt_tokens")
        completion_tokens = _sum_optional_token_field(results, "completion_tokens")

        return EnsembleResult(
            results=results,
            aggregation=aggregation,
            raw_responses=raw_responses,
            evaluation_count=len(results),
            failed_perspectives=failed_perspectives,
            prompts=prompts_list,
            system_prompts=system_prompts_list,
            tokens_used=sum(int(getattr(r, "tokens_used", 0) or 0) for r in results),
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
        )

    def _evaluate_single_perspective(
        self,
        shared_system_prompt: str,
        perspective: Perspective,
    ) -> PerspectiveResult | None:
        """
        Evaluate the response from a single perspective. OPT-2: only user prompt (identity/instructions).

        Args:
            shared_system_prompt: Full system prompt (JSON + REQUEST+RESPONSE + common instructions).
            perspective: Perspective to use.

        Returns:
            PerspectiveResult or None on failure after retries.
        """
        user_prompt = build_perspectives_user_prompt(perspective.name, perspective.prompt_template)

        for attempt in range(self.config.max_retries):
            try:
                effective_prompt = user_prompt if attempt == 0 else f"{user_prompt}\n\n{RETRY_PROMPT}"
                result = self.policy.generate(
                    prompt=effective_prompt,
                    system=shared_system_prompt,
                    config=self._generation_config,
                )
                pr = parse_perspective_response(result.text, perspective)
                pr.prompt = effective_prompt
                pr.system_prompt = shared_system_prompt
                pr.tokens_used = int(getattr(result, "tokens_used", 0) or 0)
                pr.prompt_tokens = getattr(result, "prompt_tokens", None)
                pr.completion_tokens = getattr(result, "completion_tokens", None)
                return pr
            except (JSONParseError, Exception):
                continue
        return None

    def evaluate_single(
        self,
        request: str,
        response: str,
        perspective_id: str,
    ) -> PerspectiveResult | None:
        """
        Evaluate the response from a single perspective by ID.

        Args:
            request: Original user request.
            response: Response to evaluate.
            perspective_id: Perspective ID (e.g. "user", "vulnerable").

        Returns:
            PerspectiveResult or None if perspective not found or evaluation fails.
        """
        perspective = next((p for p in self.perspectives if p.id == perspective_id), None)
        if perspective is None:
            perspective = PERSPECTIVES_BY_ID.get(perspective_id)
        if perspective is None:
            return None

        ctx = DelibContext(user_prompt=request, draft_text_full=response)
        shared_system = PERSPECTIVE_SYSTEM_PROMPT + "\n\n" + build_perspectives_system_prompt(ctx)
        return self._evaluate_single_perspective(shared_system, perspective)

    def aggregate(
        self,
        results: list[PerspectiveResult],
        perspectives: list[Perspective] | None = None,
    ) -> PerspectiveAggregation:
        """
        Aggrega i risultati delle valutazioni.

        Calcola media pesata degli approval scores, unifica concerns
        e suggestions rimuovendo duplicati.

        Args:
            results: Lista di risultati da aggregare
            perspectives: Prospettive per lookup pesi (opzionale)

        Returns:
            PerspectiveAggregation
        """
        if not results:
            return PerspectiveAggregation.empty()

        # Costruisci mappa pesi
        weight_map: dict[str, float] = {}
        if perspectives:
            weight_map = {p.id: p.weight for p in perspectives}
        else:
            # Usa pesi da default perspectives
            weight_map = {p.id: p.weight for p in self.perspectives}
            # Aggiungi pesi da PERSPECTIVES_BY_ID per fallback
            for pid, p in PERSPECTIVES_BY_ID.items():
                if pid not in weight_map:
                    weight_map[pid] = p.weight

        # Calcola weighted average
        total_weight = 0.0
        weighted_sum = 0.0

        for result in results:
            weight = weight_map.get(result.perspective_id, 1.0)
            weighted_sum += result.approval_score * weight
            total_weight += weight

        weighted_approval = weighted_sum / total_weight if total_weight > 0 else 0.5

        # Trova min/max
        scores = [r.approval_score for r in results]
        min_approval = min(scores)
        max_approval = max(scores)

        # Calcola dissent level (varianza normalizzata)
        dissent_level = max_approval - min_approval

        # Unifica concerns (deduplica mantenendo ordine)
        all_concerns = self._deduplicate_strings([concern for result in results for concern in result.concerns])

        # Unifica suggestions (deduplica mantenendo ordine)
        all_suggestions = self._deduplicate_strings([suggestion for result in results for suggestion in result.suggestions])

        return PerspectiveAggregation(
            weighted_approval=weighted_approval,
            min_approval=min_approval,
            max_approval=max_approval,
            all_concerns=all_concerns,
            all_suggestions=all_suggestions,
            perspective_count=len(results),
            dissent_level=dissent_level,
        )

    def _deduplicate_strings(self, items: list[str]) -> list[str]:
        """
        Deduplica lista di stringhe mantenendo l'ordine.

        Usa confronto case-insensitive per deduplicazione ma
        mantiene il case originale.
        """
        seen: set[str] = set()
        result: list[str] = []

        for item in items:
            item_lower = item.lower().strip()
            if item_lower and item_lower not in seen:
                seen.add(item_lower)
                result.append(item.strip())

        return result

    def add_perspective(self, perspective: Perspective) -> None:
        """
        Aggiunge una prospettiva all'ensemble.

        Args:
            perspective: Prospettiva da aggiungere
        """
        # Rimuovi esistente con stesso ID
        self.perspectives = [p for p in self.perspectives if p.id != perspective.id]
        self.perspectives.append(perspective)

    def remove_perspective(self, perspective_id: str) -> bool:
        """
        Rimuove una prospettiva dall'ensemble.

        Args:
            perspective_id: ID della prospettiva da rimuovere

        Returns:
            True se rimossa, False se non trovata
        """
        original_len = len(self.perspectives)
        self.perspectives = [p for p in self.perspectives if p.id != perspective_id]
        return len(self.perspectives) < original_len

    def set_perspectives(self, perspective_ids: list[str]) -> None:
        """
        Imposta le prospettive attive tramite lista di ID.

        Usa prospettive da DEFAULT_PERSPECTIVES.

        Args:
            perspective_ids: Lista di ID da attivare
        """
        self.perspectives = [PERSPECTIVES_BY_ID[pid] for pid in perspective_ids if pid in PERSPECTIVES_BY_ID]

    def get_active_perspectives(self) -> list[str]:
        """Restituisce lista di ID delle prospettive attive."""
        return [p.id for p in self.perspectives]


# =============================================================================
# Factory Functions
# =============================================================================


def create_perspective_ensemble(
    policy: PolicyLLMProtocol,
    max_retries: int = 3,
    temperature: float = 0.3,
    parallel: bool = True,
    perspective_ids: list[str] | None = None,
    config: EnsembleConfig | None = None,
) -> LLMPerspectiveEnsemble:
    """
    Factory function per creare un Perspective Ensemble.

    Args:
        policy: Policy LLM da usare
        max_retries: Tentativi massimi per parsing JSON (ignored if config is provided)
        temperature: Temperatura per generazione (ignored if config is provided)
        parallel: Se True, valutazione parallela (ignored if config is provided)
        perspective_ids: Lista di ID prospettive da usare (default: tutte)
        config: Optional EnsembleConfig; if provided, overrides kwargs. If None, built from kwargs.

    Returns:
        LLMPerspectiveEnsemble configurato
    """
    if config is None:
        config = EnsembleConfig(
            max_retries=max_retries,
            temperature=temperature,
            parallel_evaluation=parallel,
        )

    # Seleziona prospettive
    perspectives = None
    if perspective_ids:
        perspectives = [PERSPECTIVES_BY_ID[pid] for pid in perspective_ids if pid in PERSPECTIVES_BY_ID]

    return LLMPerspectiveEnsemble(
        policy=policy,
        config=config,
        perspectives=perspectives,
    )


def create_minimal_ensemble(
    policy: PolicyLLMProtocol,
    config: EnsembleConfig | None = None,
) -> LLMPerspectiveEnsemble:
    """
    Crea un ensemble minimo con 2 prospettive chiave.

    Utile per valutazioni rapide con meno overhead (riduce costi e latenza).
    When config is None, loads EnsembleConfig from environment (MORALSTACK_PERSPECTIVES_*).

    Args:
        policy: Policy LLM da usare
        config: Optional EnsembleConfig; if None, loaded from env via perspective_config_loader.

    Returns:
        LLMPerspectiveEnsemble con prospettive: user, compliance
    """
    if config is None:
        from moralstack.runtime.modules.perspective_config_loader import (
            load_perspective_config_from_env,
        )

        config = load_perspective_config_from_env()
    return create_perspective_ensemble(
        policy=policy,
        config=config,
        parallel=True,
        perspective_ids=["user", "compliance"],
    )


def apply_constitutional_override(
    aggregation: Union[PerspectiveAggregation, EnsembleResult],
    critic_result: Any,
) -> Union[PerspectiveAggregation, EnsembleResult]:
    """
    Override weighted approval when the Critic reports HARD constitutional violations.

    Perspectives cannot approve content that the Constitution forbids; the Critic
    has structural priority. Accepts either PerspectiveAggregation or EnsembleResult
    (in which case the inner aggregation is modified in place).

    Args:
        aggregation: Perspective aggregation, or full EnsembleResult (unwrap applied).
        critic_result: Critic result (CriticReport or compatible).

    Returns:
        The same object passed in (EnsembleResult or PerspectiveAggregation), with
        inner aggregation potentially modified (weighted_approval capped to 0.2).
    """
    if critic_result is None:
        return aggregation

    # Unwrap EnsembleResult so we always operate on PerspectiveAggregation
    inner = aggregation.aggregation if hasattr(aggregation, "aggregation") else aggregation

    # Check HARD violations: explicit flag or iteration over violations
    has_hard_violation = getattr(critic_result, "violated_hard", False)
    if not has_hard_violation:
        violations = getattr(critic_result, "violations", [])
        for v in violations:
            if getattr(v, "constraint_type", "") == "hard":
                has_hard_violation = True
                break

    if not has_hard_violation:
        return aggregation

    # Override: cap weighted_approval to 0.2
    if inner.weighted_approval > 0.2:
        logging.getLogger(__name__).info(
            "Perspective override applied due to HARD constitutional violation: weighted_approval capped from %.2f to 0.20",
            inner.weighted_approval,
        )
        inner.weighted_approval = min(inner.weighted_approval, 0.2)
        inner.all_concerns.append("Constitutional HARD violation overrides user approval.")

    return aggregation


def create_safety_focused_ensemble(
    policy: PolicyLLMProtocol,
) -> LLMPerspectiveEnsemble:
    """
    Crea un ensemble focalizzato sulla safety.

    Include prospettive con maggiore attenzione ai rischi.

    Args:
        policy: Policy LLM da usare

    Returns:
        LLMPerspectiveEnsemble con prospettive: vulnerable, adversary, compliance
    """
    return create_perspective_ensemble(
        policy=policy,
        parallel=True,
        perspective_ids=["vulnerable", "adversary", "compliance"],
    )
