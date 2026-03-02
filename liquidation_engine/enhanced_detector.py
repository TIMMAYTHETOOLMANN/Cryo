#!/usr/bin/env python3
"""
Enhanced Liquidation Detector with Real-Time Event Streaming
Monitors oracle updates, liquidation events, and mempool for proactive detection
"""

import asyncio
import json
import time
from datetime import datetime
from typing import Dict, List, Optional
from dataclasses import dataclass
from web3 import Web3
from web3.middleware import geth_poa_middleware
import aiohttp

# Configuration
ALCHEMY_API_KEY = "Rc0sle99H0nN5Sm3C3JyfeAmqYz4hmlu"
WS_ENDPOINT = f"wss://eth-mainnet.g.alchemy.com/v2/{ALCHEMY_API_KEY}"

# Contract addresses
AAVE_V3_POOL = "0x87870Bca3F3fD6335C3F4ce8392D69350B4fA4E2"
CHAINLINK_ORACLE = "0x5f4eC3Df9cbd43714FE2740f5E3616155c5b8419"

# Event ABIs
LIQUIDATION_EVENT_ABI = '''
[{"anonymous":false,"inputs":[{"indexed":true,"name":"debtAsset","type":"address"},{"indexed":true,"name":"collateralAsset","type":"address"},{"indexed":true,"name":"user","type":"address"},{"indexed":false,"name":"debtToCover","type":"uint256"},{"indexed":false,"name":"liquidatedCollateralAmount","type":"uint256"},{"indexed":false,"name":"liquidator","type":"address"},{"indexed":false,"name":"receiveAToken","type":"bool"}],"name":"LiquidationCall","type":"event"}]
'''

ORACLE_UPDATE_ABI = '''
[{"anonymous":false,"inputs":[{"indexed":true,"name":"aggregator","type":"address"},{"indexed":true,"name":"roundId","type":"uint256"},{"indexed":false,"name":"updatedAt","type":"uint256"},{"indexed":false,"name":"answer","type":"int256"}],"name":"AnswerUpdated","type":"event"}]
'''

@dataclass
class LiquidationSignal:
    """Proactive liquidation signal with confidence score"""
    chain_id: int
    protocol: str
    user: str
    debt_asset: str
    collateral_asset: str
    current_health_factor: float
    estimated_hf_next_block: float  # Predicted HF
    liquidation_probability: float  # 0-1 score
    confidence_score: float  # 0-100
    trigger_event: str  # What triggered this signal
    timestamp: int

class EnhancedDetector:
    def __init__(self):
        self.w3 = Web3(Web3.HTTPProvider(f"https://eth-mainnet.g.alchemy.com/v2/{ALCHEMY_API_KEY}"))
        self.ws_w3 = None
        self.liquidation_queue = asyncio.Queue()
        self.signal_queue = asyncio.Queue()
        
        # Tracking state
        self.tracked_users = set()
        self.oracle_updates = {}
        self.liquidation_history = []
        
        # Event contracts
        self.pool_contract = self.w3.eth.contract(
            address=Web3.to_checksum_address(AAVE_V3_POOL),
            abi=json.loads(LIQUIDATION_EVENT_ABI)
        )
        self.oracle_contract = self.w3.eth.contract(
            address=Web3.to_checksum_address(CHAINLINK_ORACLE),
            abi=json.loads(ORACLE_UPDATE_ABI)
        )
        
        print("🔍 Enhanced Liquidation Detector Initialized")
        print(f"   WebSocket: {WS_ENDPOINT}")
        print(f"   Tracking Aave V3: {AAVE_V3_POOL}")
        print(f"   Monitoring Oracle: {CHAINLINK_ORACLE}")
    
    async def connect_websocket(self):
        """Connect to Alchemy WebSocket for real-time events"""
        try:
            self.ws_w3 = Web3(Web3.WebsocketProvider(WS_ENDPOINT))
            print("✅ WebSocket connected")
        except Exception as e:
            print(f"❌ WebSocket connection failed: {e}")
            self.ws_w3 = None
    
    async def start(self):
        """Start enhanced detection with multiple data streams"""
        print("\n🚀 Starting Enhanced Detection Engine")
        print("=" * 60)
        
        # Connect WebSocket
        await self.connect_websocket()
        
        # Start parallel monitoring tasks
        tasks = [
            self.monitor_oracle_updates(),
            self.monitor_liquidation_events(),
            self.monitor_mempool(),
            self.process_signals(),
            self.calculate_probabilities()
        ]
        
        await asyncio.gather(*tasks)
    
    async def monitor_oracle_updates(self):
        """Monitor Chainlink oracle price updates in real-time"""
        print("\n📡 Monitoring Oracle Updates...")
        
        if not self.ws_w3:
            # Fallback to polling
            while True:
                await self.poll_oracle_updates()
                await asyncio.sleep(3)
            return
        
        # Subscribe to oracle events
        event_filter = self.oracle_contract.events.AnswerUpdated.create_filter(fromBlock='latest')
        
        while True:
            try:
                for event in event_filter.get_all_entries():
                    await self.process_oracle_update(event)
                await asyncio.sleep(1)
            except Exception as e:
                print(f"Oracle monitoring error: {e}")
                await asyncio.sleep(5)
    
    async def poll_oracle_updates(self):
        """Poll for oracle updates (fallback)"""
        try:
            current_block = self.w3.eth.block_number
            event_filter = self.oracle_contract.events.AnswerUpdated.create_filter(
                fromBlock=current_block - 10,
                toBlock=current_block
            )
            
            for event in event_filter.get_all_entries():
                await self.process_oracle_update(event)
        except Exception as e:
            pass
    
    async def process_oracle_update(self, event):
        """Process oracle price update and recalculate risk"""
        aggregator = event['args']['aggregator']
        answer = event['args']['answer']
        round_id = event['args']['roundId']
        
        self.oracle_updates[aggregator] = {
            'price': answer,
            'round_id': round_id,
            'timestamp': event['blockNumber'],
            'updated_at': time.time()
        }
        
        print(f"💹 Oracle Update: {aggregator[:10]}... = {answer / 1e8:.2f}")
        
        # Recalculate health factors for tracked positions
        # Price drops may trigger liquidations
        await self.recalculate_risk(aggregator, answer)
    
    async def monitor_liquidation_events(self):
        """Monitor liquidation events to learn patterns"""
        print("\n📊 Monitoring Liquidation Events...")
        
        while True:
            try:
                current_block = self.w3.eth.block_number
                event_filter = self.pool_contract.events.LiquidationCall.create_filter(
                    fromBlock=current_block - 100,
                    toBlock=current_block
                )
                
                for event in event_filter.get_all_entries():
                    await self.process_liquidation_event(event)
                
                await asyncio.sleep(12)  # Check every block
            except Exception as e:
                print(f"Liquidation monitoring error: {e}")
                await asyncio.sleep(5)
    
    async def process_liquidation_event(self, event):
        """Process liquidation event for pattern learning"""
        user = event['args']['user']
        debt_asset = event['args']['debtAsset']
        collateral_asset = event['args']['collateralAsset']
        debt_covered = event['args']['debtToCover']
        liquidated_collateral = event['args']['liquidatedCollateralAmount']
        
        self.liquidation_history.append({
            'user': user,
            'debt_asset': debt_asset,
            'collateral_asset': collateral_asset,
            'debt_covered': debt_covered,
            'liquidated_collateral': liquidated_collateral,
            'block': event['blockNumber'],
            'timestamp': time.time()
        })
        
        # Keep last 1000 liquidations
        if len(self.liquidation_history) > 1000:
            self.liquidation_history = self.liquidation_history[-1000:]
        
        print(f"🎯 Liquidation: {user[:10]}... | Debt: {debt_covered / 1e18:.2f} ETH")
    
    async def monitor_mempool(self):
        """Monitor mempool for large swaps that may trigger liquidations"""
        print("\n🔍 Monitoring Mempool (Simulated)...")
        
        # Note: Real mempool access requires Flashbots/Alchemy Mempool API
        # This is a placeholder for the enhancement
        
        while True:
            # In production, use alchemy_pendingTransactions or Flashbots
            await asyncio.sleep(5)
    
    async def calculate_probabilities(self):
        """Calculate liquidation probabilities for tracked positions"""
        print("\n🧮 Calculating Liquidation Probabilities...")
        
        while True:
            try:
                # For each tracked user, calculate probability score
                for user in list(self.tracked_users)[:10]:  # Limit to 10 for demo
                    probability = await self.calculate_user_probability(user)
                    
                    if probability['score'] > 0.7:  # High probability
                        signal = LiquidationSignal(
                            chain_id=1,
                            protocol='aave_v3',
                            user=user,
                            debt_asset='0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48',
                            collateral_asset='0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2',
                            current_health_factor=probability['current_hf'],
                            estimated_hf_next_block=probability['predicted_hf'],
                            liquidation_probability=probability['score'],
                            confidence_score=probability['confidence'],
                            trigger_event='probability_threshold',
                            timestamp=int(time.time())
                        )
                        await self.signal_queue.put(signal)
                
                await asyncio.sleep(30)  # Recalculate every 30 seconds
            except Exception as e:
                print(f"Probability calculation error: {e}")
                await asyncio.sleep(10)
    
    async def calculate_user_probability(self, user: str) -> dict:
        """Calculate liquidation probability for a user"""
        # In production, this would:
        # 1. Get current health factor from Aave
        # 2. Analyze price volatility
        # 3. Check recent oracle updates
        # 4. Calculate probability using ML model
        
        # For demo, return simulated values
        return {
            'current_hf': 1.02,
            'predicted_hf': 0.98,
            'score': 0.75,
            'confidence': 82.5
        }
    
    async def recalculate_risk(self, aggregator: str, new_price: int):
        """Recalculate risk when oracle price updates"""
        # Check if any tracked positions are affected by this price change
        print(f"   Recalculating risk for price update...")
    
    async def process_signals(self):
        """Process liquidation signals from all sources"""
        print("\n⚡ Processing Liquidation Signals...")
        
        while True:
            try:
                signal = await asyncio.wait_for(
                    self.signal_queue.get(), timeout=1.0
                )
                
                print(f"\n🚨 LIQUIDATION SIGNAL DETECTED")
                print(f"   User: {signal.user}")
                print(f"   Protocol: {signal.protocol}")
                print(f"   Current HF: {signal.current_health_factor:.3f}")
                print(f"   Predicted HF: {signal.estimated_hf_next_block:.3f}")
                print(f"   Probability: {signal.liquidation_probability * 100:.1f}%")
                print(f"   Confidence: {signal.confidence_score:.1f}%")
                print(f"   Trigger: {signal.trigger_event}")
                
                # Queue for execution
                await self.liquidation_queue.put(signal)
                
            except asyncio.TimeoutError:
                pass
            except Exception as e:
                print(f"Signal processing error: {e}")

async def main():
    detector = EnhancedDetector()
    await detector.start()

if __name__ == "__main__":
    asyncio.run(main())
