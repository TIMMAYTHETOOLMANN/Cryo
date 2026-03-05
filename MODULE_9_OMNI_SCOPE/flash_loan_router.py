# ... existing code ...

class UniversalFlashLoanRouter:
    """Enhanced router serving ALL profit modules with intelligent fallbacks"""
    
    def __init__(self):
        self.provider_registry = DynamicProviderRegistry()
        self.fee_minimizer = FeeOptimizer()
        self.fallback_manager = MultiProviderFallback()
    
    async def get_optimal_flash_loan(self, asset, amount, strategy_type):
        """Get optimal flash loan for ANY strategy type"""
        
        providers = await self.provider_registry.get_available_providers(
            asset, amount, strategy_type
        )
        
        # Your enhanced provider selection logic
        optimal_provider = await self._select_optimal_provider(providers, strategy_type)
        
        # Zero-fee exploitation for maximum profit
        if await self.fee_minimizer.can_exploit_zero_fee(optimal_provider):
            return await self._setup_zero_fee_execution(optimal_provider, asset, amount)
        
        return optimal_provider
    
    async def _select_optimal_provider(self, providers, strategy_type):
        """Intelligent provider selection based on strategy requirements"""
        
        if strategy_type == "liquidation":
            # Prioritize speed and reliability
            return min(providers, key=lambda p: p.success_rate * p.speed_score)
        elif strategy_type == "arbitrage":
            # Prioritize lowest fees
            return min(providers, key=lambda p: p.total_fee)
        elif strategy_type == "cross_chain":
            # Prioritize chain compatibility
            return max(providers, key=lambda p: p.cross_chain_support)
