#!/usr/bin/env python3
"""
hub.recon_coordinator — Unified Reconnaissance Coordinator
=============================================================
Ties together **all** reconnaissance and analysis subsystems into a
single coordinator that can be started, stopped, and queried through
one interface.

Subsystems managed:
  - Mempool Radar (signal merger, advanced filter)
  - Contract Crawler (protocol classifier, deployer monitor, clone
    detector, funding intelligence, KOL tracker, social mindshare)
  - Static Analyzer (vulnerability scanner, MEV pattern matcher,
    liquidation formula extractor)
  - Cross-Chain Monitor (bridge registry, multi-chain graph,
    N-hop pathfinder, liquidity tracker, atomic arb detector)
  - Archive Indexer (deep crawl for serial liquidatees)
  - Alpha Seeker (sentiment + governance)
  - ML Ranker (quality scorer, competition estimator, complexity
    analyzer, dynamic router)
  - Opportunity Scanner (6-vector profit scanner)

The coordinator feeds all discovered signals into a shared results
list that higher-level deployment strategies can consume.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .registry import ModuleRegistry

logger = logging.getLogger(__name__)


@dataclass
class ReconResult:
    """A single result emitted by any recon subsystem."""

    source: str               # e.g. "vulnerability_scanner"
    category: str             # e.g. "recon", "detection"
    summary: str
    confidence: float = 0.0
    estimated_value_usd: float = 0.0
    chain_id: int = 1
    target: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)


class ReconCoordinator:
    """
    Unified reconnaissance coordinator.

    Accepts a :class:`ModuleRegistry` (already discovered) and
    orchestrates initialization and parallel scanning across all
    recon + detection + analysis modules.

    Usage::

        reg = ModuleRegistry().discover()
        coord = ReconCoordinator(reg)
        await coord.initialize()
        results = await coord.run_sweep(chain_ids=[1, 42161])
        coord.print_report(results)
    """

    def __init__(self, registry: ModuleRegistry):
        self._registry = registry
        self._initialized = False
        self._results: List[ReconResult] = []

    # ── Lifecycle ──────────────────────────────────────────────

    async def initialize(self) -> None:
        """Import and instantiate all recon-related modules."""
        for cat in ("recon", "detection", "analysis"):
            self._registry.initialize_category(cat)
        self._initialized = True
        logger.info("ReconCoordinator initialized")

    # ── Sweep ──────────────────────────────────────────────────

    async def run_sweep(
        self,
        chain_ids: Optional[List[int]] = None,
        timeout: float = 30.0,
    ) -> List[ReconResult]:
        """
        Run a single recon sweep across all initialized modules.

        Each module's scan is wrapped in an individual timeout so a
        single slow subsystem doesn't block the rest.

        Returns accumulated :class:`ReconResult` list.
        """
        if not self._initialized:
            await self.initialize()

        chain_ids = chain_ids or [1]  # default: Ethereum mainnet

        tasks = []
        for info in self._registry.list_modules():
            if info.category not in ("recon", "detection", "analysis"):
                continue
            if not info.is_available():
                continue
            tasks.append(
                asyncio.create_task(
                    self._probe_module(info.name, info.instance, chain_ids, timeout)
                )
            )

        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

        return list(self._results)

    async def _probe_module(
        self,
        name: str,
        instance: Any,
        chain_ids: List[int],
        timeout: float,
    ) -> None:
        """Probe a single module and append results."""
        try:
            async with asyncio.timeout(timeout):
                # Modules expose heterogeneous APIs; we normalise here.
                if hasattr(instance, "get_stats"):
                    stats = instance.get_stats() if not asyncio.iscoroutinefunction(
                        getattr(instance, "get_stats", None)
                    ) else await instance.get_stats()
                    self._results.append(ReconResult(
                        source=name,
                        category="stats",
                        summary=f"{name} stats: {_summarise(stats)}",
                        metadata=stats if isinstance(stats, dict) else {"raw": str(stats)},
                    ))
                else:
                    self._results.append(ReconResult(
                        source=name,
                        category="available",
                        summary=f"{name} ready (no get_stats)",
                    ))
        except asyncio.TimeoutError:
            self._results.append(ReconResult(
                source=name,
                category="timeout",
                summary=f"{name} timed out after {timeout}s",
            ))
        except Exception as exc:
            self._results.append(ReconResult(
                source=name,
                category="error",
                summary=f"{name} error: {exc}",
            ))

    # ── Reporting ──────────────────────────────────────────────

    def print_report(self, results: Optional[List[ReconResult]] = None) -> None:
        """Print a human-readable sweep report to stdout."""
        results = results or self._results
        print()
        print("=" * 72)
        print("  RECON SWEEP REPORT")
        print("=" * 72)
        print(f"  Results: {len(results)}")
        print()
        for r in results:
            icon = {"stats": "+", "available": "+", "timeout": "!", "error": "X"}.get(
                r.category, "?"
            )
            print(f"  [{icon}] {r.source:30s}  {r.summary[:60]}")
        print()
        print("=" * 72)

    def get_results(self) -> List[ReconResult]:
        return list(self._results)

    def clear(self) -> None:
        self._results.clear()


# ── Helpers ────────────────────────────────────────────────────────

def _summarise(stats: Any, max_len: int = 50) -> str:
    """Produce a short summary string from a stats dict or object."""
    if isinstance(stats, dict):
        parts = []
        for k, v in list(stats.items())[:4]:
            parts.append(f"{k}={v}")
        return ", ".join(parts)
    return str(stats)[:max_len]
