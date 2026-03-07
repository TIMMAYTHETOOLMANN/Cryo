"""
core_v2.subgraph_indexer — re-exports from canonical location.

Canonical module:
    MODULE_1_LIQUIDATION_ENGINE.stage_1_detection.subgraph_indexer
"""

from MODULE_1_LIQUIDATION_ENGINE.stage_1_detection.subgraph_indexer import (  # noqa: F401
    SubgraphIndexer,
    LiquidationCandidate,
    Protocol,
    ReserveConfig,
    UserPosition,
    IndexerStats,
    AAVE_V3_SUBGRAPHS,
    COMPOUND_V3_SUBGRAPHS,
    AAVE_V3_POOLS,
    CHAIN_NAMES,
)

__all__ = [
    "SubgraphIndexer",
    "LiquidationCandidate",
    "Protocol",
    "ReserveConfig",
    "UserPosition",
    "IndexerStats",
    "AAVE_V3_SUBGRAPHS",
    "COMPOUND_V3_SUBGRAPHS",
    "AAVE_V3_POOLS",
    "CHAIN_NAMES",
]
