# ... existing code ...

class CapitalOptimizedGasManager:
    """Optimize gas usage for $24.37 total capital"""
    
    def __init__(self):
        self.wallet_balances = {
            'ethereum': 0.00798,  # ETH
            'base': 0.00309,      # ETH
            'arbitrum': 0,
            'optimism': 0,
            'polygon': 0
        }
        
        self.eth_price_usd = 2200  # Current ETH price
        self.total_capacity_usd = 24.37
        
    async def allocate_gas_capital(self):
        """Optimally allocate limited capital across chains"""
        
        # Strategy: Focus on chains with existing ETH balances first
        allocation_strategy = {
            'ethereum': {
                'gas_balance': 0.00798,
                'priority': 'high',  # Highest profitability
                'min_gas_balance': 0.001,  # Keep minimum for emergencies
                'target_utilization': 0.00698  # Use ~87% of balance
            },
            'base': {
                'gas_balance': 0.00309,
                'priority': 'high',
                'min_gas_balance': 0.0005,
                'target_utilization': 0.00259  # Use ~84% of balance
            },
            'arbitrum': {
                'gas_balance': 0,
                'priority': 'medium',
                'min_gas_balance': 0,
                'target_utilization': 0  # No capital allocated
            }
        }
        
        return allocation_strategy
    
    async def optimize_transaction_gas(self, chain_id: str, tx_complexity: str) -> Dict:
        """Optimize gas for transaction based on available capital"""
        
        if chain_id == 'ethereum':
            # Ethereum is expensive - ultra optimization
            return await self._ultra_optimize_ethereum_gas(tx_complexity)
        elif chain_id == 'base':
            # Base is cheaper - aggressive optimization
            return await self._optimize_base_gas(tx_complexity)
        else:
            # Skip chains without gas allocation
            return {'viable': False}
    
    async def _ultra_optimize_ethereum_gas(self, complexity: str) -> Dict:
        """Ultra-optimization for Ethereum with limited capital"""
        
        # Base gas prices (current average)
        base_gas_prices = {
            'simple': {'gas_limit': 120000, 'priority_fee': 2},
            'medium': {'gas_limit': 250000, 'priority_fee': 3},
            'complex': {'gas_limit': 500000, 'priority_fee': 5}
        }
        
        gas_params = base_gas_prices.get(complexity, base_gas_prices['medium'])
        
        # Ultra-optimized gas estimation
        estimated_cost_eth = (gas_params['gas_limit'] * (15 + gas_params['priority_fee'])) / 10**9
        
        # Check if we can afford this transaction
        available_eth = self.wallet_balances['ethereum']
        if estimated_cost_eth > available_eth * 0.8:  # Use max 80% of balance
            return {'viable': False, 'reason': 'Insufficient gas'}
        
        return {
            'viable': True,
            'gas_limit': int(gas_params['gas_limit'] * 0.9),  # Conservative estimate
            'max_priority_fee': gas_params['priority_fee'],
            'max_fee_per_gas': 25,  # Cap at 25 gwei
            'estimated_cost_eth': estimated_cost_eth,
            'estimated_cost_usd': estimated_cost_eth * self.eth_price_usd
        }
