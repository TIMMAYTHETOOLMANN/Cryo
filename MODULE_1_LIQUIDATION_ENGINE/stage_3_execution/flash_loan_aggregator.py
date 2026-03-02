#!/usr/bin/env python3
"""
STAGE 3 — Flash Loan Aggregator (Consolidated)
================================================
Unified interface for all 6 flash loan providers.
Selects cheapest provider for each liquidation.

Providers:
  1. Aave V3      (0.05%)  — 10+ chains
  2. Balancer V2   (0%)     — ETH, Polygon, Arbitrum
  3. MakerDAO      (0%)     — ETH only, DAI only
  4. dYdX          (0%)     — ETH only (deprecated but live)
  5. Uniswap V3   (0.01-1%) — all EVM
  6. Uniswap V2   (0.3%)   — all EVM
"""

# Re-export from the production src/ module which already has full
# implementations for all 6 providers.
# This ensures the stage folder is self-documenting while avoiding
# code duplication.
try:
    from ..src.flash_loan_providers.aggregator import (
        FlashLoanAggregator,
        FlashLoanProviderType,
        FlashLoanQuote,
        FlashLoanExecutionResult,
        AaveV3FlashLoanProvider,
        UniswapV3FlashLoanProvider,
        BalancerV2FlashLoanProvider,
        MakerDAOFlashMintProvider,
        DYdXFlashLoanProvider,
        UniswapV2FlashSwapProvider,
        get_flash_loan_aggregator,
    )
except ImportError:
    # If src/ path isn't available (e.g. running standalone),
    # provide a minimal stub
    import logging
    logging.getLogger(__name__).warning(
        "Could not import FlashLoanAggregator from src/ — "
        "running in stub mode"
    )

    class FlashLoanAggregator:
        pass

    def get_flash_loan_aggregator():
        return FlashLoanAggregator()

__all__ = [
    "FlashLoanAggregator",
    "get_flash_loan_aggregator",
]
