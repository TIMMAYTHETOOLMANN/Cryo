#!/usr/bin/env python3
"""
N-Hop Pathfinder
Find profitable multi-hop arbitrage paths across chains

Uses:
- Bellman-Ford for negative cycle detection
- DFS/BFS for path enumeration
- Profitability calculation with gas costs
"""

import asyncio
import time
from typing import Dict, List, Optional, Any, Set, Tuple
from dataclasses import dataclass, field
from collections import defaultdict
import heapq

from .multi_chain_graph import MultiChainGraph, Edge, GraphNode, Pool


@dataclass
class ArbitragePath:
    """Arbitrage path with profitability data"""
    path_id: str
    edges: List[Edge]
    start_token: str
    start_chain: int
    end_token: str
    end_chain: int
    hops: int
    input_amount: float
    output_amount: float
    profit_amount: float
    profit_percent: float
    total_fees: float
    total_gas_usd: float
    execution_time_ms: int
    bridges_used: List[str] = field(default_factory=list)
    protocols_used: List[str] = field(default_factory=list)
    is_profitable: bool = False
    risk_score: float = 0  # 0-1, higher = riskier


@dataclass
class PathfindingResult:
    """Result of pathfinding operation"""
    paths_found: int
    profitable_paths: List[ArbitragePath]
    best_path: Optional[ArbitragePath]
    search_time_ms: int
    chains_searched: Set[int]
    tokens_searched: Set[str]


class NHopPathfinder:
    """
    N-Hop arbitrage pathfinder
    Finds profitable multi-hop paths across chains
    """

    def __init__(self, graph: MultiChainGraph, config: Dict[str, Any] = None):
        self.graph = graph
        self.config = config or {}

        # Configuration
        self.max_hops = self.config.get('max_hops', 6)  # Maximum hops in path
        self.min_profit_usd = self.config.get('min_profit_usd', 10)  # Minimum profit to consider
        self.min_profit_percent = self.config.get('min_profit_percent', 0.001)  # 0.1%
        self.gas_prices: Dict[int, float] = self.config.get('gas_prices', {})  # Chain ID -> gas price

        # Statistics
        self.paths_analyzed = 0
        self.profitable_paths_found = 0

        print("🔍 N-Hop Pathfinder initialized")

    def find_all_arbitrage_paths(self, start_token: str = None, start_chain: int = None,
                                  amount_usd: float = 10000) -> PathfindingResult:
        """Find all arbitrage paths starting from token/chain"""
        start_time = time.time()

        profitable_paths = []
        chains_searched = set()
        tokens_searched = set()

        # Get starting nodes
        start_nodes = self._get_start_nodes(start_token, start_chain)

        for start_node in start_nodes:
            # Find cycles back to this node
            cycles = self._find_cycles(start_node, self.max_hops)

            for cycle in cycles:
                path = self._evaluate_cycle(cycle, amount_usd)
                if path and path.is_profitable:
                    profitable_paths.append(path)
                    self.profitable_paths_found += 1

                self.paths_analyzed += 1
                chains_searched.add(start_node.chain_id)
                if start_node.token:
                    tokens_searched.add(start_node.token)

        # Sort by profit percent
        profitable_paths.sort(key=lambda p: p.profit_percent, reverse=True)

        search_time_ms = int((time.time() - start_time) * 1000)

        return PathfindingResult(
            paths_found=len(profitable_paths),
            profitable_paths=profitable_paths,
            best_path=profitable_paths[0] if profitable_paths else None,
            search_time_ms=search_time_ms,
            chains_searched=chains_searched,
            tokens_searched=tokens_searched
        )

    def find_cross_chain_arb(self, src_chain: int, dst_chain: int,
                              token: str, amount: float) -> List[ArbitragePath]:
        """Find arbitrage paths between two chains for specific token"""
        paths = []

        # Get token nodes on both chains
        src_node_id = f"{src_chain}:{token}"
        dst_node_id = f"{dst_chain}:{token}"

        src_node = self.graph.get_node(src_node_id)
        dst_node = self.graph.get_node(dst_node_id)

        if not src_node or not dst_node:
            return paths

        # Find paths from src to dst and back
        forward_paths = self._find_paths_to_node(src_node, dst_node, self.max_hops // 2)
        backward_paths = self._find_paths_to_node(dst_node, src_node, self.max_hops // 2)

        # Combine forward and backward paths
        for forward in forward_paths:
            for backward in backward_paths:
                # Check if they form a valid cycle
                if self._paths_connect(forward, backward):
                    cycle = forward + backward
                    path = self._evaluate_cycle(cycle, amount)
                    if path and path.is_profitable:
                        paths.append(path)

        return paths

    def _get_start_nodes(self, token: str = None, chain: int = None) -> List[GraphNode]:
        """Get starting nodes for search"""
        nodes = []

        for node in self.graph.get_all_nodes():
            if token and node.token != token:
                continue
            if chain and node.chain_id != chain:
                continue
            nodes.append(node)

        # If no filters, return sample of nodes
        if not token and not chain:
            nodes = nodes[:50]  # Limit to 50 starting nodes

        return nodes

    def _find_cycles(self, start_node: GraphNode, max_hops: int) -> List[List[Edge]]:
        """Find all cycles from start node using DFS"""
        cycles = []

        def dfs(current: str, path: List[Edge], visited: Set[str], depth: int):
            if depth > max_hops:
                return

            for edge in self.graph.get_neighbors(current):
                new_path = path + [edge]

                # Check if we're back at start
                if edge.destination == start_node.node_id and len(new_path) >= 2:
                    cycles.append(new_path)
                    continue

                # Continue DFS if not visited
                if edge.destination not in visited and depth < max_hops:
                    visited.add(edge.destination)
                    dfs(edge.destination, new_path, visited, depth + 1)
                    visited.remove(edge.destination)

        visited = {start_node.node_id}
        dfs(start_node.node_id, [], visited, 0)

        return cycles

    def _find_paths_to_node(self, start: GraphNode, end: GraphNode,
                            max_hops: int) -> List[List[Edge]]:
        """Find all paths from start to end node"""
        paths = []

        def dfs(current: str, path: List[Edge], depth: int):
            if depth > max_hops:
                return

            for edge in self.graph.get_neighbors(current):
                new_path = path + [edge]

                if edge.destination == end.node_id:
                    paths.append(new_path)
                    continue

                if depth < max_hops:
                    dfs(edge.destination, new_path, depth + 1)

        dfs(start.node_id, [], 0)

        return paths

    def _paths_connect(self, path1: List[Edge], path2: List[Edge]) -> bool:
        """Check if two paths connect end-to-end"""
        if not path1 or not path2:
            return False
        return path1[-1].destination == path2[0].source

    def _evaluate_cycle(self, cycle: List[Edge], input_amount: float) -> Optional[ArbitragePath]:
        """Evaluate profitability of a cycle"""
        if not cycle:
            return None

        # Calculate output through cycle
        amount = input_amount
        total_fees = 0
        bridges_used = []
        protocols_used = []

        for edge in cycle:
            output = amount * edge.effective_rate()
            total_fees += amount * edge.fee
            amount = output

            if edge.edge_type == "bridge":
                bridges_used.append(edge.metadata.get('bridge', 'unknown'))
            elif edge.edge_type == "swap":
                protocols_used.append(edge.metadata.get('protocol', 'unknown'))

        output_amount = amount
        profit_amount = output_amount - input_amount
        profit_percent = (profit_amount / input_amount) if input_amount > 0 else 0

        # Calculate gas costs
        chains_involved = set()
        for edge in cycle:
            # Extract chain from node ID
            src_chain = int(edge.source.split(':')[0])
            dst_chain = int(edge.destination.split(':')[0])
            chains_involved.add(src_chain)
            chains_involved.add(dst_chain)

        total_gas_usd = sum(self.gas_prices.get(c, 0.1) for c in chains_involved)

        # Net profit after gas
        net_profit = profit_amount - total_gas_usd
        is_profitable = net_profit >= self.min_profit_usd and profit_percent >= self.min_profit_percent

        # Calculate execution time
        execution_time_ms = sum(e.latency_ms for e in cycle)

        # Create path ID
        path_id = f"arb_{cycle[0].source}_{len(cycle)}hops_{int(time.time())}"

        return ArbitragePath(
            path_id=path_id,
            edges=cycle,
            start_token=cycle[0].source.split(':')[1] if ':' in cycle[0].source else '',
            start_chain=int(cycle[0].source.split(':')[0]) if ':' in cycle[0].source else 0,
            end_token=cycle[-1].destination.split(':')[1] if ':' in cycle[-1].destination else '',
            end_chain=int(cycle[-1].destination.split(':')[0]) if ':' in cycle[-1].destination else 0,
            hops=len(cycle),
            input_amount=input_amount,
            output_amount=output_amount,
            profit_amount=net_profit,
            profit_percent=profit_percent,
            total_fees=total_fees,
            total_gas_usd=total_gas_usd,
            execution_time_ms=execution_time_ms,
            bridges_used=list(set(bridges_used)),
            protocols_used=list(set(protocols_used)),
            is_profitable=is_profitable,
            risk_score=self._calculate_risk(cycle)
        )

    def _calculate_risk(self, cycle: List[Edge]) -> float:
        """Calculate risk score for path"""
        risk = 0.0

        # More hops = more risk
        hop_risk = min(len(cycle) * 0.1, 0.5)
        risk += hop_risk

        # Bridge risk
        bridge_count = sum(1 for e in cycle if e.edge_type == "bridge")
        bridge_risk = min(bridge_count * 0.15, 0.3)
        risk += bridge_risk

        # Low liquidity risk
        min_liquidity = min(e.liquidity for e in cycle) if cycle else float('inf')
        if min_liquidity < 10000:
            risk += 0.2
        elif min_liquidity < 100000:
            risk += 0.1

        return min(risk, 1.0)

    def bellman_ford_negative_cycle(self, start_node: str) -> Optional[List[Edge]]:
        """
        Find negative cycle (arbitrage opportunity) using Bellman-Ford
        Returns cycle if found, None otherwise
        """
        nodes = list(self.graph._nodes.keys())
        n = len(nodes)

        # Initialize distances
        dist = {node: float('inf') for node in nodes}
        parent = {node: None for node in nodes}
        dist[start_node] = 0

        # Relax edges n-1 times
        for _ in range(n - 1):
            updated = False
            for u in nodes:
                if dist[u] == float('inf'):
                    continue
                for edge in self.graph.get_neighbors(u):
                    # Use negative log of rate for arbitrage detection
                    weight = -1 * (edge.effective_rate())

                    if dist[u] + weight < dist[edge.destination]:
                        dist[edge.destination] = dist[u] + weight
                        parent[edge.destination] = (u, edge)
                        updated = True

            if not updated:
                break

        # Check for negative cycles
        for u in nodes:
            if dist[u] == float('inf'):
                continue
            for edge in self.graph.get_neighbors(u):
                weight = -1 * (edge.effective_rate())
                if dist[u] + weight < dist[edge.destination]:
                    # Negative cycle found, reconstruct it
                    cycle = self._reconstruct_cycle(parent, edge.destination, u, edge)
                    return cycle

        return None

    def _reconstruct_cycle(self, parent: Dict, start: str, from_node: str,
                           last_edge: Edge) -> List[Edge]:
        """Reconstruct cycle from parent pointers"""
        cycle = [last_edge]
        current = from_node

        while current != start and current in parent and parent[current]:
            prev_node, edge = parent[current]
            cycle.append(edge)
            current = prev_node

        cycle.reverse()
        return cycle

    def get_stats(self) -> Dict:
        """Get pathfinder statistics"""
        return {
            'paths_analyzed': self.paths_analyzed,
            'profitable_paths_found': self.profitable_paths_found,
            'max_hops': self.max_hops,
            'min_profit_usd': self.min_profit_usd,
        }
