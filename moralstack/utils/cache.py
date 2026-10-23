"""
MoralStack Caching Utilities.

Provides LRU caching for LLM calls and module results to optimize
performance and avoid redundant calls.

PERFORMANCE FIX: Avoids calling the LLM again if the input is identical.
"""

from __future__ import annotations

import hashlib
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any, Generic, TypeVar

from moralstack.utils.history_window import recent_turns, truncate_turn_content

T = TypeVar("T")


@dataclass
class CacheEntry(Generic[T]):
    """Cache entry with metadata."""

    value: T
    created_at: float = field(default_factory=time.time)
    hits: int = 0

    def touch(self) -> None:
        """Increments the hit counter."""
        self.hits += 1


class LRUCache(Generic[T]):
    """
    Thread-safe LRU cache for results.

    Uses OrderedDict to keep the access order and supports
    TTL (time-to-live) based invalidation.

    Attributes:
        max_size: Maximum number of entries
        ttl_seconds: Entry time-to-live (0 = infinite)

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
        Initializes the cache.

        Args:
            max_size: Maximum number of entries (default 100)
            ttl_seconds: TTL in seconds, 0 for infinite (default 0)
        """
        self.max_size = max_size
        self.ttl_seconds = ttl_seconds
        self._cache: OrderedDict[str, CacheEntry[T]] = OrderedDict()
        self._lock = threading.RLock()
        self._hits = 0
        self._misses = 0

    def get(self, key: str) -> T | None:
        """
        Gets a value from the cache.

        Args:
            key: Key to look up

        Returns:
            Value if present and valid, None otherwise
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
                    # Expired entry
                    del self._cache[key]
                    self._misses += 1
                    return None

            # Hit: move to the end (most recent)
            self._cache.move_to_end(key)
            entry.touch()
            self._hits += 1
            return entry.value

    def set(self, key: str, value: T) -> None:
        """
        Inserts a value into the cache.

        Args:
            key: Key
            value: Value to cache
        """
        with self._lock:
            # If already present, update
            if key in self._cache:
                self._cache[key] = CacheEntry(value=value)
                self._cache.move_to_end(key)
                return

            # Evict if necessary
            while len(self._cache) >= self.max_size:
                self._cache.popitem(last=False)  # Remove the oldest

            self._cache[key] = CacheEntry(value=value)

    def clear(self) -> None:
        """Clears the cache."""
        with self._lock:
            self._cache.clear()
            self._hits = 0
            self._misses = 0

    def invalidate(self, key: str) -> bool:
        """
        Invalidates a specific entry.

        Returns:
            True if the entry existed and was removed
        """
        with self._lock:
            if key in self._cache:
                del self._cache[key]
                return True
            return False

    @property
    def stats(self) -> dict[str, Any]:
        """Cache statistics."""
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
    Cache specialized for MoralStack module results.

    Optimized for caching results of:
    - Perspectives (request, response) -> EnsembleResult
    - Simulation (request, response) -> SimulationResult
    - Hindsight (request, response, consequences) -> HindsightResult

    Supports selective invalidation when the response changes.

    Usage:
        cache = ModuleResultCache()

        # Store the perspectives result
        cache.set_perspective_result(request, response, result)

        # Retrieve if available
        cached = cache.get_perspective_result(request, response)
        if cached:
            return cached
    """

    def __init__(
        self,
        max_perspectives: int = 50,
        max_simulations: int = 50,
        max_hindsight: int = 50,
        ttl_seconds: float = 600,  # 10 minutes default
    ) -> None:
        """
        Initializes the caches for each module type.

        Args:
            max_perspectives: Max entries for perspectives
            max_simulations: Max entries for simulations
            max_hindsight: Max entries for hindsight
            ttl_seconds: TTL for all caches
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
        """Clears all caches."""
        self._perspectives.clear()
        self._simulations.clear()
        self._hindsight.clear()

    @property
    def stats(self) -> dict[str, dict[str, Any]]:
        """Aggregated statistics of all caches."""
        return {
            "perspectives": self._perspectives.stats,
            "simulations": self._simulations.stats,
            "hindsight": self._hindsight.stats,
        }


def build_context_fingerprint(
    *,
    developer_contract: Any = None,
    conversation_history: Any = None,
    prompt_set_tag: str = "",
) -> str:
    """
    Build a deterministic fingerprint of the conversational context.

    ``prompt_set_tag`` (``PromptSet.cache_tag_for(ctx)``) isolates non-default prompt sets in the
    shared cache. It is empty for the default set, so every default key stays byte-identical.

    Returns an empty string when all inputs are missing or empty.
    """
    parts: list[str] = []

    if developer_contract is not None:
        contract_hash = getattr(developer_contract, "contract_hash", "")
        if contract_hash:
            parts.append(f"dc:{contract_hash}")

    if conversation_history:
        # Same window as the modules see (moralstack/utils/history_window.py):
        # hashing less than a module is shown would let two conversations that
        # differ only past the cut collide on one cache entry.
        last_turns = recent_turns(conversation_history)

        if last_turns:
            turn_strings: list[str] = []
            for turn in last_turns:
                role = str(getattr(turn, "role", "") or "")
                content = truncate_turn_content(str(getattr(turn, "content", "") or ""))
                turn_strings.append(f"{role}:{content}")
            turns_blob = "|".join(turn_strings)
            history_hash = hashlib.md5(turns_blob.encode()).hexdigest()[:12]
            parts.append(f"ch:{history_hash}")

    if prompt_set_tag:
        parts.append(prompt_set_tag)

    return ";".join(parts)


# Global shared cache instance
_global_cache: ModuleResultCache | None = None


def get_global_cache() -> ModuleResultCache:
    """
    Gets the global cache, creating it if necessary.

    Returns:
        Shared ModuleResultCache
    """
    global _global_cache
    if _global_cache is None:
        _global_cache = ModuleResultCache()
    return _global_cache
