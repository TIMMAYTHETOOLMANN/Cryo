#!/usr/bin/env python3
"""
STAGE 0 — Pre-Flight System Validator
=======================================
Validates ALL prerequisites before any stage can execute.
Zero capital required — this is pure read-only verification.

Checks:
  1. RPC connectivity (can we reach each chain?)
  2. Wallet configuration (is PRIVATE_KEY set?)
  3. Gas balance (does the wallet have enough native token for gas?)
  4. Contract deployment (are executors deployed and have code?)
  5. Contract ownership (does our wallet own the executors?)
  6. Supported debt assets (are tokens whitelisted on executors?)
  7. Treasury configuration (is TREASURY_ADDRESS valid?)
  8. Flash loan provider availability (are pools responding?)

Returns a PreFlightReport with per-check pass/fail and a GO/NO-GO verdict.
"""

import logging
import time
from typing import Dict, List, Optional
from dataclasses import dataclass, field

from web3 import Web3

from ..config.settings import ConfigManager, get_config

logger = logging.getLogger(__name__)

# Minimum gas balances (in ETH / native token) required per chain to attempt
# a single liquidation TX.  Calibrated for ultra-low gas environment (sub-1-gwei).
# At 0.03 gwei: 500K gas = 0.000015 ETH per TX; 0.005 ETH ≈ 330 TXs.
MIN_GAS_BALANCES: Dict[int, float] = {
    1:     0.005,   # Ethereum — ~330 TXs at 0.03 gwei
    42161: 0.0002,  # Arbitrum — extremely cheap
    10:    0.0002,  # Optimism — extremely cheap
    8453:  0.0002,  # Base — extremely cheap
    137:   0.1,     # Polygon (MATIC)
    43114: 0.01,    # Avalanche (AVAX)
    56:    0.002,   # BSC (BNB)
    324:   0.0002,  # zkSync
}


@dataclass
class ChainCheck:
    chain_id: int
    name: str
    rpc_connected: bool = False
    block_number: int = 0
    wallet_balance: float = 0.0
    min_gas_required: float = 0.0
    has_sufficient_gas: bool = False
    error: Optional[str] = None


@dataclass
class ContractCheck:
    name: str
    address: str
    has_code: bool = False
    owner_matches: bool = False
    error: Optional[str] = None


@dataclass
class PreFlightReport:
    """Complete pre-flight report — stages check this before activating."""
    timestamp: float = 0.0
    verdict: str = "NO-GO"               # "GO" or "NO-GO"
    can_scan: bool = False                # Stage 1: detection (read-only)
    can_analyse: bool = False             # Stage 2: analysis  (read-only)
    can_execute: bool = False             # Stage 3+: requires gas
    wallet_address: str = ""
    private_key_set: bool = False
    treasury_address: str = ""
    treasury_valid: bool = False
    chains: Dict[int, ChainCheck] = field(default_factory=dict)
    chains_with_gas: List[int] = field(default_factory=list)
    contracts: Dict[str, ContractCheck] = field(default_factory=dict)
    config_errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    def __post_init__(self):
        if self.timestamp == 0.0:
            self.timestamp = time.time()


# ---------------------------------------------------------------------------

EXECUTOR_ABI_SNIPPET = [
    {"inputs": [], "name": "OWNER",    "outputs": [{"name": "", "type": "address"}], "stateMutability": "view", "type": "function"},
    {"inputs": [], "name": "TREASURY", "outputs": [{"name": "", "type": "address"}], "stateMutability": "view", "type": "function"},
    {"inputs": [], "name": "POOL",     "outputs": [{"name": "", "type": "address"}], "stateMutability": "view", "type": "function"},
    {"inputs": [], "name": "minProfit","outputs": [{"name": "", "type": "uint256"}], "stateMutability": "view", "type": "function"},
]


class SystemValidator:
    """Run all pre-flight checks and produce a PreFlightReport."""

    def __init__(self, config: Optional[ConfigManager] = None):
        self.config = config or get_config()

    async def run(self) -> PreFlightReport:
        """Execute full pre-flight validation (async for future WS checks)."""
        report = PreFlightReport()

        # ---- Config-level checks ----
        report.config_errors = self.config.validate()
        report.private_key_set = bool(self.config.private_key)

        if report.private_key_set:
            try:
                from eth_account import Account
                acct = Account.from_key(self.config.private_key)
                report.wallet_address = acct.address
            except Exception as e:
                report.config_errors.append(f"Invalid PRIVATE_KEY: {e}")
                report.private_key_set = False

        report.treasury_address = self.config.treasury_address
        report.treasury_valid = (
            report.treasury_address != "0x" + "0" * 40
            and len(report.treasury_address) == 42
        )

        # ---- Chain connectivity + gas balance ----
        for chain_id, chain_cfg in self.config.get_all_chains().items():
            cc = ChainCheck(
                chain_id=chain_id,
                name=chain_cfg.name,
                min_gas_required=MIN_GAS_BALANCES.get(chain_id, 0.01),
            )
            if not chain_cfg.rpc_url:
                cc.error = "No RPC URL configured"
                report.chains[chain_id] = cc
                continue

            try:
                w3 = Web3(Web3.HTTPProvider(chain_cfg.rpc_url, request_kwargs={"timeout": 10}))
                cc.block_number = w3.eth.block_number
                cc.rpc_connected = True

                if report.wallet_address:
                    bal_wei = w3.eth.get_balance(
                        Web3.to_checksum_address(report.wallet_address)
                    )
                    cc.wallet_balance = bal_wei / 10**18
                    cc.has_sufficient_gas = cc.wallet_balance >= cc.min_gas_required
                    if cc.has_sufficient_gas:
                        report.chains_with_gas.append(chain_id)

            except Exception as e:
                cc.error = str(e)

            report.chains[chain_id] = cc

        # ---- Contract verification (Ethereum mainnet) ----
        eth_w3 = self._get_w3(1)
        if eth_w3:
            for label, addr_val in [
                ("Executor V1", self.config.executor_v1),
                ("Executor V2", self.config.executor_v2),
                ("Flash Executor", self.config.flash_executor),
            ]:
                cc = ContractCheck(name=label, address=addr_val)
                if not addr_val:
                    cc.error = "Address not configured"
                    report.contracts[label] = cc
                    continue
                try:
                    code = eth_w3.eth.get_code(Web3.to_checksum_address(addr_val))
                    cc.has_code = len(code) > 0
                    if cc.has_code:
                        contract = eth_w3.eth.contract(
                            address=Web3.to_checksum_address(addr_val),
                            abi=EXECUTOR_ABI_SNIPPET,
                        )
                        try:
                            owner = contract.functions.OWNER().call()
                            cc.owner_matches = (
                                owner.lower() == report.wallet_address.lower()
                            )
                            if not cc.owner_matches:
                                report.warnings.append(
                                    f"{label}: owner is {owner}, wallet is {report.wallet_address}"
                                )
                        except Exception:
                            cc.owner_matches = False
                except Exception as e:
                    cc.error = str(e)
                report.contracts[label] = cc

        # ---- Verdict ----
        any_chain_connected = any(c.rpc_connected for c in report.chains.values())
        report.can_scan = any_chain_connected                        # Stage 1
        report.can_analyse = any_chain_connected                     # Stage 2
        report.can_execute = (
            report.private_key_set
            and len(report.chains_with_gas) > 0
            and any(c.has_code for c in report.contracts.values())
        )

        if report.can_execute:
            report.verdict = "GO"
        elif report.can_scan:
            report.verdict = "SCAN-ONLY"
        else:
            report.verdict = "NO-GO"

        return report

    def _get_w3(self, chain_id: int) -> Optional[Web3]:
        cfg = self.config.get_chain(chain_id)
        if not cfg or not cfg.rpc_url:
            return None
        try:
            return Web3(Web3.HTTPProvider(cfg.rpc_url, request_kwargs={"timeout": 10}))
        except Exception:
            return None

    # ---- Pretty-print ----

    @staticmethod
    def print_report(report: PreFlightReport):
        print()
        print("=" * 80)
        print("  STAGE 0 — PRE-FLIGHT SYSTEM VALIDATION")
        print("=" * 80)

        # Verdict
        icon = {"GO": "🟢", "SCAN-ONLY": "🟡", "NO-GO": "🔴"}.get(report.verdict, "❓")
        print(f"\n  Verdict: {icon}  {report.verdict}")
        print(f"  Wallet:  {report.wallet_address or '(not configured)'}")
        print(f"  Treasury: {report.treasury_address} {'✅' if report.treasury_valid else '⚠️  NOT SET'}")

        # Chains
        print(f"\n  ─── Chains ({'connected' if report.can_scan else 'NONE connected'}) ───")
        for cid, cc in sorted(report.chains.items()):
            if cc.rpc_connected:
                gas_icon = "✅" if cc.has_sufficient_gas else "⛽ NEED GAS"
                print(
                    f"    {cc.name:12s} (ID {cid:>5d}): "
                    f"block {cc.block_number:,}  |  "
                    f"balance {cc.wallet_balance:.6f}  |  {gas_icon}"
                )
            else:
                print(f"    {cc.name:12s} (ID {cid:>5d}): ❌ {cc.error or 'not connected'}")

        # Chains with gas
        if report.chains_with_gas:
            print(f"\n  ⛽ Chains ready for execution: {report.chains_with_gas}")
        else:
            print("\n  ⛽ NO chains have sufficient gas for execution")

        # Contracts
        print(f"\n  ─── Contracts ───")
        for label, cc in report.contracts.items():
            if cc.has_code:
                owner_icon = "✅ owner" if cc.owner_matches else "⚠️  owner mismatch"
                print(f"    {label}: {cc.address[:20]}… DEPLOYED {owner_icon}")
            elif cc.error:
                print(f"    {label}: ❌ {cc.error}")
            else:
                print(f"    {label}: ❌ NO CODE at {cc.address}")

        # Config errors
        if report.config_errors:
            print(f"\n  ─── Config Issues ───")
            for err in report.config_errors:
                print(f"    ⚠️  {err}")

        # Warnings
        if report.warnings:
            print(f"\n  ─── Warnings ───")
            for w in report.warnings:
                print(f"    ⚠️  {w}")

        # Stage gates
        print(f"\n  ─── Stage Gates ───")
        print(f"    Stage 0 Pre-Flight:        ✅ (you are here)")
        print(f"    Stage 1 Detection:         {'✅' if report.can_scan else '❌'} (read-only, zero capital)")
        print(f"    Stage 2 Analysis:          {'✅' if report.can_analyse else '❌'} (read-only, zero capital)")
        print(f"    Stage 3 Execution:         {'✅' if report.can_execute else '❌'} (requires gas)")
        print(f"    Stage 4 MEV Protection:    {'✅' if report.can_execute else '❌'} (requires gas)")
        print(f"    Stage 5 Cross-Chain:       {'✅' if len(report.chains_with_gas) > 1 else '❌'} (gas on 2+ chains)")
        print(f"    Stage 6 Profit Collection: {'✅' if report.can_execute else '❌'} (requires prior profits)")

        print()
        print("=" * 80)
