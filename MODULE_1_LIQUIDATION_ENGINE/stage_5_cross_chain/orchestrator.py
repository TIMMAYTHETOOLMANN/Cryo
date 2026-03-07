#!/usr/bin/env python3
"""
STAGE 5 — Enhanced Cross-Chain Orchestrator (Script 3 + Module 5 Upgraded)
===========================================================================
Dynamic cross-chain collateral arbitrage + atomic cross-chain liquidations
+ cross-chain messaging via LayerZero/CCIP/Wormhole + incentive aggregation.

Enhancement #2: Dynamic Cross-Chain Collateral Arbitrage
  - After acquiring collateral, select the best EXIT CHAIN
  - Query real-time prices across chains for price discrepancies
  - Use Across/Stargate/LI.FI for bridge+swap

Enhancement #9: Atomic Cross-Chain Liquidations
  - Handle positions where debt on chain A, collateral on chain B
  - Coordinate flash loans on both chains via relayer

Module 5: Cross-Chain Coordination Layer
  - Central coordinator dispatches mitigation actions across 96+ chains
  - Message relay via LayerZero, Chainlink CCIP, or Wormhole
  - Incentive aggregation: sweep all chain profits to treasury stablecoin
"""

import asyncio
import logging
import time
from collections import defaultdict
from typing import Any, Dict, List, Optional, Tuple
from dataclasses import dataclass, field
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
    INCENTIVE_AGGREGATION = "incentive_aggregation"


class MessagingProtocol(Enum):
    """Cross-chain messaging protocols for executor dispatch."""
    LAYERZERO = "layerzero"
    CHAINLINK_CCIP = "chainlink_ccip"
    WORMHOLE = "wormhole"
    DIRECT_RPC = "direct_rpc"  # Fallback: direct TX via RPC


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


@dataclass
class CrossChainMessage:
    """
    Message dispatched to a remote chain's RiskMitigationExecutor.
    Sent via LayerZero, CCIP, or Wormhole depending on chain support.
    """
    chain_id: int
    protocol: str                    # e.g., "aave-v3"
    user: str                        # Borrower address
    debt_asset: str                  # Debt token address on target chain
    debt_amount: int                 # Debt amount in wei
    collateral_asset: str            # Collateral token on target chain
    liquidity_provider: str          # Flash loan provider to use
    max_gas_price: int               # Gas price cap in wei
    messaging_protocol: MessagingProtocol = MessagingProtocol.DIRECT_RPC
    executor_address: str = ""       # RiskMitigationExecutor on target chain
    nonce: int = 0


@dataclass
class IncentiveRecord:
    """Tracks incentive (profit) collected on a specific chain."""
    chain_id: int
    asset: str                       # Token address of collected incentive
    amount: int                      # Amount in token units
    value_usd: float                 # USD value at collection time
    timestamp: float = 0.0
    swept_to_treasury: bool = False


# Messaging protocol configs per chain
MESSAGING_CONFIGS: Dict[int, Dict[MessagingProtocol, Dict[str, Any]]] = {
    1: {
        MessagingProtocol.LAYERZERO: {"endpoint": "0x66A71Dcef29a0fFBDBE3c6a460a3B5BC225Cd675", "chain_id_lz": 101},
        MessagingProtocol.CHAINLINK_CCIP: {"router": "0x80226fc0Ee2b096224EeAc085Bb9a8cba1146f7D", "chain_selector": 5009297550715157269},
    },
    42161: {
        MessagingProtocol.LAYERZERO: {"endpoint": "0x3c2269811836af69497E5F486A85D7316753cf62", "chain_id_lz": 110},
        MessagingProtocol.CHAINLINK_CCIP: {"router": "0x141fa059441E0ca23ce184B6A78bafD2A517DdE8", "chain_selector": 4949039107694359620},
    },
    10: {
        MessagingProtocol.LAYERZERO: {"endpoint": "0x3c2269811836af69497E5F486A85D7316753cf62", "chain_id_lz": 111},
        MessagingProtocol.CHAINLINK_CCIP: {"router": "0x3206695CaE29952f4b0c22a169725a865bc8Ce0f", "chain_selector": 3734403246176062136},
    },
    137: {
        MessagingProtocol.LAYERZERO: {"endpoint": "0x3c2269811836af69497E5F486A85D7316753cf62", "chain_id_lz": 109},
        MessagingProtocol.CHAINLINK_CCIP: {"router": "0x849c5ED5a80F5B408Dd4969b78c2C8fdf0390927", "chain_selector": 4051577828743386545},
    },
    8453: {
        MessagingProtocol.LAYERZERO: {"endpoint": "0xb6319cC6c8c27A8F5dAF0dD3DF91EA35C4720dd7", "chain_id_lz": 184},
        MessagingProtocol.CHAINLINK_CCIP: {"router": "0x881e3A65B4d4a04dD529061dd0071cf975F58bCD", "chain_selector": 15971525489660198786},
    },
    43114: {
        MessagingProtocol.LAYERZERO: {"endpoint": "0x3c2269811836af69497E5F486A85D7316753cf62", "chain_id_lz": 106},
    },
    56: {
        MessagingProtocol.LAYERZERO: {"endpoint": "0x3c2269811836af69497E5F486A85D7316753cf62", "chain_id_lz": 102},
    },
}


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
    Multi-chain coordinator with dynamic exit chain selection,
    atomic cross-chain liquidation support, cross-chain messaging
    dispatch (LayerZero/CCIP/Wormhole), and incentive aggregation.
    """

    def __init__(self, config: Optional[ConfigManager] = None):
        self.config = config or get_config()
        self._prices: Dict[Tuple[int, str], ChainPrice] = {}
        self._gas_prices: Dict[int, float] = {}
        self._workers: Dict[int, asyncio.Queue] = defaultdict(asyncio.Queue)
        # Executor contract addresses per chain (RiskMitigationExecutor)
        self._executors: Dict[int, str] = {}
        # Collected incentives awaiting aggregation
        self._incentives: List[IncentiveRecord] = []
        # Preferred messaging protocol per chain
        self._preferred_messaging: Dict[int, MessagingProtocol] = {}
        self.stats = {
            "cross_chain_arbs_found": 0,
            "cross_chain_liqs_found": 0,
            "bridges_used": 0,
            "total_bridge_advantage_usd": 0.0,
            "messages_dispatched": 0,
            "incentives_collected_usd": 0.0,
            "incentive_sweeps": 0,
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
                "gas_prices_tracked": len(self._gas_prices),
                "executors_registered": len(self._executors),
                "pending_incentives": len(self._incentives)}

    # ── Executor Registration ────────────────────────────────────────

    def register_executor(self, chain_id: int, executor_address: str):
        """Register a RiskMitigationExecutor contract on a target chain."""
        self._executors[chain_id] = executor_address
        logger.info("🔗 Executor registered: chain %d → %s", chain_id, executor_address[:12])

    def set_preferred_messaging(self, chain_id: int, protocol: MessagingProtocol):
        """Set preferred cross-chain messaging protocol for a chain."""
        self._preferred_messaging[chain_id] = protocol

    # ── Cross-Chain Message Dispatch (Module 5) ──────────────────────

    def build_mitigation_message(
        self,
        chain_id: int,
        protocol: str,
        user: str,
        debt_asset: str,
        debt_amount: int,
        collateral_asset: str,
        liquidity_provider: str = "aave-v3-pool",
    ) -> Optional[CrossChainMessage]:
        """
        Build a cross-chain mitigation message for dispatch to a remote executor.

        Returns None if no executor is registered for the target chain.
        """
        executor = self._executors.get(chain_id)
        if not executor:
            logger.warning("No executor registered for chain %d", chain_id)
            return None

        max_gas = int(self._gas_prices.get(chain_id, 50) * 1e9)

        # Select best messaging protocol for this chain
        preferred = self._preferred_messaging.get(chain_id)
        if preferred and chain_id in MESSAGING_CONFIGS:
            if preferred in MESSAGING_CONFIGS[chain_id]:
                msg_protocol = preferred
            else:
                msg_protocol = MessagingProtocol.DIRECT_RPC
        elif chain_id in MESSAGING_CONFIGS:
            # Default: LayerZero if available, then CCIP, then direct RPC
            chain_protocols = MESSAGING_CONFIGS[chain_id]
            if MessagingProtocol.LAYERZERO in chain_protocols:
                msg_protocol = MessagingProtocol.LAYERZERO
            elif MessagingProtocol.CHAINLINK_CCIP in chain_protocols:
                msg_protocol = MessagingProtocol.CHAINLINK_CCIP
            else:
                msg_protocol = MessagingProtocol.DIRECT_RPC
        else:
            msg_protocol = MessagingProtocol.DIRECT_RPC

        return CrossChainMessage(
            chain_id=chain_id,
            protocol=protocol,
            user=user,
            debt_asset=debt_asset,
            debt_amount=debt_amount,
            collateral_asset=collateral_asset,
            liquidity_provider=liquidity_provider,
            max_gas_price=max_gas,
            messaging_protocol=msg_protocol,
            executor_address=executor,
        )

    async def dispatch_mitigation(self, message: CrossChainMessage) -> bool:
        """
        Dispatch a risk mitigation message to the target chain.

        For DIRECT_RPC: queues the TX for execution via the chain's worker.
        For LayerZero/CCIP: constructs the cross-chain message payload.
        """
        logger.info(
            "📡 Dispatching mitigation: chain=%d protocol=%s user=%s via %s",
            message.chain_id, message.protocol,
            message.user[:12], message.messaging_protocol.value,
        )

        payload = {
            "action": "mitigation",
            "chain_id": message.chain_id,
            "protocol": message.protocol,
            "user": message.user,
            "debt_asset": message.debt_asset,
            "debt_amount": str(message.debt_amount),
            "collateral_asset": message.collateral_asset,
            "liquidity_provider": message.liquidity_provider,
            "max_gas_price": str(message.max_gas_price),
            "executor": message.executor_address,
            "messaging": message.messaging_protocol.value,
        }

        await self._workers[message.chain_id].put(payload)
        self.stats["messages_dispatched"] += 1
        return True

    # ── Incentive Aggregation (Module 5) ─────────────────────────────

    def record_incentive(
        self,
        chain_id: int,
        asset: str,
        amount: int,
        value_usd: float,
    ):
        """Record an incentive collected from a mitigation action."""
        self._incentives.append(IncentiveRecord(
            chain_id=chain_id,
            asset=asset,
            amount=amount,
            value_usd=value_usd,
            timestamp=time.time(),
        ))
        self.stats["incentives_collected_usd"] += value_usd
        logger.info(
            "💰 Incentive recorded: chain=%d $%.2f (total: $%.2f)",
            chain_id, value_usd, self.stats["incentives_collected_usd"],
        )

    def get_pending_incentives(self) -> Dict[int, float]:
        """Get unsewpt incentive totals per chain."""
        by_chain: Dict[int, float] = defaultdict(float)
        for inc in self._incentives:
            if not inc.swept_to_treasury:
                by_chain[inc.chain_id] += inc.value_usd
        return dict(by_chain)

    async def sweep_incentives_to_treasury(
        self,
        min_sweep_usd: float = 50.0,
        target_chain: int = 1,
        target_asset: str = "USDC",
    ) -> Dict[str, Any]:
        """
        Aggregate incentives from all chains to treasury on target chain.

        Incentives below min_sweep_usd are held for batching.
        Larger amounts are bridged + swapped to target_asset (USDC).
        """
        pending = self.get_pending_incentives()
        sweep_result = {
            "chains_swept": 0,
            "total_swept_usd": 0.0,
            "held_for_batching_usd": 0.0,
        }

        for chain_id, total_usd in pending.items():
            if chain_id == target_chain:
                # Same chain: just swap to target asset, no bridge needed
                if total_usd >= min_sweep_usd:
                    await self._workers[chain_id].put({
                        "action": "sweep_to_treasury",
                        "chain_id": chain_id,
                        "target_asset": target_asset,
                        "amount_usd": total_usd,
                    })
                    self._mark_incentives_swept(chain_id)
                    sweep_result["chains_swept"] += 1
                    sweep_result["total_swept_usd"] += total_usd
                else:
                    sweep_result["held_for_batching_usd"] += total_usd
            else:
                # Different chain: bridge + swap
                if total_usd >= min_sweep_usd:
                    best_bridge = self._find_best_bridge(chain_id, target_chain)
                    if best_bridge:
                        bridge_fee = total_usd * BRIDGE_CONFIGS[best_bridge]["fee_pct"]
                        net_after_bridge = total_usd - bridge_fee
                        if net_after_bridge > min_sweep_usd * 0.5:
                            await self._workers[chain_id].put({
                                "action": "bridge_sweep",
                                "source_chain": chain_id,
                                "target_chain": target_chain,
                                "bridge": best_bridge.value,
                                "target_asset": target_asset,
                                "amount_usd": total_usd,
                            })
                            self._mark_incentives_swept(chain_id)
                            sweep_result["chains_swept"] += 1
                            sweep_result["total_swept_usd"] += net_after_bridge
                            self.stats["bridges_used"] += 1
                else:
                    sweep_result["held_for_batching_usd"] += total_usd

        self.stats["incentive_sweeps"] += 1
        logger.info(
            "🧹 Incentive sweep: %d chains, $%.2f swept, $%.2f held",
            sweep_result["chains_swept"],
            sweep_result["total_swept_usd"],
            sweep_result["held_for_batching_usd"],
        )
        return sweep_result

    def _mark_incentives_swept(self, chain_id: int):
        """Mark all pending incentives on a chain as swept."""
        for inc in self._incentives:
            if inc.chain_id == chain_id and not inc.swept_to_treasury:
                inc.swept_to_treasury = True


def get_orchestrator(config=None) -> CrossChainOrchestrator:
    return CrossChainOrchestrator(config)

__all__ = ["CrossChainOrchestrator", "get_orchestrator", "BridgeProvider",
           "CrossChainExitRoute", "CrossChainLiquidation", "CrossChainMessage",
           "IncentiveRecord", "MessagingProtocol", "MESSAGING_CONFIGS"]
