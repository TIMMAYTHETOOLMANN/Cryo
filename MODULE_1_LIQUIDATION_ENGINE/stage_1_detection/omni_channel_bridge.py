#!/usr/bin/env python3
"""
OMNI-CHANNEL BRIDGE — Wires omni_channel strategies into Module 1 pipeline
=============================================================================
Routes opportunity signals from multiple sources:
  1. omni_channel.cross_chain_monitor — N-hop cross-chain arb paths
  2. omni_channel.mempool_radar — backrun & sandwich signals
  3. omni_channel.contract_crawler — new protocol discovery
  4. omni_channel.ml_aggregator — quality-scored + ranked signals

Converts omni_channel signals → LiquidatablePosition format for
unified pipeline processing through the same gas gate and execution stages.
"""

import asyncio
import logging
import os
import time
from typing import Dict, List, Optional
from dataclasses import dataclass
from web3 import Web3

from ..config.settings import ConfigManager, get_config

logger = logging.getLogger(__name__)


@dataclass
class OmniSignal:
    """Unified signal from any omni_channel source."""
    source: str              # e.g. "cross_chain_arb", "backrun", "contract_crawler"
    chain_id: int
    profit_usd: float
    gas_cost_usd: float
    net_profit_usd: float
    confidence: float        # 0-1
    execution_type: str      # "arbitrage", "backrun", "liquidation", "cross_chain"
    metadata: Dict           # source-specific data
    timestamp: int = 0


class OmniChannelBridge:
    """
    Bridge between omni_channel modules and MODULE_1 pipeline.

    Initializes and runs omni_channel subsystems, converts their
    signals into pipeline-compatible format, and feeds them into
    Stage 1 detection alongside liquidation scanning.
    """

    def __init__(self, config: Optional[ConfigManager] = None):
        self.config = config or get_config()
        self._initialized = False
        self._orchestrator = None
        self._quality_scorer = None
        self._pathfinder = None
        self._execution_manager = None
        self._bridge_monitor = None

        self.stats = {
            "signals_received": 0,
            "signals_converted": 0,
            "arb_paths_found": 0,
            "backruns_detected": 0,
            "new_protocols_found": 0,
        }

        # Lower thresholds — $2 minimum
        self.min_profit_usd = float(os.getenv("ARB_MIN_PROFIT_USD", "2"))

    async def initialize(self):
        """Initialize omni_channel subsystems."""
        logger.info("🌐 Omni-Channel Bridge: Initializing subsystems...")

        # Try to import and initialize each subsystem independently
        # so partial failures don't block everything

        # 1. Cross-chain N-hop pathfinder
        try:
            from omni_channel.cross_chain_monitor.multi_chain_graph import MultiChainGraph
            from omni_channel.cross_chain_monitor.n_hop_pathfinder import NHopPathfinder

            graph = MultiChainGraph()
            self._pathfinder = NHopPathfinder(graph, config={
                'min_profit_usd': self.min_profit_usd,
                'max_hops': 4,
            })
            logger.info("  ✅ N-Hop Pathfinder ready")
        except Exception as e:
            logger.debug(f"  ⚠ N-Hop Pathfinder not available: {e}")

        # 2. Quality scorer for ranking signals
        try:
            from omni_channel.ml_aggregator.quality_scorer import QualityScorer
            self._quality_scorer = QualityScorer(config={
                'min_quality_score': 0.2,  # Low threshold — catch more
            })
            logger.info("  ✅ Quality Scorer ready")
        except Exception as e:
            logger.debug(f"  ⚠ Quality Scorer not available: {e}")

        # 3. Execution manager (arb, backrun, cross-chain executors)
        try:
            from omni_channel.execution_router.execution_manager import ExecutionManager
            self._execution_manager = ExecutionManager(config={
                'arbitrage': {'min_profit_usd': self.min_profit_usd},
                'backrun': {'min_profit_usd': self.min_profit_usd},
                'cross_chain': {'min_profit_usd': self.min_profit_usd},
            })
            await self._execution_manager.start()
            logger.info("  ✅ Execution Manager ready (arb + backrun + cross-chain)")
        except Exception as e:
            logger.debug(f"  ⚠ Execution Manager not available: {e}")

        # 4. Bridge monitor for cross-chain price discrepancies
        try:
            from omni_channel.cross_chain_monitor.bridge_registry import BridgeRegistry
            self._bridge_monitor = BridgeRegistry()
            logger.info("  ✅ Bridge Registry ready")
        except Exception as e:
            logger.debug(f"  ⚠ Bridge Registry not available: {e}")

        active = sum(1 for x in [self._pathfinder, self._quality_scorer,
                                  self._execution_manager, self._bridge_monitor] if x)
        logger.info(f"🌐 Omni-Channel Bridge: {active}/4 subsystems active, min profit ${self.min_profit_usd}")
        self._initialized = True

    async def scan_once(self) -> List[OmniSignal]:
        """
        Run one scan cycle across all omni_channel subsystems.
        Returns a list of signals that can be converted to pipeline positions.
        """
        if not self._initialized:
            return []

        signals: List[OmniSignal] = []

        # 1. Cross-chain arb paths
        if self._pathfinder:
            try:
                arb_signals = await self._scan_cross_chain_arbs()
                signals.extend(arb_signals)
            except Exception as e:
                logger.debug(f"Cross-chain scan error: {e}")

        # 2. Bridge price discrepancies
        if self._bridge_monitor:
            try:
                bridge_signals = await self._scan_bridge_discrepancies()
                signals.extend(bridge_signals)
            except Exception as e:
                logger.debug(f"Bridge scan error: {e}")

        self.stats["signals_received"] += len(signals)

        # Filter by min profit
        profitable = [s for s in signals if s.net_profit_usd >= self.min_profit_usd]
        self.stats["signals_converted"] += len(profitable)

        if profitable:
            logger.info(
                f"🌐 Omni-Channel: {len(profitable)} profitable signals "
                f"(total scanned: {len(signals)}, min: ${self.min_profit_usd})"
            )

        return profitable

    async def _scan_cross_chain_arbs(self) -> List[OmniSignal]:
        """Scan for N-hop cross-chain arbitrage paths."""
        signals = []

        if not self._pathfinder:
            return signals

        try:
            # Scan for arb paths starting from common tokens
            for start_token in ["WETH", "USDC"]:
                for start_chain in [1, 42161, 10, 8453, 137]:
                    result = self._pathfinder.find_all_arbitrage_paths(
                        start_token=start_token,
                        start_chain=start_chain,
                        amount_usd=5000,
                    )
                    for path in (result.profitable_paths if result else []):
                        self.stats["arb_paths_found"] += 1
                        signals.append(OmniSignal(
                            source="cross_chain_arb",
                            chain_id=path.start_chain,
                            profit_usd=path.profit_amount,
                            gas_cost_usd=path.total_gas_usd,
                            net_profit_usd=path.profit_amount - path.total_gas_usd - path.total_fees,
                            confidence=max(0, 1 - path.risk_score),
                            execution_type="cross_chain",
                            metadata={
                                "path_id": path.path_id,
                                "hops": path.hops,
                                "bridges": path.bridges_used,
                                "protocols": path.protocols_used,
                                "input_amount": path.input_amount,
                                "output_amount": path.output_amount,
                            },
                            timestamp=int(time.time()),
                        ))
        except Exception as e:
            logger.debug(f"Cross-chain arb scan error: {e}")

        return signals

    async def _scan_bridge_discrepancies(self) -> List[OmniSignal]:
        """Scan for bridge-based price discrepancies."""
        signals = []

        if not self._bridge_monitor:
            return signals

        try:
            # Check for same-token price differences across chains
            if hasattr(self._bridge_monitor, 'get_price_discrepancies'):
                discrepancies = self._bridge_monitor.get_price_discrepancies(
                    min_diff_percent=0.1  # 0.1% minimum
                )
                for disc in (discrepancies or []):
                    signals.append(OmniSignal(
                        source="bridge_arb",
                        chain_id=disc.get("cheap_chain", 1),
                        profit_usd=disc.get("profit_usd", 0),
                        gas_cost_usd=disc.get("gas_usd", 0),
                        net_profit_usd=disc.get("net_usd", 0),
                        confidence=0.7,
                        execution_type="arbitrage",
                        metadata=disc,
                        timestamp=int(time.time()),
                    ))
        except Exception as e:
            logger.debug(f"Bridge scan error: {e}")

        return signals

    def get_execution_manager(self):
        """Return the omni_channel execution manager for direct dispatch."""
        return self._execution_manager

    def get_stats(self) -> Dict:
        return {
            **self.stats,
            "initialized": self._initialized,
            "pathfinder_active": self._pathfinder is not None,
            "scorer_active": self._quality_scorer is not None,
            "exec_mgr_active": self._execution_manager is not None,
            "bridge_active": self._bridge_monitor is not None,
        }
