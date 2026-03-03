#!/usr/bin/env python3
"""
Hub Integration Tests
Validates the consolidated command center — registry, recon coordinator,
and deployment strategy selector.

Run with: pytest hub/tests/test_hub.py -v --tb=short --asyncio-mode=auto
"""

import asyncio
import pytest
import subprocess
import sys
import os
from pathlib import Path

PROJECT_ROOT = str(Path(__file__).resolve().parents[2])
sys.path.insert(0, PROJECT_ROOT)

from hub.registry import ModuleRegistry, ModuleInfo, ModuleStatus
from hub.recon_coordinator import ReconCoordinator, ReconResult
from hub.deployment import DeploymentStrategy, deploy


# ============================================================================
# Registry Tests
# ============================================================================

class TestModuleRegistry:
    """Tests for ModuleRegistry — catalogue, discovery, init, status."""

    def test_discover_populates_registry(self):
        reg = ModuleRegistry().discover()
        mods = reg.list_modules()
        assert len(mods) >= 20

    def test_categories_present(self):
        reg = ModuleRegistry().discover()
        cats = reg.categories
        for expected in ("detection", "recon", "analysis", "execution", "monitoring", "orchestrator"):
            assert expected in cats

    def test_status_shape(self):
        reg = ModuleRegistry().discover()
        st = reg.status()
        assert "total_modules" in st
        assert "ready" in st
        assert "pending" in st
        assert "categories" in st
        assert st["ready"] == 0  # nothing initialized yet

    def test_initialize_single_module(self):
        reg = ModuleRegistry().discover()
        inst = reg.initialize("vulnerability_scanner")
        assert inst is not None
        info = reg.get("vulnerability_scanner")
        assert info.status == ModuleStatus.READY
        assert info.instance is inst

    def test_initialize_idempotent(self):
        reg = ModuleRegistry().discover()
        inst1 = reg.initialize("ml_ranker")
        inst2 = reg.initialize("ml_ranker")
        assert inst1 is inst2

    def test_initialize_unknown_raises(self):
        reg = ModuleRegistry().discover()
        with pytest.raises(KeyError):
            reg.initialize("nonexistent_module")

    def test_initialize_bad_factory_sets_error(self):
        reg = ModuleRegistry().discover()
        # opportunity_scanner needs args we don't provide
        with pytest.raises(Exception):
            reg.initialize("opportunity_scanner")
        info = reg.get("opportunity_scanner")
        assert info.status == ModuleStatus.ERROR
        assert info.error is not None

    def test_initialize_category(self):
        reg = ModuleRegistry().discover()
        ok = reg.initialize_category("analysis")
        # At least ml_ranker and gas_optimizer should succeed
        assert "ml_ranker" in ok
        assert "gas_optimizer" in ok

    def test_get_instance(self):
        reg = ModuleRegistry().discover()
        assert reg.get_instance("ml_ranker") is None
        reg.initialize("ml_ranker")
        assert reg.get_instance("ml_ranker") is not None

    def test_list_modules_by_category(self):
        reg = ModuleRegistry().discover()
        recon = reg.list_modules(category="recon")
        assert len(recon) >= 4
        for m in recon:
            assert m.category == "recon"

    def test_register_custom_module(self):
        reg = ModuleRegistry().discover()
        before = len(reg.list_modules())
        custom = ModuleInfo(
            name="custom_test",
            category="test",
            description="A test module",
            import_path="os.path",
            factory="join",
        )
        reg.register(custom)
        assert len(reg.list_modules()) == before + 1
        assert reg.get("custom_test") is not None

    def test_module_is_available(self):
        info = ModuleInfo(
            name="x", category="t", description="", import_path="", factory=""
        )
        assert not info.is_available()
        info.status = ModuleStatus.READY
        assert info.is_available()
        info.status = ModuleStatus.RUNNING
        assert info.is_available()
        info.status = ModuleStatus.ERROR
        assert not info.is_available()

    def test_print_status_runs(self, capsys):
        reg = ModuleRegistry().discover()
        reg.print_status()
        out = capsys.readouterr().out
        assert "CRYO MODULE REGISTRY" in out
        assert "detection" in out.lower()


# ============================================================================
# ReconCoordinator Tests
# ============================================================================

class TestReconCoordinator:
    """Tests for ReconCoordinator — sweep, report, results."""

    @pytest.mark.asyncio
    async def test_initialize(self):
        reg = ModuleRegistry().discover()
        coord = ReconCoordinator(reg)
        await coord.initialize()
        # At least some modules should be ready
        ready = [m for m in reg.list_modules() if m.is_available()]
        assert len(ready) > 0

    @pytest.mark.asyncio
    async def test_run_sweep_returns_results(self):
        reg = ModuleRegistry().discover()
        coord = ReconCoordinator(reg)
        results = await coord.run_sweep(chain_ids=[1])
        assert isinstance(results, list)
        assert len(results) > 0

    @pytest.mark.asyncio
    async def test_sweep_result_shape(self):
        reg = ModuleRegistry().discover()
        coord = ReconCoordinator(reg)
        results = await coord.run_sweep(chain_ids=[1])
        for r in results:
            assert isinstance(r, ReconResult)
            assert r.source
            assert r.summary

    @pytest.mark.asyncio
    async def test_clear(self):
        reg = ModuleRegistry().discover()
        coord = ReconCoordinator(reg)
        await coord.run_sweep(chain_ids=[1])
        assert len(coord.get_results()) > 0
        coord.clear()
        assert len(coord.get_results()) == 0

    @pytest.mark.asyncio
    async def test_print_report(self, capsys):
        reg = ModuleRegistry().discover()
        coord = ReconCoordinator(reg)
        results = await coord.run_sweep(chain_ids=[1])
        coord.print_report(results)
        out = capsys.readouterr().out
        assert "RECON SWEEP REPORT" in out


# ============================================================================
# DeploymentStrategy Tests
# ============================================================================

class TestDeploymentStrategy:
    """Tests for DeploymentStrategy enum and deploy()."""

    def test_all_strategies_defined(self):
        expected = {
            "status", "preflight", "recon", "scan_only",
            "phase_1", "phase_2", "phase_3", "full_pipeline", "monitor",
            "ops_full_recon", "ops_target_acquire", "ops_system_check",
            "ops_phase2_activate",
        }
        actual = {s.value for s in DeploymentStrategy}
        assert expected == actual

    @pytest.mark.asyncio
    async def test_deploy_status(self, capsys):
        rc = await deploy(DeploymentStrategy.STATUS)
        assert rc == 0
        out = capsys.readouterr().out
        assert "CRYO MODULE REGISTRY" in out

    @pytest.mark.asyncio
    async def test_deploy_recon(self, capsys):
        rc = await deploy(DeploymentStrategy.RECON, chain_ids=[1])
        assert rc == 0
        out = capsys.readouterr().out
        assert "RECON SWEEP REPORT" in out


# ============================================================================
# CLI Integration Tests
# ============================================================================

class TestCLIIntegration:
    """Tests that main.py --hub dispatching works."""

    def test_hub_status_exit_code(self):
        result = subprocess.run(
            [sys.executable, "main.py", "--hub", "status"],
            capture_output=True, text=True, cwd=PROJECT_ROOT,
        )
        assert result.returncode == 0
        assert "CRYO MODULE REGISTRY" in result.stdout

    def test_hub_invalid_strategy_exit_code(self):
        result = subprocess.run(
            [sys.executable, "main.py", "--hub", "bogus"],
            capture_output=True, text=True, cwd=PROJECT_ROOT,
        )
        assert result.returncode == 1
        assert "Valid strategies" in result.stdout

    def test_hub_recon_exit_code(self):
        result = subprocess.run(
            [sys.executable, "main.py", "--hub", "recon"],
            capture_output=True, text=True, cwd=PROJECT_ROOT,
        )
        assert result.returncode == 0
        assert "RECON SWEEP REPORT" in result.stdout

    def test_hub_ops_system_check_exit_code(self):
        result = subprocess.run(
            [sys.executable, "main.py", "--hub", "ops_system_check"],
            capture_output=True, text=True, cwd=PROJECT_ROOT,
        )
        assert result.returncode == 0
        assert "TACTICAL OPERATION" in result.stdout

    def test_hub_ops_full_recon_exit_code(self):
        result = subprocess.run(
            [sys.executable, "main.py", "--hub", "ops_full_recon"],
            capture_output=True, text=True, cwd=PROJECT_ROOT,
        )
        assert result.returncode == 0
        assert "FULL_RECON" in result.stdout


# ============================================================================
# Signal Adapter Tests
# ============================================================================

class TestSignalAdapter:
    """Tests for the bidirectional signal translation layer."""

    def _make_m9_signal(self, **overrides):
        from MODULE_9_OMNI_SCOPE.data_bus import (
            OpportunitySignal as M9Signal,
            SignalType as M9Type,
            SignalSource as M9Src,
        )
        defaults = dict(
            signal_type=M9Type.PENDING_LIQUIDATION,
            source=M9Src.MEMPOOL_RADAR,
            chain_id=1,
            confidence=0.85,
            estimated_profit_usd=5000.0,
            gas_cost_estimate_usd=50.0,
            urgency_seconds=12.0,
            target_protocol="aave_v3",
            target_user="0x" + "ab" * 20,
            target_contract="0x" + "cd" * 20,
            health_factor=0.95,
            quality_score=0.92,
            routed_to="liquidation_engine",
        )
        defaults.update(overrides)
        return M9Signal(**defaults)

    def _make_omni_signal(self, **overrides):
        from omni_channel.data_lake.data_models import (
            OpportunitySignal as OmniSignal,
            SignalType as OmniType,
            SignalSource as OmniSrc,
        )
        defaults = dict(
            signal_type=OmniType.LIQUIDATION,
            source_module=OmniSrc.MEMPOOL_RADAR,
            chain_id=1,
            expected_value_usd=5000.0,
            confidence=0.85,
            urgency_score=80.0,
            target_contract="0x" + "cd" * 20,
            user_address="0x" + "ab" * 20,
            health_factor=0.95,
        )
        defaults.update(overrides)
        return OmniSignal(**defaults)

    def test_m9_to_omni_preserves_profit(self):
        from hub.signal_adapter import m9_to_omni
        m9 = self._make_m9_signal(estimated_profit_usd=1234.0)
        omni = m9_to_omni(m9)
        assert omni.expected_value_usd == 1234.0

    def test_m9_to_omni_preserves_confidence(self):
        from hub.signal_adapter import m9_to_omni
        m9 = self._make_m9_signal(confidence=0.73)
        omni = m9_to_omni(m9)
        assert omni.confidence == 0.73

    def test_m9_to_omni_preserves_quality_score_in_metadata(self):
        from hub.signal_adapter import m9_to_omni
        m9 = self._make_m9_signal(quality_score=0.92)
        omni = m9_to_omni(m9)
        assert omni.metadata["m9_quality_score"] == 0.92

    def test_m9_to_omni_preserves_routed_to_in_metadata(self):
        from hub.signal_adapter import m9_to_omni
        m9 = self._make_m9_signal(routed_to="liquidation_engine")
        omni = m9_to_omni(m9)
        assert omni.metadata["m9_routed_to"] == "liquidation_engine"

    def test_m9_to_omni_maps_type_liquidation(self):
        from hub.signal_adapter import m9_to_omni
        from MODULE_9_OMNI_SCOPE.data_bus import SignalType as M9Type
        m9 = self._make_m9_signal(signal_type=M9Type.PENDING_LIQUIDATION)
        omni = m9_to_omni(m9)
        assert omni.signal_type.value == "liquidation"

    def test_m9_to_omni_maps_type_arbitrage(self):
        from hub.signal_adapter import m9_to_omni
        from MODULE_9_OMNI_SCOPE.data_bus import SignalType as M9Type
        m9 = self._make_m9_signal(signal_type=M9Type.ARBITRAGE)
        omni = m9_to_omni(m9)
        assert omni.signal_type.value == "arbitrage"

    def test_m9_to_omni_maps_type_cross_chain(self):
        from hub.signal_adapter import m9_to_omni
        from MODULE_9_OMNI_SCOPE.data_bus import SignalType as M9Type
        m9 = self._make_m9_signal(signal_type=M9Type.CROSS_CHAIN_ARB)
        omni = m9_to_omni(m9)
        assert omni.signal_type.value == "cross_chain_arb"

    def test_m9_to_omni_maps_type_bridge_imbalance(self):
        from hub.signal_adapter import m9_to_omni
        from MODULE_9_OMNI_SCOPE.data_bus import SignalType as M9Type
        m9 = self._make_m9_signal(signal_type=M9Type.BRIDGE_IMBALANCE)
        omni = m9_to_omni(m9)
        assert omni.signal_type.value == "cross_chain_arb"

    def test_m9_to_omni_sets_execution_module(self):
        from hub.signal_adapter import m9_to_omni
        from omni_channel.data_lake.data_models import ExecutionModule
        m9 = self._make_m9_signal(routed_to="liquidation_engine")
        omni = m9_to_omni(m9)
        assert omni.routed_to == ExecutionModule.LIQUIDATION_ENGINE

    def test_m9_to_omni_net_profit(self):
        from hub.signal_adapter import m9_to_omni
        m9 = self._make_m9_signal(
            estimated_profit_usd=500.0,
            gas_cost_estimate_usd=20.0,
        )
        omni = m9_to_omni(m9)
        assert omni.net_profit_usd == 480.0

    def test_omni_to_m9_preserves_profit(self):
        from hub.signal_adapter import omni_to_m9
        omni = self._make_omni_signal(expected_value_usd=3000.0)
        m9 = omni_to_m9(omni)
        assert m9.estimated_profit_usd == 3000.0

    def test_omni_to_m9_preserves_confidence(self):
        from hub.signal_adapter import omni_to_m9
        omni = self._make_omni_signal(confidence=0.65)
        m9 = omni_to_m9(omni)
        assert m9.confidence == 0.65

    def test_omni_to_m9_preserves_signal_id(self):
        from hub.signal_adapter import omni_to_m9
        omni = self._make_omni_signal()
        m9 = omni_to_m9(omni)
        assert m9.metadata["omni_signal_id"] == omni.signal_id

    def test_omni_to_m9_maps_type(self):
        from hub.signal_adapter import omni_to_m9
        from omni_channel.data_lake.data_models import SignalType as OmniType
        omni = self._make_omni_signal(signal_type=OmniType.ARBITRAGE)
        m9 = omni_to_m9(omni)
        assert m9.signal_type.value == "arbitrage"

    def test_roundtrip_m9_to_omni_to_m9(self):
        from hub.signal_adapter import m9_to_omni, omni_to_m9
        m9_orig = self._make_m9_signal(
            estimated_profit_usd=7777.0,
            confidence=0.91,
            chain_id=42161,
        )
        omni = m9_to_omni(m9_orig)
        m9_back = omni_to_m9(omni)
        assert m9_back.estimated_profit_usd == 7777.0
        assert m9_back.confidence == 0.91
        assert m9_back.chain_id == 42161

    def test_signal_bridge_translate(self):
        from MODULE_9_OMNI_SCOPE.data_bus import DataBus
        from hub.signal_adapter import SignalBridge
        bus = DataBus()
        bridge = SignalBridge()
        bridge.attach(bus)

        bus.publish(self._make_m9_signal(estimated_profit_usd=100.0))
        bus.publish(self._make_m9_signal(estimated_profit_usd=200.0))

        translated = bridge.consume(limit=10)
        assert len(translated) == 2
        # Sorted by profit desc
        assert translated[0].expected_value_usd == 200.0
        assert translated[1].expected_value_usd == 100.0

    def test_signal_bridge_stats(self):
        from MODULE_9_OMNI_SCOPE.data_bus import DataBus
        from hub.signal_adapter import SignalBridge
        bus = DataBus()
        bridge = SignalBridge()
        bridge.attach(bus)

        bus.publish(self._make_m9_signal())
        stats = bridge.get_stats()
        assert stats["signals_received"] == 1
        assert stats["signals_translated"] == 1
        assert stats["translation_errors"] == 0


# ============================================================================
# Tactical Ops Tests
# ============================================================================

class TestTacticalOps:
    """Tests for step-by-step tactical operations."""

    @pytest.mark.asyncio
    async def test_available_operations(self):
        from hub.tactical_ops import TacticalOps
        reg = ModuleRegistry().discover()
        ops = TacticalOps(reg)
        available = ops.available_operations
        assert "full_recon" in available
        assert "target_acquire" in available
        assert "system_check" in available
        assert "phase2_activate" in available

    @pytest.mark.asyncio
    async def test_execute_unknown_op_raises(self):
        from hub.tactical_ops import TacticalOps
        reg = ModuleRegistry().discover()
        ops = TacticalOps(reg)
        with pytest.raises(ValueError, match="Unknown operation"):
            await ops.execute("nonexistent_op")

    @pytest.mark.asyncio
    async def test_system_check_returns_report(self):
        from hub.tactical_ops import TacticalOps, OpReport
        reg = ModuleRegistry().discover()
        ops = TacticalOps(reg)
        report = await ops.execute("system_check")
        assert isinstance(report, OpReport)
        assert report.operation == "system_check"
        assert len(report.steps) >= 3
        assert report.total_duration_s >= 0

    @pytest.mark.asyncio
    async def test_system_check_step_names(self):
        from hub.tactical_ops import TacticalOps
        reg = ModuleRegistry().discover()
        ops = TacticalOps(reg)
        report = await ops.execute("system_check")
        step_names = [s.name for s in report.steps]
        assert "registry_status" in step_names
        assert "log_analysis" in step_names
        assert "process_check" in step_names

    @pytest.mark.asyncio
    async def test_full_recon_returns_report(self):
        from hub.tactical_ops import TacticalOps
        reg = ModuleRegistry().discover()
        ops = TacticalOps(reg)
        report = await ops.execute("full_recon")
        assert report.operation == "full_recon"
        assert len(report.steps) >= 4  # init_recon, init_detect, init_analysis, sweep, rank
        assert report.passed >= 4

    @pytest.mark.asyncio
    async def test_target_acquire_returns_report(self):
        from hub.tactical_ops import TacticalOps
        reg = ModuleRegistry().discover()
        ops = TacticalOps(reg)
        report = await ops.execute("target_acquire")
        assert report.operation == "target_acquire"
        assert len(report.steps) >= 3

    @pytest.mark.asyncio
    async def test_phase2_activate_returns_report(self):
        from hub.tactical_ops import TacticalOps
        reg = ModuleRegistry().discover()
        ops = TacticalOps(reg)
        report = await ops.execute("phase2_activate")
        assert report.operation == "phase2_activate"
        step_names = [s.name for s in report.steps]
        assert "phase2_summary" in step_names

    @pytest.mark.asyncio
    async def test_print_report(self, capsys):
        from hub.tactical_ops import TacticalOps
        reg = ModuleRegistry().discover()
        ops = TacticalOps(reg)
        report = await ops.execute("system_check")
        TacticalOps.print_report(report)
        out = capsys.readouterr().out
        assert "TACTICAL OPERATION" in out
        assert "SYSTEM_CHECK" in out

    @pytest.mark.asyncio
    async def test_deploy_ops_system_check(self, capsys):
        rc = await deploy(DeploymentStrategy.OPS_SYSTEM_CHECK)
        assert rc == 0
        out = capsys.readouterr().out
        assert "TACTICAL OPERATION" in out

    @pytest.mark.asyncio
    async def test_deploy_ops_full_recon(self, capsys):
        rc = await deploy(DeploymentStrategy.OPS_FULL_RECON)
        assert rc == 0
        out = capsys.readouterr().out
        assert "FULL_RECON" in out
