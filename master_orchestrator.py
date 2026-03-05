#!/usr/bin/env python3
"""
╔══════════════════════════════════════════════════════════════════════════╗
║  CRYO MASTER PROFIT ORCHESTRATOR                                        ║
║                                                                          ║
║  Unified command that activates every profit-generating module in        ║
║  parallel for maximum extraction velocity.                              ║
║                                                                          ║
║  Architecture:                                                           ║
║                                                                          ║
║    ┌─────────────────────────────────────────────────────────────┐       ║
║    │                   MASTER ORCHESTRATOR                        │       ║
║    │                                                             │       ║
║    │  ┌──────────────┐  ┌──────────────┐  ┌───────────────┐     │       ║
║    │  │ Module 9     │  │ Omni-Channel │  │ Profit Engine │     │       ║
║    │  │ Omni-Scope   │  │ Orchestrator │  │ Triangulated  │     │       ║
║    │  │ 5 Arrays     │  │ 5 Detectors  │  │ 6 Vectors     │     │       ║
║    │  └──────┬───────┘  └──────┬───────┘  └──────┬────────┘     │       ║
║    │         │  signals        │  signals        │  signals     │       ║
║    │         └────────┬────────┘────────┬────────┘              │       ║
║    │                  ▼                 ▼                        │       ║
║    │  ┌──────────────────────────────────────────────────┐      │       ║
║    │  │         SIGNAL AGGREGATOR & DEDUPLICATOR         │      │       ║
║    │  │  Module 9 ↔ Omni-Channel via SignalBridge        │      │       ║
║    │  └──────────────────────┬───────────────────────────┘      │       ║
║    │                         ▼ ranked by profit                 │       ║
║    │  ┌──────────────────────────────────────────────────┐      │       ║
║    │  │         MODULE 1 PIPELINE  (Stages 0→7)          │      │       ║
║    │  │  Detect → Analyse → Gas Gate → Execute → Collect │      │       ║
║    │  │  + ZeroRevertPipeline  + JITLiquidationEngine    │      │       ║
║    │  │  + MEV Protection      + Cross-Chain Routing     │      │       ║
║    │  └──────────────────────┬───────────────────────────┘      │       ║
║    │                         ▼                                  │       ║
║    │  ┌──────────────────────────────────────────────────┐      │       ║
║    │  │         EXECUTION BRIDGE                          │      │       ║
║    │  │  Signal → Calldata → On-Chain TX                  │      │       ║
║    │  └──────────────────────┬───────────────────────────┘      │       ║
║    │                         ▼                                  │       ║
║    │  ┌──────────────────────────────────────────────────┐      │       ║
║    │  │         PROFIT LEDGER  (P&L tracking)            │      │       ║
║    │  │  Phase transitions: $0 → $2.5K → $45K → ∞       │      │       ║
║    │  └──────────────────────────────────────────────────┘      │       ║
║    └─────────────────────────────────────────────────────────────┘       ║
║                                                                          ║
║  Revenue Streams (all running concurrently):                             ║
║    1. Flash Loan Liquidations    — 5-15% collateral bonus               ║
║    2. DEX Arbitrage              — cross-DEX price spread                ║
║    3. Cross-Chain Arbitrage      — N-hop bridge gaps                    ║
║    4. Oracle Front-Running       — 200-500ms price advantage            ║
║    5. MEV Backrunning            — sandwich/backrun extraction          ║
║    6. Surplus Capital Deployment — idle flash-loan utilization           ║
║    7. Zero-Revert Liquidations   — oracle-reactive, no simulation       ║
║    8. JIT Liquidations           — simulate-before-send, Flashbots      ║
║    9. New Protocol Sniping       — early alpha from contract crawler    ║
║   10. NFT-Backed Loan Liq.      — NFT collateral opportunities         ║
║                                                                          ║
║  Usage:                                                                  ║
║    python main.py --master                                               ║
║    python main.py --master --scan-only                                   ║
╚══════════════════════════════════════════════════════════════════════════╝
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# ────────────────────────────────────────────────────────────────────
#  Data Models
# ────────────────────────────────────────────────────────────────────


@dataclass
class AggregatedSignal:
    """A deduplicated, ranked opportunity ready for execution."""
    signal_id: str
    source: str
    signal_type: str
    chain_id: int
    target: str
    estimated_profit_usd: Decimal
    confidence: float
    raw_signal: Any = None


@dataclass
class OrchestratorMetrics:
    """Live performance counters."""
    start_time: float = 0.0
    cycles_completed: int = 0
    signals_received: int = 0
    signals_deduplicated: int = 0
    signals_executed: int = 0
    total_profit_usd: Decimal = Decimal("0")
    total_gas_spent_usd: Decimal = Decimal("0")
    executions_succeeded: int = 0
    executions_failed: int = 0
    active_modules: List[str] = field(default_factory=list)

    @property
    def net_profit_usd(self) -> Decimal:
        return self.total_profit_usd - self.total_gas_spent_usd

    @property
    def uptime_seconds(self) -> float:
        return time.time() - self.start_time if self.start_time else 0.0

    @property
    def success_rate(self) -> float:
        total = self.executions_succeeded + self.executions_failed
        return self.executions_succeeded / total if total > 0 else 0.0


# ────────────────────────────────────────────────────────────────────
#  Master Orchestrator
# ────────────────────────────────────────────────────────────────────


class MasterProfitOrchestrator:
    """
    Activates every profit module concurrently and funnels all signals
    through a single aggregation → execution → collection pipeline.

    Modules activated:
      - profit_engine.TriangulatedProfitEngine  (6-vector scanner)
      - MODULE_1_LIQUIDATION_ENGINE.Pipeline    (7-stage pipeline)
      - MODULE_9_OMNI_SCOPE.OmniScopeEngine    (5 detector arrays)
      - omni_channel.OmniOrchestrator           (5-array triangulation)
      - hub.SignalBridge                        (cross-module translation)
      - unified_execution_bridge                (signal → on-chain TX)
      - fire_engine                             (composable FIRE scripts)
    """

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        self.config = config or {}
        self.metrics = OrchestratorMetrics()
        self._scan_only = self.config.get("scan_only", False)
        self._cycle_interval = float(
            self.config.get("cycle_interval", os.getenv("SCAN_INTERVAL", "12"))
        )
        self._min_profit_usd = Decimal(
            str(self.config.get("min_profit_usd", os.getenv("MIN_PROFIT_USD", "50")))
        )
        self._running = False
        self._tasks: List[asyncio.Task] = []

        # Module references (lazy-loaded)
        self._profit_engine = None
        self._pipeline = None
        self._omni_scope = None
        self._omni_orchestrator = None
        self._signal_bridge = None
        self._execution_bridge = None

        # Signal aggregation
        self._signal_queue: asyncio.Queue = asyncio.Queue(maxsize=5000)
        self._seen_signals: Dict[str, float] = {}
        self._dedup_window = 60.0  # seconds

    # ── Lifecycle ──────────────────────────────────────────────

    async def start(self) -> None:
        """Initialize all modules and begin concurrent profit extraction."""
        logger.info("=" * 70)
        logger.info("  MASTER PROFIT ORCHESTRATOR — INITIALIZING")
        logger.info("=" * 70)

        self.metrics.start_time = time.time()
        self._running = True

        # Phase 1: Initialize all modules
        await self._init_modules()

        # Phase 2: Launch concurrent extraction loops
        self._tasks = [
            asyncio.create_task(self._profit_engine_loop(), name="profit_engine"),
            asyncio.create_task(self._pipeline_loop(), name="module1_pipeline"),
            asyncio.create_task(self._omni_scope_loop(), name="omni_scope"),
            asyncio.create_task(self._omni_channel_loop(), name="omni_channel"),
            asyncio.create_task(self._signal_aggregation_loop(), name="aggregator"),
            asyncio.create_task(self._dashboard_loop(), name="dashboard"),
            asyncio.create_task(self._dedup_cleanup_loop(), name="dedup_gc"),
        ]

        logger.info("  All %d extraction loops launched", len(self._tasks))
        logger.info("  Scan-only mode: %s", self._scan_only)
        logger.info("  Min profit threshold: $%s", self._min_profit_usd)
        logger.info("  Cycle interval: %ss", self._cycle_interval)
        logger.info("=" * 70)

    async def stop(self) -> None:
        """Gracefully shut down all modules."""
        logger.info("Master Orchestrator shutting down...")
        self._running = False

        for task in self._tasks:
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()

        # Shutdown modules in reverse order
        await self._shutdown_modules()

        logger.info(
            "Shutdown complete. Net profit: $%s | %d executions | %.0fs uptime",
            self.metrics.net_profit_usd,
            self.metrics.executions_succeeded,
            self.metrics.uptime_seconds,
        )

    async def run(self) -> int:
        """Start orchestrator and run until interrupted. Returns exit code."""
        try:
            await self.start()
            # Wait for all tasks — any exception propagates
            done, _ = await asyncio.wait(
                self._tasks, return_when=asyncio.FIRST_EXCEPTION,
            )
            for task in done:
                if task.exception() and not isinstance(
                    task.exception(), asyncio.CancelledError
                ):
                    logger.error(
                        "Task %s failed: %s", task.get_name(), task.exception()
                    )
            return 0
        except (KeyboardInterrupt, asyncio.CancelledError):
            return 0
        finally:
            await self.stop()

    # ── Module Initialization ─────────────────────────────────

    async def _init_modules(self) -> None:
        """Lazy-load and initialize all profit modules."""

        # 1. Profit Engine (6-vector triangulated scanner)
        try:
            from profit_engine import TriangulatedProfitEngine
            self._profit_engine = TriangulatedProfitEngine(self.config)
            self.metrics.active_modules.append("profit_engine")
            logger.info("  [+] Profit Engine initialized (6 vectors)")
        except Exception as exc:
            logger.warning("  [-] Profit Engine unavailable: %s", exc)

        # 2. Module 1 Pipeline (7-stage liquidation pipeline)
        try:
            from MODULE_1_LIQUIDATION_ENGINE.pipeline import Pipeline
            self._pipeline = Pipeline()
            self.metrics.active_modules.append("module1_pipeline")
            logger.info("  [+] Module 1 Pipeline initialized (stages 0-7)")
        except Exception as exc:
            logger.warning("  [-] Module 1 Pipeline unavailable: %s", exc)

        # 3. Module 9 Omni-Scope (5 detector arrays + ML ranker)
        try:
            from MODULE_9_OMNI_SCOPE.engine import OmniScopeEngine
            self._omni_scope = OmniScopeEngine()
            self.metrics.active_modules.append("omni_scope")
            logger.info("  [+] Omni-Scope Engine initialized (5 arrays)")
        except Exception as exc:
            logger.warning("  [-] Omni-Scope Engine unavailable: %s", exc)

        # 4. Omni-Channel Orchestrator (multi-strategy router)
        try:
            from omni_channel import OmniOrchestrator
            self._omni_orchestrator = OmniOrchestrator(self.config)
            self.metrics.active_modules.append("omni_channel")
            logger.info("  [+] Omni-Channel Orchestrator initialized")
        except Exception as exc:
            logger.warning("  [-] Omni-Channel Orchestrator unavailable: %s", exc)

        # 5. Signal Bridge (Module 9 ↔ Omni-Channel translation)
        try:
            from hub import SignalBridge
            self._signal_bridge = SignalBridge()
            if self._omni_scope:
                try:
                    from MODULE_9_OMNI_SCOPE.data_bus import DataBus
                    bus = DataBus()
                    self._signal_bridge.attach(bus)
                    logger.info("  [+] SignalBridge attached to Module 9 DataBus")
                except Exception:
                    pass
            self.metrics.active_modules.append("signal_bridge")
            logger.info("  [+] Signal Bridge initialized")
        except Exception as exc:
            logger.warning("  [-] Signal Bridge unavailable: %s", exc)

        # 6. Unified Execution Bridge (signal → on-chain TX)
        if not self._scan_only:
            try:
                from unified_execution_bridge import UnifiedExecutionBridge
                self._execution_bridge = UnifiedExecutionBridge(self.config)
                await self._execution_bridge.initialize()
                self.metrics.active_modules.append("execution_bridge")
                logger.info("  [+] Execution Bridge initialized")
            except Exception as exc:
                logger.warning("  [-] Execution Bridge unavailable: %s", exc)

        logger.info(
            "  Modules active: %d/%d",
            len(self.metrics.active_modules),
            6 if not self._scan_only else 5,
        )

    async def _shutdown_modules(self) -> None:
        """Shut down all initialized modules."""
        if self._execution_bridge:
            try:
                await self._execution_bridge.shutdown()
            except Exception:
                pass
        if self._profit_engine:
            try:
                await self._profit_engine.stop()
            except Exception:
                pass
        if self._pipeline:
            try:
                await self._pipeline.stop()
            except Exception:
                pass

    # ── Signal Aggregation & Dedup ────────────────────────────

    def _signal_key(self, source: str, signal_type: str, target: str,
                    chain_id: int) -> str:
        """Generate a deduplication key for a signal."""
        return f"{source}:{signal_type}:{target}:{chain_id}"

    async def _enqueue_signal(
        self,
        source: str,
        signal_type: str,
        chain_id: int,
        target: str,
        estimated_profit_usd: float,
        confidence: float,
        raw_signal: Any = None,
    ) -> bool:
        """Add a signal to the aggregation queue if not a duplicate."""
        key = self._signal_key(source, signal_type, target, chain_id)
        now = time.time()

        self.metrics.signals_received += 1

        # Dedup check
        if key in self._seen_signals:
            if now - self._seen_signals[key] < self._dedup_window:
                self.metrics.signals_deduplicated += 1
                return False

        self._seen_signals[key] = now

        # Profit threshold gate
        profit = Decimal(str(estimated_profit_usd))
        if profit < self._min_profit_usd:
            return False

        signal = AggregatedSignal(
            signal_id=f"{source}_{int(now * 1000)}",
            source=source,
            signal_type=signal_type,
            chain_id=chain_id,
            target=target,
            estimated_profit_usd=profit,
            confidence=confidence,
            raw_signal=raw_signal,
        )

        try:
            self._signal_queue.put_nowait(signal)
            return True
        except asyncio.QueueFull:
            logger.warning("Signal queue full — dropping low-value signal")
            return False

    # ── Extraction Loops ──────────────────────────────────────

    async def _profit_engine_loop(self) -> None:
        """Run the TriangulatedProfitEngine scanner loop."""
        if not self._profit_engine:
            return

        logger.info("[profit_engine] Starting 6-vector scan loop")
        try:
            await self._profit_engine.start()
            # Engine runs its own internal loop; we just keep it alive
            while self._running:
                await asyncio.sleep(self._cycle_interval)
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            logger.error("[profit_engine] Error: %s", exc)

    async def _pipeline_loop(self) -> None:
        """Run the Module 1 pipeline in continuous cycles."""
        if not self._pipeline:
            return

        logger.info("[module1_pipeline] Starting stage-gated pipeline")
        try:
            while self._running:
                try:
                    result = await self._pipeline.run()
                    self.metrics.cycles_completed += 1
                    if result and hasattr(result, "profit_usd"):
                        self.metrics.total_profit_usd += Decimal(
                            str(result.profit_usd)
                        )
                except Exception as exc:
                    logger.error("[module1_pipeline] Cycle error: %s", exc)
                await asyncio.sleep(self._cycle_interval)
        except asyncio.CancelledError:
            pass

    async def _omni_scope_loop(self) -> None:
        """Poll the Omni-Scope engine for ranked opportunities."""
        if not self._omni_scope:
            return

        logger.info("[omni_scope] Starting 5-array detector sweep")
        try:
            while self._running:
                try:
                    signals = await self._omni_scope.consume()
                    for sig in (signals or []):
                        await self._enqueue_signal(
                            source="omni_scope",
                            signal_type=getattr(sig, "signal_type", "unknown"),
                            chain_id=getattr(sig, "chain_id", 1),
                            target=getattr(sig, "target", ""),
                            estimated_profit_usd=float(
                                getattr(sig, "net_profit_usd", 0)
                            ),
                            confidence=float(
                                getattr(sig, "confidence", 0)
                            ),
                            raw_signal=sig,
                        )
                except Exception as exc:
                    logger.error("[omni_scope] Sweep error: %s", exc)
                await asyncio.sleep(self._cycle_interval)
        except asyncio.CancelledError:
            pass

    async def _omni_channel_loop(self) -> None:
        """Poll the Omni-Channel orchestrator for opportunities."""
        if not self._omni_orchestrator:
            return

        logger.info("[omni_channel] Starting multi-strategy detection")
        try:
            while self._running:
                try:
                    signals = await self._omni_orchestrator.get_opportunities()
                    for sig in (signals or []):
                        await self._enqueue_signal(
                            source="omni_channel",
                            signal_type=getattr(sig, "signal_type", "unknown"),
                            chain_id=getattr(sig, "chain_id", 1),
                            target=getattr(sig, "target", ""),
                            estimated_profit_usd=float(
                                getattr(sig, "net_profit_usd", 0)
                            ),
                            confidence=float(
                                getattr(sig, "confidence", 0)
                            ),
                            raw_signal=sig,
                        )
                except Exception as exc:
                    logger.error("[omni_channel] Detection error: %s", exc)
                await asyncio.sleep(self._cycle_interval)
        except asyncio.CancelledError:
            pass

    async def _signal_aggregation_loop(self) -> None:
        """Consume aggregated signals and route to execution."""
        logger.info("[aggregator] Signal aggregation loop started")
        try:
            while self._running:
                try:
                    signal = await asyncio.wait_for(
                        self._signal_queue.get(), timeout=5.0
                    )
                except asyncio.TimeoutError:
                    continue

                self.metrics.signals_executed += 1

                if self._scan_only:
                    logger.info(
                        "  [SCAN] %s | %s | chain=%d | profit=$%s | conf=%.2f",
                        signal.source,
                        signal.signal_type,
                        signal.chain_id,
                        signal.estimated_profit_usd,
                        signal.confidence,
                    )
                    continue

                # Execute via bridge
                if self._execution_bridge and signal.raw_signal:
                    try:
                        result = await self._execution_bridge.process_signal(
                            signal.raw_signal
                        )
                        if result.get("status") == "success":
                            profit = Decimal(str(result.get("profit_usd", 0)))
                            self.metrics.total_profit_usd += profit
                            self.metrics.executions_succeeded += 1
                            logger.info(
                                "  [EXEC] SUCCESS $%s | %s | %s",
                                profit,
                                signal.signal_type,
                                result.get("tx_hash", ""),
                            )
                        else:
                            self.metrics.executions_failed += 1
                            logger.warning(
                                "  [EXEC] FAILED: %s", result.get("reason", "unknown")
                            )
                    except Exception as exc:
                        self.metrics.executions_failed += 1
                        logger.error("  [EXEC] Error: %s", exc)

        except asyncio.CancelledError:
            pass

    # ── Maintenance Loops ─────────────────────────────────────

    async def _dashboard_loop(self) -> None:
        """Print periodic performance dashboard."""
        try:
            while self._running:
                await asyncio.sleep(30)
                self._print_dashboard()
        except asyncio.CancelledError:
            pass

    async def _dedup_cleanup_loop(self) -> None:
        """Periodically clean expired dedup entries."""
        try:
            while self._running:
                await asyncio.sleep(120)
                now = time.time()
                expired = [
                    k for k, ts in self._seen_signals.items()
                    if now - ts > self._dedup_window * 2
                ]
                for k in expired:
                    del self._seen_signals[k]
        except asyncio.CancelledError:
            pass

    # ── Dashboard ─────────────────────────────────────────────

    def _print_dashboard(self) -> None:
        """Print live performance summary."""
        m = self.metrics
        uptime_min = m.uptime_seconds / 60

        logger.info("=" * 60)
        logger.info("  MASTER ORCHESTRATOR DASHBOARD")
        logger.info("-" * 60)
        logger.info("  Uptime:         %.1f min", uptime_min)
        logger.info("  Active modules: %s", ", ".join(m.active_modules))
        logger.info("  Cycles:         %d", m.cycles_completed)
        logger.info("  Signals:        %d received | %d deduped | %d processed",
                     m.signals_received, m.signals_deduplicated, m.signals_executed)
        logger.info("  Executions:     %d OK | %d FAIL | %.0f%% success",
                     m.executions_succeeded, m.executions_failed,
                     m.success_rate * 100)
        logger.info("  Gross profit:   $%s", m.total_profit_usd)
        logger.info("  Gas spent:      $%s", m.total_gas_spent_usd)
        logger.info("  NET PROFIT:     $%s", m.net_profit_usd)
        if uptime_min > 0:
            hourly_rate = float(m.net_profit_usd) / (uptime_min / 60)
            logger.info("  Hourly rate:    $%.2f/hr", hourly_rate)
        logger.info("  Queue depth:    %d", self._signal_queue.qsize())
        logger.info("=" * 60)

    def get_stats(self) -> Dict[str, Any]:
        """Return metrics as a dictionary for API/monitoring."""
        m = self.metrics
        return {
            "uptime_seconds": m.uptime_seconds,
            "active_modules": m.active_modules,
            "cycles_completed": m.cycles_completed,
            "signals_received": m.signals_received,
            "signals_deduplicated": m.signals_deduplicated,
            "signals_executed": m.signals_executed,
            "executions_succeeded": m.executions_succeeded,
            "executions_failed": m.executions_failed,
            "success_rate": m.success_rate,
            "total_profit_usd": str(m.total_profit_usd),
            "total_gas_spent_usd": str(m.total_gas_spent_usd),
            "net_profit_usd": str(m.net_profit_usd),
            "queue_depth": self._signal_queue.qsize(),
            "scan_only": self._scan_only,
        }
