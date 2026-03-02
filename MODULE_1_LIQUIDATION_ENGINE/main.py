#!/usr/bin/env python3
"""
MODULE 1 — ENHANCED FLASH LOAN LIQUIDATION ENGINE
====================================================
Single entry point.

Usage:
    python -m MODULE_1_LIQUIDATION_ENGINE.main
    python -m MODULE_1_LIQUIDATION_ENGINE.main --preflight-only
    python -m MODULE_1_LIQUIDATION_ENGINE.main --gas-report
    python -m MODULE_1_LIQUIDATION_ENGINE.main --treasury
    python -m MODULE_1_LIQUIDATION_ENGINE.main --analytics
"""

import asyncio
import logging
import sys

from .config.settings import get_config
from .pipeline import Pipeline
from .stage_0_preflight.system_validator import SystemValidator
from .stage_3_execution.gas_manager import GasManager
from .stage_6_profit_collection.treasury_manager import TreasuryManager
from .stage_7_analytics.analytics_engine import AnalyticsEngine


def setup_logging():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


async def run_preflight():
    """Run pre-flight only and exit."""
    cfg = get_config()
    validator = SystemValidator(cfg)
    report = await validator.run()
    SystemValidator.print_report(report)
    return 0 if report.verdict != "NO-GO" else 1


async def run_gas_report():
    """Show gas report only and exit."""
    cfg = get_config()
    gm = GasManager(cfg)
    report = gm.full_report()
    GasManager.print_report(report)
    return 0


def run_treasury():
    """Show treasury snapshot and exit."""
    cfg = get_config()
    tm = TreasuryManager(cfg)
    tm.print_snapshot()
    return 0


def run_analytics():
    """Show analytics summary and exit."""
    cfg = get_config()
    engine = AnalyticsEngine(cfg)
    engine.print_summary()
    recs = engine.get_tuning_recommendations()
    if recs:
        print("\n  Tuning Recommendations:")
        for r in recs:
            print(f"    💡 {r}")
    return 0


async def run_pipeline():
    """Run the full enhanced pipeline."""
    pipeline = Pipeline()
    await pipeline.run()
    return 0


def main():
    setup_logging()

    args = sys.argv[1:]

    if "--preflight-only" in args:
        return asyncio.run(run_preflight())
    elif "--gas-report" in args:
        return asyncio.run(run_gas_report())
    elif "--treasury" in args:
        return run_treasury()
    elif "--analytics" in args:
        return run_analytics()
    else:
        return asyncio.run(run_pipeline())


if __name__ == "__main__":
    sys.exit(main() or 0)
