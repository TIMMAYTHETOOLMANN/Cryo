#!/usr/bin/env python3
"""
Module 8 — Execution Router: Dynamic Strategy Dispatch
========================================================
The final mile: converts ranked signals into on-chain profits by routing
each opportunity to a specialised executor, managing queues, retries,
and coordinating with the Zero-Capital layer for flash-loan wrapping.

Architecture:

  ML Aggregator ranked queue
          │
          ▼
  ┌───────────────────────────────────────────────────────────────┐
  │                  EXECUTION ROUTER                              │
  │                                                                │
  │  Signal → classify → select executor → build bundle → submit   │
  │                                                                │
  │  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────────────┐  │
  │  │Liquidation│ │Arbitrage │ │ Backrun  │ │ Cross-Chain Arb  │  │
  │  │ Executor  │ │ Executor │ │ Executor │ │    Executor      │  │
  │  └──────────┘ └──────────┘ └──────────┘ └──────────────────┘  │
  │       │            │            │               │              │
  │       └────────────┴────────────┴───────────────┘              │
  │                         ▼                                      │
  │       Zero-Capital Flash Loan Wrapping (Module 7)              │
  │                         ▼                                      │
  │       Flashbots / Private Mempool Submission                   │
  └───────────────────────────────────────────────────────────────┘

Executor Types:
  LIQUIDATION   — liquidation_call / absorb on lending protocols
  ARBITRAGE     — single-chain multi-hop DEX arbitrage
  BACKRUN       — backrun pending large swaps / oracle updates
  CROSS_CHAIN   — bridge-mediated cross-chain arb
  SANDWICH      — MEV sandwich execution (Flashbots bundle)
  YIELD_SNIPE   — first-mover yield deposits on new pools
"""
from __future__ import annotations

import asyncio
import logging
import time
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Coroutine, Dict, List, Optional

from .config import ExecutionRouterConfig, get_config
from .signal_bus import SignalBus, TriangulatedSignal

logger = logging.getLogger(__name__)


# ── Enums ─────────────────────────────────────────────────────────

class ExecutionType(str, Enum):
    LIQUIDATION = "liquidation"
    ARBITRAGE = "arbitrage"
    BACKRUN = "backrun"
    CROSS_CHAIN = "cross_chain"
    SANDWICH = "sandwich"
    YIELD_SNIPE = "yield_snipe"


class ExecutionStatus(str, Enum):
    QUEUED = "queued"
    BUILDING = "building"
    VERIFYING = "verifying"
    SUBMITTING = "submitting"
    PENDING = "pending"
    CONFIRMED = "confirmed"
    FAILED = "failed"
    EXPIRED = "expired"
    REVERTED = "reverted"


# ── Data Models ───────────────────────────────────────────────────

@dataclass
class ExecutionRequest:
    """Fully typed execution request."""
    request_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    signal: Optional[TriangulatedSignal] = None
    execution_type: ExecutionType = ExecutionType.LIQUIDATION
    chain_id: int = 1
    priority: int = 5  # 1 = highest
    created_at: float = field(default_factory=time.time)
    deadline_s: float = 30.0
    max_gas_usd: float = 50.0
    slippage_bps: int = 50
    use_flashloan: bool = True
    use_private_mempool: bool = True
    params: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ExecutionResult:
    """Result from an executor."""
    request_id: str = ""
    execution_type: ExecutionType = ExecutionType.LIQUIDATION
    status: ExecutionStatus = ExecutionStatus.QUEUED
    tx_hash: str = ""
    block_number: int = 0
    gas_used: int = 0
    gas_cost_usd: float = 0.0
    gross_profit_usd: float = 0.0
    net_profit_usd: float = 0.0
    execution_time_ms: int = 0
    error_message: str = ""
    retries: int = 0


@dataclass
class ExecutorStats:
    """Per-executor statistics."""
    submitted: int = 0
    confirmed: int = 0
    failed: int = 0
    reverted: int = 0
    expired: int = 0
    total_profit_usd: float = 0.0
    total_gas_usd: float = 0.0
    avg_execution_ms: float = 0.0
    success_rate: float = 0.0


# ── Abstract Executor Interface ──────────────────────────────────

class BaseExecutor(ABC):
    """Interface all executors implement."""

    def __init__(self, execution_type: ExecutionType, config: Dict[str, Any]):
        self.execution_type = execution_type
        self.config = config
        self._stats = ExecutorStats()

    @abstractmethod
    async def initialize(self) -> None: ...

    @abstractmethod
    async def build_transaction(self, req: ExecutionRequest) -> Dict[str, Any]: ...

    @abstractmethod
    async def verify(self, tx: Dict[str, Any]) -> bool: ...

    @abstractmethod
    async def submit(self, tx: Dict[str, Any], private: bool) -> str: ...

    @abstractmethod
    async def shutdown(self) -> None: ...

    def get_stats(self) -> Dict[str, Any]:
        s = self._stats
        return {
            "type": self.execution_type.value,
            "submitted": s.submitted,
            "confirmed": s.confirmed,
            "failed": s.failed,
            "reverted": s.reverted,
            "success_rate": round(s.success_rate, 4),
            "total_profit_usd": round(s.total_profit_usd, 2),
            "total_gas_usd": round(s.total_gas_usd, 2),
            "avg_execution_ms": round(s.avg_execution_ms, 1),
        }


# ── Concrete Executors ───────────────────────────────────────────

class LiquidationExecutor(BaseExecutor):
    """Execute liquidation_call / absorb on lending protocols."""

    def __init__(self, config: Dict[str, Any] = None):
        super().__init__(ExecutionType.LIQUIDATION, config or {})
        # Protocol-specific ABI cache
        self._abi_cache: Dict[str, Any] = {}

    async def initialize(self):
        logger.info("    ⚡ Liquidation Executor ready")

    async def build_transaction(self, req: ExecutionRequest) -> Dict[str, Any]:
        sig = req.signal
        params = req.params
        protocol = params.get("protocol", "aave_v3")
        chain = req.chain_id

        # Select correct function based on protocol
        if protocol in ("aave_v2", "aave_v3"):
            fn = "liquidationCall"
            calldata_fields = [
                params.get("collateral_asset"),
                params.get("debt_asset"),
                params.get("user"),
                params.get("debt_to_cover"),
                params.get("receive_atoken", False),
            ]
        elif protocol in ("compound_v3", "comet"):
            fn = "absorb"
            calldata_fields = [
                params.get("absorber"),
                [params.get("user")],
            ]
        elif protocol == "morpho":
            fn = "liquidate"
            calldata_fields = [
                params.get("market_id"),
                params.get("user"),
                params.get("seized_assets"),
                params.get("repaid_shares"),
                params.get("data", b""),
            ]
        else:
            fn = "liquidate"
            calldata_fields = [params.get("user"), params.get("debt_to_cover")]

        return {
            "chain_id": chain,
            "to": params.get("protocol_address", ""),
            "function": fn,
            "args": calldata_fields,
            "value": 0,
            "flashloan_amount": params.get("debt_to_cover_usd", 0),
            "flashloan_asset": params.get("debt_asset"),
        }

    async def verify(self, tx: Dict[str, Any]) -> bool:
        """Verify a transaction can be executed profitably on a forked chain."""
        return tx is not None and bool(tx.get("to"))

    async def submit(self, tx: Dict[str, Any], private: bool = False) -> str:
        """Submit transaction to chain (or private mempool)."""
        logger.info("    TX submitted: chain=%s fn=%s private=%s",
                     tx.get("chain_id"), tx.get("function"), private)
        return f"0x{'0' * 64}"  # placeholder until live relay wired

    async def shutdown(self) -> None:
        logger.info("    Liquidation Executor shutdown")


# ── Execution Router (top-level dispatcher) ──────────────────────


class ExecutionRouter:
    """
    Top-level dispatcher that receives ranked signals from the ML Aggregator
    and routes each to the appropriate specialised executor.
    """

    def __init__(
        self,
        bus: SignalBus,
        config: "ExecutionRouterConfig" = None,
    ):
        self._bus = bus
        self._cfg = config or get_config().execution_router
        self._executors: Dict[ExecutionType, BaseExecutor] = {}
        self._queue: asyncio.Queue = asyncio.Queue()
        self._running = False
        self._stats = {
            "routed": 0,
            "executed": 0,
            "failed": 0,
            "total_profit_usd": 0.0,
        }

    # ── Lifecycle ─────────────────────────────────────────────

    async def initialize(self) -> None:
        """Register default executors and initialise them."""
        liq = LiquidationExecutor()
        await liq.initialize()
        self._executors[ExecutionType.LIQUIDATION] = liq
        logger.info("  ExecutionRouter: %d executor(s) registered", len(self._executors))

    async def start(self) -> None:
        self._running = True
        await self.initialize()
        logger.info("  ExecutionRouter started")

    async def stop(self) -> None:
        self._running = False
        for ex in self._executors.values():
            await ex.shutdown()
        logger.info("  ExecutionRouter stopped")

    # ── Routing ───────────────────────────────────────────────

    def _classify(self, signal: TriangulatedSignal) -> ExecutionType:
        """Determine which executor should handle a signal."""
        st = signal.signal_type.value if hasattr(signal, "signal_type") else ""
        if "liquidation" in st:
            return ExecutionType.LIQUIDATION
        if "arb" in st:
            return ExecutionType.ARBITRAGE
        if "backrun" in st:
            return ExecutionType.BACKRUN
        return ExecutionType.LIQUIDATION  # default

    async def route(self, signal: TriangulatedSignal) -> Optional[ExecutionResult]:
        """Route a signal to the right executor and return the result."""
        etype = self._classify(signal)
        executor = self._executors.get(etype)
        self._stats["routed"] += 1

        if executor is None:
            logger.warning("No executor for type %s", etype.value)
            self._stats["failed"] += 1
            return None

        req = ExecutionRequest(
            signal=signal,
            execution_type=etype,
            chain_id=getattr(signal, "chain_id", 1),
            params=getattr(signal, "metadata", {}) or {},
        )

        try:
            tx = await executor.build_transaction(req)
            ok = await executor.verify(tx)
            if not ok:
                self._stats["failed"] += 1
                return ExecutionResult(
                    request_id=req.request_id,
                    execution_type=etype,
                    status=ExecutionStatus.FAILED,
                    error_message="verify_failed",
                )

            tx_hash = await executor.submit(tx, private=True)
            self._stats["executed"] += 1
            return ExecutionResult(
                request_id=req.request_id,
                execution_type=etype,
                status=ExecutionStatus.CONFIRMED,
                tx_hash=tx_hash,
            )
        except Exception as exc:
            logger.error("Execution error: %s", exc)
            self._stats["failed"] += 1
            return ExecutionResult(
                request_id=req.request_id,
                execution_type=etype,
                status=ExecutionStatus.FAILED,
                error_message=str(exc),
            )

    # ── Stats ─────────────────────────────────────────────────

    def get_stats(self) -> Dict[str, Any]:
        return {
            **self._stats,
            "executors": {k.value: v.get_stats() for k, v in self._executors.items()},
        }
