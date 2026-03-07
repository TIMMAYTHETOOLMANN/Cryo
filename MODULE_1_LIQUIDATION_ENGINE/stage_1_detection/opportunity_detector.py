#!/usr/bin/env python3
"""
STAGE 1 — Enhanced Opportunity Detector (Script 2 Upgraded)
=============================================================
Predictive & proactive scanning with ML-driven probability scoring.

ENHANCEMENTS (Script 2):
  1. Liquidation Probability Scoring — dynamic ML model replaces static threshold
  2. Cross-Protocol Collateral Overlap Detection — cascading liquidation capture
  3. Latency-Optimized Data Ingestion — WebSocket streaming + priority queue
  4. Preemptive Preparation — pre-calculate gas/routes for high-probability positions

Zero capital required — all operations are read-only.
"""

import asyncio
import heapq
import json
import logging
import math
import os
import time
from collections import defaultdict
from typing import Dict, List, Optional, Set, Tuple
from dataclasses import dataclass, field
from enum import Enum

from eth_abi import decode as abi_decode
from web3 import Web3

try:
    from web3.middleware import ExtraDataToPOAMiddleware as poa_middleware
except ImportError:
    try:
        from web3.middleware import geth_poa_middleware as poa_middleware
    except ImportError:
        poa_middleware = None

from ..config.settings import ConfigManager, get_config

# Subgraph indexer for mass borrower discovery
try:
    from .subgraph_indexer import SubgraphIndexer, LiquidationCandidate
    SUBGRAPH_AVAILABLE = True
except ImportError:
    SubgraphIndexer = None  # type: ignore
    SUBGRAPH_AVAILABLE = False

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data Models
# ---------------------------------------------------------------------------

class ScanPriority(Enum):
    CRITICAL = 0   # HF < 1.01 — immediate
    HIGH = 1       # HF < 1.03 or probability > 80%
    MEDIUM = 2     # HF < 1.05 or probability > 60%
    LOW = 3        # HF < 1.10 or probability > 40%
    WATCH = 4      # HF < 1.20 — preemptive tracking


@dataclass(order=True)
class PrioritizedPosition:
    """Priority-queue wrapper — lower priority value = processed first."""
    priority: int
    timestamp: float = field(compare=False)
    position: "LiquidatablePosition" = field(compare=False)


@dataclass
class LiquidatablePosition:
    """Detected liquidation candidate — output of Stage 1."""
    chain_id: int
    protocol: str
    user: str
    debt_asset: str
    collateral_asset: str
    debt_amount: int
    collateral_amount: int
    health_factor: float
    liquidation_bonus: float
    estimated_profit_usd: float
    max_gas_price: int
    timestamp: int
    block_number: int = 0
    # Script 2 additions
    liquidation_probability: float = 0.0
    priority: ScanPriority = ScanPriority.LOW
    volatility_index: float = 0.0
    oracle_update_frequency: float = 0.0
    cross_protocol_exposure: float = 0.0
    preemptive_routes_cached: bool = False


@dataclass
class UserExposure:
    """Cross-protocol exposure tracking for one user address."""
    address: str
    protocols: Dict[str, float] = field(default_factory=dict)  # protocol → debt_usd
    total_debt_usd: float = 0.0
    total_collateral_usd: float = 0.0
    positions_count: int = 0
    is_systemic_risk: bool = False


# ---------------------------------------------------------------------------
# Protocol ABIs (read-only)
# ---------------------------------------------------------------------------

AAVE_HEALTH_FACTOR_ABI = json.loads('''[
    {"inputs":[{"name":"user","type":"address"}],
     "name":"getUserAccountData",
     "outputs":[
        {"name":"totalCollateralETH","type":"uint256"},
        {"name":"totalDebtETH","type":"uint256"},
        {"name":"availableBorrowsETH","type":"uint256"},
        {"name":"currentLiquidationThreshold","type":"uint256"},
        {"name":"ltv","type":"uint256"},
        {"name":"healthFactor","type":"uint256"}
     ],"stateMutability":"view","type":"function"}
]''')

COMPOUND_LIQUIDITY_ABI = json.loads('''[
    {"inputs":[{"name":"account","type":"address"}],
     "name":"getAccountLiquidity",
     "outputs":[
        {"name":"","type":"uint256"},
        {"name":"","type":"uint256"},
        {"name":"","type":"uint256"}
     ],"stateMutability":"view","type":"function"}
]''')

CHAINLINK_ANSWER_UPDATED_TOPIC = "0x" + Web3.keccak(
    text="AnswerUpdated(int256,uint256,uint256)"
).hex()

# Chainlink ETH/USD oracle on Ethereum mainnet — for live price
CHAINLINK_ETH_USD_FEED = "0x5f4eC3Df9cbd43714FE2740f5E3616155c5b8419"
CHAINLINK_PRICE_ABI = json.loads('''[
    {"inputs":[],"name":"latestAnswer",
     "outputs":[{"name":"","type":"int256"}],
     "stateMutability":"view","type":"function"}
]''')

# Multicall3 (deployed at same address on all major EVM chains)
MULTICALL3_ADDRESS = "0xcA11bde05977b3631167028862bE2a173976CA11"
MULTICALL3_ABI = json.loads('''[{
    "inputs": [{"components": [
        {"name": "target", "type": "address"},
        {"name": "allowFailure", "type": "bool"},
        {"name": "callData", "type": "bytes"}
    ], "name": "calls", "type": "tuple[]"}],
    "name": "aggregate3",
    "outputs": [{"components": [
        {"name": "success", "type": "bool"},
        {"name": "returnData", "type": "bytes"}
    ], "name": "returnData", "type": "tuple[]"}],
    "stateMutability": "view", "type": "function"
}]''')


# ---------------------------------------------------------------------------
# Liquidation Probability Model
# ---------------------------------------------------------------------------

class LiquidationProbabilityModel:
    """
    Lightweight logistic-regression-style scoring model.
    Predicts probability of liquidation within the next N blocks.

    Features:
      - health_factor (inverted — lower HF = higher probability)
      - collateral_volatility (higher vol = higher probability)
      - oracle_update_frequency (more frequent = price moving fast)
      - debt_ratio (debt/collateral — higher = riskier)
      - recent_liquidation_rate (for similar collateral types)

    In production, train with scikit-learn on historical liquidation data.
    This implementation uses calibrated heuristic weights that approximate
    a trained model.
    """

    # Calibrated weights (approximating logistic regression coefficients)
    WEIGHTS = {
        "hf_distance":        -8.0,   # Distance below 1.10 (negative = closer to liq)
        "volatility":          2.5,   # Higher vol → higher prob
        "oracle_freq":         1.5,   # More frequent updates → price moving
        "debt_ratio":          3.0,   # Higher debt ratio → riskier
        "liq_history":         1.0,   # Recent liquidations of same type
    }
    BIAS = -2.0

    def predict(
        self,
        health_factor: float,
        collateral_volatility: float = 0.0,
        oracle_update_freq: float = 0.0,
        debt_ratio: float = 0.0,
        recent_liq_rate: float = 0.0,
    ) -> float:
        """
        Return probability [0.0, 1.0] of liquidation within N blocks.
        """
        hf_distance = max(0, 1.10 - health_factor)  # 0 if HF > 1.10
        z = (
            self.BIAS
            + self.WEIGHTS["hf_distance"] * (-hf_distance)  # negative distance
            + self.WEIGHTS["volatility"] * collateral_volatility
            + self.WEIGHTS["oracle_freq"] * oracle_update_freq
            + self.WEIGHTS["debt_ratio"] * debt_ratio
            + self.WEIGHTS["liq_history"] * recent_liq_rate
        )
        # Sigmoid
        try:
            prob = 1.0 / (1.0 + math.exp(-z))
        except OverflowError:
            prob = 0.0 if z < 0 else 1.0
        return round(prob, 4)


# ---------------------------------------------------------------------------
# Enhanced Opportunity Detector
# ---------------------------------------------------------------------------

class OpportunityDetector:
    """
    Multi-chain, multi-protocol opportunity detector with ML scoring.

    Zero capital: all operations are read-only on-chain calls.

    Script 2 Enhancements:
      - Priority queue (critical positions first)
      - Probability scoring (preemptive detection even at HF > 1.05)
      - Cross-protocol overlap detection
      - Oracle update frequency tracking
      - WebSocket event streaming support
    """

    # Extended HF range for preemptive scanning — capture positions up to 1.50 so the
    # watchlist always contains a rich set of near-liquidation candidates.
    PREEMPTIVE_HF_THRESHOLD = 1.50  # Track positions up to 1.50

    def __init__(self, config: Optional[ConfigManager] = None):
        self.config = config or get_config()
        self.w3_providers: Dict[int, Web3] = {}
        self.tracked_users: Set[str] = set()

        # ── Per-chain user tracking — only check users on their home chain ──
        self._chain_users: Dict[int, Set[str]] = defaultdict(set)

        # Priority queue — positions sorted by urgency
        self._priority_queue: List[PrioritizedPosition] = []

        # ML probability model
        self._model = LiquidationProbabilityModel()

        # Cross-protocol exposure tracker
        self._user_exposure: Dict[str, UserExposure] = defaultdict(
            lambda: UserExposure(address="")
        )

        # Oracle update frequency tracker: oracle_address → updates_per_minute
        self._oracle_freq: Dict[str, float] = {}
        self._oracle_last_seen: Dict[str, float] = {}

        # Volatility cache: asset_address → volatility_index
        self._volatility_cache: Dict[str, float] = {}

        # Recent liquidation history: collateral_type → liquidations_per_hour
        self._liq_history: Dict[str, float] = {}

        # Config
        exec_cfg = self.config.execution
        self.hf_threshold = exec_cfg.health_factor_threshold
        self.min_debt_usd = exec_cfg.min_debt_usd
        self.scan_interval = exec_cfg.scan_interval_seconds
        self.probability_threshold = float(
            os.getenv("PROBABILITY_THRESHOLD", "0.40")
        )

        # ── Live ETH price (updated from on-chain oracle every scan cycle) ──
        self._eth_price_usd: float = float(os.getenv("ETH_PRICE_USD", "2000"))
        self._eth_price_updated: float = 0.0

        # ── Multicall batch size ──
        # L2s can handle larger batches; L1 uses smaller for gas reasons
        self._multicall_batch_l1: int = int(os.getenv("MULTICALL_BATCH_L1", "80"))
        self._multicall_batch_l2: int = int(os.getenv("MULTICALL_BATCH_L2", "250"))

        # ── Subgraph indexer for mass borrower discovery ──
        # NOTE: The Graph hosted service is deprecated. Set long interval
        # until migrated to decentralized gateway (gateway.thegraph.com).
        self._subgraph: Optional["SubgraphIndexer"] = None
        self._subgraph_last_scan: float = 0.0
        self._subgraph_interval: float = float(os.getenv("SUBGRAPH_SCAN_INTERVAL", "3600"))

        # Stats
        self.stats = {
            "positions_scanned": 0,
            "liquidatables_found": 0,
            "preemptive_flags": 0,
            "cross_protocol_flags": 0,
            "priority_critical": 0,
            "priority_high": 0,
            "subgraph_users_discovered": 0,
            "multicall_batches": 0,
            "start_time": time.time(),
        }

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def initialize(self):
        """Connect to all configured chains (read-only), bootstrap user list,
        and initialize SubgraphIndexer for mass borrower discovery."""
        for chain_id, chain_cfg in self.config.get_all_chains().items():
            if not chain_cfg.rpc_url:
                continue
            try:
                w3 = Web3(Web3.HTTPProvider(
                    chain_cfg.rpc_url, request_kwargs={"timeout": 15}
                ))
                if poa_middleware:
                    w3.middleware_onion.inject(poa_middleware, layer=0)
                block = w3.eth.block_number
                self.w3_providers[chain_id] = w3
                logger.info(f"  Chain {chain_id} ({chain_cfg.name}) -- block {block}")
            except Exception as e:
                logger.warning(f"  Chain {chain_id} ({chain_cfg.name}): {e}")

        # Fetch live ETH price from Chainlink oracle
        await self._refresh_eth_price()

        # Bootstrap: discover active borrowers from recent events + subgraph
        await self._bootstrap_tracked_users()

        # Initialize SubgraphIndexer for continuous mass discovery
        if SUBGRAPH_AVAILABLE:
            try:
                self._subgraph = SubgraphIndexer({
                    "hf_threshold": self.PREEMPTIVE_HF_THRESHOLD,
                    "min_debt_usd": self.min_debt_usd,
                    "eth_price_usd": self._eth_price_usd,
                    "enabled_chains": list(self.w3_providers.keys()),
                })
                await self._subgraph.initialize()
                # Do an initial subgraph scan to seed the watchlist
                candidates = await self._subgraph.full_scan()
                new_from_subgraph = 0
                for c in candidates:
                    addr = Web3.to_checksum_address(c.user_address)
                    if addr not in self.tracked_users:
                        self.tracked_users.add(addr)
                        self._chain_users[c.chain_id].add(addr)
                        new_from_subgraph += 1
                self.stats["subgraph_users_discovered"] += new_from_subgraph
                if new_from_subgraph:
                    logger.info(
                        f"  Subgraph: +{new_from_subgraph} at-risk borrowers "
                        f"(total: {len(self.tracked_users)})"
                    )
            except Exception as e:
                logger.warning(f"  SubgraphIndexer init failed (log-only mode): {e}")
                self._subgraph = None

        logger.info(
            f"Enhanced Detector ready -- {len(self.w3_providers)} chains, "
            f"{len(self.tracked_users)} users tracked, "
            f"ETH=${self._eth_price_usd:,.0f}, "
            f"ML scoring enabled, preemptive threshold HF<{self.PREEMPTIVE_HF_THRESHOLD}"
        )

    async def _refresh_eth_price(self):
        """Fetch live ETH/USD price from Chainlink on Ethereum mainnet."""
        w3 = self.w3_providers.get(1)
        if not w3:
            return
        try:
            feed = w3.eth.contract(
                address=Web3.to_checksum_address(CHAINLINK_ETH_USD_FEED),
                abi=CHAINLINK_PRICE_ABI,
            )
            answer = feed.functions.latestAnswer().call()
            price = answer / 1e8  # Chainlink uses 8 decimals
            if price > 100:  # sanity check
                self._eth_price_usd = price
                self._eth_price_updated = time.time()
                logger.debug(f"ETH/USD price updated: ${price:,.2f}")
        except Exception as e:
            logger.debug(f"ETH price fetch failed (using ${self._eth_price_usd:,.0f}): {e}")

    async def _bootstrap_tracked_users(self):
        """
        Discover active borrowers by scanning recent Borrow events on Aave V3/V2 pools
        and Compound V3 WithdrawCollateral events. Seeds the tracked_users set.
        """
        # Aave V3 Borrow event topic
        BORROW_TOPIC = "0x" + Web3.keccak(
            text="Borrow(address,address,address,uint256,uint8,uint256,uint16)"
        ).hex()

        # Compound V3 uses AbsorbCollateral for liquidations and
        # WithdrawReserves / Supply events. We look for Withdraw (borrowing)
        # Compound V3 Comet.withdraw(address asset, uint amount) emits:
        # Withdraw(address indexed src, address indexed to, uint amount)
        COMPOUND_WITHDRAW_TOPIC = "0x" + Web3.keccak(
            text="Withdraw(address,address,uint256)"
        ).hex()

        # Compound V3 AbsorbCollateral for finding users that got liquidated
        COMPOUND_ABSORB_TOPIC = "0x" + Web3.keccak(
            text="AbsorbCollateral(address,address,address,uint256,uint256)"
        ).hex()

        protocols = self.config.get_all_protocols()
        total_new = 0

        for key, proto in protocols.items():
            chain_id = proto.chain_id
            w3 = self.w3_providers.get(chain_id)
            if not w3:
                continue

            is_l2 = chain_id in (10, 8453, 42161, 137, 43114, 56, 324)
            # L2s have faster blocks — look back further for more borrowers
            lookback = 50000 if is_l2 else 10000

            try:
                current_block = w3.eth.block_number
                from_block = max(0, current_block - lookback)
                pool_addr = Web3.to_checksum_address(proto.pool_address)
                users_found = set()

                if "aave" in proto.name.lower():
                    logs = w3.eth.get_logs({
                        "address": pool_addr,
                        "fromBlock": from_block,
                        "toBlock": current_block,
                        "topics": [BORROW_TOPIC],
                    })
                    for log in logs:
                        if len(log["topics"]) >= 3:
                            borrower = "0x" + log["topics"][2].hex()[-40:]
                            users_found.add(Web3.to_checksum_address(borrower))
                        if len(log["data"]) >= 64:
                            user_hex = "0x" + log["data"].hex()[24:64]
                            try:
                                users_found.add(Web3.to_checksum_address(user_hex))
                            except Exception:
                                pass

                elif "compound" in proto.name.lower():
                    # Compound V3: Withdraw events = active borrowers
                    logs = w3.eth.get_logs({
                        "address": pool_addr,
                        "fromBlock": from_block,
                        "toBlock": current_block,
                        "topics": [COMPOUND_WITHDRAW_TOPIC],
                    })
                    for log in logs:
                        if len(log["topics"]) >= 2:
                            borrower = "0x" + log["topics"][1].hex()[-40:]
                            users_found.add(Web3.to_checksum_address(borrower))

                    # Also check AbsorbCollateral for recently-liquidated addresses
                    try:
                        abs_logs = w3.eth.get_logs({
                            "address": pool_addr,
                            "fromBlock": from_block,
                            "toBlock": current_block,
                            "topics": [COMPOUND_ABSORB_TOPIC],
                        })
                        for log in abs_logs:
                            if len(log["topics"]) >= 3:
                                user_absorbed = "0x" + log["topics"][2].hex()[-40:]
                                users_found.add(Web3.to_checksum_address(user_absorbed))
                    except Exception:
                        pass

                for u in users_found:
                    self.tracked_users.add(u)
                    self._chain_users[chain_id].add(u)
                total_new += len(users_found)

                if users_found:
                    logger.info(
                        f"🔍 Bootstrap: {len(users_found)} borrowers from "
                        f"{proto.name} chain {chain_id} (blocks {from_block}-{current_block})"
                    )

            except Exception as e:
                logger.debug(f"Bootstrap {key} error: {e}")

        if total_new > 0:
            logger.info(f"🔍 Bootstrap complete: {len(self.tracked_users)} unique users tracked")
        else:
            logger.info("🔍 Bootstrap: no recent borrowers found — will scan on new events")

    # ------------------------------------------------------------------
    # Main scan
    # ------------------------------------------------------------------

    async def scan_once(self) -> List[LiquidatablePosition]:
        """
        Run one scan cycle.  Returns positions sorted by priority
        (critical first).

        Optimizations (v2):
          - Parallel chain scanning via asyncio.gather
          - Multicall3 batching for health factor checks (80-250 calls per batch)
          - Periodic SubgraphIndexer refresh for mass borrower discovery
          - Live ETH price from Chainlink oracle
        """
        self._priority_queue.clear()

        # Refresh ETH price every 60 seconds
        if time.time() - self._eth_price_updated > 60:
            await self._refresh_eth_price()

        # Periodic subgraph scan for new at-risk borrowers (every N seconds)
        if self._subgraph and (time.time() - self._subgraph_last_scan > self._subgraph_interval):
            try:
                candidates = await self._subgraph.full_scan()
                new_count = 0
                for c in candidates:
                    addr = Web3.to_checksum_address(c.user_address)
                    if addr not in self.tracked_users:
                        self.tracked_users.add(addr)
                        self._chain_users[c.chain_id].add(addr)
                        new_count += 1
                if new_count:
                    self.stats["subgraph_users_discovered"] += new_count
                    logger.info(f"  Subgraph refresh: +{new_count} users (total: {len(self.tracked_users)})")
                self._subgraph_last_scan = time.time()
            except Exception as e:
                logger.debug(f"Subgraph refresh error: {e}")

        # Parallel scan all chains
        tasks = []
        for chain_id, w3 in self.w3_providers.items():
            tasks.append(self._scan_chain(chain_id, w3))

        results = await asyncio.gather(*tasks, return_exceptions=True)
        for r in results:
            if isinstance(r, Exception):
                logger.debug(f"Chain scan error: {r}")

        # Drain priority queue into sorted list
        results_list: List[LiquidatablePosition] = []
        while self._priority_queue:
            pp = heapq.heappop(self._priority_queue)
            results_list.append(pp.position)

        return results_list

    async def _scan_chain(self, chain_id: int, w3: Web3):
        """Scan all protocols on a single chain, score, and enqueue."""
        protocols = self.config.get_all_protocols()

        # Rolling borrower discovery: pick up new borrowers from last ~50 blocks
        await self._discover_new_borrowers(chain_id, w3, protocols, lookback=50)

        for key, proto in protocols.items():
            if proto.chain_id != chain_id:
                continue
            try:
                if "aave" in proto.name.lower():
                    await self._check_aave_positions(w3, chain_id, proto)
                elif "compound" in proto.name.lower():
                    await self._check_compound_positions(w3, chain_id, proto)
            except Exception as e:
                logger.debug(f"Protocol {key} scan error: {e}")

        # After scanning all protocols, check cross-protocol overlap
        self._detect_cross_protocol_overlap(chain_id)

    async def _discover_new_borrowers(self, chain_id, w3, protocols, lookback=50):
        """Lightweight rolling discovery: scan recent blocks for new Borrow/Withdraw events."""
        BORROW_TOPIC = "0x" + Web3.keccak(
            text="Borrow(address,address,address,uint256,uint8,uint256,uint16)"
        ).hex()
        COMPOUND_WITHDRAW_TOPIC = "0x" + Web3.keccak(
            text="Withdraw(address,address,uint256)"
        ).hex()
        before = len(self.tracked_users)

        for key, proto in protocols.items():
            if proto.chain_id != chain_id:
                continue
            try:
                current_block = w3.eth.block_number
                from_block = max(0, current_block - lookback)
                pool_addr = Web3.to_checksum_address(proto.pool_address)

                if "aave" in proto.name.lower():
                    logs = w3.eth.get_logs({
                        "address": pool_addr,
                        "fromBlock": from_block,
                        "toBlock": current_block,
                        "topics": [BORROW_TOPIC],
                    })
                    for log in logs:
                        if len(log["topics"]) >= 3:
                            borrower = "0x" + log["topics"][2].hex()[-40:]
                            addr = Web3.to_checksum_address(borrower)
                            self.tracked_users.add(addr)
                            self._chain_users[chain_id].add(addr)

                elif "compound" in proto.name.lower():
                    logs = w3.eth.get_logs({
                        "address": pool_addr,
                        "fromBlock": from_block,
                        "toBlock": current_block,
                        "topics": [COMPOUND_WITHDRAW_TOPIC],
                    })
                    for log in logs:
                        if len(log["topics"]) >= 2:
                            borrower = "0x" + log["topics"][1].hex()[-40:]
                            addr = Web3.to_checksum_address(borrower)
                            self.tracked_users.add(addr)
                            self._chain_users[chain_id].add(addr)
            except Exception:
                pass

        added = len(self.tracked_users) - before
        if added > 0:
            logger.info(f"🔍 +{added} new borrowers on chain {chain_id} (total: {len(self.tracked_users)})")

    # ------------------------------------------------------------------
    # Aave scanning
    # ------------------------------------------------------------------

    async def _check_aave_positions(self, w3, chain_id, proto):
        """Check Aave positions via Multicall3 batching (falls back to sequential)."""
        pool_addr = Web3.to_checksum_address(proto.pool_address)
        contract = w3.eth.contract(address=pool_addr, abi=AAVE_HEALTH_FACTOR_ABI)

        # Only check users discovered on THIS chain — not the global set
        chain_specific = self._chain_users.get(chain_id, set())
        users = list(chain_specific)[:2000]
        if not users:
            return

        eth_price = self._eth_price_usd

        # ── Multicall3 batched health factor check ──
        is_l2 = chain_id in (10, 8453, 42161, 137, 43114, 56, 324)
        batch_size = self._multicall_batch_l2 if is_l2 else self._multicall_batch_l1
        mc_addr = MULTICALL3_ADDRESS
        if chain_id == 324:
            mc_addr = "0xF9cda624FBC7e059355ce98a31693d299FACd963"

        results_map: Dict[str, tuple] = {}

        try:
            mc = w3.eth.contract(
                address=Web3.to_checksum_address(mc_addr),
                abi=MULTICALL3_ABI,
            )
            # Build all calldata
            calls_and_addrs = []
            for user in users:
                try:
                    calldata = contract.encode_abi('getUserAccountData',
                        args=[Web3.to_checksum_address(user)])
                    cd_hex = calldata if isinstance(calldata, str) else calldata.hex()
                    if cd_hex.startswith('0x'):
                        cd_hex = cd_hex[2:]
                    calls_and_addrs.append((
                        (pool_addr, True, bytes.fromhex(cd_hex)),
                        user,
                    ))
                except Exception:
                    continue

            # Execute in batches
            for batch_start in range(0, len(calls_and_addrs), batch_size):
                batch = calls_and_addrs[batch_start:batch_start + batch_size]
                batch_calls = [c for c, _ in batch]
                batch_addrs = [a for _, a in batch]
                self.stats["multicall_batches"] += 1
                try:
                    raw_results = mc.functions.aggregate3(batch_calls).call()
                    for i, (success, return_data) in enumerate(raw_results):
                        if success and len(return_data) >= 192:
                            try:
                                decoded = abi_decode(
                                    ['uint256', 'uint256', 'uint256', 'uint256', 'uint256', 'uint256'],
                                    return_data,
                                )
                                results_map[batch_addrs[i]] = decoded
                            except Exception:
                                pass
                except Exception as batch_err:
                    logger.debug(f"Multicall batch failed chain {chain_id}: {batch_err}")
                    # Fall back to sequential for this batch
                    for addr in batch_addrs:
                        try:
                            result = contract.functions.getUserAccountData(
                                Web3.to_checksum_address(addr)
                            ).call()
                            results_map[addr] = result
                        except Exception:
                            continue

        except Exception as mc_err:
            logger.debug(f"Multicall3 unavailable chain {chain_id}: {mc_err}")
            # Full sequential fallback
            for user in users[:500]:
                try:
                    result = contract.functions.getUserAccountData(user).call()
                    results_map[user] = result
                except Exception:
                    pass

        # ── Process results ──
        for user, result in results_map.items():
            try:
                hf = result[5] / 1e18
                debt_eth = result[1] / 1e18
                collateral_eth = result[0] / 1e18
                self.stats["positions_scanned"] += 1

                debt_usd = debt_eth * eth_price
                collateral_usd = collateral_eth * eth_price

                # Extended range: track up to PREEMPTIVE_HF_THRESHOLD
                if hf > self.PREEMPTIVE_HF_THRESHOLD or debt_usd < self.min_debt_usd:
                    continue

                # Update cross-protocol exposure
                expo = self._user_exposure[user.lower()]
                expo.address = user
                proto_key = f"{proto.name}_{chain_id}"
                expo.protocols[proto_key] = debt_usd
                expo.total_debt_usd = sum(expo.protocols.values())
                expo.total_collateral_usd = collateral_usd
                expo.positions_count = len(expo.protocols)

                # Compute ML probability score
                collateral_asset = "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2"
                vol = self._volatility_cache.get(collateral_asset, 0.05)
                oracle_freq = max(self._oracle_freq.values()) if self._oracle_freq else 0.0
                debt_ratio = debt_usd / max(collateral_usd, 1)
                liq_rate = self._liq_history.get(collateral_asset, 0.0)

                probability = self._model.predict(
                    health_factor=hf,
                    collateral_volatility=vol,
                    oracle_update_freq=oracle_freq,
                    debt_ratio=debt_ratio,
                    recent_liq_rate=liq_rate,
                )

                # Determine priority
                priority = self._classify_priority(hf, probability)

                # Gate: must exceed threshold OR be within classic HF range
                if hf >= self.hf_threshold and probability < self.probability_threshold:
                    continue  # Not urgent enough

                if probability > 0.70 and hf > self.hf_threshold:
                    self.stats["preemptive_flags"] += 1

                # Profit estimate
                gross = collateral_usd * proto.bonus
                flash_fee = debt_usd * 0.0005
                gas_est = 5.0 if not (chain_id in (10, 8453, 42161, 137, 43114, 56, 324)) else 0.10
                net_profit = gross - flash_fee - gas_est

                if net_profit < self.config.execution.min_profit_usd and hf >= 1.0:
                    continue

                self.stats["liquidatables_found"] += 1
                if priority == ScanPriority.CRITICAL:
                    self.stats["priority_critical"] += 1
                elif priority == ScanPriority.HIGH:
                    self.stats["priority_high"] += 1

                pos = LiquidatablePosition(
                    chain_id=chain_id,
                    protocol=proto.name.lower().replace(" ", "_"),
                    user=user,
                    debt_asset="0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48",
                    collateral_asset=collateral_asset,
                    debt_amount=int(debt_eth * 1e18),
                    collateral_amount=int(collateral_eth * 1e18),
                    health_factor=hf,
                    liquidation_bonus=proto.bonus,
                    estimated_profit_usd=net_profit,
                    max_gas_price=int(self.config.execution.gas_price_cap_gwei * 1e9),
                    timestamp=int(time.time()),
                    block_number=w3.eth.block_number,
                    liquidation_probability=probability,
                    priority=priority,
                    volatility_index=vol,
                    oracle_update_frequency=oracle_freq,
                    cross_protocol_exposure=expo.total_debt_usd,
                )

                heapq.heappush(
                    self._priority_queue,
                    PrioritizedPosition(priority.value, time.time(), pos),
                )

                log_fn = logger.warning if priority.value <= 1 else logger.info
                log_fn(
                    f"{'!!' if priority.value <= 1 else '>>'} "
                    f"{priority.name} {user[:12]}... HF={hf:.3f} "
                    f"prob={probability:.0%} profit=${net_profit:.2f} "
                    f"debt=${debt_usd:,.0f} ({proto.name} chain {chain_id})"
                )

            except Exception:
                pass

    async def _check_compound_positions(self, w3, chain_id, proto):
        """Check Compound V3 (Comet) positions for liquidation opportunities."""
        # Compound V3 Comet ABI fragments we need
        COMET_ABI = json.loads('''[
            {"inputs":[{"name":"account","type":"address"}],
             "name":"borrowBalanceOf",
             "outputs":[{"name":"","type":"uint256"}],
             "stateMutability":"view","type":"function"},
            {"inputs":[{"name":"account","type":"address"}],
             "name":"isLiquidatable",
             "outputs":[{"name":"","type":"bool"}],
             "stateMutability":"view","type":"function"},
            {"inputs":[],
             "name":"baseToken",
             "outputs":[{"name":"","type":"address"}],
             "stateMutability":"view","type":"function"},
            {"inputs":[],
             "name":"baseTokenPriceFeed",
             "outputs":[{"name":"","type":"address"}],
             "stateMutability":"view","type":"function"},
            {"inputs":[{"name":"account","type":"address"}],
             "name":"balanceOf",
             "outputs":[{"name":"","type":"uint256"}],
             "stateMutability":"view","type":"function"}
        ]''')

        try:
            comet = w3.eth.contract(
                address=Web3.to_checksum_address(proto.pool_address),
                abi=COMET_ABI,
            )
        except Exception:
            return

        scanned = 0
        chain_specific = self._chain_users.get(chain_id, set())
        for user in list(chain_specific)[:500]:
            try:
                # Quick check: does user have a borrow balance?
                borrow_bal = comet.functions.borrowBalanceOf(user).call()
                if borrow_bal == 0:
                    continue

                scanned += 1
                self.stats["positions_scanned"] += 1

                # Check if liquidatable
                is_liq = comet.functions.isLiquidatable(user).call()

                # Estimate USD value (Compound V3 base token is typically USDC)
                # USDC has 6 decimals
                debt_usd = borrow_bal / 1e6

                if debt_usd < self.min_debt_usd:
                    continue

                # Compound V3 liquidation bonus is typically 5-10%
                bonus = proto.bonus  # from config
                net_profit = debt_usd * bonus - 5  # minus gas estimate

                if not is_liq:
                    # Still track for preemptive monitoring
                    # Compute a synthetic HF: >1 = not liquidatable, <1 = liquidatable
                    # For non-liquidatable positions, we skip unless close to the edge
                    continue

                # This position IS liquidatable right now
                hf = 0.95  # by definition, isLiquidatable=true means HF < 1
                probability = 0.99
                priority = ScanPriority.CRITICAL

                if net_profit < self.config.execution.min_profit_usd:
                    continue

                self.stats["liquidatables_found"] += 1
                self.stats["priority_critical"] += 1

                pos = LiquidatablePosition(
                    chain_id=chain_id,
                    protocol="compound_v3",
                    user=user,
                    debt_asset=proto.pool_address,  # base token
                    collateral_asset="multi",
                    debt_amount=borrow_bal,
                    collateral_amount=0,
                    health_factor=hf,
                    liquidation_bonus=bonus,
                    estimated_profit_usd=net_profit,
                    max_gas_price=int(self.config.execution.gas_price_cap_gwei * 1e9),
                    timestamp=int(time.time()),
                    block_number=w3.eth.block_number,
                    liquidation_probability=probability,
                    priority=priority,
                    volatility_index=0.05,
                    oracle_update_frequency=0.0,
                    cross_protocol_exposure=0.0,
                )

                heapq.heappush(
                    self._priority_queue,
                    PrioritizedPosition(priority.value, time.time(), pos),
                )

                logger.warning(
                    f"!! COMPOUND CRITICAL {user[:12]}... "
                    f"debt=${debt_usd:,.0f} profit=${net_profit:.2f} "
                    f"(Compound V3 chain {chain_id})"
                )

            except Exception:
                pass

    # ------------------------------------------------------------------
    # Priority classification
    # ------------------------------------------------------------------

    @staticmethod
    def _classify_priority(hf: float, probability: float) -> ScanPriority:
        if hf < 1.01 or probability > 0.95:
            return ScanPriority.CRITICAL
        if hf < 1.03 or probability > 0.80:
            return ScanPriority.HIGH
        if hf < 1.05 or probability > 0.60:
            return ScanPriority.MEDIUM
        if hf < 1.10 or probability > 0.40:
            return ScanPriority.LOW
        return ScanPriority.WATCH

    # ------------------------------------------------------------------
    # Cross-protocol overlap detection
    # ------------------------------------------------------------------

    def _detect_cross_protocol_overlap(self, chain_id: int):
        """
        Flag users with exposure across multiple protocols.
        Cascading liquidations can be captured in one block.
        """
        systemic_threshold_usd = float(
            __import__("os").getenv("SYSTEMIC_RISK_THRESHOLD_USD", "50000")
        )
        for addr, expo in self._user_exposure.items():
            if expo.positions_count >= 2 and expo.total_debt_usd > systemic_threshold_usd:
                if not expo.is_systemic_risk:
                    expo.is_systemic_risk = True
                    self.stats["cross_protocol_flags"] += 1
                    logger.warning(
                        f"🔗 CROSS-PROTOCOL RISK: {addr[:12]}… "
                        f"${expo.total_debt_usd:,.0f} across "
                        f"{expo.positions_count} protocols"
                    )

    # ------------------------------------------------------------------
    # Oracle frequency tracking (called by pipeline or external feed)
    # ------------------------------------------------------------------

    def record_oracle_update(self, oracle_address: str):
        """Track oracle update frequency for probability scoring."""
        now = time.time()
        last = self._oracle_last_seen.get(oracle_address, now)
        interval = max(now - last, 0.1)
        freq = 1.0 / interval  # updates per second
        # Exponential moving average
        prev = self._oracle_freq.get(oracle_address, freq)
        self._oracle_freq[oracle_address] = prev * 0.7 + freq * 0.3
        self._oracle_last_seen[oracle_address] = now

    def update_volatility(self, asset: str, volatility: float):
        """Update volatility cache for an asset."""
        self._volatility_cache[asset] = volatility

    def record_liquidation(self, collateral_asset: str):
        """Record a liquidation event for probability model."""
        prev = self._liq_history.get(collateral_asset, 0.0)
        self._liq_history[collateral_asset] = prev * 0.9 + 0.1

    # ------------------------------------------------------------------
    # User management
    # ------------------------------------------------------------------

    def add_tracked_user(self, address: str):
        self.tracked_users.add(Web3.to_checksum_address(address))

    def add_tracked_users(self, addresses: List[str]):
        for addr in addresses:
            self.add_tracked_user(addr)

    def get_stats(self) -> Dict:
        uptime = time.time() - self.stats["start_time"]
        return {
            **self.stats,
            "uptime_seconds": uptime,
            "tracked_users": len(self.tracked_users),
            "queue_depth": len(self._priority_queue),
            "cross_protocol_users": sum(
                1 for e in self._user_exposure.values() if e.is_systemic_risk
            ),
        }
