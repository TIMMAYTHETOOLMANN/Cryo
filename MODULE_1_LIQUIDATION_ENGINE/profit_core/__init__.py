#!/usr/bin/env python3
"""
PROFIT CORE — Integrated Profit Mechanics for MODULE 1
=======================================================
Consolidated from the former top-level ``profit_engine/`` package.

Contains:
  - opportunity_scanner   → 6-vector detection engine (feeds Stage 1)
  - flash_loan_router     → Zero-capital provider selection (feeds Stage 3)
  - gas_optimizer         → Dynamic gas prediction (feeds Stage 2/3)
  - zero_revert_pipeline  → Oracle-reactive precision execution (Stage 3)
  - heat_map              → Pattern learning (feeds Stage 7)
  - profit_ledger         → P&L tracking (feeds Stage 6)
  - capital_multiplier    → Exponential compounding (feeds Stage 6)
"""

from .flash_loan_router import FlashLoanRouter, FlashLoanProvider
from .gas_optimizer import GasOptimizer, GasEstimate
from .heat_map import HeatMap, HeatMapEntry
from .profit_ledger import ProfitLedger, ProfitEntry, PhaseState
from .capital_multiplier import CapitalMultiplier, MultiplierState
from .zero_revert_pipeline import ZeroRevertPipeline, MempoolSniffer
from .opportunity_scanner import OpportunityScanner, OpportunityVector
from .triangulated_profit_engine import TriangulatedProfitEngine, build_default_config

__all__ = [
    'FlashLoanRouter', 'FlashLoanProvider',
    'GasOptimizer', 'GasEstimate',
    'HeatMap', 'HeatMapEntry',
    'ProfitLedger', 'ProfitEntry', 'PhaseState',
    'CapitalMultiplier', 'MultiplierState',
    'ZeroRevertPipeline', 'MempoolSniffer',
    'OpportunityScanner', 'OpportunityVector',
    'TriangulatedProfitEngine', 'build_default_config',
]
