#!/usr/bin/env python3
"""
MODULE 1 — Module 7: MEV Protection & Backrunning Strategy
Protects liquidation transactions from front-running and captures additional
profit by back-running oracle updates that trigger liquidations.

Features:
- Flashbots bundle submission (private mempool)
- bloXroute private transaction routing
- Oracle AnswerUpdated event monitoring
- Backrun bundle construction (oracle update + liquidation)
- Gas price bidding strategy for Flashbots
"""

import asyncio
import json
import logging
import os
import time
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass
from enum import Enum

from web3 import Web3
from eth_account import Account
from eth_account.messages import encode_defunct

from ..config_manager import ConfigManager, get_config

logger = logging.getLogger(__name__)


# ============================================================================
# DATA MODELS
# ============================================================================

class MEVRoute(Enum):
    """Transaction routing strategy"""
    DIRECT = "direct"                # Standard public mempool
    FLASHBOTS = "flashbots"          # Flashbots Protect relay
    FLASHBOTS_BUILDER = "flashbots_builder"  # Flashbots Builder API
    MEV_SHARE = "mev_share"          # Flashbots MEV-Share (order flow auction)
    BLOXROUTE = "bloxroute"          # bloXroute BDN private mempool
    MEV_BLOCKER = "mev_blocker"      # MEV Blocker (CoW Protocol)


@dataclass
class FlashbotsBundle:
    """A Flashbots bundle containing one or more transactions"""
    transactions: List[str]       # Signed raw transaction hex strings
    target_block: int             # Target block number
    min_timestamp: Optional[int] = None
    max_timestamp: Optional[int] = None
    reverting_tx_hashes: Optional[List[str]] = None

    def to_params(self) -> Dict:
        params = {
            "txs": self.transactions,
            "blockNumber": hex(self.target_block),
        }
        if self.min_timestamp is not None:
            params["minTimestamp"] = self.min_timestamp
        if self.max_timestamp is not None:
            params["maxTimestamp"] = self.max_timestamp
        if self.reverting_tx_hashes:
            params["revertingTxHashes"] = self.reverting_tx_hashes
        return params


@dataclass
class BundleResult:
    """Result of a Flashbots bundle submission"""
    success: bool
    bundle_hash: Optional[str] = None
    target_block: int = 0
    preflight_success: bool = False
    preflight_error: Optional[str] = None
    effective_gas_price: int = 0
    coinbase_diff: int = 0
    error_message: Optional[str] = None


@dataclass
class OracleBackrunSignal:
    """Signal generated when an oracle update creates liquidation opportunities"""
    oracle_address: str
    oracle_tx_hash: str
    asset: str
    old_price: int
    new_price: int
    price_change_percent: float
    affected_users: List[str]
    estimated_total_profit: float
    block_number: int
    timestamp: float


# ============================================================================
# MEV PROTECTION
# ============================================================================

class MEVProtection:
    """
    MEV protection and backrunning strategy for liquidation transactions.

    Provides:
    1. Private transaction routing via Flashbots / bloXroute
    2. Bundle construction for backrunning oracle updates
    3. Gas bidding strategy to ensure bundle inclusion
    4. Preflight verification before submission to avoid reverts
    """

    # Flashbots relay endpoint
    FLASHBOTS_RELAY = "https://relay.flashbots.net"

    # Flashbots MEV-Share (Matchmaker) relay endpoint
    MEV_SHARE_RELAY = "https://relay.flashbots.net"

    # Default Flashbots Builder API endpoint
    FLASHBOTS_BUILDER_URL = "https://builder0x69.io,https://rpc.beaverbuild.org,https://rsync-builder.xyz"

    # Chainlink AnswerUpdated event topic
    ANSWER_UPDATED_TOPIC = "0x" + Web3.keccak(
        text="AnswerUpdated(int256,uint256,uint256)"
    ).hex()

    # Known Chainlink aggregator addresses (Ethereum mainnet)
    CHAINLINK_AGGREGATORS = {
        "ETH/USD": "0x5f4eC3Df9cbd43714FE2740f5E3616155c5b8419",
        "BTC/USD": "0xF4030086522a5bEEa4988F8cA5B36dbC97BeE88c",
        "LINK/USD": "0x2c1d072e956AFFC0D435Cb7AC38EF18d24d9127c",
        "AAVE/USD": "0x547a514d5e3769680Ce22B2361c10Ea13619e8a9",
    }

    def __init__(self, config: Optional[ConfigManager] = None):
        self.config = config or get_config()

        # Flashbots auth signer (separate from execution key)
        self._flashbots_signer: Optional[Account] = None
        self._init_flashbots_signer()

        # Relay URLs
        self._flashbots_relay = os.getenv(
            "FLASHBOTS_RELAY_URL", self.FLASHBOTS_RELAY
        )
        self._bloxroute_url = os.getenv("BLOXROUTE_BACKRUNNING_URL", "")
        self._bloxroute_auth = os.getenv("BLOXROUTE_AUTH_HEADER", "")

        # MEV-Share relay (Flashbots Matchmaker)
        self._mev_share_relay = os.getenv(
            "MEV_SHARE_RELAY_URL", self.MEV_SHARE_RELAY
        )

        # Flashbots Builder API endpoints
        self._builder_urls: List[str] = [
            url.strip()
            for url in os.getenv(
                "FLASHBOTS_BUILDER_URLS",
                self.FLASHBOTS_BUILDER_URL,
            ).split(",")
            if url.strip()
        ]

        # Preferred routing (default to Flashbots)
        self.default_route = MEVRoute.FLASHBOTS

        # Oracle monitoring state
        self._oracle_signals: asyncio.Queue = asyncio.Queue()

        # Statistics
        self.stats = {
            "bundles_submitted": 0,
            "bundles_included": 0,
            "bundles_failed": 0,
            "mev_share_submitted": 0,
            "builder_submitted": 0,
            "backruns_detected": 0,
            "total_mev_protected_usd": 0.0,
            "start_time": time.time(),
        }

        logger.info("MEVProtection initialized")
        logger.info(f"  Flashbots relay: {self._flashbots_relay}")
        logger.info(f"  MEV-Share relay: {self._mev_share_relay}")
        logger.info(f"  Builder endpoints: {len(self._builder_urls)}")
        logger.info(f"  Default route: {self.default_route.value}")

    # ------------------------------------------------------------------
    # Initialisation helpers
    # ------------------------------------------------------------------

    def _init_flashbots_signer(self):
        """
        Create a random signer for Flashbots authentication.
        This does NOT need to hold funds — it only signs the relay request.
        """
        try:
            self._flashbots_signer = Account.create()
            logger.info(
                f"  Flashbots signer: {self._flashbots_signer.address}"
            )
        except Exception as e:
            logger.warning(f"Could not create Flashbots signer: {e}")

    # ------------------------------------------------------------------
    # Bundle construction
    # ------------------------------------------------------------------

    def build_liquidation_bundle(
        self,
        signed_liquidation_tx: str,
        target_block: int,
        oracle_tx_hash: Optional[str] = None,
    ) -> FlashbotsBundle:
        """
        Build a Flashbots bundle for a liquidation.

        If oracle_tx_hash is provided, the bundle will backrun that
        oracle update transaction.
        """
        txs = []

        # If backrunning an oracle update, include the trigger TX first
        if oracle_tx_hash:
            # The oracle TX is already in the mempool; Flashbots will
            # ensure it's included before our TX in the same block.
            txs.append(oracle_tx_hash)

        # Add our signed liquidation TX
        txs.append(signed_liquidation_tx)

        bundle = FlashbotsBundle(
            transactions=txs,
            target_block=target_block,
            # Allow the bundle to land in the next 3 blocks
            reverting_tx_hashes=[oracle_tx_hash] if oracle_tx_hash else None,
        )

        return bundle

    # ------------------------------------------------------------------
    # Bundle submission (Flashbots)
    # ------------------------------------------------------------------

    async def submit_bundle(
        self,
        bundle: FlashbotsBundle,
        w3: Web3,
    ) -> BundleResult:
        """
        Submit a bundle to the Flashbots relay.

        Steps:
          1. Verify the bundle
          2. If verification passes, send to relay
          3. Wait for target block to check inclusion
        """
        if not self._flashbots_signer:
            return BundleResult(
                success=False,
                error_message="Flashbots signer not initialised",
            )

        # -- Preflight verification --
        sim_result = await self._preflight_bundle(bundle, w3)
        if not sim_result.preflight_success:
            self.stats["bundles_failed"] += 1
            return sim_result

        # -- Submission --
        try:
            import aiohttp

            payload = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "eth_sendBundle",
                "params": [bundle.to_params()],
            }

            body = json.dumps(payload)
            signature = self._sign_flashbots_payload(body)

            headers = {
                "Content-Type": "application/json",
                "X-Flashbots-Signature": (
                    f"{self._flashbots_signer.address}:{signature}"
                ),
            }

            async with aiohttp.ClientSession() as session:
                async with session.post(
                    self._flashbots_relay, json=payload, headers=headers
                ) as resp:
                    data = await resp.json()

            if "error" in data:
                self.stats["bundles_failed"] += 1
                return BundleResult(
                    success=False,
                    target_block=bundle.target_block,
                    preflight_success=True,
                    error_message=data["error"].get("message", str(data["error"])),
                )

            bundle_hash = data.get("result", {}).get("bundleHash", "")
            self.stats["bundles_submitted"] += 1

            logger.info(
                f"📦 Bundle submitted — hash {bundle_hash[:16]}… "
                f"target block {bundle.target_block}"
            )

            return BundleResult(
                success=True,
                bundle_hash=bundle_hash,
                target_block=bundle.target_block,
                preflight_success=True,
            )

        except Exception as e:
            self.stats["bundles_failed"] += 1
            return BundleResult(
                success=False,
                target_block=bundle.target_block,
                error_message=str(e),
            )

    # ------------------------------------------------------------------
    # Bundle preflight verification
    # ------------------------------------------------------------------

    async def _preflight_bundle(
        self, bundle: FlashbotsBundle, w3: Web3
    ) -> BundleResult:
        """Verify a bundle via eth_callBundle on the Flashbots relay"""
        try:
            import aiohttp

            payload = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "eth_callBundle",
                "params": [
                    {
                        "txs": bundle.transactions,
                        "blockNumber": hex(bundle.target_block),
                        "stateBlockNumber": "latest",
                    }
                ],
            }

            body = json.dumps(payload)
            signature = self._sign_flashbots_payload(body)

            headers = {
                "Content-Type": "application/json",
                "X-Flashbots-Signature": (
                    f"{self._flashbots_signer.address}:{signature}"
                ),
            }

            async with aiohttp.ClientSession() as session:
                async with session.post(
                    self._flashbots_relay, json=payload, headers=headers
                ) as resp:
                    data = await resp.json()

            if "error" in data:
                return BundleResult(
                    success=False,
                    target_block=bundle.target_block,
                    preflight_success=False,
                    preflight_error=str(data["error"]),
                )

            result = data.get("result", {})
            coinbase_diff = int(result.get("coinbaseDiff", "0"), 16)
            gas_price = int(result.get("gasFees", "0"), 16)

            logger.info(
                f"✅ Bundle preflight verification passed — "
                f"coinbaseDiff {coinbase_diff / 1e18:.6f} ETH"
            )

            return BundleResult(
                success=True,
                target_block=bundle.target_block,
                preflight_success=True,
                effective_gas_price=gas_price,
                coinbase_diff=coinbase_diff,
            )

        except Exception as e:
            return BundleResult(
                success=False,
                target_block=bundle.target_block,
                preflight_success=False,
                preflight_error=str(e),
            )

    # ------------------------------------------------------------------
    # Flashbots authentication helper
    # ------------------------------------------------------------------

    def _sign_flashbots_payload(self, body: str) -> str:
        """Sign payload for Flashbots X-Flashbots-Signature header"""
        message = encode_defunct(text=Web3.keccak(text=body).hex())
        signed = self._flashbots_signer.sign_message(message)
        return signed.signature.hex()

    # ------------------------------------------------------------------
    # Oracle backrun monitoring
    # ------------------------------------------------------------------

    async def monitor_oracle_updates(self, w3: Web3):
        """
        Monitor Chainlink AnswerUpdated events.
        When a price update could trigger liquidations, emit a signal.
        """
        logger.info("📡 Starting oracle backrun monitor…")

        last_block = w3.eth.block_number

        while True:
            try:
                current_block = w3.eth.block_number
                if current_block <= last_block:
                    await asyncio.sleep(1)
                    continue

                # Scan for AnswerUpdated events across known oracles
                for name, address in self.CHAINLINK_AGGREGATORS.items():
                    try:
                        logs = w3.eth.get_logs({
                            "address": Web3.to_checksum_address(address),
                            "fromBlock": last_block + 1,
                            "toBlock": current_block,
                            "topics": [self.ANSWER_UPDATED_TOPIC],
                        })

                        for log in logs:
                            await self._process_oracle_log(name, log, w3)

                    except Exception as e:
                        logger.debug(f"Oracle scan error ({name}): {e}")

                last_block = current_block
                await asyncio.sleep(1)

            except Exception as e:
                logger.error(f"Oracle monitor error: {e}")
                await asyncio.sleep(5)

    async def _process_oracle_log(self, oracle_name: str, log, w3: Web3):
        """Process an AnswerUpdated event and check for backrun opportunities"""
        try:
            # Decode answer from event data
            new_price = int(log["data"][:66], 16) if log["data"] else 0

            signal = OracleBackrunSignal(
                oracle_address=log["address"],
                oracle_tx_hash=log["transactionHash"].hex(),
                asset=oracle_name,
                old_price=0,  # Would need previous round
                new_price=new_price,
                price_change_percent=0,
                affected_users=[],  # Populated by detector
                estimated_total_profit=0,
                block_number=log["blockNumber"],
                timestamp=time.time(),
            )

            self.stats["backruns_detected"] += 1
            await self._oracle_signals.put(signal)

            logger.info(
                f"💹 Oracle update: {oracle_name} = "
                f"{new_price / 1e8:.2f} (block {log['blockNumber']})"
            )

        except Exception as e:
            logger.debug(f"Oracle log parse error: {e}")

    async def get_oracle_signal(self, timeout: float = 5.0) -> Optional[OracleBackrunSignal]:
        """Get the next oracle backrun signal (or None on timeout)"""
        try:
            return await asyncio.wait_for(self._oracle_signals.get(), timeout=timeout)
        except asyncio.TimeoutError:
            return None

    # ------------------------------------------------------------------
    # Private TX routing (bloXroute)
    # ------------------------------------------------------------------

    async def send_private_transaction(
        self,
        signed_tx: str,
        w3: Web3,
        route: MEVRoute = MEVRoute.FLASHBOTS,
        max_block_number: Optional[int] = None,
        builders: Optional[List[str]] = None,
    ) -> Optional[str]:
        """
        Send a single transaction via a private mempool.

        For simple protection (no backrunning), this avoids the public
        mempool entirely.

        Routes:
          FLASHBOTS        — Flashbots Protect RPC (private, no bundle)
          FLASHBOTS_BUILDER — Send directly to block builders via Flashbots
          MEV_SHARE        — Flashbots MEV-Share order flow auction
          BLOXROUTE        — bloXroute BDN private mempool
          DIRECT           — Public mempool (no protection)
        """
        if route == MEVRoute.FLASHBOTS:
            return await self._send_via_flashbots_protect(signed_tx, w3)
        elif route == MEVRoute.MEV_SHARE:
            target_block = max_block_number or (w3.eth.block_number + 1)
            return await self._send_via_mev_share(signed_tx, w3, target_block)
        elif route == MEVRoute.FLASHBOTS_BUILDER:
            return await self._send_via_builders(signed_tx, w3, builders)
        elif route == MEVRoute.BLOXROUTE:
            return await self._send_via_bloxroute(signed_tx)
        else:
            # Direct — public mempool
            tx_hash = w3.eth.send_raw_transaction(bytes.fromhex(signed_tx.replace("0x", "")))
            return tx_hash.hex()

    async def _send_via_flashbots_protect(
        self, signed_tx: str, w3: Web3
    ) -> Optional[str]:
        """Send via Flashbots Protect RPC (simple private TX, no bundle)"""
        try:
            import aiohttp

            payload = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "eth_sendRawTransaction",
                "params": [signed_tx],
            }

            async with aiohttp.ClientSession() as session:
                async with session.post(
                    self._flashbots_relay, json=payload
                ) as resp:
                    data = await resp.json()

            if "result" in data:
                logger.info(f"🛡️ TX sent via Flashbots Protect: {data['result']}")
                return data["result"]

            logger.warning(f"Flashbots Protect error: {data}")
            return None

        except Exception as e:
            logger.error(f"Flashbots Protect send error: {e}")
            return None

    async def _send_via_bloxroute(self, signed_tx: str) -> Optional[str]:
        """Send via bloXroute private mempool"""
        if not self._bloxroute_url or not self._bloxroute_auth:
            logger.warning("bloXroute not configured")
            return None

        try:
            import aiohttp

            headers = {
                "Authorization": self._bloxroute_auth,
                "Content-Type": "application/json",
            }

            payload = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "blxr_tx",
                "params": {"transaction": signed_tx},
            }

            async with aiohttp.ClientSession() as session:
                async with session.post(
                    self._bloxroute_url.replace("wss://", "https://"),
                    json=payload,
                    headers=headers,
                ) as resp:
                    data = await resp.json()

            if "result" in data:
                logger.info(f"🛡️ TX sent via bloXroute: {data['result']}")
                return data["result"].get("txHash")

            return None

        except Exception as e:
            logger.error(f"bloXroute send error: {e}")
            return None

    # ------------------------------------------------------------------
    # MEV-Share / Matchmaker (Flashbots order flow auction)
    # ------------------------------------------------------------------

    async def _send_via_mev_share(
        self, signed_tx: str, w3: Web3, target_block: int
    ) -> Optional[str]:
        """
        Submit a transaction via Flashbots MEV-Share (Matchmaker).

        MEV-Share allows searchers to share MEV with users via an order
        flow auction. The bundle is submitted with privacy hints that
        control what information is revealed to other searchers.

        See: https://docs.flashbots.net/flashbots-mev-share/searchers/understanding-bundles
        """
        if not self._flashbots_signer:
            logger.warning("MEV-Share: Flashbots signer not initialized")
            return None

        try:
            import aiohttp

            # MEV-Share uses mev_sendBundle with inclusion and privacy hints
            bundle_params = {
                "version": "v0.1",
                "inclusion": {
                    "block": hex(target_block),
                    "maxBlock": hex(target_block + 5),
                },
                "body": [
                    {"tx": signed_tx, "canRevert": False},
                ],
                "privacy": {
                    "hints": [
                        "contract_address",
                        "function_selector",
                        "calldata",
                        "logs",
                        "hash",
                    ],
                    "builders": ["flashbots"],
                },
            }

            payload = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "mev_sendBundle",
                "params": [bundle_params],
            }

            body = json.dumps(payload)
            signature = self._sign_flashbots_payload(body)

            headers = {
                "Content-Type": "application/json",
                "X-Flashbots-Signature": (
                    f"{self._flashbots_signer.address}:{signature}"
                ),
            }

            async with aiohttp.ClientSession() as session:
                async with session.post(
                    self._mev_share_relay, json=payload, headers=headers
                ) as resp:
                    data = await resp.json()

            if "error" in data:
                logger.warning(f"MEV-Share error: {data['error']}")
                return None

            result = data.get("result", "")
            bundle_hash = result if isinstance(result, str) else result.get("bundleHash", "")
            self.stats["mev_share_submitted"] += 1

            logger.info(
                f"🔄 MEV-Share bundle submitted — hash {bundle_hash[:16]}… "
                f"target block {target_block}"
            )
            return bundle_hash

        except Exception as e:
            logger.error(f"MEV-Share send error: {e}")
            return None

    # ------------------------------------------------------------------
    # Flashbots Builder API (direct builder submission)
    # ------------------------------------------------------------------

    async def _send_via_builders(
        self,
        signed_tx: str,
        w3: Web3,
        builder_urls: Optional[List[str]] = None,
    ) -> Optional[str]:
        """
        Submit a signed transaction directly to Flashbots block builders.

        Sends eth_sendRawTransaction to multiple builder endpoints in
        parallel for maximum inclusion probability.

        See: https://docs.flashbots.net/flashbots-auction/advanced/builders
        """
        urls = builder_urls or self._builder_urls
        if not urls:
            logger.warning("No builder URLs configured")
            return None

        try:
            import aiohttp

            payload = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "eth_sendRawTransaction",
                "params": [signed_tx],
            }

            # Submit to all builders in parallel for best inclusion odds
            results: List[Optional[str]] = []
            async with aiohttp.ClientSession() as session:
                tasks = []
                for url in urls:
                    tasks.append(
                        self._submit_to_builder(session, url, payload)
                    )
                results = await asyncio.gather(*tasks, return_exceptions=True)

            # Return the first successful result
            for r in results:
                if isinstance(r, str) and r:
                    self.stats["builder_submitted"] += 1
                    logger.info(f"🏗️ TX sent to builders: {r[:16]}…")
                    return r

            logger.warning("All builder submissions failed")
            return None

        except Exception as e:
            logger.error(f"Builder API send error: {e}")
            return None

    async def _submit_to_builder(
        self, session, url: str, payload: Dict
    ) -> Optional[str]:
        """Submit a transaction to a single builder endpoint."""
        try:
            async with session.post(
                url, json=payload, timeout=aiohttp.ClientTimeout(total=5)
            ) as resp:
                data = await resp.json()
                if "result" in data:
                    return data["result"]
                return None
        except Exception:
            return None

    # ------------------------------------------------------------------
    # MEV-Share bundle with backrun (advanced)
    # ------------------------------------------------------------------

    async def submit_mev_share_backrun(
        self,
        backrun_signed_tx: str,
        pending_tx_hash: str,
        w3: Web3,
    ) -> Optional[str]:
        """
        Submit a backrun bundle via MEV-Share, referencing a pending
        transaction hash to execute after.

        This is the primary MEV-Share searcher flow: watch for pending
        transactions on the Matchmaker event stream, then submit a
        backrun bundle that executes after the target transaction.

        See: https://docs.flashbots.net/flashbots-mev-share/searchers/sending-bundles
        """
        if not self._flashbots_signer:
            return None

        target_block = w3.eth.block_number + 1

        try:
            import aiohttp

            bundle_params = {
                "version": "v0.1",
                "inclusion": {
                    "block": hex(target_block),
                    "maxBlock": hex(target_block + 3),
                },
                "body": [
                    {"hash": pending_tx_hash},
                    {"tx": backrun_signed_tx, "canRevert": False},
                ],
                "privacy": {
                    "builders": ["flashbots"],
                },
            }

            payload = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "mev_sendBundle",
                "params": [bundle_params],
            }

            body = json.dumps(payload)
            signature = self._sign_flashbots_payload(body)

            headers = {
                "Content-Type": "application/json",
                "X-Flashbots-Signature": (
                    f"{self._flashbots_signer.address}:{signature}"
                ),
            }

            async with aiohttp.ClientSession() as session:
                async with session.post(
                    self._mev_share_relay, json=payload, headers=headers
                ) as resp:
                    data = await resp.json()

            if "error" in data:
                logger.warning(f"MEV-Share backrun error: {data['error']}")
                return None

            result = data.get("result", "")
            bundle_hash = result if isinstance(result, str) else result.get("bundleHash", "")
            self.stats["mev_share_submitted"] += 1
            self.stats["backruns_detected"] += 1

            logger.info(
                f"🔄 MEV-Share backrun submitted — hash {bundle_hash[:16]}… "
                f"backrunning {pending_tx_hash[:16]}…"
            )
            return bundle_hash

        except Exception as e:
            logger.error(f"MEV-Share backrun error: {e}")
            return None

    # ------------------------------------------------------------------
    # Stats
    # ------------------------------------------------------------------

    def get_stats(self) -> Dict:
        uptime = time.time() - self.stats["start_time"]
        return {
            **self.stats,
            "uptime_seconds": uptime,
            "inclusion_rate": (
                self.stats["bundles_included"]
                / max(self.stats["bundles_submitted"], 1)
                * 100
            ),
        }
