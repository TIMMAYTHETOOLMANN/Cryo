#!/usr/bin/env python3
"""
SIMPLE MONITOR - Tests basic functionality first
"""

from web3 import Web3
import time

# Config
RPC_URL = "https://eth-mainnet.g.alchemy.com/v2/Rc0sle99H0nN5Sm3C3JyfeAmqYz4hmlu"
EXECUTORS = {
    'V1': '0x76dF81F4E3F6058DD6a9c0a6ed2f224b49375B9f',
    'V2': '0xFf11E1d641f2ED7aD98629F04d1e103Ff6B44890',
}
TREASURY = '0xB323C6E32C6efe28FB9cfB1A83F4071c544eA1e4'

print("=" * 70)
print("SIMPLE MONITOR - TESTING CONNECTION")
print("=" * 70)

# Test connection
print("\n[1] Testing RPC connection...")
w3 = Web3(Web3.HTTPProvider(RPC_URL))
try:
    block = w3.eth.block_number
    print(f"    SUCCESS: Connected to block {block}")
except Exception as e:
    print(f"    FAILED: {e}")
    exit(1)

# Test treasury
print("\n[2] Checking treasury...")
try:
    balance = w3.eth.get_balance(TREASURY) / 1e18
    print(f"    Treasury: {balance:.6f} ETH")
except Exception as e:
    print(f"    FAILED: {e}")
    exit(1)

# Test contract existence
print("\n[3] Checking executor contracts...")
for name, addr in EXECUTORS.items():
    try:
        code = w3.eth.get_code(addr)
        if code:
            print(f"    {name}: EXISTS at {addr[:20]}...")
        else:
            print(f"    {name}: NO CODE - Contract not deployed!")
    except Exception as e:
        print(f"    {name}: ERROR - {e}")

# Test event scanning
print("\n[4] Testing event scanning...")
event_signature = w3.keccak(text="LiquidationExecuted(address,address,address,uint256,uint256,uint256)").hex()
print(f"    Event signature: {event_signature[:20]}...")

for name, addr in EXECUTORS.items():
    try:
        logs = w3.eth.get_logs({
            'address': addr,
            'fromBlock': block - 100,
            'toBlock': block,
            'topics': [event_signature]
        })
        print(f"    {name}: Found {len(logs)} events in last 100 blocks")
    except Exception as e:
        print(f"    {name}: ERROR - {e}")

# Start monitoring loop
print("\n[5] Starting monitoring loop...")
print("=" * 70)
print("Monitoring every 10 seconds. Press Ctrl+C to stop.")
print("=" * 70)

last_block = block
profits_found = 0
start_time = time.time()

try:
    while True:
        current_block = w3.eth.block_number
        
        # Check for new blocks
        if current_block > last_block:
            print(f"\n[{time.strftime('%H:%M:%S')}] Block: {current_block} | Profits: {profits_found}/5")
            
            # Scan for events
            for name, addr in EXECUTORS.items():
                try:
                    logs = w3.eth.get_logs({
                        'address': addr,
                        'fromBlock': last_block,
                        'toBlock': current_block,
                        'topics': [event_signature]
                    })
                    
                    for log in logs:
                        profits_found += 1
                        print(f"    [!] PROFIT #{profits_found} on {name} at block {log['blockNumber']}")
                        print(f"        Topics: {len(log['topics'])} topics")
                        print(f"        Data: {log['data'][:66]}...")
                        
                        if profits_found >= 5:
                            print("\n" + "=" * 70)
                            print("MISSION COMPLETE - 5 PROFITS VERIFIED!")
                            print("=" * 70)
                            exit(0)
                            
                except Exception as e:
                    print(f"    [X] Error scanning {name}: {e}")
            
            last_block = current_block
        
        time.sleep(10)
        
except KeyboardInterrupt:
    print("\n\n[X] Monitoring stopped by user")
    print(f"Total profits found: {profits_found}/5")
    exit(0)
