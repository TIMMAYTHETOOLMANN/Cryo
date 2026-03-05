"""
Enhanced Circuit Breaker with Health Monitoring
Integrated with your failover strategy
"""

import time
from typing import Dict, List
import asyncio

class IntelligentCircuitBreaker:
    """Your circuit breaker pattern with health checks"""
    
    def __init__(self):
        self.failure_records = {}
        self.health_checker = EndpointHealthChecker()
        self.circuit_states = {}  # endpoint_id -> state dict
        
    async def execute_with_failover(self, primary_endpoint: str, request_data: Dict, fallbacks: List[str]) -> Any:
        """Your failover execution pattern"""
        
        # Check if circuit is open
        if await self._is_circuit_open(primary_endpoint):
            # Try fallbacks immediately
            for fallback in fallbacks:
                if not await self._is_circuit_open(fallback):
                    try:
                        return await self._execute_endpoint_request(fallback, request_data)
                    except Exception:
                        await self._record_failure(fallback)
                        continue
            
            raise Exception("All circuit breakers open")
        
        # Try primary endpoint
        try:
            response = await self._execute_endpoint_request(primary_endpoint, request_data)
            await self._record_success(primary_endpoint)
            return response
        except Exception as error:
            await self._record_failure(primary_endpoint)
            # Recursive fallback
            return await self.execute_with_failover(fallbacks[0], request_data, fallbacks[1:])
    
    async def _record_failure(self, endpoint_id: str):
        """Your failure tracking logic"""
        
        record = self.failure_records.get(endpoint_id, {'count': 0, 'last_failure': 0})
        record['count'] += 1
        record['last_failure'] = time.time()
        
        # Open circuit after threshold (your 5 failures in 60s rule)
        if record['count'] > 5 and (time.time() - record['last_failure']) < 60:
            await self._open_circuit(endpoint_id, 30)  # 30-second cooldown
        
        self.failure_records[endpoint_id] = record
    
    async def _open_circuit(self, endpoint_id: str, cooldown_seconds: int):
        """Open circuit breaker"""
        
        self.circuit_states[endpoint_id] = {
            'state': 'OPEN',
            'opened_at': time.time(),
            'cooldown': cooldown_seconds
        }
