#!/usr/bin/env python3
"""
hub.registry — Module Registry
================================
Central catalogue of every subsystem in CRYO.  Each module is
described by a :class:`ModuleInfo` dataclass and held in a flat
dict keyed by a short canonical name.

The registry is deliberately *import-lazy*: it records the dotted
import path and callable factory but does **not** import anything
until :meth:`ModuleRegistry.initialize` (or its per-module variant)
is called.  This keeps startup fast and avoids import-order issues.

Usage::

    reg = ModuleRegistry()
    reg.discover()          # populate from built-in catalogue
    reg.initialize("profit_engine")
    print(reg.status())
"""

from __future__ import annotations

import importlib
import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)


# ── Status enum ────────────────────────────────────────────────────

class ModuleStatus(Enum):
    REGISTERED = "registered"
    INITIALIZING = "initializing"
    READY = "ready"
    RUNNING = "running"
    STOPPED = "stopped"
    ERROR = "error"


# ── Module descriptor ─────────────────────────────────────────────

@dataclass
class ModuleInfo:
    """Describes a single subsystem that the hub can manage."""

    name: str
    category: str               # e.g. "detection", "recon", "execution", …
    description: str
    import_path: str            # e.g. "profit_engine.triangulated_profit_engine"
    factory: str                # e.g. "TriangulatedProfitEngine"
    status: ModuleStatus = ModuleStatus.REGISTERED
    instance: Any = field(default=None, repr=False)
    error: Optional[str] = None
    initialized_at: Optional[float] = None

    # Dependency names (other ModuleInfo.name values)
    depends_on: List[str] = field(default_factory=list)

    def is_available(self) -> bool:
        return self.status in (ModuleStatus.READY, ModuleStatus.RUNNING)


# ── Built-in catalogue ────────────────────────────────────────────

def _built_in_modules() -> List[ModuleInfo]:
    """Return the static list of known subsystems."""
    return [
        # ── Detection ──────────────────────────────────────────
        ModuleInfo(
            name="opportunity_scanner",
            category="detection",
            description="6-vector opportunity scanner (liquidation, arb, backrun, "
                        "reserve, cross-chain, oracle)",
            import_path="profit_engine.opportunity_scanner",
            factory="OpportunityScanner",
        ),
        ModuleInfo(
            name="collateral_health_monitor",
            category="detection",
            description="Aave v2/v3, Compound v2, MakerDAO health factor monitor",
            import_path="MODULE_1_LIQUIDATION_ENGINE.stage_1_detection.collateral_health_monitor",
            factory="CollateralHealthMonitor",
        ),
        ModuleInfo(
            name="dex_arb_scanner",
            category="detection",
            description="DEX arbitrage opportunity scanner",
            import_path="MODULE_1_LIQUIDATION_ENGINE.stage_1_detection.dex_arb_scanner",
            factory="DexArbScanner",
        ),
        ModuleInfo(
            name="nft_detector",
            category="detection",
            description="NFT liquidation detector",
            import_path="MODULE_1_LIQUIDATION_ENGINE.stage_1_detection.nft_detector",
            factory="NFTLiquidationDetector",
        ),

        # ── Reconnaissance ─────────────────────────────────────
        ModuleInfo(
            name="mempool_radar",
            category="recon",
            description="Multi-provider mempool monitoring (Bloxroute, Infura, "
                        "Blocknative)",
            import_path="omni_channel.mempool_radar.signal_merger",
            factory="SignalMerger",
        ),
        ModuleInfo(
            name="contract_crawler",
            category="recon",
            description="Protocol classifier, deployer monitor, clone detector, "
                        "funding intel, KOL tracker, social mindshare",
            import_path="omni_channel.contract_crawler.protocol_classifier",
            factory="ProtocolClassifier",
        ),
        ModuleInfo(
            name="vulnerability_scanner",
            category="recon",
            description="Static vulnerability scanner + MEV pattern matcher",
            import_path="omni_channel.static_analyzer.vulnerability_scanner",
            factory="VulnerabilityScanner",
        ),
        ModuleInfo(
            name="cross_chain_monitor",
            category="recon",
            description="Bridge registry, multi-chain graph, N-hop pathfinder, "
                        "liquidity tracker",
            import_path="omni_channel.cross_chain_monitor.multi_chain_graph",
            factory="MultiChainGraph",
        ),
        ModuleInfo(
            name="archive_indexer",
            category="recon",
            description="Archive indexer — deep crawl for serial liquidatees",
            import_path="MODULE_9_OMNI_SCOPE.archive_indexer.deep_crawl",
            factory="ArchiveIndexer",
        ),
        ModuleInfo(
            name="alpha_seeker",
            category="recon",
            description="Sentiment crawler — governance + social alpha",
            import_path="MODULE_9_OMNI_SCOPE.alpha_seeker.sentiment_crawler",
            factory="AlphaSeeker",
        ),

        # ── Analysis ───────────────────────────────────────────
        ModuleInfo(
            name="ml_ranker",
            category="analysis",
            description="ML quality scorer + competition estimator + dynamic "
                        "router",
            import_path="omni_channel.ml_aggregator.quality_scorer",
            factory="QualityScorer",
        ),
        ModuleInfo(
            name="profitability_calculator",
            category="analysis",
            description="Multi-exit profitability calculator (Module 1 Stage 2)",
            import_path="MODULE_1_LIQUIDATION_ENGINE.stage_2_analysis.profitability_calculator",
            factory="ProfitabilityCalculator",
        ),
        ModuleInfo(
            name="risk_manager",
            category="analysis",
            description="Risk manager — caps, thresholds, blacklists",
            import_path="MODULE_1_LIQUIDATION_ENGINE.stage_2_analysis.risk_manager",
            factory="RiskManager",
        ),
        ModuleInfo(
            name="gas_optimizer",
            category="analysis",
            description="Dynamic gas estimation & margin validation",
            import_path="profit_engine.gas_optimizer",
            factory="GasOptimizer",
        ),

        # ── Execution ──────────────────────────────────────────
        ModuleInfo(
            name="profit_engine",
            category="execution",
            description="Triangulated Profit Engine — master orchestrator for "
                        "scanner → gas → flash loan → execution → ledger loop",
            import_path="profit_engine.triangulated_profit_engine",
            factory="TriangulatedProfitEngine",
        ),
        ModuleInfo(
            name="flash_loan_router",
            category="execution",
            description="Flash loan provider router (Aave, dYdX, Balancer)",
            import_path="profit_engine.flash_loan_router",
            factory="FlashLoanRouter",
        ),
        ModuleInfo(
            name="execution_manager",
            category="execution",
            description="Execution manager — routes requests to typed executors",
            import_path="omni_channel.execution_router.execution_manager",
            factory="ExecutionManager",
        ),
        ModuleInfo(
            name="zero_revert_pipeline",
            category="execution",
            description="Zero-revert execution pipeline — oracle reactor + "
                        "block watcher + mempool sniffer",
            import_path="profit_engine.zero_revert_pipeline",
            factory="ZeroRevertPipeline",
        ),
        ModuleInfo(
            name="jit_engine",
            category="execution",
            description="JIT liquidation timing optimizer",
            import_path="profit_engine.jit_liquidation_engine",
            factory="JITLiquidationEngine",
        ),

        # ── Monitoring / Analytics ─────────────────────────────
        ModuleInfo(
            name="profit_ledger",
            category="monitoring",
            description="P&L ledger with phase transitions",
            import_path="profit_engine.profit_ledger",
            factory="ProfitLedger",
        ),
        ModuleInfo(
            name="heat_map",
            category="monitoring",
            description="Heat map — profitable pattern tracker by time & chain",
            import_path="profit_engine.heat_map",
            factory="HeatMap",
        ),
        ModuleInfo(
            name="analytics_engine",
            category="monitoring",
            description="A/B testing, RL parameter tuning, anomaly detection",
            import_path="MODULE_1_LIQUIDATION_ENGINE.stage_7_analytics.analytics_engine",
            factory="AnalyticsEngine",
        ),

        # ── Orchestrators ──────────────────────────────────────
        ModuleInfo(
            name="module1_pipeline",
            category="orchestrator",
            description="Module 1 — 8-stage pipeline (preflight → analytics)",
            import_path="MODULE_1_LIQUIDATION_ENGINE.pipeline",
            factory="Pipeline",
        ),
        ModuleInfo(
            name="omni_scope",
            category="orchestrator",
            description="Module 9 — 5 detector arrays + ML ranker",
            import_path="MODULE_9_OMNI_SCOPE.engine",
            factory="OmniScopeEngine",
        ),
        ModuleInfo(
            name="omni_orchestrator",
            category="orchestrator",
            description="Omni-Channel orchestrator — mempool radar + Phase 2 "
                        "modules",
            import_path="omni_channel.omni_orchestrator",
            factory="OmniOrchestrator",
        ),

        # ── Enhanced Modules v2.0 ──────────────────────────────
        ModuleInfo(
            name="ml_probability_scorer",
            category="enhanced_detection",
            description="ML-based liquidation probability scoring (GBM model)",
            import_path="enhanced_modules.module_1_opportunity_detector.ml_probability_scorer",
            factory="MLProbabilityScorer",
        ),
        ModuleInfo(
            name="cross_protocol_detector",
            category="enhanced_detection",
            description="Cross-protocol cascading liquidation detector",
            import_path="enhanced_modules.module_1_opportunity_detector.cross_protocol_detector",
            factory="CrossProtocolDetector",
        ),
        ModuleInfo(
            name="realtime_stream",
            category="enhanced_detection",
            description="WebSocket real-time event streaming with priority queue",
            import_path="enhanced_modules.module_1_opportunity_detector.realtime_stream",
            factory="RealtimeStreamIngester",
        ),
        ModuleInfo(
            name="multi_exit_optimizer",
            category="enhanced_analysis",
            description="8-strategy multi-exit profit optimizer",
            import_path="enhanced_modules.module_2_profitability_calculator.multi_exit_optimizer",
            factory="MultiExitOptimizer",
        ),
        ModuleInfo(
            name="gas_price_predictor",
            category="enhanced_analysis",
            description="Hybrid EIP-1559 + EMA gas price predictor",
            import_path="enhanced_modules.module_2_profitability_calculator.gas_price_predictor",
            factory="GasPricePredictor",
        ),
        ModuleInfo(
            name="flash_loan_fee_minimizer",
            category="enhanced_analysis",
            description="Zero-fee flash loan routing and split-loan optimization",
            import_path="enhanced_modules.module_2_profitability_calculator.flash_loan_fee_minimizer",
            factory="FlashLoanFeeMinimizer",
        ),
        ModuleInfo(
            name="adaptive_flash_router",
            category="enhanced_execution",
            description="Success-rate-weighted adaptive flash loan provider router",
            import_path="enhanced_modules.module_3_flash_loan_aggregator.adaptive_router",
            factory="AdaptiveFlashLoanRouter",
        ),
        ModuleInfo(
            name="multi_provider_fallback",
            category="enhanced_execution",
            description="Cascading fallback chain with per-provider circuit breakers",
            import_path="enhanced_modules.module_3_flash_loan_aggregator.multi_provider_fallback",
            factory="MultiProviderFallback",
        ),
        ModuleInfo(
            name="zero_fee_exploiter",
            category="enhanced_execution",
            description="Maximizes zero-fee flash loan provider usage",
            import_path="enhanced_modules.module_3_flash_loan_aggregator.zero_fee_exploiter",
            factory="ZeroFeeExploiter",
        ),
        ModuleInfo(
            name="protocol_adapter_registry",
            category="enhanced_execution",
            description="10+ protocol liquidation adapters (Aave, Compound, Morpho, Euler, etc.)",
            import_path="enhanced_modules.module_4_liquidation_executor.expanded_protocols",
            factory="ProtocolAdapterRegistry",
        ),
        ModuleInfo(
            name="gas_golfer",
            category="enhanced_execution",
            description="Gas optimization: multicall batching, permit, calldata compression",
            import_path="enhanced_modules.module_4_liquidation_executor.gas_golfer",
            factory="GasGolfer",
        ),
        ModuleInfo(
            name="safety_guard",
            category="enhanced_execution",
            description="Pre-execution safety: reentrancy, profit recheck, gas guard",
            import_path="enhanced_modules.module_4_liquidation_executor.safety_guard",
            factory="SafetyGuard",
        ),
        ModuleInfo(
            name="batch_liquidation_coordinator",
            category="enhanced_crosschain",
            description="Batch same-chain liquidations into single multicall TX",
            import_path="enhanced_modules.module_5_cross_chain.batch_liquidation_coordinator",
            factory="BatchLiquidationCoordinator",
        ),
        ModuleInfo(
            name="light_client_verifier",
            category="enhanced_crosschain",
            description="Light-client verification for cross-chain bridge delivery",
            import_path="enhanced_modules.module_5_cross_chain.light_client_verifier",
            factory="LightClientVerifier",
        ),
        ModuleInfo(
            name="dynamic_fee_manager",
            category="enhanced_crosschain",
            description="Real-time cross-chain gas tracking and auto chain management",
            import_path="enhanced_modules.module_5_cross_chain.dynamic_fee_manager",
            factory="DynamicFeeManager",
        ),
        ModuleInfo(
            name="twap_oracle",
            category="enhanced_risk",
            description="TWAP oracle: Uniswap V3 + Chainlink manipulation-resistant prices",
            import_path="enhanced_modules.module_6_risk_protection.twap_oracle",
            factory="TWAPOracle",
        ),
        ModuleInfo(
            name="dynamic_slippage",
            category="enhanced_risk",
            description="Volatility-responsive dynamic slippage calculator",
            import_path="enhanced_modules.module_6_risk_protection.dynamic_slippage",
            factory="DynamicSlippageCalculator",
        ),
        ModuleInfo(
            name="circuit_breaker_v2",
            category="enhanced_risk",
            description="3-tier circuit breaker (GREEN/YELLOW/RED) with exponential backoff",
            import_path="enhanced_modules.module_6_risk_protection.circuit_breaker_v2",
            factory="CircuitBreakerV2",
        ),
        ModuleInfo(
            name="multi_block_mev",
            category="enhanced_mev",
            description="Multi-block MEV execution and MegaBundle submission",
            import_path="enhanced_modules.module_7_mev_strategy.multi_block_mev",
            factory="MultiBlockMEV",
        ),
        ModuleInfo(
            name="order_flow_capture",
            category="enhanced_mev",
            description="Pending TX analysis and backrun opportunity detection",
            import_path="enhanced_modules.module_7_mev_strategy.order_flow_capture",
            factory="OrderFlowCapture",
        ),
        ModuleInfo(
            name="private_mempool_aggregator",
            category="enhanced_mev",
            description="Multi-provider private mempool aggregation (Flashbots, bloXroute, etc.)",
            import_path="enhanced_modules.module_7_mev_strategy.private_mempool_aggregator",
            factory="PrivateMempoolAggregator",
        ),
        ModuleInfo(
            name="live_dashboard",
            category="enhanced_analytics",
            description="Real-time Prometheus metrics + JSON API dashboard",
            import_path="enhanced_modules.module_8_analytics_dashboard.live_dashboard",
            factory="LiveDashboard",
        ),
        ModuleInfo(
            name="ab_testing_engine",
            category="enhanced_analytics",
            description="N-variant A/B testing with Bayesian significance",
            import_path="enhanced_modules.module_8_analytics_dashboard.ab_testing_engine",
            factory="ABTestingEngine",
        ),
        ModuleInfo(
            name="anomaly_detector",
            category="enhanced_analytics",
            description="Isolation Forest anomaly detection for profit/gas/competitor patterns",
            import_path="enhanced_modules.module_8_analytics_dashboard.anomaly_detector",
            factory="AnomalyDetector",
        ),

        # ── Omni-Scope Triangulation Engine v2 ─────────────────
        ModuleInfo(
            name="omni_scope_v2",
            category="orchestrator",
            description="Unified Omni-Scope Triangulation Engine v2 — 7 interlocking modules",
            import_path="omni_scope_v2.engine",
            factory="OmniScopeTriangulationEngine",
        ),
        ModuleInfo(
            name="v2_deep_crawl_indexer",
            category="v2_detection",
            description="Module 1: Deep-Crawl Archive Indexer — subgraph, wallet clusters, governance",
            import_path="omni_scope_v2.module_1_deep_crawl_indexer",
            factory="DeepCrawlIndexer",
        ),
        ModuleInfo(
            name="v2_mempool_microscope",
            category="v2_detection",
            description="Module 2: Mempool Microscope — multi-provider sniffer, fork verification, gas LSTM",
            import_path="omni_scope_v2.module_2_mempool_microscope",
            factory="MempoolMicroscope",
        ),
        ModuleInfo(
            name="v2_hyper_solver",
            category="v2_analysis",
            description="Module 3: Yield & Arbitrage Hyper-Solver — Bellman-Ford, yield spread, cross-chain",
            import_path="omni_scope_v2.module_3_hyper_solver",
            factory="HyperSolver",
        ),
        ModuleInfo(
            name="v2_alpha_seeker",
            category="v2_detection",
            description="Module 4: Alpha-Seeker — social NLP, KOL tracking, funding rounds, contract clones",
            import_path="omni_scope_v2.module_4_alpha_seeker",
            factory="AlphaSeekerCrawler",
        ),
        ModuleInfo(
            name="v2_static_analysis",
            category="v2_analysis",
            description="Module 5: Static Analysis Engine — vulnerability patterns, MEV scoring, liquidation inference",
            import_path="omni_scope_v2.module_5_static_analysis",
            factory="StaticAnalysisEngine",
        ),
        ModuleInfo(
            name="v2_ml_aggregator",
            category="v2_analysis",
            description="Module 6: ML Aggregator & Ranker — GBM scoring, strategy routing, online retraining",
            import_path="omni_scope_v2.module_6_ml_aggregator",
            factory="MLAggregator",
        ),
        ModuleInfo(
            name="v2_zero_capital",
            category="v2_execution",
            description="Module 7: Zero-Capital Layer — flash loan surplus, gas bootstrap, reinvestment",
            import_path="omni_scope_v2.module_7_zero_capital",
            factory="ZeroCapitalLayer",
        ),

        # ── Module 10: Intelligent RPC Gateway & Load Balancer ──
        ModuleInfo(
            name="enhanced_rpc_gateway",
            category="infrastructure",
            description="Module 10: Enhanced RPC Gateway — unified facade over all 96+ endpoints",
            import_path="MODULE_10_RPC_GATEWAY.gateway",
            factory="EnhancedRPCGateway",
        ),
        ModuleInfo(
            name="priority_channel_manager",
            category="infrastructure",
            description="Module 10: Dedicated high-priority RPC channels for liquidation execution",
            import_path="MODULE_10_RPC_GATEWAY.priority_channel_manager",
            factory="PriorityChannelManager",
        ),
        ModuleInfo(
            name="resilient_ws_manager",
            category="infrastructure",
            description="Module 10: Persistent WebSocket connections with exponential backoff reconnect",
            import_path="MODULE_10_RPC_GATEWAY.websocket_manager",
            factory="ResilientWebSocketManager",
        ),
        ModuleInfo(
            name="distributed_rate_limiter",
            category="infrastructure",
            description="Module 10: Redis-backed cross-instance rate coordination",
            import_path="MODULE_10_RPC_GATEWAY.distributed_rate_limiter",
            factory="DistributedRateLimiter",
        ),
        ModuleInfo(
            name="method_optimizer",
            category="infrastructure",
            description="Module 10: Per-method RPC routing hints, cost tiers, and TTL/LRU caching",
            import_path="MODULE_10_RPC_GATEWAY.method_optimizer",
            factory="MethodOptimizer",
        ),
    ]


# ── Registry ──────────────────────────────────────────────────────

class ModuleRegistry:
    """
    Central registry that discovers, tracks, and lazily initialises
    every CRYO subsystem.
    """

    def __init__(self):
        self._modules: Dict[str, ModuleInfo] = {}

    # ── Discovery ──────────────────────────────────────────────

    def discover(self) -> "ModuleRegistry":
        """Populate the registry from the built-in catalogue."""
        for info in _built_in_modules():
            self._modules[info.name] = info
        logger.info("Registry discovered %d modules", len(self._modules))
        return self

    def register(self, info: ModuleInfo) -> None:
        """Add or overwrite a module entry."""
        self._modules[info.name] = info

    # ── Initialization ─────────────────────────────────────────

    def initialize(self, name: str, *args: Any, **kwargs: Any) -> Any:
        """
        Import and instantiate a single module by name.

        Returns the module instance (also stored on ``info.instance``).
        """
        info = self._modules.get(name)
        if info is None:
            raise KeyError(f"Unknown module: {name!r}")

        if info.is_available():
            return info.instance

        info.status = ModuleStatus.INITIALIZING
        try:
            mod = importlib.import_module(info.import_path)
            cls = getattr(mod, info.factory)
            info.instance = cls(*args, **kwargs)
            info.status = ModuleStatus.READY
            info.error = None
            info.initialized_at = time.time()
            logger.info("Initialized %s (%s.%s)", name, info.import_path, info.factory)
        except Exception as exc:
            info.status = ModuleStatus.ERROR
            info.error = str(exc)
            logger.warning("Failed to init %s: %s", name, exc)
            raise
        return info.instance

    def initialize_category(self, category: str, **kwargs: Any) -> List[str]:
        """Initialize every module in *category*.  Returns names that succeeded."""
        ok: List[str] = []
        for info in self._modules.values():
            if info.category != category:
                continue
            try:
                self.initialize(info.name, **kwargs)
                ok.append(info.name)
            except Exception:
                pass  # already logged
        return ok

    # ── Queries ────────────────────────────────────────────────

    def get(self, name: str) -> Optional[ModuleInfo]:
        return self._modules.get(name)

    def get_instance(self, name: str) -> Any:
        info = self._modules.get(name)
        return info.instance if info else None

    def list_modules(self, category: Optional[str] = None) -> List[ModuleInfo]:
        mods = list(self._modules.values())
        if category:
            mods = [m for m in mods if m.category == category]
        return mods

    @property
    def categories(self) -> List[str]:
        return sorted({m.category for m in self._modules.values()})

    # ── Status ─────────────────────────────────────────────────

    def status(self) -> Dict[str, Any]:
        """Return a status dict suitable for JSON serialisation or display."""
        by_cat: Dict[str, List[Dict[str, Any]]] = {}
        for info in self._modules.values():
            entry = {
                "name": info.name,
                "status": info.status.value,
                "description": info.description,
            }
            if info.error:
                entry["error"] = info.error
            by_cat.setdefault(info.category, []).append(entry)

        total = len(self._modules)
        ready = sum(1 for m in self._modules.values() if m.is_available())
        return {
            "total_modules": total,
            "ready": ready,
            "pending": total - ready,
            "categories": by_cat,
        }

    def print_status(self) -> None:
        """Pretty-print the registry to stdout."""
        st = self.status()
        print()
        print("=" * 72)
        print("  CRYO MODULE REGISTRY")
        print("=" * 72)
        print(f"  Total modules: {st['total_modules']}   "
              f"Ready: {st['ready']}   Pending: {st['pending']}")
        print()
        for cat, mods in st["categories"].items():
            print(f"  [{cat.upper()}]")
            for m in mods:
                icon = "+" if m["status"] in ("ready", "running") else "-"
                line = f"    {icon} {m['name']:30s}  {m['status']:12s}  {m['description'][:50]}"
                print(line)
            print()
        print("=" * 72)
