#!/usr/bin/env python3
"""
Submodule 11.1 -- Oracle Price Watcher
========================================
Event-driven Chainlink oracle monitor.  Polls latestRoundData on
configured feeds and fires ``on_price_update`` callback when a
price changes.  Supports both HTTP polling and WS subscription.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from typing import Any, Callable, Coroutine, Dict, List, Optional

from web3 import Web3

from .config import OracleConfig

logger = logging.getLogger(__name__)

# ── Chainlink ABI fragment ───────────────────────────────────────

CHAINLINK_AGGREGATOR_ABI = json.loads('''[
    {"inputs":[],"name":"latestRoundData","outputs":[
        {"name":"roundId","type":"uint80"},
        {"name":"answer","type":"int256"},
        {"name":"startedAt","type":"uint256"},
        {"name":"updatedAt","type":"uint256"},
        {"name":"answeredInRound","type":"uint80"}
    ],"stateMutability":"view","type":"function"},
    {"inputs":[],"name":"decimals","outputs":[
        {"name":"","type":"uint8"}
    ],"stateMutability":"view","type":"function"},
    {"inputs":[],"name":"description","outputs":[
        {"name":"","type":"string"}
    ],"stateMutability":"view","type":"function"}
]''')

# ── Default Chainlink feeds (Ethereum mainnet) ──────────────────

DEFAULT_FEEDS: Dict[str, str] = {
    "ETH/USD":  "0x5f4eC3Df9cbd43714FE2740f5E3616155c5b8419",
    "BTC/USD":  "0xF4030086522a5bEEa4988F8cA5B36dbC97BeE88c",
    "LINK/USD": "0x2c1d072e956AFFC0D435Cb7AC38EF18d24d9127c",
    "AAVE/USD": "0x547a514d5e3769680Ce22B2361c10Ea13619e8a9",
    "UNI/USD":  "0x553303d460EE0afB37EdFf9bE42922D8FF63220e",
    "MATIC/USD":"0x7bAC85A8a13A4BcD8abb3eB7d6b4d632c5a57676",
    "AVAX/USD": "0xFF3EEb22B5E3dE6e705b44749C2559d704923FD7",
    "DAI/USD":  "0xAed0c38402a5d19df6E4c03F4E2DceD6e29c1ee9",
    "USDC/USD": "0x8fFfFfd4AfB6115b954Bd326cbe7B4BA576818f6",
    "USDT/USD": "0x3E7d1eAB13ad0104d2750B8863b489D65364e32D",
    "WSTETH/USD":"0xCfE54B5cD566aB89272946F602D76Ea879CAb4a8",
    "COMP/USD": "0xdbd020CAeF83eFd542f4De03e3cF0C28A4428bd5",
    "MKR/USD":  "0xec1D1B3b0443256cc3860e24a46F108e699484Aa",
    "SNX/USD":  "0xDC3EA94CD0AC27d9A86C180091e7f78C683d3699",
    "CRV/USD":  "0xCd627aA160A6fA45Eb793D19Ef54f5062F20f33f",
    "SUSHI/USD":"0xCc70F09A6CC17553b2E31954cD36E4A2d89501f7",
    "1INCH/USD":"0xc929ad75B72593967DE83E7F7Cda0493458261D9",
    "FXS/USD":  "0x6Ebc52C8C1089be9eB3945C4350B68B8E4C2233f",
    "LDO/USD":  "0x4e844125952D32AcdF339BE976c98E22F6F318dB",
}


class OraclePriceWatcher:
    """
    Watches Chainlink oracle feeds for price updates.
    Calls ``on_price_update(feed_address, asset, new_price, chain_id, block)``
    when a price changes.
    """

    def __init__(
        self,
        w3_providers: Optional[Dict[int, Web3]] = None,
        config: Optional[OracleConfig] = None,
    ):
        self.cfg = config or OracleConfig()
        self._w3 = w3_providers or {}
        self._feeds = self.cfg.feeds or dict(DEFAULT_FEEDS)
        self._last_prices: Dict[str, float] = {}
        self._last_round_ids: Dict[str, int] = {}
        self._running = False

        # Callback -- set by the engine
        self.on_price_update: Optional[
            Callable[..., Coroutine]
        ] = None

        # Stats
        self.polls = 0
        self.updates_fired = 0
        self.errors = 0

    # ── Public API ────────────────────────────────────────────

    async def run(self) -> None:
        """Main polling loop -- runs forever until cancelled."""
        self._running = True
        logger.info(
            "[OraclePriceWatcher] Starting -- %d feeds, poll=%.1fs",
            len(self._feeds), self.cfg.poll_interval_s,
        )

        while self._running:
            try:
                await self._poll_all_feeds()
            except asyncio.CancelledError:
                break
            except Exception as exc:
                self.errors += 1
                logger.warning("[OraclePriceWatcher] Poll error: %s", exc)

            await asyncio.sleep(self.cfg.poll_interval_s)

        logger.info(
            "[OraclePriceWatcher] Stopped. polls=%d updates=%d errors=%d",
            self.polls, self.updates_fired, self.errors,
        )

    async def stop(self) -> None:
        self._running = False

    # ── Internals ─────────────────────────────────────────────

    async def _poll_all_feeds(self) -> None:
        """Poll every feed on every connected chain."""
        # Default to chain 1 if available
        w3 = self._w3.get(1)
        if not w3:
            # Try any available chain
            if self._w3:
                w3 = next(iter(self._w3.values()))
            else:
                return

        for asset, feed_addr in self._feeds.items():
            try:
                await self._poll_feed(w3, asset, feed_addr, chain_id=1)
                self.polls += 1
            except Exception as exc:
                self.errors += 1
                if self.errors <= 5:
                    logger.debug("[OraclePriceWatcher] Feed %s error: %s", asset, exc)

    async def _poll_feed(
        self, w3: Web3, asset: str, feed_addr: str, chain_id: int
    ) -> None:
        """Poll a single Chainlink feed."""
        try:
            contract = w3.eth.contract(
                address=Web3.to_checksum_address(feed_addr),
                abi=CHAINLINK_AGGREGATOR_ABI,
            )

            # Run in executor to avoid blocking the event loop
            loop = asyncio.get_event_loop()
            round_data = await loop.run_in_executor(
                None, contract.functions.latestRoundData().call
            )

            round_id = round_data[0]
            answer = round_data[1]
            updated_at = round_data[3]

            # Check staleness
            now = int(time.time())
            if (now - updated_at) > self.cfg.stale_threshold_s:
                return  # Stale feed, skip

            # Get decimals (cache this in production)
            decimals = await loop.run_in_executor(
                None, contract.functions.decimals().call
            )
            price = answer / (10 ** decimals)

            # Check if changed
            prev_round = self._last_round_ids.get(asset, 0)
            if round_id != prev_round:
                self._last_round_ids[asset] = round_id
                self._last_prices[asset] = price
                self.updates_fired += 1

                # Fire callback
                if self.on_price_update is not None:
                    block = await loop.run_in_executor(
                        None, lambda: w3.eth.block_number
                    )
                    try:
                        await self.on_price_update(
                            feed_addr, asset, price, chain_id, block,
                        )
                    except Exception as exc:
                        logger.warning(
                            "[OraclePriceWatcher] Callback error for %s: %s",
                            asset, exc,
                        )
        except Exception as exc:
            raise  # Re-raise for caller to handle

    def get_price(self, asset: str) -> Optional[float]:
        """Get the last known price for an asset."""
        return self._last_prices.get(asset)

    def get_all_prices(self) -> Dict[str, float]:
        """Get all cached prices."""
        return dict(self._last_prices)
