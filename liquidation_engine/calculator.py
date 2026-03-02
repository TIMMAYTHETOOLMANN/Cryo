#!/usr/bin/env python3
"""
Profitability Calculator for Flash Loan Liquidations
Computes net profit after flash loan fees, gas, and slippage
"""

from dataclasses import dataclass
from typing import Optional
from web3 import Web3

@dataclass
class ProfitabilityResult:
    gross_profit_usd: float
    flash_loan_fee_usd: float
    gas_cost_usd: float
    net_profit_usd: float
    is_profitable: bool
    roi_percent: float

class ProfitabilityCalculator:
    def __init__(self):
        # Flash loan fees by provider
        self.flash_loan_fees = {
            "aave_v3": 0.0005,  # 0.05%
            "balancer": 0.0,     # 0%
            "uniswap_v3": 0.003, # 0.3%
            "dodo": 0.0,         # 0%
        }
        
        # Gas estimates by operation
        self.gas_estimates = {
            "aave_v3_liquidation": 300000,
            "compound_liquidation": 350000,
            "maker_liquidation": 400000,
        }
        
        # Minimum profit threshold
        self.min_profit_usd = 10.0
    
    def calculate(
        self,
        debt_amount_usd: float,
        collateral_amount_usd: float,
        liquidation_bonus: float,
        flash_loan_provider: str,
        gas_price_gwei: float,
        eth_price_usd: float,
        chain_id: int = 1
    ) -> ProfitabilityResult:
        """
        Calculate profitability of a liquidation
        
        Args:
            debt_amount_usd: Debt to cover in USD
            collateral_amount_usd: Collateral value in USD
            liquidation_bonus: Bonus percentage (e.g., 0.05 for 5%)
            flash_loan_provider: Provider name
            gas_price_gwei: Current gas price in gwei
            eth_price_usd: ETH price in USD
            chain_id: Chain ID for gas cost estimation
        
        Returns:
            ProfitabilityResult with profit analysis
        """
        # Calculate collateral seized with bonus
        collateral_seized_usd = debt_amount_usd * (1 + liquidation_bonus)
        
        # Gross profit = collateral seized - debt repaid
        gross_profit_usd = collateral_seized_usd - debt_amount_usd
        
        # Flash loan fee
        fee_rate = self.flash_loan_fees.get(flash_loan_provider, 0.001)
        flash_loan_fee_usd = debt_amount_usd * fee_rate
        
        # Gas cost
        gas_estimate = self.gas_estimates.get("aave_v3_liquidation", 300000)
        
        # Adjust for L2 chains
        if chain_id in [42161, 10, 8453]:  # Arbitrum, Optimism, Base
            gas_estimate = gas_estimate // 10  # ~10x cheaper
        elif chain_id == 137:  # Polygon
            gas_estimate = gas_estimate // 100  # ~100x cheaper
        
        gas_cost_eth = (gas_estimate * gas_price_gwei) / 1e9
        gas_cost_usd = gas_cost_eth * eth_price_usd
        
        # Net profit
        net_profit_usd = gross_profit_usd - flash_loan_fee_usd - gas_cost_usd
        
        # ROI
        capital_required = debt_amount_usd * fee_rate + gas_cost_usd
        roi_percent = (net_profit_usd / capital_required * 100) if capital_required > 0 else 0
        
        return ProfitabilityResult(
            gross_profit_usd=gross_profit_usd,
            flash_loan_fee_usd=flash_loan_fee_usd,
            gas_cost_usd=gas_cost_usd,
            net_profit_usd=net_profit_usd,
            is_profitable=net_profit_usd > self.min_profit_usd,
            roi_percent=roi_percent
        )
    
    def example_calculation(self):
        """Example liquidation profitability"""
        print("=" * 60)
        print("LIQUIDATION PROFITABILITY EXAMPLE")
        print("=" * 60)
        
        # Example: Aave V3 position
        debt_usd = 10000  # $10,000 debt
        collateral_usd = 8500  # $8,500 collateral (undercollateralized)
        bonus = 0.05  # 5% liquidation bonus
        provider = "aave_v3"
        gas_price = 30  # 30 gwei
        eth_price = 2000  # $2,000 ETH
        
        result = self.calculate(
            debt_amount_usd=debt_usd,
            collateral_amount_usd=collateral_usd,
            liquidation_bonus=bonus,
            flash_loan_provider=provider,
            gas_price_gwei=gas_price,
            eth_price_usd=eth_price,
            chain_id=1
        )
        
        print(f"Debt Amount:      ${debt_usd:,.2f}")
        print(f"Collateral Value: ${collateral_usd:,.2f}")
        print(f"Liquidation Bonus: {bonus * 100:.1f}%")
        print()
        print("--- Profit Breakdown ---")
        print(f"Gross Profit:     ${result.gross_profit_usd:,.2f}")
        print(f"Flash Loan Fee:   ${result.flash_loan_fee_usd:,.2f}")
        print(f"Gas Cost:         ${result.gas_cost_usd:,.2f}")
        print(f"Net Profit:       ${result.net_profit_usd:,.2f}")
        print()
        print(f"Profitable:       {'YES' if result.is_profitable else 'NO'}")
        print(f"ROI:              {result.roi_percent:.1f}%")
        print("=" * 60)

if __name__ == "__main__":
    calc = ProfitabilityCalculator()
    calc.example_calculation()
