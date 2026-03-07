#!/usr/bin/env python3
"""
Module 6 — ML Aggregator & Ranker (The Brain)
================================================
Filters the firehose of signals and routes only the highest-quality
opportunities to execution.

Capabilities:
  6.1  Probabilistic Scoring & Ranking
       - Expected Value: potential profit × probability of success
       - Competition Estimation: how many bots likely see this?
       - Execution Complexity: simple backrun vs. 4-hop cross-chain
       - Gas & Latency Cost: predicted cost on destination chain
       - Urgency Weighting: time-to-opportunity decay
  6.2  Dynamic Strategy Routing
       - Route to liquidation engine, arb bot, MEV strategy, etc.
  6.3  Continuous Model Retraining from Execution Outcomes
       - Online learning from success/failure feedback

Data flow:
  All signals from Modules 1-5 → subscribe on SignalBus
  → extract features → score → rank → push to ranked queue
  → Engine consumes top-N for execution
"""
from __future__ import annotations

import logging
import math
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Any, Deque, Dict, Optional

from .config import MLAggregatorConfig, get_config
from .signal_bus import (
    SignalBus, SignalType, StrategyRoute, TriangulatedSignal,
)

logger = logging.getLogger(__name__)

# Attempt scikit-learn import for GBM model
try:
    from sklearn.ensemble import GradientBoostingClassifier
    import numpy as np
    SKLEARN_AVAILABLE = True
except ImportError:
    GradientBoostingClassifier = None  # type: ignore[assignment,misc]
    np = None  # type: ignore[assignment]
    SKLEARN_AVAILABLE = False


# ── Feature Extraction ───────────────────────────────────────────

FEATURE_NAMES = [
    "expected_value",        # profit * confidence
    "competition",           # 0-1 inverted (low competition = high score)
    "complexity",            # 0-1 inverted
    "gas_cost_ratio",        # gas / profit
    "urgency",               # decayed by time
    "confidence",            # raw confidence
    "profit_usd",            # raw profit estimate
    "chain_popularity",      # higher = more competition on popular chains
    "signal_age_s",          # seconds since signal created
    "source_reliability",    # historical success rate per source
]


@dataclass
class ScoredSignal:
    """A signal with ML-assigned quality score and features."""
    signal: TriangulatedSignal
    features: Dict[str, float] = field(default_factory=dict)
    quality_score: float = 0.0
    strategy_route: StrategyRoute = StrategyRoute.MANUAL_REVIEW


@dataclass
class ExecutionOutcome:
    """Feedback from execution for model retraining."""
    signal_type: str
    source: str
    chain_id: int
    predicted_profit: float
    actual_profit: float
    success: bool
    features: Dict[str, float] = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)


# ── Module ───────────────────────────────────────────────────────

class MLAggregator:
    """
    Module 6: ML Aggregator & Ranker.

    Subscribes to the SignalBus, scores every incoming signal with a
    multi-feature quality model, and pushes ranked signals into the
    bus's priority queue for consumption by the engine.
    """

    def __init__(
        self,
        bus: SignalBus,
        config: Optional[MLAggregatorConfig] = None,
    ):
        self.bus = bus
        self._cfg = config or get_config().ml_aggregator
        self._running = False

        # Feature weights (linear model — upgraded to GBM after training data)
        self._weights = dict(self._cfg.feature_weights)

        # GBM model (trained online from outcomes)
        self._model: Any = None
        self._model_trained = False

        # Source reliability history (source_name → success_rate)
        self._source_stats: Dict[str, Dict[str, int]] = defaultdict(
            lambda: {"attempts": 0, "successes": 0},
        )

        # Chain competition estimates
        self._chain_competition: Dict[int, float] = {
            1: 0.9, 42161: 0.6, 10: 0.5, 8453: 0.4,
            137: 0.5, 56: 0.3, 43114: 0.3,
        }

        # Training data buffer
        self._training_buffer: Deque[ExecutionOutcome] = deque(maxlen=10_000)
        self._last_retrain: float = 0.0

        # Stats
        self._stats = {
            "signals_scored": 0,
            "signals_routed": 0,
            "signals_filtered": 0,
            "model_retrains": 0,
            "avg_quality_score": 0.0,
            "outcomes_recorded": 0,
        }
        self._score_sum = 0.0

    # ── Lifecycle ─────────────────────────────────────────────

    async def start(self):
        self._running = True

        # Subscribe to the bus to auto-score incoming signals
        self.bus.subscribe(self._on_signal)

        # Try to initialise GBM model
        if SKLEARN_AVAILABLE:
            self._model = GradientBoostingClassifier(
                n_estimators=100, max_depth=4, learning_rate=0.1,
            )
            logger.info("[MLAggregator] GBM model ready (awaiting training data)")

        logger.info("[MLAggregator] Started — weights: %s", self._weights)

    async def stop(self):
        self._running = False
        logger.info("[MLAggregator] Stopped — scored %d signals",
                     self._stats["signals_scored"])

    # ── Signal Scoring ───────────────────────────────────────

    def _on_signal(self, signal: TriangulatedSignal):
        """Callback: score an incoming signal and push to ranked queue."""
        if not self._running:
            return

        scored = self._score_signal(signal)

        # Filter below minimum quality
        if scored.quality_score < self._cfg.min_quality_score:
            self._stats["signals_filtered"] += 1
            return

        # Assign score to signal and push to ranked queue
        signal.quality_score = scored.quality_score
        signal.routed_to = scored.strategy_route
        signal.ml_features = scored.features
        self.bus.push_ranked(signal)

        self._stats["signals_scored"] += 1
        self._stats["signals_routed"] += 1
        self._score_sum += scored.quality_score
        self._stats["avg_quality_score"] = (
            self._score_sum / max(1, self._stats["signals_scored"])
        )

    def _score_signal(self, signal: TriangulatedSignal) -> ScoredSignal:
        """Extract features and compute quality score."""
        features = self._extract_features(signal)

        # Use GBM model if trained, otherwise weighted linear scoring
        if self._model_trained and SKLEARN_AVAILABLE and self._model:
            score = self._score_with_model(features)
        else:
            score = self._score_linear(features)

        route = self._route_signal(signal)

        return ScoredSignal(
            signal=signal,
            features=features,
            quality_score=score,
            strategy_route=route,
        )

    def _extract_features(self, signal: TriangulatedSignal) -> Dict[str, float]:
        """Extract numeric features from a signal for scoring."""
        profit = max(0.0, signal.estimated_profit_usd)
        gas = max(0.01, signal.gas_cost_estimate_usd)
        confidence = max(0.0, min(1.0, signal.confidence))
        competition = max(0.0, min(1.0, signal.competition_estimate))
        complexity = max(0.0, min(1.0, signal.execution_complexity))
        age = signal.age_seconds

        # Urgency decay: exponential decay based on urgency window
        urgency_window = max(1.0, signal.urgency_seconds)
        urgency = math.exp(-age / urgency_window)

        # Source reliability
        src_name = signal.source.value
        src_stats = self._source_stats[src_name]
        source_reliability = (
            src_stats["successes"] / max(1, src_stats["attempts"])
            if src_stats["attempts"] > 0 else 0.5
        )

        # Chain competition
        chain_pop = self._chain_competition.get(signal.chain_id, 0.5)

        return {
            "expected_value": profit * confidence,
            "competition": 1.0 - competition,  # Inverted: low competition → high score
            "complexity": 1.0 - complexity,    # Inverted: low complexity → high score
            "gas_cost_ratio": max(0.0, 1.0 - (gas / max(1.0, profit))),
            "urgency": urgency,
            "confidence": confidence,
            "profit_usd": min(profit / 1000.0, 1.0),  # Normalise to 0-1
            "chain_popularity": 1.0 - chain_pop,
            "signal_age_s": max(0.0, 1.0 - age / 300.0),
            "source_reliability": source_reliability,
        }

    def _score_linear(self, features: Dict[str, float]) -> float:
        """Linear weighted scoring (default before GBM training)."""
        score = 0.0
        total_weight = sum(self._weights.values())
        for feat_name, weight in self._weights.items():
            val = features.get(feat_name, 0.0)
            score += val * (weight / total_weight)
        return max(0.0, min(1.0, score))

    def _score_with_model(self, features: Dict[str, float]) -> float:
        """Score using the trained GBM model."""
        try:
            feature_vec = np.array([[features.get(f, 0.0) for f in FEATURE_NAMES]])
            proba = self._model.predict_proba(feature_vec)[0]
            # Class 1 = profitable execution
            return float(proba[1]) if len(proba) > 1 else float(proba[0])
        except Exception:
            return self._score_linear(features)

    # ── 6.2 Dynamic Strategy Routing ─────────────────────────

    def _route_signal(self, signal: TriangulatedSignal) -> StrategyRoute:
        """Route a signal to the appropriate execution strategy."""
        sig_type = signal.signal_type.value

        # Use configured routing map
        route_name = self._cfg.strategy_routes.get(sig_type)
        if route_name:
            try:
                return StrategyRoute(route_name)
            except ValueError:
                pass

        # Heuristic routing
        if signal.signal_type in (
            SignalType.PENDING_LIQUIDATION,
            SignalType.CASCADING_LIQUIDATION,
        ):
            return StrategyRoute.LIQUIDATION_ENGINE
        elif signal.signal_type in (
            SignalType.ARBITRAGE,
            SignalType.BRIDGE_IMBALANCE,
        ):
            return StrategyRoute.ARB_MODULE
        elif signal.signal_type in (
            SignalType.CROSS_CHAIN_ARB,
        ):
            return StrategyRoute.CROSS_CHAIN_EXECUTOR
        elif signal.signal_type in (
            SignalType.SANDWICH,
            SignalType.BACKRUN,
            SignalType.ORACLE_FRONT_RUN,
        ):
            return StrategyRoute.MEV_STRATEGY
        elif signal.signal_type == SignalType.YIELD_OPPORTUNITY:
            return StrategyRoute.YIELD_OPTIMIZER

        return StrategyRoute.MANUAL_REVIEW

    # ── 6.3 Outcome Recording & Retraining ──────────────────

    def record_outcome(self, signal: TriangulatedSignal,
                       actual_profit: float, success: bool):
        """Record execution outcome for model improvement."""
        # Update source reliability
        src_name = signal.source.value
        self._source_stats[src_name]["attempts"] += 1
        if success:
            self._source_stats[src_name]["successes"] += 1

        # Store training example
        outcome = ExecutionOutcome(
            signal_type=signal.signal_type.value,
            source=src_name,
            chain_id=signal.chain_id,
            predicted_profit=signal.estimated_profit_usd,
            actual_profit=actual_profit,
            success=success,
            features=signal.ml_features,
        )
        self._training_buffer.append(outcome)
        self._stats["outcomes_recorded"] += 1

        # Check if we should retrain
        if (len(self._training_buffer) >= 100 and
                time.time() - self._last_retrain > self._cfg.retrain_interval_hours * 3600):
            self._retrain_model()

    def _retrain_model(self):
        """Retrain the GBM model from accumulated outcomes."""
        if not SKLEARN_AVAILABLE or not self._model:
            return

        outcomes = list(self._training_buffer)
        if len(outcomes) < 50:
            return

        try:
            X = []
            y = []
            for o in outcomes:
                feature_vec = [o.features.get(f, 0.0) for f in FEATURE_NAMES]
                X.append(feature_vec)
                y.append(1 if o.success and o.actual_profit > 0 else 0)

            X_arr = np.array(X)
            y_arr = np.array(y)

            # Need both classes represented
            if len(set(y_arr)) < 2:
                return

            self._model.fit(X_arr, y_arr)
            self._model_trained = True
            self._last_retrain = time.time()
            self._stats["model_retrains"] += 1

            # Log feature importances
            importances = dict(zip(FEATURE_NAMES, self._model.feature_importances_))
            logger.info("[MLAggregator] Model retrained on %d samples. Importances: %s",
                         len(outcomes), {k: f"{v:.3f}" for k, v in
                                         sorted(importances.items(), key=lambda x: -x[1])[:5]})

        except Exception as exc:
            logger.warning("[MLAggregator] Retrain failed: %s", exc)

    # ── Stats ─────────────────────────────────────────────────

    def get_stats(self) -> Dict[str, Any]:
        return {
            **self._stats,
            "weights": self._weights,
            "model_trained": self._model_trained,
            "training_buffer_size": len(self._training_buffer),
            "source_reliability": {
                k: v["successes"] / max(1, v["attempts"])
                for k, v in self._source_stats.items()
            },
        }
