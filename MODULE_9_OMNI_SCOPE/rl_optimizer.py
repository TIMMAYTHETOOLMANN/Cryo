"""
Reinforcement Learning Optimizer - Self-Tuning Parameters
Integrated with ML ranker for continuous improvement
"""

class RLOptimizer:
    def __init__(self):
        self.q_learning_agent = QLearningAgent()
        self.parameter_space = self._define_parameter_space()
        self.reward_calculator = ProfitRewardCalculator()
    
    async def optimize_parameters(self, recent_performance_data):
        """Use RL to optimize system parameters based on performance"""
        
        # Calculate reward from recent performance
        reward = await self.reward_calculator.calculate_reward(recent_performance_data)
        
        # Update Q-values based on reward
        await self.q_learning_agent.update_q_values(reward)
        
        # Get new optimized parameters
        optimized_params = await self.q_learning_agent.get_optimal_parameters()
        
        # Apply to live system
        await self._update_live_parameters(optimized_params)
        
        return optimized_params
    
    def _define_parameter_space(self):
        """Define tunable parameters for optimization"""
        return {
            'min_profit_threshold': {'min': 0.001, 'max': 0.1, 'step': 0.001},
            'max_gas_price_gwei': {'min': 10, 'max': 200, 'step': 5},
            'slippage_tolerance': {'min': 0.001, 'max': 0.05, 'step': 0.001},
            'health_factor_threshold': {'min': 1.01, 'max': 1.1, 'step': 0.001}
        }
