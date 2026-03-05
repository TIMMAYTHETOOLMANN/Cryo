"""
Oracle Event Listener - Real-time Chainlink Monitoring
Reads CAUSES (oracle updates) instead of EFFECTS (HF polling)
"""

import asyncio
from web3 import Web3
from typing import Dict, List

class OracleEventListener:
    """Your real-time oracle monitoring integrated with existing pipeline"""
    
    def __init__(self, rpc_gateway, position_index):
        self.rpc = rpc_gateway
        self.positions = position_index
        self.oracle_mappings = self._build_oracle_position_map()
        
    async def start_oracle_monitoring(self):
        """Start monitoring all relevant oracles"""
        
        # Get all unique oracles from watchlist positions
        unique_oracles = self._extract_unique_oracles()
        
        for chain_id, oracle_addresses in unique_oracles.items():
            for oracle_addr in oracle_addresses:
                # Subscribe using RPC gateway's optimized WebSocket
                asyncio.create_task(
                    self._subscribe_oracle_events(chain_id, oracle_addr)
                )
    
    async def _subscribe_oracle_events(self, chain_id: int, oracle_addr: str):
        """Subscribe to specific oracle events"""
        
        # Your WebSocket subscription logic
        subscription_params = {
            'address': oracle_addr,
            'topics': [Web3.keccak(text='AnswerUpdated(int256,uint256,uint256)').hex()]
        }
        
        async for event in self.rpc.subscribe_logs(subscription_params):
            await self._process_oracle_update(chain_id, event)
    
    async def _process_oracle_update(self, chain_id: int, event: Dict):
        """Process oracle update and trigger immediate liquidations"""
        
        # Extract price data
        new_price = int(event['data']['current'], 16) / 1e8  # Your Chainlink decimals logic
        
        # Find affected positions using this oracle
        affected_positions = self._get_affected_positions(chain_id, event['address'])
        
        liquidation_triggers = []
        
        for position in affected_positions:
            # Your critical insight: recalc HF WITH NEW PRICE
            new_hf = await self._recalculate_health_factor(position, new_price)
            
            # Detect threshold crossing: from above 1.0 to below 1.0
            if new_hf < 1.0 and position.current_hf >= 1.0:
                liquidation_triggers.append({
                    'position': position,
                    'old_hf': position.current_hf,
                    'new_hf': new_hf,
                    'trigger_price': new_price,
                    'oracle_address': event['address'],
                    'block_hash': event['blockHash'],  # Same block execution
                    'timestamp': event['timestamp']
                })
        
        # Execute immediate liquidations for valid triggers
        await self._execute_immediate_liquidations(liquidation_triggers)
    
    async def _execute_immediate_liquidations(self, triggers: List[Dict]):
        """Execute liquidations triggered by oracle updates"""
        
        for trigger in triggers:
            position = trigger['position']
            
            # Build liquidation transaction
            liquidation_tx = await self._build_liquidation_transaction(position)
            
            # Use Flashbots for same-block inclusion
            flashbots_bundle = [{
                'transaction': liquidation_tx,
                'canRevert': False
            }]
            
            # Submit via Flashbots relay for minimal inclusion delay
            result = await self.rpc.submit_flashbots_bundle(flashbots_bundle)
            
            if result['success']:
                print(f"✅ Oracle-triggered liquidation submitted | Position: {position['id']}")
            else:
                print(f"❌ Oracle liquidation failed | Error: {result['error']}")
