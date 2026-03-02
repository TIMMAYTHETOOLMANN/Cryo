#!/usr/bin/env python3
"""
DEPLOYMENT LAUNCHER — Zero-Capital Flash Loan Liquidation Engine
===================================================================
Single-command launcher for production deployment.

Usage:
    python deploy.py                    # Full system (Module 1 + Module 9)
    python deploy.py --preflight        # Preflight checks only
    python deploy.py --gas              # Gas report only
    python deploy.py --scan-only        # Scan mode (Stages 0-2 only, no execution)
    python deploy.py --omni-test        # Test Module 9 Omni-Scope arrays
"""

import asyncio
import logging
import os
import sys
import time
from pathlib import Path

# Force UTF-8 output for Windows
if sys.platform == "win32":
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

# Ensure project root is on path
PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_ROOT))

from dotenv import load_dotenv
load_dotenv(PROJECT_ROOT / ".env")


def setup_logging():
    log_file = os.getenv("LOG_FILE", "module1_liquidation_engine.log")
    
    # File handler with immediate flush
    fh = logging.FileHandler(log_file, mode="a")
    fh.setLevel(logging.INFO)
    fh.setFormatter(logging.Formatter(
        "%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    ))
    
    # Stream handler
    sh = logging.StreamHandler(sys.stdout)
    sh.setLevel(logging.INFO)
    sh.setFormatter(logging.Formatter(
        "%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    ))
    
    root = logging.getLogger()
    root.setLevel(getattr(logging, os.getenv("LOG_LEVEL", "INFO")))
    root.addHandler(fh)
    root.addHandler(sh)
    
    # Force flush on every write
    class FlushHandler(logging.StreamHandler):
        def emit(self, record):
            super().emit(record)
            self.flush()
    
    # Replace file handler with flushing version (UTF-8)
    root.removeHandler(fh)
    ffh = FlushHandler(open(log_file, "a", encoding="utf-8", errors="replace"))
    ffh.setLevel(logging.INFO)
    ffh.setFormatter(logging.Formatter(
        "%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    ))
    root.addHandler(ffh)


def print_banner():
    print()
    print("=" * 80)
    print("  CRYO1 -- ZERO-CAPITAL FLASH LOAN LIQUIDATION ENGINE")
    print("  Module 1: Stage-Gated Execution Pipeline")
    print("  Module 9: Omni-Scope Triangulation Engine")
    print("=" * 80)
    print()


async def run_preflight():
    from MODULE_1_LIQUIDATION_ENGINE.config.settings import get_config
    from MODULE_1_LIQUIDATION_ENGINE.stage_0_preflight.system_validator import SystemValidator
    cfg = get_config()
    validator = SystemValidator(cfg)
    report = await validator.run()
    SystemValidator.print_report(report)
    return report


async def run_gas_report():
    from MODULE_1_LIQUIDATION_ENGINE.config.settings import get_config
    from MODULE_1_LIQUIDATION_ENGINE.stage_3_execution.gas_manager import GasManager
    cfg = get_config()
    gm = GasManager(cfg)
    report = gm.full_report()
    GasManager.print_report(report)


async def run_omni_test():
    print("Running Module 9 Omni-Scope diagnostic test…\n")
    # Import and run the test from MODULE_9_OMNI_SCOPE.main
    from MODULE_9_OMNI_SCOPE.main import _test
    await _test()


async def run_full_system():
    """Launch the complete system: Module 1 pipeline + Module 9 Omni-Scope."""
    from MODULE_1_LIQUIDATION_ENGINE.pipeline import Pipeline

    print_banner()

    # Summary
    print("  Configuration Summary:")
    print(f"    Wallet:           {os.getenv('TREASURY_ADDRESS', 'NOT SET')}")
    print(f"    Scan interval:    {os.getenv('SCAN_INTERVAL_SECONDS', '10')}s")
    print(f"    Min profit:       ${os.getenv('MIN_PROFIT_USD', '50')}")
    print(f"    HF threshold:     {os.getenv('HEALTH_FACTOR_THRESHOLD', '1.05')}")
    print(f"    Gas cap:          {os.getenv('GAS_PRICE_CAP_GWEI', '50')} gwei")
    print(f"    ML quality min:   {os.getenv('ML_MIN_QUALITY_SCORE', '0.3')}")
    print(f"    Bootstrap:        {os.getenv('BOOTSTRAP_ENABLED', 'true')}")
    print(f"    RL tuning:        lr={os.getenv('RL_LEARNING_RATE', '0.1')}")
    print()

    # Quick preflight
    report = await run_preflight()
    if report.verdict == "NO-GO":
        print("\n⛔ PREFLIGHT FAILED — Cannot start pipeline.")
        print("Fix the issues above and try again.")
        return 1

    print("\n✅ Preflight passed. Starting pipeline…\n")
    await asyncio.sleep(2)

    # Launch pipeline
    pipeline = Pipeline()
    try:
        await pipeline.run()
    except KeyboardInterrupt:
        print("\n🛑 Graceful shutdown requested…")
    return 0


def main():
    setup_logging()
    args = set(sys.argv[1:])

    if "--preflight" in args:
        asyncio.run(run_preflight())
    elif "--gas" in args:
        asyncio.run(run_gas_report())
    elif "--omni-test" in args:
        asyncio.run(run_omni_test())
    elif "--scan-only" in args:
        os.environ["SCAN_ONLY_MODE"] = "true"
        asyncio.run(run_full_system())
    else:
        result = asyncio.run(run_full_system())
        sys.exit(result or 0)


if __name__ == "__main__":
    main()
