"""
Profit Threshold Manager - Capital-Optimized
Dynamically adjusts thresholds based on available gas
"""

class CapitalAwareProfitManager:
    """Manage profit thresholds based on available capital"""
    
    def __init__(self):
        self.base_thresholds = {
            'ethereum': 50,  # Higher due to gas costs
            'base': 15,      # Lower due to cheaper execution
            'arbitrum': 10,   # If we get gas, lower threshold
            'optimism': 10,   # If we get gas, lower threshold
            'polygon': 8      # If we get gas, lowest threshold
        }
        
        self.capital_multipliers = {
            'high': 0.5,    # When capital > $100
            'medium': 1.0,  # When capital $25-$100
            'low': 2.0      # When capital < $25 (current)
        }
    
    async def get_dynamic_threshold(self, chain_id: str, total_capital_usd: float) -> float:
        """Get dynamic profit threshold based on available capital"""
        
        base_threshold = self.base_thresholds.get(chain_id, 20)
        
        # Adjust based on total capital
        if total_capital_usd > 100:
            multiplier = self.capital_multipliers['high']
        elif total_capital_usd > 25:
            multiplier = self.capital_multipliers['medium']
        else:
            multiplier = self.capital_multipliers['low']
        
        # Current situation: $24.37 → low multiplier (2.0)
        dynamic_threshold = base_threshold * multiplier
        
        # Ensure minimum threshold for viability
        return max(dynamic_threshold, 10)  # Never below $10

# Update the main profit engine
class EnhancedProfitEngine:
    """Enhanced with capital-aware thresholds"""
    
    async def assess_opportunity(self, opportunity: Dict) -> bool:
        """Assess if opportunity meets capital-optimized thresholds"""
        
        # Get dynamic threshold based on current capital
        threshold = await self.threshold_manager.get_dynamic_threshold(
            opportunity['chain_id'], 
            self.capital_manager.total_capacity_usd
        )
        
        # Only proceed if profit exceeds threshold AND we have gas
        gas_viable = await self.gas_manager.check_gas_viability(
            opportunity['chain_id'], opportunity['complexity']
        )
        
        return opportunity['net_profit_usd'] > threshold and gas_viable['viable']
