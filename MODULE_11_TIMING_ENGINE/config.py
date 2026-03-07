#!/usr/bin/env python3
"""
MODULE 11 -- Timing Engine Configuration
==========================================
All sub-config dataclasses for the Timing Engine's submodules.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from dotenv import load_dotenv

load_dotenv()


# ── Helper ────────────────────────────────────────────────────────

def _env_float(key: str, default: float) -> float:
    try:
        return float(os.getenv(key, str(default)))
    except (ValueError, TypeError):
        return default


def _env_int(key: str, default: int) -> int:
    try:
        return int(os.getenv(key, str(default)))
    except (ValueError, TypeError):
        return default


def _env_bool(key: str, default: bool = False) -> bool:
    return os.getenv(key, str(default)).lower().strip() in ("true", "1", "yes")


# ═══════════════════════════════════════════════════════════════════
#  SUB-CONFIGS  (one per submodule)
# ═══════════════════════════════════════════════════════════════════

@dataclass
class OracleConfig:
    """11.1 -- Oracle Price Watcher config."""
    poll_interval_s: float = 1.0
    stale_threshold_s: int = 3600
    enabled: bool = True
    feeds: Dict[str, str] = field(default_factory=dict)  # asset -> feed_address
    ws_enabled: bool = True


@dataclass
class MempoolConfig:
    """11.2 -- Mempool Sniffer config."""
    enabled: bool = True
    min_swap_impact_usd: float = 50_000.0
    ws_url: str = ""
    watch_routers: List[str] = field(default_factory=lambda: [
        "0x7a250d5630B4cF539739dF2C5dAcb4c659F2488D",  # UniswapV2
        "0xE592427A0AEce92De3Edee1F18E0157C05861564",  # UniswapV3
    ])
    backrun_min_profit_usd: float = 1.0


@dataclass
class PredictiveModelConfig:
    """11.3 -- Predictive Health Model config."""
    model_type: str = "gradient_boosting"
    min_training_samples: int = 100
    confidence_threshold: float = 0.6
    high_confidence_threshold: float = 0.85
    retrain_interval_s: float = 3600.0
    feature_count: int = 12


@dataclass
class JITExecutorConfig:
    """11.4 -- Just-in-Time Executor config."""
    max_pre_signed_txs: int = 20
    stale_tx_ttl_s: float = 300.0
    gas_tip_multiplier: float = 1.5
    max_gas_price_gwei: float = 50.0
    preflight_block_state: str = "pending"
    flashbots_rpc: str = "https://relay.flashbots.net"
    private_relays: List[str] = field(default_factory=lambda: [
        "https://rpc.titanbuilder.xyz",
        "https://rpc.beaverbuild.org",
    ])
    receipt_timeout_s: float = 30.0
    submission_channels: List[str] = field(default_factory=lambda: [
        "flashbots", "public", "bloxroute",
    ])


@dataclass
class ProfitabilityConfig:
    """11.5 -- Profitability Recheck config."""
    min_net_profit_usd: float = 0.50
    gas_buffer_pct: float = 20.0
    default_eth_price_usd: float = 2500.0
    default_gas_units: int = 350_000
    recheck_before_submit: bool = True


# ═══════════════════════════════════════════════════════════════════
#  MASTER CONFIG
# ═══════════════════════════════════════════════════════════════════

@dataclass
class TimingConfig:
    """Top-level configuration for the Timing Optimizer Engine."""
    # Sub-module configs
    oracle: OracleConfig = field(default_factory=OracleConfig)
    mempool: MempoolConfig = field(default_factory=MempoolConfig)
    predictor: PredictiveModelConfig = field(default_factory=PredictiveModelConfig)
    jit: JITExecutorConfig = field(default_factory=JITExecutorConfig)
    profitability: ProfitabilityConfig = field(default_factory=ProfitabilityConfig)

    # Flat convenience aliases (backward compat)
    oracle_poll_interval_s: float = 1.0
    oracle_stale_threshold_s: int = 3600
    mempool_enabled: bool = True
    mempool_min_impact_usd: float = 50_000.0
    ml_prediction_horizon_blocks: int = 5
    ml_min_probability: float = 0.6
    jit_pre_sign_pool_size: int = 20
    jit_submission_channels: List[str] = field(default_factory=lambda: [
        "flashbots", "public", "bloxroute",
    ])
    jit_max_priority_fee_gwei: float = 3.0
    recheck_enabled: bool = True
    recheck_min_profit_usd: float = 0.50
    cross_chain_enabled: bool = True
    rpc_endpoints: Dict[int, str] = field(default_factory=dict)
    execution_enabled: bool = True
    scan_interval_s: float = 1.0


# ═══════════════════════════════════════════════════════════════════
#  FACTORY
# ═══════════════════════════════════════════════════════════════════

_config: Optional[TimingConfig] = None


def get_timing_config() -> TimingConfig:
    """Build TimingConfig from environment variables (singleton)."""
    global _config
    if _config is not None:
        return _config

    oracle_cfg = OracleConfig(
        poll_interval_s=_env_float("ORACLE_POLL_INTERVAL_S", 1.0),
        stale_threshold_s=_env_int("ORACLE_STALE_SECONDS", 3600),
    )
    mempool_cfg = MempoolConfig(
        enabled=_env_bool("MEMPOOL_ENABLED", True),
        min_swap_impact_usd=_env_float("MEMPOOL_MIN_SWAP_IMPACT_USD", 50_000),
    )
    predictor_cfg = PredictiveModelConfig(
        confidence_threshold=_env_float("ML_MIN_PROBABILITY", 0.6),
    )
    jit_cfg = JITExecutorConfig(
        max_pre_signed_txs=_env_int("JIT_PRE_SIGN_POOL_SIZE", 20),
        max_gas_price_gwei=_env_float("JIT_MAX_PRIORITY_FEE_GWEI", 50.0),
    )
    profitability_cfg = ProfitabilityConfig(
        min_net_profit_usd=_env_float("MIN_PROFIT_USD", 0.50),
    )

    _config = TimingConfig(
        oracle=oracle_cfg,
        mempool=mempool_cfg,
        predictor=predictor_cfg,
        jit=jit_cfg,
        profitability=profitability_cfg,
        oracle_poll_interval_s=oracle_cfg.poll_interval_s,
        oracle_stale_threshold_s=oracle_cfg.stale_threshold_s,
        mempool_enabled=mempool_cfg.enabled,
        mempool_min_impact_usd=mempool_cfg.min_swap_impact_usd,
        execution_enabled=_env_bool("EXECUTION_ENABLED", True),
        scan_interval_s=_env_float("SCAN_INTERVAL_SECONDS", 1.0),
    )
    return _config
