#!/usr/bin/env python3
"""
HEAT MAP — Profitable Pattern Learning & Opportunity Frequency Tracker
========================================================================
Builds a continuously-updated "heat map" of:
  - Which opportunity types are most profitable right now
  - Which chains have the highest frequency of each type
  - Which protocols are yielding the best margins
  - Which time windows (blocks) see the most activity
  - Competition density per opportunity vector

The heat map is the bridge between Phase 1 (cold start) and Phase 3 (multiplier).
It tells the Capital Multiplier *where* to concentrate capital for maximum ROI.
"""

import asyncio
import time
import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any, Tuple
from collections import defaultdict, deque
from enum import Enum


@dataclass
class HeatMapEntry:
    """Single heat map cell (opportunity_type × chain × protocol)"""
    opportunity_type: str
    chain_id: int
    protocol: str
    # Counters
    total_seen: int = 0
    total_executed: int = 0
    total_profitable: int = 0
    # Financial
    total_profit_usd: float = 0.0
    avg_profit_usd: float = 0.0
    max_profit_usd: float = 0.0
    avg_roi_percent: float = 0.0
    # Timing
    avg_frequency_per_hour: float = 0.0   # How often this type appears
    avg_execution_time_ms: float = 0.0
    # Competition
    avg_competition: float = 0.0          # 0-1 scale
    win_rate: float = 0.0                 # successful / attempts
    # Recency
    last_seen_at: float = 0.0
    last_profit_at: float = 0.0
    # Heat score (composite ranking)
    heat_score: float = 0.0

    @property
    def key(self) -> str:
        return f"{self.opportunity_type}:{self.chain_id}:{self.protocol}"


class HeatMap:
    """
    Real-time profitable pattern tracker.
    Updated after every trade (successful or not).
    Queried by the Capital Multiplier to decide capital allocation.
    """

    # Decay factor — older data counts less (half-life ~2 hours)
    DECAY_HALF_LIFE_SECONDS = 7200.0

    def __init__(self, config: Dict[str, Any] = None):
        self.config = config or {}
        self.start_time = time.time()

        # Primary data store: key → HeatMapEntry
        self.entries: Dict[str, HeatMapEntry] = {}

        # Time-series windows for frequency calculation (per key)
        self._event_timestamps: Dict[str, deque] = defaultdict(
            lambda: deque(maxlen=500)
        )

        # Global rankings (recomputed periodically)
        self._ranked_entries: List[HeatMapEntry] = []
        self._last_rank_time = 0.0
        self._rank_interval = 30.0  # Rerank every 30 seconds

        print("🗺️  Heat Map initialized")

    # ──────────────────────────────────────────────
    # RECORDING
    # ──────────────────────────────────────────────

    def record_observation(
        self,
        opportunity_type: str,
        chain_id: int,
        protocol: str,
        was_executed: bool = False,
        was_profitable: bool = False,
        profit_usd: float = 0.0,
        roi_percent: float = 0.0,
        competition: float = 0.0,
        execution_time_ms: float = 0.0,
    ):
        """Record an observed (or executed) opportunity."""
        key = f"{opportunity_type}:{chain_id}:{protocol}"
        now = time.time()

        if key not in self.entries:
            self.entries[key] = HeatMapEntry(
                opportunity_type=opportunity_type,
                chain_id=chain_id,
                protocol=protocol,
            )

        entry = self.entries[key]
        entry.total_seen += 1
        entry.last_seen_at = now
        self._event_timestamps[key].append(now)

        if was_executed:
            entry.total_executed += 1
            if was_profitable:
                entry.total_profitable += 1
                entry.total_profit_usd += profit_usd
                entry.max_profit_usd = max(entry.max_profit_usd, profit_usd)
                entry.last_profit_at = now
                # Running averages
                n = entry.total_profitable
                entry.avg_profit_usd = (
                    entry.avg_profit_usd * (n - 1) + profit_usd
                ) / n
                entry.avg_roi_percent = (
                    entry.avg_roi_percent * (n - 1) + roi_percent
                ) / n
            if entry.total_executed > 0:
                entry.win_rate = entry.total_profitable / entry.total_executed
            entry.avg_execution_time_ms = (
                entry.avg_execution_time_ms * (entry.total_executed - 1) + execution_time_ms
            ) / entry.total_executed

        # Update competition EMA
        alpha = 0.1
        entry.avg_competition = (1 - alpha) * entry.avg_competition + alpha * competition

        # Update frequency (events in last hour)
        entry.avg_frequency_per_hour = self._compute_frequency(key)

        # Recompute heat score
        entry.heat_score = self._compute_heat_score(entry)

    # ──────────────────────────────────────────────
    # HEAT SCORE COMPUTATION
    # ──────────────────────────────────────────────

    def _compute_heat_score(self, entry: HeatMapEntry) -> float:
        """
        Composite heat score:
          heat = (avg_profit × frequency × win_rate) / (competition + ε)
          with time-decay for recency
        """
        now = time.time()
        recency = self._decay_weight(now - entry.last_profit_at) if entry.last_profit_at > 0 else 0.1

        profit_component = entry.avg_profit_usd * entry.avg_frequency_per_hour
        reliability_component = entry.win_rate
        competition_penalty = max(entry.avg_competition, 0.05)

        raw = (profit_component * reliability_component * recency) / competition_penalty

        # Normalize to 0-100 scale (soft cap)
        return min(100.0, raw / 10.0)

    def _decay_weight(self, age_seconds: float) -> float:
        """Exponential decay weight: w = 2^(-age / half_life)"""
        return math.pow(2, -age_seconds / self.DECAY_HALF_LIFE_SECONDS)

    def _compute_frequency(self, key: str) -> float:
        """Compute events per hour for a key."""
        timestamps = self._event_timestamps.get(key)
        if not timestamps or len(timestamps) < 2:
            return 0.0
        now = time.time()
        # Count events in last hour
        one_hour_ago = now - 3600
        recent = sum(1 for t in timestamps if t >= one_hour_ago)
        return float(recent)

    # ──────────────────────────────────────────────
    # RANKING & QUERIES
    # ──────────────────────────────────────────────

    def get_ranked(self, top_n: int = 20) -> List[HeatMapEntry]:
        """Get top N heat map entries by heat score."""
        now = time.time()
        if now - self._last_rank_time > self._rank_interval:
            self._ranked_entries = sorted(
                self.entries.values(),
                key=lambda e: e.heat_score,
                reverse=True,
            )
            self._last_rank_time = now
        return self._ranked_entries[:top_n]

    def get_hottest_by_type(self, opportunity_type: str, top_n: int = 5) -> List[HeatMapEntry]:
        """Get best chain+protocol combos for a given opportunity type."""
        filtered = [e for e in self.entries.values() if e.opportunity_type == opportunity_type]
        return sorted(filtered, key=lambda e: e.heat_score, reverse=True)[:top_n]

    def get_hottest_by_chain(self, chain_id: int, top_n: int = 5) -> List[HeatMapEntry]:
        """Get best opportunity types for a given chain."""
        filtered = [e for e in self.entries.values() if e.chain_id == chain_id]
        return sorted(filtered, key=lambda e: e.heat_score, reverse=True)[:top_n]

    def get_capital_allocation_weights(self, top_n: int = 10) -> Dict[str, float]:
        """
        Returns normalized allocation weights for the Capital Multiplier.
        Higher heat score → more capital allocation.
        """
        ranked = self.get_ranked(top_n)
        if not ranked:
            return {}

        total_score = sum(e.heat_score for e in ranked)
        if total_score <= 0:
            # Equal allocation
            weight = 1.0 / max(len(ranked), 1)
            return {e.key: weight for e in ranked}

        return {e.key: e.heat_score / total_score for e in ranked}

    # ──────────────────────────────────────────────
    # STATUS
    # ──────────────────────────────────────────────

    def status_report(self) -> Dict[str, Any]:
        ranked = self.get_ranked(10)
        return {
            'total_entries': len(self.entries),
            'top_10': [
                {
                    'key': e.key,
                    'heat_score': round(e.heat_score, 2),
                    'avg_profit': round(e.avg_profit_usd, 2),
                    'frequency_per_hr': round(e.avg_frequency_per_hour, 1),
                    'win_rate': round(e.win_rate, 3),
                    'competition': round(e.avg_competition, 3),
                    'total_profit': round(e.total_profit_usd, 2),
                }
                for e in ranked
            ],
        }

    def print_heat_map(self, top_n: int = 10):
        ranked = self.get_ranked(top_n)
        print(f"\n{'='*90}")
        print(f"  🗺️  HEAT MAP (Top {min(top_n, len(ranked))} Opportunities)")
        print(f"{'='*90}")
        print(f"  {'#':<3} {'Type':<20} {'Chain':<8} {'Protocol':<15} {'Heat':>6} {'AvgProfit':>10} {'Freq/hr':>8} {'Win%':>6}")
        print(f"  {'-'*84}")
        for i, e in enumerate(ranked, 1):
            print(f"  {i:<3} {e.opportunity_type:<20} {e.chain_id:<8} {e.protocol:<15} "
                  f"{e.heat_score:>6.1f} ${e.avg_profit_usd:>9.2f} {e.avg_frequency_per_hour:>7.1f} "
                  f"{e.win_rate*100:>5.1f}%")
        print(f"{'='*90}")
