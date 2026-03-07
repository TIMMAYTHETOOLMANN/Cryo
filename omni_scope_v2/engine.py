#!/usr/bin/env python3
"""
omni_scope_v2.engine — Unified Omni-Scope Triangulation Engine
================================================================
Master orchestrator that wires all 7 modules + data lake + signal bus
into a single, cohesive, self-bootstrapping system.

Architecture:

  ┌──────────────────────────────────────────────────────────────────┐
  │                      DATA LAKE                                   │
  │  Kafka ◆ Redis ◆ TimescaleDB ◆ Neo4j                            │
  └──────────┬───────────────────────────────────────────────────────┘
             │  real-time stream
  ┌──────────▼───────────────────────────────────────────────────────┐
  │              5 DETECTOR MODULES (Modules 1-5)                    │
  │                                                                  │
  │  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌────────┐│
  │  │  Mod 1   │ │  Mod 2   │ │  Mod 3   │ │  Mod 4   │ │ Mod 5  ││
  │  │ DeepCrawl│ │ Mempool  │ │  Hyper   │ │  Alpha   │ │ Static ││
  │  │ Indexer  │ │ Micro-   │ │  Solver  │ │  Seeker  │ │Analysis││
  │  │          │ │ scope    │ │          │ │          │ │        ││
  │  └────┬─────┘ └────┬─────┘ └────┬─────┘ └────┬─────┘ └───┬────┘│
  │       │            │            │            │           │      │
  │       └────────────┴────────────┴────────────┴───────────┘      │
  │                         ▼  signals  ▼                            │
  │  ┌────────────────────────────────────────────────────────────┐  │
  │  │              SIGNAL BUS (dedup + pub/sub)                  │  │
  │  └──────────────────────────┬─────────────────────────────────┘  │
  │                             ▼                                    │
  │  ┌────────────────────────────────────────────────────────────┐  │
  │  │      Module 6: ML AGGREGATOR & RANKER (The Brain)          │  │
  │  │      Score → Rank → Route → Priority Queue                 │  │
  │  └──────────────────────────┬─────────────────────────────────┘  │
  │                             ▼  top-N ranked                      │
  │  ┌────────────────────────────────────────────────────────────┐  │
  │  │      Module 7: ZERO-CAPITAL LAYER                          │  │
  │  │      Flash Loan → Execute → Repay → Gas Self-Fund          │  │
  │  └──────────────────────────┬─────────────────────────────────┘  │
  │                             ▼                                    │
  │  ┌────────────────────────────────────────────────────────────┐  │
  │  │      EXECUTION HAND-OFF → existing Pipeline / Bridge       │  │
  │  └────────────────────────────────────────────────────────────┘  │
  └──────────────────────────────────────────────────────────────────┘

Usage::

    engine = OmniScopeTriangulationEngine()
    await engine.start()
    # ... runs forever, consuming opportunities ...
    await engine.stop()

Integration with MasterOrchestrator::

    # In master_orchestrator.py _init_modules:
    from omni_scope_v2 import OmniScopeTriangulationEngine
    self._omni_v2 = OmniScopeTriangulationEngine(config)
    await self._omni_v2.start()
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Dict, List, Optional

from .config import OmniScopeV2Config, get_config
from .data_lake import DataLake
from .signal_bus import SignalBus, TriangulatedSignal

from .module_1_deep_crawl_indexer import DeepCrawlIndexer
from .module_2_mempool_microscope import MempoolMicroscope
from .module_3_hyper_solver import HyperSolver
from .module_4_alpha_seeker import AlphaSeekerCrawler
from .module_5_static_analysis import StaticAnalysisEngine
from .module_6_ml_aggregator import MLAggregator
from .module_7_zero_capital import ZeroCapitalLayer
from .module_8_execution_router import ExecutionRouter
from .module_9_performance_optimizer import PerformanceOptimizer
from .bridge_registry import BridgeRegistry
from .protocol_classifier import ProtocolClassifier

logger = logging.getLogger(__name__)


class OmniScopeTriangulationEngine:
    """
    Unified Omni-Scope Triangulation Engine v2.

    Orchestrates all 7 modules + data lake + signal bus in a single
    event loop.  Each module runs on its own cycle cadence while the
    engine manages lifecycle, diagnostics, and integration with the
    existing MasterOrchestrator / Pipeline.
    """

    VERSION = "2.0.0"

    def __init__(self, config: Optional[OmniScopeV2Config] = None):
        self.config = config or get_config()

        # ── Infrastructure ────────────────────────────────────
        self.lake = DataLake(self.config.data_lake)
        self.bus = SignalBus(self.config.signal_bus)

        # ── 7 Interlocking Modules ────────────────────────────
        self.deep_crawl = DeepCrawlIndexer(
            self.bus, self.lake, self.config.deep_crawl,
        )
        self.mempool = MempoolMicroscope(
            self.bus, self.lake, self.config.mempool_microscope,
        )
        self.hyper_solver = HyperSolver(
            self.bus, self.lake, self.config.hyper_solver,
        )
        self.alpha_seeker = AlphaSeekerCrawler(
            self.bus, self.lake, self.config.alpha_seeker,
        )
        self.static_analysis = StaticAnalysisEngine(
            self.bus, self.lake, self.config.static_analysis,
        )
        self.ml_aggregator = MLAggregator(
            self.bus, self.config.ml_aggregator,
        )
        self.zero_capital = ZeroCapitalLayer(
            self.bus, self.lake, self.config.zero_capital,
        )
        self.execution_router = ExecutionRouter(
            self.bus, self.config.execution_router,
        )
        self.performance = PerformanceOptimizer(
            self.config.performance,
        )
        self.bridge_registry = BridgeRegistry()
        self.protocol_classifier = ProtocolClassifier()

        # ── Engine State ──────────────────────────────────────
        self._running = False
        self._tasks: List[asyncio.Task] = []
        self._start_time = 0.0
        self._cycle_count = 0

        # Module cycle intervals (seconds)
        self._intervals = {
            "deep_crawl": 60.0,
            "mempool": 5.0,
            "hyper_solver": 30.0,
            "alpha_seeker": 30.0,
            "static_analysis": 60.0,
            "zero_capital": 15.0,
            "exec_router": 2.0,
            "performance": 30.0,
            "bus_gc": 120.0,
        }

    # ── Lifecycle ─────────────────────────────────────────────

    async def start(self):
        """Initialise all infrastructure and start module loops."""
        self._running = True
        self._start_time = time.time()

        logger.info("\n" + "=" * 80)
        logger.info("  OMNI-SCOPE TRIANGULATION ENGINE v%s", self.VERSION)
        logger.info("  Predictive · Omni-Directional · Zero-Capital")
        logger.info("=" * 80)

        # 1. Connect data lake backends
        availability = await self.lake.connect()
        logger.info("  Data Lake: %s", availability)

        # 2. Start all 7 modules
        await self.deep_crawl.start()
        logger.info("  [+] Module 1: Deep-Crawl Archive Indexer")

        await self.mempool.start()
        logger.info("  [+] Module 2: Mempool Microscope")

        await self.hyper_solver.start()
        logger.info("  [+] Module 3: Yield & Arbitrage Hyper-Solver")

        await self.alpha_seeker.start()
        logger.info("  [+] Module 4: Alpha-Seeker Sentiment Crawler")

        await self.static_analysis.start()
        logger.info("  [+] Module 5: Static Analysis Engine")

        await self.ml_aggregator.start()
        logger.info("  [+] Module 6: ML Aggregator & Ranker (The Brain)")

        await self.zero_capital.start()
        logger.info("  [+] Module 7: Zero-Capital Scalability Layer")

        await self.execution_router.start()
        logger.info("  [+] Module 8: Execution Router — Dynamic Strategy Dispatch")

        await self.performance.start()
        logger.info("  [+] Module 9: Performance Optimizer — %d chain pools",
                     len(self.performance._pools))

        logger.info("  [+] Bridge Registry: %d bridges, %d routes",
                     len(self.bridge_registry._bridges),
                     sum(len(r) for r in self.bridge_registry._routes.values()))
        logger.info("  [+] Protocol Classifier: ready")

        # 3. Launch async loops
        self._tasks = [
            asyncio.create_task(self._loop_deep_crawl(), name="deep_crawl"),
            asyncio.create_task(self._loop_mempool(), name="mempool"),
            asyncio.create_task(self._loop_hyper_solver(), name="hyper_solver"),
            asyncio.create_task(self._loop_alpha_seeker(), name="alpha_seeker"),
            asyncio.create_task(self._loop_static_analysis(), name="static_analysis"),
            asyncio.create_task(self._loop_zero_capital(), name="zero_capital"),
            asyncio.create_task(self._loop_execution_router(), name="exec_router"),
            asyncio.create_task(self._loop_performance(), name="performance"),
            asyncio.create_task(self._loop_bus_gc(), name="bus_gc"),
            asyncio.create_task(self._loop_diagnostics(), name="diagnostics"),
        ]

        logger.info("  %d module loops launched", len(self._tasks))
        logger.info("=" * 80)

    async def stop(self):
        """Gracefully shut down all modules and infrastructure."""
        logger.info("[OmniScopeV2] Shutting down...")
        self._running = False

        # Cancel tasks
        for task in self._tasks:
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()

        # Stop modules in reverse order
        await self.performance.stop()
        await self.execution_router.stop()
        await self.zero_capital.stop()
        await self.static_analysis.stop()
        await self.alpha_seeker.stop()
        await self.hyper_solver.stop()
        await self.mempool.stop()
        await self.deep_crawl.stop()
        await self.ml_aggregator.stop()

        # Close data lake
        await self.lake.close()

        uptime = time.time() - self._start_time
        logger.info(
            "[OmniScopeV2] Stopped — %.1f hours uptime, %d cycles",
            uptime / 3600, self._cycle_count,
        )

    async def run(self) -> int:
        """Start engine and run until interrupted."""
        try:
            await self.start()
            done, _ = await asyncio.wait(
                self._tasks, return_when=asyncio.FIRST_EXCEPTION,
            )
            for task in done:
                exc = task.exception()
                if exc and not isinstance(exc, asyncio.CancelledError):
                    logger.error("Task %s failed: %s", task.get_name(), exc)
            return 0
        except (KeyboardInterrupt, asyncio.CancelledError):
            return 0
        finally:
            await self.stop()

    # ── Pipeline Integration API ─────────────────────────────

    def consume(self, limit: int = 10) -> List[TriangulatedSignal]:
        """
        Consume top-ranked signals for execution.
        Called by MasterOrchestrator or Pipeline each cycle.
        """
        return self.bus.consume_ranked(limit)

    def publish_external(self, signal: TriangulatedSignal):
        """Publish an external signal into the engine's bus."""
        self.bus.publish(signal)

    def record_execution_outcome(
        self, signal: TriangulatedSignal, profit: float, success: bool,
    ):
        """Record execution result for ML model improvement & gas tracking."""
        self.ml_aggregator.record_outcome(signal, profit, success)

        if success and profit > 0:
            self.zero_capital.record_profit(
                signal.chain_id, profit, gas_funded=True,
            )
            if self.zero_capital.should_self_fund():
                plan = self.zero_capital.create_funding_plan()
                if plan:
                    self.zero_capital.execute_funding(plan)

    def create_execution_bundle(
        self, signal: TriangulatedSignal, debt_amount_usd: float,
    ):
        """Create a zero-capital execution bundle for a signal."""
        return self.zero_capital.create_execution_bundle(signal, debt_amount_usd)

    # ── Module Loops ─────────────────────────────────────────

    async def _loop_deep_crawl(self):
        """Deep-Crawl Archive Indexer loop."""
        try:
            while self._running:
                try:
                    await self.deep_crawl.run_cycle()
                    self._cycle_count += 1
                except Exception as exc:
                    logger.error("[deep_crawl] Cycle error: %s", exc)
                await asyncio.sleep(self._intervals["deep_crawl"])
        except asyncio.CancelledError:
            pass

    async def _loop_mempool(self):
        """Mempool Microscope loop (high frequency)."""
        try:
            while self._running:
                try:
                    await self.mempool.run_cycle()
                except Exception as exc:
                    logger.error("[mempool] Cycle error: %s", exc)
                await asyncio.sleep(self._intervals["mempool"])
        except asyncio.CancelledError:
            pass

    async def _loop_hyper_solver(self):
        """Hyper-Solver loop."""
        try:
            while self._running:
                try:
                    await self.hyper_solver.run_cycle()
                except Exception as exc:
                    logger.error("[hyper_solver] Cycle error: %s", exc)
                await asyncio.sleep(self._intervals["hyper_solver"])
        except asyncio.CancelledError:
            pass

    async def _loop_alpha_seeker(self):
        """Alpha-Seeker Sentiment loop."""
        try:
            while self._running:
                try:
                    await self.alpha_seeker.run_cycle()
                except Exception as exc:
                    logger.error("[alpha_seeker] Cycle error: %s", exc)
                await asyncio.sleep(self._intervals["alpha_seeker"])
        except asyncio.CancelledError:
            pass

    async def _loop_static_analysis(self):
        """Static Analysis Engine loop."""
        try:
            while self._running:
                try:
                    await self.static_analysis.run_cycle()
                except Exception as exc:
                    logger.error("[static_analysis] Cycle error: %s", exc)
                await asyncio.sleep(self._intervals["static_analysis"])
        except asyncio.CancelledError:
            pass

    async def _loop_zero_capital(self):
        """Zero-Capital Layer maintenance loop."""
        try:
            while self._running:
                try:
                    await self.zero_capital.run_cycle()
                except Exception as exc:
                    logger.error("[zero_capital] Cycle error: %s", exc)
                await asyncio.sleep(self._intervals["zero_capital"])
        except asyncio.CancelledError:
            pass

    async def _loop_execution_router(self):
        """Execution Router — process queued signals."""
        try:
            while self._running:
                try:
                    await self.execution_router.run_cycle()
                except Exception as exc:
                    logger.error("[exec_router] Cycle error: %s", exc)
                await asyncio.sleep(self._intervals.get("exec_router", 2.0))
        except asyncio.CancelledError:
            pass

    async def _loop_performance(self):
        """Performance Optimizer — health checks, auto-tune."""
        try:
            while self._running:
                try:
                    await self.performance.run_cycle()
                except Exception as exc:
                    logger.error("[performance] Cycle error: %s", exc)
                await asyncio.sleep(self._intervals.get("performance", 30.0))
        except asyncio.CancelledError:
            pass

    async def _loop_bus_gc(self):
        """Signal bus garbage collection loop."""
        try:
            while self._running:
                self.bus.gc_dedup_cache()
                await asyncio.sleep(self._intervals["bus_gc"])
        except asyncio.CancelledError:
            pass

    async def _loop_diagnostics(self):
        """Periodic diagnostics / status print."""
        try:
            while self._running:
                await asyncio.sleep(60.0)
                self._print_status()
        except asyncio.CancelledError:
            pass

    # ── Diagnostics ──────────────────────────────────────────

    def get_full_stats(self) -> Dict[str, Any]:
        uptime = time.time() - self._start_time if self._start_time else 0
        return {
            "version": self.VERSION,
            "uptime_hours": round(uptime / 3600, 2),
            "cycles": self._cycle_count,
            "data_lake": self.lake.get_stats(),
            "signal_bus": self.bus.get_stats(),
            "module_1_deep_crawl": self.deep_crawl.get_stats(),
            "module_2_mempool": self.mempool.get_stats(),
            "module_3_hyper_solver": self.hyper_solver.get_stats(),
            "module_4_alpha_seeker": self.alpha_seeker.get_stats(),
            "module_5_static_analysis": self.static_analysis.get_stats(),
            "module_6_ml_aggregator": self.ml_aggregator.get_stats(),
            "module_7_zero_capital": self.zero_capital.get_stats(),
            "module_8_execution_router": self.execution_router.get_stats(),
            "module_9_performance": self.performance.get_stats(),
            "bridge_registry": self.bridge_registry.get_stats(),
            "protocol_classifier": self.protocol_classifier.get_stats(),
        }

    def _print_status(self):
        stats = self.get_full_stats()
        bus = stats["signal_bus"]
        ml = stats["module_6_ml_aggregator"]
        zc = stats["module_7_zero_capital"]

        logger.info("─" * 70)
        logger.info("  OMNI-SCOPE v2 STATUS")
        logger.info("─" * 70)
        logger.info("  Uptime:      %.2f hrs | Cycles: %d", stats["uptime_hours"], stats["cycles"])
        logger.info("  Signals:     %d published | %d scored | %d ranked | %d consumed",
                     bus.get("total_published", 0),
                     ml.get("signals_scored", 0),
                     bus.get("ranked_queue_size", 0),
                     bus.get("total_consumed", 0))
        logger.info("  Dedup:       %d filtered | ML avg: %.3f",
                     bus.get("duplicates_filtered", 0),
                     ml.get("avg_quality_score", 0))
        logger.info("  Profit:      $%.2f total | $%.2f gas funded",
                     zc.get("total_profit_usd", 0),
                     zc.get("total_gas_funded_usd", 0))
        logger.info("  Data Lake:   %s",
                     {k: "✓" if v else "✗" for k, v in stats["data_lake"].items()
                      if k != "connected"})

        # Per-module signal counts
        for key in ["module_1_deep_crawl", "module_2_mempool", "module_3_hyper_solver",
                     "module_4_alpha_seeker", "module_5_static_analysis"]:
            mod = stats[key]
            emitted = mod.get("signals_emitted", 0)
            logger.info("  %-25s signals=%d", key.replace("module_", "Mod "), emitted)
        logger.info("─" * 70)

    def print_status(self):
        """Public API: print comprehensive status to stdout."""
        stats = self.get_full_stats()
        bus = stats["signal_bus"]
        ml = stats["module_6_ml_aggregator"]
        zc = stats["module_7_zero_capital"]

        print()
        print("=" * 80)
        print("  OMNI-SCOPE TRIANGULATION ENGINE v2.0 — STATUS REPORT")
        print("=" * 80)
        print(f"  Uptime:              {stats['uptime_hours']:.2f} hours")
        print(f"  Total cycles:        {stats['cycles']}")
        print()
        print("  ─── Data Lake ───")
        lake = stats["data_lake"]
        for backend, status in lake.items():
            if backend == "connected":
                continue
            icon = "✓" if status else "✗"
            print(f"    {icon} {backend}")
        print()
        print("  ─── Signal Bus ───")
        print(f"    Published:         {bus.get('total_published', 0)}")
        print(f"    Consumed:          {bus.get('total_consumed', 0)}")
        print(f"    Duplicates:        {bus.get('duplicates_filtered', 0)}")
        print(f"    Ranked queue:      {bus.get('ranked_queue_size', 0)}")
        print()
        print("  ─── Module Stats ───")
        for i, key in enumerate([
            "module_1_deep_crawl", "module_2_mempool", "module_3_hyper_solver",
            "module_4_alpha_seeker", "module_5_static_analysis",
        ], 1):
            mod = stats[key]
            name = key.split("_", 2)[-1].replace("_", " ").title()
            print(f"    Module {i}: {name:25s}  signals={mod.get('signals_emitted', 0)}")
        print()
        print("  ─── ML Ranker (Module 6) ───")
        print(f"    Scored:            {ml.get('signals_scored', 0)}")
        print(f"    Filtered:          {ml.get('signals_filtered', 0)}")
        print(f"    Avg score:         {ml.get('avg_quality_score', 0):.3f}")
        print(f"    Model trained:     {ml.get('model_trained', False)}")
        print(f"    Retrains:          {ml.get('model_retrains', 0)}")
        print()
        print("  ─── Zero-Capital (Module 7) ───")
        print(f"    Total profit:      ${zc.get('total_profit_usd', 0):.2f}")
        print(f"    Gas funded:        ${zc.get('total_gas_funded_usd', 0):.2f}")
        print(f"    Bundles created:   {zc.get('bundles_created', 0)}")
        print(f"    Bundles executed:  {zc.get('bundles_executed', 0)}")
        gas_funds = zc.get("gas_funds", {})
        if gas_funds:
            print("    Gas balances:")
            for cid, info in gas_funds.items():
                icon = "✓" if not info["needs_funding"] else "!"
                print(f"      {icon} Chain {cid}: ${info['balance_usd']:.2f} / ${info['target_usd']:.2f}")
        print()

        # ── Execution Router (Module 8) ──
        er = stats.get("module_8_execution_router", {})
        print("  ─── Execution Router (Module 8) ───")
        print(f"    Queue depth:       {er.get('queue_depth', 0)}")
        print(f"    Submitted:         {er.get('total_submitted', 0)}")
        print(f"    Confirmed:         {er.get('total_confirmed', 0)}")
        print(f"    Failed:            {er.get('total_failed', 0)}")
        print(f"    Profit:            ${er.get('total_profit_usd', 0):.2f}")
        print(f"    Gas spent:         ${er.get('total_gas_usd', 0):.2f}")
        executors = er.get("executors", {})
        if executors:
            print("    Executors:")
            for etype, es in executors.items():
                print(f"      {etype:20s} sub={es.get('submitted',0)} "
                      f"ok={es.get('confirmed',0)} "
                      f"fail={es.get('failed',0)}")
        print()

        # ── Performance Optimizer (Module 9) ──
        perf = stats.get("module_9_performance", {})
        print("  ─── Performance Optimizer (Module 9) ───")
        cache_s = perf.get("cache", {})
        print(f"    Cache hit rate:    {cache_s.get('hit_rate', 0):.2%}")
        print(f"    Cache L1 size:     {cache_s.get('l1_size', 0)}")
        mem_s = perf.get("memory", {})
        print(f"    Memory:            {mem_s.get('current_mb', 0):.0f} MB "
              f"(peak {mem_s.get('peak_mb', 0):.0f} MB)")
        print(f"    Batch operations:  {perf.get('batch_operations', 0)}")
        print()

        # ── Bridge Registry ──
        br = stats.get("bridge_registry", {})
        print("  ─── Bridge Registry ───")
        print(f"    Bridges:           {br.get('bridges_active', 0)} active "
              f"/ {br.get('bridges_registered', 0)} total")
        print(f"    Routes:            {br.get('total_routes', 0)}")
        print()

        # ── Protocol Classifier ──
        pc = stats.get("protocol_classifier", {})
        print("  ─── Protocol Classifier ───")
        print(f"    Classified:        {pc.get('classified', 0)}")
        print(f"    Liquidation cfgs:  {pc.get('liquidation_configs', 0)}")
        by_type = pc.get("by_type", {})
        if by_type:
            for ptype, cnt in sorted(by_type.items(), key=lambda x: -x[1]):
                print(f"      {ptype:25s} {cnt}")
        print()

        if bus.get("by_type"):
            print("  ─── Signals by Type ───")
            for typ, count in sorted(bus["by_type"].items(), key=lambda x: -x[1]):
                print(f"    {typ:35s} {count}")
        print("=" * 80)
