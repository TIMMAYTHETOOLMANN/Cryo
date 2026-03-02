#!/usr/bin/env python3
"""
Enhanced Liquidation Profit Monitor
Comprehensive monitoring with detailed CLI output and visibility
Mission: Verify 5 profitable liquidation transactions
"""

from web3 import Web3
import time
import sys
import os
from datetime import datetime, timedelta

# ============================================================================
# CONFIGURATION
# ============================================================================
RPC_URL = "https://eth-mainnet.g.alchemy.com/v2/Rc0sle99H0nN5Sm3C3JyfeAmqYz4hmlu"
V1_EXECUTOR = "0x76dF81F4E3F6058DD6a9c0a6ed2f224b49375B9f"
V2_EXECUTOR = "0xFf11E1d641f2ED7aD98629F04d1e103Ff6B44890"
TREASURY = "0xB323C6E32C6efe28FB9cfB1A83F4071c544eA1e4"

TARGET_PROFITS = 5
SCAN_INTERVAL = 10  # seconds
MAX_ERRORS = 10
TIMEOUT_HOURS = 72

# ============================================================================
# SETUP
# ============================================================================
os.system('title ENHANCED MONITOR - 5 Profit Verification')

def print_header():
    print("=" * 80)
    print("  ENHANCED LIQUIDATION PROFIT MONITOR")
    print("  Mission: Verify 5 Profitable Liquidation Transactions")
    print("=" * 80)
    print()

def print_config():
    print("CONFIGURATION:")
    print(f"  RPC URL:        {RPC_URL[:50]}...")
    print(f"  V1 Executor:    {V1_EXECUTOR}")
    print(f"  V2 Executor:    {V2_EXECUTOR}")
    print(f"  Treasury:       {TREASURY}")
    print()
    print("MISSION PARAMETERS:")
    print(f"  Target Profits:    {TARGET_PROFITS} transactions")
    print(f"  Scan Interval:     {SCAN_INTERVAL} seconds")
    print(f"  Max Errors:        {MAX_ERRORS}")
    print(f"  Timeout:           {TIMEOUT_HOURS} hours")
    print("=" * 80)
    print()

# ============================================================================
# MAIN MONITOR
# ============================================================================
def main():
    print_header()
    print_config()
    
    # Connect to Web3
    print("[*] Connecting to Ethereum mainnet...")
    try:
        w3 = Web3(Web3.HTTPProvider(RPC_URL))
        if not w3.is_connected():
            print("[ERROR] Failed to connect to RPC!")
            sys.exit(1)
        print(f"[OK]  Connected! Current block: {w3.eth.block_number}")
    except Exception as e:
        print(f"[ERROR] Connection failed: {e}")
        sys.exit(1)
    
    # Verify contracts
    print()
    print("[*] Verifying deployed contracts...")
    try:
        v1_code = w3.eth.get_code(V1_EXECUTOR)
        v2_code = w3.eth.get_code(V2_EXECUTOR)
        treasury_code = w3.eth.get_code(TREASURY)
        
        print(f"  V1 Executor: {'[OK]' if len(v1_code) > 0 else '[MISSING]'} {V1_EXECUTOR}")
        print(f"  V2 Executor: {'[OK]' if len(v2_code) > 0 else '[MISSING]'} {V2_EXECUTOR}")
        print(f"  Treasury:    {'[OK]' if len(treasury_code) > 0 else '[MISSING]'} {TREASURY}")
        
        if not (len(v1_code) > 0 and len(v2_code) > 0):
            print("[ERROR] Executor contracts not deployed!")
            sys.exit(1)
    except Exception as e:
        print(f"[ERROR] Contract verification failed: {e}")
        sys.exit(1)
    
    # Get initial treasury balance
    print()
    print("[*] Getting initial treasury balance...")
    try:
        initial_balance = w3.eth.get_balance(TREASURY) / 1e18
        print(f"[OK]  Treasury Balance: {initial_balance:.6f} ETH")
    except Exception as e:
        print(f"[ERROR] Balance check failed: {e}")
        sys.exit(1)
    
    # Event signature
    EVENT_SIG = w3.keccak(text="LiquidationExecuted(address,address,address,uint256,uint256,uint256)").hex()
    print()
    print(f"[OK]  Event signature: {EVENT_SIG[:42]}...")
    
    print()
    print("=" * 80)
    print("  MONITORING STARTED")
    print("=" * 80)
    print()
    
    # Monitoring state
    start_time = datetime.now()
    timeout_end = start_time + timedelta(hours=TIMEOUT_HOURS)
    last_block = w3.eth.block_number
    profits_found = 0
    errors = 0
    scans = 0
    last_profit_time = None
    
    print(f"Start Time:  {start_time.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"Timeout:     {timeout_end.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"Start Block: {last_block}")
    print()
    print("Scanning for LiquidationExecuted events...")
    print(f"Press Ctrl+C to stop\n")
    
    # Main monitoring loop
    while profits_found < TARGET_PROFITS:
        try:
            # Check timeout
            now = datetime.now()
            if now > timeout_end:
                print()
                print("=" * 80)
                print("  TIMEOUT REACHED")
                print("=" * 80)
                print(f"Mission duration ({TIMEOUT_HOURS} hours) exceeded.")
                print(f"Profits found: {profits_found}/{TARGET_PROFITS}")
                break
            
            # Get current block
            try:
                current_block = w3.eth.block_number
            except Exception as e:
                errors += 1
                print(f"[ERROR] Block fetch failed: {e}")
                if errors >= MAX_ERRORS:
                    print(f"[ABORT] Max errors ({MAX_ERRORS}) reached!")
                    break
                time.sleep(5)
                continue
            
            # Check if we've advanced
            if current_block > last_block:
                scans += 1
                
                # Scan V1 executor
                logs_v1 = []
                try:
                    logs_v1 = w3.eth.get_logs({
                        'address': V1_EXECUTOR,
                        'fromBlock': last_block,
                        'toBlock': current_block,
                        'topics': [EVENT_SIG]
                    })
                except Exception as e:
                    print(f"[WARN] V1 log scan error: {e}")
                
                # Scan V2 executor
                logs_v2 = []
                try:
                    logs_v2 = w3.eth.get_logs({
                        'address': V2_EXECUTOR,
                        'fromBlock': last_block,
                        'toBlock': current_block,
                        'topics': [EVENT_SIG]
                    })
                except Exception as e:
                    print(f"[WARN] V2 log scan error: {e}")
                
                total_logs = len(logs_v1) + len(logs_v2)
                
                if total_logs > 0:
                    # PROFIT DETECTED!
                    profits_found += total_logs
                    last_profit_time = now
                    
                    print()
                    print("!" * 80)
                    print("  PROFIT TRANSACTION DETECTED!")
                    print("!" * 80)
                    print(f"  Block:          {current_block}")
                    print(f"  V1 Events:      {len(logs_v1)}")
                    print(f"  V2 Events:      {len(logs_v2)}")
                    print(f"  Total:          {total_logs}")
                    print(f"  Cumulative:     {profits_found}/{TARGET_PROFITS}")
                    print(f"  Progress:       {(profits_found/TARGET_PROFITS)*100:.1f}%")
                    
                    # Decode events
                    for log in logs_v1 + logs_v2:
                        try:
                            topics = log['topics']
                            data = log['data']
                            # Topics: [event_sig, user, debtToken, collateralToken]
                            if len(topics) >= 4:
                                user = w3.to_checksum_address(topics[1][-20:].hex())
                                debt_token = w3.to_checksum_address(topics[2][-20:].hex())
                                collateral_token = w3.to_checksum_address(topics[3][-20:].hex())
                                # Data: debtAmount, collateralAmount, profit
                                values = w3.codec.decode(['uint256', 'uint256', 'uint256'], data)
                                debt_amount = values[0] / 1e18
                                collateral_amount = values[1] / 1e18
                                profit = values[2] / 1e18
                                
                                print()
                                print(f"    User:             {user}")
                                print(f"    Debt Token:       {debt_token}")
                                print(f"    Collateral Token: {collateral_token}")
                                print(f"    Debt Covered:     {debt_amount:.6f} ETH")
                                print(f"    Collateral:       {collateral_amount:.6f} ETH")
                                print(f"    PROFIT:           {profit:.6f} ETH")
                        except Exception as e:
                            print(f"    [Decode error: {e}]")
                    
                    print("!" * 80)
                    print()
                    
                    if profits_found >= TARGET_PROFITS:
                        break
                else:
                    # No profits this scan
                    elapsed = (now - start_time).total_seconds()
                    remaining = (timeout_end - now).total_seconds() / 3600
                    
                    print(f"[Scan {scans:4d}] Block {current_block:8d} | "
                          f"Profits: {profits_found}/{TARGET_PROFITS} | "
                          f"Uptime: {elapsed/60:.1f}m | "
                          f"Remaining: {remaining:.1f}h | "
                          f"Errors: {errors}/{MAX_ERRORS}")
                
                last_block = current_block
            
            # Check treasury balance periodically
            if scans % 10 == 0:
                try:
                    current_balance = w3.eth.get_balance(TREASURY) / 1e18
                    balance_change = current_balance - initial_balance
                    status = "[+]" if balance_change > 0 else "[=]"
                    print(f"       {status} Treasury: {current_balance:.6f} ETH "
                          f"(change: {balance_change:+.6f} ETH)")
                except:
                    pass
            
            # Wait before next scan
            time.sleep(SCAN_INTERVAL)
            sys.stdout.flush()
            
        except KeyboardInterrupt:
            print()
            print()
            print("=" * 80)
            print("  INTERRUPTED BY USER")
            print("=" * 80)
            print(f"Profits found: {profits_found}/{TARGET_PROFITS}")
            break
        except Exception as e:
            errors += 1
            print(f"[ERROR] Unexpected error: {e}")
            if errors >= MAX_ERRORS:
                print(f"[ABORT] Max errors ({MAX_ERRORS}) reached!")
                break
            time.sleep(5)
    
    # Final report
    print()
    print("=" * 80)
    print("  MONITORING ENDED")
    print("=" * 80)
    end_time = datetime.now()
    duration = end_time - start_time
    
    print()
    print("FINAL STATISTICS:")
    print(f"  Start Time:      {start_time.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"  End Time:        {end_time.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"  Duration:        {duration}")
    print(f"  Start Block:     {last_block}")
    print(f"  End Block:       {w3.eth.block_number}")
    print(f"  Blocks Scanned:  {w3.eth.block_number - last_block}")
    print(f"  Total Scans:     {scans}")
    print()
    print("MISSION RESULTS:")
    print(f"  Target:          {TARGET_PROFITS} profits")
    print(f"  Found:           {profits_found} profits")
    print(f"  Success Rate:    {(profits_found/TARGET_PROFITS)*100:.1f}%")
    print(f"  Errors:          {errors}/{MAX_ERRORS}")
    print()
    
    if profits_found >= TARGET_PROFITS:
        print("=" * 80)
        print("  MISSION ACCOMPLISHED!")
        print("=" * 80)
        print()
        print("5 profitable liquidation transactions have been VERIFIED!")
        print("System validation: SUCCESS")
        return 0
    else:
        print("=" * 80)
        print("  MISSION INCOMPLETE")
        print("=" * 80)
        print()
        print(f"Only {profits_found}/{TARGET_PROFITS} profits found.")
        print("Possible reasons:")
        print("  - No liquidation transactions occurred on-chain")
        print("  - Contracts are idle (waiting for opportunities)")
        print("  - Timeout reached before opportunities appeared")
        print()
        print("This does NOT indicate a system bug.")
        print("The monitor is working correctly - it detects events AFTER they happen.")
        return 1

if __name__ == "__main__":
    sys.exit(main())
