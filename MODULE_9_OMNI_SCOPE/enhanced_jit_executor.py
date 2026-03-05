"""
Enhanced JIT Executor - Pre-Signed Transactions with Optimal Gas
"""

import asyncio
import time
from typing import Dict, List

class EnhancedJITExecutor:
    """Your intelligent transaction preparation system"""
    
    def __init__(self, rpc_gateway, flashbots_relay, gas_oracle):
        self.rpc = rpc_gateway
        self.flashbots = flashbots_relay
        self.gas_oracle = gas_oracle
        self.ready_transactions = {}  # position_id -> signed_tx_data
        
    async def prepare_for_position(self, position: Dict, confidence_score: float):
        """Prepare pre-signed transaction for high-probability positions"""
        
        # Your transaction building logic
        tx_data = await self._build_liquidation_tx(position)
        
        # Sign with current nonce
        signed_tx = await self._sign_transaction(tx_data)
        
        # Dynamic gas pricing for next block
        gas_params = await self._get_optimal_gas_params()
        tx_data.update(gas_params)
        
        # Store for immediate execution
        self.ready_transactions[position['id']] = {
            'signed_tx': signed_tx,
            'gas_params': gas_params,
            'prepared_at': time.time(),
            'confidence': confidence_score
        }
    
    async def on_block_mined(self, new_block: Dict):
        """Execute when a new block is mined - check for threshold crossings"""
        
        # Get positions that became liquidatable in this block
        newly_liquidatable = await self._get_newly_liquidatable_positions(new_block)
        
        for position in newly_liquidatable:
            if position['id'] in self.ready_transactions:
                # Pre-signed transaction ready - execute immediately
                signed_tx = self.ready_transactions[position['id']]['signed_tx']
                
                # Submit via optimal channel based on urgency
                if position['urgency'] == 'high':
                    await self.flashbots.send_raw_transaction(signed_tx)
                else:
                    await self.rpc.submit_transaction(signed_tx)
                
                print(f"✅ JIT liquidation executed | Position: {position['id']}")
    
    async def _get_optimal_gas_params(self) -> Dict:
        """Your dynamic gas pricing strategy"""
        
        # Get next block fee estimation
        base_fee, priority_fee = await self.gas_oracle.estimate_next_block_fees()
        
        return {
            'maxFeePerGas': int(base_fee * 1.2 + priority_fee * 3),  # Aggressive bidding
            'maxPriorityFeePerGas': int(priority_fee * 2),
            'gasLimit': 500000  # Ample gas limit
        }
