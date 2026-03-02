#!/usr/bin/env python3
"""
Flash Loan Liquidation Engine - Opportunity Detector
Scans for undercollateralized positions across DeFi protocols
"""

import asyncio
import json
import time
from datetime import datetime
from typing import Dict, List, Optional
from dataclasses import dataclass, asdict
from web3 import Web3
from web3.middleware import geth_poa_middleware
import aiohttp

# Configuration — use environment RPC endpoints (managed by RPC Gateway)
import os
from dotenv import load_dotenv
load_dotenv()

ALCHEMY_API_KEY = os.getenv("ALCHEMY_API_KEY", "Rc0sle99H0nN5Sm3C3JyfeAmqYz4hmlu")

RPC_ENDPOINTS = {
    1: os.getenv("ETH_RPC_URL", f"https://eth-mainnet.g.alchemy.com/v2/{ALCHEMY_API_KEY}"),
    42161: os.getenv("ARBITRUM_RPC_URL", f"https://arb-mainnet.g.alchemy.com/v2/{ALCHEMY_API_KEY}"),
    10: os.getenv("OPTIMISM_RPC_URL", f"https://opt-mainnet.g.alchemy.com/v2/{ALCHEMY_API_KEY}"),
    137: os.getenv("POLYGON_RPC_URL", f"https://polygon-mainnet.g.alchemy.com/v2/{ALCHEMY_API_KEY}"),
    8453: os.getenv("BASE_RPC_URL", f"https://base-mainnet.g.alchemy.com/v2/{ALCHEMY_API_KEY}"),
}

# Protocol addresses by chain
PROTOCOLS = {
    1: {
        "aave_v2": {
            "pool": "0x7d2768dE32b0b80b7a3454c06BdAc94A69DDc7A9",
            "liquidation_threshold": 0.80,  # 80% LTV
            "bonus": 0.05,  # 5% bonus
        },
        "aave_v3": {
            "pool": "0x87870Bca3F3fD6335C3F4ce8392D69350B4fA4E2",
            "liquidation_threshold": 0.825,
            "bonus": 0.05,
        },
        "compound_v2": {
            "comptroller": "0x3d9819210A31b4961b30EF54bE2aeD79B9c9Cd3B",
            "liquidation_threshold": 0.75,
            "bonus": 0.08,
        },
    },
    42161: {
        "aave_v3": {
            "pool": "0x794a61358D6845594F94dc1DB02A252b5b4814aD",
            "liquidation_threshold": 0.825,
            "bonus": 0.05,
        },
    },
}

# ABI fragments
HEALTH_FACTOR_ABI = '''
[{"inputs":[{"name":"user","type":"address"}],"name":"getUserAccountData","outputs":[{"name":"totalCollateralETH","type":"uint256"},{"name":"totalDebtETH","type":"uint256"},{"name":"availableBorrowsETH","type":"uint256"},{"name":"currentLiquidationThreshold","type":"uint256"},{"name":"ltv","type":"uint256"},{"name":"healthFactor","type":"uint256"}],"stateMutability":"view","type":"function"}]
'''

LIQUIDATION_CALL_ABI = '''
[{"inputs":[{"name":"collateral","type":"address"},{"name":"debt","type":"address"},{"name":"user","type":"address"},{"name":"debtToCover","type":"uint256"},{"name":"receiveAToken","type":"bool"}],"name":"liquidationCall","outputs":[],"stateMutability":"nonpayable","type":"function"}]
'''

@dataclass
class LiquidatablePosition:
    chain_id: int
    protocol: str
    user: str
    debt_asset: str
    collateral_asset: str
    debt_amount: int
    collateral_amount: int
    health_factor: float
    liquidation_bonus: float
    estimated_profit_usd: float
    max_gas_price: int
    timestamp: int

class OpportunityDetector:
    def __init__(self):
        self.w3_clients = {}
        self.liquidation_queue = asyncio.Queue()
        self.min_debt_usd = 1000  # Minimum debt to consider
        self.min_profit_usd = 10  # Minimum profit threshold
        
        for chain_id, rpc in RPC_ENDPOINTS.items():
            w3 = Web3(Web3.HTTPProvider(rpc))
            w3.middleware_onion.inject(geth_poa_middleware, layer=0)
            self.w3_clients[chain_id] = w3
    
    async def start(self):
        """Start the opportunity detection loop"""
        print(f"🔍 Starting Liquidation Opportunity Detector")
        print(f"Monitoring {len(RPC_ENDPOINTS)} chains")
        
        tasks = []
        for chain_id in RPC_ENDPOINTS.keys():
            tasks.append(self.scan_chain(chain_id))
        
        await asyncio.gather(*tasks)
    
    async def scan_chain(self, chain_id: int):
        """Continuously scan a single chain for liquidatable positions"""
        w3 = self.w3_clients[chain_id]
        
        while True:
            try:
                block_number = w3.eth.block_number
                print(f"📊 Scanning chain {chain_id} at block {block_number}")
                
                # In production, you would:
                # 1. Query your database of tracked positions
                # 2. Check health factors for positions near threshold
                # 3. Flag those with HF < 1.05
                
                # For demo, we'll simulate checking known positions
                await self.check_positions(chain_id)
                
                await asyncio.sleep(3)  # Check every 3 seconds
                
            except Exception as e:
                print(f"❌ Error scanning chain {chain_id}: {e}")
                await asyncio.sleep(5)
    
    async def check_positions(self, chain_id: int):
        """Check positions for liquidatability"""
        w3 = self.w3_clients[chain_id]
        protocols = PROTOCOLS.get(chain_id, {})
        
        for protocol_name, config in protocols.items():
            if protocol_name == "aave_v3":
                await self.check_aave_v3_positions(
                    w3, chain_id, protocol_name, config
                )
    
    async def check_aave_v3_positions(
        self, w3, chain_id: int, protocol_name: str, config: dict
    ):
        """Check Aave V3 positions for liquidation"""
        pool_address = config["pool"]
        pool_contract = w3.eth.contract(
            address=Web3.to_checksum_address(pool_address),
            abi=json.loads(HEALTH_FACTOR_ABI)
        )
        
        # In production, query your database of tracked users
        # For demo, check some known addresses
        test_users = [
            "0x47ac0Fb4F2D84898e4D9E7b4DaB3C24507a6D503",  # Known Aave user
        ]
        
        for user in test_users:
            try:
                # Get health factor
                health_factor = await self.get_health_factor(
                    pool_contract, user
                )
                
                if health_factor and health_factor < 1.10:
                    print(f"⚠️  LIQUIDATABLE: {user} HF={health_factor}")
                    
                    # Create liquidation opportunity
                    position = LiquidatablePosition(
                        chain_id=chain_id,
                        protocol=protocol_name,
                        user=user,
                        debt_asset="0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48",  # USDC
                        collateral_asset="0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2",  # WETH
                        debt_amount=10000 * 10**6,  # $10k
                        collateral_amount=8500 * 10**18,  # ~$8.5k ETH
                        health_factor=health_factor,
                        liquidation_bonus=config["bonus"],
                        estimated_profit_usd=500,  # Example
                        max_gas_price=50 * 10**9,
                        timestamp=int(time.time())
                    )
                    
                    await self.liquidation_queue.put(position)
                    print(f"✅ Queued liquidation opportunity")
                    
            except Exception as e:
                print(f"Error checking user {user}: {e}")
    
    async def get_health_factor(self, contract, user: str) -> Optional[float]:
        """Get user's health factor from Aave"""
        try:
            result = contract.functions.getUserAccountData(user).call()
            health_factor = result[5] / 1e18  # Scale from 1e18
            return health_factor
        except Exception as e:
            return None
    
    async def process_queue(self):
        """Process liquidation opportunities"""
        while True:
            try:
                position = await asyncio.wait_for(
                    self.liquidation_queue.get(), timeout=1.0
                )
                
                print(f"🎯 Processing liquidation:")
                print(f"   Chain: {position.chain_id}")
                print(f"   Protocol: {position.protocol}")
                print(f"   User: {position.user}")
                print(f"   Health Factor: {position.health_factor}")
                print(f"   Est. Profit: ${position.estimated_profit_usd}")
                
                # In production, this would:
                # 1. Calculate exact profitability
                # 2. Request flash loan
                # 3. Execute liquidation
                # 4. Repay flash loan
                # 5. Transfer profit
                
            except asyncio.TimeoutError:
                pass
            except Exception as e:
                print(f"Error processing queue: {e}")

async def main():
    detector = OpportunityDetector()
    
    # Start detection and queue processing
    await asyncio.gather(
        detector.start(),
        detector.process_queue()
    )

if __name__ == "__main__":
    asyncio.run(main())
