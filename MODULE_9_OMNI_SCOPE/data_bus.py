#!/usr/bin/env python3
"""
MODULE 9 — Central Data Bus
==============================
In-process event bus (Kafka-like) for all detector arrays.

All arrays publish OpportunitySignal objects to the bus.
The ML Ranker subscribes and scores them.
The Pipeline consumes the ranked queue.

Production upgrade: replace with Apache Kafka or Redis Pub/Sub
for multi-process / multi-host deployment.
"""

import logging
import time
from collections import deque
from typing import Callable, Dict, List, Set
from dataclasses import dataclass, field
from enum import Enum

logger = logging.getLogger(__name__)


class SignalType(Enum):
    """Category of opportunity signal."""
    PENDING_LIQUIDATION = "pending_liquidation"
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


class SignalSource(Enum):
    """Which array produced the signal."""
    MEMPOOL_RADAR = "mempool_radar"
    CONTRACT_CRAWLER = "contract_crawler"
    STATIC_ANALYZER = "static_analyzer"
    BRIDGE_MONITOR = "bridge_monitor"
    ARCHIVE_INDEXER = "archive_indexer"
    ALPHA_SEEKER = "alpha_seeker"
    EXTERNAL = "external"


@dataclass
class OpportunitySignal:
    """Universal signal format — all arrays produce these."""
    signal_type: SignalType
    source: SignalSource
    chain_id: int
    confidence: float           # 0.0–1.0
    estimated_profit_usd: float
    gas_cost_estimate_usd: float
    urgency_seconds: float      # How fast must we act? (0 = this block)
    timestamp: float = 0.0

    # Enrichment fields (filled by different arrays)
    target_protocol: str = ""
    target_user: str = ""
    target_asset: str = ""
    target_contract: str = ""
    tx_hash: str = ""           # For mempool signals
    health_factor: float = 0.0
    debt_amount_usd: float = 0.0
    collateral_amount_usd: float = 0.0
    liquidation_bonus: float = 0.0
    competition_estimate: float = 0.5   # 0=no competition, 1=many bots
    execution_complexity: float = 0.5   # 0=trivial, 1=extremely complex

    # ML-assigned score (filled by Array 5)
    quality_score: float = 0.0

    # Route (filled by Array 5)
    routed_to: str = ""  # "liquidation_engine", "arb_module", "backrun_bot"

    # Metadata
    metadata: Dict = field(default_factory=dict)

    def __post_init__(self):
        if self.timestamp == 0:
            self.timestamp = time.time()

    @property
    def net_expected_value(self) -> float:
        return (self.estimated_profit_usd * self.confidence
                - self.gas_cost_estimate_usd)

    @property
    def age_seconds(self) -> float:
        return time.time() - self.timestamp


class DataBus:
    """
    Central event bus for the Omni-Scope engine.

    Arrays publish signals → bus stores + notifies subscribers.
    The ML Ranker subscribes and assigns quality scores.
    The pipeline consumes ranked signals.
    """

    MAX_BUFFER = 10_000
    SIGNAL_TTL = 300  # Signals older than 5 min are evicted

    def __init__(self):
        self._buffer: deque[OpportunitySignal] = deque(maxlen=self.MAX_BUFFER)
        self._subscribers: List[Callable[[OpportunitySignal], None]] = []
        self._ranked_queue: List[OpportunitySignal] = []
        self._seen_hashes: Set[str] = set()

        # Stats
        self.stats = {
            "total_published": 0,
            "total_consumed": 0,
            "duplicates_filtered": 0,
            "expired_evicted": 0,
            "by_source": {},
            "by_type": {},
        }

    def subscribe(self, callback: Callable[[OpportunitySignal], None]):
        """Register a callback for new signals."""
        self._subscribers.append(callback)

    def publish(self, signal: OpportunitySignal):
        """Publish a signal from any detector array."""
        # Dedup by tx_hash if present
        if signal.tx_hash and signal.tx_hash in self._seen_hashes:
            self.stats["duplicates_filtered"] += 1
            return
        if signal.tx_hash:
            self._seen_hashes.add(signal.tx_hash)
            if len(self._seen_hashes) > 50_000:
                self._seen_hashes = set(list(self._seen_hashes)[-25_000:])

        self._buffer.append(signal)
        self.stats["total_published"] += 1

        # Track by source/type
        src = signal.source.value
        self.stats["by_source"][src] = self.stats["by_source"].get(src, 0) + 1
        typ = signal.signal_type.value
        self.stats["by_type"][typ] = self.stats["by_type"].get(typ, 0) + 1

        # Notify subscribers
        for cb in self._subscribers:
            try:
                cb(signal)
            except Exception as e:
                logger.debug(f"Bus subscriber error: {e}")

    def publish_batch(self, signals: List[OpportunitySignal]):
        for s in signals:
            self.publish(s)

    def consume_ranked(self, limit: int = 10) -> List[OpportunitySignal]:
        """
        Get top-N signals by quality_score.
        Returns signals sorted best-first and removes them from queue.
        """
        self._evict_expired()
        self._ranked_queue.sort(key=lambda s: s.quality_score, reverse=True)
        batch = self._ranked_queue[:limit]
        self._ranked_queue = self._ranked_queue[limit:]
        self.stats["total_consumed"] += len(batch)
        return batch

    def push_ranked(self, signal: OpportunitySignal):
        """Push a scored signal into the ranked queue (called by ML Ranker)."""
        self._ranked_queue.append(signal)

    def get_recent(self, limit: int = 50) -> List[OpportunitySignal]:
        """Get most recent signals (unranked, for display/debug)."""
        return list(self._buffer)[-limit:]

    def _evict_expired(self):
        now = time.time()
        before = len(self._ranked_queue)
        self._ranked_queue = [
            s for s in self._ranked_queue
            if (now - s.timestamp) < self.SIGNAL_TTL
        ]
        evicted = before - len(self._ranked_queue)
        self.stats["expired_evicted"] += evicted

    def get_stats(self) -> Dict:
        return {
            **self.stats,
            "buffer_size": len(self._buffer),
            "ranked_queue_size": len(self._ranked_queue),
            "subscribers": len(self._subscribers),
        }
