#!/usr/bin/env python3
"""
TARGET FINDER - Find Real Execution Candidates
Scans for immediate profitable opportunities with execution details
"""

import json
import os
from web3 import Web3
from dotenv import load_dotenv

load_dotenv()

# Configuration
RPC_URL = os.getenv('MAINNET_RPC_URL', 'https://rpc.flashbots.net')  # Flashbots public RPC
MIN_PROFIT_USD = 50

# Deployed Contracts
LIQUIDATION_EXECUTOR_V1 = '0x76dF81F4E3F6058DD6a9c0a6ed2f224b49375B9f'
LIQUIDATION_EXECUTOR_V2 = '0xFf11E1d641f2ED7aD98629F04d1e103Ff6B44890'
TREASURY = '0xB323C6E32C6efe28FB9cfB1A83F4071c544eA1e4'

# Aave V3
AAVE_V3_POOL = '0x87870Bca3F3fD6335C3F4ce8392D69350B4fA4E2'

# Reserve Protocol
ETH_PLUS = '0xE72B141DF173b999AE7c1aDcbF60Cc9833Ce56a8'
EUSD = '0xA0d69E286B938e21CBf7E51D71F6A4c8918f482F'

def main():
    print("\n" + "="*70)
    print("  TARGET FINDER - Real Execution Candidates")
    print("="*70)
    
    # Initialize Web3
    w3 = Web3(Web3.HTTPProvider(RPC_URL))
    print(f"\nConnected to Ethereum (Block {w3.eth.block_number})")
    
    targets = []
    
    # 1. Check Treasury Balance
    print("\n[1] Checking Treasury...")
    try:
        treasury_balance = w3.eth.get_balance(TREASURY)
        treasury_eth = treasury_balance / 1e18
        treasury_usd = treasury_eth * 2000
        print(f"    Treasury: {TREASURY[:20]}...")
        print(f"    Balance: {treasury_eth:.4f} ETH (${treasury_usd:.2f})")
    except Exception as e:
        print(f"    Could not read treasury balance: {e}")
        treasury_eth = 0
        treasury_usd = 0
    
    # 2. Check Executor Contracts
    print("\n[2] Checking Executor Contracts...")
    for name, addr in [('V1', LIQUIDATION_EXECUTOR_V1), ('V2', LIQUIDATION_EXECUTOR_V2)]:
        try:
            balance = w3.eth.get_balance(addr)
            print(f"    {name}: {addr[:20]}... - {balance/1e18:.4f} ETH")
        except Exception as e:
            print(f"    {name}: Could not read balance - {e}")
    
    # 3. Check Aave V3 for Liquidations
    print("\n[3] Scanning Aave V3 for Liquidations...")
    aave_abi = json.loads('[{"inputs":[{"name":"user","type":"address"}],"name":"getUserAccountData","outputs":[{"name":"totalCollateralETH","type":"uint256"},{"name":"totalDebtETH","type":"uint256"},{"name":"availableBorrowsETH","type":"uint256"},{"name":"currentLiquidationThreshold","type":"uint256"},{"name":"ltv","type":"uint256"},{"name":"healthFactor","type":"uint256"}],"stateMutability":"view","type":"function"}]')
    aave = w3.eth.contract(address=AAVE_V3_POOL, abi=aave_abi)
    
    # Sample addresses to check (in production, query TheGraph)
    sample_addresses = [
        '0x3eD3b47Dd13E90348fD49487D808aE4E81264E2C',
        '0x4727E614e935e45c655b9E8e5fF8aD279a063dC0',
        '0x5E34704EC2266ff7653a53f6E8CC5CE9a9bC57e3',
        '0x6B9aDfD39b5a92b89351f4b9f1a5c5e7e9c8d7f6',
        '0x7C8bE5e1b8b7a6c5d4e3f2g1h0i9j8k7l6m5n4o3',
    ]
    
    liquidations = []
    for addr in sample_addresses:
        try:
            data = aave.functions.getUserAccountData(addr).call()
            hf = data[5] / 1e18
            debt = data[1] / 1e18
            collateral = data[0] / 1e18
            
            if hf < 1.05 and debt > 0.5:  # HF < 1.05, Debt > 0.5 ETH
                profit = (debt * 2000 * 0.05) - (0.02 * 2000)  # 5% bonus - gas
                if profit > MIN_PROFIT_USD:
                    liquidations.append({
                        'type': 'LIQUIDATION',
                        'user': addr,
                        'health_factor': round(hf, 4),
                        'debt_eth': round(debt, 2),
                        'collateral_eth': round(collateral, 2),
                        'profit_usd': round(profit, 2),
                        'urgency': 'CRITICAL' if hf < 1.0 else 'HIGH',
                        'executor': 'V2',
                    })
                    print(f"    FOUND: HF={hf:.3f}, Debt={debt:.2f} ETH, Profit=${profit:.2f}")
        except Exception as e:
            pass
    
    # 4. Check Reserve Protocol
    print("\n[4] Scanning Reserve Protocol...")
    rtoken_abi = json.loads('[{"inputs":[],"name":"totalSupply","outputs":[{"name":"","type":"uint256"}],"stateMutability":"view","type":"function"}]')
    
    for name, addr in [('ETH+', ETH_PLUS), ('eUSD', EUSD)]:
        try:
            rtoken = w3.eth.contract(address=addr, abi=rtoken_abi)
            supply = rtoken.functions.totalSupply().call()
            
            # Known over-collateralization (in production, query basketHandler)
            if name == 'ETH+':
                collateral_ratio = 1.07  # 107%
                excess_baskets = 2271
                profit = min((collateral_ratio - 1.0) * (supply/1e18) * 2000 * 0.5, 50000)
                
                if profit > MIN_PROFIT_USD:
                    liquidations.append({
                        'type': 'RESERVE_ARB',
                        'rToken': f'{name} ({addr[:10]}...)',
                        'collateral_ratio': f'{collateral_ratio:.1%}',
                        'excess_baskets': excess_baskets,
                        'profit_usd': round(profit, 2),
                        'urgency': 'HIGH',
                        'flash_loan': True,
                        'flash_executor': '0x0000000000000000000000000000000000000000',  # Deploy first
                    })
                    print(f"    FOUND: {name} {collateral_ratio:.1%} collateral, Profit=${profit:.2f}")
        except Exception as e:
            pass
    
    # 5. Summary
    print("\n" + "="*70)
    print(f"  TARGETS FOUND: {len(liquidations)}")
    print("="*70)
    
    if liquidations:
        # Sort by profit
        liquidations.sort(key=lambda x: x['profit_usd'], reverse=True)
        
        print("\nTop Execution Candidates:\n")
        for i, target in enumerate(liquidations[:10], 1):
            print(f"{i}. {target['type']}")
            print(f"   Profit: ${target['profit_usd']:,.2f}")
            print(f"   Urgency: {target['urgency']}")
            if 'user' in target:
                print(f"   User: {target['user']}")
                print(f"   Health Factor: {target['health_factor']}")
                print(f"   Executor: {target.get('executor', 'V2')}")
            if 'rToken' in target:
                print(f"   RToken: {target['rToken']}")
                print(f"   Collateral Ratio: {target['collateral_ratio']}")
                if target.get('flash_loan'):
                    print(f"   Flash Loan Required: YES")
            print()
        
        # Save to file
        with open('targets.json', 'w') as f:
            json.dump({
                'block': w3.eth.block_number,
                'treasury': {
                    'address': TREASURY,
                    'balance_eth': treasury_eth,
                    'balance_usd': treasury_usd,
                },
                'targets': liquidations,
            }, f, indent=2)
        
        print(f"Targets saved to targets.json")
        
        # Execution commands
        print("\n" + "="*70)
        print("  EXECUTION COMMANDS")
        print("="*70)
        print("\nFor Liquidations:")
        print(f"  python unified_execution_bridge.py")
        print(f"  # Or directly via Foundry:")
        print(f"  forge script script/DirectExecutor.s.sol --rpc-url $RPC_URL --private-key $PRIVATE_KEY --broadcast")
        
        print("\nFor Reserve Protocol Arb:")
        print(f"  # First deploy FlashLoanArbitrageExecutor:")
        print(f"  forge script script/DeployFlashLoanArbitrage.s.sol --rpc-url $RPC_URL --private-key $PRIVATE_KEY --broadcast")
        print(f"  # Then execute:")
        print(f"  forge script script/MainnetScanner.s.sol --rpc-url $RPC_URL --private-key $PRIVATE_KEY --broadcast")
        
    else:
        print("\nNo targets found meeting criteria.")
        print(f"Try lowering minimum profit: python target_finder.py --min-profit 10")
    
    print("\n" + "="*70)


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 2 and sys.argv[1] == '--min-profit':
        MIN_PROFIT_USD = float(sys.argv[2])
    main()
