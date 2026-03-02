#!/usr/bin/env python3
"""
Execution Interface
Abstract interface for all execution modules

Defines the standard interface that all executors must implement
"""

import asyncio
import time
from abc import ABC, abstractmethod
from typing import Dict, List, Optional, Any, Callable, Awaitable
from dataclasses import dataclass, field
from enum import Enum


class ExecutionStatus(Enum):
    """Execution status"""
    PENDING = "pending"
    SIMULATING = "simulating"
    SUBMITTING = "submitting"
    SUBMITTED = "submitted"
    CONFIRMING = "confirming"
    CONFIRMED = "confirmed"
    FAILED = "failed"
    REVERTED = "reverted"
    TIMEOUT = "timeout"


class ExecutionType(Enum):
    """Type of execution"""
    LIQUIDATION = "liquidation"
    ARBITRAGE = "arbitrage"
    BACKRUN = "backrun"
    SANDWICH = "sandwich"
    CROSS_CHAIN = "cross_chain"
    CUSTOM = "custom"


@dataclass
class ExecutionRequest:
    """Request to execute an opportunity"""
    request_id: str
    execution_type: ExecutionType
    chain_id: int
    opportunity_data: Dict[str, Any]
    target_contract: str
    calldata: str
    value: int = 0  # wei
    gas_limit: int = 0
    gas_price: int = 0  # wei
    max_fee_per_gas: int = 0  # For EIP-1559
    max_priority_fee: int = 0  # For EIP-1559
    nonce: Optional[int] = None
    deadline: int = 0  # Block timestamp deadline
    metadata: Dict[str, Any] = field(default_factory=dict)
    created_at: int = 0


@dataclass
class ExecutionResult:
    """Result of execution"""
    request_id: str
    status: ExecutionStatus
    tx_hash: Optional[str] = None
    block_number: Optional[int] = None
    gas_used: int = 0
    effective_gas_price: int = 0
    profit_usd: float = 0
    error_message: str = ""
    simulation_result: Optional[Dict] = None
    receipts: List[Dict] = field(default_factory=list)
    executed_at: int = 0
    confirmed_at: int = 0


@dataclass
class ExecutionStats:
    """Execution statistics"""
    total_executions: int = 0
    successful_executions: int = 0
    failed_executions: int = 0
    total_profit_usd: float = 0
    total_gas_spent_usd: float = 0
    avg_execution_time_ms: float = 0
    success_rate: float = 0.0
    avg_profit_usd: float = 0.0


class ExecutionInterface(ABC):
    """
    Abstract base class for all execution modules
    All executors must implement this interface
    """

    def __init__(self, config: Dict[str, Any] = None):
        self.config = config or {}
        self.is_running = False
        self._stats = ExecutionStats()

    @property
    @abstractmethod
    def name(self) -> str:
        """Executor name"""
        pass

    @property
    @abstractmethod
    def execution_type(self) -> ExecutionType:
        """Type of execution this module handles"""
        pass

    @abstractmethod
    async def initialize(self):
        """Initialize executor (connections, contracts, etc.)"""
        pass

    @abstractmethod
    async def shutdown():
        """Shutdown executor gracefully"""
        pass

    @abstractmethod
    async def validate_request(self, request: ExecutionRequest) -> bool:
        """Validate execution request"""
        pass

    @abstractmethod
    async def simulate(self, request: ExecutionRequest) -> Dict[str, Any]:
        """Simulate execution without submitting"""
        pass

    @abstractmethod
    async def execute(self, request: ExecutionRequest) -> ExecutionResult:
        """Execute the opportunity"""
        pass

    @abstractmethod
    async def cancel(self, request_id: str) -> bool:
        """Cancel pending execution"""
        pass

    def get_stats(self) -> ExecutionStats:
        """Get execution statistics"""
        if self._stats.total_executions > 0:
            self._stats.success_rate = self._stats.successful_executions / self._stats.total_executions
            self._stats.avg_profit_usd = self._stats.total_profit_usd / self._stats.total_executions
        return self._stats

    def _update_stats(self, result: ExecutionResult, execution_time_ms: int):
        """Update statistics after execution"""
        self._stats.total_executions += 1

        if result.status == ExecutionStatus.CONFIRMED:
            self._stats.successful_executions += 1
            self._stats.total_profit_usd += result.profit_usd
        else:
            self._stats.failed_executions += 1

        # Update average execution time
        total = self._stats.total_executions
        self._stats.avg_execution_time_ms = (
            (self._stats.avg_execution_time_ms * (total - 1) + execution_time_ms) / total
        )

        # Calculate gas spent
        if result.gas_used > 0 and result.effective_gas_price > 0:
            eth_price = 2000  # Would use real price
            gas_cost_eth = (result.gas_used * result.effective_gas_price) / 1e18
            self._stats.total_gas_spent_usd += gas_cost_eth * eth_price


# Default gas limits for different execution types
DEFAULT_GAS_LIMITS = {
    ExecutionType.LIQUIDATION: 500000,
    ExecutionType.ARBITRAGE: 800000,
    ExecutionType.BACKRUN: 300000,
    ExecutionType.SANDWICH: 600000,
    ExecutionType.CROSS_CHAIN: 1000000,
}

# Execution timeouts
EXECUTION_TIMEOUTS = {
    ExecutionType.LIQUIDATION: 30,  # seconds
    ExecutionType.ARBITRAGE: 60,
    ExecutionType.BACKRUN: 10,
    ExecutionType.SANDWICH: 10,
    ExecutionType.CROSS_CHAIN: 300,
}
