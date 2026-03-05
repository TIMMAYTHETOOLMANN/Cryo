# ... existing code ...

class EnhancedGasOptimizer:
    """Enhanced with gas token minting and refund optimization"""
    
    def __init__(self):
        self.gas_token_manager = GasTokenManager()
        self.gas_price_predictor = GasPricePredictor()
        self.gas_relay_integration = GasRelayService()
    
    async def optimize_transaction_gas(self, transaction_data, strategy_type):
        """Enhanced gas optimization with multiple strategies"""
        
        gas_optimization_strategies = []
        
        # Strategy 1: Gas token burning
        if await self.gas_token_manager.can_use_gas_tokens(transaction_data.chain_id):
            gas_savings = await self.gas_token_manager.calculate_gas_savings(
                transaction_data.gas_estimate
            )
            gas_optimization_strategies.append({
                'strategy': 'gas_token_burn',
                'savings': gas_savings,
                'priority': 1
            })
        
        # Strategy 2: Gas relay service (pay with stablecoins)
        if await self.gas_relay_integration.is_available(transaction_data.chain_id):
            relay_cost = await self.gas_relay_integration.calculate_cost(
                transaction_data.gas_estimate
            )
            gas_optimization_strategies.append({
                'strategy': 'gas_relay',
                'savings': relay_cost.savings,
                'priority': 2
            })
        
        # Strategy 3: Optimal gas price prediction
        optimal_gas = await self.gas_price_predictor.get_optimal_gas_price(
            transaction_data.urgency
        )
        gas_optimization_strategies.append({
            'strategy': 'gas_price_optimization',
            'savings': optimal_gas.savings,
            'priority': 3
        })
        
        # Select best strategy
        best_strategy = max(gas_optimization_strategies, key=lambda x: x['savings'])
        
        return await self._apply_gas_strategy(transaction_data, best_strategy)

# ... existing code ...
