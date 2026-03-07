#!/usr/bin/env python3
"""
enhanced_modules.module_6_risk_protection.dynamic_slippage
============================================================
Computes volatility-responsive slippage tolerance for DEX swaps.

Instead of static slippage (e.g., 1%), this calculator uses:
  - 5-minute price variance (on-chain data)
  - DEX pool depth / liquidity
  - Asset volatility class (stablecoin vs volatile)
  - Trade size relative to pool TVL

Higher volatility → wider tolerance (prevent failed swaps).
Lower volatility → tighter tolerance (maximize protection).
"""
from __future__ import annotations

import logging
import math
import time
from collections import deque
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Deque, Dict, List, Optional

import numpy as np

from enhanced_modules.common.base import EnhancedModule

logger = logging.getLogger(__name__)


class AssetVolatilityClass:
    STABLECOIN = "stablecoin"
    LOW_VOL = "low_vol"
    MEDIUM_VOL = "medium_vol"
    HIGH_VOL = "high_vol"
    EXTREME_VOL = "extreme_vol"


# Base slippage by volatility class (bps)
BASE_SLIPPAGE_BPS = {
    AssetVolatilityClass.STABLECOIN: 5,
    AssetVolatilityClass.LOW_VOL: 30,
    AssetVolatilityClass.MEDIUM_VOL: 80,
    AssetVolatilityClass.HIGH_VOL: 150,
    AssetVolatilityClass.EXTREME_VOL: 300,
}

# Known stablecoins
STABLECOINS = {"USDC", "USDT", "DAI", "FRAX", "LUSD", "crvUSD", "GHO", "PYUSD"}


@dataclass
class SlippageRecommendation:
    """Recommended slippage for a swap."""
    asset: str
    recommended_slippage_bps: int
    min_amount_out_pct: float  # 1.0 - slippage
    volatility_class: str
    volatility_5min: float
    pool_depth_factor: float
    trade_size_factor: float
    reasoning: str


class DynamicSlippageCalculator(EnhancedModule):
    """
    Calculates optimal slippage tolerance based on real-time volatility
    and liquidity conditions.
    """

    # Safety bounds
    MIN_SLIPPAGE_BPS = 3     # 0.03% absolute minimum
    MAX_SLIPPAGE_BPS = 500   # 5% absolute maximum

    # Volatility thresholds (5-min annualized)
    VOL_THRESHOLDS = {
        0.02: AssetVolatilityClass.STABLECOIN,
        0.20: AssetVolatilityClass.LOW_VOL,
        0.50: AssetVolatilityClass.MEDIUM_VOL,
        1.00: AssetVolatilityClass.HIGH_VOL,
    }

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        super().__init__("dynamic_slippage_calculator", config)
        # asset -> deque of recent prices (5-min window)
        self._price_buffer: Dict[str, Deque] = {}
        self._pool_tvl: Dict[str, float] = {}  # asset -> TVL in USD

    async def _on_start(self) -> None:
        logger.info("[DynamicSlippage] Initialized with bounds [%d, %d] bps",
                     self.MIN_SLIPPAGE_BPS, self.MAX_SLIPPAGE_BPS)

    async def _on_stop(self) -> None:
        pass

    # ── Price Ingestion ────────────────────────────────────────

    def update_price(self, asset: str, price_usd: float) -> None:
        """Add a price observation for volatility calculation."""
        if asset not in self._price_buffer:
            self._price_buffer[asset] = deque(maxlen=300)  # 5 min at 1/sec
        self._price_buffer[asset].append((time.time(), price_usd))

    def update_pool_tvl(self, asset: str, tvl_usd: float) -> None:
        """Update the known pool TVL for an asset."""
        self._pool_tvl[asset] = tvl_usd

    # ── Slippage Calculation ───────────────────────────────────

    def calculate(
        self,
        asset: str,
        trade_size_usd: float,
    ) -> SlippageRecommendation:
        """
        Calculate recommended slippage for a swap.
        """
        # 1. Determine volatility class
        vol_5min = self._compute_5min_volatility(asset)
        vol_class = self._classify_volatility(asset, vol_5min)

        # 2. Base slippage from volatility class
        base_bps = BASE_SLIPPAGE_BPS[vol_class]

        # 3. Pool depth adjustment
        pool_tvl = self._pool_tvl.get(asset, 10_000_000)  # Default 10M
        depth_factor = self._pool_depth_factor(trade_size_usd, pool_tvl)

        # 4. Trade size adjustment
        size_factor = self._trade_size_factor(trade_size_usd, pool_tvl)

        # 5. Combine factors
        adjusted_bps = int(base_bps * depth_factor * size_factor)
        final_bps = max(self.MIN_SLIPPAGE_BPS, min(self.MAX_SLIPPAGE_BPS, adjusted_bps))

        min_out_pct = 1.0 - final_bps / 10000

        self.record_success()
        return SlippageRecommendation(
            asset=asset,
            recommended_slippage_bps=final_bps,
            min_amount_out_pct=round(min_out_pct, 6),
            volatility_class=vol_class,
            volatility_5min=round(vol_5min, 6),
            pool_depth_factor=round(depth_factor, 3),
            trade_size_factor=round(size_factor, 3),
            reasoning=(
                f"Base={base_bps}bps ({vol_class}), "
                f"depth_adj={depth_factor:.2f}x, "
                f"size_adj={size_factor:.2f}x → {final_bps}bps"
            ),
        )

    # ── Volatility Computation ─────────────────────────────────

    def _compute_5min_volatility(self, asset: str) -> float:
        """Compute 5-minute annualized volatility from price buffer."""
        buffer = self._price_buffer.get(asset)
        if not buffer or len(buffer) < 10:
            # Default moderate volatility
            return 0.30 if asset not in STABLECOINS else 0.005

        prices = [p for _, p in buffer]
        if len(prices) < 2:
            return 0.0

        # Log returns
        log_returns = np.diff(np.log(np.array(prices, dtype=np.float64)))
        if len(log_returns) == 0:
            return 0.0

        # Standard deviation of returns
        std = float(np.std(log_returns))

        # Annualize (assume ~1 observation/second, 300s window)
        # Annualization factor: sqrt(365 * 24 * 3600)
        annual_factor = math.sqrt(365 * 24 * 3600)
        return std * annual_factor

    def _classify_volatility(self, asset: str, vol: float) -> str:
        """Classify volatility into a class."""
        if asset in STABLECOINS:
            return AssetVolatilityClass.STABLECOIN

        for threshold, vol_class in sorted(self.VOL_THRESHOLDS.items()):
            if vol <= threshold:
                return vol_class
        return AssetVolatilityClass.EXTREME_VOL

    @staticmethod
    def _pool_depth_factor(trade_size_usd: float, pool_tvl: float) -> float:
        """Adjust slippage based on pool depth. Thin pools = wider slippage."""
        ratio = trade_size_usd / max(pool_tvl, 1)
        if ratio < 0.001:
            return 0.8  # Very small trade, tighten
        elif ratio < 0.01:
            return 1.0  # Normal
        elif ratio < 0.05:
            return 1.5  # Medium impact
        elif ratio < 0.10:
            return 2.5  # Large impact
        else:
            return 4.0  # Very large — extremely wide

    @staticmethod
    def _trade_size_factor(trade_size_usd: float, pool_tvl: float) -> float:
        """Additional factor for absolute trade size."""
        if trade_size_usd < 1000:
            return 0.8  # Small trade, tighten
        elif trade_size_usd < 50000:
            return 1.0
        elif trade_size_usd < 500000:
            return 1.3
        else:
            return 1.8  # Very large trade
