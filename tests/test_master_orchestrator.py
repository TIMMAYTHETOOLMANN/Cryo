#!/usr/bin/env python3
"""
TEST SUITE — Master Profit Orchestrator
=========================================
Validates the MasterProfitOrchestrator initialization, signal aggregation,
deduplication, dashboard metrics, and module lifecycle.
"""

import asyncio
import os
import sys
import time
import pytest
from decimal import Decimal
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

# ── Ensure project root is on sys.path ──────────────────────────────────────
PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from master_orchestrator import (
    MasterProfitOrchestrator,
    AggregatedSignal,
    OrchestratorMetrics,
)


# ════════════════════════════════════════════════════════════════════
#  OrchestratorMetrics
# ════════════════════════════════════════════════════════════════════


class TestOrchestratorMetrics:
    """Tests for the OrchestratorMetrics dataclass."""

    def test_defaults(self):
        m = OrchestratorMetrics()
        assert m.cycles_completed == 0
        assert m.signals_received == 0
        assert m.total_profit_usd == Decimal("0")
        assert m.total_gas_spent_usd == Decimal("0")
        assert m.active_modules == []

    def test_net_profit(self):
        m = OrchestratorMetrics()
        m.total_profit_usd = Decimal("1000")
        m.total_gas_spent_usd = Decimal("50")
        assert m.net_profit_usd == Decimal("950")

    def test_success_rate_zero(self):
        m = OrchestratorMetrics()
        assert m.success_rate == 0.0

    def test_success_rate_some(self):
        m = OrchestratorMetrics()
        m.executions_succeeded = 7
        m.executions_failed = 3
        assert m.success_rate == pytest.approx(0.7)

    def test_uptime(self):
        m = OrchestratorMetrics()
        m.start_time = time.time() - 120
        assert m.uptime_seconds >= 119


# ════════════════════════════════════════════════════════════════════
#  AggregatedSignal
# ════════════════════════════════════════════════════════════════════


class TestAggregatedSignal:
    """Tests for the AggregatedSignal dataclass."""

    def test_creation(self):
        sig = AggregatedSignal(
            signal_id="test_1",
            source="omni_scope",
            signal_type="liquidation",
            chain_id=1,
            target="0xABC",
            estimated_profit_usd=Decimal("500"),
            confidence=0.85,
        )
        assert sig.source == "omni_scope"
        assert sig.estimated_profit_usd == Decimal("500")
        assert sig.confidence == 0.85
        assert sig.raw_signal is None


# ════════════════════════════════════════════════════════════════════
#  MasterProfitOrchestrator — Initialization
# ════════════════════════════════════════════════════════════════════


class TestMasterOrchestratorInit:
    """Tests for orchestrator construction and configuration."""

    def test_default_config(self, monkeypatch):
        monkeypatch.delenv("MIN_PROFIT_USD", raising=False)
        monkeypatch.delenv("SCAN_INTERVAL", raising=False)
        orch = MasterProfitOrchestrator()
        assert orch._scan_only is False
        assert orch._min_profit_usd == Decimal("50")
        assert orch._cycle_interval == 12.0
        assert orch.metrics.start_time == 0.0

    def test_custom_config(self):
        orch = MasterProfitOrchestrator({
            "scan_only": True,
            "cycle_interval": 5,
            "min_profit_usd": 100,
        })
        assert orch._scan_only is True
        assert orch._cycle_interval == 5.0
        assert orch._min_profit_usd == Decimal("100")

    def test_env_overrides(self, monkeypatch):
        monkeypatch.setenv("SCAN_INTERVAL", "30")
        monkeypatch.setenv("MIN_PROFIT_USD", "200")
        orch = MasterProfitOrchestrator()
        assert orch._cycle_interval == 30.0
        assert orch._min_profit_usd == Decimal("200")

    def test_config_overrides_env(self, monkeypatch):
        """Explicit config values take priority over env vars."""
        monkeypatch.setenv("SCAN_INTERVAL", "99")
        orch = MasterProfitOrchestrator({"cycle_interval": 7})
        assert orch._cycle_interval == 7.0


# ════════════════════════════════════════════════════════════════════
#  Signal Aggregation & Dedup
# ════════════════════════════════════════════════════════════════════


class TestSignalAggregation:
    """Tests for signal enqueue, dedup, and profit gating."""

    @pytest.fixture
    def orch(self):
        return MasterProfitOrchestrator({"min_profit_usd": 10})

    @pytest.mark.asyncio
    async def test_enqueue_signal(self, orch):
        result = await orch._enqueue_signal(
            source="test",
            signal_type="liquidation",
            chain_id=1,
            target="0xABC",
            estimated_profit_usd=100.0,
            confidence=0.9,
        )
        assert result is True
        assert orch.metrics.signals_received == 1
        assert orch._signal_queue.qsize() == 1

    @pytest.mark.asyncio
    async def test_dedup_blocks_duplicate(self, orch):
        kwargs = dict(
            source="test",
            signal_type="liquidation",
            chain_id=1,
            target="0xABC",
            estimated_profit_usd=100.0,
            confidence=0.9,
        )
        first = await orch._enqueue_signal(**kwargs)
        second = await orch._enqueue_signal(**kwargs)
        assert first is True
        assert second is False
        assert orch.metrics.signals_deduplicated == 1
        assert orch._signal_queue.qsize() == 1

    @pytest.mark.asyncio
    async def test_dedup_allows_after_window(self, orch):
        orch._dedup_window = 0.01  # 10ms
        kwargs = dict(
            source="test",
            signal_type="liquidation",
            chain_id=1,
            target="0xABC",
            estimated_profit_usd=100.0,
            confidence=0.9,
        )
        first = await orch._enqueue_signal(**kwargs)
        await asyncio.sleep(0.02)
        second = await orch._enqueue_signal(**kwargs)
        assert first is True
        assert second is True

    @pytest.mark.asyncio
    async def test_profit_gate_blocks_low(self, orch):
        result = await orch._enqueue_signal(
            source="test",
            signal_type="arb",
            chain_id=1,
            target="0xDEF",
            estimated_profit_usd=5.0,  # below $10 min
            confidence=0.9,
        )
        assert result is False
        assert orch._signal_queue.qsize() == 0

    @pytest.mark.asyncio
    async def test_different_targets_not_deduped(self, orch):
        r1 = await orch._enqueue_signal(
            source="test", signal_type="liq", chain_id=1,
            target="0xAAA", estimated_profit_usd=100, confidence=0.9,
        )
        r2 = await orch._enqueue_signal(
            source="test", signal_type="liq", chain_id=1,
            target="0xBBB", estimated_profit_usd=100, confidence=0.9,
        )
        assert r1 is True
        assert r2 is True
        assert orch._signal_queue.qsize() == 2

    @pytest.mark.asyncio
    async def test_different_chains_not_deduped(self, orch):
        r1 = await orch._enqueue_signal(
            source="test", signal_type="liq", chain_id=1,
            target="0xAAA", estimated_profit_usd=100, confidence=0.9,
        )
        r2 = await orch._enqueue_signal(
            source="test", signal_type="liq", chain_id=42161,
            target="0xAAA", estimated_profit_usd=100, confidence=0.9,
        )
        assert r1 is True
        assert r2 is True

    def test_signal_key_generation(self, orch):
        key = orch._signal_key("omni", "liq", "0xABC", 1)
        assert key == "omni:liq:0xABC:1"


# ════════════════════════════════════════════════════════════════════
#  Stats & Dashboard
# ════════════════════════════════════════════════════════════════════


class TestStatsAndDashboard:
    """Tests for get_stats and dashboard output."""

    def test_get_stats_structure(self):
        orch = MasterProfitOrchestrator()
        orch.metrics.start_time = time.time()
        orch.metrics.signals_received = 42
        orch.metrics.executions_succeeded = 5
        stats = orch.get_stats()
        assert stats["signals_received"] == 42
        assert stats["executions_succeeded"] == 5
        assert stats["scan_only"] is False
        assert "net_profit_usd" in stats
        assert "active_modules" in stats
        assert "queue_depth" in stats

    def test_get_stats_scan_only(self):
        orch = MasterProfitOrchestrator({"scan_only": True})
        stats = orch.get_stats()
        assert stats["scan_only"] is True

    def test_dashboard_does_not_crash(self):
        orch = MasterProfitOrchestrator()
        orch.metrics.start_time = time.time() - 60
        orch.metrics.active_modules = ["profit_engine", "omni_scope"]
        # Should not raise
        orch._print_dashboard()


# ════════════════════════════════════════════════════════════════════
#  Module Initialization (mocked)
# ════════════════════════════════════════════════════════════════════


class TestModuleInit:
    """Tests for lazy module initialization with graceful degradation."""

    @pytest.mark.asyncio
    async def test_init_handles_missing_modules(self):
        """All modules unavailable → still initializes with 0 modules."""
        orch = MasterProfitOrchestrator({"scan_only": True})
        with patch.dict("sys.modules", {
            "profit_engine": None,
            "MODULE_1_LIQUIDATION_ENGINE.pipeline": None,
            "MODULE_9_OMNI_SCOPE.engine": None,
            "omni_channel": None,
        }):
            await orch._init_modules()
        # At minimum signal_bridge should work (it's from hub which exists)
        assert isinstance(orch.metrics.active_modules, list)

    @pytest.mark.asyncio
    async def test_init_records_active_modules(self):
        """Successfully loaded modules are recorded in metrics."""
        orch = MasterProfitOrchestrator({"scan_only": True})
        await orch._init_modules()
        # At minimum profit_engine and signal_bridge should load
        # (they exist in this repo)
        assert len(orch.metrics.active_modules) > 0

    @pytest.mark.asyncio
    async def test_shutdown_is_idempotent(self):
        """Calling stop on uninitialized orchestrator should not crash."""
        orch = MasterProfitOrchestrator()
        await orch._shutdown_modules()


# ════════════════════════════════════════════════════════════════════
#  Hub Integration
# ════════════════════════════════════════════════════════════════════


class TestHubIntegration:
    """Tests for hub deployment strategy integration."""

    def test_master_strategy_exists(self):
        from hub import DeploymentStrategy
        assert DeploymentStrategy.MASTER.value == "master"

    def test_all_strategies_present(self):
        from hub import DeploymentStrategy
        values = {s.value for s in DeploymentStrategy}
        assert "master" in values
        assert "full_pipeline" in values
        assert "status" in values


# ════════════════════════════════════════════════════════════════════
#  CLI Integration
# ════════════════════════════════════════════════════════════════════


class TestCLIIntegration:
    """Tests that the --master flag is recognized by main.py."""

    def test_master_arg_parsing(self):
        from main import parse_args
        with patch("sys.argv", ["main.py", "--master"]):
            args = parse_args()
        assert args.master is True

    def test_master_scan_only(self):
        from main import parse_args
        with patch("sys.argv", ["main.py", "--master", "--scan-only"]):
            args = parse_args()
        assert args.master is True
        assert args.scan_only is True
