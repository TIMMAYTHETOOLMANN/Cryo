#!/usr/bin/env python3
"""
Backrun Executor
Execute backrun opportunities after large swaps

Features:
- Detect large swaps in mempool
- Execute immediately after target transaction
- Capture price impact profits
"""

import asyncio
import time
from typing import Dict, List, Optional, Any
from web3 import Web3
import os

from .execution_interface import (
    ExecutionInterface, ExecutionRequest, ExecutionResult,
    ExecutionStatus, ExecutionType
)


class BackrunExecutor(ExecutionInterface):
    """Execute backrun opportunities"""

    def __init__(self, config: Dict[str, Any] = None):
        super().__init__(config)
        self._w3_providers: Dict[int, Web3] = {}
        self._pending_backruns: Dict[str, ExecutionRequest] = {}
        self.private_key = os.getenv('PRIVATE_KEY')

    @property
    def name(self) -> str:
        return "BackrunExecutor"

    @property
    def execution_type(self) -> ExecutionType:
        return ExecutionType.BACKRUN

    async def initialize(self):
        """Initialize backrun executor"""
        print("\n🏃 Initializing Backrun Executor...")
        self.is_running = True
        await self._init_providers()
        self._register_dexes()
        print("   ✅ Backrun Executor initialized")

    async def shutdown(self):
        """Shutdown executor"""
        self.is_running = False
        print("   🏃 Backrun Executor shutdown complete")

    async def _init_providers(self):
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
                self._w3_providers[chain_id] = Web3(Web3.HTTPProvider(rpc_url))
            except Exception as e:
                print(f"   ⚠️  RPC init error for chain {chain_id}: {e}")

    def _register_dexes(self):
        """Register DEX router addresses"""
        self._dex_routers = {
            'uniswap_v3': '0xE592427A0AEce92De3Edee1F18E0157C05861564',
            'uniswap_v2': '0x7a250d5630B4cF539739dF2C5dAcb4c659F2488D',
            'sushiswap': '0xd9e1cE17f2641f24aE83637ab66a2cca9C378B9F',
        }

    async def validate_request(self, request: ExecutionRequest) -> bool:
        """Validate backrun request"""
        if request.execution_type != ExecutionType.BACKRUN:
            return False

        if request.chain_id not in self._w3_providers:
            return False

        # Check target transaction
        target_tx = request.metadata.get('target_tx')
        if not target_tx:
            return False

        return True

    async def simulate(self, request: ExecutionRequest) -> Dict[str, Any]:
        """Simulate backrun execution"""
        try:
            w3 = self._w3_providers.get(request.chain_id)
            if not w3:
                return {'success': False, 'error': 'No provider'}

            tx_params = {
                'from': w3.eth.default_account or '0x' + '0' * 40,
                'to': request.target_contract,
                'data': request.calldata,
                'value': request.value,
            }

            try:
                gas_estimate = w3.eth.estimate_gas(tx_params)
                return {
                    'success': True,
                    'gas_estimate': int(gas_estimate * 1.1),  # Lower buffer for backrun
                }
            except Exception as e:
                return {'success': False, 'error': f'Gas estimation failed: {e}'}

        except Exception as e:
            return {'success': False, 'error': str(e)}

    async def execute(self, request: ExecutionRequest) -> ExecutionResult:
        """Execute backrun - optimized for speed"""
        start_time = time.time()

        result = ExecutionResult(
            request_id=request.request_id,
            status=ExecutionStatus.PENDING,
            executed_at=int(time.time())
        )

        try:
            if not await self.validate_request(request):
                result.status = ExecutionStatus.FAILED
                result.error_message = "Invalid backrun request"
                return result

            w3 = self._w3_providers[request.chain_id]
            result.status = ExecutionStatus.SIMULATING

            # Quick simulation
            sim_result = await self.simulate(request)
            result.simulation_result = sim_result

            if not sim_result.get('success'):
                result.status = ExecutionStatus.FAILED
                result.error_message = f"Simulation failed: {sim_result.get('error')}"
                return result

            result.status = ExecutionStatus.SUBMITTING

            # Build transaction with priority gas
            account = w3.eth.account.from_key(self.private_key)
            tx_params = {
                'from': account.address,
                'to': request.target_contract,
                'data': request.calldata,
                'value': request.value,
                'gas': request.gas_limit or sim_result.get('gas_estimate', 300000),
            }

            # Priority gas pricing for backrun
            base_gas = w3.eth.gas_price
            priority_multiplier = request.metadata.get('priority_multiplier', 1.5)
            tx_params['gasPrice'] = int(base_gas * priority_multiplier)

            if request.nonce is not None:
                tx_params['nonce'] = request.nonce

            # Send transaction
            try:
                signed_tx = w3.eth.account.sign_transaction(tx_params, self.private_key)
                tx_hash = await asyncio.to_thread(w3.eth.send_raw_transaction, signed_tx.raw_transaction)
                result.tx_hash = tx_hash.hex()
                result.status = ExecutionStatus.SUBMITTED
            except Exception as e:
                result.status = ExecutionStatus.FAILED
                result.error_message = f"Transaction failed: {e}"
                return result

            # Wait for confirmation
            result.status = ExecutionStatus.CONFIRMING
            receipt = await asyncio.to_thread(w3.eth.wait_for_transaction_receipt, tx_hash, timeout=60)

            result.block_number = receipt['blockNumber']
            result.gas_used = receipt['gasUsed']
            result.effective_gas_price = receipt.get('effectiveGasPrice', 0)

            if receipt['status'] == 1:
                result.status = ExecutionStatus.CONFIRMED
                result.confirmed_at = int(time.time())
                result.profit_usd = request.metadata.get('expected_profit_usd', 0)
            else:
                result.status = ExecutionStatus.REVERTED
                result.error_message = "Transaction reverted"

        except Exception as e:
            result.status = ExecutionStatus.FAILED
            result.error_message = str(e)

        execution_time_ms = int((time.time() - start_time) * 1000)
        self._update_stats(result, execution_time_ms)

        return result

    async def cancel(self, request_id: str) -> bool:
        """Cancel pending backrun"""
        if request_id in self._pending_backruns:
            del self._pending_backruns[request_id]
            return True
        return False

    def get_stats(self) -> Dict:
        """Get executor statistics"""
        base_stats = super().get_stats()
        return {
            **base_stats.__dict__,
            'pending_backruns': len(self._pending_backruns),
        }
