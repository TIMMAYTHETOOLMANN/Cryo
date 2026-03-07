#!/usr/bin/env python3
"""
╔══════════════════════════════════════════════════════════════════════════╗
║  CRYO — FULL CAPACITY DEPLOYMENT  (PERSISTENT SUPERVISOR v2)            ║
║                                                                          ║
║  Deploys EVERY engine, scanner, and module at maximum throughput.         ║
║  Auto-restarts crashed subsystems. Tracks benchmark metrics to disk.     ║
║                                                                          ║
║  What fires:                                                             ║
║    ✦ TriangulatedProfitEngine (12 vector scanner + flash + gas + ledger) ║
║    ✦ Module 1 Pipeline (stages 0-7: detection → analysis → execution)    ║
║    ✦ Module 9 Omni-Scope (5 detector arrays + ML ranker)                 ║
║    ✦ Module 10 RPC Gateway (enterprise load balancer, 8 chains)          ║
║    ✦ Module 11 Timing Engine (JIT execution + oracle + mempool)          ║
║    ✦ Enhanced Modules 1-8 (ML, flash loan aggregator, MEV, analytics)    ║
║    ✦ Signal Bridge + Execution Bridge (signal → on-chain TX)             ║
║    ✦ Zero-Revert Pipeline (oracle-reactive, no preflight)                ║
║    ✦ Flash Loan Router (Balancer 0%, Aave, DODO, Uni, Maker)             ║
║    ✦ Capital Multiplier (80% reinvestment, exponential compounding)       ║
║    ✦ MEV Protection (12 builders × 8 chains, private mempool agg)        ║
║    ✦ Cross-Chain Arbitrage (6-hop pathfinder, LayerZero + CCIP)          ║
║    ✦ NFT Liquidation Scanner (BendDAO, NFTfi, ParaSpace, Blend)          ║
║    ✦ Surplus Capital Deployment                                           ║
║                                                                          ║
║  Supervisor features:                                                     ║
║    ✦ Auto-restart any crashed subsystem (exponential backoff, max 60s)    ║
║    ✦ Benchmark tracker  → .benchmark_tracker.json (disk-persisted)        ║
║    ✦ Health heartbeat every 30s to console + log                          ║
║    ✦ Graceful shutdown on SIGINT / SIGTERM / KeyboardInterrupt            ║
║                                                                          ║
║  Configuration:                                                          ║
║    Scan interval:     0.5s global / 0.25s per L2                         ║
║    Reinvest rate:     80%                                                 ║
║    MEV builders:      12 (all relays, all chains)                         ║
║    Watchlist size:    2000 positions                                      ║
║    Signal queue:      20,000 capacity                                     ║
║    Min profit:        $0.50                                               ║
║    Execution:         LIVE                                                ║
║                                                                          ║
║  Usage:                                                                   ║
║    python deploy_full_capacity.py                                         ║
╚══════════════════════════════════════════════════════════════════════════╝
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
import signal
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Coroutine, Dict, Optional

# ── Encoding safety ──────────────────────────────────────────────────
os.environ['PYTHONIOENCODING'] = 'utf-8'
for _stream_name in ('stdout', 'stderr'):
    _stream = getattr(sys, _stream_name)
    try:
        _stream.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        import io
        try:
            setattr(sys, _stream_name,
                    io.TextIOWrapper(_stream.buffer, encoding='utf-8', errors='replace'))
        except Exception:
            pass

# ── Path & env ───────────────────────────────────────────────────────
PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_ROOT))

from dotenv import load_dotenv
load_dotenv(PROJECT_ROOT / ".env")


# ════════════════════════════════════════════════════════════════════
#  LOGGING
# ════════════════════════════════════════════════════════════════════

def setup_logging():
    fmt = logging.Formatter(
        "%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    root = logging.getLogger()
    root.setLevel(getattr(logging, os.getenv("LOG_LEVEL", "INFO")))

    sh = logging.StreamHandler(sys.stdout)
    sh.setLevel(logging.INFO)
    sh.setFormatter(fmt)
    root.addHandler(sh)

    log_file = os.getenv("LOG_FILE", "cryo_full_capacity.log")
    fh = logging.FileHandler(log_file, encoding="utf-8")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(fmt)
    root.addHandler(fh)


logger = logging.getLogger("deploy_full_capacity")


# ════════════════════════════════════════════════════════════════════
#  BENCHMARK TRACKER  (persisted to .benchmark_tracker.json)
# ════════════════════════════════════════════════════════════════════

BENCHMARK_FILE = PROJECT_ROOT / ".benchmark_tracker.json"


class BenchmarkTracker:
    """
    Tracks cumulative runtime metrics across restarts.
    Persists to disk every flush so nothing is lost on crash.
    """

    def __init__(self, path: Path = BENCHMARK_FILE):
        self._path = path
        self._data: Dict[str, Any] = self._load()

    # ── persistence ──────────────────────────────────────────────
    def _load(self) -> Dict[str, Any]:
        if self._path.exists():
            try:
                return json.loads(self._path.read_text(encoding="utf-8"))
            except Exception:
                pass
        return self._default()

    def _default(self) -> Dict[str, Any]:
        return {
            "first_boot": datetime.now(timezone.utc).isoformat(),
            "last_boot": datetime.now(timezone.utc).isoformat(),
            "total_boots": 0,
            "total_uptime_s": 0.0,
            "subsystem_restarts": {},
            "subsystem_errors": {},
            "cycles_completed": 0,
            "opportunities_found": 0,
            "executions_attempted": 0,
            "executions_succeeded": 0,
            "total_profit_usd": 0.0,
            "peak_profit_usd": 0.0,
        }

    def flush(self):
        try:
            self._path.write_text(
                json.dumps(self._data, indent=2, default=str),
                encoding="utf-8",
            )
        except Exception as exc:
            logger.warning("Benchmark flush failed: %s", exc)

    # ── mutators ─────────────────────────────────────────────────
    def record_boot(self):
        self._data["total_boots"] += 1
        self._data["last_boot"] = datetime.now(timezone.utc).isoformat()
        self.flush()

    def record_uptime(self, seconds: float):
        self._data["total_uptime_s"] += seconds
        self.flush()

    def record_subsystem_restart(self, name: str, error: str):
        restarts = self._data.setdefault("subsystem_restarts", {})
        restarts[name] = restarts.get(name, 0) + 1
        errors = self._data.setdefault("subsystem_errors", {})
        errors[name] = error[:500]
        self.flush()

    def record_cycle(self):
        self._data["cycles_completed"] += 1

    def record_opportunity(self):
        self._data["opportunities_found"] += 1

    def record_execution(self, succeeded: bool, profit_usd: float = 0.0):
        self._data["executions_attempted"] += 1
        if succeeded:
            self._data["executions_succeeded"] += 1
            self._data["total_profit_usd"] += profit_usd
            if profit_usd > self._data.get("peak_profit_usd", 0.0):
                self._data["peak_profit_usd"] = profit_usd

    def summary(self) -> str:
        d = self._data
        uptime_h = d["total_uptime_s"] / 3600
        restarts_total = sum(d.get("subsystem_restarts", {}).values())
        return (
            f"boots={d['total_boots']}  uptime={uptime_h:.2f}h  "
            f"cycles={d['cycles_completed']}  "
            f"opps={d['opportunities_found']}  "
            f"exec={d['executions_succeeded']}/{d['executions_attempted']}  "
            f"profit=${d['total_profit_usd']:.4f}  "
            f"peak=${d['peak_profit_usd']:.4f}  "
            f"sub_restarts={restarts_total}"
        )

    @property
    def data(self) -> Dict[str, Any]:
        return dict(self._data)


# ════════════════════════════════════════════════════════════════════
#  PRE-DEPLOYMENT VERIFICATION
# ════════════════════════════════════════════════════════════════════

def verify_deployment_readiness() -> bool:
    """Verify all critical environment variables are set before deployment."""
    print()
    print("=" * 72)
    print("  FULL CAPACITY DEPLOYMENT -- PRE-FLIGHT CHECK")
    print("=" * 72)

    checks = {
        "PRIVATE_KEY": bool(os.getenv("PRIVATE_KEY")),
        "TREASURY_ADDRESS": bool(os.getenv("TREASURY_ADDRESS")),
        "EXECUTION_ENABLED": os.getenv("EXECUTION_ENABLED", "").lower() == "true",
        "MAINNET_RPC_URL / ETH_RPC_URL": bool(os.getenv("MAINNET_RPC_URL") or os.getenv("ETH_RPC_URL")),
        "ARBITRUM_RPC_URL": bool(os.getenv("ARBITRUM_RPC_URL")),
        "OPTIMISM_RPC_URL": bool(os.getenv("OPTIMISM_RPC_URL")),
        "POLYGON_RPC_URL": bool(os.getenv("POLYGON_RPC_URL")),
        "BASE_RPC_URL": bool(os.getenv("BASE_RPC_URL")),
        "AVALANCHE_RPC_URL": bool(os.getenv("AVALANCHE_RPC_URL")),
        "BSC_RPC_URL": bool(os.getenv("BSC_RPC_URL")),
        "ZKSYNC_RPC_URL": bool(os.getenv("ZKSYNC_RPC_URL")),
        "LIQUIDATION_EXECUTOR_V1": bool(os.getenv("LIQUIDATION_EXECUTOR_V1")),
        "LIQUIDATION_EXECUTOR_V2": bool(os.getenv("LIQUIDATION_EXECUTOR_V2")),
        "FLASH_EXECUTOR": bool(os.getenv("FLASH_EXECUTOR")),
    }

    all_pass = True
    for name, ok in checks.items():
        status = "PASS" if ok else "FAIL"
        icon = "+" if ok else "!"
        print(f"  [{icon}] {name:40s} {status}")
        if not ok and name in ("PRIVATE_KEY", "TREASURY_ADDRESS", "MAINNET_RPC_URL / ETH_RPC_URL"):
            all_pass = False

    # Report configuration
    reinvest = float(os.getenv("REINVEST_RATE", "0.80"))
    scan_interval = float(os.getenv("SCAN_INTERVAL_SECONDS", "0.5"))
    builders = os.getenv("FLASHBOTS_BUILDER_URLS", "")
    n_builders = len([b for b in builders.split(",") if b.strip()])

    print()
    print("  -- DEPLOYMENT CONFIG --")
    print(f"  Execution Mode:    LIVE")
    print(f"  Reinvest Rate:     {reinvest * 100:.0f}%")
    print(f"  Scan Interval:     {scan_interval}s")
    print(f"  MEV Builders:      {n_builders}")
    print(f"  Min Profit:        ${os.getenv('MIN_PROFIT_USD', '0.50')}")
    print(f"  Treasury:          {os.getenv('TREASURY_ADDRESS', 'NOT SET')}")
    print(f"  Watchlist:         {os.getenv('MAX_WATCHLIST_SIZE', '2000')} positions")
    print("=" * 72)
    print()

    return all_pass


# ════════════════════════════════════════════════════════════════════
#  SUPERVISED SUBSYSTEM RUNNER  (auto-restart with backoff)
# ════════════════════════════════════════════════════════════════════

class SupervisedSubsystem:
    """
    Wraps an async coroutine-factory with auto-restart + exponential backoff.
    If the coroutine exits or raises, the supervisor waits and respawns it.
    """

    MAX_BACKOFF_S = 60.0

    def __init__(
        self,
        name: str,
        factory: Callable[[], Coroutine],
        benchmark: BenchmarkTracker,
        shutdown_event: asyncio.Event,
    ):
        self.name = name
        self._factory = factory
        self._benchmark = benchmark
        self._shutdown = shutdown_event
        self._consecutive_failures = 0
        self._task: Optional[asyncio.Task] = None

    async def run_forever(self):
        """Supervisor loop -- keeps respawning the subsystem until shutdown."""
        while not self._shutdown.is_set():
            try:
                logger.info("[supervisor] Starting subsystem: %s", self.name)
                self._task = asyncio.current_task()
                await self._factory()
                # If it exits cleanly, still restart unless shutdown
                if not self._shutdown.is_set():
                    logger.warning("[supervisor] %s exited cleanly -- restarting", self.name)
                    self._consecutive_failures = 0
            except asyncio.CancelledError:
                logger.info("[supervisor] %s cancelled", self.name)
                return
            except Exception as exc:
                self._consecutive_failures += 1
                err_msg = f"{type(exc).__name__}: {exc}"
                logger.error(
                    "[supervisor] %s crashed (#%d): %s",
                    self.name, self._consecutive_failures, err_msg,
                )
                logger.debug(traceback.format_exc())
                self._benchmark.record_subsystem_restart(self.name, err_msg)

            if self._shutdown.is_set():
                return

            # Exponential backoff: 1s, 2s, 4s, 8s ... 60s cap
            delay = min(2 ** (self._consecutive_failures - 1), self.MAX_BACKOFF_S)
            logger.info("[supervisor] Restarting %s in %.1fs", self.name, delay)
            try:
                await asyncio.wait_for(self._shutdown.wait(), timeout=delay)
                return  # shutdown requested during backoff
            except asyncio.TimeoutError:
                pass  # backoff elapsed, loop back


# ════════════════════════════════════════════════════════════════════
#  HEALTH HEARTBEAT
# ════════════════════════════════════════════════════════════════════

async def heartbeat_loop(
    benchmark: BenchmarkTracker,
    shutdown_event: asyncio.Event,
    interval: float = 30.0,
    boot_time: float = 0.0,
):
    """Print health status every `interval` seconds and flush benchmarks."""
    while not shutdown_event.is_set():
        try:
            await asyncio.wait_for(shutdown_event.wait(), timeout=interval)
            break
        except asyncio.TimeoutError:
            pass

        uptime_s = time.monotonic() - boot_time
        benchmark.record_uptime(interval)
        benchmark.flush()
        logger.info(
            "[heartbeat] uptime=%.0fs  %s",
            uptime_s, benchmark.summary(),
        )


# ════════════════════════════════════════════════════════════════════
#  DUAL ENGINE DEPLOYMENT  (with persistent supervisor)
# ════════════════════════════════════════════════════════════════════

async def deploy_full_capacity():
    """
    Deploy the COMPLETE system at full capacity with persistent supervision:

    1. TriangulatedProfitEngine  -- 12-vector scanner + flash loan + gas + ledger
    2. MasterProfitOrchestrator  -- all 9+ concurrent extraction loops

    Both are wrapped in SupervisedSubsystem runners that auto-restart on
    crash with exponential backoff.  A heartbeat loop flushes benchmark
    metrics to .benchmark_tracker.json every 30 seconds.
    """
    from MODULE_1_LIQUIDATION_ENGINE.profit_core.triangulated_profit_engine import (
        TriangulatedProfitEngine, build_default_config,
    )
    from master_orchestrator import MasterProfitOrchestrator

    # ── Benchmark tracker ──────────────────────────────────────
    benchmark = BenchmarkTracker()
    benchmark.record_boot()
    boot_time = time.monotonic()

    # ── Build engine config (full capacity) ────────────────────
    config = build_default_config()

    # Force full execution mode
    config["execution"]["scan_only_mode"] = False
    config["wallet"]["execution_enabled"] = True

    # Maximum aggression for Phase 1 cold start
    config["scanner"]["min_profit_usd"] = 0.01
    config["scanner"]["min_confidence"] = 0.10
    config["scanner"]["scan_interval"] = 0.5
    config["gas"]["min_margin_percent"] = 0.01
    config["gas"]["min_margin_usd"] = 0.01
    config["execution"]["liquidation"]["min_profit_usd"] = 0.01

    # 80% reinvest
    config["multiplier"]["reinvest_rate"] = 0.80
    config["multiplier"]["trades_per_hour"] = 120

    print()
    print("=" * 72)
    print("  CRYO -- FULL CAPACITY DEPLOYMENT  (PERSISTENT SUPERVISOR v2)")
    print("  All engines firing. All modules live. Auto-restart enabled.")
    print("=" * 72)
    print(f"  Started:       {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"  Mode:          FULL EXECUTION")
    print(f"  Reinvest:      80%")
    print(f"  Scan Speed:    0.5s global / 0.25s per L2")
    print(f"  MEV Builders:  {len(config.get('mev', {}).get('flashbots_builder_urls', []))}")
    print(f"  Chains:        {len(config.get('rpc', {}))}")
    print(f"  Treasury:      {config.get('wallet', {}).get('treasury_address', 'NOT SET')}")
    print(f"  Benchmark:     {BENCHMARK_FILE}")
    print(f"  Boot #:        {benchmark.data['total_boots']}")
    print("=" * 72)
    print()

    # ── Construct subsystem objects ────────────────────────────
    engine = TriangulatedProfitEngine(config)
    orchestrator = MasterProfitOrchestrator({
        "scan_only": False,
        "cycle_interval": 1.0,
        "min_profit_usd": 0.50,
        "execution_enabled": True,
        "reinvest_rate": 0.80,
    })

    # ── Shutdown coordination ─────────────────────────────────
    shutdown_event = asyncio.Event()

    def _signal_handler(*_):
        print("\n\n  +===============================+")
        print("  |  SHUTDOWN SIGNAL RECEIVED     |")
        print("  +===============================+")
        shutdown_event.set()

    loop = asyncio.get_event_loop()
    try:
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, _signal_handler)
    except (NotImplementedError, AttributeError):
        pass  # Windows fallback -- KeyboardInterrupt still works

    # ── Build supervised runners ──────────────────────────────
    engine_sup = SupervisedSubsystem(
        "triangulated_engine", engine.start, benchmark, shutdown_event,
    )
    orchestrator_sup = SupervisedSubsystem(
        "master_orchestrator", orchestrator.run, benchmark, shutdown_event,
    )

    # ── Fire everything ───────────────────────────────────────
    tasks = [
        asyncio.create_task(engine_sup.run_forever(), name="sup:engine"),
        asyncio.create_task(orchestrator_sup.run_forever(), name="sup:orchestrator"),
        asyncio.create_task(
            heartbeat_loop(benchmark, shutdown_event, interval=30.0, boot_time=boot_time),
            name="heartbeat",
        ),
        asyncio.create_task(shutdown_event.wait(), name="shutdown_watcher"),
    ]

    try:
        done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)

        for task in done:
            if task.get_name() == "shutdown_watcher":
                logger.info("Shutdown requested by user")
            elif task.exception() and not isinstance(task.exception(), asyncio.CancelledError):
                logger.error("Task %s failed: %s", task.get_name(), task.exception())

    except KeyboardInterrupt:
        logger.info("KeyboardInterrupt received")
    finally:
        shutdown_event.set()
        print("\n  Shutting down all engines...")

        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

        # Graceful stop hooks
        for obj, name in [(engine, "engine"), (orchestrator, "orchestrator")]:
            try:
                await obj.stop()
            except Exception as exc:
                logger.warning("Error stopping %s: %s", name, exc)

        # Final benchmark flush
        final_uptime = time.monotonic() - boot_time
        benchmark.record_uptime(final_uptime % 30)  # remaining since last heartbeat
        benchmark.flush()

        print()
        print("  -- FINAL BENCHMARK --")
        print(f"  {benchmark.summary()}")
        print(f"  Persisted to: {BENCHMARK_FILE}")
        print("\n  All engines stopped. Deployment complete.")

    return 0


# ════════════════════════════════════════════════════════════════════
#  MAIN
# ════════════════════════════════════════════════════════════════════

def main():
    setup_logging()

    print()
    print("+==================================================================+")
    print("|  CRYO FULL CAPACITY DEPLOYMENT SYSTEM  (PERSISTENT SUPERVISOR)   |")
    print("|  Every engine. Every scanner. Every module. 100%.                |")
    print("|  Auto-restart. Benchmark tracking. Zero downtime.                |")
    print("+==================================================================+")
    print()

    # Pre-flight
    ready = verify_deployment_readiness()
    if not ready:
        print("  CRITICAL: Missing required configuration.")
        print("  Set PRIVATE_KEY, TREASURY_ADDRESS, and at least one RPC URL in .env")
        print("  Then run again.")
        sys.exit(1)

    print("  Pre-flight PASSED. Deploying full capacity in 3 seconds...")
    print()
    time.sleep(3)

    try:
        result = asyncio.run(deploy_full_capacity())
        sys.exit(result or 0)
    except KeyboardInterrupt:
        print("\n  Deployment terminated.")
        sys.exit(0)


if __name__ == "__main__":
    main()
