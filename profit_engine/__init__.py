#!/usr/bin/env python3
"""
PROFIT ENGINE — Triangulated Profit Extraction System
=====================================================
Phase 1: Cold Start (zero capital flash-loan extraction)
Phase 2: Heat Map (pattern learning, opportunity frequency)
Phase 3: Capital Multiplier (exponential compounding)

Integrates with:
  - Omni-Channel Detector Arrays (mempool_radar, contract_crawler, static_analyzer, cross_chain_monitor)
  - Execution Router (liquidation_executor, arbitrage_executor, backrun_executor, cross_chain_executor)
  - ML Aggregator (quality_scorer, dynamic_router)
  - Deployed Smart Contracts (LiquidationExecutor V1/V2, FlashLoanArbitrageExecutor)
"""

from .triangulated_profit_engine import TriangulatedProfitEngine
from .capital_multiplier import CapitalMultiplier, MultiplierState
from .opportunity_scanner import OpportunityScanner, OpportunityVector
from .heat_map import HeatMap, HeatMapEntry
from .flash_loan_router import FlashLoanRouter, FlashLoanProvider
from .gas_optimizer import GasOptimizer, GasEstimate
from .profit_ledger import ProfitLedger, ProfitEntry, PhaseState
from .rpc_gateway import RPCGateway, RequestPriority, build_default_gateway
from .zero_revert_pipeline import ZeroRevertPipeline

__all__ = [
    'TriangulatedProfitEngine',
    'CapitalMultiplier', 'MultiplierState',
    'OpportunityScanner', 'OpportunityVector',
    'HeatMap', 'HeatMapEntry',
    'FlashLoanRouter', 'FlashLoanProvider',
    'GasOptimizer', 'GasEstimate',
    'ProfitLedger', 'ProfitEntry', 'PhaseState',
    'RPCGateway', 'RequestPriority', 'build_default_gateway',
    'ZeroRevertPipeline',
]
