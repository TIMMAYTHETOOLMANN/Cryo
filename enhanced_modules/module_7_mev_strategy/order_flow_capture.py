#!/usr/bin/env python3
"""
enhanced_modules.module_7_mev_strategy.order_flow_capture
==========================================================
Captures MEV from order flow by monitoring pending transactions
that will affect prices, and constructing profitable backrun bundles.

Integrates with MEV-Share (Flashbots), MEV-Blocker, and direct
mempool monitoring to identify:
  - Large DEX swaps that push positions into liquidation
  - Oracle update transactions (Chainlink keepers)
  - Collateral withdraw/deposit transactions
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum
from typing import Any, Dict, List, Optional

from enhanced_modules.common.base import EnhancedModule
from enhanced_modules.common.models import EnrichedPosition

logger = logging.getLogger(__name__)


class OrderFlowType(Enum):
    LARGE_SWAP = "large_swap"
    ORACLE_UPDATE = "oracle_update"
    COLLATERAL_WITHDRAW = "collateral_withdraw"
    LARGE_BORROW = "large_borrow"
    BRIDGE_DEPOSIT = "bridge_deposit"
    LIQUIDATION_ATTEMPT = "liquidation_attempt"


@dataclass
class PendingOrderFlow:
    """A detected pending transaction with MEV potential."""
    tx_hash: str
    flow_type: OrderFlowType
    chain_id: int
    from_address: str
    to_address: str
    value_usd: Decimal
    # Price impact analysis
    affected_asset: str
    expected_price_impact_pct: float
    # Positions that become liquidatable after this TX
    affected_positions: List[str] = field(default_factory=list)
    gas_price_gwei: float = 0.0
    detected_at: float = field(default_factory=time.time)

    @property
    def is_actionable(self) -> bool:
        return len(self.affected_positions) > 0 and abs(self.expected_price_impact_pct) > 0.1


@dataclass
class BackrunOpportunity:
    """A profitable backrun opportunity."""
    trigger_tx: PendingOrderFlow
    target_position: EnrichedPosition
    expected_profit_usd: Decimal
    confidence: float
    time_window_ms: int  # How long before this TX lands
    bundle_priority_gwei: float


# Known DEX router addresses (simplified)
DEX_ROUTERS = {
    "0x68b3465833fb72a70ecdf485e0e4c7bd8665fc45": "uniswap_v3",
    "0xe592427a0aece92de3edee1f18e0157c05861564": "uniswap_v3_old",
    "0xef1c6e67703c7bd7107eed8303fbe6ec2554bf6b": "uniswap_universal",
    "0x1111111254eeb25477b68fb85ed929f73a960582": "1inch_v5",
    "0xdef1c0ded9bec7f1a1670819833240f027b25eff": "0x_exchange",
}

# Chainlink keeper/automation addresses
ORACLE_KEEPERS = {
    "0x02777053d6764996e594c3e88af1d58d5363a2e6": "chainlink_keeper",
    "0x5787befdc0ecd210dfa948264631cd53e68f7802": "chainlink_automation",
}

# Minimum swap value to consider (USD)
MIN_SWAP_VALUE_USD = 50000


class OrderFlowCapture(EnhancedModule):
    """
    Captures MEV from order flow by detecting pending transactions
    and constructing backrun bundles.
    """

    # Price impact thresholds by asset type
    IMPACT_THRESHOLDS = {
        "major": 0.1,     # ETH, BTC — 0.1% impact
        "mid_cap": 0.3,   # LINK, UNI — 0.3%
        "small_cap": 1.0, # Long tail — 1%
    }

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        super().__init__("order_flow_capture", config)
        self._pending_flows: List[PendingOrderFlow] = []
        self._opportunities: List[BackrunOpportunity] = []
        self._flows_detected = 0
        self._backruns_created = 0

    async def _on_start(self) -> None:
        logger.info("[OrderFlow] Monitoring %d DEX routers, %d oracle keepers",
                     len(DEX_ROUTERS), len(ORACLE_KEEPERS))

    async def _on_stop(self) -> None:
        logger.info(
            "[OrderFlow] Flows detected: %d, Backruns created: %d",
            self._flows_detected, self._backruns_created,
        )

    # ── TX Analysis ────────────────────────────────────────────

    def analyze_pending_tx(self, tx: Dict[str, Any]) -> Optional[PendingOrderFlow]:
        """
        Analyze a pending transaction for MEV potential.
        Returns PendingOrderFlow if actionable, None otherwise.
        """
        to_addr = (tx.get("to") or "").lower()
        input_data = tx.get("input", "0x")
        value_wei = int(tx.get("value", "0x0"), 16) if isinstance(tx.get("value"), str) else tx.get("value", 0)
        gas_price = int(tx.get("gasPrice", "0x0"), 16) if isinstance(tx.get("gasPrice"), str) else tx.get("gasPrice", 0)

        flow_type = None
        affected_asset = "unknown"
        price_impact = 0.0
        value_usd = Decimal("0")

        # Check if it's a DEX swap
        if to_addr in DEX_ROUTERS:
            flow_type = OrderFlowType.LARGE_SWAP
            # Parse swap data (simplified — in production: full ABI decode)
            swap_data = self._parse_swap(input_data, value_wei)
            affected_asset = swap_data.get("output_asset", "unknown")
            value_usd = Decimal(str(swap_data.get("value_usd", 0)))
            price_impact = swap_data.get("price_impact", 0.0)

        # Check if it's an oracle update
        elif to_addr in ORACLE_KEEPERS:
            flow_type = OrderFlowType.ORACLE_UPDATE
            affected_asset = self._parse_oracle_update(input_data)
            price_impact = 0.5  # Oracle updates can have significant impact

        # Check if value transfer is significant
        elif value_wei > 10 * 10**18:  # > 10 ETH
            flow_type = OrderFlowType.LARGE_BORROW
            value_usd = Decimal(str(value_wei / 10**18 * 3500))  # Rough ETH price

        if not flow_type:
            return None

        if value_usd < MIN_SWAP_VALUE_USD and flow_type == OrderFlowType.LARGE_SWAP:
            return None

        self._flows_detected += 1
        flow = PendingOrderFlow(
            tx_hash=tx.get("hash", ""),
            flow_type=flow_type,
            chain_id=tx.get("chainId", 1),
            from_address=tx.get("from", ""),
            to_address=to_addr,
            value_usd=value_usd,
            affected_asset=affected_asset,
            expected_price_impact_pct=price_impact,
            gas_price_gwei=gas_price / 1e9 if gas_price > 0 else 0,
        )
        self._pending_flows.append(flow)
        return flow

    # ── Backrun Construction ───────────────────────────────────

    async def find_backrun_opportunities(
        self,
        flow: PendingOrderFlow,
        positions: List[EnrichedPosition],
    ) -> List[BackrunOpportunity]:
        """
        Given a pending order flow, find positions that will become
        liquidatable after the flow executes.
        """
        opportunities = []

        for pos in positions:
            if pos.collateral_asset != flow.affected_asset:
                continue

            # Estimate new health factor after price impact
            impact_factor = 1.0 - flow.expected_price_impact_pct / 100
            new_hf = pos.health_factor * impact_factor

            if new_hf < 1.0 and pos.health_factor >= 1.0:
                # This position will become liquidatable!
                profit = pos.estimated_bonus_usd
                confidence = min(0.9, 0.5 + flow.expected_price_impact_pct * 0.1)

                opp = BackrunOpportunity(
                    trigger_tx=flow,
                    target_position=pos,
                    expected_profit_usd=profit,
                    confidence=confidence,
                    time_window_ms=12000,  # ~1 block
                    bundle_priority_gwei=flow.gas_price_gwei * 1.1,
                )
                opportunities.append(opp)
                flow.affected_positions.append(pos.borrower)
                self._backruns_created += 1

        self.record_success()
        return sorted(opportunities, key=lambda o: o.expected_profit_usd, reverse=True)

    # ── Parsing Helpers ────────────────────────────────────────

    def _parse_swap(self, input_data: str, value_wei: int) -> Dict[str, Any]:
        """Parse swap calldata to extract assets and amounts."""
        # Simplified: in production, decode via ABI
        selector = input_data[:10] if len(input_data) >= 10 else ""

        # Common swap selectors
        swap_selectors = {
            "0x5ae401dc": "multicall",
            "0x3593564c": "execute",  # Universal Router
            "0x38ed1739": "swapExactTokensForTokens",
        }

        swap_type = swap_selectors.get(selector, "unknown_swap")

        # Rough estimate
        value_usd = value_wei / 10**18 * 3500  # Assume ETH value
        # Estimate price impact based on value
        price_impact = min(2.0, value_usd / 5_000_000)  # 1% per $5M

        return {
            "swap_type": swap_type,
            "output_asset": "WETH",  # Simplified
            "value_usd": value_usd,
            "price_impact": price_impact,
        }

    def _parse_oracle_update(self, input_data: str) -> str:
        """Parse oracle update to determine affected asset."""
        # Simplified — in production, map automation ID to feed
        return "WETH"  # Default

    # ── Stats ──────────────────────────────────────────────────

    def get_stats(self) -> Dict[str, Any]:
        return {
            "flows_detected": self._flows_detected,
            "backruns_created": self._backruns_created,
            "pending_flows": len(self._pending_flows),
            "conversion_rate": self._backruns_created / max(1, self._flows_detected),
        }
