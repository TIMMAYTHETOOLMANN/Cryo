#!/usr/bin/env python3
"""
MODULE 11 — Liquidation Timing Optimizer & Just-in-Time Execution Engine
=========================================================================
Transforms the system from reactive scanning into a preemptive execution
machine that predicts the exact block where HF crosses 1.0 and submits
a pre-signed transaction to land in that same block.

Submodules:
  11.1  OraclePriceWatcher      — Event-driven Chainlink monitoring + WS
  11.2  MempoolSniffer          — Pending TX analysis for price-impact backruns
  11.3  PredictiveHealthModel   — XGBoost probability of liquidation within N blocks
  11.4  JustInTimeExecutor      — Pre-signed TX pool, multi-channel submission
  11.5  ProfitabilityRecheck    — Last-millisecond profit verification
  11.6  CrossChainCoordinator   — Cross-chain flash loan liquidation atomics

Composes with:
  - profit_engine.jit_liquidation_engine  (v1 JIT core)
  - MODULE_10_RPC_GATEWAY                 (endpoint routing)
  - enhanced_modules.module_7_mev_strategy (Flashbots / private mempool)
"""

from .engine import TimingOptimizerEngine
from .config import TimingConfig, get_timing_config

__all__ = [
    "TimingOptimizerEngine",
    "TimingConfig",
    "get_timing_config",
]
