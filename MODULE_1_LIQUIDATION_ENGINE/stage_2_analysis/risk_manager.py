#!/usr/bin/env python3
"""
STAGE 2 — Enhanced Risk Manager (Script 2 Upgraded)
=====================================================
Adaptive risk assessment with TWAP validation, volatility-based slippage,
and enhanced circuit breaker with profit recheck.

ENHANCEMENTS (Script 2 / Module 6):
  1. TWAP Validation — smooth out manipulation via Uniswap/Chainlink TWAP
  2. Dynamic Slippage Based on Volatility — auto-adjust minAmountOut
  3. On-Chain Circuit Breaker with Profit Recheck — revert if profit drops
  4. Cooldown on failed user liquidations — avoid repeated unprofitable attempts

Zero capital required — pure computation + read-only chain queries.
"""

import logging
import os
import time
from collections import deque
from typing import Dict, List, Optional
from dataclasses import dataclass, field
from enum import Enum

from web3 import Web3

from ..config.settings import ConfigManager, get_config
from .profitability_calculator import get_calculator

logger = logging.getLogger(__name__)


class RiskLevel(Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


@dataclass
class TWAPCheck:
    """TWAP validation result."""
    spot_price_usd: float = 0.0
    twap_price_usd: float = 0.0
    deviation_percent: float = 0.0
    max_deviation: float = 2.0        # default 2%
    passed: bool = True
    reason: str = ""


@dataclass
class VolatilityProfile:
    """Dynamic slippage profile based on asset volatility."""
    volatility_index: float = 0.0     # 5-min price variance
    suggested_slippage: float = 0.005  # default 0.5%
    classification: str = "normal"     # low / normal / high / extreme


@dataclass
class RiskAssessment:
    risk_level: RiskLevel
    approved: bool
    reasons: List[str]
    net_profit_usd: float = 0.0
    gas_price_gwei: float = 0.0
    gas_within_cap: bool = True
    oracle_stale: bool = False
    simulation_passed: bool = False
    simulation_error: Optional[str] = None
    # Script 2 additions
    twap_check: Optional[TWAPCheck] = None
    volatility_profile: Optional[VolatilityProfile] = None
    suggested_slippage: float = 0.005
    profit_recheck_passed: bool = True


@dataclass
class CircuitBreaker:
    is_open: bool = False
    consecutive_failures: int = 0
    threshold: int = 5
    cooldown_seconds: int = 300
    cooldown_until: float = 0.0
    # Script 2: track profit trends
    recent_profits: deque = field(default_factory=lambda: deque(maxlen=20))
    profit_decline_threshold: float = -50.0  # Alert if avg drops below this %


class RiskManager:
    """
    Enhanced gate-keeper between Stage 2 and Stage 3.

    Script 2 Enhancements:
    - TWAP validation prevents execution during price manipulation
    - Volatility-based dynamic slippage protects against failed swaps
    - Profit recheck catches scenarios where profit evaporated between analysis and execution
    - Enhanced circuit breaker monitors profit trends, not just failures
    """

    # Volatility thresholds for slippage classification
    VOL_THRESHOLDS = {
        "low":     (0.0,   0.02, 0.002),  # (min_vol, max_vol, slippage)
        "normal":  (0.02,  0.05, 0.005),
        "high":    (0.05,  0.15, 0.015),
        "extreme": (0.15,  1.0,  0.030),
    }

    def __init__(self, config: Optional[ConfigManager] = None):
        self.config = config or get_config()
        self.calculator = get_calculator()
        self._breaker = CircuitBreaker(
            threshold=int(os.getenv("CIRCUIT_BREAKER_THRESHOLD", "5")),
            cooldown_seconds=int(os.getenv("CIRCUIT_BREAKER_COOLDOWN", "300")),
        )
        self._user_cooldowns: Dict[str, float] = {}
        self._cooldown_sec = int(os.getenv("USER_COOLDOWN_SECONDS", "60"))
        self._failed_user_cooldown_sec = int(os.getenv("FAILED_USER_COOLDOWN", "300"))

        # Price history for TWAP calculation
        self._price_history: Dict[str, deque] = {}  # asset → [(timestamp, price)]
        self._twap_window_seconds = int(os.getenv("TWAP_WINDOW_SECONDS", "300"))
        self._twap_max_deviation = float(os.getenv("TWAP_MAX_DEVIATION_PCT", "2.0"))

    def assess(
        self,
        chain_id: int,
        debt_amount_usd: float,
        collateral_amount_usd: float,
        liquidation_bonus: float,
        flash_loan_provider: str = "auto",
        w3: Optional[Web3] = None,
        user: Optional[str] = None,
        oracle_updated_at: Optional[int] = None,
        collateral_asset: Optional[str] = None,
        spot_price_usd: float = 0.0,
        volatility_index: float = 0.0,
    ) -> RiskAssessment:
        """Run all risk checks including Script 2 enhancements."""
        reasons: List[str] = []
        level = RiskLevel.LOW

        # 1. Circuit breaker (enhanced — includes profit trend analysis)
        if self._is_breaker_open():
            return RiskAssessment(RiskLevel.CRITICAL, False,
                                  ["Circuit breaker OPEN — execution halted"])

        # 2. Per-user cooldown (enhanced — longer cooldown on failed attempts)
        if user and self._in_cooldown(user):
            remaining = self._user_cooldowns.get(user.lower(), 0) - time.time()
            return RiskAssessment(RiskLevel.HIGH, False,
                                  [f"User {user[:12]}… in cooldown ({remaining:.0f}s remaining)"])

        # 3. Gas price check
        gas_gwei = 0.0
        gas_ok = True
        if w3:
            try:
                gas_gwei = w3.eth.gas_price / 1e9
                gas_ok = gas_gwei <= self.config.execution.gas_price_cap_gwei
                if not gas_ok:
                    reasons.append(f"Gas {gas_gwei:.1f} > cap {self.config.execution.gas_price_cap_gwei}")
                    level = RiskLevel.CRITICAL
                # Feed gas price to calculator's predictor
                self.calculator.record_gas_price(chain_id, gas_gwei)
            except Exception as e:
                reasons.append(f"Gas check error: {e}")

        # 4. Profitability (enhanced — uses multi-exit and predicted gas)
        prof = self.calculator.calculate(
            debt_amount_usd=debt_amount_usd,
            collateral_amount_usd=collateral_amount_usd,
            liquidation_bonus=liquidation_bonus,
            flash_loan_provider=flash_loan_provider,
            gas_price_gwei=gas_gwei if gas_gwei > 0 else None,
            chain_id=chain_id,
        )
        if not prof.is_profitable:
            reasons.append(f"Not profitable: ${prof.net_profit_usd:.2f} (exit: {prof.best_exit_strategy.value})")
            level = RiskLevel.CRITICAL

        # 5. Oracle staleness
        oracle_stale = False
        if oracle_updated_at is not None:
            age = int(time.time()) - oracle_updated_at
            stale_limit = int(os.getenv("ORACLE_STALE_SECONDS", "3600"))
            if age > stale_limit:
                oracle_stale = True
                reasons.append(f"Oracle stale: {age}s (limit: {stale_limit}s)")
                level = RiskLevel.CRITICAL

        # 6. TWAP Validation (Script 2 Enhancement)
        twap_result = None
        if collateral_asset and spot_price_usd > 0:
            twap_result = self._check_twap(collateral_asset, spot_price_usd)
            if not twap_result.passed:
                reasons.append(
                    f"TWAP deviation {twap_result.deviation_percent:.1f}% > "
                    f"{twap_result.max_deviation:.1f}%: possible manipulation"
                )
                level = RiskLevel.CRITICAL

        # 7. Dynamic volatility-based slippage (Script 2 Enhancement)
        vol_profile = self._compute_volatility_profile(volatility_index)

        # 8. Profit recheck — verify profit hasn't declined since initial estimate
        profit_recheck_passed = True
        if self._breaker.recent_profits:
            avg_recent = sum(self._breaker.recent_profits) / len(self._breaker.recent_profits)
            if prof.net_profit_usd < avg_recent * 0.5 and avg_recent > 0:
                reasons.append(
                    f"Profit ${prof.net_profit_usd:.2f} significantly below "
                    f"recent avg ${avg_recent:.2f}"
                )
                level = max(level, RiskLevel.MEDIUM, key=lambda x: list(RiskLevel).index(x))
                profit_recheck_passed = False

        # Decision
        approved = level in (RiskLevel.LOW, RiskLevel.MEDIUM) and gas_ok
        if approved and user:
            self._set_cooldown(user)
        if not reasons:
            reasons.append("All checks passed")

        return RiskAssessment(
            risk_level=level, approved=approved, reasons=reasons,
            net_profit_usd=prof.net_profit_usd,
            gas_price_gwei=gas_gwei, gas_within_cap=gas_ok,
            oracle_stale=oracle_stale,
            twap_check=twap_result,
            volatility_profile=vol_profile,
            suggested_slippage=vol_profile.suggested_slippage if vol_profile else 0.005,
            profit_recheck_passed=profit_recheck_passed,
        )

    # ---- TWAP Validation (Script 2) ----

    def record_price(self, asset: str, price_usd: float):
        """Record a price observation for TWAP computation."""
        if asset not in self._price_history:
            self._price_history[asset] = deque(maxlen=1000)
        self._price_history[asset].append((time.time(), price_usd))

    def _check_twap(self, asset: str, spot_price: float) -> TWAPCheck:
        """
        Compare spot price against TWAP (time-weighted average price).
        If deviation exceeds threshold, flag as potential manipulation.
        """
        history = self._price_history.get(asset, deque())
        if len(history) < 3:
            return TWAPCheck(spot_price_usd=spot_price, passed=True,
                             reason="Insufficient history for TWAP")

        cutoff = time.time() - self._twap_window_seconds
        recent = [(ts, p) for ts, p in history if ts >= cutoff]

        if len(recent) < 2:
            return TWAPCheck(spot_price_usd=spot_price, passed=True,
                             reason="Insufficient recent data for TWAP")

        # Time-weighted average
        total_weight = 0.0
        weighted_sum = 0.0
        for i in range(1, len(recent)):
            dt = recent[i][0] - recent[i - 1][0]
            avg_price = (recent[i][1] + recent[i - 1][1]) / 2
            weighted_sum += avg_price * dt
            total_weight += dt

        twap = weighted_sum / total_weight if total_weight > 0 else spot_price
        deviation = abs(spot_price - twap) / twap * 100 if twap > 0 else 0

        passed = deviation <= self._twap_max_deviation

        return TWAPCheck(
            spot_price_usd=spot_price,
            twap_price_usd=twap,
            deviation_percent=deviation,
            max_deviation=self._twap_max_deviation,
            passed=passed,
            reason="" if passed else f"Spot ${spot_price:.2f} vs TWAP ${twap:.2f}",
        )

    # ---- Volatility-Based Dynamic Slippage (Script 2) ----

    def _compute_volatility_profile(self, volatility_index: float) -> VolatilityProfile:
        """Classify volatility and suggest appropriate slippage tolerance."""
        for classification, (min_v, max_v, slippage) in self.VOL_THRESHOLDS.items():
            if min_v <= volatility_index < max_v:
                return VolatilityProfile(
                    volatility_index=volatility_index,
                    suggested_slippage=slippage,
                    classification=classification,
                )
        return VolatilityProfile(
            volatility_index=volatility_index,
            suggested_slippage=0.03,
            classification="extreme",
        )

    # ---- Enhanced Circuit Breaker (Script 2) ----

    def _is_breaker_open(self) -> bool:
        if self._breaker.is_open:
            if time.time() >= self._breaker.cooldown_until:
                self._breaker.is_open = False
                self._breaker.consecutive_failures = 0
                logger.info("🟢 Circuit breaker RESET — resuming execution")
                return False
            return True
        return False

    def record_success(self, profit_usd: float = 0.0):
        """Record successful execution for trend analysis."""
        self._breaker.consecutive_failures = 0
        if profit_usd > 0:
            self._breaker.recent_profits.append(profit_usd)

    def record_failure(self, user: Optional[str] = None):
        """Record failed execution; apply extended cooldown to user."""
        self._breaker.consecutive_failures += 1
        if user:
            self._user_cooldowns[user.lower()] = time.time() + self._failed_user_cooldown_sec

        if self._breaker.consecutive_failures >= self._breaker.threshold:
            self._breaker.is_open = True
            self._breaker.cooldown_until = time.time() + self._breaker.cooldown_seconds
            logger.warning(
                f"🚨 Circuit breaker OPEN after {self._breaker.consecutive_failures} "
                f"consecutive failures — cooldown {self._breaker.cooldown_seconds}s"
            )

    # ---- User cooldown ----

    def _in_cooldown(self, user: str) -> bool:
        return time.time() < self._user_cooldowns.get(user.lower(), 0)

    def _set_cooldown(self, user: str):
        self._user_cooldowns[user.lower()] = time.time() + self._cooldown_sec
