"""
Enhanced Rate Limit Tracker - Redis-Backed Distributed Coordination
Integrated with your intelligent throttling strategies
"""

import asyncio
import redis
import time
from typing import Dict, List

class EnhancedRateLimitTracker:
    """Your sophisticated rate limiting with method-based tracking"""
    
    def __init__(self):
        self.redis_client = redis.Redis(decode_responses=True)
        self.local_token_buckets = {}
        self.method_weights = {
            'eth_getLogs': 5.0,      # Expensive - high weight
            'eth_call': 2.0,         # Moderate weight
            'eth_getBalance': 0.5,    # Cheap - low weight
            'eth_blockNumber': 0.1   # Very cheap
        }
    
    async def can_request(self, endpoint_id: str, method: str) -> bool:
        """Check if request is allowed considering both local and global limits"""
        
        # Check local token bucket first
        if not self._local_token_bucket_consume(endpoint_id, method):
            return False
        
        # Check Redis for distributed coordination
        global_key = f"rate:{endpoint_id}"
        method_key = f"method:{endpoint_id}:{method}"
        
        # Your method-based tracking
        method_remaining = await self._check_method_limit(method_key, method)
        global_remaining = await self._check_global_limit(global_key)
        
        return method_remaining > 0 and global_remaining > 0
    
    async def track_request(self, endpoint_id: str, method: str):
        """Track request usage across distributed system"""
        
        # Update Redis counters with expiry
        pipe = self.redis_client.pipeline()
        pipe.incr(f"method:{endpoint_id}:{method}")
        pipe.incr(f"rate:{endpoint_id}")
        pipe.expire(f"method:{endpoint_id}:{method}", 60)  # 60-second window
        pipe.expire(f"rate:{endpoint_id}", 60)
        await pipe.execute()
        
        # Update local counters
        self._update_local_counters(endpoint_id, method)
    
    async def handle_rate_limit_error(self, endpoint_id: str, retry_after: int = None):
        """Your intelligent rate limit error handling"""
        
        cooldown = retry_after or 60  # Default 60-second cooldown
        cooldown_key = f"cooldown:{endpoint_id}"
        
        await self.redis_client.setex(cooldown_key, cooldown, "1")
        
        # Log for analysis and ML optimization
        await self._log_rate_limit_event(endpoint_id, retry_after)
        
        # Update circuit breaker
        self._update_circuit_breaker(endpoint_id, "rate_limit_hit")
