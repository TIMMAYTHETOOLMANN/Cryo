#!/usr/bin/env python3
"""
CAPITAL MULTIPLIER — Adaptive Compounding Reinvestment Engine
==============================================================
Implements the exponential growth model from Phase 3:

  C(t+1) = C(t) + reinvest_rate × hourly_profit(t)
  profit_reserve += (1 - reinvest_rate) × hourly_profit(t)

Where:
  - C(t)            = capital base at hour t
  - reinvest_rate   = 0.60 (60% reinvested, 40% locked)
  - hourly_profit   = capital × avg_return × trades_per_hour

The multiplier uses the Heat Map to allocate capital across
opportunity types, chains, and protocols.

Mathematical Model (from projections):
  Base Capital (C₀)          = $2,500  (accumulated Phase 1)
  Reinvestment Rate (r)      = 60%
  Average Return/Trade (ROI) = 0.35%
  Trades/Hour (t)            = 45

  Compounding yields ~$7.3M capital base by hour 24.
"""

import asyncio
import time
import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any, Tuple
from enum import Enum

from .heat_map import HeatMap
from .profit_ledger import ProfitLedger, PhaseState


class MultiplierState(Enum):
    """Multiplier operating modes"""
    INACTIVE = "inactive"             # Phases 1 & 2 — multiplier off
    WARMING_UP = "warming_up"         # First hour of Phase 3
    ACTIVE = "active"                 # Fully compounding
    MAX_EXPOSURE = "max_exposure"     # Near risk limits
    COOLDOWN = "cooldown"             # Reducing exposure after losses


@dataclass
class AllocationSlot:
    """Capital allocated to a specific opportunity vector"""
    heat_key: str               # From heat map (type:chain:protocol)
    allocated_usd: float
    weight: float               # 0-1 allocation weight
    expected_roi: float         # From heat map avg_roi_percent
    expected_trades_per_hour: float
    expected_hourly_profit: float
    actual_profit_usd: float = 0.0
    trades_executed: int = 0


@dataclass
class MultiplierSnapshot:
    """Point-in-time snapshot of multiplier state"""
    hour: int
    capital_base: float
    reinvested: float
    profit_reserve: float
    trades_this_hour: int
    avg_profit_per_trade: float
    hourly_profit: float
    cumulative_reinvested: float
    state: MultiplierState
    allocations: Dict[str, float] = field(default_factory=dict)


class CapitalMultiplier:
    """
    Adaptive Capital Reinvestment Engine.
    Only active during Phase 3 (cumulative profit ≥ $45,000).
    """

    # ── Configurable Parameters ──
    DEFAULT_REINVEST_RATE = 0.60          # 60% reinvested
    DEFAULT_AVG_RETURN = 0.0035           # 0.35% per trade
    DEFAULT_TRADES_PER_HOUR = 45
    MAX_SINGLE_POSITION_PCT = 0.15        # No single trade > 15% of capital
    MAX_TOTAL_EXPOSURE_USD = 50_000_000   # Hard cap
    DRAWDOWN_COOLDOWN_THRESHOLD = 0.10    # 10% drawdown triggers cooldown
    COOLDOWN_REINVEST_RATE = 0.30         # Reduced rate during cooldown
    WARMUP_HOURS = 1                      # Hours of Phase 3 warm-up

    def __init__(
        self,
        ledger: ProfitLedger,
        heat_map: HeatMap,
        config: Dict[str, Any] = None,
    ):
        self.ledger = ledger
        self.heat_map = heat_map
        self.config = config or {}

        # Parameters (overridable)
        self.reinvest_rate = self.config.get('reinvest_rate', self.DEFAULT_REINVEST_RATE)
        self.avg_return = self.config.get('avg_return', self.DEFAULT_AVG_RETURN)
        self.trades_per_hour = self.config.get('trades_per_hour', self.DEFAULT_TRADES_PER_HOUR)

        # State
        self.state = MultiplierState.INACTIVE
        self.capital_base = 0.0
        self.profit_reserve = 0.0
        self.cumulative_reinvested = 0.0
        self.peak_capital = 0.0
        self.activation_time: Optional[float] = None

        # Allocation slots
        self.allocations: Dict[str, AllocationSlot] = {}

        # History
        self.snapshots: List[MultiplierSnapshot] = []
        self._snapshot_interval = 3600  # Every hour
        self._last_snapshot_time = 0.0

        print("🔄 Capital Multiplier initialized")
        print(f"   Reinvestment Rate: {self.reinvest_rate*100:.0f}%")
        print(f"   Avg Return/Trade:  {self.avg_return*100:.2f}%")
        print(f"   Target Trades/Hr:  {self.trades_per_hour}")
        print(f"   Max Exposure:      ${self.MAX_TOTAL_EXPOSURE_USD:,.0f}")

    # ──────────────────────────────────────────────
    # LIFECYCLE
    # ──────────────────────────────────────────────

    async def activate(self, initial_capital: float):
        """
        Activate the multiplier (called when Phase 3 begins).
        initial_capital = cumulative profit from Phases 1 & 2.
        """
        self.capital_base = initial_capital
        self.peak_capital = initial_capital
        self.activation_time = time.time()
        self.state = MultiplierState.WARMING_UP

        # Seed allocations from heat map
        await self.rebalance_allocations()

        print(f"\n{'='*70}")
        print(f"   🔄 CAPITAL MULTIPLIER ACTIVATED")
        print(f"   Initial Capital: ${self.capital_base:,.2f}")
        print(f"   State: {self.state.value}")
        print(f"{'='*70}\n")

    async def deactivate(self):
        self.state = MultiplierState.INACTIVE
        print("   🔄 Capital Multiplier deactivated")

    # ──────────────────────────────────────────────
    # TRADE SIZING
    # ──────────────────────────────────────────────

    def get_position_size(
        self,
        opportunity_type: str,
        chain_id: int,
        protocol: str,
        base_profit_usd: float,
    ) -> float:
        """
        Given a detected opportunity, return the capital to deploy.
        In Phases 1-2: returns 0 (flash-loan only).
        In Phase 3: returns position sized by heat map allocation.
        """
        if self.state == MultiplierState.INACTIVE:
            return 0.0  # Flash loan only — no capital needed

        heat_key = f"{opportunity_type}:{chain_id}:{protocol}"
        slot = self.allocations.get(heat_key)

        if slot:
            # Use allocated capital, capped at max single position
            max_position = self.capital_base * self.MAX_SINGLE_POSITION_PCT
            return min(slot.allocated_usd, max_position)
        else:
            # New opportunity type not in heat map — small exploratory position
            exploratory = self.capital_base * 0.02  # 2% exploratory
            return min(exploratory, 1_000.0)

    def get_max_flash_loan(self) -> float:
        """
        Max flash loan amount the system should pursue.
        In Phase 3, we can bid for larger opportunities because
        we can afford higher priority fees.
        """
        if self.state == MultiplierState.INACTIVE:
            return 500_000.0  # Default $500k max during cold start

        # Scale with capital base — can pursue up to 100× capital
        return min(self.capital_base * 100, self.MAX_TOTAL_EXPOSURE_USD)

    # ──────────────────────────────────────────────
    # REINVESTMENT
    # ──────────────────────────────────────────────

    async def process_profit(self, net_profit_usd: float, opportunity_key: str = ""):
        """
        Process a realized profit through the multiplier.
        Splits between reinvestment and profit reserve.
        """
        if self.state == MultiplierState.INACTIVE:
            return

        # Check for warmup → active transition
        if self.state == MultiplierState.WARMING_UP and self.activation_time:
            hours_active = (time.time() - self.activation_time) / 3600
            if hours_active >= self.WARMUP_HOURS:
                self.state = MultiplierState.ACTIVE
                print(f"   🔄 Multiplier warm-up complete → ACTIVE")

        # Determine reinvestment rate (reduced during cooldown)
        rate = self.COOLDOWN_REINVEST_RATE if self.state == MultiplierState.COOLDOWN else self.reinvest_rate

        reinvest_amount = net_profit_usd * rate
        reserve_amount = net_profit_usd * (1 - rate)

        self.capital_base += reinvest_amount
        self.profit_reserve += reserve_amount
        self.cumulative_reinvested += reinvest_amount

        # Update peak
        if self.capital_base > self.peak_capital:
            self.peak_capital = self.capital_base

        # Drawdown check
        if self.peak_capital > 0:
            drawdown = (self.peak_capital - self.capital_base) / self.peak_capital
            if drawdown >= self.DRAWDOWN_COOLDOWN_THRESHOLD:
                if self.state != MultiplierState.COOLDOWN:
                    self.state = MultiplierState.COOLDOWN
                    print(f"   ⚠️ Drawdown {drawdown*100:.1f}% → COOLDOWN mode")
            elif self.state == MultiplierState.COOLDOWN and drawdown < 0.05:
                self.state = MultiplierState.ACTIVE
                print(f"   ✅ Drawdown recovered → ACTIVE mode")

        # Max exposure check
        if self.capital_base >= self.MAX_TOTAL_EXPOSURE_USD:
            self.state = MultiplierState.MAX_EXPOSURE
            # Stop reinvesting, everything goes to reserve
            overflow = self.capital_base - self.MAX_TOTAL_EXPOSURE_USD
            self.capital_base = self.MAX_TOTAL_EXPOSURE_USD
            self.profit_reserve += overflow

        # Update allocation slot
        if opportunity_key and opportunity_key in self.allocations:
            self.allocations[opportunity_key].actual_profit_usd += net_profit_usd
            self.allocations[opportunity_key].trades_executed += 1

        # Update ledger capital tracking
        self.ledger.capital_base_usd = self.capital_base
        self.ledger.profit_reserve_usd = self.profit_reserve

        # Maybe snapshot
        await self._maybe_snapshot()

    async def process_loss(self, loss_usd: float, opportunity_key: str = ""):
        """Process a realized loss (failed trade with gas costs)."""
        if self.state == MultiplierState.INACTIVE:
            return
        self.capital_base = max(0, self.capital_base - abs(loss_usd))
        self.ledger.capital_base_usd = self.capital_base

    # ──────────────────────────────────────────────
    # ALLOCATION REBALANCING
    # ──────────────────────────────────────────────

    async def rebalance_allocations(self):
        """
        Rebalance capital allocations using heat map weights.
        Called periodically (every 5-10 minutes) or on major events.
        """
        if self.state == MultiplierState.INACTIVE:
            return

        weights = self.heat_map.get_capital_allocation_weights(top_n=15)

        new_allocations: Dict[str, AllocationSlot] = {}

        for heat_key, weight in weights.items():
            # Parse key
            parts = heat_key.split(':')
            if len(parts) < 3:
                continue
            opp_type, chain_str, protocol = parts[0], parts[1], parts[2]

            # Get heat map entry for ROI and frequency estimates
            entry = self.heat_map.entries.get(heat_key)
            expected_roi = entry.avg_roi_percent / 100 if entry else self.avg_return
            expected_freq = entry.avg_frequency_per_hour if entry else 5.0

            allocated = self.capital_base * weight

            new_allocations[heat_key] = AllocationSlot(
                heat_key=heat_key,
                allocated_usd=allocated,
                weight=weight,
                expected_roi=expected_roi,
                expected_trades_per_hour=expected_freq,
                expected_hourly_profit=allocated * expected_roi * expected_freq,
            )

        self.allocations = new_allocations

    # ──────────────────────────────────────────────
    # PROJECTIONS
    # ──────────────────────────────────────────────

    def project_growth(self, hours: int = 12) -> List[Dict]:
        """
        Project capital growth over next N hours using current parameters.
        """
        projections = []
        capital = self.capital_base
        reserve = self.profit_reserve

        for h in range(1, hours + 1):
            hourly_profit = capital * self.avg_return * self.trades_per_hour
            reinvest = hourly_profit * self.reinvest_rate
            to_reserve = hourly_profit * (1 - self.reinvest_rate)
            capital += reinvest
            reserve += to_reserve

            capital = min(capital, self.MAX_TOTAL_EXPOSURE_USD)

            projections.append({
                'hour': h,
                'capital_base': round(capital, 2),
                'hourly_profit': round(hourly_profit, 2),
                'reinvested': round(reinvest, 2),
                'profit_reserve': round(reserve, 2),
                'trades': self.trades_per_hour,
                'avg_profit_per_trade': round(hourly_profit / max(self.trades_per_hour, 1), 2),
            })

        return projections

    # ──────────────────────────────────────────────
    # SNAPSHOTS
    # ──────────────────────────────────────────────

    async def _maybe_snapshot(self):
        now = time.time()
        if now - self._last_snapshot_time < self._snapshot_interval:
            return

        hour = int((now - (self.activation_time or now)) / 3600)
        snapshot = MultiplierSnapshot(
            hour=hour,
            capital_base=round(self.capital_base, 2),
            reinvested=round(self.cumulative_reinvested, 2),
            profit_reserve=round(self.profit_reserve, 2),
            trades_this_hour=self.ledger.total_transactions,  # Approximate
            avg_profit_per_trade=round(self.ledger.avg_profit_per_tx(), 2),
            hourly_profit=round(self.ledger.hourly_run_rate(), 2),
            cumulative_reinvested=round(self.cumulative_reinvested, 2),
            state=self.state,
            allocations={k: round(v.allocated_usd, 2) for k, v in self.allocations.items()},
        )
        self.snapshots.append(snapshot)
        self._last_snapshot_time = now

    # ──────────────────────────────────────────────
    # STATUS
    # ──────────────────────────────────────────────

    def status_report(self) -> Dict[str, Any]:
        hours_active = 0
        if self.activation_time:
            hours_active = (time.time() - self.activation_time) / 3600

        return {
            'state': self.state.value,
            'capital_base': round(self.capital_base, 2),
            'profit_reserve': round(self.profit_reserve, 2),
            'peak_capital': round(self.peak_capital, 2),
            'cumulative_reinvested': round(self.cumulative_reinvested, 2),
            'reinvest_rate': self.reinvest_rate,
            'hours_active': round(hours_active, 2),
            'num_allocations': len(self.allocations),
            'max_flash_loan': round(self.get_max_flash_loan(), 2),
            'projected_next_12h': self.project_growth(12)[-1] if self.capital_base > 0 else {},
        }

    def print_dashboard(self):
        r = self.status_report()
        print(f"\n{'='*70}")
        print(f"  🔄 CAPITAL MULTIPLIER DASHBOARD")
        print(f"{'='*70}")
        print(f"  State:              {r['state']}")
        print(f"  Capital Base:       ${r['capital_base']:,.2f}")
        print(f"  Profit Reserve:     ${r['profit_reserve']:,.2f}")
        print(f"  Peak Capital:       ${r['peak_capital']:,.2f}")
        print(f"  Reinvest Rate:      {r['reinvest_rate']*100:.0f}%")
        print(f"  Hours Active:       {r['hours_active']:.2f}")
        print(f"  Active Allocations: {r['num_allocations']}")
        print(f"  Max Flash Loan:     ${r['max_flash_loan']:,.2f}")

        if r.get('projected_next_12h'):
            p = r['projected_next_12h']
            print(f"  ── 12hr Projection ──")
            print(f"  Capital:            ${p['capital_base']:,.2f}")
            print(f"  Hourly Profit:      ${p['hourly_profit']:,.2f}")
            print(f"  Reserve:            ${p['profit_reserve']:,.2f}")

        if self.allocations:
            print(f"\n  ── Top Allocations ──")
            sorted_allocs = sorted(self.allocations.values(), key=lambda a: a.allocated_usd, reverse=True)
            for slot in sorted_allocs[:5]:
                print(f"  {slot.heat_key:<40} ${slot.allocated_usd:>12,.2f} ({slot.weight*100:.1f}%)")

        print(f"{'='*70}")
