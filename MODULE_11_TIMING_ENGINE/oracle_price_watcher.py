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

# ── Multi-chain Chainlink feeds for lending protocol collateral ──
#
# Maps (chain_id, asset) → Chainlink price feed address.
# Covers every collateral asset on Aave V3, Compound V3, and
# Spark across Ethereum, Arbitrum, Optimism, Polygon, and Base.

LENDING_PROTOCOL_FEEDS: Dict[int, Dict[str, str]] = {
    1: {  # Ethereum mainnet — Aave V3, Compound V3, Spark
        "ETH/USD":    "0x5f4eC3Df9cbd43714FE2740f5E3616155c5b8419",
        "BTC/USD":    "0xF4030086522a5bEEa4988F8cA5B36dbC97BeE88c",
        "USDC/USD":   "0x8fFfFfd4AfB6115b954Bd326cbe7B4BA576818f6",
        "USDT/USD":   "0x3E7d1eAB13ad0104d2750B8863b489D65364e32D",
        "DAI/USD":    "0xAed0c38402a5d19df6E4c03F4E2DceD6e29c1ee9",
        "LINK/USD":   "0x2c1d072e956AFFC0D435Cb7AC38EF18d24d9127c",
        "AAVE/USD":   "0x547a514d5e3769680Ce22B2361c10Ea13619e8a9",
        "WSTETH/ETH": "0x86392dC19c0b719886221c78AB11eb8Cf5c52812",
        "WSTETH/USD": "0xCfE54B5cD566aB89272946F602D76Ea879CAb4a8",
        "RETH/ETH":   "0x536218f9E9Eb48863970252233c8F271f554C2d0",
        "CBETH/ETH":  "0xF017fcB346A1885194689bA23Eff2fE6fA5C483b",
        "CRV/USD":    "0xCd627aA160A6fA45Eb793D19Ef54f5062F20f33f",
        "MKR/USD":    "0xec1D1B3b0443256cc3860e24a46F108e699484Aa",
        "SNX/USD":    "0xDC3EA94CD0AC27d9A86C180091e7f78C683d3699",
        "LDO/USD":    "0x4e844125952D32AcdF339BE976c98E22F6F318dB",
        "ENS/USD":    "0x5C00128d4d1c2F4f652C267d7bcdD7aC99C16E16",
        "UNI/USD":    "0x553303d460EE0afB37EdFf9bE42922D8FF63220e",
        "COMP/USD":   "0xdbd020CAeF83eFd542f4De03e3cF0C28A4428bd5",
        "BAL/USD":    "0xdF2917806E30300537aEB49A7663062F4d1F2b5F",
        "FRAX/USD":   "0xB9E1E3A9feFf48998E45Fa90847ed4D467E8BcfD",
        "LUSD/USD":   "0x3D7aE7E594f2f2091Ad8798313450130d0Aba3a0",
        "GHO/USD":    "0x3f12643D3f6f874d39C2a4c9f2Cd6f2DbAC877FC",
    },
    42161: {  # Arbitrum — Aave V3
        "ETH/USD":    "0x639Fe6ab55C921f74e7fac1ee960C0B6293ba612",
        "BTC/USD":    "0x6ce185860a4963106506C203335A2910413708e9",
        "USDC/USD":   "0x50834F3163758fcC1Df9973b6e91f0F0F0434aD3",
        "USDT/USD":   "0x3f3f5dF88dC9F13eac63DF89EC16ef6e7E25DdE7",
        "DAI/USD":    "0xc5C8E77B397E531B8EC06BFb0048328B30E9eCfB",
        "LINK/USD":   "0x86E53CF1B870786351Da77A57575e79CB55812CB",
        "ARB/USD":    "0xb2A824043730FE05F3DA2efaFa1CBbe83fa548D6",
        "WSTETH/ETH": "0xB1552C5e96B312d0Bf8b554186F846C40614a540",
        "RETH/ETH":   "0xD6aB2298946840262FcC278fF31516D39fF611eF",
        "GMX/USD":    "0xDB98056FecFff59D032aB628337A4887110df3dB",
        "FRAX/USD":   "0x0809E3d38d1B4214958faf06D8b1B1a2b73f2ab8",
    },
    10: {  # Optimism — Aave V3
        "ETH/USD":    "0x13e3Ee699D1909E989722E753853AE30b17e08c5",
        "BTC/USD":    "0xD702DD976Fb76Fffc2D3963D037dfDae5b04E593",
        "USDC/USD":   "0x16a9FA2FDa030272Ce99B29CF780dFA30361E0f3",
        "USDT/USD":   "0xECef79E109e997bCA29c1c0897ec9d7b03647F5E",
        "DAI/USD":    "0x8dBa75e83DA73cc766A7e5a0ee71F656BAb470d6",
        "LINK/USD":   "0xCc232dcFAAE6354cE191Bd574108c1aD03f86229",
        "OP/USD":     "0x0D276FC14719f9292D5FAe2d49a25bD0b7dB5144",
        "WSTETH/ETH": "0x698B585CbC4407e2D54aa898B2600B53C68958f7",
    },
    137: {  # Polygon — Aave V3
        "ETH/USD":    "0xF9680D99D6C9589e2a93a78A04A279e509205945",
        "BTC/USD":    "0xc907E116054Ad103354f2D350FD2514433D57F6f",
        "MATIC/USD":  "0xAB594600376Ec9fD91F8e8dC29eA7868d8B16ab7",
        "USDC/USD":   "0xfE4A8cc5b5B2366C1B58Bea3858e81843583ee2e",
        "USDT/USD":   "0x0A6513e40db6EB1b165753AD52E80663aeA50545",
        "DAI/USD":    "0x4746DeC9e833A82EC7C2C1245845D6FF868Bc170",
        "LINK/USD":   "0xd9FFdb71EbE7496cC440152d43986Aae0AB76665",
        "AAVE/USD":   "0x72484B12719E23115761D5DA1646945632979bB6",
        "WSTETH/ETH": "0x10f964234cae09cB6a9854B56FF7D4F38f2120BD",
    },
    8453: {  # Base — Aave V3
        "ETH/USD":    "0x71041dddad3595F9CEd3DcCFBe3D1F4b0a16Bb70",
        "USDC/USD":   "0x7e860098F58bBFC8648a4311b374B1D669a2bc6B",
        "CBETH/ETH":  "0x868a501e68F3D1E89CfC0D22F6b22E8dabce5F04",
    },
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

    # ── Multi-chain lending-protocol feed management ─────────────

    def get_lending_feeds(self, chain_id: int) -> Dict[str, str]:
        """
        Return the Chainlink feed mapping for a specific chain,
        covering all collateral assets on Aave/Compound/Spark.

        Returns
        -------
        dict[str, str]
            ``{asset_pair: feed_address, …}``
        """
        return dict(LENDING_PROTOCOL_FEEDS.get(chain_id, {}))

    def get_all_lending_feeds(self) -> Dict[int, Dict[str, str]]:
        """Return feed maps for every supported chain."""
        return {cid: dict(feeds)
                for cid, feeds in LENDING_PROTOCOL_FEEDS.items()}

    def load_lending_feeds(self, chain_id: Optional[int] = None) -> int:
        """
        Merge the lending-protocol feed addresses into the active
        feed set so they are polled automatically.

        Parameters
        ----------
        chain_id : int | None
            Load feeds for a specific chain.  ``None`` loads all.

        Returns
        -------
        int
            Number of new feeds added.
        """
        added = 0
        targets = (
            {chain_id: LENDING_PROTOCOL_FEEDS.get(chain_id, {})}
            if chain_id is not None
            else LENDING_PROTOCOL_FEEDS
        )
        for cid, feed_map in targets.items():
            for asset, addr in feed_map.items():
                key = f"{cid}:{asset}"
                if key not in self._feeds:
                    self._feeds[key] = addr
                    added += 1
        if added:
            logger.info(
                "[OraclePriceWatcher] Loaded %d lending-protocol feeds", added,
            )
        return added

    def simulate_health_factors(
        self,
        positions: List[Dict],
        new_prices: Dict[str, float],
    ) -> List[Dict]:
        """
        Given a pending oracle update, simulate new health factors
        for every position that uses the affected asset.

        Parameters
        ----------
        positions : list[dict]
            Each entry requires::

                {
                    "user": str,
                    "chain_id": int,
                    "collateral_asset": str,   # e.g. "ETH/USD"
                    "collateral_usd": float,
                    "debt_usd": float,
                    "current_health_factor": float,
                    "liquidation_threshold": float,  # e.g. 0.825
                }

        new_prices : dict[str, float]
            Simulated oracle prices: ``{"ETH/USD": 1850.0, …}``

        Returns
        -------
        list[dict]
            Positions whose simulated HF drops below 1.0, with
            ``simulated_health_factor`` and ``action`` fields added.
        """
        at_risk: List[Dict] = []

        for pos in positions:
            asset = pos.get("collateral_asset", "")
            new_price = new_prices.get(asset)
            if new_price is None:
                continue

            old_price = self._last_prices.get(asset)
            if not old_price or old_price <= 0:
                continue

            # Scale collateral value by price change
            price_ratio = new_price / old_price
            new_collateral_usd = pos["collateral_usd"] * price_ratio
            debt_usd = pos["debt_usd"]
            liq_threshold = pos.get("liquidation_threshold", 0.825)

            if debt_usd <= 0:
                continue

            new_hf = (new_collateral_usd * liq_threshold) / debt_usd

            if new_hf < 1.0:
                at_risk.append({
                    **pos,
                    "simulated_health_factor": round(new_hf, 6),
                    "previous_health_factor": pos["current_health_factor"],
                    "price_change_pct": round(
                        (price_ratio - 1.0) * 100, 4
                    ),
                    "action": "prepare_liquidation",
                })

        # Most at-risk first
        at_risk.sort(key=lambda p: p["simulated_health_factor"])
        return at_risk
