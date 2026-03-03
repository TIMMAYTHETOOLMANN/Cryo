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
