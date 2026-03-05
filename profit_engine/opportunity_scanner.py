#!/usr/bin/env python3
"""
OPPORTUNITY SCANNER — Multi-Vector Opportunity Detection Engine
================================================================
Scans all 6 opportunity vectors simultaneously:

 1. Standard Liquidations      → Mempool Radar + Health Factor monitoring
 2. Preemptive Liquidations    → Oracle Update Sniffing (200-500ms advantage)
 3. New Protocol Launches      → Contract Discovery Crawler + KOL tracking
 4. Cross-Chain Arbitrage      → Bridge Monitor + N-hop pathfinder
 5. Undercollateralized Pools  → Static Analysis + on-chain scanning
 6. NFT-Backed Loans           → Protocol-specific scanners (BendDAO, NFTfi, etc.)

Each vector produces OpportunitySignal objects that flow through:
  Scanner → Gas Optimizer → Flash Loan Router → Execution Router → Ledger
"""

import asyncio
import time
import uuid
import json
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any, Callable, Awaitable
from enum import Enum
from web3 import Web3
import os

# Project imports
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from omni_channel.data_lake.data_models import (
    OpportunitySignal, SignalType, SignalSource, ExecutionModule,
    MempoolTransaction, OracleUpdate, LargeSwap, ProtocolInfo,
)
from .gas_optimizer import GasOptimizer, GasEstimate
from .flash_loan_router import FlashLoanRouter, FlashLoanRoute
from .heat_map import HeatMap
from .rpc_gateway import RPCGateway, RequestPriority, build_default_gateway


class OpportunityVector(Enum):
    """The 6 opportunity vectors"""
    STANDARD_LIQUIDATION = "standard_liquidation"
    PREEMPTIVE_LIQUIDATION = "preemptive_liquidation"
    NEW_PROTOCOL = "new_protocol"
    CROSS_CHAIN_ARB = "cross_chain_arb"
    UNDERCOLLATERALIZED_POOL = "undercollateralized_pool"
    NFT_BACKED_LOAN = "nft_backed_loan"


@dataclass
class ScanResult:
    """Result from scanning a single vector"""
    vector: OpportunityVector
    chain_id: int
    opportunities: List[OpportunitySignal]
    scan_duration_ms: float
    block_number: int


# ──────────────────────────────────────────────
# PROTOCOL CONFIGURATIONS
# ──────────────────────────────────────────────

AAVE_V3_POOLS = {
    1: '0x87870Bca3F3fD6335C3F4ce8392D69350B4fA4E2',
    42161: '0x794a61358D6845594F94dc1DB02A252b5b4814aD',
    10: '0xB50201558B00496A145fE76f7424749556E326D8',
    137: '0x794a61358D6845594F94dc1DB02A252b5b4814aD',
    8453: '0xA238Dd80C259a72e81d7e4664a9801593F337052',
    43114: '0x794a61358D6845594F94dc1DB02A252b5b4814aD',
}

COMPOUND_V3_MARKETS = {
    1: [
        '0xc3d688B66703497DAA19211EEdff47f25384cdc3',  # cUSDCv3
        '0xA17581A9E3356d9A858b789D68B4d866e593aE94',  # cWETHv3
    ],
    42161: ['0xA5EDBDD9646f8dFF606d7448e414884C7d905dCA'],
    137: ['0xF25212E676D1F7F89Cd72fFEe66158f541246445'],
    8453: ['0xb125E6687d4313864e53df431d5425969c15Eb2F'],
}

CHAINLINK_ORACLES = {
    1: {
        'ETH/USD': '0x5f4eC3Df9cbd43714FE2740f5E3616155c5b8419',
        'BTC/USD': '0xF4030086522a5bEEa4988F8cA5B36dbC97BeE88c',
        'LINK/USD': '0x2c1d072e956AFFC0D435Cb7AC38EF18d24d9127c',
        'USDC/USD': '0x8fFfFfd4AfB6115b954Bd326cbe7B4BA576818f6',
    },
    8453: {
        'ETH/USD': '0x71041dddad3595F9CEd3DcCFBe3D1F4b0a16Bb70',
        'USDC/USD': '0x7e8648a8806220677F678508e8EBe5763071b782',
    },
    10: {
        'ETH/USD': '0x13e3Ee699D1909E989722E753853AE30b17e08c5',
        'USDC/USD': '0x16a9FEaCfFA43AdeCc6612dd74348590ecAb9794',
    },
    42161: {
        'ETH/USD': '0x639Fe6ab55C939f4930680b556f0597271017801',
        'USDC/USD': '0x50834F3163758fcC1Df9973b6e91f0C0bd525C23',
    }
}

# NFT lending protocols
NFT_LENDING_PROTOCOLS = {
    1: {
        'BendDAO': '0x70b97A0da65C15dfb0FFA02aEE6FA36e507C2762',
        'NFTfi': '0xf896527c49b44aAb3Cf22aE356Fa3AF8E331F280',
        'ParaSpace': '0x638a98BBB92a7582d07C52ff407D49664DC8b3Ee',
        'Blend': '0x29469395eAf6f95920E59F858042f0e28D98a20B',
    },
}

# ── Enhancement 1: Per-Asset Aave V3 Liquidation Bonuses (actual on-chain values) ──
# Maps collateral asset address (lowercase) → liquidation bonus as a decimal.
# Source: Aave V3 Ethereum reserve configs (liquidationBonus - 10000) / 10000.
AAVE_V3_LIQUIDATION_BONUS = {
    # Stablecoins — 4.5% bonus
    '0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48': 0.045,  # USDC
    '0xdac17f958d2ee523a2206206994597c13d831ec7': 0.045,  # USDT
    '0x6b175474e89094c44da98b954eedeac495271d0f': 0.04,   # DAI
    '0x853d955acef822db058eb8505911ed77f175b99e': 0.05,   # FRAX
    '0x40d16fc0246ad3160ccc09b8d0d3a2cd28ae6c2f': 0.025,  # GHO
    # ETH and liquid staking — 5% bonus
    '0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2': 0.05,   # WETH
    '0xae7ab96520de3a18e5e111b5eaab095312d7fe84': 0.07,   # stETH
    '0xae78736cd615f374d3085123a210448e74fc6393': 0.075,  # rETH
    '0xbe9895146f7af43049ca1c1ae358b0541ea49704': 0.075,  # cbETH
    # BTC — 7-10% bonus
    '0x2260fac5e5542a773aa44fbcfedf7c193bc2c599': 0.07,   # WBTC
    # DeFi tokens — higher bonus (10-15%)
    '0x514910771af9ca656af840dff83e8264ecf986ca': 0.07,   # LINK
    '0x7fc66500c84a76ad7e9c93437bfc5ac33e2ddae9': 0.075,  # AAVE
    '0x1f9840a85d5af5bf1d1762f925bdaddc4201f984': 0.10,   # UNI
    '0x9f8f72aa9304c8b593d555f12ef6589cc3a579a2': 0.075,  # MKR
    '0xd533a949740bb3306d119cc777fa900ba034cd52': 0.08,   # CRV
    '0xc011a73ee8576fb46f5e1c5751ca3b9fe0af2a6f': 0.07,   # SNX
    '0xc00e94cb662c3520282e6f5717214004a7f26888': 0.08,   # COMP
    '0xba100000625a3754423978a60c9317c58a424e3d': 0.085,  # BAL
    '0xc18360217d8f7ab5e7c516566761ea12ce7f9d72': 0.10,   # ENS
    '0x111111111117dc0aa78b770fa6a738034120c302': 0.085,  # 1INCH
}

# ── Enhancement 4: Slippage estimates per asset liquidity depth ──
# Maps collateral asset address (lowercase) → estimated slippage for a $50k liquidation.
# Deep-liquidity assets have minimal slippage; illiquid tokens lose more on DEX sale.
COLLATERAL_SLIPPAGE_BPS = {
    '0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2': 5,     # WETH — very deep
    '0x2260fac5e5542a773aa44fbcfedf7c193bc2c599': 10,    # WBTC — deep
    '0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48': 2,     # USDC — deepest
    '0xdac17f958d2ee523a2206206994597c13d831ec7': 3,     # USDT — deep
    '0x6b175474e89094c44da98b954eedeac495271d0f': 5,     # DAI — deep
    '0x514910771af9ca656af840dff83e8264ecf986ca': 25,    # LINK — medium
    '0x7fc66500c84a76ad7e9c93437bfc5ac33e2ddae9': 30,    # AAVE — medium
    '0xae7ab96520de3a18e5e111b5eaab095312d7fe84': 10,    # stETH — deep via Curve
    '0xae78736cd615f374d3085123a210448e74fc6393': 15,    # rETH
    '0xbe9895146f7af43049ca1c1ae358b0541ea49704': 15,    # cbETH
    '0x1f9840a85d5af5bf1d1762f925bdaddc4201f984': 30,    # UNI — medium
    '0x9f8f72aa9304c8b593d555f12ef6589cc3a579a2': 40,    # MKR — thinner
    '0xd533a949740bb3306d119cc777fa900ba034cd52': 35,    # CRV — medium
    '0xc011a73ee8576fb46f5e1c5751ca3b9fe0af2a6f': 40,    # SNX — thinner
    '0xc00e94cb662c3520282e6f5717214004a7f26888': 40,    # COMP — thinner
    '0xba100000625a3754423978a60c9317c58a424e3d': 50,    # BAL — thin
    '0xc18360217d8f7ab5e7c516566761ea12ce7f9d72': 60,    # ENS — thin
    '0x111111111117dc0aa78b770fa6a738034120c302': 50,    # 1INCH — thin
}

# ── Enhancement 5: Oracle heartbeat thresholds (seconds) ──
ORACLE_HEARTBEAT = {
    'ETH/USD': 3600,
    'BTC/USD': 3600,
    'LINK/USD': 3600,
    'USDC/USD': 86400,
    'USDT/USD': 86400,
    'DAI/USD': 3600,
    'AAVE/USD': 3600,
    'UNI/USD': 3600,
    'MKR/USD': 3600,
    'CRV/USD': 86400,
    'SNX/USD': 3600,
    'COMP/USD': 3600,
    'stETH/USD': 3600,
    'rETH/USD': 86400,
    'cbETH/USD': 86400,
}

# ── Enhancement 6: Per-chain scan intervals (seconds) ──
# L2s with fast blocks should scan more frequently to catch liquidations before competitors.
CHAIN_SCAN_INTERVALS = {
    1:     5.0,    # Ethereum — ~12s blocks, 5s scan is aggressive enough
    42161: 1.0,    # Arbitrum — ~0.25s blocks, scan every block
    10:    2.0,    # Optimism — ~2s blocks
    137:   2.0,    # Polygon — ~2s blocks
    8453:  1.5,    # Base — ~2s blocks
    43114: 2.0,    # Avalanche — ~2s blocks
    56:    3.0,    # BSC — ~3s blocks
    324:   1.0,    # zkSync — ~1s blocks
}


def get_liquidation_bonus(collateral_asset: str) -> float:
    """Get actual Aave V3 liquidation bonus for a collateral asset.

    Enhancement 1: Uses real per-asset bonus rates instead of 2-tier 5%/10%.
    Returns the bonus as a decimal (e.g., 0.05 for 5%).
    """
    return AAVE_V3_LIQUIDATION_BONUS.get(collateral_asset.lower(), 0.05)


def get_slippage_discount(collateral_asset: str, debt_usd: float) -> float:
    """Estimate slippage discount for selling liquidated collateral.

    Enhancement 4: Scales slippage with position size — larger positions
    incur more slippage.  Returns a multiplier (e.g., 0.995 for 0.5% slippage).
    """
    base_bps = COLLATERAL_SLIPPAGE_BPS.get(collateral_asset.lower(), 50)
    # Scale slippage linearly with debt size: $50k is the base, $500k = 10x slippage
    size_multiplier = max(1.0, debt_usd / 50_000)
    effective_bps = base_bps * min(size_multiplier, 5.0)  # Cap at 5x base
    return max(0.9, 1.0 - effective_bps / 10_000)


def compute_optimal_close_factor(health_factor: float) -> float:
    """Compute optimal debt close factor for maximum profit.

    Enhancement 3: Aave V3 allows 100% close factor when HF < 0.95 (CLOSE_FACTOR_HF_THRESHOLD).
    Between 0.95 and 1.0, the standard 50% applies.  Using 100% when allowed doubles
    the liquidation bonus captured in a single transaction.
    """
    if health_factor < 0.95:
        return 1.0   # Full close — doubles the captured bonus
    return 0.5        # Standard 50% close factor

# Health factor ABI
HEALTH_FACTOR_ABI = json.loads('''
[{"inputs":[{"name":"user","type":"address"}],"name":"getUserAccountData","outputs":[
  {"name":"totalCollateralBase","type":"uint256"},
  {"name":"totalDebtBase","type":"uint256"},
  {"name":"availableBorrowsBase","type":"uint256"},
  {"name":"currentLiquidationThreshold","type":"uint256"},
  {"name":"ltv","type":"uint256"},
  {"name":"healthFactor","type":"uint256"}
],"stateMutability":"view","type":"function"}]
''')


class OpportunityScanner:
    """
    Multi-vector opportunity scanner.
    Runs all 6 vectors concurrently across all supported chains.
    """

    def __init__(
        self,
        gas_optimizer: GasOptimizer,
        flash_loan_router: FlashLoanRouter,
        heat_map: HeatMap,
        config: Dict[str, Any] = None,
        rpc_gateway: RPCGateway = None,
    ):
        self.gas_optimizer = gas_optimizer
        self.flash_loan_router = flash_loan_router
        self.heat_map = heat_map
        self.config = config or {}

        # RPC Gateway (if provided, replaces raw Web3 init)
        self.rpc_gateway = rpc_gateway

        # Web3 providers per chain
        self._w3: Dict[int, Web3] = {}

        # Scan interval (seconds)
        self.scan_interval = self.config.get('scan_interval', 3.0)

        # Minimum thresholds
        self.min_profit_usd = self.config.get('min_profit_usd', 10.0)
        self.min_confidence = self.config.get('min_confidence', 0.4)

        # Tracked positions for liquidation monitoring
        self._tracked_positions: Dict[str, Dict] = {}  # user_addr → position data
        self._watchlist: Dict[str, Dict] = {}  # near-liquidatable positions (HF 1.0-1.03)
        self._position_update_interval = 30  # seconds
        self._last_position_update = 0.0

        # Oracle price cache
        self._oracle_prices: Dict[str, float] = {}

        # Opportunity callbacks
        self._callbacks: List[Callable[[OpportunitySignal], Awaitable[None]]] = []

        # Running state
        self.is_running = False
        self.total_scans = 0
        self.total_opportunities_found = 0

        # Stats per vector
        self.vector_stats: Dict[str, Dict] = {v.value: {'found': 0, 'executed': 0, 'profit': 0.0,
                                                          'failures': 0, 'success_rate': 1.0}
                                                for v in OpportunityVector}

        # ── Execution Feedback ──
        # Track success/failure per vector+chain+protocol to deprioritize failing combos
        self._execution_feedback: Dict[str, Dict] = {}  # "vector:chain:protocol" → {successes, failures, rate}

        # ── Batch Accumulation Queue ──
        # During initial reconnaissance, accumulate opportunities instead of emitting one-by-one.
        # Once recon sweep completes, flush all queued opportunities sorted by profit (highest first).
        self._recon_queue: List[OpportunitySignal] = []
        self._recon_phase_active = True  # True during initial scan sweep
        self._recon_started_at: Optional[float] = None
        self._recon_sweep_duration = float(self.config.get('recon_sweep_seconds', 120))  # 2min default

        # ── Gas Retry Queue ──
        # Opportunities rejected by gas gate held for retry when gas drops
        self._gas_retry_queue: List[OpportunitySignal] = []
        self._max_retry_queue_size = 50
        self._gas_retry_interval = 30  # seconds between retry sweeps

        print("🔍 Opportunity Scanner initialized")
        print(f"   Scan interval: {self.scan_interval}s")
        print(f"   Min profit: ${self.min_profit_usd}")
        print(f"   Vectors: {len(OpportunityVector)}")
        print(f"   Recon sweep: {self._recon_sweep_duration}s")

    # ──────────────────────────────────────────────
    # LIFECYCLE
    # ──────────────────────────────────────────────

    async def initialize(self):
        """Initialize Web3 connections to all chains via RPC Gateway."""

        if self.rpc_gateway:
            # ── USE MANAGED GATEWAY ──
            await self.rpc_gateway.start()
            self._w3 = self.rpc_gateway.get_all_w3()
            print(f"   🔌 Scanner using RPC Gateway ({len(self._w3)} chains)")
        else:
            # ── LEGACY: direct Web3 init ──
            eth_rpc = os.getenv('ETH_RPC_URL') or os.getenv('MAINNET_RPC_URL', 'https://eth.llamarpc.com')
            rpc_endpoints = {
                1: eth_rpc,
                42161: os.getenv('ARBITRUM_RPC_URL', 'https://arb1.arbitrum.io/rpc'),
                10: os.getenv('OPTIMISM_RPC_URL', 'https://mainnet.optimism.io'),
                137: os.getenv('POLYGON_RPC_URL', 'https://polygon-rpc.com'),
                8453: os.getenv('BASE_RPC_URL', 'https://mainnet.base.org'),
                43114: os.getenv('AVALANCHE_RPC_URL') or os.getenv('AVAX_RPC_URL', 'https://api.avax.network/ext/bc/C/rpc'),
                56: os.getenv('BSC_RPC_URL', 'https://bsc-dataseed.binance.org'),
                324: os.getenv('ZKSYNC_RPC_URL', 'https://mainnet.era.zksync.io'),
            }

            for chain_id, rpc_url in rpc_endpoints.items():
                try:
                    w3 = Web3(Web3.HTTPProvider(rpc_url, request_kwargs={'timeout': 10}))
                    if w3.is_connected():
                        self._w3[chain_id] = w3
                        print(f"   ✅ Chain {chain_id}: connected")
                    else:
                        print(f"   ⚠️  Chain {chain_id}: failed to connect")
                except Exception as e:
                    print(f"   ⚠️  Chain {chain_id}: {e}")

        print(f"   Connected to {len(self._w3)} chains")

    async def start(self):
        """Start scanning loop."""
        self.is_running = True
        self._recon_started_at = time.time()
        self._recon_phase_active = True
        print("\n🔍 Opportunity Scanner ACTIVE")
        print(f"   📡 Recon phase: accumulating targets for {self._recon_sweep_duration}s before batch execution")

        # Launch concurrent vector scanners
        tasks = [
            asyncio.create_task(self._scan_loop_position_indexer()),
            asyncio.create_task(self._scan_loop_standard_liquidations()),
            asyncio.create_task(self._scan_loop_preemptive_liquidations()),
            asyncio.create_task(self._scan_loop_cross_chain_arb()),
            asyncio.create_task(self._scan_loop_undercollateralized_pools()),
            asyncio.create_task(self._scan_loop_new_protocols()),
            asyncio.create_task(self._scan_loop_nft_loans()),
            asyncio.create_task(self._scan_loop_watchlist()),
            asyncio.create_task(self._gas_update_loop()),
            asyncio.create_task(self._heartbeat_loop()),
            asyncio.create_task(self._recon_flush_loop()),
            asyncio.create_task(self._gas_retry_loop()),
        ]

        await asyncio.gather(*tasks, return_exceptions=True)

    async def stop(self):
        self.is_running = False
        print("   🔍 Scanner stopped")

    def on_opportunity(self, callback: Callable[[OpportunitySignal], Awaitable[None]]):
        """Register callback for discovered opportunities."""
        self._callbacks.append(callback)

    async def _emit(self, signal: OpportunitySignal):
        """Emit opportunity — during recon phase, queue; after recon, emit directly."""
        self.total_opportunities_found += 1

        if self._recon_phase_active:
            # Accumulate during recon sweep instead of immediate emission
            self._recon_queue.append(signal)
            return

        # After recon phase: emit directly to execution pipeline
        await self._emit_direct(signal)

    async def _emit_direct(self, signal: OpportunitySignal):
        """Emit opportunity directly to all registered callbacks."""
        for cb in self._callbacks:
            try:
                await cb(signal)
            except Exception as e:
                print(f"   ⚠️ Callback error: {e}")

    # ──────────────────────────────────────────────
    # RECON PHASE COORDINATION
    # ──────────────────────────────────────────────

    async def _recon_flush_loop(self):
        """Monitor recon phase. Once sweep duration expires, sort and flush all queued targets."""
        while self.is_running:
            await asyncio.sleep(5)

            if not self._recon_phase_active:
                continue

            elapsed = time.time() - (self._recon_started_at or time.time())
            if elapsed >= self._recon_sweep_duration:
                await self._flush_recon_queue()
                self._recon_phase_active = False

    @staticmethod
    def _signal_profit(signal: OpportunitySignal) -> float:
        """Extract the best available profit estimate from a signal."""
        # Prefer realized net profit when available, falling back to expected value.
        net_profit = getattr(signal, "net_profit_usd", None)
        if net_profit is not None:
            return net_profit

        expected_value = getattr(signal, "expected_value_usd", None)
        if expected_value is not None:
            return expected_value

        return 0.0

    async def _flush_recon_queue(self):
        """Sort accumulated reconnaissance targets by profit priority and emit in order."""
        if not self._recon_queue:
            print("   📡 Recon sweep complete — no targets accumulated")
            return

        # Sort by expected profit descending (highest profit first)
        self._recon_queue.sort(key=self._signal_profit, reverse=True)

        count = len(self._recon_queue)
        total_profit = sum(self._signal_profit(s) for s in self._recon_queue)
        print(f"\n   🎯 RECON SWEEP COMPLETE — {count} targets identified, ${total_profit:,.2f} total potential")
        print(f"   🚀 Executing in profit-priority order (highest first)...")

        # Emit all in profit-priority order
        for signal in self._recon_queue:
            await self._emit_direct(signal)

        self._recon_queue.clear()
        print(f"   ✅ All {count} targets dispatched to execution pipeline")

    # ──────────────────────────────────────────────
    # GAS RETRY QUEUE
    # ──────────────────────────────────────────────

    def queue_gas_retry(self, signal: OpportunitySignal):
        """Add opportunity to gas retry queue when gas gate blocks it.
        When queue is full, only add if new signal is more profitable than the worst entry."""
        new_profit = signal.net_profit_usd or 0.0
        if len(self._gas_retry_queue) >= self._max_retry_queue_size:
            # Sort descending so worst entry is last
            self._gas_retry_queue.sort(key=lambda s: s.net_profit_usd or 0, reverse=True)
            worst_profit = self._gas_retry_queue[-1].net_profit_usd or 0.0
            if new_profit <= worst_profit:
                return  # New signal isn't better than worst queued entry
            self._gas_retry_queue.pop()
        self._gas_retry_queue.append(signal)

    async def _gas_retry_loop(self):
        """Periodically re-check gas-deferred opportunities."""
        while self.is_running:
            await asyncio.sleep(self._gas_retry_interval)

            if not self._gas_retry_queue:
                continue

            retryable = []
            for signal in self._gas_retry_queue:
                # Re-check if gas is now affordable.
                # GasOptimizer.estimate() expects gross profit (before gas deduction);
                # net_profit_usd already has gas subtracted, so using it would double-count.
                gross_profit_usd = signal.gross_profit_usd
                if gross_profit_usd is None:
                    # Fall back to expected value if gross profit is unavailable
                    gross_profit_usd = (
                        signal.expected_value_usd
                        if signal.expected_value_usd is not None
                        else 0.0
                    )
                gas_est = self.gas_optimizer.estimate(
                    signal.chain_id,
                    signal.metadata.get('vector', 'liquidation'),
                    gross_profit_usd,
                )
                if gas_est.is_profitable_at_current:
                    signal.estimated_cost_usd = gas_est.gas_cost_usd
                    signal.net_profit_usd = gas_est.margin_remaining_usd
                    await self._emit_direct(signal)
                else:
                    retryable.append(signal)

            retried = len(self._gas_retry_queue) - len(retryable)
            if retried > 0:
                print(f"   ⛽ Gas retry: {retried} opportunities re-dispatched")
            self._gas_retry_queue = retryable

    # ──────────────────────────────────────────────
    # EXECUTION FEEDBACK (closed-loop learning)
    # ──────────────────────────────────────────────

    def record_execution_outcome(self, vector: str, chain_id: int, protocol: str,
                                  success: bool, profit_usd: float = 0.0):
        """Record execution outcome to adjust scanner priorities.
        Called by the engine after each execution completes."""
        key = f"{vector}:{chain_id}:{protocol}"
        if key not in self._execution_feedback:
            self._execution_feedback[key] = {'successes': 0, 'failures': 0, 'total_profit': 0.0}

        fb = self._execution_feedback[key]
        if success:
            fb['successes'] += 1
            fb['total_profit'] += profit_usd
        else:
            fb['failures'] += 1

        total = fb['successes'] + fb['failures']
        fb['rate'] = fb['successes'] / total if total > 0 else 1.0

        # Update vector_stats
        if vector in self.vector_stats:
            if success:
                self.vector_stats[vector]['executed'] += 1
                self.vector_stats[vector]['profit'] += profit_usd
            else:
                self.vector_stats[vector]['failures'] = self.vector_stats[vector].get('failures', 0) + 1
            total_v = self.vector_stats[vector]['executed'] + self.vector_stats[vector].get('failures', 0)
            self.vector_stats[vector]['success_rate'] = (
                self.vector_stats[vector]['executed'] / total_v if total_v > 0 else 1.0
            )

    def get_feedback_score(self, vector: str, chain_id: int, protocol: str) -> float:
        """Get the success rate for a vector+chain+protocol combo (0.0 to 1.0).
        Returns 1.0 for unknown combos (no penalty for untested)."""
        key = f"{vector}:{chain_id}:{protocol}"
        fb = self._execution_feedback.get(key)
        if not fb:
            return 1.0
        return fb.get('rate', 1.0)

    # ──────────────────────────────────────────────
    # VECTOR 1: STANDARD LIQUIDATIONS
    # ──────────────────────────────────────────────

    async def _scan_loop_standard_liquidations(self):
        """Continuously scan for standard liquidation opportunities.
        Chunks positions (50 per cycle) to avoid RPC rate limits."""
        chunk_size = 50
        chunk_offset = 0
        backoff = 5

        while self.is_running:
            try:
                for chain_id, pool_addr in AAVE_V3_POOLS.items():
                    w3 = self._w3.get(chain_id)
                    if not w3:
                        continue

                    # Get positions for this chain
                    chain_positions = {
                        addr: data for addr, data in self._tracked_positions.items()
                        if data.get('chain_id') == chain_id or data.get('pool') == pool_addr
                    }
                    if not chain_positions:
                        continue

                    # Slice a chunk
                    all_addrs = list(chain_positions.keys())
                    start = chunk_offset % max(len(all_addrs), 1)
                    chunk_addrs = all_addrs[start:start + chunk_size]
                    chunk_dict = {addr: chain_positions[addr] for addr in chunk_addrs}

                    signals = await self._scan_aave_liquidations(w3, chain_id, pool_addr, chunk_dict)
                    for sig in signals:
                        gas_est = self.gas_optimizer.estimate(
                            chain_id, 'liquidation', sig.expected_value_usd
                        )
                        if not gas_est.is_profitable_at_current:
                            continue

                        sig.estimated_cost_usd = gas_est.gas_cost_usd
                        sig.net_profit_usd = gas_est.margin_remaining_usd
                        sig.gas_estimate = gas_est.estimated_gas_units
                        sig.gas_price_gwei = int(gas_est.total_fee_gwei)

                        route = self.flash_loan_router.find_best_route(
                            chain_id, 'USDC',
                            sig.expected_value_usd / max(sig.metadata.get('liquidation_bonus', 0.05), 0.01),
                            sig.expected_value_usd
                        )
                        if route:
                            sig.metadata['flash_provider'] = route.provider.value
                            sig.metadata['flash_fee_usd'] = route.fee_usd
                            sig.estimated_cost_usd += route.fee_usd
                            sig.net_profit_usd -= route.fee_usd

                        if sig.net_profit_usd >= self.min_profit_usd:
                            self.vector_stats['standard_liquidation']['found'] += 1
                            self.heat_map.record_observation(
                                'standard_liquidation', chain_id, 'aave_v3',
                                competition=sig.competition_estimate,
                            )
                            await self._emit(sig)

                chunk_offset += chunk_size
                # Enhancement 6: Use per-chain scan interval — L2s scan faster
                chain_interval = min(CHAIN_SCAN_INTERVALS.get(cid, 5.0)
                                     for cid in self._w3.keys()) if self._w3 else 5.0
                backoff = max(chain_interval, 1.0)
                await asyncio.sleep(backoff)

            except Exception as e:
                err_str = str(e)
                if '429' in err_str or 'Too Many Requests' in err_str:
                    backoff = min(backoff * 2, 60)
                    print(f"   ⚠️ Liquidation scan rate-limited — backing off {backoff}s")
                else:
                    print(f"   ⚠️ Standard liquidation scan error: {e}")
                    backoff = max(backoff, 10)
                await asyncio.sleep(backoff)

    async def _scan_aave_liquidations(
        self, w3: Web3, chain_id: int, pool_addr: str,
        positions_chunk: Dict[str, Dict] = None
    ) -> List[OpportunitySignal]:
        """Enhancement 7: Batch position checks via Multicall3.

        Instead of N individual RPC calls, packs getUserAccountData for all
        positions into a single Multicall3 aggregate3 call.  Falls back to
        sequential calls if Multicall3 is unavailable.
        """
        signals = []
        positions = positions_chunk if positions_chunk else self._tracked_positions
        if not positions:
            return signals

        try:
            pool = w3.eth.contract(
                address=Web3.to_checksum_address(pool_addr),
                abi=HEALTH_FACTOR_ABI,
            )

            # Build Multicall3 batch
            multicall_addr = '0xcA11bde05977b3631167028862bE2a173976CA11'
            if chain_id == 324:
                multicall_addr = '0xF9cda624FBC7e059355ce98a31693d299FACd963'

            results_map: Dict[str, tuple] = {}
            addr_list = list(positions.keys())

            try:
                # Build calldata for each position
                calls = []
                for user_addr in addr_list:
                    calldata = pool.encodeABI(
                        fn_name='getUserAccountData',
                        args=[Web3.to_checksum_address(user_addr)],
                    )
                    # encodeABI returns hex string '0x...' — convert to bytes
                    calldata_hex = calldata if isinstance(calldata, str) else calldata.hex()
                    if calldata_hex.startswith('0x'):
                        calldata_hex = calldata_hex[2:]
                    calls.append((Web3.to_checksum_address(pool_addr), True, bytes.fromhex(calldata_hex)))

                # Multicall3 aggregate3 ABI
                multicall_abi = json.loads('''[{
                    "inputs": [{"components": [
                        {"name": "target", "type": "address"},
                        {"name": "allowFailure", "type": "bool"},
                        {"name": "callData", "type": "bytes"}
                    ], "name": "calls", "type": "tuple[]"}],
                    "name": "aggregate3",
                    "outputs": [{"components": [
                        {"name": "success", "type": "bool"},
                        {"name": "returnData", "type": "bytes"}
                    ], "name": "returnData", "type": "tuple[]"}],
                    "stateMutability": "view", "type": "function"
                }]''')

                mc = w3.eth.contract(
                    address=Web3.to_checksum_address(multicall_addr),
                    abi=multicall_abi,
                )

                # Execute batch — L1 uses smaller batches, L2s can handle more
                batch_size = 200 if chain_id in (42161, 10, 8453, 137, 324) else 50
                for batch_start in range(0, len(calls), batch_size):
                    batch = calls[batch_start:batch_start + batch_size]
                    batch_addrs = addr_list[batch_start:batch_start + batch_size]
                    try:
                        raw_results = mc.functions.aggregate3(batch).call()
                        for i, (success, return_data) in enumerate(raw_results):
                            if success and len(return_data) >= 192:
                                decoded = w3.codec.decode(
                                    ['uint256', 'uint256', 'uint256', 'uint256', 'uint256', 'uint256'],
                                    return_data,
                                )
                                results_map[batch_addrs[i]] = decoded
                    except Exception:
                        # Multicall failed for this batch — fall back to sequential
                        for addr in batch_addrs:
                            try:
                                result = pool.functions.getUserAccountData(
                                    Web3.to_checksum_address(addr)
                                ).call()
                                results_map[addr] = result
                            except Exception:
                                continue

            except Exception:
                # Multicall3 not available — sequential fallback
                for user_addr in addr_list:
                    try:
                        result = pool.functions.getUserAccountData(
                            Web3.to_checksum_address(user_addr)
                        ).call()
                        results_map[user_addr] = result
                    except Exception:
                        continue

            # Process results
            for user_addr, result in results_map.items():
                pos_data = positions.get(user_addr, {})
                try:
                    total_collateral = result[0] / 1e8
                    total_debt = result[1] / 1e8
                    health_factor = result[5] / 1e18

                    if total_debt < 5:
                        continue

                    if health_factor < 1.005:
                        # === FIRE ZONE — broadcast liquidation immediately ===
                        # Only fires when HF is within 0.5% of liquidation threshold.
                        # The ZRP BlockWatcher handles the exact HF < 1.0 timing.
                        collateral_asset = pos_data.get('collateral_asset', '0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2')
                        liquidation_bonus = get_liquidation_bonus(collateral_asset)
                        close_factor = compute_optimal_close_factor(health_factor)
                        slippage_mult = get_slippage_discount(collateral_asset, total_debt)
                        gross_profit = total_debt * liquidation_bonus * close_factor * slippage_mult


                        sig = OpportunitySignal(
                            signal_id=str(uuid.uuid4()),
                            signal_type=SignalType.LIQUIDATION,
                            source_module=SignalSource.ENHANCED_DETECTOR,
                            chain_id=chain_id,
                            target_contract=pool_addr,
                            user_address=user_addr,
                            expected_value_usd=gross_profit,
                            gross_profit_usd=gross_profit,
                            confidence=0.95,
                            competition_estimate=0.4 if total_debt < 1000 else 0.6,
                            urgency_score=100,
                            execution_complexity=3,
                            health_factor=health_factor,
                            debt_amount=int(total_debt * 1e8),
                            collateral_amount=int(total_collateral * 1e8),
                            metadata={
                                'protocol': 'aave_v3',
                                'debt_asset': pos_data.get('debt_asset', '0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48'),
                                'collateral_asset': pos_data.get('collateral_asset', '0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2'),
                                'debt_amount': int(total_debt * 1e8),
                                'liquidation_bonus': liquidation_bonus,
                                'user': user_addr,
                                'is_flash_loan': True,
                                'min_collateral': 0,
                                'vector': 'standard_liquidation',
                            },
                        )
                        signals.append(sig)

                    elif health_factor < 1.50 and total_debt > 5:
                        # === NEAR-LIQUIDATABLE — add to tight watchlist ===
                        # Wider net: HF < 1.50 captures 5x more positions for ZRP monitoring
                        # The Zero-Revert Pipeline will rank them by proximity
                        watch_key = user_addr.lower()
                        if watch_key not in self._watchlist:
                            self._watchlist[watch_key] = {
                                'chain_id': chain_id,
                                'pool': pool_addr,
                                'debt_asset': pos_data.get('debt_asset', '0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48'),
                                'collateral_asset': pos_data.get('collateral_asset', '0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2'),
                                'last_hf': health_factor,
                                'debt_usd': total_debt,
                                'added_at': time.time(),
                            }
                            if health_factor < 1.20:
                                print(f"   👁️  WATCHLIST +1: HF={health_factor:.4f} debt=${total_debt:,.0f} chain={chain_id} ({user_addr[:12]}...)")


                    elif health_factor < 1.25 and total_debt > 50:
                        # AT RISK — record for heat map
                        self.heat_map.record_observation(
                            'preemptive_liquidation', chain_id, 'aave_v3',
                            competition=0.3,
                        )

                except Exception:
                    continue

        except Exception:
            pass

        return signals

    # ──────────────────────────────────────────────
    # VECTOR 2: PREEMPTIVE LIQUIDATIONS (Oracle Sniffing)
    # ──────────────────────────────────────────────

    async def _scan_loop_preemptive_liquidations(self):
        """Monitor oracle updates to predict liquidations."""
        backoff = 12  # Start at 12s (once per block)

        oracle_abi = json.loads('''[{
            "inputs": [],
            "name": "latestRoundData",
            "outputs": [
                {"name": "roundId", "type": "uint80"},
                {"name": "answer", "type": "int256"},
                {"name": "startedAt", "type": "uint256"},
                {"name": "updatedAt", "type": "uint256"},
                {"name": "answeredInRound", "type": "uint80"}
            ],
            "stateMutability": "view",
            "type": "function"
        }]''')

        while self.is_running:
            try:
                w3 = self._w3.get(1)  # Ethereum mainnet
                if not w3:
                    await asyncio.sleep(30)
                    continue

                # Check latest block for oracle update events
                latest = w3.eth.block_number
                for asset_pair, oracle_addr in CHAINLINK_ORACLES.get(1, {}).items():
                    try:
                        oracle = w3.eth.contract(
                            address=Web3.to_checksum_address(oracle_addr),
                            abi=oracle_abi,
                        )
                        round_data = oracle.functions.latestRoundData().call()
                        current_price = round_data[1] / 1e8

                        # Enhancement 5: Oracle staleness guard — reject stale/zero prices
                        updated_at = round_data[3]
                        heartbeat = ORACLE_HEARTBEAT.get(asset_pair, 3600)
                        now_ts = int(time.time())
                        if current_price <= 0 or (now_ts - updated_at) > heartbeat * 1.5:
                            continue  # Stale or zero price — skip

                        prev_price = self._oracle_prices.get(asset_pair, current_price)
                        price_change_pct = abs(current_price - prev_price) / max(prev_price, 0.01)
                        self._oracle_prices[asset_pair] = current_price

                        # Significant price movement → preemptive liquidation opportunity
                        if price_change_pct > 0.001:  # >0.1% move — very aggressive
                            estimated_positions = max(1, int(price_change_pct * 200))
                            avg_profit = 50 + (price_change_pct * 10000)

                            sig = OpportunitySignal(
                                signal_id=str(uuid.uuid4()),
                                signal_type=SignalType.ORACLE_UPDATE,
                                source_module=SignalSource.MEMPOOL_RADAR,
                                chain_id=1,
                                expected_value_usd=avg_profit,
                                gross_profit_usd=avg_profit,
                                confidence=min(0.9, price_change_pct * 10),
                                competition_estimate=0.3,
                                urgency_score=90,
                                latency_requirement_ms=500,
                                metadata={
                                    'vector': 'preemptive_liquidation',
                                    'oracle': oracle_addr,
                                    'asset_pair': asset_pair,
                                    'price_change_pct': price_change_pct,
                                    'current_price': current_price,
                                    'prev_price': prev_price,
                                    'estimated_positions': estimated_positions,
                                },
                            )

                            gas_est = self.gas_optimizer.estimate(1, 'flash_loan_liquidation', avg_profit)
                            if gas_est.is_profitable_at_current:
                                sig.estimated_cost_usd = gas_est.gas_cost_usd
                                sig.net_profit_usd = gas_est.margin_remaining_usd
                                self.vector_stats['preemptive_liquidation']['found'] += 1
                                self.heat_map.record_observation(
                                    'preemptive_liquidation', 1, 'chainlink',
                                    competition=0.3,
                                )
                                await self._emit(sig)

                    except Exception:
                        continue

                # Success — reset backoff to normal block interval
                backoff = 12
                await asyncio.sleep(backoff)

            except Exception as e:
                err_str = str(e)
                if '429' in err_str or 'Too Many Requests' in err_str:
                    backoff = min(backoff * 2, 120)  # Exponential backoff, max 2 min
                    print(f"   ⚠️ Oracle scan rate-limited — backing off {backoff}s")
                else:
                    print(f"   ⚠️ Preemptive scan error: {e}")
                    backoff = max(backoff, 15)
                await asyncio.sleep(backoff)

    # ──────────────────────────────────────────────
    # VECTOR 3: NEW PROTOCOL LAUNCHES
    # ──────────────────────────────────────────────

    async def _scan_loop_new_protocols(self):
        """Detect new protocol deployments with zero competition windows."""
        # Only scan highest-activity chains to save RPC budget
        scan_chains = [1, 8453]

        while self.is_running:
            try:
                for chain_id in scan_chains:
                    w3 = self._w3.get(chain_id)
                    if not w3:
                        continue
                    try:
                        latest = w3.eth.get_block('latest', full_transactions=True)
                        for tx in latest.get('transactions', []):
                            # Contract creation = to is None
                            if tx.get('to') is None and tx.get('input', '0x') != '0x':
                                # New contract deployment
                                receipt = None
                                try:
                                    receipt = w3.eth.get_transaction_receipt(tx['hash'])
                                except Exception:
                                    continue

                                if receipt and receipt.get('contractAddress'):
                                    contract_addr = receipt['contractAddress']
                                    code = w3.eth.get_code(Web3.to_checksum_address(contract_addr))

                                    if len(code) > 100:  # Non-trivial contract
                                        # Check for lending/DEX patterns in bytecode
                                        code_hex = code.hex()
                                        is_lending = any(sig in code_hex for sig in [
                                            'e8eda9df',  # liquidationCall
                                            '69328dec',  # withdraw
                                            'a415bcad',  # borrow
                                        ])
                                        is_dex = any(sig in code_hex for sig in [
                                            '128acb08',  # swap (UniV3)
                                            '022c0d9f',  # swap (UniV2)
                                            '38ed1739',  # swapExactTokensForTokens
                                        ])

                                        if is_lending or is_dex:
                                            estimated_profit = 1000 if is_lending else 500

                                            sig = OpportunitySignal(
                                                signal_id=str(uuid.uuid4()),
                                                signal_type=SignalType.NEW_PROTOCOL,
                                                source_module=SignalSource.CONTRACT_CRAWLER,
                                                chain_id=chain_id,
                                                target_contract=contract_addr,
                                                expected_value_usd=estimated_profit,
                                                gross_profit_usd=estimated_profit,
                                                confidence=0.6,
                                                competition_estimate=0.05,  # Near zero
                                                urgency_score=70,
                                                metadata={
                                                    'vector': 'new_protocol',
                                                    'deployer': tx.get('from', ''),
                                                    'is_lending': is_lending,
                                                    'is_dex': is_dex,
                                                    'code_size': len(code),
                                                    'block': latest['number'],
                                                },
                                            )
                                            self.vector_stats['new_protocol']['found'] += 1
                                            self.heat_map.record_observation(
                                                'new_protocol', chain_id, 'discovery',
                                                competition=0.05,
                                            )
                                            await self._emit(sig)

                    except Exception:
                        continue

                await asyncio.sleep(30)  # Every 2-3 blocks

            except Exception as e:
                print(f"   ⚠️ New protocol scan error: {e}")
                await asyncio.sleep(30)

    # ──────────────────────────────────────────────
    # VECTOR 4: CROSS-CHAIN ARBITRAGE
    # ──────────────────────────────────────────────

    async def _scan_loop_cross_chain_arb(self):
        """Detect price discrepancies across chains for the same asset."""
        # Token addresses per chain (USDC example)
        usdc_addresses = {
            1: '0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48',
            42161: '0xaf88d065e77c8cC2239327C5EDb3A432268e5831',
            10: '0x0b2C639c533813f4Aa9D7837CAf62653d097Ff85',
            137: '0x3c499c542cEF5E3811e1192ce70d8cC03d5c3359',
            8453: '0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913',
        }

        # Per-chain Chainlink ETH/USD feed addresses for real price divergence
        eth_usd_feeds = {
            1:     '0x5f4eC3Df9cbd43714FE2740f5E3616155c5b8419',
            42161: '0x639Fe6ab55C921f74e7fac1ee960C0B6293ba612',
            10:    '0x13e3Ee699D1909E989722E753853AE30b17e08c5',
            137:   '0xF9680D99D6C9589e2a93a78A04A279e509205945',
            8453:  '0x71041dddad3595F9CEd3DcCFBe3D1F4b0a16Bb70',
            43114: '0x976B3D034E162d8bD72D6b9C989d545b839003b0',
        }

        oracle_abi_simple = json.loads('''[{
            "inputs": [],
            "name": "latestRoundData",
            "outputs": [
                {"name": "roundId", "type": "uint80"},
                {"name": "answer", "type": "int256"},
                {"name": "startedAt", "type": "uint256"},
                {"name": "updatedAt", "type": "uint256"},
                {"name": "answeredInRound", "type": "uint80"}
            ],
            "stateMutability": "view",
            "type": "function"
        }]''')

        while self.is_running:
            try:
                # Enhancement 10: Read actual per-chain oracle prices for real divergence
                prices: Dict[int, float] = {}

                for chain_id, w3 in self._w3.items():
                    feed_addr = eth_usd_feeds.get(chain_id)
                    if not feed_addr:
                        continue
                    try:
                        oracle = w3.eth.contract(
                            address=Web3.to_checksum_address(feed_addr),
                            abi=oracle_abi_simple,
                        )
                        round_data = oracle.functions.latestRoundData().call()
                        price = round_data[1] / 1e8
                        updated_at = round_data[3]
                        # Staleness guard — use ETH/USD heartbeat (3600s) × 1.5
                        heartbeat = ORACLE_HEARTBEAT.get('ETH/USD', 3600)
                        if price > 0 and (int(time.time()) - updated_at) < heartbeat * 1.5:
                            prices[chain_id] = price
                    except Exception:
                        # Fallback to cached oracle price
                        cached = self._oracle_prices.get('ETH/USD')
                        if cached and cached > 0:
                            prices[chain_id] = cached

                # Find arbitrage between chain pairs
                chain_ids = list(prices.keys())
                for i in range(len(chain_ids)):
                    for j in range(i + 1, len(chain_ids)):
                        c1, c2 = chain_ids[i], chain_ids[j]
                        p1, p2 = prices[c1], prices[c2]
                        spread = abs(p1 - p2) / min(p1, p2)

                        if spread > 0.002:  # >0.2% spread
                            buy_chain = c1 if p1 < p2 else c2
                            sell_chain = c2 if p1 < p2 else c1
                            trade_size = 50_000  # $50k notional
                            gross = trade_size * spread

                            sig = OpportunitySignal(
                                signal_id=str(uuid.uuid4()),
                                signal_type=SignalType.CROSS_CHAIN_ARB,
                                source_module=SignalSource.CROSS_CHAIN_MONITOR,
                                chain_id=buy_chain,
                                expected_value_usd=gross,
                                gross_profit_usd=gross,
                                confidence=0.7,
                                competition_estimate=0.6,
                                urgency_score=50,
                                metadata={
                                    'vector': 'cross_chain_arb',
                                    'buy_chain': buy_chain,
                                    'sell_chain': sell_chain,
                                    'spread_pct': spread,
                                    'buy_price': min(p1, p2),
                                    'sell_price': max(p1, p2),
                                    'trade_size': trade_size,
                                },
                            )

                            # Gas check on both chains
                            gas_buy = self.gas_optimizer.estimate(buy_chain, 'cross_chain_bridge', gross)
                            gas_sell = self.gas_optimizer.estimate(sell_chain, 'cross_chain_execute', gross)
                            total_gas = gas_buy.gas_cost_usd + gas_sell.gas_cost_usd

                            if gross - total_gas >= self.min_profit_usd:
                                sig.estimated_cost_usd = total_gas
                                sig.net_profit_usd = gross - total_gas
                                self.vector_stats['cross_chain_arb']['found'] += 1
                                self.heat_map.record_observation(
                                    'cross_chain_arb', buy_chain, 'bridge',
                                    competition=0.6,
                                )
                                await self._emit(sig)

                await asyncio.sleep(5)

            except Exception as e:
                print(f"   ⚠️ Cross-chain arb scan error: {e}")
                await asyncio.sleep(10)

    # ──────────────────────────────────────────────
    # VECTOR 5: UNDERCOLLATERALIZED POOLS
    # ──────────────────────────────────────────────

    async def _scan_loop_undercollateralized_pools(self):
        """Enhancement 2: Compound V3 per-borrower liquidation scanning.

        Previous version only checked pool-level utilization and never emitted
        actionable signals.  Now: when utilization > 75%, scan Withdraw events
        to discover borrowers and check each via ``isLiquidatable()``.
        Liquidatable borrowers are emitted as real execution signals.
        """

        # Compound V3 Comet ABI fragments
        compound_abi = json.loads('''[
            {"inputs":[],"name":"getUtilization","outputs":[{"name":"","type":"uint256"}],"stateMutability":"view","type":"function"},
            {"inputs":[{"name":"account","type":"address"}],"name":"isLiquidatable","outputs":[{"name":"","type":"bool"}],"stateMutability":"view","type":"function"},
            {"inputs":[{"name":"account","type":"address"}],"name":"borrowBalanceOf","outputs":[{"name":"","type":"uint256"}],"stateMutability":"view","type":"function"}
        ]''')

        # Compound V3 Withdraw event topic for borrower discovery
        withdraw_topic = Web3.keccak(
            text='Withdraw(address,address,uint256)'
        ).hex()

        # Track discovered borrowers per market
        compound_borrowers: Dict[str, set] = {}

        while self.is_running:
            try:
                for chain_id, markets in COMPOUND_V3_MARKETS.items():
                    w3 = self._w3.get(chain_id)
                    if not w3:
                        continue

                    for market_addr in markets:
                        try:
                            market = w3.eth.contract(
                                address=Web3.to_checksum_address(market_addr),
                                abi=compound_abi,
                            )

                            utilization = market.functions.getUtilization().call() / 1e18

                            if utilization > 0.75:
                                # Discover borrowers from recent Withdraw events
                                market_key = f"{chain_id}:{market_addr}"
                                if market_key not in compound_borrowers:
                                    compound_borrowers[market_key] = set()
                                    try:
                                        current_block = w3.eth.block_number
                                        # Chain-specific lookback: ~6h worth of blocks
                                        # L2s: ~0.25-2s blocks ≈ 10000-80000 blocks/6h
                                        # L1: ~12s blocks ≈ 1800 blocks/6h
                                        blocks_per_6h = {1: 1800, 42161: 80000, 10: 10800, 137: 10800, 8453: 10800}
                                        lookback = blocks_per_6h.get(chain_id, 5000)
                                        from_block = max(0, current_block - lookback)
                                        logs = w3.eth.get_logs({
                                            'address': Web3.to_checksum_address(market_addr),
                                            'topics': [withdraw_topic],
                                            'fromBlock': from_block,
                                            'toBlock': current_block,
                                        })
                                        for log in logs:
                                            if len(log['topics']) >= 2:
                                                borrower = '0x' + log['topics'][1].hex()[-40:]
                                                compound_borrowers[market_key].add(borrower.lower())
                                    except Exception:
                                        pass

                                # Check each known borrower for liquidatability
                                for borrower_addr in list(compound_borrowers.get(market_key, set())):
                                    try:
                                        is_liq = market.functions.isLiquidatable(
                                            Web3.to_checksum_address(borrower_addr)
                                        ).call()
                                        if not is_liq:
                                            continue

                                        debt = market.functions.borrowBalanceOf(
                                            Web3.to_checksum_address(borrower_addr)
                                        ).call() / 1e6  # USDC = 6 decimals

                                        if debt < 10:
                                            continue

                                        # Compound V3 liquidation bonus is ~5% on average
                                        gross_profit = debt * 0.05

                                        gas_est = self.gas_optimizer.estimate(
                                            chain_id, 'liquidation', gross_profit
                                        )
                                        if not gas_est.is_profitable_at_current:
                                            continue

                                        sig = OpportunitySignal(
                                            signal_id=str(uuid.uuid4()),
                                            signal_type=SignalType.LIQUIDATION,
                                            source_module=SignalSource.STATIC_ANALYZER,
                                            chain_id=chain_id,
                                            target_contract=market_addr,
                                            user_address=borrower_addr,
                                            expected_value_usd=gross_profit,
                                            gross_profit_usd=gross_profit,
                                            confidence=0.85,
                                            competition_estimate=0.3,
                                            urgency_score=80,
                                            execution_complexity=3,
                                            estimated_cost_usd=gas_est.gas_cost_usd,
                                            net_profit_usd=gas_est.margin_remaining_usd,
                                            metadata={
                                                'vector': 'undercollateralized_pool',
                                                'protocol': 'compound_v3',
                                                'utilization': utilization,
                                                'market': market_addr,
                                                'user': borrower_addr,
                                                'debt_usd': debt,
                                            },
                                        )
                                        self.vector_stats['undercollateralized_pool']['found'] += 1
                                        self.heat_map.record_observation(
                                            'undercollateralized_pool', chain_id, 'compound_v3',
                                            competition=0.3,
                                        )
                                        await self._emit(sig)
                                    except Exception:
                                        continue
                            else:
                                # Low utilization — still record for heat map
                                self.heat_map.record_observation(
                                    'undercollateralized_pool', chain_id, 'compound_v3',
                                    competition=0.2,
                                )

                        except Exception:
                            continue

                await asyncio.sleep(15)

            except Exception as e:
                print(f"   ⚠️ Undercollateralized pool scan error: {e}")
                await asyncio.sleep(10)

    # ──────────────────────────────────────────────
    # VECTOR 6: NFT-BACKED LOANS
    # ──────────────────────────────────────────────

    async def _scan_loop_nft_loans(self):
        """Scan NFT lending protocols for liquidatable positions."""
        while self.is_running:
            try:
                w3 = self._w3.get(1)  # NFT lending is mainly on Ethereum
                if not w3:
                    await asyncio.sleep(30)
                    continue

                for protocol_name, contract_addr in NFT_LENDING_PROTOCOLS.get(1, {}).items():
                    try:
                        # Check for recent liquidation events (signal of active market)
                        # Each protocol has different interfaces; this is a generalized scanner

                        # Check contract activity (proxy for opportunity density)
                        code = w3.eth.get_code(Web3.to_checksum_address(contract_addr))
                        if len(code) < 100:
                            continue

                        # Record observation for heat map even if we can't execute yet
                        self.vector_stats['nft_backed_loan']['found'] += 1
                        self.heat_map.record_observation(
                            'nft_backed_loan', 1, protocol_name.lower(),
                            competition=0.1,  # Extremely low competition
                        )

                    except Exception:
                        continue

                await asyncio.sleep(30)  # NFT loans are slower frequency

            except Exception as e:
                print(f"   ⚠️ NFT loan scan error: {e}")
                await asyncio.sleep(30)

    # ──────────────────────────────────────────────
    # WATCHLIST — Fast-poll near-liquidatable positions
    # ──────────────────────────────────────────────

    async def _scan_loop_watchlist(self):
        """
        Enhancement 11: Graduated polling — positions closer to liquidation
        are polled more frequently, saving RPC budget for safe positions.
        HF < 1.02: every 1s (critical), HF 1.02-1.10: every 5s, HF 1.10-1.50: every 15s.
        """
        print("   👁️  Watchlist scanner starting (graduated polling)...")
        backoff = 3
        # Track last-poll time per position for graduated frequency
        _last_poll: Dict[str, float] = {}

        while self.is_running:
            try:
                if not self._watchlist:
                    await asyncio.sleep(5)
                    continue

                now = time.time()
                stale_keys = []
                for user_addr, watch_data in list(self._watchlist.items()):
                    # Enhancement 11: Skip positions that were polled too recently
                    last_hf = watch_data.get('last_hf', 1.5)
                    if last_hf < 1.02:
                        poll_interval = 1.0   # Critical — poll every second
                    elif last_hf < 1.10:
                        poll_interval = 5.0   # Near-risk — poll every 5s
                    else:
                        poll_interval = 15.0  # Safe-ish — poll every 15s

                    last_polled = _last_poll.get(user_addr, 0.0)
                    if now - last_polled < poll_interval:
                        continue
                    _last_poll[user_addr] = now

                    chain_id = watch_data['chain_id']
                    pool_addr = watch_data['pool']
                    w3 = self._w3.get(chain_id)
                    if not w3:
                        continue

                    try:
                        pool = w3.eth.contract(
                            address=Web3.to_checksum_address(pool_addr),
                            abi=HEALTH_FACTOR_ABI,
                        )
                        result = pool.functions.getUserAccountData(
                            Web3.to_checksum_address(user_addr)
                        ).call()

                        total_collateral = result[0] / 1e8
                        total_debt = result[1] / 1e8
                        health_factor = result[5] / 1e18

                        # Update last known HF
                        watch_data['last_hf'] = health_factor

                        if health_factor < 1.005 and total_debt > 5:
                            # === FIRE ZONE — broadcast liquidation ===
                            # Only fires when HF < 1.005 (near-certainty of on-chain success)
                            collateral_asset = watch_data.get('collateral_asset', '0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2')
                            liquidation_bonus = get_liquidation_bonus(collateral_asset)
                            close_factor = compute_optimal_close_factor(health_factor)
                            slippage_mult = get_slippage_discount(collateral_asset, total_debt)
                            gross_profit = total_debt * liquidation_bonus * close_factor * slippage_mult

                            print(f"   🔥 WATCHLIST HIT! HF={health_factor:.4f} debt=${total_debt:,.0f} chain={chain_id} ({user_addr[:12]}...)")

                            sig = OpportunitySignal(
                                signal_id=str(uuid.uuid4()),
                                signal_type=SignalType.LIQUIDATION,
                                source_module=SignalSource.ENHANCED_DETECTOR,
                                chain_id=chain_id,
                                target_contract=pool_addr,
                                user_address=user_addr,
                                expected_value_usd=gross_profit,
                                gross_profit_usd=gross_profit,
                                confidence=min(0.95, 1.0 / max(health_factor, 0.01)),
                                competition_estimate=0.3,
                                urgency_score=99,
                                execution_complexity=3,
                                health_factor=health_factor,
                                debt_amount=int(total_debt * 1e8),
                                collateral_amount=int(total_collateral * 1e8),
                                metadata={
                                    'protocol': 'aave_v3',
                                    'debt_asset': watch_data.get('debt_asset', '0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48'),
                                    'collateral_asset': watch_data.get('collateral_asset', '0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2'),
                                    'debt_amount': int(total_debt * 1e8),
                                    'liquidation_bonus': liquidation_bonus,
                                    'user': user_addr,
                                    'is_flash_loan': True,
                                    'min_collateral': 0,
                                    'vector': 'standard_liquidation',
                                },
                            )

                            gas_est = self.gas_optimizer.estimate(
                                chain_id, 'flash_loan_liquidation', gross_profit
                            )
                            if gas_est.is_profitable_at_current:
                                sig.estimated_cost_usd = gas_est.gas_cost_usd
                                sig.net_profit_usd = gas_est.margin_remaining_usd
                                sig.gas_estimate = gas_est.estimated_gas_units
                                sig.gas_price_gwei = int(gas_est.total_fee_gwei)
                                self.vector_stats['standard_liquidation']['found'] += 1
                                await self._emit(sig)

                            stale_keys.append(user_addr)

                        elif health_factor > 1.60:
                            # Position recovered — remove from watchlist
                            stale_keys.append(user_addr)

                        # Expire entries older than 6 hours
                        elif time.time() - watch_data.get('added_at', 0) > 21600:
                            stale_keys.append(user_addr)

                    except Exception:
                        continue

                # Clean up
                for key in stale_keys:
                    self._watchlist.pop(key, None)
                    _last_poll.pop(key, None)

                # Graduated polling: loop every 1s, per-position pacing is above
                backoff = 1
                await asyncio.sleep(backoff)

            except Exception as e:
                err_str = str(e)
                if '429' in err_str or 'Too Many Requests' in err_str:
                    backoff = min(backoff * 2, 60)
                    print(f"   ⚠️ Watchlist rate-limited — backing off {backoff}s")
                else:
                    print(f"   ⚠️ Watchlist scan error: {e}")
                    backoff = max(backoff, 5)
                await asyncio.sleep(backoff)

    # ──────────────────────────────────────────────
    # GAS UPDATE LOOP
    # ──────────────────────────────────────────────

    async def _gas_update_loop(self):
        """Periodically update gas prices on all chains."""
        while self.is_running:
            try:
                for chain_id, w3 in self._w3.items():
                    await self.gas_optimizer.update_gas(chain_id, w3)
                await asyncio.sleep(12)  # Every block
            except Exception:
                await asyncio.sleep(5)

    # ──────────────────────────────────────────────
    # POSITION INDEXER (auto-discover borrowers)
    # ──────────────────────────────────────────────

    BORROW_EVENT_ABI = json.loads('''[{
        "anonymous": false,
        "inputs": [
            {"indexed": true, "name": "reserve", "type": "address"},
            {"indexed": false, "name": "user", "type": "address"},
            {"indexed": true, "name": "onBehalfOf", "type": "address"},
            {"indexed": false, "name": "amount", "type": "uint256"},
            {"indexed": false, "name": "interestRateMode", "type": "uint8"},
            {"indexed": false, "name": "borrowRate", "type": "uint256"},
            {"indexed": true, "name": "referralCode", "type": "uint16"}
        ],
        "name": "Borrow",
        "type": "event"
    }]''')

    # Pre-computed Borrow event topic for raw getLogs
    _BORROW_TOPIC = Web3.keccak(
        text='Borrow(address,address,address,uint256,uint8,uint256,uint16)'
    ).hex()

    async def _scan_loop_position_indexer(self):
        """
        Discover active borrowers by scanning recent Borrow events on Aave V3.
        Uses raw eth_getLogs for maximum RPC compatibility.
        Populates _tracked_positions so the liquidation scanner has targets.
        """
        print("   📇 Position indexer starting...")

        while self.is_running:
            try:
                for chain_id, pool_addr in AAVE_V3_POOLS.items():
                    w3 = self._w3.get(chain_id)
                    if not w3:
                        continue

                    try:
                        current_block = w3.eth.block_number
                        # Scan last 100000 blocks (~2.3d on Base, ~13.8d on Mainnet)
                        from_block = max(0, current_block - 100000)

                        # Use raw getLogs for maximum Alchemy/RPC compatibility
                        logs = w3.eth.get_logs({
                            'address': Web3.to_checksum_address(pool_addr),
                            'topics': [self._BORROW_TOPIC],
                            'fromBlock': from_block,
                            'toBlock': current_block,
                        })

                        new_count = 0
                        for log in logs:
                            if len(log['topics']) >= 3:
                                # topic[1] = reserve (indexed), topic[2] = onBehalfOf (indexed)
                                reserve = '0x' + log['topics'][1].hex()[-40:]
                                borrower = '0x' + log['topics'][2].hex()[-40:]
                                borrower_cs = Web3.to_checksum_address(borrower)

                                if borrower_cs.lower() not in self._tracked_positions:
                                    self._tracked_positions[borrower_cs.lower()] = {
                                        'chain_id': chain_id,
                                        'debt_asset': Web3.to_checksum_address(reserve),
                                        'collateral_asset': '0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2',
                                        'pool': pool_addr,
                                        'indexed_at': time.time(),
                                        'indexed_block': current_block,
                                    }
                                    new_count += 1

                        if new_count > 0:
                            print(f"   📇 Chain {chain_id}: +{new_count} borrowers from {len(logs)} events "
                                  f"(total tracked: {len(self._tracked_positions)})")

                    except Exception as e:
                        # Log but continue — some RPCs may not support log queries
                        pass

                # Re-index every 60 seconds
                await asyncio.sleep(60)

            except Exception as e:
                print(f"   ⚠️ Position indexer error: {e}")
                await asyncio.sleep(30)

    # ──────────────────────────────────────────────
    # HEARTBEAT (scan visibility)
    # ──────────────────────────────────────────────

    async def _heartbeat_loop(self):
        """Print scan heartbeat every 15 seconds for visibility."""
        scan_count = 0
        while self.is_running:
            await asyncio.sleep(15)
            scan_count += 1
            self.total_scans += 1
            gas_1 = self.gas_optimizer.chain_states.get(1)
            gas_str = f"{gas_1.current_base_fee:.4f} gwei" if gas_1 else "N/A"
            recon_str = ""
            if self._recon_phase_active:
                queued = len(self._recon_queue)
                elapsed = time.time() - (self._recon_started_at or time.time())
                remaining = max(0, self._recon_sweep_duration - elapsed)
                recon_str = f" | 📡 RECON: {queued} queued, {remaining:.0f}s left"
            retry_str = f" | ⛽ retry_q: {len(self._gas_retry_queue)}" if self._gas_retry_queue else ""
            print(f"   ⏱️  Scan #{scan_count} | "
                  f"Positions: {len(self._tracked_positions)} | "
                  f"Watchlist: {len(self._watchlist)} | "
                  f"Opps found: {self.total_opportunities_found} | "
                  f"ETH gas: {gas_str} | "
                  f"Vectors: {sum(1 for v in self.vector_stats.values() if v['found'] > 0)}/6 active"
                  f"{recon_str}{retry_str}")

    # ──────────────────────────────────────────────
    # POSITION MANAGEMENT
    # ──────────────────────────────────────────────

    def add_tracked_position(self, user_addr: str, position_data: Dict):
        """Add a position to monitor for liquidation."""
        self._tracked_positions[user_addr.lower()] = position_data

    def add_tracked_positions_batch(self, positions: Dict[str, Dict]):
        """Add multiple positions."""
        for addr, data in positions.items():
            self._tracked_positions[addr.lower()] = data

    # ──────────────────────────────────────────────
    # STATUS
    # ──────────────────────────────────────────────

    def status_report(self) -> Dict[str, Any]:
        return {
            'is_running': self.is_running,
            'connected_chains': list(self._w3.keys()),
            'tracked_positions': len(self._tracked_positions),
            'total_opportunities_found': self.total_opportunities_found,
            'total_scans': self.total_scans,
            'vector_stats': self.vector_stats,
            'oracle_prices': dict(self._oracle_prices),
            'gas_status': self.gas_optimizer.chain_status(),
            'recon_phase_active': self._recon_phase_active,
            'recon_queue_size': len(self._recon_queue),
            'gas_retry_queue_size': len(self._gas_retry_queue),
            'execution_feedback_keys': len(self._execution_feedback),
        }
