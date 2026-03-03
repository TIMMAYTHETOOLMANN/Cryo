#!/usr/bin/env python3
"""
TEST SUITE — Full-Stack Integration Tests
===========================================
End-to-end tests validating the complete system flow:
  1. Non-configured → correct error reporting
  2. Minimal config → scan-only capability
  3. Scanner → Gas Optimizer → Flash Loan Router pipeline
  4. ProfitLedger → Phase transition → CapitalMultiplier activation
  5. ConfigManager → SystemValidator → SituationAssessor coherence

These tests require NO external RPC connections or API keys.
"""

import asyncio
import json
import os
import sys
import time
import tempfile
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock

# ── Ensure project root is on sys.path ──────────────────────────────────────
PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from MODULE_1_LIQUIDATION_ENGINE.config.settings import (
    ConfigManager, _RPC_ENV_MAP, ExecutionConfig,
)
from MODULE_1_LIQUIDATION_ENGINE.stage_0_preflight.system_validator import (
    PreFlightReport, ChainCheck, ContractCheck,
)
from profit_engine.flash_loan_router import FlashLoanRouter, FlashLoanProvider
from profit_engine.gas_optimizer import GasOptimizer
from profit_engine.profit_ledger import ProfitLedger, ProfitEntry, PhaseState
from profit_engine.heat_map import HeatMap

# Keys to clear for environment isolation in config tests
_ENV_KEYS_TO_CLEAR = [
    "MAINNET_RPC_URL", "ARBITRUM_RPC_URL", "OPTIMISM_RPC_URL",
    "POLYGON_RPC_URL", "BASE_RPC_URL", "AVALANCHE_RPC_URL",
    "BSC_RPC_URL", "ZKSYNC_RPC_URL",
    "PRIVATE_KEY", "TREASURY_ADDRESS", "EXECUTION_ENABLED",
    "AAVE_V3_POOL_ETHEREUM", "AAVE_V3_POOL_ARBITRUM",
    "AAVE_V3_POOL_OPTIMISM", "AAVE_V3_POOL_BASE",
    "AAVE_V3_POOL_POLYGON", "AAVE_V3_POOL_AVALANCHE",
    "AAVE_V2_POOL_ETHEREUM",
    "COMPOUND_V3_ETHEREUM", "COMPOUND_V3_ARBITRUM", "COMPOUND_V3_BASE",
    "UNISWAP_V3_ROUTER_ETHEREUM", "UNISWAP_V3_ROUTER_ARBITRUM",
    "UNISWAP_V3_ROUTER_OPTIMISM", "UNISWAP_V3_ROUTER_BASE",
    "BALANCER_V2_VAULT_ETHEREUM", "BALANCER_V2_VAULT_ARBITRUM",
    "BALANCER_V2_VAULT_POLYGON",
    "LIQUIDATION_EXECUTOR_V1", "LIQUIDATION_EXECUTOR_V2",
    "FLASH_EXECUTOR",
]


# ═══════════════════════════════════════════════════════════════════════════════
#  Non-Configured → Error Detection
# ═══════════════════════════════════════════════════════════════════════════════


class TestNonConfiguredDetection:
    """Test that a completely unconfigured system properly reports all issues."""

    @pytest.fixture(autouse=True)
    def isolate_env(self, monkeypatch):
        for key in _ENV_KEYS_TO_CLEAR:
            monkeypatch.delenv(key, raising=False)

    @pytest.fixture
    def empty_config(self, tmp_path):
        env_file = tmp_path / ".env"
        env_file.write_text("# No configuration\n")
        return ConfigManager(env_file=str(env_file))

    def test_empty_config_reports_missing_private_key(self, empty_config):
        """Missing PRIVATE_KEY is reported."""
        errors = empty_config.validate()
        assert any("PRIVATE_KEY" in e for e in errors)

    def test_empty_config_reports_missing_rpc(self, empty_config):
        """Missing RPC URLs are reported for all chains."""
        errors = empty_config.validate()
        rpc_errors = [e for e in errors if "RPC" in e or "No RPC" in e]
        # Should have errors for all 8 chains
        assert len(rpc_errors) == 8

    def test_empty_config_reports_missing_treasury(self, empty_config):
        """Missing treasury address is reported."""
        errors = empty_config.validate()
        assert any("TREASURY" in e for e in errors)

    def test_empty_config_no_protocols(self, empty_config):
        """Empty config has no protocols loaded."""
        protocols = empty_config.get_all_protocols()
        assert len(protocols) == 0

    def test_empty_config_no_flash_providers(self, empty_config):
        """Empty config has no flash loan providers loaded."""
        providers = empty_config.get_all_flash_providers()
        assert len(providers) == 0

    def test_empty_config_execution_disabled(self, empty_config):
        """Empty config has execution disabled."""
        assert empty_config.execution.execution_enabled is False

    def test_preflight_report_no_go(self, empty_config):
        """PreFlightReport with empty config → NO-GO."""
        report = PreFlightReport()
        report.config_errors = empty_config.validate()
        report.private_key_set = bool(empty_config.private_key)
        assert report.private_key_set is False
        assert report.verdict == "NO-GO"

    def test_total_error_count(self, empty_config):
        """Non-configured system should report 10+ errors."""
        errors = empty_config.validate()
        assert len(errors) >= 10, (
            f"Expected 10+ errors from empty config, got {len(errors)}: {errors}"
        )


# ═══════════════════════════════════════════════════════════════════════════════
#  Minimal Config → Scan-Only Capability
# ═══════════════════════════════════════════════════════════════════════════════


class TestMinimalConfig:
    """Test that minimal configuration enables scan-only mode."""

    @pytest.fixture(autouse=True)
    def isolate_env(self, monkeypatch):
        for key in _ENV_KEYS_TO_CLEAR:
            monkeypatch.delenv(key, raising=False)

    @pytest.fixture
    def scan_only_config(self, tmp_path):
        """Config with just one RPC URL — no private key."""
        env_file = tmp_path / ".env"
        env_file.write_text(
            "MAINNET_RPC_URL=https://eth-mainnet.g.alchemy.com/v2/test-key\n"
            "AAVE_V3_POOL_ETHEREUM=0x87870Bca3F3fD6335C3F4ce8392D69350B4fA4E2\n"
        )
        return ConfigManager(env_file=str(env_file))

    def test_scan_only_has_rpc(self, scan_only_config):
        """Ethereum chain has RPC configured."""
        eth = scan_only_config.get_chain(1)
        assert eth.rpc_url != ""

    def test_scan_only_no_private_key(self, scan_only_config):
        """No private key in scan-only mode."""
        assert scan_only_config.private_key is None or scan_only_config.private_key == ""

    def test_scan_only_has_protocol(self, scan_only_config):
        """At least one protocol loaded."""
        protocols = scan_only_config.get_all_protocols()
        assert len(protocols) > 0

    def test_scan_only_execution_disabled(self, scan_only_config):
        """Execution disabled in scan-only mode."""
        assert scan_only_config.execution.execution_enabled is False

    def test_scan_only_partial_errors(self, scan_only_config):
        """Scan-only config reports some but not all errors."""
        errors = scan_only_config.validate()
        # Should have PRIVATE_KEY and missing chains errors
        assert any("PRIVATE_KEY" in e for e in errors)
        # But not missing ETH RPC
        assert not any("Ethereum" in e and "No RPC" in e for e in errors)


# ═══════════════════════════════════════════════════════════════════════════════
#  Full Config → GO Verdict
# ═══════════════════════════════════════════════════════════════════════════════


class TestFullConfig:
    """Test that full configuration passes all checks."""

    @pytest.fixture(autouse=True)
    def isolate_env(self, monkeypatch):
        for key in _ENV_KEYS_TO_CLEAR:
            monkeypatch.delenv(key, raising=False)

    @pytest.fixture
    def full_config(self, tmp_path):
        """Fully configured .env file."""
        env_file = tmp_path / ".env"
        lines = []
        for chain_id, env_key in _RPC_ENV_MAP.items():
            lines.append(f"{env_key}=https://rpc.example.com/{chain_id}")
        lines.extend([
            "PRIVATE_KEY=0x0000000000000000000000000000000000000000000000000000000000000001",
            "TREASURY_ADDRESS=0x1234567890abcdef1234567890abcdef12345678",
            "EXECUTION_ENABLED=false",
            "AAVE_V3_POOL_ETHEREUM=0x87870Bca3F3fD6335C3F4ce8392D69350B4fA4E2",
            "AAVE_V3_POOL_ARBITRUM=0x794a61358D6845594F94dc1DB02A252b5b4814aD",
            "COMPOUND_V3_ETHEREUM=0xc3d688B66703497DAA19211EEdff47f25384cdc3",
            "UNISWAP_V3_ROUTER_ETHEREUM=0xE592427A0AEce92De3Edee1F18E0157C05861564",
            "BALANCER_V2_VAULT_ETHEREUM=0xBA12222222228d8Ba445958a75a0704d566BF2C8",
        ])
        env_file.write_text("\n".join(lines))
        return ConfigManager(env_file=str(env_file))

    def test_full_config_no_errors(self, full_config):
        """Full config passes validation with zero errors."""
        errors = full_config.validate()
        assert len(errors) == 0, f"Unexpected errors: {errors}"

    def test_full_config_all_chains_have_rpc(self, full_config):
        """All chains have RPC URLs."""
        for chain_id, chain in full_config.get_all_chains().items():
            assert chain.rpc_url != "", f"Chain {chain_id} missing RPC"

    def test_full_config_has_private_key(self, full_config):
        """Private key is set."""
        assert full_config.private_key is not None

    def test_full_config_has_treasury(self, full_config):
        """Treasury address is set."""
        assert full_config.treasury_address != "0x" + "0" * 40

    def test_full_config_has_protocols(self, full_config):
        """Protocols are loaded from full config."""
        protocols = full_config.get_all_protocols()
        assert len(protocols) >= 3  # Aave V3 (2 chains) + Compound V3

    def test_full_config_has_flash_providers(self, full_config):
        """Flash loan providers are loaded."""
        providers = full_config.get_all_flash_providers()
        assert len(providers) >= 3


# ═══════════════════════════════════════════════════════════════════════════════
#  Pipeline Integration: Scanner → Gas → Flash Loan
# ═══════════════════════════════════════════════════════════════════════════════


class TestPipelineIntegration:
    """Test the Scanner → Gas Optimizer → Flash Loan Router pipeline."""

    @pytest.fixture
    def pipeline(self):
        """Create the full pipeline without RPC connections."""
        router = FlashLoanRouter()
        optimizer = GasOptimizer()
        return router, optimizer

    def test_gas_then_route_profitable(self, pipeline):
        """Profitable opportunity passes gas check and gets a route."""
        router, optimizer = pipeline
        # Step 1: Gas check
        est = optimizer.estimate(1, 'flash_loan_liquidation', 500.0)
        assert est.is_profitable_at_current
        # Step 2: Route selection
        route = router.find_best_route(1, "WETH", 10000, est.margin_remaining_usd)
        assert route is not None
        assert route.fee_usd < est.margin_remaining_usd

    def test_gas_then_route_unprofitable(self, pipeline):
        """Unprofitable opportunity correctly rejected by gas check."""
        router, optimizer = pipeline
        # Tiny profit that gas would eat on L1
        est = optimizer.estimate(1, 'flash_loan_liquidation', 0.001)
        # Should be unprofitable (gas > profit on L1)
        assert est.is_profitable_at_current is False or est.margin_remaining_usd < 0.01

    def test_l2_more_profitable_than_l1(self, pipeline):
        """Same opportunity is more profitable on L2 (lower gas)."""
        router, optimizer = pipeline
        l1_est = optimizer.estimate(1, 'liquidation', 200.0)
        l2_est = optimizer.estimate(42161, 'liquidation', 200.0)
        assert l2_est.margin_remaining_usd > l1_est.margin_remaining_usd

    def test_multi_chain_route_selection(self, pipeline):
        """Routes available on multiple chains."""
        router, optimizer = pipeline
        chains_with_routes = []
        for chain_id in [1, 42161, 10, 137, 8453]:
            route = router.find_best_route(chain_id, "WETH", 10000, 500)
            if route is not None:
                chains_with_routes.append(chain_id)
        assert len(chains_with_routes) >= 3, (
            "Expected routes on 3+ chains"
        )


# ═══════════════════════════════════════════════════════════════════════════════
#  Phase Transition Integration
# ═══════════════════════════════════════════════════════════════════════════════


class TestPhaseTransitionIntegration:
    """Test the full phase transition lifecycle."""

    @pytest.fixture
    def system(self, tmp_path):
        ledger = ProfitLedger(config={
            'phase2_threshold': 100.0,   # Low thresholds for testing
            'phase3_threshold': 500.0,
            'ledger_path': str(tmp_path / 'l.json'),
            'snapshot_path': str(tmp_path / 's.json'),
        })
        heatmap = HeatMap()
        optimizer = GasOptimizer()
        router = FlashLoanRouter()
        return ledger, heatmap, optimizer, router

    def _entry(self, net_profit, opp_type="liquidation"):
        return ProfitEntry(
            entry_id=f"test-{time.time()}",
            timestamp=time.time(),
            chain_id=1,
            opportunity_type=opp_type,
            protocol="aave_v3",
            tx_hash="0x" + "b" * 64,
            gross_profit_usd=net_profit * 1.2,
            gas_cost_usd=net_profit * 0.1,
            flash_loan_fee_usd=net_profit * 0.05,
            net_profit_usd=net_profit,
            capital_deployed_usd=0.0,
            roi_percent=80.0,
            execution_time_ms=100,
            block_number=19000000,
            phase=PhaseState.COLD_START,
        )

    @pytest.mark.asyncio
    async def test_full_phase_lifecycle(self, system):
        """Complete phase 1 → 2 → 3 transition."""
        ledger, heatmap, optimizer, router = system

        # Phase 1: Cold Start
        assert ledger.current_phase == PhaseState.COLD_START

        # Accumulate $100 → Phase 2
        for _ in range(11):
            await ledger.record(self._entry(10.0))
        assert ledger.current_phase == PhaseState.HEAT_MAP

        # Accumulate $500 → Phase 3
        for _ in range(45):
            await ledger.record(self._entry(10.0))
        assert ledger.current_phase == PhaseState.MULTIPLIER

    @pytest.mark.asyncio
    async def test_gas_and_fee_tracking_through_pipeline(self, system):
        """Gas costs and fees accumulate correctly through pipeline."""
        ledger, _, _, _ = system
        for _ in range(5):
            await ledger.record(self._entry(100.0))
        assert ledger.cumulative_gas_usd > 0
        assert ledger.cumulative_fees_usd > 0
        report = ledger.status_report()
        assert report['cumulative_gas_usd'] > 0
        assert report['cumulative_fees_usd'] > 0

    @pytest.mark.asyncio
    async def test_ledger_status_report_completeness(self, system):
        """Status report has all required fields."""
        ledger, _, _, _ = system
        await ledger.record(self._entry(50.0))
        report = ledger.status_report()
        required = [
            'phase', 'elapsed_hours', 'total_transactions',
            'successful_transactions', 'failed_transactions',
            'success_rate', 'cumulative_profit_usd',
            'cumulative_gas_usd', 'cumulative_fees_usd',
            'avg_profit_per_tx', 'hourly_run_rate',
            'capital_base_usd', 'profit_reserve_usd',
            'top_opportunity_types', 'top_chains',
            'phase2_progress', 'phase3_progress',
            'gas_inefficient_vectors',
        ]
        for key in required:
            assert key in report, f"Missing key in status report: {key}"


# ═══════════════════════════════════════════════════════════════════════════════
#  Cross-Module Coherence
# ═══════════════════════════════════════════════════════════════════════════════


class TestCrossModuleCoherence:
    """Test that different modules are consistent with each other."""

    def test_gas_optimizer_chains_match_config(self, tmp_path):
        """GasOptimizer chains match ConfigManager supported chains."""
        from MODULE_1_LIQUIDATION_ENGINE.config.settings import SUPPORTED_CHAINS
        optimizer = GasOptimizer()
        for chain_id in SUPPORTED_CHAINS:
            assert chain_id in optimizer.chain_states, (
                f"GasOptimizer missing chain {chain_id}"
            )

    def test_flash_router_chains_cover_config(self):
        """FlashLoanRouter providers cover config chains."""
        from MODULE_1_LIQUIDATION_ENGINE.config.settings import SUPPORTED_CHAINS
        router = FlashLoanRouter()
        covered_chains = set()
        for provider, profile in router.providers.items():
            covered_chains.update(profile.supported_chains)
        for chain_id in SUPPORTED_CHAINS:
            assert chain_id in covered_chains or chain_id == 324, (
                f"FlashLoanRouter has no provider for chain {chain_id}"
            )

    def test_gas_units_cover_common_ops(self):
        """GasOptimizer has gas units for all common operation types."""
        required_ops = [
            'liquidation', 'flash_loan_liquidation',
            'arbitrage_2hop', 'arbitrage_3hop',
        ]
        for op in required_ops:
            assert op in GasOptimizer.GAS_UNITS, (
                f"GasOptimizer missing gas estimate for '{op}'"
            )

    def test_eth_prices_reasonable(self):
        """ETH prices in GasOptimizer are reasonable."""
        for chain_id, price in GasOptimizer.DEFAULT_ETH_PRICES.items():
            assert price > 0, f"Chain {chain_id} has non-positive ETH price"
            if chain_id in [1, 42161, 10, 8453, 324]:
                # ETH-based chains
                assert price >= 100, (
                    f"Chain {chain_id} ETH price {price} seems too low"
                )

    def test_phase_thresholds_ascending(self):
        """Phase thresholds are ascending: Phase2 < Phase3."""
        assert ProfitLedger.PHASE2_THRESHOLD < ProfitLedger.PHASE3_THRESHOLD

    def test_phase_states_defined(self):
        """All phase states are defined."""
        assert PhaseState.COLD_START.value == "phase_1_cold_start"
        assert PhaseState.HEAT_MAP.value == "phase_2_heat_map"
        assert PhaseState.MULTIPLIER.value == "phase_3_capital_multiplier"
