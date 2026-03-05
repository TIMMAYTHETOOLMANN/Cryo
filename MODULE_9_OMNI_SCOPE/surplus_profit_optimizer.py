"""
Flash Loan Surplus Utilization - Multi-Objective Profit Multiplier
Integrated with existing flash loan router for zero additional gas cost
"""

class SurplusProfitMaximizer:
    def __init__(self):
        self.secondary_strategies = {
            'quick_arbitrage': QuickArbitrageExecutor(),
            'yield_farming': YieldFarmingOptimizer(),
            'micro_liquidation': MicroLiquidationFinder(),
            'gas_token_minting': GasTokenMinter()
        }
    
    async def optimize_surplus_utilization(self, base_transaction, surplus_amount):
        """Find optimal secondary strategy for unused flash loan surplus"""
        
        available_strategies = []
        
        for strategy_name, executor in self.secondary_strategies.items():
            # Evaluate each strategy with current surplus
            profit_potential = await executor.calculate_profit_potential(
                surplus_amount, base_transaction.chain_id
            )
            
            if profit_potential > 0:
                available_strategies.append({
                    'strategy': strategy_name,
                    'profit_potential': profit_potential,
                    'executor': executor,
                    'risk_score': await self._calculate_risk(strategy_name)
                })
        
        # Select strategy with highest profit/risk ratio
        if available_strategies:
            best_strategy = max(
                available_strategies, 
                key=lambda x: x['profit_potential'] / max(x['risk_score'], 0.1)
            )
            
            return await self._execute_combined_transaction(
                base_transaction, best_strategy, surplus_amount
            )
        
        return base_transaction

# ... existing code ...
