#!/usr/bin/env python3
"""
enhanced_modules.module_2_profitability_calculator.flash_loan_fee_minimizer
============================================================================
Dynamically selects the cheapest flash loan provider (or combination)
for each liquidation, including split-loan optimization across multiple
zero-fee and low-fee providers.

Provider Fee Schedule (as of 2026):
  - Balancer V2:  0 bps (truly free)
  - DODO:         0 bps (truly free)
  - Maker DssFlash: 0 bps (for DAI only)
  - Aave V3:      5 bps (0.05%)
  - Uniswap V3:   30 bps (0.3% — via flash swap)
  - dYdX:         2 bps (0.02%)

The minimizer evaluates:
  1. Direct borrow (if asset is available on a zero-fee provider)
  2. Borrow + swap (borrow USDC free, swap to debt asset)
  3. Split borrow (part from zero-fee, remainder from low-fee)
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Dict, List, Optional, Tuple

from enhanced_modules.common.base import EnhancedModule
from enhanced_modules.common.models import FlashLoanProviderType, ProviderProfile

logger = logging.getLogger(__name__)


@dataclass
class FlashLoanQuote:
    """A flash loan quote from a single provider."""
    provider: FlashLoanProviderType
    asset: str
    amount_usd: Decimal
    fee_usd: Decimal
    fee_bps: int
    gas_overhead: int
    gas_cost_usd: Decimal
    total_cost_usd: Decimal
    available_liquidity_usd: Decimal
    requires_swap: bool = False
    swap_cost_usd: Decimal = Decimal("0")
    confidence: float = 1.0

    @property
    def effective_cost_usd(self) -> Decimal:
        return self.total_cost_usd + self.swap_cost_usd


@dataclass
class SplitLoanPlan:
    """A plan that splits the loan across multiple providers."""
    quotes: List[FlashLoanQuote]
    total_amount_usd: Decimal
    total_fee_usd: Decimal
    total_gas_usd: Decimal
    total_cost_usd: Decimal
    provider_count: int
    is_split: bool

    @property
    def avg_fee_bps(self) -> float:
        if self.total_amount_usd == 0:
            return 0
        return float(self.total_fee_usd / self.total_amount_usd * 10000)


@dataclass
class ProviderRegistry:
    """Registry of available flash loan providers per chain."""
    chain_id: int
    providers: List[ProviderProfile] = field(default_factory=list)
    last_updated: float = field(default_factory=time.time)

    def get_by_asset(self, asset: str) -> List[ProviderProfile]:
        return [
            p for p in self.providers
            if p.is_active and (asset in p.supported_assets or "*" in p.supported_assets)
        ]

    def get_zero_fee(self) -> List[ProviderProfile]:
        return [p for p in self.providers if p.is_active and p.fee_bps == 0]

    def get_sorted_by_fee(self, asset: str) -> List[ProviderProfile]:
        providers = self.get_by_asset(asset)
        return sorted(providers, key=lambda p: (p.fee_bps, -p.avg_success_rate))


# ── Default Provider Profiles ─────────────────────────────────────

def _default_providers(chain_id: int = 1) -> List[ProviderProfile]:
    """Default provider profiles for Ethereum mainnet."""
    return [
        ProviderProfile(
            provider_type=FlashLoanProviderType.BALANCER_V2,
            chain_id=chain_id,
            address="0xBA12222222228d8Ba445958a75a0704d566BF2C8",
            fee_bps=0,
            max_loan_usd=Decimal("200000000"),
            supported_assets=["WETH", "USDC", "DAI", "USDT", "WBTC", "wstETH"],
            avg_success_rate=0.98,
            avg_gas_overhead=180000,
        ),
        ProviderProfile(
            provider_type=FlashLoanProviderType.DODO,
            chain_id=chain_id,
            address="0x335aC99bb3E51BDbF22025f092Ebc1Cf2c5cC498",
            fee_bps=0,
            max_loan_usd=Decimal("50000000"),
            supported_assets=["USDC", "USDT", "DAI", "WETH"],
            avg_success_rate=0.95,
            avg_gas_overhead=200000,
        ),
        ProviderProfile(
            provider_type=FlashLoanProviderType.MAKER_DSS,
            chain_id=chain_id,
            address="0x1EB4CF3A948E7D72A198fe073cCb8C7a948cD853",
            fee_bps=0,
            max_loan_usd=Decimal("500000000"),
            supported_assets=["DAI"],
            avg_success_rate=0.99,
            avg_gas_overhead=150000,
        ),
        ProviderProfile(
            provider_type=FlashLoanProviderType.AAVE_V3,
            chain_id=chain_id,
            address="0x87870Bca3F3fD6335C3F4ce8392D69350B4fA4E2",
            fee_bps=5,
            max_loan_usd=Decimal("1000000000"),
            supported_assets=["*"],  # Supports all listed assets
            avg_success_rate=0.99,
            avg_gas_overhead=200000,
        ),
        ProviderProfile(
            provider_type=FlashLoanProviderType.DYDX,
            chain_id=chain_id,
            address="0x1E0447b19BB6EcFdAe1e4AE1694b0C3659614e4e",
            fee_bps=2,
            max_loan_usd=Decimal("100000000"),
            supported_assets=["WETH", "USDC", "DAI"],
            avg_success_rate=0.97,
            avg_gas_overhead=250000,
        ),
        ProviderProfile(
            provider_type=FlashLoanProviderType.UNISWAP_V3,
            chain_id=chain_id,
            address="0xE592427A0AEce92De3Edee1F18E0157C05861564",
            fee_bps=30,
            max_loan_usd=Decimal("500000000"),
            supported_assets=["*"],
            avg_success_rate=0.97,
            avg_gas_overhead=170000,
        ),
    ]


class FlashLoanFeeMinimizer(EnhancedModule):
    """
    Minimizes flash loan costs by selecting optimal providers,
    routing through zero-fee providers when possible, and splitting
    loans across multiple sources.
    """

    # Swap cost estimate for borrow-and-swap strategy (bps)
    SWAP_COST_BPS = 30
    # Gas price estimate for cost calculations (gwei)
    DEFAULT_GAS_PRICE_GWEI = 30.0
    # ETH price for gas cost conversion
    DEFAULT_ETH_PRICE_USD = 3500.0

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        super().__init__("flash_loan_fee_minimizer", config)
        self._registries: Dict[int, ProviderRegistry] = {}
        self._optimization_history: List[Dict[str, Any]] = []

    async def _on_start(self) -> None:
        # Initialize default registries for common chains
        for chain_id in [1, 10, 42161, 8453, 137]:  # ETH, OP, Arb, Base, Polygon
            self._registries[chain_id] = ProviderRegistry(
                chain_id=chain_id,
                providers=_default_providers(chain_id),
            )
        logger.info(
            "[FeeMinimizer] Initialized with %d chain registries",
            len(self._registries),
        )

    async def _on_stop(self) -> None:
        if self._optimization_history:
            avg_savings = sum(
                h.get("savings_bps", 0) for h in self._optimization_history[-100:]
            ) / min(100, len(self._optimization_history))
            logger.info("[FeeMinimizer] Avg fee savings: %.1f bps", avg_savings)

    # ── Core Optimization ──────────────────────────────────────

    async def find_cheapest(
        self,
        debt_asset: str,
        amount_usd: Decimal,
        chain_id: int = 1,
        gas_price_gwei: Optional[float] = None,
        eth_price_usd: Optional[float] = None,
    ) -> SplitLoanPlan:
        """
        Find the cheapest flash loan route for a given debt asset and amount.

        Evaluates:
          1. Direct borrow from zero-fee provider
          2. Borrow stablecoin (free) + swap
          3. Split across multiple providers
          4. Direct borrow from lowest-fee provider
        """
        gas_price = gas_price_gwei or self.DEFAULT_GAS_PRICE_GWEI
        eth_price = eth_price_usd or self.DEFAULT_ETH_PRICE_USD

        registry = self._registries.get(chain_id)
        if not registry:
            # Fallback: create a temporary registry
            registry = ProviderRegistry(chain_id=chain_id, providers=_default_providers(chain_id))

        # Strategy 1: Direct zero-fee
        zero_fee_plan = self._try_zero_fee_direct(
            registry, debt_asset, amount_usd, gas_price, eth_price
        )

        # Strategy 2: Borrow stablecoin free + swap
        swap_plan = self._try_zero_fee_swap(
            registry, debt_asset, amount_usd, gas_price, eth_price
        )

        # Strategy 3: Split loan
        split_plan = self._try_split_loan(
            registry, debt_asset, amount_usd, gas_price, eth_price
        )

        # Strategy 4: Cheapest single provider
        direct_plan = self._try_cheapest_direct(
            registry, debt_asset, amount_usd, gas_price, eth_price
        )

        # Select best plan
        plans = [p for p in [zero_fee_plan, swap_plan, split_plan, direct_plan] if p]
        plans.sort(key=lambda p: p.total_cost_usd)

        best = plans[0] if plans else self._fallback_plan(amount_usd)

        # Track optimization
        baseline_cost = float(amount_usd) * 5 / 10000  # Aave's 5 bps as baseline
        actual_cost = float(best.total_cost_usd)
        savings_bps = (baseline_cost - actual_cost) / float(amount_usd) * 10000 if amount_usd > 0 else 0

        self._optimization_history.append({
            "asset": debt_asset,
            "amount_usd": float(amount_usd),
            "best_cost_usd": actual_cost,
            "savings_bps": savings_bps,
            "provider_count": best.provider_count,
            "is_split": best.is_split,
        })

        self.record_success()
        return best

    # ── Strategy Implementations ───────────────────────────────

    def _try_zero_fee_direct(
        self,
        registry: ProviderRegistry,
        asset: str,
        amount_usd: Decimal,
        gas_price: float,
        eth_price: float,
    ) -> Optional[SplitLoanPlan]:
        """Try to borrow directly from a zero-fee provider."""
        zero_providers = registry.get_zero_fee()

        for provider in zero_providers:
            if asset in provider.supported_assets and provider.max_loan_usd >= amount_usd:
                gas_cost = self._calc_gas_cost(provider.avg_gas_overhead, gas_price, eth_price)
                quote = FlashLoanQuote(
                    provider=provider.provider_type,
                    asset=asset,
                    amount_usd=amount_usd,
                    fee_usd=Decimal("0"),
                    fee_bps=0,
                    gas_overhead=provider.avg_gas_overhead,
                    gas_cost_usd=gas_cost,
                    total_cost_usd=gas_cost,
                    available_liquidity_usd=provider.max_loan_usd,
                    confidence=provider.avg_success_rate,
                )
                return SplitLoanPlan(
                    quotes=[quote],
                    total_amount_usd=amount_usd,
                    total_fee_usd=Decimal("0"),
                    total_gas_usd=gas_cost,
                    total_cost_usd=gas_cost,
                    provider_count=1,
                    is_split=False,
                )
        return None

    def _try_zero_fee_swap(
        self,
        registry: ProviderRegistry,
        target_asset: str,
        amount_usd: Decimal,
        gas_price: float,
        eth_price: float,
    ) -> Optional[SplitLoanPlan]:
        """Borrow a stablecoin at 0% fee, then swap to the target asset."""
        zero_providers = registry.get_zero_fee()

        # Find a zero-fee provider that supports a stable we can swap
        swap_assets = ["USDC", "DAI", "USDT"]

        for provider in zero_providers:
            for swap_asset in swap_assets:
                if (
                    swap_asset in provider.supported_assets
                    and swap_asset != target_asset
                    and provider.max_loan_usd >= amount_usd
                ):
                    gas_overhead = provider.avg_gas_overhead + 150000  # Extra for swap
                    gas_cost = self._calc_gas_cost(gas_overhead, gas_price, eth_price)
                    swap_cost = amount_usd * Decimal(str(self.SWAP_COST_BPS)) / Decimal("10000")

                    total = gas_cost + swap_cost
                    quote = FlashLoanQuote(
                        provider=provider.provider_type,
                        asset=swap_asset,
                        amount_usd=amount_usd,
                        fee_usd=Decimal("0"),
                        fee_bps=0,
                        gas_overhead=gas_overhead,
                        gas_cost_usd=gas_cost,
                        total_cost_usd=total,
                        available_liquidity_usd=provider.max_loan_usd,
                        requires_swap=True,
                        swap_cost_usd=swap_cost,
                        confidence=provider.avg_success_rate * 0.95,
                    )
                    return SplitLoanPlan(
                        quotes=[quote],
                        total_amount_usd=amount_usd,
                        total_fee_usd=Decimal("0"),
                        total_gas_usd=gas_cost,
                        total_cost_usd=total,
                        provider_count=1,
                        is_split=False,
                    )
        return None

    def _try_split_loan(
        self,
        registry: ProviderRegistry,
        asset: str,
        amount_usd: Decimal,
        gas_price: float,
        eth_price: float,
    ) -> Optional[SplitLoanPlan]:
        """Split the loan across zero-fee providers to cover the full amount."""
        sorted_providers = registry.get_sorted_by_fee(asset)
        if not sorted_providers:
            return None

        remaining = amount_usd
        quotes: List[FlashLoanQuote] = []

        for provider in sorted_providers:
            if remaining <= 0:
                break

            borrow_amount = min(remaining, provider.max_loan_usd)
            if borrow_amount <= 0:
                continue

            fee = borrow_amount * Decimal(str(provider.fee_bps)) / Decimal("10000")
            gas_cost = self._calc_gas_cost(provider.avg_gas_overhead, gas_price, eth_price)

            quotes.append(FlashLoanQuote(
                provider=provider.provider_type,
                asset=asset,
                amount_usd=borrow_amount,
                fee_usd=fee,
                fee_bps=provider.fee_bps,
                gas_overhead=provider.avg_gas_overhead,
                gas_cost_usd=gas_cost,
                total_cost_usd=fee + gas_cost,
                available_liquidity_usd=provider.max_loan_usd,
                confidence=provider.avg_success_rate,
            ))
            remaining -= borrow_amount

        if remaining > 0 or not quotes:
            return None

        total_fee = sum(q.fee_usd for q in quotes)
        total_gas = sum(q.gas_cost_usd for q in quotes)

        return SplitLoanPlan(
            quotes=quotes,
            total_amount_usd=amount_usd,
            total_fee_usd=total_fee,
            total_gas_usd=total_gas,
            total_cost_usd=total_fee + total_gas,
            provider_count=len(quotes),
            is_split=len(quotes) > 1,
        )

    def _try_cheapest_direct(
        self,
        registry: ProviderRegistry,
        asset: str,
        amount_usd: Decimal,
        gas_price: float,
        eth_price: float,
    ) -> Optional[SplitLoanPlan]:
        """Find the single cheapest provider (may have fees)."""
        providers = registry.get_sorted_by_fee(asset)

        for provider in providers:
            if provider.max_loan_usd >= amount_usd:
                fee = amount_usd * Decimal(str(provider.fee_bps)) / Decimal("10000")
                gas_cost = self._calc_gas_cost(provider.avg_gas_overhead, gas_price, eth_price)
                total = fee + gas_cost

                quote = FlashLoanQuote(
                    provider=provider.provider_type,
                    asset=asset,
                    amount_usd=amount_usd,
                    fee_usd=fee,
                    fee_bps=provider.fee_bps,
                    gas_overhead=provider.avg_gas_overhead,
                    gas_cost_usd=gas_cost,
                    total_cost_usd=total,
                    available_liquidity_usd=provider.max_loan_usd,
                    confidence=provider.avg_success_rate,
                )
                return SplitLoanPlan(
                    quotes=[quote],
                    total_amount_usd=amount_usd,
                    total_fee_usd=fee,
                    total_gas_usd=gas_cost,
                    total_cost_usd=total,
                    provider_count=1,
                    is_split=False,
                )
        return None

    def _fallback_plan(self, amount_usd: Decimal) -> SplitLoanPlan:
        """Worst-case fallback: assume Aave V3 pricing."""
        fee = amount_usd * Decimal("5") / Decimal("10000")
        gas = Decimal("15")
        return SplitLoanPlan(
            quotes=[],
            total_amount_usd=amount_usd,
            total_fee_usd=fee,
            total_gas_usd=gas,
            total_cost_usd=fee + gas,
            provider_count=1,
            is_split=False,
        )

    # ── Helpers ────────────────────────────────────────────────

    @staticmethod
    def _calc_gas_cost(gas_units: int, gas_price_gwei: float, eth_price_usd: float) -> Decimal:
        """Calculate gas cost in USD."""
        gas_eth = gas_units * gas_price_gwei * 1e-9
        return Decimal(str(round(gas_eth * eth_price_usd, 4)))

    # ── Provider Management ────────────────────────────────────

    def update_provider(
        self,
        chain_id: int,
        provider_type: FlashLoanProviderType,
        **updates: Any,
    ) -> None:
        """Update a provider's profile (e.g., after observing a new fee)."""
        registry = self._registries.get(chain_id)
        if not registry:
            return
        for p in registry.providers:
            if p.provider_type == provider_type:
                for key, value in updates.items():
                    if hasattr(p, key):
                        setattr(p, key, value)
                p.last_updated = time.time()
                break

    def get_fee_savings_stats(self) -> Dict[str, Any]:
        """Return fee optimization statistics."""
        if not self._optimization_history:
            return {"total_optimizations": 0}

        recent = self._optimization_history[-100:]
        return {
            "total_optimizations": len(self._optimization_history),
            "avg_savings_bps": round(sum(h["savings_bps"] for h in recent) / len(recent), 2),
            "avg_cost_usd": round(sum(h["best_cost_usd"] for h in recent) / len(recent), 4),
            "split_loan_pct": round(sum(1 for h in recent if h["is_split"]) / len(recent) * 100, 1),
        }
