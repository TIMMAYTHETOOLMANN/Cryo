#!/usr/bin/env python3
"""
Gateway Configuration
=====================
Centralised frozen-dataclass config for every RPC Gateway subcomponent.
All values have sane defaults and can be overridden via env vars.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Optional

from dotenv import load_dotenv

load_dotenv()

_INSTANCE: Optional["GatewayConfig"] = None


# ── Priority Channel Config ──────────────────────────────────────

@dataclass(frozen=True)
class PriorityChannelConfig:
    """Config for dedicated high-value chain endpoints."""
    high_value_chains: tuple = (1, 42161, 10, 8453, 137, 43114, 56, 324)  # ALL chains
    priority_rate_limit_rps: float = 200.0
    disable_tls: bool = False  # safety default; enable on trusted infra
    max_dedicated_per_chain: int = 5


# ── WebSocket Manager Config ─────────────────────────────────────

@dataclass(frozen=True)
class WebSocketConfig:
    """Config for resilient WebSocket connection management."""
    heartbeat_interval_s: float = 30.0
    initial_reconnect_delay_s: float = 0.5
    max_reconnect_delay_s: float = 15.0
    max_reconnect_attempts: int = 100  # 0 = unlimited
    dead_connection_timeout_s: float = 60.0
    max_connections_per_chain: int = 5
    subscription_buffer_size: int = 20000


# ── Distributed Rate Limiter Config ──────────────────────────────

@dataclass(frozen=True)
class DistributedRateLimiterConfig:
    """Config for Redis-backed cross-instance rate coordination."""
    redis_url: str = field(
        default_factory=lambda: os.getenv("REDIS_URL", "redis://localhost:6379/0")
    )
    key_prefix: str = "cryo:rpc:rate"
    window_seconds: int = 60
    enable_redis: bool = field(
        default_factory=lambda: os.getenv("RPC_GATEWAY_REDIS", "0") == "1"
    )
    cooldown_default_s: float = 60.0
    sync_interval_s: float = 5.0


# ── Method Optimizer Config ──────────────────────────────────────

@dataclass(frozen=True)
class MethodOptimizerConfig:
    """Config for per-method RPC routing optimisation."""
    cache_block_ttl_s: float = 1.0  # cache results for ~half block
    cache_max_entries: int = 50_000
    expensive_method_multiplier: float = 3.0
    enable_caching: bool = True


# ── Core Gateway Config ──────────────────────────────────────

@dataclass(frozen=True)
class CoreGatewayConfig:
    """Config for the base RPCGateway from profit_engine."""
    max_batch_size: int = 100
    batch_flush_ms: float = 50.0
    health_check_interval_s: float = 15.0
    metrics_log_interval_s: float = 60.0
    request_timeout_s: float = 8.0


# ── Key Rotation Config ─────────────────────────────────────────

@dataclass(frozen=True)
class KeyRotationConfig:
    """Config for API key rotation across providers."""
    alchemy_keys: tuple = field(
        default_factory=lambda: tuple(
            k.strip()
            for k in os.getenv("ALCHEMY_API_KEYS", os.getenv("ALCHEMY_API_KEY", "")).split(",")
            if k.strip()
        )
    )
    infura_keys: tuple = field(
        default_factory=lambda: tuple(
            k.strip()
            for k in os.getenv("INFURA_API_KEYS", os.getenv("INFURA_API_KEY", "")).split(",")
            if k.strip()
        )
    )
    quicknode_keys: tuple = field(
        default_factory=lambda: tuple(
            k.strip()
            for k in os.getenv("QUICKNODE_API_KEYS", "").split(",")
            if k.strip()
        )
    )
    rotation_threshold: float = 0.80  # Rotate at 80% quota usage


# ── Master Config ────────────────────────────────────────────────

@dataclass(frozen=True)
class GatewayConfig:
    """
    Top-level configuration for the entire MODULE_10 RPC Gateway.
    """
    core: CoreGatewayConfig = field(default_factory=CoreGatewayConfig)
    priority_channels: PriorityChannelConfig = field(default_factory=PriorityChannelConfig)
    websocket: WebSocketConfig = field(default_factory=WebSocketConfig)
    distributed_rate: DistributedRateLimiterConfig = field(default_factory=DistributedRateLimiterConfig)
    method_optimizer: MethodOptimizerConfig = field(default_factory=MethodOptimizerConfig)
    key_rotation: KeyRotationConfig = field(default_factory=KeyRotationConfig)


def get_gateway_config() -> GatewayConfig:
    """Singleton accessor for the gateway configuration."""
    global _INSTANCE
    if _INSTANCE is None:
        _INSTANCE = GatewayConfig()
    return _INSTANCE
