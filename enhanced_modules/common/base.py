#!/usr/bin/env python3
"""
enhanced_modules.common.base — Abstract Base for All Enhanced Modules
=====================================================================
Every enhanced module implements this ABC so the MasterOrchestrator
can manage them uniformly via start / stop / status lifecycle.
"""
from __future__ import annotations

import asyncio
import logging
import time
from abc import ABC, abstractmethod
from enum import Enum
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


class ModuleState(Enum):
    """Lifecycle states for an enhanced module."""
    CREATED = "created"
    STARTING = "starting"
    RUNNING = "running"
    DEGRADED = "degraded"
    STOPPING = "stopping"
    STOPPED = "stopped"
    ERROR = "error"


class EnhancedModule(ABC):
    """
    Abstract base class for all 8 enhanced modules.

    Provides:
      - Uniform lifecycle (start / stop / status)
      - Built-in metrics accumulation
      - Error counting with automatic degradation after threshold
      - Heartbeat tracking
    """

    MAX_CONSECUTIVE_ERRORS = 5  # degrade after this many

    def __init__(self, name: str, config: Optional[Dict[str, Any]] = None):
        self.name = name
        self.config = config or {}
        self.state = ModuleState.CREATED
        self._started_at: Optional[float] = None
        self._metrics: Dict[str, Any] = {
            "invocations": 0,
            "successes": 0,
            "failures": 0,
            "last_error": None,
            "consecutive_errors": 0,
        }
        self._task: Optional[asyncio.Task] = None
        self._lock = asyncio.Lock()

    # ── Lifecycle ──────────────────────────────────────────────

    async def start(self) -> None:
        """Initialize resources and transition to RUNNING."""
        if self.state == ModuleState.RUNNING:
            return
        self.state = ModuleState.STARTING
        self._started_at = time.time()
        try:
            await self._on_start()
            self.state = ModuleState.RUNNING
            logger.info("[%s] Started", self.name)
        except Exception as exc:
            self.state = ModuleState.ERROR
            self._metrics["last_error"] = str(exc)
            logger.error("[%s] Start failed: %s", self.name, exc)
            raise

    async def stop(self) -> None:
        """Release resources and transition to STOPPED."""
        self.state = ModuleState.STOPPING
        try:
            if self._task and not self._task.done():
                self._task.cancel()
                try:
                    await self._task
                except asyncio.CancelledError:
                    pass
            await self._on_stop()
        finally:
            self.state = ModuleState.STOPPED
            logger.info("[%s] Stopped", self.name)

    def status(self) -> Dict[str, Any]:
        """Return a status dict for monitoring / dashboard."""
        uptime = time.time() - self._started_at if self._started_at else 0.0
        return {
            "name": self.name,
            "state": self.state.value,
            "uptime_seconds": round(uptime, 1),
            **self._metrics,
        }

    # ── Error Tracking ─────────────────────────────────────────

    def record_success(self) -> None:
        self._metrics["invocations"] += 1
        self._metrics["successes"] += 1
        self._metrics["consecutive_errors"] = 0
        if self.state == ModuleState.DEGRADED:
            self.state = ModuleState.RUNNING

    def record_failure(self, error: str) -> None:
        self._metrics["invocations"] += 1
        self._metrics["failures"] += 1
        self._metrics["consecutive_errors"] += 1
        self._metrics["last_error"] = error
        if self._metrics["consecutive_errors"] >= self.MAX_CONSECUTIVE_ERRORS:
            self.state = ModuleState.DEGRADED
            logger.warning(
                "[%s] DEGRADED — %d consecutive errors",
                self.name, self._metrics["consecutive_errors"],
            )

    # ── Subclass Hooks ─────────────────────────────────────────

    @abstractmethod
    async def _on_start(self) -> None:
        """Called during start() — allocate resources, load models, etc."""

    @abstractmethod
    async def _on_stop(self) -> None:
        """Called during stop() — release connections, flush buffers."""
