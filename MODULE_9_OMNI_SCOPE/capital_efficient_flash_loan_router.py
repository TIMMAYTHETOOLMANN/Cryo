# ... existing code ...

class CapitalEfficientFlashLoanRouter:
    """Optimized flash loan routing for limited capital"""
    
    def __init__(self):
        self.available_chains = ['ethereum', 'base']
        self.zero_capital_threshold = 0.001  # Minimum gas required
    
    async def get_optimal_flash_loan(self, position: Dict) -> Optional[Dict]:
        """Get optimal flash loan strategy for capital-constrained execution"""
        
        # Only execute if we have sufficient gas
        chain_id = position['chain_id']
        if chain_id not in self.available_chains:
            return None
        
        gas_viability = await self.gas_optimizer.check_gas_viability(chain_id, 'medium')
        if not gas_viability['viable']:
            return None
        
        # Prefer zero-fee flash loans
        zero_fee_providers = await self._get_zero_fee_providers(chain_id)
        if zero_fee_providers:
            return {
                'provider': zero_fee_providers[0],
                'fee_percentage': 0,
                'gas_cost_eth': gas_viability['estimated_cost_eth'],
                'total_cost_usd': gas_viability['estimated_cost_usd'],
                'strategy': 'zero_fee_flash_loan'
            }
        
        # Fallback to lowest-fee provider
        low_fee_provider = await self._get_lowest_fee_provider(chain_id)
        if low_fee_provider:
            return {
                'provider': low_fee_provider,
                'fee_percentage': await self._get_provider_fee(low_fee_provider),
                'gas_cost_eth': gas_viability['estimated_cost_eth'],
                'total_cost_usd': gas_viability['estimated_cost_usd'] + self._calculate_fee_cost(position),
                'strategy': 'low_fee_flash_loan'
            }
        
        return None

# ... existing code ...
