#!/usr/bin/env python3
"""
╔══════════════════════════════════════════════════════════════════════╗
║  RUN UNTIL PROFIT — Persistent Monitor                              ║
║                                                                      ║
║  Starts the Triangulated Profit Engine and monitors it continuously  ║
║  until 5 confirmed profitable transactions have been recorded.       ║
║                                                                      ║
║  Features:                                                           ║
║    • Fresh ledger (clean start)                                      ║
║    • Real-time health factor monitoring across 250+ positions        ║
║    • Adaptive thresholds that loosen over time if no hits            ║
║    • 30-second status dashboard with position health breakdown       ║
║    • Auto-restart on fatal errors                                    ║
║    • Self-heal: reconnects dropped RPC, re-indexes stale positions   ║
║    • Writes confirmed_profits.json on each new profit                ║
║    • Exits cleanly with full report after 5 profits                  ║
╚══════════════════════════════════════════════════════════════════════╝
"""

import asyncio
import os
import sys
import json
import time
import signal
import io
from datetime import datetime, timedelta

# Force UTF-8 encoding — MUST be before any print/output
os.environ['PYTHONIOENCODING'] = 'utf-8'
try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    try:
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    except Exception:
        pass
try:
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    try:
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')
    except Exception:
        pass

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from dotenv import load_dotenv
load_dotenv()

from profit_engine.triangulated_profit_engine import TriangulatedProfitEngine, build_default_config
from profit_engine.profit_ledger import ProfitEntry, PhaseState

# ════════════════════════════════════════════════════════════════════
#  CONFIGURATION
# ════════════════════════════════════════════════════════════════════

TARGET_PROFITS = 5
PROFIT_LOG_FILE = "confirmed_profits.json"
LEDGER_FILE = "profit_ledger.json"
STATUS_INTERVAL = 30         # seconds between status prints
ADAPTIVE_INTERVAL = 300      # seconds before adaptive threshold relaxation
MAX_RUNTIME_HOURS = 48       # safety cap

# ════════════════════════════════════════════════════════════════════
#  MONITOR CLASS
# ════════════════════════════════════════════════════════════════════

class ProfitMonitor:
    """
    Wraps the TriangulatedProfitEngine with persistent monitoring.
    Intercepts every execution result and tracks confirmed profits.
    """

    def __init__(self):
        self.confirmed_profits: list = []
        self.start_time = time.time()
        self.engine: TriangulatedProfitEngine = None
        self.shutdown_event = asyncio.Event()

        # Load any previous confirmed profits
        if os.path.exists(PROFIT_LOG_FILE):
            try:
                with open(PROFIT_LOG_FILE, 'r') as f:
                    self.confirmed_profits = json.load(f)
                print(f"   📂 Loaded {len(self.confirmed_profits)} previous confirmed profits")
            except Exception:
                self.confirmed_profits = []

    def _reset_ledger(self):
        """Reset the profit ledger for a clean start."""
        fresh = {
            "current_phase": "phase_1_cold_start",
            "start_time": time.time(),
            "cumulative_profit_usd": 0.0,
            "cumulative_gross_usd": 0.0,
            "cumulative_gas_usd": 0.0,
            "cumulative_fees_usd": 0.0,
            "total_transactions": 0,
            "successful_transactions": 0,
            "failed_transactions": 0,
            "capital_base_usd": 0.0,
            "profit_reserve_usd": 0.0,
            "profit_by_type": {},
            "count_by_type": {},
            "last_updated": time.time()
        }
        with open(LEDGER_FILE, 'w') as f:
            json.dump(fresh, f, indent=2)
        print("   📒 Ledger reset to $0.00")

    def _save_profit(self, entry: dict):
        """Persist a confirmed profit to disk immediately."""
        self.confirmed_profits.append(entry)
        with open(PROFIT_LOG_FILE, 'w') as f:
            json.dump(self.confirmed_profits, f, indent=2)

    def _build_config(self) -> dict:
        """Build maximum aggression scanning config."""
        config = build_default_config()
        # FULL SEND — accept any net-positive trade
        config['scanner']['min_profit_usd'] = 0.01
        config['scanner']['min_confidence'] = 0.01
        config['scanner']['scan_interval'] = 2.0
        config['gas']['min_margin_percent'] = 0.01
        config['gas']['min_margin_usd'] = 0.01
        config['execution']['liquidation']['min_profit_usd'] = 0.01
        return config

    async def run(self):
        """Main run loop — starts engine and monitors until target profits."""
        print()
        print("╔" + "═" * 68 + "╗")
        print("║" + " RUN UNTIL PROFIT — PERSISTENT MONITOR ".center(68) + "║")
        print("╠" + "═" * 68 + "╣")
        print("║" + f" Target: {TARGET_PROFITS} confirmed profitable transactions".ljust(68) + "║")
        print("║" + f" Started: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}".ljust(68) + "║")
        print("║" + f" Max runtime: {MAX_RUNTIME_HOURS}h".ljust(68) + "║")
        print("╚" + "═" * 68 + "╝")
        print()

        # Reset ledger for clean tracking
        self._reset_ledger()

        attempt = 0
        while len(self.confirmed_profits) < TARGET_PROFITS:
            attempt += 1
            elapsed_h = (time.time() - self.start_time) / 3600

            if elapsed_h > MAX_RUNTIME_HOURS:
                print(f"\n⏰ Max runtime ({MAX_RUNTIME_HOURS}h) reached. Stopping.")
                break

            print(f"\n{'='*70}")
            print(f"  🚀 ENGINE RUN #{attempt} | Profits: {len(self.confirmed_profits)}/{TARGET_PROFITS} | Elapsed: {elapsed_h:.2f}h")
            print(f"{'='*70}")

            try:
                config = self._build_config()
                self.engine = TriangulatedProfitEngine(config)

                # Monkey-patch the execution result handler to intercept profits
                original_handler = self.engine._handle_execution_result

                async def intercepted_handler(result):
                    await original_handler(result)
                    # Check if this was profitable
                    from omni_channel.execution_router.execution_interface import ExecutionStatus
                    if result.status == ExecutionStatus.CONFIRMED and result.profit_usd > 0:
                        entry = {
                            'profit_number': len(self.confirmed_profits) + 1,
                            'timestamp': datetime.now().isoformat(),
                            'tx_hash': result.tx_hash or 'N/A',
                            'profit_usd': result.profit_usd,
                            'gas_used': result.gas_used,
                            'block_number': result.block_number,
                            'request_id': result.request_id,
                            'elapsed_seconds': time.time() - self.start_time,
                        }
                        self._save_profit(entry)
                        count = len(self.confirmed_profits)

                        print()
                        print(f"  💰{'═'*60}💰")
                        print(f"  ║ CONFIRMED PROFIT #{count}/{TARGET_PROFITS}")
                        print(f"  ║ Amount:  ${result.profit_usd:,.2f}")
                        print(f"  ║ TX:      {result.tx_hash or 'N/A'}")
                        print(f"  ║ Block:   {result.block_number}")
                        print(f"  ║ Gas:     {result.gas_used:,}")
                        print(f"  ║ Time:    {datetime.now().strftime('%H:%M:%S')}")
                        print(f"  💰{'═'*60}💰")
                        print()

                        if count >= TARGET_PROFITS:
                            print(f"\n  🎯 TARGET REACHED: {TARGET_PROFITS} PROFITABLE TRANSACTIONS CONFIRMED!")
                            self.shutdown_event.set()

                self.engine._handle_execution_result = intercepted_handler

                # Start engine with monitoring
                engine_task = asyncio.create_task(self.engine.start())
                monitor_task = asyncio.create_task(self._status_loop())
                adaptive_task = asyncio.create_task(self._adaptive_loop())

                # Wait for either shutdown or engine completion
                done, pending = await asyncio.wait(
                    [engine_task, monitor_task, adaptive_task,
                     asyncio.create_task(self.shutdown_event.wait())],
                    return_when=asyncio.FIRST_COMPLETED,
                )

                # Clean up
                for task in pending:
                    task.cancel()
                    try:
                        await task
                    except (asyncio.CancelledError, Exception):
                        pass

                await self.engine.stop()

                if self.shutdown_event.is_set():
                    break

            except KeyboardInterrupt:
                print("\n🛑 Keyboard interrupt — shutting down...")
                if self.engine:
                    await self.engine.stop()
                break

            except Exception as e:
                print(f"\n  ⚠️ ENGINE ERROR (attempt #{attempt}): {e}")
                print(f"  ↻ Auto-restarting in 10 seconds...")
                await asyncio.sleep(10)

        # ── Final Report ──
        self._print_final_report()

    async def _status_loop(self):
        """Print status every STATUS_INTERVAL seconds."""
        cycle = 0
        while not self.shutdown_event.is_set():
            await asyncio.sleep(STATUS_INTERVAL)
            cycle += 1
            self._print_status(cycle)

    async def _adaptive_loop(self):
        """
        Adaptively relax thresholds if no opportunities are being found.
        Every ADAPTIVE_INTERVAL seconds, if zero new profits, lower bars.
        """
        last_profit_count = len(self.confirmed_profits)

        while not self.shutdown_event.is_set():
            await asyncio.sleep(ADAPTIVE_INTERVAL)

            if not self.engine:
                continue

            current_count = len(self.confirmed_profits)

            if current_count == last_profit_count:
                # No new profits in this interval — relax thresholds
                scanner = self.engine.scanner
                old_min = scanner.min_profit_usd
                old_conf = scanner.min_confidence

                scanner.min_profit_usd = max(0.01, old_min * 0.5)
                scanner.min_confidence = max(0.01, old_conf * 0.7)

                print(f"\n  🔧 ADAPTIVE: No new profits in {ADAPTIVE_INTERVAL}s")
                print(f"     min_profit: ${old_min:.2f} → ${scanner.min_profit_usd:.2f}")
                print(f"     min_confidence: {old_conf:.2f} → {scanner.min_confidence:.2f}")
                print(f"     Positions tracked: {len(scanner._tracked_positions)}")

                # Log health factor distribution
                await self._log_health_factors()

            last_profit_count = current_count

    async def _log_health_factors(self):
        """Query and log health factor distribution of tracked positions."""
        if not self.engine or not self.engine.scanner._tracked_positions:
            return

        from web3 import Web3

        scanner = self.engine.scanner
        w3 = scanner._w3.get(1)
        if not w3:
            return

        pool_addr = '0x87870Bca3F3fD6335C3F4ce8392D69350B4fA4E2'
        abi = json.loads('[{"inputs":[{"name":"user","type":"address"}],"name":"getUserAccountData","outputs":[{"name":"totalCollateralBase","type":"uint256"},{"name":"totalDebtBase","type":"uint256"},{"name":"availableBorrowsBase","type":"uint256"},{"name":"currentLiquidationThreshold","type":"uint256"},{"name":"ltv","type":"uint256"},{"name":"healthFactor","type":"uint256"}],"stateMutability":"view","type":"function"}]')
        pool = w3.eth.contract(address=Web3.to_checksum_address(pool_addr), abi=abi)


        # Sample up to 30 positions
        positions = list(scanner._tracked_positions.items())[:30]
        buckets = {'<1.0': 0, '1.0-1.05': 0, '1.05-1.10': 0, '1.10-1.20': 0, '1.20-1.50': 0, '>1.50': 0, 'error': 0}
        closest_hf = 999.0
        closest_addr = ''
        closest_debt = 0.0

        for addr, data in positions:
            try:
                result = pool.functions.getUserAccountData(
                    Web3.to_checksum_address(addr)
                ).call()
                hf = result[5] / 1e18
                debt_usd = result[1] / 1e8

                if hf < 1.0:
                    buckets['<1.0'] += 1
                elif hf < 1.05:
                    buckets['1.0-1.05'] += 1
                elif hf < 1.10:
                    buckets['1.05-1.10'] += 1
                elif hf < 1.20:
                    buckets['1.10-1.20'] += 1
                elif hf < 1.50:
                    buckets['1.20-1.50'] += 1
                else:
                    buckets['>1.50'] += 1

                if hf < closest_hf and debt_usd > 100:
                    closest_hf = hf
                    closest_addr = addr
                    closest_debt = debt_usd

            except Exception:
                buckets['error'] += 1

        print(f"\n  📊 HEALTH FACTOR DISTRIBUTION (sampled {len(positions)} of {len(scanner._tracked_positions)}):")
        for bucket, count in buckets.items():
            bar = '█' * count
            indicator = ' ← LIQUIDATABLE!' if bucket == '<1.0' and count > 0 else ''
            print(f"     {bucket:>10}: {count:>3} {bar}{indicator}")

        if closest_hf < 999:
            print(f"     Closest to liquidation: HF={closest_hf:.4f} debt=${closest_debt:,.0f} ({closest_addr[:10]}...)")

    def _print_status(self, cycle: int):
        """Print compact status line."""
        if not self.engine:
            return

        elapsed = time.time() - self.start_time
        elapsed_str = str(timedelta(seconds=int(elapsed)))
        profits = len(self.confirmed_profits)
        total_profit = sum(p['profit_usd'] for p in self.confirmed_profits)
        positions = len(self.engine.scanner._tracked_positions)
        opps_found = self.engine.opportunities_received
        opps_exec = self.engine.opportunities_executed
        opps_skip = self.engine.opportunities_skipped
        phase = self.engine.ledger.current_phase.value

        # Vector stats
        vector_summary = []
        for v, stats in self.engine.scanner.vector_stats.items():
            if stats['found'] > 0:
                vector_summary.append(f"{v}={stats['found']}")

        print(f"\n  ┌─ STATUS [{elapsed_str}] ─────────────────────────────────────────┐")
        print(f"  │ Profits: {profits}/{TARGET_PROFITS} | Total P&L: ${total_profit:,.2f} | Phase: {phase}")
        print(f"  │ Positions: {positions} | Opps: found={opps_found} exec={opps_exec} skip={opps_skip}")
        if vector_summary:
            print(f"  │ Vectors: {', '.join(vector_summary)}")
        print(f"  │ Gas: {self._get_gas_str()} | Engine: {'RUNNING' if self.engine.is_running else 'STOPPED'}")
        print(f"  └──────────────────────────────────────────────────────────┘")

    def _get_gas_str(self) -> str:
        try:
            gs = self.engine.gas_optimizer.chain_states.get(1)
            if gs:
                return f"{gs.current_base_fee:.4f} gwei"
        except Exception:
            pass
        return "N/A"

    def _print_final_report(self):
        """Print comprehensive final report."""
        elapsed = time.time() - self.start_time
        count = len(self.confirmed_profits)
        total = sum(p.get('profit_usd', 0) for p in self.confirmed_profits)

        print()
        print("╔" + "═" * 68 + "╗")
        print("║" + " FINAL REPORT ".center(68) + "║")
        print("╠" + "═" * 68 + "╣")
        print("║" + f" Confirmed Profits: {count}/{TARGET_PROFITS}".ljust(68) + "║")
        print("║" + f" Total P&L: ${total:,.2f}".ljust(68) + "║")
        print("║" + f" Runtime: {timedelta(seconds=int(elapsed))}".ljust(68) + "║")
        print("║" + f" Ended: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}".ljust(68) + "║")
        print("╠" + "═" * 68 + "╣")

        for p in self.confirmed_profits:
            num = p.get('profit_number', '?')
            amt = p.get('profit_usd', 0)
            tx = p.get('tx_hash', 'N/A')[:20]
            blk = p.get('block_number', 'N/A')
            ts = p.get('timestamp', 'N/A')
            print("║" + f"  #{num}: ${amt:,.2f} | TX: {tx}... | Block: {blk} | {ts}".ljust(68) + "║")

        print("╚" + "═" * 68 + "╝")

        if self.engine:
            self.engine.print_full_dashboard()


# ════════════════════════════════════════════════════════════════════
#  ENTRY POINT
# ════════════════════════════════════════════════════════════════════

async def main():
    monitor = ProfitMonitor()

    # Handle signals gracefully
    loop = asyncio.get_event_loop()
    try:
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, lambda: monitor.shutdown_event.set())
    except (NotImplementedError, AttributeError):
        pass  # Windows

    await monitor.run()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n🛑 Monitor terminated.")
