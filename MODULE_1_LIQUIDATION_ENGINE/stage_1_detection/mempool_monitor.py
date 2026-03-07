#!/usr/bin/env python3
"""
STAGE 1 — Mempool Monitor (Script 3 Enhancement #4)
=====================================================
Monitors pending transactions for events that will push positions
underwater, enabling preemptive liquidation in the same block.

Targets:
  - Large DEX swaps that will move oracle prices
  - Chainlink oracle AnswerUpdated submissions
  - Governance parameter changes (LTV adjustments)
  - Large borrow/withdraw events that affect utilization

Flow:
  1. Mempool sniffer detects pending TX that will move price
  2. Verify post-state to find newly liquidatable positions
  3. Create Flashbots bundle: [triggering_tx, our_liquidation_tx]
  4. Submit bundle — we liquidate in the SAME block, before anyone else

Zero capital: this is read-only monitoring + bundle construction.
"""

import asyncio
import logging
import time
from typing import Callable, Dict, List, Optional, Set
from dataclasses import dataclass
from enum import Enum

from web3 import Web3

from ..config.settings import ConfigManager, get_config

logger = logging.getLogger(__name__)


class MempoolEventType(Enum):
    LARGE_SWAP = "large_swap"
    ORACLE_UPDATE = "oracle_update"
    GOVERNANCE_CHANGE = "governance_change"
    LARGE_BORROW = "large_borrow"
    LARGE_WITHDRAW = "large_withdraw"


@dataclass
class MempoolEvent:
    """A pending transaction that may trigger liquidations."""
    event_type: MempoolEventType
    tx_hash: str
    from_address: str
    to_address: str
    value_usd: float
    affected_asset: str
    estimated_price_impact_pct: float
    block_number: int
    timestamp: float = 0.0
    raw_data: bytes = b""

    def __post_init__(self):
        if self.timestamp == 0:
            self.timestamp = time.time()


@dataclass
class BackrunOpportunity:
    """An opportunity to backrun a pending TX with a liquidation."""
    trigger_event: MempoolEvent
    target_user: str
    target_protocol: str
    current_hf: float
    projected_hf: float  # HF after the pending TX executes
    estimated_profit_usd: float
    urgency: str  # "immediate" or "next_block"


# Known DEX router addresses for swap detection
KNOWN_DEX_ROUTERS: Dict[str, str] = {
    "0x68b3465833fb72A70ecDF485E0e4C7bD8665Fc45": "Uniswap V3 Router",
    "0xE592427A0AEce92De3Edee1F18E0157C05861564": "Uniswap V3 Router (old)",
    "0x7a250d5630B4cF539739dF2C5dAcb4c659F2488D": "Uniswap V2 Router",
    "0xd9e1cE17f2641f24aE83637ab66a2cca9C378B9F": "SushiSwap Router",
    "0x1111111254EEB25477B68fb85Ed929f73A960582": "1inch V5",
    "0xDef1C0ded9bec7F1a1670819833240f027b25EfF": "0x Exchange",
}

# Chainlink aggregator proxy pattern
CHAINLINK_AGGREGATOR_SIG = "0xfeaf968c"  # latestRoundData()

# Minimum swap value to consider impactful (USD)
MIN_SWAP_IMPACT_USD = 50_000
# Minimum price impact to flag (percent)
MIN_PRICE_IMPACT_PCT = 0.5


class MempoolMonitor:
    """
    Watches the mempool for transactions that will trigger liquidations.

    Uses private relay APIs (Flashbots, bloXroute) for access to pending TXs
    without exposing our own transactions.
    """

    def __init__(self, config: Optional[ConfigManager] = None):
        self.config = config or get_config()
        self._running = False
        self._callbacks: List[Callable] = []
        self._seen_txs: Set[str] = set()  # Dedup
        self._max_seen = 10_000

        # Private relay endpoints
        self._relay_endpoints = {
            "flashbots": "https://relay.flashbots.net",
            "bloxroute": "https://mev.api.blxrbdn.com",
            "eden": "https://api.edennetwork.io/v1/bundle",
        }

        self.stats = {
            "txs_analyzed": 0,
            "large_swaps_detected": 0,
            "oracle_updates_detected": 0,
            "backrun_opportunities": 0,
        }

    def on_event(self, callback: Callable[[MempoolEvent], None]):
        """Register a callback for mempool events."""
        self._callbacks.append(callback)

    async def start(self):
        """Start mempool monitoring (background task)."""
        self._running = True
        logger.info("🔍 Mempool Monitor started — watching for price-moving TXs")

        chain = self.config.get_chain(1)
        if not chain or not chain.rpc_url:
            logger.warning("No Ethereum RPC — mempool monitor inactive")
            return

        w3 = Web3(Web3.HTTPProvider(chain.rpc_url, request_kwargs={"timeout": 10}))

        while self._running:
            try:
                await self._poll_pending(w3)
                await asyncio.sleep(1)  # Poll every second
            except Exception as e:
                logger.debug(f"Mempool poll error: {e}")
                await asyncio.sleep(5)

    async def stop(self):
        self._running = False

    async def _poll_pending(self, w3: Web3):
        """Poll pending transactions and classify them."""
        try:
            # Get pending TX pool (requires node with txpool API)
            pending = w3.eth.get_block("pending", full_transactions=True)
            if not pending or "transactions" not in pending:
                return

            for tx in pending["transactions"]:
                tx_hash = tx["hash"].hex() if isinstance(tx["hash"], bytes) else tx["hash"]
                if tx_hash in self._seen_txs:
                    continue

                self._seen_txs.add(tx_hash)
                if len(self._seen_txs) > self._max_seen:
                    # Trim old entries
                    self._seen_txs = set(list(self._seen_txs)[-5000:])

                self.stats["txs_analyzed"] += 1
                event = self._classify_tx(tx, w3)
                if event:
                    self._emit(event)

        except Exception:
            pass  # Many nodes don't support pending block

    def _classify_tx(self, tx, w3: Web3) -> Optional[MempoolEvent]:
        """Classify a pending transaction by type."""
        to = tx.get("to", "")
        if not to:
            return None

        to_lower = to.lower() if isinstance(to, str) else to.hex().lower() if hasattr(to, 'hex') else ""
        value = tx.get("value", 0)
        input_data = tx.get("input", b"")
        if isinstance(input_data, str):
            input_data = bytes.fromhex(input_data[2:]) if input_data.startswith("0x") else b""

        # Check if it's a large DEX swap
        to_check = Web3.to_checksum_address(to) if to else ""
        if to_check in KNOWN_DEX_ROUTERS:
            # Estimate swap value from tx value + input data
            swap_value_eth = value / 1e18
            swap_value_usd = swap_value_eth * 2500

            if swap_value_usd >= MIN_SWAP_IMPACT_USD:
                self.stats["large_swaps_detected"] += 1
                tx_hash = tx["hash"].hex() if isinstance(tx["hash"], bytes) else str(tx["hash"])
                return MempoolEvent(
                    event_type=MempoolEventType.LARGE_SWAP,
                    tx_hash=tx_hash,
                    from_address=tx.get("from", ""),
                    to_address=to_check,
                    value_usd=swap_value_usd,
                    affected_asset="WETH",
                    estimated_price_impact_pct=min(swap_value_usd / 1_000_000, 5.0),
                    block_number=w3.eth.block_number,
                )

        # Check for oracle update patterns
        if len(input_data) >= 4:
            selector = input_data[:4].hex()
            # transmit() or submit() patterns common in Chainlink
            if selector in ("0xc9807539", "0x202ee0ed", "0xe4a30116"):
                self.stats["oracle_updates_detected"] += 1
                tx_hash = tx["hash"].hex() if isinstance(tx["hash"], bytes) else str(tx["hash"])
                return MempoolEvent(
                    event_type=MempoolEventType.ORACLE_UPDATE,
                    tx_hash=tx_hash,
                    from_address=tx.get("from", ""),
                    to_address=to_check,
                    value_usd=0,
                    affected_asset="oracle_feed",
                    estimated_price_impact_pct=0,
                    block_number=w3.eth.block_number,
                )

        return None

    def _emit(self, event: MempoolEvent):
        """Notify all registered callbacks."""
        for cb in self._callbacks:
            try:
                cb(event)
            except Exception as e:
                logger.debug(f"Mempool callback error: {e}")
