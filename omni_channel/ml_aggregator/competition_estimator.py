#!/usr/bin/env python3
"""
Competition Estimator
Estimate number of competing bots for each opportunity

Factors:
- Signal visibility (public mempool vs private)
- Opportunity type popularity
- Gas price competition
- Historical success rate
- Time of day patterns
"""

import asyncio
import time
from typing import Dict, List, Optional, Any, Set, Tuple
from dataclasses import dataclass, field
from enum import Enum
import math

from ..data_lake.data_models import OpportunitySignal, SignalType


@dataclass
class CompetitionData:
    """Competition analysis data"""
    signal_id: str
    estimated_competitors: int
    competition_level: str  # low, medium, high
    competition_score: float  # 0-1, higher = more competition (0 = no competition)
    visibility_score: float  # 0-1, how visible is this opportunity
    gas_competition_factor: float  # Multiplier for gas wars
    success_probability: float  # Chance of winning against competition
    recommended_gas_multiplier: float  # How much to boost gas
    analyzed_at: int = 0


class CompetitionLevel(Enum):
    """Competition level classification"""
    LOW = "low"  # 0-2 competitors
    MEDIUM = "medium"  # 3-10 competitors
    HIGH = "high"  # 10+ competitors


class CompetitionEstimator:
    """
    Estimate competition for each opportunity
    """

    def __init__(self, config: Dict[str, Any] = None):
        self.config = config or {}
        self.is_running = False

        # Competition baselines by signal type
        self.type_baselines: Dict[SignalType, float] = {
            SignalType.LIQUIDATION: 0.7,  # High competition
            SignalType.ARBITRAGE: 0.6,
            SignalType.SANDWICH: 0.8,  # Very high competition
            SignalType.BACKRUN: 0.5,
            SignalType.FRONT_RUN: 0.6,
            SignalType.CROSS_CHAIN_ARB: 0.3,  # Lower competition (harder)
            SignalType.ORACLE_UPDATE: 0.4,
            SignalType.LARGE_SWAP: 0.5,
            SignalType.NEW_PROTOCOL: 0.2,  # Low competition (early discovery)
            SignalType.VULNERABILITY: 0.1,  # Very low competition
        }

        # Gas competition thresholds
        self.high_gas_threshold = self.config.get('high_gas_threshold', 100)  # Gwei
        self.medium_gas_threshold = self.config.get('medium_gas_threshold', 50)

        # Historical data (would be populated from past executions)
        self._historical_success: Dict[str, List[bool]] = {}  # signal_type -> success list
        self._competitor_tracking: Dict[str, Set[str]] = {}  # tx_hash -> competitor addresses

        # Statistics
        self.estimations_made = 0
        self.accurate_predictions = 0

        print("🎯 Competition Estimator initialized")

    async def start(self):
        """Start estimator"""
        print("\n🎯 Starting Competition Estimator...")
        self.is_running = True
        print("   ✅ Competition Estimator started")

    async def stop(self):
        """Stop estimator"""
        self.is_running = False
        print("   🎯 Competition Estimator stopped")

    def estimate(self, signal: OpportunitySignal) -> CompetitionData:
        """Estimate competition for a signal"""
        signal_id = signal.signal_id if hasattr(signal, 'signal_id') else signal.trigger_tx_hash

        # Base competition from signal type
        base_competition = self.type_baselines.get(signal.signal_type, 0.5)

        # Adjust for visibility
        visibility = self._calculate_visibility(signal)

        # Adjust for gas competition
        gas_factor = self._calculate_gas_factor(signal)

        # Adjust for time patterns
        time_factor = self._calculate_time_factor()

        # Calculate estimated competitors
        estimated_count = int((base_competition * visibility * gas_factor * time_factor) * 20)

        # Determine competition level
        if estimated_count <= 2:
            level = CompetitionLevel.LOW
        elif estimated_count <= 10:
            level = CompetitionLevel.MEDIUM
        else:
            level = CompetitionLevel.HIGH

        # Calculate success probability
        success_prob = self._calculate_success_probability(estimated_count, signal)

        # Calculate recommended gas multiplier
        gas_multiplier = self._calculate_gas_multiplier(level, estimated_count)

        # Compute numeric competition score (0=no competition, 1=max competition)
        competition_score = round(min(1.0, base_competition * visibility * gas_factor * time_factor), 4)

        self.estimations_made += 1

        return CompetitionData(
            signal_id=signal_id,
            estimated_competitors=estimated_count,
            competition_level=level.value,
            competition_score=competition_score,
            visibility_score=round(visibility, 4),
            gas_competition_factor=round(gas_factor, 4),
            success_probability=round(success_prob, 4),
            recommended_gas_multiplier=round(gas_multiplier, 4),
            analyzed_at=int(time.time())
        )

    def estimate_batch(self, signals: List[OpportunitySignal]) -> Dict[str, CompetitionData]:
        """Estimate competition for batch of signals"""
        results = {}

        for signal in signals:
            signal_id = signal.signal_id if hasattr(signal, 'signal_id') else signal.trigger_tx_hash
            results[signal_id] = self.estimate(signal)

        return results

    def _calculate_visibility(self, signal: OpportunitySignal) -> float:
        """Calculate how visible this opportunity is to other bots"""
        visibility = 0.5  # Base visibility

        # Public mempool = higher visibility
        if signal.source_module.value == 'mempool_radar':
            visibility += 0.3

        # Large swaps are very visible
        if signal.signal_type == SignalType.LARGE_SWAP:
            if signal.expected_value_usd > 1000000:
                visibility += 0.2
            elif signal.expected_value_usd > 100000:
                visibility += 0.1

        # Liquidations on major protocols are highly visible
        if signal.signal_type == SignalType.LIQUIDATION:
            visibility += 0.2

        # New protocol discoveries are less visible
        if signal.signal_type == SignalType.NEW_PROTOCOL:
            visibility -= 0.3

        # Cross-chain is less visible (harder to detect)
        if signal.signal_type == SignalType.CROSS_CHAIN_ARB:
            visibility -= 0.2

        return min(max(visibility, 0.1), 1.0)

    def _calculate_gas_factor(self, signal: OpportunitySignal) -> float:
        """Calculate gas competition factor"""
        gas_price = signal.gas_price_gwei if hasattr(signal, 'gas_price_gwei') else 30

        if gas_price >= self.high_gas_threshold:
            return 1.5  # High gas = more competition
        elif gas_price >= self.medium_gas_threshold:
            return 1.2
        else:
            return 1.0

    def _calculate_time_factor(self) -> float:
        """Calculate time-based competition factor"""
        # Get current hour (UTC)
        hour = time.gmtime().tm_hour

        # Higher competition during US/Europe trading hours
        if 13 <= hour <= 21:  # 13:00-21:00 UTC = US trading
            return 1.3
        elif 7 <= hour <= 16:  # 07:00-16:00 UTC = Europe trading
            return 1.2
        elif 0 <= hour <= 6:  # 00:00-06:00 UTC = Low activity
            return 0.8
        else:
            return 1.0

    def _calculate_success_probability(self, competitors: int,
                                        signal: OpportunitySignal) -> float:
        """Calculate probability of winning against competition"""
        if competitors == 0:
            return 0.95  # Near certain if no competition

        # Base probability decreases with more competitors
        base_prob = 1 / (competitors + 1)

        # Adjust for signal type
        type_adjustments = {
            SignalType.LIQUIDATION: 0.9,  # Skill-based
            SignalType.ARBITRAGE: 0.8,
            SignalType.SANDWICH: 0.7,  # Very competitive
            SignalType.BACKRUN: 0.85,
            SignalType.CROSS_CHAIN_ARB: 0.6,  # Complex, fewer winners
        }

        adjustment = type_adjustments.get(signal.signal_type, 0.8)

        # Adjust for latency requirement
        latency_factor = 1.0
        if hasattr(signal, 'latency_requirement_ms'):
            if signal.latency_requirement_ms < 100:
                latency_factor = 0.7  # Hard to win very fast races
            elif signal.latency_requirement_ms > 1000:
                latency_factor = 1.1  # Easier to win slower races

        probability = base_prob * adjustment * latency_factor

        return min(max(probability, 0.01), 0.95)

    def _calculate_gas_multiplier(self, level: CompetitionLevel,
                                   competitors: int) -> float:
        """Calculate recommended gas price multiplier"""
        if level == CompetitionLevel.LOW:
            return 1.0  # No need to boost
        elif level == CompetitionLevel.MEDIUM:
            return 1.2 + (competitors * 0.05)  # Small boost
        else:  # HIGH
            return 1.5 + (competitors * 0.02)  # Significant boost

    def record_outcome(self, signal_id: str, signal_type: SignalType,
                       won: bool, competitor_count: int = 0):
        """Record execution outcome for learning"""
        if signal_type.value not in self._historical_success:
            self._historical_success[signal_type.value] = []

        self._historical_success[signal_type.value].append(won)

        # Keep only last 1000 outcomes
        if len(self._historical_success[signal_type.value]) > 1000:
            self._historical_success[signal_type.value] = \
                self._historical_success[signal_type.value][-1000:]

    def get_historical_win_rate(self, signal_type: SignalType) -> float:
        """Get historical win rate for signal type"""
        history = self._historical_success.get(signal_type.value, [])

        if not history:
            return 0.5  # Default

        wins = sum(1 for w in history if w)
        return wins / len(history)

    def get_stats(self) -> Dict:
        """Get estimator statistics"""
        return {
            'estimations_made': self.estimations_made,
            'signal_baselines': {k.value: v for k, v in self.type_baselines.items()},
            'historical_win_rates': {
                k: sum(v) / len(v) if v else 0.5
                for k, v in self._historical_success.items()
            },
        }


# Competition patterns by time of day
TIME_PATTERNS = {
    'us_open': {'hours': (13, 15), 'multiplier': 1.5},
    'us_close': {'hours': (19, 21), 'multiplier': 1.4},
    'europe_open': {'hours': (7, 9), 'multiplier': 1.3},
    'asia_open': {'hours': (0, 2), 'multiplier': 1.1},
    'weekend': {'days': (5, 6), 'multiplier': 0.7},  # Sat/Sun
}
