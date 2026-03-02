#!/usr/bin/env python3
"""
ARRAY 5 — ML Aggregator & Ranker: The Brain of the Operation
===============================================================
Central intelligence unit that scores, ranks, and routes ALL signals
from all detector arrays.

Quality Score = f(expected_value, competition, complexity, gas_cost)

Components:
  1. Feature Extraction — normalize signal attributes into model features
  2. Gradient Boosting Scorer — predict quality score per signal
  3. Competition Estimator — estimate how many bots will see this
  4. Dynamic Strategy Router — route to appropriate execution module
  5. Continuous Learning — update model weights from execution outcomes

The ranker subscribes to the DataBus, scores every incoming signal,
and pushes ranked signals back for consumption by the pipeline.
"""

import logging
import math
from collections import deque
from typing import Dict, Optional
from dataclasses import dataclass

from ..config import OmniScopeConfig, get_omni_config
from ..data_bus import DataBus, OpportunitySignal, SignalType, SignalSource

logger = logging.getLogger(__name__)


@dataclass
class FeatureVector:
    """Normalized feature vector for ML scoring."""
    expected_value: float      # profit * confidence
    competition: float         # 0=none, 1=extreme
    complexity: float          # 0=trivial, 1=max
    gas_ratio: float           # gas_cost / profit
    urgency: float             # 0=hours, 1=this_block
    source_reliability: float  # Historical accuracy of this source
    chain_efficiency: float    # Gas cost efficiency of target chain
    signal_age_seconds: float
    is_liquidation: float      # 1 if liquidation, 0 otherwise
    is_arbitrage: float        # 1 if arb, 0 otherwise
    is_new_protocol: float     # 1 if new protocol, 0 otherwise


# Source reliability priors (updated via continuous learning)
SOURCE_RELIABILITY = {
    SignalSource.MEMPOOL_RADAR: 0.7,
    SignalSource.CONTRACT_CRAWLER: 0.5,
    SignalSource.STATIC_ANALYZER: 0.4,
    SignalSource.BRIDGE_MONITOR: 0.6,
    SignalSource.ARCHIVE_INDEXER: 0.65,
    SignalSource.ALPHA_SEEKER: 0.3,
    SignalSource.EXTERNAL: 0.5,
}

# Chain gas efficiency (lower = more efficient for execution)
CHAIN_EFFICIENCY = {
    1: 0.2,       # Ethereum — expensive but liquid
    42161: 0.9,   # Arbitrum — cheap
    10: 0.85,     # Optimism — cheap
    8453: 0.9,    # Base — cheap
    137: 0.8,     # Polygon — cheap
    43114: 0.7,
    56: 0.75,
}

# Strategy routing map
ROUTE_MAP = {
    SignalType.PENDING_LIQUIDATION: "liquidation_engine",
    SignalType.NFT_LIQUIDATION: "liquidation_engine",
    SignalType.ARBITRAGE: "arb_module",
    SignalType.CROSS_CHAIN_ARB: "cross_chain_arb",
    SignalType.BACKRUN: "mev_module",
    SignalType.SANDWICH: "mev_module",
    SignalType.NEW_PROTOCOL: "discovery_queue",
    SignalType.GOVERNANCE_CHANGE: "governance_watcher",
    SignalType.SOCIAL_SPIKE: "alpha_queue",
    SignalType.YIELD_OPPORTUNITY: "yield_optimizer",
    SignalType.BRIDGE_IMBALANCE: "bridge_arb",
    SignalType.FLASH_LOAN_SURPLUS: "surplus_engine",
}


class OpportunityRanker:
    """
    ML-based opportunity scorer and router.

    Subscribes to DataBus, scores every signal, and pushes
    ranked signals back into the bus's ranked queue.
    """

    def __init__(self, bus: DataBus, config: Optional[OmniScopeConfig] = None):
        self.bus = bus
        self.config = config or get_omni_config()
        self._cfg = self.config.ml_ranker

        # Feature weights (trained via continuous learning)
        self._weights = dict(self._cfg.feature_weights)

        # Learning history
        self._history: deque = deque(maxlen=1000)
        self._source_accuracy: Dict[str, deque] = {
            s.value: deque(maxlen=100) for s in SignalSource
        }

        # Register as DataBus subscriber
        self.bus.subscribe(self._on_signal)

        self.stats = {
            "signals_scored": 0,
            "signals_routed": 0,
            "signals_filtered": 0,
            "avg_quality_score": 0.0,
            "top_routes": {},
        }

    # ------------------------------------------------------------------
    # DataBus subscriber callback
    # ------------------------------------------------------------------

    def _on_signal(self, signal: OpportunitySignal):
        """Score incoming signal and push to ranked queue if above threshold."""
        features = self._extract_features(signal)
        score = self._compute_quality_score(features)
        signal.quality_score = score

        self.stats["signals_scored"] += 1

        # Update running average
        n = self.stats["signals_scored"]
        prev_avg = self.stats["avg_quality_score"]
        self.stats["avg_quality_score"] = prev_avg + (score - prev_avg) / n

        if score < self._cfg.min_quality_score:
            self.stats["signals_filtered"] += 1
            return

        # Route to appropriate module
        signal.routed_to = ROUTE_MAP.get(signal.signal_type, "general_queue")
        self.stats["signals_routed"] += 1

        route = signal.routed_to
        self.stats["top_routes"][route] = self.stats["top_routes"].get(route, 0) + 1

        # Push to ranked queue
        self.bus.push_ranked(signal)

        if score > 0.8:
            logger.info(
                f"⭐ HIGH QUALITY [{score:.2f}]: {signal.signal_type.value} "
                f"chain={signal.chain_id} profit=${signal.estimated_profit_usd:.0f} "
                f"→ {signal.routed_to}"
            )

    # ------------------------------------------------------------------
    # Feature Extraction
    # ------------------------------------------------------------------

    def _extract_features(self, signal: OpportunitySignal) -> FeatureVector:
        """Extract normalized feature vector from a signal."""
        # Expected value: profit * confidence
        ev = signal.estimated_profit_usd * signal.confidence

        # Gas ratio (lower is better)
        gas_ratio = (
            signal.gas_cost_estimate_usd / max(signal.estimated_profit_usd, 1)
        )

        # Urgency: normalize to 0-1 (0 = hours, 1 = this block)
        urgency = 1.0 / (1.0 + signal.urgency_seconds / 12.0)

        # Source reliability
        source_rel = SOURCE_RELIABILITY.get(signal.source, 0.5)

        # Chain efficiency
        chain_eff = CHAIN_EFFICIENCY.get(signal.chain_id, 0.5)

        return FeatureVector(
            expected_value=min(ev / 1000.0, 1.0),  # Normalize to 0-1
            competition=signal.competition_estimate,
            complexity=signal.execution_complexity,
            gas_ratio=min(gas_ratio, 1.0),
            urgency=urgency,
            source_reliability=source_rel,
            chain_efficiency=chain_eff,
            signal_age_seconds=signal.age_seconds,
            is_liquidation=1.0 if signal.signal_type in (
                SignalType.PENDING_LIQUIDATION, SignalType.NFT_LIQUIDATION
            ) else 0.0,
            is_arbitrage=1.0 if signal.signal_type in (
                SignalType.ARBITRAGE, SignalType.CROSS_CHAIN_ARB
            ) else 0.0,
            is_new_protocol=1.0 if signal.signal_type == SignalType.NEW_PROTOCOL else 0.0,
        )

    # ------------------------------------------------------------------
    # Quality Score Computation (Gradient Boosting-inspired)
    # ------------------------------------------------------------------

    def _compute_quality_score(self, f: FeatureVector) -> float:
        """
        Compute quality score using weighted feature combination
        with non-linear transformations.

        score = Σ(weight_i * transform_i(feature_i))
        """
        w = self._weights

        # Expected Value component (higher = better)
        ev_score = f.expected_value * w.get("expected_value", 0.35)

        # Competition component (lower competition = better)
        comp_score = (1.0 - f.competition) * w.get("competition", 0.25)

        # Complexity component (lower = better, with diminishing penalty)
        complex_score = (1.0 - f.complexity ** 0.5) * w.get("complexity", 0.20)

        # Gas cost component (lower gas ratio = better)
        gas_score = (1.0 - f.gas_ratio) * w.get("gas_cost", 0.20)

        # Bonuses
        urgency_bonus = f.urgency * 0.1  # Urgent = slight bonus
        source_bonus = f.source_reliability * 0.05
        chain_bonus = f.chain_efficiency * 0.05

        # Penalties
        age_penalty = min(f.signal_age_seconds / 60.0, 0.3)  # Stale signals penalized

        # Type bonuses (liquidations are proven profitable)
        type_bonus = f.is_liquidation * 0.1 + f.is_new_protocol * 0.05

        raw_score = (
            ev_score + comp_score + complex_score + gas_score
            + urgency_bonus + source_bonus + chain_bonus + type_bonus
            - age_penalty
        )

        # Sigmoid normalization to 0-1
        return 1.0 / (1.0 + math.exp(-5 * (raw_score - 0.3)))

    # ------------------------------------------------------------------
    # Continuous Learning
    # ------------------------------------------------------------------

    def record_outcome(
        self, signal: OpportunitySignal, actual_profit: float, success: bool
    ):
        """
        Record execution outcome for continuous model improvement.
        Updates source reliability and feature weights.
        """
        # Update source accuracy
        src = signal.source.value
        if src in self._source_accuracy:
            self._source_accuracy[src].append(1.0 if success else 0.0)
            # Update SOURCE_RELIABILITY with running accuracy
            if len(self._source_accuracy[src]) >= 10:
                accuracy = sum(self._source_accuracy[src]) / len(self._source_accuracy[src])
                SOURCE_RELIABILITY[signal.source] = accuracy

        # Record for batch weight update
        self._history.append({
            "predicted_score": signal.quality_score,
            "actual_profit": actual_profit,
            "success": success,
            "features": self._extract_features(signal),
        })

        # Periodic weight update (simple gradient step)
        if len(self._history) >= 50 and len(self._history) % 50 == 0:
            self._update_weights()

    def _update_weights(self):
        """Simple online weight update based on recent outcomes."""
        if len(self._history) < 20:
            return

        recent = list(self._history)[-50:]
        lr = 0.01

        for record in recent:
            f = record["features"]
            target = 1.0 if record["success"] and record["actual_profit"] > 0 else 0.0
            predicted = record["predicted_score"]
            error = target - predicted

            # Gradient update on weights
            self._weights["expected_value"] += lr * error * f.expected_value
            self._weights["competition"] += lr * error * (1 - f.competition)
            self._weights["complexity"] += lr * error * (1 - f.complexity)
            self._weights["gas_cost"] += lr * error * (1 - f.gas_ratio)

        # Normalize weights to sum to 1
        total = sum(abs(v) for v in self._weights.values())
        if total > 0:
            for k in self._weights:
                self._weights[k] = abs(self._weights[k]) / total

    def get_stats(self) -> Dict:
        return {
            **self.stats,
            "weights": dict(self._weights),
            "source_reliability": {
                k: (sum(v) / len(v) if v else 0)
                for k, v in self._source_accuracy.items()
                if v
            },
        }
