#!/usr/bin/env python3
"""
STAGE 3 — Flash Loan Surplus Utilization (Script 3 Enhancement #1)
====================================================================
Multi-objective transactions: use leftover flash loan capacity for
secondary profitable actions within the SAME transaction.

Concept:
  You borrow 10M DAI for a liquidation that only needs 9.5M.
  The remaining 500K can execute a secondary action (arb, swap, mini-liq)
  inside the same TX — zero extra gas cost.

Surplus strategies (evaluated in order):
  1. DEX Arbitrage — if a profitable arb route exists for the surplus
  2. Secondary Liquidation — if another small position is liquidatable
  3. Yield Deposit — deposit surplus into a lending pool briefly
  4. Hold — do nothing with surplus (safest, always available)

Safety:
  - balanceBefore snapshot at TX start
  - Final balance MUST >= flash loan repayment + fee
  - Surplus strategy reverts if it would make repayment fail
"""

import logging
from typing import Dict, List, Optional
from dataclasses import dataclass
from enum import Enum

logger = logging.getLogger(__name__)


class SurplusAction(Enum):
    NONE = "none"
    DEX_ARBITRAGE = "dex_arbitrage"
    SECONDARY_LIQUIDATION = "secondary_liquidation"
    YIELD_DEPOSIT = "yield_deposit"


@dataclass
class SurplusOpportunity:
    """A secondary action that can be packed into the flash loan TX."""
    action: SurplusAction
    asset: str
    amount_usd: float
    estimated_profit_usd: float
    extra_gas_units: int
    risk_score: float  # 0=safe, 1=risky
    params: Dict = None

    def __post_init__(self):
        if self.params is None:
            self.params = {}


@dataclass
class SurplusAnalysis:
    """Result of surplus analysis for one liquidation."""
    surplus_available_usd: float
    best_action: SurplusAction
    estimated_extra_profit_usd: float
    total_profit_with_surplus_usd: float
    opportunities_evaluated: int
    selected_opportunity: Optional[SurplusOpportunity] = None


# Known DEX arb routes (simplified — in production, query on-chain)
_COMMON_ARB_ROUTES = [
    {"pair": "WETH/USDC", "dex_a": "uniswap_v3", "dex_b": "sushiswap",
     "min_spread_bps": 5, "gas_units": 180_000},
    {"pair": "WBTC/WETH", "dex_a": "curve", "dex_b": "uniswap_v3",
     "min_spread_bps": 8, "gas_units": 200_000},
    {"pair": "DAI/USDC", "dex_a": "curve", "dex_b": "uniswap_v2",
     "min_spread_bps": 2, "gas_units": 150_000},
]


class SurplusStrategyEngine:
    """
    Evaluates and selects the best secondary action for flash loan surplus.

    Called by the calculator BEFORE execution to determine if surplus
    utilization is profitable.  The selected strategy is encoded into
    the executor's calldata.
    """

    # Minimum surplus to consider secondary actions (in USD)
    MIN_SURPLUS_USD = 500.0
    # Maximum risk tolerance for surplus (0-1)
    MAX_SURPLUS_RISK = 0.3

    def analyze(
        self,
        borrowed_usd: float,
        debt_to_repay_usd: float,
        flash_loan_fee_usd: float,
        collateral_seized_usd: float,
        gas_price_gwei: float = 30.0,
        eth_price_usd: float = 2500.0,
        chain_id: int = 1,
    ) -> SurplusAnalysis:
        """
        Determine if surplus exists and what to do with it.

        surplus = collateral_seized - debt_repaid - flash_fee
        If surplus > MIN_SURPLUS_USD, evaluate secondary actions.
        """
        surplus = collateral_seized_usd - debt_to_repay_usd - flash_loan_fee_usd

        if surplus < self.MIN_SURPLUS_USD:
            return SurplusAnalysis(
                surplus_available_usd=max(surplus, 0),
                best_action=SurplusAction.NONE,
                estimated_extra_profit_usd=0,
                total_profit_with_surplus_usd=surplus,
                opportunities_evaluated=0,
            )

        opportunities: List[SurplusOpportunity] = []

        # Strategy 1: DEX Arbitrage with surplus funds
        for route in _COMMON_ARB_ROUTES:
            extra_gas_cost = (route["gas_units"] * gas_price_gwei) / 1e9 * eth_price_usd
            spread_profit = surplus * (route["min_spread_bps"] / 10000)
            net = spread_profit - extra_gas_cost

            if net > 0:
                opportunities.append(SurplusOpportunity(
                    action=SurplusAction.DEX_ARBITRAGE,
                    asset=route["pair"],
                    amount_usd=surplus,
                    estimated_profit_usd=net,
                    extra_gas_units=route["gas_units"],
                    risk_score=0.2,
                    params={"route": route},
                ))

        # Strategy 2: Secondary mini-liquidation
        # (In production, this checks the detector's queue for small positions)
        mini_liq_profit = surplus * 0.005  # Assume 0.5% bonus on mini-liq
        mini_liq_gas = (200_000 * gas_price_gwei) / 1e9 * eth_price_usd
        if mini_liq_profit > mini_liq_gas and surplus > 2000:
            opportunities.append(SurplusOpportunity(
                action=SurplusAction.SECONDARY_LIQUIDATION,
                asset="any_liquidatable",
                amount_usd=surplus * 0.5,  # Use half for safety
                estimated_profit_usd=mini_liq_profit - mini_liq_gas,
                extra_gas_units=200_000,
                risk_score=0.4,
            ))

        # Strategy 3: Yield deposit (extremely low risk, low reward)
        # Deposit into Aave for 1 block → earn ~0.0001% yield
        yield_profit = surplus * 0.000001  # Negligible per block
        if yield_profit > 0:
            opportunities.append(SurplusOpportunity(
                action=SurplusAction.YIELD_DEPOSIT,
                asset="surplus_token",
                amount_usd=surplus,
                estimated_profit_usd=yield_profit,
                extra_gas_units=100_000,
                risk_score=0.05,
            ))

        # Filter by risk tolerance and select best
        viable = [o for o in opportunities if o.risk_score <= self.MAX_SURPLUS_RISK]
        viable.sort(key=lambda o: o.estimated_profit_usd, reverse=True)

        if viable:
            best = viable[0]
            return SurplusAnalysis(
                surplus_available_usd=surplus,
                best_action=best.action,
                estimated_extra_profit_usd=best.estimated_profit_usd,
                total_profit_with_surplus_usd=surplus + best.estimated_profit_usd,
                opportunities_evaluated=len(opportunities),
                selected_opportunity=best,
            )

        return SurplusAnalysis(
            surplus_available_usd=surplus,
            best_action=SurplusAction.NONE,
            estimated_extra_profit_usd=0,
            total_profit_with_surplus_usd=surplus,
            opportunities_evaluated=len(opportunities),
        )
