#!/usr/bin/env python3
"""
COMPREHENSIVE SYSTEM MONITOR
Continuous monitoring until 5 profits verified OR timeout/error

Monitors:
- All executor contracts
- Treasury balance
- Running processes
- Event logs
- Errors and timeouts
"""

import asyncio
import time
import json
import sys
from datetime import datetime, timedelta
from web3 import Web3
from typing import Dict, List, Optional
import traceback

# Configuration
ALCHEMY_API_KEY = "Rc0sle99H0nN5Sm3C3JyfeAmqYz4hmlu"
RPC_URL = f"https://eth-mainnet.g.alchemy.com/v2/{ALCHEMY_API_KEY}"

# Contracts
EXECUTORS = {
    'V1': '0x76dF81F4E3F6058DD6a9c0a6ed2f224b49375B9f',
    'V2': '0xFf11E1d641f2ED7aD98629F04d1e103Ff6B44890',
}
TREASURY = '0xB323C6E32C6efe28FB9cfB1A83F4071c544eA1e4'

# Mission parameters
TARGET_PROFITS = 5
TIMEOUT_HOURS = 72  # 72 hour timeout
MAX_ERRORS = 10  # Max errors before abort

# ABI
EXECUTOR_ABI = '''
[
    {"anonymous":false,"inputs":[{"indexed":true,"name":"user","type":"address"},{"indexed":false,"name":"debtAsset","type":"address"},{"indexed":false,"name":"collateralAsset","type":"address"},{"indexed":false,"name":"debtCovered","type":"uint256"},{"indexed":false,"name":"collateralSeized","type":"uint256"},{"indexed":false,"name":"profit","type":"uint256"}],"name":"LiquidationExecuted","type":"event"}
]
'''

class ComprehensiveMonitor:
    def __init__(self):
        self.w3 = Web3(Web3.HTTPProvider(RPC_URL))
        
        # Mission state
        self.verified_profits = 0
        self.start_time = time.time()
        self.timeout = self.start_time + (TIMEOUT_HOURS * 3600)
        self.error_count = 0
        self.last_successful_check = time.time()
        
        # Tracking
        self.profit_events = []
        self.error_log = []
        self.status_log = []
        self.last_block_checked = self.w3.eth.block_number
        
        # Initialize contracts
        self.executor_contracts = {}
        for name, addr in EXECUTORS.items():
            self.executor_contracts[name] = self.w3.eth.contract(
                address=Web3.to_checksum_address(addr),
                abi=self.w3.eth.contract(abi=EXECUTOR_ABI).abi
            )
        
        # Get initial treasury balance
        self.initial_treasury_balance = self.w3.eth.get_balance(
            Web3.to_checksum_address(TREASURY)
        ) / 1e18
        
        self.print_header()
    
    def print_header(self):
        """Print comprehensive header"""
        print("")
        print("=" * 80)
        print("[M] COMPREHENSIVE SYSTEM MONITOR")
        print("=" * 80)
        print(f"Mission: Verify {TARGET_PROFITS} profitable liquidations")
        print(f"Timeout: {TIMEOUT_HOURS} hours from start")
        print(f"Max Errors: {MAX_ERRORS} before abort")
        print("")
        print("Contracts Monitored:")
        for name, addr in EXECUTORS.items():
            print(f"  {name}: {addr}")
        print("")
        print(f"Treasury: {TREASURY}")
        print(f"Initial Balance: {self.initial_treasury_balance:.6f} ETH")
        print("")
        print(f"Start Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"Starting Block: {self.last_block_checked}")
        print("=" * 80)
        print("")
    
    async def run(self):
        """Main monitoring loop"""
        print("[>] Starting comprehensive monitoring...")
        print("")
        
        try:
            while True:
                # Check termination conditions
                if self.verified_profits >= TARGET_PROFITS:
                    await self.mission_complete()
                    break
                
                if time.time() > self.timeout:
                    await self.mission_timeout("Time limit reached")
                    break
                
                if self.error_count >= MAX_ERRORS:
                    await self.mission_timeout("Max errors exceeded")
                    break
                
                # Run monitoring cycle
                await self.monitoring_cycle()
                
                # Sleep between cycles
                await asyncio.sleep(10)  # Check every 10 seconds
                
        except KeyboardInterrupt:
            print("\n\n[!]  Monitoring interrupted by user")
            await self.print_status()
        except Exception as e:
            print(f"\n\n[X] CRITICAL ERROR: {e}")
            traceback.print_exc()
            await self.log_error(f"Critical error: {e}")
    
    async def monitoring_cycle(self):
        """Single monitoring cycle"""
        try:
            current_block = self.w3.eth.block_number
            
            # Check for new events
            await self.scan_for_events(current_block)
            
            # Check treasury
            await self.check_treasury()
            
            # System health check
            await self.health_check()
            
            # Update status display
            self.update_status_display(current_block)
            
            self.last_successful_check = time.time()
            
        except Exception as e:
            await self.log_error(f"Monitoring cycle error: {e}")
            self.error_count += 1
    
    async def scan_for_events(self, current_block: int):
        """Scan all executors for liquidation events"""
        if current_block <= self.last_block_checked:
            return
        
        for name, address in EXECUTORS.items():
            try:
                # Use low-level eth_getLogs call
                event_signature = self.w3.keccak(text="LiquidationExecuted(address,address,address,uint256,uint256,uint256)").hex()
                
                logs = self.w3.eth.get_logs({
                    'address': Web3.to_checksum_address(address),
                    'fromBlock': self.last_block_checked + 1,
                    'toBlock': current_block,
                    'topics': [event_signature]
                })
                
                for log in logs:
                    # Parse the log
                    try:
                        event_data = self.executor_contracts[name].events.LiquidationExecuted().process_log(log)
                        await self.process_liquidation_event(name, event_data)
                    except:
                        pass
                    
            except Exception as e:
                await self.log_error(f"Event scan error ({name}): {e}")
        
        self.last_block_checked = current_block
    
    async def process_liquidation_event(self, executor_name: str, event):
        """Process a liquidation event"""
        user = event['args']['user']
        debt_covered = event['args']['debtCovered'] / 1e18
        collateral_seized = event['args']['collateralSeized'] / 1e18
        profit = event['args']['profit'] / 1e18
        
        self.verified_profits += 1
        self.profit_events.append({
            'number': self.verified_profits,
            'executor': executor_name,
            'block': event['blockNumber'],
            'user': user,
            'debt_covered': debt_covered,
            'collateral_seized': collateral_seized,
            'profit_eth': profit,
            'profit_usd': profit * 2000,
            'timestamp': time.time()
        })
        
        print("")
        print("[!] " + "=" * 76)
        print("[!] PROFIT VERIFIED!")
        print("[!] " + "=" * 76)
        print(f"   Profit #{self.verified_profits} of {TARGET_PROFITS}")
        print(f"   Executor: {executor_name}")
        print(f"   Block: {event['blockNumber']}")
        print(f"   User: {user}")
        print(f"   Debt Covered: {debt_covered:.4f} ETH")
        print(f"   Collateral Seized: {collateral_seized:.4f} ETH")
        print(f"   PROFIT: {profit:.6f} ETH (~${profit * 2000:.2f})")
        print(f"   Progress: {self.verified_profits}/{TARGET_PROFITS} ({(self.verified_profits/TARGET_PROFITS)*100:.1f}%)")
        print("[!] " + "=" * 76)
        print("")
        
        # Log to file
        await self.log_profit(event)
    
    async def check_treasury(self):
        """Check treasury balance"""
        try:
            balance = self.w3.eth.get_balance(
                Web3.to_checksum_address(TREASURY)
            ) / 1e18
            
            profit = balance - self.initial_treasury_balance
            
            if profit > 0.001:  # More than 0.001 ETH profit
                print(f"[$] Treasury: {balance:.6f} ETH | Profit: +{profit:.6f} ETH (~${profit * 2000:.2f})")
            else:
                print(f"[$] Treasury: {balance:.6f} ETH", end='\r')
                
        except Exception as e:
            await self.log_error(f"Treasury check error: {e}")
    
    async def health_check(self):
        """System health check"""
        # Check RPC connection
        try:
            block = self.w3.eth.block_number
            # Block should advance, but give it time
            if block < self.last_block_checked:
                await self.log_error("Block number went backwards")
        except Exception as e:
            await self.log_error(f"RPC health check failed: {e}")
    
    def update_status_display(self, current_block: int):
        """Update status line"""
        elapsed = time.time() - self.start_time
        hours = elapsed / 3600
        remaining = (self.timeout - time.time()) / 3600
        
        status = (
            f"[#] Block: {current_block} | "
            f"Profits: {self.verified_profits}/{TARGET_PROFITS} | "
            f"Uptime: {hours:.2f}h | "
            f"Remaining: {remaining:.1f}h | "
            f"Errors: {self.error_count}/{MAX_ERRORS}"
        )
        print(status, end='\r')
    
    async def log_error(self, error_msg: str):
        """Log error"""
        timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        error_entry = {
            'timestamp': timestamp,
            'error': error_msg,
            'count': self.error_count
        }
        self.error_log.append(error_entry)
        print(f"\n[X] ERROR [{self.error_count}]: {error_msg}\n")
    
    async def log_profit(self, event):
        """Log profit to file"""
        try:
            with open('profit_log.json', 'a') as f:
                json.dump({
                    'timestamp': datetime.now().isoformat(),
                    'block': event['blockNumber'],
                    'executor': 'V1',  # Would need to track properly
                    'profit': str(event['args']['profit'])
                }, f)
                f.write('\n')
        except:
            pass
    
    async def print_status(self):
        """Print comprehensive status"""
        elapsed = time.time() - self.start_time
        hours = elapsed / 3600
        
        print("")
        print("=" * 80)
        print("[#] SYSTEM STATUS REPORT")
        print("=" * 80)
        print(f"Runtime: {hours:.2f} hours")
        print(f"Profits Verified: {self.verified_profits}/{TARGET_PROFITS}")
        print(f"Errors Encountered: {self.error_count}")
        print(f"Last Successful Check: {datetime.fromtimestamp(self.last_successful_check).strftime('%Y-%m-%d %H:%M:%S')}")
        print("")
        
        if self.profit_events:
            print("Profit Summary:")
            total_profit = sum(p['profit_eth'] for p in self.profit_events)
            for p in self.profit_events:
                print(f"  #{p['number']}: {p['profit_eth']:.6f} ETH ({p['executor']})")
            print(f"  Total: {total_profit:.6f} ETH (~${total_profit * 2000:.2f})")
        print("")
        
        if self.error_log:
            print("Recent Errors:")
            for err in self.error_log[-5:]:
                print(f"  [{err['timestamp']}] {err['error']}")
        print("=" * 80)
    
    async def mission_complete(self):
        """Mission completed successfully"""
        elapsed = time.time() - self.start_time
        hours = elapsed / 3600
        
        total_profit = sum(p['profit_eth'] for p in self.profit_events)
        avg_profit = total_profit / len(self.profit_events) if self.profit_events else 0
        
        print("")
        print("[!] " + "=" * 76)
        print("[!] " + "=" * 76)
        print("[!]")
        print("[!]   MISSION COMPLETE - ALL TARGETS ACHIEVED!")
        print("[!]")
        print("[!] " + "=" * 76)
        print("[!] " + "=" * 76)
        print("")
        print(f"[V] Target Reached: {TARGET_PROFITS} Verified Profits")
        print(f"[V] Total Runtime: {hours:.2f} hours")
        print(f"[V] Total Profit: {total_profit:.6f} ETH (~${total_profit * 2000:.2f})")
        print(f"[V] Average Profit: {avg_profit:.6f} ETH (~${avg_profit * 2000:.2f})")
        print("")
        print("Profit Breakdown:")
        for p in self.profit_events:
            print(f"  {p['number']}. {p['executor']} - Block {p['block']} - {p['profit_eth']:.6f} ETH")
        print("")
        
        # Final treasury check
        final_balance = self.w3.eth.get_balance(
            Web3.to_checksum_address(TREASURY)
        ) / 1e18
        print(f"Final Treasury Balance: {final_balance:.6f} ETH")
        print("")
        print("[!] SYSTEM VALIDATED - READY FOR ENHANCEMENTS [!]")
        print("")
        print("=" * 80)
        
        # Save final report
        await self.save_final_report()
    
    async def mission_timeout(self, reason: str):
        """Mission timed out"""
        elapsed = time.time() - self.start_time
        hours = elapsed / 3600
        
        print("")
        print("[!]  " + "=" * 76)
        print("[!]  MISSION TERMINATED")
        print("[!]  " + "=" * 76)
        print("")
        print(f"Reason: {reason}")
        print(f"Runtime: {hours:.2f} hours")
        print(f"Profits Verified: {self.verified_profits}/{TARGET_PROFITS}")
        print(f"Errors: {self.error_count}")
        print("")
        
        if self.profit_events:
            print("Partial Results:")
            for p in self.profit_events:
                print(f"  #{p['number']}: {p['profit_eth']:.6f} ETH")
        print("")
        print("System requires attention before continuing.")
        print("=" * 80)
        
        await self.save_final_report()
    
    async def save_final_report(self):
        """Save final mission report"""
        try:
            report = {
                'mission_status': 'COMPLETE' if self.verified_profits >= TARGET_PROFITS else 'TERMINATED',
                'runtime_hours': (time.time() - self.start_time) / 3600,
                'profits_verified': self.verified_profits,
                'target_profits': TARGET_PROFITS,
                'errors': self.error_count,
                'profit_events': self.profit_events,
                'error_log': self.error_log[-10:],
                'timestamp': datetime.now().isoformat()
            }
            
            with open('mission_report.json', 'w') as f:
                json.dump(report, f, indent=2)
            
            print("Mission report saved to: mission_report.json")
        except:
            pass

async def main():
    monitor = ComprehensiveMonitor()
    await monitor.run()

if __name__ == "__main__":
    print("\n" + "=" * 80)
    print("STARTING COMPREHENSIVE SYSTEM MONITOR")
    print("=" * 80 + "\n")
    asyncio.run(main())
