#!/usr/bin/env python3
"""
STAGE 5 — Enhanced Cross-Chain Orchestrator (Script 3 Upgraded)
=================================================================
Dynamic cross-chain collateral arbitrage + atomic cross-chain liquidations.

Enhancement #2: Dynamic Cross-Chain Collateral Arbitrage
  - After acquiring collateral, select the best EXIT CHAIN
  - Query real-time prices across chains for price discrepancies
  - Use Across/Stargate/LI.FI for bridge+swap

Enhancement #9: Atomic Cross-Chain Liquidations
  - Handle positions where debt on chain A, collateral on chain B
  - Coordinate flash loans on both chains via relayer
"""

import asyncio
import logging
import time
from collections import defaultdict
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass
from enum import Enum

from ..config.settings import ConfigManager, get_config

logger = logging.getLogger(__name__)


class BridgeProvider(Enum):
    ACROSS = "across"
    HOP = "hop"
    STARGATE = "stargate"
    LIFI = "lifi"
    SOCKET = "socket"


class CrossChainAction(Enum):
    BRIDGE_AND_SELL = "bridge_and_sell"
    ATOMIC_CROSS_LIQUIDATION = "atomic_cross_liquidation"
    PROFIT_REPATRIATION = "profit_repatriation"


@dataclass
class ChainPrice:
    chain_id: int
    asset: str
    price_usd: float
    dex_liquidity_usd: float
    timestamp: float = 0.0


@dataclass
class CrossChainExitRoute:
    source_chain: int
    exit_chain: int
    bridge: BridgeProvider
    bridge_fee_usd: float
    bridge_time_seconds: int
    exit_price_usd: float
    source_price_usd: float
    price_advantage_usd: float
    net_advantage_usd: float
    total_gas_usd: float


@dataclass
class CrossChainLiquidation:
    debt_chain: int
    collateral_chain: int
    borrower: str
    debt_asset: str
    debt_amount_usd: float
    collateral_asset: str
    collateral_amount_usd: float
    health_factor: float
    bridge: BridgeProvider
    estimated_profit_usd: float
    complexity: str


BRIDGE_CONFIGS = {
    BridgeProvider.ACROSS: {
        "supported_chains": [1, 42161, 10, 8453, 137],
        "avg_time_seconds": 120,
        "fee_pct": 0.001,
        "max_amount_usd": 5_000_000,
    },
    BridgeProvider.HOP: {
        "supported_chains": [1, 42161, 10, 137],
        "avg_time_seconds": 300,
        "fee_pct": 0.002,
        "max_amount_usd": 2_000_000,
    },
    BridgeProvider.STARGATE: {
        "supported_chains": [1, 42161, 10, 137, 43114, 56],
        "avg_time_seconds": 60,
        "fee_pct": 0.0006,
        "max_amount_usd": 10_000_000,
    },
    BridgeProvider.LIFI: {
        "supported_chains": [1, 42161, 10, 8453, 137, 43114, 56],
        "avg_time_seconds": 180,
        "fee_pct": 0.003,
        "max_amount_usd": 1_000_000,
    },
}


class CrossChainOrchestrator:
    """
    Multi-chain coordinator with dynamic exit chain selection
    and atomic cross-chain liquidation support.
    """

    def __init__(self, config: Optional[ConfigManager] = None):
        self.config = config or get_config()
        self._prices: Dict[Tuple[int, str], ChainPrice] = {}
        self._gas_prices: Dict[int, float] = {}
        self._workers: Dict[int, asyncio.Queue] = defaultdict(asyncio.Queue)
        self.stats = {
            "cross_chain_arbs_found": 0,
            "cross_chain_liqs_found": 0,
            "bridges_used": 0,
            "total_bridge_advantage_usd": 0.0,
        }

    def update_price(self, chain_id: int, asset: str, price_usd: float,
                     liquidity_usd: float = 1_000_000):
        self._prices[(chain_id, asset)] = ChainPrice(
            chain_id=chain_id, asset=asset, price_usd=price_usd,
            dex_liquidity_usd=liquidity_usd, timestamp=time.time(),
        )

    def update_gas_price(self, chain_id: int, gas_gwei: float):
        self._gas_prices[chain_id] = gas_gwei

    def find_best_exit_chain(
        self, source_chain: int, asset: str, amount_usd: float,
    ) -> Optional[CrossChainExitRoute]:
        source_price = self._prices.get((source_chain, asset))
        if not source_price:
            return None
        best_route: Optional[CrossChainExitRoute] = None
        for (cid, a), price_data in self._prices.items():
            if a != asset or cid == source_chain:
                continue
            if time.time() - price_data.timestamp > 60:
                continue
            for bridge, cfg in BRIDGE_CONFIGS.items():
                if source_chain not in cfg["supported_chains"]:
                    continue
                if cid not in cfg["supported_chains"]:
                    continue
                if amount_usd > cfg["max_amount_usd"]:
                    continue
                bridge_fee = amount_usd * cfg["fee_pct"]
                price_diff = price_data.price_usd - source_price.price_usd
                price_advantage = (price_diff / source_price.price_usd) * amount_usd
                exit_gas_gwei = self._gas_prices.get(cid, 30.0)
                exit_gas_usd = (200_000 * exit_gas_gwei) / 1e9 * 2500
                net_advantage = price_advantage - bridge_fee - exit_gas_usd
                if net_advantage > 0:
                    route = CrossChainExitRoute(
                        source_chain=source_chain, exit_chain=cid,
                        bridge=bridge, bridge_fee_usd=bridge_fee,
                        bridge_time_seconds=cfg["avg_time_seconds"],
                        exit_price_usd=price_data.price_usd,
                        source_price_usd=source_price.price_usd,
                        price_advantage_usd=price_advantage,
                        net_advantage_usd=net_advantage,
                        total_gas_usd=exit_gas_usd,
                    )
                    if best_route is None or route.net_advantage_usd > best_route.net_advantage_usd:
                        best_route = route
        if best_route:
            self.stats["cross_chain_arbs_found"] += 1
            self.stats["total_bridge_advantage_usd"] += best_route.net_advantage_usd
            logger.info(
                f"🌉 Cross-chain arb: {asset} {source_chain}→{best_route.exit_chain} "
                f"via {best_route.bridge.value} +${best_route.net_advantage_usd:.2f}"
            )
        return best_route

    def detect_cross_chain_positions(
        self, user: str, positions: List[Dict],
    ) -> List[CrossChainLiquidation]:
        by_chain: Dict[int, List[Dict]] = defaultdict(list)
        for pos in positions:
            by_chain[pos.get("chain_id", 1)].append(pos)
        opps: List[CrossChainLiquidation] = []
        chains_with_debt = {
            cid for cid, ps in by_chain.items()
            if any(p.get("debt_amount_usd", 0) > 0 for p in ps)
        }
        chains_with_coll = {
            cid for cid, ps in by_chain.items()
            if any(p.get("collateral_amount_usd", 0) > 0 for p in ps)
        }
        for dc in chains_with_debt:
            for cc in chains_with_coll:
                if dc == cc:
                    continue
                best_bridge = self._find_best_bridge(dc, cc)
                if not best_bridge:
                    continue
                for dp in by_chain[dc]:
                    if dp.get("debt_amount_usd", 0) <= 0:
                        continue
                    for cp in by_chain[cc]:
                        if cp.get("collateral_amount_usd", 0) <= 0:
                            continue
                        hf = cp["collateral_amount_usd"] / max(dp["debt_amount_usd"], 1)
                        if hf < 1.05:
                            bf = dp["debt_amount_usd"] * BRIDGE_CONFIGS[best_bridge]["fee_pct"]
                            profit = (
                                cp["collateral_amount_usd"]
                                * cp.get("liquidation_bonus", 0.05)
                                - bf - 20
                            )
                            if profit > 0:
                                opps.append(CrossChainLiquidation(
                                    debt_chain=dc, collateral_chain=cc,
                                    borrower=user, debt_asset=dp.get("debt_asset", ""),
                                    debt_amount_usd=dp["debt_amount_usd"],
                                    collateral_asset=cp.get("collateral_asset", ""),
                                    collateral_amount_usd=cp["collateral_amount_usd"],
                                    health_factor=hf, bridge=best_bridge,
                                    estimated_profit_usd=profit, complexity="cross_chain",
                                ))
                                self.stats["cross_chain_liqs_found"] += 1
        return opps

    def _find_best_bridge(self, a: int, b: int) -> Optional[BridgeProvider]:
        best, best_fee = None, float("inf")
        for bridge, cfg in BRIDGE_CONFIGS.items():
            if a in cfg["supported_chains"] and b in cfg["supported_chains"]:
                if cfg["fee_pct"] < best_fee:
                    best_fee = cfg["fee_pct"]
                    best = bridge
        return best

    async def submit_to_chain(self, chain_id: int, task: Dict):
        await self._workers[chain_id].put(task)

    def get_stats(self) -> Dict:
        return {**self.stats, "price_cache_size": len(self._prices),
                "gas_prices_tracked": len(self._gas_prices)}


def get_orchestrator(config=None) -> CrossChainOrchestrator:
    return CrossChainOrchestrator(config)

__all__ = ["CrossChainOrchestrator", "get_orchestrator", "BridgeProvider",
           "CrossChainExitRoute", "CrossChainLiquidation"]
