#!/usr/bin/env python3
"""
enhanced_modules.module_7_mev_strategy.multi_block_mev
=======================================================
Multi-block MEV strategy for large liquidations that exceed
single-block gas limits or require oracle-update backruns
spanning consecutive blocks.

Supports:
  - Flashbots MegaBundle (multi-block bundles)
  - Multi-relay submission for maximum inclusion probability
  - Split liquidation across N and N+1 blocks
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum
from typing import Any, Dict, List, Optional

from enhanced_modules.common.base import EnhancedModule
from enhanced_modules.common.models import MEVBundle, EnrichedPosition

logger = logging.getLogger(__name__)


class MEVRelay(Enum):
    FLASHBOTS = "flashbots"
    EDEN = "eden"
    BLOXROUTE = "bloxroute"
    SECURERPC = "securerpc"
    ULTRASOUND = "ultrasound"
    AGNOSTIC = "agnostic"
    TITAN = "titan"
    BEAVERBUILD = "beaverbuild"
    BUILDER0X69 = "builder0x69"
    RSYNC = "rsync"
    PAYLOAD = "payload"
    BLOCKNATIVE = "blocknative"


class BundleStrategy(Enum):
    SINGLE_BLOCK = "single_block"
    MULTI_BLOCK = "multi_block"
    MEGA_BUNDLE = "mega_bundle"
    ORACLE_BACKRUN = "oracle_backrun"


@dataclass
class MultiBlockPlan:
    """A plan for executing across multiple blocks."""
    strategy: BundleStrategy
    bundles: List[MEVBundle]
    total_expected_profit_usd: Decimal
    relays: List[MEVRelay]
    target_blocks: List[int]  # [N, N+1, ...]
    is_urgent: bool
    max_block_range: int
    priority_fee_gwei: float
    created_at: float = field(default_factory=time.time)

    @property
    def block_span(self) -> int:
        return len(self.target_blocks)


@dataclass
class RelaySubmissionResult:
    """Result of submitting a bundle to a relay."""
    relay: MEVRelay
    success: bool
    bundle_hash: Optional[str] = None
    error: Optional[str] = None
    latency_ms: float = 0.0


class MultiBlockMEV(EnhancedModule):
    """
    Multi-block MEV execution engine.

    For liquidations too large for a single block or requiring
    oracle-update backruns, this engine splits execution across
    multiple blocks and submits to multiple relays.
    """

    # Block gas limit (approximate)
    BLOCK_GAS_LIMIT = 30_000_000
    # Max gas for a single liquidation TX
    MAX_TX_GAS = 15_000_000
    # Relay endpoints — FULL 12-BUILDER ROSTER (production)
    RELAY_ENDPOINTS = {
        MEVRelay.FLASHBOTS: "https://relay.flashbots.net",
        MEVRelay.EDEN: "https://api.edennetwork.io/v1/bundle",
        MEVRelay.BLOXROUTE: "https://mev.api.blxrbdn.com",
        MEVRelay.ULTRASOUND: "https://relay.ultrasound.money",
        MEVRelay.AGNOSTIC: "https://agnostic-relay.net",
        MEVRelay.TITAN: "https://rpc.titanbuilder.xyz",
        MEVRelay.BEAVERBUILD: "https://rpc.beaverbuild.org",
        MEVRelay.BUILDER0X69: "https://builder0x69.io",
        MEVRelay.RSYNC: "https://rsync-builder.xyz",
        MEVRelay.PAYLOAD: "https://rpc.payload.de",
        MEVRelay.BLOCKNATIVE: "https://api.blocknative.com/v1/auction",
        MEVRelay.SECURERPC: "https://api.securerpc.com/v1",
    }

    # Per-chain relay endpoints — L2 MEV protection
    L2_RELAY_ENDPOINTS = {
        42161: {  # Arbitrum
            "flashbots": "https://relay.flashbots.net",
            "titan": "https://rpc.titanbuilder.xyz",
        },
        10: {  # Optimism
            "flashbots": "https://relay.flashbots.net",
        },
        8453: {  # Base
            "flashbots": "https://relay.flashbots.net",
            "titan": "https://rpc.titanbuilder.xyz",
        },
        137: {  # Polygon
            "flashbuild": "https://rpc.flashbuild.io",
        },
        56: {  # BSC
            "48club": "https://rpc.48.club",
            "bloxroute": "https://mev.api.blxrbdn.com",
        },
    }

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        super().__init__("multi_block_mev", config)
        self._plans_created = 0
        self._submissions = 0
        self._inclusions = 0

    async def _on_start(self) -> None:
        logger.info(
            "[MultiBlockMEV] Initialized with %d relays",
            len(self.RELAY_ENDPOINTS),
        )

    async def _on_stop(self) -> None:
        inclusion_rate = self._inclusions / max(1, self._submissions)
        logger.info(
            "[MultiBlockMEV] Plans: %d, Submissions: %d, Inclusions: %d (%.1f%%)",
            self._plans_created, self._submissions,
            self._inclusions, inclusion_rate * 100,
        )

    # ── Plan Construction ──────────────────────────────────────

    async def create_plan(
        self,
        positions: List[EnrichedPosition],
        current_block: int,
        gas_per_liquidation: int = 400000,
        priority_fee_gwei: float = 2.0,
    ) -> MultiBlockPlan:
        """
        Create a multi-block execution plan for a set of positions.
        """
        total_gas = sum(gas_per_liquidation for _ in positions)

        # Determine strategy
        if total_gas <= self.MAX_TX_GAS:
            strategy = BundleStrategy.SINGLE_BLOCK
            block_count = 1
        elif total_gas <= self.BLOCK_GAS_LIMIT:
            strategy = BundleStrategy.MULTI_BLOCK
            block_count = 2
        else:
            strategy = BundleStrategy.MEGA_BUNDLE
            block_count = min(5, (total_gas // self.MAX_TX_GAS) + 1)

        target_blocks = list(range(current_block + 1, current_block + 1 + block_count))

        # Split positions across blocks
        bundles = self._split_into_bundles(
            positions, target_blocks, gas_per_liquidation, priority_fee_gwei
        )

        # Select relays based on urgency
        is_urgent = any(p.health_factor < 1.01 for p in positions)
        relays = self._select_relays(is_urgent)

        total_profit = sum(p.estimated_bonus_usd for p in positions)

        self._plans_created += 1
        self.record_success()

        return MultiBlockPlan(
            strategy=strategy,
            bundles=bundles,
            total_expected_profit_usd=total_profit,
            relays=relays,
            target_blocks=target_blocks,
            is_urgent=is_urgent,
            max_block_range=block_count,
            priority_fee_gwei=priority_fee_gwei,
        )

    async def create_oracle_backrun_plan(
        self,
        oracle_update_tx: Dict[str, Any],
        liquidation_position: EnrichedPosition,
        current_block: int,
        priority_fee_gwei: float = 3.0,
    ) -> MultiBlockPlan:
        """
        Create a backrun bundle that executes immediately after
        an oracle price update.
        """
        target_block = current_block + 1

        # Bundle: [oracle_update_tx, our_liquidation_tx]
        bundle = MEVBundle(
            bundle_id=f"backrun_{current_block}_{int(time.time() * 1000)}",
            chain_id=liquidation_position.chain_id,
            target_block=target_block,
            transactions=[
                oracle_update_tx,  # The oracle update we're backrunning
                {
                    "description": f"Liquidate {liquidation_position.borrower[:10]}...",
                    "gas_limit": 400000,
                    "priority_fee_gwei": priority_fee_gwei,
                },
            ],
            relay="flashbots",
            max_block_range=2,
            priority_fee_gwei=priority_fee_gwei,
            expected_profit_usd=liquidation_position.estimated_bonus_usd,
        )

        return MultiBlockPlan(
            strategy=BundleStrategy.ORACLE_BACKRUN,
            bundles=[bundle],
            total_expected_profit_usd=liquidation_position.estimated_bonus_usd,
            relays=[MEVRelay.FLASHBOTS, MEVRelay.ULTRASOUND],
            target_blocks=[target_block, target_block + 1],
            is_urgent=True,
            max_block_range=2,
            priority_fee_gwei=priority_fee_gwei,
        )

    # ── Submission ─────────────────────────────────────────────

    async def submit_plan(self, plan: MultiBlockPlan) -> List[RelaySubmissionResult]:
        """Submit a plan to all selected relays."""
        results: List[RelaySubmissionResult] = []

        for relay in plan.relays:
            start = time.time()
            try:
                result = await self._submit_to_relay(relay, plan)
                self._submissions += 1
                results.append(result)
            except Exception as exc:
                results.append(RelaySubmissionResult(
                    relay=relay, success=False, error=str(exc),
                    latency_ms=(time.time() - start) * 1000,
                ))

        successful = [r for r in results if r.success]
        if successful:
            logger.info(
                "[MultiBlockMEV] Submitted to %d/%d relays for blocks %s",
                len(successful), len(plan.relays), plan.target_blocks,
            )
        else:
            logger.warning("[MultiBlockMEV] All relay submissions failed")

        return results

    async def _submit_to_relay(
        self, relay: MEVRelay, plan: MultiBlockPlan
    ) -> RelaySubmissionResult:
        """Submit multi-block plan to a MEV relay via HTTP POST."""
        import aiohttp
        start = time.time()

        # Use the full RELAY_ENDPOINTS registry
        url = self.RELAY_ENDPOINTS.get(relay)
        if not url:
            return RelaySubmissionResult(
                relay=relay, success=False, bundle_hash="",
                latency_ms=(time.time() - start) * 1000,
                error=f"No URL configured for relay {relay.value}",
            )

        try:
            payload = {
                "jsonrpc": "2.0", "id": 1,
                "method": "eth_sendBundle",
                "params": [{
                    "txs": plan.raw_transactions,
                    "blockNumber": hex(plan.target_blocks[0]) if plan.target_blocks else "0x0",
                }],
            }
            async with aiohttp.ClientSession() as session:
                async with session.post(url, json=payload, timeout=aiohttp.ClientTimeout(total=5)) as resp:
                    data = await resp.json()

            latency = (time.time() - start) * 1000
            bundle_hash = data.get("result", {}).get("bundleHash", "")

            if "error" in data:
                return RelaySubmissionResult(
                    relay=relay, success=False, bundle_hash="",
                    latency_ms=latency,
                    error=str(data["error"]),
                )

            return RelaySubmissionResult(
                relay=relay,
                success=True,
                bundle_hash=bundle_hash,
                latency_ms=latency,
            )
        except Exception as e:
            return RelaySubmissionResult(
                relay=relay, success=False, bundle_hash="",
                latency_ms=(time.time() - start) * 1000,
                error=str(e),
            )

    # ── Internal ───────────────────────────────────────────────

    def _split_into_bundles(
        self,
        positions: List[EnrichedPosition],
        target_blocks: List[int],
        gas_per_liq: int,
        priority_fee: float,
    ) -> List[MEVBundle]:
        """Split positions into bundles for each target block."""
        bundles = []
        pos_per_block = max(1, len(positions) // len(target_blocks))

        for i, block in enumerate(target_blocks):
            start_idx = i * pos_per_block
            end_idx = start_idx + pos_per_block if i < len(target_blocks) - 1 else len(positions)
            block_positions = positions[start_idx:end_idx]

            if not block_positions:
                continue

            txs = [
                {
                    "borrower": p.borrower,
                    "protocol": p.protocol,
                    "debt_usd": float(p.debt_usd),
                    "gas_limit": gas_per_liq,
                }
                for p in block_positions
            ]

            profit = sum(p.estimated_bonus_usd for p in block_positions)

            bundles.append(MEVBundle(
                bundle_id=f"mb_{block}_{int(time.time() * 1000)}",
                chain_id=block_positions[0].chain_id,
                target_block=block,
                transactions=txs,
                relay="multi",
                max_block_range=1,
                priority_fee_gwei=priority_fee,
                expected_profit_usd=profit,
            ))

        return bundles

    def _select_relays(self, is_urgent: bool) -> List[MEVRelay]:
        """Select relays based on urgency.
        FULL CAPACITY: even non-urgent uses 5 relays for higher inclusion rate.
        """
        if is_urgent:
            return list(MEVRelay)  # All 12 relays for maximum inclusion
        # Standard: top 5 highest-inclusion relays
        return [
            MEVRelay.FLASHBOTS, MEVRelay.ULTRASOUND, MEVRelay.TITAN,
            MEVRelay.BEAVERBUILD, MEVRelay.BLOXROUTE,
        ]
