"""
Profitability Recheck - Last-Moment Validation
Integrated with existing profit calculator
"""

import asyncio
from typing import Dict

class ProfitabilityRecheck:
    """Your last-moment profit validation"""
    
    def __init__(self, dex_quotes, gas_oracle):
        self.dex = dex_quotes
        self.gas = gas_oracle
        
    async def should_execute(self, position: Dict, signed_tx: Dict) -> bool:
        """Re-check profitability at execution moment"""
        
        # Get real-time prices
        collateral_price = await self.dex.get_current_price(position['collateral_asset'])
        debt_price = 1.0  # Assuming stablecoin
        
        # Calculate profit with current data
        profit = await self._calculate_profit(position, collateral_price, debt_price)
        
        # Estimate gas cost
        gas_cost = await self._estimate_gas_cost(signed_tx)
        
        # Your buffer logic
        min_profit = gas_cost * 1.1  # 10% buffer
        
        return profit > min_profit
