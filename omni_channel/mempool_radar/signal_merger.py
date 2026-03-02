#!/usr/bin/env python3
"""
Signal Merger
Merges transactions from multiple mempool providers with deduplication
and latency-based ranking
"""

import asyncio
import time
from collections import defaultdict
from typing import Dict, List, Optional, Set, Callable, Awaitable
from dataclasses import dataclass, field

from ..data_lake.data_models import MempoolTransaction, SignalSource
from .base_provider import BaseMempoolProvider, ProviderStats
from .bloxroute_provider import BloXrouteProvider
from .infura_provider import InfuraProvider
from .blocknative_provider import BlocknativeProvider
from .base_provider import ProviderConfig


@dataclass
class MergedTransaction:
    """Transaction merged from multiple providers"""
    tx_hash: str
    transaction: MempoolTransaction
    providers_seen: List[str] = field(default_factory=list)
    first_seen_time: float = 0.0
    best_latency_ms: float = 0.0
    confidence_score: float = 0.0


class SignalMerger:
    """
    Merges mempool transactions from multiple providers
    - Deduplicates by transaction hash
    - Tracks which providers saw each transaction first
    - Calculates confidence scores based on provider agreement
    """
    
    def __init__(self, dedup_window_ms: float = 5000):
        self.dedup_window_ms = dedup_window_ms
        self._pending: Dict[str, MergedTransaction] = {}
        self._seen_hashes: Set[str] = set()
        self._provider_latencies: Dict[str, List[float]] = defaultdict(list)
        self._lock = asyncio.Lock()
        
        # Statistics
        self.total_received = 0
        self.total_deduplicated = 0
        self.total_merged = 0
    
    async def add_transaction(self, tx: MempoolTransaction, provider: str):
        """Add transaction from provider"""
        async with self._lock:
            self.total_received += 1
            tx_hash = tx.hash.lower()
            current_time = time.time()
            
            # Check if we've seen this transaction
            if tx_hash in self._pending:
                merged = self._pending[tx_hash]
                
                # Add provider if not already seen
                if provider not in merged.providers_seen:
                    merged.providers_seen.append(provider)
                    merged.confidence_score = self._calculate_confidence(merged.providers_seen)
                    
                    # Update latency
                    latency_ms = (current_time - merged.first_seen_time) * 1000
                    self._provider_latencies[provider].append(latency_ms)
                
                self.total_merged += 1
                
            else:
                # New transaction
                merged = MergedTransaction(
                    tx_hash=tx_hash,
                    transaction=tx,
                    providers_seen=[provider],
                    first_seen_time=current_time,
                    best_latency_ms=0.0,
                    confidence_score=0.5  # Single provider = medium confidence
                )
                
                self._pending[tx_hash] = merged
                self._seen_hashes.add(tx_hash)
                
                # Clean old transactions
                await self._cleanup_old_transactions()
    
    def _calculate_confidence(self, providers: List[str]) -> float:
        """Calculate confidence score based on provider agreement"""
        num_providers = len(providers)
        
        if num_providers == 1:
            return 0.5
        elif num_providers == 2:
            return 0.75
        elif num_providers >= 3:
            return 0.95
        
        return 0.5
    
    async def _cleanup_old_transactions(self):
        """Remove transactions older than dedup window"""
        current_time = time.time()
        window_seconds = self.dedup_window_ms / 1000
        
        to_remove = []
        for tx_hash, merged in self._pending.items():
            if current_time - merged.first_seen_time > window_seconds:
                to_remove.append(tx_hash)
        
        for tx_hash in to_remove:
            del self._pending[tx_hash]
            self._seen_hashes.discard(tx_hash)
    
    async def get_merged_transaction(self, tx_hash: str) -> Optional[MergedTransaction]:
        """Get merged transaction by hash"""
        async with self._lock:
            return self._pending.get(tx_hash.lower())
    
    async def get_all_pending(self) -> List[MergedTransaction]:
        """Get all pending merged transactions"""
        async with self._lock:
            return list(self._pending.values())
    
    def get_provider_stats(self) -> Dict[str, Dict]:
        """Get latency statistics per provider"""
        stats = {}
        
        for provider, latencies in self._provider_latencies.items():
            if latencies:
                sorted_lat = sorted(latencies)
                stats[provider] = {
                    'count': len(latencies),
                    'p50_ms': sorted_lat[len(latencies) // 2],
                    'p99_ms': sorted_lat[int(len(latencies) * 0.99)] if len(latencies) > 100 else sorted_lat[-1],
                    'avg_ms': sum(latencies) / len(latencies)
                }
        
        return stats
    
    def get_stats(self) -> Dict:
        """Get merger statistics"""
        return {
            'pending_count': len(self._pending),
            'total_received': self.total_received,
            'total_deduplicated': self.total_deduplicated,
            'total_merged': self.total_merged,
            'provider_stats': self.get_provider_stats()
        }


class MempoolRadar:
    """
    Main Mempool Radar class
    Coordinates multiple providers and merges their signals
    """
    
    def __init__(self, config: Dict[str, ProviderConfig]):
        self.providers: Dict[str, BaseMempoolProvider] = {}
        self.merger = SignalMerger()
        self.is_running = False
        
        # Transaction callbacks
        self._transaction_callbacks: List[Callable[[MergedTransaction], Awaitable[None]]] = []
        
        # Initialize providers
        if 'bloxroute' in config:
            self.providers['bloxroute'] = BloXrouteProvider(config['bloxroute'])
        if 'infura' in config:
            self.providers['infura'] = InfuraProvider(config['infura'])
        if 'blocknative' in config:
            self.providers['blocknative'] = BlocknativeProvider(config['blocknative'])
        
        print(f"📡 Mempool Radar initialized with {len(self.providers)} providers")
    
    async def start(self):
        """Start all providers"""
        print("\n🚀 Starting Mempool Radar...")
        print("=" * 60)
        
        self.is_running = True
        
        # Start all providers
        for name, provider in self.providers.items():
            provider.on_transaction(lambda tx, p=name: self._handle_transaction(tx, p))
            await provider.start()
        
        # Start merger processing
        asyncio.create_task(self._process_merged_signals())
        
        print("=" * 60)
    
    async def stop(self):
        """Stop all providers"""
        print("\n📡 Stopping Mempool Radar...")
        
        self.is_running = False
        
        for provider in self.providers.values():
            await provider.disconnect()
        
        print("   ✅ Mempool Radar stopped")
    
    async def _handle_transaction(self, tx: MempoolTransaction, provider: str):
        """Handle transaction from provider"""
        await self.merger.add_transaction(tx, provider)
    
    async def _process_merged_signals(self):
        """Process merged transactions and emit to callbacks"""
        processed_hashes = set()
        
        while self.is_running:
            try:
                merged_txs = await self.merger.get_all_pending()
                
                for merged in merged_txs:
                    if merged.tx_hash not in processed_hashes:
                        # Only emit if seen by at least 2 providers or high confidence
                        if len(merged.providers_seen) >= 2 or merged.confidence_score > 0.7:
                            await self._emit_merged_transaction(merged)
                            processed_hashes.add(merged.tx_hash)
                
                await asyncio.sleep(0.1)  # 100ms processing interval
                
            except Exception as e:
                print(f"   ⚠️  Merger processing error: {e}")
                await asyncio.sleep(1)
    
    def on_merged_transaction(self, callback: Callable[[MergedTransaction], Awaitable[None]]):
        """Register callback for merged transactions"""
        self._transaction_callbacks.append(callback)
    
    async def _emit_merged_transaction(self, merged: MergedTransaction):
        """Emit merged transaction to all callbacks"""
        for callback in self._transaction_callbacks:
            try:
                await callback(merged)
            except Exception as e:
                print(f"   ⚠️  Callback error: {e}")
    
    def get_stats(self) -> Dict:
        """Get radar statistics"""
        provider_stats = {
            name: provider.get_stats()
            for name, provider in self.providers.items()
        }
        
        return {
            'is_running': self.is_running,
            'providers': list(self.providers.keys()),
            'provider_stats': provider_stats,
            'merger_stats': self.merger.get_stats()
        }
