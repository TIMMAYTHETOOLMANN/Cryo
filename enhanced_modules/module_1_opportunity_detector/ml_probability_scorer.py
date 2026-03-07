#!/usr/bin/env python3
"""
enhanced_modules.module_1_opportunity_detector.ml_probability_scorer
====================================================================
Replaces static health-factor thresholds with a **dynamic ML-based
liquidation probability scorer** trained on historical position data.

Features used for prediction:
  - health_factor (current)
  - volatility_index (30-min rolling σ of collateral price)
  - oracle_update_frequency (AnswerUpdated events / hour)
  - cross_protocol_exposure (total debt across all protocols for this user)
  - debt_amount_usd
  - collateral_amount_usd
  - collateral_type_encoded (one-hot or label-encoded asset class)
  - time_since_last_liquidation (seconds since this user was last liquidated)
  - block_utilization (target chain gas usage %)

Outputs:
  - liquidation_probability ∈ [0, 1]
  - recommended_action: MONITOR | PREPARE | EXECUTE_NOW
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

try:
    from sklearn.ensemble import GradientBoostingClassifier
    from sklearn.model_selection import train_test_split
    from sklearn.metrics import roc_auc_score
    import joblib

    _ML_AVAILABLE = True
except ImportError:
    _ML_AVAILABLE = False

from enhanced_modules.common.base import EnhancedModule
from enhanced_modules.common.models import EnrichedPosition

logger = logging.getLogger(__name__)

# ── Constants ──────────────────────────────────────────────────────

FEATURE_NAMES = [
    "health_factor",
    "volatility_index",
    "oracle_update_freq",
    "cross_protocol_exposure_usd",
    "debt_usd",
    "collateral_usd",
    "collateral_ratio",
    "time_since_last_liq_hrs",
    "block_utilization",
]

MODEL_DIR = Path(os.getenv("CRYO_MODEL_DIR", "models"))
MODEL_PATH = MODEL_DIR / "liquidation_prob_scorer_v2.pkl"


@dataclass
class ScoringResult:
    """Output of the ML probability scorer."""
    borrower: str
    protocol: str
    chain_id: int
    health_factor: float
    liquidation_probability: float
    action: str  # MONITOR | PREPARE | EXECUTE_NOW
    confidence: float
    features: Dict[str, float] = field(default_factory=dict)
    scored_at: float = field(default_factory=time.time)


class MLProbabilityScorer(EnhancedModule):
    """
    Gradient Boosting-based liquidation probability scorer.

    Instead of a static `healthFactor < 1.05` check, this scores every
    position on a [0, 1] probability scale. Positions with probability
    > 0.70 are flagged for preparation even if current HF > 1.05.
    """

    # Action thresholds
    EXECUTE_THRESHOLD = 0.85
    PREPARE_THRESHOLD = 0.70
    MONITOR_THRESHOLD = 0.40

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        super().__init__("ml_probability_scorer", config)
        self._model: Optional[GradientBoostingClassifier] = None
        self._training_data: List[Tuple[np.ndarray, int]] = []
        self._scores_cache: Dict[str, ScoringResult] = {}
        self._cache_ttl = float(self.config.get("score_cache_ttl", 30.0))
        self._model_version = 0

    # ── Lifecycle ──────────────────────────────────────────────

    async def _on_start(self) -> None:
        if not _ML_AVAILABLE:
            logger.warning("[MLScorer] scikit-learn not available — using fallback heuristic")
            return
        # Try to load persisted model
        if MODEL_PATH.exists():
            try:
                self._model = joblib.load(MODEL_PATH)
                self._model_version = 1
                logger.info("[MLScorer] Loaded model from %s", MODEL_PATH)
            except Exception as exc:
                logger.warning("[MLScorer] Could not load model: %s — will train fresh", exc)

    async def _on_stop(self) -> None:
        # Persist model if we have one
        if self._model and _ML_AVAILABLE:
            try:
                MODEL_DIR.mkdir(parents=True, exist_ok=True)
                joblib.dump(self._model, MODEL_PATH)
                logger.info("[MLScorer] Persisted model to %s", MODEL_PATH)
            except Exception as exc:
                logger.warning("[MLScorer] Could not persist model: %s", exc)

    # ── Scoring ────────────────────────────────────────────────

    async def score_position(self, position: EnrichedPosition) -> ScoringResult:
        """
        Score a single enriched position.  Returns probability + action.
        """
        cache_key = f"{position.borrower}:{position.protocol}:{position.chain_id}"
        now = time.time()

        # Check cache
        if cache_key in self._scores_cache:
            cached = self._scores_cache[cache_key]
            if now - cached.scored_at < self._cache_ttl:
                return cached

        features = self._extract_features(position)

        if self._model and _ML_AVAILABLE:
            probability = await self._predict_ml(features)
        else:
            probability = self._heuristic_score(position)

        action = self._determine_action(probability, position.health_factor)
        confidence = min(0.95, probability * 1.1) if self._model else 0.6

        result = ScoringResult(
            borrower=position.borrower,
            protocol=position.protocol,
            chain_id=position.chain_id,
            health_factor=position.health_factor,
            liquidation_probability=round(probability, 4),
            action=action,
            confidence=round(confidence, 4),
            features={FEATURE_NAMES[i]: float(features[i]) for i in range(len(FEATURE_NAMES))},
        )

        self._scores_cache[cache_key] = result
        self.record_success()
        return result

    async def score_batch(
        self, positions: List[EnrichedPosition]
    ) -> List[ScoringResult]:
        """Score multiple positions concurrently."""
        tasks = [self.score_position(p) for p in positions]
        return await asyncio.gather(*tasks)

    def get_actionable(
        self, results: List[ScoringResult], min_prob: float = 0.70
    ) -> List[ScoringResult]:
        """Filter results to only those exceeding probability threshold."""
        return sorted(
            [r for r in results if r.liquidation_probability >= min_prob],
            key=lambda r: r.liquidation_probability,
            reverse=True,
        )

    # ── Training ───────────────────────────────────────────────

    def add_training_sample(
        self, position: EnrichedPosition, was_liquidated: bool
    ) -> None:
        """Add an observed outcome to the training buffer."""
        features = self._extract_features(position)
        self._training_data.append((features, int(was_liquidated)))

    async def train(self, min_samples: int = 200) -> Dict[str, float]:
        """
        Train / retrain the model using accumulated data.
        Returns metrics dict.
        """
        if not _ML_AVAILABLE:
            return {"error": "scikit-learn not available"}

        if len(self._training_data) < min_samples:
            return {"error": f"Need {min_samples} samples, have {len(self._training_data)}"}

        X = np.array([s[0] for s in self._training_data])
        y = np.array([s[1] for s in self._training_data])

        X_train, X_test, y_train, y_test = train_test_split(
            X, y, test_size=0.2, random_state=42, stratify=y
        )

        model = GradientBoostingClassifier(
            n_estimators=200,
            max_depth=5,
            learning_rate=0.1,
            subsample=0.8,
            min_samples_leaf=10,
            random_state=42,
        )

        # Run training in executor to avoid blocking event loop
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, model.fit, X_train, y_train)

        # Evaluate
        y_pred = model.predict_proba(X_test)[:, 1]
        auc = roc_auc_score(y_test, y_pred)

        self._model = model
        self._model_version += 1

        # Persist
        MODEL_DIR.mkdir(parents=True, exist_ok=True)
        joblib.dump(model, MODEL_PATH)

        metrics = {
            "auc_roc": round(auc, 4),
            "train_samples": len(X_train),
            "test_samples": len(X_test),
            "model_version": self._model_version,
            "feature_importance": {
                FEATURE_NAMES[i]: round(float(v), 4)
                for i, v in enumerate(model.feature_importances_)
            },
        }
        logger.info("[MLScorer] Trained model v%d — AUC: %.4f", self._model_version, auc)
        return metrics

    # ── Internal ───────────────────────────────────────────────

    def _extract_features(self, pos: EnrichedPosition) -> np.ndarray:
        """Extract a fixed-length feature vector from an enriched position."""
        collateral_ratio = (
            float(pos.collateral_usd) / float(pos.debt_usd)
            if pos.debt_usd > 0
            else 10.0
        )
        time_since_liq = pos.metadata.get("time_since_last_liq_sec", 86400) / 3600.0
        block_util = pos.metadata.get("block_utilization", 0.5)

        return np.array([
            pos.health_factor,
            pos.volatility_index,
            pos.oracle_update_frequency,
            float(pos.cross_protocol_exposure),
            float(pos.debt_usd),
            float(pos.collateral_usd),
            collateral_ratio,
            time_since_liq,
            block_util,
        ], dtype=np.float64)

    async def _predict_ml(self, features: np.ndarray) -> float:
        """Run the GBM model for a single feature vector."""
        loop = asyncio.get_running_loop()
        proba = await loop.run_in_executor(
            None, lambda: self._model.predict_proba(features.reshape(1, -1))[0, 1]
        )
        return float(proba)

    def _heuristic_score(self, pos: EnrichedPosition) -> float:
        """
        Fallback heuristic when ML model is unavailable.
        Uses a weighted combination of health factor, volatility, and exposure.
        """
        hf = pos.health_factor
        if hf <= 1.0:
            base = 0.95
        elif hf <= 1.02:
            base = 0.85
        elif hf <= 1.05:
            base = 0.65
        elif hf <= 1.10:
            base = 0.35
        elif hf <= 1.20:
            base = 0.15
        else:
            base = 0.05

        # Volatility boost (high vol → higher probability)
        vol_boost = min(0.15, pos.volatility_index * 0.3)

        # Cross-protocol exposure boost
        exposure_boost = 0.0
        if pos.cross_protocol_exposure > 0:
            exposure_pct = float(pos.cross_protocol_exposure) / max(float(pos.debt_usd), 1)
            exposure_boost = min(0.10, exposure_pct * 0.05)

        # Oracle staleness boost
        oracle_boost = 0.0
        if pos.oracle_update_frequency < 1.0:  # less than 1 update/hour = stale
            oracle_boost = 0.05

        return min(0.99, base + vol_boost + exposure_boost + oracle_boost)

    def _determine_action(self, probability: float, health_factor: float) -> str:
        """Map probability to recommended action."""
        if probability >= self.EXECUTE_THRESHOLD or health_factor < 1.0:
            return "EXECUTE_NOW"
        elif probability >= self.PREPARE_THRESHOLD:
            return "PREPARE"
        elif probability >= self.MONITOR_THRESHOLD:
            return "MONITOR"
        return "IGNORE"
