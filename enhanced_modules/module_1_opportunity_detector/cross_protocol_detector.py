#!/usr/bin/env python3
"""
enhanced_modules.module_1_opportunity_detector.cross_protocol_detector
======================================================================
Detects **cascading liquidation risk** by correlating a single user's
positions across multiple lending protocols (Aave V3, Compound V3,
MakerDAO, Morpho, Euler, etc.).

If a user has deposited collateral on Protocol A and used the borrowed
asset as collateral on Protocol B, a price drop can trigger cascading
liquidations across both protocols — yielding multiple bonuses in a
single block.

Flow:
  1. Build an address → protocol → position graph.
  2. Detect shared-collateral chains (A deposits → borrows → B deposits).
  3. Compute systemic exposure score.
  4. Flag any position in the chain if total exposure exceeds threshold.
"""
from __future__ import annotations

import asyncio
import logging
import time
from collections import defaultdict
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Dict, List, Optional, Set, Tuple

from enhanced_modules.common.base import EnhancedModule
from enhanced_modules.common.models import EnrichedPosition

logger = logging.getLogger(__name__)


# ── Data Models ────────────────────────────────────────────────────

@dataclass
class ProtocolPosition:
    """A single position on a single protocol."""
    protocol: str
    chain_id: int
    debt_asset: str
    debt_usd: Decimal
    collateral_asset: str
    collateral_usd: Decimal
    health_factor: float
    is_active: bool = True


@dataclass
class UserExposureGraph:
    """Full exposure graph for a single address."""
    address: str
    positions: List[ProtocolPosition] = field(default_factory=list)
    total_debt_usd: Decimal = Decimal("0")
    total_collateral_usd: Decimal = Decimal("0")
    systemic_risk_score: float = 0.0
    cascading_chains: List[List[str]] = field(default_factory=list)
    lowest_health_factor: float = 999.0
    protocols_involved: Set[str] = field(default_factory=set)
    last_updated: float = field(default_factory=time.time)

    @property
    def protocol_count(self) -> int:
        return len(self.protocols_involved)

    @property
    def is_multi_protocol(self) -> bool:
        return self.protocol_count > 1


@dataclass
class CascadingLiquidationOpportunity:
    """An identified cascading liquidation opportunity."""
    address: str
    chain_id: int
    positions: List[ProtocolPosition]
    cascade_order: List[str]  # protocol names in liquidation order
    total_bonus_usd: Decimal
    systemic_risk_score: float
    estimated_gas_usd: Decimal
    net_profit_usd: Decimal
    confidence: float


# ── Protocol Adapters (address → positions) ────────────────────────

SUPPORTED_PROTOCOLS = [
    "aave_v3",
    "compound_v3",
    "maker",
    "morpho",
    "euler",
    "radiant",
    "silo",
    "benqi",
    "venus",
    "fraxlend",
]

# Liquidation bonus percentages by protocol (approximate)
LIQUIDATION_BONUS = {
    "aave_v3": 0.05,
    "compound_v3": 0.08,
    "maker": 0.13,
    "morpho": 0.05,
    "euler": 0.10,
    "radiant": 0.05,
    "silo": 0.05,
    "benqi": 0.08,
    "venus": 0.10,
    "fraxlend": 0.05,
}


class CrossProtocolDetector(EnhancedModule):
    """
    Scans for users with overlapping positions across multiple
    lending protocols to identify cascading liquidation opportunities.
    """

    # Systemic risk threshold — flag users above this
    SYSTEMIC_RISK_THRESHOLD = 0.65
    # Maximum number of addresses to track concurrently
    MAX_TRACKED_ADDRESSES = 10000
    # Cache TTL for exposure graphs (seconds)
    EXPOSURE_CACHE_TTL = 120.0

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        super().__init__("cross_protocol_detector", config)
        self._exposure_cache: Dict[str, UserExposureGraph] = {}
        self._known_addresses: Set[str] = set()
        self._opportunities: List[CascadingLiquidationOpportunity] = []
        self._protocol_positions: Dict[str, Dict[str, List[ProtocolPosition]]] = (
            defaultdict(lambda: defaultdict(list))
        )  # protocol -> address -> positions

    async def _on_start(self) -> None:
        logger.info("[CrossProtocol] Initializing cross-protocol detector")
        logger.info("[CrossProtocol] Tracking %d protocols", len(SUPPORTED_PROTOCOLS))

    async def _on_stop(self) -> None:
        self._exposure_cache.clear()
        self._opportunities.clear()

    # ── Ingestion ──────────────────────────────────────────────

    def ingest_position(
        self,
        address: str,
        protocol: str,
        chain_id: int,
        debt_asset: str,
        debt_usd: Decimal,
        collateral_asset: str,
        collateral_usd: Decimal,
        health_factor: float,
    ) -> None:
        """Ingest a single user position from any protocol."""
        pos = ProtocolPosition(
            protocol=protocol,
            chain_id=chain_id,
            debt_asset=debt_asset,
            debt_usd=debt_usd,
            collateral_asset=collateral_asset,
            collateral_usd=collateral_usd,
            health_factor=health_factor,
        )
        addr = address.lower()
        self._protocol_positions[protocol][addr].append(pos)
        self._known_addresses.add(addr)

        # Evict oldest if too many
        if len(self._known_addresses) > self.MAX_TRACKED_ADDRESSES:
            oldest = next(iter(self._known_addresses))
            self._evict_address(oldest)

    def ingest_enriched_positions(
        self, positions: List[EnrichedPosition]
    ) -> int:
        """Batch-ingest enriched positions. Returns count ingested."""
        count = 0
        for p in positions:
            self.ingest_position(
                address=p.borrower,
                protocol=p.protocol,
                chain_id=p.chain_id,
                debt_asset=p.debt_asset,
                debt_usd=p.debt_usd,
                collateral_asset=p.collateral_asset,
                collateral_usd=p.collateral_usd,
                health_factor=p.health_factor,
            )
            count += 1
        return count

    # ── Analysis ───────────────────────────────────────────────

    async def build_exposure_graph(self, address: str) -> UserExposureGraph:
        """Build the full cross-protocol exposure graph for one address."""
        addr = address.lower()
        now = time.time()

        # Check cache
        if addr in self._exposure_cache:
            cached = self._exposure_cache[addr]
            if now - cached.last_updated < self.EXPOSURE_CACHE_TTL:
                return cached

        # Gather positions from all protocols
        all_positions: List[ProtocolPosition] = []
        protocols_involved: Set[str] = set()

        for protocol in SUPPORTED_PROTOCOLS:
            positions = self._protocol_positions.get(protocol, {}).get(addr, [])
            for pos in positions:
                if pos.is_active:
                    all_positions.append(pos)
                    protocols_involved.add(protocol)

        # Compute totals
        total_debt = sum(p.debt_usd for p in all_positions)
        total_collateral = sum(p.collateral_usd for p in all_positions)
        lowest_hf = min((p.health_factor for p in all_positions), default=999.0)

        # Detect cascading chains (collateral overlap)
        cascading_chains = self._detect_cascading_chains(all_positions)

        # Compute systemic risk score
        risk_score = self._compute_systemic_risk(
            all_positions, cascading_chains, total_debt, total_collateral
        )

        graph = UserExposureGraph(
            address=addr,
            positions=all_positions,
            total_debt_usd=total_debt,
            total_collateral_usd=total_collateral,
            systemic_risk_score=risk_score,
            cascading_chains=cascading_chains,
            lowest_health_factor=lowest_hf,
            protocols_involved=protocols_involved,
        )

        self._exposure_cache[addr] = graph
        return graph

    async def scan_all_addresses(self) -> List[CascadingLiquidationOpportunity]:
        """Scan all known addresses for cascading liquidation opportunities."""
        opportunities: List[CascadingLiquidationOpportunity] = []

        for addr in list(self._known_addresses):
            try:
                graph = await self.build_exposure_graph(addr)

                # Only interested in multi-protocol users above risk threshold
                if not graph.is_multi_protocol:
                    continue
                if graph.systemic_risk_score < self.SYSTEMIC_RISK_THRESHOLD:
                    continue

                opp = self._build_opportunity(graph)
                if opp and opp.net_profit_usd > 0:
                    opportunities.append(opp)

            except Exception as exc:
                self.record_failure(str(exc))

        # Sort by net profit descending
        opportunities.sort(key=lambda o: o.net_profit_usd, reverse=True)
        self._opportunities = opportunities
        self.record_success()
        return opportunities

    # ── Cascading Chain Detection ──────────────────────────────

    def _detect_cascading_chains(
        self, positions: List[ProtocolPosition]
    ) -> List[List[str]]:
        """
        Detect chains where borrowed asset from Protocol A is used as
        collateral on Protocol B.
        """
        chains: List[List[str]] = []

        # Build a mapping: borrowed_asset → protocol
        borrowers: Dict[str, List[str]] = defaultdict(list)
        # Build a mapping: collateral_asset → protocol
        depositors: Dict[str, List[str]] = defaultdict(list)

        for pos in positions:
            borrowers[pos.debt_asset].append(pos.protocol)
            depositors[pos.collateral_asset].append(pos.protocol)

        # Find overlaps: asset borrowed from A, deposited into B
        for asset in borrowers:
            if asset in depositors:
                for borrow_proto in borrowers[asset]:
                    for deposit_proto in depositors[asset]:
                        if borrow_proto != deposit_proto:
                            chains.append([borrow_proto, deposit_proto])

        return chains

    def _compute_systemic_risk(
        self,
        positions: List[ProtocolPosition],
        cascading_chains: List[List[str]],
        total_debt: Decimal,
        total_collateral: Decimal,
    ) -> float:
        """
        Compute a [0, 1] systemic risk score.

        Factors:
          - Number of cascading chains (more = higher risk)
          - Lowest health factor across all positions
          - Leverage ratio (total debt / total collateral)
          - Number of protocols involved
        """
        if not positions:
            return 0.0

        # Base: leverage ratio
        leverage = float(total_debt / total_collateral) if total_collateral > 0 else 10.0
        leverage_score = min(1.0, leverage / 2.0)  # 1.0 at 200% leverage

        # Health factor penalty
        lowest_hf = min(p.health_factor for p in positions)
        if lowest_hf < 1.0:
            hf_score = 1.0
        elif lowest_hf < 1.05:
            hf_score = 0.8
        elif lowest_hf < 1.15:
            hf_score = 0.5
        else:
            hf_score = 0.2

        # Cascading chain bonus
        chain_bonus = min(0.3, len(cascading_chains) * 0.1)

        # Protocol diversity bonus
        protocols = set()
        for p in positions:
            protocols.add(p.protocol)
        diversity_bonus = min(0.2, (len(protocols) - 1) * 0.1)

        score = (leverage_score * 0.35 + hf_score * 0.35 +
                 chain_bonus + diversity_bonus)
        return min(1.0, score)

    def _build_opportunity(
        self, graph: UserExposureGraph
    ) -> Optional[CascadingLiquidationOpportunity]:
        """Build a cascading liquidation opportunity from an exposure graph."""
        if not graph.positions:
            return None

        # Determine cascade order (liquidate lowest HF first)
        sorted_positions = sorted(graph.positions, key=lambda p: p.health_factor)
        cascade_order = [p.protocol for p in sorted_positions]

        # Estimate total bonus
        total_bonus = Decimal("0")
        for pos in sorted_positions:
            bonus_pct = LIQUIDATION_BONUS.get(pos.protocol, 0.05)
            bonus = pos.collateral_usd * Decimal(str(bonus_pct))
            total_bonus += bonus

        # Rough gas estimate ($15 per liquidation on mainnet)
        est_gas = Decimal("15") * len(sorted_positions)

        # Pick chain from first position
        chain_id = sorted_positions[0].chain_id if sorted_positions else 1

        net = total_bonus - est_gas

        return CascadingLiquidationOpportunity(
            address=graph.address,
            chain_id=chain_id,
            positions=sorted_positions,
            cascade_order=cascade_order,
            total_bonus_usd=total_bonus,
            systemic_risk_score=graph.systemic_risk_score,
            estimated_gas_usd=est_gas,
            net_profit_usd=max(Decimal("0"), net),
            confidence=min(0.90, graph.systemic_risk_score),
        )

    # ── Helpers ────────────────────────────────────────────────

    def _evict_address(self, address: str) -> None:
        """Remove an address from all tracking structures."""
        self._known_addresses.discard(address)
        self._exposure_cache.pop(address, None)
        for protocol in SUPPORTED_PROTOCOLS:
            self._protocol_positions.get(protocol, {}).pop(address, None)
