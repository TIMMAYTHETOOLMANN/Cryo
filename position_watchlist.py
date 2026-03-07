#!/usr/bin/env python3
"""
POSITION WATCHLIST — Real-Time Oracle-Driven Execution Trigger
================================================================
Bridges SubgraphIndexer (discovery) with UnifiedPipeline (execution).

The SubgraphIndexer finds positions that are APPROACHING liquidation.
The Watchlist monitors them in real-time and fires the instant the
math confirms HF has crossed below 1.0.

Three Monitoring Layers:

    Layer 1 — Pre-computed Liquidation Prices
        For each watched position, compute the exact collateral price
        at which HF crosses 1.0. Pure math, zero RPC calls:

            liquidation_price = (total_debt / (total_collateral_units * LT))

        Where LT = liquidation threshold from Aave reserve config.
        This gives a "trigger price" for each position.

    Layer 2 — Oracle Event Reactor
        Subscribe to Chainlink AnswerUpdated events via WebSocket.
        On every price update, check if the new price has crossed
        any watched position's liquidation_price. If yes -> fire.

        This gives ~200-500ms advantage over block-based scanners
        because we react to the pending oracle TX, not the confirmed block.

    Layer 3 — Block Verification (Multicall)
        Every new block, batch-verify the top-priority positions via
        multicall to catch anything the oracle reactor missed:
        - Multi-collateral positions where a single oracle isn't sufficient
        - Price movements in less-monitored assets
        - Subgraph index lag

    Layer 2 is the speed advantage. Layer 3 is the safety net.

Data Flow:
    SubgraphIndexer.full_scan()
        -> watchlist.ingest(candidates)
            -> pre_compute_liquidation_prices()
            -> subscribe_oracle_feeds()

    [Chainlink event fires]
        -> oracle_reactor(new_price)
            -> check_triggered_positions()
                -> pipeline.process_single(candidate)

    [New block arrives]
        -> block_verifier()
            -> multicall(top_positions)
                -> pipeline.process_batch(triggered)
"""

from __future__ import annotations

import asyncio
import time
import json
import logging
import os
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any, Callable
from collections import defaultdict, deque
from enum import Enum

from web3 import Web3
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)


# =====================================================================
# DATA MODELS
# =====================================================================

class WatchPriority(Enum):
    """Priority levels for watched positions."""
    CRITICAL = 1    # HF < 1.02 -- check every block
    HIGH = 2        # HF 1.02-1.05 -- check every 3 blocks
    MEDIUM = 3      # HF 1.05-1.10 -- check every 10 blocks
    LOW = 4         # HF > 1.10 -- oracle events only


@dataclass
class WatchedPosition:
    """
    A position being actively monitored for liquidation.
    Enhanced from jit_liquidation_engine.py's WatchedPosition
    with pre-computed liquidation prices.
    """
    # Identity
    user_address: str
    chain_id: int
    protocol: str
    pool_address: str

    # Position data (from SubgraphIndexer)
    total_collateral_usd: float = 0.0
    total_debt_usd: float = 0.0
    health_factor: float = 999.0

    # Best liquidation path
    collateral_asset: str = ""
    collateral_symbol: str = ""
    debt_asset: str = ""
    debt_symbol: str = ""
    max_liquidatable_usd: float = 0.0
    liquidation_bonus_pct: float = 0.0

    # Pre-computed trigger (Layer 1)
    liquidation_threshold: float = 0.0       # From reserve config (e.g. 0.825)
    collateral_units: float = 0.0            # Actual token amount
    collateral_price_usd: float = 0.0        # Current price
    liquidation_price_usd: float = 0.0       # Price at which HF = 1.0
    price_distance_pct: float = 0.0          # % price must drop to trigger

    # Oracle mapping
    oracle_feed_address: str = ""            # Chainlink aggregator for collateral
    oracle_chain_id: int = 0

    # Monitoring state
    priority: WatchPriority = WatchPriority.MEDIUM
    last_checked_block: int = 0
    last_checked_time: float = 0.0
    last_hf_on_chain: float = 0.0
    check_count: int = 0
    times_triggered: int = 0

    # Execution state
    is_triggered: bool = False
    triggered_at: float = 0.0
    execution_sent: bool = False

    @property
    def position_key(self) -> str:
        return f"{self.chain_id}:{self.protocol}:{self.user_address.lower()}"

    @property
    def estimated_gross_profit(self) -> float:
        return self.max_liquidatable_usd * self.liquidation_bonus_pct

    def compute_liquidation_price(self):
        """
        Pre-compute the collateral price at which HF crosses 1.0.

        From Aave's health factor formula:
            HF = (collateral_value * liquidation_threshold) / debt_value

        Setting HF = 1.0 and solving for collateral_price:
            1.0 = (units * price * LT) / debt_usd
            price = debt_usd / (units * LT)

        This is the exact price at which the position becomes liquidatable.
        """
        if self.collateral_units <= 0 or self.liquidation_threshold <= 0:
            self.liquidation_price_usd = 0.0
            self.price_distance_pct = 0.0
            return

        self.liquidation_price_usd = (
            self.total_debt_usd
            / (self.collateral_units * self.liquidation_threshold)
        )

        # How far the current price is from the trigger
        if self.collateral_price_usd > 0:
            self.price_distance_pct = (
                (self.collateral_price_usd - self.liquidation_price_usd)
                / self.collateral_price_usd
                * 100
            )
        else:
            self.price_distance_pct = 0.0

    def update_priority(self):
        """Assign monitoring priority based on health factor proximity."""
        if self.health_factor < 1.02:
            self.priority = WatchPriority.CRITICAL
        elif self.health_factor < 1.05:
            self.priority = WatchPriority.HIGH
        elif self.health_factor < 1.10:
            self.priority = WatchPriority.MEDIUM
        else:
            self.priority = WatchPriority.LOW


@dataclass
class OracleFeed:
    """Tracks a Chainlink price feed and its associated positions."""
    feed_address: str
    chain_id: int
    asset_symbol: str
    current_price: float = 0.0
    last_update_block: int = 0
    last_update_time: float = 0.0
    decimals: int = 8

    # Positions watching this feed (position_key -> WatchedPosition)
    watching_positions: Dict[str, WatchedPosition] = field(default_factory=dict)

    # Price history for trend analysis
    price_history: deque = field(default_factory=lambda: deque(maxlen=200))


@dataclass
class WatchlistStats:
    """Monitoring statistics."""
    total_positions_watched: int = 0
    positions_by_priority: Dict[str, int] = field(default_factory=dict)
    oracle_events_received: int = 0
    positions_triggered: int = 0
    block_checks_performed: int = 0
    multicall_batches: int = 0
    false_triggers: int = 0
    avg_trigger_latency_ms: float = 0.0


# =====================================================================
# CHAINLINK CONFIGURATION
# =====================================================================

CHAINLINK_ABI = json.loads("""[
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
    {"anonymous":false,"inputs":[
        {"indexed":true,"name":"current","type":"int256"},
        {"indexed":true,"name":"roundId","type":"uint256"},
        {"indexed":false,"name":"updatedAt","type":"uint256"}
    ],"name":"AnswerUpdated","type":"event"}
]""")

# Chainlink price feeds per chain
# Maps: chain_id -> asset_symbol -> aggregator_address
CHAINLINK_FEEDS: Dict[int, Dict[str, str]] = {
    1: {
        "ETH": "0x5f4eC3Df9cbd43714FE2740f5E3616155c5b8419",
        "BTC": "0xF4030086522a5bEEa4988F8cA5B36dbC97BeE88c",
        "LINK": "0x2c1d072e956AFFC0D435Cb7AC38EF18d24d9127c",
        "USDC": "0x8fFfFfd4AfB6115b954Bd326cbe7B4BA576818f6",
        "USDT": "0x3E7d1eAB13ad0104d2750B8863b489D65364e32D",
        "DAI": "0xAed0c38402a5d19df6E4c03F4E2DceD6e29c1ee9",
        "AAVE": "0x547a514d5e3769680Ce22B2361c10Ea13619e8a9",
        "UNI": "0x553303d460EE0afB37EdFf9bE42922D8FF63220e",
        "wstETH": "0x164b276057258d81941e97B0a900D4C7B358bCe0",
        "cbETH": "0xF017fcB346A1885194689bA23Eff2fE6fA5C483b",
        "rETH": "0x536218f9E9Eb48863970252233c8F271f554C2d0",
    },
    42161: {
        "ETH": "0x639Fe6ab55C939f4930680b556f0597271017801",
        "BTC": "0x6ce185860a4963106506C203335A2910413708e9",
        "USDC": "0x50834F3163758fcC1Df9973b6e91f0C0bd525C23",
        "ARB": "0xb2A824043730FE05F3DA2efaFa1CBbe83fa548D6",
    },
    10: {
        "ETH": "0x13e3Ee699D1909E989722E753853AE30b17e08c5",
        "BTC": "0xD702DD976Fb76Fffc2D3963D037dfDae5b04E593",
        "USDC": "0x16a9FEaCfFA43AdeCc6612dd74348590ecAb9794",
        "OP": "0x0D276FC14719f9292D5C1eA2198673d1f4269246",
    },
    8453: {
        "ETH": "0x71041dddad3595F9CEd3DcCFBe3D1F4b0a16Bb70",
        "USDC": "0x7e8648a8806220677F678508e8EBe5763071b782",
        "cbETH": "0xd7818272B9e248357d13057AAb0B417aF31E817d",
    },
    137: {
        "ETH": "0xF9680D99D6C9589e2a93a78A04A279e509205945",
        "BTC": "0xc907E116054Ad103354f2D350FD2514433D57F6f",
        "USDC": "0xfE4A8cc5b5B2366C1B58Bea3858e81843583ee2e",
        "MATIC": "0xAB594600376Ec9fD91F8e8dC4E7C98648D6Eb93b",
    },
}

# Symbol normalization: collateral symbols returned by the subgraph often have
# a "W" prefix (WETH, WBTC) or mixed-case LST names (wstETH, cbETH, rETH).
# This map canonicalises them to Chainlink feed keys.
_SYMBOL_ALIASES: Dict[str, str] = {
    "WETH": "ETH",
    "WBTC": "BTC",
    "WMATIC": "MATIC",
    # LST tokens keep their exact casing from CHAINLINK_FEEDS
    "WSTETH": "wstETH",
    "CBETH": "cbETH",
    "RETH": "rETH",
}

# Aave getUserAccountData ABI
AAVE_ACCOUNT_ABI = json.loads("""[
    {"inputs":[{"name":"user","type":"address"}],"name":"getUserAccountData","outputs":[
        {"name":"totalCollateralBase","type":"uint256"},
        {"name":"totalDebtBase","type":"uint256"},
        {"name":"availableBorrowsBase","type":"uint256"},
        {"name":"currentLiquidationThreshold","type":"uint256"},
        {"name":"ltv","type":"uint256"},
        {"name":"healthFactor","type":"uint256"}
    ],"stateMutability":"view","type":"function"}
]""")

# Multicall3 addresses (same on all EVM chains)
MULTICALL3 = "0xcA11bde05977b3631167028862bE2a173976CA11"
MULTICALL3_ABI = json.loads("""[
    {"inputs":[{"components":[
        {"name":"target","type":"address"},
        {"name":"callData","type":"bytes"}
    ],"name":"calls","type":"tuple[]"}],
    "name":"aggregate","outputs":[
        {"name":"blockNumber","type":"uint256"},
        {"name":"returnData","type":"bytes[]"}
    ],"stateMutability":"view","type":"function"}
]""")

CHAIN_NAMES = {
    1: "Ethereum", 42161: "Arbitrum", 10: "Optimism",
    137: "Polygon", 8453: "Base", 43114: "Avalanche",
}


# =====================================================================
# POSITION WATCHLIST
# =====================================================================

class PositionWatchlist:
    """
    Real-time position monitoring with three-layer trigger system.

    Usage:
        watchlist = PositionWatchlist(config, pipeline)
        await watchlist.initialize()

        # Ingest from SubgraphIndexer
        candidates = await indexer.full_scan()
        watchlist.ingest(candidates)

        # Start monitoring (blocks until stopped)
        await watchlist.start()
    """

    DEFAULT_CONFIG = {
        # Maximum positions to watch simultaneously
        "max_watched": 5000,

        # How often to refresh the full watchlist from subgraph (seconds)
        "refresh_interval": 60,

        # Block check intervals per priority level
        "check_interval_critical": 1,   # Every block
        "check_interval_high": 3,       # Every 3 blocks
        "check_interval_medium": 10,    # Every 10 blocks
        "check_interval_low": 0,        # Oracle events only

        # Multicall batch size
        "multicall_batch_size": 100,

        # Minimum profit to trigger (USD)
        "min_trigger_profit_usd": 1.0,

        # WebSocket RPC URLs for oracle event subscriptions
        "ws_rpc_urls": {
            1:     os.getenv("MAINNET_WS_URL", ""),
            42161: os.getenv("ARBITRUM_WS_URL", ""),
            10:    os.getenv("OPTIMISM_WS_URL", ""),
            8453:  os.getenv("BASE_WS_URL", ""),
            137:   os.getenv("POLYGON_WS_URL", ""),
        },

        # HTTP RPC URLs (fallback for multicall)
        "rpc_urls": {
            1:     os.getenv("MAINNET_RPC_URL", ""),
            42161: os.getenv("ARBITRUM_RPC_URL", ""),
            10:    os.getenv("OPTIMISM_RPC_URL", ""),
            137:   os.getenv("POLYGON_RPC_URL", ""),
            8453:  os.getenv("BASE_RPC_URL", ""),
            43114: os.getenv("AVALANCHE_RPC_URL", ""),
        },

        # Auto-evict positions that have been watched for too long
        # without triggering (they've probably recovered)
        "max_watch_age_seconds": 3600,  # 1 hour

        # HF threshold for ingestion
        "ingest_max_hf": 1.10,
    }

    def __init__(self, config: Dict[str, Any] = None, pipeline: Any = None):
        self.config = {**self.DEFAULT_CONFIG, **(config or {})}
        self.pipeline = pipeline  # UnifiedPipeline instance
        self.stats = WatchlistStats()
        self.is_running = False

        # Core state
        self._positions: Dict[str, WatchedPosition] = {}   # position_key -> WatchedPosition
        self._oracle_feeds: Dict[str, OracleFeed] = {}      # feed_key -> OracleFeed

        # Web3 connections
        self._w3_http: Dict[int, Web3] = {}

        # Block tracking per chain
        self._last_block: Dict[int, int] = {}

        # Callback for triggered positions (alternative to pipeline)
        self._on_trigger: Optional[Callable] = None

        logger.info("PositionWatchlist created")

    # -- Lifecycle ---------------------------------------------------

    async def initialize(self):
        """Initialize Web3 connections."""
        # HTTP connections for multicall
        for chain_id, url in self.config["rpc_urls"].items():
            if url:
                try:
                    w3 = Web3(Web3.HTTPProvider(url))
                    if w3.is_connected():
                        self._w3_http[chain_id] = w3
                        self._last_block[chain_id] = w3.eth.block_number
                        logger.info(
                            f"  HTTP connected: {CHAIN_NAMES.get(chain_id, chain_id)}"
                        )
                except Exception as e:
                    logger.warning(f"  HTTP failed for chain {chain_id}: {e}")

        logger.info(
            f"PositionWatchlist initialized -- "
            f"{len(self._w3_http)} chains connected"
        )

    async def shutdown(self):
        """Shut down monitoring."""
        self.is_running = False

    # -- Ingestion (from SubgraphIndexer) ---------------------------

    def ingest(self, candidates: list) -> int:
        """
        Ingest candidates from SubgraphIndexer into the watchlist.
        Returns number of new positions added.

        Candidates can be LiquidationCandidate objects or dicts.
        """
        added = 0
        evicted = 0

        for candidate in candidates:
            # Extract fields (handle both object and dict)
            if hasattr(candidate, "position_key"):
                key = candidate.position_key
                hf = candidate.health_factor
                user = candidate.user_address
                chain = candidate.chain_id
                protocol = (
                    candidate.protocol.value
                    if hasattr(candidate.protocol, "value")
                    else str(candidate.protocol)
                )
                pool = candidate.pool_address
                collateral_usd = candidate.total_collateral_usd
                debt_usd = candidate.total_debt_usd
                coll_asset = candidate.best_collateral_asset
                debt_asset = candidate.best_debt_asset
                max_liq = candidate.max_liquidatable_usd
                bonus = candidate.liquidation_bonus_pct
            else:
                continue

            # Filter
            if hf > self.config["ingest_max_hf"]:
                continue

            # Capacity check -- evict lowest priority if full
            if (
                len(self._positions) >= self.config["max_watched"]
                and key not in self._positions
            ):
                evicted_key = self._evict_lowest_priority()
                if evicted_key:
                    evicted += 1
                else:
                    continue  # Can't evict anything, skip

            # Create or update WatchedPosition
            if key in self._positions:
                # Update existing
                wp = self._positions[key]
                wp.health_factor = hf
                wp.total_collateral_usd = collateral_usd
                wp.total_debt_usd = debt_usd
                wp.max_liquidatable_usd = max_liq
                wp.update_priority()
                wp.compute_liquidation_price()
            else:
                # New position
                wp = WatchedPosition(
                    user_address=user,
                    chain_id=chain,
                    protocol=protocol,
                    pool_address=pool,
                    total_collateral_usd=collateral_usd,
                    total_debt_usd=debt_usd,
                    health_factor=hf,
                    collateral_asset=coll_asset,
                    debt_asset=debt_asset,
                    max_liquidatable_usd=max_liq,
                    liquidation_bonus_pct=bonus,
                )
                wp.update_priority()

                # Map to oracle feed
                self._map_oracle_feed(wp)

                # Compute liquidation trigger price
                wp.compute_liquidation_price()

                self._positions[key] = wp
                added += 1

        # Update stats
        self.stats.total_positions_watched = len(self._positions)
        self._update_priority_stats()

        if added > 0 or evicted > 0:
            logger.info(
                f"Watchlist updated: +{added} added, -{evicted} evicted, "
                f"{len(self._positions)} total"
            )

        return added

    # -- Main Monitoring Loop ---------------------------------------

    async def start(self):
        """
        Start the three-layer monitoring system.
        Runs until shutdown() is called.
        """
        self.is_running = True
        logger.info("Watchlist monitoring started")

        # Run all layers concurrently
        await asyncio.gather(
            self._block_verifier_loop(),
            self._position_cleanup_loop(),
            return_exceptions=True,
        )

    # -- Layer 2: Oracle Event Reactor ------------------------------

    async def handle_oracle_update(
        self, feed_address: str, chain_id: int, new_price: float
    ):
        """
        Called when a Chainlink AnswerUpdated event is detected.
        This is the speed advantage -- react to pending oracle TX.

        In production, this is called from a WebSocket subscription
        to the Chainlink aggregator contract's AnswerUpdated event.
        """
        self.stats.oracle_events_received += 1

        feed_key = f"{chain_id}:{feed_address.lower()}"
        feed = self._oracle_feeds.get(feed_key)
        if not feed:
            return

        old_price = feed.current_price
        feed.current_price = new_price
        feed.last_update_time = time.time()
        feed.price_history.append((time.time(), new_price))

        # Check all positions watching this feed
        triggered: List[WatchedPosition] = []

        for _pos_key, wp in feed.watching_positions.items():
            if wp.execution_sent:
                continue

            # Has the price crossed the liquidation trigger?
            if wp.liquidation_price_usd > 0 and new_price <= wp.liquidation_price_usd:
                wp.is_triggered = True
                wp.triggered_at = time.time()
                triggered.append(wp)

                logger.info(
                    f"ORACLE TRIGGER: {wp.collateral_symbol} "
                    f"${old_price:.2f} -> ${new_price:.2f} "
                    f"(trigger: ${wp.liquidation_price_usd:.2f}) "
                    f"user={wp.user_address[:10]}... "
                    f"est_profit=${wp.estimated_gross_profit:.2f}"
                )

        # Fire triggered positions through pipeline
        if triggered and self.pipeline:
            for wp in triggered:
                wp.execution_sent = True
                wp.times_triggered += 1
                self.stats.positions_triggered += 1

                # Convert to format pipeline expects
                candidate = self._to_pipeline_candidate(wp)
                try:
                    result = await self.pipeline.process_single(candidate)
                    logger.info(
                        f"Pipeline result: {result.stage.value} "
                        f"for {wp.user_address[:10]}..."
                    )
                except Exception as e:
                    logger.error(f"Pipeline execution failed: {e}")
                    wp.execution_sent = False  # Allow retry

    # -- Layer 3: Block Verifier (Multicall) ------------------------

    async def _block_verifier_loop(self):
        """
        Every new block, batch-verify priority positions via multicall.
        Catches anything the oracle reactor missed.
        """
        while self.is_running:
            try:
                for chain_id, w3 in self._w3_http.items():
                    current_block = w3.eth.block_number
                    last_block = self._last_block.get(chain_id, 0)

                    if current_block <= last_block:
                        continue

                    self._last_block[chain_id] = current_block
                    blocks_elapsed = current_block - last_block

                    # Get positions to check this block
                    to_check = self._get_positions_for_block_check(
                        chain_id, blocks_elapsed
                    )

                    if to_check:
                        await self._multicall_hf_check(chain_id, to_check, w3)

            except Exception as e:
                logger.error(f"Block verifier error: {e}")

            await asyncio.sleep(1)  # Poll every second

    async def _multicall_hf_check(
        self, chain_id: int, positions: List[WatchedPosition], w3: Web3
    ):
        """
        Batch-check health factors via Multicall3.
        Much cheaper than individual eth_calls.

        Uses the same w3.codec.decode / encodeABI pattern established in
        profit_engine.opportunity_scanner for consistency.
        """
        if not positions:
            return

        batch_size = self.config["multicall_batch_size"]
        self.stats.block_checks_performed += 1

        for i in range(0, len(positions), batch_size):
            batch = positions[i : i + batch_size]
            self.stats.multicall_batches += 1

            try:
                # Build multicall payload
                multicall = w3.eth.contract(
                    address=Web3.to_checksum_address(MULTICALL3),
                    abi=MULTICALL3_ABI,
                )

                calls: List[tuple] = []
                valid_batch: List[WatchedPosition] = []
                for wp in batch:
                    if not wp.pool_address:
                        continue

                    pool = w3.eth.contract(
                        address=Web3.to_checksum_address(wp.pool_address),
                        abi=AAVE_ACCOUNT_ABI,
                    )
                    calldata = pool.encodeABI(
                        fn_name="getUserAccountData",
                        args=[Web3.to_checksum_address(wp.user_address)],
                    )
                    # encodeABI returns hex string '0x...' -- convert to bytes
                    calldata_hex = (
                        calldata if isinstance(calldata, str) else calldata.hex()
                    )
                    if calldata_hex.startswith("0x"):
                        calldata_hex = calldata_hex[2:]

                    calls.append((
                        Web3.to_checksum_address(wp.pool_address),
                        bytes.fromhex(calldata_hex),
                    ))
                    valid_batch.append(wp)

                if not calls:
                    continue

                # Execute multicall
                block_num, return_data = multicall.functions.aggregate(
                    calls
                ).call()

                # Parse results
                triggered: List[WatchedPosition] = []

                for idx, (wp, data) in enumerate(zip(valid_batch, return_data)):
                    try:
                        # Decode getUserAccountData return
                        # (totalCollateralBase, totalDebtBase,
                        #  availableBorrowsBase, currentLiquidationThreshold,
                        #  ltv, healthFactor)
                        decoded = w3.codec.decode(
                            [
                                "uint256", "uint256", "uint256",
                                "uint256", "uint256", "uint256",
                            ],
                            data,
                        )
                        hf = decoded[5] / 1e18

                        # Update position
                        wp.last_hf_on_chain = hf
                        wp.last_checked_block = block_num
                        wp.last_checked_time = time.time()
                        wp.check_count += 1
                        wp.health_factor = hf
                        wp.update_priority()

                        # Check if liquidatable
                        if hf < 1.0 and not wp.execution_sent:
                            wp.is_triggered = True
                            wp.triggered_at = time.time()
                            triggered.append(wp)
                            logger.info(
                                f"BLOCK TRIGGER: HF={hf:.4f} "
                                f"user={wp.user_address[:10]}... "
                                f"chain={CHAIN_NAMES.get(chain_id, chain_id)}"
                            )

                    except Exception as e:
                        logger.debug(
                            f"Failed to decode multicall result {idx}: {e}"
                        )

                # Fire triggered positions
                if triggered and self.pipeline:
                    for wp in triggered:
                        wp.execution_sent = True
                        wp.times_triggered += 1
                        self.stats.positions_triggered += 1

                        candidate = self._to_pipeline_candidate(wp)
                        try:
                            await self.pipeline.process_single(candidate)
                        except Exception as e:
                            logger.error(f"Pipeline failed: {e}")
                            wp.execution_sent = False

            except Exception as e:
                logger.error(
                    f"Multicall batch failed on chain {chain_id}: {e}"
                )

    def _get_positions_for_block_check(
        self, chain_id: int, blocks_elapsed: int
    ) -> List[WatchedPosition]:
        """
        Select positions that should be checked this block based on priority.
        """
        check_intervals = {
            WatchPriority.CRITICAL: self.config["check_interval_critical"],
            WatchPriority.HIGH: self.config["check_interval_high"],
            WatchPriority.MEDIUM: self.config["check_interval_medium"],
            WatchPriority.LOW: self.config["check_interval_low"],
        }

        positions: List[WatchedPosition] = []

        for wp in self._positions.values():
            if wp.chain_id != chain_id:
                continue
            if wp.execution_sent:
                continue

            interval = check_intervals.get(wp.priority, 0)
            if interval <= 0:
                continue  # Oracle-only monitoring

            blocks_since_check = (
                self._last_block.get(chain_id, 0) - wp.last_checked_block
            )
            if blocks_since_check >= interval:
                positions.append(wp)

        # Sort by priority (CRITICAL first)
        positions.sort(key=lambda p: p.priority.value)

        return positions

    # -- Position Cleanup -------------------------------------------

    async def _position_cleanup_loop(self):
        """Periodically evict stale positions that have recovered."""
        while self.is_running:
            try:
                now = time.time()
                max_age = self.config["max_watch_age_seconds"]
                evicted: List[str] = []

                for key, wp in list(self._positions.items()):
                    # Evict if watched too long without triggering
                    if (
                        wp.last_checked_time > 0
                        and (now - wp.last_checked_time) > max_age
                        and wp.check_count > 5
                    ):
                        # Only evict if HF has recovered above safe threshold
                        if wp.health_factor > 1.10:
                            evicted.append(key)

                    # Evict if already executed
                    if (
                        wp.execution_sent
                        and wp.triggered_at > 0
                        and (now - wp.triggered_at) > 120
                    ):
                        evicted.append(key)

                for key in evicted:
                    self._remove_position(key)

                if evicted:
                    logger.info(f"Evicted {len(evicted)} stale positions")
                    self.stats.total_positions_watched = len(self._positions)

            except Exception as e:
                logger.error(f"Cleanup error: {e}")

            await asyncio.sleep(30)  # Run every 30 seconds

    # -- Oracle Feed Mapping ----------------------------------------

    def _map_oracle_feed(self, wp: WatchedPosition):
        """
        Map a position to its Chainlink oracle feed.

        Uses _SYMBOL_ALIASES to normalise wrapped/LST token symbols
        (WETH->ETH, WBTC->BTC, wstETH->wstETH) before looking up
        the CHAINLINK_FEEDS table.
        """
        chain_feeds = CHAINLINK_FEEDS.get(wp.chain_id, {})
        raw_symbol = wp.collateral_symbol.upper()

        # Step 1: check the alias table (handles WETH, WBTC, wstETH, etc.)
        canonical = _SYMBOL_ALIASES.get(raw_symbol)

        # Step 2: if no alias, try the uppercased symbol directly
        if canonical is None:
            canonical = raw_symbol

        feed_address = chain_feeds.get(canonical, "")

        # Step 3: if still no match, try original mixed-case symbol
        if not feed_address:
            feed_address = chain_feeds.get(wp.collateral_symbol, "")

        if feed_address:
            wp.oracle_feed_address = feed_address
            wp.oracle_chain_id = wp.chain_id

            feed_key = f"{wp.chain_id}:{feed_address.lower()}"
            if feed_key not in self._oracle_feeds:
                self._oracle_feeds[feed_key] = OracleFeed(
                    feed_address=feed_address,
                    chain_id=wp.chain_id,
                    asset_symbol=canonical,
                )

            self._oracle_feeds[feed_key].watching_positions[wp.position_key] = wp

    # -- Helpers ----------------------------------------------------

    def _to_pipeline_candidate(self, wp: WatchedPosition) -> Any:
        """
        Convert WatchedPosition to a format the UnifiedPipeline accepts.

        Creates a lightweight shim that satisfies
        PipelineCandidate.from_indexer_candidate()'s attribute access:
            .position_key, .protocol.value, .user_address, etc.
        """

        class _LiqCandidate:
            """Shim bridging WatchedPosition -> PipelineCandidate ingestion."""

            def __init__(self, w: WatchedPosition):
                self.user_address = w.user_address
                self.chain_id = w.chain_id
                # PipelineCandidate.from_indexer_candidate reads .protocol.value
                self.protocol = type("_Proto", (), {"value": w.protocol})()
                self.pool_address = w.pool_address
                self.health_factor = w.health_factor
                self.total_collateral_usd = w.total_collateral_usd
                self.total_debt_usd = w.total_debt_usd
                self.best_collateral_asset = w.collateral_asset
                self.best_debt_asset = w.debt_asset
                self.max_liquidatable_usd = w.max_liquidatable_usd
                self.liquidation_bonus_pct = w.liquidation_bonus_pct
                self.estimated_gross_profit_usd = w.estimated_gross_profit
                # process_batch checks hasattr(candidate, 'position_key')
                self.position_key = w.position_key

        return _LiqCandidate(wp)

    def _evict_lowest_priority(self) -> Optional[str]:
        """Evict the lowest-priority, oldest position."""
        if not self._positions:
            return None

        # Score: higher = worse (more evictable)
        worst_key: Optional[str] = None
        worst_score = -1.0

        for key, wp in self._positions.items():
            score = wp.priority.value * 1000.0 + (time.time() - wp.last_checked_time)
            if score > worst_score:
                worst_score = score
                worst_key = key

        if worst_key:
            self._remove_position(worst_key)
            return worst_key
        return None

    def _remove_position(self, key: str):
        """Remove a position and clean up oracle feed references."""
        wp = self._positions.pop(key, None)
        if wp and wp.oracle_feed_address:
            feed_key = f"{wp.chain_id}:{wp.oracle_feed_address.lower()}"
            feed = self._oracle_feeds.get(feed_key)
            if feed:
                feed.watching_positions.pop(key, None)

    def _update_priority_stats(self):
        """Update priority distribution stats."""
        counts: Dict[str, int] = defaultdict(int)
        for wp in self._positions.values():
            counts[wp.priority.name] += 1
        self.stats.positions_by_priority = dict(counts)

    # -- Diagnostics ------------------------------------------------

    def get_stats(self) -> Dict[str, Any]:
        """Return watchlist statistics."""
        return {
            "total_watched": self.stats.total_positions_watched,
            "by_priority": self.stats.positions_by_priority,
            "oracle_events": self.stats.oracle_events_received,
            "triggered": self.stats.positions_triggered,
            "block_checks": self.stats.block_checks_performed,
            "multicall_batches": self.stats.multicall_batches,
            "oracle_feeds_active": len(self._oracle_feeds),
            "chains_connected": len(self._w3_http),
        }

    def get_top_positions(self, n: int = 20) -> List[Dict]:
        """Return top N positions by urgency."""
        sorted_positions = sorted(
            self._positions.values(),
            key=lambda p: p.health_factor,
        )[:n]

        return [
            {
                "user": wp.user_address[:10] + "...",
                "chain": CHAIN_NAMES.get(wp.chain_id, wp.chain_id),
                "hf": round(wp.health_factor, 4),
                "debt_usd": round(wp.total_debt_usd, 2),
                "collateral": wp.collateral_symbol,
                "liq_price": round(wp.liquidation_price_usd, 2),
                "current_price": round(wp.collateral_price_usd, 2),
                "distance_pct": round(wp.price_distance_pct, 2),
                "priority": wp.priority.name,
                "est_profit": round(wp.estimated_gross_profit, 2),
                "triggered": wp.is_triggered,
            }
            for wp in sorted_positions
        ]
