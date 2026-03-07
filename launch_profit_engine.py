#!/usr/bin/env python3
"""
LAUNCH TRIANGULATED PROFIT ENGINE — Production Deployment
============================================================
Single-command launcher with full environment variable configuration,
wallet balance verification, and hardened startup.

Phases:
  Phase 1: Cold Start  -> Flash-loan-only extraction ($0 capital)
  Phase 2: Heat Map    -> Pattern learning & frequency optimization
  Phase 3: Multiplier  -> Exponential compounding reinvestment

Usage:
    python launch_profit_engine.py
    python launch_profit_engine.py --min-profit 25 --scan-interval 2
    python launch_profit_engine.py --phase2-threshold 5000
    python launch_profit_engine.py --scan-only
    python launch_profit_engine.py --preset aggressive
"""

import asyncio
import argparse
import io
import logging
import os
import sys
import signal
import time
from datetime import datetime
from pathlib import Path

# Force UTF-8 output on Windows
if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

# Add project root to path
PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_ROOT))

from dotenv import load_dotenv
load_dotenv(PROJECT_ROOT / ".env")

from profit_engine.triangulated_profit_engine import (
    TriangulatedProfitEngine,
    build_default_config,
)


# ═══════════════════════════════════════════════════════════════════
#  LOGGING
# ═══════════════════════════════════════════════════════════════════

def setup_logging():
    log_file = os.getenv("LOG_FILE", "module1_liquidation_engine.log")
    log_level = getattr(logging, os.getenv("LOG_LEVEL", "INFO").upper(), logging.INFO)

    fmt = logging.Formatter(
        "%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    root = logging.getLogger()
    root.setLevel(log_level)

    # Console handler
    sh = logging.StreamHandler(sys.stdout)
    sh.setLevel(log_level)
    sh.setFormatter(fmt)
    root.addHandler(sh)

    # File handler (UTF-8)
    try:
        fh = logging.FileHandler(log_file, mode="a", encoding="utf-8")
        fh.setLevel(log_level)
        fh.setFormatter(fmt)
        root.addHandler(fh)
    except Exception:
        pass


# ═══════════════════════════════════════════════════════════════════
#  PRESETS
# ═══════════════════════════════════════════════════════════════════

PRESETS = {
    "conservative": {
        "min_profit": 50.0,
        "min_confidence": 0.6,
        "scan_interval": 5.0,
        "min_margin_pct": 0.20,
        "reinvest_rate": 0.40,
    },
    "balanced": {
        "min_profit": 10.0,
        "min_confidence": 0.4,
        "scan_interval": 3.0,
        "min_margin_pct": 0.10,
        "reinvest_rate": 0.60,
    },
    "aggressive": {
        "min_profit": 0.50,
        "min_confidence": 0.15,
        "scan_interval": 2.0,
        "min_margin_pct": 0.03,
        "reinvest_rate": 0.70,
    },
    "maximum": {
        "min_profit": 0.01,
        "min_confidence": 0.10,
        "scan_interval": 1.0,
        "min_margin_pct": 0.01,
        "reinvest_rate": 0.80,
    },
}


# ═══════════════════════════════════════════════════════════════════
#  CLI
# ═══════════════════════════════════════════════════════════════════

def parse_args():
    parser = argparse.ArgumentParser(
        description="Launch the Triangulated Profit Engine",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python launch_profit_engine.py
  python launch_profit_engine.py --preset aggressive
  python launch_profit_engine.py --min-profit 25
  python launch_profit_engine.py --scan-only
  python launch_profit_engine.py --reinvest-rate 0.70 --trades-per-hour 50
        """,
    )

    parser.add_argument("--preset", choices=list(PRESETS.keys()), default=None,
                        help="Use a named preset (overrides individual flags)")
    parser.add_argument("--scan-only", action="store_true",
                        help="Scan only — no live transactions")
    parser.add_argument("--min-profit", type=float, default=None,
                        help="Minimum net profit per trade (USD)")
    parser.add_argument("--min-confidence", type=float, default=None,
                        help="Minimum signal confidence (0-1)")
    parser.add_argument("--scan-interval", type=float, default=None,
                        help="Scan interval in seconds")
    parser.add_argument("--phase2-threshold", type=float, default=None,
                        help="Cumulative profit to trigger Phase 2 ($)")
    parser.add_argument("--phase3-threshold", type=float, default=None,
                        help="Cumulative profit to trigger Phase 3 ($)")
    parser.add_argument("--reinvest-rate", type=float, default=None,
                        help="Reinvestment rate for multiplier (0-1)")
    parser.add_argument("--avg-return", type=float, default=None,
                        help="Avg return per trade (decimal)")
    parser.add_argument("--trades-per-hour", type=int, default=None,
                        help="Target trades per hour")
    parser.add_argument("--min-margin-pct", type=float, default=None,
                        help="Minimum margin after gas (0-1)")

    return parser.parse_args()


def build_config(args) -> dict:
    """Build config from env vars + preset + CLI overrides."""
    config = build_default_config()

    # Apply preset first (if specified)
    if args.preset:
        preset = PRESETS[args.preset]
        config["scanner"]["min_profit_usd"] = preset["min_profit"]
        config["scanner"]["min_confidence"] = preset["min_confidence"]
        config["scanner"]["scan_interval"] = preset["scan_interval"]
        config["gas"]["min_margin_percent"] = preset["min_margin_pct"]
        config["multiplier"]["reinvest_rate"] = preset["reinvest_rate"]
        config["execution"]["liquidation"]["min_profit_usd"] = preset["min_profit"]

    # CLI overrides (take priority over preset)
    if args.min_profit is not None:
        config["scanner"]["min_profit_usd"] = args.min_profit
        config["execution"]["liquidation"]["min_profit_usd"] = args.min_profit
    if args.min_confidence is not None:
        config["scanner"]["min_confidence"] = args.min_confidence
    if args.scan_interval is not None:
        config["scanner"]["scan_interval"] = args.scan_interval
    if args.phase2_threshold is not None:
        config["ledger"]["phase2_threshold"] = args.phase2_threshold
    if args.phase3_threshold is not None:
        config["ledger"]["phase3_threshold"] = args.phase3_threshold
    if args.reinvest_rate is not None:
        config["multiplier"]["reinvest_rate"] = args.reinvest_rate
    if args.avg_return is not None:
        config["multiplier"]["avg_return"] = args.avg_return
    if args.trades_per_hour is not None:
        config["multiplier"]["trades_per_hour"] = args.trades_per_hour
    if args.min_margin_pct is not None:
        config["gas"]["min_margin_percent"] = args.min_margin_pct

    # --scan-only flag
    if args.scan_only:
        config["execution"]["scan_only_mode"] = True

    return config


# ═══════════════════════════════════════════════════════════════════
#  PREFLIGHT CHECKS
# ═══════════════════════════════════════════════════════════════════

def run_preflight(config: dict) -> bool:
    """
    Verify critical environment variables are set before launch.
    Returns True if preflight passes.
    """
    print()
    print("  PREFLIGHT CHECKS")
    print("  " + "-" * 50)
    passed = True
    warnings = 0

    # Check RPC endpoints
    rpc_cfg = config.get("rpc", {})
    rpc_count = 0
    for chain_id, url in rpc_cfg.items():
        if url and "YOUR_API_KEY" not in url and "llamarpc" not in url:
            rpc_count += 1
            print(f"  [OK]  Chain {chain_id}: RPC configured")
        elif url:
            rpc_count += 1
            print(f"  [..] Chain {chain_id}: using public RPC (slow)")
            warnings += 1

    if rpc_count == 0:
        print("  [FAIL] No RPC endpoints configured!")
        passed = False
    else:
        print(f"  [OK]  {rpc_count} chain(s) configured")

    # Check wallet
    wallet_cfg = config.get("wallet", {})
    pk = wallet_cfg.get("private_key", "")
    treasury = wallet_cfg.get("treasury_address", "")

    if pk and len(pk) > 10:
        print(f"  [OK]  Wallet private key configured")
    else:
        print(f"  [..] No PRIVATE_KEY — scan-only mode")
        warnings += 1

    if treasury and treasury.startswith("0x"):
        print(f"  [OK]  Treasury: {treasury[:10]}...{treasury[-6:]}")
    else:
        print(f"  [..] No TREASURY_ADDRESS set")
        warnings += 1

    # Check contracts
    exec_cfg = config.get("execution", {}).get("liquidation", {})
    contracts_set = 0
    for name in ("executor_v1", "executor_v2", "flash_executor"):
        addr = exec_cfg.get(name, "")
        if addr and addr.startswith("0x") and len(addr) == 42:
            contracts_set += 1

    if contracts_set > 0:
        print(f"  [OK]  {contracts_set} execution contract(s) configured")
    else:
        print(f"  [..] No execution contracts — will use flash-loan-only mode")
        warnings += 1

    # Check MEV protection
    mev_cfg = config.get("mev", {})
    if mev_cfg.get("flashbots_relay_url"):
        builders = len(mev_cfg.get("flashbots_builder_urls", []))
        print(f"  [OK]  Flashbots relay + {builders} builder(s)")
    else:
        print(f"  [..] No MEV protection configured")
        warnings += 1

    # Check gas configuration
    gas_cfg = config.get("gas", {})
    print(f"  [OK]  Gas cap: {gas_cfg.get('gas_price_cap_gwei', 50)} gwei")
    print(f"  [OK]  Gas buffer: {gas_cfg.get('gas_limit_buffer', 1.2)}x")
    print(f"  [OK]  Min margin: {gas_cfg.get('min_margin_percent', 0.03)*100:.0f}%")

    # Check alerts (optional)
    alerts_cfg = config.get("alerts", {})
    alert_channels = 0
    if alerts_cfg.get("telegram_bot_token"):
        alert_channels += 1
    if alerts_cfg.get("discord_webhook_url"):
        alert_channels += 1
    if alert_channels > 0:
        print(f"  [OK]  {alert_channels} alert channel(s) configured")
    else:
        print(f"  [..] No alert channels (Telegram/Discord) configured")

    print()
    if not passed:
        print("  PREFLIGHT: FAILED")
    elif warnings > 0:
        print(f"  PREFLIGHT: PASSED with {warnings} warning(s)")
    else:
        print("  PREFLIGHT: ALL CLEAR")
    print()

    return passed


# ═══════════════════════════════════════════════════════════════════
#  WALLET BALANCE CHECK
# ═══════════════════════════════════════════════════════════════════

async def check_wallet_balance(config: dict) -> None:
    """Check wallet balances across all configured chains."""
    pk = config.get("wallet", {}).get("private_key", "")
    if not pk or len(pk) < 10:
        return

    try:
        from web3 import Web3
        from eth_account import Account

        # Derive address from private key
        if pk.startswith("0x"):
            pk_clean = pk
        else:
            pk_clean = "0x" + pk

        try:
            account = Account.from_key(pk_clean)
            address = account.address
        except Exception:
            return

        print(f"  Wallet: {address}")
        print("  " + "-" * 50)

        rpc_cfg = config.get("rpc", {})
        total_usd = 0.0
        chain_names = {
            1: "Ethereum", 42161: "Arbitrum", 10: "Optimism",
            137: "Polygon", 8453: "Base", 43114: "Avalanche",
            56: "BSC", 324: "zkSync",
        }
        native_symbols = {
            1: "ETH", 42161: "ETH", 10: "ETH", 137: "MATIC",
            8453: "ETH", 43114: "AVAX", 56: "BNB", 324: "ETH",
        }
        # Rough prices for gas estimation display
        native_prices = {
            1: 2500, 42161: 2500, 10: 2500, 137: 0.50,
            8453: 2500, 43114: 35, 56: 600, 324: 2500,
        }

        for chain_id, rpc_url in rpc_cfg.items():
            if not rpc_url:
                continue
            try:
                w3 = Web3(Web3.HTTPProvider(rpc_url, request_kwargs={"timeout": 5}))
                if not w3.is_connected():
                    continue
                bal_wei = w3.eth.get_balance(address)
                bal = bal_wei / 1e18
                symbol = native_symbols.get(chain_id, "???")
                price = native_prices.get(chain_id, 0)
                bal_usd = bal * price
                total_usd += bal_usd
                name = chain_names.get(chain_id, f"Chain {chain_id}")
                if bal > 0:
                    print(f"  {name:12s}: {bal:.6f} {symbol} (~${bal_usd:,.2f})")
                else:
                    print(f"  {name:12s}: 0 {symbol}")
            except Exception:
                pass

        print("  " + "-" * 50)
        print(f"  Total (est): ~${total_usd:,.2f}")
        if total_usd > 10:
            print("  Gas: SUFFICIENT")
        elif total_usd > 1:
            print("  Gas: LOW — consider funding more chains")
        else:
            print("  Gas: CRITICAL — fund wallet before live execution")
        print()

    except ImportError:
        print("  (web3 not available — skipping balance check)")
        print()


# ═══════════════════════════════════════════════════════════════════
#  MAIN
# ═══════════════════════════════════════════════════════════════════

async def main():
    setup_logging()
    args = parse_args()
    config = build_config(args)

    scan_only = config.get("execution", {}).get("scan_only_mode", False)
    mode_str = "SCAN ONLY" if scan_only else "LIVE EXECUTION"

    print()
    print("=" * 72)
    print("  TRIANGULATED PROFIT ENGINE".center(72))
    print("  Multi-Vector Flash Loan Extraction System".center(72))
    print("=" * 72)
    print(f"  Started:            {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"  Mode:               {mode_str}")
    print(f"  Preset:             {args.preset or 'custom (env vars + CLI)'}")
    print(f"  Phase 1:            Cold Start (flash-loan only, $0 capital)")
    print(f"  Phase 2 threshold:  ${config['ledger']['phase2_threshold']:,.0f} cumulative")
    print(f"  Phase 3 threshold:  ${config['ledger']['phase3_threshold']:,.0f} cumulative")
    print(f"  Reinvest Rate:      {config['multiplier']['reinvest_rate']*100:.0f}%")
    print(f"  Avg Return/Trade:   {config['multiplier']['avg_return']*100:.2f}%")
    print(f"  Target Trades/Hour: {config['multiplier']['trades_per_hour']}")
    print(f"  Min Profit:         ${config['scanner']['min_profit_usd']}")
    print(f"  Min Confidence:     {config['scanner']['min_confidence']}")
    print(f"  Scan Interval:      {config['scanner']['scan_interval']}s")
    print(f"  Gas Cap:            {config['gas']['gas_price_cap_gwei']} gwei")
    print(f"  Min Margin:         {config['gas']['min_margin_percent']*100:.0f}%")
    print("=" * 72)
    print()

    # ── Preflight ─────────────────────────────────────────────
    if not run_preflight(config):
        print("  ABORT: Fix preflight issues before launching.")
        sys.exit(1)

    # ── Wallet balance ────────────────────────────────────────
    print("  WALLET BALANCES")
    print("  " + "-" * 50)
    await check_wallet_balance(config)

    # ── Launch engine ─────────────────────────────────────────
    engine = TriangulatedProfitEngine(config)

    # Handle graceful shutdown
    shutdown_event = asyncio.Event()

    def _signal_handler():
        print("\n\n  Shutdown signal received...")
        shutdown_event.set()

    loop = asyncio.get_event_loop()
    try:
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, _signal_handler)
    except (NotImplementedError, AttributeError):
        pass  # Windows

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
        print("\n  Engine terminated.")
