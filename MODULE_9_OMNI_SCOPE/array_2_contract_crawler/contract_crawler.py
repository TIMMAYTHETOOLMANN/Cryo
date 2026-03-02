#!/usr/bin/env python3
"""
ARRAY 2 — Contract Discovery Crawler: Unearthing Unlisted Alpha
==================================================================
Discovers new DeFi protocols before they appear on aggregators.

Capabilities:
  1. Funding-Based Exploration — track VC/deployer wallets for new contracts
  2. KOL & Smart Money Wallet Tracking — flag interactions with unverified contracts
  3. Cross-Chain Bytecode Mirroring — detect clones deployed on multiple chains
  4. New Lending Protocol Detection — pre-index health factor formulas

Data flow: New contract detected → classify → emit OpportunitySignal → DataBus
"""

import asyncio
import hashlib
import logging
import os
import time
from collections import defaultdict
from typing import Dict, List, Optional, Set
from dataclasses import dataclass, field

from web3 import Web3

from ..config import OmniScopeConfig, get_omni_config
from ..data_bus import DataBus, OpportunitySignal, SignalType, SignalSource

logger = logging.getLogger(__name__)


@dataclass
class DiscoveredContract:
    """A newly discovered contract."""
    address: str
    chain_id: int
    deployer: str
    bytecode_hash: str
    creation_tx: str
    block_number: int
    timestamp: float
    is_verified: bool = False
    is_lending: bool = False
    is_dex: bool = False
    function_signatures: List[str] = field(default_factory=list)
    matched_known_protocol: str = ""  # e.g., "aave_v3_clone"
    kol_interaction: bool = False


# Known lending protocol function signatures for detection
LENDING_SIGNATURES = {
    "0x69328dec": "withdraw(address,uint256,address)",  # Aave V3
    "0xa415bcad": "borrow(address,uint256,uint256,uint16,address)",
    "0xe8eda9df": "deposit(address,uint256,address,uint16)",
    "0x573ade81": "liquidationCall(address,address,address,uint256,bool)",
    "0xc5ebeaec": "borrow(uint256)",  # Compound
    "0x852a12e3": "redeemUnderlying(uint256)",
    "0xf5e3c462": "liquidateBorrow(address,uint256,address)",
}

# Known DEX function signatures
DEX_SIGNATURES = {
    "0x38ed1739": "swapExactTokensForTokens",
    "0x8803dbee": "swapTokensForExactTokens",
    "0x414bf389": "exactInputSingle",  # Uniswap V3
    "0xc04b8d59": "exactInput",
    "0xe449022e": "uniswapV3Swap",  # 1inch
}

# Known bytecode hashes of popular protocols (for clone detection)
KNOWN_BYTECODE_HASHES: Dict[str, str] = {
    # These would be populated with actual bytecode hashes in production
}


class ContractCrawler:
    """
    Discovers new protocols by monitoring deployer activity,
    KOL interactions, and cross-chain bytecode patterns.
    """

    def __init__(self, bus: DataBus, config: Optional[OmniScopeConfig] = None):
        self.bus = bus
        self.config = config or get_omni_config()
        self._cfg = self.config.contract_crawler
        self._running = False

        # Tracked deployer/KOL wallets
        self._watched_wallets: Set[str] = set()
        for w in self._cfg.kol_wallets:
            self._watched_wallets.add(w.lower())

        # Discovered contracts registry
        self._discovered: Dict[str, DiscoveredContract] = {}

        # Bytecode hash → list of (chain_id, address) for cross-chain detection
        self._bytecode_registry: Dict[str, List[tuple]] = defaultdict(list)

        # Chain connections
        self._w3_cache: Dict[int, Web3] = {}

        self.stats = {
            "contracts_discovered": 0,
            "lending_protocols_found": 0,
            "dex_protocols_found": 0,
            "cross_chain_clones_found": 0,
            "kol_interactions_flagged": 0,
            "signals_emitted": 0,
        }

    async def start(self):
        """Start the contract discovery crawler."""
        self._running = True
        logger.info("🔍 Array 2: Contract Crawler starting…")

        tasks = [
            asyncio.create_task(self._scan_new_contracts()),
            asyncio.create_task(self._track_kol_wallets()),
        ]

        try:
            await asyncio.gather(*tasks, return_exceptions=True)
        except asyncio.CancelledError:
            pass

    async def stop(self):
        self._running = False

    # ------------------------------------------------------------------
    # Scanner: New Contract Deployments
    # ------------------------------------------------------------------

    async def _scan_new_contracts(self):
        """Monitor recent blocks for new contract deployments."""
        chains = [1, 42161, 10, 8453, 137]  # Main chains

        while self._running:
            for chain_id in chains:
                try:
                    w3 = self._get_w3(chain_id)
                    if not w3:
                        continue

                    latest = w3.eth.block_number
                    block = w3.eth.get_block(latest, full_transactions=True)

                    for tx in block.get("transactions", []):
                        if isinstance(tx, dict) and tx.get("to") is None:
                            # Contract creation TX
                            receipt = None
                            try:
                                tx_hash = (
                                    tx["hash"].hex()
                                    if isinstance(tx["hash"], bytes)
                                    else str(tx["hash"])
                                )
                                receipt = w3.eth.get_transaction_receipt(tx_hash)
                            except Exception:
                                continue

                            if receipt and receipt.get("contractAddress"):
                                await self._process_new_contract(
                                    chain_id, w3, receipt, tx
                                )

                except Exception as e:
                    logger.debug(f"Contract scan error on chain {chain_id}: {e}")

            await asyncio.sleep(self._cfg.scan_interval_seconds)

    async def _process_new_contract(
        self, chain_id: int, w3: Web3, receipt: dict, tx: dict
    ):
        """Analyze a newly deployed contract."""
        addr = receipt["contractAddress"]
        deployer = tx.get("from", "")
        if isinstance(deployer, bytes):
            deployer = deployer.hex()

        # Get bytecode
        try:
            code = w3.eth.get_code(Web3.to_checksum_address(addr))
            if len(code) < 10:
                return  # Empty or trivial
        except Exception:
            return

        code_hash = hashlib.sha256(code).hexdigest()[:16]

        # Extract function signatures from bytecode
        sigs = self._extract_signatures(code)

        # Classify
        is_lending = any(s in LENDING_SIGNATURES for s in sigs)
        is_dex = any(s in DEX_SIGNATURES for s in sigs)
        is_kol = deployer.lower() in self._watched_wallets

        contract = DiscoveredContract(
            address=addr,
            chain_id=chain_id,
            deployer=deployer,
            bytecode_hash=code_hash,
            creation_tx=receipt.get("transactionHash", b"").hex() if isinstance(receipt.get("transactionHash"), bytes) else str(receipt.get("transactionHash", "")),
            block_number=receipt.get("blockNumber", 0),
            timestamp=time.time(),
            is_lending=is_lending,
            is_dex=is_dex,
            function_signatures=sigs,
            kol_interaction=is_kol,
        )

        # Cross-chain clone detection
        if code_hash in self._bytecode_registry:
            existing = self._bytecode_registry[code_hash]
            if not any(e[0] == chain_id for e in existing):
                contract.matched_known_protocol = f"clone_of_{existing[0][1][:10]}"
                self.stats["cross_chain_clones_found"] += 1
                logger.info(
                    f"🔄 Cross-chain clone: {addr[:12]}… on chain {chain_id} "
                    f"matches {existing[0][1][:12]}… on chain {existing[0][0]}"
                )

        self._bytecode_registry[code_hash].append((chain_id, addr))
        self._discovered[f"{chain_id}:{addr}"] = contract
        self.stats["contracts_discovered"] += 1

        # Emit signals for interesting contracts
        if is_lending:
            self.stats["lending_protocols_found"] += 1
            self.bus.publish(OpportunitySignal(
                signal_type=SignalType.NEW_PROTOCOL,
                source=SignalSource.CONTRACT_CRAWLER,
                chain_id=chain_id,
                confidence=0.8 if is_kol else 0.5,
                estimated_profit_usd=500.0,  # First-mover liquidation advantage
                gas_cost_estimate_usd=0,
                urgency_seconds=3600,  # Hours-scale opportunity
                target_contract=addr,
                competition_estimate=0.1,  # Very low — new protocol
                execution_complexity=0.7,
                metadata={
                    "deployer": deployer, "is_lending": True,
                    "signatures": sigs[:5], "bytecode_hash": code_hash,
                },
            ))
            self.stats["signals_emitted"] += 1

        if is_dex:
            self.stats["dex_protocols_found"] += 1

        if is_kol:
            self.stats["kol_interactions_flagged"] += 1

    # ------------------------------------------------------------------
    # KOL Wallet Tracking
    # ------------------------------------------------------------------

    async def _track_kol_wallets(self):
        """Monitor KOL/smart money wallet interactions."""
        while self._running:
            for chain_id in [1, 42161, 10]:
                w3 = self._get_w3(chain_id)
                if not w3:
                    continue

                for wallet in list(self._watched_wallets)[:50]:
                    try:
                        nonce = w3.eth.get_transaction_count(
                            Web3.to_checksum_address(wallet)
                        )
                        # In production: compare with cached nonce to detect new TXs
                        # and analyze what contracts they're interacting with
                    except Exception:
                        pass

            await asyncio.sleep(60)  # Check every minute

    # ------------------------------------------------------------------
    # Bytecode Analysis
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_signatures(bytecode: bytes) -> List[str]:
        """Extract 4-byte function selectors from bytecode."""
        sigs = []
        hex_code = bytecode.hex()
        # Simple heuristic: look for PUSH4 opcodes (0x63) followed by 4 bytes
        i = 0
        while i < len(hex_code) - 10:
            if hex_code[i:i + 2] == "63":
                selector = "0x" + hex_code[i + 2:i + 10]
                if selector not in sigs:
                    sigs.append(selector)
            i += 2
        return sigs[:50]  # Limit to 50 signatures

    def add_watched_wallet(self, address: str):
        """Add a wallet to track (KOL, VC, deployer)."""
        self._watched_wallets.add(address.lower())

    def _get_w3(self, chain_id: int) -> Optional[Web3]:
        if chain_id in self._w3_cache:
            return self._w3_cache[chain_id]
        rpc_map = {
            1: "MAINNET_RPC_URL", 42161: "ARBITRUM_RPC_URL",
            10: "OPTIMISM_RPC_URL", 8453: "BASE_RPC_URL",
            137: "POLYGON_RPC_URL",
        }
        rpc = os.getenv(rpc_map.get(chain_id, ""), "")
        if not rpc:
            return None
        try:
            w3 = Web3(Web3.HTTPProvider(rpc, request_kwargs={"timeout": 15}))
            self._w3_cache[chain_id] = w3
            return w3
        except Exception:
            return None

    def get_stats(self) -> Dict:
        return {
            **self.stats,
            "watched_wallets": len(self._watched_wallets),
            "discovered_contracts": len(self._discovered),
            "bytecode_patterns": len(self._bytecode_registry),
        }
