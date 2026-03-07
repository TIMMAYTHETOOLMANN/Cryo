#!/usr/bin/env python3
"""
MODULE 11 — Timing Optimizer Engine: Master Orchestrator
==========================================================
Wires all 6 submodules into a unified preemptive execution machine that
predicts the exact block where HF crosses 1.0 and submits a pre-signed
transaction to land in that same block.

Architecture::

    ┌─────────────────────────────────────────────────────────────────┐
    │                   TIMING OPTIMIZER ENGINE                       │
    ├─────────────────────────────────────────────────────────────────┤
    │                                                                 │
    │  ┌──────────────┐  ┌──────────────┐  ┌──────────────────────┐ │
    │  │ 11.1 Oracle  │  │ 11.2 Mempool │  │ 11.3 Predictive      │ │
    │  │ PriceWatcher │  │ Sniffer      │  │ HealthModel          │ │
    │  └──────┬───────┘  └──────┬───────┘  └──────────┬───────────┘ │
    │         │ price_tick       │ backrun_signal       │ probability │
    │         └──────────┬───────┘─────────────────────┘             │
    │                    ▼                                            │
    │  ┌─────────────────────────────────────────────────────────┐   │
    │  │              DECISION ENGINE                             │   │
    │  │  Aggregate signals → Determine action → Route            │   │
    │  └─────────────────────────┬───────────────────────────────┘   │
    │                            ▼                                    │
    │  ┌─────────────────────────────────────────────────────────┐   │
    │  │         11.5 ProfitabilityRecheck                       │   │
    │  │  Last-millisecond profit verification at current prices  │   │
    │  └─────────────────────────┬───────────────────────────────┘   │
    │                            ▼                                    │
    │  ┌─────────────────────────────────────────────────────────┐   │
    │  │         11.4 JustInTimeExecutor                          │   │
    │  │  Pre-signed TX pool → Flashbots / Relay / Public submit  │   │
    │  └─────────────────────────────────────────────────────────┘   │
    └─────────────────────────────────────────────────────────────────┘

Integration::

    # In master_orchestrator.py:
    from MODULE_11_TIMING_ENGINE import TimingOptimizerEngine, get_timing_config
    self._timing_engine = TimingOptimizerEngine(config, w3_providers)
    await self._timing_engine.start()

    # In each pipeline cycle, feed watchlist positions:
    await self._timing_engine.ingest_positions(watchlist)

    # The engine autonomously:
    #   - Monitors oracle feeds for price movements
    #   - Scans mempool for cascading triggers
    #   - Predicts HF crossings with ML model
    #   - Pre-signs and pools liquidation TXs
    #   - Submits at the optimal moment
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Coroutine, Dict, List, Optional

from web3 import Web3

from .config import TimingConfig, get_timing_config
from .oracle_price_watcher import OraclePriceWatcher
from .mempool_sniffer import MempoolSniffer
from .predictive_health_model import PredictiveHealthModel, PositionFeatures
from .jit_executor import JustInTimeExecutor
from .profitability_recheck import ProfitabilityRecheck

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════
# WATCHED POSITION — Shared State for the Engine
# ═══════════════════════════════════════════════════════════════════

@dataclass
class TimingPosition:
    """
    A position the Timing Engine is actively monitoring.
    Enriched with ML predictions and oracle state.
    """
    user_address: str
    chain_id: int
    pool_address: str
    debt_asset: str
    collateral_asset: str
    total_debt_usd: float
    total_collateral_usd: float
    health_factor: float
    liquidation_threshold: float = 0.825
    # Computed fields
    liquidation_price: float = 0.0
    current_collateral_price: float = 0.0
    price_distance_pct: float = 0.0
    estimated_profit_usd: float = 0.0
    # ML prediction
    ml_probability: float = 0.0
    ml_action: str = "monitor"
    # State flags
    tx_prepared: bool = False
    oracle_triggered: bool = False
    mempool_triggered: bool = False
    last_updated: float = field(default_factory=time.time)

    @property
    def position_key(self) -> str:
        return f"{self.chain_id}:{self.pool_address}:{self.user_address}"

    def compute_liquidation_price(self) -> None:
        """Pre-compute the collateral price at which HF crosses 1.0."""
        if (
            self.total_collateral_usd > 0
            and self.liquidation_threshold > 0
            and self.current_collateral_price > 0
        ):
            # HF = (collateral * liq_threshold) / debt
            # HF=1 → collateral * liq_threshold = debt
            # collateral_at_liq = debt / liq_threshold
            # price_at_liq = (debt / liq_threshold) / (collateral_units)
            # Simplified: price_at_liq = current_price * (1 / HF)
            if self.health_factor > 0:
                self.liquidation_price = (
                    self.current_collateral_price / self.health_factor
                )
                self.price_distance_pct = (
                    (self.current_collateral_price - self.liquidation_price)
                    / self.current_collateral_price
                    * 100
                )
            else:
                self.liquidation_price = self.current_collateral_price
                self.price_distance_pct = 0.0


# ═══════════════════════════════════════════════════════════════════
# ENGINE STATS
# ═══════════════════════════════════════════════════════════════════

@dataclass
class TimingEngineStats:
    """Aggregate statistics for the timing engine."""
    uptime_seconds: float = 0.0
    positions_watched: int = 0
    oracle_ticks_received: int = 0
    mempool_triggers_received: int = 0
    predictions_made: int = 0
    high_confidence_triggers: int = 0
    txs_prepared: int = 0
    txs_submitted: int = 0
    txs_confirmed: int = 0
    txs_reverted: int = 0
    profit_recheck_passed: int = 0
    profit_recheck_rejected: int = 0
    total_profit_usd: float = 0.0


# ═══════════════════════════════════════════════════════════════════
# TIMING OPTIMIZER ENGINE
# ═══════════════════════════════════════════════════════════════════

class TimingOptimizerEngine:
    """
    Master orchestrator for the Timing Optimizer & JIT Execution system.

    Wires:
      11.1  OraclePriceWatcher      — Event-driven price monitoring
      11.2  MempoolSniffer          — Pending TX analysis → backrun signals
      11.3  PredictiveHealthModel   — ML probability of liquidation
      11.4  JustInTimeExecutor      — Pre-signed TX pool + multi-channel submit
      11.5  ProfitabilityRecheck    — Last-millisecond profit verification

    Lifecycle:
      engine = TimingOptimizerEngine(config, w3_providers)
      await engine.start()
      await engine.ingest_positions(positions)  # Feed from pipeline
      # ... runs autonomously ...
      await engine.stop()
    """

    def __init__(
        self,
        config: Optional[TimingConfig] = None,
        w3_providers: Optional[Dict[int, Web3]] = None,
        rpc_gateway: Any = None,
    ):
        self.cfg = config or get_timing_config()
        self._w3 = w3_providers or {}
        self._rpc_gateway = rpc_gateway

        # ── Submodules ────────────────────────────────────────
        self.oracle_watcher = OraclePriceWatcher(
            w3_providers=self._w3,
            config=self.cfg.oracle,
        )
        self.mempool_sniffer = MempoolSniffer(
            w3_providers=self._w3,
            config=self.cfg.mempool,
        )
        self.predictor = PredictiveHealthModel(config=self.cfg.predictor)
        self.jit_executor = JustInTimeExecutor(
            w3_providers=self._w3,
            private_key=os.getenv("PRIVATE_KEY", ""),
            config=self.cfg.jit,
        )
        self.profit_recheck = ProfitabilityRecheck(
            w3_providers=self._w3,
            config=self.cfg.profitability,
        )

        # ── Watchlist ─────────────────────────────────────────
        self._positions: Dict[str, TimingPosition] = {}

        # ── State ─────────────────────────────────────────────
        self._running = False
        self._tasks: List[asyncio.Task] = []
        self._start_time = 0.0
        self.stats = TimingEngineStats()

        # ── External callbacks ────────────────────────────────
        self._on_execution_callback: Optional[
            Callable[..., Coroutine]
        ] = None

    # ── Lifecycle ────────────────────────────────────────────────

    async def start(self) -> None:
        """Initialize all submodules and begin autonomous operation."""
        self._running = True
        self._start_time = time.time()

        logger.info("=" * 70)
        logger.info("  MODULE 11 — TIMING OPTIMIZER ENGINE")
        logger.info("  Preemptive Execution & JIT Liquidation")
        logger.info("=" * 70)

        # 1. Initialize the ML predictor
        await self.predictor.initialize()
        await self.predictor.start()
        logger.info("  [+] 11.3 Predictive Health Model initialized (%s)",
                     self.predictor._model_type)

        # 2. Start the JIT executor (background stale reaper)
        await self.jit_executor.start()
        logger.info("  [+] 11.4 JIT Executor started (pool capacity=%d)",
                     self.cfg.jit.max_pre_signed_txs)

        # 3. Register oracle watcher callbacks
        self.oracle_watcher.on_price_update = self._handle_oracle_tick
        logger.info("  [+] 11.1 Oracle Price Watcher ready (%d chains)",
                     len(self._w3))

        # 4. Register mempool sniffer callbacks
        self.mempool_sniffer.on_backrun_opportunity = self._handle_mempool_trigger
        logger.info("  [+] 11.2 Mempool Sniffer ready")

        # 5. Start background loops
        self._tasks = [
            asyncio.create_task(
                self._prediction_loop(), name="timing_prediction"
            ),
            asyncio.create_task(
                self._position_refresh_loop(), name="timing_refresh"
            ),
            asyncio.create_task(
                self._stats_loop(), name="timing_stats"
            ),
        ]

        # 6. Start oracle watcher & mempool sniffer background loops
        try:
            oracle_task = asyncio.create_task(
                self.oracle_watcher.run(), name="oracle_watcher"
            )
            self._tasks.append(oracle_task)
            logger.info("  [+] Oracle watcher loop running")
        except Exception as exc:
            logger.warning("  [-] Oracle watcher failed to start: %s", exc)

        if self.cfg.mempool.enabled:
            try:
                sniffer_task = asyncio.create_task(
                    self.mempool_sniffer.run(), name="mempool_sniffer"
                )
                self._tasks.append(sniffer_task)
                logger.info("  [+] Mempool sniffer loop running")
            except Exception as exc:
                logger.warning("  [-] Mempool sniffer failed to start: %s", exc)

        logger.info("  Timing Engine: %d background tasks active", len(self._tasks))
        logger.info("=" * 70)

    async def stop(self) -> None:
        """Gracefully shut down all submodules."""
        logger.info("Timing Engine shutting down...")
        self._running = False

        # Cancel all background tasks
        for task in self._tasks:
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()

        # Stop submodules
        await self.jit_executor.stop()
        await self.predictor.stop()

        # Persist predictor model
        self.predictor._persist()

        logger.info(
            "Timing Engine stopped. Stats: %d prepared, %d submitted, "
            "%d confirmed, $%.2f profit",
            self.stats.txs_prepared,
            self.stats.txs_submitted,
            self.stats.txs_confirmed,
            self.stats.total_profit_usd,
        )

    # ── Position Ingestion (from pipeline / scanner) ─────────────

    async def ingest_positions(
        self, positions: List[Dict[str, Any]]
    ) -> int:
        """
        Ingest watchlist positions from the main pipeline.

        Accepts dicts with keys:
          user_address, chain_id, pool_address, debt_asset, collateral_asset,
          total_debt_usd, total_collateral_usd, health_factor, ...

        Returns number of positions added/updated.
        """
        ingested = 0
        for pos_data in positions:
            hf = pos_data.get("health_factor", 999)
            # Only watch positions approaching liquidation
            if hf > 1.15:
                continue

            key = (
                f"{pos_data.get('chain_id', 0)}:"
                f"{pos_data.get('pool_address', '')}:"
                f"{pos_data.get('user_address', '')}"
            )
            if key in self._positions:
                # Update existing
                p = self._positions[key]
                p.health_factor = hf
                p.total_debt_usd = pos_data.get("total_debt_usd", p.total_debt_usd)
                p.total_collateral_usd = pos_data.get(
                    "total_collateral_usd", p.total_collateral_usd
                )
                p.estimated_profit_usd = pos_data.get(
                    "estimated_profit_usd", p.estimated_profit_usd
                )
                p.last_updated = time.time()
            else:
                p = TimingPosition(
                    user_address=pos_data.get("user_address", ""),
                    chain_id=pos_data.get("chain_id", 1),
                    pool_address=pos_data.get("pool_address", ""),
                    debt_asset=pos_data.get("debt_asset", ""),
                    collateral_asset=pos_data.get("collateral_asset", ""),
                    total_debt_usd=pos_data.get("total_debt_usd", 0),
                    total_collateral_usd=pos_data.get("total_collateral_usd", 0),
                    health_factor=hf,
                    liquidation_threshold=pos_data.get(
                        "liquidation_threshold", 0.825
                    ),
                    current_collateral_price=pos_data.get(
                        "current_collateral_price", 0
                    ),
                    estimated_profit_usd=pos_data.get("estimated_profit_usd", 0),
                )
                p.compute_liquidation_price()
                self._positions[key] = p
            ingested += 1

        self.stats.positions_watched = len(self._positions)
        return ingested

    # ── Oracle Tick Handler ──────────────────────────────────────

    async def _handle_oracle_tick(
        self,
        feed_address: str,
        asset: str,
        new_price: float,
        chain_id: int,
        block_number: int,
    ) -> None:
        """
        Called by OraclePriceWatcher when a Chainlink price updates.
        Checks if any watched position's HF would cross < 1.0 at this price.
        """
        self.stats.oracle_ticks_received += 1

        for key, pos in list(self._positions.items()):
            if pos.chain_id != chain_id:
                continue
            # Check if this price feed affects this position's collateral
            if pos.liquidation_price <= 0:
                continue

            # Update collateral price
            pos.current_collateral_price = new_price
            pos.compute_liquidation_price()

            # Check crossing
            if new_price <= pos.liquidation_price:
                pos.oracle_triggered = True
                logger.info(
                    "[TimingEngine] ORACLE TRIGGER: %s HF would cross 1.0 "
                    "(price=%.4f <= liq_price=%.4f)",
                    key, new_price, pos.liquidation_price,
                )
                await self._attempt_execution(pos, trigger="oracle_update")

    # ── Mempool Trigger Handler ──────────────────────────────────

    async def _handle_mempool_trigger(
        self,
        tx_hash: str,
        affected_pair: str,
        estimated_impact_pct: float,
        chain_id: int,
    ) -> None:
        """
        Called by MempoolSniffer when a pending TX would impact prices
        enough to trigger a liquidation.
        """
        self.stats.mempool_triggers_received += 1

        for key, pos in list(self._positions.items()):
            if pos.chain_id != chain_id:
                continue
            if pos.health_factor > 1.05:
                continue

            # Check if the price impact would push this position underwater
            new_hf = pos.health_factor * (1 - estimated_impact_pct / 100)
            if new_hf < 1.0:
                pos.mempool_triggered = True
                logger.info(
                    "[TimingEngine] MEMPOOL TRIGGER: %s predicted HF=%.4f "
                    "after %.2f%% impact from %s",
                    key, new_hf, estimated_impact_pct, tx_hash[:16],
                )
                await self._attempt_execution(
                    pos,
                    trigger="mempool_backrun",
                    trigger_tx_hash=tx_hash,
                )

    # ── Execution Attempt ────────────────────────────────────────

    async def _attempt_execution(
        self,
        pos: TimingPosition,
        trigger: str = "",
        trigger_tx_hash: str = "",
    ) -> bool:
        """
        Full execution flow:
          1. Profitability recheck at current prices
          2. Prepare or retrieve pre-signed TX
          3. Submit via optimal channel
        """
        key = pos.position_key

        # Step 1: Last-millisecond profitability recheck
        recheck = await self.profit_recheck.verify(
            chain_id=pos.chain_id,
            pool_address=pos.pool_address,
            user_address=pos.user_address,
            collateral_asset=pos.collateral_asset,
            debt_asset=pos.debt_asset,
            total_debt_usd=pos.total_debt_usd,
            total_collateral_usd=pos.total_collateral_usd,
            estimated_profit_usd=pos.estimated_profit_usd,
        )
        if not recheck.profitable:
            self.stats.profit_recheck_rejected += 1
            logger.debug(
                "[TimingEngine] Profit recheck REJECTED for %s: %s",
                key, recheck.reason,
            )
            return False
        self.stats.profit_recheck_passed += 1

        # Step 2: Prepare TX (or use existing from pool)
        if key not in self.jit_executor.pool:
            entry = await self.jit_executor.prepare_transaction(
                position_key=key,
                user_address=pos.user_address,
                chain_id=pos.chain_id,
                pool_address=pos.pool_address,
                collateral_asset=pos.collateral_asset,
                debt_asset=pos.debt_asset,
                total_debt_usd=pos.total_debt_usd,
                estimated_profit_usd=recheck.adjusted_profit_usd,
                trigger_type=trigger,
                trigger_tx_hash=trigger_tx_hash,
            )
            if not entry:
                return False
            self.stats.txs_prepared += 1

        # Step 3: Submit
        result = await self.jit_executor.on_threshold_crossed(key)
        if result:
            self.stats.txs_submitted += 1
            channel = result.get("channel", "unknown")
            logger.info(
                "[TimingEngine] TX SUBMITTED for %s via %s (profit=$%.2f)",
                key, channel, recheck.adjusted_profit_usd,
            )

            # Feed outcome back to ML model
            self.predictor.record_outcome(
                PositionFeatures(
                    health_factor=pos.health_factor,
                    debt_collateral_ratio=(
                        pos.total_debt_usd / max(pos.total_collateral_usd, 1)
                    ),
                    price_distance_pct=pos.price_distance_pct,
                    debt_usd=pos.total_debt_usd,
                    collateral_usd=pos.total_collateral_usd,
                    chain_id=pos.chain_id,
                ),
                liquidated=True,
            )

            # Notify external callback if registered
            if self._on_execution_callback:
                try:
                    await self._on_execution_callback(
                        position_key=key,
                        trigger=trigger,
                        profit_usd=recheck.adjusted_profit_usd,
                        result=result,
                    )
                except Exception as exc:
                    logger.debug("Execution callback error: %s", exc)

            return True
        return False

    # ── Background Loops ─────────────────────────────────────────

    async def _prediction_loop(self) -> None:
        """
        Continuously run ML predictions on all watched positions.
        For high-confidence predictions, pre-sign TXs proactively.
        """
        while self._running:
            try:
                for key, pos in list(self._positions.items()):
                    if not self._running:
                        break
                    if pos.health_factor > 1.10:
                        continue

                    features = PositionFeatures(
                        health_factor=pos.health_factor,
                        debt_collateral_ratio=(
                            pos.total_debt_usd
                            / max(pos.total_collateral_usd, 1)
                        ),
                        price_distance_pct=pos.price_distance_pct,
                        debt_usd=pos.total_debt_usd,
                        collateral_usd=pos.total_collateral_usd,
                        chain_id=pos.chain_id,
                    )

                    result = await self.predictor.predict(features)
                    self.stats.predictions_made += 1

                    pos.ml_probability = result.probability
                    pos.ml_action = result.action

                    if result.action == "execute_now":
                        self.stats.high_confidence_triggers += 1
                        logger.info(
                            "[TimingEngine] ML EXECUTE_NOW: %s prob=%.3f",
                            key, result.probability,
                        )
                        await self._attempt_execution(pos, trigger="prediction")

                    elif result.action == "prepare" and not pos.tx_prepared:
                        # Pre-sign a TX so it's ready when the moment comes
                        entry = await self.jit_executor.prepare_transaction(
                            position_key=key,
                            user_address=pos.user_address,
                            chain_id=pos.chain_id,
                            pool_address=pos.pool_address,
                            collateral_asset=pos.collateral_asset,
                            debt_asset=pos.debt_asset,
                            total_debt_usd=pos.total_debt_usd,
                            estimated_profit_usd=pos.estimated_profit_usd,
                            trigger_type="prediction",
                        )
                        if entry:
                            pos.tx_prepared = True
                            self.stats.txs_prepared += 1

                await asyncio.sleep(self.cfg.oracle.poll_interval_s)

            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.error("[TimingEngine] Prediction loop error: %s", exc)
                await asyncio.sleep(5)

    async def _position_refresh_loop(self) -> None:
        """
        Periodically prune stale positions and refresh HF from on-chain.
        """
        while self._running:
            try:
                await asyncio.sleep(30)  # Every 30 seconds
                now = time.time()
                stale_keys = [
                    k for k, p in self._positions.items()
                    if now - p.last_updated > 300  # 5 minutes stale
                ]
                for k in stale_keys:
                    del self._positions[k]

                if stale_keys:
                    logger.debug(
                        "[TimingEngine] Pruned %d stale positions", len(stale_keys)
                    )
                self.stats.positions_watched = len(self._positions)

            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.debug("[TimingEngine] Refresh loop error: %s", exc)
                await asyncio.sleep(10)

    async def _stats_loop(self) -> None:
        """Periodically update aggregate stats from submodules."""
        while self._running:
            try:
                await asyncio.sleep(60)  # Every minute
                self.stats.uptime_seconds = time.time() - self._start_time

                # Sync JIT executor stats
                jit = self.jit_executor.get_stats()
                self.stats.txs_confirmed = jit["txs_confirmed"]
                self.stats.txs_reverted = jit["txs_reverted"]
                self.stats.total_profit_usd = jit["total_profit_usd"]

            except asyncio.CancelledError:
                break
            except Exception:
                await asyncio.sleep(10)

    # ── External API ─────────────────────────────────────────────

    def set_execution_callback(
        self, callback: Callable[..., Coroutine]
    ) -> None:
        """Register an external callback for execution events."""
        self._on_execution_callback = callback

    def get_watched_positions(self) -> List[Dict[str, Any]]:
        """Return all currently watched positions as dicts."""
        return [
            {
                "position_key": p.position_key,
                "user_address": p.user_address,
                "chain_id": p.chain_id,
                "health_factor": p.health_factor,
                "liquidation_price": p.liquidation_price,
                "price_distance_pct": p.price_distance_pct,
                "ml_probability": p.ml_probability,
                "ml_action": p.ml_action,
                "tx_prepared": p.tx_prepared,
                "estimated_profit_usd": p.estimated_profit_usd,
            }
            for p in self._positions.values()
        ]

    def get_stats(self) -> Dict[str, Any]:
        """Return comprehensive engine statistics."""
        self.stats.uptime_seconds = time.time() - self._start_time
        predictor_stats = self.predictor.get_stats()
        jit_stats = self.jit_executor.get_stats()

        return {
            "uptime_seconds": self.stats.uptime_seconds,
            "positions_watched": self.stats.positions_watched,
            "oracle_ticks": self.stats.oracle_ticks_received,
            "mempool_triggers": self.stats.mempool_triggers_received,
            "predictions_made": self.stats.predictions_made,
            "high_confidence_triggers": self.stats.high_confidence_triggers,
            "profit_recheck_passed": self.stats.profit_recheck_passed,
            "profit_recheck_rejected": self.stats.profit_recheck_rejected,
            "txs_prepared": self.stats.txs_prepared,
            "txs_submitted": self.stats.txs_submitted,
            "txs_confirmed": self.stats.txs_confirmed,
            "txs_reverted": self.stats.txs_reverted,
            "total_profit_usd": self.stats.total_profit_usd,
            "predictor": predictor_stats,
            "jit_executor": jit_stats,
        }

    def print_status(self) -> None:
        """Print a formatted status report."""
        s = self.get_stats()
        print()
        print("=" * 70)
        print("  MODULE 11 — TIMING OPTIMIZER STATUS")
        print("=" * 70)
        print(f"  Uptime:                {s['uptime_seconds'] / 60:.1f} min")
        print(f"  Positions watched:     {s['positions_watched']}")
        print(f"  Oracle ticks:          {s['oracle_ticks']}")
        print(f"  Mempool triggers:      {s['mempool_triggers']}")
        print(f"  ML predictions:        {s['predictions_made']}")
        print(f"  High-conf triggers:    {s['high_confidence_triggers']}")
        print(f"  Profit recheck:        {s['profit_recheck_passed']} pass / "
              f"{s['profit_recheck_rejected']} reject")
        print(f"  TXs prepared:          {s['txs_prepared']}")
        print(f"  TXs submitted:         {s['txs_submitted']}")
        print(f"  TXs confirmed:         {s['txs_confirmed']}")
        print(f"  TXs reverted:          {s['txs_reverted']}")
        print(f"  Total profit:          ${s['total_profit_usd']:.2f}")
        print("=" * 70)
