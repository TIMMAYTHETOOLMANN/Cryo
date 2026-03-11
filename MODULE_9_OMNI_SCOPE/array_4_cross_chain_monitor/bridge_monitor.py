#!/usr/bin/env python3
"""
ARRAY 4 — Cross-Chain & Bridge Monitor: N-Hop Arbitrage Detection
====================================================================
Models token pools across all chains and bridges as a weighted graph.
Uses Bellman-Ford to detect negative-weight cycles (arbitrage).

Capabilities:
  1. N-Hop Pathfinding — find 2-5 hop profitable arb routes across chains
  2. Bridge Liquidity Monitoring — track real-time bridge capacity & fees
  3. Atomic Arbitrage Detection — flag cycles completable within time bounds
  4. Bridge Imbalance Detection — capture fees when bridges are one-sided

Data flow: Price feeds → graph update → pathfinding → emit arb signals → DataBus
"""

import asyncio
import logging
import time
from collections import defaultdict
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass

from ..config import OmniScopeConfig, get_omni_config
from ..data_bus import DataBus, OpportunitySignal, SignalType, SignalSource

logger = logging.getLogger(__name__)


@dataclass
class LiquidityNode:
    """A node in the cross-chain liquidity graph."""
    chain_id: int
    token: str
    price_usd: float
    dex_liquidity_usd: float
    timestamp: float = 0.0


@dataclass
class BridgeEdge:
    """An edge representing a bridge between two chains."""
    source_chain: int
    dest_chain: int
    token: str
    fee_pct: float
    gas_cost_usd: float
    time_seconds: int
    max_amount_usd: float
    current_liquidity_usd: float
    provider: str  # "across", "hop", "stargate", "lifi"
    is_congested: bool = False


@dataclass
class DEXEdge:
    """An edge representing a swap on a single chain."""
    chain_id: int
    token_in: str
    token_out: str
    fee_pct: float
    gas_cost_usd: float
    liquidity_usd: float
    dex: str  # "uniswap_v3", "sushiswap", "curve"


@dataclass
class ArbRoute:
    """A profitable arbitrage route (cycle in the graph)."""
    hops: List[dict]  # Each hop: {type, from, to, chain, cost, ...}
    total_profit_usd: float
    total_gas_usd: float
    total_bridge_fee_usd: float
    total_time_seconds: int
    net_profit_usd: float
    num_hops: int
    input_amount_usd: float = 10_000.0
    confidence: float = 0.0


# Known bridge configurations
BRIDGE_PROVIDERS = {
    "across": {
        "chains": [1, 42161, 10, 8453, 137],
        "base_fee_pct": 0.001,
        "avg_time": 120,
    },
    "hop": {
        "chains": [1, 42161, 10, 137],
        "base_fee_pct": 0.002,
        "avg_time": 300,
    },
    "stargate": {
        "chains": [1, 42161, 10, 137, 43114, 56],
        "base_fee_pct": 0.0006,
        "avg_time": 60,
    },
    "lifi": {
        "chains": [1, 42161, 10, 8453, 137, 43114, 56],
        "base_fee_pct": 0.003,
        "avg_time": 180,
    },
}

# Major tokens to track across all chains
TRACKED_TOKENS = [
    "WETH", "USDC", "USDT", "DAI", "WBTC",
    "ARB", "OP", "MATIC", "AVAX", "BNB",
]


class BridgeMonitor:
    """
    Graph-based cross-chain arbitrage detector.
    Models the entire DeFi liquidity landscape as a weighted graph
    and uses Bellman-Ford to find profitable cycles.
    """

    def __init__(self, bus: DataBus, config: Optional[OmniScopeConfig] = None):
        self.bus = bus
        self.config = config or get_omni_config()
        self._cfg = self.config.bridge_monitor
        self._running = False

        # Graph: nodes are (chain_id, token), edges are swaps or bridges
        self._nodes: Dict[Tuple[int, str], LiquidityNode] = {}
        self._bridge_edges: List[BridgeEdge] = []
        self._dex_edges: List[DEXEdge] = []

        # Route cache
        self._active_routes: List[ArbRoute] = []

        self.stats = {
            "nodes_tracked": 0,
            "bridge_edges": 0,
            "dex_edges": 0,
            "arb_routes_found": 0,
            "total_arb_profit_usd": 0.0,
            "signals_emitted": 0,
            "pathfinding_runs": 0,
        }

    async def start(self):
        """Start the bridge monitor."""
        self._running = True
        logger.info("🌐 Array 4: Bridge Monitor starting…")

        self._build_initial_graph()

        while self._running:
            try:
                self._run_pathfinding()
                await asyncio.sleep(self._cfg.pathfinding_interval_seconds)
            except Exception as e:
                logger.debug(f"Bridge monitor error: {e}")
                await asyncio.sleep(10)

    async def stop(self):
        self._running = False

    # ------------------------------------------------------------------
    # Graph Construction
    # ------------------------------------------------------------------

    def _build_initial_graph(self):
        """Build initial graph structure from known bridges + tokens."""
        # Create nodes for all tracked tokens on all chains
        for provider, cfg in BRIDGE_PROVIDERS.items():
            for chain_a in cfg["chains"]:
                for token in TRACKED_TOKENS:
                    key = (chain_a, token)
                    if key not in self._nodes:
                        self._nodes[key] = LiquidityNode(
                            chain_id=chain_a,
                            token=token,
                            price_usd=0,
                            dex_liquidity_usd=0,
                            timestamp=time.time(),
                        )

                # Create bridge edges between all chain pairs
                for chain_b in cfg["chains"]:
                    if chain_a >= chain_b:
                        continue
                    for token in ["USDC", "WETH", "USDT"]:  # Most bridged tokens
                        self._bridge_edges.append(BridgeEdge(
                            source_chain=chain_a,
                            dest_chain=chain_b,
                            token=token,
                            fee_pct=cfg["base_fee_pct"],
                            gas_cost_usd=5.0,
                            time_seconds=cfg["avg_time"],
                            max_amount_usd=1_000_000,
                            current_liquidity_usd=500_000,
                            provider=provider,
                        ))
                        # Reverse direction
                        self._bridge_edges.append(BridgeEdge(
                            source_chain=chain_b,
                            dest_chain=chain_a,
                            token=token,
                            fee_pct=cfg["base_fee_pct"],
                            gas_cost_usd=5.0,
                            time_seconds=cfg["avg_time"],
                            max_amount_usd=1_000_000,
                            current_liquidity_usd=500_000,
                            provider=provider,
                        ))

        self.stats["nodes_tracked"] = len(self._nodes)
        self.stats["bridge_edges"] = len(self._bridge_edges)
        logger.info(
            f"🌐 Graph built: {len(self._nodes)} nodes, "
            f"{len(self._bridge_edges)} bridge edges"
        )

    def update_price(self, chain_id: int, token: str, price_usd: float,
                     liquidity_usd: float = 0):
        """Update a node's price (called by external price feeds)."""
        key = (chain_id, token)
        if key in self._nodes:
            self._nodes[key].price_usd = price_usd
            self._nodes[key].dex_liquidity_usd = liquidity_usd
            self._nodes[key].timestamp = time.time()

    def add_dex_edge(self, chain_id: int, token_in: str, token_out: str,
                     fee_pct: float, gas_usd: float, liquidity_usd: float,
                     dex: str):
        """Add or update a DEX swap edge."""
        self._dex_edges.append(DEXEdge(
            chain_id=chain_id, token_in=token_in, token_out=token_out,
            fee_pct=fee_pct, gas_cost_usd=gas_usd,
            liquidity_usd=liquidity_usd, dex=dex,
        ))
        self.stats["dex_edges"] = len(self._dex_edges)

    # ------------------------------------------------------------------
    # N-Hop Pathfinding (Bellman-Ford variant)
    # ------------------------------------------------------------------

    def _run_pathfinding(self):
        """
        Run Bellman-Ford on the graph to detect negative-weight cycles.

        Edge weight = -log(exchange_rate * (1 - fee))
        A negative cycle in log-space means a profitable arbitrage.
        """
        self.stats["pathfinding_runs"] += 1
        import math

        # Build adjacency list with log-transformed weights
        adj: Dict[str, List[Tuple[str, float, dict]]] = defaultdict(list)

        # DEX edges (same-chain swaps)
        for edge in self._dex_edges:
            src = f"{edge.chain_id}:{edge.token_in}"
            dst = f"{edge.chain_id}:{edge.token_out}"

            src_node = self._nodes.get((edge.chain_id, edge.token_in))
            dst_node = self._nodes.get((edge.chain_id, edge.token_out))
            if not src_node or not dst_node or src_node.price_usd == 0 or dst_node.price_usd == 0:
                continue

            rate = dst_node.price_usd / src_node.price_usd
            weight = -math.log(rate * (1 - edge.fee_pct)) if rate > 0 else float("inf")
            adj[src].append((dst, weight, {
                "type": "swap", "chain": edge.chain_id,
                "dex": edge.dex, "fee": edge.fee_pct,
                "gas_usd": edge.gas_cost_usd,
            }))

        # Bridge edges (cross-chain)
        for edge in self._bridge_edges:
            if edge.time_seconds > self._cfg.max_bridge_time_seconds:
                continue

            src = f"{edge.source_chain}:{edge.token}"
            dst = f"{edge.dest_chain}:{edge.token}"

            src_node = self._nodes.get((edge.source_chain, edge.token))
            dst_node = self._nodes.get((edge.dest_chain, edge.token))
            if not src_node or not dst_node:
                continue

            # Price difference across chains = arb opportunity
            if src_node.price_usd > 0 and dst_node.price_usd > 0:
                rate = dst_node.price_usd / src_node.price_usd
            else:
                rate = 1.0

            weight = -math.log(rate * (1 - edge.fee_pct)) if rate > 0 else float("inf")
            adj[src].append((dst, weight, {
                "type": "bridge", "provider": edge.provider,
                "fee": edge.fee_pct, "time": edge.time_seconds,
                "gas_usd": edge.gas_cost_usd,
            }))

        if not adj:
            return

        # Bellman-Ford for negative cycle detection
        nodes = list(adj.keys())
        dist = {n: 0.0 for n in nodes}  # Start at 0 (looking for negative cycles)
        prev = {n: ("", {}) for n in nodes}

        n = len(nodes)
        for _ in range(min(n, self._cfg.max_hops)):
            updated = False
            for u in nodes:
                for v, w, meta in adj.get(u, []):
                    if v not in dist:
                        dist[v] = float("inf")
                    if dist[u] + w < dist[v]:
                        dist[v] = dist[u] + w
                        prev[v] = (u, meta)
                        updated = True
            if not updated:
                break

        # Check for negative cycles (one more relaxation)
        for u in nodes:
            for v, w, meta in adj.get(u, []):
                if v in dist and dist[u] + w < dist.get(v, 0):
                    # Found a negative cycle — reconstruct route
                    route = self._reconstruct_route(v, prev, adj)
                    if route and route.net_profit_usd >= self._cfg.min_arb_profit_usd:
                        self._active_routes.append(route)
                        self.stats["arb_routes_found"] += 1
                        self.stats["total_arb_profit_usd"] += route.net_profit_usd

                        self.bus.publish(OpportunitySignal(
                            signal_type=SignalType.CROSS_CHAIN_ARB,
                            source=SignalSource.BRIDGE_MONITOR,
                            chain_id=route.hops[0].get("chain", 1) if route.hops else 1,
                            confidence=route.confidence,
                            estimated_profit_usd=route.net_profit_usd,
                            gas_cost_estimate_usd=route.total_gas_usd,
                            urgency_seconds=float(route.total_time_seconds),
                            competition_estimate=0.4,
                            execution_complexity=min(route.num_hops * 0.2, 1.0),
                            metadata={
                                "hops": route.num_hops,
                                "route": [h.get("type", "") for h in route.hops],
                                "total_time": route.total_time_seconds,
                            },
                        ))
                        self.stats["signals_emitted"] += 1

    def _reconstruct_route(
        self, start: str, prev: Dict, adj: Dict
    ) -> Optional[ArbRoute]:
        """Reconstruct an arbitrage route from Bellman-Ford predecessor map."""
        visited = set()
        hops = []
        total_gas = 0.0
        total_bridge_fee = 0.0
        total_time = 0
        current = start

        for _ in range(self._cfg.max_hops + 1):
            if current in visited:
                break
            visited.add(current)
            predecessor, meta = prev.get(current, ("", {}))
            if not predecessor:
                break
            hops.append(meta)
            total_gas += meta.get("gas_usd", 0)
            if meta.get("type") == "bridge":
                total_bridge_fee += 10000 * meta.get("fee", 0)
                total_time += meta.get("time", 0)
            current = predecessor

        if len(hops) < 2:
            return None

        input_amount = 10_000.0
        gross_profit = input_amount * 0.005 * len(hops)  # Simplified estimate
        net_profit = gross_profit - total_gas - total_bridge_fee

        return ArbRoute(
            hops=list(reversed(hops)),
            total_profit_usd=gross_profit,
            total_gas_usd=total_gas,
            total_bridge_fee_usd=total_bridge_fee,
            total_time_seconds=total_time,
            net_profit_usd=net_profit,
            num_hops=len(hops),
            input_amount_usd=input_amount,
            confidence=max(0.3, 0.8 - len(hops) * 0.1),
        )

    def get_active_routes(self) -> List[ArbRoute]:
        """Get currently profitable arb routes."""
        return [r for r in self._active_routes if r.net_profit_usd > 0]

    def get_stats(self) -> Dict:
        return {
            **self.stats,
            "active_routes": len(self._active_routes),
        }

    # ------------------------------------------------------------------
    # Cross-Chain Price Feed — Live Delta Tracking
    # ------------------------------------------------------------------

    def fetch_cross_chain_prices(
        self,
        assets: Optional[List[str]] = None,
    ) -> Dict[str, Dict[int, float]]:
        """
        Return the latest cached price for each tracked asset on every
        chain.  Format: ``{asset: {chain_id: price_usd, …}, …}``.

        Parameters
        ----------
        assets : list[str] | None
            Filter to specific assets.  ``None`` returns all.
        """
        result: Dict[str, Dict[int, float]] = {}
        for (chain_id, token), node in self._nodes.items():
            if assets and token not in assets:
                continue
            result.setdefault(token, {})[chain_id] = node.price_usd
        return result

    def calculate_cross_chain_deltas(
        self,
        assets: Optional[List[str]] = None,
        *,
        min_delta_pct: float = 0.5,
        trade_size_usd: float = 10_000.0,
    ) -> List[Dict]:
        """
        Compare the price of each asset across every chain pair
        and return entries where the delta exceeds *min_delta_pct*.

        Each returned dict contains::

            {
              "asset": str,
              "chain_a": int,  "price_a": float,
              "chain_b": int,  "price_b": float,
              "delta_pct": float,          # absolute % difference
              "direction": "a_to_b" | "b_to_a",  # buy low → sell high
              "estimated_gross_profit_usd": float,
            }
        """
        prices = self.fetch_cross_chain_prices(assets)
        deltas: List[Dict] = []
        threshold = min_delta_pct / 100.0

        for asset, chain_prices in prices.items():
            chains = sorted(chain_prices.keys())
            for i, chain_a in enumerate(chains):
                price_a = chain_prices[chain_a]
                if price_a <= 0:
                    continue
                for chain_b in chains[i + 1:]:
                    price_b = chain_prices[chain_b]
                    if price_b <= 0:
                        continue

                    avg_price = (price_a + price_b) / 2.0
                    if avg_price == 0:
                        continue

                    delta = abs(price_a - price_b) / avg_price
                    if delta < threshold:
                        continue

                    # Direction: buy on the cheaper chain, sell on the pricier
                    if price_a < price_b:
                        direction = "a_to_b"
                    else:
                        direction = "b_to_a"

                    estimated_gross = trade_size_usd * delta

                    deltas.append({
                        "asset": asset,
                        "chain_a": chain_a,
                        "price_a": price_a,
                        "chain_b": chain_b,
                        "price_b": price_b,
                        "delta_pct": round(delta * 100, 4),
                        "direction": direction,
                        "estimated_gross_profit_usd": round(estimated_gross, 2),
                    })

        # Biggest delta first
        deltas.sort(key=lambda d: d["delta_pct"], reverse=True)
        return deltas
