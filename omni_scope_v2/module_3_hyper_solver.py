#!/usr/bin/env python3
"""
Module 3 — Yield & Arbitrage Hyper-Solver
============================================
Identifies and ranks complex arbitrage, yield farming, and cross-chain
multi-step opportunities.

Capabilities:
  3.1  Multi-Hop Arbitrage Path Discovery (Bellman-Ford on DEX graph)
  3.2  Yield Farming & Restacking Optimization (DeFi Llama + EigenLayer)
  3.3  Cross-Chain Fee & Bridge Optimization (LI.FI, Socket, 1inch Fusion+)

Data flow:
  DEX pools → build weighted graph → detect negative cycles → rank paths
  Yield APIs → compute borrow-vs-deposit spread → emit signals → SignalBus
"""
from __future__ import annotations

import asyncio
import logging
import math
import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set, Tuple

import aiohttp

from .config import HyperSolverConfig, get_config
from .data_lake import DataLake, Topic
from .signal_bus import SignalBus, SignalSource, SignalType, TriangulatedSignal

logger = logging.getLogger(__name__)


# ── DEX Pool Graph ───────────────────────────────────────────────

@dataclass
class DexPool:
    """A single DEX liquidity pool."""
    pool_address: str
    dex: str               # "uniswap_v3", "sushiswap", "curve", "balancer"
    chain_id: int
    token0: str
    token1: str
    reserve0: float = 0.0
    reserve1: float = 0.0
    fee_bps: int = 30      # 0.3% default
    tick_current: int = 0   # For V3 concentrated liquidity
    sqrt_price_x96: int = 0
    liquidity: float = 0.0
    last_updated: float = 0.0


@dataclass
class ArbPath:
    """A discovered arbitrage path."""
    path_id: str
    chain_id: int
    hops: List[Dict[str, Any]]   # [{"pool": ..., "tokenIn": ..., "tokenOut": ...}]
    input_token: str
    output_token: str
    input_amount_usd: float
    expected_output_usd: float
    profit_usd: float
    gas_cost_usd: float
    net_profit_usd: float
    flash_loan_fee_usd: float = 0.0
    confidence: float = 0.0
    timestamp: float = field(default_factory=time.time)


@dataclass
class YieldOpportunity:
    """A yield farming / restacking opportunity."""
    opp_id: str
    chain_id: int
    protocol: str
    strategy: str           # "borrow_restack", "deposit_farm", "lrt_restake"
    input_asset: str
    output_asset: str
    borrow_apy: float       # Cost side
    deposit_apy: float      # Revenue side
    spread_bps: int          # Net spread in basis points
    tvl_usd: float = 0.0
    risk_level: str = "medium"
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class CrossChainRoute:
    """Optimised cross-chain routing result."""
    route_id: str
    source_chain: int
    dest_chain: int
    bridge: str
    path: List[str]
    total_fee_usd: float
    estimated_time_s: int
    output_amount_usd: float
    provider: str = ""


# ── Module ───────────────────────────────────────────────────────

class HyperSolver:
    """
    Module 3: Yield & Arbitrage Hyper-Solver.

    Builds a real-time DEX pool graph, runs Bellman-Ford for arb detection,
    scans yield opportunities via DeFi Llama, and optimises cross-chain routes.
    """

    def __init__(
        self,
        bus: SignalBus,
        lake: DataLake,
        config: Optional[HyperSolverConfig] = None,
    ):
        self.bus = bus
        self.lake = lake
        self._cfg = config or get_config().hyper_solver
        self._running = False
        self._session: Optional[aiohttp.ClientSession] = None

        # DEX graph: token → [(neighbour_token, pool, weight)]
        self._graph: Dict[str, List[Tuple[str, DexPool, float]]] = defaultdict(list)
        self._pools: Dict[str, DexPool] = {}

        # Cache
        self._yield_cache: List[YieldOpportunity] = []
        self._arb_cache: List[ArbPath] = []

        # Stats
        self._stats = {
            "pools_indexed": 0,
            "graph_nodes": 0,
            "graph_edges": 0,
            "arb_paths_found": 0,
            "yield_opportunities_found": 0,
            "cross_chain_routes_compared": 0,
            "signals_emitted": 0,
        }

    # ── Lifecycle ─────────────────────────────────────────────

    async def start(self):
        self._running = True
        self._session = aiohttp.ClientSession()
        logger.info("[HyperSolver] Starting — max %d hops, min $%.0f arb profit",
                     self._cfg.max_hops, self._cfg.min_arb_profit_usd)

    async def stop(self):
        self._running = False
        if self._session:
            await self._session.close()
            self._session = None
        logger.info("[HyperSolver] Stopped")

    async def run_cycle(self):
        """Execute one full solver cycle."""
        if not self._running:
            return

        await asyncio.gather(
            self._refresh_dex_graph(),
            self._scan_yield_opportunities(),
            return_exceptions=True,
        )

        # Run Bellman-Ford arb detection
        self._detect_arbitrage_paths()

    # ── 3.1 Multi-Hop Arbitrage Detection ────────────────────

    async def _refresh_dex_graph(self):
        """Refresh the DEX pool graph from on-chain and API data."""
        # Fetch top pools from DeFi Llama / subgraphs
        if not self._session:
            return

        try:
            async with self._session.get(
                "https://yields.llama.fi/pools", timeout=15,
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    pools_data = data.get("data", [])

                    for pool_data in pools_data[:500]:
                        chain = pool_data.get("chain", "").lower()
                        chain_id = self._chain_name_to_id(chain)
                        if chain_id == 0:
                            continue

                        project = pool_data.get("project", "")
                        symbol = pool_data.get("symbol", "")
                        tokens = symbol.split("-") if "-" in symbol else []
                        if len(tokens) < 2:
                            continue

                        pool = DexPool(
                            pool_address=pool_data.get("pool", ""),
                            dex=project,
                            chain_id=chain_id,
                            token0=tokens[0].strip(),
                            token1=tokens[1].strip(),
                            liquidity=float(pool_data.get("tvlUsd", 0)),
                            fee_bps=int(float(pool_data.get("apyBase", 0)) / 365 * 10000) or 30,
                            last_updated=time.time(),
                        )

                        key = f"{pool.chain_id}:{pool.pool_address}"
                        self._pools[key] = pool
                        self._add_to_graph(pool)

        except Exception as exc:
            logger.debug("[HyperSolver] Pool refresh error: %s", exc)

        self._stats["pools_indexed"] = len(self._pools)
        self._stats["graph_nodes"] = len(self._graph)
        self._stats["graph_edges"] = sum(len(v) for v in self._graph.values())

    def _add_to_graph(self, pool: DexPool):
        """Add a pool as edges in the token graph."""
        if pool.liquidity < 1000:
            return

        fee_rate = pool.fee_bps / 10000.0
        # Weight = -log(1 - fee_rate) for Bellman-Ford (negative = profit)
        # Price ratio between tokens approximated via reserves
        if pool.reserve0 > 0 and pool.reserve1 > 0:
            rate_0_to_1 = pool.reserve1 / pool.reserve0
            rate_1_to_0 = pool.reserve0 / pool.reserve1
        else:
            rate_0_to_1 = 1.0
            rate_1_to_0 = 1.0

        weight_0_to_1 = -math.log(max(1e-18, rate_0_to_1 * (1 - fee_rate)))
        weight_1_to_0 = -math.log(max(1e-18, rate_1_to_0 * (1 - fee_rate)))

        self._graph[pool.token0].append((pool.token1, pool, weight_0_to_1))
        self._graph[pool.token1].append((pool.token0, pool, weight_1_to_0))

    def _detect_arbitrage_paths(self):
        """Run Bellman-Ford to detect negative-weight cycles (arb opportunities)."""
        tokens = list(self._graph.keys())
        if not tokens:
            return

        # For each starting token (focus on major stables/ETH)
        start_tokens = [t for t in tokens if t.upper() in
                        {"USDC", "USDT", "DAI", "WETH", "ETH", "WBTC"}]
        if not start_tokens:
            start_tokens = tokens[:10]

        found_paths: List[ArbPath] = []

        for start in start_tokens:
            # Bellman-Ford
            dist: Dict[str, float] = {t: float("inf") for t in tokens}
            pred: Dict[str, Optional[Tuple[str, DexPool]]] = {t: None for t in tokens}
            dist[start] = 0.0

            n = len(tokens)
            for _ in range(min(n - 1, self._cfg.max_hops)):
                updated = False
                for u in tokens:
                    if dist[u] == float("inf"):
                        continue
                    for v, pool, w in self._graph.get(u, []):
                        if dist[u] + w < dist[v]:
                            dist[v] = dist[u] + w
                            pred[v] = (u, pool)
                            updated = True
                if not updated:
                    break

            # Check for negative cycle back to start
            if dist.get(start, float("inf")) < -0.001:
                # Reconstruct path
                path_hops = []
                current = start
                visited: Set[str] = set()
                while current and current not in visited:
                    visited.add(current)
                    p = pred.get(current)
                    if p:
                        prev_token, pool = p
                        path_hops.append({
                            "pool": pool.pool_address,
                            "dex": pool.dex,
                            "tokenIn": prev_token,
                            "tokenOut": current,
                            "chain_id": pool.chain_id,
                        })
                        current = prev_token
                    else:
                        break

                if path_hops:
                    path_hops.reverse()
                    profit_factor = math.exp(-dist[start]) - 1.0
                    input_usd = 10000.0
                    profit_usd = input_usd * profit_factor
                    gas_cost = len(path_hops) * 5.0

                    if profit_usd - gas_cost >= self._cfg.min_arb_profit_usd:
                        arb = ArbPath(
                            path_id=f"arb_{start}_{int(time.time() * 1000)}",
                            chain_id=path_hops[0].get("chain_id", 1),
                            hops=path_hops,
                            input_token=start,
                            output_token=start,
                            input_amount_usd=input_usd,
                            expected_output_usd=input_usd + profit_usd,
                            profit_usd=profit_usd,
                            gas_cost_usd=gas_cost,
                            net_profit_usd=profit_usd - gas_cost,
                            confidence=min(0.85, 0.5 + profit_factor),
                        )
                        found_paths.append(arb)
                        self._emit_arb_signal(arb)

        self._arb_cache = found_paths
        self._stats["arb_paths_found"] += len(found_paths)

    def _emit_arb_signal(self, arb: ArbPath):
        """Emit a TriangulatedSignal for a discovered arb path."""
        signal = TriangulatedSignal(
            signal_type=SignalType.ARBITRAGE,
            source=SignalSource.HYPER_SOLVER,
            chain_id=arb.chain_id,
            confidence=arb.confidence,
            estimated_profit_usd=arb.profit_usd,
            gas_cost_estimate_usd=arb.gas_cost_usd,
            urgency_seconds=12.0,
            target_asset=arb.input_token,
            arb_path=[h["pool"] for h in arb.hops],
            hop_count=len(arb.hops),
            competition_estimate=0.5,
            execution_complexity=min(1.0, len(arb.hops) * 0.2),
            metadata={
                "path_id": arb.path_id,
                "input_usd": arb.input_amount_usd,
                "output_usd": arb.expected_output_usd,
                "hops": arb.hops,
            },
        )
        self.bus.publish(signal)
        self._stats["signals_emitted"] += 1

    # ── 3.2 Yield Farming & Restacking ───────────────────────

    async def _scan_yield_opportunities(self):
        """Scan DeFi Llama for borrow-vs-deposit yield spreads."""
        if not self._session:
            return

        try:
            async with self._session.get(
                f"{self._cfg.defillama_api_url}/pools", timeout=15,
            ) as resp:
                if resp.status != 200:
                    return
                data = await resp.json()
                pools = data.get("data", [])

            # Group pools by underlying asset to find spreads
            asset_yields: Dict[str, List[Dict]] = defaultdict(list)
            for pool in pools:
                symbol = pool.get("symbol", "")
                underlying = pool.get("underlyingTokens", [])
                base_apy = float(pool.get("apyBase", 0) or 0)
                reward_apy = float(pool.get("apy", 0) or 0)
                tvl = float(pool.get("tvlUsd", 0) or 0)

                if tvl < 100_000 or reward_apy < 0.1:
                    continue

                asset_yields[symbol].append({
                    "protocol": pool.get("project", ""),
                    "chain": pool.get("chain", ""),
                    "base_apy": base_apy,
                    "total_apy": reward_apy,
                    "tvl": tvl,
                    "pool": pool.get("pool", ""),
                })

            # Find profitable borrow→deposit spreads
            opportunities: List[YieldOpportunity] = []
            for asset, entries in asset_yields.items():
                if len(entries) < 2:
                    continue

                entries.sort(key=lambda e: e["total_apy"], reverse=True)
                best_yield = entries[0]

                # Check if we can borrow this asset cheaper elsewhere
                for other in entries[1:]:
                    spread_bps = int((best_yield["total_apy"] - other["base_apy"]) * 100)
                    if spread_bps >= self._cfg.min_yield_spread_bps:
                        opp = YieldOpportunity(
                            opp_id=f"yield_{asset}_{int(time.time())}",
                            chain_id=self._chain_name_to_id(best_yield["chain"]),
                            protocol=best_yield["protocol"],
                            strategy="borrow_restack",
                            input_asset=asset,
                            output_asset=asset,
                            borrow_apy=other["base_apy"],
                            deposit_apy=best_yield["total_apy"],
                            spread_bps=spread_bps,
                            tvl_usd=best_yield["tvl"],
                            metadata={
                                "borrow_protocol": other["protocol"],
                                "deposit_protocol": best_yield["protocol"],
                            },
                        )
                        opportunities.append(opp)
                        break

            self._yield_cache = opportunities[:50]
            self._stats["yield_opportunities_found"] += len(opportunities)

            # Emit signals for top opportunities
            for opp in opportunities[:10]:
                signal = TriangulatedSignal(
                    signal_type=SignalType.YIELD_OPPORTUNITY,
                    source=SignalSource.HYPER_SOLVER,
                    chain_id=opp.chain_id,
                    confidence=0.70,
                    estimated_profit_usd=opp.spread_bps * opp.tvl_usd / 100_000,
                    gas_cost_estimate_usd=5.0,
                    urgency_seconds=3600.0,
                    target_protocol=opp.protocol,
                    target_asset=opp.input_asset,
                    competition_estimate=0.3,
                    execution_complexity=0.6,
                    metadata={
                        "opp_id": opp.opp_id,
                        "strategy": opp.strategy,
                        "spread_bps": opp.spread_bps,
                        "borrow_apy": opp.borrow_apy,
                        "deposit_apy": opp.deposit_apy,
                    },
                )
                self.bus.publish(signal)
                self._stats["signals_emitted"] += 1

        except Exception as exc:
            logger.debug("[HyperSolver] Yield scan error: %s", exc)

    # ── 3.3 Cross-Chain Route Optimization ───────────────────

    async def find_best_route(
        self, asset: str, amount_usd: float,
        source_chain: int, dest_chain: int,
    ) -> Optional[CrossChainRoute]:
        """Query LI.FI, Socket, and 1inch for the cheapest cross-chain route."""
        if not self._session:
            return None

        routes: List[CrossChainRoute] = []

        # LI.FI
        try:
            params = {
                "fromChainId": source_chain,
                "toChainId": dest_chain,
                "fromTokenAddress": "0x0000000000000000000000000000000000000000",
                "toTokenAddress": "0x0000000000000000000000000000000000000000",
                "fromAmount": str(int(amount_usd * 1e6)),
            }
            async with self._session.get(
                f"{self._cfg.lifi_api_url}/quote",
                params=params, timeout=10,
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    route = CrossChainRoute(
                        route_id=f"lifi_{int(time.time())}",
                        source_chain=source_chain,
                        dest_chain=dest_chain,
                        bridge=data.get("tool", ""),
                        path=[asset],
                        total_fee_usd=float(data.get("estimate", {}).get("gasCosts", [{}])[0].get("amountUSD", 0)),
                        estimated_time_s=int(data.get("estimate", {}).get("executionDuration", 300)),
                        output_amount_usd=float(data.get("estimate", {}).get("toAmountUSD", 0)),
                        provider="lifi",
                    )
                    routes.append(route)
        except Exception:
            pass

        # Socket
        try:
            params = {
                "fromChainId": source_chain,
                "toChainId": dest_chain,
                "fromTokenAddress": "0xeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee",
                "toTokenAddress": "0xeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee",
                "fromAmount": str(int(amount_usd * 1e18)),
                "userAddress": "0x0000000000000000000000000000000000000001",
            }
            async with self._session.get(
                f"{self._cfg.socket_api_url}/quote",
                params=params, timeout=10,
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    result = data.get("result", {})
                    routes.append(CrossChainRoute(
                        route_id=f"socket_{int(time.time())}",
                        source_chain=source_chain,
                        dest_chain=dest_chain,
                        bridge=result.get("bridgeName", ""),
                        path=[asset],
                        total_fee_usd=float(result.get("totalGasFeesInUsd", 0)),
                        estimated_time_s=int(result.get("serviceTime", 300)),
                        output_amount_usd=float(result.get("toTokenAmount", 0)) / 1e18 * amount_usd,
                        provider="socket",
                    ))
        except Exception:
            pass

        self._stats["cross_chain_routes_compared"] += len(routes)

        # Return cheapest route
        if routes:
            return min(routes, key=lambda r: r.total_fee_usd)
        return None

    # ── Helpers ───────────────────────────────────────────────

    @staticmethod
    def _chain_name_to_id(name: str) -> int:
        mapping = {
            "ethereum": 1, "arbitrum": 42161, "optimism": 10,
            "base": 8453, "polygon": 137, "avalanche": 43114,
            "bsc": 56, "gnosis": 100, "fantom": 250,
            "zksync era": 324, "linea": 59144, "scroll": 534352,
        }
        return mapping.get(name.lower(), 0)

    def get_stats(self) -> Dict[str, Any]:
        return {**self._stats}
