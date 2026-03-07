#!/usr/bin/env python3
"""
Enhanced RPC Gateway — Unified Facade
=======================================
Composes the existing ``profit_engine.rpc_gateway`` core with the new
MODULE_10 subcomponents into a single enterprise-grade gateway that
every scanner, detector, and executor in CryoSUPER consumes.

Architecture::

    ┌─────────────────────────────────────────────────────────────────┐
    │                   ENHANCED RPC GATEWAY                          │
    ├─────────────────────────────────────────────────────────────────┤
    │                                                                 │
    │  ┌─────────────┐  ┌─────────────┐  ┌──────────────────────┐   │
    │  │ Method       │  │ TTL/LRU     │  │ Key Rotation         │   │
    │  │ Optimizer    │  │ Cache       │  │ Manager              │   │
    │  └──────┬──────┘  └──────┬──────┘  └──────────┬───────────┘   │
    │         │                │                     │               │
    │  ┌──────▼────────────────▼─────────────────────▼───────────┐   │
    │  │           CORE GATEWAY  (profit_engine.rpc_gateway)      │   │
    │  │  Load Balancer · Rate Tracker · Batch Agg · CircuitBreak │   │
    │  └──────────────────────┬──────────────────────────────────┘   │
    │                         │                                       │
    │  ┌──────────────────────▼──────────────────────────────────┐   │
    │  │ Priority Channels │ Distributed Rate │ WebSocket Mgr    │   │
    │  └─────────────────────────────────────────────────────────┘   │
    └─────────────────────────────────────────────────────────────────┘

Lifecycle::

    gateway = EnhancedRPCGateway()
    await gateway.start()

    # Single call (method-optimised routing)
    block = await gateway.call(1, "eth_blockNumber", [])

    # Batch call
    results = await gateway.batch_call(1, [
        ("eth_call", [tx, "latest"]),
        ("eth_call", [tx2, "latest"]),
    ])

    # Priority channel for liquidation TXs
    result = await gateway.call(1, "eth_sendRawTransaction", [raw_tx],
                                priority="critical")

    # WebSocket subscription
    await gateway.subscribe_ws(1, "newHeads", on_block_callback)

    # Graceful shutdown
    await gateway.stop()
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

from .config import GatewayConfig, KeyRotationConfig, get_gateway_config
from .priority_channel_manager import PriorityChannelManager
from .websocket_manager import ResilientWebSocketManager
from .distributed_rate_limiter import DistributedRateLimiter
from .method_optimizer import MethodOptimizer, RoutingStrategy

logger = logging.getLogger(__name__)

# Late-import the core gateway to avoid circular imports at module level
_RPCGateway = None
_RequestPriority = None


def _import_core():
    global _RPCGateway, _RequestPriority
    if _RPCGateway is None:
        try:
            from profit_engine.rpc_gateway import RPCGateway, RequestPriority
            _RPCGateway = RPCGateway
            _RequestPriority = RequestPriority
        except ImportError:
            _RPCGateway = None
            _RequestPriority = None


# ── Priority string → RequestPriority enum mapping ─────────────

_PRIORITY_MAP: Dict[str, int] = {
    "critical": 1,
    "high": 2,
    "normal": 3,
    "low": 4,
    "bulk": 5,
}


def _resolve_priority(priority):
    """Convert a string or enum priority to the core RequestPriority."""
    _import_core()
    if _RequestPriority is None:
        return None
    if isinstance(priority, str):
        val = _PRIORITY_MAP.get(priority.lower(), 3)
        return _RequestPriority(val)
    return priority


# ── Key Rotation Manager ─────────────────────────────────────────

class KeyRotationManager:
    """
    Rotates API keys across providers when approaching quota limits.
    Maintains a round-robin index per provider and tracks per-key
    request counts within the current billing window.
    """

    def __init__(self, cfg: KeyRotationConfig) -> None:
        self.cfg = cfg
        self._indices: Dict[str, int] = {
            "alchemy": 0,
            "infura": 0,
            "quicknode": 0,
        }
        self._key_usage: Dict[str, int] = {}  # key_hash → request_count

    def get_current_key(self, provider: str) -> Optional[str]:
        """Get the current active API key for a provider."""
        keys = self._keys_for(provider)
        if not keys:
            return None
        idx = self._indices.get(provider, 0) % len(keys)
        return keys[idx]

    def rotate_if_needed(self, provider: str, usage_ratio: float) -> bool:
        """
        Rotate to the next key if usage exceeds threshold.
        Returns ``True`` if a rotation occurred.
        """
        keys = self._keys_for(provider)
        if len(keys) < 2:
            return False

        if usage_ratio >= self.cfg.rotation_threshold:
            current = self._indices.get(provider, 0)
            self._indices[provider] = (current + 1) % len(keys)
            logger.info(
                "[KeyRotation] Rotated %s key (usage=%.0f%%, now key %d/%d)",
                provider, usage_ratio * 100,
                self._indices[provider] + 1, len(keys),
            )
            return True
        return False

    def _keys_for(self, provider: str) -> tuple:
        if provider == "alchemy":
            return self.cfg.alchemy_keys
        if provider == "infura":
            return self.cfg.infura_keys
        if provider == "quicknode":
            return self.cfg.quicknode_keys
        return ()


# ── Enhanced RPC Gateway ─────────────────────────────────────────

class EnhancedRPCGateway:
    """
    Enterprise-grade RPC gateway composing all Module 10 subcomponents.

    Sits between every CryoSUPER scanner/executor and the blockchain,
    ensuring:
      - Zero rate-limit induced downtime
      - Optimal per-method routing & caching
      - Automatic failover across 96+ endpoints
      - Dedicated priority channels for liquidation execution
      - Persistent WebSocket subscriptions with auto-reconnect
      - Redis-backed distributed rate coordination
      - API key rotation before quota exhaustion
    """

    def __init__(self, config: Optional[GatewayConfig] = None) -> None:
        self.cfg = config or get_gateway_config()

        # Core gateway (from profit_engine.rpc_gateway)
        _import_core()
        self._core = None  # Initialised in start()

        # New subcomponents
        self.priority_channels = PriorityChannelManager(self.cfg.priority_channels)
        self.ws_manager = ResilientWebSocketManager(self.cfg.websocket)
        self.distributed_rate = DistributedRateLimiter(self.cfg.distributed_rate)
        self.method_optimizer = MethodOptimizer(self.cfg.method_optimizer)
        self.key_rotation = KeyRotationManager(self.cfg.key_rotation)

        # Aggregate stats
        self._total_calls = 0
        self._total_cache_hits = 0
        self._total_priority_calls = 0
        self._total_ws_subs = 0
        self._start_time: float = 0.0
        self._running = False

        # Background tasks
        self._health_task: Optional[asyncio.Task] = None
        self._stats_task: Optional[asyncio.Task] = None

    # ── Lifecycle ──────────────────────────────────────────────

    async def start(self) -> None:
        """Initialise all subcomponents and start background tasks."""
        if self._running:
            return

        logger.info("=" * 60)
        logger.info("  MODULE 10: ENHANCED RPC GATEWAY — STARTING")
        logger.info("=" * 60)

        self._start_time = time.time()

        # 1. Core gateway
        if _RPCGateway is not None:
            core_config = {
                "max_batch_size": self.cfg.core.max_batch_size,
                "batch_flush_ms": self.cfg.core.batch_flush_ms,
            }
            self._core = _RPCGateway(core_config)
            await self._core.start()
            logger.info("  [+] Core RPC Gateway started (load balancer + batch + circuit breaker)")
        else:
            logger.warning("  [-] profit_engine.rpc_gateway unavailable — running without core")

        # 2. Priority channels
        try:
            await self.priority_channels.start()
            logger.info("  [+] Priority Channel Manager started")
        except Exception as exc:
            logger.warning("  [-] Priority channels unavailable: %s", exc)

        # 3. Distributed rate limiter
        try:
            await self.distributed_rate.start()
            logger.info("  [+] Distributed Rate Limiter started")
        except Exception as exc:
            logger.warning("  [-] Distributed rate limiter unavailable: %s", exc)

        # 4. Method optimizer (synchronous init)
        logger.info(
            "  [+] Method Optimizer ready (%d method profiles, cache=%s)",
            len(self.method_optimizer.get_stats()),
            "ON" if self.cfg.method_optimizer.enable_caching else "OFF",
        )

        # 5. Key rotation
        alchemy_count = len(self.cfg.key_rotation.alchemy_keys)
        infura_count = len(self.cfg.key_rotation.infura_keys)
        logger.info(
            "  [+] Key Rotation Manager ready (alchemy=%d keys, infura=%d keys)",
            alchemy_count, infura_count,
        )

        # 6. Background tasks
        self._health_task = asyncio.ensure_future(self._health_loop())
        self._stats_task = asyncio.ensure_future(self._stats_loop())

        self._running = True

        total_endpoints = 0
        if self._core:
            for eps in self._core.pool_manager.endpoints.values():
                total_endpoints += len(eps)

        logger.info("=" * 60)
        logger.info("  ENHANCED RPC GATEWAY ONLINE")
        logger.info("    Endpoints: %d", total_endpoints)
        logger.info("    Priority channels: %s", self.priority_channels.get_stats().get("total_channels", 0))
        logger.info("    Redis distributed: %s", self.distributed_rate._using_redis)
        logger.info("=" * 60)

    async def stop(self) -> None:
        """Gracefully shut down all subcomponents."""
        logger.info("[EnhancedGateway] Shutting down ...")
        self._running = False

        if self._health_task:
            self._health_task.cancel()
        if self._stats_task:
            self._stats_task.cancel()

        # Stop in reverse order
        try:
            await self.ws_manager.stop()
        except Exception:
            pass
        try:
            await self.distributed_rate.stop()
        except Exception:
            pass
        try:
            await self.priority_channels.stop()
        except Exception:
            pass
        if self._core:
            try:
                await self._core.stop()
            except Exception:
                pass

        logger.info("[EnhancedGateway] Shutdown complete")

    # ══════════════════════════════════════════════════════════
    #  PUBLIC API
    # ══════════════════════════════════════════════════════════

    async def call(
        self,
        chain_id: int,
        method: str,
        params: list,
        priority: str = "normal",
        timeout: float = 10.0,
    ) -> Any:
        """
        Execute a single RPC call with full gateway orchestration.

        Flow:
          1. Check method-level cache
          2. Check distributed rate limits
          3. Route via priority channel (if applicable)
          4. Fallback to core gateway with load balancing
          5. Cache result (if cacheable)
        """
        self._total_calls += 1

        # 1. Check cache
        cached = self.method_optimizer.get_cached(chain_id, method, params)
        if cached is not None:
            self._total_cache_hits += 1
            return cached

        # 2. Route based on method profile
        profile = self.method_optimizer.get_profile(method)

        # 3. Priority routing for liquidation / TX submission
        if profile.routing == RoutingStrategy.PRIORITY or priority in ("critical", "high"):
            result = await self._priority_call(chain_id, method, params, priority, timeout)
            if result is not None:
                self.method_optimizer.cache_result(chain_id, method, params, result)
                return result
            # Fallthrough to core gateway if priority channel unavailable

        # 4. Distributed rate check
        if self._core:
            endpoints = self._core.pool_manager.get_endpoints(chain_id)
            if endpoints:
                ep = self._core.load_balancer.select_endpoint(
                    endpoints, _resolve_priority(priority)
                )
                if ep:
                    can = await self.distributed_rate.can_request(ep.id, method)
                    if not can:
                        # All distributed-rate-limited; brief wait
                        await asyncio.sleep(0.1)

        # 5. Core gateway call
        if self._core:
            result = await self._core.call(
                chain_id, method, params,
                priority=_resolve_priority(priority),
                timeout=timeout,
            )
            self.method_optimizer.cache_result(chain_id, method, params, result)

            # Track in distributed limiter
            if self._core.pool_manager.get_endpoints(chain_id):
                ep = self._core.load_balancer.select_endpoint(
                    self._core.pool_manager.get_endpoints(chain_id),
                    _resolve_priority(priority),
                )
                if ep:
                    await self.distributed_rate.track_request(ep.id, method)

            return result

        raise Exception(f"No RPC backend available for chain {chain_id}")

    async def batch_call(
        self,
        chain_id: int,
        calls: List[Tuple[str, list]],
        priority: str = "normal",
    ) -> List[Any]:
        """
        Execute multiple RPC calls as a JSON-RPC batch.

        Automatically:
          - Serves cached results inline
          - Batches only the uncached calls
          - Caches new results
        """
        if not calls:
            return []

        self._total_calls += len(calls)

        # Split into cached vs uncached
        results: List[Any] = [None] * len(calls)
        uncached_indices: List[int] = []
        uncached_calls: List[Tuple[str, list]] = []

        for i, (method, params) in enumerate(calls):
            cached = self.method_optimizer.get_cached(chain_id, method, params)
            if cached is not None:
                results[i] = cached
                self._total_cache_hits += 1
            else:
                uncached_indices.append(i)
                uncached_calls.append((method, params))

        # Execute uncached calls via core batch
        if uncached_calls and self._core:
            batch_results = await self._core.batch_call(
                chain_id, uncached_calls,
                priority=_resolve_priority(priority),
            )
            for idx, br in zip(uncached_indices, batch_results):
                results[idx] = br
                # Cache the result
                method, params = calls[idx]
                self.method_optimizer.cache_result(chain_id, method, params, br)

        return results

    def get_w3(self, chain_id: int):
        """Get a managed Web3 instance (legacy compatibility)."""
        if self._core:
            return self._core.get_w3(chain_id)
        return None

    def get_all_w3(self) -> Dict[int, Any]:
        """Get Web3 instances for all chains (legacy compatibility)."""
        if self._core:
            return self._core.get_all_w3()
        return {}

    # ── WebSocket API ──────────────────────────────────────────

    async def subscribe_ws(
        self,
        chain_id: int,
        event: str,
        callback: Callable,
    ) -> bool:
        """
        Subscribe to a real-time blockchain event via WebSocket.

        Starts the WS manager lazily if not already running.
        """
        if not self.ws_manager._running:
            await self.ws_manager.start(chain_ids=[chain_id])

        self._total_ws_subs += 1
        return await self.ws_manager.subscribe(chain_id, event, callback)

    async def unsubscribe_ws(self, chain_id: int, event: str) -> None:
        """Unsubscribe from a WebSocket event."""
        await self.ws_manager.unsubscribe(chain_id, event)

    # ══════════════════════════════════════════════════════════
    #  INTERNAL
    # ══════════════════════════════════════════════════════════

    async def _priority_call(
        self,
        chain_id: int,
        method: str,
        params: list,
        priority: str,
        timeout: float,
    ) -> Optional[Any]:
        """Route through dedicated priority channel if available."""
        ep = self.priority_channels.get_priority_endpoint(
            chain_id, request_type=priority,
        )
        if ep is None:
            return None

        self._total_priority_calls += 1

        # Use aiohttp directly against the dedicated endpoint
        try:
            import aiohttp

            payload = {
                "jsonrpc": "2.0", "method": method,
                "params": params, "id": 1,
            }
            start = time.time()
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    ep.url, json=payload,
                    timeout=aiohttp.ClientTimeout(total=timeout),
                    headers={"Content-Type": "application/json"},
                ) as resp:
                    latency_ms = (time.time() - start) * 1000
                    if resp.status == 200:
                        data = await resp.json()
                        self.priority_channels.record_request(ep, latency_ms, True)
                        if "error" in data:
                            raise Exception(data["error"].get("message", str(data["error"])))
                        return data.get("result")
                    elif resp.status == 429:
                        self.priority_channels.record_request(ep, latency_ms, False)
                        await self.distributed_rate.handle_rate_limit(
                            f"priority_{chain_id}", retry_after=5.0,
                        )
                        return None  # Fallthrough to core
                    else:
                        self.priority_channels.record_request(ep, latency_ms, False)
                        return None
        except ImportError:
            return None
        except Exception as exc:
            logger.debug("[EnhancedGateway] Priority call failed: %s", exc)
            return None

    # ── Background Tasks ───────────────────────────────────────

    async def _health_loop(self) -> None:
        """Periodic health checks on priority channels + key rotation."""
        try:
            while self._running:
                await asyncio.sleep(self.cfg.core.health_check_interval_s)

                # Health-check priority channels
                try:
                    health = await self.priority_channels.health_check_all()
                    for chain_id, count in health.items():
                        if count == 0:
                            logger.warning(
                                "[EnhancedGateway] All priority channels DOWN for chain %d",
                                chain_id,
                            )
                except Exception:
                    pass

                # Check key rotation needs
                if self._core:
                    for chain_id, eps in self._core.pool_manager.endpoints.items():
                        for ep in eps:
                            if ep.total_requests > 0:
                                usage = ep.current_load / max(ep.rate_limit_rps, 1)
                                provider = ep.provider.value
                                self.key_rotation.rotate_if_needed(provider, usage)

        except asyncio.CancelledError:
            pass

    async def _stats_loop(self) -> None:
        """Periodic stats logging."""
        try:
            while self._running:
                await asyncio.sleep(self.cfg.core.metrics_log_interval_s)
                self._log_stats()
        except asyncio.CancelledError:
            pass

    def _log_stats(self) -> None:
        """Print comprehensive gateway statistics."""
        elapsed = time.time() - self._start_time
        rps = self._total_calls / max(elapsed, 1)
        cache_stats = self.method_optimizer.get_stats()
        dist_stats = self.distributed_rate.get_stats()
        prio_stats = self.priority_channels.get_stats()

        logger.info("─" * 60)
        logger.info("  MODULE 10 — ENHANCED RPC GATEWAY STATS")
        logger.info("─" * 60)
        logger.info("  Total calls:    %d (%.1f rps)", self._total_calls, rps)
        logger.info("  Cache hits:     %d (rate=%.1f%%)",
                     self._total_cache_hits,
                     cache_stats.get("hit_rate", 0) * 100)
        logger.info("  Priority calls: %d", self._total_priority_calls)
        logger.info("  WS subs:        %d", self._total_ws_subs)
        logger.info("  Dist. rate:     redis=%s, tracked=%d, cooled=%d",
                     dist_stats.get("using_redis"),
                     dist_stats.get("tracked_endpoints", 0),
                     dist_stats.get("cooled_down", 0))
        logger.info("  Prio channels:  %d total, chains=%d",
                     prio_stats.get("total_channels", 0),
                     len(prio_stats.get("chains", {})))

        if self._core:
            core = self._core.get_stats()
            logger.info("  Core 429s:      %d", core.get("total_429s", 0))
            logger.info("  Core failovers: %d", core.get("total_failovers", 0))
            logger.info("  Core batched:   %d", core.get("total_batched", 0))

        logger.info("─" * 60)

    # ── Aggregate Stats ────────────────────────────────────────

    def get_stats(self) -> Dict[str, Any]:
        """Return comprehensive gateway statistics for API/monitoring."""
        elapsed = time.time() - self._start_time if self._start_time else 0
        stats: Dict[str, Any] = {
            "running": self._running,
            "uptime_s": round(elapsed, 1),
            "total_calls": self._total_calls,
            "rps": round(self._total_calls / max(elapsed, 1), 2),
            "total_cache_hits": self._total_cache_hits,
            "total_priority_calls": self._total_priority_calls,
            "total_ws_subscriptions": self._total_ws_subs,
            "method_optimizer": self.method_optimizer.get_stats(),
            "distributed_rate": self.distributed_rate.get_stats(),
            "priority_channels": self.priority_channels.get_stats(),
            "websocket_manager": self.ws_manager.get_stats(),
        }
        if self._core:
            stats["core_gateway"] = self._core.get_stats()
        return stats
