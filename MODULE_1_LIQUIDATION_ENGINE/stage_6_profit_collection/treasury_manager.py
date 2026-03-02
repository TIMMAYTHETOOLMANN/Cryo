#!/usr/bin/env python3
"""
STAGE 6 — Treasury Manager
============================
Monitors profit collection, tracks balances across chains,
and optionally self-funds gas on deficit chains using profits
from surplus chains.
"""

import logging
import time
from typing import Dict, List, Optional
from dataclasses import dataclass

from web3 import Web3

from ..config.settings import ConfigManager, get_config

logger = logging.getLogger(__name__)


@dataclass
class ChainBalance:
    chain_id: int
    name: str
    treasury_balance: float      # native token in treasury
    executor_balance: float      # native token sitting in executor contracts
    total: float = 0.0

    def __post_init__(self):
        self.total = self.treasury_balance + self.executor_balance


@dataclass
class TreasurySnapshot:
    timestamp: float
    total_usd: float
    per_chain: Dict[int, ChainBalance]
    cumulative_profit_usd: float


class TreasuryManager:
    """Track and manage treasury balances across all chains."""

    _NATIVE_PRICES: Dict[int, float] = {
        1: 2500.0, 42161: 2500.0, 10: 2500.0, 8453: 2500.0,
        137: 0.50, 43114: 25.0, 56: 300.0, 324: 2500.0,
    }

    def __init__(self, config: Optional[ConfigManager] = None):
        self.config = config or get_config()
        self._w3_cache: Dict[int, Web3] = {}
        self._initial_snapshot: Optional[TreasurySnapshot] = None
        self._snapshots: List[TreasurySnapshot] = []

    def snapshot(self) -> TreasurySnapshot:
        """Take a point-in-time snapshot of treasury across all chains."""
        treasury = self.config.treasury_address
        per_chain: Dict[int, ChainBalance] = {}
        total_usd = 0.0

        for chain_id, chain_cfg in self.config.get_all_chains().items():
            w3 = self._get_w3(chain_id)
            if not w3:
                continue
            try:
                t_bal = w3.eth.get_balance(Web3.to_checksum_address(treasury)) / 10**18

                # Check executor balances (tokens may sit there before sweep)
                e_bal = 0.0
                for addr in [self.config.executor_v1, self.config.executor_v2, self.config.flash_executor]:
                    if addr:
                        try:
                            e_bal += w3.eth.get_balance(Web3.to_checksum_address(addr)) / 10**18
                        except Exception:
                            pass

                cb = ChainBalance(chain_id=chain_id, name=chain_cfg.name,
                                  treasury_balance=t_bal, executor_balance=e_bal)
                per_chain[chain_id] = cb
                native_price = self._NATIVE_PRICES.get(chain_id, 2500.0)
                total_usd += cb.total * native_price
            except Exception as e:
                logger.debug(f"Treasury check failed on chain {chain_id}: {e}")

        cumulative = 0.0
        if self._initial_snapshot:
            cumulative = total_usd - self._initial_snapshot.total_usd

        snap = TreasurySnapshot(
            timestamp=time.time(), total_usd=total_usd,
            per_chain=per_chain, cumulative_profit_usd=cumulative,
        )
        self._snapshots.append(snap)
        if self._initial_snapshot is None:
            self._initial_snapshot = snap
        return snap

    def print_snapshot(self, snap: Optional[TreasurySnapshot] = None):
        if snap is None:
            snap = self.snapshot()
        print()
        print("=" * 80)
        print("  STAGE 6 — TREASURY STATUS")
        print("=" * 80)
        for cid, cb in sorted(snap.per_chain.items()):
            print(
                f"  {cb.name:12s}  treasury {cb.treasury_balance:.6f}  "
                f"executors {cb.executor_balance:.6f}  "
                f"total {cb.total:.6f}"
            )
        print(f"\n  💰 Total (USD): ${snap.total_usd:,.2f}")
        print(f"  📈 Cumulative profit: ${snap.cumulative_profit_usd:,.2f}")
        print("=" * 80)

    # ------------------------------------------------------------------

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
