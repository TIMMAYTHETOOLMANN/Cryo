"""
Intelligent RPC Gateway & Load Balancer - Critical Infrastructure
Unified integration with all profit modules for exponential scaling
"""

import asyncio
import redis
import json
from typing import Dict, List, Any
import aiohttp
import time
from dataclasses import dataclass
from collections import deque

@dataclass
class EndpointConfig:
    url: str
    chain_id: int
    provider: str  # 'alchemy', 'infura', 'quicknode', 'custom'
    weight: float
    rate_limit: int  # Requests per second
    current_load: int = 0
    latency_ms: float = 0
    success_rate: float = 100.0
    is_active: bool = True
    last_health_check: float = 0
    failure_count: int = 0

class IntelligentRPCGateway:
    """Enhanced gateway integrating all your brilliant architectural components"""
    
    def __init__(self):
        self.endpoints = {}  # endpoint_id -> EndpointConfig
        self.redis_client = redis.Redis(host='localhost', port=6379, decode_responses=True)
        self.request_queue = asyncio.Queue()
        self.local_counters = {}
        self.circuit_breakers = {}
        self.batch_aggregator = BatchAggregator()
        self.priority_manager = PriorityChannelManager()
        
        # Initialize endpoint pool
        self._initialize_endpoint_pool()
        
    def _initialize_endpoint_pool(self):
        """Initialize your 96 Alchemy endpoints + fallbacks"""
        # Load Alchemy endpoints (your 96 endpoints)
        self._load_alchemy_endpoints()
        
        # Add fallback providers
        self._add_fallback_providers()
        
        # Initialize token buckets for rate limiting
        self._initialize_token_buckets()
        
    async def route_request(self, request_data: Dict) -> Any:
        """Main routing function called by ALL profit modules"""
        
        # Step 1: Check if request can be batched
        if await self._can_batch_request(request_data):
            return await self.batch_aggregator.enqueue_request(request_data)
        
        # Step 2: Get optimal endpoint considering priority
        endpoint_id = await self._select_optimal_endpoint(
            request_data['chain_id'], 
            request_data['priority'],
            request_data['method']
        )
        
        # Step 3: Check rate limits
        if not await self._check_rate_limits(endpoint_id, request_data['method']):
            endpoint_id = await self._get_fallback_endpoint(endpoint_id, request_data['chain_id'])
        
        # Step 4: Execute with circuit breaker protection
        return await self._execute_with_failover(endpoint_id, request_data)
    
    async def _select_optimal_endpoint(self, chain_id: int, priority: str, method: str) -> str:
        """Your enhanced weighted scoring algorithm"""
        
        candidates = self._get_candidates_for_chain(chain_id)
        
        scored_endpoints = []
        for endpoint_id, endpoint in candidates.items():
            score = await self._calculate_endpoint_score(endpoint, priority, method)
            scored_endpoints.append((endpoint_id, score, endpoint))
        
        # Sort by score and select best
        scored_endpoints.sort(key=lambda x: x[1], reverse=True)
        return scored_endpoints[0][0]
    
    async def _calculate_endpoint_score(self, endpoint: EndpointConfig, priority: str, method: str) -> float:
        """Your sophisticated scoring algorithm"""
        
        # Base score from configured weight
        score = endpoint.weight
        
        # Penalize high latency (your formula)
        score *= (1000 / (endpoint.latency_ms + 100))
        
        # Boost for high success rate
        score *= (endpoint.success_rate / 100)
        
        # Penalize near rate-limit (your critical insight)
        usage_ratio = endpoint.current_load / endpoint.rate_limit
        if usage_ratio > 0.8:
            score *= (1 - usage_ratio)
        
        # Priority boost for critical operations
        if priority == 'high':  # liquidation, arbitrage
            score *= 1.5
        
        # Method-specific adjustments
        if method in ['eth_getLogs', 'eth_call']:  # Expensive methods
            score *= 0.7  # Penalize heavy methods
        
        return score
    
    async def _execute_with_failover(self, primary_endpoint_id: str, request_data: Dict) -> Any:
        """Circuit breaker pattern with automatic failover"""
        
        # Try primary endpoint
        try:
            if await self.circuit_breakers.get(primary_endpoint_id, {}).get('is_open', False):
                raise Exception(f"Circuit breaker open for {primary_endpoint_id}")
            
            response = await self._execute_single_request(primary_endpoint_id, request_data)
            self._record_success(primary_endpoint_id)
            return response
            
        except Exception as error:
            # Record failure
            self._record_failure(primary_endpoint_id)
            
            # Try fallback endpoints
            fallbacks = self._get_fallback_endpoints(request_data['chain_id'], primary_endpoint_id)
            
            for fallback_id in fallbacks:
                try:
                    response = await self._execute_single_request(fallback_id, request_data)
                    self._record_success(fallback_id)
                    return response
                except Exception:
                    self._record_failure(fallback_id)
                    continue
            
            # All endpoints failed
            raise Exception("All endpoints failed for request")
    
    # Your Batch Aggregator Implementation
    class BatchAggregator:
        def __init__(self):
            self.batch_queues = {}
            self.batch_interval = 0.05  # 50ms batches
            self.max_batch_size = 100
            
        async def enqueue_request(self, request_data: Dict) -> Any:
            """Batch multiple requests into single HTTP calls"""
            
            chain_id = request_data['chain_id']
            if chain_id not in self.batch_queues:
                self.batch_queues[chain_id] = asyncio.Queue()
                asyncio.create_task(self._process_batch_queue(chain_id))
            
            # Create promise for batching
            future = asyncio.Future()
            await self.batch_queues[chain_id].put((request_data, future))
            
            return await future
        
        async def _process_batch_queue(self, chain_id: int):
            """Process batch queue every 50ms"""
            while True:
                await asyncio.sleep(self.batch_interval)
                
                batch_requests = []
                while not self.batch_queues[chain_id].empty():
                    if len(batch_requests) >= self.max_batch_size:
                        break
                    batch_requests.append(await self.batch_queues[chain_id].get())
                
                if batch_requests:
                    await self._execute_batch(chain_id, batch_requests)
