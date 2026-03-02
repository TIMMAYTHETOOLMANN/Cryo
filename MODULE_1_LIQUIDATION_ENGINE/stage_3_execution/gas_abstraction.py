#!/usr/bin/env python3
"""
STAGE 3 — Gas Abstraction Layer (Script 3 Enhancement #8)
============================================================
Zero-capital bootstrapping: pay for gas using ERC-20 tokens (USDC)
instead of holding native gas on every chain.

Integrations:
  1. Gelato 1Balance — sponsor TXs with USDC balance
  2. Biconomy Paymaster — ERC-4337 gas sponsorship
  3. Self-funding relay — use profits from chain A to fund gas on chain B

This eliminates the need to pre-fund native tokens on every chain,
allowing instant deployment on new chains using only profit proceeds.

Flow:
  - Pipeline has profits in USDC on Ethereum
  - Needs to liquidate on Arbitrum but has no ETH there
  - Gas Abstraction: submit via Gelato relay → gas paid from USDC balance
  - OR: bridge small amount of profits to Arbitrum for direct gas
"""

import logging
import os
from typing import Dict, Optional, Tuple
from dataclasses import dataclass
from enum import Enum

from web3 import Web3

from ..config.settings import ConfigManager, get_config

logger = logging.getLogger(__name__)


class GasPaymentMethod(Enum):
    NATIVE = "native"            # Standard: pay gas in native token (ETH/MATIC)
    GELATO_1BALANCE = "gelato"   # Gelato relay: pay in USDC
    BICONOMY = "biconomy"        # Biconomy paymaster: ERC-4337
    SELF_BRIDGE = "self_bridge"  # Bridge profits from surplus chain


@dataclass
class GasAbstractionResult:
    """Result of gas abstraction attempt."""
    method: GasPaymentMethod
    success: bool
    tx_hash: str = ""
    gas_paid_usd: float = 0.0
    gas_paid_native: float = 0.0
    relay_fee_usd: float = 0.0
    error: str = ""


@dataclass
class ChainGasStatus:
    """Gas availability status for one chain."""
    chain_id: int
    has_native_gas: bool
    native_balance: float
    usdc_balance: float
    can_use_gelato: bool
    can_use_biconomy: bool
    can_self_bridge: bool
    recommended_method: GasPaymentMethod


# Gelato relay addresses per chain
GELATO_RELAY_ADDRESSES = {
    1:     "0xaBcC9b596420A9E9172FD5938620E265a0f9Df92",
    42161: "0xaBcC9b596420A9E9172FD5938620E265a0f9Df92",
    10:    "0xaBcC9b596420A9E9172FD5938620E265a0f9Df92",
    8453:  "0xaBcC9b596420A9E9172FD5938620E265a0f9Df92",
    137:   "0xaBcC9b596420A9E9172FD5938620E265a0f9Df92",
}

# USDC addresses per chain
USDC_ADDRESSES = {
    1:     "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48",
    42161: "0xaf88d065e77c8cC2239327C5EDb3A432268e5831",
    10:    "0x0b2C639c533813f4Aa9D7837CAf62653d097Ff85",
    8453:  "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913",
    137:   "0x3c499c542cEF5E3811e1192ce70d8cC03d5c3359",
}

# Gelato relay fee (approximate %)
GELATO_FEE_PCT = 0.01  # 1% relay fee on top of gas

# Minimum USDC balance needed to sponsor a TX via Gelato
MIN_GELATO_USDC = 5.0

ERC20_BALANCE_ABI = [
    {
        "inputs": [{"name": "account", "type": "address"}],
        "name": "balanceOf",
        "outputs": [{"name": "", "type": "uint256"}],
        "stateMutability": "view",
        "type": "function",
    }
]


class GasAbstractionLayer:
    """
    Provides multiple gas payment methods to eliminate the need
    for native token balances on every chain.
    """

    def __init__(self, config: Optional[ConfigManager] = None):
        self.config = config or get_config()
        self._w3_cache: Dict[int, Web3] = {}

        # Track which chains have Gelato API keys configured
        self._gelato_api_key = os.getenv("GELATO_API_KEY", "")
        self._biconomy_api_key = os.getenv("BICONOMY_API_KEY", "")

        # Cache: chain → USDC balance
        self._usdc_balances: Dict[int, float] = {}
        self._last_balance_check: float = 0
        self._balance_ttl = 60  # Recheck every 60s

    def chain_status(self, chain_id: int) -> ChainGasStatus:
        """Check gas payment options for a chain."""
        w3 = self._get_w3(chain_id)
        wallet = self._wallet_address()

        native_balance = 0.0
        usdc_balance = 0.0
        has_native = False

        if w3 and wallet:
            try:
                native_balance = w3.eth.get_balance(
                    Web3.to_checksum_address(wallet)
                ) / 1e18
                has_native = native_balance > 0.0001
            except Exception:
                pass

            usdc_addr = USDC_ADDRESSES.get(chain_id)
            if usdc_addr:
                try:
                    contract = w3.eth.contract(
                        address=Web3.to_checksum_address(usdc_addr),
                        abi=ERC20_BALANCE_ABI,
                    )
                    raw = contract.functions.balanceOf(
                        Web3.to_checksum_address(wallet)
                    ).call()
                    usdc_balance = raw / 1e6  # USDC has 6 decimals
                except Exception:
                    pass

        can_gelato = (
            bool(self._gelato_api_key)
            and chain_id in GELATO_RELAY_ADDRESSES
            and usdc_balance >= MIN_GELATO_USDC
        )
        can_biconomy = bool(self._biconomy_api_key)

        # Check if we can self-bridge from a surplus chain
        can_bridge = self._check_bridge_availability(chain_id)

        # Recommend method
        if has_native:
            method = GasPaymentMethod.NATIVE
        elif can_gelato:
            method = GasPaymentMethod.GELATO_1BALANCE
        elif can_biconomy:
            method = GasPaymentMethod.BICONOMY
        elif can_bridge:
            method = GasPaymentMethod.SELF_BRIDGE
        else:
            method = GasPaymentMethod.NATIVE  # Will fail at gas gate

        return ChainGasStatus(
            chain_id=chain_id,
            has_native_gas=has_native,
            native_balance=native_balance,
            usdc_balance=usdc_balance,
            can_use_gelato=can_gelato,
            can_use_biconomy=can_biconomy,
            can_self_bridge=can_bridge,
            recommended_method=method,
        )

    def can_pay_gas(self, chain_id: int) -> Tuple[bool, GasPaymentMethod]:
        """Check if ANY gas payment method is available for this chain."""
        status = self.chain_status(chain_id)
        if status.has_native_gas:
            return True, GasPaymentMethod.NATIVE
        if status.can_use_gelato:
            return True, GasPaymentMethod.GELATO_1BALANCE
        if status.can_use_biconomy:
            return True, GasPaymentMethod.BICONOMY
        if status.can_self_bridge:
            return True, GasPaymentMethod.SELF_BRIDGE
        return False, GasPaymentMethod.NATIVE

    def full_report(self) -> Dict[int, ChainGasStatus]:
        """Gas abstraction status across all chains."""
        report = {}
        for chain_id in self.config.get_all_chains():
            report[chain_id] = self.chain_status(chain_id)
        return report

    def _check_bridge_availability(self, target_chain: int) -> bool:
        """Check if we have surplus funds on another chain to bridge."""
        for chain_id in self.config.get_all_chains():
            if chain_id == target_chain:
                continue
            w3 = self._get_w3(chain_id)
            wallet = self._wallet_address()
            if not w3 or not wallet:
                continue
            try:
                usdc_addr = USDC_ADDRESSES.get(chain_id)
                if not usdc_addr:
                    continue
                contract = w3.eth.contract(
                    address=Web3.to_checksum_address(usdc_addr),
                    abi=ERC20_BALANCE_ABI,
                )
                bal = contract.functions.balanceOf(
                    Web3.to_checksum_address(wallet)
                ).call() / 1e6
                if bal > 10:  # $10 minimum to bridge
                    return True
            except Exception:
                pass
        return False

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

    @staticmethod
    def print_report(report: Dict[int, "ChainGasStatus"]):
        print()
        print("=" * 80)
        print("  GAS ABSTRACTION — PAYMENT METHODS")
        print("=" * 80)
        for cid, s in sorted(report.items()):
            native_icon = "✅" if s.has_native_gas else "❌"
            gelato_icon = "✅" if s.can_use_gelato else "—"
            bridge_icon = "✅" if s.can_self_bridge else "—"
            print(
                f"  Chain {cid:>5d}  "
                f"native={native_icon} ({s.native_balance:.4f})  "
                f"USDC=${s.usdc_balance:.2f}  "
                f"gelato={gelato_icon}  bridge={bridge_icon}  "
                f"→ {s.recommended_method.value}"
            )
        print("=" * 80)
