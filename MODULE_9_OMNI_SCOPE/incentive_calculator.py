"""
Enhanced Incentive Feasibility Calculator - Real-time Profit Calculation
Integrated with gas oracle and flash loan optimization
"""

import asyncio
from typing import Dict

class EnhancedIncentiveCalculator:
    """Production profit calculator with real-time data"""
    
    def __init__(self, gas_oracle, flash_loan_router, dex_aggregator):
        self.gas_oracle = gas_oracle
        self.flash_loan_router = flash_loan_router
        self.dex_aggregator = dex_aggregator
        self.profit_models = self._initialize_profit_models()
    
    async def calculate_incentive(self, position: Dict, chain_id: str, protocol: str) -> Dict[str, float]:
        """Calculate exact profit including all costs"""
        
        # Real-time gas cost estimation
        gas_cost = await self.gas_oracle.estimate_transaction_cost(
            chain_id, position['complexity']
        )
        
        # Flash loan fee calculation
        flash_loan_cost = await self.flash_loan_router.calculate_flash_loan_cost(
            position['debt_amount'], position['debt_asset'], chain_id
        )
        
        # Slippage estimation
        slippage_cost = await self._estimate_slippage_cost(position)
        
        # Gross liquidation profit
        gross_profit = position['liquidation_bonus'] * position['debt_amount']
        
        # Net profit calculation
        net_profit = gross_profit - gas_cost - flash_loan_cost - slippage_cost
        
        return {
            'gross_profit_usd': gross_profit,
            'gas_cost_usd': gas_cost,
            'flash_loan_cost_usd': flash_loan_cost,
            'slippage_cost_usd': slippage_cost,
            'net_profit_usd': net_profit,
            'profitability_ratio': net_profit / (gas_cost + 0.001),  # Avoid division by zero
            'viable': net_profit > self.min_profit_threshold
        }
    
    async def quick_check(self, position: Dict) -> bool:
        """Quick profitability pre-screening"""
        
        # Fast estimation without detailed calculation
        if position['health_factor'] > 1.05:  # Too healthy
            return False
        
        if position['debt_amount_usd'] < 1000:  # Too small
            return False
        
        # Quick gas check
        gas_estimate = await self.gas_oracle.get_current_gas_price(chain_id)
        if gas_estimate > self.gas_price_cap:
            return False
        
        return True
