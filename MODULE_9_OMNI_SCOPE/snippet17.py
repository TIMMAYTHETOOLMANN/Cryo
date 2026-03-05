#!/usr/bin/env python3
"""
Production Deployment Script - Sets up CryoSUPER for live mainnet operation
"""

import asyncio
import json
from typing import Dict
import os

class ProductionDeployer:
    """Deploys and configures CryoSUPER for production"""
    
    def __init__(self):
        self.config = self._load_production_config()
        self.blockchain_setup = BlockchainSetup()
        self.wallet_manager = WalletManager()
    
    async def deploy_full_system(self):
        """Deploy complete CryoSUPER production system"""
        
        print("🚀 Deploying CryoSUPER Production System...")
        
        # Step 1: Deploy smart contracts
        print("📦 Deploying smart contracts...")
        contract_addresses = await self._deploy_smart_contracts()
        
        # Step 2: Fund executor wallet
        print("💰 Funding executor wallet...")
        await self._fund_executor_wallet()
        
        # Step 3: Configure RPC endpoints
        print("🔌 Configuring RPC gateway...")
        await self._configure_rpc_gateway()
        
        # Step 4: Initialize monitoring systems
        print("👁️ Initializing monitoring systems...")
        await self._initialize_monitoring()
        
        # Step 5: Start production orchestrator
        print("⚡ Starting production orchestrator...")
        await self._start_production_orchestrator()
        
        print("✅ CryoSUPER Production System deployed successfully!")
        print("📊 Monitor profits: python continuous_profit_monitor.py")
        print("🚨 Safety: Execution currently DISABLED - Enable in config when ready")
    
    async def _deploy_smart_contracts(self) -> Dict[str, str]:
        """Deploy required smart contracts to all chains"""
        
        contracts_to_deploy = {
            'CollateralHealthMonitor': None,  # View-only, no deployment needed
            'IncentiveFeasibilityCalculator': await self._get_oracle_addresses(),
            'RiskMitigationExecutor': {
                'owner': self.config['executor_wallet'],
                'trusted_flash_loan_providers': self._get_flash_loan_providers()
            }
        }
        
        deployed_addresses = {}
        
        for chain_id in self.config['chains']:
            chain_addresses = {}
            for contract_name, constructor_args in contracts_to_deploy.items():
                address = await self._deploy_contract(chain_id, contract_name, constructor_args)
                chain_addresses[contract_name] = address
            
            deployed_addresses[chain_id] = chain_addresses
        
        return deployed_addresses
    
    async def _fund_executor_wallet(self):
        """Fund executor wallet with initial gas capital"""
        
        # Transfer 0.5 ETH to executor for gas
        funding_amount = int(0.5 * 10**18)  # 0.5 ETH in wei
        
        await self.wallet_manager.transfer_eth(
            from_wallet='deployer',
            to_wallet='executor', 
            amount=funding_amount,
            chain_id='ethereum'
        )
        
        print(f"✅ Funded executor with 0.5 ETH for gas")

# Configuration file
PRODUCTION_CONFIG = {
    'execution_enabled': False,  # SAFETY: Enable after testing
    'min_profit_threshold_usd': 20,
    'chains': ['ethereum', 'arbitrum', 'optimism', 'polygon'],
    'rpc_providers': {
        'ethereum': ['alchemy', 'infura', 'quicknode'],
        'arbitrum': ['alchemy', 'infura'],
        'optimism': ['alchemy', 'infura'],
        'polygon': ['alchemy', 'infura']
    },
    'flash_loan_providers': ['aave', 'balancer', 'uniswap', 'dydx'],
    'mev_protection': ['flashbots', 'bloxroute', 'eden'],
    'gas_price_cap_gwei': 50
}

if __name__ == "__main__":
    deployer = ProductionDeployer()
    asyncio.run(deployer.deploy_full_system())
