#!/usr/bin/env python3
"""
╔══════════════════════════════════════════════════════════════════════════╗
║  CRYO TACTICAL DEPLOYMENT — LOW CAPITAL MAXIMIZER                        ║
║                                                                          ║
║  Purpose:                                                                ║
║    Deploy every zero-capital profit vector at maximum aggression in      ║
║    the current ultra-low gas environment.                                ║
║                                                                          ║
║  Capital Assessment (live):                                              ║
║    • ETH Mainnet: ~0.008 ETH (~$15) @ 0.03 gwei → ~500 TXs budget      ║
║    • Base:        ~0.003 ETH (~$6)  @ 0.006 gwei → ~3000 TXs budget     ║
║    • Contracts:   V1 ✓  V2 ✓  FlashArb ✓  (all deployed, code present)  ║
║                                                                          ║
║  Strategy:                                                               ║
║    1. FLASH LOAN LIQUIDATIONS — 0% fee (Balancer) or 0.05% (Aave)       ║
║       Scan Aave V3, Compound V3 across ETH + Base for HF < 1.05         ║
║    2. RESERVE PROTOCOL ARB — ETH+ overcollateralization via flash loan   ║
║    3. ZERO-REVERT PIPELINE — Oracle-reactive, fires only on HF < 1.0    ║
║    4. DEX ARBITRAGE — Cross-fee-tier & triangular arbs via flash loan    ║
║    5. MEV BACKRUNNING — Detect large swaps, execute profitable backruns  ║
║    6. JIT LIQUIDATIONS — Just-in-time oracle-triggered execution         ║
║    7. MULTI-CHAIN — ETH + Base simultaneous scanning                     ║
║                                                                          ║
║  All vectors use FLASH LOANS = $0 capital required                       ║
║  Gas budget: ~$22 total → hundreds of execution attempts possible        ║
║                                                                          ║
║  Usage:                                                                  ║
║    python deploy_tactical.py                                             ║
║    python deploy_tactical.py --scan-only    # Detection only             ║
║    python deploy_tactical.py --diagnostic   # Run diagnostics first      ║
╚══════════════════════════════════════════════════════════════════════════╝
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import signal
import sys
import time
import traceback
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, List, Optional

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

from web3 import Web3
from eth_account import Account


# ════════════════════════════════════════════════════════════════════
#  LOGGING
# ════════════════════════════════════════════════════════════════════

def setup_logging():
    fmt = logging.Formatter(
        "%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    root = logging.getLogger()
    root.setLevel(logging.INFO)

    sh = logging.StreamHandler(sys.stdout)
    sh.setLevel(logging.INFO)
    sh.setFormatter(fmt)
    root.addHandler(sh)

    log_file = "cryo_tactical.log"
    fh = logging.FileHandler(log_file, encoding="utf-8")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(fmt)
    root.addHandler(fh)


logger = logging.getLogger("deploy_tactical")


# ════════════════════════════════════════════════════════════════════
#  LIVE CAPITAL ASSESSMENT
# ════════════════════════════════════════════════════════════════════

class CapitalAssessor:
    """
    Performs real-time assessment of available capital, gas costs,
    and executable opportunity count per chain.
    """

    # Chains where we have capital or can profit with zero capital
    PRIORITY_CHAINS = {
        1:    {"name": "Ethereum", "rpc_env": "ETH_RPC_URL"},
        8453: {"name": "Base",     "rpc_env": "BASE_RPC_URL"},
        42161: {"name": "Arbitrum", "rpc_env": "ARBITRUM_RPC_URL"},
        10:   {"name": "Optimism", "rpc_env": "OPTIMISM_RPC_URL"},
    }

    def __init__(self):
        self.wallet_address = os.getenv("TREASURY_ADDRESS", "")
        self.private_key = os.getenv("PRIVATE_KEY", "")
        self._w3: Dict[int, Web3] = {}
        self.chain_status: Dict[int, Dict[str, Any]] = {}
        self.total_capital_usd = Decimal("0")
        self.total_tx_budget = 0
        self.eth_price_usd = Decimal("2000")  # Updated live

    def assess(self) -> bool:
        """Run full assessment. Returns True if at least one chain is executable."""
        print()
        print("=" * 72)
        print("  TACTICAL CAPITAL ASSESSMENT")
        print("=" * 72)

        # Connect to RPCs
        for chain_id, meta in self.PRIORITY_CHAINS.items():
            rpc_url = os.getenv(meta["rpc_env"], "")
            if not rpc_url:
                continue
            try:
                w3 = Web3(Web3.HTTPProvider(rpc_url, request_kwargs={"timeout": 10}))
                if w3.is_connected():
                    self._w3[chain_id] = w3
            except Exception:
                pass

        if not self._w3:
            print("  FATAL: No RPC connections available.")
            return False

        # Get ETH price from chain 1
        if 1 in self._w3:
            try:
                chainlink_eth_usd = "0x5f4eC3Df9cbd43714FE2740f5E3616155c5b8419"
                abi = [{"inputs": [], "name": "latestRoundData", "outputs": [
                    {"name": "roundId", "type": "uint80"},
                    {"name": "answer", "type": "int256"},
                    {"name": "startedAt", "type": "uint256"},
                    {"name": "updatedAt", "type": "uint256"},
                    {"name": "answeredInRound", "type": "uint80"}
                ], "stateMutability": "view", "type": "function"}]
                contract = self._w3[1].eth.contract(
                    address=Web3.to_checksum_address(chainlink_eth_usd), abi=abi)
                _, answer, _, _, _ = contract.functions.latestRoundData().call()
                self.eth_price_usd = Decimal(str(answer)) / Decimal("1e8")
                print(f"  ETH Price (Chainlink): ${self.eth_price_usd:.2f}")
            except Exception as e:
                print(f"  ETH price fetch failed: {e}, using $2000 default")

        # Assess each chain
        any_executable = False
        for chain_id, w3 in self._w3.items():
            meta = self.PRIORITY_CHAINS[chain_id]
            try:
                bal_wei = w3.eth.get_balance(Web3.to_checksum_address(self.wallet_address))
                bal_eth = Decimal(str(bal_wei)) / Decimal("1e18")
                gas_price_wei = w3.eth.gas_price
                gas_price_gwei = Decimal(str(gas_price_wei)) / Decimal("1e9")
                block = w3.eth.block_number

                # Estimate cost per TX (500K gas typical liquidation)
                gas_per_tx = 500_000
                cost_per_tx_eth = (Decimal(str(gas_per_tx)) * Decimal(str(gas_price_wei))) / Decimal("1e18")
                cost_per_tx_usd = cost_per_tx_eth * self.eth_price_usd
                tx_budget = int(bal_eth / cost_per_tx_eth) if cost_per_tx_eth > 0 else 0
                bal_usd = bal_eth * self.eth_price_usd

                executable = bal_eth > cost_per_tx_eth * 2  # Need at least 2 TXs worth

                status = {
                    "name": meta["name"],
                    "block": block,
                    "balance_eth": float(bal_eth),
                    "balance_usd": float(bal_usd),
                    "gas_gwei": float(gas_price_gwei),
                    "cost_per_tx_usd": float(cost_per_tx_usd),
                    "tx_budget": tx_budget,
                    "executable": executable,
                }
                self.chain_status[chain_id] = status

                icon = "+" if executable else "!"
                print(f"  [{icon}] {meta['name']:12s}  "
                      f"bal={bal_eth:.6f} ETH (${bal_usd:.2f})  "
                      f"gas={gas_price_gwei:.4f} gwei  "
                      f"tx_cost=${cost_per_tx_usd:.4f}  "
                      f"budget={tx_budget} TXs  "
                      f"block={block}")

                self.total_capital_usd += bal_usd
                self.total_tx_budget += tx_budget
                if executable:
                    any_executable = True

            except Exception as e:
                print(f"  [!] {meta['name']:12s}  ERROR: {e}")

        # Contract verification
        print()
        print("  -- CONTRACT STATUS --")
        contract_addrs = {
            "LiquidationExecutor V1": os.getenv("LIQUIDATION_EXECUTOR_V1"),
            "LiquidationExecutor V2": os.getenv("LIQUIDATION_EXECUTOR_V2"),
            "FlashLoanArbitrage":     os.getenv("FLASH_EXECUTOR"),
        }
        for name, addr in contract_addrs.items():
            if addr and 1 in self._w3:
                try:
                    code = self._w3[1].eth.get_code(Web3.to_checksum_address(addr))
                    has_code = len(code) > 2
                    print(f"  [{'+'if has_code else '!'}] {name:30s} {addr[:16]}... ({len(code)} bytes)")
                except Exception as e:
                    print(f"  [!] {name:30s} ERROR: {e}")

        # Summary
        print()
        print("  -- SUMMARY --")
        print(f"  Total Capital:       ${self.total_capital_usd:.2f}")
        print(f"  Total TX Budget:     {self.total_tx_budget} transactions")
        print(f"  Executable Chains:   {sum(1 for s in self.chain_status.values() if s['executable'])}")
        print(f"  Flash Loan Capital:  UNLIMITED (Balancer 0%, Aave 0.05%)")
        print(f"  All capital needed:  GAS ONLY — all profit vectors are flash-loan-funded")
        print("=" * 72)
        print()

        return any_executable


# ════════════════════════════════════════════════════════════════════
#  TACTICAL CONFIG BUILDER
# ════════════════════════════════════════════════════════════════════

def build_tactical_config(assessor: CapitalAssessor) -> Dict[str, Any]:
    """
    Build a config optimized for the current capital & gas situation.
    Heavily favors chains with lowest gas and highest TX budget.
    """
    from MODULE_1_LIQUIDATION_ENGINE.profit_core.triangulated_profit_engine import (
        build_default_config,
    )

    config = build_default_config()

    # ── Force LIVE execution ──────────────────────────────────
    config["execution"]["scan_only_mode"] = False
    config["wallet"]["execution_enabled"] = True

    # ── Ultra-aggressive thresholds (accept any profit) ───────
    config["scanner"]["min_profit_usd"] = 0.01    # Accept $0.01 profit
    config["scanner"]["min_confidence"] = 0.05     # Very low confidence floor
    config["scanner"]["scan_interval"] = 0.5       # Max speed
    config["scanner"]["max_watchlist_size"] = 5000  # Track more positions
    config["scanner"]["recon_sweep_seconds"] = 10   # Fast recon phase (was 120s)

    # ── Gas: match the ultra-low environment ──────────────────
    config["gas"]["gas_price_cap_gwei"] = 5.0       # Hard cap at 5 gwei (currently 0.03)
    config["gas"]["max_priority_fee_gwei"] = 0.5    # Minimal tip
    config["gas"]["max_fee_per_gas_gwei"] = 10.0    # Safety ceiling
    config["gas"]["min_margin_percent"] = 0.005     # 0.5% margin (gas is near-free)
    config["gas"]["min_margin_usd"] = 0.01          # $0.01 minimum margin
    config["gas"]["gas_limit_buffer"] = 1.1         # Tight buffer (save gas)

    # ── Execution: ultra-aggressive ───────────────────────────
    config["execution"]["liquidation"]["min_profit_usd"] = 0.01
    config["execution"]["liquidation"]["min_debt_usd"] = 50     # Lower floor
    config["execution"]["liquidation"]["health_factor_threshold"] = 1.05

    # ── Reinvestment: 90% (maximize compounding) ──────────────
    config["multiplier"]["reinvest_rate"] = 0.90
    config["multiplier"]["trades_per_hour"] = 200

    # ── Chain priority based on live assessment ───────────────
    # Sort chains by TX budget (cheapest gas first)
    chain_priority = sorted(
        assessor.chain_status.items(),
        key=lambda x: x[1].get("tx_budget", 0),
        reverse=True,
    )
    active_chains = [cid for cid, status in chain_priority if status.get("executable")]

    logger.info("Chain execution priority: %s",
                [(cid, assessor.chain_status[cid]["name"]) for cid in active_chains])

    return config


# ════════════════════════════════════════════════════════════════════
#  SUPERVISED SUBSYSTEM (auto-restart)
# ════════════════════════════════════════════════════════════════════

class SupervisedTask:
    """Auto-restarts a coroutine on crash with exponential backoff."""

    MAX_BACKOFF = 30.0

    def __init__(self, name: str, factory, shutdown_event: asyncio.Event):
        self.name = name
        self._factory = factory
        self._shutdown = shutdown_event
        self._failures = 0

    async def run_forever(self):
        while not self._shutdown.is_set():
            try:
                logger.info("[sup] Starting: %s", self.name)
                await self._factory()
                self._failures = 0
                if not self._shutdown.is_set():
                    logger.warning("[sup] %s exited cleanly — restarting", self.name)
            except asyncio.CancelledError:
                return
            except Exception as exc:
                self._failures += 1
                logger.error("[sup] %s crashed (#%d): %s", self.name, self._failures, exc)
                logger.debug(traceback.format_exc())

            if self._shutdown.is_set():
                return

            delay = min(2 ** (self._failures - 1), self.MAX_BACKOFF)
            logger.info("[sup] Restarting %s in %.1fs", self.name, delay)
            try:
                await asyncio.wait_for(self._shutdown.wait(), timeout=delay)
                return
            except asyncio.TimeoutError:
                pass


# ════════════════════════════════════════════════════════════════════
#  TACTICAL DEPLOYMENT
# ════════════════════════════════════════════════════════════════════

async def deploy_tactical(scan_only: bool = False, diagnostic: bool = False):
    """
    TACTICAL DEPLOYMENT — Every zero-capital profit vector at maximum aggression.

    Engines deployed:
      1. TriangulatedProfitEngine — 12-vector scanner + flash loan router + gas optimizer
      2. MasterProfitOrchestrator — all modules concurrent (pipeline, omni-scope,
         omni-channel, enhanced modules 1-8, RPC gateway, timing engine)
      3. ZeroRevertPipeline — oracle-reactive execution (fire when HF math guarantees <1.0)

    All flash-loan-funded. Gas-only capital required (~$22 available).
    """

    # ── Step 1: Capital Assessment ─────────────────────────────
    assessor = CapitalAssessor()
    can_execute = assessor.assess()

    if not can_execute and not scan_only:
        print("  WARNING: No chain has sufficient gas for execution.")
        print("  Falling back to SCAN-ONLY mode to detect opportunities.")
        scan_only = True

    # ── Step 2: Run diagnostic if requested ────────────────────
    if diagnostic:
        print()
        print("=" * 72)
        print("  RUNNING DIAGNOSTIC (10s engine test)...")
        print("=" * 72)
        try:
            from _diagnose import diagnose
            await diagnose()
        except Exception as e:
            logger.warning("Diagnostic failed: %s", e)
        print()

    # ── Step 3: Build optimized config ─────────────────────────
    config = build_tactical_config(assessor)

    if scan_only:
        config["execution"]["scan_only_mode"] = True
        config["wallet"]["execution_enabled"] = False
        os.environ["SCAN_ONLY_MODE"] = "true"

    # Force ultra-low min profit env var for all downstream consumers
    os.environ["MIN_PROFIT_USD"] = "0.01"

    # ── Step 4: Import engines ─────────────────────────────────
    from MODULE_1_LIQUIDATION_ENGINE.profit_core.triangulated_profit_engine import (
        TriangulatedProfitEngine,
    )
    from master_orchestrator import MasterProfitOrchestrator

    # ── Step 5: Build engine instances ─────────────────────────
    engine = TriangulatedProfitEngine(config)
    orchestrator = MasterProfitOrchestrator({
        "scan_only": scan_only,
        "cycle_interval": 0.5,       # Fast cycles
        "min_profit_usd": 0.01,      # Accept any profit
        "execution_enabled": not scan_only,
        "reinvest_rate": 0.90,
    })

    # ── Step 5b: Bridge engine→orchestrator signal flow ────────
    # The engine's scanner discovers opportunities via 6 vectors.  Feed these
    # into the orchestrator's aggregation queue so the dashboard reflects
    # system-wide activity and signals get deduplicated across both systems.
    async def _bridge_signal(signal):
        """Forward scanner signals to orchestrator's aggregation queue."""
        try:
            chain_id = getattr(signal, "chain_id", 1)
            sig_type = getattr(signal, "signal_type", "unknown")
            if hasattr(sig_type, "value"):
                sig_type = sig_type.value
            target = getattr(signal, "user_address", "") or getattr(signal, "target_contract", "")
            profit = getattr(signal, "net_profit_usd", None) or getattr(signal, "expected_value_usd", 0)
            conf = getattr(signal, "confidence", 0)
            await orchestrator._enqueue_signal(
                source="triangulated_engine",
                signal_type=sig_type,
                chain_id=chain_id,
                target=target,
                estimated_profit_usd=float(profit),
                confidence=float(conf),
                raw_signal=signal,
            )
        except Exception:
            pass  # Non-critical — don't break the engine's pipeline

    engine.scanner.on_opportunity(_bridge_signal)

    # ── Step 6: Shutdown coordination ──────────────────────────
    shutdown_event = asyncio.Event()

    def _signal_handler(*_):
        print("\n")
        print("  +=======================================+")
        print("  |  TACTICAL SHUTDOWN SIGNAL RECEIVED    |")
        print("  +=======================================+")
        shutdown_event.set()

    loop = asyncio.get_event_loop()
    try:
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, _signal_handler)
    except (NotImplementedError, AttributeError):
        pass  # Windows — KeyboardInterrupt still works

    # ── Step 7: Print deployment banner ────────────────────────
    mode = "SCAN ONLY" if scan_only else "LIVE EXECUTION"
    exec_chains = [
        f"{s['name']}({cid})" for cid, s in assessor.chain_status.items()
        if s.get("executable")
    ]

    print()
    print("+" + "=" * 70 + "+")
    print("|" + " CRYO TACTICAL DEPLOYMENT ".center(70) + "|")
    print("+" + "=" * 70 + "+")
    print("|" + f"  Mode:         {mode}".ljust(70) + "|")
    print("|" + f"  Started:      {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}".ljust(70) + "|")
    print("|" + f"  Capital:      ${assessor.total_capital_usd:.2f} (gas only)".ljust(70) + "|")
    print("|" + f"  TX Budget:    {assessor.total_tx_budget} transactions".ljust(70) + "|")
    print("|" + f"  Flash Loans:  Balancer (0%), Aave (0.05%), Maker (0%), DODO (0%)".ljust(70) + "|")
    print("|" + f"  Chains:       {', '.join(exec_chains) or 'None (scan-only)'}".ljust(70) + "|")
    print("|" + f"  Min Profit:   $0.01".ljust(70) + "|")
    print("|" + f"  Reinvest:     90%".ljust(70) + "|")
    print("|" + f"  ETH Price:    ${assessor.eth_price_usd:.2f}".ljust(70) + "|")
    print("|" + "".ljust(70) + "|")
    print("|" + "  PROFIT VECTORS:".ljust(70) + "|")
    print("|" + "    1. Flash Loan Liquidations (Aave V3, Compound V3, MakerDAO)".ljust(70) + "|")
    print("|" + "    2. Reserve Protocol Over-Collateralization Arbitrage".ljust(70) + "|")
    print("|" + "    3. Zero-Revert Pipeline (oracle-reactive, 0 preflight)".ljust(70) + "|")
    print("|" + "    4. DEX Arbitrage (cross-fee-tier, triangular)".ljust(70) + "|")
    print("|" + "    5. MEV Backrunning (Flashbots, 12 builders)".ljust(70) + "|")
    print("|" + "    6. JIT Liquidations (Module 11 timing optimizer)".ljust(70) + "|")
    print("|" + "    7. Cross-Chain Arbitrage (ETH <-> Base pathfinder)".ljust(70) + "|")
    print("|" + "    8. NFT Liquidation Scanner (BendDAO, NFTfi)".ljust(70) + "|")
    print("|" + "    9. Flash Loan Surplus Utilization (secondary arbs in-TX)".ljust(70) + "|")
    print("|" + "   10. Omni-Scope 5-Array Detector (ML-ranked signals)".ljust(70) + "|")
    print("+" + "=" * 70 + "+")
    print()

    # ── Step 8: Launch supervised engines ──────────────────────
    engine_sup = SupervisedTask("triangulated_engine", engine.start, shutdown_event)
    orchestrator_sup = SupervisedTask("master_orchestrator",
                                      lambda: orchestrator.run(), shutdown_event)

    # Heartbeat: periodic status print + metric flush
    async def heartbeat():
        cycle = 0
        while not shutdown_event.is_set():
            try:
                await asyncio.wait_for(shutdown_event.wait(), timeout=30.0)
                break
            except asyncio.TimeoutError:
                pass

            cycle += 1
            # Pull stats from both engines
            eng_stats = {
                "scans": getattr(engine.scanner, "total_scans", 0),
                "opps": getattr(engine.scanner, "total_opportunities_found", 0),
                "executed": getattr(engine, "_executed", 0),
                "skipped": getattr(engine, "_skipped", 0),
            }
            orch_stats = orchestrator.get_stats() if hasattr(orchestrator, "get_stats") else {}

            logger.info(
                "[heartbeat #%d] engine: scans=%d opps=%d exec=%d skip=%d | "
                "orchestrator: signals=%s exec_ok=%s profit=$%s",
                cycle,
                eng_stats["scans"], eng_stats["opps"],
                eng_stats["executed"], eng_stats["skipped"],
                orch_stats.get("signals_received", "?"),
                orch_stats.get("executions_succeeded", "?"),
                orch_stats.get("net_profit_usd", "0"),
            )

    tasks = [
        asyncio.create_task(engine_sup.run_forever(), name="sup:engine"),
        asyncio.create_task(orchestrator_sup.run_forever(), name="sup:orchestrator"),
        asyncio.create_task(heartbeat(), name="heartbeat"),
        asyncio.create_task(shutdown_event.wait(), name="shutdown_watcher"),
    ]

    logger.info("All engines launched. Monitoring...")

    try:
        done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in done:
            if task.get_name() == "shutdown_watcher":
                logger.info("Shutdown requested")
            elif task.exception() and not isinstance(task.exception(), asyncio.CancelledError):
                logger.error("Task %s failed: %s", task.get_name(), task.exception())
    except KeyboardInterrupt:
        logger.info("KeyboardInterrupt")
    finally:
        shutdown_event.set()
        print("\n  Shutting down all engines...")
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

        # Graceful cleanup
        for obj, name in [(engine, "engine"), (orchestrator, "orchestrator")]:
            try:
                await obj.stop()
            except Exception as exc:
                logger.warning("Error stopping %s: %s", name, exc)

        # Final report
        print()
        print("  -- FINAL REPORT --")
        try:
            orch_stats = orchestrator.get_stats()
            print(f"  Signals received:     {orch_stats.get('signals_received', 0)}")
            print(f"  Signals deduplicated: {orch_stats.get('signals_deduplicated', 0)}")
            print(f"  Signals executed:     {orch_stats.get('signals_executed', 0)}")
            print(f"  Executions OK:        {orch_stats.get('executions_succeeded', 0)}")
            print(f"  Executions FAIL:      {orch_stats.get('executions_failed', 0)}")
            print(f"  Net Profit:           ${orch_stats.get('net_profit_usd', '0')}")
            print(f"  Active Modules:       {', '.join(orch_stats.get('active_modules', []))}")
        except Exception:
            pass
        print("\n  All engines stopped. Tactical deployment complete.")

    return 0


# ════════════════════════════════════════════════════════════════════
#  MAIN
# ════════════════════════════════════════════════════════════════════

def main():
    setup_logging()

    scan_only = "--scan-only" in sys.argv
    diagnostic = "--diagnostic" in sys.argv

    print()
    print("+==================================================================+")
    print("|  CRYO TACTICAL DEPLOYMENT — LOW CAPITAL MAXIMIZER               |")
    print("|  Flash-loan-funded zero-capital profit extraction                |")
    print("|  Every vector. Maximum aggression. Persistent supervision.       |")
    print("+==================================================================+")
    print()

    if scan_only:
        print("  MODE: SCAN ONLY (detection without execution)")
    else:
        print("  MODE: LIVE EXECUTION")
    print()

    try:
        result = asyncio.run(deploy_tactical(scan_only=scan_only, diagnostic=diagnostic))
        sys.exit(result or 0)
    except KeyboardInterrupt:
        print("\n  Deployment terminated.")
        sys.exit(0)


if __name__ == "__main__":
    main()
