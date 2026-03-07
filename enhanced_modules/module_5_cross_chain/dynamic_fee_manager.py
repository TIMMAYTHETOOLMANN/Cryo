#!/usr/bin/env python3
"""
enhanced_modules.module_5_cross_chain.dynamic_fee_manager
==========================================================
Real-time cross-chain gas price tracking and dynamic fee management.

Maintains a registry of current gas prices on each chain and adjusts
execution parameters in real-time. If a chain's gas price spikes
above expected profit, temporarily disables liquidations there.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Dict, List, Optional, Set

from enhanced_modules.common.base import EnhancedModule

logger = logging.getLogger(__name__)


@dataclass
class ChainGasState:
    """Real-time gas state for a chain."""
    chain_id: int
    name: str
    current_gas_gwei: float = 0.0
    avg_gas_gwei_1h: float = 0.0
    avg_gas_gwei_24h: float = 0.0
    gas_token_price_usd: float = 0.0
    is_enabled: bool = True
    disabled_reason: Optional[str] = None
    disabled_at: Optional[float] = None
    last_updated: float = field(default_factory=time.time)
    # History for trend analysis
    gas_history: List[float] = field(default_factory=list)

    @property
    def cost_per_100k_gas_usd(self) -> float:
        """Cost to execute 100k gas units in USD."""
        return self.current_gas_gwei * 1e-9 * 100_000 * self.gas_token_price_usd

    @property
    def is_spike(self) -> bool:
        """True if current gas is > 2x the 1h average."""
        return (
            self.avg_gas_gwei_1h > 0
            and self.current_gas_gwei > self.avg_gas_gwei_1h * 2
        )


@dataclass
class FeeDecision:
    """Decision on whether to execute on a chain."""
    chain_id: int
    should_execute: bool
    max_gas_price_gwei: float
    estimated_cost_usd: Decimal
    reason: str


class DynamicFeeManager(EnhancedModule):
    """
    Manages cross-chain gas prices and dynamically enables/disables
    chains based on profitability thresholds.
    """

    # Auto-disable threshold: gas cost > X% of expected profit
    GAS_COST_THRESHOLD_PCT = 60.0
    # Auto-re-enable when gas drops below this % of threshold
    RE_ENABLE_PCT = 40.0
    # Maximum gas history entries per chain
    MAX_GAS_HISTORY = 300

    # Default gas token prices (USD)
    GAS_TOKEN_PRICES = {
        1: ("ETH", 3500.0),
        10: ("ETH", 3500.0),
        42161: ("ETH", 3500.0),
        8453: ("ETH", 3500.0),
        137: ("MATIC", 0.50),
        56: ("BNB", 600.0),
        43114: ("AVAX", 35.0),
        324: ("ETH", 3500.0),
        534352: ("ETH", 3500.0),
    }

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        super().__init__("dynamic_fee_manager", config)
        self._chains: Dict[int, ChainGasState] = {}
        self._disabled_chains: Set[int] = set()

    async def _on_start(self) -> None:
        # Initialize chain states
        chain_names = {
            1: "Ethereum", 10: "Optimism", 42161: "Arbitrum",
            8453: "Base", 137: "Polygon", 56: "BSC",
            43114: "Avalanche", 324: "zkSync", 534352: "Scroll",
        }
        for chain_id, name in chain_names.items():
            token_name, token_price = self.GAS_TOKEN_PRICES.get(chain_id, ("ETH", 3500.0))
            self._chains[chain_id] = ChainGasState(
                chain_id=chain_id,
                name=name,
                gas_token_price_usd=token_price,
            )
        logger.info("[FeeManager] Tracking gas on %d chains", len(self._chains))

    async def _on_stop(self) -> None:
        logger.info(
            "[FeeManager] Disabled chains at shutdown: %s",
            self._disabled_chains or "none",
        )

    # ── Gas Price Updates ──────────────────────────────────────

    def update_gas_price(self, chain_id: int, gas_gwei: float) -> None:
        """Update the current gas price for a chain."""
        state = self._chains.get(chain_id)
        if not state:
            return

        state.current_gas_gwei = gas_gwei
        state.gas_history.append(gas_gwei)
        if len(state.gas_history) > self.MAX_GAS_HISTORY:
            state.gas_history = state.gas_history[-self.MAX_GAS_HISTORY:]

        # Update rolling averages
        recent_60 = state.gas_history[-60:]  # ~1 hour at 1 update/min
        state.avg_gas_gwei_1h = sum(recent_60) / len(recent_60)
        state.avg_gas_gwei_24h = sum(state.gas_history) / len(state.gas_history)
        state.last_updated = time.time()

    def update_gas_token_price(self, chain_id: int, price_usd: float) -> None:
        """Update the gas token price for a chain."""
        state = self._chains.get(chain_id)
        if state:
            state.gas_token_price_usd = price_usd

    # ── Decision Making ────────────────────────────────────────

    def should_execute(
        self,
        chain_id: int,
        expected_profit_usd: Decimal,
        gas_estimate: int = 350000,
    ) -> FeeDecision:
        """Decide whether to execute on a chain given current gas."""
        state = self._chains.get(chain_id)
        if not state:
            return FeeDecision(
                chain_id=chain_id,
                should_execute=False,
                max_gas_price_gwei=0,
                estimated_cost_usd=Decimal("0"),
                reason="Unknown chain",
            )

        if not state.is_enabled:
            return FeeDecision(
                chain_id=chain_id,
                should_execute=False,
                max_gas_price_gwei=0,
                estimated_cost_usd=Decimal("0"),
                reason=f"Chain disabled: {state.disabled_reason}",
            )

        # Calculate gas cost
        gas_cost_eth = state.current_gas_gwei * 1e-9 * gas_estimate
        gas_cost_usd = Decimal(str(round(gas_cost_eth * state.gas_token_price_usd, 4)))

        # Check threshold
        if expected_profit_usd > 0:
            cost_pct = float(gas_cost_usd / expected_profit_usd * 100)
        else:
            cost_pct = 100.0

        should_exec = cost_pct < self.GAS_COST_THRESHOLD_PCT

        # Calculate max acceptable gas price
        if expected_profit_usd > 0 and gas_estimate > 0:
            max_cost_usd = float(expected_profit_usd) * self.GAS_COST_THRESHOLD_PCT / 100
            max_gas_eth = max_cost_usd / max(state.gas_token_price_usd, 1)
            max_gas_gwei = max_gas_eth / gas_estimate * 1e9
        else:
            max_gas_gwei = 0.0

        reason = (
            f"Gas cost ${gas_cost_usd} = {cost_pct:.1f}% of profit "
            f"(threshold: {self.GAS_COST_THRESHOLD_PCT}%)"
        )

        return FeeDecision(
            chain_id=chain_id,
            should_execute=should_exec,
            max_gas_price_gwei=round(max_gas_gwei, 2),
            estimated_cost_usd=gas_cost_usd,
            reason=reason,
        )

    # ── Chain Enable/Disable ───────────────────────────────────

    def disable_chain(self, chain_id: int, reason: str) -> None:
        """Temporarily disable a chain."""
        state = self._chains.get(chain_id)
        if state:
            state.is_enabled = False
            state.disabled_reason = reason
            state.disabled_at = time.time()
            self._disabled_chains.add(chain_id)
            logger.warning("[FeeManager] DISABLED chain %d (%s): %s",
                           chain_id, state.name, reason)

    def enable_chain(self, chain_id: int) -> None:
        """Re-enable a chain."""
        state = self._chains.get(chain_id)
        if state:
            state.is_enabled = True
            state.disabled_reason = None
            state.disabled_at = None
            self._disabled_chains.discard(chain_id)
            logger.info("[FeeManager] RE-ENABLED chain %d (%s)", chain_id, state.name)

    def auto_manage_chains(self) -> List[int]:
        """
        Automatically disable/enable chains based on gas conditions.
        Returns list of chain IDs that changed state.
        """
        changed = []
        for chain_id, state in self._chains.items():
            if state.is_spike and state.is_enabled:
                self.disable_chain(chain_id, f"Gas spike: {state.current_gas_gwei:.0f} gwei")
                changed.append(chain_id)
            elif not state.is_spike and not state.is_enabled and state.disabled_reason and "spike" in state.disabled_reason:
                self.enable_chain(chain_id)
                changed.append(chain_id)
        return changed

    # ── Status ─────────────────────────────────────────────────

    def get_all_chain_status(self) -> List[Dict[str, Any]]:
        return [
            {
                "chain_id": s.chain_id,
                "name": s.name,
                "gas_gwei": round(s.current_gas_gwei, 2),
                "avg_1h": round(s.avg_gas_gwei_1h, 2),
                "cost_per_100k_usd": round(s.cost_per_100k_gas_usd, 4),
                "enabled": s.is_enabled,
                "spike": s.is_spike,
            }
            for s in self._chains.values()
        ]
