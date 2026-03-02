#!/usr/bin/env python3
"""
UNIFIED EXECUTION BRIDGE
Connects opportunity signals to smart contract execution

This is the production-ready bridge that:
1. Receives OpportunitySignal from Omni-Channel system
2. Converts signal to ExecutionRequest with proper calldata
3. Executes via deployed smart contracts (LiquidationExecutor.sol, FlashLoanArbitrageExecutor.sol)
4. Monitors transaction and reports profit

Usage:
    python unified_execution_bridge.py
"""

import asyncio
import os
import sys
from typing import Dict, Any, Optional
from dotenv import load_dotenv

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from omni_channel.data_lake.data_models import OpportunitySignal, SignalType, ExecutionModule
from omni_channel.execution_router.execution_interface import ExecutionRequest, ExecutionType
from omni_channel.execution_router.liquidation_executor import LiquidationExecutor
from omni_channel.execution_router.execution_manager import ExecutionManager, create_execution_request

# Load environment
load_dotenv()


class UnifiedExecutionBridge:
    """
    Production bridge connecting opportunity signals to smart contract execution
    
    Signal Flow:
    1. OpportunitySignal detected (from Mempool Radar, Contract Crawler, etc.)
    2. Convert to ExecutionRequest with proper calldata encoding
    3. Execute via LiquidationExecutor or FlashLoanArbitrageExecutor contract
    4. Monitor transaction and collect profit
    """
    
    def __init__(self, config: Dict[str, Any] = None):
        self.config = config or {}
        self.execution_manager: Optional[ExecutionManager] = None
        self.liquidation_executor: Optional[LiquidationExecutor] = None
        
        # Configuration from environment
        self.private_key = os.getenv('PRIVATE_KEY')
        self.treasury_address = os.getenv('TREASURY_ADDRESS', '0xB323C6E32C6efe28FB9cfB1A83F4071c544eA1e4')
        self.min_profit_usd = float(os.getenv('MIN_PROFIT_USD', '50'))
        
        print("\n🔗 UNIFIED EXECUTION BRIDGE INITIALIZED")
        print("=" * 60)
        print(f"   Treasury: {self.treasury_address}")
        print(f"   Min Profit: ${self.min_profit_usd}")
        print(f"   Private Key Set: {'✅ Yes' if self.private_key else '❌ No'}")
        print("=" * 60)
    
    async def initialize(self):
        """Initialize execution components"""
        print("\n🔧 Initializing execution bridge...")
        
        # Initialize liquidation executor
        self.liquidation_executor = LiquidationExecutor({
            'liquidation': {
                'min_profit_usd': self.min_profit_usd,
            }
        })
        await self.liquidation_executor.initialize()
        
        # Initialize execution manager
        self.execution_manager = ExecutionManager({
            'liquidation': {},
            'arbitrage': {},
            'backrun': {},
            'cross_chain': {},
        })
        await self.execution_manager.start()
        
        print("   ✅ Execution bridge ready")
    
    async def shutdown(self):
        """Shutdown execution bridge"""
        print("\n🛑 Shutting down execution bridge...")
        
        if self.execution_manager:
            await self.execution_manager.stop()
        
        if self.liquidation_executor:
            await self.liquidation_executor.shutdown()
        
        print("   ✅ Execution bridge stopped")
    
    async def process_signal(self, signal: OpportunitySignal) -> Dict[str, Any]:
        """
        Process opportunity signal and execute if profitable
        
        Args:
            signal: OpportunitySignal from Omni-Channel system
            
        Returns:
            Execution result dictionary
        """
        print(f"\n🎯 Processing signal: {signal.signal_type.value}")
        print(f"   Expected Value: ${signal.expected_value_usd}")
        print(f"   Confidence: {signal.confidence:.2%}")
        
        # Check minimum profit threshold
        if signal.expected_value_usd < self.min_profit_usd:
            print(f"   ⚠️  Skipping: Below min profit (${signal.expected_value_usd} < ${self.min_profit_usd})")
            return {'status': 'skipped', 'reason': 'below_min_profit'}
        
        # Check confidence threshold
        if signal.confidence < 0.5:
            print(f"   ⚠️  Skipping: Low confidence ({signal.confidence:.2%} < 50%)")
            return {'status': 'skipped', 'reason': 'low_confidence'}
        
        # Convert signal to execution request
        execution_request = self._convert_signal_to_request(signal)
        
        # Validate request
        if not await self.liquidation_executor.validate_request(execution_request):
            print(f"   ⚠️  Invalid execution request")
            return {'status': 'failed', 'reason': 'invalid_request'}
        
        # Execute
        print(f"   🚀 Executing...")
        result = await self.liquidation_executor.execute(execution_request)
        
        # Report result
        if result.status.value == 'confirmed':
            print(f"   ✅ EXECUTION SUCCESSFUL")
            print(f"      TX Hash: {result.tx_hash}")
            print(f"      Block: {result.block_number}")
            print(f"      Profit: ${result.profit_usd}")
            return {
                'status': 'success',
                'tx_hash': result.tx_hash,
                'block': result.block_number,
                'profit_usd': result.profit_usd,
            }
        else:
            print(f"   ❌ EXECUTION FAILED: {result.error_message}")
            return {
                'status': 'failed',
                'reason': result.error_message,
            }
    
    def _convert_signal_to_request(self, signal: OpportunitySignal) -> ExecutionRequest:
        """
        Convert OpportunitySignal to ExecutionRequest with proper calldata
        
        This is the critical bridge function that encodes smart contract calls
        """
        from omni_channel.execution_router.execution_interface import ExecutionType
        
        # Determine execution type from signal
        exec_type_map = {
            SignalType.LIQUIDATION: ExecutionType.LIQUIDATION,
            SignalType.ARBITRAGE: ExecutionType.ARBITRAGE,
            SignalType.BACK_RUN: ExecutionType.BACKRUN,
            SignalType.CROSS_CHAIN_ARB: ExecutionType.CROSS_CHAIN,
        }
        exec_type = exec_type_map.get(signal.signal_type, ExecutionType.CUSTOM)
        
        # Build calldata based on signal type
        calldata = '0x'
        if signal.signal_type == SignalType.LIQUIDATION:
            # Build liquidation calldata
            calldata = self.liquidation_executor.build_liquidation_calldata(
                protocol=signal.metadata.get('protocol', 'aave_v3'),
                debt_asset=signal.metadata.get('debt_asset', '0x' + '0' * 40),
                debt_amount=signal.metadata.get('debt_amount', 0),
                user=signal.metadata.get('user', '0x' + '0' * 40),
                collateral_asset=signal.metadata.get('collateral_asset', '0x' + '0' * 40),
                min_collateral=signal.metadata.get('min_collateral', 0),
            )
        elif signal.signal_type == SignalType.ARBITRAGE:
            # Check if Reserve Protocol arbitrage
            if signal.metadata.get('protocol') == 'ReserveProtocol':
                calldata = self.liquidation_executor.build_reserve_arbitrage_calldata(
                    rToken=signal.metadata.get('rToken', '0x' + '0' * 40),
                    flash_loan_amount=signal.metadata.get('flash_loan_amount', 0),
                )
        
        # Create execution request
        request = ExecutionRequest(
            request_id=signal.signal_id,
            execution_type=exec_type,
            chain_id=signal.chain_id,
            opportunity_data={
                'signal_type': signal.signal_type.value,
                'expected_value_usd': signal.expected_value_usd,
                'confidence': signal.confidence,
            },
            target_contract=self.liquidation_executor.liquidation_executor_v1,
            calldata=calldata,
            value=0,  # No ETH sent with transaction
            gas_limit=signal.gas_estimate if hasattr(signal, 'gas_estimate') else 500000,
            gas_price=int(signal.gas_price_gwei * 1e9) if hasattr(signal, 'gas_price_gwei') else 30e9,
            deadline=int(asyncio.get_event_loop().time()) + 300,  # 5 minute timeout
            metadata={
                'expected_profit_usd': signal.expected_value_usd,
                'source': signal.source_module.value,
                'debt_asset': signal.metadata.get('debt_asset'),
                'debt_amount': signal.metadata.get('debt_amount'),
                'collateral_asset': signal.metadata.get('collateral_asset'),
                'user': signal.metadata.get('user'),
                'min_collateral': signal.metadata.get('min_collateral', 0),
                'protocol': signal.metadata.get('protocol'),
                'is_flash_loan': signal.metadata.get('is_flash_loan', True),
            }
        )
        
        return request


async def main():
    """
    Main entry point - demonstrates execution bridge usage
    """
    print("\n" + "=" * 60)
    print("  UNIFIED EXECUTION BRIDGE - DEMONSTRATION")
    print("=" * 60)
    
    # Initialize bridge
    bridge = UnifiedExecutionBridge()
    
    try:
        await bridge.initialize()
        
        # Create test signal (in production, this comes from Omni-Channel)
        test_signal = OpportunitySignal(
            signal_id="test_liquidation_001",
            signal_type=SignalType.LIQUIDATION,
            source_module=ExecutionModule.LIQUIDATION_ENGINE,
            chain_id=1,
            target_contract="0x87870Bca3F3fD6335C3F4ce8392D69350B4fA4E2",  # Aave V3
            expected_value_usd=500.0,
            confidence=0.85,
            urgency_score=80,
            execution_complexity=3,
            gas_estimate=500000,
            gas_price_gwei=30,
            latency_requirement_ms=100,
            expiry_block=20000000 + 2,
            metadata={
                'protocol': 'aave_v3',
                'user': '0x' + '1' * 40,
                'debt_asset': '0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48',  # USDC
                'debt_amount': 10000 * 10**6,  # 10,000 USDC
                'collateral_asset': '0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2',  # WETH
                'min_collateral': 0,
                'is_flash_loan': True,
            }
        )
        
        print("\n📥 Received test signal:")
        print(f"   Type: {test_signal.signal_type.value}")
        print(f"   Expected Profit: ${test_signal.expected_value_usd}")
        print(f"   Target: Aave V3 liquidation")
        
        # Check if we should actually execute
        if not os.getenv('PRIVATE_KEY'):
            print("\n⚠️  PRIVATE_KEY not set - Simulation mode only")
            print("   To enable real execution, set PRIVATE_KEY in .env file")
            print("\n   In simulation mode, the bridge will:")
            print("   1. ✅ Build proper calldata")
            print("   2. ✅ Validate execution request")
            print("   3. ❌ Skip actual transaction (no private key)")
        
        # Process signal
        result = await bridge.process_signal(test_signal)
        
        print("\n" + "=" * 60)
        print("  EXECUTION RESULT")
        print("=" * 60)
        for key, value in result.items():
            print(f"   {key}: {value}")
        print("=" * 60)
        
    except KeyboardInterrupt:
        print("\n\n🛑 Received interrupt")
    except Exception as e:
        print(f"\n❌ Error: {e}")
        import traceback
        traceback.print_exc()
    finally:
        await bridge.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
