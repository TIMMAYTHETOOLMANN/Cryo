#!/usr/bin/env python3
"""
Liquidity Tracker
Monitor bridge and pool liquidity across all chains

Features:
- Real-time liquidity monitoring
- Liquidity change alerts
- Capacity tracking for bridges
- Pool reserve monitoring
"""

import asyncio
import aiohttp
import time
from typing import Dict, List, Optional, Any, Callable, Awaitable, Set
from dataclasses import dataclass, field
from web3 import Web3
import os

from .multi_chain_graph import Pool


@dataclass
class LiquiditySnapshot:
    """Snapshot of liquidity at a point in time"""
    timestamp: int
    protocol: str  # bridge or DEX name
    chain_id: int
    token: str
    amount: float
    amount_usd: float
    capacity_percent: float  # For bridges, % of total capacity
    is_sufficient: bool = True


@dataclass
class LiquidityAlert:
    """Liquidity threshold alert"""
    alert_id: str
    protocol: str
    chain_id: int
    token: str
    alert_type: str  # low_liquidity, high_utilization, capacity_exceeded
    current_amount: float
    threshold_amount: float
    severity: str  # warning, critical
    timestamp: int
    message: str


class LiquidityTracker:
    """
    Track liquidity across bridges and DEXes
    """

    def __init__(self, config: Dict[str, Any] = None):
        self.config = config or {}
        self.is_running = False

        # Web3 providers
        self.w3_providers: Dict[int, Web3] = {}
        self._init_providers()

        # Liquidity data
        self._bridge_liquidity: Dict[str, Dict[int, Dict[str, float]]] = {}  # bridge -> chain -> token -> amount
        self._pool_liquidity: Dict[str, Pool] = {}  # pool address -> Pool
        self._snapshots: List[LiquiditySnapshot] = []

        # Alert thresholds
        self.low_liquidity_threshold_usd = self.config.get('low_liquidity_threshold_usd', 100000)
        self.high_utilization_threshold = self.config.get('high_utilization_threshold', 0.8)

        # Callbacks
        self._alert_callbacks: List[Callable[[LiquidityAlert], Awaitable[None]]] = []
        self._update_callbacks: List[Callable[[LiquiditySnapshot], Awaitable[None]]] = []

        # Statistics
        self.snapshots_taken = 0
        self.alerts_triggered = 0

        print("💧 Liquidity Tracker initialized")

    def _init_providers(self):
        """Initialize Web3 providers"""
        rpc_endpoints = {
            1: os.getenv('ETH_RPC_URL', 'https://eth.llamarpc.com'),
            42161: os.getenv('ARBITRUM_RPC_URL', 'https://arb1.arbitrum.io/rpc'),
            10: os.getenv('OPTIMISM_RPC_URL', 'https://mainnet.optimism.io'),
            137: os.getenv('POLYGON_RPC_URL', 'https://polygon-rpc.com'),
            8453: os.getenv('BASE_RPC_URL', 'https://mainnet.base.org'),
        }

        for chain_id, rpc_url in rpc_endpoints.items():
            try:
                self.w3_providers[chain_id] = Web3(Web3.HTTPProvider(rpc_url))
            except Exception as e:
                print(f"   ⚠️  RPC init error for chain {chain_id}: {e}")

    async def start(self):
        """Start liquidity tracking"""
        print("\n💧 Starting Liquidity Tracker...")
        self.is_running = True

        # Start background monitoring
        asyncio.create_task(self._monitor_loop())

        print("   ✅ Liquidity Tracker started")

    async def stop(self):
        """Stop tracking"""
        self.is_running = False
        print("   💧 Liquidity Tracker stopped")

    async def _monitor_loop(self):
        """Background monitoring loop"""
        while self.is_running:
            try:
                # Update liquidity every 30 seconds
                await self._update_bridge_liquidity()
                await self._update_pool_liquidity()
                await asyncio.sleep(30)

            except Exception as e:
                print(f"   ⚠️  Liquidity Tracker error: {e}")
                await asyncio.sleep(15)

    async def _update_bridge_liquidity(self):
        """Update bridge liquidity data"""
        # This would fetch real liquidity data from bridge contracts
        # For now, simulate with placeholder data

        bridges = ['stargate', 'hop', 'synapse', 'across']
        chains = [1, 42161, 10, 137, 8453]
        tokens = ['USDC', 'USDT', 'ETH']

        for bridge in bridges:
            if bridge not in self._bridge_liquidity:
                self._bridge_liquidity[bridge] = {}

            for chain in chains:
                if chain not in self._bridge_liquidity[bridge]:
                    self._bridge_liquidity[bridge][chain] = {}

                for token in tokens:
                    # Simulate liquidity (would be real data from contracts)
                    liquidity = self._simulate_liquidity(bridge, chain, token)
                    self._bridge_liquidity[bridge][chain][token] = liquidity

                    # Create snapshot
                    snapshot = LiquiditySnapshot(
                        timestamp=int(time.time()),
                        protocol=bridge,
                        chain_id=chain,
                        token=token,
                        amount=liquidity,
                        amount_usd=liquidity * self._get_token_price(token),
                        capacity_percent=liquidity / 1000000,  # Assume 1M capacity
                        is_sufficient=liquidity * self._get_token_price(token) > self.low_liquidity_threshold_usd
                    )

                    self._snapshots.append(snapshot)
                    self.snapshots_taken += 1

                    # Check for alerts
                    await self._check_alerts(snapshot)

        # Keep only recent snapshots
        cutoff = time.time() - 3600  # 1 hour
        self._snapshots = [s for s in self._snapshots if s.timestamp > cutoff]

    async def _update_pool_liquidity(self):
        """Update DEX pool liquidity"""
        # This would fetch pool data from DEXes
        # For now, placeholder
        pass

    def _simulate_liquidity(self, bridge: str, chain: int, token: str) -> float:
        """Simulate bridge liquidity (replace with real data)"""
        # Base liquidity
        base = {
            'USDC': 500000,
            'USDT': 400000,
            'ETH': 200,
        }.get(token, 100000)

        # Vary by bridge
        bridge_multiplier = {
            'stargate': 1.5,
            'hop': 1.2,
            'synapse': 1.0,
            'across': 0.8,
        }.get(bridge, 1.0)

        # Vary by chain
        chain_multiplier = {
            1: 2.0,
            42161: 1.5,
            10: 1.2,
            137: 1.0,
            8453: 0.8,
        }.get(chain, 1.0)

        return base * bridge_multiplier * chain_multiplier

    def _get_token_price(self, token: str) -> float:
        """Get token price in USD (would use oracle)"""
        prices = {
            'USDC': 1.0,
            'USDT': 1.0,
            'ETH': 2000,
            'WBTC': 40000,
            'DAI': 1.0,
        }
        return prices.get(token.upper(), 1.0)

    async def _check_alerts(self, snapshot: LiquiditySnapshot):
        """Check if snapshot triggers alert"""
        if not snapshot.is_sufficient:
            alert = LiquidityAlert(
                alert_id=f"low_liq_{snapshot.protocol}_{snapshot.chain_id}_{snapshot.token}_{snapshot.timestamp}",
                protocol=snapshot.protocol,
                chain_id=snapshot.chain_id,
                token=snapshot.token,
                alert_type="low_liquidity",
                current_amount=snapshot.amount,
                threshold_amount=self.low_liquidity_threshold_usd / self._get_token_price(snapshot.token),
                severity="warning" if snapshot.capacity_percent > 0.5 else "critical",
                timestamp=snapshot.timestamp,
                message=f"Low liquidity on {snapshot.protocol} chain {snapshot.chain_id}: {snapshot.token}"
            )

            self.alerts_triggered += 1
            await self._emit_alert(alert)

    def get_bridge_liquidity(self, bridge: str, chain_id: int = None, token: str = None) -> Dict:
        """Get liquidity for bridge"""
        if bridge not in self._bridge_liquidity:
            return {}

        data = self._bridge_liquidity[bridge]

        if chain_id:
            data = {chain_id: data.get(chain_id, {})}

        if token:
            filtered = {}
            for chain, tokens in data.items():
                if token in tokens:
                    filtered[chain] = {token: tokens[token]}
            return filtered

        return data

    def get_best_bridge_route(self, src_chain: int, dst_chain: int,
                               token: str, amount: float) -> Optional[str]:
        """Get bridge with best liquidity for route"""
        best_bridge = None
        best_liquidity = 0

        for bridge, chains in self._bridge_liquidity.items():
            # Check if bridge supports both chains
            if src_chain not in chains or dst_chain not in chains:
                continue

            # Check liquidity on both chains
            src_liq = chains[src_chain].get(token, 0)
            dst_liq = chains[dst_chain].get(token, 0)

            min_liq = min(src_liq, dst_liq)

            if min_liq > best_liquidity and min_liq >= amount:
                best_liquidity = min_liq
                best_bridge = bridge

        return best_bridge

    def get_all_snapshots(self, hours: int = 1) -> List[LiquiditySnapshot]:
        """Get recent snapshots"""
        cutoff = time.time() - (hours * 3600)
        return [s for s in self._snapshots if s.timestamp > cutoff]

    def get_protocol_liquidity(self, protocol: str) -> List[LiquiditySnapshot]:
        """Get liquidity history for protocol"""
        return [s for s in self._snapshots if s.protocol == protocol]

    def on_alert(self, callback: Callable[[LiquidityAlert], Awaitable[None]]):
        """Register alert callback"""
        self._alert_callbacks.append(callback)

    def on_update(self, callback: Callable[[LiquiditySnapshot], Awaitable[None]]):
        """Register update callback"""
        self._update_callbacks.append(callback)

    async def _emit_alert(self, alert: LiquidityAlert):
        """Emit alert to callbacks"""
        print(f"   🚨 ALERT: {alert.message}")
        for callback in self._alert_callbacks:
            try:
                await callback(alert)
            except Exception as e:
                print(f"   ⚠️  Alert callback error: {e}")

    async def _emit_update(self, snapshot: LiquiditySnapshot):
        """Emit update to callbacks"""
        for callback in self._update_callbacks:
            try:
                await callback(snapshot)
            except Exception as e:
                print(f"   ⚠️  Update callback error: {e}")

    def get_stats(self) -> Dict:
        """Get statistics"""
        total_liquidity_usd = 0

        for bridge, chains in self._bridge_liquidity.items():
            for chain, tokens in chains.items():
                for token, amount in tokens.items():
                    total_liquidity_usd += amount * self._get_token_price(token)

        return {
            'snapshots_taken': self.snapshots_taken,
            'alerts_triggered': self.alerts_triggered,
            'bridges_tracked': len(self._bridge_liquidity),
            'total_liquidity_usd': total_liquidity_usd,
        }
