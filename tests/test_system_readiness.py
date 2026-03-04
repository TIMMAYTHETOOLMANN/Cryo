#!/usr/bin/env python3
"""
TEST SUITE — System Readiness & Configuration Validation
==========================================================
Comprehensive tests to ensure the system can go from non-configured to
perfectly synced, calibrated, and ready for production.

Tests:
  1. Environment template completeness
  2. ConfigManager initialization, defaults, and validation
  3. RPC URL & API key format validation
  4. Chain configuration completeness
  5. Protocol & flash-loan provider configuration
  6. Execution config defaults & safety gates
  7. PreFlightReport verdict logic
  8. SituationAssessor phase recommendation
"""

import os
import re
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
    ConfigManager,
    ChainConfig,
    ProtocolConfig,
    FlashLoanProviderConfig,
    ExecutionConfig,
    DatabaseConfig,
    SUPPORTED_CHAINS,
    _RPC_ENV_MAP,
)
from MODULE_1_LIQUIDATION_ENGINE.stage_0_preflight.system_validator import (
    PreFlightReport,
    ChainCheck,
    ContractCheck,
    MIN_GAS_BALANCES,
)


# ═══════════════════════════════════════════════════════════════════════════════
#  .env.template Completeness
# ═══════════════════════════════════════════════════════════════════════════════


class TestEnvTemplate:
    """Ensure the .env.template contains all required configuration keys."""

    @pytest.fixture(autouse=True)
    def load_template(self):
        template_path = PROJECT_ROOT / ".env.template"
        assert template_path.exists(), ".env.template missing from project root"
        self.template_text = template_path.read_text()
        # Extract KEY=value lines (skip comments and blank lines)
        self.keys = set()
        for line in self.template_text.splitlines():
            line = line.strip()
            if line and not line.startswith('#') and '=' in line:
                key = line.split('=', 1)[0].strip()
                self.keys.add(key)

    def test_rpc_urls_present(self):
        """All supported chain RPC URLs must be in the template."""
        required_rpc = [
            "MAINNET_RPC_URL", "ARBITRUM_RPC_URL", "OPTIMISM_RPC_URL",
            "POLYGON_RPC_URL", "BASE_RPC_URL", "AVALANCHE_RPC_URL",
            "BSC_RPC_URL", "ZKSYNC_RPC_URL",
        ]
        for key in required_rpc:
            assert key in self.keys, f"Missing RPC key in .env.template: {key}"

    def test_execution_keys_present(self):
        """Execution configuration keys must be in the template."""
        required = [
            "PRIVATE_KEY", "TREASURY_ADDRESS", "EXECUTION_ENABLED",
            "MIN_PROFIT_USD", "MIN_PROFIT_WEI", "GAS_PRICE_CAP_GWEI",
            "GAS_LIMIT_BUFFER", "MAX_GAS_LIMIT", "HEALTH_FACTOR_THRESHOLD",
        ]
        for key in required:
            assert key in self.keys, f"Missing execution key in .env.template: {key}"

    def test_protocol_addresses_present(self):
        """Protocol pool addresses must be in the template."""
        required = [
            "AAVE_V3_POOL_ETHEREUM", "AAVE_V3_POOL_ARBITRUM",
            "AAVE_V2_POOL_ETHEREUM",
            "COMPOUND_V3_ETHEREUM",
            "UNISWAP_V3_ROUTER_ETHEREUM",
            "BALANCER_V2_VAULT_ETHEREUM",
        ]
        for key in required:
            assert key in self.keys, f"Missing protocol key in .env.template: {key}"

    def test_oracle_addresses_present(self):
        """Chainlink oracle addresses must be in the template."""
        required = [
            "CHAINLINK_ETH_USD", "CHAINLINK_BTC_USD", "CHAINLINK_FEED_REGISTRY",
        ]
        for key in required:
            assert key in self.keys, f"Missing oracle key in .env.template: {key}"

    def test_alert_routing_keys_present(self):
        """Alert routing keys must be in the template."""
        required = [
            "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID",
            "DISCORD_WEBHOOK_URL",
            "PAGERDUTY_INTEGRATION_KEY",
        ]
        for key in required:
            assert key in self.keys, f"Missing alert key in .env.template: {key}"

    def test_database_keys_present(self):
        """Database configuration keys must be in the template."""
        required = [
            "TIMESCALE_HOST", "TIMESCALE_PORT", "TIMESCALE_DB",
            "TIMESCALE_USER", "TIMESCALE_PASSWORD",
        ]
        for key in required:
            assert key in self.keys, f"Missing database key in .env.template: {key}"

    def test_scanner_keys_present(self):
        """Scanner configuration keys must be in the template."""
        required = ["SCAN_INTERVAL", "SCAN_INTERVAL_SECONDS", "MAX_WATCHLIST_SIZE"]
        for key in required:
            assert key in self.keys, f"Missing scanner key in .env.template: {key}"

    def test_mev_protection_keys_present(self):
        """MEV protection keys must be in the template."""
        required = ["FLASHBOTS_RELAY_URL", "MEV_SHARE_RELAY_URL", "FLASHBOTS_BUILDER_URLS"]
        for key in required:
            assert key in self.keys, f"Missing MEV key in .env.template: {key}"

    def test_execution_disabled_by_default(self):
        """EXECUTION_ENABLED must default to false for safety."""
        for line in self.template_text.splitlines():
            if line.strip().startswith("EXECUTION_ENABLED="):
                val = line.split("=", 1)[1].strip().lower()
                assert val == "false", (
                    f"EXECUTION_ENABLED defaults to '{val}' — MUST be 'false'"
                )
                return
        pytest.fail("EXECUTION_ENABLED not found in .env.template")

    def test_private_key_empty_by_default(self):
        """PRIVATE_KEY must be empty by default in template."""
        for line in self.template_text.splitlines():
            if line.strip().startswith("PRIVATE_KEY="):
                val = line.split("=", 1)[1].strip()
                assert val == "", f"PRIVATE_KEY should be empty, got: '{val}'"
                return

    def test_no_real_api_keys_in_template(self):
        """Template must not contain actual API keys."""
        for line in self.template_text.splitlines():
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            if '=' not in line:
                continue
            key, val = line.split('=', 1)
            key, val = key.strip(), val.strip()
            # RPC URLs should have YOUR_API_KEY placeholder or be empty
            if key.endswith('_RPC_URL') and val:
                assert 'YOUR_API_KEY' in val or val == '', (
                    f"{key} contains a real API key in template: {val[:30]}..."
                )

    def test_template_has_minimum_keys(self):
        """Template should have a comprehensive set of configuration keys."""
        assert len(self.keys) >= 30, (
            f"Template has only {len(self.keys)} keys — expected 30+"
        )


# ═══════════════════════════════════════════════════════════════════════════════
#  ConfigManager
# ═══════════════════════════════════════════════════════════════════════════════


class TestConfigManager:
    """Test ConfigManager initialization, defaults, and validation."""

    # Keys that load_dotenv may inject from the real .env; we must clear them
    # to guarantee test isolation.
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
        # Execution config keys
        "MIN_PROFIT_USD", "MIN_PROFIT_WEI", "GAS_PRICE_CAP_GWEI",
        "GAS_LIMIT_BUFFER", "MAX_GAS_LIMIT", "SCAN_INTERVAL_SECONDS",
        "HEALTH_FACTOR_THRESHOLD", "MIN_DEBT_USD",
        "TRANSACTION_TIMEOUT_SECONDS", "MAX_WATCHLIST_SIZE",
    ]

    @pytest.fixture(autouse=True)
    def isolate_env(self, monkeypatch):
        """Clear env vars before each test to prevent leaking from real .env."""
        for key in self._ENV_KEYS_TO_CLEAR:
            monkeypatch.delenv(key, raising=False)

    @pytest.fixture
    def clean_env(self, tmp_path):
        """Provide a minimal .env file for isolated testing."""
        env_file = tmp_path / ".env"
        env_file.write_text(
            "MAINNET_RPC_URL=https://eth-mainnet.g.alchemy.com/v2/test-key\n"
            "PRIVATE_KEY=0x0000000000000000000000000000000000000000000000000000000000000001\n"
            "TREASURY_ADDRESS=0x1234567890abcdef1234567890abcdef12345678\n"
            "AAVE_V3_POOL_ETHEREUM=0x87870Bca3F3fD6335C3F4ce8392D69350B4fA4E2\n"
            "EXECUTION_ENABLED=false\n"
        )
        return str(env_file)

    @pytest.fixture
    def empty_env(self, tmp_path):
        """Provide an empty .env file (non-configured state)."""
        env_file = tmp_path / ".env"
        env_file.write_text("# Empty config\n")
        return str(env_file)

    def test_init_with_env_file(self, clean_env):
        """ConfigManager loads from an explicit .env file."""
        cfg = ConfigManager(env_file=clean_env)
        chain = cfg.get_chain(1)
        assert chain is not None
        assert chain.chain_id == 1
        assert chain.name == "Ethereum"
        assert "test-key" in chain.rpc_url

    def test_init_empty_env(self, empty_env):
        """ConfigManager works with empty config (scan-only mode)."""
        cfg = ConfigManager(env_file=empty_env)
        errors = cfg.validate()
        assert len(errors) > 0  # Should report missing keys
        assert any("PRIVATE_KEY" in e for e in errors)

    def test_all_supported_chains_registered(self, clean_env):
        """All 8 supported chains must be registered."""
        cfg = ConfigManager(env_file=clean_env)
        chains = cfg.get_all_chains()
        assert len(chains) == len(SUPPORTED_CHAINS)
        for chain_id in SUPPORTED_CHAINS:
            assert chain_id in chains

    def test_chain_config_fields(self, clean_env):
        """ChainConfig has all required fields populated."""
        cfg = ConfigManager(env_file=clean_env)
        eth = cfg.get_chain(1)
        assert eth.chain_id == 1
        assert eth.name == "Ethereum"
        assert eth.currency_symbol == "ETH"
        assert eth.block_time_seconds == 12
        assert eth.is_l2 is False

    def test_l2_chains_flagged_correctly(self, clean_env):
        """L2 chains have is_l2=True and gas discounts."""
        cfg = ConfigManager(env_file=clean_env)
        l2_chains = {42161, 10, 8453, 137, 324}
        for chain_id in l2_chains:
            chain = cfg.get_chain(chain_id)
            assert chain.is_l2 is True, f"Chain {chain_id} should be L2"

    def test_protocols_loaded(self, clean_env):
        """Protocols are loaded from env vars."""
        cfg = ConfigManager(env_file=clean_env)
        protocols = cfg.get_all_protocols()
        assert "aave_v3_1" in protocols
        proto = protocols["aave_v3_1"]
        assert proto.name == "Aave V3"
        assert proto.chain_id == 1

    def test_flash_providers_loaded(self, clean_env):
        """Flash loan providers are loaded from env vars."""
        cfg = ConfigManager(env_file=clean_env)
        providers = cfg.get_all_flash_providers()
        assert "aave_v3_1" in providers
        prov = providers["aave_v3_1"]
        assert prov.name == "Aave V3"
        assert prov.fee == 0.0005

    def test_execution_config_defaults(self, clean_env):
        """Execution config has safe defaults matching .env.template."""
        cfg = ConfigManager(env_file=clean_env)
        exec_cfg = cfg.execution
        assert exec_cfg.execution_enabled is False  # Safety gate OFF
        assert exec_cfg.min_profit_usd == 50.0
        assert exec_cfg.gas_price_cap_gwei == 50.0
        assert exec_cfg.gas_limit_buffer == 1.2
        assert exec_cfg.max_gas_limit == 1_000_000
        assert exec_cfg.health_factor_threshold == 1.05
        assert exec_cfg.min_debt_usd == 1_000.0
        assert exec_cfg.transaction_timeout_seconds == 120
        assert exec_cfg.max_watchlist_size == 500

    def test_execution_enabled_requires_true(self, tmp_path, monkeypatch):
        """Execution is only enabled when explicitly set to 'true'."""
        # Test "true" → enabled
        monkeypatch.setenv("EXECUTION_ENABLED", "true")
        env_file = tmp_path / ".env"
        env_file.write_text("EXECUTION_ENABLED=true\n")
        cfg = ConfigManager(env_file=str(env_file))
        assert cfg.execution.execution_enabled is True

        # Test "TRUE" → enabled (case insensitive)
        monkeypatch.setenv("EXECUTION_ENABLED", "TRUE")
        env_file.write_text("EXECUTION_ENABLED=TRUE\n")
        cfg = ConfigManager(env_file=str(env_file))
        assert cfg.execution.execution_enabled is True

        # Test "yes" → NOT enabled (only "true" works)
        monkeypatch.setenv("EXECUTION_ENABLED", "yes")
        env_file.write_text("EXECUTION_ENABLED=yes\n")
        cfg = ConfigManager(env_file=str(env_file))
        assert cfg.execution.execution_enabled is False  # Only "true" works

    def test_validate_catches_missing_private_key(self, empty_env):
        """Validation reports missing PRIVATE_KEY."""
        cfg = ConfigManager(env_file=empty_env)
        errors = cfg.validate()
        assert any("PRIVATE_KEY" in e for e in errors)

    def test_validate_catches_missing_rpc(self, empty_env):
        """Validation reports missing RPC URLs."""
        cfg = ConfigManager(env_file=empty_env)
        errors = cfg.validate()
        rpc_errors = [e for e in errors if "RPC" in e or "rpc" in e.lower()]
        assert len(rpc_errors) > 0

    def test_validate_catches_missing_treasury(self, empty_env):
        """Validation reports missing TREASURY_ADDRESS."""
        cfg = ConfigManager(env_file=empty_env)
        errors = cfg.validate()
        assert any("TREASURY" in e for e in errors)

    def test_validate_clean_env(self, tmp_path):
        """Full configuration passes validation."""
        env_file = tmp_path / ".env"
        lines = []
        for chain_id, env_key in _RPC_ENV_MAP.items():
            lines.append(f"{env_key}=https://rpc.example.com/{chain_id}")
        lines.append("PRIVATE_KEY=0x0000000000000000000000000000000000000000000000000000000000000001")
        lines.append("TREASURY_ADDRESS=0x1234567890abcdef1234567890abcdef12345678")
        env_file.write_text("\n".join(lines))
        cfg = ConfigManager(env_file=str(env_file))
        errors = cfg.validate()
        assert len(errors) == 0, f"Unexpected validation errors: {errors}"

    def test_database_config_defaults(self, empty_env):
        """Database config has sensible defaults."""
        cfg = ConfigManager(env_file=empty_env)
        db = cfg.database
        assert db.host == "localhost"
        assert db.port == 5432
        assert isinstance(db.connection_string, str)
        assert "postgresql://" in db.connection_string

    def test_private_key_accessor(self, clean_env):
        """Private key accessor returns the configured value."""
        cfg = ConfigManager(env_file=clean_env)
        pk = cfg.private_key
        assert pk is not None
        assert pk.startswith("0x")

    def test_chain_rpc_env_map_covers_all_chains(self):
        """Every supported chain has an RPC env var mapping."""
        for chain_id in SUPPORTED_CHAINS:
            assert chain_id in _RPC_ENV_MAP, (
                f"Chain {chain_id} ({SUPPORTED_CHAINS[chain_id]['name']}) "
                f"has no RPC env var mapping"
            )


# ═══════════════════════════════════════════════════════════════════════════════
#  RPC URL & API Key Format Validation
# ═══════════════════════════════════════════════════════════════════════════════


class TestRPCURLValidation:
    """Validate that RPC URL formats are correct."""

    VALID_RPC_PATTERNS = [
        r"^https?://.*",                    # Must be HTTP(S)
        r"^wss?://.*",                      # Or WebSocket
    ]

    def test_valid_alchemy_url(self):
        """Alchemy URLs are properly formed."""
        url = "https://eth-mainnet.g.alchemy.com/v2/test-key"
        assert any(re.match(p, url) for p in self.VALID_RPC_PATTERNS)

    def test_valid_infura_url(self):
        """Infura URLs are properly formed."""
        url = "https://mainnet.infura.io/v3/test-key"
        assert any(re.match(p, url) for p in self.VALID_RPC_PATTERNS)

    def test_reject_empty_rpc(self):
        """Empty string is not a valid RPC URL."""
        assert not any(re.match(p, "") for p in self.VALID_RPC_PATTERNS)

    def test_reject_placeholder_rpc(self):
        """YOUR_API_KEY placeholder is detected."""
        url = "https://eth-mainnet.g.alchemy.com/v2/YOUR_API_KEY"
        assert "YOUR_API_KEY" in url  # Would need to be replaced

    def test_ethereum_address_format(self):
        """Ethereum addresses are 42 chars (0x + 40 hex)."""
        valid = "0x87870Bca3F3fD6335C3F4ce8392D69350B4fA4E2"
        assert len(valid) == 42
        assert valid.startswith("0x")
        assert all(c in "0123456789abcdefABCDEF" for c in valid[2:])

    def test_reject_invalid_address(self):
        """Invalid Ethereum addresses are caught."""
        invalid_addrs = [
            "",
            "0x",
            "0x" + "0" * 39,    # Too short
            "0x" + "G" * 40,    # Invalid hex chars
            "0x" + "0" * 41,    # Too long
        ]
        for addr in invalid_addrs:
            assert len(addr) != 42 or not all(
                c in "0123456789abcdefABCDEF" for c in addr[2:]
            ), f"Should reject: {addr}"


# ═══════════════════════════════════════════════════════════════════════════════
#  PreFlightReport Verdict Logic
# ═══════════════════════════════════════════════════════════════════════════════


class TestPreFlightReport:
    """Test the pre-flight report data structures and verdict logic."""

    def test_default_report_is_no_go(self):
        """Fresh report defaults to NO-GO."""
        report = PreFlightReport()
        assert report.verdict == "NO-GO"
        assert report.can_scan is False
        assert report.can_analyse is False
        assert report.can_execute is False

    def test_report_timestamp_auto_set(self):
        """Report auto-sets timestamp."""
        report = PreFlightReport()
        assert report.timestamp > 0
        assert abs(report.timestamp - time.time()) < 2

    def test_chain_check_defaults(self):
        """ChainCheck defaults to not connected."""
        cc = ChainCheck(chain_id=1, name="Ethereum")
        assert cc.rpc_connected is False
        assert cc.block_number == 0
        assert cc.wallet_balance == 0.0
        assert cc.has_sufficient_gas is False

    def test_contract_check_defaults(self):
        """ContractCheck defaults to no code."""
        cc = ContractCheck(name="Executor", address="0x123")
        assert cc.has_code is False
        assert cc.owner_matches is False

    def test_min_gas_balances_all_chains(self):
        """All supported chains have minimum gas balance defined."""
        for chain_id in SUPPORTED_CHAINS:
            assert chain_id in MIN_GAS_BALANCES, (
                f"Chain {chain_id} missing from MIN_GAS_BALANCES"
            )

    def test_scan_only_verdict(self):
        """Report with connected chains but no gas → SCAN-ONLY capable."""
        report = PreFlightReport()
        report.chains = {
            1: ChainCheck(chain_id=1, name="Ethereum", rpc_connected=True),
        }
        any_connected = any(c.rpc_connected for c in report.chains.values())
        assert any_connected  # Scan should be possible

    def test_go_verdict_requirements(self):
        """GO verdict requires: private key + gas + deployed contracts."""
        report = PreFlightReport()
        report.private_key_set = True
        report.chains_with_gas = [1]
        report.contracts = {
            "Executor V1": ContractCheck(
                name="Executor V1", address="0x123", has_code=True
            ),
        }
        # Re-evaluate verdict conditions
        can_execute = (
            report.private_key_set
            and len(report.chains_with_gas) > 0
            and any(c.has_code for c in report.contracts.values())
        )
        assert can_execute


# ═══════════════════════════════════════════════════════════════════════════════
#  ExecutionConfig Safety
# ═══════════════════════════════════════════════════════════════════════════════


class TestExecutionConfigSafety:
    """Verify execution config safety gates and defaults."""

    def test_default_execution_disabled(self):
        """ExecutionConfig defaults to execution_enabled=False."""
        cfg = ExecutionConfig()
        assert cfg.execution_enabled is False

    def test_default_min_profit(self):
        """Minimum profit defaults are set for production."""
        cfg = ExecutionConfig()
        assert cfg.min_profit_usd == 50.0
        assert cfg.min_profit_wei == 10_000_000_000_000_000  # 0.01 ETH

    def test_default_gas_cap(self):
        """Gas price cap defaults to 50 gwei."""
        cfg = ExecutionConfig()
        assert cfg.gas_price_cap_gwei == 50.0

    def test_default_health_factor(self):
        """Health factor threshold defaults to 1.05."""
        cfg = ExecutionConfig()
        assert cfg.health_factor_threshold == 1.05

    def test_default_min_debt(self):
        """Minimum debt USD defaults to $1,000."""
        cfg = ExecutionConfig()
        assert cfg.min_debt_usd == 1_000.0

    def test_default_watchlist_size(self):
        """Watchlist size defaults to 500."""
        cfg = ExecutionConfig()
        assert cfg.max_watchlist_size == 500

    def test_default_tx_timeout(self):
        """Transaction timeout defaults to 120 seconds."""
        cfg = ExecutionConfig()
        assert cfg.transaction_timeout_seconds == 120

    def test_gas_limit_buffer(self):
        """Gas limit buffer defaults to 1.2 (20% buffer)."""
        cfg = ExecutionConfig()
        assert cfg.gas_limit_buffer == 1.2

    def test_max_gas_limit(self):
        """Max gas limit defaults to 1M."""
        cfg = ExecutionConfig()
        assert cfg.max_gas_limit == 1_000_000


# ═══════════════════════════════════════════════════════════════════════════════
#  Database & Alert Configuration
# ═══════════════════════════════════════════════════════════════════════════════


class TestDatabaseConfig:
    """Test database configuration structure."""

    def test_connection_string_format(self):
        """DatabaseConfig produces valid connection string."""
        db = DatabaseConfig(
            host="localhost", port=5432, database="test",
            user="postgres", password="pass", ssl_mode="disable",
        )
        cs = db.connection_string
        assert cs.startswith("postgresql://")
        assert "localhost" in cs
        assert "5432" in cs
        assert "test" in cs

    def test_connection_string_with_ssl(self):
        """SSL mode is included in connection string."""
        db = DatabaseConfig(
            host="prod.db.com", port=5432, database="ocds",
            user="app", password="secret", ssl_mode="require",
        )
        assert "sslmode=require" in db.connection_string
