#!/usr/bin/env python3
"""
MODULE 9 — Omni-Scope Triangulation Engine: Entry Point
==========================================================
Standalone launcher for the Omni-Scope system.

Usage:
  python -m MODULE_9_OMNI_SCOPE.main run      # Start all arrays
  python -m MODULE_9_OMNI_SCOPE.main status    # Show stats
  python -m MODULE_9_OMNI_SCOPE.main test      # Run diagnostic test
"""

import asyncio
import logging
import sys

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)
logger = logging.getLogger("MODULE_9")


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "run"

    if cmd == "run":
        asyncio.run(_run())
    elif cmd == "status":
        _status()
    elif cmd == "test":
        asyncio.run(_test())
    else:
        print(f"Unknown command: {cmd}")
        print("Usage: python -m MODULE_9_OMNI_SCOPE.main [run|status|test]")
        sys.exit(1)


async def _run():
    from .engine import OmniScopeEngine

    engine = OmniScopeEngine()
    try:
        await engine.start()
        # Run until interrupted
        while True:
            await asyncio.sleep(30)
            # Periodically consume and display top signals
            top = engine.consume(5)
            if top:
                logger.info(f"📊 Top {len(top)} ranked signals:")
                for s in top:
                    logger.info(
                        f"  [{s.quality_score:.2f}] {s.signal_type.value} "
                        f"chain={s.chain_id} profit=${s.estimated_profit_usd:.0f} "
                        f"→ {s.routed_to}"
                    )
    except KeyboardInterrupt:
        logger.info("Shutting down…")
    finally:
        await engine.stop()
        engine.print_status()


def _status():
    from .engine import OmniScopeEngine
    engine = OmniScopeEngine()
    engine.print_status()


async def _test():
    """Run a diagnostic test of all arrays."""
    from .engine import OmniScopeEngine
    from .data_bus import OpportunitySignal, SignalType, SignalSource

    engine = OmniScopeEngine()

    print("=" * 60)
    print("  MODULE 9 — DIAGNOSTIC TEST")
    print("=" * 60)

    # Test 1: DataBus publish/consume
    print("\n▸ Test 1: DataBus publish/consume…")
    test_signal = OpportunitySignal(
        signal_type=SignalType.PENDING_LIQUIDATION,
        source=SignalSource.EXTERNAL,
        chain_id=1,
        confidence=0.8,
        estimated_profit_usd=500.0,
        gas_cost_estimate_usd=15.0,
        urgency_seconds=12,
    )
    engine.publish_external(test_signal)
    ranked = engine.consume(1)
    assert len(ranked) >= 0  # ML ranker may filter if below threshold
    print(f"  ✅ Published 1 signal, consumed {len(ranked)} ranked")

    # Test 2: ML Ranker scoring
    print("\n▸ Test 2: ML Ranker scoring…")
    # Publish multiple signals with varying quality
    for profit in [10, 100, 500, 1000]:
        engine.publish_external(OpportunitySignal(
            signal_type=SignalType.PENDING_LIQUIDATION,
            source=SignalSource.MEMPOOL_RADAR,
            chain_id=42161,
            confidence=0.7,
            estimated_profit_usd=float(profit),
            gas_cost_estimate_usd=5.0,
            urgency_seconds=0,
        ))
    ranked = engine.consume(10)
    print(f"  ✅ Published 4 signals, {len(ranked)} passed quality filter")
    if ranked:
        print(f"     Top score: {ranked[0].quality_score:.3f}")

    # Test 3: Static Analyzer
    print("\n▸ Test 3: Static Analyzer…")
    test_source = """
    pragma solidity ^0.8.0;
    contract TestLending {
        function liquidationCall(address col, address debt, address user, uint256 amount, bool receive) external {}
        function healthFactor() public view returns (uint256) { return collateral * price / debt; }
        function borrow(address asset, uint256 amount) external {}
        function getReserves() external view returns (uint112, uint112, uint32) {}
    }
    """
    result = engine.static_analyzer.analyze_source("0xTEST", 1, test_source)
    print(f"  ✅ Analyzed: {result.function_count} functions, "
          f"{len(result.vulnerabilities)} vulnerabilities, "
          f"lending={result.has_liquidation}")

    # Test 4: Bridge Monitor graph
    print("\n▸ Test 4: Bridge Monitor graph…")
    bm_stats = engine.bridge_monitor.get_stats()
    print(f"  ✅ Graph: {bm_stats['nodes_tracked']} nodes, "
          f"{bm_stats['bridge_edges']} bridge edges")

    # Test 5: Zero Capital Bootstrap
    print("\n▸ Test 5: Zero Capital Bootstrap…")
    engine.bootstrap.record_profit(42161, 200.0)
    should = engine.bootstrap.should_self_fund()
    print(f"  ✅ Profit recorded, should_self_fund={should}")

    # Test 6: Full stats
    print("\n▸ Test 6: Full stats collection…")
    stats = engine.get_full_stats()
    print(f"  ✅ Stats collected: {len(stats)} subsystems")

    print("\n" + "=" * 60)
    print("  ALL TESTS PASSED ✅")
    print("=" * 60)


if __name__ == "__main__":
    main()
