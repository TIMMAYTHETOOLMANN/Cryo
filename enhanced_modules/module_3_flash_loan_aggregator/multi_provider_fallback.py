#!/usr/bin/env python3
"""
enhanced_modules.module_3_flash_loan_aggregator.multi_provider_fallback
========================================================================
Implements a **cascading fallback chain** for flash loan execution.

If the primary provider fails (out of liquidity, reverted, etc.), the
system automatically retries with the next best provider — all within
the same execution context.

Fallback Order (default):
  1. Balancer V2 (0% fee)
  2. DODO (0% fee)
  3. Maker DssFlash (0% fee, DAI only)
  4. Aave V3 (0.05%)
  5. dYdX (0.02%)

Each provider has a circuit breaker that trips after 3 consecutive
failures, temporarily removing it from the chain.
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum
from typing import Any, Callable, Coroutine, Dict, List, Optional

from enhanced_modules.common.base import EnhancedModule
from enhanced_modules.common.models import FlashLoanProviderType

logger = logging.getLogger(__name__)


class CircuitState(Enum):
    CLOSED = "closed"      # Normal — requests pass through
    OPEN = "open"          # Tripped — requests rejected
    HALF_OPEN = "half_open"  # Testing — allow one request


@dataclass
class ProviderCircuitBreaker:
    """Circuit breaker for a single flash loan provider."""
    provider_type: FlashLoanProviderType
    failure_threshold: int = 3
    recovery_timeout: float = 300.0  # 5 minutes
    # State
    state: CircuitState = CircuitState.CLOSED
    consecutive_failures: int = 0
    last_failure_time: float = 0.0
    total_trips: int = 0

    def record_success(self) -> None:
        self.consecutive_failures = 0
        self.state = CircuitState.CLOSED

    def record_failure(self) -> None:
        self.consecutive_failures += 1
        self.last_failure_time = time.time()
        if self.consecutive_failures >= self.failure_threshold:
            self.state = CircuitState.OPEN
            self.total_trips += 1
            logger.warning(
                "[CircuitBreaker] %s TRIPPED (trip #%d)",
                self.provider_type.value, self.total_trips,
            )

    def is_available(self) -> bool:
        if self.state == CircuitState.CLOSED:
            return True
        if self.state == CircuitState.OPEN:
            # Check if recovery timeout has passed
            if time.time() - self.last_failure_time > self.recovery_timeout:
                self.state = CircuitState.HALF_OPEN
                return True
            return False
        # HALF_OPEN: allow one attempt
        return True


@dataclass
class FallbackResult:
    """Result of a flash loan attempt through the fallback chain."""
    success: bool
    provider_used: Optional[FlashLoanProviderType]
    attempts: int
    providers_tried: List[str]
    error: Optional[str] = None
    gas_used: int = 0
    fee_paid_usd: Decimal = Decimal("0")
    latency_ms: float = 0.0
    result_data: Any = None


# Callable type for flash loan execution
FlashLoanExecutor = Callable[
    [FlashLoanProviderType, str, Decimal, int],
    Coroutine[Any, Any, Dict[str, Any]],
]


class MultiProviderFallback(EnhancedModule):
    """
    Cascading flash loan fallback chain with per-provider circuit breakers.

    Usage:
        fallback = MultiProviderFallback()
        result = await fallback.execute_with_fallback(
            asset="WETH",
            amount_usd=Decimal("100000"),
            chain_id=1,
            executor_fn=my_flash_loan_fn,
        )
    """

    MAX_RETRIES = 3  # Within the chain, not per provider

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        super().__init__("multi_provider_fallback", config)

        # Default fallback order (can be overridden by adaptive router)
        self._default_chain: List[FlashLoanProviderType] = [
            FlashLoanProviderType.BALANCER_V2,
            FlashLoanProviderType.DODO,
            FlashLoanProviderType.MAKER_DSS,
            FlashLoanProviderType.AAVE_V3,
            FlashLoanProviderType.DYDX,
            FlashLoanProviderType.UNISWAP_V3,
        ]

        # Circuit breakers per chain per provider
        self._breakers: Dict[int, Dict[FlashLoanProviderType, ProviderCircuitBreaker]] = {}
        self._total_attempts = 0
        self._fallback_triggers = 0

    async def _on_start(self) -> None:
        for chain_id in [1, 10, 42161, 8453, 137]:
            self._breakers[chain_id] = {
                pt: ProviderCircuitBreaker(provider_type=pt)
                for pt in FlashLoanProviderType
            }
        logger.info("[Fallback] Initialized with %d providers in chain", len(self._default_chain))

    async def _on_stop(self) -> None:
        total_trips = sum(
            b.total_trips
            for chain_breakers in self._breakers.values()
            for b in chain_breakers.values()
        )
        logger.info(
            "[Fallback] %d total attempts, %d fallback triggers, %d circuit trips",
            self._total_attempts, self._fallback_triggers, total_trips,
        )

    # ── Main Execution ─────────────────────────────────────────

    async def execute_with_fallback(
        self,
        asset: str,
        amount_usd: Decimal,
        chain_id: int,
        executor_fn: FlashLoanExecutor,
        preferred_chain: Optional[List[FlashLoanProviderType]] = None,
    ) -> FallbackResult:
        """
        Attempt flash loan execution through the fallback chain.

        Args:
            asset: Debt asset to borrow
            amount_usd: Amount in USD
            chain_id: Target chain
            executor_fn: Async function that performs the actual flash loan
            preferred_chain: Optional custom provider ordering
        """
        chain = preferred_chain or self._default_chain
        breakers = self._breakers.get(chain_id, {})

        providers_tried: List[str] = []
        attempts = 0

        for provider_type in chain:
            if attempts >= self.MAX_RETRIES:
                break

            # Check circuit breaker
            breaker = breakers.get(provider_type)
            if breaker and not breaker.is_available():
                continue

            providers_tried.append(provider_type.value)
            attempts += 1
            self._total_attempts += 1

            start_time = time.time()
            try:
                result = await executor_fn(provider_type, asset, amount_usd, chain_id)

                latency = (time.time() - start_time) * 1000
                if result.get("success", False):
                    if breaker:
                        breaker.record_success()

                    if attempts > 1:
                        self._fallback_triggers += 1

                    self.record_success()
                    return FallbackResult(
                        success=True,
                        provider_used=provider_type,
                        attempts=attempts,
                        providers_tried=providers_tried,
                        gas_used=result.get("gas_used", 0),
                        fee_paid_usd=Decimal(str(result.get("fee_usd", 0))),
                        latency_ms=latency,
                        result_data=result,
                    )
                else:
                    # Provider returned failure (e.g., insufficient liquidity)
                    if breaker:
                        breaker.record_failure()
                    logger.info(
                        "[Fallback] %s failed: %s — trying next",
                        provider_type.value, result.get("error", "unknown"),
                    )

            except Exception as exc:
                if breaker:
                    breaker.record_failure()
                logger.warning(
                    "[Fallback] %s exception: %s — trying next",
                    provider_type.value, exc,
                )

        # All providers exhausted
        self.record_failure("All providers exhausted")
        return FallbackResult(
            success=False,
            provider_used=None,
            attempts=attempts,
            providers_tried=providers_tried,
            error=f"All {attempts} providers failed",
        )

    # ── Circuit Breaker Management ─────────────────────────────

    def reset_breaker(self, chain_id: int, provider_type: FlashLoanProviderType) -> None:
        """Manually reset a circuit breaker."""
        breaker = self._breakers.get(chain_id, {}).get(provider_type)
        if breaker:
            breaker.state = CircuitState.CLOSED
            breaker.consecutive_failures = 0

    def get_breaker_status(self, chain_id: int) -> Dict[str, Any]:
        """Get circuit breaker status for all providers on a chain."""
        breakers = self._breakers.get(chain_id, {})
        return {
            pt.value: {
                "state": b.state.value,
                "consecutive_failures": b.consecutive_failures,
                "total_trips": b.total_trips,
                "available": b.is_available(),
            }
            for pt, b in breakers.items()
        }

    def get_fallback_stats(self) -> Dict[str, Any]:
        """Return fallback execution statistics."""
        return {
            "total_attempts": self._total_attempts,
            "fallback_triggers": self._fallback_triggers,
            "fallback_rate": (
                self._fallback_triggers / max(1, self._total_attempts)
            ),
        }
