#!/usr/bin/env python3
"""
CORE V2 -- Unified Execution System
====================================
Three-module stack replacing the fragmented MODULE_1 + profit_engine architecture.

Modules:
    1. SubgraphIndexer   -- Discovers all at-risk positions across chains/protocols
    2. UnifiedPipeline   -- Single-path execution with tiered sim/speed tradeoff
    3. PositionWatchlist  -- Real-time oracle-driven monitoring and trigger system

Usage:
    python -m core_v2.main

    # Or import and use programmatically:
    from core_v2 import SubgraphIndexer, UnifiedPipeline, PositionWatchlist
"""

from .subgraph_indexer import (
    SubgraphIndexer,
    LiquidationCandidate,
    Protocol,
    ReserveConfig,
    UserPosition,
    IndexerStats,
)

from .unified_pipeline import (
    UnifiedPipeline,
    PipelineCandidate,
    PipelineStage,
    ExecutionTier,
    RejectionReason,
    CircuitBreakerState,
    PipelineStats,
)

from .position_watchlist import (
    PositionWatchlist,
    WatchedPosition,
    WatchPriority,
    OracleFeed,
    WatchlistStats,
)

__all__ = [
    "SubgraphIndexer",
    "UnifiedPipeline",
    "PositionWatchlist",
    "LiquidationCandidate",
    "PipelineCandidate",
    "WatchedPosition",
    "Protocol",
    "PipelineStage",
    "ExecutionTier",
    "WatchPriority",
]

__version__ = "2.0.0"
