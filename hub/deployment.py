#!/usr/bin/env python3
"""
hub.deployment — Deployment Strategy Selector
================================================
Provides a clean enum of deployment modes and a single ``deploy()``
coroutine that wires the :class:`ModuleRegistry` and
:class:`ReconCoordinator` into the chosen strategy.

Strategies:
  STATUS        Print system status and exit.
  PREFLIGHT     Run Stage 0 preflight checks only.
  RECON         Run a unified reconnaissance sweep (all recon + analysis
                modules) and print the report.
  SCAN_ONLY     Phase-gated engine in detection-only mode (no execution).
  PHASE_1       Cold Start — flash-loan-only, $0 capital.
  PHASE_2       Heat Map — pattern learning + frequency optimisation.
  PHASE_3       Capital Multiplier — exponential compounding reinvestment.
  FULL_PIPELINE Module 1 stage-gated pipeline (Stages 0-7 + Module 9).
  MONITOR       Continuous profit monitor — watch executor events.

Usage::

    from hub import DeploymentStrategy, deploy
    await deploy(DeploymentStrategy.RECON, chain_ids=[1, 42161])
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional

from .registry import ModuleRegistry
from .recon_coordinator import ReconCoordinator

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


class DeploymentStrategy(Enum):
    """Available deployment modes."""

    STATUS = "status"
    PREFLIGHT = "preflight"
    RECON = "recon"
    SCAN_ONLY = "scan_only"
    PHASE_1 = "phase_1"
    PHASE_2 = "phase_2"
    PHASE_3 = "phase_3"
    FULL_PIPELINE = "full_pipeline"
    MONITOR = "monitor"


# ── Deployer ──────────────────────────────────────────────────────

async def deploy(
    strategy: DeploymentStrategy,
    *,
    chain_ids: Optional[List[int]] = None,
    config: Optional[Dict[str, Any]] = None,
    scan_only: bool = False,
) -> int:
    """
    Launch CRYO in the requested deployment strategy.

    Returns an exit code (0 = success).
    """
    config = config or {}
    chain_ids = chain_ids or [1]

    # Every strategy begins with a registry discovery
    registry = ModuleRegistry().discover()

    # ── STATUS ─────────────────────────────────────────────────
    if strategy == DeploymentStrategy.STATUS:
        registry.print_status()
        return 0

    # ── PREFLIGHT ──────────────────────────────────────────────
    if strategy == DeploymentStrategy.PREFLIGHT:
        return await _run_preflight()

    # ── RECON ──────────────────────────────────────────────────
    if strategy == DeploymentStrategy.RECON:
        coordinator = ReconCoordinator(registry)
        await coordinator.initialize()
        results = await coordinator.run_sweep(chain_ids=chain_ids)
        coordinator.print_report(results)
        return 0

    # ── SCAN_ONLY ──────────────────────────────────────────────
    if strategy == DeploymentStrategy.SCAN_ONLY:
        return await _run_phase(1, scan_only=True)

    # ── PHASE 1/2/3 ───────────────────────────────────────────
    if strategy in (
        DeploymentStrategy.PHASE_1,
        DeploymentStrategy.PHASE_2,
        DeploymentStrategy.PHASE_3,
    ):
        phase_map = {
            DeploymentStrategy.PHASE_1: 1,
            DeploymentStrategy.PHASE_2: 2,
            DeploymentStrategy.PHASE_3: 3,
        }
        return await _run_phase(phase_map[strategy], scan_only=scan_only)

    # ── FULL_PIPELINE ─────────────────────────────────────────
    if strategy == DeploymentStrategy.FULL_PIPELINE:
        return await _run_full_pipeline(scan_only=scan_only)

    # ── MONITOR ───────────────────────────────────────────────
    if strategy == DeploymentStrategy.MONITOR:
        return await _run_monitor()

    logger.error("Unknown strategy: %s", strategy)
    return 1


# ── Internal launchers (thin wrappers over existing entry points) ─

async def _run_preflight() -> int:
    """Delegate to existing main.run_preflight."""
    from main import run_preflight
    try:
        report = await run_preflight()
        return 0 if getattr(report, "verdict", "") != "NO-GO" else 1
    except Exception as exc:
        logger.error("Preflight error: %s", exc)
        return 1


async def _run_phase(phase: int, scan_only: bool = False) -> int:
    """Delegate to existing main.run_phase."""
    from main import run_phase
    return await run_phase(phase, scan_only=scan_only)


async def _run_full_pipeline(scan_only: bool = False) -> int:
    """Delegate to existing main.run_module1_pipeline."""
    from main import run_module1_pipeline
    return await run_module1_pipeline(scan_only=scan_only)


async def _run_monitor() -> int:
    """Lightweight async wrapper around ContinuousMonitor."""
    try:
        from continuous_profit_monitor import ContinuousMonitor
        monitor = ContinuousMonitor()
        # run() is blocking; run in executor to keep async compatibility
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, monitor.run)
        return 0
    except Exception as exc:
        logger.error("Monitor error: %s", exc)
        return 1
