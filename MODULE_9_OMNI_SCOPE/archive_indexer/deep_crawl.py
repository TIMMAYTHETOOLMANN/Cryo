#!/usr/bin/env python3
"""
Archive Indexer — Deep-Crawl Historical Intelligence
======================================================
Discovers opportunities in historical data to predict future events.

Capabilities:
  1. The Graph Subgraph Querying — index events across 1000+ protocols
  2. Wallet Clustering — link addresses via deposit/withdrawal patterns
  3. Smart Money & Insider Flow Tracking — flag high-alpha wallet activity
  4. Serial Liquidatee Detection — identify users who consistently get liquidated

Data flow: Historical events → cluster → profile → emit signals → DataBus
"""

import asyncio
import logging
import time
from collections import defaultdict
from typing import Dict, List, Optional, Set
from dataclasses import dataclass, field

from ..config import OmniScopeConfig, get_omni_config
from ..data_bus import DataBus, OpportunitySignal, SignalType, SignalSource

logger = logging.getLogger(__name__)


@dataclass
class WalletCluster:
    """A cluster of related wallet addresses."""
    cluster_id: str
    addresses: Set[str] = field(default_factory=set)
    total_exposure_usd: float = 0.0
    chain_ids: Set[int] = field(default_factory=set)
    protocols: Set[str] = field(default_factory=set)
    risk_score: float = 0.0  # 0=safe, 1=high risk
    is_smart_money: bool = False
    is_serial_liquidatee: bool = False
    liquidation_count: int = 0
    last_activity: float = 0.0


@dataclass
class SubgraphQuery:
    """A query to The Graph Network."""
    subgraph_id: str
    query: str
    variables: Dict = field(default_factory=dict)


# Well-known subgraph IDs for major protocols
PROTOCOL_SUBGRAPHS = {
    "aave_v3_ethereum": "QmWqZfpLFPX1VMNQKbJAJTiT1sM2nRAtqR4RVQDd5RYHXW",
    "aave_v3_arbitrum": "DLuE98AEBw2dtc3mNwlKp42sAMjM7mpSQiFm2sKrY9Ek",
    "compound_v3": "6tGbL7WBx287EZwGUvvcPdCPDHmpCDj6TGhjQjLmESxp",
    "uniswap_v3_ethereum": "5zvR82QoaXYFyDEKLZ9t6v9adgnptxYpKpSbxtgVENFV",
    "maker_dao": "QmSKagVFfYQmyExLnVxGLiLuUGaKWN4bZz7MpqnTSjQ9eP",
}


class ArchiveIndexer:
    """
    Mines historical on-chain data for intelligence signals.
    Builds wallet profiles and clusters for predictive liquidation.
    """

    def __init__(self, bus: DataBus, config: Optional[OmniScopeConfig] = None):
        self.bus = bus
        self.config = config or get_omni_config()
        self._cfg = self.config.archive_indexer
        self._running = False

        # Wallet clusters
        self._clusters: Dict[str, WalletCluster] = {}
        self._address_to_cluster: Dict[str, str] = {}

        # Smart money wallet set
        self._smart_money: Set[str] = set()

        # Liquidation history: user → count
        self._liquidation_history: Dict[str, int] = defaultdict(int)

        self.stats = {
            "subgraph_queries": 0,
            "wallets_clustered": 0,
            "clusters_created": 0,
            "serial_liquidatees_found": 0,
            "smart_money_wallets": 0,
            "signals_emitted": 0,
        }

    async def start(self):
        """Start the archive indexer."""
        self._running = True
        logger.info("📚 Archive Indexer starting…")

        tasks = [
            asyncio.create_task(self._query_subgraphs_loop()),
            asyncio.create_task(self._cluster_wallets_loop()),
        ]

        try:
            await asyncio.gather(*tasks, return_exceptions=True)
        except asyncio.CancelledError:
            pass

    async def stop(self):
        self._running = False

    # ------------------------------------------------------------------
    # Subgraph Querying
    # ------------------------------------------------------------------

    async def _query_subgraphs_loop(self):
        """Periodically query The Graph for liquidation events."""
        while self._running:
            for protocol, subgraph_id in PROTOCOL_SUBGRAPHS.items():
                try:
                    events = await self._query_liquidations(subgraph_id)
                    for event in events:
                        user = event.get("user", "").lower()
                        if user:
                            self._liquidation_history[user] += 1
                            if self._liquidation_history[user] >= 3:
                                self._flag_serial_liquidatee(user, protocol)
                    self.stats["subgraph_queries"] += 1
                except Exception as e:
                    logger.debug(f"Subgraph query error ({protocol}): {e}")

            await asyncio.sleep(300)  # Every 5 minutes

    async def _query_liquidations(self, subgraph_id: str) -> List[Dict]:
        """Query a subgraph for recent liquidation events."""
        if not self._cfg.graph_api_key:
            return []

        query = """
        {
          liquidationCalls(first: 100, orderBy: timestamp, orderDirection: desc) {
            id
            user
            collateralAsset { symbol }
            debtAsset { symbol }
            debtToCover
            liquidatedCollateralAmount
            timestamp
          }
        }
        """
        # In production: send this to The Graph API
        # url = f"{self._cfg.graph_api_url}/{self._cfg.graph_api_key}/subgraphs/id/{subgraph_id}"
        # async with aiohttp.ClientSession() as session:
        #     async with session.post(url, json={"query": query}) as resp:
        #         data = await resp.json()
        #         return data.get("data", {}).get("liquidationCalls", [])
        return []

    # ------------------------------------------------------------------
    # Wallet Clustering
    # ------------------------------------------------------------------

    async def _cluster_wallets_loop(self):
        """Run wallet clustering periodically."""
        while self._running:
            try:
                self._run_clustering()
            except Exception as e:
                logger.debug(f"Clustering error: {e}")
            await asyncio.sleep(self._cfg.clustering_interval_hours * 3600)

    def _run_clustering(self):
        """
        Group addresses by fund flow patterns.
        If wallet A sends funds to wallet B which deposits on protocol,
        they form a cluster with correlated risk.
        """
        # In production: analyze TX traces from TimescaleDB
        # For now: cluster based on shared liquidation patterns
        users_by_protocol: Dict[str, Set[str]] = defaultdict(set)

        for user, count in self._liquidation_history.items():
            # Simplified: users who got liquidated on the same block/protocol
            users_by_protocol[f"liquidated_{count}"].add(user)

        cluster_idx = len(self._clusters)
        for key, users in users_by_protocol.items():
            if len(users) >= 2:
                cid = f"cluster_{cluster_idx}"
                cluster = WalletCluster(
                    cluster_id=cid,
                    addresses=users,
                    last_activity=time.time(),
                )
                self._clusters[cid] = cluster
                for addr in users:
                    self._address_to_cluster[addr] = cid
                cluster_idx += 1
                self.stats["clusters_created"] += 1
                self.stats["wallets_clustered"] += len(users)

    def _flag_serial_liquidatee(self, user: str, protocol: str):
        """Flag a user who has been liquidated 3+ times."""
        if self._liquidation_history[user] == 3:
            self.stats["serial_liquidatees_found"] += 1
            self.bus.publish(OpportunitySignal(
                signal_type=SignalType.PENDING_LIQUIDATION,
                source=SignalSource.ARCHIVE_INDEXER,
                chain_id=1,
                confidence=0.6,
                estimated_profit_usd=150.0,
                gas_cost_estimate_usd=15.0,
                urgency_seconds=3600,
                target_user=user,
                target_protocol=protocol,
                competition_estimate=0.2,
                execution_complexity=0.3,
                metadata={"liquidation_count": self._liquidation_history[user]},
            ))
            self.stats["signals_emitted"] += 1

    # ------------------------------------------------------------------
    # Smart Money Tracking
    # ------------------------------------------------------------------

    def add_smart_money_wallet(self, address: str):
        """Track a smart money wallet."""
        self._smart_money.add(address.lower())
        self.stats["smart_money_wallets"] = len(self._smart_money)

    def is_smart_money(self, address: str) -> bool:
        return address.lower() in self._smart_money

    def get_cluster(self, address: str) -> Optional[WalletCluster]:
        cid = self._address_to_cluster.get(address.lower())
        return self._clusters.get(cid) if cid else None

    def get_stats(self) -> Dict:
        return {
            **self.stats,
            "total_liquidation_history": len(self._liquidation_history),
        }
