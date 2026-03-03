#!/usr/bin/env python3
"""
Hub Integration Tests
Validates the consolidated command center — registry, recon coordinator,
and deployment strategy selector.

Run with: pytest hub/tests/test_hub.py -v --tb=short --asyncio-mode=auto
"""

import asyncio
import pytest
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

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
        import subprocess
        result = subprocess.run(
            [sys.executable, "main.py", "--hub", "status"],
            capture_output=True, text=True, cwd=os.path.dirname(os.path.dirname(
                os.path.dirname(os.path.abspath(__file__))
            )),
        )
        assert result.returncode == 0
        assert "CRYO MODULE REGISTRY" in result.stdout

    def test_hub_invalid_strategy_exit_code(self):
        import subprocess
        result = subprocess.run(
            [sys.executable, "main.py", "--hub", "bogus"],
            capture_output=True, text=True, cwd=os.path.dirname(os.path.dirname(
                os.path.dirname(os.path.abspath(__file__))
            )),
        )
        assert result.returncode == 1
        assert "Valid strategies" in result.stdout

    def test_hub_recon_exit_code(self):
        import subprocess
        result = subprocess.run(
            [sys.executable, "main.py", "--hub", "recon"],
            capture_output=True, text=True, cwd=os.path.dirname(os.path.dirname(
                os.path.dirname(os.path.abspath(__file__))
            )),
        )
        assert result.returncode == 0
        assert "RECON SWEEP REPORT" in result.stdout
