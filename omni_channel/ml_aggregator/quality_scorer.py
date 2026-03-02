#!/usr/bin/env python3
"""
Quality Scorer
Calculate quality scores for opportunity signals

Scoring formula:
Quality Score = (EV × Confidence) / (Complexity × Cost × Competition)

Where:
- EV = Expected Value (profit × success probability)
- Confidence = Signal reliability (0-1)
- Complexity = Execution difficulty (1-10)
- Cost = Gas + latency cost
- Competition = Estimated competing bots (0-1)
"""

import asyncio
import time
from typing import Dict, List, Optional, Any, Tuple
from dataclasses import dataclass, field
from enum import Enum
import math

from ..data_lake.data_models import OpportunitySignal, SignalType, ExecutionModule


class ScoreTier(Enum):
    """Score tier classification"""
    EXCELLENT = "excellent"  # 0.8-1.0
    GOOD = "good"  # 0.6-0.8
    FAIR = "fair"  # 0.4-0.6
    POOR = "poor"  # 0.2-0.4
    VERY_POOR = "very_poor"  # 0-0.2


@dataclass
class ScoredSignal:
    """Opportunity signal with quality score"""
    signal: OpportunitySignal
    quality_score: float
    ev_score: float
    confidence_score: float
    complexity_score: float
    cost_score: float
    competition_score: float
    tier: ScoreTier
    ranked_position: int = 0
    routed_to: Optional[ExecutionModule] = None
    scored_at: int = 0


class QualityScorer:
    """
    Calculate quality scores for opportunity signals
    """

    def __init__(self, config: Dict[str, Any] = None):
        self.config = config or {}
        self.is_running = False

        # Scoring weights (can be tuned)
        self.ev_weight = self.config.get('ev_weight', 0.35)
        self.confidence_weight = self.config.get('confidence_weight', 0.25)
        self.complexity_weight = self.config.get('complexity_weight', 0.15)
        self.cost_weight = self.config.get('cost_weight', 0.15)
        self.competition_weight = self.config.get('competition_weight', 0.10)

        # Thresholds
        self.min_quality_score = self.config.get('min_quality_score', 0.3)
        self.excellent_threshold = self.config.get('excellent_threshold', 0.8)
        self.good_threshold = self.config.get('good_threshold', 0.6)

        # Gas prices for cost calculation (Gwei)
        self.gas_prices: Dict[int, float] = self.config.get('gas_prices', {
            1: 30,      # Ethereum
            42161: 0.1,  # Arbitrum
            10: 0.01,   # Optimism
            137: 30,    # Polygon
            8453: 0.01,  # Base
        })

        # Statistics
        self.signals_scored = 0
        self.excellent_count = 0
        self.good_count = 0

        print("📊 Quality Scorer initialized")

    async def start(self):
        """Start scorer"""
        print("\n📊 Starting Quality Scorer...")
        self.is_running = True
        print("   ✅ Quality Scorer started")

    async def stop(self):
        """Stop scorer"""
        self.is_running = False
        print("   📊 Quality Scorer stopped")

    def score(self, signal: OpportunitySignal,
              competition_score: float = 0.5,
              complexity_score: float = 5) -> ScoredSignal:
        """Calculate quality score for a signal"""
        # Calculate component scores

        # EV Score (0-1)
        ev_score = self._calculate_ev_score(signal)

        # Confidence Score (0-1)
        confidence_score = self._calculate_confidence_score(signal)

        # Cost Score (0-1, lower is better)
        cost_score = self._calculate_cost_score(signal)

        # Use provided or default complexity
        normalized_complexity = complexity_score / 10.0

        # Use provided or default competition
        normalized_competition = competition_score

        # Calculate raw quality score
        numerator = (ev_score * self.ev_weight +
                    confidence_score * self.confidence_weight)

        denominator = (normalized_complexity * self.complexity_weight +
                      cost_score * self.cost_weight +
                      normalized_competition * self.competition_weight + 0.001)  # Avoid division by zero

        raw_score = numerator / denominator

        # Normalize to 0-1 range
        quality_score = min(max(raw_score / 2, 0), 1)  # Divide by 2 to normalize

        # Determine tier
        tier = self._get_tier(quality_score)

        # Create scored signal
        scored = ScoredSignal(
            signal=signal,
            quality_score=round(quality_score, 4),
            ev_score=round(ev_score, 4),
            confidence_score=round(confidence_score, 4),
            complexity_score=round(normalized_complexity, 4),
            cost_score=round(cost_score, 4),
            competition_score=round(normalized_competition, 4),
            tier=tier,
            scored_at=int(time.time())
        )

        # Update statistics
        self.signals_scored += 1
        if tier == ScoreTier.EXCELLENT:
            self.excellent_count += 1
        elif tier == ScoreTier.GOOD:
            self.good_count += 1

        return scored

    def score_batch(self, signals: List[OpportunitySignal],
                    competition_scores: Dict[str, float] = None,
                    complexity_scores: Dict[str, float] = None) -> List[ScoredSignal]:
        """Score batch of signals"""
        scored_signals = []

        for signal in signals:
            signal_id = signal.signal_id if hasattr(signal, 'signal_id') else signal.trigger_tx_hash

            competition = competition_scores.get(signal_id, 0.5) if competition_scores else 0.5
            complexity = complexity_scores.get(signal_id, 5) if complexity_scores else 5

            scored = self.score(signal, competition, complexity)
            scored_signals.append(scored)

        # Sort by quality score
        scored_signals.sort(key=lambda s: s.quality_score, reverse=True)

        # Assign rankings
        for i, scored in enumerate(scored_signals):
            scored.ranked_position = i + 1

        return scored_signals

    def _calculate_ev_score(self, signal: OpportunitySignal) -> float:
        """Calculate expected value score (0-1)"""
        ev_usd = signal.expected_value_usd

        if ev_usd <= 0:
            return 0

        # Logarithmic scaling
        # $10 = 0.2, $100 = 0.5, $1000 = 0.75, $10000 = 0.9
        if ev_usd < 10:
            return 0.1

        log_ev = math.log10(ev_usd)
        normalized = (log_ev - 1) / 3  # Normalize around $10-$10000

        return min(max(normalized, 0), 1)

    def _calculate_confidence_score(self, signal: OpportunitySignal) -> float:
        """Calculate confidence score (0-1)"""
        # Use signal's built-in confidence
        base_confidence = signal.confidence

        # Adjust based on signal type
        type_multipliers = {
            SignalType.LIQUIDATION: 1.0,
            SignalType.ARBITRAGE: 0.9,
            SignalType.SANDWICH: 0.8,
            SignalType.BACKRUN: 0.85,
            SignalType.FRONT_RUN: 0.75,
            SignalType.CROSS_CHAIN_ARB: 0.7,
            SignalType.ORACLE_UPDATE: 0.95,
            SignalType.LARGE_SWAP: 0.9,
            SignalType.NEW_PROTOCOL: 0.6,
            SignalType.VULNERABILITY: 0.7,
        }

        multiplier = type_multipliers.get(signal.signal_type, 0.8)

        # Adjust based on urgency
        urgency_bonus = min(signal.urgency_score / 100, 0.1) if hasattr(signal, 'urgency_score') else 0

        confidence = base_confidence * multiplier + urgency_bonus

        return min(max(confidence, 0), 1)

    def _calculate_cost_score(self, signal: OpportunitySignal) -> float:
        """Calculate cost score (0-1, lower is better)"""
        # Get gas estimate
        gas_estimate = signal.gas_estimate if hasattr(signal, 'gas_estimate') else 200000
        gas_price = signal.gas_price_gwei if hasattr(signal, 'gas_price_gwei') else 30

        # Calculate gas cost in USD
        eth_price = 2000  # Would use real price
        gas_cost_usd = (gas_estimate * gas_price * eth_price) / 1e18

        # Add latency cost (opportunity cost of capital)
        latency_ms = signal.latency_requirement_ms if hasattr(signal, 'latency_requirement_ms') else 1000
        latency_cost = latency_ms * 0.0001  # $0.0001 per ms

        total_cost = gas_cost_usd + latency_cost

        # Normalize (cost > $100 = score 1, cost = $0 = score 0)
        normalized = min(total_cost / 100, 1)

        return normalized

    def _get_tier(self, quality_score: float) -> ScoreTier:
        """Determine score tier"""
        if quality_score >= self.excellent_threshold:
            return ScoreTier.EXCELLENT
        elif quality_score >= self.good_threshold:
            return ScoreTier.GOOD
        elif quality_score >= 0.4:
            return ScoreTier.FAIR
        elif quality_score >= 0.2:
            return ScoreTier.POOR
        else:
            return ScoreTier.VERY_POOR

    def get_recommended_signals(self, scored_signals: List[ScoredSignal],
                                 min_tier: ScoreTier = ScoreTier.FAIR,
                                 limit: int = 10) -> List[ScoredSignal]:
        """Get recommended signals based on tier"""
        tier_order = {
            ScoreTier.EXCELLENT: 0,
            ScoreTier.GOOD: 1,
            ScoreTier.FAIR: 2,
            ScoreTier.POOR: 3,
            ScoreTier.VERY_POOR: 4,
        }

        min_order = tier_order.get(min_tier, 2)

        filtered = [s for s in scored_signals if tier_order.get(s.tier, 4) <= min_order]

        return filtered[:limit]

    def get_stats(self) -> Dict:
        """Get scorer statistics"""
        return {
            'signals_scored': self.signals_scored,
            'excellent_count': self.excellent_count,
            'good_count': self.good_count,
            'excellent_rate': self.excellent_count / max(self.signals_scored, 1),
            'weights': {
                'ev': self.ev_weight,
                'confidence': self.confidence_weight,
                'complexity': self.complexity_weight,
                'cost': self.cost_weight,
                'competition': self.competition_weight,
            }
        }


# Pre-configured scoring profiles
SCORING_PROFILES = {
    'aggressive': {
        'ev_weight': 0.45,
        'confidence_weight': 0.20,
        'complexity_weight': 0.10,
        'cost_weight': 0.10,
        'competition_weight': 0.15,
        'min_quality_score': 0.2,
    },
    'balanced': {
        'ev_weight': 0.35,
        'confidence_weight': 0.25,
        'complexity_weight': 0.15,
        'cost_weight': 0.15,
        'competition_weight': 0.10,
        'min_quality_score': 0.3,
    },
    'conservative': {
        'ev_weight': 0.25,
        'confidence_weight': 0.35,
        'complexity_weight': 0.20,
        'cost_weight': 0.15,
        'competition_weight': 0.05,
        'min_quality_score': 0.5,
    },
}
