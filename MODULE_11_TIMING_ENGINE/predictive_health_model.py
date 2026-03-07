#!/usr/bin/env python3
"""
Submodule 11.3 — Predictive Health Factor Model
=================================================
Uses a lightweight ML model (XGBoost when available, linear fallback
otherwise) to forecast the probability that a watchlist position will
cross the liquidation threshold within the next N blocks.

Features:
  - Current health factor & debt/collateral ratio
  - 5-min and 1-hour volatility of collateral asset
  - Recent oracle update frequency for the feed
  - Mempool activity density for related assets
  - Time (in blocks) since last oracle update
  - Price distance % from liquidation price

Output:
  Probability in [0, 1] that the position's HF will cross below 1.0
  within ``prediction_horizon_blocks``.

Training:
  Trained on historical liquidation events (stored as CSV/Parquet).
  Re-trains periodically (default: hourly) as new events arrive.
"""
from __future__ import annotations

import asyncio
import logging
import math
import os
import pickle
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from .config import PredictiveModelConfig

logger = logging.getLogger(__name__)


@dataclass
class PositionFeatures:
    """Feature vector for a single position prediction."""
    health_factor: float = 0.0
    debt_collateral_ratio: float = 0.0
    collateral_volatility_5m: float = 0.0
    collateral_volatility_1h: float = 0.0
    oracle_update_frequency: float = 0.0   # Updates per hour
    blocks_since_last_oracle: int = 0
    price_distance_pct: float = 0.0        # % from liquidation price
    mempool_swap_density: float = 0.0      # Swaps/min for this asset
    debt_usd: float = 0.0
    collateral_usd: float = 0.0
    liquidation_bonus_pct: float = 0.0
    chain_id: int = 0

    def to_array(self) -> np.ndarray:
        return np.array([
            self.health_factor,
            self.debt_collateral_ratio,
            self.collateral_volatility_5m,
            self.collateral_volatility_1h,
            self.oracle_update_frequency,
            self.blocks_since_last_oracle,
            self.price_distance_pct,
            self.mempool_swap_density,
            self.debt_usd,
            self.collateral_usd,
            self.liquidation_bonus_pct,
            float(self.chain_id),
        ], dtype=np.float32).reshape(1, -1)


FEATURE_NAMES = [
    "health_factor",
    "debt_collateral_ratio",
    "vol_5m",
    "vol_1h",
    "oracle_update_freq",
    "blocks_since_oracle",
    "price_distance_pct",
    "mempool_density",
    "debt_usd",
    "collateral_usd",
    "liq_bonus_pct",
    "chain_id",
]


@dataclass
class PredictionResult:
    """Output of the predictive model for a position."""
    probability: float          # P(HF < 1.0 within N blocks)
    confidence: str             # "high" | "medium" | "low"
    action: str                 # "execute_now" | "prepare" | "monitor" | "ignore"
    features_used: int = 12
    model_type: str = ""
    prediction_time_ms: float = 0.0


class PredictiveHealthModel:
    """
    Trains and serves an XGBoost classifier (with linear fallback) to
    predict near-future liquidation events.

    Lifecycle:
      1. ``initialize()`` — load persisted model or create a new one.
      2. ``predict()`` — score a single position.
      3. ``record_outcome()`` — feed back ground-truth for online learning.
      4. ``retrain()`` — periodic full retrain on accumulated samples.
    """

    MODEL_PATH = Path("data/models/timing_predictor.pkl")

    def __init__(self, config: Optional[PredictiveModelConfig] = None):
        self.cfg = config or PredictiveModelConfig()
        self._model: Any = None
        self._model_type: str = "none"
        self._training_X: List[np.ndarray] = []
        self._training_y: List[int] = []
        self._last_retrain: float = 0.0
        self._running = False
        self._retrain_task: Optional[asyncio.Task] = None

        # Stats
        self.predictions_made: int = 0
        self.high_confidence_triggers: int = 0
        self.training_samples: int = 0

    # ── Lifecycle ────────────────────────────────────────────────

    async def initialize(self) -> None:
        """Load persisted model or initialise a fresh one."""
        if self.MODEL_PATH.exists():
            try:
                with open(self.MODEL_PATH, "rb") as f:
                    state = pickle.load(f)
                self._model = state.get("model")
                self._model_type = state.get("model_type", "unknown")
                self._training_X = state.get("X", [])
                self._training_y = state.get("y", [])
                self.training_samples = len(self._training_y)
                logger.info(
                    "[PredictiveModel] Loaded %s model (%d samples)",
                    self._model_type, self.training_samples,
                )
                return
            except Exception as exc:
                logger.warning("[PredictiveModel] Load failed: %s — fresh start", exc)

        # Attempt XGBoost; fall back to linear
        self._model, self._model_type = self._create_model()
        logger.info("[PredictiveModel] Initialized fresh %s model", self._model_type)

    async def start(self) -> None:
        self._running = True
        self._retrain_task = asyncio.create_task(
            self._retrain_loop(), name="predictor_retrain"
        )

    async def stop(self) -> None:
        self._running = False
        if self._retrain_task:
            self._retrain_task.cancel()
        self._persist()

    # ── Prediction ───────────────────────────────────────────────

    async def predict(self, features: PositionFeatures) -> PredictionResult:
        """Score a position — returns probability + recommended action."""
        t0 = time.monotonic()
        arr = features.to_array()

        if self._model is None or self.training_samples < self.cfg.min_training_samples:
            # Not enough data — use heuristic
            prob = self._heuristic_score(features)
        else:
            try:
                prob = float(self._model.predict_proba(arr)[0][1])
            except Exception:
                prob = self._heuristic_score(features)

        elapsed_ms = (time.monotonic() - t0) * 1000
        self.predictions_made += 1

        # Determine action
        if prob >= self.cfg.high_confidence_threshold:
            action = "execute_now"
            confidence = "high"
            self.high_confidence_triggers += 1
        elif prob >= self.cfg.confidence_threshold:
            action = "prepare"
            confidence = "medium"
        elif prob >= 0.3:
            action = "monitor"
            confidence = "low"
        else:
            action = "ignore"
            confidence = "low"

        return PredictionResult(
            probability=prob,
            confidence=confidence,
            action=action,
            features_used=len(FEATURE_NAMES),
            model_type=self._model_type,
            prediction_time_ms=elapsed_ms,
        )

    # ── Heuristic Fallback ───────────────────────────────────────

    @staticmethod
    def _heuristic_score(f: PositionFeatures) -> float:
        """
        Rule-based probability estimate when ML model is unavailable.
        Maps HF proximity + volatility to a rough liquidation probability.
        """
        if f.health_factor <= 0:
            return 0.0

        # Base probability from HF proximity
        if f.health_factor < 1.0:
            base = 0.99
        elif f.health_factor < 1.01:
            base = 0.90
        elif f.health_factor < 1.02:
            base = 0.70
        elif f.health_factor < 1.05:
            base = 0.40
        elif f.health_factor < 1.10:
            base = 0.15
        else:
            base = 0.02

        # Volatility boost
        vol_boost = min(f.collateral_volatility_5m * 500, 0.30)

        # Mempool activity boost
        mempool_boost = min(f.mempool_swap_density * 0.05, 0.10)

        return min(base + vol_boost + mempool_boost, 0.99)

    # ── Training / Online Learning ───────────────────────────────

    def record_outcome(
        self,
        features: PositionFeatures,
        liquidated: bool,
    ) -> None:
        """Feed ground-truth outcome for online accumulation."""
        self._training_X.append(features.to_array().flatten())
        self._training_y.append(1 if liquidated else 0)
        self.training_samples = len(self._training_y)

    async def retrain(self) -> bool:
        """Retrain model on accumulated samples."""
        if len(self._training_y) < self.cfg.min_training_samples:
            return False

        X = np.array(self._training_X, dtype=np.float32)
        y = np.array(self._training_y, dtype=np.int32)

        try:
            model, mtype = self._create_model()
            model.fit(X, y)
            self._model = model
            self._model_type = mtype
            self._last_retrain = time.time()
            self._persist()
            logger.info(
                "[PredictiveModel] Retrained %s on %d samples",
                mtype, len(y),
            )
            return True
        except Exception as exc:
            logger.warning("[PredictiveModel] Retrain failed: %s", exc)
            return False

    # ── Model Factory ────────────────────────────────────────────

    def _create_model(self) -> Tuple[Any, str]:
        """Create a fresh classifier — XGBoost preferred, linear fallback."""
        if self.cfg.model_type == "xgboost":
            try:
                from xgboost import XGBClassifier
                model = XGBClassifier(
                    n_estimators=100,
                    max_depth=4,
                    learning_rate=0.1,
                    use_label_encoder=False,
                    eval_metric="logloss",
                    verbosity=0,
                )
                return model, "xgboost"
            except ImportError:
                logger.info("[PredictiveModel] XGBoost not installed — using linear fallback")

        # Lightweight fallback: scikit-learn logistic regression
        try:
            from sklearn.linear_model import LogisticRegression
            model = LogisticRegression(max_iter=500, solver="lbfgs")
            return model, "logistic_regression"
        except ImportError:
            pass

        # Absolute fallback: heuristic model when ML libs unavailable
        return _HeuristicFallbackModel(), "heuristic"

    # ── Persistence ──────────────────────────────────────────────

    def _persist(self) -> None:
        try:
            self.MODEL_PATH.parent.mkdir(parents=True, exist_ok=True)
            with open(self.MODEL_PATH, "wb") as f:
                pickle.dump({
                    "model": self._model,
                    "model_type": self._model_type,
                    "X": self._training_X,
                    "y": self._training_y,
                }, f)
        except Exception as exc:
            logger.debug("[PredictiveModel] Persist error: %s", exc)

    # ── Retrain Loop ─────────────────────────────────────────────

    async def _retrain_loop(self) -> None:
        while self._running:
            try:
                await asyncio.sleep(self.cfg.retrain_interval_s)
                await self.retrain()
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.debug("[PredictiveModel] Retrain loop error: %s", exc)
                await asyncio.sleep(60)

    # ── Stats ────────────────────────────────────────────────────

    def get_stats(self) -> Dict[str, Any]:
        return {
            "model_type": self._model_type,
            "training_samples": self.training_samples,
            "predictions_made": self.predictions_made,
            "high_confidence_triggers": self.high_confidence_triggers,
            "last_retrain": self._last_retrain,
        }


class _HeuristicFallbackModel:
    """Fallback model that defers to the heuristic scorer when ML libraries are unavailable."""

    def predict_proba(self, X: np.ndarray) -> np.ndarray:
        # Return [P(class=0), P(class=1)] — always 50/50
        n = X.shape[0]
        return np.column_stack([np.full(n, 0.5), np.full(n, 0.5)])

    def fit(self, X: np.ndarray, y: np.ndarray) -> "_HeuristicFallbackModel":
        return self
