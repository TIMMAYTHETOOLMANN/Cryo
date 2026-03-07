#!/usr/bin/env python3
"""
╔══════════════════════════════════════════════════════════════════════════╗
║  CRYO MASTER PROFIT ORCHESTRATOR (CONSOLIDATED)                         ║
║                                                                          ║
║  Single entry point — activates every profit-generating module in       ║
║  parallel for maximum extraction velocity.                              ║
║                                                                          ║
║  Architecture (post-consolidation):                                      ║
║                                                                          ║
║    ┌─────────────────────────────────────────────────────────────┐       ║
║    │                   MASTER ORCHESTRATOR                        │       ║
║    │                                                             │       ║
║    │  MODULE_1_LIQUIDATION_ENGINE (THE ENGINE)                   │       ║
║    │  ┌──────────────┐  ┌──────────────┐  ┌───────────────┐     │       ║
║    │  │ pipeline.py  │  │ detectors/   │  │ profit_core/  │     │       ║
║    │  │ stages 0-7   │  │ (omni-ch)    │  │ (scanning+    │     │       ║
║    │  │ detection→   │  │ mempool_radar│  │  flash+gas+   │     │       ║
║    │  │ analysis→    │  │ contract_    │  │  ledger+heat  │     │       ║
║    │  │ execution→   │  │  crawler     │  │  +multiplier) │     │       ║
║    │  │ profit       │  │ ml_aggregator│  │               │     │       ║
║    │  └──────┬───────┘  └──────┬───────┘  └──────┬────────┘     │       ║
║    │         └────────┬────────┘────────┬────────┘              │       ║
║    │                  ▼                 ▼                        │       ║
║    │  ┌──────────────────────────────────────────────────┐      │       ║
║    │  │       MODULE_9 OMNI-SCOPE (5 detector arrays)    │      │       ║
║    │  │       MODULE_10 RPC GATEWAY (load balancer)      │      │       ║
║    │  │       MODULE_11 TIMING ENGINE (JIT execution)    │      │       ║
║    │  │       enhanced_modules 1-8 (ML + MEV + risk)     │      │       ║
║    │  └──────────────────────┬───────────────────────────┘      │       ║
║    │                         ▼ ranked by profit                 │       ║
║    │  ┌──────────────────────────────────────────────────┐      │       ║
║    │  │       EXECUTION BRIDGE → ON-CHAIN TX             │      │       ║
║    │  │       Phase: $0 → $2.5K → $45K → ∞              │      │       ║
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
║    7. Zero-Revert Liquidations   — oracle-reactive, no preflight       ║
║    8. JIT Liquidations           — verify-before-send, Flashbots      ║
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
import io
import logging
import os
import sys
import time
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Dict, List, Optional

# ── Windows encoding safety — prevent charmap errors from emoji ──
os.environ['PYTHONIOENCODING'] = 'utf-8'
for _sname in ('stdout', 'stderr'):
    _s = getattr(sys, _sname)
    try:
        _s.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        try:
            setattr(sys, _sname,
                    io.TextIOWrapper(_s.buffer, encoding='utf-8', errors='replace'))
        except Exception:
            pass

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

    Post-consolidation modules (all under MODULE_1_LIQUIDATION_ENGINE):
      - MODULE_1.pipeline         — 7-stage pipeline (THE engine)
      - MODULE_1.detectors        — was omni_channel (5 detector arrays)
      - MODULE_1.profit_core      — was profit_engine (scanner, flash, gas, ledger)
      - MODULE_9_OMNI_SCOPE       — predictive 5-array detector
      - MODULE_10_RPC_GATEWAY     — enterprise load balancer
      - MODULE_11_TIMING_ENGINE   — JIT execution + oracle watcher
      - enhanced_modules          — v2 ML enhancements (8 subsystems)
      - hub.SignalBridge           — cross-module signal translation
      - unified_execution_bridge   — signal → on-chain TX
    """

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        self.config = config or {}
        self.metrics = OrchestratorMetrics()
        self._scan_only = self.config.get("scan_only", False)
        self._cycle_interval = float(
            self.config.get("cycle_interval", os.getenv("SCAN_INTERVAL", "1"))
        )
        self._min_profit_usd = Decimal(
            str(self.config.get("min_profit_usd", os.getenv("MIN_PROFIT_USD", "0.50")))
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
        self._omni_v2 = None  # Omni-Scope Triangulation Engine v2 (deprecated)
        self._rpc_gateway = None  # Module 10: Enhanced RPC Gateway
        self._timing_engine = None  # Module 11: Timing Optimizer Engine

        # ── Enhanced Modules (v2.0) ────────────────────────────
        self._ml_scorer = None
        self._cross_protocol = None
        self._realtime_stream = None
        self._multi_exit = None
        self._gas_predictor = None
        self._fee_minimizer = None
        self._adaptive_router = None
        self._fallback_chain = None
        self._zero_fee = None
        self._protocol_registry = None
        self._gas_golfer = None
        self._safety_guard = None
        self._batch_coordinator = None
        self._light_client = None
        self._dynamic_fees = None
        self._twap_oracle = None
        self._dynamic_slippage = None
        self._circuit_breaker = None
        self._multi_block_mev = None
        self._order_flow = None
        self._private_mempool = None
        self._live_dashboard = None
        self._ab_testing = None
        self._anomaly_detector = None

        # Signal aggregation
        self._signal_queue: asyncio.Queue = asyncio.Queue(maxsize=20000)
        self._seen_signals: Dict[str, float] = {}
        self._dedup_window = 30.0  # seconds — tighter dedup for faster cycles

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
            asyncio.create_task(self._pipeline_loop(), name="module1_pipeline"),
            asyncio.create_task(self._omni_scope_loop(), name="omni_scope"),
            asyncio.create_task(self._omni_channel_loop(), name="omni_channel"),
            asyncio.create_task(self._signal_aggregation_loop(), name="aggregator"),
            asyncio.create_task(self._dashboard_loop(), name="dashboard"),
            asyncio.create_task(self._dedup_cleanup_loop(), name="dedup_gc"),
            asyncio.create_task(self._enhanced_modules_loop(), name="enhanced_v2"),
            asyncio.create_task(self._rpc_gateway_loop(), name="rpc_gateway"),
            asyncio.create_task(self._timing_engine_loop(), name="timing_engine"),
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
        """Lazy-load and initialize all profit modules.

        Post-consolidation layout:
          MODULE_1_LIQUIDATION_ENGINE/pipeline.py  — 7-stage pipeline (THE engine)
          MODULE_1_LIQUIDATION_ENGINE/detectors/   — was omni_channel
          MODULE_1_LIQUIDATION_ENGINE/profit_core/  — was profit_engine
          MODULE_9_OMNI_SCOPE/                     — 5 detector arrays
          MODULE_10_RPC_GATEWAY/                   — load balancer
          MODULE_11_TIMING_ENGINE/                 — JIT execution
          enhanced_modules/                        — v2 ML enhancements
        """

        # 1. Module 1 Pipeline — the CORE engine (stages 0-7 + profit_core + detectors)
        try:
            from MODULE_1_LIQUIDATION_ENGINE.pipeline import Pipeline
            self._pipeline = Pipeline()
            self.metrics.active_modules.append("module1_pipeline")
            logger.info("  [+] Module 1 Pipeline initialized (stages 0-7 + profit_core + detectors)")
        except Exception as exc:
            logger.warning("  [-] Module 1 Pipeline unavailable: %s", exc)

        # 2. Module 9 Omni-Scope (5 detector arrays + ML ranker)
        try:
            from MODULE_9_OMNI_SCOPE.engine import OmniScopeEngine
            self._omni_scope = OmniScopeEngine()
            self.metrics.active_modules.append("omni_scope")
            logger.info("  [+] Omni-Scope Engine initialized (5 arrays)")
        except Exception as exc:
            logger.warning("  [-] Omni-Scope Engine unavailable: %s", exc)

        # 3. Omni-Channel Orchestrator (now canonical: MODULE_1/detectors)
        try:
            from MODULE_1_LIQUIDATION_ENGINE.detectors.omni_orchestrator import OmniOrchestrator
            self._omni_orchestrator = OmniOrchestrator(self.config)
            self.metrics.active_modules.append("omni_channel")
            logger.info("  [+] Omni-Channel Orchestrator initialized (via MODULE_1/detectors)")
        except Exception as exc:
            logger.warning("  [-] Omni-Channel Orchestrator unavailable: %s", exc)

        # 4. Signal Bridge (Module 9 ↔ Omni-Channel translation)
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

        # 5. Unified Execution Bridge (signal → on-chain TX)
        # Always initialize — must be hot and ready for instant execution
        try:
            from unified_execution_bridge import UnifiedExecutionBridge
            self._execution_bridge = UnifiedExecutionBridge(self.config)
            await self._execution_bridge.initialize()
            self.metrics.active_modules.append("execution_bridge")
            logger.info("  [+] Execution Bridge initialized (mode=%s)",
                        "SCAN" if self._scan_only else "LIVE")
        except Exception as exc:
            logger.warning("  [-] Execution Bridge unavailable: %s", exc)

        # 6. Module 10 — Enhanced RPC Gateway & Load Balancer
        try:
            from MODULE_10_RPC_GATEWAY import EnhancedRPCGateway
            self._rpc_gateway = EnhancedRPCGateway()
            await self._rpc_gateway.start()
            self.metrics.active_modules.append("rpc_gateway")
            logger.info("  [+] Module 10: Enhanced RPC Gateway initialized")
        except Exception as exc:
            logger.warning("  [-] Module 10 RPC Gateway unavailable: %s", exc)

        # 7. Module 11 — Timing Optimizer & JIT Execution Engine
        try:
            from MODULE_11_TIMING_ENGINE import TimingOptimizerEngine, get_timing_config
            timing_cfg = get_timing_config()
            self._timing_engine = TimingOptimizerEngine(
                config=timing_cfg,
                rpc_gateway=self._rpc_gateway,
            )
            await self._timing_engine.start()
            self.metrics.active_modules.append("timing_engine")
            logger.info("  [+] Module 11: Timing Optimizer Engine initialized "
                        "(oracle + mempool + ML + JIT)")
        except Exception as exc:
            logger.warning("  [-] Module 11 Timing Engine unavailable: %s", exc)

        # 8. Enhanced Modules (v2.0) — all 8 enhanced subsystems
        await self._init_enhanced_modules()

        logger.info(
            "  Modules active: %d",
            len(self.metrics.active_modules),
        )

    # ── Enhanced Module Initialization (v2.0) ──────────────────

    async def _init_enhanced_modules(self) -> None:
        """Initialize all 8 enhanced module subsystems."""
        logger.info("  ── Enhanced Modules v2.0 ──")

        # Module 1: Opportunity Detector
        try:
            from enhanced_modules.module_1_opportunity_detector import (
                MLProbabilityScorer, CrossProtocolDetector, RealtimeStreamIngester,
            )
            self._ml_scorer = MLProbabilityScorer(self.config)
            self._cross_protocol = CrossProtocolDetector(self.config)
            self._realtime_stream = RealtimeStreamIngester(self.config)
            await self._ml_scorer.start()
            await self._cross_protocol.start()
            await self._realtime_stream.start()
            self.metrics.active_modules.append("enhanced_m1_detector")
            logger.info("  [+] Enhanced Module 1: Opportunity Detector (ML + CrossProtocol + Stream)")
        except Exception as exc:
            logger.warning("  [-] Enhanced Module 1 unavailable: %s", exc)

        # Module 2: Profitability Calculator
        try:
            from enhanced_modules.module_2_profitability_calculator import (
                MultiExitOptimizer, GasPricePredictor, FlashLoanFeeMinimizer,
            )
            self._multi_exit = MultiExitOptimizer(self.config)
            self._gas_predictor = GasPricePredictor(self.config)
            self._fee_minimizer = FlashLoanFeeMinimizer(self.config)
            await self._multi_exit.start()
            await self._gas_predictor.start()
            await self._fee_minimizer.start()
            self.metrics.active_modules.append("enhanced_m2_calculator")
            logger.info("  [+] Enhanced Module 2: Profitability Calculator (8 exits + gas pred)")
        except Exception as exc:
            logger.warning("  [-] Enhanced Module 2 unavailable: %s", exc)

        # Module 3: Flash Loan Aggregator
        try:
            from enhanced_modules.module_3_flash_loan_aggregator import (
                AdaptiveFlashLoanRouter, MultiProviderFallback, ZeroFeeExploiter,
            )
            self._adaptive_router = AdaptiveFlashLoanRouter(self.config)
            self._fallback_chain = MultiProviderFallback(self.config)
            self._zero_fee = ZeroFeeExploiter(self.config)
            await self._adaptive_router.start()
            await self._fallback_chain.start()
            await self._zero_fee.start()
            self.metrics.active_modules.append("enhanced_m3_aggregator")
            logger.info("  [+] Enhanced Module 3: Flash Loan Aggregator (adaptive + fallback + zero-fee)")
        except Exception as exc:
            logger.warning("  [-] Enhanced Module 3 unavailable: %s", exc)

        # Module 4: Liquidation Executor
        try:
            from enhanced_modules.module_4_liquidation_executor import (
                ProtocolAdapterRegistry, GasGolfer, SafetyGuard,
            )
            self._protocol_registry = ProtocolAdapterRegistry(self.config)
            self._gas_golfer = GasGolfer(self.config)
            self._safety_guard = SafetyGuard(self.config)
            await self._protocol_registry.start()
            await self._gas_golfer.start()
            await self._safety_guard.start()
            self.metrics.active_modules.append("enhanced_m4_executor")
            logger.info("  [+] Enhanced Module 4: Liquidation Executor (10 protocols + gas golf)")
        except Exception as exc:
            logger.warning("  [-] Enhanced Module 4 unavailable: %s", exc)

        # Module 5: Cross-Chain Orchestrator
        try:
            from enhanced_modules.module_5_cross_chain import (
                BatchLiquidationCoordinator, DynamicFeeManager, LightClientVerifier,
            )
            self._batch_coordinator = BatchLiquidationCoordinator(self.config)
            self._light_client = LightClientVerifier(self.config)
            self._dynamic_fees = DynamicFeeManager(self.config)
            await self._batch_coordinator.start()
            await self._light_client.start()
            await self._dynamic_fees.start()
            self.metrics.active_modules.append("enhanced_m5_crosschain")
            logger.info("  [+] Enhanced Module 5: Cross-Chain Orchestrator (batch + fees + light-client)")
        except Exception as exc:
            logger.warning("  [-] Enhanced Module 5 unavailable: %s", exc)

        # Module 6: Risk & Slippage Protection
        try:
            from enhanced_modules.module_6_risk_protection import (
                TWAPOracle, DynamicSlippageCalculator, CircuitBreakerV2,
            )
            self._twap_oracle = TWAPOracle(self.config)
            self._dynamic_slippage = DynamicSlippageCalculator(self.config)
            self._circuit_breaker = CircuitBreakerV2(self.config)
            await self._twap_oracle.start()
            await self._dynamic_slippage.start()
            await self._circuit_breaker.start()
            self.metrics.active_modules.append("enhanced_m6_risk")
            logger.info("  [+] Enhanced Module 6: Risk Protection (TWAP + slippage + circuit breaker)")
        except Exception as exc:
            logger.warning("  [-] Enhanced Module 6 unavailable: %s", exc)

        # Module 7: MEV & Backrunning
        try:
            from enhanced_modules.module_7_mev_strategy import (
                MultiBlockMEV, OrderFlowCapture, PrivateMempoolAggregator,
            )
            self._multi_block_mev = MultiBlockMEV(self.config)
            self._order_flow = OrderFlowCapture(self.config)
            self._private_mempool = PrivateMempoolAggregator(self.config)
            await self._multi_block_mev.start()
            await self._order_flow.start()
            await self._private_mempool.start()
            self.metrics.active_modules.append("enhanced_m7_mev")
            logger.info("  [+] Enhanced Module 7: MEV Strategy (multi-block + order flow + private)")
        except Exception as exc:
            logger.warning("  [-] Enhanced Module 7 unavailable: %s", exc)

        # Module 8: Analytics Dashboard
        try:
            from enhanced_modules.module_8_analytics_dashboard import (
                LiveDashboard, ABTestingEngine, AnomalyDetector,
            )
            self._live_dashboard = LiveDashboard(self.config)
            self._ab_testing = ABTestingEngine(self.config)
            self._anomaly_detector = AnomalyDetector(self.config)
            await self._live_dashboard.start()
            await self._ab_testing.start()
            await self._anomaly_detector.start()
            self.metrics.active_modules.append("enhanced_m8_analytics")
            logger.info("  [+] Enhanced Module 8: Analytics (dashboard + A/B + anomaly)")
        except Exception as exc:
            logger.warning("  [-] Enhanced Module 8 unavailable: %s", exc)

        enhanced_count = sum(
            1 for m in self.metrics.active_modules if m.startswith("enhanced_")
        )
        logger.info("  Enhanced modules active: %d/8", enhanced_count)

    async def _shutdown_enhanced_modules(self) -> None:
        """Shut down all enhanced modules."""
        enhanced_refs = [
            self._ml_scorer, self._cross_protocol, self._realtime_stream,
            self._multi_exit, self._gas_predictor, self._fee_minimizer,
            self._adaptive_router, self._fallback_chain, self._zero_fee,
            self._protocol_registry, self._gas_golfer, self._safety_guard,
            self._batch_coordinator, self._light_client, self._dynamic_fees,
            self._twap_oracle, self._dynamic_slippage, self._circuit_breaker,
            self._multi_block_mev, self._order_flow, self._private_mempool,
            self._live_dashboard, self._ab_testing, self._anomaly_detector,
        ]
        for module in enhanced_refs:
            if module:
                try:
                    await module.stop()
                except Exception:
                    pass

    async def _enhanced_modules_loop(self) -> None:
        """
        Continuous loop for enhanced module orchestration.
        Coordinates the ML scorer, cross-protocol detector, circuit breaker,
        dynamic fees, and anomaly detector in each cycle.
        """
        logger.info("[enhanced_v2] Starting enhanced modules orchestration loop")
        try:
            while self._running:
                try:
                    # 1. Check circuit breaker
                    if self._circuit_breaker:
                        decision = self._circuit_breaker.check()
                        if not decision.allow_execution:
                            logger.debug("[enhanced_v2] Circuit breaker: %s", decision.message)
                            await asyncio.sleep(self._cycle_interval)
                            continue

                    # 2. Auto-manage chain gas fees
                    if self._dynamic_fees:
                        changed = self._dynamic_fees.auto_manage_chains()
                        if changed:
                            logger.info("[enhanced_v2] Chain state changes: %s", changed)

                    # 3. Process realtime stream queue
                    if self._realtime_stream:
                        batch = self._realtime_stream.dequeue_batch(20)
                        for pp in batch:
                            pos = pp.position
                            # Score with ML
                            if self._ml_scorer:
                                score = await self._ml_scorer.score_position(pos)
                                if score.action in ("PREPARE", "EXECUTE_NOW"):
                                    await self._enqueue_signal(
                                        source="enhanced_ml",
                                        signal_type="ml_liquidation",
                                        chain_id=pos.chain_id,
                                        target=pos.borrower,
                                        estimated_profit_usd=float(pos.estimated_bonus_usd),
                                        confidence=score.confidence,
                                        raw_signal=pos,
                                    )

                    # 4. Scan for cascading liquidations
                    if self._cross_protocol:
                        opps = await self._cross_protocol.scan_all_addresses()
                        for opp in opps[:5]:  # Top 5
                            await self._enqueue_signal(
                                source="enhanced_cascade",
                                signal_type="cascading_liquidation",
                                chain_id=opp.chain_id,
                                target=opp.address,
                                estimated_profit_usd=float(opp.net_profit_usd),
                                confidence=opp.confidence,
                                raw_signal=opp,
                            )

                    # 5. Update dashboard
                    if self._live_dashboard:
                        self._live_dashboard.record_queue_depth(self._signal_queue.qsize())

                except Exception as exc:
                    logger.error("[enhanced_v2] Cycle error: %s", exc)
                    if self._circuit_breaker:
                        self._circuit_breaker.record_failure(str(exc))

                await asyncio.sleep(self._cycle_interval)
        except asyncio.CancelledError:
            pass

    # ── Module Shutdown ───────────────────────────────────────

    async def _shutdown_modules(self) -> None:
        """Shut down all initialized modules."""
        # Shutdown enhanced modules first
        await self._shutdown_enhanced_modules()

        # Shutdown Module 11 Timing Engine
        if self._timing_engine:
            try:
                await self._timing_engine.stop()
            except Exception:
                pass

        # Shutdown Module 10 RPC Gateway
        if self._rpc_gateway:
            try:
                await self._rpc_gateway.stop()
            except Exception:
                pass

        if self._execution_bridge:
            try:
                await self._execution_bridge.shutdown()
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
                    signals = self._omni_scope.consume()
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

    async def _rpc_gateway_loop(self) -> None:
        """Monitor the Enhanced RPC Gateway health and log stats."""
        if not self._rpc_gateway:
            return

        logger.info("[rpc_gateway] Module 10 health monitoring active")
        _last_down_warn = 0.0  # Rate-limit priority channel warnings
        try:
            while self._running:
                try:
                    # Periodic health check on priority channels
                    if hasattr(self._rpc_gateway, 'priority_channels'):
                        health = await self._rpc_gateway.priority_channels.health_check_all()
                        now = time.time()
                        down_chains = [cid for cid, count in health.items() if count == 0]
                        # Only warn about downed channels once every 5 minutes
                        if down_chains and now - _last_down_warn > 300:
                            for chain_id in down_chains:
                                logger.warning(
                                    "[rpc_gateway] Chain %d: all priority channels DOWN",
                                    chain_id,
                                )
                            _last_down_warn = now
                except Exception as exc:
                    logger.error("[rpc_gateway] Health check error: %s", exc)
                await asyncio.sleep(60)  # Check every 60s (not every half-cycle)
        except asyncio.CancelledError:
            pass

    async def _timing_engine_loop(self) -> None:
        """
        Feed watchlist positions into the Timing Optimizer Engine (Module 11).
        The engine runs autonomously (oracle watcher, mempool sniffer, ML
        predictions, JIT execution) — we just feed it fresh position data.
        """
        if not self._timing_engine:
            return

        logger.info("[timing_engine] Module 11 position feed loop started")
        try:
            while self._running:
                try:
                    # Collect positions from the signal queue snapshot
                    # Feed any ML-scored positions from enhanced modules
                    positions = []
                    if self._ml_scorer and self._realtime_stream:
                        batch = self._realtime_stream.dequeue_batch(50)
                        for pp in batch:
                            pos = pp.position
                            positions.append({
                                "user_address": pos.borrower,
                                "chain_id": pos.chain_id,
                                "pool_address": pos.metadata.get("pool_address", ""),
                                "debt_asset": pos.debt_asset,
                                "collateral_asset": pos.collateral_asset,
                                "total_debt_usd": float(pos.debt_usd),
                                "total_collateral_usd": float(pos.collateral_usd),
                                "health_factor": pos.health_factor,
                                "estimated_profit_usd": float(
                                    pos.estimated_bonus_usd
                                ),
                            })

                    if positions:
                        ingested = await self._timing_engine.ingest_positions(
                            positions
                        )
                        if ingested > 0:
                            logger.debug(
                                "[timing_engine] Fed %d positions to Module 11",
                                ingested,
                            )

                    # Record any execution results back to the orchestrator
                    stats = self._timing_engine.get_stats()
                    confirmed = stats.get("txs_confirmed", 0)
                    profit = stats.get("total_profit_usd", 0)
                    if profit > 0:
                        self.metrics.total_profit_usd = max(
                            self.metrics.total_profit_usd,
                            Decimal(str(profit)),
                        )

                except Exception as exc:
                    logger.error("[timing_engine] Feed loop error: %s", exc)
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
