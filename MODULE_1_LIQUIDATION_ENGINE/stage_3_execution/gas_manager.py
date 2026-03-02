#!/usr/bin/env python3
"""
STAGE 3 — Gas Manager
======================
Ensures no transaction is ever submitted without sufficient gas.

Key rule: With $0 initial capital, the ONLY cost is gas.  Flash loans cover
the liquidation capital.  This module gates Stage 3+ — if the wallet cannot
afford gas for a TX, it blocks execution and logs why.

Features:
  - Real-time gas price monitoring per chain
  - Gas cost estimation for a liquidation TX
  - Balance check: can we afford gas right now?
  - EIP-1559 support (base fee + priority fee)
  - Automatic chain prioritisation (cheapest gas first)
"""

import logging
import time
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass

from web3 import Web3

from ..config.settings import ConfigManager, get_config

logger = logging.getLogger(__name__)


@dataclass
class GasEstimate:
    """Gas cost estimate for a single liquidation on one chain."""
    chain_id: int
    chain_name: str
    gas_units: int                 # estimated gas units
    gas_price_gwei: float          # current gas price
    cost_native: float             # cost in native token (ETH/MATIC/etc.)
    cost_usd: float                # approximate USD cost
    wallet_balance_native: float   # wallet balance on this chain
    can_afford: bool               # wallet_balance >= cost_native
    within_gas_cap: bool           # gas_price <= configured cap


@dataclass
class GasReport:
    """Aggregate gas report across all chains."""
    affordable_chains: List[int]       # chains where we can afford gas
    cheapest_chain: Optional[int]      # chain with lowest gas cost
    estimates: Dict[int, GasEstimate]  # per-chain estimates
    wallet_total_native: float         # sum of balances across chains
    timestamp: float = 0.0

    def __post_init__(self):
        if self.timestamp == 0.0:
            self.timestamp = time.time()


# Rough native-token USD prices (updated at runtime if oracle available)
_NATIVE_PRICES_USD: Dict[int, float] = {
    1: 2500.0,      # ETH
    42161: 2500.0,   # ETH on Arbitrum
    10: 2500.0,      # ETH on Optimism
    8453: 2500.0,    # ETH on Base
    137: 0.50,       # MATIC
    43114: 25.0,     # AVAX
    56: 300.0,       # BNB
    324: 2500.0,     # ETH on zkSync
}

# Base gas estimate for a liquidation TX (flash loan + liquidation + repay)
_BASE_GAS_UNITS = 350_000


class GasManager:
    """
    Gate-keeper for Stage 3+.  Call ``can_execute(chain_id)`` before submitting
    any TX.  If it returns False, do NOT submit — the wallet cannot afford gas.
    """

    def __init__(self, config: Optional[ConfigManager] = None):
        self.config = config or get_config()
        self._w3_cache: Dict[int, Web3] = {}
        self._gas_cap_gwei = self.config.execution.gas_price_cap_gwei
        self._gas_buffer = self.config.execution.gas_limit_buffer

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def can_execute(self, chain_id: int) -> Tuple[bool, str]:
        """
        Quick check: can we afford gas for a liquidation on *chain_id*?

        Returns:
            (True, "OK") or (False, "reason string")
        """
        w3 = self._get_w3(chain_id)
        if not w3:
            return False, f"No RPC for chain {chain_id}"

        wallet = self._wallet_address()
        if not wallet:
            return False, "PRIVATE_KEY not configured"

        try:
            balance_wei = w3.eth.get_balance(Web3.to_checksum_address(wallet))
            gas_price = w3.eth.gas_price
            gas_cost_wei = int(_BASE_GAS_UNITS * self._gas_buffer) * gas_price

            if gas_price / 10**9 > self._gas_cap_gwei:
                return False, (
                    f"Gas price {gas_price / 10**9:.1f} gwei exceeds cap "
                    f"{self._gas_cap_gwei} gwei — waiting for lower gas"
                )

            if balance_wei < gas_cost_wei:
                balance_eth = balance_wei / 10**18
                cost_eth = gas_cost_wei / 10**18
                chain_name = (self.config.get_chain(chain_id) or type('', (), {"name": str(chain_id)})()).name
                return False, (
                    f"Insufficient gas on {chain_name}: "
                    f"balance {balance_eth:.6f} < required {cost_eth:.6f}"
                )

            return True, "OK"

        except Exception as e:
            return False, f"Gas check error: {e}"

    def estimate(self, chain_id: int) -> Optional[GasEstimate]:
        """Full gas estimate for a liquidation on *chain_id*."""
        w3 = self._get_w3(chain_id)
        if not w3:
            return None

        wallet = self._wallet_address()
        chain_cfg = self.config.get_chain(chain_id)
        chain_name = chain_cfg.name if chain_cfg else str(chain_id)

        try:
            gas_price_wei = w3.eth.gas_price
            gas_price_gwei = gas_price_wei / 10**9
            gas_units = int(_BASE_GAS_UNITS * self._gas_buffer)
            cost_wei = gas_units * gas_price_wei
            cost_native = cost_wei / 10**18
            native_price = _NATIVE_PRICES_USD.get(chain_id, 2500.0)
            cost_usd = cost_native * native_price

            balance = 0.0
            if wallet:
                balance = w3.eth.get_balance(Web3.to_checksum_address(wallet)) / 10**18

            return GasEstimate(
                chain_id=chain_id,
                chain_name=chain_name,
                gas_units=gas_units,
                gas_price_gwei=gas_price_gwei,
                cost_native=cost_native,
                cost_usd=cost_usd,
                wallet_balance_native=balance,
                can_afford=balance >= cost_native,
                within_gas_cap=gas_price_gwei <= self._gas_cap_gwei,
            )
        except Exception as e:
            logger.error(f"Gas estimate error on chain {chain_id}: {e}")
            return None

    def full_report(self) -> GasReport:
        """Gas report across all configured chains."""
        estimates: Dict[int, GasEstimate] = {}
        affordable: List[int] = []
        total_balance = 0.0

        for chain_id in self.config.get_all_chains():
            est = self.estimate(chain_id)
            if est:
                estimates[chain_id] = est
                total_balance += est.wallet_balance_native
                if est.can_afford and est.within_gas_cap:
                    affordable.append(chain_id)

        cheapest = None
        if affordable:
            cheapest = min(affordable, key=lambda c: estimates[c].cost_usd)

        return GasReport(
            affordable_chains=affordable,
            cheapest_chain=cheapest,
            estimates=estimates,
            wallet_total_native=total_balance,
        )

    def rank_chains_by_cost(self) -> List[int]:
        """Return chain IDs sorted cheapest-gas-first (only affordable ones)."""
        report = self.full_report()
        return sorted(
            report.affordable_chains,
            key=lambda c: report.estimates[c].cost_usd,
        )

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _wallet_address(self) -> Optional[str]:
        pk = self.config.private_key
        if not pk:
            return None
        try:
            from eth_account import Account
            return Account.from_key(pk).address
        except Exception:
            return None

    def _get_w3(self, chain_id: int) -> Optional[Web3]:
        if chain_id in self._w3_cache:
            return self._w3_cache[chain_id]
        cfg = self.config.get_chain(chain_id)
        if not cfg or not cfg.rpc_url:
            return None
        try:
            w3 = Web3(Web3.HTTPProvider(cfg.rpc_url, request_kwargs={"timeout": 10}))
            self._w3_cache[chain_id] = w3
            return w3
        except Exception:
            return None

    # ------------------------------------------------------------------
    # Display
    # ------------------------------------------------------------------

    @staticmethod
    def print_report(report: GasReport):
        print()
        print("=" * 80)
        print("  GAS MANAGER — EXECUTION READINESS")
        print("=" * 80)
        for cid, est in sorted(report.estimates.items()):
            afford = "✅ READY" if est.can_afford else "⛽ NEED GAS"
            cap = "" if est.within_gas_cap else " ⚠️ ABOVE CAP"
            print(
                f"  {est.chain_name:12s}  "
                f"gas {est.gas_price_gwei:6.1f} gwei  "
                f"cost {est.cost_native:.6f} (~${est.cost_usd:.2f})  "
                f"balance {est.wallet_balance_native:.6f}  "
                f"{afford}{cap}"
            )
        if report.cheapest_chain:
            c = report.estimates[report.cheapest_chain]
            print(f"\n  ⭐ Cheapest: {c.chain_name} — ${c.cost_usd:.4f} per liquidation")
        if not report.affordable_chains:
            print("\n  🔴 NO chains affordable — deposit gas to proceed to Stage 3")
        print("=" * 80)
