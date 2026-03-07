#!/usr/bin/env python3
"""
enhanced_modules.module_2_profitability_calculator.gas_price_predictor
======================================================================
Hybrid gas price prediction model combining:
  1. EIP-1559 base fee dynamics (block utilization formula)
  2. Exponential Moving Average (EMA) of recent gas prices
  3. Block utilization trend analysis

Provides 1–3 block-ahead gas price predictions for profitability
rechecks before execution.
"""
from __future__ import annotations

import logging
import math
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Deque, Dict, List, Optional

import numpy as np

from enhanced_modules.common.base import EnhancedModule

logger = logging.getLogger(__name__)


@dataclass
class GasPrediction:
    """A gas price prediction for a future block."""
    predicted_base_fee_gwei: float
    predicted_priority_fee_gwei: float
    predicted_total_gwei: float
    confidence: float
    target_block_offset: int  # +1, +2, +3 from current
    model_used: str
    timestamp: float = field(default_factory=time.time)

    @property
    def predicted_total_wei(self) -> int:
        return int(self.predicted_total_gwei * 1e9)


@dataclass
class BlockSample:
    """A gas observation from a single block."""
    block_number: int
    base_fee_gwei: float
    avg_priority_fee_gwei: float
    gas_used: int
    gas_limit: int
    timestamp: float
    tx_count: int = 0

    @property
    def utilization(self) -> float:
        return self.gas_used / self.gas_limit if self.gas_limit > 0 else 0.5


class GasPricePredictor(EnhancedModule):
    """
    Multi-model gas price predictor with EIP-1559 awareness.

    Uses a sliding window of block samples to predict gas prices
    1–3 blocks ahead. Falls back to simple EMA if insufficient data.
    """

    # EIP-1559 constants
    BASE_FEE_MAX_CHANGE_DENOMINATOR = 8
    ELASTICITY_MULTIPLIER = 2
    TARGET_GAS_USED = 15_000_000  # 50% of 30M limit

    # Prediction parameters
    WINDOW_SIZE = 100  # blocks of history
    EMA_ALPHA = 0.15   # EMA smoothing factor
    MIN_SAMPLES = 10   # minimum before ML kicks in

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        super().__init__("gas_price_predictor", config)
        self._samples: Deque[BlockSample] = deque(maxlen=self.WINDOW_SIZE)
        self._ema_base_fee: Optional[float] = None
        self._ema_priority_fee: Optional[float] = None
        self._prediction_count = 0
        self._prediction_errors: List[float] = []

    async def _on_start(self) -> None:
        logger.info("[GasPredictor] Initialized with window=%d, EMA alpha=%.2f",
                     self.WINDOW_SIZE, self.EMA_ALPHA)

    async def _on_stop(self) -> None:
        if self._prediction_errors:
            avg_error = np.mean(self._prediction_errors[-100:])
            logger.info("[GasPredictor] Avg prediction error (last 100): %.2f gwei", avg_error)

    # ── Data Ingestion ─────────────────────────────────────────

    def ingest_block(self, sample: BlockSample) -> None:
        """Add a new block observation."""
        self._samples.append(sample)

        # Update EMA
        if self._ema_base_fee is None:
            self._ema_base_fee = sample.base_fee_gwei
            self._ema_priority_fee = sample.avg_priority_fee_gwei
        else:
            self._ema_base_fee = (
                self.EMA_ALPHA * sample.base_fee_gwei
                + (1 - self.EMA_ALPHA) * self._ema_base_fee
            )
            self._ema_priority_fee = (
                self.EMA_ALPHA * sample.avg_priority_fee_gwei
                + (1 - self.EMA_ALPHA) * self._ema_priority_fee
            )

    def ingest_raw(
        self,
        block_number: int,
        base_fee_gwei: float,
        avg_priority_fee_gwei: float,
        gas_used: int,
        gas_limit: int,
        timestamp: float,
        tx_count: int = 0,
    ) -> None:
        """Ingest from raw values."""
        self.ingest_block(BlockSample(
            block_number=block_number,
            base_fee_gwei=base_fee_gwei,
            avg_priority_fee_gwei=avg_priority_fee_gwei,
            gas_used=gas_used,
            gas_limit=gas_limit,
            timestamp=timestamp,
            tx_count=tx_count,
        ))

    # ── Prediction ─────────────────────────────────────────────

    def predict(self, blocks_ahead: int = 1) -> GasPrediction:
        """
        Predict gas price `blocks_ahead` blocks in the future.

        Uses the best available model based on data quality.
        """
        self._prediction_count += 1

        if len(self._samples) >= self.MIN_SAMPLES:
            return self._predict_hybrid(blocks_ahead)
        elif self._ema_base_fee is not None:
            return self._predict_ema(blocks_ahead)
        else:
            return self._predict_fallback(blocks_ahead)

    def predict_range(self, max_blocks: int = 3) -> List[GasPrediction]:
        """Predict for blocks +1 through +max_blocks."""
        return [self.predict(i) for i in range(1, max_blocks + 1)]

    def is_profitable_at_gas(
        self,
        expected_profit_usd: float,
        gas_units: int,
        eth_price_usd: float,
        blocks_ahead: int = 1,
    ) -> bool:
        """Quick check: will the trade be profitable at predicted gas?"""
        pred = self.predict(blocks_ahead)
        gas_cost_eth = (pred.predicted_total_gwei * 1e-9) * gas_units
        gas_cost_usd = gas_cost_eth * eth_price_usd
        return expected_profit_usd > gas_cost_usd

    # ── Prediction Models ──────────────────────────────────────

    def _predict_hybrid(self, blocks_ahead: int) -> GasPrediction:
        """
        Hybrid model: EIP-1559 base fee formula + trend analysis.
        """
        latest = self._samples[-1]
        samples_arr = list(self._samples)

        # 1. EIP-1559 base fee projection
        eip1559_base = self._eip1559_project(latest, blocks_ahead)

        # 2. Trend-based adjustment
        trend = self._compute_trend(samples_arr, blocks_ahead)

        # 3. Blend: 60% EIP-1559, 30% trend, 10% EMA
        blended_base = (
            eip1559_base * 0.6
            + trend * 0.3
            + (self._ema_base_fee or latest.base_fee_gwei) * 0.1
        )

        # Priority fee: EMA-based (less volatile)
        priority = self._ema_priority_fee or latest.avg_priority_fee_gwei

        # Confidence decreases with lookahead
        confidence = max(0.5, 0.95 - blocks_ahead * 0.1)

        return GasPrediction(
            predicted_base_fee_gwei=round(blended_base, 4),
            predicted_priority_fee_gwei=round(priority, 4),
            predicted_total_gwei=round(blended_base + priority, 4),
            confidence=confidence,
            target_block_offset=blocks_ahead,
            model_used="hybrid_eip1559_trend",
        )

    def _predict_ema(self, blocks_ahead: int) -> GasPrediction:
        """Simple EMA-based prediction."""
        base = self._ema_base_fee or 30.0
        priority = self._ema_priority_fee or 2.0

        # Slight increase for lookahead uncertainty
        base *= 1.0 + blocks_ahead * 0.02

        return GasPrediction(
            predicted_base_fee_gwei=round(base, 4),
            predicted_priority_fee_gwei=round(priority, 4),
            predicted_total_gwei=round(base + priority, 4),
            confidence=max(0.4, 0.7 - blocks_ahead * 0.1),
            target_block_offset=blocks_ahead,
            model_used="ema",
        )

    def _predict_fallback(self, blocks_ahead: int) -> GasPrediction:
        """Startup fallback with conservative defaults until live data arrives."""
        return GasPrediction(
            predicted_base_fee_gwei=30.0,
            predicted_priority_fee_gwei=2.0,
            predicted_total_gwei=32.0,
            confidence=0.3,
            target_block_offset=blocks_ahead,
            model_used="fallback",
        )

    # ── EIP-1559 Base Fee Projection ───────────────────────────

    def _eip1559_project(self, block: BlockSample, steps: int) -> float:
        """
        Project base fee using the EIP-1559 formula:
          new_base = old_base * (1 + change_factor)
          change_factor = (gas_used - target) / target / 8
        """
        base = block.base_fee_gwei
        utilization = block.utilization

        for _ in range(steps):
            gas_used_estimate = utilization * block.gas_limit
            target = block.gas_limit // self.ELASTICITY_MULTIPLIER

            if gas_used_estimate > target:
                delta = gas_used_estimate - target
                change = base * delta / target / self.BASE_FEE_MAX_CHANGE_DENOMINATOR
                base += change
            else:
                delta = target - gas_used_estimate
                change = base * delta / target / self.BASE_FEE_MAX_CHANGE_DENOMINATOR
                base = max(0, base - change)

        return base

    # ── Trend Analysis ─────────────────────────────────────────

    def _compute_trend(self, samples: List[BlockSample], lookahead: int) -> float:
        """Compute trend-based prediction using linear regression."""
        if len(samples) < 5:
            return samples[-1].base_fee_gwei if samples else 30.0

        recent = samples[-min(20, len(samples)):]
        x = np.arange(len(recent), dtype=np.float64)
        y = np.array([s.base_fee_gwei for s in recent], dtype=np.float64)

        # Simple linear regression
        x_mean = np.mean(x)
        y_mean = np.mean(y)
        num = np.sum((x - x_mean) * (y - y_mean))
        den = np.sum((x - x_mean) ** 2)
        slope = num / den if den != 0 else 0
        intercept = y_mean - slope * x_mean

        predicted = intercept + slope * (len(recent) + lookahead - 1)
        return max(0, predicted)

    # ── Validation ─────────────────────────────────────────────

    def record_actual(self, block_number: int, actual_base_fee_gwei: float) -> None:
        """Record actual gas price for prediction accuracy tracking."""
        # Find the prediction we made for this block
        if self._samples:
            latest = self._samples[-1]
            if latest.block_number < block_number:
                # We made a prediction — compare
                offset = block_number - latest.block_number
                if offset <= 3:
                    pred = self.predict(offset)
                    error = abs(pred.predicted_base_fee_gwei - actual_base_fee_gwei)
                    self._prediction_errors.append(error)

    def get_accuracy_stats(self) -> Dict[str, float]:
        """Return prediction accuracy statistics."""
        if not self._prediction_errors:
            return {"avg_error_gwei": 0.0, "samples": 0}

        errors = self._prediction_errors[-100:]
        return {
            "avg_error_gwei": round(float(np.mean(errors)), 4),
            "max_error_gwei": round(float(np.max(errors)), 4),
            "p95_error_gwei": round(float(np.percentile(errors, 95)), 4),
            "samples": len(errors),
            "total_predictions": self._prediction_count,
        }
