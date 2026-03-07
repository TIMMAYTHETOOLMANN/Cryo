#!/usr/bin/env python3
"""
SUBGRAPH INDEXER — Live Protocol Position Discovery
=====================================================
Replaces hardcoded sample addresses with real-time subgraph queries
across Aave V3 and Compound V3 on all supported chains.

This is the single highest-impact enhancement: the existing system
checked 5 hardcoded addresses (one of which was invalid hex). This
module queries the full borrower universe — typically 10,000–50,000
active positions per protocol per chain.

Data Flow:
    SubgraphIndexer.poll()
        → GraphQL query to Aave V3 / Compound V3 subgraphs
        → Filter positions by health_factor < configurable threshold
        → Enrich with reserve config (liquidation bonus, decimals, etc.)
        → Yield LiquidationCandidate objects
        → Feed into PositionWatchlist (Priority 3)

Supported Protocols:
    - Aave V3 (Ethereum, Arbitrum, Optimism, Base, Polygon, Avalanche)
    - Compound V3 (Ethereum, Arbitrum, Polygon, Base)

Query Architecture:
    - Paginated queries (1000 positions per page, The Graph limit)
    - Exponential backoff on rate limits
    - Parallel chain queries via asyncio.gather
    - Local dedup cache (position_key = chain:protocol:user)
    - Staleness tracking per chain/protocol pair
"""

import asyncio
import time
import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any, Tuple
from enum import Enum
from collections import defaultdict

import aiohttp

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════
# DATA MODELS
# ═══════════════════════════════════════════════════════════════════

class Protocol(Enum):
    AAVE_V3 = "aave_v3"
    COMPOUND_V3 = "compound_v3"


@dataclass
class ReserveConfig:
    """On-chain reserve configuration for a collateral/debt asset."""
    asset_address: str
    asset_symbol: str
    decimals: int
    liquidation_threshold: float    # e.g. 0.825
    liquidation_bonus: float        # e.g. 0.05 (5%)
    ltv: float                      # e.g. 0.80
    usage_as_collateral_enabled: bool
    borrowing_enabled: bool
    is_active: bool
    oracle_address: str = ""
    current_price_usd: float = 0.0


@dataclass
class UserPosition:
    """A single collateral or debt position for a user."""
    asset_address: str
    asset_symbol: str
    balance_raw: int                # Raw on-chain value (scaled)
    balance_usd: float
    is_collateral: bool             # True = collateral, False = debt


@dataclass
class LiquidationCandidate:
    """
    A borrower position eligible for or approaching liquidation.
    This is the primary output of the SubgraphIndexer.
    """
    # Identity
    user_address: str
    chain_id: int
    protocol: Protocol
    pool_address: str

    # Health
    health_factor: float
    total_collateral_usd: float
    total_debt_usd: float

    # Positions (for choosing optimal collateral/debt pair)
    collateral_positions: List[UserPosition] = field(default_factory=list)
    debt_positions: List[UserPosition] = field(default_factory=list)

    # Best liquidation path (pre-computed)
    best_collateral_asset: str = ""
    best_debt_asset: str = ""
    max_liquidatable_usd: float = 0.0
    liquidation_bonus_pct: float = 0.0
    estimated_gross_profit_usd: float = 0.0

    # Metadata
    last_updated_block: int = 0
    last_indexed_at: float = 0.0
    staleness_seconds: float = 0.0

    @property
    def position_key(self) -> str:
        return f"{self.chain_id}:{self.protocol.value}:{self.user_address.lower()}"

    @property
    def is_liquidatable(self) -> bool:
        return self.health_factor < 1.0

    @property
    def is_approaching(self) -> bool:
        return 1.0 <= self.health_factor < 1.05

    @property
    def urgency_score(self) -> float:
        """0-1 urgency. 1.0 = liquidatable now, 0.0 = safe."""
        if self.health_factor <= 0:
            return 1.0
        if self.health_factor >= 1.1:
            return 0.0
        # Linear scale: HF 1.0 → 0.9, HF 0.5 → 1.0, HF 1.1 → 0.0
        return max(0.0, min(1.0, (1.1 - self.health_factor) / 1.1))


@dataclass
class IndexerStats:
    """Tracking metrics for the indexer."""
    total_queries: int = 0
    total_positions_scanned: int = 0
    total_candidates_found: int = 0
    queries_failed: int = 0
    last_full_scan_duration_ms: float = 0.0
    positions_by_chain: Dict[int, int] = field(default_factory=lambda: defaultdict(int))
    candidates_by_chain: Dict[int, int] = field(default_factory=lambda: defaultdict(int))


# ═══════════════════════════════════════════════════════════════════
# SUBGRAPH ENDPOINTS
# ═══════════════════════════════════════════════════════════════════

# ── The Graph Decentralized Network ──────────────────────────────
# The old hosted service (api.thegraph.com/subgraphs/name/...) was
# permanently shut down.  All queries must go through the decentralized
# network gateway, which requires a GRAPH_API_KEY.
#
# Subgraph deployment IDs on the decentralized network:
import os as _os
_GRAPH_API_KEY = _os.getenv("GRAPH_API_KEY", "")

def _graph_url(deployment_id: str) -> str:
    """Build a decentralized Graph gateway URL for a given deployment ID."""
    if not _GRAPH_API_KEY:
        return ""   # No key → endpoint disabled; on-chain bootstrap is primary
    return (
        f"https://gateway.thegraph.com/api/{_GRAPH_API_KEY}"
        f"/subgraphs/id/{deployment_id}"
    )

# Aave V3 subgraph deployment IDs (decentralized network)
AAVE_V3_SUBGRAPHS: Dict[int, str] = {
    1:     _graph_url("C2zniPn45RnLDGzVeGZCx2Sw3GXrbc9gL4ZfL8B8Em2j"),   # Ethereum
    42161: _graph_url("DLuE98kEb26JkDY3zrstHNKEMB4GxPrGdKmcJEpfpvkR"),   # Arbitrum
    10:    _graph_url("DSfLz8oQBUeU5atALgUFQKMTSYV5j1AkRRwSGFCLgEn"),    # Optimism
    137:   _graph_url("Co2URyXjM1mXhN62tHBRkJv8Cn97JBMEUBenT6CwzAv4"),   # Polygon
    8453:  _graph_url("GQFbb95cE6d8mB1EuRBR7FjtCSy82DFaq2BRCV4g82wP"),   # Base
    43114: _graph_url("2h9woxy8RTjHu1HJsCEnmzL9pvMRKBEJEA7VDAe3AQGE"),   # Avalanche
}

# Compound V3 subgraph deployment IDs
COMPOUND_V3_SUBGRAPHS: Dict[int, str] = {
    1:     _graph_url("4EL6BRMj2RGBi2WjcDs5FMsNpLmocmJqBWowav37aRJB"),   # Ethereum
    42161: _graph_url("9VAdxs1x7UDioVbiCJvLFNjEnJboJEkyDz7BFn5Fu5X9"),   # Arbitrum
    137:   _graph_url("9nGHiCJYJqJQJG6Yv3WFAF99jrqPoUgqPyPzjZnLFRM3"),   # Polygon
    8453:  _graph_url("HvRtiT9CAdBB7hCHLuPbGnSawHNwWviZ6p3Maq4RWec2"),   # Base
}

# Remove empty endpoints (no API key configured)
AAVE_V3_SUBGRAPHS = {k: v for k, v in AAVE_V3_SUBGRAPHS.items() if v}
COMPOUND_V3_SUBGRAPHS = {k: v for k, v in COMPOUND_V3_SUBGRAPHS.items() if v}

if not _GRAPH_API_KEY:
    logger.warning(
        "GRAPH_API_KEY not set — subgraph queries disabled. "
        "On-chain event bootstrap is primary borrower discovery path. "
        "Get a free key at https://thegraph.com/studio/apikeys/"
    )

# Aave V3 pool addresses per chain (for LiquidationCandidate.pool_address)
AAVE_V3_POOLS: Dict[int, str] = {
    1:     "0x87870Bca3F3fD6335C3F4ce8392D69350B4fA4E2",
    42161: "0x794a61358D6845594F94dc1DB02A252b5b4814aD",
    10:    "0xB50201558B00496A145fE76f7424749556E326D8",
    137:   "0x794a61358D6845594F94dc1DB02A252b5b4814aD",
    8453:  "0xA238Dd80C259a72e81d7e4664a9801593F337052",
    43114: "0x794a61358D6845594F94dc1DB02A252b5b4814aD",
}

# Chain names for logging
CHAIN_NAMES: Dict[int, str] = {
    1: "Ethereum", 42161: "Arbitrum", 10: "Optimism",
    137: "Polygon", 8453: "Base", 43114: "Avalanche",
}


# ═══════════════════════════════════════════════════════════════════
# GRAPHQL QUERIES
# ═══════════════════════════════════════════════════════════════════

# Aave V3: fetch all users with health factor below threshold.
# The subgraph stores positions per-reserve per-user.  We query
# userReserve entities where the parent user's borrowing is active,
# then aggregate off-chain into LiquidationCandidate objects.
AAVE_V3_POSITIONS_QUERY = """
query GetAtRiskPositions($hfThreshold: BigDecimal!, $skip: Int!, $first: Int!) {
  users(
    where: {
      borrowedReservesCount_gt: 0
    }
    first: $first
    skip: $skip
    orderBy: id
  ) {
    id
    borrowedReservesCount
    reserves(where: { currentTotalDebt_gt: "0" }) {
      currentATokenBalance
      currentTotalDebt
      reserve {
        underlyingAsset
        symbol
        decimals
        liquidationThreshold
        reserveLiquidationBonus
        baseLTVasCollateral
        usageAsCollateralEnabled
        borrowingEnabled
        isActive
        price {
          priceInEth
        }
      }
      usageAsCollateralEnabledOnUser
    }
  }
}
"""

# Lighter query for just user-level aggregate data (faster initial scan)
AAVE_V3_USERS_LIGHT_QUERY = """
query GetBorrowers($skip: Int!, $first: Int!) {
  users(
    where: { borrowedReservesCount_gt: 0 }
    first: $first
    skip: $skip
    orderBy: id
  ) {
    id
    borrowedReservesCount
  }
}
"""

# Compound V3: query accounts with negative balance (borrowing)
COMPOUND_V3_POSITIONS_QUERY = """
query GetBorrowers($skip: Int!, $first: Int!) {
  accounts(
    where: { health_lt: "1100000000000000000" }
    first: $first
    skip: $skip
    orderBy: health
    orderDirection: asc
  ) {
    id
    address
    health
    totalBorrowBalanceUsd
    totalCollateralBalanceUsd
    positions {
      asset {
        address
        symbol
        decimals
        price
      }
      balance
      isCollateral
    }
  }
}
"""

# Reserve config query (run once at startup per chain)
AAVE_V3_RESERVES_QUERY = """
query GetReserves {
  reserves(where: { isActive: true }) {
    underlyingAsset
    symbol
    decimals
    liquidationThreshold
    reserveLiquidationBonus
    baseLTVasCollateral
    usageAsCollateralEnabled
    borrowingEnabled
    isActive
    price {
      priceInEth
    }
  }
}
"""


# ═══════════════════════════════════════════════════════════════════
# SUBGRAPH INDEXER
# ═══════════════════════════════════════════════════════════════════

class SubgraphIndexer:
    """
    Queries Aave V3 and Compound V3 subgraphs across all chains to
    discover real borrowing positions approaching or below liquidation.

    Usage:
        indexer = SubgraphIndexer(config)
        await indexer.initialize()

        # Full scan — returns all candidates across all chains
        candidates = await indexer.full_scan()

        # Continuous polling loop
        async for batch in indexer.poll_loop(interval_seconds=12):
            for candidate in batch:
                watchlist.add(candidate)
    """

    DEFAULT_CONFIG = {
        # Health factor threshold: positions with HF below this are indexed.
        # 1.10 captures "approaching" positions with buffer.
        "hf_threshold": 1.10,

        # Minimum debt size (USD) — skip dust positions.
        "min_debt_usd": 100.0,

        # Maximum positions per query page (The Graph caps at 1000).
        "page_size": 1000,

        # Maximum pages per chain/protocol pair (safety cap).
        "max_pages": 50,

        # Chains to scan. Default: all supported.
        "enabled_chains": [1, 42161, 10, 137, 8453, 43114],

        # Protocols to scan.
        "enabled_protocols": ["aave_v3", "compound_v3"],

        # Request timeout per query (seconds).
        "query_timeout": 30,

        # Retry config.
        "max_retries": 3,
        "retry_backoff_base": 2.0,

        # ETH price (fallback if oracle unavailable). Updated at runtime.
        "eth_price_usd": 2000.0,

        # Poll interval for continuous mode.
        "poll_interval_seconds": 12,  # ~1 Ethereum block

        # Parallel chain queries.
        "max_concurrent_chains": 6,
    }

    def __init__(self, config: Dict[str, Any] = None):
        self.config = {**self.DEFAULT_CONFIG, **(config or {})}
        self.stats = IndexerStats()
        self._session: Optional[aiohttp.ClientSession] = None

        # Reserve config cache: chain_id → asset_address → ReserveConfig
        self._reserve_cache: Dict[int, Dict[str, ReserveConfig]] = defaultdict(dict)
        self._reserve_cache_age: Dict[int, float] = {}

        # Position dedup cache: position_key → LiquidationCandidate
        self._position_cache: Dict[str, LiquidationCandidate] = {}

        # Rate limit tracking per endpoint
        self._last_query_time: Dict[str, float] = defaultdict(float)

        logger.info("SubgraphIndexer initialized")
        logger.info(f"  Chains: {[CHAIN_NAMES.get(c, c) for c in self.config['enabled_chains']]}")
        logger.info(f"  HF threshold: {self.config['hf_threshold']}")
        logger.info(f"  Min debt: ${self.config['min_debt_usd']}")

    # ── Lifecycle ──────────────────────────────────────────────────

    async def initialize(self):
        """Initialize HTTP session and warm reserve config cache."""
        self._session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=self.config["query_timeout"]),
            headers={
                "Content-Type": "application/json",
                "Accept-Encoding": "gzip, deflate",   # avoid brotli (br) — no decoder
            },
        )
        # Warm reserve configs for all chains
        await self._warm_reserve_caches()
        logger.info("SubgraphIndexer ready")

    async def shutdown(self):
        """Close HTTP session."""
        if self._session:
            await self._session.close()
            self._session = None

    # ── Core Scan Methods ─────────────────────────────────────────

    async def full_scan(self) -> List[LiquidationCandidate]:
        """
        Scan all enabled chains and protocols in parallel.
        Returns deduplicated list of LiquidationCandidates sorted by urgency.
        """
        t0 = time.time()
        all_candidates: List[LiquidationCandidate] = []

        # Build task list: one task per (chain, protocol) pair
        tasks = []
        for chain_id in self.config["enabled_chains"]:
            for proto_str in self.config["enabled_protocols"]:
                protocol = Protocol(proto_str)
                tasks.append(self._scan_chain_protocol(chain_id, protocol))

        # Execute with concurrency limit
        sem = asyncio.Semaphore(self.config["max_concurrent_chains"])

        async def bounded(coro):
            async with sem:
                return await coro

        results = await asyncio.gather(
            *[bounded(t) for t in tasks],
            return_exceptions=True,
        )

        # Collect results, log errors
        for result in results:
            if isinstance(result, Exception):
                logger.error(f"Scan task failed: {result}")
                self.stats.queries_failed += 1
            elif isinstance(result, list):
                all_candidates.extend(result)

        # Dedup by position_key (keep freshest)
        deduped: Dict[str, LiquidationCandidate] = {}
        for c in all_candidates:
            key = c.position_key
            if key not in deduped or c.last_indexed_at > deduped[key].last_indexed_at:
                deduped[key] = c

        candidates = list(deduped.values())

        # Update cache
        for c in candidates:
            self._position_cache[c.position_key] = c

        # Sort by urgency (lowest HF first)
        candidates.sort(key=lambda c: c.health_factor)

        elapsed_ms = (time.time() - t0) * 1000
        self.stats.last_full_scan_duration_ms = elapsed_ms
        self.stats.total_candidates_found = len(candidates)

        logger.info(
            f"Full scan complete: {len(candidates)} candidates "
            f"from {self.stats.total_positions_scanned} positions "
            f"in {elapsed_ms:.0f}ms"
        )

        return candidates

    async def poll_loop(self, interval_seconds: float = None):
        """
        Async generator: yields batches of candidates at regular intervals.

        Usage:
            async for batch in indexer.poll_loop():
                process(batch)
        """
        interval = interval_seconds or self.config["poll_interval_seconds"]

        while True:
            try:
                candidates = await self.full_scan()
                if candidates:
                    yield candidates
            except Exception as e:
                logger.error(f"Poll cycle error: {e}")

            await asyncio.sleep(interval)

    # ── Per-Chain/Protocol Scan ───────────────────────────────────

    async def _scan_chain_protocol(
        self, chain_id: int, protocol: Protocol
    ) -> List[LiquidationCandidate]:
        """Scan a single chain+protocol pair with pagination."""

        if protocol == Protocol.AAVE_V3:
            return await self._scan_aave_v3(chain_id)
        elif protocol == Protocol.COMPOUND_V3:
            return await self._scan_compound_v3(chain_id)
        return []

    async def _scan_aave_v3(self, chain_id: int) -> List[LiquidationCandidate]:
        """Query Aave V3 subgraph for at-risk positions on a single chain."""
        endpoint = AAVE_V3_SUBGRAPHS.get(chain_id)
        if not endpoint:
            return []

        candidates: List[LiquidationCandidate] = []
        skip = 0
        page_size = self.config["page_size"]
        max_pages = self.config["max_pages"]
        eth_price = self.config["eth_price_usd"]

        chain_name = CHAIN_NAMES.get(chain_id, str(chain_id))
        logger.info(f"Scanning Aave V3 on {chain_name}...")

        users_on_last_page: List[Dict] = []

        for page in range(max_pages):
            variables = {
                "hfThreshold": str(self.config["hf_threshold"]),
                "skip": skip,
                "first": page_size,
            }

            data = await self._graphql_query(
                endpoint, AAVE_V3_POSITIONS_QUERY, variables
            )
            if not data or "users" not in data:
                break

            users = data["users"]
            if not users:
                break

            users_on_last_page = users

            self.stats.total_positions_scanned += len(users)
            self.stats.positions_by_chain[chain_id] += len(users)

            for user_data in users:
                candidate = self._parse_aave_v3_user(
                    user_data, chain_id, eth_price
                )
                if candidate and self._passes_filters(candidate):
                    candidates.append(candidate)

            # If we got fewer results than page_size, we've exhausted
            if len(users) < page_size:
                break

            skip += page_size

        self.stats.candidates_by_chain[chain_id] += len(candidates)
        logger.info(
            f"  {chain_name} Aave V3: {len(candidates)} candidates "
            f"(scanned {skip + len(users_on_last_page)} users)"
        )

        return candidates

    async def _scan_compound_v3(self, chain_id: int) -> List[LiquidationCandidate]:
        """Query Compound V3 subgraph for at-risk positions."""
        endpoint = COMPOUND_V3_SUBGRAPHS.get(chain_id)
        if not endpoint:
            return []

        candidates: List[LiquidationCandidate] = []
        skip = 0
        page_size = self.config["page_size"]
        max_pages = self.config["max_pages"]

        chain_name = CHAIN_NAMES.get(chain_id, str(chain_id))
        logger.info(f"Scanning Compound V3 on {chain_name}...")

        for page in range(max_pages):
            variables = {"skip": skip, "first": page_size}

            data = await self._graphql_query(
                endpoint, COMPOUND_V3_POSITIONS_QUERY, variables
            )
            if not data or "accounts" not in data:
                break

            accounts = data["accounts"]
            if not accounts:
                break

            self.stats.total_positions_scanned += len(accounts)

            for acct in accounts:
                candidate = self._parse_compound_v3_account(acct, chain_id)
                if candidate and self._passes_filters(candidate):
                    candidates.append(candidate)

            if len(accounts) < page_size:
                break

            skip += page_size

        logger.info(f"  {chain_name} Compound V3: {len(candidates)} candidates")
        return candidates

    # ── Parsers ───────────────────────────────────────────────────

    def _parse_aave_v3_user(
        self, user_data: Dict, chain_id: int, eth_price: float
    ) -> Optional[LiquidationCandidate]:
        """
        Parse an Aave V3 subgraph user entity into a LiquidationCandidate.

        The subgraph doesn't directly expose healthFactor — we compute it
        from the per-reserve data:

            HF = Σ(collateral_i × LT_i) / Σ(debt_j)

        where collateral and debt are in USD terms.
        """
        try:
            user_address = user_data["id"]
            reserves = user_data.get("reserves", [])

            if not reserves:
                return None

            collateral_positions: List[UserPosition] = []
            debt_positions: List[UserPosition] = []
            total_collateral_usd = 0.0
            total_debt_usd = 0.0
            weighted_threshold_sum = 0.0  # For HF calculation

            for r in reserves:
                reserve_info = r.get("reserve", {})
                symbol = reserve_info.get("symbol", "UNKNOWN")
                asset = reserve_info.get("underlyingAsset", "")
                decimals = int(reserve_info.get("decimals", 18))

                # Price in ETH → USD
                price_in_eth = float(
                    reserve_info.get("price", {}).get("priceInEth", "0")
                )
                # priceInEth from Aave subgraph is in wei (1e18 scale)
                price_usd = (price_in_eth / 1e18) * eth_price if price_in_eth > 0 else 0

                liq_threshold = float(
                    reserve_info.get("liquidationThreshold", "0")
                ) / 10000  # Aave stores as basis points × 100

                liq_bonus = float(
                    reserve_info.get("reserveLiquidationBonus", "0")
                ) / 10000

                is_collateral_enabled = r.get("usageAsCollateralEnabledOnUser", False)

                # Collateral (aToken balance)
                atoken_raw = int(r.get("currentATokenBalance", "0"))
                if atoken_raw > 0 and is_collateral_enabled and price_usd > 0:
                    balance_usd = (atoken_raw / (10 ** decimals)) * price_usd
                    total_collateral_usd += balance_usd
                    weighted_threshold_sum += balance_usd * liq_threshold

                    collateral_positions.append(UserPosition(
                        asset_address=asset,
                        asset_symbol=symbol,
                        balance_raw=atoken_raw,
                        balance_usd=balance_usd,
                        is_collateral=True,
                    ))

                # Debt
                debt_raw = int(r.get("currentTotalDebt", "0"))
                if debt_raw > 0 and price_usd > 0:
                    debt_usd = (debt_raw / (10 ** decimals)) * price_usd
                    total_debt_usd += debt_usd

                    debt_positions.append(UserPosition(
                        asset_address=asset,
                        asset_symbol=symbol,
                        balance_raw=debt_raw,
                        balance_usd=debt_usd,
                        is_collateral=False,
                    ))

            # Compute health factor
            if total_debt_usd <= 0:
                return None

            health_factor = (
                weighted_threshold_sum / total_debt_usd
                if total_debt_usd > 0
                else 999.0
            )

            # Pre-compute best liquidation path
            best_collateral, best_debt, max_liq_usd, bonus_pct, gross_profit = (
                self._compute_best_liquidation_path(
                    collateral_positions, debt_positions, reserves, chain_id
                )
            )

            return LiquidationCandidate(
                user_address=user_address,
                chain_id=chain_id,
                protocol=Protocol.AAVE_V3,
                pool_address=AAVE_V3_POOLS.get(chain_id, ""),
                health_factor=health_factor,
                total_collateral_usd=total_collateral_usd,
                total_debt_usd=total_debt_usd,
                collateral_positions=collateral_positions,
                debt_positions=debt_positions,
                best_collateral_asset=best_collateral,
                best_debt_asset=best_debt,
                max_liquidatable_usd=max_liq_usd,
                liquidation_bonus_pct=bonus_pct,
                estimated_gross_profit_usd=gross_profit,
                last_indexed_at=time.time(),
            )

        except Exception as e:
            logger.debug(f"Failed to parse Aave user: {e}")
            return None

    def _parse_compound_v3_account(
        self, acct: Dict, chain_id: int
    ) -> Optional[LiquidationCandidate]:
        """Parse a Compound V3 subgraph account into a LiquidationCandidate."""
        try:
            user_address = acct.get("address", acct.get("id", ""))
            health_raw = acct.get("health", "0")

            # Compound V3 health is stored as 18-decimal fixed point
            health_factor = int(health_raw) / 1e18 if health_raw else 999.0

            total_borrow = float(acct.get("totalBorrowBalanceUsd", "0"))
            total_collateral = float(acct.get("totalCollateralBalanceUsd", "0"))

            if total_borrow <= 0:
                return None

            collateral_positions: List[UserPosition] = []
            debt_positions: List[UserPosition] = []

            for pos in acct.get("positions", []):
                asset_info = pos.get("asset", {})
                balance = float(pos.get("balance", "0"))
                price = float(asset_info.get("price", "0"))
                is_collateral = pos.get("isCollateral", False)

                balance_usd = abs(balance) * price / 1e8 if price > 0 else 0

                up = UserPosition(
                    asset_address=asset_info.get("address", ""),
                    asset_symbol=asset_info.get("symbol", ""),
                    balance_raw=int(balance),
                    balance_usd=balance_usd,
                    is_collateral=is_collateral,
                )
                if is_collateral:
                    collateral_positions.append(up)
                else:
                    debt_positions.append(up)

            # Compound V3 liquidation incentive is typically 5%
            bonus_pct = 0.05
            # Compound allows liquidating the full position
            max_liq_usd = total_borrow
            gross_profit = max_liq_usd * bonus_pct

            return LiquidationCandidate(
                user_address=user_address,
                chain_id=chain_id,
                protocol=Protocol.COMPOUND_V3,
                pool_address="",  # Set from config
                health_factor=health_factor,
                total_collateral_usd=total_collateral,
                total_debt_usd=total_borrow,
                collateral_positions=collateral_positions,
                debt_positions=debt_positions,
                best_collateral_asset=(
                    collateral_positions[0].asset_address if collateral_positions else ""
                ),
                best_debt_asset=(
                    debt_positions[0].asset_address if debt_positions else ""
                ),
                max_liquidatable_usd=max_liq_usd,
                liquidation_bonus_pct=bonus_pct,
                estimated_gross_profit_usd=gross_profit,
                last_indexed_at=time.time(),
            )

        except Exception as e:
            logger.debug(f"Failed to parse Compound account: {e}")
            return None

    # ── Liquidation Path Optimizer ────────────────────────────────

    def _compute_best_liquidation_path(
        self,
        collateral_positions: List[UserPosition],
        debt_positions: List[UserPosition],
        raw_reserves: List[Dict],
        chain_id: int,
    ) -> Tuple[str, str, float, float, float]:
        """
        For a given user, determine the optimal collateral/debt pair
        to maximize profit from liquidation.

        Aave V3 allows liquidating up to 50% of a user's debt (close factor).
        The liquidator repays debt and receives collateral + bonus.

        Returns: (best_collateral, best_debt, max_liq_usd, bonus_pct, gross_profit)
        """
        best: Tuple[str, str, float, float, float] = ("", "", 0.0, 0.0, 0.0)

        if not collateral_positions or not debt_positions:
            return best

        # Build lookup: asset_address → liquidation bonus
        bonus_lookup: Dict[str, float] = {}
        for r in raw_reserves:
            reserve = r.get("reserve", {})
            asset = reserve.get("underlyingAsset", "").lower()
            bonus = float(reserve.get("reserveLiquidationBonus", "0")) / 10000
            # Aave stores bonus as 10000 + bonus_bps, so 10500 = 5%
            # After dividing by 10000: 1.05; subtract 1.0 to get 0.05
            actual_bonus = max(0.0, bonus - 1.0) if bonus > 1.0 else bonus
            bonus_lookup[asset] = actual_bonus

        # Aave close factor: 50% of total debt in normal conditions,
        # 100% when HF < 0.95 (CLOSE_FACTOR_HF_THRESHOLD)
        close_factor = 0.5

        best_profit = 0.0

        for collateral in collateral_positions:
            bonus = bonus_lookup.get(collateral.asset_address.lower(), 0.05)

            for debt in debt_positions:
                # Max debt we can cover = debt * close_factor
                max_debt_cover_usd = debt.balance_usd * close_factor

                # But limited by available collateral (including bonus)
                max_by_collateral = (
                    collateral.balance_usd / (1 + bonus)
                    if bonus > 0
                    else collateral.balance_usd
                )

                liquidatable_usd = min(max_debt_cover_usd, max_by_collateral)
                gross_profit = liquidatable_usd * bonus

                if gross_profit > best_profit:
                    best_profit = gross_profit
                    best = (
                        collateral.asset_address,
                        debt.asset_address,
                        liquidatable_usd,
                        bonus,
                        gross_profit,
                    )

        return best

    # ── Filters ───────────────────────────────────────────────────

    def _passes_filters(self, candidate: LiquidationCandidate) -> bool:
        """Apply configurable filters to determine if a candidate is worth tracking."""
        # Health factor filter
        if candidate.health_factor > self.config["hf_threshold"]:
            return False

        # Minimum debt filter
        if candidate.total_debt_usd < self.config["min_debt_usd"]:
            return False

        # Must have both collateral and debt
        if not candidate.collateral_positions or not candidate.debt_positions:
            return False

        return True

    # ── Reserve Cache ─────────────────────────────────────────────

    async def _warm_reserve_caches(self):
        """Pre-fetch reserve configurations for all enabled chains."""
        tasks = []
        for chain_id in self.config["enabled_chains"]:
            if chain_id in AAVE_V3_SUBGRAPHS:
                tasks.append(self._fetch_reserves(chain_id))

        results = await asyncio.gather(*tasks, return_exceptions=True)
        for r in results:
            if isinstance(r, Exception):
                logger.warning(f"Failed to warm reserve cache: {r}")

    async def _fetch_reserves(self, chain_id: int):
        """Fetch and cache reserve configs for a chain."""
        endpoint = AAVE_V3_SUBGRAPHS.get(chain_id)
        if not endpoint:
            return

        data = await self._graphql_query(endpoint, AAVE_V3_RESERVES_QUERY, {})
        if not data or "reserves" not in data:
            return

        for reserve in data["reserves"]:
            asset = reserve.get("underlyingAsset", "").lower()
            self._reserve_cache[chain_id][asset] = ReserveConfig(
                asset_address=asset,
                asset_symbol=reserve.get("symbol", ""),
                decimals=int(reserve.get("decimals", 18)),
                liquidation_threshold=(
                    float(reserve.get("liquidationThreshold", "0")) / 10000
                ),
                liquidation_bonus=(
                    float(reserve.get("reserveLiquidationBonus", "0")) / 10000 - 1.0
                ),
                ltv=float(reserve.get("baseLTVasCollateral", "0")) / 10000,
                usage_as_collateral_enabled=reserve.get(
                    "usageAsCollateralEnabled", False
                ),
                borrowing_enabled=reserve.get("borrowingEnabled", False),
                is_active=reserve.get("isActive", False),
            )

        self._reserve_cache_age[chain_id] = time.time()
        logger.info(
            f"  Cached {len(self._reserve_cache[chain_id])} reserves "
            f"for {CHAIN_NAMES.get(chain_id, chain_id)}"
        )

    # ── GraphQL Transport ─────────────────────────────────────────

    async def _graphql_query(
        self, endpoint: str, query: str, variables: Dict
    ) -> Optional[Dict]:
        """
        Execute a GraphQL query with retry and exponential backoff.
        Returns the 'data' field from the response, or None on failure.
        """
        if not self._session:
            raise RuntimeError(
                "SubgraphIndexer not initialized. Call initialize() first."
            )

        max_retries = self.config["max_retries"]
        backoff_base = self.config["retry_backoff_base"]

        payload = {"query": query, "variables": variables}

        for attempt in range(max_retries):
            try:
                # Rate limiting: minimum 100ms between requests to same endpoint
                last = self._last_query_time.get(endpoint, 0)
                elapsed = time.time() - last
                if elapsed < 0.1:
                    await asyncio.sleep(0.1 - elapsed)

                self._last_query_time[endpoint] = time.time()
                self.stats.total_queries += 1

                async with self._session.post(endpoint, json=payload) as resp:
                    if resp.status == 429:
                        # Rate limited — back off
                        wait = backoff_base ** (attempt + 1)
                        logger.warning(
                            f"Rate limited by {endpoint}, "
                            f"backing off {wait:.1f}s"
                        )
                        await asyncio.sleep(wait)
                        continue

                    if resp.status != 200:
                        body = ""
                        try:
                            body = await resp.text()
                        except Exception:
                            pass
                        logger.error(
                            f"Query error on {endpoint}: {resp.status}, "
                            f"message:\n  {body[:200]}"
                        )
                        continue

                    result = await resp.json(content_type=None)

                    # Detect dead/migrated endpoints
                    if "message" in result and "removed" in str(result.get("message", "")).lower():
                        logger.error(
                            f"Subgraph endpoint removed: {endpoint} — "
                            f"set GRAPH_API_KEY for decentralized network"
                        )
                        return None

                    if "errors" in result:
                        logger.warning(
                            f"GraphQL errors from {endpoint}: "
                            f"{result['errors'][:200]}"
                        )
                        # Some errors are recoverable (timeout), retry
                        if attempt < max_retries - 1:
                            await asyncio.sleep(backoff_base ** attempt)
                            continue
                        return None

                    return result.get("data")

            except asyncio.TimeoutError:
                logger.warning(
                    f"Timeout querying {endpoint} (attempt {attempt + 1})"
                )
                if attempt < max_retries - 1:
                    await asyncio.sleep(backoff_base ** attempt)
            except Exception as e:
                logger.error(f"Query error on {endpoint}: {e}")
                if attempt < max_retries - 1:
                    await asyncio.sleep(backoff_base ** attempt)

        self.stats.queries_failed += 1
        return None

    # ── Utility ───────────────────────────────────────────────────

    def get_cached_candidates(
        self,
        chain_id: Optional[int] = None,
        max_hf: Optional[float] = None,
        min_profit_usd: Optional[float] = None,
    ) -> List[LiquidationCandidate]:
        """Query the local cache with optional filters."""
        results = list(self._position_cache.values())

        if chain_id is not None:
            results = [c for c in results if c.chain_id == chain_id]

        if max_hf is not None:
            results = [c for c in results if c.health_factor <= max_hf]

        if min_profit_usd is not None:
            results = [
                c for c in results
                if c.estimated_gross_profit_usd >= min_profit_usd
            ]

        results.sort(key=lambda c: c.health_factor)
        return results

    def get_stats(self) -> Dict[str, Any]:
        """Return current indexer statistics."""
        return {
            "total_queries": self.stats.total_queries,
            "queries_failed": self.stats.queries_failed,
            "total_positions_scanned": self.stats.total_positions_scanned,
            "total_candidates_found": self.stats.total_candidates_found,
            "cached_positions": len(self._position_cache),
            "last_scan_ms": self.stats.last_full_scan_duration_ms,
            "positions_by_chain": dict(self.stats.positions_by_chain),
            "candidates_by_chain": dict(self.stats.candidates_by_chain),
        }
