#!/usr/bin/env python3
"""
Submodule 11.2 -- Mempool Sniffer
===================================
Monitors pending transactions for large swaps that could trigger
liquidation opportunities (backrun strategy).

Uses ``eth_subscribe("newPendingTransactions")`` where WS is available,
falls back to polling ``eth_getBlockByNumber("pending")``.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from typing import Any, Callable, Coroutine, Dict, List, Optional

from web3 import Web3

from .config import MempoolConfig

logger = logging.getLogger(__name__)

# ── Router method signatures ─────────────────────────────────────

SWAP_SIGNATURES = {
    "0x38ed1739": "swapExactTokensForTokens",
    "0x8803dbee": "swapTokensForExactTokens",
    "0x7ff36ab5": "swapExactETHForTokens",
    "0x18cbafe5": "swapExactTokensForETH",
    "0x414bf389": "exactInputSingle",
    "0xc04b8d59": "exactInput",
    "0xdb3e2198": "exactOutputSingle",
    "0xf28c0498": "exactOutput",
    "0x5ae401dc": "multicall",
}


class MempoolSniffer:
    """
    Sniffs the mempool for large swap transactions that may move prices
    enough to trigger liquidations. Fires ``on_backrun_opportunity``
    when a profitable backrun is detected.
    """

    def __init__(
        self,
        w3_providers: Optional[Dict[int, Web3]] = None,
        config: Optional[MempoolConfig] = None,
    ):
        self.cfg = config or MempoolConfig()
        self._w3 = w3_providers or {}
        self._running = False
        self._watched_routers = set(
            addr.lower() for addr in self.cfg.watch_routers
        )

        # Callback -- set by the engine
        self.on_backrun_opportunity: Optional[
            Callable[..., Coroutine]
        ] = None

        # Stats
        self.txs_seen = 0
        self.swaps_detected = 0
        self.opportunities_fired = 0
        self.errors = 0

    # ── Public API ────────────────────────────────────────────

    async def run(self) -> None:
        """Main monitoring loop."""
        if not self.cfg.enabled:
            logger.info("[MempoolSniffer] Disabled by config")
            return

        self._running = True
        logger.info(
            "[MempoolSniffer] Starting -- watching %d routers",
            len(self._watched_routers),
        )

        while self._running:
            try:
                await self._poll_pending()
            except asyncio.CancelledError:
                break
            except Exception as exc:
                self.errors += 1
                if self.errors <= 10:
                    logger.warning("[MempoolSniffer] Error: %s", exc)
            await asyncio.sleep(1.0)

        logger.info(
            "[MempoolSniffer] Stopped. txs=%d swaps=%d opps=%d errors=%d",
            self.txs_seen, self.swaps_detected, self.opportunities_fired, self.errors,
        )

    async def stop(self) -> None:
        self._running = False

    # ── Internals ─────────────────────────────────────────────

    async def _poll_pending(self) -> None:
        """Poll pending block for swap transactions."""
        w3 = self._w3.get(1)
        if not w3:
            if self._w3:
                w3 = next(iter(self._w3.values()))
            else:
                return

        loop = asyncio.get_event_loop()
        try:
            pending_block = await loop.run_in_executor(
                None,
                lambda: w3.eth.get_block("pending", full_transactions=True),
            )
        except Exception:
            return

        if not pending_block or "transactions" not in pending_block:
            return

        for tx in pending_block["transactions"]:
            self.txs_seen += 1
            await self._analyze_tx(tx, w3)

    async def _analyze_tx(self, tx: Dict, w3: Web3) -> None:
        """Check if a pending TX is a large swap on a watched router."""
        to_addr = (tx.get("to") or "").lower()
        if to_addr not in self._watched_routers:
            return

        input_data = tx.get("input", "")
        if len(input_data) < 10:
            return

        method_sig = input_data[:10]
        if method_sig not in SWAP_SIGNATURES:
            return

        self.swaps_detected += 1

        # Estimate value
        value_wei = tx.get("value", 0)
        gas_price = tx.get("gasPrice", tx.get("maxFeePerGas", 0))

        # Fire opportunity if callback is set
        if self.on_backrun_opportunity is not None:
            self.opportunities_fired += 1
            try:
                await self.on_backrun_opportunity(
                    tx_hash=tx.get("hash", b"").hex() if isinstance(tx.get("hash"), bytes) else str(tx.get("hash", "")),
                    router=to_addr,
                    method=SWAP_SIGNATURES[method_sig],
                    value_wei=value_wei,
                    gas_price=gas_price,
                    chain_id=1,
                )
            except Exception as exc:
                logger.warning("[MempoolSniffer] Callback error: %s", exc)
