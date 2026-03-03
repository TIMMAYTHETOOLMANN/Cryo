#!/usr/bin/env python3
"""
STAGE 1 — Collateral Health Monitor (Module 1)
=================================================
Continuously scans all tracked positions and flags those with
healthFactor < 1.05 (or protocol-specific thresholds) as candidates
for automated risk mitigation.

Integration:
  - Consumes position data via Aave/Compound/MakerDAO on-chain calls
  - Queries every new block (WebSocket newHeads) or at configurable intervals
  - Filters positions where health_factor < threshold AND debt > MIN_DEBT
  - Outputs AtRiskPosition objects to a queue for downstream processing

Protocol-Specific Liquidatability:
  - Aave v2/v3: healthFactor < 1e18
  - Compound v2: shortfall > 0 from getAccountLiquidity()
  - MakerDAO: (ink * spot) < (art * rate)

Zero capital required — all operations are read-only.
"""

import asyncio
import logging
import time
from collections import defaultdict
from decimal import Decimal, getcontext
from typing import Any, Dict, List, Optional, Tuple
from dataclasses import dataclass, field
from enum import Enum

getcontext().prec = 50

logger = logging.getLogger(__name__)


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
