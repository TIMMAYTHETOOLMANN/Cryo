#!/usr/bin/env python3
"""
TEST SUITE — Profit Engine Core Modules
=========================================
Comprehensive tests for the core profit engine modules:
  - FlashLoanRouter: provider init, route selection, fee optimization
  - GasOptimizer: gas estimation, profitability, competition bidding
  - ProfitLedger: recording, phase transitions, analytics, persistence
  - HeatMap: pattern tracking, scoring, ranking
  - CapitalMultiplier: activation, compounding, allocation

These tests run entirely offline with no RPC/blockchain connections required.
"""

import asyncio
import os
import json
import sys
import time
import tempfile
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock, AsyncMock
from collections import defaultdict

# ── Ensure project root is on sys.path ──────────────────────────────────────
PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from profit_engine.flash_loan_router import (
    FlashLoanRouter,
    FlashLoanProvider,
    FlashLoanRoute,
    ProviderProfile,
)
from profit_engine.gas_optimizer import (
    GasOptimizer,
    GasEstimate,
    ChainGasState,
)
from profit_engine.profit_ledger import (
    ProfitLedger,
    ProfitEntry,
    PhaseState,
    HourlySnapshot,
)
from profit_engine.heat_map import (
    HeatMap,
    HeatMapEntry,
)


# ═══════════════════════════════════════════════════════════════════════════════
#  FlashLoanRouter
# ═══════════════════════════════════════════════════════════════════════════════


class TestFlashLoanRouter:
    """Test flash loan provider selection and routing."""

    @pytest.fixture
    def router(self):
        return FlashLoanRouter()

    def test_init_registers_all_providers(self, router):
        """Router initializes with all 6 providers."""
        assert len(router.providers) == 6
        expected = {
            FlashLoanProvider.BALANCER,
            FlashLoanProvider.DODO,
            FlashLoanProvider.AAVE_V3,
            FlashLoanProvider.AAVE_V2,
            FlashLoanProvider.UNISWAP_V3,
            FlashLoanProvider.MAKER,
        }
        assert set(router.providers.keys()) == expected

    def test_balancer_is_zero_fee(self, router):
        """Balancer charges 0% fee."""
        profile = router.providers[FlashLoanProvider.BALANCER]
        assert profile.fee_bps == 0.0

    def test_dodo_is_zero_fee(self, router):
        """DODO charges 0% fee."""
        profile = router.providers[FlashLoanProvider.DODO]
        assert profile.fee_bps == 0.0

    def test_aave_v3_fee(self, router):
        """Aave V3 charges 5 bps (0.05%)."""
        profile = router.providers[FlashLoanProvider.AAVE_V3]
        assert profile.fee_bps == 5.0

    def test_aave_v2_fee(self, router):
        """Aave V2 charges 9 bps (0.09%)."""
        profile = router.providers[FlashLoanProvider.AAVE_V2]
        assert profile.fee_bps == 9.0

    def test_uniswap_v3_fee(self, router):
        """Uniswap V3 charges 30 bps (0.3%)."""
        profile = router.providers[FlashLoanProvider.UNISWAP_V3]
        assert profile.fee_bps == 30.0

    def test_maker_dai_only(self, router):
        """Maker flash loans only support DAI."""
        profile = router.providers[FlashLoanProvider.MAKER]
        assert profile.supported_assets == ['DAI']
        assert profile.supported_chains == [1]

    def test_find_best_route_prefers_zero_fee(self, router):
        """Router selects lowest-fee provider for Ethereum WETH."""
        route = router.find_best_route(
            chain_id=1, asset="WETH", amount_usd=10000, gross_profit_usd=500,
        )
        assert route is not None
        # Should pick Balancer or DODO (both 0% fee)
        assert route.fee_bps == 0.0

    def test_find_best_route_respects_chain(self, router):
        """Route selection respects chain availability."""
        # BSC only available on DODO among zero-fee providers
        route = router.find_best_route(
            chain_id=56, asset="WETH", amount_usd=5000, gross_profit_usd=200,
        )
        if route is not None:
            assert 56 in router.providers[route.provider].supported_chains

    def test_find_best_route_respects_asset(self, router):
        """DAI on Ethereum should consider Maker (0% fee)."""
        route = router.find_best_route(
            chain_id=1, asset="DAI", amount_usd=100000, gross_profit_usd=1000,
        )
        assert route is not None
        assert route.fee_bps == 0.0

    def test_find_best_route_none_for_unsupported(self, router):
        """Returns None for unsupported chain/asset combos."""
        route = router.find_best_route(
            chain_id=999, asset="WETH", amount_usd=1000, gross_profit_usd=100,
        )
        assert route is None

    def test_find_best_route_rejects_excessive_fee(self, router):
        """Route rejected if fee exceeds 50% of gross profit."""
        # Very small profit makes most providers too expensive
        route = router.find_best_route(
            chain_id=1, asset="WETH", amount_usd=1_000_000,
            gross_profit_usd=0.01,
        )
        # Only zero-fee providers should survive
        if route is not None:
            assert route.fee_bps == 0.0

    def test_route_has_pool_address(self, router):
        """Route includes pool address for on-chain execution."""
        route = router.find_best_route(
            chain_id=1, asset="WETH", amount_usd=10000, gross_profit_usd=500,
        )
        assert route is not None
        assert route.pool_address.startswith("0x")
        assert len(route.pool_address) == 42

    def test_route_has_gas_estimate(self, router):
        """Route includes gas estimate."""
        route = router.find_best_route(
            chain_id=1, asset="WETH", amount_usd=10000, gross_profit_usd=500,
        )
        assert route is not None
        assert route.estimated_gas_units > 0

    def test_route_has_calldata_template(self, router):
        """Route includes calldata template."""
        route = router.find_best_route(
            chain_id=1, asset="WETH", amount_usd=10000, gross_profit_usd=500,
        )
        assert route is not None
        assert route.calldata_template.startswith("0x")

    def test_find_multi_provider_route(self, router):
        """Multi-provider route for multiple assets."""
        routes = router.find_multi_provider_route(
            chain_id=1,
            assets=["WETH", "USDC"],
            amounts_usd=[10000, 5000],
            gross_profit_usd=500,
        )
        assert len(routes) == 2
        assert all(r.chain_id == 1 for r in routes)

    def test_report_outcome_updates_stats(self, router):
        """Outcome reporting updates provider statistics."""
        router.report_outcome(FlashLoanProvider.AAVE_V3, True, 50000)
        profile = router.providers[FlashLoanProvider.AAVE_V3]
        assert profile.total_loans == 1
        assert profile.total_volume_usd == 50000

    def test_report_outcome_failure_lowers_rate(self, router):
        """Failed outcomes decrease provider success rate."""
        initial_rate = router.providers[FlashLoanProvider.AAVE_V3].success_rate
        for _ in range(20):
            router.report_outcome(FlashLoanProvider.AAVE_V3, False, 1000)
        assert router.providers[FlashLoanProvider.AAVE_V3].success_rate < initial_rate

    def test_status_report(self, router):
        """Status report returns structured data."""
        status = router.status()
        assert 'routes_computed' in status
        assert 'total_fees_saved_usd' in status
        assert 'providers' in status
        assert 'balancer' in status['providers']

    def test_routes_counter_increments(self, router):
        """Routes computed counter increments on each route."""
        assert router.routes_computed == 0
        router.find_best_route(1, "WETH", 10000, 500)
        assert router.routes_computed == 1
        router.find_best_route(1, "USDC", 5000, 200)
        assert router.routes_computed == 2


# ═══════════════════════════════════════════════════════════════════════════════
#  GasOptimizer
# ═══════════════════════════════════════════════════════════════════════════════


class TestGasOptimizer:
    """Test gas estimation and profitability checks."""

    @pytest.fixture
    def optimizer(self):
        return GasOptimizer()

    def test_init_all_chains(self, optimizer):
        """Optimizer initializes gas state for all 8 chains."""
        assert len(optimizer.chain_states) == 8
        for chain_id in [1, 42161, 10, 137, 8453, 43114, 56, 324]:
            assert chain_id in optimizer.chain_states

    def test_chain_multipliers_defined(self, optimizer):
        """All chains have gas cost multipliers."""
        assert len(GasOptimizer.CHAIN_MULTIPLIERS) == 8
        assert GasOptimizer.CHAIN_MULTIPLIERS[1] == 1.0  # Ethereum baseline

    def test_l2_chains_are_cheaper(self, optimizer):
        """L2 chains have lower gas multipliers than Ethereum."""
        for chain_id in [42161, 10, 8453, 324]:
            assert GasOptimizer.CHAIN_MULTIPLIERS[chain_id] < 0.1

    def test_estimate_returns_gas_estimate(self, optimizer):
        """Estimate returns a structured GasEstimate."""
        est = optimizer.estimate(1, 'liquidation', 500.0)
        assert isinstance(est, GasEstimate)
        assert est.chain_id == 1
        assert est.estimated_gas_units > 0
        assert est.gas_cost_usd >= 0

    def test_estimate_profitable_trade(self, optimizer):
        """Highly profitable trade should be flagged as profitable."""
        est = optimizer.estimate(1, 'liquidation', 10000.0)
        assert est.is_profitable_at_current is True
        assert est.margin_remaining_usd > 0

    def test_estimate_l2_cheaper_than_l1(self, optimizer):
        """L2 gas costs should be much lower than L1."""
        l1_est = optimizer.estimate(1, 'liquidation', 500.0)
        l2_est = optimizer.estimate(42161, 'liquidation', 500.0)
        assert l2_est.gas_cost_usd < l1_est.gas_cost_usd

    def test_estimate_unknown_chain_refuses(self, optimizer):
        """Unknown chain returns conservative refusal with high penalty cost."""
        est = optimizer.estimate(99999, 'liquidation', 500.0)
        assert est.is_profitable_at_current is False
        # Gas cost should be extremely high to block execution on unknown chains
        assert est.gas_cost_usd > 0

    def test_gas_units_for_known_operations(self, optimizer):
        """Known operation types have gas unit estimates."""
        known_ops = [
            'liquidation', 'flash_loan_liquidation', 'arbitrage_2hop',
            'arbitrage_3hop', 'cross_chain_bridge', 'backrun',
        ]
        for op in known_ops:
            assert op in GasOptimizer.GAS_UNITS

    def test_max_acceptable_gas_calculated(self, optimizer):
        """Max acceptable gas gwei is computed based on profit."""
        est = optimizer.estimate(1, 'liquidation', 500.0)
        assert est.max_acceptable_gas_gwei > 0

    def test_margin_percent_calculated(self, optimizer):
        """Margin percentage is computed correctly."""
        est = optimizer.estimate(1, 'liquidation', 500.0)
        assert 0 <= est.margin_percent <= 1.0

    def test_gas_override(self, optimizer):
        """Gas units can be overridden."""
        est = optimizer.estimate(1, 'liquidation', 500.0, gas_units_override=100_000)
        assert est.estimated_gas_units == 100_000

    def test_compute_priority_bid_low_competition(self, optimizer):
        """Low competition → low priority fee bid."""
        bid = optimizer.compute_priority_bid(1, 500.0, 0.1)
        assert bid >= 0

    def test_compute_priority_bid_high_competition(self, optimizer):
        """High competition → higher priority fee bid."""
        low_bid = optimizer.compute_priority_bid(1, 500.0, 0.1)
        high_bid = optimizer.compute_priority_bid(1, 500.0, 0.9)
        assert high_bid >= low_bid

    def test_compute_priority_bid_unknown_chain(self, optimizer):
        """Unknown chain returns default bid."""
        bid = optimizer.compute_priority_bid(99999, 500.0, 0.5)
        assert bid == 2.0

    def test_chain_status_report(self, optimizer):
        """Chain status returns data for all chains."""
        status = optimizer.chain_status()
        assert len(status) == 8
        for cid, data in status.items():
            assert 'base_fee_gwei' in data
            assert 'priority_gwei' in data
            assert 'congestion' in data
            assert 'eth_price' in data

    def test_eth_prices_set(self, optimizer):
        """ETH prices are initialized for all chains."""
        for chain_id, state in optimizer.chain_states.items():
            assert state.eth_price_usd > 0


# ═══════════════════════════════════════════════════════════════════════════════
#  ProfitLedger
# ═══════════════════════════════════════════════════════════════════════════════


class TestProfitLedger:
    """Test profit recording, phase transitions, and analytics."""

    @pytest.fixture
    def ledger(self, tmp_path):
        """Fresh ledger with no prior state."""
        return ProfitLedger(config={
            'ledger_path': str(tmp_path / 'ledger.json'),
            'snapshot_path': str(tmp_path / 'snapshots.json'),
        })

    def _make_entry(self, net_profit=100.0, gross=120.0, gas=15.0, fee=5.0,
                    chain_id=1, opp_type="liquidation", protocol="aave_v3"):
        return ProfitEntry(
            entry_id=f"test-{time.time()}",
            timestamp=time.time(),
            chain_id=chain_id,
            opportunity_type=opp_type,
            protocol=protocol,
            tx_hash="0x" + "a" * 64,
            gross_profit_usd=gross,
            gas_cost_usd=gas,
            flash_loan_fee_usd=fee,
            net_profit_usd=net_profit,
            capital_deployed_usd=0.0,
            roi_percent=83.3,
            execution_time_ms=150,
            block_number=19000000,
            phase=PhaseState.COLD_START,
        )

    def test_initial_state(self, ledger):
        """Fresh ledger starts in Phase 1 with zero totals."""
        assert ledger.current_phase == PhaseState.COLD_START
        assert ledger.cumulative_profit_usd == 0.0
        assert ledger.total_transactions == 0
        assert ledger.successful_transactions == 0

    @pytest.mark.asyncio
    async def test_record_profitable_trade(self, ledger):
        """Recording a profitable trade updates all counters."""
        entry = self._make_entry(net_profit=100.0)
        await ledger.record(entry)
        assert ledger.total_transactions == 1
        assert ledger.successful_transactions == 1
        assert ledger.cumulative_profit_usd == 100.0

    @pytest.mark.asyncio
    async def test_record_losing_trade(self, ledger):
        """Recording a losing trade increments failure counter."""
        entry = self._make_entry(net_profit=-10.0, gross=5.0, gas=10.0, fee=5.0)
        await ledger.record(entry)
        assert ledger.total_transactions == 1
        assert ledger.failed_transactions == 1

    @pytest.mark.asyncio
    async def test_phase2_transition(self, ledger):
        """Phase transitions to Heat Map at $2,500."""
        assert ledger.current_phase == PhaseState.COLD_START
        # Record enough profits to cross Phase 2 threshold
        for _ in range(26):
            entry = self._make_entry(net_profit=100.0)
            await ledger.record(entry)
        assert ledger.current_phase == PhaseState.HEAT_MAP

    @pytest.mark.asyncio
    async def test_phase3_transition(self, ledger):
        """Phase transitions to Multiplier at $45,000."""
        for _ in range(460):
            entry = self._make_entry(net_profit=100.0)
            await ledger.record(entry)
        assert ledger.current_phase == PhaseState.MULTIPLIER

    @pytest.mark.asyncio
    async def test_profit_by_type_tracking(self, ledger):
        """Profits tracked by opportunity type."""
        await ledger.record(self._make_entry(
            net_profit=100, opp_type="liquidation"))
        await ledger.record(self._make_entry(
            net_profit=200, opp_type="arbitrage"))
        assert ledger.profit_by_type["liquidation"] == 100
        assert ledger.profit_by_type["arbitrage"] == 200

    @pytest.mark.asyncio
    async def test_profit_by_chain_tracking(self, ledger):
        """Profits tracked by chain."""
        await ledger.record(self._make_entry(
            net_profit=100, chain_id=1))
        await ledger.record(self._make_entry(
            net_profit=150, chain_id=42161))
        assert ledger.profit_by_chain[1] == 100
        assert ledger.profit_by_chain[42161] == 150

    @pytest.mark.asyncio
    async def test_profit_by_protocol_tracking(self, ledger):
        """Profits tracked by protocol."""
        await ledger.record(self._make_entry(
            net_profit=100, protocol="aave_v3"))
        await ledger.record(self._make_entry(
            net_profit=200, protocol="compound_v3"))
        assert ledger.profit_by_protocol["aave_v3"] == 100
        assert ledger.profit_by_protocol["compound_v3"] == 200

    @pytest.mark.asyncio
    async def test_gas_efficiency_tracking(self, ledger):
        """Gas efficiency tracked per vector:chain:protocol key."""
        await ledger.record(self._make_entry(
            gross=120, gas=80, net_profit=35))
        key = "liquidation:1:aave_v3"
        assert key in ledger.gas_efficiency
        assert ledger.gas_efficiency[key]['gross'] == 120
        assert ledger.gas_efficiency[key]['gas'] == 80

    @pytest.mark.asyncio
    async def test_gas_inefficient_vectors(self, ledger):
        """Vectors with >50% gas ratio flagged as inefficient."""
        for _ in range(5):
            await ledger.record(self._make_entry(gross=100, gas=60, net_profit=35))
        inefficient = ledger.gas_inefficient_vectors(threshold=0.5)
        assert len(inefficient) > 0
        assert inefficient[0]['gas_ratio'] > 0.5

    def test_success_rate_no_trades(self, ledger):
        """Success rate is 0 with no trades."""
        assert ledger.success_rate() == 0.0

    @pytest.mark.asyncio
    async def test_success_rate_calculation(self, ledger):
        """Success rate calculated correctly."""
        await ledger.record(self._make_entry(net_profit=100))
        await ledger.record(self._make_entry(net_profit=50))
        await ledger.record(self._make_entry(net_profit=-10, gross=5, gas=10, fee=5))
        assert ledger.success_rate() == pytest.approx(2/3)

    def test_avg_profit_no_trades(self, ledger):
        """Avg profit is 0 with no trades."""
        assert ledger.avg_profit_per_tx() == 0.0

    @pytest.mark.asyncio
    async def test_top_opportunity_types(self, ledger):
        """Top opportunity types sorted by profit."""
        await ledger.record(self._make_entry(net_profit=500, opp_type="arb"))
        await ledger.record(self._make_entry(net_profit=100, opp_type="liq"))
        top = ledger.top_opportunity_types(n=2)
        assert top[0][0] == "arb"
        assert top[0][1] == 500

    def test_status_report(self, ledger):
        """Status report returns all required fields."""
        report = ledger.status_report()
        required_keys = [
            'phase', 'elapsed_hours', 'total_transactions',
            'cumulative_profit_usd', 'success_rate',
            'phase2_progress', 'phase3_progress',
        ]
        for key in required_keys:
            assert key in report, f"Missing key: {key}"

    @pytest.mark.asyncio
    async def test_persistence(self, ledger, tmp_path):
        """Ledger state persists to disk."""
        await ledger.record(self._make_entry(net_profit=100))
        ledger_path = tmp_path / 'ledger.json'
        assert ledger_path.exists()
        with open(ledger_path) as f:
            state = json.load(f)
        assert state['cumulative_profit_usd'] == 100.0
        assert state['total_transactions'] == 1

    @pytest.mark.asyncio
    async def test_reload_state(self, tmp_path):
        """Ledger reloads persisted state."""
        cfg = {
            'ledger_path': str(tmp_path / 'ledger.json'),
            'snapshot_path': str(tmp_path / 'snapshots.json'),
        }
        ledger1 = ProfitLedger(config=cfg)
        await ledger1.record(self._make_entry(net_profit=500))
        assert ledger1.cumulative_profit_usd == 500.0

        # Create new ledger instance — should reload state
        ledger2 = ProfitLedger(config=cfg)
        assert ledger2.cumulative_profit_usd == 500.0

    def test_custom_phase_thresholds(self, tmp_path):
        """Phase thresholds can be customized via config."""
        ledger = ProfitLedger(config={
            'phase2_threshold': 100.0,
            'phase3_threshold': 1000.0,
            'ledger_path': str(tmp_path / 'l.json'),
            'snapshot_path': str(tmp_path / 's.json'),
        })
        assert ledger.PHASE2_THRESHOLD == 100.0
        assert ledger.PHASE3_THRESHOLD == 1000.0

    @pytest.mark.asyncio
    async def test_cumulative_gas_tracking(self, ledger):
        """Cumulative gas costs tracked."""
        await ledger.record(self._make_entry(gas=15))
        await ledger.record(self._make_entry(gas=20))
        assert ledger.cumulative_gas_usd == 35.0

    @pytest.mark.asyncio
    async def test_cumulative_fee_tracking(self, ledger):
        """Cumulative flash loan fees tracked."""
        await ledger.record(self._make_entry(fee=5))
        await ledger.record(self._make_entry(fee=10))
        assert ledger.cumulative_fees_usd == 15.0


# ═══════════════════════════════════════════════════════════════════════════════
#  HeatMap
# ═══════════════════════════════════════════════════════════════════════════════


class TestHeatMap:
    """Test pattern tracking and scoring."""

    @pytest.fixture
    def heatmap(self):
        return HeatMap()

    def test_init_empty(self, heatmap):
        """HeatMap starts empty."""
        assert len(heatmap.entries) == 0

    def test_heat_map_entry_key(self):
        """HeatMapEntry key is type:chain:protocol."""
        entry = HeatMapEntry(
            opportunity_type="liquidation", chain_id=1, protocol="aave_v3",
        )
        assert entry.key == "liquidation:1:aave_v3"

    def test_entry_defaults(self):
        """HeatMapEntry has sensible defaults."""
        entry = HeatMapEntry(
            opportunity_type="arb", chain_id=42161, protocol="uniswap",
        )
        assert entry.total_seen == 0
        assert entry.total_executed == 0
        assert entry.heat_score == 0.0
        assert entry.win_rate == 0.0


# ═══════════════════════════════════════════════════════════════════════════════
#  CapitalMultiplier
# ═══════════════════════════════════════════════════════════════════════════════


class TestCapitalMultiplier:
    """Test capital multiplier activation and compounding."""

    @pytest.fixture
    def multiplier(self, tmp_path):
        from profit_engine.capital_multiplier import CapitalMultiplier, MultiplierState
        heatmap = HeatMap()
        ledger = ProfitLedger(config={
            'ledger_path': str(tmp_path / 'l.json'),
            'snapshot_path': str(tmp_path / 's.json'),
        })
        return CapitalMultiplier(
            heat_map=heatmap,
            ledger=ledger,
        )

    def test_multiplier_starts_inactive(self, multiplier):
        from profit_engine.capital_multiplier import MultiplierState
        assert multiplier.state == MultiplierState.INACTIVE

    def test_default_reinvest_rate(self, multiplier):
        """Default reinvestment rate is 60%."""
        assert multiplier.reinvest_rate == 0.60

    def test_default_max_single_trade(self, multiplier):
        """Max single trade position has a limit."""
        assert multiplier.MAX_SINGLE_POSITION_PCT > 0
        assert multiplier.MAX_SINGLE_POSITION_PCT <= 1.0
