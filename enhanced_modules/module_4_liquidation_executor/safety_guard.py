#!/usr/bin/env python3
"""
enhanced_modules.module_4_liquidation_executor.safety_guard
=============================================================
Pre-execution safety checks including:
  1. Reentrancy detection via eth_call verification at pending block state.
  2. Profit circuit breaker — revert if profit drops below threshold.
  3. Slippage guard — verify prices haven't moved during execution.
  4. Gas price guard — abort if gas spikes above profitability.
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Dict, List, Optional

from enhanced_modules.common.base import EnhancedModule

logger = logging.getLogger(__name__)


@dataclass
class SafetyCheck:
    """Result of a single safety check."""
    check_name: str
    passed: bool
    details: str = ""
    value: Optional[float] = None
    threshold: Optional[float] = None


@dataclass
class SafetyReport:
    """Aggregate safety report for a liquidation."""
    all_passed: bool
    checks: List[SafetyCheck]
    recommendation: str  # PROCEED | ABORT | RETRY_LATER
    checked_at: float = field(default_factory=time.time)

    @property
    def failed_checks(self) -> List[SafetyCheck]:
        return [c for c in self.checks if not c.passed]


class SafetyGuard(EnhancedModule):
    """
    Multi-layer pre-execution safety system.

    Runs all checks before submitting a liquidation transaction:
      1. Reentrancy preflight check
      2. Profit recheck
      3. Gas price check
      4. Slippage tolerance check
      5. Cooldown check (avoid repeated failures)
    """

    # Default thresholds
    MIN_PROFIT_USD = Decimal("5")
    MAX_GAS_PRICE_GWEI = 200.0
    MAX_SLIPPAGE_PCT = 5.0
    COOLDOWN_SECONDS = 60.0

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        super().__init__("safety_guard", config)
        self._cooldown_map: Dict[str, float] = {}  # borrower -> last_attempt_time
        self._checks_run = 0
        self._aborts = 0

    async def _on_start(self) -> None:
        self.MIN_PROFIT_USD = Decimal(str(self.config.get("min_profit_usd", 5)))
        self.MAX_GAS_PRICE_GWEI = float(self.config.get("max_gas_gwei", 200))
        logger.info(
            "[SafetyGuard] min_profit=$%s, max_gas=%s gwei",
            self.MIN_PROFIT_USD, self.MAX_GAS_PRICE_GWEI,
        )

    async def _on_stop(self) -> None:
        logger.info(
            "[SafetyGuard] %d checks run, %d aborts (%.1f%% abort rate)",
            self._checks_run, self._aborts,
            self._aborts / max(1, self._checks_run) * 100,
        )

    # ── Main Check ─────────────────────────────────────────────

    async def run_all_checks(
        self,
        borrower: str,
        expected_profit_usd: Decimal,
        current_gas_price_gwei: float,
        price_deviation_pct: float,
        preflight_passed: bool = True,
    ) -> SafetyReport:
        """
        Run all safety checks and return a report.
        """
        checks: List[SafetyCheck] = []
        self._checks_run += 1

        # 1. Reentrancy / preflight verification check
        checks.append(SafetyCheck(
            check_name="preflight_verification",
            passed=preflight_passed,
            details="Preflight eth_call passed" if preflight_passed else "Preflight eth_call reverted",
        ))

        # 2. Profit check
        profit_ok = expected_profit_usd >= self.MIN_PROFIT_USD
        checks.append(SafetyCheck(
            check_name="profit_threshold",
            passed=profit_ok,
            details=f"${expected_profit_usd} vs min ${self.MIN_PROFIT_USD}",
            value=float(expected_profit_usd),
            threshold=float(self.MIN_PROFIT_USD),
        ))

        # 3. Gas price check
        gas_ok = current_gas_price_gwei <= self.MAX_GAS_PRICE_GWEI
        checks.append(SafetyCheck(
            check_name="gas_price",
            passed=gas_ok,
            details=f"{current_gas_price_gwei} gwei vs max {self.MAX_GAS_PRICE_GWEI}",
            value=current_gas_price_gwei,
            threshold=self.MAX_GAS_PRICE_GWEI,
        ))

        # 4. Slippage check
        slippage_ok = abs(price_deviation_pct) <= self.MAX_SLIPPAGE_PCT
        checks.append(SafetyCheck(
            check_name="slippage",
            passed=slippage_ok,
            details=f"{price_deviation_pct:.2f}% vs max {self.MAX_SLIPPAGE_PCT}%",
            value=price_deviation_pct,
            threshold=self.MAX_SLIPPAGE_PCT,
        ))

        # 5. Cooldown check
        now = time.time()
        last_attempt = self._cooldown_map.get(borrower, 0.0)
        cooldown_ok = (now - last_attempt) >= self.COOLDOWN_SECONDS
        checks.append(SafetyCheck(
            check_name="cooldown",
            passed=cooldown_ok,
            details=f"{now - last_attempt:.0f}s since last attempt (min {self.COOLDOWN_SECONDS}s)",
        ))

        # Update cooldown
        self._cooldown_map[borrower] = now

        all_passed = all(c.passed for c in checks)

        if not all_passed:
            self._aborts += 1
            # Determine recommendation based on which checks failed
            failed_names = {c.check_name for c in checks if not c.passed}
            if "preflight_verification" in failed_names:
                rec = "ABORT"
            elif "gas_price" in failed_names or "cooldown" in failed_names:
                rec = "RETRY_LATER"
            else:
                rec = "ABORT"
        else:
            rec = "PROCEED"

        self.record_success()
        return SafetyReport(
            all_passed=all_passed,
            checks=checks,
            recommendation=rec,
        )

    # ── Cooldown Management ────────────────────────────────────

    def clear_cooldown(self, borrower: str) -> None:
        self._cooldown_map.pop(borrower, None)

    def clear_all_cooldowns(self) -> None:
        self._cooldown_map.clear()

    def get_cooldown_count(self) -> int:
        return len(self._cooldown_map)
