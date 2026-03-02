#!/usr/bin/env python3
"""
PROFIT LEDGER — Real-time Profit & Loss + Phase Transition Tracker
===================================================================
Tracks every executed trade, cumulative P&L, phase transitions, and
generates the financial state required by the Capital Multiplier.

Phase Transitions:
  Phase 1 (Cold Start)   → active until cumulative profit ≥ PHASE2_THRESHOLD
  Phase 2 (Heat Map)     → active until cumulative profit ≥ PHASE3_THRESHOLD
  Phase 3 (Multiplier)   → active indefinitely (compounding)
"""

import asyncio
import json
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Any
from collections import defaultdict
from pathlib import Path


class PhaseState(Enum):
    """Engine operating phase"""
    COLD_START = "phase_1_cold_start"
    HEAT_MAP = "phase_2_heat_map"
    MULTIPLIER = "phase_3_capital_multiplier"


@dataclass
class ProfitEntry:
    """Single profit/loss record"""
    entry_id: str
    timestamp: float
    chain_id: int
    opportunity_type: str          # liquidation, arbitrage, cross_chain, etc.
    protocol: str                  # aave_v3, uniswap_v3, etc.
    tx_hash: str
    gross_profit_usd: float
    gas_cost_usd: float
    flash_loan_fee_usd: float
    net_profit_usd: float
    capital_deployed_usd: float    # 0 for flash-loan-only trades
    roi_percent: float
    execution_time_ms: int
    block_number: int
    phase: PhaseState
    metadata: Dict[str, Any] = field(default_factory=dict)

    @property
    def is_profitable(self) -> bool:
        return self.net_profit_usd > 0


@dataclass
class HourlySnapshot:
    """Aggregated hourly metrics"""
    hour: int                       # Hour number since start
    timestamp: float
    transactions: int
    avg_profit_per_tx: float
    total_profit: float
    cumulative_profit: float
    capital_base: float
    phase: PhaseState
    opportunity_breakdown: Dict[str, int] = field(default_factory=dict)


class ProfitLedger:
    """
    Central profit tracking system.
    Records every trade, maintains running totals, triggers phase transitions.
    """

    # Phase transition thresholds (cumulative net profit USD)
    PHASE2_THRESHOLD = 2_500.0      # $2,500 → enable heat map
    PHASE3_THRESHOLD = 45_000.0     # $45,000 → enable capital multiplier

    def __init__(self, config: Dict[str, Any] = None):
        self.config = config or {}

        # Override thresholds from config
        self.PHASE2_THRESHOLD = self.config.get('phase2_threshold', self.PHASE2_THRESHOLD)
        self.PHASE3_THRESHOLD = self.config.get('phase3_threshold', self.PHASE3_THRESHOLD)

        # State
        self.current_phase = PhaseState.COLD_START
        self.start_time = time.time()
        self.entries: List[ProfitEntry] = []
        self.hourly_snapshots: List[HourlySnapshot] = []

        # Running totals
        self.cumulative_profit_usd = 0.0
        self.cumulative_gross_usd = 0.0
        self.cumulative_gas_usd = 0.0
        self.cumulative_fees_usd = 0.0
        self.total_transactions = 0
        self.successful_transactions = 0
        self.failed_transactions = 0

        # Per-type counters
        self.profit_by_type: Dict[str, float] = defaultdict(float)
        self.count_by_type: Dict[str, int] = defaultdict(int)
        self.profit_by_chain: Dict[int, float] = defaultdict(float)
        self.profit_by_protocol: Dict[str, float] = defaultdict(float)

        # Capital tracking
        self.capital_base_usd = 0.0  # Available capital for multiplier
        self.profit_reserve_usd = 0.0  # Locked profit (not reinvested)

        # Phase transition callbacks
        self._phase_callbacks: List = []

        # Persistence
        self._ledger_path = Path(self.config.get('ledger_path', 'profit_ledger.json'))
        self._snapshot_path = Path(self.config.get('snapshot_path', 'hourly_snapshots.json'))

        # Load existing state if available
        self._load_state()

        print("📒 Profit Ledger initialized")
        print(f"   Phase: {self.current_phase.value}")
        print(f"   Cumulative P&L: ${self.cumulative_profit_usd:,.2f}")
        print(f"   Phase 2 threshold: ${self.PHASE2_THRESHOLD:,.0f}")
        print(f"   Phase 3 threshold: ${self.PHASE3_THRESHOLD:,.0f}")

    # ──────────────────────────────────────────────
    # RECORDING
    # ──────────────────────────────────────────────

    async def record(self, entry: ProfitEntry) -> PhaseState:
        """
        Record a trade and return the (possibly updated) phase.
        """
        entry.phase = self.current_phase
        self.entries.append(entry)
        self.total_transactions += 1

        if entry.is_profitable:
            self.successful_transactions += 1
        else:
            self.failed_transactions += 1

        # Update running totals
        self.cumulative_gross_usd += entry.gross_profit_usd
        self.cumulative_gas_usd += entry.gas_cost_usd
        self.cumulative_fees_usd += entry.flash_loan_fee_usd
        self.cumulative_profit_usd += entry.net_profit_usd

        # Per-type / per-chain / per-protocol
        self.profit_by_type[entry.opportunity_type] += entry.net_profit_usd
        self.count_by_type[entry.opportunity_type] += 1
        self.profit_by_chain[entry.chain_id] += entry.net_profit_usd
        self.profit_by_protocol[entry.protocol] += entry.net_profit_usd

        # Check phase transition
        old_phase = self.current_phase
        self._evaluate_phase_transition()

        if self.current_phase != old_phase:
            print(f"\n{'='*70}")
            print(f"   🚀 PHASE TRANSITION: {old_phase.value} → {self.current_phase.value}")
            print(f"   Cumulative Profit: ${self.cumulative_profit_usd:,.2f}")
            print(f"{'='*70}\n")
            await self._fire_phase_callbacks(old_phase, self.current_phase)

        # Auto-snapshot every hour
        await self._maybe_snapshot()

        # Persist
        self._persist_state()

        return self.current_phase

    # ──────────────────────────────────────────────
    # PHASE TRANSITIONS
    # ──────────────────────────────────────────────

    def _evaluate_phase_transition(self):
        if self.current_phase == PhaseState.COLD_START:
            if self.cumulative_profit_usd >= self.PHASE2_THRESHOLD:
                self.current_phase = PhaseState.HEAT_MAP
        elif self.current_phase == PhaseState.HEAT_MAP:
            if self.cumulative_profit_usd >= self.PHASE3_THRESHOLD:
                self.current_phase = PhaseState.MULTIPLIER

    def on_phase_change(self, callback):
        self._phase_callbacks.append(callback)

    async def _fire_phase_callbacks(self, old: PhaseState, new: PhaseState):
        for cb in self._phase_callbacks:
            try:
                await cb(old, new)
            except Exception as e:
                print(f"   ⚠️ Phase callback error: {e}")

    # ──────────────────────────────────────────────
    # SNAPSHOTS
    # ──────────────────────────────────────────────

    async def _maybe_snapshot(self):
        elapsed_hours = int((time.time() - self.start_time) / 3600)
        if len(self.hourly_snapshots) <= elapsed_hours:
            # Build snapshot for this hour
            hour_entries = [e for e in self.entries
                           if int((e.timestamp - self.start_time) / 3600) == elapsed_hours]
            snapshot = HourlySnapshot(
                hour=elapsed_hours,
                timestamp=time.time(),
                transactions=len(hour_entries),
                avg_profit_per_tx=(
                    sum(e.net_profit_usd for e in hour_entries) / max(len(hour_entries), 1)
                ),
                total_profit=sum(e.net_profit_usd for e in hour_entries),
                cumulative_profit=self.cumulative_profit_usd,
                capital_base=self.capital_base_usd,
                phase=self.current_phase,
                opportunity_breakdown=dict(self.count_by_type),
            )
            self.hourly_snapshots.append(snapshot)

    # ──────────────────────────────────────────────
    # ANALYTICS
    # ──────────────────────────────────────────────

    def success_rate(self) -> float:
        if self.total_transactions == 0:
            return 0.0
        return self.successful_transactions / self.total_transactions

    def avg_profit_per_tx(self) -> float:
        if self.successful_transactions == 0:
            return 0.0
        return self.cumulative_profit_usd / self.successful_transactions

    def elapsed_hours(self) -> float:
        return (time.time() - self.start_time) / 3600

    def hourly_run_rate(self) -> float:
        hours = self.elapsed_hours()
        if hours < 0.01:
            return 0.0
        return self.cumulative_profit_usd / hours

    def top_opportunity_types(self, n: int = 5) -> List[tuple]:
        """Return top N opportunity types by cumulative profit."""
        sorted_types = sorted(self.profit_by_type.items(), key=lambda x: x[1], reverse=True)
        return sorted_types[:n]

    def top_chains(self, n: int = 5) -> List[tuple]:
        sorted_chains = sorted(self.profit_by_chain.items(), key=lambda x: x[1], reverse=True)
        return sorted_chains[:n]

    # ──────────────────────────────────────────────
    # STATUS REPORT
    # ──────────────────────────────────────────────

    def status_report(self) -> Dict[str, Any]:
        return {
            'phase': self.current_phase.value,
            'elapsed_hours': round(self.elapsed_hours(), 2),
            'total_transactions': self.total_transactions,
            'successful_transactions': self.successful_transactions,
            'failed_transactions': self.failed_transactions,
            'success_rate': round(self.success_rate() * 100, 1),
            'cumulative_profit_usd': round(self.cumulative_profit_usd, 2),
            'cumulative_gas_usd': round(self.cumulative_gas_usd, 2),
            'cumulative_fees_usd': round(self.cumulative_fees_usd, 2),
            'avg_profit_per_tx': round(self.avg_profit_per_tx(), 2),
            'hourly_run_rate': round(self.hourly_run_rate(), 2),
            'capital_base_usd': round(self.capital_base_usd, 2),
            'profit_reserve_usd': round(self.profit_reserve_usd, 2),
            'top_opportunity_types': self.top_opportunity_types(),
            'top_chains': self.top_chains(),
            'phase2_progress': min(100, round(self.cumulative_profit_usd / self.PHASE2_THRESHOLD * 100, 1)),
            'phase3_progress': min(100, round(self.cumulative_profit_usd / self.PHASE3_THRESHOLD * 100, 1)),
        }

    def print_dashboard(self):
        r = self.status_report()
        print(f"\n{'='*70}")
        print(f"  💰 PROFIT LEDGER DASHBOARD")
        print(f"{'='*70}")
        print(f"  Phase:              {r['phase']}")
        print(f"  Elapsed:            {r['elapsed_hours']} hours")
        print(f"  Transactions:       {r['total_transactions']} ({r['success_rate']}% success)")
        print(f"  Cumulative Profit:  ${r['cumulative_profit_usd']:,.2f}")
        print(f"  Gas Spent:          ${r['cumulative_gas_usd']:,.2f}")
        print(f"  Flash Loan Fees:    ${r['cumulative_fees_usd']:,.2f}")
        print(f"  Avg Profit/TX:      ${r['avg_profit_per_tx']:,.2f}")
        print(f"  Hourly Run Rate:    ${r['hourly_run_rate']:,.2f}/hr")
        print(f"  Capital Base:       ${r['capital_base_usd']:,.2f}")
        print(f"  Profit Reserve:     ${r['profit_reserve_usd']:,.2f}")
        print(f"  Phase 2 Progress:   {r['phase2_progress']}%")
        print(f"  Phase 3 Progress:   {r['phase3_progress']}%")
        print(f"{'='*70}")

    # ──────────────────────────────────────────────
    # PERSISTENCE
    # ──────────────────────────────────────────────

    def _persist_state(self):
        try:
            state = {
                'current_phase': self.current_phase.value,
                'start_time': self.start_time,
                'cumulative_profit_usd': self.cumulative_profit_usd,
                'cumulative_gross_usd': self.cumulative_gross_usd,
                'cumulative_gas_usd': self.cumulative_gas_usd,
                'cumulative_fees_usd': self.cumulative_fees_usd,
                'total_transactions': self.total_transactions,
                'successful_transactions': self.successful_transactions,
                'failed_transactions': self.failed_transactions,
                'capital_base_usd': self.capital_base_usd,
                'profit_reserve_usd': self.profit_reserve_usd,
                'profit_by_type': dict(self.profit_by_type),
                'count_by_type': dict(self.count_by_type),
                'last_updated': time.time(),
            }
            with open(self._ledger_path, 'w') as f:
                json.dump(state, f, indent=2)
        except Exception:
            pass  # Non-critical

    def _load_state(self):
        try:
            if self._ledger_path.exists():
                with open(self._ledger_path) as f:
                    state = json.load(f)
                self.current_phase = PhaseState(state.get('current_phase', PhaseState.COLD_START.value))
                self.start_time = state.get('start_time', time.time())
                self.cumulative_profit_usd = state.get('cumulative_profit_usd', 0.0)
                self.cumulative_gross_usd = state.get('cumulative_gross_usd', 0.0)
                self.cumulative_gas_usd = state.get('cumulative_gas_usd', 0.0)
                self.cumulative_fees_usd = state.get('cumulative_fees_usd', 0.0)
                self.total_transactions = state.get('total_transactions', 0)
                self.successful_transactions = state.get('successful_transactions', 0)
                self.failed_transactions = state.get('failed_transactions', 0)
                self.capital_base_usd = state.get('capital_base_usd', 0.0)
                self.profit_reserve_usd = state.get('profit_reserve_usd', 0.0)
                for k, v in state.get('profit_by_type', {}).items():
                    self.profit_by_type[k] = v
                for k, v in state.get('count_by_type', {}).items():
                    self.count_by_type[k] = v
                print(f"   📒 Loaded existing ledger state (${self.cumulative_profit_usd:,.2f})")
        except Exception:
            pass  # Fresh start
