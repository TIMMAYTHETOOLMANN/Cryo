#!/usr/bin/env python3
"""
enhanced_modules.module_1_opportunity_detector.realtime_stream
==============================================================
Replaces periodic polling with **real-time WebSocket event streaming**
for sub-200ms latency detection of liquidation opportunities.

Subscribes to:
  - Protocol liquidation events (LiquidationCall, LiquidateBorrow, Bite)
  - Chainlink oracle updates (AnswerUpdated)
  - DEX large swap events (Swap with value > threshold)
  - Mempool pending transactions (via bloXroute / Blocknative)

Implements a **priority queue** where positions are ranked by:
  1. Liquidation probability (from ML scorer)
  2. Estimated profit (highest first)
  3. Health factor (lowest first — most urgent)
"""
from __future__ import annotations

import asyncio
import heapq
import json
import logging
import time
from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Set

from enhanced_modules.common.base import EnhancedModule
from enhanced_modules.common.models import EnrichedPosition

logger = logging.getLogger(__name__)


# ── Event Types ────────────────────────────────────────────────────

class StreamEventType(Enum):
    LIQUIDATION_CALL = "LiquidationCall"
    LIQUIDATE_BORROW = "LiquidateBorrow"
    MAKER_BITE = "Bite"
    ORACLE_UPDATE = "AnswerUpdated"
    LARGE_SWAP = "Swap"
    HEALTH_FACTOR_CHANGE = "HealthFactorChange"
    NEW_BORROW = "Borrow"
    MEMPOOL_TX = "MempoolTx"


@dataclass(order=True)
class PrioritizedPosition:
    """
    Wrapper for heapq-based priority queue.
    Lower priority number = processed first.
    We negate profit so highest profit comes first in a min-heap.
    """
    priority: float
    position: EnrichedPosition = field(compare=False)
    event_type: str = field(compare=False, default="unknown")
    ingested_at: float = field(compare=False, default_factory=time.time)

    @classmethod
    def from_position(cls, pos: EnrichedPosition, event_type: str = "scan") -> "PrioritizedPosition":
        """Create a prioritized position from an enriched position."""
        # Priority: lower is better
        # - Probability inverted (high prob → low priority number)
        # - Health factor (lower → more urgent)
        # - Profit inverted
        prob_score = 1.0 - pos.liquidation_probability
        hf_score = max(0, pos.health_factor - 0.9)  # 0 for HF=0.9, 0.2 for HF=1.1
        profit_score = 1.0 / (float(pos.estimated_bonus_usd) + 1.0)

        priority = prob_score * 0.5 + hf_score * 0.3 + profit_score * 0.2
        return cls(priority=priority, position=pos, event_type=event_type)


@dataclass
class StreamConfig:
    """Configuration for a WebSocket stream."""
    name: str
    url: str
    chain_id: int
    event_types: List[StreamEventType]
    reconnect_delay: float = 5.0
    max_reconnects: int = 50
    heartbeat_interval: float = 30.0


# ── Event Parsers ──────────────────────────────────────────────────

class EventParser:
    """Parses raw WebSocket messages into structured events."""

    # Mapping of event topic hashes → event types
    EVENT_TOPICS = {
        "0xe413a321e8681d831f4dbccbca790d2952b56f977908e45be37335533e005286": StreamEventType.LIQUIDATION_CALL,
        "0x298637f684da70674f26509b10f07ec2fbc77a335ab1e7a6fff849315ee028": StreamEventType.LIQUIDATE_BORROW,
        "0x0f3e2e56e1b5de77dfade782e5dbc0bb0875e19e32ff1e9f245e0e845883ac9e": StreamEventType.ORACLE_UPDATE,
    }

    @staticmethod
    def parse_log_event(log: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Parse an Ethereum log event into a structured dict."""
        topics = log.get("topics", [])
        if not topics:
            return None

        topic0 = topics[0] if isinstance(topics[0], str) else topics[0].hex()

        event_type = EventParser.EVENT_TOPICS.get(topic0)
        if not event_type:
            return None

        return {
            "event_type": event_type,
            "address": log.get("address", ""),
            "block_number": int(log.get("blockNumber", "0x0"), 16) if isinstance(log.get("blockNumber"), str) else log.get("blockNumber", 0),
            "tx_hash": log.get("transactionHash", ""),
            "data": log.get("data", ""),
            "topics": topics,
            "timestamp": time.time(),
        }

    @staticmethod
    def parse_mempool_tx(tx: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Parse a mempool pending transaction."""
        value = int(tx.get("value", "0x0"), 16) if isinstance(tx.get("value"), str) else tx.get("value", 0)
        gas_price = int(tx.get("gasPrice", "0x0"), 16) if isinstance(tx.get("gasPrice"), str) else tx.get("gasPrice", 0)

        return {
            "event_type": StreamEventType.MEMPOOL_TX,
            "tx_hash": tx.get("hash", ""),
            "from": tx.get("from", ""),
            "to": tx.get("to", ""),
            "value_wei": value,
            "gas_price_wei": gas_price,
            "input": tx.get("input", "0x"),
            "timestamp": time.time(),
        }


# ── Main Streaming Engine ─────────────────────────────────────────

class RealtimeStreamIngester(EnhancedModule):
    """
    Real-time WebSocket streaming engine with priority queue processing.

    Replaces periodic polling with event-driven detection for sub-200ms
    latency on liquidation opportunities.
    """

    MAX_QUEUE_SIZE = 10000
    PROCESS_BATCH_SIZE = 50

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        super().__init__("realtime_stream_ingester", config)
        self._priority_queue: List[PrioritizedPosition] = []
        self._streams: List[StreamConfig] = []
        self._stream_tasks: List[asyncio.Task] = []
        self._callbacks: List[Callable] = []
        self._event_count = 0
        self._connected_streams: Set[str] = set()
        self._parser = EventParser()

    async def _on_start(self) -> None:
        self._streams = self._build_stream_configs()
        logger.info(
            "[RealtimeStream] Configured %d WebSocket streams", len(self._streams)
        )

    async def _on_stop(self) -> None:
        for task in self._stream_tasks:
            task.cancel()
        if self._stream_tasks:
            await asyncio.gather(*self._stream_tasks, return_exceptions=True)
        self._stream_tasks.clear()
        self._priority_queue.clear()

    # ── Stream Management ──────────────────────────────────────

    def add_stream(self, stream_config: StreamConfig) -> None:
        """Add a stream configuration (before or after start)."""
        self._streams.append(stream_config)

    def register_callback(self, callback: Callable) -> None:
        """Register a callback for processed positions."""
        self._callbacks.append(callback)

    async def connect_all(self) -> int:
        """Connect to all configured streams. Returns count connected."""
        connected = 0
        for stream in self._streams:
            task = asyncio.create_task(
                self._stream_loop(stream), name=f"stream_{stream.name}"
            )
            self._stream_tasks.append(task)
            connected += 1
        return connected

    async def _stream_loop(self, config: StreamConfig) -> None:
        """
        Persistent WebSocket connection loop with automatic reconnection.
        """
        reconnect_count = 0

        while reconnect_count < config.max_reconnects:
            try:
                import websockets
                async with websockets.connect(
                    config.url,
                    ping_interval=config.heartbeat_interval,
                    ping_timeout=10,
                    close_timeout=5,
                ) as ws:
                    self._connected_streams.add(config.name)
                    reconnect_count = 0
                    logger.info("[RealtimeStream] Connected: %s", config.name)

                    # Subscribe to events
                    await self._subscribe(ws, config)

                    # Process messages
                    async for message in ws:
                        await self._handle_message(message, config)

            except asyncio.CancelledError:
                break
            except ImportError:
                logger.warning("[RealtimeStream] websockets not installed — using mock mode")
                break
            except Exception as exc:
                self._connected_streams.discard(config.name)
                reconnect_count += 1
                logger.warning(
                    "[RealtimeStream] %s disconnected (%d/%d): %s",
                    config.name, reconnect_count, config.max_reconnects, exc,
                )
                await asyncio.sleep(config.reconnect_delay * min(reconnect_count, 10))

    async def _subscribe(self, ws: Any, config: StreamConfig) -> None:
        """Send subscription messages for the configured event types."""
        # Standard eth_subscribe for logs
        for event_type in config.event_types:
            if event_type == StreamEventType.MEMPOOL_TX:
                sub = {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "eth_subscribe",
                    "params": ["newPendingTransactions"],
                }
            else:
                sub = {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "eth_subscribe",
                    "params": [
                        "logs",
                        {"topics": [self._event_type_to_topic(event_type)]},
                    ],
                }
            await ws.send(json.dumps(sub))

    async def _handle_message(self, message: str, config: StreamConfig) -> None:
        """Parse and enqueue a WebSocket message."""
        try:
            data = json.loads(message)
            params = data.get("params", {})
            result = params.get("result", {})

            if isinstance(result, dict) and "topics" in result:
                event = self._parser.parse_log_event(result)
            elif isinstance(result, str):
                # Pending tx hash — need to fetch details
                event = {"event_type": StreamEventType.MEMPOOL_TX, "tx_hash": result}
            else:
                return

            if event:
                self._event_count += 1
                await self._process_event(event, config.chain_id)

        except (json.JSONDecodeError, KeyError, ValueError) as exc:
            logger.debug("[RealtimeStream] Parse error: %s", exc)

    async def _process_event(self, event: Dict[str, Any], chain_id: int) -> None:
        """Convert event to enriched position and enqueue."""
        event_type = event.get("event_type")

        if event_type in (
            StreamEventType.LIQUIDATION_CALL,
            StreamEventType.LIQUIDATE_BORROW,
        ):
            # Direct liquidation event — highest priority
            position = EnrichedPosition(
                borrower=event.get("address", "unknown"),
                protocol=self._infer_protocol(event.get("address", "")),
                chain_id=chain_id,
                health_factor=0.99,  # Already liquidatable
                debt_usd=Decimal("0"),
                collateral_usd=Decimal("0"),
                debt_asset="unknown",
                collateral_asset="unknown",
                liquidation_probability=0.95,
                metadata={"raw_event": event, "source": "stream"},
            )
            pp = PrioritizedPosition.from_position(position, event_type.value if hasattr(event_type, 'value') else str(event_type))
            self._enqueue(pp)

        elif event_type == StreamEventType.ORACLE_UPDATE:
            # Oracle update — flag all positions with this collateral
            position = EnrichedPosition(
                borrower="oracle_trigger",
                protocol="multi",
                chain_id=chain_id,
                health_factor=1.05,
                debt_usd=Decimal("0"),
                collateral_usd=Decimal("0"),
                debt_asset="unknown",
                collateral_asset="unknown",
                liquidation_probability=0.70,
                metadata={"raw_event": event, "source": "oracle_stream"},
            )
            pp = PrioritizedPosition.from_position(position, "oracle_update")
            self._enqueue(pp)

        # Invoke callbacks
        for cb in self._callbacks:
            try:
                if asyncio.iscoroutinefunction(cb):
                    await cb(event, chain_id)
                else:
                    cb(event, chain_id)
            except Exception as exc:
                logger.debug("[RealtimeStream] Callback error: %s", exc)

    # ── Priority Queue Operations ──────────────────────────────

    def _enqueue(self, item: PrioritizedPosition) -> None:
        """Add to priority queue, evicting if full."""
        if len(self._priority_queue) >= self.MAX_QUEUE_SIZE:
            heapq.heapreplace(self._priority_queue, item)
        else:
            heapq.heappush(self._priority_queue, item)

    def dequeue(self) -> Optional[PrioritizedPosition]:
        """Pop highest-priority position."""
        if self._priority_queue:
            return heapq.heappop(self._priority_queue)
        return None

    def dequeue_batch(self, count: int = 50) -> List[PrioritizedPosition]:
        """Pop up to `count` highest-priority positions."""
        batch = []
        for _ in range(min(count, len(self._priority_queue))):
            batch.append(heapq.heappop(self._priority_queue))
        return batch

    @property
    def queue_depth(self) -> int:
        return len(self._priority_queue)

    # ── Helpers ────────────────────────────────────────────────

    def _build_stream_configs(self) -> List[StreamConfig]:
        """Build stream configs from module config."""
        configs = []
        ws_endpoints = self.config.get("ws_endpoints", {})

        for name, url in ws_endpoints.items():
            chain_id = self.config.get("chain_ids", {}).get(name, 1)
            configs.append(
                StreamConfig(
                    name=name,
                    url=url,
                    chain_id=chain_id,
                    event_types=[
                        StreamEventType.LIQUIDATION_CALL,
                        StreamEventType.ORACLE_UPDATE,
                        StreamEventType.LARGE_SWAP,
                    ],
                )
            )

        # Default: at least mainnet if no config
        if not configs:
            default_ws = self.config.get(
                "default_ws_url", "wss://eth-mainnet.g.alchemy.com/v2/demo"
            )
            configs.append(
                StreamConfig(
                    name="eth_mainnet",
                    url=default_ws,
                    chain_id=1,
                    event_types=[
                        StreamEventType.LIQUIDATION_CALL,
                        StreamEventType.ORACLE_UPDATE,
                        StreamEventType.MEMPOOL_TX,
                    ],
                )
            )
        return configs

    @staticmethod
    def _event_type_to_topic(event_type: StreamEventType) -> Optional[str]:
        """Map event type to EVM topic0 hash."""
        topics = {
            StreamEventType.LIQUIDATION_CALL: "0xe413a321e8681d831f4dbccbca790d2952b56f977908e45be37335533e005286",
            StreamEventType.LIQUIDATE_BORROW: "0x298637f684da70674f26509b10f07ec2fbc77a335ab1e7a6fff849315ee028",
            StreamEventType.ORACLE_UPDATE: "0x0f3e2e56e1b5de77dfade782e5dbc0bb0875e19e32ff1e9f245e0e845883ac9e",
            StreamEventType.MAKER_BITE: "0xa716da86bc1fb6d43d1f7bccaaee5734757e816d3b7911cec2e4a2aa6e4a0e95",
        }
        return topics.get(event_type)

    @staticmethod
    def _infer_protocol(address: str) -> str:
        """Infer protocol name from contract address (simplified)."""
        # Known Aave V3 pool addresses (mainnet)
        aave_pools = {
            "0x87870bca3f3fd6335c3f4ce8392d69350b4fa4e2",
        }
        compound_pools = {
            "0xc3d688b66703497daa19211eedff47f25384cdc3",
        }

        addr_lower = address.lower()
        if addr_lower in aave_pools:
            return "aave_v3"
        elif addr_lower in compound_pools:
            return "compound_v3"
        return "unknown"

    def get_stream_status(self) -> Dict[str, Any]:
        """Return current stream status for monitoring."""
        return {
            "configured_streams": len(self._streams),
            "connected_streams": len(self._connected_streams),
            "connected_names": list(self._connected_streams),
            "total_events_processed": self._event_count,
            "queue_depth": self.queue_depth,
            "callbacks_registered": len(self._callbacks),
        }
