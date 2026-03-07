#!/usr/bin/env python3
"""
MODULE 10 — Intelligent RPC Gateway & Load Balancer
=====================================================
Enterprise-grade RPC traffic management layer sitting between the
triangulation engine and 96+ blockchain endpoints.

Composes:
  - Adaptive Load Balancer   (profit_engine.rpc_gateway)
  - Rate Limit Tracker       (profit_engine.rpc_gateway)
  - Circuit Breaker           (profit_engine.rpc_gateway)
  - Batch Aggregator          (profit_engine.rpc_gateway)
  - Endpoint Pool Manager     (profit_engine.rpc_gateway)
  + Priority Channel Manager  (this package — dedicated high-value channels)
  + Resilient WebSocket Mgr   (this package — persistent WS + reconnect)
  + Distributed Rate Limiter  (this package — Redis-backed cross-instance)
  + Method Optimizer          (this package — per-method routing hints)
  + Enhanced Gateway Facade   (this package — unified composition)
"""

from .gateway import EnhancedRPCGateway
from .config import GatewayConfig, get_gateway_config
from .priority_channel_manager import PriorityChannelManager
from .websocket_manager import ResilientWebSocketManager
from .distributed_rate_limiter import DistributedRateLimiter
from .method_optimizer import MethodOptimizer

__all__ = [
    "EnhancedRPCGateway",
    "GatewayConfig",
    "get_gateway_config",
    "PriorityChannelManager",
    "ResilientWebSocketManager",
    "DistributedRateLimiter",
    "MethodOptimizer",
]
