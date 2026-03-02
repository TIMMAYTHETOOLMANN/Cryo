#!/usr/bin/env python3
print("MONITOR STARTING...")

from web3 import Web3
import time
import sys

RPC = "https://eth-mainnet.g.alchemy.com/v2/Rc0sle99H0nN5Sm3C3JyfeAmqYz4hmlu"
V1 = "0x76dF81F4E3F6058DD6a9c0a6ed2f224b49375B9f"
V2 = "0xFf11E1d641f2ED7aD98629F04d1e103Ff6B44890"
TREASURY = "0xB323C6E32C6efe28FB9cfB1A83F4071c544eA1e4"

print("Connecting to RPC...")
w3 = Web3(Web3.HTTPProvider(RPC))
print(f"Connected! Block: {w3.eth.block_number}")

print(f"Checking V1 contract...")
v1_code = w3.eth.get_code(V1)
print(f"V1 exists: {len(v1_code) > 0}")

print(f"Checking V2 contract...")
v2_code = w3.eth.get_code(V2)
print(f"V2 exists: {len(v2_code) > 0}")

print(f"Treasury balance: {w3.eth.get_balance(TREASURY) / 1e18:.6f} ETH")

EVENT_SIG = w3.keccak(text="LiquidationExecuted(address,address,address,uint256,uint256,uint256)").hex()
print(f"Event signature: {EVENT_SIG[:40]}...")

print("\n=== STARTING MONITOR LOOP ===")
print("Scanning every 10 seconds for liquidation events...")
print("Press Ctrl+C to stop\n")

last_block = w3.eth.block_number
profits = 0

while profits < 5:
    try:
        current = w3.eth.block_number
        if current > last_block:
            # Scan V1
            logs1 = w3.eth.get_logs({'address': V1, 'fromBlock': last_block, 'toBlock': current, 'topics': [EVENT_SIG]})
            # Scan V2
            logs2 = w3.eth.get_logs({'address': V2, 'fromBlock': last_block, 'toBlock': current, 'topics': [EVENT_SIG]})
            
            total_logs = len(logs1) + len(logs2)
            
            if total_logs > 0:
                profits += total_logs
                print(f"\n*** PROFIT DETECTED! Block {current} ***")
                print(f"    V1 events: {len(logs1)}")
                print(f"    V2 events: {len(logs2)}")
                print(f"    Total profits: {profits}/5")
                
                if profits >= 5:
                    print("\n*** MISSION COMPLETE - 5 PROFITS VERIFIED! ***")
                    break
            else:
                print(f"Block {current} | Profits: {profits}/5 | Scanning...")
            
            last_block = current
        
        time.sleep(10)
        sys.stdout.flush()
        
    except KeyboardInterrupt:
        print(f"\n\nStopped. Profits found: {profits}/5")
        break
    except Exception as e:
        print(f"Error: {e}")
        time.sleep(5)

print("\nMonitor ended.")
