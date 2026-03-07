#!/usr/bin/env python3
"""
enhanced_modules.module_6_risk_protection.circuit_breaker_v2
==============================================================
3-tier circuit breaker system with exponential backoff recovery:

  🟢 GREEN  — Normal operations, full execution.
  🟡 YELLOW — Degraded mode: reduced position sizes, mandatory profit recheck.
  🔴 RED    — Halt all execution. Auto-recovery after cooldown.

Triggers:
  - Consecutive execution failures
  - Profit drops below threshold
  - Gas price spikes
  - Competitor front-running detection
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional

from enhanced_modules.common.base import EnhancedModule

logger = logging.getLogger(__name__)


class BreakerTier(Enum):
    GREEN = "green"
    YELLOW = "yellow"
    RED = "red"


@dataclass
class BreakerState:
    """Current state of the circuit breaker."""
    tier: BreakerTier = BreakerTier.GREEN
    consecutive_failures: int = 0
    total_failures: int = 0
    total_successes: int = 0
    last_failure_time: Optional[float] = None
    last_recovery_time: Optional[float] = None
    recovery_attempts: int = 0
    cooldown_seconds: float = 60.0  # Current cooldown (grows with backoff)
    base_cooldown: float = 60.0     # Initial cooldown
    max_cooldown: float = 3600.0    # Max cooldown (1 hour)

    @property
    def time_since_failure(self) -> float:
        if not self.last_failure_time:
            return float("inf")
        return time.time() - self.last_failure_time

    @property
    def is_in_cooldown(self) -> bool:
        return self.time_since_failure < self.cooldown_seconds


@dataclass
class BreakerDecision:
    """What the circuit breaker recommends."""
    tier: BreakerTier
    allow_execution: bool
    max_position_size_pct: float  # 1.0 = 100%, 0 = none
    require_profit_recheck: bool
    message: str


class CircuitBreakerV2(EnhancedModule):
    """
    3-tier circuit breaker with exponential backoff recovery.

    Transitions:
      GREEN → YELLOW: After N consecutive failures or profit drop
      YELLOW → RED: After M more failures or gas spike
      RED → YELLOW: After cooldown (with exponential backoff)
      YELLOW → GREEN: After K consecutive successes
    """

    # Tier transition thresholds
    GREEN_TO_YELLOW_FAILURES = 3
    YELLOW_TO_RED_FAILURES = 5
    RED_RECOVERY_SUCCESSES = 3
    YELLOW_RECOVERY_SUCCESSES = 5

    # Position size limits by tier
    TIER_POSITION_LIMITS = {
        BreakerTier.GREEN: 1.0,
        BreakerTier.YELLOW: 0.5,
        BreakerTier.RED: 0.0,
    }

    # Backoff multiplier for cooldown
    BACKOFF_MULTIPLIER = 2.0

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        super().__init__("circuit_breaker_v2", config)
        self._state = BreakerState()
        self._recovery_task: Optional[asyncio.Task] = None
        self._transition_log: List[Dict[str, Any]] = []

    async def _on_start(self) -> None:
        logger.info("[CircuitBreakerV2] Starting in GREEN tier")

    async def _on_stop(self) -> None:
        if self._recovery_task and not self._recovery_task.done():
            self._recovery_task.cancel()

    # ── Decision ───────────────────────────────────────────────

    def check(self) -> BreakerDecision:
        """Check current breaker state and get execution guidance."""
        tier = self._state.tier

        # Check for auto-recovery from RED
        if tier == BreakerTier.RED and not self._state.is_in_cooldown:
            self._transition(BreakerTier.YELLOW, "Cooldown expired")

        return BreakerDecision(
            tier=self._state.tier,
            allow_execution=self._state.tier != BreakerTier.RED,
            max_position_size_pct=self.TIER_POSITION_LIMITS[self._state.tier],
            require_profit_recheck=self._state.tier == BreakerTier.YELLOW,
            message=self._get_status_message(),
        )

    # ── Event Recording ────────────────────────────────────────

    def record_success(self) -> None:
        """Record a successful execution."""
        self._state.total_successes += 1
        self._state.consecutive_failures = 0

        # Check for recovery transitions
        if self._state.tier == BreakerTier.YELLOW:
            if self._state.total_successes >= self.YELLOW_RECOVERY_SUCCESSES:
                self._transition(BreakerTier.GREEN, "Consecutive successes")
        elif self._state.tier == BreakerTier.RED:
            # Shouldn't happen (RED blocks execution), but handle gracefully
            self._transition(BreakerTier.YELLOW, "Success during RED (manual override)")

    def record_failure(self, reason: str = "unknown") -> None:
        """Record a failed execution."""
        self._state.total_failures += 1
        self._state.consecutive_failures += 1
        self._state.last_failure_time = time.time()

        # Check for degradation transitions
        if self._state.tier == BreakerTier.GREEN:
            if self._state.consecutive_failures >= self.GREEN_TO_YELLOW_FAILURES:
                self._transition(BreakerTier.YELLOW, f"Failures: {reason}")
        elif self._state.tier == BreakerTier.YELLOW:
            if self._state.consecutive_failures >= self.YELLOW_TO_RED_FAILURES:
                self._transition(BreakerTier.RED, f"Failures: {reason}")

    def record_gas_spike(self, gas_gwei: float, threshold_gwei: float) -> None:
        """Record a gas price spike — may trigger degradation."""
        if gas_gwei > threshold_gwei * 3 and self._state.tier == BreakerTier.GREEN:
            self._transition(BreakerTier.YELLOW, f"Gas spike: {gas_gwei} gwei")
        elif gas_gwei > threshold_gwei * 5:
            self._transition(BreakerTier.RED, f"Extreme gas: {gas_gwei} gwei")

    def record_frontrun_detected(self) -> None:
        """Record that a competitor front-ran us."""
        self._state.consecutive_failures += 2  # Count as 2 failures
        self.record_failure("front-running detected")

    # ── Tier Transitions ───────────────────────────────────────

    def _transition(self, new_tier: BreakerTier, reason: str) -> None:
        """Transition to a new tier."""
        old_tier = self._state.tier
        if old_tier == new_tier:
            return

        self._state.tier = new_tier

        # Adjust cooldown with exponential backoff
        if new_tier == BreakerTier.RED:
            self._state.recovery_attempts += 1
            self._state.cooldown_seconds = min(
                self._state.base_cooldown * (self.BACKOFF_MULTIPLIER ** self._state.recovery_attempts),
                self._state.max_cooldown,
            )
        elif new_tier == BreakerTier.GREEN:
            # Reset on full recovery
            self._state.recovery_attempts = 0
            self._state.cooldown_seconds = self._state.base_cooldown
            self._state.last_recovery_time = time.time()

        # Log transition
        entry = {
            "from": old_tier.value,
            "to": new_tier.value,
            "reason": reason,
            "timestamp": time.time(),
            "consecutive_failures": self._state.consecutive_failures,
            "cooldown": self._state.cooldown_seconds,
        }
        self._transition_log.append(entry)

        icon = {"green": "🟢", "yellow": "🟡", "red": "🔴"}
        logger.warning(
            "[CircuitBreakerV2] %s %s → %s %s | Reason: %s | Cooldown: %.0fs",
            icon.get(old_tier.value, ""), old_tier.value.upper(),
            icon.get(new_tier.value, ""), new_tier.value.upper(),
            reason, self._state.cooldown_seconds,
        )

    # ── Manual Controls ────────────────────────────────────────

    def force_green(self) -> None:
        """Force reset to GREEN (manual override)."""
        self._transition(BreakerTier.GREEN, "Manual override")
        self._state.consecutive_failures = 0

    def force_red(self, reason: str = "manual") -> None:
        """Force to RED (emergency stop)."""
        self._transition(BreakerTier.RED, f"Emergency: {reason}")

    # ── Status ─────────────────────────────────────────────────

    def _get_status_message(self) -> str:
        s = self._state
        return (
            f"Tier: {s.tier.value.upper()} | "
            f"Failures: {s.consecutive_failures} consecutive, {s.total_failures} total | "
            f"Successes: {s.total_successes} | "
            f"Cooldown: {s.cooldown_seconds:.0f}s"
        )

    def get_full_status(self) -> Dict[str, Any]:
        s = self._state
        return {
            "tier": s.tier.value,
            "consecutive_failures": s.consecutive_failures,
            "total_failures": s.total_failures,
            "total_successes": s.total_successes,
            "cooldown_seconds": s.cooldown_seconds,
            "is_in_cooldown": s.is_in_cooldown,
            "recovery_attempts": s.recovery_attempts,
            "recent_transitions": self._transition_log[-10:],
        }
