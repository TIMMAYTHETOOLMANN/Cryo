#!/usr/bin/env python3
"""
bridge_registry — Comprehensive Cross-Chain Bridge Database
=============================================================
Canonical database of all supported bridges with real-time route
discovery, liquidity tracking, fee estimation, and finality windows.

Used by:
  - Module 3 (Hyper-Solver) for N-hop cross-chain pathfinding
  - Module 8 (Execution Router) CrossChainExecutor for bridge selection
  - Module 6 (ML Aggregator) for competition / complexity scoring

Bridges Supported:
  Stargate Finance       — LayerZero-based, LP bridging, instant finality
  Hop Protocol           — Bonder-based, optimistic fast bridging
  Across Protocol        — UMA optimistic oracle, fast bridging
  Synapse Protocol       — Cross-chain AMM + canonical bridging
  Wormhole / Portal      — Guardian-based, lock-and-mint
  LayerZero (OFT / ONFT) — Omnichain fungible tokens
  Connext (Amarok)       — NXTP v2, router-based
  Celer cBridge          — HTLC + liquidity pool
  Axelar                 — Cosmos-SDK IBC-style cross-chain
  Multichain (legacy)    — Deprecated but still has liquidity
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


# ── Enums ─────────────────────────────────────────────────────────

class BridgeType(str, Enum):
    LIQUIDITY_POOL = "liquidity_pool"
    LOCK_AND_MINT = "lock_and_mint"
    ATOMIC = "atomic"
    OPTIMISTIC = "optimistic"
    CANONICAL = "canonical"
    ZK_LIGHT_CLIENT = "zk_light_client"
    HTLC = "htlc"
    GUARDIAN = "guardian"


class FinalityClass(str, Enum):
    INSTANT = "instant"          # < 30 s
    FAST = "fast"                # 30 s – 5 min
    STANDARD = "standard"        # 5 – 20 min
    SLOW = "slow"                # 20 min – 2 hr
    VERY_SLOW = "very_slow"      # > 2 hr


# ── Data Models ───────────────────────────────────────────────────

@dataclass
class Bridge:
    """Top-level bridge protocol."""
    name: str
    bridge_type: BridgeType
    chains: List[int] = field(default_factory=list)
    tokens: List[str] = field(default_factory=list)
    fee_range_bps: Tuple[float, float] = (5, 50)  # min, max basis points
    finality_range_s: Tuple[int, int] = (60, 1800)
    security_model: str = ""
    tvl_usd: float = 0.0
    volume_24h_usd: float = 0.0
    is_active: bool = True
    contracts: Dict[int, Dict[str, str]] = field(default_factory=dict)
    website: str = ""
    docs: str = ""


@dataclass
class BridgeRoute:
    """A specific route between two chains for one token."""
    bridge_name: str
    source_chain: int
    dest_chain: int
    token: str
    source_pool: str = ""
    dest_pool: str = ""
    fee_bps: float = 0.0
    estimated_time_s: int = 120
    finality_class: FinalityClass = FinalityClass.FAST
    min_amount: float = 0.0
    max_amount: float = float("inf")
    liquidity_available_usd: float = 0.0
    is_active: bool = True
    last_checked: float = 0.0


@dataclass
class BridgeQuote:
    """Quote from a bridge for a specific transfer."""
    bridge_name: str
    source_chain: int
    dest_chain: int
    token: str
    input_amount: float
    output_amount: float
    fee_usd: float
    fee_bps: float
    estimated_time_s: int
    gas_cost_source_usd: float = 0.0
    gas_cost_dest_usd: float = 0.0
    total_cost_usd: float = 0.0
    effective_rate: float = 0.0  # output / input


@dataclass
class BridgeLiquidity:
    """Real-time liquidity snapshot for a route."""
    bridge_name: str
    chain_id: int
    token: str
    available_usd: float
    utilization_pct: float
    last_updated: float


# ── Bridge Registry ──────────────────────────────────────────────

class BridgeRegistry:
    """
    Comprehensive bridge database with route discovery,
    liquidity tracking, and fee estimation.
    """

    def __init__(self) -> None:
        self._bridges: Dict[str, Bridge] = {}
        self._routes: Dict[str, List[BridgeRoute]] = {}   # key: "{src}-{dst}-{token}"
        self._liquidity: Dict[str, BridgeLiquidity] = {}   # key: "{bridge}-{chain}-{token}"
        self._quotes_cache: Dict[str, BridgeQuote] = {}

        self._init_bridges()
        self._init_routes()

    # ── Initialization ────────────────────────────────────────

    def _init_bridges(self) -> None:
        """Populate bridge database."""
        self._bridges = {
            # ── Stargate Finance ──────────────────────────────
            "stargate": Bridge(
                name="Stargate Finance",
                bridge_type=BridgeType.LIQUIDITY_POOL,
                chains=[1, 42161, 10, 137, 8453, 43114, 56, 250],
                tokens=["USDC", "USDT", "ETH", "DAI", "FRAX", "STG"],
                fee_range_bps=(1, 10),
                finality_range_s=(30, 120),
                security_model="LayerZero Oracles + Relayers",
                tvl_usd=400_000_000,
                website="https://stargate.finance",
                docs="https://stargateprotocol.gitbook.io",
                contracts={
                    1: {"router": "0x8731d54E9D02c286767d56ac03e8037C07e01e98"},
                    42161: {"router": "0x53Bf833A5d6c4ddA888F69c22C88C9f356a41614"},
                    10: {"router": "0xB0D502E938ed5f4df2E681fE6E419ff29631d62b"},
                    137: {"router": "0x45A01E4e04F14f7A4a6702c74187c5F6222033cd"},
                    8453: {"router": "0x45f1A95A4D3f3836523F5c83673c797f4d4d263B"},
                    43114: {"router": "0x45A01E4e04F14f7A4a6702c74187c5F6222033cd"},
                    56: {"router": "0x4a364f8c717cAAD9A442737Eb7b8A55cc6cf18D8"},
                },
            ),

            # ── Hop Protocol ──────────────────────────────────
            "hop": Bridge(
                name="Hop Protocol",
                bridge_type=BridgeType.OPTIMISTIC,
                chains=[1, 42161, 10, 137, 100, 8453],
                tokens=["ETH", "USDC", "USDT", "DAI", "MATIC", "HOP"],
                fee_range_bps=(4, 30),
                finality_range_s=(60, 600),
                security_model="Bonder + Challenge Period",
                tvl_usd=50_000_000,
                website="https://hop.exchange",
                docs="https://docs.hop.exchange",
                contracts={
                    1: {"bridge": "0xb8901acB165ed027E32754E0FFe830802919727f"},
                    42161: {"bridge": "0x33ceb27b39d2Bb7D2bb27A4dF2E71aB2E4508C04"},
                    10: {"bridge": "0x83f6244Bd87662118d96D9a6D44f09dffF14b30E"},
                    137: {"bridge": "0x76b22b8C1079A44F1211D867D68b1eda76a635A7"},
                },
            ),

            # ── Across Protocol ───────────────────────────────
            "across": Bridge(
                name="Across Protocol",
                bridge_type=BridgeType.OPTIMISTIC,
                chains=[1, 42161, 10, 137, 8453, 324],
                tokens=["ETH", "USDC", "USDT", "WBTC", "DAI", "ACX"],
                fee_range_bps=(2, 20),
                finality_range_s=(30, 300),
                security_model="UMA Optimistic Oracle",
                tvl_usd=200_000_000,
                website="https://across.to",
                docs="https://docs.across.to",
                contracts={
                    1: {"spoke_pool": "0x5c7BCd6E7De5423a257D81B442095A1a6ced35C5"},
                    42161: {"spoke_pool": "0xe35e9842fceaCA96570B734083f4a58e8F7C5f2A"},
                    10: {"spoke_pool": "0x6f26Bf09B1C792e3228e5467807a900A503c0281"},
                    8453: {"spoke_pool": "0x09aea4b2242abC8bb4BB78D537A67a245A7bEC64"},
                },
            ),

            # ── Synapse Protocol ──────────────────────────────
            "synapse": Bridge(
                name="Synapse Protocol",
                bridge_type=BridgeType.LIQUIDITY_POOL,
                chains=[1, 42161, 10, 137, 43114, 56, 250, 1284, 1285],
                tokens=["USDC", "USDT", "DAI", "nUSD", "SYN", "ETH"],
                fee_range_bps=(5, 40),
                finality_range_s=(60, 900),
                security_model="MPC Validators + AMM",
                tvl_usd=100_000_000,
                website="https://synapseprotocol.com",
                docs="https://docs.synapseprotocol.com",
                contracts={
                    1: {"bridge": "0x2796317b0fF8538F253012862c06787Adfb8cEb6"},
                    42161: {"bridge": "0x6F4e8eBa4D337f874Ab57478AcC2Cb5BACdc19c9"},
                    10: {"bridge": "0xAf41a65F786339e7911F4acDAD6BD49426F2Dc6b"},
                },
            ),

            # ── Wormhole / Portal ─────────────────────────────
            "wormhole": Bridge(
                name="Wormhole (Portal)",
                bridge_type=BridgeType.GUARDIAN,
                chains=[1, 42161, 10, 137, 43114, 56, 250, 1284, 1285, 101],
                tokens=["ETH", "USDC", "USDT", "WBTC", "SOL"],
                fee_range_bps=(0, 5),
                finality_range_s=(300, 1800),
                security_model="19-of-19 Guardian Multisig",
                tvl_usd=300_000_000,
                website="https://wormhole.com",
                docs="https://docs.wormhole.com",
                contracts={
                    1: {"core": "0x98f3c9e6E3fAce36bAAd05FE09d375Ef1464288B"},
                },
            ),

            # ── Connext (Amarok) ──────────────────────────────
            "connext": Bridge(
                name="Connext (Amarok)",
                bridge_type=BridgeType.OPTIMISTIC,
                chains=[1, 42161, 10, 137, 100, 56],
                tokens=["ETH", "USDC", "USDT", "DAI", "WETH"],
                fee_range_bps=(5, 30),
                finality_range_s=(60, 1200),
                security_model="Router Network + Canonical Verification",
                tvl_usd=50_000_000,
                website="https://connext.network",
                docs="https://docs.connext.network",
                contracts={
                    1: {"connext": "0x8898B472C54c31894e3B9bb83cEA802a5d0e63C6"},
                },
            ),

            # ── Celer cBridge ─────────────────────────────────
            "celer": Bridge(
                name="Celer cBridge",
                bridge_type=BridgeType.HTLC,
                chains=[1, 42161, 10, 137, 56, 43114, 250, 1284],
                tokens=["USDC", "USDT", "ETH", "DAI", "CELR"],
                fee_range_bps=(4, 50),
                finality_range_s=(60, 900),
                security_model="State Guardian Network (SGN)",
                tvl_usd=80_000_000,
                website="https://cbridge.celer.network",
                docs="https://cbridge-docs.celer.network",
            ),

            # ── Axelar ───────────────────────────────────────
            "axelar": Bridge(
                name="Axelar",
                bridge_type=BridgeType.CANONICAL,
                chains=[1, 42161, 10, 137, 43114, 56, 250, 1284],
                tokens=["USDC", "USDT", "WBTC", "WETH", "AXL"],
                fee_range_bps=(5, 30),
                finality_range_s=(120, 1200),
                security_model="Cosmos SDK PoS Validators",
                tvl_usd=150_000_000,
                website="https://axelar.network",
                docs="https://docs.axelar.dev",
            ),

            # ── LayerZero OFT ─────────────────────────────────
            "layerzero_oft": Bridge(
                name="LayerZero OFT",
                bridge_type=BridgeType.ATOMIC,
                chains=[1, 42161, 10, 137, 8453, 43114, 56],
                tokens=["OFT_TOKENS"],  # Protocol-specific
                fee_range_bps=(0, 5),
                finality_range_s=(30, 120),
                security_model="Ultra Light Node (ULN)",
                tvl_usd=0,  # TVL is per-OFT
                website="https://layerzero.network",
                docs="https://docs.layerzero.network",
            ),
        }

    def _init_routes(self) -> None:
        """Generate routes from bridge data."""
        MAJOR_CHAINS = [1, 42161, 10, 137, 8453, 43114, 56]
        MAJOR_TOKENS = ["ETH", "USDC", "USDT", "DAI", "WBTC"]

        for bname, bridge in self._bridges.items():
            if not bridge.is_active:
                continue
            for token in bridge.tokens:
                if token not in MAJOR_TOKENS and token not in ("FRAX", "nUSD"):
                    continue
                for src in bridge.chains:
                    for dst in bridge.chains:
                        if src == dst or src not in MAJOR_CHAINS or dst not in MAJOR_CHAINS:
                            continue
                        # ETH not on BSC / Polygon natively
                        if token == "ETH" and dst in (56, 137):
                            continue

                        route_key = f"{src}-{dst}-{token}"
                        if route_key not in self._routes:
                            self._routes[route_key] = []

                        self._routes[route_key].append(BridgeRoute(
                            bridge_name=bname,
                            source_chain=src,
                            dest_chain=dst,
                            token=token,
                            fee_bps=bridge.fee_range_bps[0],
                            estimated_time_s=bridge.finality_range_s[0],
                            finality_class=self._classify_finality(bridge.finality_range_s[0]),
                            source_pool=bridge.contracts.get(src, {}).get("router", ""),
                            dest_pool=bridge.contracts.get(dst, {}).get("router", ""),
                            last_checked=time.time(),
                        ))

    @staticmethod
    def _classify_finality(seconds: int) -> FinalityClass:
        if seconds < 30:
            return FinalityClass.INSTANT
        if seconds < 300:
            return FinalityClass.FAST
        if seconds < 1200:
            return FinalityClass.STANDARD
        if seconds < 7200:
            return FinalityClass.SLOW
        return FinalityClass.VERY_SLOW

    # ── Route Discovery ───────────────────────────────────────

    def find_routes(
        self,
        source_chain: int,
        dest_chain: int,
        token: str,
        min_liquidity_usd: float = 0,
    ) -> List[BridgeRoute]:
        """Find all routes for a transfer."""
        key = f"{source_chain}-{dest_chain}-{token}"
        routes = self._routes.get(key, [])
        if min_liquidity_usd > 0:
            routes = [r for r in routes if r.liquidity_available_usd >= min_liquidity_usd]
        return sorted(routes, key=lambda r: r.fee_bps)

    def find_cheapest_route(
        self, source_chain: int, dest_chain: int, token: str,
    ) -> Optional[BridgeRoute]:
        routes = self.find_routes(source_chain, dest_chain, token)
        return routes[0] if routes else None

    def find_fastest_route(
        self, source_chain: int, dest_chain: int, token: str,
    ) -> Optional[BridgeRoute]:
        routes = self.find_routes(source_chain, dest_chain, token)
        return min(routes, key=lambda r: r.estimated_time_s) if routes else None

    def get_all_bridges_for_chain(self, chain_id: int) -> List[str]:
        """Get all bridge names that support a chain."""
        return [
            name for name, b in self._bridges.items()
            if chain_id in b.chains and b.is_active
        ]

    def get_bridge(self, name: str) -> Optional[Bridge]:
        return self._bridges.get(name)

    # ── Quote Generation ──────────────────────────────────────

    async def get_quote(
        self,
        bridge_name: str,
        source_chain: int,
        dest_chain: int,
        token: str,
        amount_usd: float,
    ) -> Optional[BridgeQuote]:
        """Generate a bridge quote with fee calculation."""
        bridge = self._bridges.get(bridge_name)
        if not bridge:
            return None

        key = f"{source_chain}-{dest_chain}-{token}"
        routes = [r for r in self._routes.get(key, []) if r.bridge_name == bridge_name]
        if not routes:
            return None

        route = routes[0]
        fee_usd = amount_usd * (route.fee_bps / 10_000)
        output = amount_usd - fee_usd

        return BridgeQuote(
            bridge_name=bridge_name,
            source_chain=source_chain,
            dest_chain=dest_chain,
            token=token,
            input_amount=amount_usd,
            output_amount=output,
            fee_usd=fee_usd,
            fee_bps=route.fee_bps,
            estimated_time_s=route.estimated_time_s,
            total_cost_usd=fee_usd,
            effective_rate=output / amount_usd if amount_usd > 0 else 0,
        )

    async def get_best_quote(
        self, source_chain: int, dest_chain: int, token: str, amount_usd: float,
    ) -> Optional[BridgeQuote]:
        """Get best quote across all bridges."""
        routes = self.find_routes(source_chain, dest_chain, token)
        best: Optional[BridgeQuote] = None

        for route in routes:
            quote = await self.get_quote(
                route.bridge_name, source_chain, dest_chain, token, amount_usd,
            )
            if quote and (best is None or quote.total_cost_usd < best.total_cost_usd):
                best = quote
        return best

    # ── Liquidity Tracking ────────────────────────────────────

    def update_liquidity(
        self,
        bridge_name: str,
        chain_id: int,
        token: str,
        available_usd: float,
        utilization_pct: float = 0.0,
    ) -> None:
        """Update liquidity for a bridge/chain/token combo."""
        key = f"{bridge_name}-{chain_id}-{token}"
        self._liquidity[key] = BridgeLiquidity(
            bridge_name=bridge_name,
            chain_id=chain_id,
            token=token,
            available_usd=available_usd,
            utilization_pct=utilization_pct,
            last_updated=time.time(),
        )

        # Update matching routes
        for routes in self._routes.values():
            for route in routes:
                if (route.bridge_name == bridge_name
                        and route.token == token
                        and (route.source_chain == chain_id or route.dest_chain == chain_id)):
                    route.liquidity_available_usd = available_usd

    def get_liquidity(
        self, bridge_name: str, chain_id: int, token: str,
    ) -> Optional[BridgeLiquidity]:
        key = f"{bridge_name}-{chain_id}-{token}"
        return self._liquidity.get(key)

    # ── Graph Export (for Hyper-Solver) ───────────────────────

    def export_graph_edges(self) -> List[Dict[str, Any]]:
        """
        Export all routes as graph edges for the N-hop solver.
        Each edge has: src_node, dst_node, weight (negative log rate),
        plus metadata.
        """
        edges = []
        for key, routes in self._routes.items():
            for route in routes:
                if not route.is_active:
                    continue
                effective_rate = 1.0 - (route.fee_bps / 10_000)
                import math
                weight = -math.log(effective_rate) if effective_rate > 0 else float("inf")

                edges.append({
                    "src_chain": route.source_chain,
                    "dst_chain": route.dest_chain,
                    "token": route.token,
                    "bridge": route.bridge_name,
                    "weight": weight,
                    "fee_bps": route.fee_bps,
                    "time_s": route.estimated_time_s,
                    "liquidity_usd": route.liquidity_available_usd,
                })
        return edges

    # ── Stats ─────────────────────────────────────────────────

    def get_stats(self) -> Dict[str, Any]:
        active = sum(1 for b in self._bridges.values() if b.is_active)
        total_routes = sum(len(r) for r in self._routes.values())
        return {
            "bridges_registered": len(self._bridges),
            "bridges_active": active,
            "total_routes": total_routes,
            "liquidity_entries": len(self._liquidity),
            "bridges": {
                name: {
                    "type": b.bridge_type.value,
                    "chains": len(b.chains),
                    "tokens": len(b.tokens),
                    "active": b.is_active,
                }
                for name, b in self._bridges.items()
            },
        }
