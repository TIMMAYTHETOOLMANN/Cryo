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

    # Fallback quality score used when the scorer is unavailable or raises.
    # Set to 0.5 (neutral) so the signal is neither boosted nor penalised.
    _NEUTRAL_QUALITY_SCORE: float = 0.5

    # Multiplier that maps net_profit_usd → urgency_score (capped at 100).
    # $50 net profit → urgency 100; lower profits scale linearly.
    _URGENCY_PER_USD: float = 2.0

    # Conservative gas estimate (units) used when scoring a signal whose
    # true gas cost is unknown — chosen to represent a typical cross-chain arb.
    _DEFAULT_GAS_ESTIMATE: int = 300_000

    # Conservative gas price (Gwei) used when building a scoring proxy signal.
    _DEFAULT_GAS_PRICE_GWEI: int = 30

    # Default latency budget (ms) for cross-chain signals; backruns use a
    # tighter budget derived from urgency_score in _apply_quality_scoring.
    _DEFAULT_LATENCY_MS: int = 500

    def __init__(self, config: Optional[ConfigManager] = None):
        self.config = config or get_config()
        self._initialized = False
        self._orchestrator = None
        self._quality_scorer = None
        self._pathfinder = None
        self._execution_manager = None
        self._bridge_monitor = None
        self._mev_matcher = None

        self.stats = {
            "signals_received": 0,
            "signals_converted": 0,
            "signals_quality_filtered": 0,
            "arb_paths_found": 0,
            "backruns_detected": 0,
            "new_protocols_found": 0,
            "external_signals_ingested": 0,
        }

        # Lower thresholds — $2 minimum
        self.min_profit_usd = float(os.getenv("ARB_MIN_PROFIT_USD", "2"))
        # Minimum quality score gate (0-1); signals below this are dropped
        self.min_quality_score = float(os.getenv("OMNI_MIN_QUALITY_SCORE", "0.2"))

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

        # 5. MEV pattern matcher (static analysis — flags sandwich / backrun patterns)
        try:
            from omni_channel.static_analyzer.mev_patterns import MEVPatternMatcher
            self._mev_matcher = MEVPatternMatcher()
            logger.info("  ✅ MEV Pattern Matcher ready")
        except Exception as e:
            logger.debug(f"  ⚠ MEV Pattern Matcher not available: {e}")

        active = sum(1 for x in [self._pathfinder, self._quality_scorer,
                                  self._execution_manager, self._bridge_monitor,
                                  self._mev_matcher] if x)
        logger.info(f"🌐 Omni-Channel Bridge: {active}/5 subsystems active, min profit ${self.min_profit_usd}")
        self._initialized = True

    async def scan_once(self) -> List[OmniSignal]:
        """
        Run one scan cycle across all omni_channel subsystems.
        Returns a quality-scored, profit-filtered list of signals.

        Pipeline:
          1. Collect raw signals from pathfinder + bridge monitor
          2. Apply ML quality scoring (if scorer is available) to rank signals
             and drop those below ``min_quality_score``
          3. Sort survivors by quality score descending (best first)
          4. Apply final profit floor filter
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

        # 3. ML Quality Scoring — rank signals and drop low-quality ones
        if self._quality_scorer and signals:
            signals = self._apply_quality_scoring(signals)

        # 4. Final profit floor filter
        profitable = [s for s in signals if s.net_profit_usd >= self.min_profit_usd]
        self.stats["signals_converted"] += len(profitable)

        if profitable:
            logger.info(
                f"🌐 Omni-Channel: {len(profitable)} profitable signals "
                f"(scanned: {self.stats['signals_received']}, "
                f"quality-filtered: {self.stats['signals_quality_filtered']}, "
                f"min: ${self.min_profit_usd})"
            )

        return profitable

    # ------------------------------------------------------------------
    # Quality scoring helper
    # ------------------------------------------------------------------

    def _apply_quality_scoring(self, signals: List[OmniSignal]) -> List[OmniSignal]:
        """
        Run each signal through the ML quality scorer.

        Converts each OmniSignal to a minimal OpportunitySignal for scoring,
        attaches the score as signal.metadata["quality_score"], drops signals
        below ``min_quality_score``, and returns survivors sorted best-first.
        """
        try:
            from omni_channel.data_lake.data_models import OpportunitySignal, SignalType, SignalSource
        except ImportError:
            return signals  # scorer not available — pass through unchanged

        scored_pairs = []
        for sig in signals:
            try:
                # Map execution_type → SignalType
                type_map = {
                    "cross_chain": SignalType.CROSS_CHAIN_ARB,
                    "arbitrage": SignalType.ARBITRAGE,
                    "backrun": SignalType.BACKRUN,
                    "liquidation": SignalType.LIQUIDATION,
                }
                signal_type = type_map.get(sig.execution_type, SignalType.ARBITRAGE)

                opp = OpportunitySignal(
                    signal_type=signal_type,
                    source_module=SignalSource.CROSS_CHAIN_MONITOR,
                    chain_id=sig.chain_id,
                    expected_value_usd=sig.profit_usd,
                    net_profit_usd=sig.net_profit_usd,
                    estimated_cost_usd=sig.gas_cost_usd,
                    confidence=sig.confidence,
                    urgency_score=min(100, sig.net_profit_usd * self._URGENCY_PER_USD),
                    execution_complexity=sig.metadata.get("hops", 3)
                    if isinstance(sig.metadata.get("hops"), int)
                    else 3,
                    gas_estimate=self._DEFAULT_GAS_ESTIMATE,
                    gas_price_gwei=self._DEFAULT_GAS_PRICE_GWEI,
                    latency_requirement_ms=self._DEFAULT_LATENCY_MS,
                    expiry_block=0,
                )
                scored = self._quality_scorer.score(opp)
                scored_pairs.append((sig, scored.quality_score))
            except Exception as e:
                logger.debug(f"Quality scoring error for signal: {e}")
                # Keep with neutral score so it still gets the profit filter
                scored_pairs.append((sig, self._NEUTRAL_QUALITY_SCORE))

        # Drop signals below quality threshold
        before = len(scored_pairs)
        scored_pairs = [(s, q) for s, q in scored_pairs if q >= self.min_quality_score]
        dropped = before - len(scored_pairs)
        self.stats["signals_quality_filtered"] += dropped
        if dropped:
            logger.debug(f"Quality filter: dropped {dropped}/{before} signals (threshold={self.min_quality_score})")

        # Sort best-first and annotate signal metadata
        scored_pairs.sort(key=lambda x: x[1], reverse=True)
        result = []
        for sig, score in scored_pairs:
            sig.metadata["quality_score"] = score
            result.append(sig)
        return result

    # ------------------------------------------------------------------
    # External signal ingestion (mempool radar, static analyzer)
    # ------------------------------------------------------------------

    def ingest_opportunity_signal(self, signal) -> Optional[OmniSignal]:
        """
        Convert an omni_channel ``OpportunitySignal`` (from the mempool radar
        or static analyzer) into an ``OmniSignal`` and apply quality scoring.

        Returns the converted signal if it passes both the quality gate and
        the profit floor; returns None otherwise.  The caller is responsible
        for feeding the return value into the pipeline.
        """
        if not self._initialized:
            return None

        try:
            exec_map = {
                "liquidation": "liquidation",
                "arbitrage": "arbitrage",
                "cross_chain_arb": "cross_chain",
                "backrun": "backrun",
                "sandwich": "backrun",
                "oracle_update": "liquidation",
                "large_swap": "backrun",
            }
            exec_type = exec_map.get(
                signal.signal_type.value if hasattr(signal.signal_type, "value") else str(signal.signal_type),
                "arbitrage",
            )

            # Quality gate (if scorer available)
            quality_score = self._NEUTRAL_QUALITY_SCORE
            if self._quality_scorer:
                try:
                    scored = self._quality_scorer.score(signal)
                    quality_score = scored.quality_score
                    if quality_score < self.min_quality_score:
                        self.stats["signals_quality_filtered"] += 1
                        return None
                except Exception:
                    pass

            net = getattr(signal, "net_profit_usd", 0.0) or (
                getattr(signal, "expected_value_usd", 0.0)
                - getattr(signal, "estimated_cost_usd", 0.0)
            )
            if net < self.min_profit_usd:
                return None

            omni = OmniSignal(
                source=getattr(signal, "source_module", "external").value
                if hasattr(getattr(signal, "source_module", None), "value")
                else str(getattr(signal, "source_module", "external")),
                chain_id=getattr(signal, "chain_id", 1),
                profit_usd=getattr(signal, "expected_value_usd", 0.0),
                gas_cost_usd=getattr(signal, "estimated_cost_usd", 0.0),
                net_profit_usd=net,
                confidence=getattr(signal, "confidence", 0.5),
                execution_type=exec_type,
                metadata={
                    **getattr(signal, "metadata", {}),
                    "quality_score": quality_score,
                    "signal_id": getattr(signal, "signal_id", ""),
                },
                timestamp=int(time.time()),
            )
            self.stats["external_signals_ingested"] += 1
            return omni

        except Exception as e:
            logger.debug(f"ingest_opportunity_signal error: {e}")
            return None

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
            "mev_matcher_active": self._mev_matcher is not None,
        }
