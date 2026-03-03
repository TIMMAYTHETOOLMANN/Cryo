#!/usr/bin/env python3
"""
hub.tactical_ops — Tactical Operations Coordinator
=====================================================
Replaces the scattered standalone scripts (``dynamic_recon.py``,
``target_finder.py``, ``activate_phase2_modules.py``,
``swiss_army_knife.py``, ``check_pipeline.py``) with a unified,
step-by-step operations framework accessible through the hub.

Each operation is a named, sequenced plan that the coordinator
executes stage-by-stage, reporting results after each step.

Built-in Operations:
  **full_recon**      Preflight → init recon modules → sweep all chains
                      → score & rank → produce target list
  **target_acquire**  Init detection modules → probe for liquidatable
                      positions → score via ML → rank by profit
  **system_check**    Registry status → log analysis → process check →
                      health summary
  **phase2_activate** Init all Phase 2 modules (crawler, analyzer,
                      cross-chain, ML) via registry

Usage::

    from hub import ModuleRegistry
    from hub.tactical_ops import TacticalOps
    reg = ModuleRegistry().discover()
    ops = TacticalOps(reg)
    report = await ops.execute("full_recon", chain_ids=[1, 42161])
    ops.print_report(report)
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from .registry import ModuleRegistry, ModuleStatus
from .recon_coordinator import ReconCoordinator, ReconResult

logger = logging.getLogger(__name__)


# ── Step / Report dataclasses ──────────────────────────────────────

@dataclass
class StepResult:
    """Outcome of one tactical step."""
    name: str
    status: str             # "ok", "warn", "fail", "skip"
    duration_s: float = 0.0
    detail: str = ""
    data: Dict[str, Any] = field(default_factory=dict)


@dataclass
class OpReport:
    """Full operation report: ordered list of step results."""
    operation: str
    steps: List[StepResult] = field(default_factory=list)
    started_at: float = 0.0
    finished_at: float = 0.0

    @property
    def total_duration_s(self) -> float:
        return self.finished_at - self.started_at if self.finished_at else 0.0

    @property
    def passed(self) -> int:
        return sum(1 for s in self.steps if s.status == "ok")

    @property
    def failed(self) -> int:
        return sum(1 for s in self.steps if s.status == "fail")


# ── Tactical Ops Coordinator ──────────────────────────────────────

class TacticalOps:
    """
    Step-by-step tactical operations framework.

    Replaces loose standalone scripts with structured, logged,
    sequenced operations that coordinate through the hub's
    ModuleRegistry.
    """

    def __init__(self, registry: ModuleRegistry):
        self._registry = registry
        self._operations: Dict[str, Callable] = {
            "full_recon":      self._op_full_recon,
            "target_acquire":  self._op_target_acquire,
            "system_check":    self._op_system_check,
            "phase2_activate": self._op_phase2_activate,
        }

    @property
    def available_operations(self) -> List[str]:
        return list(self._operations.keys())

    # ── Execute ────────────────────────────────────────────────

    async def execute(
        self,
        operation: str,
        *,
        chain_ids: Optional[List[int]] = None,
        config: Optional[Dict[str, Any]] = None,
    ) -> OpReport:
        """
        Execute a named operation.  Returns an :class:`OpReport`.
        """
        fn = self._operations.get(operation)
        if fn is None:
            raise ValueError(
                f"Unknown operation {operation!r}. "
                f"Available: {', '.join(self._operations)}"
            )

        report = OpReport(operation=operation, started_at=time.time())
        await fn(report, chain_ids=chain_ids or [1], config=config or {})
        report.finished_at = time.time()
        return report

    # ── Built-in Operations ────────────────────────────────────

    async def _op_full_recon(
        self, report: OpReport, chain_ids: List[int], config: Dict,
    ) -> None:
        """
        Full reconnaissance sweep.

        Steps:
          1. Init recon + detection + analysis modules
          2. Run parallel sweep across all chains
          3. Aggregate and rank results
        """
        # Step 1: Init modules
        step = await self._timed_step(
            "init_recon_modules",
            lambda: self._registry.initialize_category("recon"),
        )
        report.steps.append(step)

        step = await self._timed_step(
            "init_detection_modules",
            lambda: self._registry.initialize_category("detection"),
        )
        report.steps.append(step)

        step = await self._timed_step(
            "init_analysis_modules",
            lambda: self._registry.initialize_category("analysis"),
        )
        report.steps.append(step)

        # Step 2: Recon sweep
        coord = ReconCoordinator(self._registry)
        coord._initialized = True  # we already init'd above
        results: List[ReconResult] = []
        t0 = time.time()
        try:
            results = await coord.run_sweep(chain_ids=chain_ids, timeout=30.0)
            step = StepResult(
                name="recon_sweep",
                status="ok",
                duration_s=time.time() - t0,
                detail=f"{len(results)} results from {len(chain_ids)} chain(s)",
                data={"result_count": len(results), "chain_ids": chain_ids},
            )
        except Exception as exc:
            step = StepResult(
                name="recon_sweep", status="fail",
                duration_s=time.time() - t0, detail=str(exc),
            )
        report.steps.append(step)

        # Step 3: Rank results
        ranked = sorted(
            [r for r in results if r.estimated_value_usd > 0],
            key=lambda r: r.estimated_value_usd,
            reverse=True,
        )

        step = StepResult(
            name="rank_results",
            status="ok" if results else "warn",
            detail=f"{len(ranked)} ranked targets",
            data={
                "ranked_count": len(ranked),
                "top_targets": [
                    {"source": r.source, "value": r.estimated_value_usd}
                    for r in ranked[:5]
                ],
            },
        )
        report.steps.append(step)

    async def _op_target_acquire(
        self, report: OpReport, chain_ids: List[int], config: Dict,
    ) -> None:
        """
        Target acquisition — init detection, probe for positions.

        Steps:
          1. Init detection modules
          2. Init analysis modules (gas optimizer, ML ranker)
          3. Probe each detection module for stats
          4. Summarise targets
        """
        for cat in ("detection", "analysis"):
            step = await self._timed_step(
                f"init_{cat}_modules",
                lambda c=cat: self._registry.initialize_category(c),
            )
            report.steps.append(step)

        # Probe all available detection + analysis modules
        probed = 0
        targets = []
        for info in self._registry.list_modules():
            if info.category not in ("detection", "analysis"):
                continue
            if not info.is_available():
                continue
            probed += 1
            if hasattr(info.instance, "get_stats"):
                try:
                    stats = info.instance.get_stats()
                    targets.append({"module": info.name, "stats": stats})
                except Exception:
                    pass

        step = StepResult(
            name="probe_modules",
            status="ok",
            detail=f"Probed {probed} modules, {len(targets)} with stats",
            data={"probed": probed, "targets": targets},
        )
        report.steps.append(step)

    async def _op_system_check(
        self, report: OpReport, chain_ids: List[int], config: Dict,
    ) -> None:
        """
        System health check — replaces check_pipeline.py.

        Steps:
          1. Registry status
          2. Log analysis (if log file exists)
          3. Process check
        """
        # Step 1: Registry status
        st = self._registry.status()
        step = StepResult(
            name="registry_status",
            status="ok",
            detail=f"{st['total_modules']} modules, {st['ready']} ready, "
                   f"{st['pending']} pending",
            data=st,
        )
        report.steps.append(step)

        # Step 2: Log analysis
        log_file = _find_log_file()
        if log_file:
            analysis = _analyze_log(log_file)
            step = StepResult(
                name="log_analysis",
                status="ok" if analysis["errors"] == 0 else "warn",
                detail=f"{analysis['total_lines']} lines, "
                       f"{analysis['scan_cycles']} cycles, "
                       f"{analysis['errors']} errors",
                data=analysis,
            )
        else:
            step = StepResult(
                name="log_analysis", status="skip",
                detail="No log file found",
            )
        report.steps.append(step)

        # Step 3: Process check
        proc_count = _count_python_processes()
        step = StepResult(
            name="process_check",
            status="ok",
            detail=f"{proc_count} Python processes running",
            data={"python_processes": proc_count},
        )
        report.steps.append(step)

    async def _op_phase2_activate(
        self, report: OpReport, chain_ids: List[int], config: Dict,
    ) -> None:
        """
        Activate all Phase 2 modules — replaces activate_phase2_modules.py.

        Steps:
          1. Init recon modules (crawler, static analyzer, cross-chain, archive, alpha)
          2. Init analysis modules (ML ranker, gas optimizer)
          3. Summary
        """
        for cat in ("recon", "analysis"):
            step = await self._timed_step(
                f"init_{cat}",
                lambda c=cat: self._registry.initialize_category(c),
            )
            report.steps.append(step)

        # Summary
        ready = [m for m in self._registry.list_modules() if m.is_available()]
        step = StepResult(
            name="phase2_summary",
            status="ok",
            detail=f"{len(ready)} modules ready",
            data={
                "ready_modules": [m.name for m in ready],
                "ready_count": len(ready),
            },
        )
        report.steps.append(step)

    # ── Helpers ────────────────────────────────────────────────

    async def _timed_step(self, name: str, fn: Callable) -> StepResult:
        """Run *fn* synchronously, timing it."""
        t0 = time.time()
        try:
            result = fn()
            return StepResult(
                name=name, status="ok",
                duration_s=time.time() - t0,
                detail=str(result) if result else "done",
                data={"result": result} if isinstance(result, (list, dict)) else {},
            )
        except Exception as exc:
            return StepResult(
                name=name, status="fail",
                duration_s=time.time() - t0,
                detail=str(exc),
            )

    # ── Reporting ──────────────────────────────────────────────

    @staticmethod
    def print_report(report: OpReport) -> None:
        """Pretty-print a tactical operation report."""
        print()
        print("=" * 72)
        print(f"  TACTICAL OPERATION: {report.operation.upper()}")
        print("=" * 72)
        print(f"  Duration: {report.total_duration_s:.2f}s   "
              f"Steps: {len(report.steps)}   "
              f"Passed: {report.passed}   Failed: {report.failed}")
        print()
        for i, step in enumerate(report.steps, 1):
            icon = {"ok": "+", "warn": "!", "fail": "X", "skip": "-"}.get(
                step.status, "?"
            )
            dur = f"({step.duration_s:.2f}s)" if step.duration_s else ""
            print(f"  {i:2d}. [{icon}] {step.name:30s} {dur:>10s}  {step.detail[:50]}")
        print()
        print("=" * 72)


# ── Utility functions (replaces check_pipeline.py) ────────────────

def _find_log_file() -> Optional[str]:
    """Locate the most likely CRYO log file."""
    project_root = Path(__file__).resolve().parent.parent
    candidates = ["cryo.log", "cryo1_live.log", "module1_liquidation_engine.log"]
    for name in candidates:
        path = project_root / name
        if path.exists():
            return str(path)
    return None


def _analyze_log(log_path: str) -> Dict[str, Any]:
    """Parse a CRYO log file for key metrics."""
    try:
        with open(log_path, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
    except Exception:
        return {"total_lines": 0, "scan_cycles": 0, "errors": 0}

    scan_cycles = sum(1 for l in lines if "Cycle" in l and "found=" in l and "INFO" in l)
    errors = sum(1 for l in lines if "ERROR" in l and "Traceback" not in l)
    profits = sum(1 for l in lines if "PROFIT:" in l)

    return {
        "total_lines": len(lines),
        "scan_cycles": scan_cycles,
        "errors": errors,
        "profits": profits,
        "log_file": log_path,
    }


def _count_python_processes() -> int:
    """Count running Python processes."""
    import subprocess
    import platform
    try:
        if platform.system() == "Windows":
            result = subprocess.run(
                ["tasklist"], capture_output=True, text=True, timeout=5,
            )
            return sum(1 for l in result.stdout.split("\n") if "python" in l.lower())
        else:
            result = subprocess.run(
                ["pgrep", "-c", "python"], capture_output=True, text=True, timeout=5,
            )
            return int(result.stdout.strip()) if result.returncode == 0 else 0
    except Exception:
        return -1
