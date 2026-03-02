#!/usr/bin/env python3
"""
MODULE 1 — Module 5: Cross-Chain Orchestrator
Manages liquidation execution across all supported chains from a single control plane.

Architecture:
- Central Coordinator monitors all chains via the detection system
- For each profitable opportunity, constructs cross-chain liquidation parameters
- Routes execution to the target chain's LiquidationExecutor contract
- Aggregates profits back to the configured treasury

Supports:
- Direct execution on target chain (same-chain)
- Cross-chain messaging via LayerZero / Chainlink CCIP (future)
- Profit aggregation and automatic stablecoin conversion
"""

import asyncio
import json
import logging
import os
import time
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, field
from enum import Enum

from web3 import Web3

from ..config_manager import ConfigManager, get_config
from ..executors.liquidation_executor import (
    LiquidationExecutor,
    LiquidationRequest,
    LiquidationResult,
    LiquidationProtocol,
)
from ..calculators.profitability_calculator import (
    ProfitabilityCalculator,
    get_calculator,
)

logger = logging.getLogger(__name__)


# ============================================================================
# DATA MODELS
# ============================================================================

class ExecutionMode(Enum):
    """How the liquidation is routed to the target chain"""
    DIRECT = "direct"               # Same-chain: call executor directly
    LAYERZERO = "layerzero"         # Cross-chain via LayerZero
    CCIP = "ccip"                   # Cross-chain via Chainlink CCIP
    WORMHOLE = "wormhole"           # Cross-chain via Wormhole


@dataclass
class CrossChainMessage:
    """Standardised cross-chain liquidation message"""
    chain_id: int
    protocol: str
    user: str
    debt_asset: str
    debt_amount: int
    collateral_asset: str
    flash_loan_provider: str
    max_gas_price: int
    timestamp: int = 0

    def __post_init__(self):
        if self.timestamp == 0:
            self.timestamp = int(time.time())

    def to_json(self) -> str:
        return json.dumps(self.__dict__)

    @classmethod
    def from_json(cls, raw: str) -> "CrossChainMessage":
        return cls(**json.loads(raw))


@dataclass
class ChainStatus:
    """Runtime status for a monitored chain"""
    chain_id: int
    name: str
    connected: bool = False
    last_block: int = 0
    last_scan_time: float = 0
    opportunities_found: int = 0
    liquidations_executed: int = 0
    total_profit_usd: float = 0.0
    error_count: int = 0


# ============================================================================
# CROSS-CHAIN ORCHESTRATOR
# ============================================================================

class CrossChainOrchestrator:
    """
    Central control plane that:
      1. Monitors all configured chains for liquidation opportunities
      2. Routes execution to target chain's LiquidationExecutor
      3. Collects and aggregates profits to treasury
    """

    def __init__(self, config: Optional[ConfigManager] = None):
        self.config = config or get_config()
        self.executor = LiquidationExecutor(self.config)
        self.calculator: ProfitabilityCalculator = get_calculator()

        # Per-chain state
        self._chain_status: Dict[int, ChainStatus] = {}

        # Web3 providers
        self._w3_providers: Dict[int, Web3] = {}

        # Execution queue (chain_id → asyncio.Queue)
        self._queues: Dict[int, asyncio.Queue] = {}

        # Aggregated stats
        self.stats = {
            "chains_active": 0,
            "total_opportunities": 0,
            "total_liquidations": 0,
            "total_profit_usd": 0.0,
            "start_time": time.time(),
        }

        logger.info("CrossChainOrchestrator initialized")

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def initialize(self):
        """Connect to all configured chains and prepare execution queues"""
        chains = self.config.get_all_chains()

        for chain_id, chain_cfg in chains.items():
            if not chain_cfg.rpc_url or "YOUR_API_KEY" in chain_cfg.rpc_url:
                logger.warning(f"Skipping chain {chain_id} ({chain_cfg.name}) — RPC not configured")
                continue

            try:
                w3 = Web3(Web3.HTTPProvider(chain_cfg.rpc_url))
                block = w3.eth.block_number

                self._w3_providers[chain_id] = w3
                self._queues[chain_id] = asyncio.Queue()
                self._chain_status[chain_id] = ChainStatus(
                    chain_id=chain_id,
                    name=chain_cfg.name,
                    connected=True,
                    last_block=block,
                )
                self.stats["chains_active"] += 1

                logger.info(f"✅ Chain {chain_id} ({chain_cfg.name}) connected — block {block}")

            except Exception as e:
                self._chain_status[chain_id] = ChainStatus(
                    chain_id=chain_id,
                    name=chain_cfg.name,
                    connected=False,
                )
                logger.error(f"❌ Chain {chain_id} ({chain_cfg.name}) connection failed: {e}")

        logger.info(f"Orchestrator ready — {self.stats['chains_active']} chains active")

    async def shutdown(self):
        """Graceful shutdown"""
        logger.info("Shutting down CrossChainOrchestrator…")
        # Drain queues
        for chain_id, q in self._queues.items():
            while not q.empty():
                try:
                    q.get_nowait()
                except asyncio.QueueEmpty:
                    break
        logger.info("✅ Orchestrator stopped")

    # ------------------------------------------------------------------
    # Main run loop
    # ------------------------------------------------------------------

    async def start(self):
        """Start orchestration across all chains"""
        logger.info("\n" + "=" * 70)
        logger.info("  CROSS-CHAIN ORCHESTRATOR — STARTING")
        logger.info("=" * 70)

        if not self._w3_providers:
            logger.error("No chains connected — cannot start")
            return

        tasks: List[asyncio.Task] = []

        # Per-chain execution workers
        for chain_id in self._w3_providers:
            tasks.append(asyncio.create_task(self._chain_worker(chain_id)))

        # Global status reporter
        tasks.append(asyncio.create_task(self._status_reporter()))

        await asyncio.gather(*tasks)

    # ------------------------------------------------------------------
    # Enqueue opportunity (called externally by Opportunity Detector)
    # ------------------------------------------------------------------

    async def submit_opportunity(self, message: CrossChainMessage):
        """Submit a liquidation opportunity to the appropriate chain queue"""
        chain_id = message.chain_id
        q = self._queues.get(chain_id)
        if not q:
            logger.warning(f"No queue for chain {chain_id} — dropping opportunity")
            return

        await q.put(message)
        self.stats["total_opportunities"] += 1

        status = self._chain_status.get(chain_id)
        if status:
            status.opportunities_found += 1

        logger.info(
            f"📥 Opportunity queued — chain {chain_id} | "
            f"protocol {message.protocol} | user {message.user[:12]}…"
        )

    # ------------------------------------------------------------------
    # Per-chain worker
    # ------------------------------------------------------------------

    async def _chain_worker(self, chain_id: int):
        """Process liquidation queue for a single chain"""
        status = self._chain_status[chain_id]
        logger.info(f"🔄 Worker started for chain {chain_id} ({status.name})")

        while True:
            try:
                msg: CrossChainMessage = await asyncio.wait_for(
                    self._queues[chain_id].get(), timeout=30.0
                )

                logger.info(
                    f"\n{'='*60}\n"
                    f"⚡ EXECUTING on chain {chain_id} ({status.name})\n"
                    f"   Protocol: {msg.protocol}\n"
                    f"   User: {msg.user}\n"
                    f"   Debt: {msg.debt_amount}\n"
                    f"{'='*60}"
                )

                # Map protocol string to enum
                protocol_map = {
                    "aave_v3": LiquidationProtocol.AAVE_V3,
                    "aave_v2": LiquidationProtocol.AAVE_V2,
                    "compound_v2": LiquidationProtocol.COMPOUND_V2,
                    "compound_v3": LiquidationProtocol.COMPOUND_V3,
                    "maker_dao": LiquidationProtocol.MAKER_DAO,
                }
                protocol = protocol_map.get(msg.protocol, LiquidationProtocol.AAVE_V3)

                request = LiquidationRequest(
                    chain_id=chain_id,
                    protocol=protocol,
                    user=msg.user,
                    debt_asset=msg.debt_asset,
                    debt_amount=msg.debt_amount,
                    collateral_asset=msg.collateral_asset,
                    max_gas_price=msg.max_gas_price,
                )

                result: LiquidationResult = await self.executor.execute(request)

                if result.success:
                    status.liquidations_executed += 1
                    status.total_profit_usd += result.profit_usd
                    self.stats["total_liquidations"] += 1
                    self.stats["total_profit_usd"] += result.profit_usd

                    logger.info(
                        f"🎉 Chain {chain_id} — Liquidation succeeded! "
                        f"Profit ${result.profit_usd:.2f} | TX: {result.tx_hash}"
                    )
                else:
                    status.error_count += 1
                    logger.error(
                        f"❌ Chain {chain_id} — Liquidation failed: {result.error_message}"
                    )

                status.last_scan_time = time.time()

            except asyncio.TimeoutError:
                # No opportunities in queue — just continue
                pass
            except Exception as e:
                status.error_count += 1
                logger.error(f"Chain {chain_id} worker error: {e}")
                await asyncio.sleep(5)

    # ------------------------------------------------------------------
    # Profit aggregation
    # ------------------------------------------------------------------

    async def aggregate_profits(self) -> Dict[int, float]:
        """
        Query treasury balances across all active chains.
        Returns {chain_id: balance_eth}.
        """
        treasury = self.config.treasury_address
        balances: Dict[int, float] = {}

        for chain_id, w3 in self._w3_providers.items():
            try:
                bal = w3.eth.get_balance(Web3.to_checksum_address(treasury))
                balances[chain_id] = bal / 10**18
            except Exception as e:
                logger.error(f"Balance check failed on chain {chain_id}: {e}")
                balances[chain_id] = -1

        return balances

    # ------------------------------------------------------------------
    # Status
    # ------------------------------------------------------------------

    async def _status_reporter(self):
        """Periodically log orchestrator status"""
        while True:
            await asyncio.sleep(60)
            uptime = time.time() - self.stats["start_time"]

            logger.info(
                f"\n📊 ORCHESTRATOR STATUS — "
                f"Chains: {self.stats['chains_active']} | "
                f"Opportunities: {self.stats['total_opportunities']} | "
                f"Liquidations: {self.stats['total_liquidations']} | "
                f"Profit: ${self.stats['total_profit_usd']:.2f} | "
                f"Uptime: {uptime/3600:.1f}h"
            )

            for chain_id, status in self._chain_status.items():
                if status.connected:
                    logger.info(
                        f"   Chain {chain_id} ({status.name}): "
                        f"opps={status.opportunities_found} "
                        f"exec={status.liquidations_executed} "
                        f"profit=${status.total_profit_usd:.2f} "
                        f"errors={status.error_count}"
                    )

    def get_stats(self) -> Dict:
        uptime = time.time() - self.stats["start_time"]
        return {
            **self.stats,
            "uptime_seconds": uptime,
            "chains": {
                cid: {
                    "name": s.name,
                    "connected": s.connected,
                    "opportunities": s.opportunities_found,
                    "liquidations": s.liquidations_executed,
                    "profit_usd": s.total_profit_usd,
                    "errors": s.error_count,
                }
                for cid, s in self._chain_status.items()
            },
        }

    def print_status(self):
        """Print human-readable status"""
        stats = self.get_stats()
        print("\n" + "=" * 80)
        print("  CROSS-CHAIN ORCHESTRATOR STATUS")
        print("=" * 80)
        print(f"  Active Chains:    {stats['chains_active']}")
        print(f"  Opportunities:    {stats['total_opportunities']}")
        print(f"  Liquidations:     {stats['total_liquidations']}")
        print(f"  Total Profit:     ${stats['total_profit_usd']:.2f}")
        print(f"  Uptime:           {stats['uptime_seconds']/3600:.2f}h")
        print()
        for cid, chain in stats["chains"].items():
            icon = "✅" if chain["connected"] else "❌"
            print(
                f"  {icon} {chain['name']} (ID {cid}): "
                f"opps={chain['opportunities']} exec={chain['liquidations']} "
                f"profit=${chain['profit_usd']:.2f}"
            )
        print("=" * 80)


# ============================================================================
# GLOBAL INSTANCE
# ============================================================================

_orchestrator: Optional[CrossChainOrchestrator] = None


def get_orchestrator() -> CrossChainOrchestrator:
    global _orchestrator
    if _orchestrator is None:
        _orchestrator = CrossChainOrchestrator()
    return _orchestrator


async def main():
    orchestrator = get_orchestrator()
    try:
        await orchestrator.initialize()
        await orchestrator.start()
    except KeyboardInterrupt:
        logger.info("Interrupted")
    finally:
        await orchestrator.shutdown()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())
