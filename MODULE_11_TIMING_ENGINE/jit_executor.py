#!/usr/bin/env python3
"""
Submodule 11.4 — Just-in-Time Transaction Executor (v2)
=========================================================
Manages a pool of **pre-signed** liquidation transactions and submits them
through the optimal channel the instant the on-chain condition is confirmed.

Submission Channels (in priority order):
  1. **Flashbots Bundle** — private, guaranteed ordering, no revert risk.
  2. **Private Relays** — bloXroute, Eden, Merkle — lower latency.
  3. **Public Mempool** — fastest raw send, but visible to other bots.

Flow:
  1. ``prepare_transaction()``  — Build + sign a liquidation TX, verify at
     ``pending`` block state.  Store in the pre-signed pool.
  2. ``on_threshold_crossed()`` — Invoked by oracle watcher / mempool sniffer
     when HF confirmed < 1.0.  Selects the fastest channel and fires.
  3. ``on_receipt()``           — Track confirmation, update stats, record profit.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

import aiohttp
from web3 import Web3

from .config import JITExecutorConfig

logger = logging.getLogger(__name__)

# ── ABI Fragments ────────────────────────────────────────────────

AAVE_LIQUIDATION_ABI = json.loads('''[{
    "inputs": [
        {"name": "collateralAsset", "type": "address"},
        {"name": "debtAsset", "type": "address"},
        {"name": "user", "type": "address"},
        {"name": "debtToCover", "type": "uint256"},
        {"name": "receiveAToken", "type": "bool"}
    ],
    "name": "liquidationCall",
    "outputs": [],
    "stateMutability": "nonpayable",
    "type": "function"
}]''')

AAVE_USER_DATA_ABI = json.loads('''[
    {"inputs":[{"name":"user","type":"address"}],"name":"getUserAccountData","outputs":[
        {"name":"totalCollateralBase","type":"uint256"},
        {"name":"totalDebtBase","type":"uint256"},
        {"name":"availableBorrowsBase","type":"uint256"},
        {"name":"currentLiquidationThreshold","type":"uint256"},
        {"name":"ltv","type":"uint256"},
        {"name":"healthFactor","type":"uint256"}
    ],"stateMutability":"view","type":"function"}
]''')


@dataclass
class PreSignedTx:
    """A pre-built, pre-signed liquidation transaction ready for broadcast."""
    position_key: str
    user_address: str
    chain_id: int
    pool_address: str
    collateral_asset: str
    debt_asset: str
    debt_to_cover: int
    raw_signed_tx: bytes
    tx_hash_hex: str
    gas_price_gwei: float
    estimated_profit_usd: float
    trigger_type: str = ""        # "oracle_update" | "mempool_backrun" | "prediction"
    trigger_tx_hash: str = ""     # For backrun bundles
    preflight_passed: bool = False
    preflight_block: int = 0
    created_at: float = field(default_factory=time.time)
    submitted: bool = False
    confirmed: bool = False
    receipt: Optional[Dict] = None


class JustInTimeExecutor:
    """
    Multi-channel JIT transaction executor.

    Manages the full lifecycle:
      prepare → preflight → sign → store → (trigger) → submit → confirm
    """

    def __init__(
        self,
        w3_providers: Dict[int, Web3],
        private_key: str = "",
        config: Optional[JITExecutorConfig] = None,
    ):
        self._w3 = w3_providers
        self._private_key = private_key or os.getenv("PRIVATE_KEY", "")
        self.cfg = config or JITExecutorConfig()
        self._account = None
        self._nonce_cache: Dict[int, int] = {}

        # Pre-signed TX pool:  position_key → PreSignedTx
        self.pool: Dict[str, PreSignedTx] = {}

        # Stats
        self.preflight_checks: int = 0
        self.preflight_passed: int = 0
        self.txs_broadcast: int = 0
        self.txs_confirmed: int = 0
        self.txs_reverted: int = 0
        self.total_profit_usd: float = 0.0

        self._running = False
        self._reaper_task: Optional[asyncio.Task] = None

        if self._private_key:
            try:
                w3_any = next(iter(self._w3.values()))
                self._account = w3_any.eth.account.from_key(self._private_key)
                logger.info(
                    "[JITExecutor] Wallet %s loaded", self._account.address[:12]
                )
            except Exception as exc:
                logger.warning("[JITExecutor] Wallet init failed: %s", exc)

    # ── Lifecycle ────────────────────────────────────────────────

    async def start(self) -> None:
        self._running = True
        self._reaper_task = asyncio.create_task(
            self._stale_reaper(), name="jit_reaper"
        )

    async def stop(self) -> None:
        self._running = False
        if self._reaper_task:
            self._reaper_task.cancel()

    # ── Prepare (Build + Verify + Sign) ─────────────────────────

    async def prepare_transaction(
        self,
        *,
        position_key: str,
        user_address: str,
        chain_id: int,
        pool_address: str,
        collateral_asset: str,
        debt_asset: str,
        total_debt_usd: float,
        estimated_profit_usd: float,
        trigger_type: str = "",
        trigger_tx_hash: str = "",
    ) -> Optional[PreSignedTx]:
        """
        Build, verify (``eth_call`` at pending), sign, and store a
        liquidation transaction.
        """
        if not self._account:
            return None
        w3 = self._w3.get(chain_id)
        if not w3:
            return None

        pool = w3.eth.contract(
            address=Web3.to_checksum_address(pool_address),
            abi=AAVE_LIQUIDATION_ABI,
        )

        debt_to_cover = int(total_debt_usd * 1e8 * 0.5)  # Cover 50% of debt

        # ── Verify via eth_call ─────────────────────────────────
        self.preflight_checks += 1
        try:
            call_data = pool.functions.liquidationCall(
                Web3.to_checksum_address(collateral_asset),
                Web3.to_checksum_address(debt_asset),
                Web3.to_checksum_address(user_address),
                debt_to_cover,
                False,
            ).build_transaction({
                "from": self._account.address,
                "gas": 500_000,
            })

            w3.eth.call(
                {
                    "from": self._account.address,
                    "to": call_data["to"],
                    "data": call_data["data"],
                    "gas": 500_000,
                },
                self.cfg.preflight_block_state,
            )
            sim_passed = True
            self.preflight_passed += 1
        except Exception as exc:
            err = str(exc)
            if "930bb771" in err or "HEALTH_FACTOR_NOT_BELOW" in err:
                logger.debug("[JITExecutor] Preflight: HF still above 1.0 for %s", position_key)
            else:
                logger.debug("[JITExecutor] Preflight failed: %s", err[:120])
            sim_passed = False

        # ── Gas pricing ──────────────────────────────────────────
        try:
            base_fee = w3.eth.get_block("pending").get("baseFeePerGas", 0)
            priority_fee = w3.eth.max_priority_fee
        except Exception:
            base_fee = w3.eth.gas_price
            priority_fee = 0

        max_fee = base_fee + int(priority_fee * self.cfg.gas_tip_multiplier)
        max_priority = int(priority_fee * self.cfg.gas_tip_multiplier)

        # Safety ceiling
        ceiling_wei = int(self.cfg.max_gas_price_gwei * 1e9)
        if max_fee > ceiling_wei:
            max_fee = ceiling_wei
            max_priority = min(max_priority, ceiling_wei)

        # ── Build + Sign ─────────────────────────────────────────
        nonce = self._get_nonce(chain_id, w3)
        try:
            tx = pool.functions.liquidationCall(
                Web3.to_checksum_address(collateral_asset),
                Web3.to_checksum_address(debt_asset),
                Web3.to_checksum_address(user_address),
                debt_to_cover,
                False,
            ).build_transaction({
                "from": self._account.address,
                "gas": 500_000,
                "nonce": nonce,
                "maxFeePerGas": max_fee,
                "maxPriorityFeePerGas": max_priority,
            })

            signed = w3.eth.account.sign_transaction(tx, self._private_key)
        except Exception as exc:
            logger.warning("[JITExecutor] TX build/sign error: %s", exc)
            return None

        current_block = 0
        try:
            current_block = w3.eth.block_number
        except Exception:
            pass

        entry = PreSignedTx(
            position_key=position_key,
            user_address=user_address,
            chain_id=chain_id,
            pool_address=pool_address,
            collateral_asset=collateral_asset,
            debt_asset=debt_asset,
            debt_to_cover=debt_to_cover,
            raw_signed_tx=signed.raw_transaction,
            tx_hash_hex=signed.hash.hex() if hasattr(signed, "hash") else "",
            gas_price_gwei=max_fee / 1e9,
            estimated_profit_usd=estimated_profit_usd,
            trigger_type=trigger_type,
            trigger_tx_hash=trigger_tx_hash,
            preflight_passed=sim_passed,
            preflight_block=current_block,
        )
        self.pool[position_key] = entry
        logger.info(
            "[JITExecutor] Prepared TX for %s preflight=%s gas=%.2f gwei",
            position_key, sim_passed, entry.gas_price_gwei,
        )
        return entry

    # ── Submit on Threshold Crossing ─────────────────────────────

    async def on_threshold_crossed(
        self, position_key: str
    ) -> Optional[Dict[str, Any]]:
        """
        Called when HF < 1.0 confirmed.  Selects the best channel and fires.
        """
        entry = self.pool.get(position_key)
        if not entry or entry.submitted:
            return None

        w3 = self._w3.get(entry.chain_id)
        if not w3:
            return None

        # Select channel based on trigger type
        if entry.trigger_type == "mempool_backrun" and entry.trigger_tx_hash:
            result = await self._submit_flashbots_bundle(
                entry, backrun_hash=entry.trigger_tx_hash
            )
        elif entry.trigger_type == "oracle_update":
            result = await self._submit_flashbots_bundle(entry)
        else:
            result = await self._submit_private_relay(entry)
            if not result:
                result = await self._submit_public(entry, w3)

        entry.submitted = True
        if result:
            self.txs_broadcast += 1
            logger.info(
                "[JITExecutor] TX submitted for %s via %s",
                position_key, result.get("channel", "unknown"),
            )
            # Async receipt tracking
            asyncio.create_task(
                self._track_receipt(entry, w3),
                name=f"receipt_{position_key}",
            )
        return result

    # ── Direct Fire (no pre-sign) ────────────────────────────────

    async def execute_immediate(
        self,
        *,
        user_address: str,
        chain_id: int,
        pool_address: str,
        collateral_asset: str,
        debt_asset: str,
        total_debt_usd: float,
        estimated_profit_usd: float,
    ) -> Optional[str]:
        """
        Build-sign-broadcast in a single shot.  Used when oracle/mempool
        triggers fire and there's no pre-signed TX available.
        """
        if not self._account:
            return None
        w3 = self._w3.get(chain_id)
        if not w3:
            return None

        pool = w3.eth.contract(
            address=Web3.to_checksum_address(pool_address),
            abi=AAVE_LIQUIDATION_ABI,
        )

        debt_to_cover = int(total_debt_usd * 1e8 * 0.5)
        nonce = self._get_nonce(chain_id, w3)

        try:
            base_fee = w3.eth.get_block("pending").get("baseFeePerGas", 0)
            priority_fee = w3.eth.max_priority_fee
        except Exception:
            base_fee = w3.eth.gas_price
            priority_fee = 0

        max_fee = base_fee + int(priority_fee * self.cfg.gas_tip_multiplier)
        max_priority = int(priority_fee * self.cfg.gas_tip_multiplier)

        try:
            tx = pool.functions.liquidationCall(
                Web3.to_checksum_address(collateral_asset),
                Web3.to_checksum_address(debt_asset),
                Web3.to_checksum_address(user_address),
                debt_to_cover,
                False,
            ).build_transaction({
                "from": self._account.address,
                "gas": 500_000,
                "nonce": nonce,
                "maxFeePerGas": max_fee,
                "maxPriorityFeePerGas": max_priority,
            })
            signed = w3.eth.account.sign_transaction(tx, self._private_key)
            tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
            self.txs_broadcast += 1
            hex_hash = tx_hash.hex()
            logger.info("[JITExecutor] IMMEDIATE TX: %s chain=%d", hex_hash[:16], chain_id)
            return hex_hash
        except Exception as exc:
            logger.warning("[JITExecutor] Immediate exec failed: %s", str(exc)[:120])
            return None

    # ── Submission Channels ──────────────────────────────────────

    async def _submit_flashbots_bundle(
        self,
        entry: PreSignedTx,
        backrun_hash: str = "",
    ) -> Optional[Dict[str, Any]]:
        """Submit via Flashbots ``eth_sendBundle``."""
        try:
            w3 = self._w3.get(entry.chain_id)
            if not w3:
                return None
            target_block = w3.eth.block_number + 1
            txs = []
            if backrun_hash:
                txs.append({"hash": backrun_hash})  # trigger TX
            txs.append({"tx": "0x" + entry.raw_signed_tx.hex()})

            body = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "eth_sendBundle",
                "params": [{
                    "txs": [
                        "0x" + entry.raw_signed_tx.hex(),
                    ],
                    "blockNumber": hex(target_block),
                }],
            }

            async with aiohttp.ClientSession() as session:
                async with session.post(
                    self.cfg.flashbots_rpc,
                    json=body,
                    timeout=aiohttp.ClientTimeout(total=5),
                ) as resp:
                    result = await resp.json()

            return {"channel": "flashbots", "target_block": target_block, "result": result}
        except Exception as exc:
            logger.debug("[JITExecutor] Flashbots error: %s", exc)
            return None

    async def _submit_private_relay(
        self, entry: PreSignedTx
    ) -> Optional[Dict[str, Any]]:
        """Try each configured private relay in order."""
        raw_hex = "0x" + entry.raw_signed_tx.hex()
        for relay_url in self.cfg.private_relays:
            try:
                body = {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "eth_sendRawTransaction",
                    "params": [raw_hex],
                }
                async with aiohttp.ClientSession() as session:
                    async with session.post(
                        relay_url,
                        json=body,
                        timeout=aiohttp.ClientTimeout(total=5),
                    ) as resp:
                        result = await resp.json()
                return {"channel": "private_relay", "relay": relay_url, "result": result}
            except Exception:
                continue
        return None

    async def _submit_public(
        self, entry: PreSignedTx, w3: Web3
    ) -> Optional[Dict[str, Any]]:
        """Public mempool fallback."""
        try:
            tx_hash = w3.eth.send_raw_transaction(entry.raw_signed_tx)
            return {"channel": "public", "tx_hash": tx_hash.hex()}
        except Exception as exc:
            logger.debug("[JITExecutor] Public send error: %s", exc)
            return None

    # ── Receipt Tracking ─────────────────────────────────────────

    async def _track_receipt(self, entry: PreSignedTx, w3: Web3) -> None:
        """Wait for confirmation and record outcome."""
        try:
            receipt = w3.eth.wait_for_transaction_receipt(
                bytes.fromhex(entry.tx_hash_hex) if entry.tx_hash_hex else b"",
                timeout=self.cfg.receipt_timeout_s,
            )
            entry.receipt = dict(receipt)
            if receipt["status"] == 1:
                entry.confirmed = True
                self.txs_confirmed += 1
                gas_used = receipt["gasUsed"]
                gas_price = receipt.get("effectiveGasPrice", 0)
                gas_cost_eth = (gas_used * gas_price) / 1e18
                gas_cost_usd = gas_cost_eth * 2500
                profit = entry.estimated_profit_usd - gas_cost_usd
                self.total_profit_usd += profit
                logger.info(
                    "[JITExecutor] ✅ CONFIRMED block=%d profit=$%.2f gas=$%.2f",
                    receipt["blockNumber"], profit, gas_cost_usd,
                )
            else:
                self.txs_reverted += 1
                logger.warning("[JITExecutor] ❌ REVERTED on-chain: %s", entry.tx_hash_hex[:16])
        except Exception as exc:
            logger.debug("[JITExecutor] Receipt error: %s", exc)

    # ── Helpers ──────────────────────────────────────────────────

    def _get_nonce(self, chain_id: int, w3: Web3) -> int:
        if not self._account:
            return 0
        try:
            nonce = w3.eth.get_transaction_count(self._account.address, "pending")
            self._nonce_cache[chain_id] = nonce
            return nonce
        except Exception:
            return self._nonce_cache.get(chain_id, 0)

    async def _stale_reaper(self) -> None:
        """Remove pre-signed TXs that are older than TTL."""
        while self._running:
            try:
                await asyncio.sleep(30)
                now = time.time()
                stale = [
                    k for k, v in self.pool.items()
                    if now - v.created_at > self.cfg.stale_tx_ttl_s and not v.submitted
                ]
                for k in stale:
                    del self.pool[k]
                if stale:
                    logger.debug("[JITExecutor] Reaped %d stale TXs", len(stale))
            except asyncio.CancelledError:
                break
            except Exception:
                await asyncio.sleep(10)

    # ── Stats ────────────────────────────────────────────────────

    def get_stats(self) -> Dict[str, Any]:
        return {
            "pool_size": len(self.pool),
            "preflight_checks": self.preflight_checks,
            "preflight_passed": self.preflight_passed,
            "txs_broadcast": self.txs_broadcast,
            "txs_confirmed": self.txs_confirmed,
            "txs_reverted": self.txs_reverted,
            "total_profit_usd": self.total_profit_usd,
            "confirmation_rate": (
                self.txs_confirmed / max(self.txs_broadcast, 1)
            ) * 100,
        }
