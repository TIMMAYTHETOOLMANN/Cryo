#!/usr/bin/env python3
"""
MODULE 1 — src/flash_loan_providers/aggregator.py
===================================================
Production-grade Flash Loan Aggregator.
Provides a unified interface to all six flash loan sources and automatically
selects the cheapest provider for each liquidation opportunity.

Providers (sorted cheapest to most expensive):
  1. Balancer V2    (0%)     — Ethereum, Polygon, Arbitrum
  2. MakerDAO       (0%)     — Ethereum only, DAI only
  3. dYdX           (0%)     — Ethereum only (deprecated, still live)
  4. Aave V3        (0.05%)  — 10+ chains
  5. Uniswap V3     (0.01–1%) — all EVM (fee depends on pool tier)
  6. Uniswap V2     (0.3%)   — all EVM

Zero capital required — flash loans are repaid in the same transaction.
"""

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional

from ..config_manager import ConfigManager, get_config

logger = logging.getLogger(__name__)


# ============================================================================
# ENUMS & DATA MODELS
# ============================================================================

class FlashLoanProviderType(Enum):
    """Supported flash loan provider identifiers."""
    AAVE_V3       = "aave_v3"
    AAVE_V2       = "aave_v2"
    BALANCER_V2   = "balancer_v2"
    MAKER_DAO     = "maker_dao"
    DYDX          = "dydx"
    UNISWAP_V3    = "uniswap_v3"
    UNISWAP_V2    = "uniswap_v2"


@dataclass
class FlashLoanQuote:
    """Quote from a single flash loan provider."""
    provider: FlashLoanProviderType
    provider_address: str
    asset: str
    amount: int            # wei
    fee_wei: int           # fee in wei
    fee_usd: float         # fee in USD (approximate)
    fee_bps: float         # fee in basis points (e.g. 5 = 0.05%)
    available: bool = True  # False if the provider lacks liquidity
    notes: str = ""


@dataclass
class FlashLoanExecutionResult:
    """Result of a flash loan execution."""
    success: bool
    provider: FlashLoanProviderType
    asset: str
    amount_borrowed: int   # wei
    fee_paid_wei: int
    tx_hash: Optional[str] = None
    error: Optional[str] = None


# ============================================================================
# PROVIDER FEE CONSTANTS
# ============================================================================

# Annualised fee rates (fractional). E.g. 0.0005 = 0.05%.
_PROVIDER_FEES: Dict[FlashLoanProviderType, float] = {
    FlashLoanProviderType.BALANCER_V2: 0.0,
    FlashLoanProviderType.MAKER_DAO:   0.0,
    FlashLoanProviderType.DYDX:        0.0,
    FlashLoanProviderType.AAVE_V3:     0.0005,
    FlashLoanProviderType.AAVE_V2:     0.0009,
    FlashLoanProviderType.UNISWAP_V3:  0.003,    # worst-case 0.3% pool
    FlashLoanProviderType.UNISWAP_V2:  0.003,
}

# Providers in cheapest-first order (ties broken by reliability).
_PROVIDER_PRIORITY: List[FlashLoanProviderType] = [
    FlashLoanProviderType.BALANCER_V2,
    FlashLoanProviderType.MAKER_DAO,
    FlashLoanProviderType.DYDX,
    FlashLoanProviderType.AAVE_V3,
    FlashLoanProviderType.AAVE_V2,
    FlashLoanProviderType.UNISWAP_V3,
    FlashLoanProviderType.UNISWAP_V2,
]

# Chains each provider supports.  Key = chain ID, value = provider contract address.
_PROVIDER_CHAIN_MAP: Dict[FlashLoanProviderType, Dict[int, str]] = {
    FlashLoanProviderType.AAVE_V3: {
        1:     "0x87870Bca3F3fD6335C3F4ce8392D69350B4fA4E2",
        42161: "0x794a61358D6845594F94dc1DB02A252b5b4814aD",
        10:    "0x794a61358D6845594F94dc1DB02A252b5b4814aD",
        8453:  "0xA238Dd80C259a72e81d7e4664a9801593F98d1c5",
        137:   "0x794a61358D6845594F94dc1DB02A252b5b4814aD",
        43114: "0x794a61358D6845594F94dc1DB02A252b5b4814aD",
    },
    FlashLoanProviderType.AAVE_V2: {
        1: "0x7d2768dE32b0b80b7a3454c06BdAc94A69DDc7A9",
    },
    FlashLoanProviderType.BALANCER_V2: {
        1:     "0xBA12222222228d8Ba445958a75a0704d566BF2C8",
        42161: "0xBA12222222228d8Ba445958a75a0704d566BF2C8",
        137:   "0xBA12222222228d8Ba445958a75a0704d566BF2C8",
    },
    FlashLoanProviderType.MAKER_DAO: {
        1: "0x60744434d6339a6B27d73d9Eda62b6F66a0a04FA",  # DssFlash
    },
    FlashLoanProviderType.DYDX: {
        1: "0x1E0447b19BB6EcFdAe1e4AE1694b0C3659614e4e",  # Solo margin
    },
    FlashLoanProviderType.UNISWAP_V3: {
        1:     "0x1F98431c8aD98523631AE4a59f267346ea31F984",  # Factory
        42161: "0x1F98431c8aD98523631AE4a59f267346ea31F984",
        10:    "0x1F98431c8aD98523631AE4a59f267346ea31F984",
        8453:  "0x33128a8fC17869897dcE68Ed026d694621f6FDfD",
        137:   "0x1F98431c8aD98523631AE4a59f267346ea31F984",
    },
    FlashLoanProviderType.UNISWAP_V2: {
        1:     "0x5C69bEe701ef814a2B6a3EDD4B1652CB9cc5aA6f",
        42161: "0xf1D7CC64Fb4452F05c498126312eBE29f30Fbcf9",
        10:    "0x0c3c1c532F1e39EdF36BE9Fe0bE1410313E074Bf",
        8453:  "0x8909Dc15e40173Ff4699343b6eB8132c65e18eC6",
        137:   "0x9e5A52f57b3038F1B8EeE45F28b3C1967e22799C",
    },
}

# MakerDAO flash mint only supports DAI.
_MAKER_DAI = "0x6B175474E89094C44Da98b954EedeAC495271d0F"


# ============================================================================
# TOKEN DECIMAL CONSTANTS
# ============================================================================

# Well-known 18-decimal token addresses (lowercase)
_DECIMALS_18_TOKENS = {
    "0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2",   # WETH
    "0x2260fac5e5542a773aa44fbcfedf7c193bc2c599",   # WBTC (8 dec – handled in 6-dec set below)
}

# Well-known 6-decimal token addresses (lowercase)
_DECIMALS_6_TOKENS = {
    "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48",   # USDC
    "0xdac17f958d2ee523a2206206994597c13d831ec7",   # USDT
    "0x6b175474e89094c44da98b954eedeac495271d0f",   # DAI (18 dec but treat as stablecoin)
}

_DECIMALS_18 = int(1e18)
_DECIMALS_6  = int(1e6)


def _token_decimals(asset_address: str) -> int:
    """Return the token decimal unit for a known asset, defaulting to 1e18."""
    addr = asset_address.lower()
    if addr in _DECIMALS_6_TOKENS:
        return _DECIMALS_6
    return _DECIMALS_18




class _BaseProvider:
    """Base class shared by all flash loan providers."""

    provider_type: FlashLoanProviderType
    fee_rate: float

    def __init__(self, chain_id: int):
        self.chain_id = chain_id
        self._address = _PROVIDER_CHAIN_MAP.get(self.provider_type, {}).get(chain_id)

    @property
    def is_available_on_chain(self) -> bool:
        return self._address is not None

    def quote(self, asset: str, amount: int, eth_price_usd: float = 2500.0) -> FlashLoanQuote:
        fee_wei  = int(amount * self.fee_rate)
        decimals = _token_decimals(asset)
        # Convert fee_wei to USD using eth_price_usd for 18-dec tokens, 1:1 for stablecoins
        if decimals == _DECIMALS_6:
            fee_usd = fee_wei / _DECIMALS_6       # USDC/USDT: 1 token = ~$1
        else:
            fee_usd = (fee_wei / _DECIMALS_18) * eth_price_usd
        return FlashLoanQuote(
            provider=self.provider_type,
            provider_address=self._address or "",
            asset=asset,
            amount=amount,
            fee_wei=fee_wei,
            fee_usd=fee_usd,
            fee_bps=self.fee_rate * 10_000,
            available=self.is_available_on_chain,
        )


class AaveV3FlashLoanProvider(_BaseProvider):
    """Aave V3 flashLoanSimple / flashLoan — 0.05% fee, 10+ chains."""
    provider_type = FlashLoanProviderType.AAVE_V3
    fee_rate = 0.0005


class AaveV2FlashLoanProvider(_BaseProvider):
    """Aave V2 flashLoan — 0.09% fee, Ethereum mainnet only."""
    provider_type = FlashLoanProviderType.AAVE_V2
    fee_rate = 0.0009


class BalancerV2FlashLoanProvider(_BaseProvider):
    """Balancer V2 Vault flashLoan — 0% fee, Ethereum / Polygon / Arbitrum."""
    provider_type = FlashLoanProviderType.BALANCER_V2
    fee_rate = 0.0


class MakerDAOFlashMintProvider(_BaseProvider):
    """MakerDAO DssFlash flash mint — 0% fee, Ethereum only, DAI only."""
    provider_type = FlashLoanProviderType.MAKER_DAO
    fee_rate = 0.0

    def quote(self, asset: str, amount: int, eth_price_usd: float = 2500.0) -> FlashLoanQuote:
        if asset.lower() != _MAKER_DAI.lower():
            return FlashLoanQuote(
                provider=self.provider_type,
                provider_address="",
                asset=asset,
                amount=amount,
                fee_wei=0,
                fee_usd=0.0,
                fee_bps=0.0,
                available=False,
                notes="MakerDAO flash mint only supports DAI",
            )
        return super().quote(asset, amount, eth_price_usd)


class DYdXFlashLoanProvider(_BaseProvider):
    """dYdX Solo Margin flash loan — 0% fee, Ethereum only (deprecated but live)."""
    provider_type = FlashLoanProviderType.DYDX
    fee_rate = 0.0


class UniswapV3FlashLoanProvider(_BaseProvider):
    """Uniswap V3 flash swap — fee depends on pool tier (0.01%–1%)."""
    provider_type = FlashLoanProviderType.UNISWAP_V3
    fee_rate = 0.003  # worst-case 0.3% (most conservative estimate)


class UniswapV2FlashSwapProvider(_BaseProvider):
    """Uniswap V2 flash swap — 0.3% fee."""
    provider_type = FlashLoanProviderType.UNISWAP_V2
    fee_rate = 0.003


# Mapping from enum → provider class.
_PROVIDER_CLASSES = {
    FlashLoanProviderType.AAVE_V3:     AaveV3FlashLoanProvider,
    FlashLoanProviderType.AAVE_V2:     AaveV2FlashLoanProvider,
    FlashLoanProviderType.BALANCER_V2: BalancerV2FlashLoanProvider,
    FlashLoanProviderType.MAKER_DAO:   MakerDAOFlashMintProvider,
    FlashLoanProviderType.DYDX:        DYdXFlashLoanProvider,
    FlashLoanProviderType.UNISWAP_V3:  UniswapV3FlashLoanProvider,
    FlashLoanProviderType.UNISWAP_V2:  UniswapV2FlashSwapProvider,
}


# ============================================================================
# AGGREGATOR
# ============================================================================

class FlashLoanAggregator:
    """
    Selects the cheapest available flash loan provider for each opportunity.

    Usage::

        agg = FlashLoanAggregator(chain_id=1)
        best = agg.best_quote(asset=USDC, amount=10_000 * 1e6)
        print(best.provider, best.fee_usd)

        all_quotes = agg.all_quotes(asset=WETH, amount=5e18)
    """

    def __init__(self, chain_id: int = 1, config: Optional[ConfigManager] = None):
        self.chain_id = chain_id
        self.config = config or get_config()
        self._providers: Dict[FlashLoanProviderType, _BaseProvider] = {
            pt: cls(chain_id) for pt, cls in _PROVIDER_CLASSES.items()
        }

    def all_quotes(
        self,
        asset: str,
        amount: int,
        eth_price_usd: float = 2500.0,
    ) -> List[FlashLoanQuote]:
        """Return quotes from all providers, sorted cheapest fee first."""
        quotes = [
            p.quote(asset, amount, eth_price_usd)
            for p in self._providers.values()
        ]
        return sorted(quotes, key=lambda q: q.fee_wei)

    def best_quote(
        self,
        asset: str,
        amount: int,
        eth_price_usd: float = 2500.0,
    ) -> Optional[FlashLoanQuote]:
        """Return the cheapest available quote, or None if no provider supports the chain/asset."""
        available = [q for q in self.all_quotes(asset, amount, eth_price_usd) if q.available]
        return available[0] if available else None

    def quote_for(
        self,
        provider: FlashLoanProviderType,
        asset: str,
        amount: int,
        eth_price_usd: float = 2500.0,
    ) -> FlashLoanQuote:
        """Get a quote from a specific provider."""
        p = self._providers.get(provider)
        if p is None:
            raise ValueError(f"Unknown provider: {provider}")
        return p.quote(asset, amount, eth_price_usd)

    def available_providers(self) -> List[FlashLoanProviderType]:
        """List of providers that have a deployment on this chain."""
        return [pt for pt, p in self._providers.items() if p.is_available_on_chain]

    def cheapest_fee_rate(self) -> float:
        """Return the cheapest available fee rate on this chain."""
        available = self.available_providers()
        if not available:
            return 0.0
        return min(_PROVIDER_FEES[pt] for pt in available)


# ============================================================================
# SINGLETON
# ============================================================================

_aggregators: Dict[int, FlashLoanAggregator] = {}


def get_flash_loan_aggregator(chain_id: int = 1) -> FlashLoanAggregator:
    """Return (or create) the singleton aggregator for a given chain."""
    if chain_id not in _aggregators:
        _aggregators[chain_id] = FlashLoanAggregator(chain_id=chain_id)
    return _aggregators[chain_id]


__all__ = [
    "FlashLoanProviderType",
    "FlashLoanQuote",
    "FlashLoanExecutionResult",
    "FlashLoanAggregator",
    "AaveV3FlashLoanProvider",
    "AaveV2FlashLoanProvider",
    "BalancerV2FlashLoanProvider",
    "MakerDAOFlashMintProvider",
    "DYdXFlashLoanProvider",
    "UniswapV3FlashLoanProvider",
    "UniswapV2FlashSwapProvider",
    "get_flash_loan_aggregator",
]
