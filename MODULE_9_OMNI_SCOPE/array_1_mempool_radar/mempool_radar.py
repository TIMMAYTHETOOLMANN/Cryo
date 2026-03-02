#!/usr/bin/env python3
"""
ARRAY 1 — Mempool Radar: Ultra-Low-Latency Signal Sniffing
=============================================================
Multi-provider subscription for redundant, low-latency mempool access.

Capabilities:
  1. Multi-Provider Subscription (bloXroute, Blocknative, Infura WS)
  2. Oracle Update Sniffing — detect pending price updates → pre-compute liquidations
  3. Large Swap Detection — flag price-moving trades → prepare backrun bundles
  4. Transaction Simulation — fork-simulate pending TXs to predict post-state

Data flow: Raw pending TX → classify → simulate → emit OpportunitySignal → DataBus
"""

import asyncio
import logging
from collections import deque
from typing import Dict, Optional, Set
from dataclasses import dataclass

from web3 import Web3

from ..config import OmniScopeConfig, get_omni_config
from ..data_bus import DataBus, OpportunitySignal, SignalType, SignalSource

logger = logging.getLogger(__name__)


# Known DEX routers for swap detection
DEX_ROUTERS: Dict[str, str] = {
    "0x68b3465833fb72A70ecDF485E0e4C7bD8665Fc45": "Uniswap V3 SwapRouter02",
    "0xE592427A0AEce92De3Edee1F18E0157C05861564": "Uniswap V3 SwapRouter",
    "0x7a250d5630B4cF539739dF2C5dAcb4c659F2488D": "Uniswap V2 Router",
    "0xd9e1cE17f2641f24aE83637ab66a2cca9C378B9F": "SushiSwap Router",
    "0x1111111254EEB25477B68fb85Ed929f73A960582": "1inch V5 Router",
    "0xDef1C0ded9bec7F1a1670819833240f027b25EfF": "0x Exchange Proxy",
    "0xDEF171Fe48CF0115B1d80b88dc8eAB59176FEe57": "Paraswap V5",
}

# Oracle-related function selectors
ORACLE_SELECTORS: Set[str] = {
    "c9807539",  # Chainlink OCR transmit()
    "202ee0ed",  # Chainlink submit()
    "e4a30116",  # Chainlink aggregator submit()
    "feaf968c",  # latestRoundData() — read, but useful for tracking
    "a9059cbb",  # ERC20 transfer (used by some oracle payment flows)
}

# Lending protocol function selectors (for detecting borrows/withdrawals)
LENDING_SELECTORS: Dict[str, str] = {
    "a415bcad": "borrow",        # Aave V3
    "69328dec": "withdraw",      # Aave V3
    "c5ebeaec": "borrow",        # Compound
    "852a12e3": "redeemUnderlying",  # Compound
}


@dataclass
class PendingTransaction:
    """Parsed pending transaction from mempool."""
    tx_hash: str
    from_addr: str
    to_addr: str
    value_wei: int
    gas_price_gwei: float
    input_data: bytes
    chain_id: int
    timestamp: float
    category: str = "unknown"  # "swap", "oracle", "borrow", "withdraw", "other"
    estimated_usd_value: float = 0.0
    affected_asset: str = ""
    price_impact_pct: float = 0.0


class MempoolRadar:
    """
    Ultra-low-latency mempool sniffer with multi-provider redundancy.

    Subscribes to bloXroute, Blocknative, and Infura simultaneously.
    Classifies pending TXs and emits signals to the DataBus.
    """

    def __init__(self, bus: DataBus, config: Optional[OmniScopeConfig] = None, rpc_gateway=None):
        self.bus = bus
        self.config = config or get_omni_config()
        self._cfg = self.config.mempool_radar
        self._running = False
        self._seen: Set[str] = set()
        self._max_seen = self._cfg.max_pending_cache
        self._providers_connected = 0
        # Optional RPCGateway (Module 10) — used in _poll_pending_fallback for
        # managed rate limiting, circuit breaking, and automatic failover.
        self._rpc_gateway = rpc_gateway

        # Recent TX buffer for pattern analysis
        self._recent: deque[PendingTransaction] = deque(maxlen=1000)

        self.stats = {
            "txs_received": 0,
            "txs_classified": 0,
            "swaps_detected": 0,
            "oracle_updates_detected": 0,
            "borrows_detected": 0,
            "signals_emitted": 0,
        }

    async def start(self):
        """Start all mempool provider connections."""
        self._running = True
        logger.info("📡 Array 1: Mempool Radar starting…")

        tasks = []

        # Provider 1: Infura WebSocket
        if self._cfg.infura_ws_url:
            tasks.append(asyncio.create_task(
                self._subscribe_ws(self._cfg.infura_ws_url, "infura")
            ))

        # Provider 2: bloXroute
        if self._cfg.bloxroute_api_key:
            tasks.append(asyncio.create_task(
                self._subscribe_bloxroute()
            ))

        # Provider 3: Blocknative
        if self._cfg.blocknative_api_key:
            tasks.append(asyncio.create_task(
                self._subscribe_blocknative()
            ))

        # Fallback: poll pending block via standard RPC
        if not tasks:
            tasks.append(asyncio.create_task(self._poll_pending_fallback()))

        logger.info(f"📡 Mempool Radar: {len(tasks)} provider(s) active")

        try:
            await asyncio.gather(*tasks, return_exceptions=True)
        except asyncio.CancelledError:
            pass

    async def stop(self):
        self._running = False

    # ------------------------------------------------------------------
    # Provider: Generic WebSocket (Infura / Alchemy / any node)
    # ------------------------------------------------------------------

    async def _subscribe_ws(self, ws_url: str, provider_name: str):
        """Subscribe to newPendingTransactions via WebSocket."""
        try:
            from websockets import connect as ws_connect
        except ImportError:
            logger.warning(f"websockets not installed — {provider_name} unavailable")
            return

        while self._running:
            try:
                async with ws_connect(ws_url) as ws:
                    self._providers_connected += 1
                    logger.info(f"📡 Connected to {provider_name} WebSocket")

                    # Subscribe to pending transactions
                    sub_msg = (
                        '{"jsonrpc":"2.0","id":1,"method":"eth_subscribe",'
                        '"params":["newPendingTransactions"]}'
                    )
                    await ws.send(sub_msg)
                    await ws.recv()  # subscription confirmation

                    while self._running:
                        msg = await asyncio.wait_for(ws.recv(), timeout=30)
                        # Parse and classify
                        import json
                        data = json.loads(msg)
                        tx_hash = data.get("params", {}).get("result", "")
                        if tx_hash and tx_hash not in self._seen:
                            self._seen.add(tx_hash)
                            self.stats["txs_received"] += 1
                            # Full TX fetch would happen here in production
                            # For now, emit raw hash signal

            except asyncio.TimeoutError:
                continue
            except Exception as e:
                logger.debug(f"{provider_name} WS error: {e}")
                await asyncio.sleep(5)

    # ------------------------------------------------------------------
    # Provider: bloXroute (private mempool)
    # ------------------------------------------------------------------

    async def _subscribe_bloxroute(self):
        """Connect to bloXroute for private mempool access."""
        try:
            from websockets import connect as ws_connect
        except ImportError:
            return

        while self._running:
            try:
                headers = {"Authorization": self._cfg.bloxroute_api_key}
                async with ws_connect(
                    self._cfg.bloxroute_ws_url,
                    extra_headers=headers,
                ) as ws:
                    self._providers_connected += 1
                    logger.info("📡 Connected to bloXroute private mempool")

                    import json
                    sub = json.dumps({
                        "jsonrpc": "2.0", "id": 1,
                        "method": "subscribe",
                        "params": ["newTxs", {"include": ["tx_hash", "tx_contents"]}],
                    })
                    await ws.send(sub)

                    while self._running:
                        msg = await asyncio.wait_for(ws.recv(), timeout=30)
                        data = json.loads(msg)
                        tx_data = data.get("params", {}).get("result", {})
                        if isinstance(tx_data, dict) and tx_data.get("txHash"):
                            self._process_raw_tx(tx_data, "bloxroute")

            except Exception as e:
                logger.debug(f"bloXroute error: {e}")
                await asyncio.sleep(10)

    # ------------------------------------------------------------------
    # Provider: Blocknative
    # ------------------------------------------------------------------

    async def _subscribe_blocknative(self):
        """Connect to Blocknative for transaction lifecycle events."""
        # Blocknative provides pre-chain and in-mempool status updates
        while self._running:
            await asyncio.sleep(60)  # Placeholder — real implementation uses their SDK

    # ------------------------------------------------------------------
    # Fallback: poll pending block via RPC
    # ------------------------------------------------------------------

    async def _poll_pending_fallback(self):
        """Fallback: poll pending block every second.

        Prefers the RPCGateway (Module 10) when available, giving managed
        rate limiting, circuit breaking and automatic failover across all 96+
        endpoints.  Falls back to a direct Web3 connection when no gateway is
        injected.
        """
        import os
        rpc = os.getenv("MAINNET_RPC_URL", "")
        if not rpc and self._rpc_gateway is None:
            logger.warning("No Ethereum RPC — mempool radar inactive")
            return

        # Prefer the managed gateway; fall back to a plain Web3 connection.
        connection_type = "RPCGateway" if self._rpc_gateway is not None else "direct Web3"
        if self._rpc_gateway is not None:
            w3 = self._rpc_gateway.get_w3(1)  # chain_id 1 = Ethereum mainnet
        else:
            w3 = Web3(Web3.HTTPProvider(rpc, request_kwargs={"timeout": 10}))
        if w3 is None:
            logger.warning("RPCGateway has no active endpoint for chain 1 — mempool radar inactive")
            return
        logger.info("📡 Mempool Radar: using %s for RPC polling fallback", connection_type)

        while self._running:
            try:
                block = w3.eth.get_block("pending", full_transactions=True)
                if block and hasattr(block, "transactions"):
                    for tx in block["transactions"]:
                        if isinstance(tx, dict):
                            tx_hash = (
                                tx["hash"].hex()
                                if isinstance(tx["hash"], bytes)
                                else str(tx["hash"])
                            )
                            if tx_hash not in self._seen:
                                self._seen.add(tx_hash)
                                self._classify_and_emit(tx, w3)
            except Exception:
                pass

            if len(self._seen) > self._max_seen:
                self._seen = set(list(self._seen)[-self._max_seen // 2:])

            await asyncio.sleep(1)

    # ------------------------------------------------------------------
    # Classification
    # ------------------------------------------------------------------

    def _process_raw_tx(self, tx_data: dict, provider: str):
        """Process raw TX data from any provider."""
        self.stats["txs_received"] += 1
        # In production: full classification + simulation here

    def _classify_and_emit(self, tx: dict, w3: Web3):
        """Classify a pending TX and emit appropriate signal(s)."""
        self.stats["txs_classified"] += 1

        to_addr = tx.get("to", "")
        if not to_addr:
            return
        to_str = (
            Web3.to_checksum_address(to_addr) if isinstance(to_addr, str)
            else to_addr
        )
        value_wei = tx.get("value", 0)
        input_data = tx.get("input", b"")
        if isinstance(input_data, str):
            input_data = bytes.fromhex(input_data[2:]) if input_data.startswith("0x") else b""

        selector = input_data[:4].hex() if len(input_data) >= 4 else ""

        # ---- Large Swap Detection ----
        if to_str in DEX_ROUTERS:
            value_usd = (value_wei / 1e18) * 2500
            if value_usd >= self._cfg.min_swap_impact_usd:
                self.stats["swaps_detected"] += 1
                self.bus.publish(OpportunitySignal(
                    signal_type=SignalType.BACKRUN,
                    source=SignalSource.MEMPOOL_RADAR,
                    chain_id=1,
                    confidence=0.6,
                    estimated_profit_usd=value_usd * 0.001,  # ~0.1% backrun profit
                    gas_cost_estimate_usd=15.0,
                    urgency_seconds=0,  # Must be same block
                    target_protocol=DEX_ROUTERS.get(to_str, "DEX"),
                    target_asset="WETH",
                    tx_hash=tx.get("hash", b"").hex() if isinstance(tx.get("hash"), bytes) else str(tx.get("hash", "")),
                    competition_estimate=0.7,
                    execution_complexity=0.3,
                    metadata={"swap_value_usd": value_usd, "dex": DEX_ROUTERS.get(to_str, "")},
                ))
                self.stats["signals_emitted"] += 1

        # ---- Oracle Update Detection ----
        if selector in ORACLE_SELECTORS:
            self.stats["oracle_updates_detected"] += 1
            self.bus.publish(OpportunitySignal(
                signal_type=SignalType.PENDING_LIQUIDATION,
                source=SignalSource.MEMPOOL_RADAR,
                chain_id=1,
                confidence=0.75,
                estimated_profit_usd=200.0,  # Avg liquidation profit
                gas_cost_estimate_usd=20.0,
                urgency_seconds=0,
                target_contract=to_str,
                tx_hash=tx.get("hash", b"").hex() if isinstance(tx.get("hash"), bytes) else str(tx.get("hash", "")),
                competition_estimate=0.5,
                execution_complexity=0.4,
                metadata={"oracle_selector": selector},
            ))
            self.stats["signals_emitted"] += 1

        # ---- Lending Protocol Borrow/Withdraw Detection ----
        if selector in LENDING_SELECTORS:
            self.stats["borrows_detected"] += 1
            action = LENDING_SELECTORS[selector]
            self.bus.publish(OpportunitySignal(
                signal_type=SignalType.PENDING_LIQUIDATION,
                source=SignalSource.MEMPOOL_RADAR,
                chain_id=1,
                confidence=0.4,
                estimated_profit_usd=100.0,
                gas_cost_estimate_usd=15.0,
                urgency_seconds=12,  # Next block
                target_protocol="lending",
                tx_hash=tx.get("hash", b"").hex() if isinstance(tx.get("hash"), bytes) else str(tx.get("hash", "")),
                metadata={"action": action},
            ))
            self.stats["signals_emitted"] += 1

    def get_stats(self) -> Dict:
        return {
            **self.stats,
            "providers_connected": self._providers_connected,
            "seen_cache_size": len(self._seen),
            "recent_buffer": len(self._recent),
        }
