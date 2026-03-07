#!/usr/bin/env python3
"""
Module 1 — Deep-Crawl Archive Indexer
========================================
Mines historical data to predict future events and uncover hidden relationships.

Capabilities:
  1.1  Universal Subgraph Querying via The Graph
  1.2  Wallet Clustering & Cross-Protocol Exposure Mapping (Neo4j)
  1.3  Smart Money & Insider Flow Tracking (Nansen, 0xPPL, DeBank)
  1.4  Governance & Parameter Change Monitoring

Data flow:
  The Graph / Nansen / DeBank → cluster wallets → predict cascades
  → emit TriangulatedSignal → SignalBus
"""
from __future__ import annotations

import asyncio
import logging
import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set

import aiohttp

from .config import DeepCrawlConfig, get_config
from .data_lake import DataLake, Topic
from .signal_bus import SignalBus, SignalSource, SignalType, TriangulatedSignal

logger = logging.getLogger(__name__)


# ── Protocol Subgraph IDs ────────────────────────────────────────

PROTOCOL_SUBGRAPHS: Dict[str, Dict[str, str]] = {
    "aave_v3": {
        "ethereum": "QmWqZfpLFPX1VMNQKbJAJTiT1sM2nRAtqR4RVQDd5RYHXW",
        "arbitrum": "DLuE98AEBw2dtc3mNwlKp42sAMjM7mpSQiFm2sKrY9Ek",
        "polygon": "Co2URyXjM1mXhNg9x3LP4RF2XfJyN62t1AvGZuGBREuY",
        "optimism": "8CuhVERLDOGqf3LGBFP4BrJ7K5RJz1YK2VcCfFQsfhBk",
    },
    "compound_v3": {
        "ethereum": "6tGbL7WBx287EZwGUvvcPdCPDHmpCDj6TGhjQjLmESxp",
    },
    "morpho": {
        "ethereum": "H8sT7CdWyJzp5YZFNT8GxiBNAcgE3QE7CJkwS3UFh7i8",
    },
    "maker": {
        "ethereum": "QmSKagVFfYQmyExLnVxGLiLuUGaKWN4bZz7MpqnTSjQ9eP",
    },
}

GOVERNANCE_ENDPOINTS: Dict[str, str] = {
    "aave": "https://governance.aave.com/api/proposals",
    "compound": "https://api.compound.finance/api/v2/governance/proposals",
    "maker": "https://vote.makerdao.com/api/executive",
    "uniswap": "https://api.uniswap.org/v1/governance/proposals",
}


# ── Data Models ──────────────────────────────────────────────────

@dataclass
class WalletCluster:
    cluster_id: str
    addresses: Set[str] = field(default_factory=set)
    total_exposure_usd: float = 0.0
    chain_ids: Set[int] = field(default_factory=set)
    protocols: Set[str] = field(default_factory=set)
    risk_score: float = 0.0
    is_smart_money: bool = False
    is_serial_liquidatee: bool = False
    liquidation_count: int = 0
    last_activity: float = 0.0


@dataclass
class GovernanceChange:
    protocol: str
    proposal_id: str
    title: str
    status: str
    affected_assets: List[str] = field(default_factory=list)
    ltv_change: float = 0.0
    liq_threshold_change: float = 0.0
    execution_timestamp: float = 0.0
    projected_underwater_count: int = 0


@dataclass
class CascadePrediction:
    """Predicted cross-protocol liquidation cascade."""
    trigger_address: str
    trigger_protocol: str
    trigger_chain_id: int
    cascade_targets: List[Dict[str, Any]] = field(default_factory=list)
    total_cascade_profit_usd: float = 0.0
    confidence: float = 0.0


# ── Module ───────────────────────────────────────────────────────

class DeepCrawlIndexer:
    """
    Module 1: Deep-Crawl Archive Indexer.

    Continuously indexes historical on-chain events via The Graph,
    clusters wallets via Neo4j, tracks smart money via external APIs,
    and monitors governance proposals for parameter changes.
    """

    def __init__(
        self,
        bus: SignalBus,
        lake: DataLake,
        config: Optional[DeepCrawlConfig] = None,
    ):
        self.bus = bus
        self.lake = lake
        self._cfg = config or get_config().deep_crawl
        self._running = False
        self._session: Optional[aiohttp.ClientSession] = None

        # State
        self._clusters: Dict[str, WalletCluster] = {}
        self._address_to_cluster: Dict[str, str] = {}
        self._smart_money: Set[str] = set()
        self._serial_liquidatees: Set[str] = set()
        self._governance_cache: Dict[str, GovernanceChange] = {}

        # Stats
        self._stats = {
            "subgraph_queries": 0,
            "events_indexed": 0,
            "wallet_clusters": 0,
            "smart_money_wallets": 0,
            "serial_liquidatees": 0,
            "governance_proposals_tracked": 0,
            "cascade_predictions": 0,
            "signals_emitted": 0,
        }

    # ── Lifecycle ─────────────────────────────────────────────

    async def start(self):
        self._running = True
        self._session = aiohttp.ClientSession()
        logger.info("[DeepCrawl] Starting — %d subgraph targets",
                    sum(len(v) for v in PROTOCOL_SUBGRAPHS.values()))

    async def stop(self):
        self._running = False
        if self._session:
            await self._session.close()
            self._session = None
        logger.info("[DeepCrawl] Stopped")

    async def run_cycle(self):
        """Execute one full deep-crawl cycle."""
        if not self._running:
            return

        await asyncio.gather(
            self._index_subgraphs(),
            self._update_wallet_clusters(),
            self._track_smart_money(),
            self._monitor_governance(),
            self._predict_cascades(),
            return_exceptions=True,
        )

    # ── 1.1 Universal Subgraph Querying ──────────────────────

    async def _index_subgraphs(self):
        """Query all known subgraphs for recent events."""
        for protocol, chains in PROTOCOL_SUBGRAPHS.items():
            for chain_name, subgraph_id in chains.items():
                try:
                    events = await self._query_subgraph(
                        subgraph_id, protocol, chain_name,
                    )
                    self._stats["events_indexed"] += len(events)

                    for event in events:
                        # Store in TimescaleDB
                        await self.lake.timescale.insert_event("lending_events", {
                            "protocol": protocol,
                            "chain": chain_name,
                            "event_type": event.get("type", "unknown"),
                            "user": event.get("user", ""),
                            "amount_usd": event.get("amountUSD", 0),
                            "ts": time.time(),
                        })

                        # Track liquidations for serial-liquidatee detection
                        if event.get("type") == "liquidation":
                            user = event.get("user", "")
                            if user:
                                self._serial_liquidatees.add(user)
                                self._stats["serial_liquidatees"] = len(self._serial_liquidatees)

                except Exception as exc:
                    logger.debug("[DeepCrawl] Subgraph %s/%s error: %s",
                                 protocol, chain_name, exc)

        self._stats["subgraph_queries"] += 1

    async def _query_subgraph(self, subgraph_id: str, protocol: str,
                              chain: str) -> List[Dict]:
        """Query a Graph subgraph for recent lending events."""
        if not self._cfg.graph_api_key or not self._session:
            return []

        url = f"{self._cfg.graph_api_url}/{self._cfg.graph_api_key}/subgraphs/id/{subgraph_id}"
        query = """
        {
            liquidationCalls(first: 100, orderBy: timestamp, orderDirection: desc) {
                id
                user { id }
                principalAmount
                collateralAmount
                timestamp
            }
            borrows(first: 100, orderBy: timestamp, orderDirection: desc) {
                id
                user { id }
                amount
                timestamp
            }
        }
        """
        try:
            async with self._session.post(url, json={"query": query}, timeout=10) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    events = []
                    for liq in data.get("data", {}).get("liquidationCalls", []):
                        events.append({
                            "type": "liquidation",
                            "user": liq.get("user", {}).get("id", ""),
                            "amountUSD": float(liq.get("principalAmount", 0)),
                            "ts": float(liq.get("timestamp", 0)),
                        })
                    for borrow in data.get("data", {}).get("borrows", []):
                        events.append({
                            "type": "borrow",
                            "user": borrow.get("user", {}).get("id", ""),
                            "amountUSD": float(borrow.get("amount", 0)),
                            "ts": float(borrow.get("timestamp", 0)),
                        })
                    return events
        except Exception:
            pass
        return []

    # ── 1.2 Wallet Clustering ────────────────────────────────

    async def _update_wallet_clusters(self):
        """Build wallet clusters from fund flows using Neo4j."""
        # Get recent transfers between tracked addresses
        events = await self.lake.timescale.query(
            "SELECT * FROM lending_events WHERE ts > $1 ORDER BY ts DESC LIMIT 1000",
            time.time() - 86400,
        )

        fund_flows: Dict[str, Set[str]] = defaultdict(set)
        for event in events:
            user = event.get("user", "")
            if user:
                # Group by protocol interaction patterns
                protocol = event.get("protocol", "")
                key = f"{protocol}:{user}"
                fund_flows[user].add(protocol)

        # Update Neo4j graph
        for address, protocols in fund_flows.items():
            await self.lake.neo4j.upsert_wallet(address, {
                "protocols": list(protocols),
                "last_seen": time.time(),
            })
            # Create edges between wallets that share protocols
            for other_addr, other_protocols in fund_flows.items():
                if address != other_addr and protocols & other_protocols:
                    await self.lake.neo4j.add_edge(
                        address, other_addr, "CO_PROTOCOL",
                        {"shared": list(protocols & other_protocols)},
                    )

        # Build cluster objects from graph
        visited: Set[str] = set()
        cluster_id = 0
        for address in fund_flows:
            if address in visited:
                continue
            members = await self.lake.neo4j.get_cluster(address)
            members.append(address)
            cluster = WalletCluster(
                cluster_id=f"cluster_{cluster_id}",
                addresses=set(members),
                chain_ids=set(),
                protocols=set(),
            )
            for m in members:
                self._address_to_cluster[m] = cluster.cluster_id
                visited.add(m)
                if m in self._serial_liquidatees:
                    cluster.is_serial_liquidatee = True
                    cluster.liquidation_count += 1
            self._clusters[cluster.cluster_id] = cluster
            cluster_id += 1

        self._stats["wallet_clusters"] = len(self._clusters)

    # ── 1.3 Smart Money Tracking ─────────────────────────────

    async def _track_smart_money(self):
        """Track smart money wallets via Nansen / DeBank APIs."""
        if not self._session:
            return

        # Nansen API
        if self._cfg.nansen_api_key:
            try:
                headers = {"Authorization": f"Bearer {self._cfg.nansen_api_key}"}
                async with self._session.get(
                    "https://api.nansen.ai/v1/smart-money",
                    headers=headers, timeout=15,
                ) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        for wallet in data.get("wallets", []):
                            addr = wallet.get("address", "").lower()
                            if addr:
                                self._smart_money.add(addr)
                                await self.lake.neo4j.upsert_wallet(addr, {
                                    "is_smart_money": True,
                                    "tags": wallet.get("tags", []),
                                    "pnl_usd": wallet.get("pnl_usd", 0),
                                })
            except Exception as exc:
                logger.debug("[DeepCrawl] Nansen API error: %s", exc)

        # DeBank API for wallet activity
        if self._cfg.debank_api_key:
            for addr in list(self._smart_money)[:50]:
                try:
                    headers = {"AccessKey": self._cfg.debank_api_key}
                    async with self._session.get(
                        f"https://pro-openapi.debank.com/v1/user/token_list?id={addr}&chain_id=eth",
                        headers=headers, timeout=10,
                    ) as resp:
                        if resp.status == 200:
                            tokens = await resp.json()
                            # If smart money holds a new/unusual asset, emit signal
                            for token in tokens:
                                if token.get("is_verified") is False:
                                    self._emit_smart_money_signal(addr, token)
                except Exception:
                    pass

        self._stats["smart_money_wallets"] = len(self._smart_money)

    def _emit_smart_money_signal(self, address: str, token_info: Dict):
        """Emit a signal when smart money interacts with an unusual asset."""
        signal = TriangulatedSignal(
            signal_type=SignalType.WHALE_MOVEMENT,
            source=SignalSource.DEEP_CRAWL_INDEXER,
            chain_id=1,
            confidence=0.65,
            estimated_profit_usd=0.0,
            gas_cost_estimate_usd=0.0,
            urgency_seconds=300.0,
            target_user=address,
            target_asset=token_info.get("symbol", ""),
            target_contract=token_info.get("id", ""),
            metadata={
                "wallet_type": "smart_money",
                "token_amount": token_info.get("amount", 0),
                "is_verified": token_info.get("is_verified", False),
            },
        )
        self.bus.publish(signal)
        self._stats["signals_emitted"] += 1

    # ── 1.4 Governance Monitoring ────────────────────────────

    async def _monitor_governance(self):
        """Monitor governance proposals for parameter changes."""
        if not self._session:
            return

        for protocol, endpoint in GOVERNANCE_ENDPOINTS.items():
            try:
                async with self._session.get(endpoint, timeout=10) as resp:
                    if resp.status != 200:
                        continue
                    data = await resp.json()
                    proposals = data if isinstance(data, list) else data.get("proposals", [])

                    for proposal in proposals[:10]:
                        pid = str(proposal.get("id", ""))
                        if pid in self._governance_cache:
                            continue

                        change = GovernanceChange(
                            protocol=protocol,
                            proposal_id=pid,
                            title=proposal.get("title", ""),
                            status=proposal.get("status", "unknown"),
                        )

                        # Check if proposal affects LTV / liquidation threshold
                        title_lower = change.title.lower()
                        if any(kw in title_lower for kw in
                               ("ltv", "liquidation", "threshold", "collateral",
                                "risk parameter", "reserve factor")):
                            change.affected_assets = self._extract_assets(change.title)
                            self._governance_cache[pid] = change
                            self._stats["governance_proposals_tracked"] += 1

                            # Emit signal
                            signal = TriangulatedSignal(
                                signal_type=SignalType.GOVERNANCE_CHANGE,
                                source=SignalSource.DEEP_CRAWL_INDEXER,
                                chain_id=1,
                                confidence=0.7,
                                estimated_profit_usd=0.0,
                                gas_cost_estimate_usd=0.0,
                                urgency_seconds=3600.0,
                                target_protocol=protocol,
                                metadata={
                                    "proposal_id": pid,
                                    "title": change.title,
                                    "status": change.status,
                                    "affected_assets": change.affected_assets,
                                },
                            )
                            self.bus.publish(signal)
                            self._stats["signals_emitted"] += 1

            except Exception as exc:
                logger.debug("[DeepCrawl] Governance %s error: %s", protocol, exc)

    @staticmethod
    def _extract_assets(text: str) -> List[str]:
        """Extract asset symbols from governance proposal text."""
        known_assets = [
            "ETH", "WETH", "USDC", "USDT", "DAI", "WBTC", "LINK",
            "AAVE", "UNI", "CRV", "MKR", "COMP", "SNX", "YFI",
            "stETH", "rETH", "cbETH", "wstETH", "GHO", "LUSD",
        ]
        found = []
        text_upper = text.upper()
        for asset in known_assets:
            if asset.upper() in text_upper:
                found.append(asset)
        return found

    # ── 1.2+ Cascade Prediction ──────────────────────────────

    async def _predict_cascades(self):
        """Predict cross-protocol liquidation cascades from wallet clusters."""
        for cluster in self._clusters.values():
            if len(cluster.protocols) < 2:
                continue

            # If a cluster has exposure across multiple protocols,
            # a liquidation on one may cascade to the others
            if cluster.is_serial_liquidatee or cluster.risk_score > 0.7:
                prediction = CascadePrediction(
                    trigger_address=next(iter(cluster.addresses)),
                    trigger_protocol=next(iter(cluster.protocols)),
                    trigger_chain_id=next(iter(cluster.chain_ids)) if cluster.chain_ids else 1,
                    cascade_targets=[
                        {"address": addr, "protocol": p}
                        for addr in list(cluster.addresses)[:5]
                        for p in cluster.protocols
                    ],
                    confidence=min(0.9, 0.3 + cluster.liquidation_count * 0.1),
                )

                signal = TriangulatedSignal(
                    signal_type=SignalType.CASCADING_LIQUIDATION,
                    source=SignalSource.DEEP_CRAWL_INDEXER,
                    chain_id=prediction.trigger_chain_id,
                    confidence=prediction.confidence,
                    estimated_profit_usd=prediction.total_cascade_profit_usd,
                    gas_cost_estimate_usd=5.0,
                    urgency_seconds=60.0,
                    target_user=prediction.trigger_address,
                    target_protocol=prediction.trigger_protocol,
                    competition_estimate=0.3,
                    metadata={
                        "cluster_id": cluster.cluster_id,
                        "cluster_size": len(cluster.addresses),
                        "cascade_depth": len(prediction.cascade_targets),
                    },
                )
                self.bus.publish(signal)
                self._stats["cascade_predictions"] += 1
                self._stats["signals_emitted"] += 1

    # ── Stats ─────────────────────────────────────────────────

    def get_stats(self) -> Dict[str, Any]:
        return {**self._stats}
