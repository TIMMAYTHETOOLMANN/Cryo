"""
Execution Router
Execution module integrations and handoff

This module handles the handoff from opportunity detection to execution:
- Liquidation Engine integration
- Arbitrage execution
- Backrun bot integration
- Cross-chain execution
"""

from .execution_interface import ExecutionInterface, ExecutionRequest, ExecutionResult, ExecutionStatus
from .liquidation_executor import LiquidationExecutor
from .arbitrage_executor import ArbitrageExecutor
from .backrun_executor import BackrunExecutor
from .cross_chain_executor import CrossChainExecutor
from .execution_manager import ExecutionManager

__all__ = [
    # Interfaces
    'ExecutionInterface',
    'ExecutionRequest',
    'ExecutionResult',
    'ExecutionStatus',
    
    # Executors
    'LiquidationExecutor',
    'ArbitrageExecutor',
    'BackrunExecutor',
    'CrossChainExecutor',
    
    # Manager
    'ExecutionManager',
]
