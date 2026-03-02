#!/usr/bin/env python3
"""
Mempool Radar - Base Provider Interface
Abstract base class for all mempool data providers
"""

from abc import ABC, abstractmethod
from typing import Dict, List, Optional, Callable, Awaitable, Any
from dataclasses import dataclass
import asyncio
import time

from ..data_lake.data_models import MempoolTransaction, OracleUpdate, LargeSwap


@dataclass
class ProviderConfig:
    """Configuration for mempool provider"""
    api_key: str
    endpoint: str
    ws_endpoint: Optional[str] = None
    timeout_ms: int = 5000
    reconnect_delay_s: int = 5
    max_retries: int = 3


@dataclass
class ProviderStats:
    """Statistics for provider performance"""
    provider_name: str
    is_connected: bool = False
    messages_received: int = 0
    transactions_parsed: int = 0
    errors: int = 0
    latency_p50_ms: float = 0.0
    latency_p99_ms: float = 0.0
    last_message_time: Optional[float] = None
    uptime_seconds: float = 0.0
    reconnect_count: int = 0


class BaseMempoolProvider(ABC):
    """
    Abstract base class for mempool data providers
    Implementations: bloXroute, Infura, Blocknative
    """
    
    def __init__(self, config: ProviderConfig):
        self.config = config
        self.is_running = False
        self.stats = ProviderStats(provider_name=self.name)
        self._transaction_callbacks: List[Callable[[MempoolTransaction], Awaitable[None]]] = []
        self._oracle_callbacks: List[Callable[[OracleUpdate], Awaitable[None]]] = []
        self._swap_callbacks: List[Callable[[LargeSwap], Awaitable[None]]] = []
        self._latencies: List[float] = []
        self._start_time: Optional[float] = None
    
    @property
    @abstractmethod
    def name(self) -> str:
        """Provider name"""
        pass
    
    @abstractmethod
    async def connect(self):
        """Establish connection to provider"""
        pass
    
    @abstractmethod
    async def disconnect(self):
        """Close connection to provider"""
        pass
    
    @abstractmethod
    async def subscribe_transactions(self):
        """Subscribe to pending transaction stream"""
        pass
    
    @abstractmethod
    async def subscribe_logs(self, contract_address: str, topics: List[str]):
        """Subscribe to specific contract event logs"""
        pass
    
    @abstractmethod
    async def get_pending_transactions(self, limit: int = 100) -> List[MempoolTransaction]:
        """Poll for pending transactions"""
        pass
    
    async def start(self):
        """Start provider and begin streaming"""
        print(f"\n📡 Starting {self.name} provider...")
        
        self._start_time = time.time()
        self.is_running = True
        
        try:
            await self.connect()
            self.stats.is_connected = True
            print(f"   ✅ {self.name} connected")
            
            # Start streaming
            asyncio.create_task(self._stream_loop())
            
        except Exception as e:
            print(f"   ❌ {self.name} failed to start: {e}")
            self.stats.is_connected = False
            self.is_running = False
    
    async def _stream_loop(self):
        """Main streaming loop"""
        retries = 0
        
        while self.is_running:
            try:
                if not self.stats.is_connected:
                    await self._reconnect()
                
                await self.subscribe_transactions()
                
                # Stream will be handled by provider-specific implementation
                await self._process_stream()
                
                retries = 0  # Reset on success
                
            except Exception as e:
                self.stats.errors += 1
                retries += 1
                
                print(f"   ⚠️  {self.name} stream error: {e}")
                
                if retries >= self.config.max_retries:
                    print(f"   ❌ {self.name} max retries reached, disconnecting")
                    self.stats.is_connected = False
                    self.is_running = False
                else:
                    print(f"   🔄 {self.name} reconnecting in {self.config.reconnect_delay_s}s...")
                    await asyncio.sleep(self.config.reconnect_delay_s)
    
    async def _reconnect(self):
        """Attempt to reconnect"""
        try:
            await self.disconnect()
            await asyncio.sleep(1)
            await self.connect()
            self.stats.is_connected = True
            self.stats.reconnect_count += 1
            print(f"   ✅ {self.name} reconnected")
        except Exception as e:
            print(f"   ❌ {self.name} reconnection failed: {e}")
            self.stats.is_connected = False
    
    @abstractmethod
    async def _process_stream(self):
        """Process incoming stream (provider-specific)"""
        pass
    
    def _record_latency(self, latency_ms: float):
        """Record message latency for statistics"""
        self._latencies.append(latency_ms)
        
        # Keep last 1000 latencies
        if len(self._latencies) > 1000:
            self._latencies = self._latencies[-1000:]
        
        # Update stats
        sorted_latencies = sorted(self._latencies)
        p50_idx = int(len(sorted_latencies) * 0.5)
        p99_idx = int(len(sorted_latencies) * 0.99)
        
        self.stats.latency_p50_ms = sorted_latencies[p50_idx] if sorted_latencies else 0
        self.stats.latency_p99_ms = sorted_latencies[p99_idx] if sorted_latencies else 0
    
    def _record_message(self):
        """Record received message"""
        self.stats.messages_received += 1
        self.stats.last_message_time = time.time()
        self.stats.uptime_seconds = time.time() - self._start_time if self._start_time else 0
    
    def on_transaction(self, callback: Callable[[MempoolTransaction], Awaitable[None]]):
        """Register transaction callback"""
        self._transaction_callbacks.append(callback)
    
    def on_oracle_update(self, callback: Callable[[OracleUpdate], Awaitable[None]]):
        """Register oracle update callback"""
        self._oracle_callbacks.append(callback)
    
    def on_large_swap(self, callback: Callable[[LargeSwap], Awaitable[None]]):
        """Register large swap callback"""
        self._swap_callbacks.append(callback)
    
    async def _emit_transaction(self, tx: MempoolTransaction):
        """Emit transaction to all callbacks"""
        self.stats.transactions_parsed += 1
        
        for callback in self._transaction_callbacks:
            try:
                await callback(tx)
            except Exception as e:
                print(f"   ❌ Transaction callback error in {self.name}: {e}")
    
    async def _emit_oracle_update(self, update: OracleUpdate):
        """Emit oracle update to all callbacks"""
        for callback in self._oracle_callbacks:
            try:
                await callback(update)
            except Exception as e:
                print(f"   ❌ Oracle callback error in {self.name}: {e}")
    
    async def _emit_swap(self, swap: LargeSwap):
        """Emit swap to all callbacks"""
        for callback in self._swap_callbacks:
            try:
                await callback(swap)
            except Exception as e:
                print(f"   ❌ Swap callback error in {self.name}: {e}")
    
    def get_stats(self) -> ProviderStats:
        """Get provider statistics"""
        self.stats.uptime_seconds = time.time() - self._start_time if self._start_time else 0
        return self.stats
    
    def parse_transaction(self, raw_tx: Dict[str, Any]) -> MempoolTransaction:
        """Parse raw transaction into MempoolTransaction"""
        return MempoolTransaction(
            hash=raw_tx.get('hash', ''),
            from_address=raw_tx.get('from', ''),
            to_address=raw_tx.get('to'),
            value=int(raw_tx.get('value', 0)),
            gas_price=int(raw_tx.get('gasPrice', 0)),
            gas_limit=int(raw_tx.get('gas', 0)),
            input_data=raw_tx.get('input', '0x'),
            nonce=int(raw_tx.get('nonce', 0)),
            block_number=raw_tx.get('blockNumber'),
            timestamp=time.time(),
            provider=self.name,
            raw_tx=raw_tx
        )
