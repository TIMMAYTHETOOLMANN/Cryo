#!/usr/bin/env python3
"""
╔══════════════════════════════════════════════════════════════════════╗
║           LAUNCH TRIANGULATED PROFIT ENGINE                         ║
║                                                                      ║
║   Phase 1: Cold Start  → Flash-loan-only extraction ($0 capital)    ║
║   Phase 2: Heat Map    → Pattern learning & frequency optimization  ║
║   Phase 3: Multiplier  → Exponential compounding reinvestment       ║
║                                                                      ║
║   Target:  $2,400-$4,800  first hour                                ║
║            $45,000-$65,000 first 12 hours                           ║
║            $3.2M-$12.1M   first 24 hours (with multiplier)         ║
╚══════════════════════════════════════════════════════════════════════╝

Usage:
    python launch_profit_engine.py
    python launch_profit_engine.py --min-profit 25 --scan-interval 2
    python launch_profit_engine.py --phase2-threshold 5000
"""

import asyncio
import argparse
import os
import sys
import signal
from datetime import datetime

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from dotenv import load_dotenv
load_dotenv()

from profit_engine.triangulated_profit_engine import (
    TriangulatedProfitEngine,
    build_default_config,
)


def parse_args():
    parser = argparse.ArgumentParser(
        description="Launch the Triangulated Profit Engine",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python launch_profit_engine.py
  python launch_profit_engine.py --min-profit 25
  python launch_profit_engine.py --reinvest-rate 0.70 --trades-per-hour 50
        """
    )

    parser.add_argument('--min-profit', type=float, default=None,
                        help='Minimum net profit per trade (USD). Default: $10')
    parser.add_argument('--min-confidence', type=float, default=None,
                        help='Minimum signal confidence (0-1). Default: 0.4')
    parser.add_argument('--scan-interval', type=float, default=None,
                        help='Scan interval in seconds. Default: 3')
    parser.add_argument('--phase2-threshold', type=float, default=None,
                        help='Cumulative profit to trigger Phase 2 ($). Default: $2,500')
    parser.add_argument('--phase3-threshold', type=float, default=None,
                        help='Cumulative profit to trigger Phase 3 ($). Default: $45,000')
    parser.add_argument('--reinvest-rate', type=float, default=None,
                        help='Reinvestment rate for multiplier (0-1). Default: 0.60')
    parser.add_argument('--avg-return', type=float, default=None,
                        help='Avg return per trade (decimal). Default: 0.0035')
    parser.add_argument('--trades-per-hour', type=int, default=None,
                        help='Target trades per hour. Default: 45')
    parser.add_argument('--min-margin-pct', type=float, default=None,
                        help='Minimum margin after gas (0-1). Default: 0.15')

    return parser.parse_args()


def build_config(args) -> dict:
    """Build config from defaults + CLI overrides."""
    config = build_default_config()

    # Apply CLI overrides
    if args.min_profit is not None:
        config['scanner']['min_profit_usd'] = args.min_profit
        config['execution']['liquidation']['min_profit_usd'] = args.min_profit
    if args.min_confidence is not None:
        config['scanner']['min_confidence'] = args.min_confidence
    if args.scan_interval is not None:
        config['scanner']['scan_interval'] = args.scan_interval
    if args.phase2_threshold is not None:
        config['ledger']['phase2_threshold'] = args.phase2_threshold
    if args.phase3_threshold is not None:
        config['ledger']['phase3_threshold'] = args.phase3_threshold
    if args.reinvest_rate is not None:
        config['multiplier']['reinvest_rate'] = args.reinvest_rate
    if args.avg_return is not None:
        config['multiplier']['avg_return'] = args.avg_return
    if args.trades_per_hour is not None:
        config['multiplier']['trades_per_hour'] = args.trades_per_hour
    if args.min_margin_pct is not None:
        config['gas']['min_margin_percent'] = args.min_margin_pct

    return config


async def main():
    args = parse_args()
    config = build_config(args)

    print()
    print("╔" + "═" * 68 + "╗")
    print("║" + " TRIANGULATED PROFIT ENGINE ".center(68) + "║")
    print("║" + " Multi-Vector Flash Loan Extraction System ".center(68) + "║")
    print("╠" + "═" * 68 + "╣")
    print("║" + f" Started: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}".ljust(68) + "║")
    print("║" + f" Phase 1: Cold Start (flash-loan only, $0 capital)".ljust(68) + "║")
    print("║" + f" Phase 2: Heat Map @ ${config['ledger']['phase2_threshold']:,.0f} cumulative".ljust(68) + "║")
    print("║" + f" Phase 3: Multiplier @ ${config['ledger']['phase3_threshold']:,.0f} cumulative".ljust(68) + "║")
    print("║" + f" Reinvest Rate: {config['multiplier']['reinvest_rate']*100:.0f}%".ljust(68) + "║")
    print("║" + f" Avg Return/Trade: {config['multiplier']['avg_return']*100:.2f}%".ljust(68) + "║")
    print("║" + f" Target Trades/Hour: {config['multiplier']['trades_per_hour']}".ljust(68) + "║")
    print("╚" + "═" * 68 + "╝")
    print()

    engine = TriangulatedProfitEngine(config)

    # Handle graceful shutdown
    shutdown_event = asyncio.Event()

    def _signal_handler():
        print("\n\n🛑 Shutdown signal received...")
        shutdown_event.set()

    loop = asyncio.get_event_loop()
    try:
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, _signal_handler)
    except (NotImplementedError, AttributeError):
        # Windows doesn't support add_signal_handler
        pass

    # Run engine with graceful shutdown support
    engine_task = asyncio.create_task(engine.start())

    try:
        await asyncio.gather(engine_task, shutdown_event.wait(), return_exceptions=True)
    except KeyboardInterrupt:
        pass
    finally:
        await engine.stop()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n🛑 Engine terminated.")
