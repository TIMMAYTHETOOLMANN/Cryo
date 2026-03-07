#!/usr/bin/env python3
"""Diagnostic: test engine startup lifecycle."""
import asyncio
import io
import sys
import os

if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

sys.path.insert(0, os.path.dirname(__file__))
from dotenv import load_dotenv
load_dotenv(".env")

from MODULE_1_LIQUIDATION_ENGINE.profit_core.triangulated_profit_engine import (
    TriangulatedProfitEngine, build_default_config,
)


async def diagnose():
    config = build_default_config()
    # Respect env config — no longer force scan_only_mode

    engine = TriangulatedProfitEngine(config)

    # 1) Test scanner initialization
    print("\n=== PHASE 1: Scanner Initialize ===")
    await engine.scanner.initialize()
    chains = list(engine.scanner._w3.keys())
    print(f"Connected chains: {chains}")
    print(f"Chain count: {len(chains)}")

    if not chains:
        print("FATAL: No chains connected. Cannot scan.")
        return

    # 2) Test gas update
    print("\n=== PHASE 2: Gas Price Fetch ===")
    await engine._update_gas_prices()
    for cid, state in engine.gas_optimizer.chain_states.items():
        if state.last_updated > 0:
            print(f"  Chain {cid}: base={state.current_base_fee:.2f} gwei, "
                  f"priority={state.current_priority_fee:.2f} gwei, "
                  f"congestion={state.congestion_level}")

    # 3) Start the full engine for 10 seconds and observe
    print("\n=== PHASE 3: Full Engine Start (10s test) ===")
    engine_task = asyncio.create_task(engine.start())

    # Wait and observe
    for tick in range(10):
        await asyncio.sleep(1)
        print(f"  [tick {tick+1}/10] running={engine._running} "
              f"scans={engine.scanner.total_scans} "
              f"opps={engine.scanner.total_opportunities_found} "
              f"executed={engine._executed} skipped={engine._skipped}")

    # 4) Check if the engine task already finished (that's the bug)
    if engine_task.done():
        print("\n*** ENGINE TASK ALREADY FINISHED (this is the bug) ***")
        exc = engine_task.exception()
        if exc:
            print(f"  Exception: {exc}")
        else:
            print(f"  Result: {engine_task.result()}")
    else:
        print("\n  Engine task still running (GOOD)")

    # 5) Shutdown
    print("\n=== PHASE 4: Shutdown ===")
    await engine.stop()
    print("Done.")


if __name__ == "__main__":
    asyncio.run(diagnose())
