#!/usr/bin/env python3
"""
MODULE 1 — Unified Configuration Manager
==========================================
Single source of truth for all stages.  Loads from the project root .env file,
validates settings, and exposes typed accessors for every subsystem.

Usage:
    from MODULE_1_LIQUIDATION_ENGINE.config import get_config
    cfg = get_config()
"""

import os
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, field
from pathlib import Path
from dotenv import load_dotenv


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class ChainConfig:
    chain_id: int
    name: str
    rpc_url: str
    ws_url: Optional[str] = None
    currency_symbol: str = "ETH"
    block_time_seconds: int = 12
    is_l2: bool = False
    l2_gas_discount: float = 1.0


@dataclass
class ProtocolConfig:
    name: str
    chain_id: int
    pool_address: str
    liquidation_threshold: float
    bonus: float
    flash_loan_fee: float
    supported: bool = True


@dataclass
class FlashLoanProviderConfig:
    name: str
    chain_id: int
    router_address: str
    fee: float
    supported_assets: List[str] = field(default_factory=list)


@dataclass
class DatabaseConfig:
    host: str
    port: int
    database: str
    user: str
    password: str
    ssl_mode: str = "disable"

    @property
    def connection_string(self) -> str:
        return (
            f"postgresql://{self.user}:{self.password}"
            f"@{self.host}:{self.port}/{self.database}"
            f"?sslmode={self.ssl_mode}"
        )


@dataclass
class ExecutionConfig:
    # Align class defaults with the env-var defaults so direct instantiation
    # (e.g. in unit tests) produces the same aggressive thresholds as a
    # production run loaded from environment variables.
    min_profit_usd: float = 0.01             # env: MIN_PROFIT_USD (micro-profit)
    min_profit_wei: int = 100_000_000_000_000  # 0.0001 ETH; env: MIN_PROFIT_WEI
    gas_price_cap_gwei: float = 5.0           # env: GAS_PRICE_CAP_GWEI
    gas_limit_buffer: float = 1.15
    max_gas_limit: int = 1_000_000
    scan_interval_seconds: float = 0.5
    health_factor_threshold: float = 1.05
    min_debt_usd: float = 25.0               # env: MIN_DEBT_USD (capture small positions)
    transaction_timeout_seconds: int = 120
    # Maximum number of positions to push into the active watchlist (ranked by HF, lowest first)
    max_watchlist_size: int = 5000
    # Execution gate — ENABLED by default for full deployment.
    # Set EXECUTION_ENABLED=false to revert to scan-only / dry-run mode.
    execution_enabled: bool = True


# ---------------------------------------------------------------------------
# Chain registry
# ---------------------------------------------------------------------------

SUPPORTED_CHAINS: Dict[int, Dict[str, Any]] = {
    1:      {"name": "Ethereum",    "symbol": "ETH",   "block_time": 12, "is_l2": False},
    42161:  {"name": "Arbitrum",    "symbol": "ETH",   "block_time": 1,  "is_l2": True,  "gas_discount": 0.1},
    10:     {"name": "Optimism",    "symbol": "ETH",   "block_time": 2,  "is_l2": True,  "gas_discount": 0.1},
    8453:   {"name": "Base",        "symbol": "ETH",   "block_time": 2,  "is_l2": True,  "gas_discount": 0.1},
    137:    {"name": "Polygon",     "symbol": "MATIC", "block_time": 2,  "is_l2": True,  "gas_discount": 0.01},
    43114:  {"name": "Avalanche",   "symbol": "AVAX",  "block_time": 2,  "is_l2": False},
    56:     {"name": "BSC",         "symbol": "BNB",   "block_time": 3,  "is_l2": False},
    324:    {"name": "zkSync Era",  "symbol": "ETH",   "block_time": 1,  "is_l2": True,  "gas_discount": 0.1},
}

# ---------------------------------------------------------------------------
# RPC env-var names that match the ROOT .env
# ---------------------------------------------------------------------------

_RPC_ENV_MAP: Dict[int, str] = {
    1:     "MAINNET_RPC_URL",
    42161: "ARBITRUM_RPC_URL",
    10:    "OPTIMISM_RPC_URL",
    8453:  "BASE_RPC_URL",
    137:   "POLYGON_RPC_URL",
    43114: "AVALANCHE_RPC_URL",
    56:    "BSC_RPC_URL",
    324:   "ZKSYNC_RPC_URL",
}


# ---------------------------------------------------------------------------
# ConfigManager
# ---------------------------------------------------------------------------

class ConfigManager:
    """Centralised config for all Module 1 stages."""

    def __init__(self, env_file: Optional[str] = None):
        self._load_env(env_file)
        self._chains: Dict[int, ChainConfig] = {}
        self._protocols: Dict[str, ProtocolConfig] = {}
        self._flash_providers: Dict[str, FlashLoanProviderConfig] = {}
        self._init_chains()
        self._init_protocols()
        self._init_flash_providers()

    # ---- env loading ----

    @staticmethod
    def _load_env(env_file: Optional[str]):
        if env_file:
            load_dotenv(env_file)
            return
        # Search order: project root .env first
        for candidate in [
            Path(__file__).resolve().parents[2] / ".env",   # Cryo1/.env
            Path(__file__).resolve().parents[1] / ".env",   # MODULE_1/.env
            Path.cwd() / ".env",
        ]:
            if candidate.exists():
                load_dotenv(candidate)
                return

    # ---- chains ----

    def _init_chains(self):
        for chain_id, meta in SUPPORTED_CHAINS.items():
            rpc_key = _RPC_ENV_MAP.get(chain_id, "")
            rpc_url = os.getenv(rpc_key, "")
            self._chains[chain_id] = ChainConfig(
                chain_id=chain_id,
                name=meta["name"],
                rpc_url=rpc_url,
                currency_symbol=meta["symbol"],
                block_time_seconds=meta["block_time"],
                is_l2=meta.get("is_l2", False),
                l2_gas_discount=meta.get("gas_discount", 1.0),
            )

    # ---- protocols ----

    def _init_protocols(self):
        aave_v3 = {
            1:     os.getenv("AAVE_V3_POOL_ETHEREUM"),
            42161: os.getenv("AAVE_V3_POOL_ARBITRUM"),
            10:    os.getenv("AAVE_V3_POOL_OPTIMISM"),
            8453:  os.getenv("AAVE_V3_POOL_BASE"),
            137:   os.getenv("AAVE_V3_POOL_POLYGON"),
            43114: os.getenv("AAVE_V3_POOL_AVALANCHE"),
        }
        for cid, addr in aave_v3.items():
            if addr:
                self._protocols[f"aave_v3_{cid}"] = ProtocolConfig(
                    "Aave V3", cid, addr, 0.825, 0.05, 0.0005)

        aave_v2 = os.getenv("AAVE_V2_POOL_ETHEREUM")
        if aave_v2:
            self._protocols["aave_v2_1"] = ProtocolConfig(
                "Aave V2", 1, aave_v2, 0.80, 0.05, 0.0005)

        compound_v3 = {
            1:    os.getenv("COMPOUND_V3_ETHEREUM"),
            42161: os.getenv("COMPOUND_V3_ARBITRUM"),
            8453: os.getenv("COMPOUND_V3_BASE"),
        }
        for cid, addr in compound_v3.items():
            if addr:
                self._protocols[f"compound_v3_{cid}"] = ProtocolConfig(
                    "Compound V3", cid, addr, 0.80, 0.05, 0.0005)

        # ── Spark Protocol (MakerDAO's lending arm) ──
        # Same liquidation interface as Aave V3 (fork), 5% bonus
        spark_pool = os.getenv("SPARK_POOL_ETHEREUM", "0xC13e21B648A5Ee794902342038FF3aDAB66BE987")
        if spark_pool:
            self._protocols["spark_1"] = ProtocolConfig(
                "Spark", 1, spark_pool, 0.825, 0.05, 0.0)

        # ── Radiant V2 (multi-chain lending, Aave V2 fork) ──
        radiant_pools = {
            42161: os.getenv("RADIANT_V2_POOL_ARBITRUM", "0xF4B1486DD74D07706052A33d31d7c0AAFD0659E1"),
            56:    os.getenv("RADIANT_V2_POOL_BSC", "0xd50Cf00b6e600Dd036Ba8eF475677d816d6c4281"),
        }
        for cid, addr in radiant_pools.items():
            if addr:
                self._protocols[f"radiant_v2_{cid}"] = ProtocolConfig(
                    "Radiant V2", cid, addr, 0.80, 0.10, 0.0009)  # 10% bonus!

    # ---- flash-loan providers ----

    def _init_flash_providers(self):
        for cid, addr_env in {
            1: "AAVE_V3_POOL_ETHEREUM", 42161: "AAVE_V3_POOL_ARBITRUM",
            10: "AAVE_V3_POOL_OPTIMISM", 8453: "AAVE_V3_POOL_BASE",
            137: "AAVE_V3_POOL_POLYGON", 43114: "AAVE_V3_POOL_AVALANCHE",
        }.items():
            addr = os.getenv(addr_env)
            if addr:
                self._flash_providers[f"aave_v3_{cid}"] = FlashLoanProviderConfig(
                    "Aave V3", cid, addr, 0.0005)

        for cid, addr_env in {
            1: "UNISWAP_V3_ROUTER_ETHEREUM", 42161: "UNISWAP_V3_ROUTER_ARBITRUM",
            10: "UNISWAP_V3_ROUTER_OPTIMISM", 8453: "UNISWAP_V3_ROUTER_BASE",
        }.items():
            addr = os.getenv(addr_env)
            if addr:
                self._flash_providers[f"uniswap_v3_{cid}"] = FlashLoanProviderConfig(
                    "Uniswap V3", cid, addr, 0.003)

        for cid, addr_env in {
            1: "BALANCER_V2_VAULT_ETHEREUM", 137: "BALANCER_V2_VAULT_POLYGON",
            42161: "BALANCER_V2_VAULT_ARBITRUM",
        }.items():
            addr = os.getenv(addr_env)
            if addr:
                self._flash_providers[f"balancer_v2_{cid}"] = FlashLoanProviderConfig(
                    "Balancer V2", cid, addr, 0.0)

    # ---- accessors ----

    def get_chain(self, chain_id: int) -> Optional[ChainConfig]:
        return self._chains.get(chain_id)

    def get_all_chains(self) -> Dict[int, ChainConfig]:
        return self._chains.copy()

    def get_all_protocols(self) -> Dict[str, ProtocolConfig]:
        return self._protocols.copy()

    def get_all_flash_providers(self) -> Dict[str, FlashLoanProviderConfig]:
        return self._flash_providers.copy()

    @property
    def database(self) -> DatabaseConfig:
        return DatabaseConfig(
            host=os.getenv("TIMESCALE_HOST", "localhost"),
            port=int(os.getenv("TIMESCALE_PORT", "5432")),
            database=os.getenv("TIMESCALE_DB", "liquidation_engine"),
            user=os.getenv("TIMESCALE_USER", "postgres"),
            password=os.getenv("TIMESCALE_PASSWORD", ""),
            ssl_mode=os.getenv("TIMESCALE_SSL_MODE", "disable"),
        )

    @property
    def execution(self) -> ExecutionConfig:
        return ExecutionConfig(
            min_profit_usd=float(os.getenv("MIN_PROFIT_USD", "0.50")),
            min_profit_wei=int(os.getenv("MIN_PROFIT_WEI", "100000000000000")),
            gas_price_cap_gwei=float(os.getenv("GAS_PRICE_CAP_GWEI", "50")),
            gas_limit_buffer=float(os.getenv("GAS_LIMIT_BUFFER", "1.15")),
            max_gas_limit=int(os.getenv("MAX_GAS_LIMIT", "1000000")),
            scan_interval_seconds=float(os.getenv("SCAN_INTERVAL_SECONDS", "0.5")),
            health_factor_threshold=float(os.getenv("HEALTH_FACTOR_THRESHOLD", "1.05")),
            min_debt_usd=float(os.getenv("MIN_DEBT_USD", "100")),
            transaction_timeout_seconds=int(os.getenv("TRANSACTION_TIMEOUT_SECONDS", "120")),
            max_watchlist_size=int(os.getenv("MAX_WATCHLIST_SIZE", "2000")),
            execution_enabled=os.getenv("EXECUTION_ENABLED", "true").lower() == "true",
        )

    @property
    def private_key(self) -> Optional[str]:
        return os.getenv("PRIVATE_KEY")

    @property
    def treasury_address(self) -> str:
        return os.getenv("TREASURY_ADDRESS", "0x" + "0" * 40)

    @property
    def executor_v1(self) -> str:
        return os.getenv("LIQUIDATION_EXECUTOR_V1", "")

    @property
    def executor_v2(self) -> str:
        return os.getenv("LIQUIDATION_EXECUTOR_V2", "")

    @property
    def flash_executor(self) -> str:
        return os.getenv("FLASH_EXECUTOR", "")

    # ---- validation ----

    def validate(self) -> List[str]:
        errors = []
        if not self.private_key:
            errors.append("PRIVATE_KEY not set — scan-only mode")
        for cid, c in self._chains.items():
            if not c.rpc_url:
                errors.append(f"No RPC for {c.name} (chain {cid})")
        if self.treasury_address == "0x" + "0" * 40:
            errors.append("TREASURY_ADDRESS not set")
        return errors


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------

_config: Optional[ConfigManager] = None


def get_config() -> ConfigManager:
    global _config
    if _config is None:
        _config = ConfigManager()
    return _config
