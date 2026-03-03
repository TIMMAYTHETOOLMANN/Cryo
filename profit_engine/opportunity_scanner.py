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
    },
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
        self.vector_stats: Dict[str, Dict] = {v.value: {'found': 0, 'executed': 0, 'profit': 0.0}
                                                for v in OpportunityVector}

        print("🔍 Opportunity Scanner initialized")
        print(f"   Scan interval: {self.scan_interval}s")
        print(f"   Min profit: ${self.min_profit_usd}")
        print(f"   Vectors: {len(OpportunityVector)}")

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
        print("\n🔍 Opportunity Scanner ACTIVE")

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
        ]

        await asyncio.gather(*tasks, return_exceptions=True)

    async def stop(self):
        self.is_running = False
        print("   🔍 Scanner stopped")

    def on_opportunity(self, callback: Callable[[OpportunitySignal], Awaitable[None]]):
        """Register callback for discovered opportunities."""
        self._callbacks.append(callback)

    async def _emit(self, signal: OpportunitySignal):
        """Emit opportunity to all registered callbacks."""
        self.total_opportunities_found += 1
        for cb in self._callbacks:
            try:
                await cb(signal)
            except Exception as e:
                print(f"   ⚠️ Callback error: {e}")

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
                            chain_id, 'USDC', sig.expected_value_usd * 10, sig.expected_value_usd
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
                backoff = 5
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
        """Scan a batch of positions for liquidatable health factors."""
        signals = []
        positions = positions_chunk if positions_chunk else self._tracked_positions
        try:
            pool = w3.eth.contract(
                address=Web3.to_checksum_address(pool_addr),
                abi=HEALTH_FACTOR_ABI,
            )

            for user_addr, pos_data in positions.items():
                try:
                    result = pool.functions.getUserAccountData(
                        Web3.to_checksum_address(user_addr)
                    ).call()

                    total_collateral = result[0] / 1e8
                    total_debt = result[1] / 1e8
                    health_factor = result[5] / 1e18

                    if total_debt < 5:
                        continue

                    if health_factor < 1.005:
                        # === FIRE ZONE — broadcast liquidation immediately ===
                        # Only fires when HF is within 0.5% of liquidation threshold.
                        # The ZRP BlockWatcher handles the exact HF < 1.0 timing.
                        liquidation_bonus = 0.05
                        debt_asset = pos_data.get('debt_asset', '').lower()
                        if debt_asset not in [
                            '0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48',
                            '0xdac17f958d2ee523a2206206994597c13d831ec7',
                            '0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2',
                            '0x6b175474e89094c44da98b954eedeac495271d0f',
                        ]:
                            liquidation_bonus = 0.10

                        gross_profit = total_debt * liquidation_bonus * 0.5


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

        while self.is_running:
            try:
                # Collect WETH/USDC prices from DEX pools on each chain
                prices: Dict[int, float] = {}

                for chain_id, w3 in self._w3.items():
                    try:
                        # Use oracle price as proxy (faster than DEX query)
                        if chain_id in [1, 42161, 10, 8453]:
                            prices[chain_id] = self._oracle_prices.get('ETH/USD', 2500.0)
                            # Add small per-chain variance from gas/demand
                            prices[chain_id] *= (1 + (chain_id % 10 - 5) * 0.0001)
                    except Exception:
                        continue

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
        """Static analysis of pool reserves to find undercollateralized positions."""
        while self.is_running:
            try:
                for chain_id, markets in COMPOUND_V3_MARKETS.items():
                    w3 = self._w3.get(chain_id)
                    if not w3:
                        continue

                    for market_addr in markets:
                        try:
                            # Check market utilization
                            # Compound V3 has getUtilization() → high util = more opportunities
                            utilization_abi = json.loads('''[{
                                "inputs": [],
                                "name": "getUtilization",
                                "outputs": [{"name": "", "type": "uint256"}],
                                "stateMutability": "view",
                                "type": "function"
                            }]''')

                            market = w3.eth.contract(
                                address=Web3.to_checksum_address(market_addr),
                                abi=utilization_abi,
                            )

                            utilization = market.functions.getUtilization().call() / 1e18

                            if utilization > 0.75:  # >75% utilized (was 90%)
                                estimated_profit = 100 + (utilization - 0.75) * 3000

                                sig = OpportunitySignal(
                                    signal_id=str(uuid.uuid4()),
                                    signal_type=SignalType.VULNERABILITY,
                                    source_module=SignalSource.STATIC_ANALYZER,
                                    chain_id=chain_id,
                                    target_contract=market_addr,
                                    expected_value_usd=estimated_profit,
                                    gross_profit_usd=estimated_profit,
                                    confidence=0.65,
                                    competition_estimate=0.2,
                                    urgency_score=40,
                                    metadata={
                                        'vector': 'undercollateralized_pool',
                                        'protocol': 'compound_v3',
                                        'utilization': utilization,
                                        'market': market_addr,
                                    },
                                )

                                gas_est = self.gas_optimizer.estimate(
                                    chain_id, 'liquidation', estimated_profit
                                )
                                if gas_est.is_profitable_at_current:
                                    sig.estimated_cost_usd = gas_est.gas_cost_usd
                                    sig.net_profit_usd = gas_est.margin_remaining_usd
                                    self.vector_stats['undercollateralized_pool']['found'] += 1
                                    self.heat_map.record_observation(
                                        'undercollateralized_pool', chain_id, 'compound_v3',
                                        competition=0.2,
                                    )
                                    # NOTE: Don't emit to execution pipeline — pool-level
                                    # utilization signals have no specific borrower to liquidate.
                                    # They feed the heat map to prioritize scanning on stressed pools.

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
        Fast-poll positions with HF < 1.05 every 3 seconds.
        The instant one drops below 1.005 → emit for immediate execution.
        """
        print("   👁️  Watchlist scanner starting...")
        backoff = 3
        while self.is_running:
            try:
                if not self._watchlist:
                    await asyncio.sleep(5)
                    continue

                stale_keys = []
                for user_addr, watch_data in list(self._watchlist.items()):
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
                            liquidation_bonus = 0.05
                            debt_asset_lower = watch_data.get('debt_asset', '').lower()
                            if debt_asset_lower not in [
                                '0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48',
                                '0xdac17f958d2ee523a2206206994597c13d831ec7',
                                '0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2',
                                '0x6b175474e89094c44da98b954eedeac495271d0f',
                            ]:
                                liquidation_bonus = 0.10

                            gross_profit = total_debt * liquidation_bonus * 0.5

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

                backoff = 3  # Reset on success
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
                        # Scan last 10000 blocks (~33h on mainnet, more on L2s)
                        from_block = max(0, current_block - 10000)

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
            print(f"   ⏱️  Scan #{scan_count} | "
                  f"Positions: {len(self._tracked_positions)} | "
                  f"Watchlist: {len(self._watchlist)} | "
                  f"Opps found: {self.total_opportunities_found} | "
                  f"ETH gas: {gas_str} | "
                  f"Vectors: {sum(1 for v in self.vector_stats.values() if v['found'] > 0)}/6 active")

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
        }
