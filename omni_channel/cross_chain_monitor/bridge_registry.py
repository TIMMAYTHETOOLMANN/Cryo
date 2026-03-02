#!/usr/bin/env python3
"""
Bridge Registry
Database of all supported bridges and their routes

Supported bridges:
- Stargate Finance
- Hop Protocol
- Synapse Protocol
- Across Protocol
- Multichain (Anyswap)
- Wormhole
- LayerZero bridges
"""

import asyncio
import aiohttp
import time
from typing import Awaitable, Callable, Dict, List, Optional, Any, Set, Tuple
from dataclasses import dataclass, field
from enum import Enum
from web3 import Web3
import os


class BridgeType(Enum):
    """Bridge architecture types"""
    LOCK_AND_MINT = "lock_and_mint"  # Lock on source, mint on destination
    LIQUIDITY_POOL = "liquidity_pool"  # LP-based bridging
    ATOMIC = "atomic"  # Atomic cross-chain
    OPTIMISTIC = "optimistic"  # Optimistic verification
    ZK_LIGHT_CLIENT = "zk_light_client"  # ZK proof-based


@dataclass
class Bridge:
    """Bridge protocol data"""
    name: str
    bridge_type: BridgeType
    website: str
    docs: str
    chains: List[int] = field(default_factory=list)  # Supported chain IDs
    tokens: List[str] = field(default_factory=list)  # Supported tokens
    fee_range_min: float = 0.0005  # 0.05%
    fee_range_max: float = 0.005  # 0.5%
    finality_time_min: int = 60  # seconds
    finality_time_max: int = 1800  # 30 minutes
    security_model: str = ""  # multisig, optimistic, zk, etc.
    tvl_usd: float = 0
    volume_24h_usd: float = 0
    is_active: bool = True
    contracts: Dict[int, Dict[str, str]] = field(default_factory=dict)  # chain_id -> contract addresses


@dataclass
class BridgeRoute:
    """Specific bridge route between chains"""
    bridge_name: str
    source_chain: int
    destination_chain: int
    token: str
    source_pool: str  # Pool/router address on source
    destination_pool: str  # Pool/router address on destination
    fee_percent: float
    estimated_time_seconds: int
    min_amount: float = 0
    max_amount: float = float('inf')
    liquidity_available: float = 0
    is_active: bool = True


@dataclass
class BridgeTransaction:
    """Bridge transaction data"""
    tx_hash: str
    bridge_name: str
    source_chain: int
    destination_chain: int
    token: str
    amount: float
    sender: str
    receiver: str
    timestamp: int
    status: str  # pending, completed, failed
    destination_tx_hash: str = ""
    fee_paid: float = 0


class StargateFinance:
    """
    Stargate Finance Bridge
    https://stargate.finance/
    LayerZero-based bridge with instant finality
    """

    CHAIN_IDS = {
        1: 101,    # Ethereum
        42161: 110,  # Arbitrum
        10: 111,   # Optimism
        137: 109,  # Polygon
        8453: 184, # Base
        43114: 106, # Avalanche
        56: 102,   # BSC
        324: 165,  # zkSync
    }

    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key
        self.session: Optional[aiohttp.ClientSession] = None
        self.base_url = "https://api.stargate.finance/api"

    async def get_pools(self) -> List[Dict]:
        """Get all Stargate pools"""
        session = await self._get_session()
        url = f"{self.base_url}/pool"

        try:
            async with session.get(url) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return data.get('pool', [])
        except Exception as e:
            print(f"   ⚠️  Stargate pools error: {e}")

        return []

    async def get_quote(self, src_chain: int, dst_chain: int, token: str, amount: float) -> Optional[Dict]:
        """Get bridge quote"""
        session = await self._get_session()
        url = f"{self.base_url}/quote"
        params = {
            'srcChainId': self.CHAIN_IDS.get(src_chain, src_chain),
            'dstChainId': self.CHAIN_IDS.get(dst_chain, dst_chain),
            'token': token,
            'amount': amount
        }

        try:
            async with session.get(url, params=params) as resp:
                if resp.status == 200:
                    return await resp.json()
        except Exception as e:
            print(f"   ⚠️  Stargate quote error: {e}")

        return None

    async def _get_session(self) -> aiohttp.ClientSession:
        if self.session is None or self.session.closed:
            self.session = aiohttp.ClientSession()
        return self.session

    async def close(self):
        if self.session and not self.session.closed:
            await self.session.close()


class HopProtocol:
    """
    Hop Protocol Bridge
    https://hop.exchange/
    AMM-based bridge with bonder system
    """

    def __init__(self):
        self.session: Optional[aiohttp.ClientSession] = None
        self.base_url = "https://api.hop.exchange/v1"

    async def get_rates(self) -> Dict:
        """Get bridge rates"""
        session = await self._get_session()
        url = f"{self.base_url}/rates"

        try:
            async with session.get(url) as resp:
                if resp.status == 200:
                    return await resp.json()
        except Exception as e:
            print(f"   ⚠️  Hop rates error: {e}")

        return {}

    async def get_bonders(self) -> Dict:
        """Get bonder addresses"""
        session = await self._get_session()
        url = f"{self.base_url}/bonders"

        try:
            async with session.get(url) as resp:
                if resp.status == 200:
                    return await resp.json()
        except Exception as e:
            print(f"   ⚠️  Hop bonder error: {e}")

        return {}

    async def _get_session(self) -> aiohttp.ClientSession:
        if self.session is None or self.session.closed:
            self.session = aiohttp.ClientSession()
        return self.session

    async def close(self):
        if self.session and not self.session.closed:
            await self.session.close()


class SynapseProtocol:
    """
    Synapse Protocol Bridge
    https://synapseprotocol.com/
    Cross-chain liquidity network
    """

    def __init__(self):
        self.session: Optional[aiohttp.ClientSession] = None
        self.base_url = "https://api.synapseprotocol.com"

    async def get_pools(self, chain_id: int) -> List[Dict]:
        """Get pools on specific chain"""
        session = await self._get_session()
        url = f"{self.base_url}/pools/{chain_id}"

        try:
            async with session.get(url) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return data.get('pools', [])
        except Exception as e:
            print(f"   ⚠️  Synapse pools error: {e}")

        return []

    async def get_swap_output(self, chain_id: int, token_in: str, token_out: str, amount: float) -> Optional[float]:
        """Calculate swap output"""
        session = await self._get_session()
        url = f"{self.base_url}/swap/output"
        params = {
            'chainId': chain_id,
            'tokenIn': token_in,
            'tokenOut': token_out,
            'amount': amount
        }

        try:
            async with session.get(url, params=params) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return float(data.get('output', 0))
        except Exception as e:
            print(f"   ⚠️  Synapse swap output error: {e}")

        return None

    async def _get_session(self) -> aiohttp.ClientSession:
        if self.session is None or self.session.closed:
            self.session = aiohttp.ClientSession()
        return self.session

    async def close(self):
        if self.session and not self.session.closed:
            await self.session.close()


class AcrossProtocol:
    """
    Across Protocol
    https://across.to/
    Optimistic bridge with fast finality
    """

    def __init__(self):
        self.session: Optional[aiohttp.ClientSession] = None
        self.base_url = "https://api.across.to/v2"

    async def get_pools(self) -> List[Dict]:
        """Get pool data"""
        session = await self._get_session()
        url = f"{self.base_url}/pools"

        try:
            async with session.get(url) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return data.get('data', [])
        except Exception as e:
            print(f"   ⚠️  Across pools error: {e}")

        return []

    async def get_suggested_fees(self, token: str, amount: float, from_chain: int, to_chain: int) -> Optional[Dict]:
        """Get suggested fees for bridge"""
        session = await self._get_session()
        url = f"{self.base_url}/suggested-fees"
        params = {
            'token': token,
            'amount': amount,
            'originChainId': from_chain,
            'destinationChainId': to_chain
        }

        try:
            async with session.get(url, params=params) as resp:
                if resp.status == 200:
                    return await resp.json()
        except Exception as e:
            print(f"   ⚠️  Across fees error: {e}")

        return None

    async def _get_session(self) -> aiohttp.ClientSession:
        if self.session is None or self.session.closed:
            self.session = aiohttp.ClientSession()
        return self.session

    async def close(self):
        if self.session and not self.session.closed:
            await self.session.close()


class BridgeRegistry:
    """
    Central registry for all bridges
    Aggregates data from multiple bridge protocols
    """

    def __init__(self, config: Dict[str, Any] = None):
        self.config = config or {}
        self.is_running = False

        # Initialize bridge clients
        self.stargate = StargateFinance()
        self.hop = HopProtocol()
        self.synapse = SynapseProtocol()
        self.across = AcrossProtocol()

        # Bridge database
        self._bridges: Dict[str, Bridge] = {}
        self._routes: Dict[str, BridgeRoute] = {}  # key: "bridge:src:dst:token"

        # Callbacks
        self._route_update_callbacks: List[Callable[[BridgeRoute], Awaitable[None]]] = []

        # Statistics
        self.bridges_registered = 0
        self.routes_tracked = 0

        print("🌉 Bridge Registry initialized")

    async def start(self):
        """Start bridge registry"""
        print("\n🌉 Starting Bridge Registry...")
        self.is_running = True

        # Initialize bridge data
        await self._initialize_bridges()

        # Start background updates
        asyncio.create_task(self._update_loop())

        print("   ✅ Bridge Registry started")

    async def stop(self):
        """Stop registry"""
        self.is_running = False

        await self.stargate.close()
        await self.hop.close()
        await self.synapse.close()
        await self.across.close()

        print("   🌉 Bridge Registry stopped")

    async def _initialize_bridges(self):
        """Initialize bridge database"""
        print("   📊 Initializing bridge data...")

        # Register Stargate
        stargate_bridge = Bridge(
            name="Stargate Finance",
            bridge_type=BridgeType.LIQUIDITY_POOL,
            website="https://stargate.finance",
            docs="https://stargateprotocol.gitbook.io",
            chains=[1, 42161, 10, 137, 8453, 43114, 56, 324],
            tokens=["USDC", "USDT", "ETH", "STG"],
            fee_range_min=0.0005,
            fee_range_max=0.002,
            finality_time_min=60,
            finality_time_max=300,
            security_model="LayerZero",
            contracts={}  # Would populate from API
        )
        self._bridges["stargate"] = stargate_bridge
        self.bridges_registered += 1

        # Register Hop
        hop_bridge = Bridge(
            name="Hop Protocol",
            bridge_type=BridgeType.LIQUIDITY_POOL,
            website="https://hop.exchange",
            docs="https://docs.hop.exchange",
            chains=[1, 42161, 10, 137, 8453],
            tokens=["ETH", "USDC", "USDT", "DAI", "MATIC"],
            fee_range_min=0.001,
            fee_range_max=0.005,
            finality_time_min=120,
            finality_time_max=600,
            security_model="Bonder Network",
        )
        self._bridges["hop"] = hop_bridge
        self.bridges_registered += 1

        # Register Synapse
        synapse_bridge = Bridge(
            name="Synapse Protocol",
            bridge_type=BridgeType.LIQUIDITY_POOL,
            website="https://synapseprotocol.com",
            docs="https://docs.synapseprotocol.com",
            chains=[1, 42161, 10, 137, 8453, 43114, 56],
            tokens=["ETH", "USDC", "USDT", "DAI", "SYN"],
            fee_range_min=0.0005,
            fee_range_max=0.003,
            finality_time_min=90,
            finality_time_max=900,
            security_model="Multisig",
        )
        self._bridges["synapse"] = synapse_bridge
        self.bridges_registered += 1

        # Register Across
        across_bridge = Bridge(
            name="Across Protocol",
            bridge_type=BridgeType.OPTIMISTIC,
            website="https://across.to",
            docs="https://docs.across.to",
            chains=[1, 42161, 10, 137, 8453],
            tokens=["ETH", "USDC", "WBTC", "DAI"],
            fee_range_min=0.0003,
            fee_range_max=0.002,
            finality_time_min=30,
            finality_time_max=120,
            security_model="Optimistic Oracle",
        )
        self._bridges["across"] = across_bridge
        self.bridges_registered += 1

        print(f"   🌉 Registered {self.bridges_registered} bridges")

    async def _update_loop(self):
        """Background update loop"""
        while self.is_running:
            try:
                # Update liquidity and fees every minute
                await self._update_routes()
                await asyncio.sleep(60)

            except Exception as e:
                print(f"   ⚠️  Bridge Registry update error: {e}")
                await asyncio.sleep(30)

    async def _update_routes(self):
        """Update route data from all bridges"""
        # This would fetch real-time data from bridge APIs
        # For now, placeholder for update logic
        pass

    def get_bridge(self, name: str) -> Optional[Bridge]:
        """Get bridge by name"""
        return self._bridges.get(name)

    def get_all_bridges(self) -> List[Bridge]:
        """Get all registered bridges"""
        return list(self._bridges.values())

    def get_bridges_for_chain(self, chain_id: int) -> List[Bridge]:
        """Get bridges that support specific chain"""
        return [b for b in self._bridges.values() if chain_id in b.chains]

    def get_routes(self, src_chain: int, dst_chain: int, token: str = None) -> List[BridgeRoute]:
        """Get available routes between chains"""
        routes = []

        for key, route in self._routes.items():
            if route.source_chain == src_chain and route.destination_chain == dst_chain:
                if token is None or route.token == token:
                    routes.append(route)

        return routes

    def get_best_route(self, src_chain: int, dst_chain: int, token: str, amount: float) -> Optional[BridgeRoute]:
        """Get best route based on fee and liquidity"""
        routes = self.get_routes(src_chain, dst_chain, token)

        # Filter by liquidity and amount limits
        valid_routes = [
            r for r in routes
            if r.is_active and r.liquidity_available >= amount
            and r.min_amount <= amount <= r.max_amount
        ]

        if not valid_routes:
            return None

        # Return lowest fee route
        return min(valid_routes, key=lambda r: r.fee_percent)

    def add_route(self, route: BridgeRoute):
        """Add or update route"""
        key = f"{route.bridge_name}:{route.source_chain}:{route.destination_chain}:{route.token}"
        self._routes[key] = route
        self.routes_tracked += 1

    def on_route_update(self, callback: Callable[[BridgeRoute], Awaitable[None]]):
        """Register route update callback"""
        self._route_update_callbacks.append(callback)

    async def _emit_route_update(self, route: BridgeRoute):
        """Emit route update to callbacks"""
        for callback in self._route_update_callbacks:
            try:
                await callback(route)
            except Exception as e:
                print(f"   ⚠️  Route callback error: {e}")

    def get_stats(self) -> Dict:
        """Get statistics"""
        return {
            'bridges_registered': self.bridges_registered,
            'routes_tracked': self.routes_tracked,
            'bridges': list(self._bridges.keys()),
        }
