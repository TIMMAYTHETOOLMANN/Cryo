#!/usr/bin/env python3
"""
Profit Monitor (Consolidated)
===============================
Watches for LiquidationExecuted events on all executor contracts.
Runs independently of the pipeline for visibility.

Consolidates:
  - liquidation_engine/monitor.py
  - liquidation_engine/simple_monitor.py
  - liquidation_engine/enhanced_monitor.py
  - liquidation_engine/comprehensive_monitor.py
  - liquidation_engine/check_status.py
"""

import logging
import os
import time
import json
from datetime import datetime
from typing import Dict, List, Optional

from web3 import Web3

from ..config.settings import get_config

logger = logging.getLogger(__name__)


class ProfitMonitor:
    """
    Unified profit monitor for all executor contracts.
    No gas required — purely listens for events.
    """

    EVENT_SIG_TEXT = "LiquidationExecuted(address,address,address,uint256,uint256,uint256)"

    def __init__(self, rpc_url: Optional[str] = None):
        cfg = get_config()
        self.rpc_url = rpc_url or cfg.get_chain(1).rpc_url if cfg.get_chain(1) else ""
        self.executors = {
            "V1": cfg.executor_v1,
            "V2": cfg.executor_v2,
            "Flash": cfg.flash_executor,
        }
        self.treasury = cfg.treasury_address
        self.w3 = Web3(Web3.HTTPProvider(self.rpc_url)) if self.rpc_url else None
        self.event_sig = ("0x" + self.w3.keccak(text=self.EVENT_SIG_TEXT).hex()) if self.w3 else ""

        self.profits_found = 0
        self.profit_events: List[Dict] = []
        self.start_block = self.w3.eth.block_number if self.w3 else 0

    def status_check(self):
        """Quick status check — print once and return."""
        if not self.w3:
            print("❌ No RPC connection")
            return

        block = self.w3.eth.block_number
        balance = self.w3.eth.get_balance(Web3.to_checksum_address(self.treasury)) / 1e18

        print()
        print("=" * 70)
        print("  PROFIT MONITOR — STATUS CHECK")
        print("=" * 70)
        print(f"  Block:    {block:,}")
        print(f"  Treasury: {balance:.6f} ETH ({self.treasury})")
        print()

        for name, addr in self.executors.items():
            if not addr:
                print(f"  {name}: ❌ not configured")
                continue
            code = self.w3.eth.get_code(Web3.to_checksum_address(addr))
            status = "✅ DEPLOYED" if len(code) > 0 else "❌ NO CODE"
            print(f"  {name}: {status} — {addr}")

        # Check for events since deployment
        for name, addr in self.executors.items():
            if not addr:
                continue
            try:
                logs = self.w3.eth.get_logs({
                    "address": Web3.to_checksum_address(addr),
                    "fromBlock": max(0, block - 100000),
                    "toBlock": block,
                    "topics": [self.event_sig],
                })
                if logs:
                    print(f"\n  🎉 {name}: {len(logs)} liquidation event(s) found!")
                else:
                    print(f"  {name}: No liquidation events in last 100k blocks")
            except Exception as e:
                print(f"  {name}: Event scan error — {e}")

        print("=" * 70)

    def run_loop(self, target_profits: int = 5, timeout_hours: int = 72,
                 scan_interval: int = 10):
        """Continuous monitoring loop."""
        if not self.w3:
            print("❌ No RPC connection")
            return

        print()
        print("=" * 70)
        print(f"  PROFIT MONITOR — SCANNING (target: {target_profits} profits)")
        print("=" * 70)

        last_block = self.w3.eth.block_number
        start = time.time()
        timeout = start + (timeout_hours * 3600)

        try:
            while self.profits_found < target_profits:
                if time.time() > timeout:
                    print(f"\n⏰ Timeout reached ({timeout_hours}h)")
                    break

                current = self.w3.eth.block_number
                if current <= last_block:
                    time.sleep(scan_interval)
                    continue

                for name, addr in self.executors.items():
                    if not addr:
                        continue
                    try:
                        logs = self.w3.eth.get_logs({
                            "address": Web3.to_checksum_address(addr),
                            "fromBlock": last_block + 1,
                            "toBlock": current,
                            "topics": [self.event_sig],
                        })
                        for log in logs:
                            self.profits_found += 1
                            self.profit_events.append({
                                "number": self.profits_found,
                                "executor": name,
                                "block": log["blockNumber"],
                                "tx": log["transactionHash"].hex(),
                                "timestamp": time.time(),
                            })
                            print(f"\n  🎉 PROFIT #{self.profits_found}/{target_profits} "
                                  f"on {name} at block {log['blockNumber']}")
                    except Exception as e:
                        logger.debug(f"Scan error ({name}): {e}")

                elapsed = (time.time() - start) / 60
                remaining = (timeout - time.time()) / 3600
                print(
                    f"  Block {current:,} | Profits {self.profits_found}/{target_profits} | "
                    f"Uptime {elapsed:.1f}m | Remaining {remaining:.1f}h",
                    end="\r",
                )

                last_block = current
                time.sleep(scan_interval)

        except KeyboardInterrupt:
            print(f"\n\n⏹️  Stopped by operator — {self.profits_found}/{target_profits} profits")

        # Final report
        print()
        print("=" * 70)
        if self.profits_found >= target_profits:
            print("  ✅ MISSION COMPLETE")
        else:
            print("  ⏸️  MONITORING PAUSED")
        print(f"  Profits: {self.profits_found}/{target_profits}")
        if self.profit_events:
            for p in self.profit_events:
                print(f"    #{p['number']}: {p['executor']} block {p['block']}")
        print("=" * 70)
