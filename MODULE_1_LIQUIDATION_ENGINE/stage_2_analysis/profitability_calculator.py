#!/usr/bin/env python3
"""
STAGE 2 — Enhanced Profitability Calculator (Script 2 Upgraded)
================================================================
Dynamic, multi-exit profit optimization.

ENHANCEMENTS (Script 2):
  1. Multi-Exit Profit Maximization — evaluate hold/sell/bridge strategies
  2. Real-Time Gas Price Prediction — moving average + EMA forecasting
  3. Flash Loan Fee Minimization — auto-select cheapest provider per opportunity

Zero capital required — pure computation.
"""

import logging
import time
from collections import deque
from typing import Dict, List, Optional
from dataclasses import dataclass, field
from enum import Enum

from ..config.settings import ConfigManager, get_config

logger = logging.getLogger(__name__)


class ExitStrategy(Enum):
    HOLD = "hold"                     # Keep collateral (simplest)
    SELL_DEX = "sell_dex"             # Sell immediately via DEX for stablecoin
    BRIDGE = "bridge"                 # Bridge to cheaper chain then sell
    CROSS_CHAIN_ARB = "cross_chain_arb"  # Script 3: sell on best-price chain


@dataclass
class ExitAnalysis:
    """Analysis of one exit strategy."""
    strategy: ExitStrategy
    gross_profit_usd: float
    flash_loan_fee_usd: float
    gas_cost_usd: float
    slippage_cost_usd: float
    bridge_cost_usd: float = 0.0
    net_profit_usd: float = 0.0
    exit_chain_id: int = 0  # Script 3: target chain for cross-chain exit


@dataclass
class ProfitabilityResult:
    gross_profit_usd: float
    flash_loan_fee_usd: float
    gas_cost_usd: float
    slippage_cost_usd: float
    net_profit_usd: float
    is_profitable: bool
    roi_percent: float
    capital_required_usd: float
    # Script 2 additions
    best_exit_strategy: ExitStrategy = ExitStrategy.HOLD
    best_provider: str = "aave_v3"
    all_exits: List[ExitAnalysis] = field(default_factory=list)
    gas_price_predicted_gwei: float = 0.0
    provider_comparison: Dict[str, float] = field(default_factory=dict)
    # Script 3 additions
    surplus_profit_usd: float = 0.0         # Extra from surplus utilization
    gas_token_savings_usd: float = 0.0      # Savings from gas token burn
    fee_rebate_usd: float = 0.0             # Protocol rebate value
    cross_chain_advantage_usd: float = 0.0  # Extra from cross-chain arb


class GasPricePredictor:
    """
    Short-term gas price forecasting using Exponential Moving Average.
    Predicts gas price for the next 1-2 blocks.
    """

    def __init__(self, window: int = 20, ema_alpha: float = 0.3):
        self._history: deque = deque(maxlen=window)
        self._ema: Optional[float] = None
        self._alpha = ema_alpha

    def record(self, gas_price_gwei: float):
        """Record an observed gas price."""
        self._history.append(gas_price_gwei)
        if self._ema is None:
            self._ema = gas_price_gwei
        else:
            self._ema = self._alpha * gas_price_gwei + (1 - self._alpha) * self._ema

    def predict(self) -> Optional[float]:
        """Predict next gas price using EMA."""
        return self._ema

    def moving_average(self) -> Optional[float]:
        if not self._history:
            return None
        return sum(self._history) / len(self._history)


class ProfitabilityCalculator:
    """
    Enhanced calculator with multi-exit optimization and gas prediction.

    Script 2 upgrades:
    - Evaluates HOLD, SELL_DEX, BRIDGE exit strategies
    - Predicts gas price for next block
    - Compares ALL flash loan providers and picks cheapest
    """

    FLASH_LOAN_FEES = {
        "aave_v3": 0.0005,    "aave_v2": 0.0005,
        "balancer_v2": 0.0,   "uniswap_v3": 0.003,
        "uniswap_v2": 0.003,  "maker_dao": 0.0,
        "dydx": 0.0,
    }

    # Sorted cheapest first for fee minimization
    PROVIDER_PRIORITY = ["balancer_v2", "maker_dao", "dydx", "aave_v3", "aave_v2", "uniswap_v3", "uniswap_v2"]

    GAS_ESTIMATES = {
        "aave_v3_liquidation": 300_000,
        "aave_v2_liquidation": 350_000,
        "compound_liquidation": 350_000,
        "maker_liquidation": 400_000,
        "liquity_liquidation": 250_000,
        "euler_liquidation": 300_000,
        "dex_swap": 150_000,
        "bridge_call": 200_000,
    }

    GAS_PRICE_DEFAULTS = {
        1: 0.5, 42161: 0.01, 10: 0.01, 8453: 0.01,
        137: 30.0, 43114: 25.0, 56: 3.0,
    }

    BRIDGE_COSTS_USD = {
        (42161, 1): 3.0,   # Arbitrum → Ethereum
        (10, 1): 3.0,      # Optimism → Ethereum
        (8453, 1): 3.0,    # Base → Ethereum
        (137, 1): 5.0,     # Polygon → Ethereum
    }

    DEX_SWAP_SLIPPAGE = {
        "default": 0.005,   # 0.5%
        "stablecoin": 0.001, # 0.1%
        "volatile": 0.015,   # 1.5%
    }

    # Script 3 Enhancement #5: Gas Token Savings (CHI/GST2)
    GAS_TOKEN_DISCOUNT = {
        1: 0.30,       # 30% discount on Ethereum mainnet
        56: 0.20,      # 20% on BSC
    }

    # Script 3 Enhancement #6: Fee Rebate Programs
    FEE_REBATES = {
        "balancer_v2": {"token": "BAL", "rebate_pct": 0.002, "price_usd": 3.50},
        "aave_v3":     {"token": "AAVE", "rebate_pct": 0.0001, "price_usd": 80.0},
    }

    # Script 3 Enhancement #2: Cross-chain price differentials (updated at runtime)
    _cross_chain_prices: Dict[tuple, float] = {}

    def __init__(self, config: Optional[ConfigManager] = None):
        self.config = config or get_config()
        self.min_profit_usd = self.config.execution.min_profit_usd

        # Gas price predictor per chain
        self._gas_predictors: Dict[int, GasPricePredictor] = {}

    def _get_predictor(self, chain_id: int) -> GasPricePredictor:
        if chain_id not in self._gas_predictors:
            self._gas_predictors[chain_id] = GasPricePredictor()
        return self._gas_predictors[chain_id]

    def record_gas_price(self, chain_id: int, gas_gwei: float):
        """Feed observed gas prices for prediction."""
        self._get_predictor(chain_id).record(gas_gwei)

    def calculate(
        self,
        debt_amount_usd: float,
        collateral_amount_usd: float,
        liquidation_bonus: float,
        flash_loan_provider: str = "auto",
        gas_price_gwei: Optional[float] = None,
        eth_price_usd: float = 2500.0,
        chain_id: int = 1,
        operation: str = "aave_v3_liquidation",
        slippage_percent: float = 0.005,
        volatility_class: str = "default",
    ) -> ProfitabilityResult:
        """
        Compute net profit with multi-exit optimization.

        When flash_loan_provider="auto", compares ALL providers and picks cheapest.
        """
        # Gas price: use prediction if available, fallback to observed, then default
        predicted_gas = None
        predictor = self._get_predictor(chain_id)
        if predictor.predict() is not None:
            predicted_gas = predictor.predict()

        if gas_price_gwei is None:
            gas_price_gwei = predicted_gas or self.GAS_PRICE_DEFAULTS.get(chain_id, 30.0)
        elif predicted_gas is not None:
            # Use the higher of observed and predicted for safety
            gas_price_gwei = max(gas_price_gwei, predicted_gas)

        chain_cfg = self.config.get_chain(chain_id)
        if chain_cfg and chain_cfg.is_l2:
            gas_price_gwei *= chain_cfg.l2_gas_discount

        # Record for future prediction
        predictor.record(gas_price_gwei)

        # ---- Flash loan fee minimization ----
        provider_comparison: Dict[str, float] = {}
        if flash_loan_provider == "auto":
            best_provider = "aave_v3"
            best_total_cost = float("inf")
            for prov in self.PROVIDER_PRIORITY:
                fee = self.FLASH_LOAN_FEES.get(prov, 0.001)
                total = debt_amount_usd * fee
                provider_comparison[prov] = total
                if total < best_total_cost:
                    best_total_cost = total
                    best_provider = prov
            flash_loan_provider = best_provider

        fee_rate = self.FLASH_LOAN_FEES.get(flash_loan_provider, 0.001)
        flash_loan_fee_usd = debt_amount_usd * fee_rate

        # ---- Multi-exit analysis ----
        collateral_seized_usd = debt_amount_usd * (1 + liquidation_bonus)
        gross_profit_usd = collateral_seized_usd - debt_amount_usd

        gas_units = self.GAS_ESTIMATES.get(operation, 300_000)
        gas_cost_eth = (gas_units * gas_price_gwei) / 1e9
        gas_cost_usd = gas_cost_eth * eth_price_usd

        slippage_pct = self.DEX_SWAP_SLIPPAGE.get(volatility_class, slippage_percent)

        exits: List[ExitAnalysis] = []

        # Exit 1: HOLD collateral
        hold = ExitAnalysis(
            strategy=ExitStrategy.HOLD,
            gross_profit_usd=gross_profit_usd,
            flash_loan_fee_usd=flash_loan_fee_usd,
            gas_cost_usd=gas_cost_usd,
            slippage_cost_usd=0,
        )
        hold.net_profit_usd = hold.gross_profit_usd - hold.flash_loan_fee_usd - hold.gas_cost_usd
        exits.append(hold)

        # Exit 2: SELL via DEX immediately
        swap_gas = (self.GAS_ESTIMATES["dex_swap"] * gas_price_gwei) / 1e9 * eth_price_usd
        sell_slippage = collateral_seized_usd * slippage_pct
        sell = ExitAnalysis(
            strategy=ExitStrategy.SELL_DEX,
            gross_profit_usd=gross_profit_usd,
            flash_loan_fee_usd=flash_loan_fee_usd,
            gas_cost_usd=gas_cost_usd + swap_gas,
            slippage_cost_usd=sell_slippage,
        )
        sell.net_profit_usd = sell.gross_profit_usd - sell.flash_loan_fee_usd - sell.gas_cost_usd - sell.slippage_cost_usd
        exits.append(sell)

        # Exit 3: BRIDGE to mainnet (only if on L2 and profit > bridge cost)
        bridge_cost = self.BRIDGE_COSTS_USD.get((chain_id, 1), 0)
        if bridge_cost > 0 and chain_cfg and chain_cfg.is_l2:
            bridge_gas = (self.GAS_ESTIMATES["bridge_call"] * gas_price_gwei) / 1e9 * eth_price_usd
            bridge = ExitAnalysis(
                strategy=ExitStrategy.BRIDGE,
                gross_profit_usd=gross_profit_usd,
                flash_loan_fee_usd=flash_loan_fee_usd,
                gas_cost_usd=gas_cost_usd + bridge_gas,
                slippage_cost_usd=sell_slippage * 0.5,  # Less slippage on mainnet
                bridge_cost_usd=bridge_cost,
            )
            bridge.net_profit_usd = (
                bridge.gross_profit_usd - bridge.flash_loan_fee_usd
                - bridge.gas_cost_usd - bridge.slippage_cost_usd
                - bridge.bridge_cost_usd
            )
            exits.append(bridge)

        # Exit 4: CROSS-CHAIN ARB — sell on a chain with higher price (Script 3 #2)
        for (src, dest), advantage_usd in self._cross_chain_prices.items():
            if src == chain_id and advantage_usd > 0:
                arb_bridge_cost = self.BRIDGE_COSTS_USD.get((src, dest), 5.0)
                arb_gas = (self.GAS_ESTIMATES.get("bridge_call", 200_000) * gas_price_gwei) / 1e9 * eth_price_usd
                arb = ExitAnalysis(
                    strategy=ExitStrategy.CROSS_CHAIN_ARB,
                    gross_profit_usd=gross_profit_usd + advantage_usd,
                    flash_loan_fee_usd=flash_loan_fee_usd,
                    gas_cost_usd=gas_cost_usd + arb_gas,
                    slippage_cost_usd=sell_slippage * 0.3,
                    bridge_cost_usd=arb_bridge_cost,
                    exit_chain_id=dest,
                )
                arb.net_profit_usd = (
                    arb.gross_profit_usd - arb.flash_loan_fee_usd
                    - arb.gas_cost_usd - arb.slippage_cost_usd
                    - arb.bridge_cost_usd
                )
                exits.append(arb)

        # Script 3 Enhancement #5: Gas Token Savings
        gas_token_savings = 0.0
        gas_discount = self.GAS_TOKEN_DISCOUNT.get(chain_id, 0)
        if gas_discount > 0:
            gas_token_savings = gas_cost_usd * gas_discount

        # Script 3 Enhancement #6: Fee Rebate Programs
        fee_rebate = 0.0
        rebate_info = self.FEE_REBATES.get(flash_loan_provider)
        if rebate_info:
            fee_rebate = debt_amount_usd * rebate_info["rebate_pct"] * rebate_info["price_usd"]

        # Script 3 Enhancement #1: Surplus estimation
        surplus_profit = 0.0
        surplus_available = collateral_seized_usd - debt_amount_usd - flash_loan_fee_usd
        if surplus_available > 500:
            surplus_profit = surplus_available * 0.001  # Conservative 0.1% from arb

        # Apply bonuses to all exits
        for ex in exits:
            ex.net_profit_usd += gas_token_savings + fee_rebate + surplus_profit

        # Pick best exit
        best_exit = max(exits, key=lambda e: e.net_profit_usd)

        net_profit_usd = best_exit.net_profit_usd
        cross_chain_adv = best_exit.net_profit_usd - hold.net_profit_usd if best_exit.strategy == ExitStrategy.CROSS_CHAIN_ARB else 0.0
        total_gas = best_exit.gas_cost_usd
        total_slippage = best_exit.slippage_cost_usd
        capital_required_usd = total_gas + flash_loan_fee_usd
        roi = (net_profit_usd / capital_required_usd * 100) if capital_required_usd > 0 else 0

        return ProfitabilityResult(
            gross_profit_usd=gross_profit_usd,
            flash_loan_fee_usd=flash_loan_fee_usd,
            gas_cost_usd=total_gas,
            slippage_cost_usd=total_slippage,
            net_profit_usd=net_profit_usd,
            is_profitable=net_profit_usd > self.min_profit_usd,
            roi_percent=roi,
            capital_required_usd=capital_required_usd,
            best_exit_strategy=best_exit.strategy,
            best_provider=flash_loan_provider,
            all_exits=exits,
            gas_price_predicted_gwei=predicted_gas or gas_price_gwei,
            provider_comparison=provider_comparison,
            surplus_profit_usd=surplus_profit,
            gas_token_savings_usd=gas_token_savings,
            fee_rebate_usd=fee_rebate,
            cross_chain_advantage_usd=cross_chain_adv,
        )

    def compare_providers(self, debt_usd: float, collateral_usd: float,
                          bonus: float, chain_id: int = 1) -> Dict[str, ProfitabilityResult]:
        return {
            provider: self.calculate(debt_usd, collateral_usd, bonus,
                                     flash_loan_provider=provider, chain_id=chain_id)
            for provider in self.FLASH_LOAN_FEES
        }

    def update_cross_chain_price(self, source_chain: int, dest_chain: int,
                                  advantage_usd: float):
        """Update cross-chain price advantage for arb exit calculation."""
        self._cross_chain_prices[(source_chain, dest_chain)] = advantage_usd


# Singleton
_calc: Optional[ProfitabilityCalculator] = None

def get_calculator() -> ProfitabilityCalculator:
    global _calc
    if _calc is None:
        _calc = ProfitabilityCalculator()
    return _calc
