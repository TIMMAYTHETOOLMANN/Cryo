# ... existing code ...

class CapitalOptimizedLauncher:
    """Launch system optimized for $24.37 capital allocation"""
    
    def __init__(self):
        self.gas_optimizer = CapitalOptimizedGasManager()
        self.focus_chains = ['ethereum', 'base']  # Only chains with gas
        self.profit_thresholds = {
            'ethereum': 50,  # Higher threshold for expensive chain
            'base': 15,      # Lower threshold for cheaper chain
            'arbitrum': 0,   # Disabled - no gas
            'optimism': 0,   # Disabled - no gas
            'polygon': 0     # Disabled - no gas
        }
    
    async def start_capital_optimized_system(self):
        """Start system optimized for available capital"""
        
        # Allocate gas capital
        gas_allocation = await self.gas_optimizer.allocate_gas_capital()
        
        # Start only chains with available gas
        active_chains = []
        for chain_id in self.focus_chains:
            if gas_allocation[chain_id]['gas_balance'] > 0:
                await self._start_chain_monitoring(chain_id, gas_allocation[chain_id])
                active_chains.append(chain_id)
        
        print(f"🚀 Started monitoring on chains: {', '.join(active_chains)}")
        print(f"💰 Available capital: ${self.gas_optimizer.total_capacity_usd:.2f}")
        print(f"⛽ Gas allocation: {gas_allocation}")
        
        # Start unified monitoring
        await self._start_unified_monitoring(active_chains)
    
    async def _start_chain_monitoring(self, chain_id: str, allocation: Dict):
        """Start monitoring for specific chain with capital constraints"""
        
        # Adjust profit thresholds based on capital allocation
        adjusted_threshold = self.profit_thresholds[chain_id] * (
            allocation['target_utilization'] / allocation['gas_balance']
        )
        
        # Start chain-specific monitoring
        monitor_config = {
            'chain_id': chain_id,
            'profit_threshold_usd': max(adjusted_threshold, 10),  # Minimum $10
            'max_gas_price_gwei': 25 if chain_id == 'ethereum' else 15,
            'capital_allocation': allocation
        }
        
        await self._initialize_chain_monitor(monitor_config)

# ... existing code ...
