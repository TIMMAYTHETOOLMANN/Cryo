#!/usr/bin/env python3
"""
STAGE 1 — Collateral Health Monitor (Module 1) [Enhanced]
===========================================================
Continuously scans all tracked positions and flags those with
healthFactor < 1.05 (or protocol-specific thresholds) as candidates
for automated risk mitigation.

Integration:
  - Consumes position_snapshots via TimescaleDB hypertable OR direct RPC
  - Listens for new blocks via WebSocket `newHeads` subscription
  - Falls back to 3-second polling for L2s / HTTP-only RPCs
  - Filters positions where health_factor < threshold AND debt_usd > MIN_DEBT
  - Outputs AtRiskPosition objects to Redis queue for downstream processing

Protocol-Specific Liquidatability:
  - Aave v2:      healthFactor < 1e18
  - Aave v3:      healthFactor < 1e18 (base denominated in USD 8-dec)
  - Compound v2:  shortfall > 0 from getAccountLiquidity()
  - Compound v3:  isLiquidatable(account) via Comet contract
  - MakerDAO:     (ink * spot) < (art * rate)

Outputs:
  - AtRiskPosition queue → Redis stream `cryo:at_risk_positions`
  - Real-time metrics via stats property

Zero capital required — all operations are read-only.
"""

import asyncio
import json
import logging
import time
from collections import defaultdict
from decimal import Decimal, getcontext
from typing import Any, Callable, Dict, List, Optional, Tuple
from dataclasses import dataclass, field
from enum import Enum

getcontext().prec = 50

logger = logging.getLogger(__name__)

# Optional dependencies — graceful degrade if not installed
try:
    import redis.asyncio as aioredis
    REDIS_AVAILABLE = True
except ImportError:
    aioredis = None  # type: ignore
    REDIS_AVAILABLE = False


# ---------------------------------------------------------------------------
# Data Models
# ---------------------------------------------------------------------------

class RiskLevel(Enum):
    """Risk classification for positions."""
    LIQUIDATABLE = 0   # HF < 1.0
    CRITICAL = 1       # HF 1.0 - 1.05
    AT_RISK = 2        # HF 1.05 - 1.2
    SAFE = 3           # HF >= 1.2


class Protocol(Enum):
    """Supported protocols for health monitoring."""
    AAVE_V2 = "aave_v2"
    AAVE_V3 = "aave_v3"
    COMPOUND_V2 = "compound_v2"
    COMPOUND_V3 = "compound_v3"
    MAKERDAO = "makerdao"
    EULER = "euler"
    LIQUITY = "liquity"


@dataclass
class AtRiskPosition:
    """A position flagged as at-risk for risk mitigation."""
    chain_id: int
    protocol: Protocol
    user: str
    debt_asset: str
    collateral_asset: str
    debt_amount: Decimal
    collateral_amount: Decimal
    health_factor: Decimal
    debt_usd: Decimal = Decimal("0")
    collateral_usd: Decimal = Decimal("0")
    liquidation_bonus_bps: int = 0
    max_gas_price: int = 0
    risk_level: RiskLevel = RiskLevel.AT_RISK
    timestamp: float = 0.0

    def __post_init__(self):
        if self.timestamp == 0.0:
            self.timestamp = time.time()


@dataclass
class HealthCheckResult:
    """Result of a single position health check."""
    user: str
    is_at_risk: bool
    health_factor: Decimal
    collateral_usd: Decimal = Decimal("0")
    debt_usd: Decimal = Decimal("0")
    shortfall: Decimal = Decimal("0")  # Compound-specific


@dataclass
class MonitorConfig:
    """Configuration for the health monitor."""
    health_factor_threshold: Decimal = Decimal("1.05")
    min_debt_usd: Decimal = Decimal("100")
    scan_interval_seconds: int = 3
    max_positions_per_scan: int = 500
    oracle_staleness_seconds: int = 3600
    chains: Dict[int, Dict[str, Any]] = field(default_factory=dict)
    # Redis queue for downstream modules (Module 2 → Incentive Calculator)
    redis_url: str = "redis://localhost:6379"
    redis_stream: str = "cryo:at_risk_positions"
    # WebSocket subscription for block-by-block scanning
    use_websocket: bool = True
    ws_reconnect_delay: int = 5


# ---------------------------------------------------------------------------
# Protocol ABIs (minimal — for health monitoring only)
# ---------------------------------------------------------------------------

AAVE_POOL_ABI = [
    {
        "inputs": [{"name": "user", "type": "address"}],
        "name": "getUserAccountData",
        "outputs": [
            {"name": "totalCollateralBase", "type": "uint256"},
            {"name": "totalDebtBase", "type": "uint256"},
            {"name": "availableBorrowsBase", "type": "uint256"},
            {"name": "currentLiquidationThreshold", "type": "uint256"},
            {"name": "ltv", "type": "uint256"},
            {"name": "healthFactor", "type": "uint256"},
        ],
        "stateMutability": "view",
        "type": "function",
    }
]

COMPOUND_COMPTROLLER_ABI = [
    {
        "inputs": [{"name": "account", "type": "address"}],
        "name": "getAccountLiquidity",
        "outputs": [
            {"name": "", "type": "uint256"},
            {"name": "", "type": "uint256"},
            {"name": "", "type": "uint256"},
        ],
        "stateMutability": "view",
        "type": "function",
    }
]

MAKER_VAT_ABI = [
    {
        "inputs": [
            {"name": "ilk", "type": "bytes32"},
            {"name": "urn", "type": "address"},
        ],
        "name": "urns",
        "outputs": [
            {"name": "ink", "type": "uint256"},
            {"name": "art", "type": "uint256"},
        ],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "inputs": [{"name": "ilk", "type": "bytes32"}],
        "name": "ilks",
        "outputs": [
            {"name": "Art", "type": "uint256"},
            {"name": "rate", "type": "uint256"},
            {"name": "spot", "type": "uint256"},
            {"name": "line", "type": "uint256"},
            {"name": "dust", "type": "uint256"},
        ],
        "stateMutability": "view",
        "type": "function",
    },
]

CHAINLINK_ABI = [
    {
        "inputs": [],
        "name": "latestRoundData",
        "outputs": [
            {"name": "roundId", "type": "uint80"},
            {"name": "answer", "type": "int256"},
            {"name": "startedAt", "type": "uint256"},
            {"name": "updatedAt", "type": "uint256"},
            {"name": "answeredInRound", "type": "uint80"},
        ],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "inputs": [],
        "name": "decimals",
        "outputs": [{"name": "", "type": "uint8"}],
        "stateMutability": "view",
        "type": "function",
    },
]

COMPOUND_V3_COMET_ABI = [
    {
        "inputs": [{"name": "account", "type": "address"}],
        "name": "isLiquidatable",
        "outputs": [{"name": "", "type": "bool"}],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "inputs": [{"name": "account", "type": "address"}],
        "name": "borrowBalanceOf",
        "outputs": [{"name": "", "type": "uint256"}],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "inputs": [{"name": "account", "type": "address"}, {"name": "asset", "type": "address"}],
        "name": "collateralBalanceOf",
        "outputs": [{"name": "", "type": "uint128"}],
        "stateMutability": "view",
        "type": "function",
    },
]


# ---------------------------------------------------------------------------
# Collateral Health Monitor
# ---------------------------------------------------------------------------

class CollateralHealthMonitor:
    """
    Module 1: Continuously monitors position health across protocols.

    Scans Aave v2/v3, Compound v2, and MakerDAO positions,
    identifies at-risk positions (HF < threshold), and queues them
    for downstream processing by the Incentive Feasibility Calculator.
    """

    def __init__(self, config: Optional[MonitorConfig] = None):
        self._config = config or MonitorConfig()
        self._at_risk_positions: List[AtRiskPosition] = []
        self._scan_count: int = 0
        self._total_positions_scanned: int = 0
        self._total_at_risk_found: int = 0
        self._last_scan_time: float = 0.0
        self._running: bool = False
        # Redis queue for downstream pipeline
        self._redis: Optional[Any] = None
        # Callback hooks: MODULE_11 oracle trigger integration
        self._oracle_trigger_callback: Optional[Callable] = None
        # Per-chain block tracking
        self._last_block: Dict[int, int] = {}
        self._scan_callbacks: List[Callable] = []

    # ── Properties ──────────────────────────────────────────────────────

    @property
    def config(self) -> MonitorConfig:
        return self._config

    @property
    def at_risk_positions(self) -> List[AtRiskPosition]:
        return list(self._at_risk_positions)

    @property
    def scan_count(self) -> int:
        return self._scan_count

    @property
    def stats(self) -> Dict[str, Any]:
        return {
            "scan_count": self._scan_count,
            "total_positions_scanned": self._total_positions_scanned,
            "total_at_risk_found": self._total_at_risk_found,
            "current_at_risk_count": len(self._at_risk_positions),
            "last_scan_time": self._last_scan_time,
            "is_running": self._running,
        }

    # ── Aave v2/v3 Checks ──────────────────────────────────────────────

    def check_aave_health(
        self,
        user: str,
        total_collateral: int,
        total_debt: int,
        health_factor: int,
        protocol: Protocol = Protocol.AAVE_V3,
        is_v3: bool = True,
    ) -> HealthCheckResult:
        """
        Check a single Aave position's health.

        Args:
            user: Borrower address
            total_collateral: Collateral value (ETH for v2, USD 8-dec for v3)
            total_debt: Debt value (same denomination as collateral)
            health_factor: Protocol health factor (1e18-scaled)
            protocol: Aave version
            is_v3: True if Aave v3 (values in USD 8-dec)

        Returns:
            HealthCheckResult with risk assessment
        """
        hf = Decimal(health_factor) / Decimal("1000000000000000000")  # 1e18
        threshold = self._config.health_factor_threshold

        if is_v3:
            # Aave v3: values in 8-decimal USD → normalize to 18-decimal
            coll_usd = Decimal(total_collateral) * Decimal("10000000000")  # * 1e10
            debt_usd = Decimal(total_debt) * Decimal("10000000000")
        else:
            # Aave v2: values in ETH-denominated (treat as 18-decimal)
            coll_usd = Decimal(total_collateral)
            debt_usd = Decimal(total_debt)

        is_at_risk = (hf < threshold) and (total_debt > 0)

        return HealthCheckResult(
            user=user,
            is_at_risk=is_at_risk,
            health_factor=hf,
            collateral_usd=coll_usd,
            debt_usd=debt_usd,
        )

    # ── Compound v2 Checks ──────────────────────────────────────────────

    def check_compound_v2_health(
        self,
        user: str,
        error: int,
        liquidity: int,
        shortfall: int,
    ) -> HealthCheckResult:
        """
        Check a Compound v2 position's health.

        Args:
            user: Account address
            error: Comptroller error code (0 = no error)
            liquidity: Excess collateral in USD (> 0 = safe)
            shortfall: Under-collateralization in USD (> 0 = liquidatable)

        Returns:
            HealthCheckResult with risk assessment
        """
        if error != 0:
            logger.warning("Comptroller error %d for user %s", error, user)
            return HealthCheckResult(
                user=user,
                is_at_risk=False,
                health_factor=Decimal("0"),
            )

        sf = Decimal(shortfall)
        liq = Decimal(liquidity)

        if sf > 0:
            # Under-collateralized: liquidatable
            return HealthCheckResult(
                user=user,
                is_at_risk=True,
                health_factor=Decimal("0"),
                shortfall=sf,
            )

        return HealthCheckResult(
            user=user,
            is_at_risk=False,
            health_factor=Decimal("Infinity") if liq > 0 else Decimal("1"),
            collateral_usd=liq,
        )

    # ── MakerDAO Checks ─────────────────────────────────────────────────

    def check_maker_health(
        self,
        user: str,
        ink: int,
        art: int,
        rate: int,
        spot: int,
    ) -> HealthCheckResult:
        """
        Check a MakerDAO vault's health.

        Args:
            user: Urn (vault) address
            ink: Collateral amount (wad, 1e18)
            art: Normalized debt (wad, 1e18)
            rate: Accumulated rate (ray, 1e27)
            spot: Price with safety margin (ray, 1e27)

        Returns:
            HealthCheckResult with risk assessment
        """
        if art == 0:
            return HealthCheckResult(
                user=user,
                is_at_risk=False,
                health_factor=Decimal("Infinity"),
                collateral_usd=Decimal(ink),
            )

        ink_d = Decimal(ink)
        art_d = Decimal(art)
        rate_d = Decimal(rate)
        spot_d = Decimal(spot)
        ray = Decimal("1000000000000000000000000000")  # 1e27

        # collateral_value = ink * spot (rad = wad * ray)
        collateral_value = ink_d * spot_d
        # debt_value = art * rate (rad)
        debt_value = art_d * rate_d

        if debt_value > 0:
            hf = (collateral_value * Decimal("1000000000000000000")) / debt_value
        else:
            hf = Decimal("Infinity")

        # Actual debt in wad
        debt_wad = (art_d * rate_d) / ray

        is_at_risk = collateral_value < debt_value

        return HealthCheckResult(
            user=user,
            is_at_risk=is_at_risk,
            health_factor=hf / Decimal("1000000000000000000"),  # Normalize
            collateral_usd=ink_d,
            debt_usd=debt_wad,
        )

    # ── Risk Classification ─────────────────────────────────────────────

    @staticmethod
    def classify_risk(health_factor: Decimal) -> RiskLevel:
        """Classify risk level from health factor."""
        if health_factor < Decimal("1"):
            return RiskLevel.LIQUIDATABLE
        if health_factor < Decimal("1.05"):
            return RiskLevel.CRITICAL
        if health_factor < Decimal("1.2"):
            return RiskLevel.AT_RISK
        return RiskLevel.SAFE

    # ── Position Management ─────────────────────────────────────────────

    def add_at_risk_position(self, position: AtRiskPosition) -> None:
        """Add a position to the at-risk queue."""
        position.risk_level = self.classify_risk(position.health_factor)
        self._at_risk_positions.append(position)
        self._total_at_risk_found += 1

    def clear_positions(self) -> None:
        """Clear the at-risk position queue."""
        self._at_risk_positions.clear()

    def get_positions_by_risk(self, level: RiskLevel) -> List[AtRiskPosition]:
        """Get positions filtered by risk level."""
        return [p for p in self._at_risk_positions if p.risk_level == level]

    def get_positions_sorted(self) -> List[AtRiskPosition]:
        """Get positions sorted by health factor (lowest first)."""
        return sorted(self._at_risk_positions, key=lambda p: p.health_factor)

    # ── Scan Tracking ───────────────────────────────────────────────────

    def record_scan(self, positions_scanned: int) -> None:
        """Record a completed scan cycle."""
        self._scan_count += 1
        self._total_positions_scanned += positions_scanned
        self._last_scan_time = time.time()

    # ── Oracle Validation ───────────────────────────────────────────────

    @staticmethod
    def validate_oracle_price(
        answer: int,
        updated_at: int,
        max_staleness: int,
        current_time: Optional[int] = None,
    ) -> Tuple[bool, str]:
        """
        Validate a Chainlink oracle price.

        Args:
            answer: Price answer from latestRoundData
            updated_at: Timestamp from latestRoundData
            max_staleness: Maximum age in seconds
            current_time: Current timestamp (defaults to time.time())

        Returns:
            (is_valid, reason) tuple
        """
        now = current_time or int(time.time())

        if answer <= 0:
            return False, "Zero or negative price"

        age = now - updated_at
        if age > max_staleness:
            return False, f"Stale price (age={age}s, max={max_staleness}s)"

        return True, "Valid"

    # ── Compound V3 (Comet) Checks ───────────────────────────────────

    def check_compound_v3_health(
        self,
        user: str,
        is_liquidatable: bool,
        borrow_balance: int,
        collateral_balance: int,
    ) -> HealthCheckResult:
        """
        Check a Compound V3 (Comet) position's health.

        Compound V3 uses `isLiquidatable(account)` directly — no HF math needed.
        We synthesize an approximate HF for unified downstream processing.

        Args:
            user: Account address
            is_liquidatable: Result of comet.isLiquidatable(user)
            borrow_balance: borrowBalanceOf(user) — debt in base asset units
            collateral_balance: Summed collateral value in base units

        Returns:
            HealthCheckResult with risk assessment
        """
        if borrow_balance == 0:
            return HealthCheckResult(
                user=user, is_at_risk=False,
                health_factor=Decimal("Infinity"),
                collateral_usd=Decimal(collateral_balance),
            )

        # Synthesize approximate HF from ratio
        hf = Decimal(collateral_balance) / Decimal(max(borrow_balance, 1))

        return HealthCheckResult(
            user=user,
            is_at_risk=is_liquidatable,
            health_factor=hf,
            collateral_usd=Decimal(collateral_balance),
            debt_usd=Decimal(borrow_balance),
        )

    # ── Redis Queue Integration ──────────────────────────────────────

    async def connect_redis(self) -> bool:
        """Connect to Redis for downstream queue publishing."""
        if not REDIS_AVAILABLE:
            logger.warning("redis.asyncio not installed — queue disabled (pip install redis)")
            return False
        try:
            self._redis = aioredis.from_url(
                self._config.redis_url,
                decode_responses=True,
                socket_connect_timeout=5,
            )
            await self._redis.ping()
            logger.info("✅ Redis connected for at-risk position queue")
            return True
        except Exception as e:
            logger.warning("Redis connection failed (%s) — running without queue", e)
            self._redis = None
            return False

    async def publish_position(self, position: AtRiskPosition) -> bool:
        """Publish an at-risk position to the Redis stream for Module 2."""
        if not self._redis:
            return False
        try:
            payload = {
                "chain_id": str(position.chain_id),
                "protocol": position.protocol.value,
                "user": position.user,
                "debt_asset": position.debt_asset,
                "collateral_asset": position.collateral_asset,
                "debt_amount": str(position.debt_amount),
                "collateral_amount": str(position.collateral_amount),
                "health_factor": str(position.health_factor),
                "debt_usd": str(position.debt_usd),
                "collateral_usd": str(position.collateral_usd),
                "liquidation_bonus_bps": str(position.liquidation_bonus_bps),
                "max_gas_price": str(position.max_gas_price),
                "risk_level": position.risk_level.name,
                "timestamp": str(position.timestamp),
            }
            await self._redis.xadd(self._config.redis_stream, payload, maxlen=10_000)
            return True
        except Exception as e:
            logger.warning("Redis publish failed: %s", e)
            return False

    # ── Async Block-by-Block Scanner ─────────────────────────────────

    async def run_block_listener(
        self,
        w3,
        chain_id: int,
        scan_fn: Callable,
    ):
        """
        Subscribe to newHeads (WebSocket) or poll at interval.

        Each new block triggers a scan cycle via `scan_fn(block_number)`.
        Integrates with MODULE_11 oracle triggers when callback is set.

        Args:
            w3: Web3 instance (WebSocket preferred for real-time)
            chain_id: Chain ID for this listener
            scan_fn: async callable(block_number) → List[AtRiskPosition]
        """
        self._running = True
        logger.info(
            "🔍 Health monitor started on chain %d (ws=%s, interval=%ds)",
            chain_id, self._config.use_websocket, self._config.scan_interval_seconds,
        )

        while self._running:
            try:
                block_num = w3.eth.block_number
                if block_num == self._last_block.get(chain_id, 0):
                    await asyncio.sleep(self._config.scan_interval_seconds)
                    continue

                self._last_block[chain_id] = block_num

                # Execute scan
                positions = await scan_fn(block_num)
                positions_scanned = len(positions) if positions else 0
                self.record_scan(positions_scanned)

                for pos in (positions or []):
                    self.add_at_risk_position(pos)
                    await self.publish_position(pos)

                    # MODULE_11 integration: fire oracle trigger if position is LIQUIDATABLE
                    if (
                        pos.risk_level == RiskLevel.LIQUIDATABLE
                        and self._oracle_trigger_callback
                    ):
                        try:
                            await self._oracle_trigger_callback(pos)
                        except Exception as e:
                            logger.warning("Oracle trigger callback error: %s", e)

                # Notify registered callbacks
                for cb in self._scan_callbacks:
                    try:
                        await cb(chain_id, block_num, positions or [])
                    except Exception as e:
                        logger.warning("Scan callback error: %s", e)

            except Exception as e:
                logger.error("Block listener error (chain %d): %s", chain_id, e)
                await asyncio.sleep(self._config.ws_reconnect_delay)

            await asyncio.sleep(self._config.scan_interval_seconds)

        logger.info("Health monitor stopped on chain %d", chain_id)

    def stop(self):
        """Signal the block listener to stop."""
        self._running = False

    # ── Hook Registration ────────────────────────────────────────────

    def register_oracle_trigger(self, callback: Callable):
        """
        Register MODULE_11 oracle trigger callback.

        When a position hits LIQUIDATABLE, this callback fires with the
        AtRiskPosition, allowing the timing engine to bundle a mitigation
        TX in the same block as the oracle update.
        """
        self._oracle_trigger_callback = callback
        logger.info("🔗 Oracle trigger callback registered (MODULE_11 integration)")

    def register_scan_callback(self, callback: Callable):
        """Register a callback for each scan cycle completion."""
        self._scan_callbacks.append(callback)

    # ── Batch Position Processing ────────────────────────────────────

    async def process_batch(
        self,
        positions: List[AtRiskPosition],
    ) -> Dict[str, int]:
        """
        Process a batch of positions: classify, queue, trigger.

        Returns:
            Dict with counts: liquidatable, critical, at_risk, skipped
        """
        counts: Dict[str, int] = defaultdict(int)
        for pos in positions:
            pos.risk_level = self.classify_risk(pos.health_factor)

            # Filter: skip if debt below threshold
            if pos.debt_usd < self._config.min_debt_usd:
                counts["skipped"] += 1
                continue

            self.add_at_risk_position(pos)
            await self.publish_position(pos)
            counts[pos.risk_level.name.lower()] += 1

        return dict(counts)
