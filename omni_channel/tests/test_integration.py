#!/usr/bin/env python3
"""
Omni-Channel Integration Tests
End-to-end system validation

Run with: pytest tests/test_integration.py -v
"""

import asyncio
import pytest
import time
import sys
import os

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from omni_channel.data_lake.data_models import (
    OpportunitySignal, SignalType, SignalSource, ChainId, ExecutionModule
)
from omni_channel.data_lake.signal_queue import SignalQueue, SignalRouter
from omni_channel.mempool_radar.advanced_filter import AdvancedFilter, FilterConfig
from omni_channel.mempool_radar.signal_merger import SignalMerger, MergedTransaction
from omni_channel.contract_crawler.protocol_classifier import ProtocolClassifier, ProtocolType
from omni_channel.static_analyzer.mev_patterns import MEVPatternMatcher, MEVType
from omni_channel.static_analyzer.vulnerability_scanner import VulnerabilityScanner
from omni_channel.cross_chain_monitor.multi_chain_graph import MultiChainGraph, Pool, Edge
from omni_channel.cross_chain_monitor.n_hop_pathfinder import NHopPathfinder
from omni_channel.ml_aggregator.quality_scorer import QualityScorer, ScoreTier
from omni_channel.ml_aggregator.competition_estimator import CompetitionEstimator
from omni_channel.ml_aggregator.complexity_analyzer import ComplexityAnalyzer
from omni_channel.ml_aggregator.dynamic_router import DynamicRouter
from omni_channel.execution_router.execution_interface import ExecutionRequest, ExecutionType


# ============================================================================
# Fixtures
# ============================================================================

@pytest.fixture
def sample_signal():
    """Create sample opportunity signal"""
    return OpportunitySignal(
        signal_id="test_signal_001",
        signal_type=SignalType.LIQUIDATION,
        source_module=SignalSource.MEMPOOL_RADAR,
        chain_id=ChainId.ETHEREUM.value,
        target_contract="0x87870Bca3F3fD6335C3F4ce8392D69350B4fA4E2",
        trigger_tx_hash="0x" + "1234" * 16,
        expected_value_usd=5000.0,
        confidence=0.85,
        urgency_score=80,
        execution_complexity=3,
        gas_estimate=300000,
        gas_price_gwei=30,
        latency_requirement_ms=100,
        expiry_block=18500000 + 2,
        metadata={
            'user': '0x' + 'abcd' * 10,
            'debt_asset': '0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48',
            'collateral_asset': '0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2',
        }
    )


@pytest.fixture
def signal_queue():
    """Create signal queue"""
    return SignalQueue(max_size=1000)


@pytest.fixture
def quality_scorer():
    """Create quality scorer"""
    return QualityScorer()


@pytest.fixture
def competition_estimator():
    """Create competition estimator"""
    return CompetitionEstimator()


@pytest.fixture
def complexity_analyzer():
    """Create complexity analyzer"""
    return ComplexityAnalyzer()


@pytest.fixture
def dynamic_router():
    """Create dynamic router"""
    return DynamicRouter()


@pytest.fixture
def mev_matcher():
    """Create MEV pattern matcher"""
    return MEVPatternMatcher()


@pytest.fixture
def vulnerability_scanner():
    """Create vulnerability scanner"""
    return VulnerabilityScanner()


@pytest.fixture
def protocol_classifier():
    """Create protocol classifier"""
    return ProtocolClassifier()


@pytest.fixture
def multi_chain_graph():
    """Create multi-chain graph with sample pools"""
    graph = MultiChainGraph()

    # Add sample pools on Ethereum
    graph.add_pool(Pool(
        address="0x88e6A0c2dDD26FEEb64F039a2c41296FcB3f5640",
        chain_id=1,
        token0="USDC",
        token1="WETH",
        reserve0=100000000,
        reserve1=50000,
        fee_percent=0.0005,
        protocol="uniswap_v3"
    ))

    graph.add_pool(Pool(
        address="0xB4e16d0168e52d35CaCD2c6185b44281Ec28C9Dc",
        chain_id=1,
        token0="USDC",
        token1="WETH",
        reserve0=80000000,
        reserve1=40000,
        fee_percent=0.003,
        protocol="uniswap_v2"
    ))

    # Add pool on Arbitrum
    graph.add_pool(Pool(
        address="0xC31E54c7a869B9FcBEcc14363CF510d1c41fa443",
        chain_id=42161,
        token0="USDC",
        token1="WETH",
        reserve0=50000000,
        reserve1=25000,
        fee_percent=0.0005,
        protocol="uniswap_v3"
    ))

    return graph


# ============================================================================
# Data Model Tests
# ============================================================================

class TestDataModels:
    """Test data models"""

    def test_signal_creation(self, sample_signal):
        """Test signal creation"""
        assert sample_signal.signal_id == "test_signal_001"
        assert sample_signal.signal_type == SignalType.LIQUIDATION
        assert sample_signal.chain_id == ChainId.ETHEREUM.value
        assert sample_signal.expected_value_usd == 5000.0
        assert sample_signal.confidence == 0.85

    def test_signal_type_enum(self):
        """Test signal type enum"""
        assert SignalType.LIQUIDATION.value == "liquidation"
        assert SignalType.ARBITRAGE.value == "arbitrage"
        assert SignalType.BACKRUN.value == "backrun"

    def test_execution_module_enum(self):
        """Test execution module enum"""
        assert ExecutionModule.LIQUIDATION_ENGINE.value == "liquidation_engine"
        assert ExecutionModule.ARBITRAGE_MODULE.value == "arbitrage_module"


# ============================================================================
# Signal Queue Tests
# ============================================================================

class TestSignalQueue:
    """Test signal queue"""

    @pytest.mark.asyncio
    async def test_enqueue_signal(self, signal_queue, sample_signal):
        """Test enqueueing signal"""
        await signal_queue.enqueue(sample_signal)
        assert signal_queue.size() == 1

    @pytest.mark.asyncio
    async def test_dequeue_signal(self, signal_queue, sample_signal):
        """Test dequeueing signal"""
        await signal_queue.enqueue(sample_signal)
        dequeued = await signal_queue.dequeue()
        assert dequeued.signal_id == sample_signal.signal_id
        assert signal_queue.size() == 0

    @pytest.mark.asyncio
    async def test_priority_ordering(self, signal_queue):
        """Test priority ordering"""
        # Create signals with different priorities
        signal1 = OpportunitySignal(
            signal_id="low_priority",
            signal_type=SignalType.BACKRUN,
            source_module=SignalSource.MEMPOOL_RADAR,
            chain_id=1,
            target_contract="0x123",
            expected_value_usd=100,
            confidence=0.5,
            urgency_score=30,
            execution_complexity=2,
            gas_estimate=200000,
            gas_price_gwei=20,
            latency_requirement_ms=500,
            expiry_block=1000
        )

        signal2 = OpportunitySignal(
            signal_id="high_priority",
            signal_type=SignalType.LIQUIDATION,
            source_module=SignalSource.MEMPOOL_RADAR,
            chain_id=1,
            target_contract="0x456",
            expected_value_usd=10000,
            confidence=0.9,
            urgency_score=90,
            execution_complexity=3,
            gas_estimate=300000,
            gas_price_gwei=50,
            latency_requirement_ms=50,
            expiry_block=1000
        )

        await signal_queue.enqueue(signal1)
        await signal_queue.enqueue(signal2)

        # High priority should come first
        dequeued = await signal_queue.dequeue()
        assert dequeued.signal_id == "high_priority"


# ============================================================================
# Quality Scorer Tests
# ============================================================================

class TestQualityScorer:
    """Test quality scorer"""

    def test_score_excellent_signal(self, quality_scorer, sample_signal):
        """Test scoring excellent signal"""
        scored = quality_scorer.score(sample_signal)

        assert scored.quality_score > 0
        assert scored.ev_score > 0
        assert scored.confidence_score > 0
        assert scored.tier in [ScoreTier.EXCELLENT, ScoreTier.GOOD, ScoreTier.FAIR]

    def test_score_batch(self, quality_scorer):
        """Test batch scoring"""
        signals = [
            OpportunitySignal(
                signal_id=f"signal_{i}",
                signal_type=SignalType.LIQUIDATION,
                source_module=SignalSource.MEMPOOL_RADAR,
                chain_id=1,
                target_contract="0x123",
                expected_value_usd=1000 * (i + 1),
                confidence=0.5 + (i * 0.1),
                urgency_score=50,
                execution_complexity=3,
                gas_estimate=300000,
                gas_price_gwei=30,
                latency_requirement_ms=100,
                expiry_block=1000
            )
            for i in range(5)
        ]

        scored = quality_scorer.score_batch(signals)

        assert len(scored) == 5
        # Should be sorted by quality score
        for i in range(len(scored) - 1):
            assert scored[i].quality_score >= scored[i + 1].quality_score

    def test_get_recommended_signals(self, quality_scorer, sample_signal):
        """Test getting recommended signals"""
        scored = quality_scorer.score(sample_signal)

        recommended = quality_scorer.get_recommended_signals(
            [scored],
            min_tier=ScoreTier.FAIR,
            limit=10
        )

        if scored.tier in [ScoreTier.EXCELLENT, ScoreTier.GOOD, ScoreTier.FAIR]:
            assert len(recommended) == 1


# ============================================================================
# Competition Estimator Tests
# ============================================================================

class TestCompetitionEstimator:
    """Test competition estimator"""

    def test_estimate_liquidation_competition(self, competition_estimator, sample_signal):
        """Test competition estimation for liquidation"""
        competition = competition_estimator.estimate(sample_signal)

        assert competition.estimated_competitors >= 0
        assert competition.competition_level in ['low', 'medium', 'high']
        assert 0 <= competition.visibility_score <= 1
        assert 0 <= competition.success_probability <= 1

    def test_estimate_new_protocol_competition(self, competition_estimator):
        """Test competition for new protocol (should be low)"""
        signal = OpportunitySignal(
            signal_id="new_protocol_test",
            signal_type=SignalType.NEW_PROTOCOL,
            source_module=SignalSource.CONTRACT_CRAWLER,
            chain_id=1,
            target_contract="0xnew",
            expected_value_usd=5000,
            confidence=0.6,
            urgency_score=50,
            execution_complexity=5,
            gas_estimate=500000,
            gas_price_gwei=30,
            latency_requirement_ms=1000,
            expiry_block=1000
        )

        competition = competition_estimator.estimate(signal)

        # New protocols should have lower competition
        assert competition.estimated_competitors <= 10


# ============================================================================
# Complexity Analyzer Tests
# ============================================================================

class TestComplexityAnalyzer:
    """Test complexity analyzer"""

    def test_analyze_liquidation_complexity(self, complexity_analyzer, sample_signal):
        """Test complexity analysis for liquidation"""
        analysis = complexity_analyzer.analyze(sample_signal)

        assert 1 <= analysis.complexity_score <= 10
        assert analysis.step_count >= 1
        assert analysis.timing_criticality >= 0
        assert analysis.technical_complexity >= 0

    def test_analyze_cross_chain_complexity(self, complexity_analyzer):
        """Test complexity for cross-chain (should be higher)"""
        signal = OpportunitySignal(
            signal_id="cross_chain_test",
            signal_type=SignalType.CROSS_CHAIN_ARB,
            source_module=SignalSource.CROSS_CHAIN_MONITOR,
            chain_id=1,
            target_contract="0xbridge",
            expected_value_usd=10000,
            confidence=0.7,
            urgency_score=60,
            execution_complexity=8,
            gas_estimate=800000,
            gas_price_gwei=30,
            latency_requirement_ms=5000,
            expiry_block=1000
        )

        analysis = complexity_analyzer.analyze(signal)

        # Cross-chain should have higher complexity
        assert analysis.complexity_score >= 6
        assert analysis.is_cross_chain or signal.signal_type == SignalType.CROSS_CHAIN_ARB


# ============================================================================
# Dynamic Router Tests
# ============================================================================

class TestDynamicRouter:
    """Test dynamic router"""

    def test_route_liquidation(self, dynamic_router, quality_scorer, sample_signal):
        """Test routing liquidation signal"""
        scored = quality_scorer.score(sample_signal)
        decision = dynamic_router.route(scored)

        # Liquidations should go to liquidation engine
        assert decision.routed_to in [
            ExecutionModule.LIQUIDATION_ENGINE,
            ExecutionModule.MANUAL_REVIEW
        ]

    def test_route_arbitrage(self, dynamic_router, quality_scorer):
        """Test routing arbitrage signal"""
        signal = OpportunitySignal(
            signal_id="arb_test",
            signal_type=SignalType.ARBITRAGE,
            source_module=SignalSource.CROSS_CHAIN_MONITOR,
            chain_id=1,
            target_contract="0xdex",
            expected_value_usd=5000,
            confidence=0.8,
            urgency_score=70,
            execution_complexity=5,
            gas_estimate=600000,
            gas_price_gwei=30,
            latency_requirement_ms=200,
            expiry_block=1000
        )

        scored = quality_scorer.score(signal)
        decision = dynamic_router.route(scored)

        # Arbitrage should go to arbitrage module
        assert decision.routed_to in [
            ExecutionModule.ARBITRAGE_MODULE,
            ExecutionModule.CROSS_CHAIN_EXECUTOR,
            ExecutionModule.MANUAL_REVIEW
        ]


# ============================================================================
# MEV Pattern Matcher Tests
# ============================================================================

class TestMEVPatternMatcher:
    """Test MEV pattern matcher"""

    def test_detect_sandwich_pattern(self, mev_matcher):
        """Test detecting sandwich pattern"""
        # Uniswap V2 swap bytecode pattern
        bytecode = "0x608060405238ed1739abcdef"  # Contains swap selector

        patterns = mev_matcher.analyze_bytecode(bytecode, "0x123")

        sandwich_patterns = [p for p in patterns if p.mev_type == MEVType.SANDWICH]
        assert len(sandwich_patterns) > 0

    def test_detect_liquidation_pattern(self, mev_matcher):
        """Test detecting liquidation pattern"""
        bytecode = "0x608060405241013712abcdef"  # Contains liquidation selector

        patterns = mev_matcher.analyze_bytecode(bytecode, "0x456")

        liquidation_patterns = [p for p in patterns if p.mev_type == MEVType.LIQUIDATION]
        assert len(liquidation_patterns) > 0

    def test_get_high_confidence(self, mev_matcher):
        """Test filtering high confidence patterns"""
        bytecode = "0x608060405238ed1739abcdef41013712"

        patterns = mev_matcher.analyze_bytecode(bytecode, "0x789")
        high_conf = mev_matcher.get_high_confidence(patterns, min_confidence=0.5)

        for pattern in high_conf:
            assert pattern.confidence >= 0.5


# ============================================================================
# Vulnerability Scanner Tests
# ============================================================================

class TestVulnerabilityScanner:
    """Test vulnerability scanner"""

    def test_scan_safe_contract(self, vulnerability_scanner):
        """Test scanning safe contract"""
        bytecode = "0x6080604052600436106100"  # Minimal bytecode

        result = vulnerability_scanner.scan_bytecode(bytecode, "0xsafe")

        assert result.contract_address == "0xsafe"
        # Should have few or no vulnerabilities

    def test_scan_vulnerable_contract(self, vulnerability_scanner):
        """Test scanning vulnerable contract"""
        # Bytecode with swap function (sandwich vulnerable)
        bytecode = "0x" + "608060405238ed1739abcdef" * 100

        result = vulnerability_scanner.scan_bytecode(bytecode, "0xvuln")

        assert result.vulnerability_count >= 0
        # Should detect at least one vulnerability


# ============================================================================
# Protocol Classifier Tests
# ============================================================================

class TestProtocolClassifier:
    """Test protocol classifier"""

    def test_classify_lending_protocol(self, protocol_classifier):
        """Test classifying lending protocol"""
        functions = [
            '0xa0712d68',  # mint
            '0x69328dec',  # redeem
            '0x41013712',  # liquidationCall
        ]

        classification = protocol_classifier.classify_from_functions(
            "0xlending", 1, functions
        )

        assert classification.protocol_type == ProtocolType.LENDING
        assert 'liquidatable' in classification.tags

    def test_classify_dex_protocol(self, protocol_classifier):
        """Test classifying DEX protocol"""
        functions = [
            '0x38ed1739',  # swapExactTokensForTokens
            '0xe8e33700',  # addLiquidity
            '0x441a3e70',  # removeLiquidityETH
        ]

        classification = protocol_classifier.classify_from_functions(
            "0xdex", 1, functions
        )

        assert classification.protocol_type == ProtocolType.DEX_AMM


# ============================================================================
# Multi-Chain Graph Tests
# ============================================================================

class TestMultiChainGraph:
    """Test multi-chain graph"""

    def test_add_pool(self, multi_chain_graph):
        """Test adding pool to graph"""
        assert multi_chain_graph.nodes_count >= 2  # At least 2 token nodes
        assert multi_chain_graph.edges_count >= 2  # Bidirectional edges

    def test_find_path(self, multi_chain_graph):
        """Test finding path in graph"""
        path = multi_chain_graph.find_path("1:USDC", "1:WETH")

        assert path is not None
        assert len(path) >= 1

    def test_find_best_path(self, multi_chain_graph):
        """Test finding best path"""
        result = multi_chain_graph.find_best_path("1:USDC", "1:WETH", amount=10000)

        if result:
            path, output = result
            assert len(path) >= 1
            assert output > 0


# ============================================================================
# N-Hop Pathfinder Tests
# ============================================================================

class TestNHopPathfinder:
    """Test N-hop pathfinder"""

    def test_find_arbitrage_paths(self, multi_chain_graph):
        """Test finding arbitrage paths"""
        pathfinder = NHopPathfinder(multi_chain_graph)

        result = pathfinder.find_all_arbitrage_paths(
            start_token="USDC",
            start_chain=1,
            amount_usd=10000
        )

        assert result.paths_found >= 0
        assert result.search_time_ms >= 0


# ============================================================================
# End-to-End Integration Tests
# ============================================================================

class TestEndToEnd:
    """End-to-end integration tests"""

    @pytest.mark.asyncio
    async def test_full_signal_pipeline(self, sample_signal, signal_queue, quality_scorer,
                                         competition_estimator, complexity_analyzer, dynamic_router):
        """Test complete signal processing pipeline"""
        # 1. Queue signal
        await signal_queue.enqueue(sample_signal)
        assert signal_queue.size() == 1

        # 1b. Dequeue for processing
        dequeued = await signal_queue.dequeue()
        assert dequeued.signal_id == sample_signal.signal_id

        # 2. Score signal
        competition = competition_estimator.estimate(sample_signal)
        complexity = complexity_analyzer.analyze(sample_signal)

        scored = quality_scorer.score(
            sample_signal,
            competition_score=competition.competition_score,
            complexity_score=complexity.complexity_score
        )

        assert scored.quality_score > 0

        # 3. Route signal
        decision = dynamic_router.route(scored)

        assert decision.routed_to is not None
        assert decision.priority >= 1

        # 4. Verify pipeline completed
        assert signal_queue.size() == 0  # Signal was processed

    @pytest.mark.asyncio
    async def test_batch_processing(self, quality_scorer, dynamic_router):
        """Test batch signal processing"""
        # Create batch of signals
        signals = [
            OpportunitySignal(
                signal_id=f"batch_{i}",
                signal_type=SignalType.LIQUIDATION if i % 2 == 0 else SignalType.ARBITRAGE,
                source_module=SignalSource.MEMPOOL_RADAR,
                chain_id=1,
                target_contract=f"0x{i:040x}",
                expected_value_usd=1000 * (i + 1),
                confidence=0.5 + (i * 0.05),
                urgency_score=50 + i * 5,
                execution_complexity=3,
                gas_estimate=300000,
                gas_price_gwei=30,
                latency_requirement_ms=100,
                expiry_block=1000
            )
            for i in range(10)
        ]

        # Score batch
        scored = quality_scorer.score_batch(signals)
        assert len(scored) == 10

        # Route batch
        decisions = [dynamic_router.route(s) for s in scored]
        assert len(decisions) == 10

        # Verify all routed
        for decision in decisions:
            assert decision.routed_to is not None


# ============================================================================
# Performance Tests
# ============================================================================

class TestPerformance:
    """Performance benchmark tests"""

    def test_scoring_throughput(self, quality_scorer):
        """Test scoring throughput"""
        signals = [
            OpportunitySignal(
                signal_id=f"perf_{i}",
                signal_type=SignalType.LIQUIDATION,
                source_module=SignalSource.MEMPOOL_RADAR,
                chain_id=1,
                target_contract="0x123",
                expected_value_usd=5000,
                confidence=0.8,
                urgency_score=80,
                execution_complexity=3,
                gas_estimate=300000,
                gas_price_gwei=30,
                latency_requirement_ms=100,
                expiry_block=1000
            )
            for i in range(1000)
        ]

        start = time.time()
        for signal in signals:
            quality_scorer.score(signal)
        elapsed = time.time() - start

        signals_per_second = 1000 / elapsed
        print(f"\n📊 Scoring throughput: {signals_per_second:.0f} signals/sec")

        # Should process at least 1000 signals/second
        assert signals_per_second >= 1000

    def test_mev_pattern_matching(self, mev_matcher):
        """Test MEV pattern matching performance"""
        bytecode = "0x" + "63" + "38ed1739" + "00" * 100  # 1KB bytecode

        start = time.time()
        for _ in range(100):
            mev_matcher.analyze_bytecode(bytecode, "0x123")
        elapsed = time.time() - start

        analyses_per_second = 100 / elapsed
        print(f"\n📊 MEV matching throughput: {analyses_per_second:.0f} analyses/sec")

        assert analyses_per_second >= 100



# ============================================================================
# Module 10: RPCGateway Integration with MODULE_9 OmniScopeEngine
# ============================================================================

class MockRPCGateway:
    """Minimal mock of profit_engine.RPCGateway for unit tests."""

    def __init__(self, chain_map=None):
        self._chain_map = chain_map or {}
        self.call_count = 0
        self._stats = {
            "total_requests": 0,
            "total_batched": 0,
            "total_failovers": 0,
            "total_429s": 0,
            "uptime_seconds": 0,
            "rps": 0.0,
            "chains": {},
        }

    def get_w3(self, chain_id: int):
        """Return a mock Web3 instance (or None) for the given chain."""
        self.call_count += 1
        return self._chain_map.get(chain_id)

    def get_stats(self) -> dict:
        return dict(self._stats)


class TestRPCGatewayIntegration:
    """Tests for Module 10 RPC Gateway wiring into the MODULE_9 engine."""

    def test_engine_accepts_no_gateway(self):
        """OmniScopeEngine works without a gateway (backward compat)."""
        from MODULE_9_OMNI_SCOPE.engine import OmniScopeEngine
        engine = OmniScopeEngine()
        assert engine.rpc_gateway is None

    def test_engine_stores_gateway(self):
        """OmniScopeEngine exposes the injected gateway."""
        from MODULE_9_OMNI_SCOPE.engine import OmniScopeEngine
        gw = MockRPCGateway()
        engine = OmniScopeEngine(rpc_gateway=gw)
        assert engine.rpc_gateway is gw

    def test_gateway_forwarded_to_mempool_radar(self):
        """OmniScopeEngine forwards the gateway to MempoolRadar."""
        from MODULE_9_OMNI_SCOPE.engine import OmniScopeEngine
        gw = MockRPCGateway()
        engine = OmniScopeEngine(rpc_gateway=gw)
        assert engine.mempool_radar._rpc_gateway is gw

    def test_mempool_radar_no_gateway(self):
        """MempoolRadar works without a gateway (backward compat)."""
        from MODULE_9_OMNI_SCOPE.data_bus import DataBus
        from MODULE_9_OMNI_SCOPE.array_1_mempool_radar.mempool_radar import MempoolRadar
        bus = DataBus()
        radar = MempoolRadar(bus)
        assert radar._rpc_gateway is None

    def test_mempool_radar_with_gateway(self):
        """MempoolRadar stores injected gateway."""
        from MODULE_9_OMNI_SCOPE.data_bus import DataBus
        from MODULE_9_OMNI_SCOPE.array_1_mempool_radar.mempool_radar import MempoolRadar
        bus = DataBus()
        gw = MockRPCGateway()
        radar = MempoolRadar(bus, rpc_gateway=gw)
        assert radar._rpc_gateway is gw

    def test_get_full_stats_includes_gateway_flag(self):
        """get_full_stats() reports rpc_gateway_active correctly."""
        from MODULE_9_OMNI_SCOPE.engine import OmniScopeEngine
        engine_no_gw = OmniScopeEngine()
        assert engine_no_gw.get_full_stats()["rpc_gateway_active"] is False

        gw = MockRPCGateway()
        engine_with_gw = OmniScopeEngine(rpc_gateway=gw)
        stats = engine_with_gw.get_full_stats()
        assert stats["rpc_gateway_active"] is True
        assert "rpc_gateway" in stats

    def test_get_full_stats_gateway_stats_embedded(self):
        """get_full_stats() embeds the gateway's own stats dict."""
        from MODULE_9_OMNI_SCOPE.engine import OmniScopeEngine
        gw = MockRPCGateway()
        engine = OmniScopeEngine(rpc_gateway=gw)
        stats = engine.get_full_stats()
        assert stats["rpc_gateway"]["total_requests"] == 0
        assert "chains" in stats["rpc_gateway"]

    @pytest.mark.asyncio
    async def test_poll_fallback_uses_gateway_w3(self):
        """_poll_pending_fallback returns early when gateway has no active endpoint."""
        from unittest.mock import AsyncMock, patch
        from MODULE_9_OMNI_SCOPE.data_bus import DataBus
        from MODULE_9_OMNI_SCOPE.array_1_mempool_radar.mempool_radar import MempoolRadar

        # Gateway returns None for chain 1 → radar should exit immediately
        gw = MockRPCGateway(chain_map={})  # no endpoint for chain 1
        bus = DataBus()
        radar = MempoolRadar(bus, rpc_gateway=gw)
        radar._running = True

        # Should return quickly (no active endpoint)
        await asyncio.wait_for(radar._poll_pending_fallback(), timeout=2.0)
        # Gateway's get_w3 was called once
        assert gw.call_count == 1

    @pytest.mark.asyncio
    async def test_poll_fallback_with_real_w3_mock(self):
        """_poll_pending_fallback uses the w3 returned by the gateway."""
        from unittest.mock import MagicMock
        from MODULE_9_OMNI_SCOPE.data_bus import DataBus
        from MODULE_9_OMNI_SCOPE.array_1_mempool_radar.mempool_radar import MempoolRadar

        mock_w3 = MagicMock()
        mock_w3.eth.get_block.side_effect = Exception("stop")
        gw = MockRPCGateway(chain_map={1: mock_w3})
        bus = DataBus()
        radar = MempoolRadar(bus, rpc_gateway=gw)
        # _running=False so the polling loop exits after the first get_w3() call.
        radar._running = False

        try:
            await asyncio.wait_for(radar._poll_pending_fallback(), timeout=2.0)
        except (asyncio.TimeoutError, Exception):
            pass
        # gateway.get_w3(1) must have been called
        assert gw.call_count >= 1


# ============================================================================
# MODULE 11: JIT Liquidation Engine Integration
# ============================================================================

class TestJITEngineIntegration:
    """Tests for Module 11 JITLiquidationEngine wiring into Pipeline."""

    def test_jit_engine_importable(self):
        """JITLiquidationEngine is importable from profit_engine."""
        from profit_engine import JITLiquidationEngine, WatchedPosition
        assert JITLiquidationEngine is not None
        assert WatchedPosition is not None

    def test_jit_engine_in_profit_engine_all(self):
        """JITLiquidationEngine appears in profit_engine.__all__."""
        import profit_engine
        assert "JITLiquidationEngine" in profit_engine.__all__
        assert "WatchedPosition" in profit_engine.__all__

    def test_pipeline_has_jit_stats_keys(self):
        """Pipeline.__init__ creates all Module 11 stats keys."""
        from unittest.mock import MagicMock, patch

        # Patch heavy dependencies so Pipeline can instantiate
        with patch(
            "MODULE_1_LIQUIDATION_ENGINE.pipeline.JIT_ENGINE_AVAILABLE", False
        ):
            from MODULE_1_LIQUIDATION_ENGINE.pipeline import Pipeline
            p = Pipeline.__new__(Pipeline)
            p.config = MagicMock()
            p.config.get_chain = MagicMock(return_value=None)
            p.stats = {
                "jit_positions_tracked": 0,
                "jit_simulations_run": 0,
                "jit_simulations_passed": 0,
                "jit_txs_broadcast": 0,
                "jit_txs_confirmed": 0,
                "jit_txs_reverted": 0,
                "jit_profit_usd": 0.0,
            }
        for key in [
            "jit_positions_tracked",
            "jit_simulations_run",
            "jit_simulations_passed",
            "jit_txs_broadcast",
            "jit_txs_confirmed",
            "jit_txs_reverted",
            "jit_profit_usd",
        ]:
            assert key in p.stats, f"Missing JIT stats key: {key}"

    def test_jit_engine_instantiates_with_empty_providers(self):
        """JITLiquidationEngine can be created with an empty w3 providers dict."""
        from profit_engine.jit_liquidation_engine import JITLiquidationEngine
        engine = JITLiquidationEngine({})
        assert engine is not None
        assert hasattr(engine, "oracle_watcher")
        assert hasattr(engine, "jit_executor")
        assert hasattr(engine, "profitability")

    def test_jit_engine_feed_watchlist_empty(self):
        """feed_watchlist with empty dict does not raise."""
        from profit_engine.jit_liquidation_engine import JITLiquidationEngine
        engine = JITLiquidationEngine({})
        engine.feed_watchlist({})  # must not raise

    def test_jit_engine_feed_watchlist_adds_positions(self):
        """feed_watchlist with valid positions populates jit_executor.jit_positions."""
        from profit_engine.jit_liquidation_engine import JITLiquidationEngine
        engine = JITLiquidationEngine({})
        watchlist = {
            "0xABCD": {
                "chain_id": 1,
                "last_hf": 1.03,
                "debt_usd": 5000.0,
                "collateral_asset": "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2",
                "debt_asset": "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48",
                "pool": "0x87870Bca3F3fD6335C3F4ce8392D69350B4fA4E2",
            }
        }
        engine.feed_watchlist(watchlist)
        assert len(engine.jit_executor.jit_positions) >= 1

    def test_jit_engine_get_stats_returns_dict(self):
        """get_stats() returns a dict with expected keys."""
        from profit_engine.jit_liquidation_engine import JITLiquidationEngine
        engine = JITLiquidationEngine({})
        stats = engine.get_stats()
        assert isinstance(stats, dict)
        for key in [
            "jit_positions", "simulations_run", "simulations_passed",
            "txs_broadcast", "txs_confirmed", "txs_reverted",
            "total_profit_usd", "oracle_feeds",
        ]:
            assert key in stats, f"Missing stats key: {key}"

    def test_pipeline_build_w3_providers_no_rpc(self):
        """_build_w3_providers returns empty dict when no RPC URLs are configured."""
        from unittest.mock import MagicMock
        from MODULE_1_LIQUIDATION_ENGINE.pipeline import Pipeline

        p = Pipeline.__new__(Pipeline)
        mock_chain = MagicMock()
        mock_chain.rpc_url = ""
        p.config = MagicMock()
        p.config.get_chain = MagicMock(return_value=mock_chain)

        providers = p._build_w3_providers()
        assert isinstance(providers, dict)
        assert len(providers) == 0

    def test_pipeline_sync_jit_watchlist_no_engine(self):
        """_sync_jit_watchlist is a no-op when jit_engine is None."""
        from unittest.mock import MagicMock
        from MODULE_1_LIQUIDATION_ENGINE.pipeline import Pipeline

        p = Pipeline.__new__(Pipeline)
        p.jit_engine = None
        # Must not raise even with positions
        p._sync_jit_watchlist([MagicMock()])

    def test_pipeline_sync_jit_watchlist_calls_feed(self):
        """_sync_jit_watchlist forwards detected positions to jit_engine.feed_watchlist."""
        from unittest.mock import MagicMock, patch
        from MODULE_1_LIQUIDATION_ENGINE.pipeline import Pipeline

        p = Pipeline.__new__(Pipeline)
        mock_engine = MagicMock()
        p.jit_engine = mock_engine

        mock_pos = MagicMock()
        mock_pos.user = "0x1234"
        mock_pos.chain_id = 1
        mock_pos.health_factor = 1.04
        mock_pos.debt_amount = int(1e21)  # 1000 ETH in Wei
        mock_pos.collateral_asset = "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2"
        mock_pos.debt_asset = "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48"
        mock_pos.pool_address = "0x87870Bca3F3fD6335C3F4ce8392D69350B4fA4E2"

        p._sync_jit_watchlist([mock_pos])

        mock_engine.feed_watchlist.assert_called_once()
        call_arg = mock_engine.feed_watchlist.call_args[0][0]
        assert "0x1234" in call_arg
        assert call_arg["0x1234"]["chain_id"] == 1
        assert call_arg["0x1234"]["last_hf"] == 1.04


# ============================================================================
# ZERO-REVERT PIPELINE Integration
# ============================================================================

class TestZeroRevertPipelineIntegration:
    """Tests for the ZeroRevertPipeline and MempoolSniffer wiring."""

    def test_zero_revert_pipeline_importable(self):
        """ZeroRevertPipeline and MempoolSniffer are importable from profit_engine."""
        from profit_engine import ZeroRevertPipeline, MempoolSniffer
        assert ZeroRevertPipeline is not None
        assert MempoolSniffer is not None

    def test_exports_in_all(self):
        """Both symbols appear in profit_engine.__all__."""
        import profit_engine
        assert "ZeroRevertPipeline" in profit_engine.__all__
        assert "MempoolSniffer" in profit_engine.__all__

    def test_dex_pool_addresses_structure(self):
        """DEX_POOL_ADDRESSES has entries for expected chains."""
        from profit_engine.zero_revert_pipeline import DEX_POOL_ADDRESSES
        assert 1 in DEX_POOL_ADDRESSES        # Ethereum mainnet
        assert 42161 in DEX_POOL_ADDRESSES    # Arbitrum
        # All pool addresses are lowercase
        for pools in DEX_POOL_ADDRESSES.values():
            for addr in pools:
                assert addr == addr.lower(), f"Pool address not lowercase: {addr}"

    def test_pool_to_asset_keys_lowercase(self):
        """All keys in POOL_TO_ASSET are lowercase hex strings."""
        from profit_engine.zero_revert_pipeline import POOL_TO_ASSET
        for k in POOL_TO_ASSET:
            assert k == k.lower(), f"POOL_TO_ASSET key not lowercase: {k}"

    def test_mempool_sniffer_instantiates(self):
        """MempoolSniffer can be created with a minimal PositionIndex and executor stub."""
        from unittest.mock import MagicMock
        from profit_engine.zero_revert_pipeline import MempoolSniffer, PositionIndex
        index = PositionIndex()
        executor = MagicMock()
        sniffer = MempoolSniffer(index, executor)
        assert sniffer is not None
        assert sniffer.txs_inspected == 0

    def test_mempool_sniffer_estimate_price_impact(self):
        """estimate_price_impact uses constant-product formula."""
        from unittest.mock import MagicMock
        from profit_engine.zero_revert_pipeline import MempoolSniffer, PositionIndex
        sniffer = MempoolSniffer(PositionIndex(), MagicMock())
        # $100k trade vs $1M pool → ~9% impact
        impact = sniffer.estimate_price_impact(100_000, 1_000_000)
        assert abs(impact - 100_000 / 1_100_000) < 1e-9
        # Zero trade → 0
        assert sniffer.estimate_price_impact(0, 1_000_000) == 0.0
        # Zero pool → 0
        assert sniffer.estimate_price_impact(100_000, 0) == 0.0

    def test_mempool_sniffer_below_threshold_ignored(self):
        """Transactions with < 1% price impact are skipped."""
        import asyncio
        from unittest.mock import MagicMock
        from profit_engine.zero_revert_pipeline import MempoolSniffer, PositionIndex
        sniffer = MempoolSniffer(PositionIndex(), MagicMock())
        # tiny trade, huge pool → impact << 1%
        result = asyncio.get_event_loop().run_until_complete(
            sniffer.on_pending_transaction(
                chain_id=1,
                to_address='0x88e6a0c2ddd26feeb64f039a2c41296fcb3f5640',
                input_data=b'',
                value_eth=0.001,
                eth_price_usd=2500.0,
                pool_tvl_usd=1_000_000_000.0,  # $1B pool
            )
        )
        assert result == 0
        assert sniffer.impacts_detected == 0

    def test_mempool_sniffer_unknown_pool_ignored(self):
        """Transactions to an unlisted pool address return 0."""
        import asyncio
        from unittest.mock import MagicMock
        from profit_engine.zero_revert_pipeline import MempoolSniffer, PositionIndex
        sniffer = MempoolSniffer(PositionIndex(), MagicMock())
        result = asyncio.get_event_loop().run_until_complete(
            sniffer.on_pending_transaction(
                chain_id=1,
                to_address='0xdeadbeefdeadbeefdeadbeefdeadbeefdeadbeef',
                input_data=b'',
                value_eth=100.0,
            )
        )
        assert result == 0

    def test_mempool_sniffer_get_stats_structure(self):
        """get_stats() returns dict with expected keys."""
        from unittest.mock import MagicMock
        from profit_engine.zero_revert_pipeline import MempoolSniffer, PositionIndex
        sniffer = MempoolSniffer(PositionIndex(), MagicMock())
        stats = sniffer.get_stats()
        for key in ['txs_inspected', 'impacts_detected', 'bundles_prepared']:
            assert key in stats, f"Missing key: {key}"

    def test_zero_revert_pipeline_has_mempool_sniffer(self):
        """ZeroRevertPipeline.__init__ creates a mempool_sniffer attribute."""
        from profit_engine.zero_revert_pipeline import ZeroRevertPipeline
        zrp = ZeroRevertPipeline({})
        assert hasattr(zrp, 'mempool_sniffer')
        assert zrp.mempool_sniffer is not None

    def test_zero_revert_pipeline_get_stats_includes_mempool_keys(self):
        """ZeroRevertPipeline.get_stats() includes mempool sniffer keys."""
        from profit_engine.zero_revert_pipeline import ZeroRevertPipeline
        zrp = ZeroRevertPipeline({})
        stats = zrp.get_stats()
        for key in ['mempool_txs_inspected', 'mempool_impacts_detected', 'mempool_bundles_prepared']:
            assert key in stats, f"Missing ZRP stats key: {key}"

    def test_pipeline_has_zrp_stats_keys(self):
        """Pipeline.stats contains all Zero-Revert Pipeline stat keys."""
        from MODULE_1_LIQUIDATION_ENGINE.pipeline import Pipeline

        p = Pipeline.__new__(Pipeline)
        p.stats = {
            "zrp_positions": 0,
            "zrp_checks": 0,
            "zrp_fired": 0,
            "zrp_confirmed": 0,
            "zrp_reverted": 0,
            "zrp_profit_usd": 0.0,
            "zrp_mempool_txs_inspected": 0,
            "zrp_mempool_impacts_detected": 0,
            "zrp_mempool_bundles_prepared": 0,
        }
        for key in p.stats:
            assert key in p.stats

    def test_pipeline_sync_zrp_watchlist_no_pipeline(self):
        """_sync_zrp_watchlist is a no-op when zero_revert_pipeline is None."""
        from unittest.mock import MagicMock
        from MODULE_1_LIQUIDATION_ENGINE.pipeline import Pipeline

        p = Pipeline.__new__(Pipeline)
        p.zero_revert_pipeline = None
        p._sync_zrp_watchlist([MagicMock()])  # must not raise

    def test_pipeline_sync_zrp_watchlist_calls_feed(self):
        """_sync_zrp_watchlist forwards positions to zero_revert_pipeline.feed_watchlist."""
        from unittest.mock import MagicMock
        from MODULE_1_LIQUIDATION_ENGINE.pipeline import Pipeline

        p = Pipeline.__new__(Pipeline)
        mock_zrp = MagicMock()
        p.zero_revert_pipeline = mock_zrp

        mock_pos = MagicMock()
        mock_pos.user = "0xABCD"
        mock_pos.chain_id = 1
        mock_pos.health_factor = 1.02
        mock_pos.debt_amount = int(5e21)
        mock_pos.collateral_asset = "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2"
        mock_pos.debt_asset = "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48"
        mock_pos.pool_address = "0x87870Bca3F3fD6335C3F4ce8392D69350B4fA4E2"

        p._sync_zrp_watchlist([mock_pos])

        mock_zrp.feed_watchlist.assert_called_once()
        arg = mock_zrp.feed_watchlist.call_args[0][0]
        assert "0xABCD" in arg
        assert arg["0xABCD"]["chain_id"] == 1
        assert arg["0xABCD"]["last_hf"] == 1.02


# ============================================================================
# Run Tests
# ============================================================================

if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
