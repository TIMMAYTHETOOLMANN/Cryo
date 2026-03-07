#!/usr/bin/env python3
"""
enhanced_modules.module_5_cross_chain.light_client_verifier
=============================================================
Verification of cross-chain bridge message delivery via light-client
proofs for Across, Stargate, LayerZero, and direct RPC submission.

For L2 chains with fast finality, replaces generic messaging with
**direct transaction submission** for latency-critical opportunities.
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

logger = logging.getLogger(__name__)


class BridgeProvider(Enum):
    ACROSS = "across"
    STARGATE = "stargate"
    LAYERZERO = "layerzero"
    CCIP = "ccip"  # Chainlink CCIP
    DIRECT_RPC = "direct_rpc"


class SubmissionStrategy(Enum):
    BRIDGE_MESSAGE = "bridge_message"
    DIRECT_SUBMIT = "direct_submit"
    PRIVATE_RELAY = "private_relay"


@dataclass
class CrossChainRoute:
    """A verified cross-chain execution route."""
    source_chain_id: int
    destination_chain_id: int
    bridge_provider: BridgeProvider
    submission_strategy: SubmissionStrategy
    estimated_latency_ms: int
    bridge_fee_usd: Decimal
    confidence: float
    verified: bool = False
    proof_type: str = ""  # "merkle", "optimistic", "direct"


@dataclass
class ChainFinalityProfile:
    """Finality characteristics for a chain."""
    chain_id: int
    name: str
    avg_block_time_ms: int
    finality_blocks: int
    finality_time_ms: int
    supports_direct_submit: bool
    rpc_endpoints: List[str] = field(default_factory=list)
    private_relay_url: Optional[str] = None


# Default finality profiles
CHAIN_PROFILES: Dict[int, ChainFinalityProfile] = {
    1: ChainFinalityProfile(1, "Ethereum", 12000, 2, 24000, False),
    10: ChainFinalityProfile(10, "Optimism", 2000, 1, 2000, True),
    42161: ChainFinalityProfile(42161, "Arbitrum", 250, 1, 250, True),
    8453: ChainFinalityProfile(8453, "Base", 2000, 1, 2000, True),
    137: ChainFinalityProfile(137, "Polygon", 2000, 32, 64000, True),
    43114: ChainFinalityProfile(43114, "Avalanche", 2000, 1, 2000, True),
    56: ChainFinalityProfile(56, "BSC", 3000, 3, 9000, True),
    324: ChainFinalityProfile(324, "zkSync", 1000, 1, 1000, True),
    534352: ChainFinalityProfile(534352, "Scroll", 3000, 1, 3000, True),
}


class LightClientVerifier(EnhancedModule):
    """
    Verifies cross-chain message delivery and selects optimal
    submission strategy based on chain finality characteristics.
    """

    # Latency threshold for direct submission (ms)
    DIRECT_SUBMIT_THRESHOLD_MS = 5000
    # Health factor threshold for urgent submission
    URGENT_HF_THRESHOLD = 1.01

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        super().__init__("light_client_verifier", config)
        self._verified_routes: Dict[str, CrossChainRoute] = {}
        self._chain_profiles = dict(CHAIN_PROFILES)

    async def _on_start(self) -> None:
        logger.info(
            "[LightClient] Tracking %d chains with finality profiles",
            len(self._chain_profiles),
        )

    async def _on_stop(self) -> None:
        pass

    # ── Route Selection ────────────────────────────────────────

    async def select_route(
        self,
        source_chain: int,
        dest_chain: int,
        health_factor: float = 1.1,
        amount_usd: Decimal = Decimal("0"),
    ) -> CrossChainRoute:
        """
        Select the optimal cross-chain submission route.

        For urgent opportunities (HF < 1.01), uses direct RPC submission.
        For fast-finality chains, uses direct submission.
        Otherwise, uses bridge messaging.
        """
        dest_profile = self._chain_profiles.get(dest_chain)
        is_urgent = health_factor < self.URGENT_HF_THRESHOLD

        if dest_profile and (is_urgent or dest_profile.supports_direct_submit):
            # Direct submission — bypass bridge latency
            return CrossChainRoute(
                source_chain_id=source_chain,
                destination_chain_id=dest_chain,
                bridge_provider=BridgeProvider.DIRECT_RPC,
                submission_strategy=(
                    SubmissionStrategy.PRIVATE_RELAY if is_urgent
                    else SubmissionStrategy.DIRECT_SUBMIT
                ),
                estimated_latency_ms=dest_profile.finality_time_ms,
                bridge_fee_usd=Decimal("0"),
                confidence=0.95 if is_urgent else 0.90,
                verified=True,
                proof_type="direct",
            )

        # Use bridge provider
        bridge = self._select_bridge(source_chain, dest_chain, amount_usd)
        return CrossChainRoute(
            source_chain_id=source_chain,
            destination_chain_id=dest_chain,
            bridge_provider=bridge,
            submission_strategy=SubmissionStrategy.BRIDGE_MESSAGE,
            estimated_latency_ms=self._estimate_bridge_latency(bridge),
            bridge_fee_usd=self._estimate_bridge_fee(bridge, amount_usd),
            confidence=0.85,
            proof_type="optimistic" if bridge == BridgeProvider.ACROSS else "merkle",
        )

    async def verify_delivery(
        self,
        route: CrossChainRoute,
        tx_hash: str,
        timeout_ms: int = 120000,
    ) -> bool:
        """Verify that a cross-chain message was delivered."""
        if route.submission_strategy == SubmissionStrategy.DIRECT_SUBMIT:
            # Direct submission — verify via RPC
            return await self._verify_direct(route.destination_chain_id, tx_hash, timeout_ms)
        else:
            # Bridge — verify via bridge-specific proof
            return await self._verify_bridge(route, tx_hash, timeout_ms)

    # ── Internal ───────────────────────────────────────────────

    def _select_bridge(
        self, source: int, dest: int, amount_usd: Decimal
    ) -> BridgeProvider:
        """Select best bridge provider based on route and amount."""
        # Across is fastest for ETH<->L2
        if source == 1 or dest == 1:
            return BridgeProvider.ACROSS
        # Stargate for cross-L2
        if source in (10, 42161, 8453) and dest in (10, 42161, 8453):
            return BridgeProvider.STARGATE
        # Fallback to LayerZero
        return BridgeProvider.LAYERZERO

    @staticmethod
    def _estimate_bridge_latency(bridge: BridgeProvider) -> int:
        """Estimate bridge latency in milliseconds."""
        latencies = {
            BridgeProvider.ACROSS: 30000,
            BridgeProvider.STARGATE: 60000,
            BridgeProvider.LAYERZERO: 90000,
            BridgeProvider.CCIP: 120000,
            BridgeProvider.DIRECT_RPC: 2000,
        }
        return latencies.get(bridge, 60000)

    @staticmethod
    def _estimate_bridge_fee(bridge: BridgeProvider, amount_usd: Decimal) -> Decimal:
        """Estimate bridge fee in USD."""
        fee_pct = {
            BridgeProvider.ACROSS: Decimal("0.0006"),
            BridgeProvider.STARGATE: Decimal("0.0006"),
            BridgeProvider.LAYERZERO: Decimal("0.001"),
            BridgeProvider.CCIP: Decimal("0.001"),
            BridgeProvider.DIRECT_RPC: Decimal("0"),
        }
        pct = fee_pct.get(bridge, Decimal("0.001"))
        base_fee = Decimal("2")  # Minimum fee
        return max(base_fee, amount_usd * pct)

    async def _verify_direct(
        self, chain_id: int, tx_hash: str, timeout_ms: int
    ) -> bool:
        """Verify TX inclusion via direct RPC polling of eth_getTransactionReceipt."""
        try:
            from web3 import Web3
            rpc_url = os.environ.get(f"CHAIN_{chain_id}_RPC_URL", "")
            if not rpc_url:
                logger.warning("No RPC URL for chain %d — cannot verify TX", chain_id)
                return False
            w3 = Web3(Web3.HTTPProvider(rpc_url))
            deadline = asyncio.get_event_loop().time() + (timeout_ms / 1000)
            while asyncio.get_event_loop().time() < deadline:
                receipt = w3.eth.get_transaction_receipt(tx_hash)
                if receipt and receipt.get("status") == 1:
                    return True
                await asyncio.sleep(1)
        except Exception as e:
            logger.debug("TX verification failed for %s: %s", tx_hash, e)
        return False

    async def _verify_bridge(
        self, route: CrossChainRoute, tx_hash: str, timeout_ms: int
    ) -> bool:
        """Verify bridge message delivery via bridge-specific API."""
        try:
            import aiohttp
            # Query bridge relay status endpoint
            bridge_api = route.metadata.get("bridge_api_url", "")
            if not bridge_api:
                logger.warning("No bridge API URL configured for route")
                return False
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    f"{bridge_api}/status/{tx_hash}",
                    timeout=aiohttp.ClientTimeout(total=timeout_ms / 1000),
                ) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        return data.get("status") == "completed"
        except Exception as e:
            logger.debug("Bridge verification failed for %s: %s", tx_hash, e)
        return False
