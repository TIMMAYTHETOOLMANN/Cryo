#!/usr/bin/env python3
"""
Priority Channel Manager
========================
Maintains dedicated high-priority endpoints for latency-critical
operations (liquidation execution, time-sensitive MEV).

For each high-value chain, the manager provisions and maintains
a pool of dedicated connections that:
  - Skip the general load-balancer queue
  - Can optionally disable TLS on trusted infrastructure
  - Have higher rate-limit ceilings
  - Are reserved exclusively for CRITICAL / HIGH priority requests
"""
from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

try:
    import aiohttp
except ImportError:
    aiohttp = None  # type: ignore[assignment]

from .config import PriorityChannelConfig, get_gateway_config

logger = logging.getLogger(__name__)


# ── Data Models ───────────────────────────────────────────────────

@dataclass
class DedicatedEndpoint:
    """A dedicated RPC endpoint reserved for priority traffic."""
    url: str
    chain_id: int
    tls_disabled: bool = False
    rate_limit_rps: float = 100.0
    current_load: float = 0.0
    latency_ms: float = 50.0
    success_rate: float = 1.0
    total_requests: int = 0
    total_errors: int = 0
    last_health_check: float = 0.0
    is_healthy: bool = True


# ── Chain name → default priority RPC env var mapping ─────────────

_PRIORITY_ENV_MAP: Dict[int, str] = {
    1: "PRIORITY_RPC_ETHEREUM",
    42161: "PRIORITY_RPC_ARBITRUM",
    10: "PRIORITY_RPC_OPTIMISM",
    8453: "PRIORITY_RPC_BASE",
    137: "PRIORITY_RPC_POLYGON",
    43114: "PRIORITY_RPC_AVALANCHE",
}

# Fallback: reuse standard env vars with higher rate limits
_STANDARD_ENV_MAP: Dict[int, str] = {
    1: "MAINNET_RPC_URL",
    42161: "ARBITRUM_RPC_URL",
    10: "OPTIMISM_RPC_URL",
    8453: "BASE_RPC_URL",
    137: "POLYGON_RPC_URL",
    43114: "AVALANCHE_RPC_URL",
}


class PriorityChannelManager:
    """
    Manages dedicated high-priority RPC channels for time-critical operations.

    Sits *alongside* the general gateway — not replacing it.  When a caller
    asks for a priority endpoint, the manager returns the fastest dedicated
    channel for that chain.  If no dedicated channel exists the manager
    returns ``None`` so the caller can fallback to the standard load balancer.
    """

    def __init__(self, config: Optional[PriorityChannelConfig] = None) -> None:
        self.cfg = config or get_gateway_config().priority_channels
        self._channels: Dict[int, List[DedicatedEndpoint]] = {}
        self._started = False

    # ── Lifecycle ──────────────────────────────────────────────

    async def start(self) -> None:
        """Provision dedicated channels for all high-value chains."""
        if self._started:
            return

        logger.info("[PriorityChannels] Provisioning dedicated channels ...")
        for chain_id in self.cfg.high_value_chains:
            await self._provision_chain(chain_id)

        total = sum(len(eps) for eps in self._channels.values())
        logger.info(
            "[PriorityChannels] %d dedicated channels across %d chains",
            total, len(self._channels),
        )
        self._started = True

    async def stop(self) -> None:
        """Tear down connection pools and release dedicated channels."""
        self._started = False
        self._channels.clear()

    # ── Public API ─────────────────────────────────────────────

    def get_priority_endpoint(
        self,
        chain_id: int,
        request_type: str = "general",
    ) -> Optional[DedicatedEndpoint]:
        """
        Get the best dedicated endpoint for a chain.

        Returns ``None`` if no dedicated channel is available — the caller
        should fallback to the standard load balancer.

        For ``liquidation`` and ``mev_bundle`` request types, only healthy
        endpoints are considered.
        """
        eps = self._channels.get(chain_id, [])
        if not eps:
            return None

        healthy = [ep for ep in eps if ep.is_healthy]
        if not healthy:
            return None

        # For critical traffic, pick lowest latency
        if request_type in ("liquidation", "mev_bundle", "critical"):
            healthy.sort(key=lambda e: e.latency_ms)
            return healthy[0]

        # For high-priority monitoring, pick least loaded
        healthy.sort(key=lambda e: e.current_load)
        return healthy[0]

    def record_request(self, endpoint: DedicatedEndpoint, latency_ms: float, success: bool) -> None:
        """Track a request against a dedicated endpoint."""
        endpoint.total_requests += 1
        if not success:
            endpoint.total_errors += 1
        # Exponential moving average for latency
        endpoint.latency_ms = endpoint.latency_ms * 0.8 + latency_ms * 0.2
        endpoint.success_rate = (
            endpoint.success_rate * 0.95 + (1.0 if success else 0.0) * 0.05
        )
        if endpoint.success_rate < 0.5:
            endpoint.is_healthy = False

    async def health_check_all(self) -> Dict[int, int]:
        """
        Run a lightweight ``eth_blockNumber`` health check on every
        dedicated endpoint.  Returns ``{chain_id: healthy_count}``.
        """
        results: Dict[int, int] = {}
        for chain_id, eps in self._channels.items():
            healthy = 0
            for ep in eps:
                ok = await self._ping(ep)
                ep.is_healthy = ok
                ep.last_health_check = time.time()
                if ok:
                    healthy += 1
            results[chain_id] = healthy
        return results

    # ── Internals ──────────────────────────────────────────────

    async def _provision_chain(self, chain_id: int) -> None:
        """Create dedicated endpoints for one chain."""
        urls: List[str] = []

        # 1. Check for explicit priority env var
        env_key = _PRIORITY_ENV_MAP.get(chain_id, "")
        if env_key:
            val = os.getenv(env_key, "")
            if val:
                urls.append(val)

        # 2. Fallback to standard env var (always try, even if priority was checked)
        std_key = _STANDARD_ENV_MAP.get(chain_id, "")
        if std_key:
            val = os.getenv(std_key, "")
            if val and val not in urls:
                urls.append(val)

        # 2b. Extra fallback: ETH_RPC_URL for chain 1
        if chain_id == 1:
            eth_rpc = os.getenv("ETH_RPC_URL", "")
            if eth_rpc and eth_rpc not in urls:
                urls.append(eth_rpc)

        # 3. Alchemy fallback
        alchemy_key = os.getenv("ALCHEMY_API_KEY", "")
        if alchemy_key:
            slug_map = {
                1: "eth-mainnet", 42161: "arb-mainnet", 10: "opt-mainnet",
                8453: "base-mainnet", 137: "polygon-mainnet", 43114: "avax-mainnet",
            }
            slug = slug_map.get(chain_id)
            if slug:
                url = f"https://{slug}.g.alchemy.com/v2/{alchemy_key}"
                if url not in urls:
                    urls.append(url)

        # Create dedicated endpoints (up to max_dedicated_per_chain)
        eps: List[DedicatedEndpoint] = []
        for url in urls[: self.cfg.max_dedicated_per_chain]:
            eps.append(DedicatedEndpoint(
                url=url,
                chain_id=chain_id,
                tls_disabled=self.cfg.disable_tls,
                rate_limit_rps=self.cfg.priority_rate_limit_rps,
            ))
        if eps:
            self._channels[chain_id] = eps
            logger.info(
                "[PriorityChannels] Chain %d: %d dedicated endpoint(s)",
                chain_id, len(eps),
            )

    async def _ping(self, ep: DedicatedEndpoint) -> bool:
        """Lightweight health ping using eth_blockNumber."""
        if aiohttp is None:
            return True  # Can't check without aiohttp
        try:
            payload = {
                "jsonrpc": "2.0", "method": "eth_blockNumber",
                "params": [], "id": 1,
            }
            start = time.time()
            connector = aiohttp.TCPConnector(ssl=False) if ep.tls_disabled else None
            async with aiohttp.ClientSession(connector=connector) as session:
                async with session.post(
                    ep.url, json=payload,
                    timeout=aiohttp.ClientTimeout(total=10),
                    headers={"Content-Type": "application/json"},
                ) as resp:
                    latency = (time.time() - start) * 1000
                    if resp.status == 200:
                        data = await resp.json()
                        if "result" in data:
                            ep.latency_ms = ep.latency_ms * 0.7 + latency * 0.3
                            return True
                    # 429 = rate limited, still alive
                    if resp.status == 429:
                        return True
                    return False
        except Exception:
            return False

    # ── Stats ──────────────────────────────────────────────────

    def get_stats(self) -> Dict[str, Any]:
        chains: Dict[int, Dict[str, Any]] = {}
        for chain_id, eps in self._channels.items():
            chains[chain_id] = {
                "endpoints": len(eps),
                "healthy": sum(1 for e in eps if e.is_healthy),
                "total_requests": sum(e.total_requests for e in eps),
                "avg_latency_ms": (
                    sum(e.latency_ms for e in eps) / max(len(eps), 1)
                ),
            }
        return {
            "started": self._started,
            "total_channels": sum(len(v) for v in self._channels.values()),
            "chains": chains,
        }
