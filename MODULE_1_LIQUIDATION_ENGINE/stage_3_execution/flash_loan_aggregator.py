#!/usr/bin/env python3
"""
STAGE 3 — Flash Loan Aggregator (Consolidated + Enhanced)
===========================================================
Unified interface for all 6+ flash loan providers with intelligent
fallback routing and obscure-token swap support.

Providers:
  1. Aave V3      (0.05%)  — 10+ chains
  2. Balancer V2   (0%)     — ETH, Polygon, Arbitrum (Vault flash loans)
  3. MakerDAO      (0%)     — ETH only, DAI flash mint
  4. dYdX          (0%)     — ETH only (deprecated but live)
  5. Uniswap V3   (0.01-1%) — all EVM
  6. Uniswap V2   (0.3%)   — all EVM

Fallback Strategy (Module 3 Enhancement):
  - If debt asset not available for flash borrowing (obscure token),
    borrow a common asset (USDC/WETH) and swap via DEX in same TX
  - Provider fallback: if cheapest provider reverts, cascade to next
  - Amount calculation: borrow exactly debt_amount of debt asset
"""

import logging
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass
from enum import Enum

logger = logging.getLogger(__name__)

# Re-export from the production src/ module which already has full
# implementations for all 6 providers.
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
    _SRC_AVAILABLE = True
except ImportError:
    _SRC_AVAILABLE = False
    logger.warning(
        "Could not import FlashLoanAggregator from src/ — running in enhanced stub mode"
    )


# ---------------------------------------------------------------------------
# Common assets for flash loan fallback (when debt asset isn't borrowable)
# ---------------------------------------------------------------------------

class CommonAsset(Enum):
    """Widely available flash loan assets for swap-and-repay fallback."""
    USDC = "usdc"
    WETH = "weth"
    DAI = "dai"
    USDT = "usdt"


# Addresses per chain for common fallback assets
COMMON_ASSET_ADDRESSES: Dict[int, Dict[CommonAsset, str]] = {
    1: {
        CommonAsset.USDC: "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48",
        CommonAsset.WETH: "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2",
        CommonAsset.DAI:  "0x6B175474E89094C44Da98b954EedeAC495271d0F",
        CommonAsset.USDT: "0xdAC17F958D2ee523a2206206994597C13D831ec7",
    },
    42161: {
        CommonAsset.USDC: "0xaf88d065e77c8cC2239327C5EDb3A432268e5831",
        CommonAsset.WETH: "0x82aF49447D8a07e3bd95BD0d56f35241523fBab1",
        CommonAsset.DAI:  "0xDA10009cBd5D07dd0CeCc66161FC93D7c9000da1",
        CommonAsset.USDT: "0xFd086bC7CD5C481DCC9C85ebE478A1C0b69FCbb9",
    },
    10: {
        CommonAsset.USDC: "0x0b2C639c533813f4Aa9D7837CAf62653d097Ff85",
        CommonAsset.WETH: "0x4200000000000000000000000000000000000006",
        CommonAsset.DAI:  "0xDA10009cBd5D07dd0CeCc66161FC93D7c9000da1",
    },
    8453: {
        CommonAsset.USDC: "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913",
        CommonAsset.WETH: "0x4200000000000000000000000000000000000006",
    },
    137: {
        CommonAsset.USDC: "0x3c499c542cEF5E3811e1192ce70d8cC03d5c3359",
        CommonAsset.WETH: "0x7ceB23fD6bC0adD59E62ac25578270cFf1b9f619",
        CommonAsset.DAI:  "0x8f3Cf7ad23Cd3CaDbD9735AFf958023239c6A063",
    },
}

# Known flashloanable assets per provider (top tokens on major chains)
PROVIDER_SUPPORTED_ASSETS: Dict[str, List[CommonAsset]] = {
    "aave_v3":     [CommonAsset.USDC, CommonAsset.WETH, CommonAsset.DAI, CommonAsset.USDT],
    "balancer_v2": [CommonAsset.WETH, CommonAsset.DAI, CommonAsset.USDC],
    "maker_flash": [CommonAsset.DAI],
    "dydx":        [CommonAsset.WETH, CommonAsset.USDC, CommonAsset.DAI],
    "uniswap_v3":  [CommonAsset.USDC, CommonAsset.WETH, CommonAsset.USDT],
    "uniswap_v2":  [CommonAsset.WETH, CommonAsset.USDC],
}


@dataclass
class FlashLoanRoute:
    """Complete flash loan route including optional intermediate swap."""
    provider: str
    borrow_asset: str            # Address of asset to borrow
    borrow_amount: int           # Amount to borrow (in borrow asset units)
    fee_bps: int                 # Provider fee in basis points
    needs_swap: bool = False     # True if borrowed asset ≠ debt asset
    swap_from: str = ""          # Intermediate asset address (if swap needed)
    swap_to: str = ""            # Target debt asset address (if swap needed)
    swap_router: str = ""        # DEX router for the swap
    estimated_swap_slippage: float = 0.005


@dataclass
class FallbackResult:
    """Result of fallback route computation."""
    routes: List[FlashLoanRoute]
    best_route: Optional[FlashLoanRoute] = None
    reason: str = ""


def compute_fallback_route(
    debt_asset: str,
    debt_amount: int,
    chain_id: int = 1,
    available_providers: Optional[List[str]] = None,
) -> FallbackResult:
    """
    Compute flash loan route with swap fallback for obscure tokens.

    If the debt asset can be directly flash-borrowed, returns a direct route.
    If not (obscure token), borrows a common asset (USDC/WETH) and includes
    a DEX swap instruction for the executor to handle in the same TX.

    Args:
        debt_asset: Address of the debt token to repay
        debt_amount: Amount needed (in token units)
        chain_id: Chain ID
        available_providers: Override provider list

    Returns:
        FallbackResult with ordered route options
    """
    providers = available_providers or list(PROVIDER_SUPPORTED_ASSETS.keys())
    chain_assets = COMMON_ASSET_ADDRESSES.get(chain_id, {})
    routes: List[FlashLoanRoute] = []

    # Known fee schedule (same as Module 2)
    fee_map = {
        "aave_v3": 5, "balancer_v2": 0, "maker_flash": 0,
        "dydx": 0, "uniswap_v3": 30, "uniswap_v2": 30,
    }

    # Check if debt_asset itself is a known common asset
    debt_is_common = any(
        addr.lower() == debt_asset.lower()
        for addr in chain_assets.values()
    )

    if debt_is_common:
        # Direct flash loan — cheapest providers first
        for prov in sorted(providers, key=lambda p: fee_map.get(p, 30)):
            routes.append(FlashLoanRoute(
                provider=prov,
                borrow_asset=debt_asset,
                borrow_amount=debt_amount,
                fee_bps=fee_map.get(prov, 30),
                needs_swap=False,
            ))
    else:
        # Obscure token — fallback to common asset + swap
        logger.info(
            "🔄 Debt asset %s not directly borrowable — computing swap fallback",
            debt_asset[:12],
        )
        for common_asset_enum, common_addr in chain_assets.items():
            for prov in sorted(providers, key=lambda p: fee_map.get(p, 30)):
                supported = PROVIDER_SUPPORTED_ASSETS.get(prov, [])
                if common_asset_enum not in supported:
                    continue
                routes.append(FlashLoanRoute(
                    provider=prov,
                    borrow_asset=common_addr,
                    borrow_amount=debt_amount,  # Will need amount adjustment for price diff
                    fee_bps=fee_map.get(prov, 30),
                    needs_swap=True,
                    swap_from=common_addr,
                    swap_to=debt_asset,
                    estimated_swap_slippage=0.005,
                ))

    # Sort by total cost (fee + swap slippage)
    routes.sort(key=lambda r: r.fee_bps + (50 if r.needs_swap else 0))

    return FallbackResult(
        routes=routes,
        best_route=routes[0] if routes else None,
        reason="direct" if debt_is_common else "swap_fallback",
    )


# Provide the enhanced classes alongside src/ re-exports
if not _SRC_AVAILABLE:
    class FlashLoanAggregator:
        """Stub aggregator when src/ is not available."""
        def __init__(self):
            self._routes = {}

        def get_route(self, debt_asset: str, debt_amount: int,
                      chain_id: int = 1) -> FallbackResult:
            return compute_fallback_route(debt_asset, debt_amount, chain_id)

    def get_flash_loan_aggregator():
        return FlashLoanAggregator()


__all__ = [
    "FlashLoanAggregator",
    "get_flash_loan_aggregator",
    "FlashLoanRoute",
    "FallbackResult",
    "CommonAsset",
    "COMMON_ASSET_ADDRESSES",
    "compute_fallback_route",
]
