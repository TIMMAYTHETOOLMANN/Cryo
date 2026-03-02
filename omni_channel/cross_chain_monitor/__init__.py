"""
Cross-Chain Monitor
N-Hop Arbitrage Detection and Bridge Monitoring

This module detects cross-chain arbitrage opportunities by:
- Monitoring bridge liquidity across all chains
- Building multi-chain graph of token pools
- Finding profitable N-hop paths
- Detecting atomic arbitrage opportunities
"""

from .bridge_registry import BridgeRegistry, Bridge, BridgeType
from .multi_chain_graph import MultiChainGraph, Pool, Edge, NodeType
from .n_hop_pathfinder import NHopPathfinder, ArbitragePath
from .atomic_arbitrage import AtomicArbitrageDetector, CrossChainArb
from .liquidity_tracker import LiquidityTracker

__all__ = [
    # Main classes
    'BridgeRegistry',
    'Bridge',
    'BridgeType',
    'MultiChainGraph',
    'Pool',
    'Edge',
    'NodeType',
    'NHopPathfinder',
    'ArbitragePath',
    'AtomicArbitrageDetector',
    'CrossChainArb',
    'LiquidityTracker',
]
