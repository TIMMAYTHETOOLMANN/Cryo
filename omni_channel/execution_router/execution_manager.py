#!/usr/bin/env python3
"""
Execution Manager
Coordinates all execution modules and handles routing

Features:
- Unified execution interface
- Queue management
- Priority handling
- Error recovery
"""

import asyncio
import time
import uuid
from typing import Dict, List, Optional, Any, Callable, Awaitable
from dataclasses import dataclass, field
from collections import defaultdict

from .execution_interface import (
    ExecutionInterface, ExecutionRequest, ExecutionResult,
    ExecutionStatus, ExecutionType, ExecutionStats
)
from .liquidation_executor import LiquidationExecutor
from .arbitrage_executor import ArbitrageExecutor
from .backrun_executor import BackrunExecutor
from .cross_chain_executor import CrossChainExecutor
from ..ml_aggregator.dynamic_router import RoutingDecision
from ..data_lake.data_models import ExecutionModule


@dataclass
class QueuedExecution:
    """Execution in queue"""
    request: ExecutionRequest
    priority: int
    queued_at: int
    deadline: int
    retries: int = 0
    max_retries: int = 3


class ExecutionManager:
    """
    Manage all execution modules
    """

    def __init__(self, config: Dict[str, Any] = None):
        self.config = config or {}
        self.is_running = False

        # Initialize executors
        self.executors: Dict[ExecutionType, ExecutionInterface] = {
            ExecutionType.LIQUIDATION: LiquidationExecutor(config.get('liquidation', {})),
            ExecutionType.ARBITRAGE: ArbitrageExecutor(config.get('arbitrage', {})),
            ExecutionType.BACKRUN: BackrunExecutor(config.get('backrun', {})),
            ExecutionType.CROSS_CHAIN: CrossChainExecutor(config.get('cross_chain', {})),
        }

        # Execution queues by priority
        self._queues: Dict[int, asyncio.Queue] = {
            i: asyncio.Queue() for i in range(1, 11)  # Priority 1-10
        }

        # Active executions
        self._active_executions: Dict[str, QueuedExecution] = {}
        self._completed_executions: Dict[str, ExecutionResult] = {}

        # Callbacks
        self._completion_callbacks: List[Callable[[ExecutionResult], Awaitable[None]]] = []

        # Statistics
        self.total_submitted = 0
        self.total_completed = 0

        print("⚙️  Execution Manager initialized")

    async def start(self):
        """Start execution manager"""
        print("\n⚙️  Starting Execution Manager...")
        self.is_running = True

        # Initialize all executors
        for executor in self.executors.values():
            await executor.initialize()

        # Start queue processors
        for priority in range(10, 0, -1):
            asyncio.create_task(self._process_queue(priority))

        print("   ✅ Execution Manager started")

    async def stop(self):
        """Stop execution manager"""
        print("\n⚙️  Stopping Execution Manager...")
        self.is_running = False

        # Shutdown all executors
        for executor in self.executors.values():
            await executor.shutdown()

        print("   ✅ Execution Manager stopped")

    async def submit(self, request: ExecutionRequest, priority: int = 5) -> str:
        """Submit execution request"""
        request.request_id = request.request_id or str(uuid.uuid4())
        request.created_at = int(time.time())

        # Set deadline if not set
        if request.deadline == 0:
            request.deadline = int(time.time()) + 300  # 5 minute default

        # Queue execution
        queued = QueuedExecution(
            request=request,
            priority=priority,
            queued_at=int(time.time()),
            deadline=request.deadline
        )

        self._active_executions[request.request_id] = queued
        await self._queues[priority].put(queued)
        self.total_submitted += 1

        print(f"   📝 Queued execution {request.request_id[:8]}... (priority {priority})")

        return request.request_id

    async def submit_from_routing(self, decision: RoutingDecision,
                                   opportunity_data: Dict[str, Any]) -> str:
        """Submit execution from routing decision"""
        # Convert routing decision to execution request
        request = ExecutionRequest(
            request_id=str(uuid.uuid4()),
            execution_type=self._routing_to_execution_type(decision.routed_to),
            chain_id=opportunity_data.get('chain_id', 1),
            opportunity_data=opportunity_data,
            target_contract=opportunity_data.get('target_contract', ''),
            calldata=opportunity_data.get('calldata', '0x'),
            value=opportunity_data.get('value', 0),
            gas_limit=opportunity_data.get('gas_limit', 0),
            gas_price=opportunity_data.get('gas_price', 0),
            deadline=opportunity_data.get('deadline', 0),
            metadata=opportunity_data.get('metadata', {}),
        )

        return await self.submit(request, decision.priority)

    def _routing_to_execution_type(self, module: ExecutionModule) -> ExecutionType:
        """Convert execution module to execution type"""
        mapping = {
            ExecutionModule.LIQUIDATION_ENGINE: ExecutionType.LIQUIDATION,
            ExecutionModule.ARBITRAGE_MODULE: ExecutionType.ARBITRAGE,
            ExecutionModule.BACKRUN_BOT: ExecutionType.BACKRUN,
            ExecutionModule.SANDWICH_BOT: ExecutionType.SANDWICH,
            ExecutionModule.CROSS_CHAIN_EXECUTOR: ExecutionType.CROSS_CHAIN,
            ExecutionModule.MANUAL_REVIEW: ExecutionType.CUSTOM,
        }
        return mapping.get(module, ExecutionType.CUSTOM)

    async def _process_queue(self, priority: int):
        """Process execution queue for priority level"""
        queue = self._queues[priority]

        while self.is_running:
            try:
                # Get execution from queue
                try:
                    queued = await asyncio.wait_for(queue.get(), timeout=1.0)
                except asyncio.TimeoutError:
                    continue

                # Check deadline
                if time.time() > queued.deadline:
                    print(f"   ⚠️  Execution {queued.request.request_id[:8]}... expired")
                    self._active_executions.pop(queued.request.request_id, None)
                    continue

                # Get executor
                executor = self.executors.get(queued.request.execution_type)
                if not executor:
                    print(f"   ⚠️  No executor for type {queued.request.execution_type}")
                    continue

                # Execute
                result = await executor.execute(queued.request)

                # Store result
                self._completed_executions[queued.request.request_id] = result
                self._active_executions.pop(queued.request.request_id, None)
                self.total_completed += 1

                # Emit callback
                await self._emit_completion(result)

                print(f"   ✅ Execution {queued.request.request_id[:8]}... completed: {result.status.value}")

            except Exception as e:
                print(f"   ⚠️  Queue processing error: {e}")
                await asyncio.sleep(1)

    async def cancel(self, request_id: str) -> bool:
        """Cancel execution"""
        if request_id not in self._active_executions:
            return False

        queued = self._active_executions[request_id]

        # Try to cancel with executor
        executor = self.executors.get(queued.request.execution_type)
        if executor:
            await executor.cancel(request_id)

        # Remove from active
        self._active_executions.pop(request_id, None)

        print(f"   🚫 Cancelled execution {request_id[:8]}...")
        return True

    def get_status(self, request_id: str) -> Optional[ExecutionResult]:
        """Get execution status"""
        return self._completed_executions.get(request_id)

    def get_active_executions(self) -> List[QueuedExecution]:
        """Get all active executions"""
        return list(self._active_executions.values())

    def get_queue_lengths(self) -> Dict[int, int]:
        """Get queue lengths by priority"""
        return {priority: queue.qsize() for priority, queue in self._queues.items()}

    def on_completion(self, callback: Callable[[ExecutionResult], Awaitable[None]]):
        """Register completion callback"""
        self._completion_callbacks.append(callback)

    async def _emit_completion(self, result: ExecutionResult):
        """Emit completion to callbacks"""
        for callback in self._completion_callbacks:
            try:
                await callback(result)
            except Exception as e:
                print(f"   ⚠️  Completion callback error: {e}")

    def get_stats(self) -> Dict:
        """Get manager statistics"""
        executor_stats = {
            et.value: exec.get_stats()
            for et, exec in self.executors.items()
        }

        return {
            'total_submitted': self.total_submitted,
            'total_completed': self.total_completed,
            'active_executions': len(self._active_executions),
            'completed_executions': len(self._completed_executions),
            'queue_lengths': self.get_queue_lengths(),
            'executor_stats': executor_stats,
        }


# Convenience function to create execution request from opportunity signal
def create_execution_request(signal: Any, calldata: str = '0x') -> ExecutionRequest:
    """Create execution request from opportunity signal"""
    from ..data_lake.data_models import SignalType

    signal_type_map = {
        SignalType.LIQUIDATION: ExecutionType.LIQUIDATION,
        SignalType.ARBITRAGE: ExecutionType.ARBITRAGE,
        SignalType.BACK_RUN: ExecutionType.BACKRUN,
        SignalType.SANDWICH: ExecutionType.SANDWICH,
        SignalType.CROSS_CHAIN_ARB: ExecutionType.CROSS_CHAIN,
    }

    exec_type = signal_type_map.get(signal.signal_type, ExecutionType.CUSTOM)

    return ExecutionRequest(
        request_id=str(uuid.uuid4()),
        execution_type=exec_type,
        chain_id=signal.chain_id,
        opportunity_data={
            'signal_type': signal.signal_type.value,
            'expected_value_usd': signal.expected_value_usd,
        },
        target_contract=signal.target_contract,
        calldata=calldata,
        gas_limit=signal.gas_estimate if hasattr(signal, 'gas_estimate') else 0,
        gas_price=signal.gas_price_gwei * 1e9 if hasattr(signal, 'gas_price_gwei') else 0,
        deadline=int(time.time()) + 300,
        metadata={
            'expected_profit_usd': signal.expected_value_usd,
            'source': signal.source_module.value,
        }
    )
