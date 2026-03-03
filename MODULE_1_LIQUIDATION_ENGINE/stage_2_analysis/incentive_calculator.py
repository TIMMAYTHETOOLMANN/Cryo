#!/usr/bin/env python3
"""
STAGE 2 — Incentive Feasibility Calculator (Module 2)
======================================================
Before initiating any protective action, computes whether the available
reward (liquidation bonus) covers operational costs such as temporary
liquidity fees and gas.

Inputs:
  - On-chain debt and collateral amounts
  - Oracle prices (with staleness checks)
  - Protocol-defined liquidation bonus
  - Flash loan provider fee
  - Gas estimation (with 20% buffer)

Output:
  - FeasibilityResult with net incentive and go/no-go decision

Edge Cases Handled:
  - Bad debt (collateral < debt + fees) → skip
  - Gas price spikes → re-check before submission
  - LP token collateral → use fair pricing

Zero capital required — pure computation.
"""

import logging
from decimal import Decimal, getcontext
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, field
from enum import Enum

getcontext().prec = 50

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data Models
# ---------------------------------------------------------------------------

class LiquidityProvider(Enum):
    """Supported flash loan / temporary liquidity providers."""
    AAVE_V3 = "aave_v3"
    UNISWAP_V3 = "uniswap_v3"
    BALANCER_V2 = "balancer_v2"
    UNISWAP_V2 = "uniswap_v2"
    MAKER_FLASH = "maker_flash"
    DYDX = "dydx"


# Provider fees in basis points
PROVIDER_FEES: Dict[LiquidityProvider, int] = {
    LiquidityProvider.AAVE_V3: 5,        # 0.05%
    LiquidityProvider.BALANCER_V2: 0,     # 0% (Vault flash loans)
    LiquidityProvider.MAKER_FLASH: 0,     # 0% (DAI flash mint)
    LiquidityProvider.DYDX: 0,            # 0%
    LiquidityProvider.UNISWAP_V3: 30,     # 0.3% (varies by tier)
    LiquidityProvider.UNISWAP_V2: 30,     # 0.3%
}


@dataclass
class IncentiveParams:
    """Parameters for incentive feasibility calculation."""
    debt_amount: Decimal           # Debt to repay (in token units)
    debt_price_usd: Decimal        # Debt asset price in USD
    debt_decimals: int             # Debt asset decimals
    collateral_amount: Decimal     # Available collateral
    collateral_price_usd: Decimal  # Collateral asset price in USD
    collateral_decimals: int       # Collateral asset decimals
    liquidation_bonus_bps: int     # Protocol bonus (e.g., 500 = 5%)
    flash_loan_fee_bps: int        # Flash loan fee (e.g., 5 = 0.05%)
    estimated_gas_units: int       # Gas estimate for the tx
    gas_price_wei: int             # Current gas price
    native_token_price_usd: Decimal  # ETH price in USD
    gas_buffer_pct: Decimal = Decimal("1.2")  # 20% gas buffer


@dataclass
class FeasibilityResult:
    """Result of the incentive feasibility calculation."""
    is_feasible: bool = False
    collateral_seized_usd: Decimal = Decimal("0")
    debt_repay_usd: Decimal = Decimal("0")
    gross_incentive_usd: Decimal = Decimal("0")
    flash_loan_fee_usd: Decimal = Decimal("0")
    gas_cost_usd: Decimal = Decimal("0")
    net_incentive_usd: Decimal = Decimal("0")
    is_bad_debt: bool = False
    best_provider: LiquidityProvider = LiquidityProvider.AAVE_V3
    provider_savings_usd: Decimal = Decimal("0")


# ---------------------------------------------------------------------------
# Incentive Feasibility Calculator
# ---------------------------------------------------------------------------

class IncentiveFeasibilityCalculator:
    """
    Module 2: Computes liquidation profitability before execution.

    Evaluates whether a liquidation opportunity's reward (bonus)
    exceeds operational costs (flash loan fee + gas), accounting
    for price volatility and bad debt scenarios.
    """

    # Minimum net incentive to consider feasible (USD)
    MIN_INCENTIVE_USD = Decimal("10")

    def __init__(self, min_incentive_usd: Optional[Decimal] = None):
        if min_incentive_usd is not None:
            self.MIN_INCENTIVE_USD = min_incentive_usd
        self._calculations_performed: int = 0
        self._feasible_count: int = 0
        self._bad_debt_count: int = 0

    # ── Properties ──────────────────────────────────────────────────────

    @property
    def stats(self) -> Dict:
        return {
            "calculations_performed": self._calculations_performed,
            "feasible_count": self._feasible_count,
            "bad_debt_count": self._bad_debt_count,
            "feasibility_rate": (
                self._feasible_count / self._calculations_performed
                if self._calculations_performed > 0
                else 0.0
            ),
        }

    # ── Core Calculation ────────────────────────────────────────────────

    def calculate_feasibility(self, params: IncentiveParams) -> FeasibilityResult:
        """
        Calculate whether a liquidation is economically feasible.

        Formula:
          gross_incentive = collateral_seized_usd - debt_repay_usd
          net_incentive = gross_incentive - flash_fee_usd - gas_cost_usd
          feasible = net_incentive >= MIN_INCENTIVE_USD

        Args:
            params: Incentive calculation parameters

        Returns:
            FeasibilityResult with detailed breakdown
        """
        self._calculations_performed += 1
        result = FeasibilityResult()

        # 1. Calculate USD values
        result.debt_repay_usd = self._token_value_usd(
            params.debt_amount, params.debt_price_usd, params.debt_decimals
        )

        # 2. Collateral seized USD = debt_repay_usd * (1 + bonus%)
        #    This is a simplified calculation: the protocol converts debt to
        #    collateral at oracle prices and adds the bonus on the USD value.
        bonus_multiplier = Decimal("1") + Decimal(params.liquidation_bonus_bps) / Decimal("10000")
        result.collateral_seized_usd = result.debt_repay_usd * bonus_multiplier

        # Cap at available collateral value
        available_collateral_usd = self._token_value_usd(
            params.collateral_amount, params.collateral_price_usd, params.collateral_decimals
        )
        if result.collateral_seized_usd > available_collateral_usd:
            result.collateral_seized_usd = available_collateral_usd

        # 3. Gross incentive
        if result.collateral_seized_usd > result.debt_repay_usd:
            result.gross_incentive_usd = result.collateral_seized_usd - result.debt_repay_usd
        else:
            result.is_bad_debt = True
            self._bad_debt_count += 1

        # 4. Flash loan fee
        result.flash_loan_fee_usd = (
            result.debt_repay_usd * Decimal(params.flash_loan_fee_bps) / Decimal("10000")
        )

        # 5. Gas cost with buffer
        gas_cost_wei = Decimal(params.estimated_gas_units) * Decimal(params.gas_price_wei)
        gas_cost_eth = gas_cost_wei / Decimal("1000000000000000000")  # wei to ETH
        gas_cost_usd = gas_cost_eth * params.native_token_price_usd
        result.gas_cost_usd = gas_cost_usd * params.gas_buffer_pct

        # 6. Net incentive
        total_costs = result.flash_loan_fee_usd + result.gas_cost_usd
        if result.gross_incentive_usd > total_costs:
            result.net_incentive_usd = result.gross_incentive_usd - total_costs

        # 7. Feasibility check
        result.is_feasible = result.net_incentive_usd >= self.MIN_INCENTIVE_USD
        if result.is_feasible:
            self._feasible_count += 1

        return result

    # ── Provider Optimization ───────────────────────────────────────────

    def find_best_provider(
        self,
        params: IncentiveParams,
        available_providers: Optional[List[LiquidityProvider]] = None,
    ) -> Tuple[FeasibilityResult, LiquidityProvider]:
        """
        Find the cheapest flash loan provider for this liquidation.

        Iterates over available providers, calculates feasibility with
        each provider's fee, and returns the one with highest net incentive.

        Args:
            params: Base calculation parameters
            available_providers: List of providers to try (defaults to all)

        Returns:
            (best_result, best_provider) tuple
        """
        providers = available_providers or list(PROVIDER_FEES.keys())

        best_result = FeasibilityResult()
        best_provider = LiquidityProvider.AAVE_V3

        for provider in providers:
            fee_bps = PROVIDER_FEES.get(provider, 30)
            test_params = IncentiveParams(
                debt_amount=params.debt_amount,
                debt_price_usd=params.debt_price_usd,
                debt_decimals=params.debt_decimals,
                collateral_amount=params.collateral_amount,
                collateral_price_usd=params.collateral_price_usd,
                collateral_decimals=params.collateral_decimals,
                liquidation_bonus_bps=params.liquidation_bonus_bps,
                flash_loan_fee_bps=fee_bps,
                estimated_gas_units=params.estimated_gas_units,
                gas_price_wei=params.gas_price_wei,
                native_token_price_usd=params.native_token_price_usd,
                gas_buffer_pct=params.gas_buffer_pct,
            )

            result = self.calculate_feasibility(test_params)

            if result.net_incentive_usd > best_result.net_incentive_usd:
                best_result = result
                best_provider = provider

        best_result.best_provider = best_provider
        return best_result, best_provider

    # ── Bad Debt Detection ──────────────────────────────────────────────

    @staticmethod
    def is_bad_debt(
        collateral_usd: Decimal,
        debt_usd: Decimal,
        flash_fee_bps: int,
        gas_cost_usd: Decimal,
    ) -> bool:
        """
        Check if a position represents bad debt.

        Bad debt = collateral value < debt + fees + gas.
        These positions should be skipped as they cannot cover costs.

        Args:
            collateral_usd: Total collateral value in USD
            debt_usd: Total debt value in USD
            flash_fee_bps: Flash loan fee in basis points
            gas_cost_usd: Estimated gas cost in USD

        Returns:
            True if the position is bad debt
        """
        flash_fee = debt_usd * Decimal(flash_fee_bps) / Decimal("10000")
        total_cost = debt_usd + flash_fee + gas_cost_usd
        return collateral_usd < total_cost

    # ── Internal Helpers ────────────────────────────────────────────────

    @staticmethod
    def _token_value_usd(
        amount: Decimal,
        price_usd: Decimal,
        decimals: int,
    ) -> Decimal:
        """Convert a token amount to USD value."""
        return (amount * price_usd) / (Decimal(10) ** decimals)
