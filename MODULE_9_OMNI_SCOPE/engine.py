#!/usr/bin/env python3
"""
MODULE 9 — Omni-Scope Triangulation Engine: Master Orchestrator
==================================================================
Wires all 5 detector arrays + support modules into a unified system.

Architecture:
  ┌──────────────────────────────────────────────────────────────┐
  │                   CENTRAL DATA BUS (Kafka-like)              │
  │                                                              │
  │  ┌─────────┐ ┌──────────┐ ┌──────────┐ ┌────────┐ ┌──────┐ │
  │  │ Array 1  │ │ Array 2  │ │ Array 3  │ │Array 4 │ │Archiv│ │
  │  │ Mempool  │ │ Contract │ │ Static   │ │ Bridge │ │Index │ │
  │  │ Radar    │ │ Crawler  │ │ Analyzer │ │Monitor │ │      │ │
  │  └────┬─────┘ └────┬─────┘ └────┬─────┘ └───┬────┘ └──┬───┘ │
  │       │            │            │            │         │     │
  │       └────────────┴────────────┴────────────┴─────────┘     │
  │                         ▼ signals ▼                          │
  │               ┌─────────────────────────┐                    │
  │               │ Array 5: ML Ranker      │                    │
  │               │ Quality Score → Route   │                    │
  │               └───────────┬─────────────┘                    │
  │                           ▼                                  │
  │               ┌─────────────────────────┐                    │
  │               │ Ranked Opportunity Queue │                    │
  │               └───────────┬─────────────┘                    │
  │                           ▼                                  │
  │               MODULE 1 Pipeline (consume_ranked)             │
  └──────────────────────────────────────────────────────────────┘

Integration with MODULE_1:
  - Pipeline calls omni_scope.consume() each cycle
  - Returns ranked OpportunitySignal list
  - Pipeline converts signals to LiquidatablePosition for execution
"""

import asyncio
import logging
import time
from typing import Dict, List, Optional

from .config import OmniScopeConfig, get_omni_config
from .data_bus import DataBus, OpportunitySignal
from .array_1_mempool_radar.mempool_radar import MempoolRadar
from .array_2_contract_crawler.contract_crawler import ContractCrawler
from .array_3_static_analysis.static_analyzer import StaticAnalyzer
from .array_4_cross_chain_monitor.bridge_monitor import BridgeMonitor
from .array_5_ml_ranker.opportunity_ranker import OpportunityRanker
from .archive_indexer.deep_crawl import ArchiveIndexer
from .alpha_seeker.sentiment_crawler import AlphaSeeker
from .zero_capital.bootstrap import ZeroCapitalBootstrap

logger = logging.getLogger(__name__)


class OmniScopeEngine:
    """
    Master orchestrator for the Omni-Scope Triangulation Engine.

    Manages all 5 detector arrays + support modules.
    Provides consume() API for MODULE_1 pipeline integration.
    """

    def __init__(self, config: Optional[OmniScopeConfig] = None, rpc_gateway=None):
        self.config = config or get_omni_config()

        # Central data bus
        self.bus = DataBus()

        # Module 10: Intelligent RPC Gateway — optional, enables managed rate
        # limiting, circuit breaking, and endpoint failover for all arrays.
        self.rpc_gateway = rpc_gateway

        # 5 Detector Arrays
        self.mempool_radar = MempoolRadar(self.bus, self.config, rpc_gateway=rpc_gateway)
        self.contract_crawler = ContractCrawler(self.bus, self.config)
        self.static_analyzer = StaticAnalyzer(self.bus, self.config)
        self.bridge_monitor = BridgeMonitor(self.bus, self.config)
        self.ml_ranker = OpportunityRanker(self.bus, self.config)

        # Support Modules
        self.archive_indexer = ArchiveIndexer(self.bus, self.config)
        self.alpha_seeker = AlphaSeeker(self.bus, self.config)
        self.bootstrap = ZeroCapitalBootstrap(self.config)

        # State
        self._running = False
        self._tasks: List[asyncio.Task] = []

        # Aggregate stats
        self._start_time = 0.0

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self):
        """Start all detector arrays and support modules."""
        self._running = True
        self._start_time = time.time()

        logger.info("\n" + "=" * 80)
        logger.info("  MODULE 9 — OMNI-SCOPE TRIANGULATION ENGINE")
        logger.info("  Predictive Omni-Directional Intelligence")
        logger.info("=" * 80)

        # Start all arrays as background tasks
        self._tasks = [
            asyncio.create_task(self.mempool_radar.start()),
            asyncio.create_task(self.contract_crawler.start()),
            asyncio.create_task(self.static_analyzer.start()),
            asyncio.create_task(self.bridge_monitor.start()),
            asyncio.create_task(self.archive_indexer.start()),
            asyncio.create_task(self.alpha_seeker.start()),
        ]

        logger.info(f"  ✅ {len(self._tasks)} detector arrays started")
        logger.info(f"  ✅ ML Ranker subscribed to DataBus")
        logger.info(f"  ✅ Zero-Capital Bootstrap ready")
        logger.info("=" * 80)

    async def stop(self):
        """Stop all arrays gracefully."""
        self._running = False

        await self.mempool_radar.stop()
        await self.contract_crawler.stop()
        await self.static_analyzer.stop()
        await self.bridge_monitor.stop()
        await self.archive_indexer.stop()
        await self.alpha_seeker.stop()

        tasks = getattr(self, "_tasks", None)
        if tasks:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

        logger.info("Module 9 Omni-Scope stopped.")

    # ------------------------------------------------------------------
    # Pipeline Integration API
    # ------------------------------------------------------------------

    def consume(self, limit: int = 10) -> List[OpportunitySignal]:
        """
        Consume top-ranked signals from the ML ranker.

        Called by MODULE_1 pipeline each cycle.
        Returns signals sorted by quality_score (best first).
        """
        return self.bus.consume_ranked(limit)

    def publish_external(self, signal: OpportunitySignal):
        """
        Publish a signal from an external source (e.g., MODULE_1 detector).
        Will be scored by the ML ranker and added to the ranked queue.
        """
        self.bus.publish(signal)

    def record_execution_outcome(
        self, signal: OpportunitySignal, profit: float, success: bool
    ):
        """
        Record execution outcome for ML model improvement.
        Called by MODULE_1 pipeline after execution attempt.
        """
        self.ml_ranker.record_outcome(signal, profit, success)

        # Track profit for gas bootstrapping
        if success and profit > 0:
            self.bootstrap.record_profit(signal.chain_id, profit)

            # Check if we should self-fund gas
            if self.bootstrap.should_self_fund():
                plan = self.bootstrap.create_funding_plan()
                if plan:
                    self.bootstrap.execute_funding(plan)

    # ------------------------------------------------------------------
    # Diagnostics
    # ------------------------------------------------------------------

    def get_full_stats(self) -> Dict:
        """Get comprehensive stats from all subsystems."""
        uptime = time.time() - self._start_time if self._start_time else 0

        stats = {
            "uptime_hours": uptime / 3600,
            "rpc_gateway_active": self.rpc_gateway is not None,
            "data_bus": self.bus.get_stats(),
            "array_1_mempool": self.mempool_radar.get_stats(),
            "array_2_crawler": self.contract_crawler.get_stats(),
            "array_3_analyzer": self.static_analyzer.get_stats(),
            "array_4_bridge": self.bridge_monitor.get_stats(),
            "array_5_ranker": self.ml_ranker.get_stats(),
            "archive_indexer": self.archive_indexer.get_stats(),
            "alpha_seeker": self.alpha_seeker.get_stats(),
            "bootstrap": self.bootstrap.get_stats(),
        }
        if self.rpc_gateway is not None:
            try:
                stats["rpc_gateway"] = self.rpc_gateway.get_stats()
            except Exception as exc:
                logger.warning("RPCGateway.get_stats() failed: %s", exc)
                stats["rpc_gateway"] = {"error": str(exc)}
        return stats

    def print_status(self):
        """Print comprehensive status report."""
        stats = self.get_full_stats()
        bus = stats["data_bus"]

        print()
        print("=" * 80)
        print("  MODULE 9 — OMNI-SCOPE STATUS REPORT")
        print("=" * 80)
        print(f"  Uptime:              {stats['uptime_hours']:.2f} hours")
        print(f"  Signals published:   {bus['total_published']}")
        print(f"  Signals consumed:    {bus['total_consumed']}")
        print(f"  Duplicates filtered: {bus['duplicates_filtered']}")
        print(f"  Ranked queue:        {bus['ranked_queue_size']}")
        print()
        print("  ─── Array Stats ───")
        for key in ["array_1_mempool", "array_2_crawler", "array_3_analyzer",
                     "array_4_bridge", "array_5_ranker"]:
            arr = stats[key]
            emitted = arr.get("signals_emitted", 0)
            print(f"  {key:25s}  signals={emitted}")
        print()
        print("  ─── Support Modules ───")
        ai = stats["archive_indexer"]
        print(f"  Archive Indexer:       {ai.get('subgraph_queries', 0)} queries, "
              f"{ai.get('serial_liquidatees_found', 0)} serial liquidatees")
        alpha = stats["alpha_seeker"]
        print(f"  Alpha Seeker:          {alpha.get('sentiment_spikes_detected', 0)} spikes, "
              f"{alpha.get('governance_proposals_tracked', 0)} proposals")
        boot = stats["bootstrap"]
        print(f"  Bootstrap:             ${boot.get('total_gas_funded_usd', 0):.2f} gas funded, "
              f"{boot.get('gas_self_fund_txs', 0)} TXs")
        print()
        print("  ─── ML Ranker ───")
        ml = stats["array_5_ranker"]
        print(f"  Scored:     {ml.get('signals_scored', 0)}")
        print(f"  Routed:     {ml.get('signals_routed', 0)}")
        print(f"  Filtered:   {ml.get('signals_filtered', 0)}")
        print(f"  Avg Score:  {ml.get('avg_quality_score', 0):.3f}")
        print(f"  Weights:    {ml.get('weights', {})}")
        if bus.get("by_type"):
            print()
            print("  ─── Signals by Type ───")
            for typ, count in sorted(bus["by_type"].items(), key=lambda x: -x[1]):
                print(f"    {typ:30s} {count}")
        print("=" * 80)
