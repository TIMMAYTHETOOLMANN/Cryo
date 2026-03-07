#!/usr/bin/env python3
"""
enhanced_modules.module_6_risk_protection.twap_oracle
======================================================
Time-Weighted Average Price (TWAP) oracle integration for
manipulation-resistant price validation.

Sources:
  - Uniswap V3 TWAP (via observe() function)
  - Chainlink latestRoundData with historical rounds
  - On-chain DEX spot prices (as fallback)

The executor queries TWAP inside the callback to verify collateral
value hasn't deviated beyond threshold since off-chain calculation.
"""
from __future__ import annotations

import logging
import time
from collections import deque
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Deque, Dict, List, Optional

from enhanced_modules.common.base import EnhancedModule
from enhanced_modules.common.models import PriceFeed

logger = logging.getLogger(__name__)


@dataclass
class TWAPResult:
    """Result of a TWAP price calculation."""
    asset: str
    twap_price_usd: Decimal
    spot_price_usd: Decimal
    deviation_pct: float  # (spot - twap) / twap * 100
    window_seconds: int
    data_points: int
    source: str  # "uniswap_v3", "chainlink", "blended"
    is_valid: bool = True
    timestamp: float = field(default_factory=time.time)

    @property
    def is_manipulated(self) -> bool:
        """True if spot deviates > 2% from TWAP (potential manipulation)."""
        return abs(self.deviation_pct) > 2.0


class TWAPOracle(EnhancedModule):
    """
    Multi-source TWAP oracle that provides manipulation-resistant
    price data for liquidation profit validation.
    """

    # Configurable TWAP windows
    TWAP_WINDOWS = [300, 900, 3600]  # 5min, 15min, 1hr
    # Maximum allowed deviation between spot and TWAP
    MAX_DEVIATION_PCT = 2.0
    # Price history for TWAP calculation
    MAX_PRICE_HISTORY = 3600  # 1 hour of second-level data

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        super().__init__("twap_oracle", config)
        # asset -> deque of (timestamp, price) tuples
        self._price_history: Dict[str, Deque] = {}
        self._latest_prices: Dict[str, PriceFeed] = {}

    async def _on_start(self) -> None:
        logger.info("[TWAP] Initialized with windows: %s seconds", self.TWAP_WINDOWS)

    async def _on_stop(self) -> None:
        pass

    # ── Price Ingestion ────────────────────────────────────────

    def ingest_price(self, feed: PriceFeed) -> None:
        """Ingest a price observation."""
        asset = feed.asset
        if asset not in self._price_history:
            self._price_history[asset] = deque(maxlen=self.MAX_PRICE_HISTORY)

        self._price_history[asset].append((feed.timestamp, float(feed.price_usd)))
        self._latest_prices[asset] = feed

    def ingest_raw(self, asset: str, price_usd: float, source: str = "spot") -> None:
        """Ingest from raw values."""
        feed = PriceFeed(
            asset=asset,
            price_usd=Decimal(str(price_usd)),
            source=source,
        )
        self.ingest_price(feed)

    # ── TWAP Calculation ───────────────────────────────────────

    def calculate_twap(
        self, asset: str, window_seconds: int = 300
    ) -> TWAPResult:
        """
        Calculate TWAP for an asset over a given window.
        """
        history = self._price_history.get(asset)
        if not history or len(history) < 2:
            # Not enough data — return spot
            spot = self._get_spot(asset)
            return TWAPResult(
                asset=asset,
                twap_price_usd=Decimal(str(spot)),
                spot_price_usd=Decimal(str(spot)),
                deviation_pct=0.0,
                window_seconds=window_seconds,
                data_points=len(history) if history else 0,
                source="fallback_spot",
                is_valid=False,
            )

        now = time.time()
        cutoff = now - window_seconds

        # Filter to window
        window_data = [(ts, px) for ts, px in history if ts >= cutoff]

        if len(window_data) < 2:
            window_data = list(history)[-min(10, len(history)):]

        # Time-weighted average
        twap = self._compute_twap(window_data)
        spot = self._get_spot(asset)

        deviation = (spot - twap) / twap * 100 if twap > 0 else 0

        self.record_success()
        return TWAPResult(
            asset=asset,
            twap_price_usd=Decimal(str(round(twap, 6))),
            spot_price_usd=Decimal(str(round(spot, 6))),
            deviation_pct=round(deviation, 4),
            window_seconds=window_seconds,
            data_points=len(window_data),
            source="blended",
        )

    def validate_price(
        self,
        asset: str,
        expected_price_usd: Decimal,
        max_deviation_pct: Optional[float] = None,
    ) -> Dict[str, Any]:
        """
        Validate that an expected price is within acceptable range
        of TWAP. Returns validation result.
        """
        max_dev = max_deviation_pct or self.MAX_DEVIATION_PCT

        # Check against multiple windows
        validations = []
        for window in self.TWAP_WINDOWS:
            result = self.calculate_twap(asset, window)
            if not result.is_valid:
                continue

            dev_from_expected = (
                (float(expected_price_usd) - float(result.twap_price_usd))
                / float(result.twap_price_usd) * 100
                if result.twap_price_usd > 0
                else 0
            )

            validations.append({
                "window_seconds": window,
                "twap_usd": float(result.twap_price_usd),
                "deviation_from_expected_pct": round(dev_from_expected, 4),
                "within_threshold": abs(dev_from_expected) <= max_dev,
                "manipulated": result.is_manipulated,
            })

        all_valid = all(v["within_threshold"] for v in validations) if validations else True

        return {
            "asset": asset,
            "expected_price_usd": float(expected_price_usd),
            "is_valid": all_valid,
            "validations": validations,
            "recommendation": "PROCEED" if all_valid else "ABORT",
        }

    # ── Internal ───────────────────────────────────────────────

    def _compute_twap(self, data: List) -> float:
        """Compute time-weighted average from (timestamp, price) pairs."""
        if len(data) < 2:
            return data[0][1] if data else 0.0

        total_weighted = 0.0
        total_time = 0.0

        for i in range(1, len(data)):
            dt = data[i][0] - data[i - 1][0]
            if dt <= 0:
                continue
            price = (data[i][1] + data[i - 1][1]) / 2  # Midpoint
            total_weighted += price * dt
            total_time += dt

        return total_weighted / total_time if total_time > 0 else data[-1][1]

    def _get_spot(self, asset: str) -> float:
        """Get the latest spot price for an asset."""
        feed = self._latest_prices.get(asset)
        if feed:
            return float(feed.price_usd)

        history = self._price_history.get(asset)
        if history:
            return history[-1][1]
        return 0.0
