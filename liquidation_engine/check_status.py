#!/usr/bin/env python3
"""Quick status check of the monitor"""

from web3 import Web3

RPC = "https://eth-mainnet.g.alchemy.com/v2/Rc0sle99H0nN5Sm3C3JyfeAmqYz4hmlu"
V1 = "0x76dF81F4E3F6058DD6a9c0a6ed2f224b49375B9f"
V2 = "0xFf11E1d641f2ED7aD98629F04d1e103Ff6B44890"
TREASURY = "0xB323C6E32C6efe28FB9cfB1A83F4071c544eA1e4"

w3 = Web3(Web3.HTTPProvider(RPC))
block = w3.eth.block_number

print("=" * 60)
print("MONITOR STATUS CHECK")
print("=" * 60)
print(f"Current Block: {block}")
print(f"Treasury: {w3.eth.get_balance(TREASURY) / 1e18:.6f} ETH")

# Check for any liquidation events ever on these contracts
EVENT_SIG = w3.keccak(text="LiquidationExecuted(address,address,address,uint256,uint256,uint256)").hex()

logs1 = w3.eth.get_logs({'address': V1, 'fromBlock': 24552642, 'toBlock': block, 'topics': [EVENT_SIG]})
logs2 = w3.eth.get_logs({'address': V2, 'fromBlock': 24552642, 'toBlock': block, 'topics': [EVENT_SIG]})

print(f"\nV1 Events (since deployment): {len(logs1)}")
print(f"V2 Events (since deployment): {len(logs2)}")

if len(logs1) > 0 or len(logs2) > 0:
    print("\n[!] LIQUIDATION EVENTS FOUND!")
    for log in logs1 + logs2:
        print(f"  Block {log['blockNumber']}: {log['address'][:20]}...")
else:
    print("\nNo liquidation events yet - monitor is waiting...")

print("\nMonitor should be scanning every 10 seconds.")
print("Check tasklist for python.exe process.")
print("=" * 60)
