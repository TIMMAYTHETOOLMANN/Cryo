"""
Enhanced Cross-Chain Orchestrator - Unified for ALL Profit Strategies
"""

class UniversalCrossChainOrchestrator:
    def __init__(self):
        self.light_client_manager = LightClientOrchestrator()
        self.batch_optimizer = BatchExecutionOptimizer()
        self.dynamic_fee_manager = CrossChainFeeOptimizer()
    
    async def execute_multi_chain_opportunity(self, opportunities):
        """Execute opportunities across multiple chains with intelligent batching"""
        
        # Group opportunities by chain for batch execution
        chain_groups = self._group_by_chain(opportunities)
        
        executed_opportunities = []
        
        for chain_id, chain_opportunities in chain_groups.items():
            if len(chain_opportunities) > 1:
                # Use your batched liquidation enhancement
                batched_result = await self._execute_batch_liquidations(
                    chain_id, chain_opportunities
                )
                executed_opportunities.extend(batched_result)
            else:
                # Single opportunity execution
                result = await self._execute_single_opportunity(
                    chain_id, chain_opportunities[0]
                )
                executed_opportunities.append(result)
        
        return executed_opportunities
    
    async def _execute_batch_liquidations(self, chain_id, opportunities):
        """Enhanced batch execution with gas optimization"""
        
        # Your batched flash loan logic
        total_debt = sum(opp.debt_amount for opp in opportunities)
        
        # Get optimal flash loan for batch
        flash_loan = await self.flash_loan_router.get_optimal_flash_loan(
            opportunities[0].debt_asset, total_debt, "batch_liquidation"
        )
        
        # Execute batch liquidation
        return await self._submit_batch_transaction(
            chain_id, opportunities, flash_loan
        )
