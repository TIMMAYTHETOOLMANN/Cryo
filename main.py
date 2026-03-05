#!/usr/bin/env python3
"""
╔══════════════════════════════════════════════════════════════════════╗
║  CRYO — UNIFIED ZERO-CAPITAL PROFIT ENGINE                          ║
║                                                                      ║
║  Single entry point for the entire system.                           ║
║  Phase-gated execution based on current capital situation.           ║
║                                                                      ║
║  Phases:                                                             ║
║    0  Preflight      Validate RPC, contracts, environment            ║
║    1  Cold Start     Flash-loan liquidations & arb ($0 capital)      ║
║    2  Heat Map       Pattern learning + frequency optimization       ║
║    3  Multiplier     Exponential compounding reinvestment            ║
║                                                                      ║
║  Usage:                                                              ║
║    python main.py                   # Interactive — recommend best   ║
║    python main.py --phase 1         # Jump straight to Phase 1       ║
║    python main.py --preflight       # Run preflight checks only      ║
║    python main.py --scan-only       # Detection only (no execution)  ║
║    python main.py --status          # Show system status             ║
║    python main.py --master          # ALL modules in parallel        ║
╚══════════════════════════════════════════════════════════════════════╝
"""

import asyncio
import argparse
import json
import logging
import os
import signal
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

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
    log_file = os.getenv("LOG_FILE", "cryo.log")

    class FlushHandler(logging.StreamHandler):
        def emit(self, record):
            super().emit(record)
            self.flush()

    fmt = logging.Formatter(
        "%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    root = logging.getLogger()
    root.setLevel(getattr(logging, os.getenv("LOG_LEVEL", "INFO")))

    # Console
    sh = logging.StreamHandler(sys.stdout)
    sh.setLevel(logging.INFO)
    sh.setFormatter(fmt)
    root.addHandler(sh)

    # File (flushing)
    fh = FlushHandler(open(log_file, "a", encoding="utf-8", errors="replace"))
    fh.setLevel(logging.INFO)
    fh.setFormatter(fmt)
    root.addHandler(fh)


# ════════════════════════════════════════════════════════════════════
#  SITUATION ASSESSMENT
# ════════════════════════════════════════════════════════════════════

class SituationAssessor:
    """
    Determines the current operational state and recommends the best
    execution phase.  This is the "brain" that gates deployment.
    """

    # Phase transition thresholds (USD cumulative profit)
    PHASE_2_THRESHOLD = 2_500
    PHASE_3_THRESHOLD = 45_000

    def __init__(self):
        self.has_rpc = bool(os.getenv('MAINNET_RPC_URL') or os.getenv('ETH_RPC_URL'))
        self.has_private_key = bool(os.getenv('PRIVATE_KEY'))
        self.execution_enabled = os.getenv('EXECUTION_ENABLED', 'false').lower() == 'true'
        # TREASURY_ADDRESS should be set in .env; fallback is the
        # project-default treasury used across deploy.py / run_until_profit.py.
        self.treasury = os.getenv(
            'TREASURY_ADDRESS',
            '0xB323C6E32C6efe28FB9cfB1A83F4071c544eA1e4',
        )

        # Attempt to read cumulative profit from existing ledger
        self.cumulative_profit = 0.0
        self.current_phase = "phase_1_cold_start"
        self._load_ledger()

    def _load_ledger(self):
        ledger_path = PROJECT_ROOT / "profit_ledger.json"
        if ledger_path.exists():
            try:
                with open(ledger_path) as f:
                    data = json.load(f)
                self.cumulative_profit = data.get('cumulative_profit_usd', 0.0)
                self.current_phase = data.get('current_phase', 'phase_1_cold_start')
            except Exception:
                pass

    def recommend_phase(self) -> int:
        """Return the recommended phase number (1, 2, or 3)."""
        if self.cumulative_profit >= self.PHASE_3_THRESHOLD:
            return 3
        if self.cumulative_profit >= self.PHASE_2_THRESHOLD:
            return 2
        return 1

    def print_assessment(self):
        phase = self.recommend_phase()
        phase_names = {1: "Cold Start", 2: "Heat Map", 3: "Capital Multiplier"}

        print()
        print("╔" + "═" * 68 + "╗")
        print("║" + "  SITUATION ASSESSMENT".ljust(68) + "║")
        print("╠" + "═" * 68 + "╣")
        print("║" + f"  RPC Configured:     {'YES' if self.has_rpc else 'NO — set MAINNET_RPC_URL'}".ljust(68) + "║")
        print("║" + f"  Private Key:        {'YES' if self.has_private_key else 'NO — set PRIVATE_KEY'}".ljust(68) + "║")
        print("║" + f"  Execution Enabled:  {'YES' if self.execution_enabled else 'NO — set EXECUTION_ENABLED=true'}".ljust(68) + "║")
        print("║" + f"  Treasury:           {self.treasury[:20]}...".ljust(68) + "║")
        print("║" + f"  Cumulative Profit:  ${self.cumulative_profit:,.2f}".ljust(68) + "║")
        print("╠" + "═" * 68 + "╣")
        print("║" + f"  RECOMMENDED PHASE:  {phase} — {phase_names[phase]}".ljust(68) + "║")

        if phase == 1:
            print("║" + "".ljust(68) + "║")
            print("║" + "  Strategy: Flash-loan liquidations & arbitrage ($0 capital)".ljust(68) + "║")
            print("║" + "  • Scan for under-collateralized Aave/Compound positions".ljust(68) + "║")
            print("║" + "  • Execute via flash loan — borrow, liquidate, profit, repay".ljust(68) + "║")
            print("║" + "  • Reserve Protocol over-collateralization arbitrage".ljust(68) + "║")
            print("║" + "  • Target: $2,400-$4,800 first hour".ljust(68) + "║")
        elif phase == 2:
            print("║" + "".ljust(68) + "║")
            print("║" + "  Strategy: Pattern learning + frequency optimization".ljust(68) + "║")
            print("║" + "  • Heat map tracks profitable patterns by time & chain".ljust(68) + "║")
            print("║" + "  • Mempool sniffer transitions to backrun mode".ljust(68) + "║")
            print("║" + "  • Adaptive thresholds based on historical success".ljust(68) + "║")
        elif phase == 3:
            print("║" + "".ljust(68) + "║")
            print("║" + "  Strategy: Exponential compounding reinvestment".ljust(68) + "║")
            print("║" + "  • 60% profit reinvested into larger positions".ljust(68) + "║")
            print("║" + "  • Cross-chain arbitrage with accumulated capital".ljust(68) + "║")
            print("║" + "  • Target: $3.2M-$12.1M per 24 hours".ljust(68) + "║")

        print("╚" + "═" * 68 + "╝")
        print()


# ════════════════════════════════════════════════════════════════════
#  PHASE RUNNERS
# ════════════════════════════════════════════════════════════════════

async def run_preflight():
    """Phase 0 — validate that the system can operate."""
    from MODULE_1_LIQUIDATION_ENGINE.config.settings import get_config
    from MODULE_1_LIQUIDATION_ENGINE.stage_0_preflight.system_validator import SystemValidator

    cfg = get_config()
    validator = SystemValidator(cfg)
    report = await validator.run()
    SystemValidator.print_report(report)
    return report


async def run_status():
    """Print system status from current ledger and logs."""
    assessor = SituationAssessor()
    assessor.print_assessment()


async def run_phase(phase: int, scan_only: bool = False):
    """
    Launch the profit engine at the specified phase.

    Phase 1 — Cold Start: flash-loan-only extraction, zero capital.
    Phase 2 — Heat Map:   adds pattern learning, adaptive frequency.
    Phase 3 — Multiplier: exponential compounding with capital reinvestment.

    All phases share the same TriangulatedProfitEngine; the phase number
    controls initial thresholds and which subsystems are active.
    """
    from profit_engine.triangulated_profit_engine import (
        TriangulatedProfitEngine,
        build_default_config,
    )

    config = build_default_config()

    if scan_only:
        os.environ["SCAN_ONLY_MODE"] = "true"

    # ── Phase-specific tuning ───────────────────────────────────────
    if phase == 1:
        # Maximum aggression — accept any net-positive trade
        config['scanner']['min_profit_usd'] = 0.01
        config['scanner']['min_confidence'] = 0.10
        config['scanner']['scan_interval'] = 2.0
        config['gas']['min_margin_percent'] = 0.01
        config['gas']['min_margin_usd'] = 0.01
        config['execution']['liquidation']['min_profit_usd'] = 0.01
    elif phase == 2:
        config['scanner']['min_profit_usd'] = 5.0
        config['scanner']['min_confidence'] = 0.3
        config['scanner']['scan_interval'] = 3.0
    elif phase == 3:
        config['scanner']['min_profit_usd'] = 25.0
        config['scanner']['min_confidence'] = 0.5
        config['scanner']['scan_interval'] = 5.0
        config['multiplier']['reinvest_rate'] = 0.60

    # ── Banner ──────────────────────────────────────────────────────
    phase_names = {1: "COLD START", 2: "HEAT MAP", 3: "CAPITAL MULTIPLIER"}
    mode_str = "SCAN ONLY" if scan_only else "LIVE"

    print()
    print("╔" + "═" * 68 + "╗")
    print("║" + " CRYO — ZERO-CAPITAL PROFIT ENGINE ".center(68) + "║")
    print("╠" + "═" * 68 + "╣")
    print("║" + f" Phase {phase}: {phase_names[phase]} [{mode_str}]".ljust(68) + "║")
    print("║" + f" Started: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}".ljust(68) + "║")
    print("║" + f" Scan interval: {config['scanner']['scan_interval']}s".ljust(68) + "║")
    print("║" + f" Min profit: ${config['scanner']['min_profit_usd']}".ljust(68) + "║")
    print("║" + f" Min confidence: {config['scanner']['min_confidence']}".ljust(68) + "║")
    if phase == 3:
        print("║" + f" Reinvest rate: {config['multiplier']['reinvest_rate']*100:.0f}%".ljust(68) + "║")
    print("╚" + "═" * 68 + "╝")
    print()

    # ── Preflight ───────────────────────────────────────────────────
    try:
        report = await run_preflight()
        if report.verdict == "NO-GO":
            print("\n  PREFLIGHT FAILED — Cannot start engine.")
            print("  Fix the issues above and try again.\n")
            return 1
        print("\n  Preflight passed. Starting engine...\n")
    except Exception as e:
        logging.warning("Preflight skipped: %s", e)

    await asyncio.sleep(1)

    # ── Engine ──────────────────────────────────────────────────────
    engine = TriangulatedProfitEngine(config)
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

    engine_task = asyncio.create_task(engine.start())

    try:
        await asyncio.gather(
            engine_task,
            shutdown_event.wait(),
            return_exceptions=True,
        )
    except KeyboardInterrupt:
        pass
    finally:
        await engine.stop()

    return 0


async def run_module1_pipeline(scan_only: bool = False):
    """
    Launch the full MODULE_1 stage-gated pipeline (Stages 0-7 + Module 9).
    This is the deeper pipeline with MEV protection, cross-chain orchestration,
    treasury management, and RL-tuned analytics.
    """
    from MODULE_1_LIQUIDATION_ENGINE.pipeline import Pipeline

    if scan_only:
        os.environ["SCAN_ONLY_MODE"] = "true"

    print()
    print("╔" + "═" * 68 + "╗")
    print("║" + " CRYO — MODULE 1 STAGE-GATED PIPELINE ".center(68) + "║")
    print("╠" + "═" * 68 + "╣")
    print("║" + f" Started: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}".ljust(68) + "║")
    print("║" + f" Mode: {'SCAN ONLY' if scan_only else 'LIVE'}".ljust(68) + "║")
    print("║" + f" Wallet: {os.getenv('TREASURY_ADDRESS', 'NOT SET')}".ljust(68) + "║")
    print("╚" + "═" * 68 + "╝")
    print()

    pipeline = Pipeline()
    try:
        await pipeline.run()
    except KeyboardInterrupt:
        print("\n  Graceful shutdown requested...")
    return 0


# ════════════════════════════════════════════════════════════════════
#  CLI
# ════════════════════════════════════════════════════════════════════

def parse_args():
    parser = argparse.ArgumentParser(
        prog="cryo",
        description="CRYO — Unified Zero-Capital Profit Engine",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python main.py                      # Assess situation & launch recommended phase
  python main.py --phase 1            # Force Phase 1 (zero-capital flash loans)
  python main.py --preflight          # Run preflight checks only
  python main.py --scan-only          # Detection only, no execution
  python main.py --status             # Show current system status
  python main.py --pipeline           # Run full Module 1 stage-gated pipeline
  python main.py --master             # ALL modules in parallel (max extraction)
  python main.py --master --scan-only # All modules, detection only
  python main.py --hub status         # Show consolidated module registry
  python main.py --hub recon          # Run unified recon sweep across all modules
  python main.py --hub master         # All modules via hub deployment
  python main.py --hub full_pipeline  # Deploy via consolidated hub
  python main.py --hub ops_full_recon      # Full tactical recon operation
  python main.py --hub ops_target_acquire  # Target acquisition sweep
  python main.py --hub ops_system_check    # System health check
  python main.py --hub ops_phase2_activate # Activate Phase 2 modules
  python main.py --fire parse "FlashLoan(x) + Swap(y)"  # Parse FIRE script
  python main.py --fire compile "FlashLoan(x) + Swap(y)" # Compile FIRE script
  python main.py --fire registry                          # List FIRE operations
        """,
    )
    parser.add_argument(
        '--phase', type=int, choices=[1, 2, 3], default=None,
        help='Force a specific phase (1=Cold Start, 2=Heat Map, 3=Multiplier)',
    )
    parser.add_argument(
        '--preflight', action='store_true',
        help='Run preflight checks only',
    )
    parser.add_argument(
        '--scan-only', action='store_true',
        help='Detection only — no execution',
    )
    parser.add_argument(
        '--status', action='store_true',
        help='Print system status and exit',
    )
    parser.add_argument(
        '--pipeline', action='store_true',
        help='Run the full Module 1 stage-gated pipeline',
    )
    parser.add_argument(
        '--hub', type=str, nargs='?', const='status', default=None,
        metavar='STRATEGY',
        help='Launch via consolidated hub. Strategies: status, preflight, '
             'recon, scan_only, phase_1, phase_2, phase_3, full_pipeline, '
             'master, monitor, ops_full_recon, ops_target_acquire, '
             'ops_system_check, ops_phase2_activate',
    )
    parser.add_argument(
        '--master', action='store_true',
        help='Launch Master Profit Orchestrator — all modules in parallel',
    )
    parser.add_argument(
        '--fire', type=str, nargs='*', default=None,
        metavar='COMMAND',
        help='FIRE engine commands: registry, parse <script>, compile <script>',
    )
    return parser.parse_args()


# ════════════════════════════════════════════════════════════════════
#  FIRE ENGINE HANDLER
# ════════════════════════════════════════════════════════════════════

def _handle_fire_command(fire_args):
    """Handle --fire CLI commands."""
    import json
    from fire_engine import OperationRegistry, FireParser, Compiler

    if not fire_args or fire_args[0] == "registry":
        # List all registered operations
        registry = OperationRegistry()
        for proto in ("aave_v3", "uniswap_v2", "compound_v2"):
            registry.register_protocol_operations(proto)
        print(f"\n  FIRE Operation Registry — {registry.count} operations\n")
        for op in registry.list_all():
            print(f"    {op.id:40s} {op.description}")
        return 0

    command = fire_args[0]
    script = " ".join(fire_args[1:]) if len(fire_args) > 1 else ""

    if command == "parse":
        if not script:
            print("  Usage: --fire parse <script>")
            return 1
        parser = FireParser()
        ast = parser.parse(script)
        print(f"\n  Parsed AST:\n    {ast!r}\n")
        return 0

    if command == "compile":
        if not script:
            print("  Usage: --fire compile <script>")
            return 1
        registry = OperationRegistry()
        for proto in ("aave_v3", "uniswap_v2", "compound_v2"):
            registry.register_protocol_operations(proto)
        parser = FireParser()
        compiler = Compiler(registry)
        ast = parser.parse(script)
        plan = compiler.compile(ast)
        print(f"\n  Compiled Plan ({plan.step_count} steps, ~{plan.total_gas_estimate} gas):\n")
        print(json.dumps(plan.to_dict(), indent=2))
        return 0

    print(f"  Unknown FIRE command: {command}")
    print("  Available: registry, parse, compile")
    return 1


# ════════════════════════════════════════════════════════════════════
#  MAIN
# ════════════════════════════════════════════════════════════════════

async def async_main():
    args = parse_args()

    # ── FIRE Engine ─────────────────────────────────────────────────
    if args.fire is not None:
        return _handle_fire_command(args.fire)

    # ── Hub (consolidated command center) ───────────────────────────
    if args.hub is not None:
        from hub import DeploymentStrategy, deploy as hub_deploy
        try:
            strategy = DeploymentStrategy(args.hub)
        except ValueError:
            valid = ", ".join(s.value for s in DeploymentStrategy)
            print(f"  Unknown hub strategy: {args.hub!r}")
            print(f"  Valid strategies: {valid}")
            return 1
        return await hub_deploy(strategy, scan_only=args.scan_only)

    # ── Status check ────────────────────────────────────────────────
    if args.status:
        await run_status()
        return 0

    # ── Preflight only ──────────────────────────────────────────────
    if args.preflight:
        await run_preflight()
        return 0

    # ── Full Module 1 pipeline ──────────────────────────────────────
    if args.pipeline:
        return await run_module1_pipeline(scan_only=args.scan_only)

    # ── Master Profit Orchestrator ─────────────────────────────────
    if args.master:
        from master_orchestrator import MasterProfitOrchestrator
        orchestrator = MasterProfitOrchestrator(
            {"scan_only": args.scan_only}
        )
        return await orchestrator.run()

    # ── Phase-gated execution ───────────────────────────────────────
    assessor = SituationAssessor()
    phase = args.phase or assessor.recommend_phase()

    # Show assessment before launching
    assessor.print_assessment()

    return await run_phase(phase, scan_only=args.scan_only)


def main():
    setup_logging()
    try:
        result = asyncio.run(async_main())
        sys.exit(result or 0)
    except KeyboardInterrupt:
        print("\n  Engine terminated.")
        sys.exit(0)


if __name__ == "__main__":
    main()
