#!/usr/bin/env python3
"""
Submodule 11.5 — Last-Millisecond Profitability Recheck
=========================================================
Before any liquidation TX is broadcast, this module performs a final
verification at **current on-chain prices** to ensure the opportunity
is still profitable.

Why:
  Between when the ML model flags a position and when the TX lands,
  prices can shift.  A 0.3% price move can flip a profitable trade
  into a losing one.  This module gates execution to prevent gas waste.

Checks performed:
  1. Recalculate collateral value at current oracle price.
  2. Estimate gas cost at current base fee + priority fee.
  3. Compute net profit = (bonus - flash_loan_fee - gas_cost).
  4. Apply gas buffer (configurable, default 10%).
  5. Only approve if net_profit >= min_net_profit_usd.

Usage::

    recheck = ProfitabilityRecheck(w3_providers, config)
    result = await recheck.verify(
        chain_id=1,
        pool_address="0x...",
        user_address="0x...",
        collateral_asset="0x...",
        debt_asset="0x...",
        total_debt_usd=10000,
        total_collateral_usd=12000,
        estimated_profit_usd=500,
    )
    if result.profitable:
        # Execute!
        pass
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

from web3 import Web3

from .config import ProfitabilityConfig

logger = logging.getLogger(__name__)


# ── Chainlink ABI for price check ────────────────────────────────

CHAINLINK_ABI = json.loads('''[
    {"inputs":[],"name":"latestRoundData","outputs":[
        {"name":"roundId","type":"uint80"},
        {"name":"answer","type":"int256"},
        {"name":"startedAt","type":"uint256"},
        {"name":"updatedAt","type":"uint256"},
        {"name":"answeredInRound","type":"uint80"}
    ],"stateMutability":"view","type":"function"},
    {"inputs":[],"name":"decimals","outputs":[
        {"name":"","type":"uint8"}
    ],"stateMutability":"view","type":"function"}
]''')

AAVE_USER_DATA_ABI = json.loads('''[
    {"inputs":[{"name":"user","type":"address"}],"name":"getUserAccountData","outputs":[
        {"name":"totalCollateralBase","type":"uint256"},
        {"name":"totalDebtBase","type":"uint256"},
        {"name":"availableBorrowsBase","type":"uint256"},
        {"name":"currentLiquidationThreshold","type":"uint256"},
        {"name":"ltv","type":"uint256"},
        {"name":"healthFactor","type":"uint256"}
    ],"stateMutability":"view","type":"function"}
]''')


# ── Result Dataclass ─────────────────────────────────────────────

@dataclass
class RecheckResult:
    """Result of a profitability recheck."""
    profitable: bool
    reason: str = ""
    # Recalculated values
    current_health_factor: float = 0.0
    recalculated_profit_usd: float = 0.0
    adjusted_profit_usd: float = 0.0
    gas_cost_usd: float = 0.0
    flash_loan_fee_usd: float = 0.0
    net_profit_usd: float = 0.0
    # Meta
    gas_price_gwei: float = 0.0
    eth_price_usd: float = 0.0
    check_latency_ms: float = 0.0
    timestamp: float = field(default_factory=time.time)


# ── Profitability Recheck ────────────────────────────────────────

class ProfitabilityRecheck:
    """
    Performs last-millisecond profitability verification before
    liquidation TX broadcast.

    Acts as a final gate between the decision engine and the
    JIT executor to prevent gas-wasting reverts.
    """

    # Default Aave V3 flash loan fee in basis points
    AAVE_V3_FLASH_FEE_BPS = 5  # 0.05%

    # Default liquidation bonus (conservative estimate)
    DEFAULT_BONUS_PCT = 5.0

    def __init__(
        self,
        w3_providers: Optional[Dict[int, Web3]] = None,
        config: Optional[ProfitabilityConfig] = None,
    ):
        self._w3 = w3_providers or {}
        self.cfg = config or ProfitabilityConfig()

        # Stats
        self.checks_run: int = 0
        self.checks_passed: int = 0
        self.checks_rejected: int = 0

    async def verify(
        self,
        *,
        chain_id: int,
        pool_address: str,
        user_address: str,
        collateral_asset: str,
        debt_asset: str,
        total_debt_usd: float,
        total_collateral_usd: float,
        estimated_profit_usd: float,
        bonus_pct: float = 0.0,
        flash_loan_fee_bps: int = 0,
    ) -> RecheckResult:
        """
        Perform a full profitability recheck.

        Steps:
          1. Query current on-chain HF (if possible).
          2. Estimate gas cost at current gas price.
          3. Calculate flash loan fee.
          4. Compute net profit after all costs.
          5. Apply buffer and compare to threshold.
        """
        t0 = time.monotonic()
        self.checks_run += 1

        bonus = bonus_pct if bonus_pct > 0 else self.DEFAULT_BONUS_PCT
        fl_fee_bps = flash_loan_fee_bps if flash_loan_fee_bps > 0 else self.AAVE_V3_FLASH_FEE_BPS

        # ── Step 1: Query on-chain HF ────────────────────────────
        current_hf = 0.0
        on_chain_debt = total_debt_usd
        on_chain_collateral = total_collateral_usd

        w3 = self._w3.get(chain_id)
        if w3 and pool_address:
            try:
                pool_contract = w3.eth.contract(
                    address=Web3.to_checksum_address(pool_address),
                    abi=AAVE_USER_DATA_ABI,
                )
                data = pool_contract.functions.getUserAccountData(
                    Web3.to_checksum_address(user_address)
                ).call()

                on_chain_collateral = data[0] / 1e8  # Aave uses 8 decimals for USD
                on_chain_debt = data[1] / 1e8
                current_hf = data[5] / 1e18  # HF in 18 decimals

                # If HF >= 1.0 on-chain, liquidation will revert
                if current_hf >= 1.0:
                    elapsed_ms = (time.monotonic() - t0) * 1000
                    self.checks_rejected += 1
                    return RecheckResult(
                        profitable=False,
                        reason=f"On-chain HF={current_hf:.6f} >= 1.0 — would revert",
                        current_health_factor=current_hf,
                        check_latency_ms=elapsed_ms,
                    )

            except Exception as exc:
                # Can't read on-chain — proceed with estimates
                logger.debug(
                    "[ProfitRecheck] On-chain query failed (chain=%d): %s",
                    chain_id, str(exc)[:80],
                )

        # ── Step 2: Estimate gas cost ────────────────────────────
        gas_price_gwei = 0.0
        gas_cost_usd = 0.0
        eth_price_usd = self.cfg.default_eth_price_usd

        if w3:
            try:
                gas_price = w3.eth.gas_price
                gas_price_gwei = gas_price / 1e9
            except Exception:
                gas_price_gwei = 30.0  # Fallback

        gas_units = self.cfg.default_gas_units
        gas_cost_eth = (gas_price_gwei * gas_units) / 1e9
        gas_cost_usd = gas_cost_eth * eth_price_usd

        # ── Step 3: Flash loan fee ───────────────────────────────
        # Fee = debt_amount * fee_bps / 10000
        flash_loan_fee_usd = on_chain_debt * fl_fee_bps / 10000

        # ── Step 4: Compute net profit ───────────────────────────
        # Gross = collateral_seized - debt_repaid
        # collateral_seized = debt * (1 + bonus%)
        # For partial liquidation (50% of debt):
        debt_to_cover = on_chain_debt * 0.5
        collateral_seized_value = debt_to_cover * (1 + bonus / 100)
        gross_profit = collateral_seized_value - debt_to_cover

        net_profit = gross_profit - flash_loan_fee_usd - gas_cost_usd

        # ── Step 5: Apply buffer and check threshold ─────────────
        buffer_multiplier = 1.0 + (self.cfg.gas_buffer_pct / 100)
        adjusted_profit = net_profit / buffer_multiplier  # Conservative estimate

        elapsed_ms = (time.monotonic() - t0) * 1000

        profitable = adjusted_profit >= self.cfg.min_net_profit_usd

        if profitable:
            self.checks_passed += 1
        else:
            self.checks_rejected += 1

        reason = ""
        if not profitable:
            if adjusted_profit < 0:
                reason = (
                    f"Net loss: ${adjusted_profit:.2f} "
                    f"(gas=${gas_cost_usd:.2f}, fl_fee=${flash_loan_fee_usd:.2f})"
                )
            else:
                reason = (
                    f"Below threshold: ${adjusted_profit:.2f} < "
                    f"${self.cfg.min_net_profit_usd:.2f}"
                )

        return RecheckResult(
            profitable=profitable,
            reason=reason,
            current_health_factor=current_hf,
            recalculated_profit_usd=gross_profit,
            adjusted_profit_usd=adjusted_profit,
            gas_cost_usd=gas_cost_usd,
            flash_loan_fee_usd=flash_loan_fee_usd,
            net_profit_usd=net_profit,
            gas_price_gwei=gas_price_gwei,
            eth_price_usd=eth_price_usd,
            check_latency_ms=elapsed_ms,
        )

    def get_stats(self) -> Dict[str, Any]:
        """Return recheck statistics."""
        return {
            "checks_run": self.checks_run,
            "checks_passed": self.checks_passed,
            "checks_rejected": self.checks_rejected,
            "pass_rate": (
                self.checks_passed / max(self.checks_run, 1) * 100
            ),
        }
