#!/usr/bin/env python3
"""
omni_scope_v2.signal_bus — High-Throughput Signal Bus
======================================================
Universal signal format and priority-routed event bus.

All 7 modules publish TriangulatedSignal objects.
The ML Aggregator (Module 6) scores and routes them.
The Engine consumes the ranked queue and hands off to execution.

Upgrade path: swap in-process deque for Apache Kafka / Redis Streams
in production multi-host deployments.
"""
from __future__ import annotations

import hashlib
import logging
import time
from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional

from .config import SignalBusConfig

logger = logging.getLogger(__name__)


# ── Signal Taxonomy ───────────────────────────────────────────────

class SignalType(Enum):
    """Opportunity category."""
    PENDING_LIQUIDATION = "pending_liquidation"
    CASCADING_LIQUIDATION = "cascading_liquidation"
    ARBITRAGE = "arbitrage"
    CROSS_CHAIN_ARB = "cross_chain_arb"
    SANDWICH = "sandwich"
    BACKRUN = "backrun"
    NFT_LIQUIDATION = "nft_liquidation"
    NEW_PROTOCOL = "new_protocol"
    GOVERNANCE_CHANGE = "governance_change"
    SOCIAL_SPIKE = "social_spike"
    YIELD_OPPORTUNITY = "yield_opportunity"
    BRIDGE_IMBALANCE = "bridge_imbalance"
    FLASH_LOAN_SURPLUS = "flash_loan_surplus"
    ORACLE_FRONT_RUN = "oracle_front_run"
    CONTRACT_CLONE = "contract_clone"
    MEV_PATTERN = "mev_pattern"
    WHALE_MOVEMENT = "whale_movement"


class SignalSource(Enum):
    """Which module produced the signal."""
    DEEP_CRAWL_INDEXER = "deep_crawl_indexer"
    MEMPOOL_MICROSCOPE = "mempool_microscope"
    HYPER_SOLVER = "hyper_solver"
    ALPHA_SEEKER = "alpha_seeker"
    STATIC_ANALYSIS = "static_analysis"
    ML_AGGREGATOR = "ml_aggregator"
    ZERO_CAPITAL = "zero_capital"
    EXTERNAL = "external"


class StrategyRoute(Enum):
    """Execution strategy assignment."""
    LIQUIDATION_ENGINE = "liquidation_engine"
    ARB_MODULE = "arb_module"
    CROSS_CHAIN_EXECUTOR = "cross_chain_executor"
    MEV_STRATEGY = "mev_strategy"
    YIELD_OPTIMIZER = "yield_optimizer"
    MANUAL_REVIEW = "manual_review"


# ── Universal Signal Format ──────────────────────────────────────

@dataclass
class TriangulatedSignal:
    """
    Universal signal format for the Omni-Scope v2 engine.
    All 7 modules produce these; the ML Aggregator scores and routes them.
    """
    signal_type: SignalType
    source: SignalSource
    chain_id: int
    confidence: float                    # 0.0–1.0
    estimated_profit_usd: float
    gas_cost_estimate_usd: float
    urgency_seconds: float               # 0 = this block

    # Target identification
    target_protocol: str = ""
    target_user: str = ""
    target_asset: str = ""
    target_contract: str = ""
    tx_hash: str = ""

    # Liquidation-specific
    health_factor: float = 0.0
    debt_amount_usd: float = 0.0
    collateral_amount_usd: float = 0.0
    liquidation_bonus_pct: float = 0.0

    # Competition & complexity scoring
    competition_estimate: float = 0.5    # 0=no competition, 1=many bots
    execution_complexity: float = 0.5    # 0=trivial, 1=extremely complex

    # Cross-chain routing
    source_chain_id: int = 0
    destination_chain_id: int = 0
    bridge_route: str = ""
    hop_count: int = 0

    # Arbitrage-specific
    arb_path: List[str] = field(default_factory=list)
    price_impact_pct: float = 0.0

    # Sentiment-specific
    sentiment_score: float = 0.0
    mention_spike_ratio: float = 0.0

    # ML-assigned fields (populated by Module 6)
    quality_score: float = 0.0
    routed_to: StrategyRoute = StrategyRoute.MANUAL_REVIEW
    ml_features: Dict[str, float] = field(default_factory=dict)

    # Timing
    timestamp: float = 0.0
    created_at: float = field(default_factory=time.time)

    # Metadata
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        if self.timestamp == 0.0:
            self.timestamp = time.time()

    @property
    def net_expected_value(self) -> float:
        return (self.estimated_profit_usd * self.confidence
                - self.gas_cost_estimate_usd)

    @property
    def age_seconds(self) -> float:
        return time.time() - self.timestamp

    @property
    def dedup_key(self) -> str:
        raw = f"{self.signal_type.value}:{self.chain_id}:{self.target_user}:{self.target_contract}:{self.tx_hash}"
        return hashlib.md5(raw.encode()).hexdigest()


# ── Signal Bus ───────────────────────────────────────────────────

class SignalBus:
    """
    High-throughput, deduplicating signal bus.

    Features:
      - Publish/subscribe with typed callbacks
      - Automatic deduplication by signal key
      - TTL-based expiration of stale signals
      - Priority-ranked output queue (filled by ML Aggregator)
      - Batch publish for high-throughput ingestion
      - Stats tracking per source and signal type
    """

    def __init__(self, config: Optional[SignalBusConfig] = None):
        self._cfg = config or SignalBusConfig()

        # Raw signal buffer
        self._buffer: deque[TriangulatedSignal] = deque(maxlen=self._cfg.max_buffer)

        # Subscriber callbacks
        self._subscribers: List[Callable[[TriangulatedSignal], None]] = []

        # ML-ranked priority queue
        self._ranked_queue: List[TriangulatedSignal] = []

        # Deduplication
        self._seen_keys: Dict[str, float] = {}
        self._dedup_window = self._cfg.dedup_window_s

        # Stats
        self._stats = {
            "total_published": 0,
            "total_consumed": 0,
            "duplicates_filtered": 0,
            "expired_evicted": 0,
            "by_source": {},
            "by_type": {},
        }

    # ── Publish ────────────────────────────────────────────────

    def subscribe(self, callback: Callable[[TriangulatedSignal], None]):
        self._subscribers.append(callback)

    def publish(self, signal: TriangulatedSignal) -> bool:
        """Publish a signal.  Returns False if deduplicated away."""
        key = signal.dedup_key
        now = time.time()

        # Dedup
        if key in self._seen_keys:
            if now - self._seen_keys[key] < self._dedup_window:
                self._stats["duplicates_filtered"] += 1
                return False

        self._seen_keys[key] = now
        self._buffer.append(signal)
        self._stats["total_published"] += 1

        # Track by source / type
        src = signal.source.value
        self._stats["by_source"][src] = self._stats["by_source"].get(src, 0) + 1
        typ = signal.signal_type.value
        self._stats["by_type"][typ] = self._stats["by_type"].get(typ, 0) + 1

        # Notify subscribers (ML Aggregator hooks in here)
        for cb in self._subscribers:
            try:
                cb(signal)
            except Exception as exc:
                logger.debug("Bus subscriber error: %s", exc)

        return True

    def publish_batch(self, signals: List[TriangulatedSignal]) -> int:
        """Publish a batch.  Returns count of accepted signals."""
        return sum(1 for s in signals if self.publish(s))

    # ── Ranked Queue (ML-scored) ──────────────────────────────

    def push_ranked(self, signal: TriangulatedSignal):
        """Push an ML-scored signal into the ranked output queue."""
        if len(self._ranked_queue) < self._cfg.ranked_queue_max:
            self._ranked_queue.append(signal)

    def consume_ranked(self, limit: int = 10) -> List[TriangulatedSignal]:
        """
        Pop top-N signals by quality_score.
        Evicts expired signals first.
        """
        self._evict_expired()
        self._ranked_queue.sort(key=lambda s: s.quality_score, reverse=True)
        batch = self._ranked_queue[:limit]
        self._ranked_queue = self._ranked_queue[limit:]
        self._stats["total_consumed"] += len(batch)
        return batch

    def peek_ranked(self, limit: int = 5) -> List[TriangulatedSignal]:
        """Peek at top signals without removing them."""
        self._ranked_queue.sort(key=lambda s: s.quality_score, reverse=True)
        return self._ranked_queue[:limit]

    def get_recent(self, limit: int = 50) -> List[TriangulatedSignal]:
        return list(self._buffer)[-limit:]

    # ── Maintenance ───────────────────────────────────────────

    def _evict_expired(self):
        now = time.time()
        before = len(self._ranked_queue)
        self._ranked_queue = [
            s for s in self._ranked_queue
            if (now - s.timestamp) < self._cfg.signal_ttl_s
        ]
        evicted = before - len(self._ranked_queue)
        if evicted:
            self._stats["expired_evicted"] += evicted

    def gc_dedup_cache(self):
        """Periodically call to prune old dedup keys."""
        now = time.time()
        expired = [k for k, ts in self._seen_keys.items()
                   if now - ts > self._dedup_window * 2]
        for k in expired:
            del self._seen_keys[k]

    # ── Stats ─────────────────────────────────────────────────

    def get_stats(self) -> Dict[str, Any]:
        return {
            **self._stats,
            "buffer_size": len(self._buffer),
            "ranked_queue_size": len(self._ranked_queue),
            "dedup_cache_size": len(self._seen_keys),
            "subscribers": len(self._subscribers),
        }
