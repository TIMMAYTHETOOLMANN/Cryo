#!/usr/bin/env python3
"""
Module 9 — Performance Optimizer: Throughput & Latency Tuning
================================================================
System-wide performance layer that wraps every network call, cache
hit, and queue operation with monitoring, pooling, and auto-tuning.

Capabilities:
  9.1  RPC Connection Pooling — per-chain connection pools with health checks
  9.2  Async Request Batching — batch contract reads / price fetches
  9.3  Multi-Tier Caching — LRU + TTL in-process cache + Redis
  9.4  Latency Monitoring — percentile tracking per component
  9.5  Memory Pressure Monitor — GC trigger + buffer trimming
  9.6  Auto-Tuning — adjust intervals, pool sizes based on metrics
"""
from __future__ import annotations

import asyncio
import gc
import logging
import os
import statistics
import sys
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Any, Callable, Deque, Dict, List, Optional, Tuple

try:
    import resource
except ImportError:
    resource = None  # type: ignore[assignment]  # Windows

from .config import PerformanceConfig, get_config

logger = logging.getLogger(__name__)


# ── Data Models ───────────────────────────────────────────────────

@dataclass
class LatencyBucket:
    """Rolling latency statistics for one component."""
    name: str
    samples: Deque[float] = field(default_factory=lambda: deque(maxlen=2000))

    def record(self, ms: float) -> None:
        self.samples.append(ms)

    @property
    def p50(self) -> float:
        return self._pct(50)

    @property
    def p95(self) -> float:
        return self._pct(95)

    @property
    def p99(self) -> float:
        return self._pct(99)

    @property
    def mean(self) -> float:
        return statistics.mean(self.samples) if self.samples else 0

    @property
    def count(self) -> int:
        return len(self.samples)

    def _pct(self, pct: int) -> float:
        if not self.samples:
            return 0
        s = sorted(self.samples)
        idx = int(len(s) * pct / 100)
        return s[min(idx, len(s) - 1)]

    def summary(self) -> Dict[str, float]:
        return {
            "count": self.count,
            "p50_ms": round(self.p50, 2),
            "p95_ms": round(self.p95, 2),
            "p99_ms": round(self.p99, 2),
            "mean_ms": round(self.mean, 2),
        }


@dataclass
class CacheEntry:
    """Single cache entry with TTL."""
    value: Any
    expires_at: float


@dataclass
class PoolHealth:
    """Health status for one connection pool."""
    chain_id: int
    rpc_url: str
    total_connections: int
    available: int
    in_use: int
    avg_response_ms: float
    error_rate: float
    is_healthy: bool


@dataclass
class TuningAction:
    """An auto-tuning adjustment."""
    timestamp: float
    component: str
    parameter: str
    old_value: Any
    new_value: Any
    reason: str


@dataclass
class OptimizationMetrics:
    """Snapshot of current system performance."""
    timestamp: float = 0.0
    total_requests: int = 0
    cache_hits: int = 0
    cache_misses: int = 0
    cache_hit_rate: float = 0.0
    avg_latency_ms: float = 0.0
    p95_latency_ms: float = 0.0
    memory_mb: float = 0.0
    gc_collections: int = 0
    active_pools: int = 0
    total_connections: int = 0
    batch_operations: int = 0
    tuning_actions: int = 0


# ── RPC Connection Pool ──────────────────────────────────────────

class RPCConnectionPool:
    """
    Per-chain connection pool with health checking and failover.
    Wraps Web3 HTTP/WS providers in a bounded async semaphore pool.
    """

    def __init__(
        self,
        chain_id: int,
        rpc_urls: List[str],
        pool_size: int = 10,
        health_interval_s: float = 30.0,
    ):
        self.chain_id = chain_id
        self.rpc_urls = rpc_urls
        self.pool_size = pool_size
        self._semaphore = asyncio.Semaphore(pool_size)
        self._health_interval = health_interval_s

        # Round-robin index
        self._rr_idx = 0

        # Per-URL health
        self._url_health: Dict[str, Dict] = {
            url: {"errors": 0, "latency_ms": deque(maxlen=100), "healthy": True}
            for url in rpc_urls
        }

        # Stats
        self._total_requests = 0
        self._total_errors = 0

    def _pick_url(self) -> str:
        """Round-robin with health filtering."""
        healthy = [u for u, h in self._url_health.items() if h["healthy"]]
        if not healthy:
            healthy = self.rpc_urls  # fallback to all
        url = healthy[self._rr_idx % len(healthy)]
        self._rr_idx += 1
        return url

    async def execute(self, method: str, params: Any = None) -> Any:
        """Execute an RPC call through the pool."""
        async with self._semaphore:
            url = self._pick_url()
            self._total_requests += 1
            t0 = time.time()
            try:
                # In production: aiohttp.post(url, json={"method": method, "params": params})
                result = {"jsonrpc": "2.0", "result": None}
                ms = (time.time() - t0) * 1000
                self._url_health[url]["latency_ms"].append(ms)
                return result.get("result")
            except Exception as exc:
                self._total_errors += 1
                h = self._url_health[url]
                h["errors"] += 1
                if h["errors"] > 10:
                    h["healthy"] = False
                    logger.warning("[Pool] Chain %d URL %s marked unhealthy", self.chain_id, url[:40])
                raise

    async def health_check(self) -> PoolHealth:
        """Run health check against all URLs."""
        healthy_count = sum(1 for h in self._url_health.values() if h["healthy"])
        total_lat = []
        for h in self._url_health.values():
            if h["latency_ms"]:
                total_lat.extend(h["latency_ms"])

        return PoolHealth(
            chain_id=self.chain_id,
            rpc_url=self.rpc_urls[0] if self.rpc_urls else "",
            total_connections=self.pool_size,
            available=self._semaphore._value,
            in_use=self.pool_size - self._semaphore._value,
            avg_response_ms=statistics.mean(total_lat) if total_lat else 0,
            error_rate=self._total_errors / max(self._total_requests, 1),
            is_healthy=healthy_count > 0,
        )


# ── Async Batch Aggregator ───────────────────────────────────────

class BatchAggregator:
    """
    Collect individual requests and fire them as batches.
    Reduces RPC calls dramatically for multi-position scans.
    """

    def __init__(
        self,
        pool: RPCConnectionPool,
        batch_size: int = 100,
        flush_interval_ms: int = 50,
    ):
        self.pool = pool
        self.batch_size = batch_size
        self.flush_interval = flush_interval_ms / 1000.0

        self._buffer: List[Tuple[str, Any, asyncio.Future]] = []
        self._lock = asyncio.Lock()
        self._flush_task: Optional[asyncio.Task] = None
        self._batches_sent = 0

    async def start(self) -> None:
        self._flush_task = asyncio.create_task(self._auto_flush())

    async def stop(self) -> None:
        if self._flush_task:
            self._flush_task.cancel()
        await self._flush_now()

    async def call(self, method: str, params: Any = None) -> Any:
        """Enqueue a call; returns when the batch response arrives."""
        fut: asyncio.Future = asyncio.get_event_loop().create_future()
        async with self._lock:
            self._buffer.append((method, params, fut))
            if len(self._buffer) >= self.batch_size:
                await self._flush_now()
        return await fut

    async def _auto_flush(self) -> None:
        try:
            while True:
                await asyncio.sleep(self.flush_interval)
                async with self._lock:
                    if self._buffer:
                        await self._flush_now()
        except asyncio.CancelledError:
            pass

    async def _flush_now(self) -> None:
        if not self._buffer:
            return

        batch = self._buffer[:]
        self._buffer.clear()
        self._batches_sent += 1

        # In production: send as JSON-RPC batch
        # For now, resolve each individually
        for method, params, fut in batch:
            try:
                result = await self.pool.execute(method, params)
                if not fut.done():
                    fut.set_result(result)
            except Exception as exc:
                if not fut.done():
                    fut.set_exception(exc)


# ── Multi-Tier Cache ─────────────────────────────────────────────

class TieredCache:
    """
    L1: In-process dict with TTL + LRU eviction.
    L2: (optional) Redis — set via set_redis_client().
    """

    def __init__(self, max_size: int = 10_000, default_ttl_s: float = 300.0):
        self.max_size = max_size
        self.default_ttl = default_ttl_s
        self._l1: Dict[str, CacheEntry] = {}
        self._access_order: Deque[str] = deque(maxlen=max_size * 2)
        self._redis = None

        # Stats
        self.hits = 0
        self.misses = 0

    def set_redis_client(self, redis_client: Any) -> None:
        self._redis = redis_client

    async def get(self, key: str) -> Optional[Any]:
        """L1 → L2 lookup."""
        # L1
        entry = self._l1.get(key)
        if entry and entry.expires_at > time.time():
            self.hits += 1
            return entry.value
        if entry:
            del self._l1[key]

        # L2 (Redis)
        if self._redis:
            try:
                raw = await self._redis.get(f"cache:{key}")
                if raw is not None:
                    import json
                    val = json.loads(raw)
                    self.set_l1(key, val)
                    self.hits += 1
                    return val
            except Exception:
                pass

        self.misses += 1
        return None

    async def set(self, key: str, value: Any, ttl_s: float = None) -> None:
        """Store in L1 + L2."""
        ttl = ttl_s or self.default_ttl
        self.set_l1(key, value, ttl)

        if self._redis:
            try:
                import json
                await self._redis.setex(f"cache:{key}", int(ttl), json.dumps(value, default=str))
            except Exception:
                pass

    def set_l1(self, key: str, value: Any, ttl_s: float = None) -> None:
        ttl = ttl_s or self.default_ttl
        self._l1[key] = CacheEntry(value=value, expires_at=time.time() + ttl)
        self._access_order.append(key)
        self._evict_if_needed()

    def _evict_if_needed(self) -> None:
        now = time.time()
        while len(self._l1) > self.max_size:
            if not self._access_order:
                break
            oldest = self._access_order.popleft()
            entry = self._l1.get(oldest)
            if entry and entry.expires_at <= now:
                del self._l1[oldest]
            elif entry:
                del self._l1[oldest]  # Over max, evict LRU

    def invalidate(self, key: str) -> None:
        self._l1.pop(key, None)

    def clear(self) -> None:
        self._l1.clear()
        self._access_order.clear()

    @property
    def hit_rate(self) -> float:
        total = self.hits + self.misses
        return self.hits / total if total else 0.0

    def get_stats(self) -> Dict[str, Any]:
        return {
            "l1_size": len(self._l1),
            "hits": self.hits,
            "misses": self.misses,
            "hit_rate": round(self.hit_rate, 4),
            "has_l2": self._redis is not None,
        }


# ── Memory Pressure Monitor ─────────────────────────────────────

class MemoryMonitor:
    """Track process memory and trigger GC when needed."""

    def __init__(self, max_mb: float = 2048.0, gc_threshold_pct: float = 0.80):
        self.max_mb = max_mb
        self.gc_threshold = gc_threshold_pct
        self._gc_runs = 0
        self._peak_mb = 0.0

    def current_mb(self) -> float:
        """Get current RSS in MB (cross-platform)."""
        try:
            if sys.platform == "win32":
                import ctypes
                import ctypes.wintypes
                kernel32 = ctypes.windll.kernel32
                process = kernel32.GetCurrentProcess()

                class PROCESS_MEMORY_COUNTERS(ctypes.Structure):
                    _fields_ = [
                        ("cb", ctypes.wintypes.DWORD),
                        ("PageFaultCount", ctypes.wintypes.DWORD),
                        ("PeakWorkingSetSize", ctypes.c_size_t),
                        ("WorkingSetSize", ctypes.c_size_t),
                        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                        ("QuotaPagedPoolUsage", ctypes.c_size_t),
                        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                        ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                        ("PagefileUsage", ctypes.c_size_t),
                        ("PeakPagefileUsage", ctypes.c_size_t),
                    ]

                pmc = PROCESS_MEMORY_COUNTERS()
                pmc.cb = ctypes.sizeof(pmc)
                ctypes.windll.psapi.GetProcessMemoryInfo(
                    process, ctypes.byref(pmc), pmc.cb,
                )
                return pmc.WorkingSetSize / (1024 * 1024)
            else:
                if resource is not None:
                    usage = resource.getrusage(resource.RUSAGE_SELF)
                    return usage.ru_maxrss / 1024  # Linux: KB → MB
                return 0.0
        except Exception:
            return 0.0

    def check_pressure(self) -> bool:
        """Return True if GC was triggered."""
        mb = self.current_mb()
        self._peak_mb = max(self._peak_mb, mb)

        if mb > self.max_mb * self.gc_threshold:
            gc.collect()
            self._gc_runs += 1
            after = self.current_mb()
            logger.info(
                "[Memory] GC triggered: %.1f MB → %.1f MB (peak %.1f MB)",
                mb, after, self._peak_mb,
            )
            return True
        return False

    def get_stats(self) -> Dict[str, Any]:
        return {
            "current_mb": round(self.current_mb(), 1),
            "peak_mb": round(self._peak_mb, 1),
            "max_mb": self.max_mb,
            "gc_runs": self._gc_runs,
        }


# ── Auto-Tuner ───────────────────────────────────────────────────

class AutoTuner:
    """
    Adjusts system parameters based on runtime metrics.
    Runs periodically and applies tuning actions.
    """

    def __init__(self) -> None:
        self._actions: List[TuningAction] = []
        self._tunable_params: Dict[str, Callable[..., Any]] = {}

    def register_tunable(self, name: str, setter: Callable[..., Any]) -> None:
        """Register a parameter that can be auto-tuned."""
        self._tunable_params[name] = setter

    def evaluate(self, metrics: OptimizationMetrics) -> List[TuningAction]:
        """Evaluate metrics and produce tuning actions."""
        actions = []

        # If p95 latency > 500ms, reduce batch timeout
        if metrics.p95_latency_ms > 500:
            actions.append(TuningAction(
                timestamp=time.time(),
                component="batch_aggregator",
                parameter="flush_interval_ms",
                old_value=50,
                new_value=25,
                reason=f"p95 latency {metrics.p95_latency_ms:.0f}ms > 500ms",
            ))

        # If cache hit rate < 50%, increase TTL
        if metrics.cache_hit_rate < 0.5 and metrics.total_requests > 100:
            actions.append(TuningAction(
                timestamp=time.time(),
                component="tiered_cache",
                parameter="default_ttl_s",
                old_value=300,
                new_value=600,
                reason=f"Cache hit rate {metrics.cache_hit_rate:.2%} < 50%",
            ))

        # If memory > 80% of max, trim caches
        if metrics.memory_mb > 0 and metrics.memory_mb > 1600:
            actions.append(TuningAction(
                timestamp=time.time(),
                component="tiered_cache",
                parameter="max_size",
                old_value=10000,
                new_value=5000,
                reason=f"Memory {metrics.memory_mb:.0f}MB > 1600MB",
            ))

        for action in actions:
            self._actions.append(action)
            setter = self._tunable_params.get(
                f"{action.component}.{action.parameter}",
            )
            if setter:
                try:
                    setter(action.new_value)
                except Exception as exc:
                    logger.warning("[AutoTuner] Failed to apply %s: %s", action.parameter, exc)

        return actions

    def get_history(self) -> List[Dict[str, Any]]:
        return [
            {
                "time": a.timestamp,
                "component": a.component,
                "parameter": a.parameter,
                "old": a.old_value,
                "new": a.new_value,
                "reason": a.reason,
            }
            for a in self._actions[-50:]
        ]


# ── Performance Optimizer (Main Class) ───────────────────────────

class PerformanceOptimizer:
    """
    System-wide performance layer.  Provides connection pools,
    caching, batching, and auto-tuning for all modules.
    """

    def __init__(self, config: Optional[PerformanceConfig] = None):
        self.config = config or get_config().performance
        self._running = False

        # RPC pools per chain
        self._pools: Dict[int, RPCConnectionPool] = {}

        # Batch aggregators per chain
        self._batchers: Dict[int, BatchAggregator] = {}

        # Shared caches
        self.cache = TieredCache(
            max_size=self.config.cache_max_size,
            default_ttl_s=self.config.cache_ttl_s,
        )

        # Latency tracking per component
        self._latency_buckets: Dict[str, LatencyBucket] = defaultdict(
            lambda: LatencyBucket(name="unknown"),
        )

        # Memory monitor
        self._memory = MemoryMonitor(
            max_mb=self.config.max_memory_mb,
        )

        # Auto-tuner
        self._tuner = AutoTuner()

        # Metrics snapshots
        self._metrics_history: List[OptimizationMetrics] = []

        # Default RPC URLs per chain (overridable)
        self._chain_rpcs: Dict[int, List[str]] = {
            1: [
                os.getenv("ETH_RPC_URL", "https://eth.llamarpc.com"),
                os.getenv("ETH_RPC_BACKUP", "https://rpc.ankr.com/eth"),
            ],
            42161: [
                os.getenv("ARBITRUM_RPC_URL", "https://arb1.arbitrum.io/rpc"),
                os.getenv("ARBITRUM_RPC_BACKUP", "https://rpc.ankr.com/arbitrum"),
            ],
            10: [
                os.getenv("OPTIMISM_RPC_URL", "https://mainnet.optimism.io"),
            ],
            8453: [
                os.getenv("BASE_RPC_URL", "https://mainnet.base.org"),
            ],
            137: [
                os.getenv("POLYGON_RPC_URL", "https://polygon-rpc.com"),
            ],
            43114: [
                os.getenv("AVALANCHE_RPC_URL", "https://api.avax.network/ext/bc/C/rpc"),
            ],
            56: [
                os.getenv("BSC_RPC_URL", "https://bsc-dataseed.binance.org"),
            ],
        }

    # ── Lifecycle ─────────────────────────────────────────────

    async def start(self) -> None:
        self._running = True

        # Create connection pools
        for chain_id, urls in self._chain_rpcs.items():
            pool = RPCConnectionPool(
                chain_id=chain_id,
                rpc_urls=urls,
                pool_size=self.config.pool_size_per_chain,
            )
            self._pools[chain_id] = pool

            batcher = BatchAggregator(
                pool=pool,
                batch_size=self.config.batch_size,
                flush_interval_ms=self.config.batch_flush_ms,
            )
            await batcher.start()
            self._batchers[chain_id] = batcher

        logger.info(
            "  [+] Module 9: Performance Optimizer — %d chain pools, "
            "batch_size=%d, cache_max=%d",
            len(self._pools), self.config.batch_size, self.config.cache_max_size,
        )

    async def stop(self) -> None:
        self._running = False
        for batcher in self._batchers.values():
            await batcher.stop()
        logger.info("  [-] Module 9: Performance Optimizer stopped")

    # ── Public API ────────────────────────────────────────────

    def get_pool(self, chain_id: int) -> Optional[RPCConnectionPool]:
        return self._pools.get(chain_id)

    def get_batcher(self, chain_id: int) -> Optional[BatchAggregator]:
        return self._batchers.get(chain_id)

    def record_latency(self, component: str, ms: float) -> None:
        """Record a latency sample for a component."""
        self._latency_buckets[component].record(ms)

    async def run_cycle(self) -> None:
        """Periodic maintenance: health checks, GC, auto-tune."""
        # Health check all pools
        for pool in self._pools.values():
            await pool.health_check()

        # Memory check
        self._memory.check_pressure()

        # Collect metrics
        metrics = self._collect_metrics()
        self._metrics_history.append(metrics)
        if len(self._metrics_history) > 500:
            self._metrics_history = self._metrics_history[-250:]

        # Auto-tune
        actions = self._tuner.evaluate(metrics)
        if actions:
            for a in actions:
                logger.info(
                    "[AutoTune] %s.%s: %s → %s (%s)",
                    a.component, a.parameter, a.old_value, a.new_value, a.reason,
                )

    def _collect_metrics(self) -> OptimizationMetrics:
        total_req = sum(p._total_requests for p in self._pools.values())
        total_conn = sum(p.pool_size for p in self._pools.values())
        all_lat = []
        for b in self._latency_buckets.values():
            all_lat.extend(b.samples)

        return OptimizationMetrics(
            timestamp=time.time(),
            total_requests=total_req,
            cache_hits=self.cache.hits,
            cache_misses=self.cache.misses,
            cache_hit_rate=self.cache.hit_rate,
            avg_latency_ms=statistics.mean(all_lat) if all_lat else 0,
            p95_latency_ms=(
                sorted(all_lat)[int(len(all_lat) * 0.95)]
                if len(all_lat) > 1 else 0
            ),
            memory_mb=self._memory.current_mb(),
            gc_collections=self._memory._gc_runs,
            active_pools=len(self._pools),
            total_connections=total_conn,
            batch_operations=sum(b._batches_sent for b in self._batchers.values()),
            tuning_actions=len(self._tuner._actions),
        )

    # ── Stats ─────────────────────────────────────────────────

    def get_stats(self) -> Dict[str, Any]:
        return {
            "pools": {
                cid: {
                    "requests": p._total_requests,
                    "errors": p._total_errors,
                }
                for cid, p in self._pools.items()
            },
            "cache": self.cache.get_stats(),
            "memory": self._memory.get_stats(),
            "latency": {
                name: bucket.summary()
                for name, bucket in self._latency_buckets.items()
            },
            "tuning_history": self._tuner.get_history()[-5:],
            "batch_operations": sum(b._batches_sent for b in self._batchers.values()),
        }
