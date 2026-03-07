#!/usr/bin/env python3
"""
CORE V2 MAIN — Entry Point
============================
Wires SubgraphIndexer -> UnifiedPipeline -> PositionWatchlist
into a single runnable system.

Usage:
    python -m core_v2.main                  # Full system
    python -m core_v2.main --scan-only      # One-shot scan (no monitoring)
    python -m core_v2.main --dry-run        # Scan + pipeline gates, no execution
"""

from __future__ import annotations

import asyncio
import argparse
import json
import logging
import signal
import sys
import os

# Ensure project root is on path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core_v2.subgraph_indexer import SubgraphIndexer
from core_v2.unified_pipeline import UnifiedPipeline
from core_v2.position_watchlist import PositionWatchlist


# =====================================================================
# LOGGING
# =====================================================================

def setup_logging(level: str = "INFO"):
    """Configure structured logging."""
    log_format = (
        "%(asctime)s | %(levelname)-7s | %(name)-24s | %(message)s"
    )
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format=log_format,
        datefmt="%H:%M:%S",
    )
    # Quiet down noisy libraries
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("web3").setLevel(logging.WARNING)
    logging.getLogger("asyncio").setLevel(logging.WARNING)


logger = logging.getLogger("core_v2.main")


# =====================================================================
# MAIN SYSTEM
# =====================================================================

class CryoNitroV2:
    """
    Top-level orchestrator for the three-module stack.

    Lifecycle:
        1. Initialize all three modules
        2. Run initial full scan via SubgraphIndexer
        3. Feed candidates into PositionWatchlist
        4. Start continuous monitoring

    The watchlist's oracle reactor and block verifier
    automatically feed triggered positions into the pipeline.
    """

    def __init__(self, config: dict = None):
        self.config = config or {}

        # Modules (initialized in start())
        self.indexer: SubgraphIndexer | None = None
        self.pipeline: UnifiedPipeline | None = None
        self.watchlist: PositionWatchlist | None = None

        self._shutdown_event = asyncio.Event()

    async def start(self, mode: str = "full"):
        """
        Start the system.

        Modes:
            'full'      -- Full monitoring loop
            'scan-only' -- One-shot scan, print results, exit
            'dry-run'   -- Scan + pipeline gates (no execution)
        """
        logger.info("=" * 60)
        logger.info("  CRYO-NITRO v2.0 -- Unified Execution System")
        logger.info("=" * 60)
        logger.info(f"  Mode: {mode}")
        logger.info("")

        # -- Initialize Modules --
        logger.info("Initializing SubgraphIndexer...")
        self.indexer = SubgraphIndexer(self.config.get("indexer", {}))
        await self.indexer.initialize()

        logger.info("Initializing UnifiedPipeline...")
        pipeline_config = self.config.get("pipeline", {})
        if mode == "dry-run":
            # In dry-run, don't actually send transactions
            pipeline_config["private_key"] = ""
        self.pipeline = UnifiedPipeline(pipeline_config)
        await self.pipeline.initialize()

        logger.info("Initializing PositionWatchlist...")
        self.watchlist = PositionWatchlist(
            self.config.get("watchlist", {}),
            pipeline=self.pipeline,
        )
        await self.watchlist.initialize()

        # -- Initial Scan --
        logger.info("")
        logger.info("Running initial scan...")
        candidates = await self.indexer.full_scan()

        if not candidates:
            logger.info("No candidates found in initial scan.")
            if mode == "scan-only":
                return

        # Print scan results
        self._print_scan_results(candidates)

        if mode == "scan-only":
            await self._shutdown()
            return

        # -- Feed into Watchlist --
        added = self.watchlist.ingest(candidates)
        logger.info(f"Watchlist populated: {added} positions being monitored")

        if mode == "dry-run":
            # Run one pipeline pass without execution
            logger.info("")
            logger.info("Dry-run: processing through pipeline gates...")
            results = await self.pipeline.process_batch(candidates[:20])
            self._print_pipeline_results(results)
            await self._shutdown()
            return

        # -- Start Continuous Monitoring --
        logger.info("")
        logger.info("Starting continuous monitoring...")
        logger.info("Press Ctrl+C to stop")
        logger.info("")

        try:
            await asyncio.gather(
                self._indexer_refresh_loop(),
                self.watchlist.start(),
                self._stats_reporter_loop(),
                return_exceptions=True,
            )
        except asyncio.CancelledError:
            pass
        finally:
            await self._shutdown()

    async def _indexer_refresh_loop(self):
        """Periodically re-scan subgraphs and update watchlist."""
        refresh_interval = self.config.get("refresh_interval", 60)

        while not self._shutdown_event.is_set():
            await asyncio.sleep(refresh_interval)

            try:
                candidates = await self.indexer.full_scan()
                if candidates and self.watchlist:
                    self.watchlist.ingest(candidates)
            except Exception as e:
                logger.error(f"Refresh cycle error: {e}")

    async def _stats_reporter_loop(self):
        """Periodically log system stats."""
        interval = self.config.get("stats_interval", 120)  # Every 2 min

        while not self._shutdown_event.is_set():
            await asyncio.sleep(interval)
            self._print_system_stats()

    async def _shutdown(self):
        """Graceful shutdown."""
        logger.info("Shutting down...")
        self._shutdown_event.set()

        if self.watchlist:
            await self.watchlist.shutdown()
        if self.pipeline:
            await self.pipeline.shutdown()
        if self.indexer:
            await self.indexer.shutdown()

        logger.info("Shutdown complete.")

    # -- Display Methods --------------------------------------------

    def _print_scan_results(self, candidates: list):
        """Print scan results in a readable format."""
        logger.info("")
        logger.info("-" * 60)
        logger.info(f"  SCAN RESULTS: {len(candidates)} candidates")
        logger.info("-" * 60)

        if not candidates:
            return

        # Group by chain
        by_chain: dict[int, list] = {}
        for c in candidates:
            chain = c.chain_id
            if chain not in by_chain:
                by_chain[chain] = []
            by_chain[chain].append(c)

        chain_names = {
            1: "Ethereum", 42161: "Arbitrum", 10: "Optimism",
            137: "Polygon", 8453: "Base", 43114: "Avalanche",
        }

        for chain_id, chain_candidates in sorted(by_chain.items()):
            name = chain_names.get(chain_id, str(chain_id))
            logger.info("")
            logger.info(f"  {name}: {len(chain_candidates)} positions")

            for c in chain_candidates[:5]:  # Top 5 per chain
                status = (
                    "LIQUIDATABLE" if c.health_factor < 1.0 else "APPROACHING"
                )
                logger.info(
                    f"    [{status}] HF={c.health_factor:.4f} "
                    f"debt=${c.total_debt_usd:,.0f} "
                    f"profit~${c.estimated_gross_profit_usd:.2f} "
                    f"user={c.user_address[:10]}..."
                )

            if len(chain_candidates) > 5:
                logger.info(f"    ... and {len(chain_candidates) - 5} more")

        # Summary
        liquidatable = sum(
            1 for c in candidates if c.health_factor < 1.0
        )
        approaching = sum(
            1 for c in candidates if 1.0 <= c.health_factor < 1.05
        )
        total_profit = sum(
            c.estimated_gross_profit_usd
            for c in candidates
            if c.health_factor < 1.0
        )

        logger.info("")
        logger.info("  Summary:")
        logger.info(f"    Liquidatable now:    {liquidatable}")
        logger.info(f"    Approaching (<1.05): {approaching}")
        logger.info(f"    Est. total profit:   ${total_profit:,.2f}")
        logger.info("-" * 60)

    def _print_pipeline_results(self, results: list):
        """Print pipeline results."""
        logger.info("")
        logger.info("-" * 60)
        logger.info(f"  PIPELINE RESULTS: {len(results)} processed")
        logger.info("-" * 60)

        for r in results[:20]:
            tier = r.execution_tier.value if r.execution_tier else "-"
            rejection = (
                r.rejection_reason.value if r.rejection_reason else "-"
            )
            logger.info(
                f"  {r.stage.value:<20} | tier={tier:<10} | "
                f"HF={r.health_factor:.4f} | net=${r.net_profit_usd:.2f} | "
                f"reject={rejection}"
            )

        if self.pipeline:
            stats = self.pipeline.get_stats()
            logger.info("")
            logger.info(f"  Pipeline stats: {json.dumps(stats, indent=2)}")

    def _print_system_stats(self):
        """Log system-wide stats."""
        logger.info(f"{'-' * 40} STATS {'-' * 40}")

        if self.indexer:
            idx_stats = self.indexer.get_stats()
            logger.info(
                f"  Indexer: {idx_stats['cached_positions']} cached, "
                f"{idx_stats['total_queries']} queries, "
                f"{idx_stats['queries_failed']} failed"
            )

        if self.pipeline:
            p_stats = self.pipeline.get_stats()
            breaker = "OPEN" if p_stats["circuit_breaker_open"] else "closed"
            logger.info(
                f"  Pipeline: {p_stats['total_ingested']} ingested, "
                f"{p_stats['confirmed']} confirmed, "
                f"${p_stats['total_profit_usd']:.2f} profit, "
                f"breaker={breaker}"
            )

        if self.watchlist:
            w_stats = self.watchlist.get_stats()
            logger.info(
                f"  Watchlist: {w_stats['total_watched']} watching, "
                f"{w_stats['triggered']} triggered, "
                f"{w_stats['oracle_events']} oracle events"
            )

        logger.info(f"{'-' * 86}")


# =====================================================================
# CLI
# =====================================================================

def parse_args():
    parser = argparse.ArgumentParser(
        description="Cryo-Nitro v2 -- Unified Execution System"
    )
    parser.add_argument(
        "--scan-only", action="store_true",
        help="Run one scan and exit (no monitoring)",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Scan + pipeline gates, but don't execute transactions",
    )
    parser.add_argument(
        "--log-level", default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Logging level",
    )
    parser.add_argument(
        "--chains", nargs="+", type=int, default=None,
        help="Specific chain IDs to scan (default: all)",
    )
    parser.add_argument(
        "--min-debt", type=float, default=100.0,
        help="Minimum debt size in USD (default: $100)",
    )
    parser.add_argument(
        "--hf-threshold", type=float, default=1.10,
        help="Health factor threshold for scanning (default: 1.10)",
    )
    return parser.parse_args()


async def main():
    args = parse_args()
    setup_logging(args.log_level)

    # Build config from args
    config: dict = {
        "indexer": {
            "hf_threshold": args.hf_threshold,
            "min_debt_usd": args.min_debt,
        },
        "pipeline": {},
        "watchlist": {},
    }

    if args.chains:
        config["indexer"]["enabled_chains"] = args.chains

    # Determine mode
    if args.scan_only:
        mode = "scan-only"
    elif args.dry_run:
        mode = "dry-run"
    else:
        mode = "full"

    # Run
    system = CryoNitroV2(config)

    # Handle Ctrl+C gracefully (cross-platform)
    loop = asyncio.get_event_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, lambda: system._shutdown_event.set())
        except NotImplementedError:
            pass  # Windows -- KeyboardInterrupt will propagate instead

    try:
        await system.start(mode)
    except KeyboardInterrupt:
        logger.info("Interrupted.")
        await system._shutdown()


if __name__ == "__main__":
    asyncio.run(main())
