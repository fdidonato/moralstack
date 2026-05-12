"""
MoralStack Caching Utilities.

Fornisce caching LRU per chiamate LLM e risultati di moduli per ottimizzare
le performance ed evitare chiamate ridondanti.

FIX PERFORMANCE: Evita di richiamare l'LLM se l'input è identico.
"""

from __future__ import annotations

import hashlib
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any, Generic, TypeVar

T = TypeVar("T")


@dataclass
class CacheEntry(Generic[T]):
    """Entry nella cache con metadata."""

    value: T
    created_at: float = field(default_factory=time.time)
    hits: int = 0

    def touch(self) -> None:
        """Incrementa il contatore di hit."""
        self.hits += 1


class LRUCache(Generic[T]):
    """
    Cache LRU thread-safe per risultati.

    Usa OrderedDict per mantenere l'ordine di accesso e supporta
    invalidazione basata su TTL (time-to-live).

    Attributes:
        max_size: Numero massimo di entry
        ttl_seconds: Tempo di vita delle entry (0 = infinito)

    Usage:
        cache = LRUCache[str](max_size=100, ttl_seconds=300)
        cache.set("key", "value")
        result = cache.get("key")  # "value"
    """

    def __init__(
        self,
        max_size: int = 100,
        ttl_seconds: float = 0,
    ) -> None:
        """
        Inizializza la cache.

        Args:
            max_size: Numero massimo di entry (default 100)
            ttl_seconds: TTL in secondi, 0 per infinito (default 0)
        """
        self.max_size = max_size
        self.ttl_seconds = ttl_seconds
        self._cache: OrderedDict[str, CacheEntry[T]] = OrderedDict()
        self._lock = threading.RLock()
        self._hits = 0
        self._misses = 0

    def get(self, key: str) -> T | None:
        """
        Ottiene un valore dalla cache.

        Args:
            key: Chiave da cercare

        Returns:
            Valore se presente e valido, None altrimenti
        """
        with self._lock:
            entry = self._cache.get(key)

            if entry is None:
                self._misses += 1
                return None

            # Check TTL
            if self.ttl_seconds > 0:
                age = time.time() - entry.created_at
                if age > self.ttl_seconds:
                    # Entry scaduta
                    del self._cache[key]
                    self._misses += 1
                    return None

            # Hit: muovi in fondo (più recente)
            self._cache.move_to_end(key)
            entry.touch()
            self._hits += 1
            return entry.value

    def set(self, key: str, value: T) -> None:
        """
        Inserisce un valore nella cache.

        Args:
            key: Chiave
            value: Valore da cachare
        """
        with self._lock:
            # Se già presente, aggiorna
            if key in self._cache:
                self._cache[key] = CacheEntry(value=value)
                self._cache.move_to_end(key)
                return

            # Evict se necessario
            while len(self._cache) >= self.max_size:
                self._cache.popitem(last=False)  # Rimuovi il più vecchio

            self._cache[key] = CacheEntry(value=value)

    def clear(self) -> None:
        """Svuota la cache."""
        with self._lock:
            self._cache.clear()
            self._hits = 0
            self._misses = 0

    def invalidate(self, key: str) -> bool:
        """
        Invalida una specifica entry.

        Returns:
            True se l'entry esisteva ed è stata rimossa
        """
        with self._lock:
            if key in self._cache:
                del self._cache[key]
                return True
            return False

    @property
    def stats(self) -> dict[str, Any]:
        """Statistiche della cache."""
        with self._lock:
            total = self._hits + self._misses
            hit_rate = self._hits / total if total > 0 else 0.0
            return {
                "size": len(self._cache),
                "max_size": self.max_size,
                "hits": self._hits,
                "misses": self._misses,
                "hit_rate": hit_rate,
            }

    def __len__(self) -> int:
        with self._lock:
            return len(self._cache)

    def __contains__(self, key: str) -> bool:
        with self._lock:
            return key in self._cache


class ModuleResultCache:
    """
    Cache specializzata per risultati di moduli MoralStack.

    Ottimizzata per cachare risultati di:
    - Perspectives (request, response) -> EnsembleResult
    - Simulation (request, response) -> SimulationResult
    - Hindsight (request, response, consequences) -> HindsightResult

    Supporta invalidazione selettiva quando la response cambia.

    Usage:
        cache = ModuleResultCache()

        # Salva risultato perspectives
        cache.set_perspective_result(request, response, result)

        # Recupera se disponibile
        cached = cache.get_perspective_result(request, response)
        if cached:
            return cached
    """

    def __init__(
        self,
        max_perspectives: int = 50,
        max_simulations: int = 50,
        max_hindsight: int = 50,
        ttl_seconds: float = 600,  # 10 minuti default
    ) -> None:
        """
        Inizializza le cache per ogni tipo di modulo.

        Args:
            max_perspectives: Max entry per perspectives
            max_simulations: Max entry per simulations
            max_hindsight: Max entry per hindsight
            ttl_seconds: TTL per tutte le cache
        """
        self._perspectives: LRUCache[Any] = LRUCache(max_perspectives, ttl_seconds)
        self._simulations: LRUCache[Any] = LRUCache(max_simulations, ttl_seconds)
        self._hindsight: LRUCache[Any] = LRUCache(max_hindsight, ttl_seconds)

    def _hash_input(self, *args: str) -> str:
        """
        Generate a hash for multiple inputs.

        Empty strings are filtered out before joining. This preserves
        byte-equality with legacy cache keys when context_fingerprint="".
        """
        parts = [a for a in args if a]
        combined = "|".join(parts)
        return hashlib.md5(combined.encode()).hexdigest()

    # --- Perspectives ---

    def get_perspective_result(
        self,
        request: str,
        response: str,
        *,
        context_fingerprint: str = "",
    ) -> Any | None:
        """Retrieve cached perspectives result."""
        key = self._hash_input(request, response, context_fingerprint)
        return self._perspectives.get(key)

    def set_perspective_result(
        self,
        request: str,
        response: str,
        result: Any,
        *,
        context_fingerprint: str = "",
    ) -> None:
        """Store perspectives result."""
        key = self._hash_input(request, response, context_fingerprint)
        self._perspectives.set(key, result)

    # --- Simulations ---

    def get_simulation_result(
        self,
        request: str,
        response: str,
        *,
        context_fingerprint: str = "",
    ) -> Any | None:
        """Retrieve cached simulation result."""
        key = self._hash_input(request, response, context_fingerprint)
        return self._simulations.get(key)

    def set_simulation_result(
        self,
        request: str,
        response: str,
        result: Any,
        *,
        context_fingerprint: str = "",
    ) -> None:
        """Store simulation result."""
        key = self._hash_input(request, response, context_fingerprint)
        self._simulations.set(key, result)

    # --- Hindsight ---

    def get_hindsight_result(
        self,
        request: str,
        response: str,
        consequences_hash: str = "",
        *,
        context_fingerprint: str = "",
    ) -> Any | None:
        """Retrieve cached hindsight result."""
        key = self._hash_input(request, response, consequences_hash, context_fingerprint)
        return self._hindsight.get(key)

    def set_hindsight_result(
        self,
        request: str,
        response: str,
        result: Any,
        consequences_hash: str = "",
        *,
        context_fingerprint: str = "",
    ) -> None:
        """Store hindsight result."""
        key = self._hash_input(request, response, consequences_hash, context_fingerprint)
        self._hindsight.set(key, result)

    def clear_all(self) -> None:
        """Svuota tutte le cache."""
        self._perspectives.clear()
        self._simulations.clear()
        self._hindsight.clear()

    @property
    def stats(self) -> dict[str, dict[str, Any]]:
        """Statistiche aggregate di tutte le cache."""
        return {
            "perspectives": self._perspectives.stats,
            "simulations": self._simulations.stats,
            "hindsight": self._hindsight.stats,
        }


def build_context_fingerprint(
    *,
    developer_contract: Any = None,
    conversation_history: Any = None,
) -> str:
    """
    Build a deterministic fingerprint of the conversational context.

    Returns an empty string when both inputs are missing or empty.
    """
    parts: list[str] = []

    if developer_contract is not None:
        contract_hash = getattr(developer_contract, "contract_hash", "")
        if contract_hash:
            parts.append(f"dc:{contract_hash}")

    if conversation_history:
        try:
            last_turns = list(conversation_history)[-3:]
        except TypeError:
            last_turns = []

        if last_turns:
            turn_strings: list[str] = []
            for turn in last_turns:
                role = str(getattr(turn, "role", "") or "")
                content = str(getattr(turn, "content", "") or "")[:200]
                turn_strings.append(f"{role}:{content}")
            turns_blob = "|".join(turn_strings)
            history_hash = hashlib.md5(turns_blob.encode()).hexdigest()[:12]
            parts.append(f"ch:{history_hash}")

    return ";".join(parts)


# Global shared cache instance
_global_cache: ModuleResultCache | None = None


def get_global_cache() -> ModuleResultCache:
    """
    Ottiene la cache globale, creandola se necessario.

    Returns:
        ModuleResultCache condivisa
    """
    global _global_cache
    if _global_cache is None:
        _global_cache = ModuleResultCache()
    return _global_cache
