#!/usr/bin/env python3
"""
enhanced_modules.module_8_analytics_dashboard.live_dashboard
==============================================================
Real-time profit dashboard with Prometheus metrics export and
optional JSON API server.

Exports:
  - cryo_profit_total (counter, labels: chain, protocol)
  - cryo_gas_spent_total (counter, labels: chain)
  - cryo_executions_total (counter, labels: chain, protocol, status)
  - cryo_success_rate (gauge)
  - cryo_active_positions (gauge, labels: chain)
  - cryo_queue_depth (gauge)
  - cryo_health_factor_min (gauge, labels: protocol)
  - cryo_flash_loan_fee_savings (counter)
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Dict, List, Optional

from enhanced_modules.common.base import EnhancedModule

logger = logging.getLogger(__name__)

# Prometheus metrics (lazy import)
_PROM_AVAILABLE = False
try:
    from prometheus_client import Counter, Gauge, Histogram, start_http_server
    _PROM_AVAILABLE = True
except ImportError:
    pass


@dataclass
class DashboardSnapshot:
    """Point-in-time snapshot of system performance."""
    timestamp: float = field(default_factory=time.time)
    total_profit_usd: float = 0.0
    total_gas_spent_usd: float = 0.0
    net_profit_usd: float = 0.0
    executions_total: int = 0
    executions_succeeded: int = 0
    executions_failed: int = 0
    success_rate: float = 0.0
    hourly_rate_usd: float = 0.0
    active_chains: int = 0
    queue_depth: int = 0
    # Per-chain breakdown
    chain_profits: Dict[int, float] = field(default_factory=dict)
    # Per-protocol breakdown
    protocol_profits: Dict[str, float] = field(default_factory=dict)
    # Top opportunities
    best_liquidation_usd: float = 0.0
    avg_profit_per_exec_usd: float = 0.0


class LiveDashboard(EnhancedModule):
    """
    Real-time performance dashboard with Prometheus metrics export
    and optional aiohttp JSON API.
    """

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        super().__init__("live_dashboard", config)
        self._snapshots: List[DashboardSnapshot] = []
        self._max_snapshots = 1000

        # Accumulators
        self._profit_by_chain: Dict[int, float] = {}
        self._profit_by_protocol: Dict[str, float] = {}
        self._gas_by_chain: Dict[int, float] = {}
        self._executions_by_status: Dict[str, int] = {"success": 0, "fail": 0}
        self._total_profit = 0.0
        self._total_gas = 0.0
        self._start_time = 0.0

        # Prometheus metrics
        self._prom_metrics: Dict[str, Any] = {}
        self._prom_port = int(self.config.get("prometheus_port", 9090))
        self._api_port = int(self.config.get("api_port", 8080))

    async def _on_start(self) -> None:
        self._start_time = time.time()

        if _PROM_AVAILABLE:
            self._init_prometheus()
            start_http_server(self._prom_port)
            logger.info("[Dashboard] Prometheus metrics on port %d", self._prom_port)

        logger.info("[Dashboard] Live dashboard started")

    async def _on_stop(self) -> None:
        snapshot = self._take_snapshot()
        logger.info(
            "[Dashboard] Final stats: net=$%.2f | %d executions | %.1f%% success",
            snapshot.net_profit_usd,
            snapshot.executions_total,
            snapshot.success_rate * 100,
        )

    # ── Prometheus Setup ───────────────────────────────────────

    def _init_prometheus(self) -> None:
        """Initialize Prometheus metrics."""
        self._prom_metrics = {
            "profit_total": Counter(
                "cryo_profit_total_usd",
                "Total profit in USD",
                ["chain_id", "protocol"],
            ),
            "gas_spent_total": Counter(
                "cryo_gas_spent_total_usd",
                "Total gas spent in USD",
                ["chain_id"],
            ),
            "executions_total": Counter(
                "cryo_executions_total",
                "Total executions",
                ["chain_id", "protocol", "status"],
            ),
            "success_rate": Gauge(
                "cryo_success_rate",
                "Current success rate",
            ),
            "active_positions": Gauge(
                "cryo_active_positions",
                "Number of monitored positions",
                ["chain_id"],
            ),
            "queue_depth": Gauge(
                "cryo_queue_depth",
                "Signal queue depth",
            ),
            "flash_loan_savings": Counter(
                "cryo_flash_loan_fee_savings_usd",
                "Flash loan fee savings from optimization",
            ),
            "profit_per_exec": Histogram(
                "cryo_profit_per_execution_usd",
                "Profit per execution",
                buckets=[0, 5, 10, 25, 50, 100, 250, 500, 1000, 5000],
            ),
        }

    # ── Event Recording ────────────────────────────────────────

    def record_execution(
        self,
        chain_id: int,
        protocol: str,
        success: bool,
        profit_usd: float = 0.0,
        gas_cost_usd: float = 0.0,
    ) -> None:
        """Record a liquidation execution."""
        status = "success" if success else "fail"
        self._executions_by_status[status] = self._executions_by_status.get(status, 0) + 1

        if success:
            self._total_profit += profit_usd
            self._profit_by_chain[chain_id] = self._profit_by_chain.get(chain_id, 0) + profit_usd
            self._profit_by_protocol[protocol] = self._profit_by_protocol.get(protocol, 0) + profit_usd

        self._total_gas += gas_cost_usd
        self._gas_by_chain[chain_id] = self._gas_by_chain.get(chain_id, 0) + gas_cost_usd

        # Update Prometheus
        if _PROM_AVAILABLE and self._prom_metrics:
            self._prom_metrics["executions_total"].labels(
                chain_id=str(chain_id), protocol=protocol, status=status
            ).inc()
            if success and profit_usd > 0:
                self._prom_metrics["profit_total"].labels(
                    chain_id=str(chain_id), protocol=protocol
                ).inc(profit_usd)
                self._prom_metrics["profit_per_exec"].observe(profit_usd)
            self._prom_metrics["gas_spent_total"].labels(chain_id=str(chain_id)).inc(gas_cost_usd)

            total_execs = sum(self._executions_by_status.values())
            success_rate = self._executions_by_status.get("success", 0) / max(1, total_execs)
            self._prom_metrics["success_rate"].set(success_rate)

    def record_queue_depth(self, depth: int) -> None:
        """Update the queue depth metric."""
        if _PROM_AVAILABLE and self._prom_metrics:
            self._prom_metrics["queue_depth"].set(depth)

    def record_fee_savings(self, savings_usd: float) -> None:
        """Record flash loan fee savings."""
        if _PROM_AVAILABLE and self._prom_metrics:
            self._prom_metrics["flash_loan_savings"].inc(savings_usd)

    # ── Snapshots ──────────────────────────────────────────────

    def _take_snapshot(self) -> DashboardSnapshot:
        """Take a point-in-time performance snapshot."""
        total_execs = sum(self._executions_by_status.values())
        successes = self._executions_by_status.get("success", 0)
        failures = self._executions_by_status.get("fail", 0)
        success_rate = successes / max(1, total_execs)
        net = self._total_profit - self._total_gas
        uptime_hrs = (time.time() - self._start_time) / 3600 if self._start_time else 1

        return DashboardSnapshot(
            total_profit_usd=self._total_profit,
            total_gas_spent_usd=self._total_gas,
            net_profit_usd=net,
            executions_total=total_execs,
            executions_succeeded=successes,
            executions_failed=failures,
            success_rate=success_rate,
            hourly_rate_usd=net / max(uptime_hrs, 0.01),
            active_chains=len(self._profit_by_chain),
            chain_profits=dict(self._profit_by_chain),
            protocol_profits=dict(self._profit_by_protocol),
            avg_profit_per_exec_usd=self._total_profit / max(1, successes),
        )

    def get_snapshot(self) -> Dict[str, Any]:
        """Get current snapshot as dict (for API)."""
        snap = self._take_snapshot()
        return {
            "timestamp": snap.timestamp,
            "net_profit_usd": round(snap.net_profit_usd, 2),
            "total_profit_usd": round(snap.total_profit_usd, 2),
            "total_gas_usd": round(snap.total_gas_spent_usd, 2),
            "executions": snap.executions_total,
            "success_rate": round(snap.success_rate * 100, 1),
            "hourly_rate_usd": round(snap.hourly_rate_usd, 2),
            "chain_profits": snap.chain_profits,
            "protocol_profits": snap.protocol_profits,
        }

    # ── API Server ─────────────────────────────────────────────

    async def start_api_server(self) -> None:
        """Start an aiohttp JSON API dashboard server."""
        try:
            from aiohttp import web

            app = web.Application()
            app.router.add_get("/api/status", self._handle_status)
            app.router.add_get("/api/snapshot", self._handle_snapshot)
            app.router.add_get("/api/chains", self._handle_chains)

            runner = web.AppRunner(app)
            await runner.setup()
            site = web.TCPSite(runner, "0.0.0.0", self._api_port)
            await site.start()
            logger.info("[Dashboard] API server on port %d", self._api_port)

        except ImportError:
            logger.warning("[Dashboard] aiohttp not available — API disabled")

    async def _handle_status(self, request: Any) -> Any:
        from aiohttp import web
        return web.json_response({"status": "running", "uptime": time.time() - self._start_time})

    async def _handle_snapshot(self, request: Any) -> Any:
        from aiohttp import web
        return web.json_response(self.get_snapshot())

    async def _handle_chains(self, request: Any) -> Any:
        from aiohttp import web
        return web.json_response({"chains": self._profit_by_chain})
