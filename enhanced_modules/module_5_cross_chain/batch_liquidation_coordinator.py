#!/usr/bin/env python3
"""
enhanced_modules.module_5_cross_chain.batch_liquidation_coordinator
====================================================================
Batches multiple same-chain liquidations into a single multicall TX
and coordinates cross-chain batches for amortized gas + bridge fees.

When multiple positions on the same chain become liquidatable within
a short window, they're bundled into one flash loan (borrowing the
sum of all debts) and executed via multicall.
"""
from __future__ import annotations

import asyncio
import logging
import time
from collections import defaultdict
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Dict, List, Optional

from enhanced_modules.common.base import EnhancedModule
from enhanced_modules.common.models import EnrichedPosition

logger = logging.getLogger(__name__)


@dataclass
class LiquidationBatch:
    """A batch of liquidations targeting the same chain."""
    batch_id: str
    chain_id: int
    positions: List[EnrichedPosition]
    total_debt_usd: Decimal
    total_bonus_usd: Decimal
    estimated_gas_usd: Decimal
    flash_loan_amount_usd: Decimal
    created_at: float = field(default_factory=time.time)

    @property
    def count(self) -> int:
        return len(self.positions)

    @property
    def estimated_net_profit_usd(self) -> Decimal:
        return self.total_bonus_usd - self.estimated_gas_usd

    @property
    def gas_per_liquidation_usd(self) -> Decimal:
        if self.count == 0:
            return Decimal("0")
        return self.estimated_gas_usd / self.count


@dataclass
class BatchCoordinatorStats:
    """Statistics for the batch coordinator."""
    total_batches_created: int = 0
    total_positions_batched: int = 0
    avg_batch_size: float = 0.0
    total_gas_saved_usd: Decimal = Decimal("0")
    single_liquidation_gas_usd: Decimal = Decimal("15")  # Baseline


class BatchLiquidationCoordinator(EnhancedModule):
    """
    Coordinates batch liquidations across chains.

    Accumulates liquidatable positions in a window and, when the window
    closes or the batch reaches max size, executes them all in a single
    multicall transaction per chain.
    """

    # Batch configuration
    BATCH_WINDOW_SECONDS = 3.0  # Time to accumulate positions
    MAX_BATCH_SIZE = 10         # Max liquidations per batch
    MIN_BATCH_SIZE = 2          # Don't batch singles
    SINGLE_GAS_USD = Decimal("15")  # Baseline gas per single liquidation

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        super().__init__("batch_liquidation_coordinator", config)
        # chain_id -> list of pending positions
        self._pending: Dict[int, List[EnrichedPosition]] = defaultdict(list)
        self._pending_timestamps: Dict[int, float] = {}
        self._stats = BatchCoordinatorStats()
        self._ready_batches: asyncio.Queue = asyncio.Queue(maxsize=100)

    async def _on_start(self) -> None:
        logger.info(
            "[BatchCoord] Window=%ss, max_size=%d, min_size=%d",
            self.BATCH_WINDOW_SECONDS, self.MAX_BATCH_SIZE, self.MIN_BATCH_SIZE,
        )

    async def _on_stop(self) -> None:
        # Flush remaining positions
        for chain_id in list(self._pending.keys()):
            if self._pending[chain_id]:
                batch = self._build_batch(chain_id)
                if batch:
                    logger.info("[BatchCoord] Flushed batch for chain %d (%d positions)",
                                chain_id, batch.count)

    # ── Position Accumulation ──────────────────────────────────

    async def add_position(self, position: EnrichedPosition) -> Optional[LiquidationBatch]:
        """
        Add a position to the pending batch. If the batch window has
        expired or max size is reached, return a ready batch.
        """
        chain_id = position.chain_id
        now = time.time()

        # Initialize window if first position on this chain
        if chain_id not in self._pending_timestamps:
            self._pending_timestamps[chain_id] = now

        self._pending[chain_id].append(position)

        # Check if batch should fire
        window_elapsed = now - self._pending_timestamps[chain_id]
        batch_full = len(self._pending[chain_id]) >= self.MAX_BATCH_SIZE

        if batch_full or window_elapsed >= self.BATCH_WINDOW_SECONDS:
            batch = self._build_batch(chain_id)
            if batch:
                await self._ready_batches.put(batch)
                return batch

        return None

    async def get_ready_batch(self, timeout: float = 5.0) -> Optional[LiquidationBatch]:
        """Get the next ready batch (blocking with timeout)."""
        try:
            return await asyncio.wait_for(self._ready_batches.get(), timeout)
        except asyncio.TimeoutError:
            return None

    async def flush_all(self) -> List[LiquidationBatch]:
        """Force-flush all pending positions into batches."""
        batches = []
        for chain_id in list(self._pending.keys()):
            if self._pending[chain_id]:
                batch = self._build_batch(chain_id)
                if batch:
                    batches.append(batch)
        return batches

    # ── Batch Building ─────────────────────────────────────────

    def _build_batch(self, chain_id: int) -> Optional[LiquidationBatch]:
        """Build a batch from pending positions on a chain."""
        positions = self._pending.pop(chain_id, [])
        self._pending_timestamps.pop(chain_id, None)

        if not positions:
            return None

        # Sort by profit potential
        positions.sort(key=lambda p: p.estimated_bonus_usd, reverse=True)

        # Calculate totals
        total_debt = sum(p.debt_usd for p in positions)
        total_bonus = sum(p.estimated_bonus_usd for p in positions)

        # Batched gas estimate: base + per-position marginal cost
        # First liquidation: full gas, each additional: ~60% marginal
        base_gas = self.SINGLE_GAS_USD
        marginal_gas = self.SINGLE_GAS_USD * Decimal("0.6")
        batched_gas = base_gas + marginal_gas * (len(positions) - 1)

        # Unbatched gas comparison
        unbatched_gas = self.SINGLE_GAS_USD * len(positions)
        gas_saved = unbatched_gas - batched_gas

        self._stats.total_batches_created += 1
        self._stats.total_positions_batched += len(positions)
        self._stats.total_gas_saved_usd += gas_saved
        self._stats.avg_batch_size = (
            self._stats.total_positions_batched / max(1, self._stats.total_batches_created)
        )

        batch_id = f"batch_{chain_id}_{int(time.time() * 1000)}"

        self.record_success()
        return LiquidationBatch(
            batch_id=batch_id,
            chain_id=chain_id,
            positions=positions,
            total_debt_usd=total_debt,
            total_bonus_usd=total_bonus,
            estimated_gas_usd=batched_gas,
            flash_loan_amount_usd=total_debt,
        )

    def get_stats(self) -> Dict[str, Any]:
        return {
            "total_batches": self._stats.total_batches_created,
            "total_positions_batched": self._stats.total_positions_batched,
            "avg_batch_size": round(self._stats.avg_batch_size, 1),
            "total_gas_saved_usd": float(self._stats.total_gas_saved_usd),
            "pending_chains": len(self._pending),
            "pending_total": sum(len(v) for v in self._pending.values()),
        }
