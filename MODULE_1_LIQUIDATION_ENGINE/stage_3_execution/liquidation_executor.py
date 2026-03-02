#!/usr/bin/env python3
"""
STAGE 3 — Liquidation Executor (Consolidated)
===============================================
Python interface to on-chain LiquidationExecutor contracts (V1, V2, Flash).
Handles simulation, gas-gating, TX submission, and event parsing.

CRITICAL: This module MUST only be called after GasManager.can_execute()
returns True.  The pipeline enforces this.
"""

# Re-export from the production src/ module
try:
    from ..src.executors.liquidation_executor import (
        LiquidationExecutor,
        LiquidationRequest,
        LiquidationResult,
        LiquidationProtocol,
        LIQUIDATION_EXECUTOR_ABI,
    )
except ImportError:
    import logging
    logging.getLogger(__name__).warning(
        "Could not import LiquidationExecutor from src/ — stub mode"
    )

    class LiquidationExecutor:
        pass

    class LiquidationRequest:
        pass

    class LiquidationResult:
        pass

__all__ = [
    "LiquidationExecutor",
    "LiquidationRequest",
    "LiquidationResult",
]
