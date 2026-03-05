#!/usr/bin/env python3
"""
Cross-Chain Executor
Execute cross-chain arbitrage opportunities

Features:
- Bridge integration
- Multi-chain transaction coordination
- Atomic execution where possible
"""

import asyncio
import time
from typing import Dict, List, Optional, Any
from dataclasses import dataclass
from web3 import Web3
import os

from .execution_interface import (
    ExecutionInterface, ExecutionRequest, ExecutionResult,
    ExecutionStatus, ExecutionType
)


@dataclass
class ChainExecution:
    """Execution on a single chain"""
    chain_id: int
    tx_hash: str
    status: ExecutionStatus
    block_number: Optional[int] = None
    confirmed_at: int = 0


class CrossChainExecutor(ExecutionInterface):
    """Execute cross-chain opportunities"""

    def __init__(self, config: Dict[str, Any] = None):
        super().__init__(config)
        self._w3_providers: Dict[int, Web3] = {}
        self._pending_executions: Dict[str, List[ChainExecution]] = {}
        self.private_key = os.getenv('PRIVATE_KEY')

    @property
    def name(self) -> str:
        return "CrossChainExecutor"

    @property
    def execution_type(self) -> ExecutionType:
        return ExecutionType.CROSS_CHAIN

    async def initialize(self):
        """Initialize cross-chain executor"""
        print("\n🌉 Initializing Cross-Chain Executor...")
        self.is_running = True
        await self._init_providers()
        self._register_bridges()
        print("   ✅ Cross-Chain Executor initialized")

    async def shutdown(self):
        """Shutdown executor"""
        self.is_running = False
        print("   🌉 Cross-Chain Executor shutdown complete")

    async def _init_providers(self):
        """Initialize Web3 providers"""
        rpc_endpoints = {
            1: os.getenv('ETH_RPC_URL', 'https://eth.llamarpc.com'),
            42161: os.getenv('ARBITRUM_RPC_URL', 'https://arb1.arbitrum.io/rpc'),
            10: os.getenv('OPTIMISM_RPC_URL', 'https://mainnet.optimism.io'),
            137: os.getenv('POLYGON_RPC_URL', 'https://polygon-rpc.com'),
            8453: os.getenv('BASE_RPC_URL', 'https://mainnet.base.org'),
            43114: os.getenv('AVALANCHE_RPC_URL', 'https://api.avax.network/ext/bc/C/rpc'),
        }

        for chain_id, rpc_url in rpc_endpoints.items():
            try:
                self._w3_providers[chain_id] = Web3(Web3.HTTPProvider(rpc_url))
            except Exception as e:
                print(f"   ⚠️  RPC init error for chain {chain_id}: {e}")

    def _register_bridges(self):
        """Register bridge contracts"""
        self._bridge_contracts = {
            'stargate': {
                'ethereum': '0x8731d54E9D02c286767d56ac032803d4529e69ED',
                'arbitrum': '0x53Bf833A5d6c4ddA888F69c22C88C9f356a41614',
                'optimism': '0xB0D502E938ed5f4df2E681fE6E419ff29631d62b',
            },
            'hop': {
                'ethereum': '0x3666f603Cc164936C1b87e207F36BEbA4AC5f182',
                'arbitrum': '0x53Bf833A5d6c4ddA888F69c22C88C9f356a41614',
            },
        }

    async def validate_request(self, request: ExecutionRequest) -> bool:
        """Validate cross-chain request"""
        if request.execution_type != ExecutionType.CROSS_CHAIN:
            return False

        chains = request.metadata.get('chains', [])
        if len(chains) < 2:
            return False

        for chain_id in chains:
            if chain_id not in self._w3_providers:
                return False

        return True

    async def simulate(self, request: ExecutionRequest) -> Dict[str, Any]:
        """Simulate cross-chain execution"""
        try:
            chains = request.metadata.get('chains', [])
            results = {}

            for chain_id in chains:
                w3 = self._w3_providers.get(chain_id)
                if not w3:
                    results[chain_id] = {'success': False, 'error': 'No provider'}
                    continue

                # Simulate on each chain
                tx_params = {
                    'from': w3.eth.default_account or '0x' + '0' * 40,
                    'to': request.target_contract,
                    'data': request.calldata,
                    'value': request.value,
                }

                try:
                    gas_estimate = w3.eth.estimate_gas(tx_params)
                    results[chain_id] = {
                        'success': True,
                        'gas_estimate': int(gas_estimate * 1.2),
                    }
                except Exception as e:
                    results[chain_id] = {'success': False, 'error': str(e)}

            return {
                'success': all(r.get('success') for r in results.values()),
                'chain_results': results,
            }

        except Exception as e:
            return {'success': False, 'error': str(e)}

    async def execute(self, request: ExecutionRequest) -> ExecutionResult:
        """Execute cross-chain arbitrage"""
        start_time = time.time()

        result = ExecutionResult(
            request_id=request.request_id,
            status=ExecutionStatus.PENDING,
            executed_at=int(time.time())
        )

        try:
            if not await self.validate_request(request):
                result.status = ExecutionStatus.FAILED
                result.error_message = "Invalid cross-chain request"
                return result

            chains = request.metadata.get('chains', [])
            chain_executions: List[ChainExecution] = []

            # Execute on each chain
            for i, chain_id in enumerate(chains):
                w3 = self._w3_providers.get(chain_id)
                if not w3:
                    continue

                # Add delay between chains if needed
                if i > 0:
                    await asyncio.sleep(1)

                # Build transaction for this chain
                account = w3.eth.account.from_key(self.private_key)
                tx_params = {
                    'from': account.address,
                    'to': request.target_contract,
                    'data': request.calldata,
                    'value': request.value,
                    'gas': request.gas_limit or 500000,
                    'gasPrice': request.gas_price or w3.eth.gas_price,
                }

                if request.nonce is not None:
                    tx_params['nonce'] = request.nonce

                try:
                    signed_tx = w3.eth.account.sign_transaction(tx_params, self.private_key)
                    tx_hash = await asyncio.to_thread(w3.eth.send_raw_transaction, signed_tx.raw_transaction)

                    chain_exec = ChainExecution(
                        chain_id=chain_id,
                        tx_hash=tx_hash.hex(),
                        status=ExecutionStatus.SUBMITTED
                    )
                    chain_executions.append(chain_exec)

                except Exception as e:
                    chain_exec = ChainExecution(
                        chain_id=chain_id,
                        tx_hash='',
                        status=ExecutionStatus.FAILED
                    )
                    chain_executions.append(chain_exec)

            self._pending_executions[request.request_id] = chain_executions
            result.status = ExecutionStatus.SUBMITTED

            # Wait for confirmations
            result.status = ExecutionStatus.CONFIRMING
            all_confirmed = True

            for exec_data in chain_executions:
                if exec_data.status != ExecutionStatus.SUBMITTED:
                    all_confirmed = False
                    continue

                w3 = self._w3_providers.get(exec_data.chain_id)
                if not w3:
                    continue

                try:
                    receipt = await asyncio.to_thread(
                        w3.eth.wait_for_transaction_receipt,
                        exec_data.tx_hash,
                        timeout=300
                    )

                    exec_data.block_number = receipt['blockNumber']
                    exec_data.confirmed_at = int(time.time())

                    if receipt['status'] == 1:
                        exec_data.status = ExecutionStatus.CONFIRMED
                    else:
                        exec_data.status = ExecutionStatus.REVERTED
                        all_confirmed = False

                except Exception as e:
                    exec_data.status = ExecutionStatus.FAILED
                    all_confirmed = False

            if all_confirmed:
                result.status = ExecutionStatus.CONFIRMED
                result.confirmed_at = int(time.time())
                result.profit_usd = request.metadata.get('expected_profit_usd', 0)
            else:
                result.status = ExecutionStatus.FAILED
                result.error_message = "Some chain executions failed"

        except Exception as e:
            result.status = ExecutionStatus.FAILED
            result.error_message = str(e)

        execution_time_ms = int((time.time() - start_time) * 1000)
        self._update_stats(result, execution_time_ms)

        return result

    async def cancel(self, request_id: str) -> bool:
        """Cancel pending cross-chain execution"""
        if request_id in self._pending_executions:
            del self._pending_executions[request_id]
            return True
        return False

    def get_pending_executions(self) -> Dict[str, List[ChainExecution]]:
        """Get all pending executions"""
        return self._pending_executions

    def get_stats(self) -> Dict:
        """Get executor statistics"""
        base_stats = super().get_stats()
        return {
            **base_stats.__dict__,
            'pending_executions': len(self._pending_executions),
            'bridges_registered': len(self._bridge_contracts),
        }
