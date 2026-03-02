#!/usr/bin/env python3
"""
Advanced Filter
Filters mempool transactions for liquidation triggers:
- Oracle updates
- Large swaps
- Flash loan borrows
- Liquidation calls
"""

import asyncio
import time
from typing import Dict, List, Optional, Set, Callable, Awaitable, Any
from dataclasses import dataclass
from eth_abi import decode
from web3 import Web3

from ..data_lake.data_models import (
    MempoolTransaction, LargeSwap, OracleUpdate,
    OpportunitySignal, SignalType, SignalSource, ChainId
)
from .signal_merger import MergedTransaction


@dataclass
class FilterConfig:
    """Configuration for advanced filter"""
    large_swap_threshold_usd: float = 100000  # $100k
    flash_loan_threshold_usd: float = 500000  # $500k
    tracked_oracles: List[str] = None
    tracked_dexes: List[str] = None
    tracked_lending: List[str] = None
    
    def __post_init__(self):
        if self.tracked_oracles is None:
            self.tracked_oracles = [
                "0x5f4eC3Df9cbd43714FE2740f5E3616155c5b8419",  # ETH/USD Chainlink
                "0xA14d53bC1F1c0A31A4cC8532e2C571233BC04472",  # WBTC/USD
                "0x510440f43A0201e7CaD53984175406f42e69F738",  # USDC/USD
            ]
        
        if self.tracked_dexes is None:
            self.tracked_dexes = [
                "0xE592427A0AEce92De3Edee1F18E0157C05861564",  # Uniswap V3 Router
                "0x7a250d5630B4cF539739dF2C5dAcb4c659F2488D",  # Uniswap V2 Router
                "0x8e764bE4288B842791989DB5b8ec06727903744F",  # Curve
                "0xBA12222222228d8Ba445958a75a0704d566BF2C8",  # Balancer
            ]
        
        if self.tracked_lending is None:
            self.tracked_lending = [
                "0x87870Bca3F3fD6335C3F4ce8392D69350B4fA4E2",  # Aave V3 Pool
                "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2",  # WETH
            ]


# Event topic hashes
ORACLE_UPDATE_TOPIC = Web3.keccak(text="AnswerUpdated(int256,indexed uint256,uint256,indexed address)").hex()
SWAP_TOPIC = Web3.keccak(text="Swap(address,address,int256,int256,uint160,uint128,int24)").hex()
FLASH_LOAN_TOPIC = Web3.keccak(text="FlashLoan(address,address,uint256,uint256)").hex()
LIQUIDATION_TOPIC = Web3.keccak(text="LiquidationCall(address,address,address,uint256,uint256)").hex()


class AdvancedFilter:
    """
    Advanced mempool filter for liquidation triggers
    Analyzes transactions for:
    - Oracle price updates
    - Large DEX swaps
    - Flash loan borrows
    - Liquidation calls
    """
    
    def __init__(self, config: FilterConfig = None):
        self.config = config or FilterConfig()
        self.w3 = Web3(Web3.HTTPProvider("https://eth.llamarpc.com"))  # Public RPC
        
        # Callbacks
        self._oracle_callbacks: List[Callable[[OracleUpdate], Awaitable[None]]] = []
        self._swap_callbacks: List[Callable[[LargeSwap], Awaitable[None]]] = []
        self._signal_callbacks: List[Callable[[OpportunitySignal], Awaitable[None]]] = []
        
        # Tracking
        self._recent_oracles: Dict[str, OracleUpdate] = {}
        self._recent_swaps: List[LargeSwap] = []
        
        # Statistics
        self.transactions_analyzed = 0
        self.oracle_updates_detected = 0
        self.large_swaps_detected = 0
        self.flash_loans_detected = 0
        self.liquidations_detected = 0
    
    async def analyze_transaction(self, tx: MempoolTransaction):
        """Analyze single transaction for triggers"""
        self.transactions_analyzed += 1
        
        # Check if transaction interacts with tracked contracts
        if tx.to_address:
            to_lower = tx.to_address.lower()
            
            # Check oracle interactions
            if to_lower in [addr.lower() for addr in self.config.tracked_oracles]:
                await self._check_oracle_update(tx)
            
            # Check DEX interactions
            if to_lower in [addr.lower() for addr in self.config.tracked_dexes]:
                await self._check_large_swap(tx)
            
            # Check lending protocol interactions
            if to_lower in [addr.lower() for addr in self.config.tracked_lending]:
                await self._check_flash_loan(tx)
                await self._check_liquidation(tx)
        
        # Decode input data for additional patterns
        if tx.input_data and len(tx.input_data) > 10:
            await self._decode_input_data(tx)
    
    async def analyze_merged_transaction(self, merged: MergedTransaction):
        """Analyze merged transaction with confidence scoring"""
        tx = merged.transaction
        
        # Use confidence score from merger
        if merged.confidence_score > 0.7:
            await self.analyze_transaction(tx)
    
    async def _check_oracle_update(self, tx: MempoolTransaction):
        """Check if transaction is oracle update"""
        try:
            # Decode function call
            if len(tx.input_data) >= 10:
                function_selector = tx.input_data[:10]
                
                # Common oracle update functions
                update_selectors = [
                    "0x515f074f",  # submit(uint256) - Chainlink
                    "0x80e634d8",  # updateAnswer()
                ]
                
                if function_selector in update_selectors:
                    self.oracle_updates_detected += 1
                    
                    update = OracleUpdate(
                        aggregator=tx.to_address,
                        asset=self._get_asset_from_oracle(tx.to_address),
                        price=0,  # Would need to decode from tx
                        round_id=0,
                        updated_at=int(time.time()),
                        block_number=tx.block_number or 0,
                        tx_hash=tx.hash,
                        price_usd=0.0
                    )
                    
                    self._recent_oracles[tx.to_address] = update
                    
                    # Emit to callbacks
                    await self._emit_oracle_update(update)
                    
                    # Create opportunity signal
                    await self._create_oracle_signal(tx, update)
                    
        except Exception as e:
            pass
    
    async def _check_large_swap(self, tx: MempoolTransaction):
        """Check if transaction is large swap"""
        try:
            # Estimate swap size from value
            value_usd = (tx.value / 1e18) * 2000  # Rough ETH price estimate
            
            # Also check input data for token amounts
            if tx.input_data and len(tx.input_data) > 138:
                # Uniswap V3 exactInputSingle function
                # function exactInputSingle((address,address,uint24,address,uint256,uint256,uint160))
                try:
                    amount_in = int.from_bytes(
                        bytes.fromhex(tx.input_data[138:202]),
                        'big'
                    )
                    if amount_in > 0:
                        value_usd = max(value_usd, amount_in / 1e6 * 1)  # USDC estimate
                except:
                    pass
            
            if value_usd >= self.config.large_swap_threshold_usd:
                self.large_swaps_detected += 1
                
                swap = LargeSwap(
                    dex=self._get_dex_name(tx.to_address),
                    token_in='',
                    token_out='',
                    amount_in=tx.value,
                    amount_out=0,
                    sender=tx.from_address,
                    receiver=tx.to_address or '',
                    tx_hash=tx.hash,
                    block_number=tx.block_number or 0,
                    price_impact_estimate=self._estimate_price_impact(value_usd),
                    value_usd=value_usd
                )
                
                self._recent_swaps.append(swap)
                
                # Keep only recent swaps
                if len(self._recent_swaps) > 100:
                    self._recent_swaps = self._recent_swaps[-100:]
                
                await self._emit_swap(swap)
                await self._create_swap_signal(tx, swap)
                
        except Exception as e:
            pass
    
    async def _check_flash_loan(self, tx: MempoolTransaction):
        """Check if transaction is flash loan"""
        try:
            # Flash loan function selectors
            flash_selectors = [
                "0x16ea91e7",  # flashLoan(address,address,uint256,bytes)
                "0x4303a8a7",  # flashLoanSimple(address,address,uint256,bytes,uint256)
                "0x6d77a6a6",  # flashLoan(address,address[],uint256[],uint256[],address,bytes,uint256)
            ]
            
            if len(tx.input_data) >= 10:
                selector = tx.input_data[:10]
                
                if selector in flash_selectors:
                    self.flash_loans_detected += 1
                    
                    # Decode flash loan amount
                    try:
                        amount = int.from_bytes(
                            bytes.fromhex(tx.input_data[138:202]),
                            'big'
                        )
                        value_usd = amount / 1e18 * 2000
                        
                        if value_usd >= self.config.flash_loan_threshold_usd:
                            # Large flash loan - potential MEV
                            pass
                    except:
                        pass
                        
        except Exception as e:
            pass
    
    async def _check_liquidation(self, tx: MempoolTransaction):
        """Check if transaction is liquidation call"""
        try:
            # Liquidation function selectors
            liq_selectors = [
                "0x41013712",  # liquidationCall(address,address,address,uint256,bool)
                "0xe78d0f49",  # liquidate(address,address,uint256)
            ]
            
            if len(tx.input_data) >= 10:
                selector = tx.input_data[:10]
                
                if selector in liq_selectors:
                    self.liquidations_detected += 1
                    # Someone else is liquidating - might be opportunity to backrun
                    
        except Exception as e:
            pass
    
    async def _decode_input_data(self, tx: MempoolTransaction):
        """Decode input data for additional patterns"""
        try:
            if len(tx.input_data) < 10:
                return
            
            selector = tx.input_data[:10]
            
            # Known MEV-related function selectors
            mev_selectors = {
                "0x38ed1739": "swapExactTokensForTokens",  # Uniswap V2
                "0x7ff36ab5": "swapExactETHForTokens",
                "0xfb3bdb41": "swapETHForExactTokens",
                "0xac9650d8": "multicall",  # Uniswap V3 multicall
                "0x5ae401dc": "sweep",  # Uniswap V3
            }
            
            if selector in mev_selectors:
                # This is a DEX swap function
                pass
                
        except Exception as e:
            pass
    
    def _get_asset_from_oracle(self, oracle_address: str) -> str:
        """Get asset symbol from oracle address"""
        oracle_assets = {
            "0x5f4eC3Df9cbd43714FE2740f5E3616155c5b8419": "ETH",
            "0xA14d53bC1F1c0A31A4cC8532e2C571233BC04472": "WBTC",
            "0x510440f43A0201e7CaD53984175406f42e69F738": "USDC",
        }
        return oracle_assets.get(oracle_address, "UNKNOWN")
    
    def _get_dex_name(self, dex_address: str) -> str:
        """Get DEX name from address"""
        dex_names = {
            "0xE592427A0AEce92De3Edee1F18E0157C05861564": "uniswap_v3",
            "0x7a250d5630B4cF539739dF2C5dAcb4c659F2488D": "uniswap_v2",
            "0x8e764bE4288B842791989DB5b8ec06727903744F": "curve",
            "0xBA12222222228d8Ba445958a75a0704d566BF2C8": "balancer",
        }
        return dex_names.get(dex_address.lower(), "unknown")
    
    def _estimate_price_impact(self, value_usd: float) -> float:
        """Estimate price impact from swap size"""
        # Simplified model - real implementation would use pool reserves
        if value_usd < 100000:
            return 0.001  # 0.1%
        elif value_usd < 500000:
            return 0.005  # 0.5%
        elif value_usd < 1000000:
            return 0.01  # 1%
        elif value_usd < 5000000:
            return 0.02  # 2%
        else:
            return 0.05  # 5%
    
    def on_oracle_update(self, callback: Callable[[OracleUpdate], Awaitable[None]]):
        """Register oracle update callback"""
        self._oracle_callbacks.append(callback)
    
    def on_swap(self, callback: Callable[[LargeSwap], Awaitable[None]]):
        """Register swap callback"""
        self._swap_callbacks.append(callback)
    
    def on_signal(self, callback: Callable[[OpportunitySignal], Awaitable[None]]):
        """Register opportunity signal callback"""
        self._signal_callbacks.append(callback)
    
    async def _emit_oracle_update(self, update: OracleUpdate):
        """Emit oracle update to callbacks"""
        for callback in self._oracle_callbacks:
            try:
                await callback(update)
            except Exception as e:
                print(f"   ⚠️  Oracle callback error: {e}")
    
    async def _emit_swap(self, swap: LargeSwap):
        """Emit swap to callbacks"""
        for callback in self._swap_callbacks:
            try:
                await callback(swap)
            except Exception as e:
                print(f"   ⚠️  Swap callback error: {e}")
    
    async def _create_oracle_signal(self, tx: MempoolTransaction, update: OracleUpdate):
        """Create opportunity signal from oracle update"""
        signal = OpportunitySignal(
            signal_type=SignalType.ORACLE_UPDATE,
            source_module=SignalSource.MEMPOOL_RADAR,
            chain_id=ChainId.ETHEREUM.value,
            target_contract=update.aggregator,
            trigger_tx_hash=tx.hash,
            trigger_tx_data=tx,
            expected_value_usd=0,  # Would calculate based on affected positions
            confidence=0.8,
            urgency_score=90,
            execution_complexity=3,
            gas_estimate=300000,
            gas_price_gwei=int(tx.gas_price / 1e9),
            latency_requirement_ms=100,
            expiry_block=(tx.block_number or 0) + 2,
            metadata={
                'oracle': update.aggregator,
                'asset': update.asset,
            }
        )
        
        for callback in self._signal_callbacks:
            try:
                await callback(signal)
            except Exception as e:
                print(f"   ⚠️  Signal callback error: {e}")
    
    async def _create_swap_signal(self, tx: MempoolTransaction, swap: LargeSwap):
        """Create opportunity signal from large swap"""
        signal = OpportunitySignal(
            signal_type=SignalType.LARGE_SWAP,
            source_module=SignalSource.MEMPOOL_RADAR,
            chain_id=ChainId.ETHEREUM.value,
            target_contract=tx.to_address,
            trigger_tx_hash=tx.hash,
            trigger_tx_data=tx,
            expected_value_usd=swap.value_usd * swap.price_impact_estimate * 0.5,  # Backrun profit estimate
            confidence=0.7,
            urgency_score=70,
            execution_complexity=4,
            gas_estimate=400000,
            gas_price_gwei=int(tx.gas_price / 1e9),
            latency_requirement_ms=200,
            expiry_block=(tx.block_number or 0) + 1,
            metadata={
                'dex': swap.dex,
                'value_usd': swap.value_usd,
                'price_impact': swap.price_impact_estimate,
            }
        )
        
        for callback in self._signal_callbacks:
            try:
                await callback(signal)
            except Exception as e:
                print(f"   ⚠️  Signal callback error: {e}")
    
    def get_stats(self) -> Dict:
        """Get filter statistics"""
        return {
            'transactions_analyzed': self.transactions_analyzed,
            'oracle_updates_detected': self.oracle_updates_detected,
            'large_swaps_detected': self.large_swaps_detected,
            'flash_loans_detected': self.flash_loans_detected,
            'liquidations_detected': self.liquidations_detected,
        }
