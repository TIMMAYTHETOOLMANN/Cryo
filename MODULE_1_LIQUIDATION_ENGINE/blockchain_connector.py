#!/usr/bin/env python3
"""
CRYOSUPER PROFIT PROTOCOL - Main Execution Entry Point
"""

import os
from web3 import Web3
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Initialize Web3 connections for all supported chains
def init_web3_connections():
    """Initialize Web3 connections for all supported chains"""
    connections = {}
    
    # Ethereum Mainnet
    if os.getenv('MAINNET_RPC_URL'):
        connections['mainnet'] = Web3(Web3.HTTPProvider(os.getenv('MAINNET_RPC_URL')))
    
    # Base Chain
    if os.getenv('BASE_RPC_URL'):
        connections['base'] = Web3(Web3.HTTPProvider(os.getenv('BASE_RPC_URL')))
    
    # Arbitrum
    if os.getenv('ARBITRUM_RPC_URL'):
        connections['arbitrum'] = Web3(Web3.HTTPProvider(os.getenv('ARBITRUM_RPC_URL')))
    
    # Optimism
    if os.getenv('OPTIMISM_RPC_URL'):
        connections['optimism'] = Web3(Web3.HTTPProvider(os.getenv('OPTIMISM_RPC_URL')))
    
    # Polygon
    if os.getenv('POLYGON_RPC_URL'):
        connections['polygon'] = Web3(Web3.HTTPProvider(os.getenv('POLYGON_RPC_URL')))
    
    # Verify connections
    for chain_name, connection in connections.items():
        if connection.is_connected():
            print(f"✅ {chain_name.upper()} connection established")
        else:
            print(f"❌ {chain_name.upper()} connection failed")
    
    return connections

# Initialize Web3 connections
w3_connections = init_web3_connections()

# Make individual variables for convenience (optional)
w3_base = w3_connections.get('base')
w3_mainnet = w3_connections.get('mainnet')
w3_arbitrum = w3_connections.get('arbitrum')

# Rest of your main execution code
if __name__ == "__main__":
    print("💎 CRYOSUPER PROFIT PROTOCOL - INITIALIZING...")
    
    # Verify Base connection exists before proceeding
    if w3_base and w3_base.is_connected():
        print("🌉 Base chain connection ready")
        # Your main execution code here
    else:
        print("❌ Base chain connection not available - check BASE_RPC_URL in .env file")
        print("Please ensure you have:")
        print("1. A valid Base RPC URL in your .env file")
        print("2. The correct BASE_RPC_URL environment variable set")
        print("3. Internet connection to reach the RPC endpoint")
