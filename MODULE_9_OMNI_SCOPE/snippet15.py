"""
Enhanced Collateral Health Monitor - Production-Ready
Integrated with RPC gateway for multi-chain monitoring
"""

import asyncio
from typing import Dict, List
from web3 import Web3

class EnhancedCollateralHealthMonitor:
    """Production monitor with WebSocket integration"""
    
    def __init__(self, rpc_gateway):
        self.rpc = rpc_gateway
        self.protocol_adapters = self._initialize_protocol_adapters()
        self.websocket_subscriptions = {}
        
    async def scan_protocol_positions(self, chain_id: str, protocol: str) -> List[Dict]:
        """Scan positions for specific protocol with optimized RPC usage"""
        
        # Batch requests using RPC gateway batch aggregation
        positions = await self._batch_scan_positions(chain_id, protocol)
        
        # Calculate health factors
        positions_with_hf = []
        for position in positions:
            health_factor = await self._calculate_health_factor(position, chain_id)
            
            positions_with_hf.append({
                **position,
                'health_factor': health_factor,
                'chain_id': chain_id,
                'protocol': protocol,
                'timestamp': time.time()
            })
        
        return positions_with_hf
    
    async def start_websocket_monitoring(self, chain_id: str):
        """Start real-time WebSocket monitoring for oracle events"""
        
        # Subscribe to protocol events (LiquidationCall, Borrow, Repay)
        subscription_params = {
            'address': self._get_protocol_address(chain_id),
            'topics': [Web3.keccak(text='LiquidationCall(address,address,address,uint256,uint256,address,bool)').hex()]
        }
        
        async for event in self.rpc.subscribe_websocket(subscription_params):
            await self._handle_liquidation_event(event, chain_id)
    
    async def _handle_liquidation_event(self, event: Dict, chain_id: str):
        """Handle real-time liquidation events for instant reaction"""
        
        # Extract position data from event
        liquidated_position = await self._extract_position_from_event(event)
        
        # Check if this presents an opportunity
        if await self._is_profitable_liquidation(liquidated_position, chain_id):
            await self._trigger_immediate_execution(liquidated_position, chain_id)
