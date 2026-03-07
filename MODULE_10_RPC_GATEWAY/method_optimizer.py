#!/usr/bin/env python3
"""
Method Optimizer
=================
Per-method RPC routing hints, caching, and cost classification.

Different RPC methods have vastly different cost profiles, caching
potential, and optimal routing strategies:

  - ``eth_getLogs``       → expensive, batch by block range, dedicate endpoint
  - ``eth_call``          → moderate, batch multiple, cache 1-2 blocks
  - ``eth_getBalance``    → cheap, batch addresses, cache aggressively
  - ``eth_subscribe``     → persistent WS, reuse subscriptions
  - ``eth_sendRawTransaction`` → dedicated high-priority channel with SWQoS

This module provides the optimiser lookup and an LRU+TTL in-process
cache for results that are block-stable.
"""
from __future__ import annotations

import logging
import time
from collections import OrderedDict
from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, Optional, Tuple

from .config import MethodOptimizerConfig, get_gateway_config

logger = logging.getLogger(__name__)


# ── Enums ─────────────────────────────────────────────────────────

class RoutingStrategy(str, Enum):
    """Optimal routing strategy for a method."""
    STANDARD = "standard"            # Load-balanced
    BATCH = "batch"                  # Should be batched with others
    DEDICATED = "dedicated"          # Use dedicated endpoint
    PRIORITY = "priority"            # Use priority channel
    WEBSOCKET = "websocket"          # Use persistent WS
    CACHEABLE = "cacheable"          # Result can be cached


class CostTier(str, Enum):
    """Cost tier for rate-limit token consumption."""
    FREE = "free"        # 0.2 tokens
    CHEAP = "cheap"      # 0.5 tokens
    STANDARD = "standard"  # 1.0 tokens
    MODERATE = "moderate"  # 2.0 tokens
    EXPENSIVE = "expensive"  # 3.0+ tokens


# ── Method Profile ────────────────────────────────────────────────

@dataclass(frozen=True)
class MethodProfile:
    """Performance profile for one RPC method."""
    method: str
    cost_tier: CostTier
    token_cost: float
    routing: RoutingStrategy
    cacheable: bool = False
    cache_ttl_s: float = 0.0  # 0 = not cacheable
    batchable: bool = True
    description: str = ""


# ── Known Method Profiles ─────────────────────────────────────────

METHOD_PROFILES: Dict[str, MethodProfile] = {
    # ── Block / Chain State ────────────────────────────────────
    "eth_blockNumber": MethodProfile(
        method="eth_blockNumber",
        cost_tier=CostTier.FREE,
        token_cost=0.2,
        routing=RoutingStrategy.STANDARD,
        cacheable=True,
        cache_ttl_s=1.0,
        description="Latest block number",
    ),
    "eth_gasPrice": MethodProfile(
        method="eth_gasPrice",
        cost_tier=CostTier.FREE,
        token_cost=0.2,
        routing=RoutingStrategy.STANDARD,
        cacheable=True,
        cache_ttl_s=2.0,
        description="Current gas price",
    ),
    "eth_maxPriorityFeePerGas": MethodProfile(
        method="eth_maxPriorityFeePerGas",
        cost_tier=CostTier.FREE,
        token_cost=0.2,
        routing=RoutingStrategy.STANDARD,
        cacheable=True,
        cache_ttl_s=2.0,
        description="Priority fee suggestion",
    ),
    "eth_chainId": MethodProfile(
        method="eth_chainId",
        cost_tier=CostTier.FREE,
        token_cost=0.1,
        routing=RoutingStrategy.CACHEABLE,
        cacheable=True,
        cache_ttl_s=3600.0,  # Never changes
        description="Chain ID (immutable)",
    ),
    "net_version": MethodProfile(
        method="net_version",
        cost_tier=CostTier.FREE,
        token_cost=0.1,
        routing=RoutingStrategy.CACHEABLE,
        cacheable=True,
        cache_ttl_s=3600.0,
        description="Network version (immutable)",
    ),

    # ── Balance & Nonce ────────────────────────────────────────
    "eth_getBalance": MethodProfile(
        method="eth_getBalance",
        cost_tier=CostTier.CHEAP,
        token_cost=0.5,
        routing=RoutingStrategy.BATCH,
        cacheable=True,
        cache_ttl_s=2.0,
        batchable=True,
        description="Account balance — batch addresses",
    ),
    "eth_getTransactionCount": MethodProfile(
        method="eth_getTransactionCount",
        cost_tier=CostTier.CHEAP,
        token_cost=0.5,
        routing=RoutingStrategy.STANDARD,
        cacheable=True,
        cache_ttl_s=1.0,
        description="Account nonce",
    ),

    # ── Contract Reads ─────────────────────────────────────────
    "eth_call": MethodProfile(
        method="eth_call",
        cost_tier=CostTier.STANDARD,
        token_cost=1.0,
        routing=RoutingStrategy.BATCH,
        cacheable=True,
        cache_ttl_s=2.0,
        batchable=True,
        description="Contract view call — batch with recent block",
    ),
    "eth_getCode": MethodProfile(
        method="eth_getCode",
        cost_tier=CostTier.MODERATE,
        token_cost=1.5,
        routing=RoutingStrategy.CACHEABLE,
        cacheable=True,
        cache_ttl_s=3600.0,  # Code doesn't change (unless proxy)
        description="Contract bytecode — cache aggressively",
    ),
    "eth_getStorageAt": MethodProfile(
        method="eth_getStorageAt",
        cost_tier=CostTier.STANDARD,
        token_cost=1.0,
        routing=RoutingStrategy.BATCH,
        cacheable=True,
        cache_ttl_s=2.0,
        batchable=True,
        description="Storage slot read",
    ),
    "eth_estimateGas": MethodProfile(
        method="eth_estimateGas",
        cost_tier=CostTier.MODERATE,
        token_cost=2.0,
        routing=RoutingStrategy.STANDARD,
        cacheable=False,
        description="Gas estimation — not cacheable (state-dependent)",
    ),

    # ── Logs & Events ──────────────────────────────────────────
    "eth_getLogs": MethodProfile(
        method="eth_getLogs",
        cost_tier=CostTier.EXPENSIVE,
        token_cost=3.0,
        routing=RoutingStrategy.DEDICATED,
        cacheable=True,
        cache_ttl_s=5.0,
        batchable=False,  # Already returns many results
        description="Event logs — expensive, batch by block range, use dedicated endpoints",
    ),

    # ── Block Data ─────────────────────────────────────────────
    "eth_getBlockByNumber": MethodProfile(
        method="eth_getBlockByNumber",
        cost_tier=CostTier.MODERATE,
        token_cost=2.0,
        routing=RoutingStrategy.STANDARD,
        cacheable=True,
        cache_ttl_s=30.0,  # Finalized blocks don't change
        description="Block data by number",
    ),
    "eth_getBlockByHash": MethodProfile(
        method="eth_getBlockByHash",
        cost_tier=CostTier.MODERATE,
        token_cost=2.0,
        routing=RoutingStrategy.CACHEABLE,
        cacheable=True,
        cache_ttl_s=3600.0,  # Block hash → immutable
        description="Block data by hash — immutable, cache forever",
    ),

    # ── Transaction Data ───────────────────────────────────────
    "eth_getTransactionByHash": MethodProfile(
        method="eth_getTransactionByHash",
        cost_tier=CostTier.STANDARD,
        token_cost=1.0,
        routing=RoutingStrategy.CACHEABLE,
        cacheable=True,
        cache_ttl_s=3600.0,  # Confirmed TXs don't change
        description="Transaction by hash — immutable once confirmed",
    ),
    "eth_getTransactionReceipt": MethodProfile(
        method="eth_getTransactionReceipt",
        cost_tier=CostTier.STANDARD,
        token_cost=1.0,
        routing=RoutingStrategy.CACHEABLE,
        cacheable=True,
        cache_ttl_s=3600.0,
        description="Transaction receipt — immutable once confirmed",
    ),

    # ── Transaction Submission ─────────────────────────────────
    "eth_sendRawTransaction": MethodProfile(
        method="eth_sendRawTransaction",
        cost_tier=CostTier.STANDARD,
        token_cost=1.0,
        routing=RoutingStrategy.PRIORITY,
        cacheable=False,
        batchable=False,
        description="TX submission — use dedicated high-priority channel with SWQoS",
    ),

    # ── Subscriptions ──────────────────────────────────────────
    "eth_subscribe": MethodProfile(
        method="eth_subscribe",
        cost_tier=CostTier.FREE,
        token_cost=0.0,
        routing=RoutingStrategy.WEBSOCKET,
        cacheable=False,
        batchable=False,
        description="Event subscription — persistent WS, reuse connections",
    ),
    "eth_unsubscribe": MethodProfile(
        method="eth_unsubscribe",
        cost_tier=CostTier.FREE,
        token_cost=0.0,
        routing=RoutingStrategy.WEBSOCKET,
        cacheable=False,
        batchable=False,
        description="Cancel subscription",
    ),
}

# Default profile for unknown methods
_DEFAULT_PROFILE = MethodProfile(
    method="unknown",
    cost_tier=CostTier.STANDARD,
    token_cost=1.0,
    routing=RoutingStrategy.STANDARD,
    cacheable=False,
    description="Unknown method — standard routing",
)


# ── LRU + TTL Cache ──────────────────────────────────────────────

class TTLLRUCache:
    """
    In-process LRU cache with per-entry TTL.
    Thread-safe via OrderedDict (single-threaded async is fine).
    """

    def __init__(self, max_entries: int = 20_000) -> None:
        self.max_entries = max_entries
        self._store: OrderedDict[str, Tuple[Any, float]] = OrderedDict()
        self.hits = 0
        self.misses = 0

    def get(self, key: str) -> Optional[Any]:
        entry = self._store.get(key)
        if entry is None:
            self.misses += 1
            return None
        value, expires_at = entry
        if time.time() > expires_at:
            del self._store[key]
            self.misses += 1
            return None
        # Move to end (most recently used)
        self._store.move_to_end(key)
        self.hits += 1
        return value

    def put(self, key: str, value: Any, ttl_s: float) -> None:
        if key in self._store:
            del self._store[key]
        elif len(self._store) >= self.max_entries:
            self._store.popitem(last=False)  # Evict oldest
        self._store[key] = (value, time.time() + ttl_s)

    def invalidate(self, key: str) -> None:
        self._store.pop(key, None)

    def clear(self) -> None:
        self._store.clear()

    @property
    def size(self) -> int:
        return len(self._store)

    @property
    def hit_rate(self) -> float:
        total = self.hits + self.misses
        return self.hits / total if total > 0 else 0.0


# ── Method Optimizer ──────────────────────────────────────────────

class MethodOptimizer:
    """
    Per-method RPC routing optimiser with built-in result caching.

    Usage::

        opt = MethodOptimizer()

        # Check if a result is cached
        cached = opt.get_cached(chain_id=1, method="eth_getCode", params=[addr, "latest"])
        if cached is not None:
            return cached

        # Get routing hint
        profile = opt.get_profile("eth_sendRawTransaction")
        if profile.routing == RoutingStrategy.PRIORITY:
            # Use priority channel
            ...

        # Cache the result
        opt.cache_result(chain_id=1, method="eth_getCode", params=[addr, "latest"], result=bytecode)
    """

    def __init__(self, config: Optional[MethodOptimizerConfig] = None) -> None:
        self.cfg = config or get_gateway_config().method_optimizer
        self._cache = TTLLRUCache(max_entries=self.cfg.cache_max_entries)

    # ── Profile Lookup ─────────────────────────────────────────

    def get_profile(self, method: str) -> MethodProfile:
        """Get the routing/cost profile for an RPC method."""
        return METHOD_PROFILES.get(method, _DEFAULT_PROFILE)

    def get_token_cost(self, method: str) -> float:
        """Get the token cost for rate-limiting purposes."""
        return self.get_profile(method).token_cost

    def should_batch(self, method: str) -> bool:
        """Check if this method benefits from JSON-RPC batching."""
        return self.get_profile(method).batchable

    def should_use_priority(self, method: str) -> bool:
        """Check if this method should use the priority channel."""
        return self.get_profile(method).routing == RoutingStrategy.PRIORITY

    def should_use_websocket(self, method: str) -> bool:
        """Check if this method should use WebSocket."""
        return self.get_profile(method).routing == RoutingStrategy.WEBSOCKET

    def should_use_dedicated(self, method: str) -> bool:
        """Check if this method should use a dedicated endpoint."""
        return self.get_profile(method).routing == RoutingStrategy.DEDICATED

    # ── Caching ────────────────────────────────────────────────

    def get_cached(
        self,
        chain_id: int,
        method: str,
        params: list,
    ) -> Optional[Any]:
        """
        Check if a result is cached for this call.
        Returns ``None`` on cache miss.
        """
        if not self.cfg.enable_caching:
            return None

        profile = self.get_profile(method)
        if not profile.cacheable or profile.cache_ttl_s <= 0:
            return None

        key = self._make_key(chain_id, method, params)
        return self._cache.get(key)

    def cache_result(
        self,
        chain_id: int,
        method: str,
        params: list,
        result: Any,
    ) -> None:
        """Cache a result if the method is cacheable."""
        if not self.cfg.enable_caching:
            return

        profile = self.get_profile(method)
        if not profile.cacheable or profile.cache_ttl_s <= 0:
            return

        key = self._make_key(chain_id, method, params)
        self._cache.put(key, result, profile.cache_ttl_s)

    def invalidate_chain(self, chain_id: int) -> None:
        """Clear all cached results for a chain (e.g. on reorg)."""
        # Simple approach: clear everything (chain-specific prefix would
        # require iterating all keys, which is expensive)
        self._cache.clear()

    # ── Key Generation ─────────────────────────────────────────

    @staticmethod
    def _make_key(chain_id: int, method: str, params: list) -> str:
        """Build a cache key from chain + method + params."""
        # Normalize params to a stable string representation
        param_str = str(params).replace(" ", "")
        return f"{chain_id}:{method}:{param_str}"

    # ── Stats ──────────────────────────────────────────────────

    def get_stats(self) -> Dict[str, Any]:
        return {
            "cache_size": self._cache.size,
            "cache_max": self.cfg.cache_max_entries,
            "cache_hits": self._cache.hits,
            "cache_misses": self._cache.misses,
            "hit_rate": round(self._cache.hit_rate, 4),
            "caching_enabled": self.cfg.enable_caching,
        }
