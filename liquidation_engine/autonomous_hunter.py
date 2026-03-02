#!/usr/bin/env python3
"""
Autonomous Profit Hunter - Runs until 5 verified profits
Monitors all executors and tracks cumulative profits
"""

import asyncio
import time
from datetime import datetime
from web3 import Web3

# Configuration
ALCHEMY_API_KEY = "Rc0sle99H0nN5Sm3C3JyfeAmqYz4hmlu"
RPC_URL = f"https://eth-mainnet.g.alchemy.com/v2/{ALCHEMY_API_KEY}"

# Deployed executors
EXECUTORS = {
    'V1': '0x76dF81F4E3F6058DD6a9c0a6ed2f224b49375B9f',
    'V2': '0xFf11E1d641f2ED7aD98629F04d1e103Ff6B44890',
}

TREASURY = '0xB323C6E32C6efe28FB9cfB1A83F4071c544eA1e4'

# ABI for events
EXECUTOR_ABI = '''
[
    {"anonymous":false,"inputs":[{"indexed":true,"name":"user","type":"address"},{"indexed":false,"name":"debtAsset","type":"address"},{"indexed":false,"name":"collateralAsset","type":"address"},{"indexed":false,"name":"debtCovered","type":"uint256"},{"indexed":false,"name":"collateralSeized","type":"uint256"},{"indexed":false,"name":"profit","type":"uint256"}],"name":"LiquidationExecuted","type":"event"},
    {"anonymous":false,"inputs":[{"indexed":false,"name":"totalLiquidations","type":"uint256"},{"indexed":false,"name":"totalProfit","type":"uint256"}],"name":"StatsUpdated","type":"event"}
]
'''

class AutonomousProfitHunter:
    def __init__(self):
        self.w3 = Web3(Web3.HTTPProvider(RPC_URL))
        self.target_profits = 5
        self.verified_profits = 0
        self.last_block = self.w3.eth.block_number
        self.start_time = time.time()
        self.profit_events = []
        
        # Initialize executor contracts
        self.executors = {}
        for name, address in EXECUTORS.items():
            self.executors[name] = self.w3.eth.contract(
                address=Web3.to_checksum_address(address),
                abi=self.w3.eth.contract(abi=EXECUTOR_ABI).abi
            )
        
        print("=" * 70)
        print("🤖 AUTONOMOUS PROFIT HUNTER INITIALIZED")
        print("=" * 70)
        print(f"Target: {self.target_profits} verified profits")
        print(f"Treasury: {TREASURY}")
        print(f"Executors: {len(EXECUTORS)} contracts")
        for name, addr in EXECUTORS.items():
            print(f"  - {name}: {addr}")
        print(f"Starting Block: {self.last_block}")
        print("=" * 70)
        print("")
    
    async def start(self):
        """Run autonomous monitoring until target reached"""
        print("🚀 Starting autonomous operation...")
        print(f"Start Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        print("")
        
        while self.verified_profits < self.target_profits:
            try:
                await self.scan_for_profits()
                await self.check_treasury()
                await asyncio.sleep(12)  # Check every block
            except Exception as e:
                print(f"❌ Error: {e}")
                await asyncio.sleep(5)
        
        # Target reached
        await self.complete_mission()
    
    async def scan_for_profits(self):
        """Scan all executors for liquidation events"""
        current_block = self.w3.eth.block_number
        
        if current_block <= self.last_block:
            return
        
        for name, executor in self.executors.items():
            events = executor.events.LiquidationExecuted.create_filter(
                fromBlock=self.last_block + 1,
                toBlock=current_block
            ).get_all_entries()
            
            for event in events:
                await self.process_profit_event(name, event)
        
        self.last_block = current_block
    
    async def process_profit_event(self, executor_name, event):
        """Process a liquidation profit event"""
        user = event['args']['user']
        debt_covered = event['args']['debtCovered'] / 1e18
        collateral_seized = event['args']['collateralSeized'] / 1e18
        profit = event['args']['profit'] / 1e18
        
        self.verified_profits += 1
        self.profit_events.append({
            'executor': executor_name,
            'block': event['blockNumber'],
            'user': user,
            'profit_eth': profit,
            'timestamp': time.time()
        })
        
        print("")
        print("🎉 PROFIT VERIFIED!")
        print("=" * 70)
        print(f"Executor: {executor_name}")
        print(f"Block: {event['blockNumber']}")
        print(f"User: {user}")
        print(f"Debt Covered: {debt_covered:.2f} ETH")
        print(f"Collateral Seized: {collateral_seized:.4f} ETH")
        print(f"PROFIT: {profit:.4f} ETH (~${profit * 2000:.2f})")
        print("=" * 70)
        print(f"Progress: {self.verified_profits}/{self.target_profits} profits")
        print("")
    
    async def check_treasury(self):
        """Check treasury balance for verification"""
        balance = self.w3.eth.get_balance(
            Web3.to_checksum_address(TREASURY)
        ) / 1e18
        
        elapsed = time.time() - self.start_time
        hours = elapsed / 3600
        
        print(f"💼 Treasury: {balance:.4f} ETH | Uptime: {hours:.2f}h | Profits: {self.verified_profits}/{self.target_profits}", end='\r')
    
    async def complete_mission(self):
        """Mission complete - 5 profits verified"""
        elapsed = time.time() - self.start_time
        hours = elapsed / 3600
        
        total_profit = sum(p['profit_eth'] for p in self.profit_events)
        avg_profit = total_profit / len(self.profit_events)
        
        print("")
        print("=" * 70)
        print("🎉🎉🎉 MISSION COMPLETE! 🎉🎉🎉")
        print("=" * 70)
        print(f"Target Reached: {self.target_profits} Verified Profits")
        print(f"Total Runtime: {hours:.2f} hours")
        print(f"Total Profit: {total_profit:.4f} ETH (~${total_profit * 2000:.2f})")
        print(f"Average Profit: {avg_profit:.4f} ETH (~${avg_profit * 2000:.2f})")
        print("")
        print("Profit Breakdown:")
        for i, p in enumerate(self.profit_events, 1):
            print(f"  {i}. {p['executor']} - Block {p['block']} - {p['profit_eth']:.4f} ETH")
        print("")
        print("System validated and ready for enhancements!")
        print("=" * 70)

async def main():
    hunter = AutonomousProfitHunter()
    await hunter.start()

if __name__ == "__main__":
    asyncio.run(main())
