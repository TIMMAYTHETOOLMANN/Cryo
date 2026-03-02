#!/usr/bin/env python3
"""
Signal Queue Management
In-memory queue with persistence layer for opportunity signals
"""

import asyncio
import json
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Callable, Awaitable
from enum import Enum
import heapq

from .data_models import OpportunitySignal, SignalType, ExecutionModule, SignalSource


class Priority(Enum):
    """Signal priority levels"""
    CRITICAL = 0  # Execute immediately
    HIGH = 1
    NORMAL = 2
    LOW = 3


@dataclass(order=True)
class PrioritizedSignal:
    """Signal wrapper for priority queue"""
    priority: int
    timestamp: float
    signal: OpportunitySignal = field(compare=False)


class SignalQueue:
    """
    Priority queue for opportunity signals
    Supports multiple consumers and signal expiration
    """
    
    def __init__(self, max_size: int = 10000):
        self.max_size = max_size
        self._queue: List[PrioritizedSignal] = []
        self._signal_map: Dict[str, PrioritizedSignal] = {}
        self._lock = asyncio.Lock()
        self._not_empty = asyncio.Condition()
        
        # Statistics
        self.signals_added = 0
        self.signals_removed = 0
        self.signals_expired = 0
        
    async def put(self, signal: OpportunitySignal, priority: Priority = Priority.NORMAL):
        """Add signal to queue with priority"""
        async with self._lock:
            if len(self._queue) >= self.max_size:
                # Remove lowest priority signal
                self._remove_lowest_priority()
            
            prioritized = PrioritizedSignal(
                priority=priority.value,
                timestamp=time.time(),
                signal=signal
            )
            
            heapq.heappush(self._queue, prioritized)
            self._signal_map[signal.signal_id] = prioritized
            self.signals_added += 1
        
        async with self._not_empty:
            self._not_empty.notify()
    
    async def get(self, timeout: Optional[float] = None) -> Optional[OpportunitySignal]:
        """Get highest priority signal"""
        async with self._not_empty:
            try:
                await asyncio.wait_for(self._not_empty.wait(), timeout)
            except asyncio.TimeoutError:
                return None
        
        async with self._lock:
            while self._queue:
                prioritized = heapq.heappop(self._queue)
                signal = prioritized.signal
                
                # Remove from map
                if signal.signal_id in self._signal_map:
                    del self._signal_map[signal.signal_id]
                
                # Check expiration
                if signal.is_expired(signal.expiry_block):
                    self.signals_expired += 1
                    continue
                
                self.signals_removed += 1
                return signal
            
        return None
    
    async def get_batch(self, max_batch: int = 10, timeout: Optional[float] = None) -> List[OpportunitySignal]:
        """Get batch of signals"""
        signals = []
        start_time = time.time()
        
        while len(signals) < max_batch:
            remaining = timeout - (time.time() - start_time) if timeout else None
            if remaining is not None and remaining <= 0:
                break
            
            signal = await self.get(timeout=remaining)
            if signal:
                signals.append(signal)
            else:
                break
        
        return signals
    
    def _remove_lowest_priority(self):
        """Remove lowest priority (highest number) signal"""
        if not self._queue:
            return
        
        # Find index of lowest priority
        lowest_idx = 0
        for i in range(1, len(self._queue)):
            if self._queue[i].priority > self._queue[lowest_idx].priority:
                lowest_idx = i
        
        # Remove
        removed = self._queue.pop(lowest_idx)
        if removed.signal.signal_id in self._signal_map:
            del self._signal_map[removed.signal.signal_id]
    
    async def remove(self, signal_id: str) -> bool:
        """Remove specific signal by ID"""
        async with self._lock:
            if signal_id in self._signal_map:
                prioritized = self._signal_map.pop(signal_id)
                self._queue.remove(prioritized)
                heapq.heapify(self._queue)  # Re-heapify
                self.signals_removed += 1
                return True
        return False
    
    def size(self) -> int:
        """Get current queue size"""
        return len(self._queue)
    
    def is_empty(self) -> bool:
        """Check if queue is empty"""
        return len(self._queue) == 0

    async def enqueue(self, signal: OpportunitySignal, priority: Priority = None):
        """Add a signal to the queue.

        If `priority` is not explicitly specified, it is derived from the
        signal's `urgency_score` (0-100) so that high-urgency signals are
        processed before low-urgency ones:
            urgency >= 80  → CRITICAL
            urgency >= 60  → HIGH
            urgency >= 30  → NORMAL
            urgency  < 30  → LOW
        """
        if priority is None:
            urgency = getattr(signal, 'urgency_score', 50)
            if urgency >= 80:
                priority = Priority.CRITICAL
            elif urgency >= 60:
                priority = Priority.HIGH
            elif urgency >= 30:
                priority = Priority.NORMAL
            else:
                priority = Priority.LOW
        return await self.put(signal, priority)

    async def dequeue(self, timeout: Optional[float] = None) -> Optional[OpportunitySignal]:
        """Remove and return highest-priority signal.

        Checks for existing items immediately before blocking on the condition
        variable, so it works correctly when items were enqueued before this
        call (i.e., the condition notification was already consumed).
        """
        # Fast path: drain any items already in the queue
        async with self._lock:
            while self._queue:
                prioritized = heapq.heappop(self._queue)
                signal = prioritized.signal
                if signal.signal_id in self._signal_map:
                    del self._signal_map[signal.signal_id]
                if signal.is_expired(signal.expiry_block):
                    self.signals_expired += 1
                    continue
                self.signals_removed += 1
                return signal
        # Queue was empty — fall back to blocking get()
        return await self.get(timeout=timeout)
    
    def get_stats(self) -> Dict:
        """Get queue statistics"""
        return {
            'size': self.size(),
            'max_size': self.max_size,
            'signals_added': self.signals_added,
            'signals_removed': self.signals_removed,
            'signals_expired': self.signals_expired,
            'utilization': self.size() / self.max_size if self.max_size > 0 else 0
        }


class SignalRouter:
    """
    Routes signals to appropriate execution modules
    Supports dynamic routing rules
    """
    
    def __init__(self):
        self._routes: Dict[ExecutionModule, asyncio.Queue] = {}
        self._routing_rules: List[Callable[[OpportunitySignal], ExecutionModule]] = []
        self._stats: Dict[ExecutionModule, int] = {}
        
    def register_module(self, module: ExecutionModule):
        """Register an execution module"""
        if module not in self._routes:
            self._routes[module] = asyncio.Queue(maxsize=1000)
            self._stats[module] = 0
    
    def add_routing_rule(self, rule: Callable[[OpportunitySignal], ExecutionModule]):
        """Add dynamic routing rule"""
        self._routing_rules.append(rule)
    
    async def route(self, signal: OpportunitySignal) -> ExecutionModule:
        """Route signal to appropriate module"""
        # Try routing rules first
        for rule in self._routing_rules:
            try:
                module = rule(signal)
                await self._routes[module].put(signal)
                self._stats[module] += 1
                return module
            except:
                continue
        
        # Default routing based on signal type
        default_module = self._default_route(signal)
        await self._routes[default_module].put(signal)
        self._stats[default_module] += 1
        return default_module
    
    def _default_route(self, signal: OpportunitySignal) -> ExecutionModule:
        """Default routing logic"""
        type_to_module = {
            SignalType.LIQUIDATION: ExecutionModule.LIQUIDATION_ENGINE,
            SignalType.ARBITRAGE: ExecutionModule.ARBITRAGE_MODULE,
            SignalType.CROSS_CHAIN_ARB: ExecutionModule.CROSS_CHAIN_EXECUTOR,
            SignalType.BACKRUN: ExecutionModule.BACKRUN_BOT,
            SignalType.SANDWICH: ExecutionModule.SANDWICH_BOT,
        }
        return type_to_module.get(signal.signal_type, ExecutionModule.MANUAL_REVIEW)
    
    async def get_signal(self, module: ExecutionModule, timeout: Optional[float] = None) -> Optional[OpportunitySignal]:
        """Get signal for specific module"""
        if module not in self._routes:
            return None
        
        try:
            return await asyncio.wait_for(self._routes[module].get(), timeout)
        except asyncio.TimeoutError:
            return None
    
    def get_stats(self) -> Dict:
        """Get routing statistics"""
        return {
            module.value: {
                'queue_size': queue.qsize(),
                'routed_count': count
            }
            for module, queue, count in [
                (m, q, self._stats.get(m, 0)) 
                for m, q in self._routes.items()
            ]
        }


class SignalStore:
    """
    In-memory store for signal history and analytics
    """
    
    def __init__(self, max_history: int = 100000):
        self.max_history = max_history
        self._signals: Dict[str, OpportunitySignal] = {}
        self._history: deque = deque(maxlen=max_history)
        self._by_type: Dict[SignalType, deque] = {}
        self._by_source: Dict[SignalSource, deque] = {}
        
    def add(self, signal: OpportunitySignal):
        """Add signal to store"""
        self._signals[signal.signal_id] = signal
        self._history.append(signal)
        
        # Index by type
        if signal.signal_type not in self._by_type:
            self._by_type[signal.signal_type] = deque(maxlen=self.max_history)
        self._by_type[signal.signal_type].append(signal)
        
        # Index by source
        if signal.source_module not in self._by_source:
            self._by_source[signal.source_module] = deque(maxlen=self.max_history)
        self._by_source[signal.source_module].append(signal)
    
    def get(self, signal_id: str) -> Optional[OpportunitySignal]:
        """Get signal by ID"""
        return self._signals.get(signal_id)
    
    def update(self, signal: OpportunitySignal):
        """Update existing signal"""
        if signal.signal_id in self._signals:
            self._signals[signal.signal_id] = signal
    
    def get_by_type(self, signal_type: SignalType, limit: int = 100) -> List[OpportunitySignal]:
        """Get recent signals by type"""
        if signal_type not in self._by_type:
            return []
        return list(self._by_type[signal_type])[-limit:]
    
    def get_by_source(self, source: SignalSource, limit: int = 100) -> List[OpportunitySignal]:
        """Get recent signals by source"""
        if source not in self._by_source:
            return []
        return list(self._by_source[source])[-limit:]
    
    def get_recent(self, limit: int = 100) -> List[OpportunitySignal]:
        """Get most recent signals"""
        return list(self._history)[-limit:]
    
    def get_stats(self) -> Dict:
        """Get store statistics"""
        return {
            'total_signals': len(self._signals),
            'history_size': len(self._history),
            'by_type': {t.value: len(d) for t, d in self._by_type.items()},
            'by_source': {s.value: len(d) for s, d in self._by_source.items()}
        }
