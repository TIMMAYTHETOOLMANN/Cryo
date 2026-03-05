"""
Unified Execution Router - Routes Opportunities to Optimal Executors
"""

class UnifiedOpportunityRouter:
    def __init__(self):
        self.execution_modules = {
            'liquidation': EnhancedLiquidationExecutor(),
            'arbitrage': TriangulatedArbitrageEngine(),
            'mev_capture': UniversalMEVProtector(),
            'cross_chain': EnhancedCrossChainOrchestrator(),
            'yield_optimization': YieldHyperSolver()
        }
    
    async def route_opportunity(self, ranked_opportunity):
        """Route opportunity to the optimal execution module"""
        
        strategy_type = ranked_opportunity['strategy_type']
        complexity = ranked_opportunity['quality_breakdown']['complexity']
        
        if strategy_type == 'liquidation':
            if complexity > 0.8:  # High complexity
                return await self.execution_modules['mev_capture'].execute(ranked_opportunity)
            else:
                return await self.execution_modules['liquidation'].execute(ranked_opportunity)
        
        elif strategy_type == 'arbitrage':
            if 'cross_chain' in ranked_opportunity['tags']:
                return await self.execution_modules['cross_chain'].execute(ranked_opportunity)
            else:
                return await self.execution_modules['arbitrage'].execute(ranked_opportunity)
        
        elif strategy_type == 'yield_optimization':
            return await self.execution_modules['yield_optimization'].execute(ranked_opportunity)
