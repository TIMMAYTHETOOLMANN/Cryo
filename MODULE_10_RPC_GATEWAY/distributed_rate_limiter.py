#!/usr/bin/env python3
"""
Distributed Rate Limiter
=========================
Redis-backed cross-instance rate coordination layer.

When multiple bot instances share the same RPC endpoints, local
token-bucket tracking is insufficient — one instance can hammer an
endpoint while others sit idle.  This module bridges that gap:

  - Syncs per-endpoint counters to Redis on a configurable cadence
  - Checks global remaining capacity before allowing requests
  - Publishes cooldown events (429 responses) so all instances back off
  - Falls back to purely local tracking when Redis is unavailable
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Any, Dict, Optional

from .config import DistributedRateLimiterConfig, get_gateway_config

logger = logging.getLogger(__name__)

# ── Redis mock for graceful degradation ──────────────────────────

class _FallbackRedis:
    """In-memory stub when Redis is unavailable."""

    def __init__(self) -> None:
        self._store: Dict[str, Any] = {}
        self._expiry: Dict[str, float] = {}

    async def get(self, key: str) -> Optional[str]:
        self._gc()
        return self._store.get(key)

    async def set(self, key: str, value: str, ex: Optional[int] = None) -> None:
        self._store[key] = value
        if ex:
            self._expiry[key] = time.time() + ex

    async def incr(self, key: str) -> int:
        self._gc()
        val = int(self._store.get(key, 0)) + 1
        self._store[key] = str(val)
        return val

    async def expire(self, key: str, seconds: int) -> None:
        self._expiry[key] = time.time() + seconds

    async def setex(self, key: str, seconds: int, value: str) -> None:
        self._store[key] = value
        self._expiry[key] = time.time() + seconds

    async def delete(self, key: str) -> None:
        self._store.pop(key, None)
        self._expiry.pop(key, None)

    async def ping(self) -> bool:
        return True

    def _gc(self) -> None:
        now = time.time()
        expired = [k for k, t in self._expiry.items() if now > t]
        for k in expired:
            self._store.pop(k, None)
            self._expiry.pop(k, None)


# ── Per-endpoint state ───────────────────────────────────────────

@dataclass
class EndpointRateState:
    """Local tracking state synced with Redis."""
    endpoint_id: str
    local_count: int = 0
    global_remaining: int = -1  # -1 = unknown / not synced
    last_sync: float = 0.0
    is_cooled_down: bool = False
    cooldown_until: float = 0.0


class DistributedRateLimiter:
    """
    Redis-backed distributed rate limiter.

    Falls back gracefully to a local-only mock when Redis is
    unavailable or ``enable_redis`` is ``False``.

    Usage::

        drl = DistributedRateLimiter()
        await drl.start()

        if await drl.can_request("alchemy_1_1234", "eth_call"):
            await drl.track_request("alchemy_1_1234", "eth_call")
            ...
        else:
            # Rate limited — try another endpoint
    """

    def __init__(self, config: Optional[DistributedRateLimiterConfig] = None) -> None:
        self.cfg = config or get_gateway_config().distributed_rate
        self._redis: Any = None  # aioredis / _FallbackRedis
        self._states: Dict[str, EndpointRateState] = {}
        self._running = False
        self._sync_task: Optional[asyncio.Task] = None
        self._using_redis = False

    # ── Lifecycle ──────────────────────────────────────────────

    async def start(self) -> None:
        """Connect to Redis (or fallback to mock)."""
        if self._running:
            return

        if self.cfg.enable_redis:
            try:
                import aioredis
                self._redis = await aioredis.from_url(
                    self.cfg.redis_url, decode_responses=True,
                )
                await self._redis.ping()
                self._using_redis = True
                logger.info("[DistRate] Connected to Redis at %s", self.cfg.redis_url)
            except Exception as exc:
                logger.warning("[DistRate] Redis unavailable (%s) — local-only mode", exc)
                self._redis = _FallbackRedis()
                self._using_redis = False
        else:
            self._redis = _FallbackRedis()
            self._using_redis = False

        self._running = True
        self._sync_task = asyncio.ensure_future(self._sync_loop())
        logger.info("[DistRate] Started (redis=%s)", self._using_redis)

    async def stop(self) -> None:
        """Shutdown."""
        self._running = False
        if self._sync_task:
            self._sync_task.cancel()
        if self._using_redis and hasattr(self._redis, "close"):
            try:
                await self._redis.close()
            except Exception:
                pass
        self._states.clear()

    # ── Public API ─────────────────────────────────────────────

    async def can_request(self, endpoint_id: str, method: str = "eth_call") -> bool:
        """
        Check if a request to ``endpoint_id`` should be allowed.

        Considers both local token-bucket state and global Redis counters.
        """
        state = self._get_state(endpoint_id)

        # Check cooldown (from 429)
        if state.is_cooled_down:
            if time.time() < state.cooldown_until:
                return False
            state.is_cooled_down = False

        # Check global remaining (if synced recently)
        if state.global_remaining == 0 and (time.time() - state.last_sync) < self.cfg.sync_interval_s:
            return False

        return True

    async def track_request(self, endpoint_id: str, method: str = "eth_call") -> None:
        """Record that a request was sent."""
        state = self._get_state(endpoint_id)
        state.local_count += 1

        # Increment global counter
        key = f"{self.cfg.key_prefix}:{endpoint_id}"
        try:
            await self._redis.incr(key)
            await self._redis.expire(key, self.cfg.window_seconds)
        except Exception:
            pass

        # Track per-method usage for analytics
        method_key = f"{self.cfg.key_prefix}:method:{endpoint_id}:{method}"
        try:
            await self._redis.incr(method_key)
            await self._redis.expire(method_key, self.cfg.window_seconds)
        except Exception:
            pass

    async def handle_rate_limit(
        self,
        endpoint_id: str,
        retry_after: float = 0.0,
    ) -> None:
        """
        Handle a 429 response: broadcast cooldown so all instances back off.
        """
        state = self._get_state(endpoint_id)
        cooldown = max(retry_after, self.cfg.cooldown_default_s)
        state.is_cooled_down = True
        state.cooldown_until = time.time() + cooldown

        # Publish cooldown to Redis so other instances see it
        cooldown_key = f"{self.cfg.key_prefix}:cooldown:{endpoint_id}"
        try:
            await self._redis.setex(cooldown_key, int(cooldown), "1")
        except Exception:
            pass

        logger.warning(
            "[DistRate] 429 on %s — cooldown %.0fs (redis=%s)",
            endpoint_id, cooldown, self._using_redis,
        )

    # ── Sync Loop ──────────────────────────────────────────────

    async def _sync_loop(self) -> None:
        """Periodically sync local state with Redis counters."""
        try:
            while self._running:
                await asyncio.sleep(self.cfg.sync_interval_s)
                for endpoint_id, state in list(self._states.items()):
                    # Read global counter
                    key = f"{self.cfg.key_prefix}:{endpoint_id}"
                    try:
                        raw = await self._redis.get(key)
                        if raw is not None:
                            state.global_remaining = max(
                                0, 100 - int(raw)
                            )  # Assume 100 req/window unless configured
                        state.last_sync = time.time()
                    except Exception:
                        pass

                    # Check for cooldown published by other instances
                    cooldown_key = f"{self.cfg.key_prefix}:cooldown:{endpoint_id}"
                    try:
                        cooldown_val = await self._redis.get(cooldown_key)
                        if cooldown_val:
                            state.is_cooled_down = True
                            state.cooldown_until = time.time() + self.cfg.cooldown_default_s
                    except Exception:
                        pass
        except asyncio.CancelledError:
            pass

    # ── Helpers ────────────────────────────────────────────────

    def _get_state(self, endpoint_id: str) -> EndpointRateState:
        if endpoint_id not in self._states:
            self._states[endpoint_id] = EndpointRateState(endpoint_id=endpoint_id)
        return self._states[endpoint_id]

    # ── Stats ─────────────────────────────────────────────────

    def get_stats(self) -> Dict[str, Any]:
        cooled = sum(1 for s in self._states.values() if s.is_cooled_down)
        return {
            "using_redis": self._using_redis,
            "tracked_endpoints": len(self._states),
            "cooled_down": cooled,
            "total_local_requests": sum(s.local_count for s in self._states.values()),
        }
