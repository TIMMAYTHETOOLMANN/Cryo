#!/usr/bin/env python3
"""
Multi-Chain Graph
Graph construction for cross-chain pathfinding

Models:
- Token pools on each chain as nodes
- Swap paths within chains as edges
- Bridge routes between chains as edges
"""

import asyncio
import time
from typing import Dict, List, Optional, Any, Set, Tuple
from dataclasses import dataclass, field
from enum import Enum
from collections import defaultdict
import heapq


class NodeType(Enum):
    """Node types in the graph"""
    TOKEN = "token"  # Token pool
    BRIDGE = "bridge"  # Bridge contract
    PROTOCOL = "protocol"  # Protocol contract


@dataclass
class Pool:
    """Token pool/liquidity pool data"""
    address: str
    chain_id: int
    token0: str
    token1: str
    reserve0: float
    reserve1: float
    fee_percent: float
    volume_24h_usd: float = 0
    tvl_usd: float = 0
    protocol: str = ""  # uniswap, curve, etc.
    is_active: bool = True


@dataclass
class Edge:
    """Edge in the graph (swap or bridge)"""
    source: str  # Node ID
    destination: str  # Node ID
    edge_type: str  # swap, bridge
    rate: float  # Exchange rate
    fee: float  # Fee in percent
    liquidity: float  # Available liquidity
    latency_ms: int = 0  # Expected execution time
    metadata: Dict[str, Any] = field(default_factory=dict)

    def effective_rate(self) -> float:
        """Get rate after fees"""
        return self.rate * (1 - self.fee)


@dataclass
class GraphNode:
    """Node in the multi-chain graph"""
    node_id: str
    node_type: NodeType
    chain_id: int
    address: str
    token: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)


class MultiChainGraph:
    """
    Multi-chain graph for pathfinding
    Nodes: Token pools on each chain
    Edges: Swaps within chain, bridges between chains
    """

    def __init__(self):
        # Adjacency list representation
        self._adjacency: Dict[str, List[Edge]] = defaultdict(list)

        # Node storage
        self._nodes: Dict[str, GraphNode] = {}
        self._pools: Dict[str, Pool] = {}

        # Chain tracking
        self._chains: Set[int] = set()

        # Statistics
        self.nodes_count = 0
        self.edges_count = 0

        print("🔗 Multi-Chain Graph initialized")

    def add_node(self, node_id: str, node_type: NodeType, chain_id: int,
                 address: str, token: str = "", metadata: Dict = None):
        """Add node to graph"""
        node = GraphNode(
            node_id=node_id,
            node_type=node_type,
            chain_id=chain_id,
            address=address,
            token=token,
            metadata=metadata or {}
        )

        self._nodes[node_id] = node
        self._chains.add(chain_id)
        self.nodes_count += 1

    def add_pool(self, pool: Pool):
        """Add liquidity pool to graph"""
        self._pools[pool.address] = pool

        # Create nodes for token0 and token1
        node0_id = f"{pool.chain_id}:{pool.token0}"
        node1_id = f"{pool.chain_id}:{pool.token1}"

        if node0_id not in self._nodes:
            self.add_node(node0_id, NodeType.TOKEN, pool.chain_id, pool.address, pool.token0)

        if node1_id not in self._nodes:
            self.add_node(node1_id, NodeType.TOKEN, pool.chain_id, pool.address, pool.token1)

        # Create bidirectional swap edges
        # Token0 -> Token1
        rate_0_to_1 = pool.reserve1 / pool.reserve0 if pool.reserve0 > 0 else 0
        edge_0_1 = Edge(
            source=node0_id,
            destination=node1_id,
            edge_type="swap",
            rate=rate_0_to_1,
            fee=pool.fee_percent,
            liquidity=min(pool.reserve0, pool.tvl_usd),
            metadata={'pool': pool.address, 'protocol': pool.protocol}
        )

        # Token1 -> Token0
        rate_1_to_0 = pool.reserve0 / pool.reserve1 if pool.reserve1 > 0 else 0
        edge_1_0 = Edge(
            source=node1_id,
            destination=node0_id,
            edge_type="swap",
            rate=rate_1_to_0,
            fee=pool.fee_percent,
            liquidity=min(pool.reserve1, pool.tvl_usd),
            metadata={'pool': pool.address, 'protocol': pool.protocol}
        )

        self._adjacency[node0_id].append(edge_0_1)
        self._adjacency[node1_id].append(edge_1_0)
        self.edges_count += 2

    def add_bridge_edge(self, src_chain: int, dst_chain: int, token: str,
                        rate: float, fee: float, liquidity: float,
                        bridge_name: str, latency_ms: int = 0):
        """Add bridge edge between chains"""
        src_node_id = f"{src_chain}:{token}"
        dst_node_id = f"{dst_chain}:{token}"

        # Only add if both nodes exist
        if src_node_id not in self._nodes or dst_node_id not in self._nodes:
            return

        edge = Edge(
            source=src_node_id,
            destination=dst_node_id,
            edge_type="bridge",
            rate=rate,
            fee=fee,
            liquidity=liquidity,
            latency_ms=latency_ms,
            metadata={'bridge': bridge_name}
        )

        self._adjacency[src_node_id].append(edge)
        self.edges_count += 1

        print(f"   🌉 Added bridge edge: {bridge_name} {token} {src_chain} -> {dst_chain}")

    def get_neighbors(self, node_id: str) -> List[Edge]:
        """Get all edges from a node"""
        return self._adjacency.get(node_id, [])

    def get_node(self, node_id: str) -> Optional[GraphNode]:
        """Get node by ID"""
        return self._nodes.get(node_id)

    def get_pools_for_chain(self, chain_id: int) -> List[Pool]:
        """Get all pools on a chain"""
        return [p for p in self._pools.values() if p.chain_id == chain_id]

    def get_chains(self) -> Set[int]:
        """Get all chains in graph"""
        return self._chains

    def get_all_nodes(self) -> List[GraphNode]:
        """Get all nodes"""
        return list(self._nodes.values())

    def get_all_edges(self) -> List[Edge]:
        """Get all edges"""
        all_edges = []
        for edges in self._adjacency.values():
            all_edges.extend(edges)
        return all_edges

    def find_path(self, start: str, end: str, amount: float = None) -> Optional[List[Edge]]:
        """Find path between two nodes using BFS"""
        if start not in self._nodes or end not in self._nodes:
            return None

        visited = set()
        queue = [(start, [])]

        while queue:
            current, path = queue.pop(0)

            if current == end:
                return path

            if current in visited:
                continue

            visited.add(current)

            for edge in self._adjacency.get(current, []):
                # Check liquidity if amount specified
                if amount and edge.liquidity < amount:
                    continue

                new_path = path + [edge]
                queue.append((edge.destination, new_path))

        return None

    def find_best_path(self, start: str, end: str, amount: float) -> Optional[Tuple[List[Edge], float]]:
        """Find best path with maximum output using modified Dijkstra"""
        if start not in self._nodes or end not in self._nodes:
            return None

        # (negative_output, current_node, path)
        # Negative because heapq is min-heap
        heap = [(0, start, [])]
        visited = set()

        while heap:
            neg_output, current, path = heapq.heappop(heap)

            if current == end:
                return (path, -neg_output)

            if current in visited:
                continue

            visited.add(current)

            for edge in self._adjacency.get(current, []):
                if edge.liquidity < amount:
                    continue

                # Calculate output through this edge
                output = amount * edge.effective_rate()

                new_path = path + [edge]
                heapq.heappush(heap, (neg_output - output, edge.destination, new_path))

        return None

    def get_stats(self) -> Dict:
        """Get graph statistics"""
        return {
            'nodes': self.nodes_count,
            'edges': self.edges_count,
            'chains': len(self._chains),
            'pools': len(self._pools),
        }

    def export_graph(self) -> Dict:
        """Export graph data for visualization"""
        return {
            'nodes': [
                {
                    'id': n.node_id,
                    'type': n.node_type.value,
                    'chain': n.chain_id,
                    'token': n.token,
                }
                for n in self._nodes.values()
            ],
            'edges': [
                {
                    'source': e.source,
                    'target': e.destination,
                    'type': e.edge_type,
                    'rate': e.rate,
                    'fee': e.fee,
                    'liquidity': e.liquidity,
                }
                for edges in self._adjacency.values()
                for e in edges
            ]
        }


# Helper functions for graph construction
def create_pool_node_id(pool: Pool) -> str:
    """Create unique node ID for pool"""
    return f"{pool.chain_id}:{pool.address.lower()}"


def create_token_node_id(chain_id: int, token: str) -> str:
    """Create unique node ID for token on chain"""
    return f"{chain_id}:{token.upper()}"


def calculate_swap_output(amount_in: float, reserve_in: float, reserve_out: float, fee: float) -> float:
    """Calculate output amount for constant product AMM"""
    if reserve_in <= 0 or reserve_out <= 0:
        return 0

    amount_in_with_fee = amount_in * (1 - fee)
    numerator = amount_in_with_fee * reserve_out
    denominator = reserve_in + amount_in_with_fee

    return numerator / denominator if denominator > 0 else 0


def calculate_price_impact(amount_in: float, reserve_in: float, reserve_out: float, fee: float) -> float:
    """Calculate price impact of swap"""
    if reserve_in <= 0 or reserve_out <= 0:
        return 1.0

    # Spot price before
    spot_price_before = reserve_out / reserve_in

    # Spot price after (approximate)
    new_reserve_in = reserve_in + amount_in * (1 - fee)
    new_reserve_out = reserve_out - calculate_swap_output(amount_in, reserve_in, reserve_out, fee)

    if new_reserve_in <= 0 or new_reserve_out <= 0:
        return 1.0

    spot_price_after = new_reserve_out / new_reserve_in

    # Price impact
    return abs(spot_price_after - spot_price_before) / spot_price_before if spot_price_before > 0 else 1.0
