"""
╔══════════════════════════════════════════════════════════════════════════════╗
║  ⚠️  DEPRECATED — Use MODULE_9_OMNI_SCOPE instead.                          ║
║  This package is kept for backward compatibility only.                       ║
║  All new development should target MODULE_9_OMNI_SCOPE.OmniScopeEngine.    ║
╠══════════════════════════════════════════════════════════════════════════════╣
║  OMNI-SCOPE TRIANGULATION ENGINE v2.0                                       ║
║  Unified Predictive DeFi Value Extraction — From Zero to Full-Scale         ║
║                                                                              ║
║  9 Interlocking Modules:                                                     ║
║    Module 1: Deep-Crawl Archive Indexer  — Historical & Behavioral Intel    ║
║    Module 2: Mempool Microscope          — Pre-Execution Signal Sniffer     ║
║    Module 3: Yield & Arbitrage Solver    — Multi-Step Path Discovery        ║
║    Module 4: Alpha-Seeker Sentiment      — Off-Chain & Social Crawler       ║
║    Module 5: Static Analysis Engine      — Pre-Deployment MEV Discovery     ║
║    Module 6: ML Aggregator & Ranker      — Probabilistic Quality Brain     ║
║    Module 7: Zero-Capital Layer          — Self-Bootstrapping Execution     ║
║    Module 8: Execution Router            — Dynamic Strategy Dispatch        ║
║    Module 9: Performance Optimizer       — Throughput & Latency Tuning      ║
║                                                                              ║
║  Core Infrastructure:                                                        ║
║    Data Lake            — Kafka / Redis / TimescaleDB / Neo4j               ║
║    Signal Bus           — High-throughput deduplication & priority routing   ║
║    Bridge Registry      — 9+ bridges, 100s of routes, real-time quotes      ║
║    Protocol Classifier  — Day-one liquidation readiness on any protocol     ║
║    Engine               — Master orchestrator with phase-gated lifecycle    ║
╚══════════════════════════════════════════════════════════════════════════════╝
"""

__version__ = "2.0.0"  # DEPRECATED — canonical version is MODULE_9_OMNI_SCOPE v4.0

import warnings
warnings.warn(
    "omni_scope_v2 is deprecated. Use MODULE_9_OMNI_SCOPE.OmniScopeEngine instead.",
    DeprecationWarning,
    stacklevel=2,
)

from .engine import OmniScopeTriangulationEngine
from .bridge_registry import BridgeRegistry
from .protocol_classifier import ProtocolClassifier

__all__ = [
    "OmniScopeTriangulationEngine",
    "BridgeRegistry",
    "ProtocolClassifier",
]
