#!/usr/bin/env python3
"""
FLASH LOAN ROUTER — Zero-Capital Flash Loan Provider Selection & Routing
==========================================================================
Routes flash loan requests to the cheapest available provider based on:
  - Fee structure (Balancer 0%, Aave 0.05%, DODO 0%, Uniswap 0.3%)
  - Available liquidity per asset per provider
  - Historical success rate per provider
  - Chain availability

Enables Phase 1 (Cold Start) by eliminating all capital requirements.
"""

import asyncio
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any, Tuple
from enum import Enum
from collections import defaultdict


class FlashLoanProvider(Enum):
    """Supported flash loan providers ordered by preference (lowest fee first)"""
    BALANCER = "balancer"          # 0% fee
    DODO = "dodo"                  # 0% fee
    AAVE_V3 = "aave_v3"           # 0.05% fee
    AAVE_V2 = "aave_v2"           # 0.09% fee
    UNISWAP_V3 = "uniswap_v3"    # 0.3% fee (via swap-back)
    MAKER = "maker"               # 0% fee (DAI only)


@dataclass
class ProviderProfile:
    """Profile for a flash loan provider"""
    provider: FlashLoanProvider
    fee_bps: float                              # Fee in basis points
    supported_chains: List[int]
    supported_assets: List[str]                 # Token symbols
    max_loan_usd: float                         # Max single loan
    pool_addresses: Dict[int, str]              # chain_id → pool address
    router_addresses: Dict[int, str] = field(default_factory=dict)
    success_rate: float = 1.0
    avg_execution_ms: float = 0.0
    total_loans: int = 0
    total_volume_usd: float = 0.0


@dataclass
class FlashLoanRoute:
    """Selected flash loan route for a trade"""
    provider: FlashLoanProvider
    chain_id: int
    asset: str
    amount_usd: float
    fee_usd: float
    fee_bps: float
    pool_address: str
    router_address: str
    calldata_template: str     # ABI-encoded template
    estimated_gas_units: int
    confidence: float          # Provider reliability 0-1


class FlashLoanRouter:
    """
    Selects the optimal flash loan provider for each trade.
    """

    def __init__(self, config: Dict[str, Any] = None):
        self.config = config or {}
        self.providers: Dict[FlashLoanProvider, ProviderProfile] = {}
        self._initialize_providers()

        # Runtime stats
        self.routes_computed = 0
        self.total_fees_saved_usd = 0.0

        print("⚡ Flash Loan Router initialized")
        print(f"   Providers: {len(self.providers)}")
        for p, profile in self.providers.items():
            print(f"     {p.value}: {profile.fee_bps}bps, chains={profile.supported_chains}")

    def _initialize_providers(self):
        """Register all flash loan providers with their profiles."""

        # ── Balancer (0% fee) ──
        self.providers[FlashLoanProvider.BALANCER] = ProviderProfile(
            provider=FlashLoanProvider.BALANCER,
            fee_bps=0.0,
            supported_chains=[1, 42161, 10, 137, 8453, 43114],
            supported_assets=['WETH', 'USDC', 'USDT', 'DAI', 'WBTC', 'BAL', 'AAVE'],
            max_loan_usd=50_000_000,
            pool_addresses={
                1: '0xBA12222222228d8Ba445958a75a0704d566BF2C8',
                42161: '0xBA12222222228d8Ba445958a75a0704d566BF2C8',
                10: '0xBA12222222228d8Ba445958a75a0704d566BF2C8',
                137: '0xBA12222222228d8Ba445958a75a0704d566BF2C8',
                8453: '0xBA12222222228d8Ba445958a75a0704d566BF2C8',
            },
        )

        # ── DODO (0% fee) ──
        self.providers[FlashLoanProvider.DODO] = ProviderProfile(
            provider=FlashLoanProvider.DODO,
            fee_bps=0.0,
            supported_chains=[1, 56, 42161, 137, 10],
            supported_assets=['WETH', 'USDC', 'USDT', 'BUSD'],
            max_loan_usd=10_000_000,
            pool_addresses={
                1: '0x5C69bEe701ef814a2B6a3EDD4B1652CB9cc5aA6f',
                56: '0x6098A5638d8D7e9Ed2f952d35B2b67c34EC6B476',
                42161: '0x6098A5638d8D7e9Ed2f952d35B2b67c34EC6B476',
            },
        )

        # ── Aave V3 (0.05% = 5 bps) ──
        self.providers[FlashLoanProvider.AAVE_V3] = ProviderProfile(
            provider=FlashLoanProvider.AAVE_V3,
            fee_bps=5.0,
            supported_chains=[1, 42161, 10, 137, 8453, 43114],
            supported_assets=['WETH', 'USDC', 'USDT', 'DAI', 'WBTC', 'LINK', 'AAVE',
                              'UNI', 'CRV', 'MKR', 'SNX', 'COMP'],
            max_loan_usd=500_000_000,
            pool_addresses={
                1: '0x87870Bca3F3fD6335C3F4ce8392D69350B4fA4E2',
                42161: '0x794a61358D6845594F94dc1DB02A252b5b4814aD',
                10: '0x794a61358D6845594F94dc1DB02A252b5b4814aD',
                137: '0x794a61358D6845594F94dc1DB02A252b5b4814aD',
                8453: '0xA238Dd80C259a72e81d7e4664a9801593F98d1c5',
            },
        )

        # ── Aave V2 (0.09% = 9 bps) ──
        self.providers[FlashLoanProvider.AAVE_V2] = ProviderProfile(
            provider=FlashLoanProvider.AAVE_V2,
            fee_bps=9.0,
            supported_chains=[1, 137],
            supported_assets=['WETH', 'USDC', 'USDT', 'DAI', 'WBTC'],
            max_loan_usd=200_000_000,
            pool_addresses={
                1: '0x7d2768dE32b0b80b7a3454c06BdAc94A69DDc7A9',
                137: '0x8dFf5E27EA6b7AC08EbFdf9eB090F32ee9a30fcf',
            },
        )

        # ── Uniswap V3 (0.3% via swap-and-back = 30 bps) ──
        self.providers[FlashLoanProvider.UNISWAP_V3] = ProviderProfile(
            provider=FlashLoanProvider.UNISWAP_V3,
            fee_bps=30.0,
            supported_chains=[1, 42161, 10, 137, 8453],
            supported_assets=['WETH', 'USDC', 'USDT', 'DAI', 'WBTC'],
            max_loan_usd=100_000_000,
            pool_addresses={
                1: '0xE592427A0AEce92De3Edee1F18E0157C05861564',
                42161: '0xE592427A0AEce92De3Edee1F18E0157C05861564',
                10: '0xE592427A0AEce92De3Edee1F18E0157C05861564',
                137: '0xE592427A0AEce92De3Edee1F18E0157C05861564',
                8453: '0x2626664c2603336E57B271c5C0b26F421741e481',
            },
        )

        # ── MakerDAO (0% DAI-only) ──
        self.providers[FlashLoanProvider.MAKER] = ProviderProfile(
            provider=FlashLoanProvider.MAKER,
            fee_bps=0.0,
            supported_chains=[1],
            supported_assets=['DAI'],
            max_loan_usd=500_000_000,
            pool_addresses={
                1: '0x1EB4CF3A948E7D72A198fe073cCb8C7a948cD853',  # DssFlash
            },
        )

    # ──────────────────────────────────────────────
    # ROUTE SELECTION
    # ──────────────────────────────────────────────

    def find_best_route(
        self,
        chain_id: int,
        asset: str,
        amount_usd: float,
        gross_profit_usd: float,
    ) -> Optional[FlashLoanRoute]:
        """
        Find the cheapest flash loan route for the given parameters.
        Returns None if no provider can serve the request.
        """
        candidates: List[Tuple[float, FlashLoanRoute]] = []

        for provider, profile in self.providers.items():
            # Check chain support
            if chain_id not in profile.supported_chains:
                continue
            # Check asset support
            if asset.upper() not in [a.upper() for a in profile.supported_assets]:
                continue
            # Check amount limit
            if amount_usd > profile.max_loan_usd:
                continue
            # Check pool exists for this chain
            pool_addr = profile.pool_addresses.get(chain_id)
            if not pool_addr:
                continue

            fee_usd = amount_usd * (profile.fee_bps / 10_000)

            # Only consider if fee doesn't eat the entire profit
            if fee_usd >= gross_profit_usd * 0.5:
                continue

            route = FlashLoanRoute(
                provider=provider,
                chain_id=chain_id,
                asset=asset.upper(),
                amount_usd=amount_usd,
                fee_usd=round(fee_usd, 2),
                fee_bps=profile.fee_bps,
                pool_address=pool_addr,
                router_address=profile.router_addresses.get(chain_id, pool_addr),
                calldata_template=self._build_calldata_template(provider, chain_id),
                estimated_gas_units=self._estimate_gas(provider),
                confidence=profile.success_rate,
            )

            # Score = fee + (1 - success_rate) penalty
            score = fee_usd + (1 - profile.success_rate) * gross_profit_usd * 0.1
            candidates.append((score, route))

        if not candidates:
            return None

        # Sort by score (lowest first)
        candidates.sort(key=lambda x: x[0])
        best_route = candidates[0][1]

        # Track savings vs worst option
        if len(candidates) > 1:
            worst_fee = candidates[-1][1].fee_usd
            self.total_fees_saved_usd += worst_fee - best_route.fee_usd

        self.routes_computed += 1
        return best_route

    def find_multi_provider_route(
        self,
        chain_id: int,
        assets: List[str],
        amounts_usd: List[float],
        gross_profit_usd: float,
    ) -> List[FlashLoanRoute]:
        """
        Find routes for multiple assets (e.g., triangular arb needing
        flash loans in different tokens).
        """
        routes = []
        for asset, amount in zip(assets, amounts_usd):
            route = self.find_best_route(chain_id, asset, amount, gross_profit_usd)
            if route:
                routes.append(route)
        return routes

    # ──────────────────────────────────────────────
    # CALLDATA HELPERS
    # ──────────────────────────────────────────────

    def _build_calldata_template(self, provider: FlashLoanProvider, chain_id: int) -> str:
        """Return function selector template for the provider."""
        templates = {
            FlashLoanProvider.BALANCER: '0x5c38449e',    # flashLoan(...)
            FlashLoanProvider.DODO: '0xd0a4f185',        # flashLoan(...)
            FlashLoanProvider.AAVE_V3: '0x42b0b77c',     # flashLoanSimple(...)
            FlashLoanProvider.AAVE_V2: '0xab9c4b5d',     # flashLoan(...)
            FlashLoanProvider.UNISWAP_V3: '0x128acb08',  # swap(...)
            FlashLoanProvider.MAKER: '0x0a5ea466',       # flashLoan(...)
        }
        return templates.get(provider, '0x')

    def _estimate_gas(self, provider: FlashLoanProvider) -> int:
        """Estimate gas units for the flash loan itself (excluding trade logic)."""
        estimates = {
            FlashLoanProvider.BALANCER: 120_000,
            FlashLoanProvider.DODO: 100_000,
            FlashLoanProvider.AAVE_V3: 150_000,
            FlashLoanProvider.AAVE_V2: 180_000,
            FlashLoanProvider.UNISWAP_V3: 200_000,
            FlashLoanProvider.MAKER: 100_000,
        }
        return estimates.get(provider, 150_000)

    # ──────────────────────────────────────────────
    # FEEDBACK
    # ──────────────────────────────────────────────

    def report_outcome(self, provider: FlashLoanProvider, success: bool, volume_usd: float):
        """Update provider stats after a completed flash loan."""
        profile = self.providers.get(provider)
        if not profile:
            return
        profile.total_loans += 1
        profile.total_volume_usd += volume_usd
        # Exponential moving average for success rate
        alpha = 0.05
        profile.success_rate = (1 - alpha) * profile.success_rate + alpha * (1.0 if success else 0.0)

    # ──────────────────────────────────────────────
    # STATUS
    # ──────────────────────────────────────────────

    def status(self) -> Dict[str, Any]:
        return {
            'routes_computed': self.routes_computed,
            'total_fees_saved_usd': round(self.total_fees_saved_usd, 2),
            'providers': {
                p.value: {
                    'fee_bps': prof.fee_bps,
                    'success_rate': round(prof.success_rate, 4),
                    'total_loans': prof.total_loans,
                    'total_volume': round(prof.total_volume_usd, 2),
                }
                for p, prof in self.providers.items()
            }
        }
