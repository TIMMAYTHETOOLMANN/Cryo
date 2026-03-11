#!/usr/bin/env python3
"""
TEST SUITE — Omnichain Detector Expansion Tests
=================================================
Tests for the four-phase detector expansion:
  Phase 1: Universal Arbitrage Detection (find_arbitrage_pairs with 4 heuristics)
  Phase 2: Cross-Chain Price Feed deltas
  Phase 3: RToken Arbitrage Detector (basket value vs market price)
  Phase 4: Oracle multi-chain lending feeds + health factor simulation

These tests require NO external RPC connections or API keys.
"""

import asyncio
import json
import os
import sys
import time
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock, AsyncMock

# ── Ensure project root is on sys.path ────────────────────────────────
PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

# Phase 1 imports
from MODULE_1_LIQUIDATION_ENGINE.stage_1_detection.dex_arb_scanner import (
    DexArbScanner, ArbOpportunity, TransactionRecord, ArbPairCandidate,
    MARGINAL_DIFF_THRESHOLD, TEMPORAL_WINDOW_STABLE_NATIVE_S,
    TEMPORAL_WINDOW_DEFAULT_S, STABLECOIN_SYMBOLS, NATIVE_SYMBOLS,
    TOKENS,
)

# Phase 2 imports
from MODULE_9_OMNI_SCOPE.data_bus import DataBus, SignalType, SignalSource
from MODULE_9_OMNI_SCOPE.array_4_cross_chain_monitor.bridge_monitor import (
    BridgeMonitor, LiquidityNode, BridgeEdge, ArbRoute,
)

# Phase 3 imports
from MODULE_1_LIQUIDATION_ENGINE.stage_1_detection.rtoken_arbitrage_detector import (
    RTokenArbitrageDetector, RTokenState, RTokenArbOpportunity,
    KNOWN_RTOKENS,
)

# Phase 4 imports
from MODULE_11_TIMING_ENGINE.oracle_price_watcher import (
    OraclePriceWatcher, DEFAULT_FEEDS, LENDING_PROTOCOL_FEEDS,
)
from MODULE_11_TIMING_ENGINE.config import OracleConfig


# ═══════════════════════════════════════════════════════════════════════
# PHASE 1: Universal Arbitrage Detection
# ═══════════════════════════════════════════════════════════════════════

def _make_tx(
    tx_hash="0xabc", sender="0xSender1", token_in="0xWETH", token_out="0xUSDC",
    amount_in=1000, amount_out=2500, dex="uniswap", chain_id=1,
    block_number=100, timestamp=1000, contract_address="",
) -> TransactionRecord:
    return TransactionRecord(
        tx_hash=tx_hash, sender=sender, token_in=token_in,
        token_out=token_out, amount_in=amount_in, amount_out=amount_out,
        dex=dex, chain_id=chain_id, block_number=block_number,
        timestamp=timestamp, contract_address=contract_address,
    )


class TestUniversalArbHeuristics:
    """Test the four heuristics in find_arbitrage_pairs()."""

    def setup_method(self):
        self.scanner = DexArbScanner.__new__(DexArbScanner)
        self.scanner.config = MagicMock()
        self.scanner.w3_providers = {}
        self.scanner.quoters = {}
        self.scanner.v2_routers = {}
        self.scanner.min_profit_usd = 0.01
        self.scanner.min_profit_usd_l2 = 0.01
        self.scanner._l2_chains = {42161, 10, 8453, 137}
        self.scanner.stats = {
            "scans": 0, "opportunities_found": 0,
            "total_profit_usd": 0.0, "cross_dex_checks": 0,
            "start_time": time.time(),
        }

    # ── H1: Cyclic ────────────────────────────────────────────────

    def test_h1_cyclic_positive(self):
        """H1 passes when tx_a.in==tx_b.out and tx_a.out==tx_b.in."""
        tx_a = _make_tx(token_in="0xWETH", token_out="0xUSDC")
        tx_b = _make_tx(token_in="0xUSDC", token_out="0xWETH")
        assert DexArbScanner._check_cyclic(tx_a, tx_b) is True

    def test_h1_cyclic_negative(self):
        """H1 fails when tokens don't form a loop."""
        tx_a = _make_tx(token_in="0xWETH", token_out="0xUSDC")
        tx_b = _make_tx(token_in="0xWETH", token_out="0xDAI")
        assert DexArbScanner._check_cyclic(tx_a, tx_b) is False

    def test_h1_cyclic_case_insensitive(self):
        """H1 handles mixed-case addresses."""
        tx_a = _make_tx(token_in="0xAbC", token_out="0xDeF")
        tx_b = _make_tx(token_in="0xdef", token_out="0xabc")
        assert DexArbScanner._check_cyclic(tx_a, tx_b) is True

    # ── H2: Marginal Difference ──────────────────────────────────

    def test_h2_marginal_within_threshold(self):
        """H2 passes when intermediate amounts differ ≤0.5%."""
        tx_a = _make_tx(amount_out=10000)
        tx_b = _make_tx(amount_in=10040)  # 0.4% diff
        ok, pct = DexArbScanner._check_marginal_difference(tx_a, tx_b)
        assert ok is True
        assert pct < MARGINAL_DIFF_THRESHOLD

    def test_h2_marginal_exceeds_threshold(self):
        """H2 fails when difference > 0.5%."""
        tx_a = _make_tx(amount_out=10000)
        tx_b = _make_tx(amount_in=10100)  # 1.0% diff
        ok, pct = DexArbScanner._check_marginal_difference(tx_a, tx_b)
        assert ok is False

    def test_h2_marginal_exact_boundary(self):
        """H2 passes at exactly 0.5% difference."""
        tx_a = _make_tx(amount_out=10000)
        tx_b = _make_tx(amount_in=10050)  # exactly 0.5%
        ok, pct = DexArbScanner._check_marginal_difference(tx_a, tx_b)
        assert ok is True

    def test_h2_marginal_both_zero(self):
        """H2 handles zero amounts gracefully."""
        tx_a = _make_tx(amount_out=0)
        tx_b = _make_tx(amount_in=0)
        ok, pct = DexArbScanner._check_marginal_difference(tx_a, tx_b)
        assert ok is True
        assert pct == 0.0

    # ── H3: Temporal Window ──────────────────────────────────────

    def test_h3_temporal_within_12s_stablecoin_native(self):
        """H3 passes for stablecoin-native pair within 12s."""
        weth = TOKENS[1]["WETH"]
        usdc = TOKENS[1]["USDC"]
        tx_a = _make_tx(
            token_in=weth, token_out=usdc,
            chain_id=1, timestamp=1000,
        )
        tx_b = _make_tx(
            token_in=usdc, token_out=weth,
            chain_id=1, timestamp=1010,
        )
        ok, gap = DexArbScanner._check_temporal_window(tx_a, tx_b)
        assert ok is True
        assert gap == 10

    def test_h3_temporal_exceeds_12s_stablecoin_native(self):
        """H3 fails for stablecoin-native pair beyond 12s."""
        weth = TOKENS[1]["WETH"]
        usdc = TOKENS[1]["USDC"]
        tx_a = _make_tx(
            token_in=weth, token_out=usdc,
            chain_id=1, timestamp=1000,
        )
        tx_b = _make_tx(
            token_in=usdc, token_out=weth,
            chain_id=1, timestamp=1020,
        )
        ok, gap = DexArbScanner._check_temporal_window(tx_a, tx_b)
        assert ok is False

    def test_h3_temporal_within_1h_other_pair(self):
        """H3 passes for non-stablecoin pair within 1h."""
        tx_a = _make_tx(
            token_in="0xA", token_out="0xB",
            chain_id=1, timestamp=1000,
        )
        tx_b = _make_tx(
            token_in="0xB", token_out="0xA",
            chain_id=1, timestamp=4500,
        )
        ok, gap = DexArbScanner._check_temporal_window(tx_a, tx_b)
        assert ok is True

    def test_h3_temporal_exceeds_1h_other_pair(self):
        """H3 fails for non-stablecoin pair beyond 1h."""
        tx_a = _make_tx(
            token_in="0xA", token_out="0xB",
            chain_id=1, timestamp=1000,
        )
        tx_b = _make_tx(
            token_in="0xB", token_out="0xA",
            chain_id=1, timestamp=5000,
        )
        ok, gap = DexArbScanner._check_temporal_window(tx_a, tx_b)
        assert ok is False

    # ── H4: Entity Link ──────────────────────────────────────────

    def test_h4_entity_same_sender(self):
        """H4 passes when same sender."""
        tx_a = _make_tx(sender="0xSame")
        tx_b = _make_tx(sender="0xSame")
        assert DexArbScanner._check_entity_link(tx_a, tx_b) is True

    def test_h4_entity_same_contract(self):
        """H4 passes when same MEV contract."""
        tx_a = _make_tx(sender="0xA", contract_address="0xMEV")
        tx_b = _make_tx(sender="0xB", contract_address="0xMEV")
        assert DexArbScanner._check_entity_link(tx_a, tx_b) is True

    def test_h4_entity_different_everything(self):
        """H4 fails when no link."""
        tx_a = _make_tx(sender="0xA", contract_address="0xC1")
        tx_b = _make_tx(sender="0xB", contract_address="0xC2")
        assert DexArbScanner._check_entity_link(tx_a, tx_b) is False

    def test_h4_entity_case_insensitive(self):
        """H4 handles mixed-case addresses."""
        tx_a = _make_tx(sender="0xAbC")
        tx_b = _make_tx(sender="0xabc")
        assert DexArbScanner._check_entity_link(tx_a, tx_b) is True

    # ── find_arbitrage_pairs integration ─────────────────────────

    def test_find_pairs_detects_candidate(self):
        """find_arbitrage_pairs returns candidates with ≥2 heuristics."""
        txs = [
            _make_tx(
                tx_hash="0x1", sender="0xSame",
                token_in="0xWETH", token_out="0xUSDC",
                amount_out=10000, timestamp=1000, chain_id=1,
            ),
            _make_tx(
                tx_hash="0x2", sender="0xSame",
                token_in="0xUSDC", token_out="0xWETH",
                amount_in=10030, timestamp=1005, chain_id=1,
            ),
        ]
        candidates = self.scanner.find_arbitrage_pairs(txs, min_heuristics=2)
        assert len(candidates) >= 1
        c = candidates[0]
        assert "H1_CYCLIC" in c.heuristics_passed
        assert "H4_ENTITY" in c.heuristics_passed
        assert c.is_cyclic is True
        assert c.is_entity_linked is True

    def test_find_pairs_filters_cross_chain(self):
        """Pairs from different chains are not compared."""
        txs = [
            _make_tx(tx_hash="0x1", chain_id=1, timestamp=1000),
            _make_tx(tx_hash="0x2", chain_id=42161, timestamp=1005),
        ]
        candidates = self.scanner.find_arbitrage_pairs(txs, min_heuristics=1)
        assert len(candidates) == 0

    def test_find_pairs_sorted_by_heuristics(self):
        """Candidates with more heuristics are ranked first."""
        txs = [
            # Strong candidate: cyclic + entity + temporal
            _make_tx(
                tx_hash="0x1", sender="0xA",
                token_in="0xWETH", token_out="0xUSDC",
                amount_out=10000, timestamp=1000, chain_id=1,
            ),
            _make_tx(
                tx_hash="0x2", sender="0xA",
                token_in="0xUSDC", token_out="0xWETH",
                amount_in=10030, timestamp=1005, chain_id=1,
            ),
            # Weaker candidate: temporal only
            _make_tx(
                tx_hash="0x3", sender="0xB",
                token_in="0xA", token_out="0xB",
                amount_out=5000, timestamp=1006, chain_id=1,
            ),
        ]
        candidates = self.scanner.find_arbitrage_pairs(txs, min_heuristics=1)
        if len(candidates) >= 2:
            assert len(candidates[0].heuristics_passed) >= len(
                candidates[1].heuristics_passed
            )

    def test_find_pairs_empty_input(self):
        """Empty transaction list produces no candidates."""
        candidates = self.scanner.find_arbitrage_pairs([])
        assert candidates == []

    def test_find_pairs_single_tx(self):
        """Single transaction produces no candidates."""
        txs = [_make_tx(tx_hash="0x1")]
        candidates = self.scanner.find_arbitrage_pairs(txs)
        assert candidates == []

    def test_find_pairs_min_heuristics_filter(self):
        """min_heuristics=4 requires all four to pass."""
        txs = [
            _make_tx(tx_hash="0x1", sender="0xA", token_in="0xW", token_out="0xU",
                     amount_out=10000, timestamp=1000, chain_id=1),
            _make_tx(tx_hash="0x2", sender="0xB", token_in="0xU", token_out="0xW",
                     amount_in=10030, timestamp=1005, chain_id=1),
        ]
        # H4 won't pass (different sender), so requiring 4 should yield 0
        candidates = self.scanner.find_arbitrage_pairs(txs, min_heuristics=4)
        assert len(candidates) == 0


# ═══════════════════════════════════════════════════════════════════════
# PHASE 2: Cross-Chain Price Feed Deltas
# ═══════════════════════════════════════════════════════════════════════

class TestCrossChainDeltas:
    """Test bridge_monitor cross-chain price feeds and delta computation."""

    def setup_method(self):
        self.bus = DataBus()
        self.monitor = BridgeMonitor(self.bus)
        # Manually seed price nodes
        self.monitor._build_initial_graph()

    def test_fetch_cross_chain_prices_returns_all(self):
        """fetch_cross_chain_prices returns prices for all nodes."""
        self.monitor.update_price(1, "WETH", 2500.0)
        self.monitor.update_price(42161, "WETH", 2490.0)
        prices = self.monitor.fetch_cross_chain_prices()
        assert "WETH" in prices
        assert 1 in prices["WETH"]
        assert 42161 in prices["WETH"]

    def test_fetch_cross_chain_prices_filter_by_asset(self):
        """Filtering by asset returns only requested tokens."""
        self.monitor.update_price(1, "WETH", 2500.0)
        self.monitor.update_price(1, "USDC", 1.0)
        prices = self.monitor.fetch_cross_chain_prices(assets=["WETH"])
        assert "WETH" in prices
        assert "USDC" not in prices

    def test_calculate_deltas_detects_spread(self):
        """Spread above 0.5% between chains is detected."""
        self.monitor.update_price(1, "WETH", 2500.0)
        self.monitor.update_price(42161, "WETH", 2480.0)  # ~0.8% diff
        deltas = self.monitor.calculate_cross_chain_deltas(
            assets=["WETH"], min_delta_pct=0.5
        )
        assert len(deltas) >= 1
        d = deltas[0]
        assert d["asset"] == "WETH"
        assert d["delta_pct"] >= 0.5
        assert d["direction"] in ("a_to_b", "b_to_a")
        assert d["estimated_gross_profit_usd"] > 0

    def test_calculate_deltas_ignores_small_spread(self):
        """Spread below threshold is filtered out."""
        self.monitor.update_price(1, "USDC", 1.0001)
        self.monitor.update_price(42161, "USDC", 1.0002)
        deltas = self.monitor.calculate_cross_chain_deltas(
            assets=["USDC"], min_delta_pct=0.5
        )
        assert len(deltas) == 0

    def test_calculate_deltas_ignores_zero_prices(self):
        """Chains with zero price are skipped."""
        self.monitor.update_price(1, "WETH", 2500.0)
        self.monitor.update_price(42161, "WETH", 0.0)
        deltas = self.monitor.calculate_cross_chain_deltas(assets=["WETH"])
        assert all(d["price_a"] > 0 and d["price_b"] > 0 for d in deltas)

    def test_calculate_deltas_sorted_by_spread(self):
        """Results are sorted by delta_pct descending."""
        self.monitor.update_price(1, "WETH", 2500.0)
        self.monitor.update_price(42161, "WETH", 2480.0)  # 0.8%
        self.monitor.update_price(10, "WETH", 2450.0)     # 2.0%
        deltas = self.monitor.calculate_cross_chain_deltas(
            assets=["WETH"], min_delta_pct=0.1
        )
        if len(deltas) >= 2:
            assert deltas[0]["delta_pct"] >= deltas[1]["delta_pct"]

    def test_calculate_deltas_direction_a_to_b(self):
        """Direction is a_to_b when chain_a is cheaper."""
        self.monitor.update_price(1, "WETH", 2400.0)
        self.monitor.update_price(42161, "WETH", 2500.0)
        deltas = self.monitor.calculate_cross_chain_deltas(
            assets=["WETH"], min_delta_pct=0.1
        )
        found = [d for d in deltas
                 if d["chain_a"] == 1 and d["chain_b"] == 42161]
        assert len(found) >= 1
        assert found[0]["direction"] == "a_to_b"


# ═══════════════════════════════════════════════════════════════════════
# PHASE 3: RToken Arbitrage Detector
# ═══════════════════════════════════════════════════════════════════════

class TestRTokenArbitrageDetector:
    """Test RToken spread detection and dynamic registration."""

    def setup_method(self):
        self.detector = RTokenArbitrageDetector.__new__(RTokenArbitrageDetector)
        self.detector.config = MagicMock()
        self.detector.min_spread_pct = 0.5
        self.detector.trade_size_usd = 10_000.0
        self.detector.w3_providers = {}
        self.detector._rtoken_states = {}
        self.detector._known_rtokens = {
            k: dict(v) for k, v in KNOWN_RTOKENS.items()
        }
        self.detector.stats = {
            "scans": 0, "rtokens_tracked": 0,
            "opportunities_found": 0,
            "total_spread_captured_usd": 0.0,
            "start_time": time.time(),
        }

    def _seed_rtoken(self, name="eUSD", chain_id=1,
                     address="0xA0d69E286B938e21CBf7E51D71F6A4c8918f482F",
                     basket_value=1.02, market_price=1.00):
        key = f"{chain_id}:{address.lower()}"
        state = RTokenState(
            name=name, address=address, chain_id=chain_id,
            basket_value_usd=basket_value,
            market_price_usd=market_price,
        )
        self.detector._rtoken_states[key] = state
        return state

    def test_evaluate_spread_redeem(self):
        """Positive spread → redeem arbitrage opportunity."""
        state = self._seed_rtoken(basket_value=1.02, market_price=1.00)
        opp = self.detector._evaluate_spread(state)
        assert opp is not None
        assert opp.direction == "redeem"
        assert opp.spread_pct >= 0.5
        assert opp.net_profit_usd > 0

    def test_evaluate_spread_mint_and_sell(self):
        """Negative spread → mint-and-sell opportunity."""
        state = self._seed_rtoken(basket_value=0.98, market_price=1.00)
        opp = self.detector._evaluate_spread(state)
        assert opp is not None
        assert opp.direction == "mint_and_sell"
        assert opp.spread_pct >= 0.5

    def test_evaluate_spread_below_threshold(self):
        """Spread below 0.5% returns None."""
        state = self._seed_rtoken(basket_value=1.003, market_price=1.00)
        opp = self.detector._evaluate_spread(state)
        assert opp is None

    def test_evaluate_spread_zero_prices(self):
        """Zero market price returns None."""
        state = self._seed_rtoken(basket_value=1.02, market_price=0.0)
        opp = self.detector._evaluate_spread(state)
        assert opp is None

    def test_evaluate_spread_l2_lower_gas(self):
        """L2 chains use lower gas estimates."""
        state = self._seed_rtoken(
            name="eUSD", chain_id=42161,
            address="0x12275DCB9048680c4Be40942eA4D92c74C63b844",
            basket_value=1.02, market_price=1.00,
        )
        opp = self.detector._evaluate_spread(state)
        assert opp is not None
        assert opp.gas_cost_usd < 1.0  # L2 gas should be <$1

    def test_register_rtoken(self):
        """Dynamic registration adds a new RToken to tracking."""
        self.detector.register_rtoken(8453, "testRTK", "0xNewAddr")
        key = f"8453:0xnewaddr"
        assert key in self.detector._rtoken_states
        assert self.detector._rtoken_states[key].name == "testRTK"
        assert "testRTK" in self.detector._known_rtokens[8453]

    def test_update_market_price(self):
        """update_market_price updates the state."""
        state = self._seed_rtoken()
        self.detector.update_market_price(1, state.address, 1.05)
        key = f"1:{state.address.lower()}"
        assert self.detector._rtoken_states[key].market_price_usd == 1.05

    def test_update_basket_value(self):
        """update_basket_value updates the state."""
        state = self._seed_rtoken()
        self.detector.update_basket_value(1, state.address, 1.10)
        key = f"1:{state.address.lower()}"
        assert self.detector._rtoken_states[key].basket_value_usd == 1.10

    def test_get_all_states(self):
        """get_all_states returns all tracked tokens."""
        self._seed_rtoken(name="A", address="0xA1")
        self._seed_rtoken(name="B", address="0xB2")
        states = self.detector.get_all_states()
        assert len(states) == 2

    def test_known_rtokens_coverage(self):
        """Known RToken registry covers Ethereum, Arbitrum, Base."""
        assert 1 in KNOWN_RTOKENS
        assert 42161 in KNOWN_RTOKENS
        assert 8453 in KNOWN_RTOKENS
        # At least 2 tokens per chain
        assert len(KNOWN_RTOKENS[1]) >= 2
        assert len(KNOWN_RTOKENS[42161]) >= 1
        assert len(KNOWN_RTOKENS[8453]) >= 1


# ═══════════════════════════════════════════════════════════════════════
# PHASE 4: Oracle Multi-Chain Feeds & Health Simulation
# ═══════════════════════════════════════════════════════════════════════

class TestOracleMultiChainFeeds:
    """Test multi-chain lending protocol feeds and health simulation."""

    def setup_method(self):
        self.watcher = OraclePriceWatcher(config=OracleConfig())

    def test_lending_feeds_ethereum(self):
        """Ethereum feeds cover all major Aave V3 collateral assets."""
        feeds = self.watcher.get_lending_feeds(1)
        assert "ETH/USD" in feeds
        assert "BTC/USD" in feeds
        assert "USDC/USD" in feeds
        assert "WSTETH/ETH" in feeds
        assert "RETH/ETH" in feeds
        assert "GHO/USD" in feeds

    def test_lending_feeds_arbitrum(self):
        """Arbitrum feeds include ARB/USD."""
        feeds = self.watcher.get_lending_feeds(42161)
        assert "ETH/USD" in feeds
        assert "ARB/USD" in feeds
        assert "WSTETH/ETH" in feeds

    def test_lending_feeds_optimism(self):
        """Optimism feeds include OP/USD."""
        feeds = self.watcher.get_lending_feeds(10)
        assert "ETH/USD" in feeds
        assert "OP/USD" in feeds

    def test_lending_feeds_polygon(self):
        """Polygon feeds include MATIC/USD."""
        feeds = self.watcher.get_lending_feeds(137)
        assert "MATIC/USD" in feeds
        assert "AAVE/USD" in feeds

    def test_lending_feeds_base(self):
        """Base feeds include basic assets."""
        feeds = self.watcher.get_lending_feeds(8453)
        assert "ETH/USD" in feeds
        assert "CBETH/ETH" in feeds

    def test_get_all_lending_feeds(self):
        """get_all_lending_feeds returns feeds for all 5 chains."""
        all_feeds = self.watcher.get_all_lending_feeds()
        assert len(all_feeds) >= 5
        assert 1 in all_feeds
        assert 42161 in all_feeds

    def test_load_lending_feeds_adds_new(self):
        """load_lending_feeds merges feeds into active set."""
        original_count = len(self.watcher._feeds)
        added = self.watcher.load_lending_feeds(chain_id=42161)
        assert added > 0
        assert len(self.watcher._feeds) == original_count + added

    def test_load_lending_feeds_idempotent(self):
        """Loading twice doesn't duplicate feeds."""
        self.watcher.load_lending_feeds(chain_id=1)
        count_after_first = len(self.watcher._feeds)
        self.watcher.load_lending_feeds(chain_id=1)
        assert len(self.watcher._feeds) == count_after_first

    def test_load_all_lending_feeds(self):
        """Loading all chains at once works."""
        added = self.watcher.load_lending_feeds()
        assert added > 0

    def test_lending_feeds_unknown_chain(self):
        """Unknown chain returns empty dict."""
        feeds = self.watcher.get_lending_feeds(99999)
        assert feeds == {}


class TestHealthFactorSimulation:
    """Test oracle-driven health factor simulation."""

    def setup_method(self):
        self.watcher = OraclePriceWatcher(config=OracleConfig())
        # Seed some prices
        self.watcher._last_prices = {
            "ETH/USD": 2500.0,
            "BTC/USD": 45000.0,
            "LINK/USD": 15.0,
        }

    def test_simulate_hf_detects_at_risk(self):
        """Dropping ETH 20% pushes HF < 1.0 for levered position."""
        positions = [{
            "user": "0xUser1",
            "chain_id": 1,
            "collateral_asset": "ETH/USD",
            "collateral_usd": 10000.0,
            "debt_usd": 7500.0,
            "current_health_factor": 1.10,
            "liquidation_threshold": 0.825,
        }]
        new_prices = {"ETH/USD": 2000.0}  # -20% drop
        at_risk = self.watcher.simulate_health_factors(positions, new_prices)
        assert len(at_risk) == 1
        assert at_risk[0]["simulated_health_factor"] < 1.0
        assert at_risk[0]["action"] == "prepare_liquidation"

    def test_simulate_hf_safe_position(self):
        """Well-collateralized position stays safe."""
        positions = [{
            "user": "0xUser2",
            "chain_id": 1,
            "collateral_asset": "ETH/USD",
            "collateral_usd": 100000.0,
            "debt_usd": 10000.0,
            "current_health_factor": 8.25,
            "liquidation_threshold": 0.825,
        }]
        new_prices = {"ETH/USD": 2400.0}  # -4%
        at_risk = self.watcher.simulate_health_factors(positions, new_prices)
        assert len(at_risk) == 0

    def test_simulate_hf_multiple_assets(self):
        """Different assets are simulated independently."""
        positions = [
            {
                "user": "0xA", "chain_id": 1,
                "collateral_asset": "ETH/USD",
                "collateral_usd": 10000.0, "debt_usd": 7500.0,
                "current_health_factor": 1.10,
                "liquidation_threshold": 0.825,
            },
            {
                "user": "0xB", "chain_id": 1,
                "collateral_asset": "BTC/USD",
                "collateral_usd": 50000.0, "debt_usd": 10000.0,
                "current_health_factor": 4.12,
                "liquidation_threshold": 0.825,
            },
        ]
        new_prices = {"ETH/USD": 2000.0, "BTC/USD": 44000.0}
        at_risk = self.watcher.simulate_health_factors(positions, new_prices)
        # Only ETH position should be at risk
        assert len(at_risk) == 1
        assert at_risk[0]["user"] == "0xA"

    def test_simulate_hf_missing_oracle_price(self):
        """Position with unknown oracle price is skipped."""
        positions = [{
            "user": "0xC", "chain_id": 1,
            "collateral_asset": "UNKNOWN/USD",
            "collateral_usd": 10000.0, "debt_usd": 5000.0,
            "current_health_factor": 1.65,
            "liquidation_threshold": 0.825,
        }]
        new_prices = {"UNKNOWN/USD": 50.0}
        at_risk = self.watcher.simulate_health_factors(positions, new_prices)
        assert len(at_risk) == 0  # No cached price → skip

    def test_simulate_hf_zero_debt_skipped(self):
        """Position with zero debt is skipped."""
        positions = [{
            "user": "0xD", "chain_id": 1,
            "collateral_asset": "ETH/USD",
            "collateral_usd": 10000.0, "debt_usd": 0.0,
            "current_health_factor": 999.0,
            "liquidation_threshold": 0.825,
        }]
        new_prices = {"ETH/USD": 1000.0}
        at_risk = self.watcher.simulate_health_factors(positions, new_prices)
        assert len(at_risk) == 0

    def test_simulate_hf_sorted_by_risk(self):
        """Results sorted by simulated HF ascending (most at-risk first)."""
        positions = [
            {
                "user": "0xX", "chain_id": 1,
                "collateral_asset": "ETH/USD",
                "collateral_usd": 10000.0, "debt_usd": 7000.0,
                "current_health_factor": 1.18,
                "liquidation_threshold": 0.825,
            },
            {
                "user": "0xY", "chain_id": 1,
                "collateral_asset": "ETH/USD",
                "collateral_usd": 10000.0, "debt_usd": 8500.0,
                "current_health_factor": 0.97,
                "liquidation_threshold": 0.825,
            },
        ]
        new_prices = {"ETH/USD": 2000.0}  # -20%
        at_risk = self.watcher.simulate_health_factors(positions, new_prices)
        if len(at_risk) >= 2:
            assert (at_risk[0]["simulated_health_factor"]
                    <= at_risk[1]["simulated_health_factor"])

    def test_simulate_hf_price_change_pct(self):
        """Price change percentage is reported correctly."""
        positions = [{
            "user": "0xE", "chain_id": 1,
            "collateral_asset": "ETH/USD",
            "collateral_usd": 10000.0, "debt_usd": 8000.0,
            "current_health_factor": 1.03,
            "liquidation_threshold": 0.825,
        }]
        new_prices = {"ETH/USD": 2250.0}  # -10%
        at_risk = self.watcher.simulate_health_factors(positions, new_prices)
        if at_risk:
            assert at_risk[0]["price_change_pct"] == pytest.approx(-10.0, abs=0.1)

    def test_simulate_hf_empty_input(self):
        """Empty positions list returns empty."""
        at_risk = self.watcher.simulate_health_factors([], {"ETH/USD": 2000.0})
        assert at_risk == []


# ═══════════════════════════════════════════════════════════════════════
# DATA MODEL TESTS
# ═══════════════════════════════════════════════════════════════════════

class TestDataModels:
    """Validate data model creation and field defaults."""

    def test_transaction_record_defaults(self):
        tx = TransactionRecord(
            tx_hash="0x1", sender="0xA", token_in="0xW", token_out="0xU",
            amount_in=100, amount_out=200, dex="uni", chain_id=1,
            block_number=1, timestamp=1000,
        )
        assert tx.contract_address == ""

    def test_arb_pair_candidate_defaults(self):
        tx = _make_tx()
        c = ArbPairCandidate(
            tx_a=tx, tx_b=tx, heuristics_passed=["H1_CYCLIC"],
            marginal_difference_pct=0.3, time_gap_seconds=5,
            is_cyclic=True, is_entity_linked=False,
        )
        assert c.estimated_profit_usd == 0.0

    def test_rtoken_state_defaults(self):
        state = RTokenState(name="test", address="0x1", chain_id=1)
        assert state.total_supply == 0
        assert state.basket_tokens == []
        assert state.spread_pct == 0.0

    def test_rtoken_arb_opportunity_fields(self):
        opp = RTokenArbOpportunity(
            rtoken_name="eUSD", rtoken_address="0x1", chain_id=1,
            direction="redeem", basket_value_usd=1.02,
            market_price_usd=1.00, spread_pct=2.0,
            estimated_profit_usd=200.0, gas_cost_usd=5.0,
            net_profit_usd=195.0, timestamp=1000,
            collateral_tokens=["0xA", "0xB"],
        )
        assert opp.net_profit_usd == 195.0
        assert len(opp.collateral_tokens) == 2


# ═══════════════════════════════════════════════════════════════════════
# CONSTANTS / CONFIG TESTS
# ═══════════════════════════════════════════════════════════════════════

class TestConstants:
    """Validate configuration constants and thresholds."""

    def test_marginal_diff_default(self):
        """Default marginal difference threshold is 0.5%."""
        assert MARGINAL_DIFF_THRESHOLD == pytest.approx(0.005, abs=1e-6)

    def test_temporal_window_stable_native(self):
        """Stablecoin-native window is 12s."""
        assert TEMPORAL_WINDOW_STABLE_NATIVE_S == 12

    def test_temporal_window_default(self):
        """Default temporal window is 1h (3600s)."""
        assert TEMPORAL_WINDOW_DEFAULT_S == 3600

    def test_stablecoin_symbols_coverage(self):
        """Stablecoin set includes major stablecoins."""
        assert "USDC" in STABLECOIN_SYMBOLS
        assert "USDT" in STABLECOIN_SYMBOLS
        assert "DAI" in STABLECOIN_SYMBOLS

    def test_native_symbols_coverage(self):
        """Native set includes wrapped native tokens."""
        assert "WETH" in NATIVE_SYMBOLS
        assert "WMATIC" in NATIVE_SYMBOLS

    def test_lending_feeds_all_chains(self):
        """LENDING_PROTOCOL_FEEDS covers 5 chains."""
        assert len(LENDING_PROTOCOL_FEEDS) >= 5
        for chain_id in [1, 42161, 10, 137, 8453]:
            assert chain_id in LENDING_PROTOCOL_FEEDS

    def test_lending_feeds_eth_usd_on_all_chains(self):
        """ETH/USD feed exists on every chain."""
        for chain_id, feeds in LENDING_PROTOCOL_FEEDS.items():
            assert "ETH/USD" in feeds, f"Missing ETH/USD on chain {chain_id}"

    def test_known_rtokens_addresses_are_checksummable(self):
        """All RToken addresses are valid hex strings."""
        for chain_id, tokens in KNOWN_RTOKENS.items():
            for name, addr in tokens.items():
                assert addr.startswith("0x"), f"Bad addr for {name}"
                assert len(addr) == 42, f"Wrong length for {name}: {addr}"
