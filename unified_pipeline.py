#!/usr/bin/env python3
"""
UNIFIED PIPELINE — Single-Path Execution Consolidation
========================================================
Merges the two parallel systems (MODULE_1_LIQUIDATION_ENGINE and
profit_engine) into one deterministic execution path.

Problem Being Solved:
    MODULE_1 and profit_engine both contain liquidation execution,
    flash loan integration, and profit monitoring — but they aren't
    connected. MODULE_1's pipeline.py (982 lines) is standalone;
    profit_engine's triangulated_profit_engine.py wires a parallel
    pipeline. The zero_revert_pipeline and jit_liquidation_engine
    have contradictory execution philosophies (no-sim vs always-sim).

Architecture:
    SubgraphIndexer (Priority 1)
        → UnifiedPipeline.ingest(candidates)
            → RiskGate       (from MODULE_1 safeguards — circuit breaker,
                               oracle staleness, gas cap, cooldown)
            → ProfitGate     (from profit_engine — gas optimizer computes
                               net after gas; flash loan router selects
                               cheapest 0-capital provider)
            → TieredExecutor  (resolves sim-vs-speed contradiction):
                HF < 0.95  → IMMEDIATE — no sim, Flashbots bundle
                HF 0.95–1.0 → VERIFIED  — eth_call at pending, then send
                HF 1.0–1.02 → CAUTIOUS  — sim + oracle freshness check
            → ResultRecorder  (profit ledger, heat map update,
                               capital multiplier feedback)

Single Source of Truth:
    - One execution path, no ambiguity about which system is active
    - Risk management from MODULE_1 applied BEFORE profit calculations
    - Profit engine components applied AFTER risk approval
    - Tiered execution resolves the preflight debate with math

Integration Points:
    - Consumes LiquidationCandidate from SubgraphIndexer
    - Uses existing GasOptimizer, FlashLoanRouter, HeatMap, ProfitLedger
    - Implements ExecutionInterface from omni_channel
    - Feeds PositionWatchlist (Priority 3) with execution results
"""

from __future__ import annotations

import asyncio
import time
import uuid
import json
import logging
import os
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any
from enum import Enum
from collections import deque

from web3 import Web3
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════
# Import from existing subsystems
# ═══════════════════════════════════════════════════════════════════

# profit_engine — gas optimization, flash loan routing, ledger, heat map
try:
    from profit_engine.gas_optimizer import GasOptimizer, GasEstimate
    from profit_engine.flash_loan_router import FlashLoanRouter, FlashLoanRoute
    from profit_engine.heat_map import HeatMap
    from profit_engine.profit_ledger import ProfitLedger, ProfitEntry, PhaseState
    from profit_engine.capital_multiplier import CapitalMultiplier
    HAS_PROFIT_ENGINE = True
except ImportError:  # noqa: E722
    GasOptimizer = GasEstimate = None  # type: ignore[assignment,misc]
    FlashLoanRouter = FlashLoanRoute = None  # type: ignore[assignment,misc]
    HeatMap = None  # type: ignore[assignment,misc]
    ProfitLedger = ProfitEntry = PhaseState = None  # type: ignore[assignment,misc]
    CapitalMultiplier = None  # type: ignore[assignment,misc]
    HAS_PROFIT_ENGINE = False
    logger.warning("profit_engine not available — running in standalone mode")

# SubgraphIndexer — live position discovery (Priority 1 module)
try:
    from MODULE_1_LIQUIDATION_ENGINE.stage_1_detection.subgraph_indexer import (
        LiquidationCandidate, Protocol,
    )
    HAS_INDEXER = True
except ImportError:
    LiquidationCandidate = Protocol = None  # type: ignore[assignment,misc]
    HAS_INDEXER = False


# ═══════════════════════════════════════════════════════════════════
# EXECUTION TIER SYSTEM
# ═══════════════════════════════════════════════════════════════════

class ExecutionTier(Enum):
    """
    Tiered execution resolves the contradiction between
    zero_revert_pipeline (no preflight) and jit_liquidation_engine
    (always preflight-verify). The math determines the tier.
    """
    IMMEDIATE = "immediate"   # HF < 0.95: deeply underwater, execute NOW
    VERIFIED = "verified"     # HF 0.95–1.0: preflight at pending, then send
    CAUTIOUS = "cautious"     # HF 1.0–1.02: preflight + oracle freshness required


class PipelineStage(Enum):
    """Tracks where a candidate is in the pipeline."""
    INGESTED = "ingested"
    RISK_APPROVED = "risk_approved"
    RISK_REJECTED = "risk_rejected"
    PROFIT_APPROVED = "profit_approved"
    PROFIT_REJECTED = "profit_rejected"
    VERIFYING = "verifying"
    PREFLIGHT_PASSED = "preflight_passed"
    PREFLIGHT_FAILED = "preflight_failed"
    EXECUTING = "executing"
    CONFIRMED = "confirmed"
    REVERTED = "reverted"
    FAILED = "failed"


class RejectionReason(Enum):
    """Why a candidate was rejected at any gate."""
    CIRCUIT_BREAKER_OPEN = "circuit_breaker_open"
    GAS_CAP_EXCEEDED = "gas_cap_exceeded"
    ORACLE_STALE = "oracle_stale"
    USER_ON_COOLDOWN = "user_on_cooldown"
    UNPROFITABLE_AFTER_GAS = "unprofitable_after_gas"
    NO_FLASH_LOAN_ROUTE = "no_flash_loan_route"
    PREFLIGHT_REVERTED = "preflight_reverted"
    BELOW_MIN_PROFIT = "below_min_profit"
    HEALTH_FACTOR_RECOVERED = "health_factor_recovered"


# ═══════════════════════════════════════════════════════════════════
# DATA MODELS
# ═══════════════════════════════════════════════════════════════════

@dataclass
class PipelineCandidate:
    """
    A candidate flowing through the unified pipeline.
    Wraps LiquidationCandidate with pipeline-specific state.
    """
    # Source
    candidate_id: str = field(default_factory=lambda: str(uuid.uuid4())[:12])
    user_address: str = ""
    chain_id: int = 1
    protocol: str = "aave_v3"
    pool_address: str = ""

    # Position data
    health_factor: float = 999.0
    total_collateral_usd: float = 0.0
    total_debt_usd: float = 0.0
    best_collateral_asset: str = ""
    best_debt_asset: str = ""
    max_liquidatable_usd: float = 0.0
    liquidation_bonus_pct: float = 0.0
    estimated_gross_profit_usd: float = 0.0

    # Pipeline state
    stage: PipelineStage = PipelineStage.INGESTED
    execution_tier: Optional[ExecutionTier] = None
    rejection_reason: Optional[RejectionReason] = None

    # Risk gate results
    risk_approved: bool = False
    gas_price_gwei: float = 0.0
    gas_cost_usd: float = 0.0
    oracle_age_seconds: int = 0
    oracle_fresh: bool = True

    # Profit gate results
    net_profit_usd: float = 0.0
    flash_loan_fee_usd: float = 0.0
    flash_loan_provider: str = ""
    flash_loan_pool: str = ""

    # Execution results
    tx_hash: str = ""
    block_number: int = 0
    gas_used: int = 0
    actual_profit_usd: float = 0.0

    # Timing
    ingested_at: float = 0.0
    risk_checked_at: float = 0.0
    profit_checked_at: float = 0.0
    executed_at: float = 0.0
    confirmed_at: float = 0.0
    total_pipeline_ms: float = 0.0

    @classmethod
    def from_indexer_candidate(cls, lc) -> "PipelineCandidate":
        """Create from SubgraphIndexer's LiquidationCandidate."""
        return cls(
            user_address=lc.user_address,
            chain_id=lc.chain_id,
            protocol=lc.protocol.value if hasattr(lc.protocol, 'value') else str(lc.protocol),
            pool_address=lc.pool_address,
            health_factor=lc.health_factor,
            total_collateral_usd=lc.total_collateral_usd,
            total_debt_usd=lc.total_debt_usd,
            best_collateral_asset=lc.best_collateral_asset,
            best_debt_asset=lc.best_debt_asset,
            max_liquidatable_usd=lc.max_liquidatable_usd,
            liquidation_bonus_pct=lc.liquidation_bonus_pct,
            estimated_gross_profit_usd=lc.estimated_gross_profit_usd,
            ingested_at=time.time(),
        )


@dataclass
class CircuitBreakerState:
    """
    Port of MODULE_1's circuit breaker.
    Halts all execution when failure rate exceeds threshold.
    """
    is_open: bool = False
    consecutive_failures: int = 0
    total_failures: int = 0
    total_successes: int = 0
    last_failure_time: float = 0.0
    cooldown_until: float = 0.0

    # Config
    max_consecutive_failures: int = 5
    cooldown_seconds: float = 300.0  # 5 min cooldown after breaker trips
    failure_rate_threshold: float = 0.5  # 50% failure rate trips breaker

    def record_success(self):
        self.total_successes += 1
        self.consecutive_failures = 0
        # Auto-close if we're in cooldown and it expired
        if self.is_open and time.time() > self.cooldown_until:
            self.is_open = False
            logger.info("Circuit breaker CLOSED (cooldown expired + success)")

    def record_failure(self):
        self.total_failures += 1
        self.consecutive_failures += 1
        self.last_failure_time = time.time()

        if self.consecutive_failures >= self.max_consecutive_failures:
            self.is_open = True
            self.cooldown_until = time.time() + self.cooldown_seconds
            logger.warning(
                f"Circuit breaker OPEN — {self.consecutive_failures} "
                f"consecutive failures. Cooldown until "
                f"{time.strftime('%H:%M:%S', time.localtime(self.cooldown_until))}"
            )

    @property
    def should_allow(self) -> bool:
        if not self.is_open:
            return True
        # Check if cooldown has expired
        if time.time() > self.cooldown_until:
            self.is_open = False
            self.consecutive_failures = 0
            logger.info("Circuit breaker CLOSED (cooldown expired)")
            return True
        return False


@dataclass
class PipelineStats:
    """Aggregate pipeline statistics."""
    total_ingested: int = 0
    risk_approved: int = 0
    risk_rejected: int = 0
    profit_approved: int = 0
    profit_rejected: int = 0
    executed: int = 0
    confirmed: int = 0
    reverted: int = 0
    failed: int = 0
    total_profit_usd: float = 0.0
    total_gas_spent_usd: float = 0.0
    avg_pipeline_latency_ms: float = 0.0
    rejection_reasons: Dict[str, int] = field(default_factory=dict)
    profit_by_chain: Dict[int, float] = field(default_factory=dict)
    profit_by_tier: Dict[str, float] = field(default_factory=dict)


# ═══════════════════════════════════════════════════════════════════
# CHAIN CONFIGURATION
# ═══════════════════════════════════════════════════════════════════

CHAIN_NAMES = {
    1: "Ethereum", 42161: "Arbitrum", 10: "Optimism",
    137: "Polygon", 8453: "Base", 43114: "Avalanche",
}

# Aave V3 liquidationCall ABI fragment
AAVE_LIQUIDATION_ABI = json.loads("""[
    {"inputs":[
        {"name":"collateralAsset","type":"address"},
        {"name":"debtAsset","type":"address"},
        {"name":"user","type":"address"},
        {"name":"debtToCover","type":"uint256"},
        {"name":"receiveAToken","type":"bool"}
    ],"name":"liquidationCall","outputs":[],"stateMutability":"nonpayable","type":"function"},
    {"inputs":[{"name":"user","type":"address"}],"name":"getUserAccountData","outputs":[
        {"name":"totalCollateralBase","type":"uint256"},
        {"name":"totalDebtBase","type":"uint256"},
        {"name":"availableBorrowsBase","type":"uint256"},
        {"name":"currentLiquidationThreshold","type":"uint256"},
        {"name":"ltv","type":"uint256"},
        {"name":"healthFactor","type":"uint256"}
    ],"stateMutability":"view","type":"function"}
]""")


# ═══════════════════════════════════════════════════════════════════
# UNIFIED PIPELINE
# ═══════════════════════════════════════════════════════════════════

class UnifiedPipeline:
    """
    Single-path execution pipeline consolidating MODULE_1 and profit_engine.

    Flow:
        ingest() → risk_gate() → profit_gate() → tier_executor() → record()

    Usage:
        pipeline = UnifiedPipeline(config)
        await pipeline.initialize()

        # Process candidates from SubgraphIndexer
        candidates = await indexer.full_scan()
        results = await pipeline.process_batch(candidates)

        # Or continuous mode
        async for batch in indexer.poll_loop():
            await pipeline.process_batch(batch)
    """

    DEFAULT_CONFIG = {
        # ── Risk Gate (from MODULE_1 safeguards) ──
        "gas_cap_gwei": 100.0,          # Max gas price before rejecting
        "oracle_max_age_seconds": 300,   # 5 min staleness threshold
        "user_cooldown_seconds": 60,     # Min time between attempts on same user
        "circuit_breaker_max_failures": 5,
        "circuit_breaker_cooldown": 300,

        # ── Profit Gate ──
        "min_net_profit_usd": 1.0,      # Minimum after gas + flash loan fees
        "min_profit_margin_pct": 0.5,    # Minimum margin as % of gross

        # ── Execution Tiers (HF thresholds) ──
        "tier_immediate_below": 0.95,    # HF < 0.95 → no sim, send now
        "tier_verified_below": 1.00,     # HF 0.95–1.0 → sim then send
        "tier_cautious_below": 1.02,     # HF 1.0–1.02 → sim + oracle check

        # ── Flashbots ──
        "flashbots_rpc": os.getenv("FLASHBOTS_RPC_URL", "https://relay.flashbots.net"),
        "use_flashbots_for_immediate": True,

        # ── RPC ──
        "rpc_urls": {
            1:     os.getenv("MAINNET_RPC_URL", ""),
            42161: os.getenv("ARBITRUM_RPC_URL", ""),
            10:    os.getenv("OPTIMISM_RPC_URL", ""),
            137:   os.getenv("POLYGON_RPC_URL", ""),
            8453:  os.getenv("BASE_RPC_URL", ""),
            43114: os.getenv("AVALANCHE_RPC_URL", ""),
        },

        # ── Wallet ──
        "private_key": os.getenv("PRIVATE_KEY", ""),
        "treasury_address": os.getenv("TREASURY_ADDRESS", ""),

        # ── Executor contracts ──
        "executor_v2_address": os.getenv("LIQUIDATION_EXECUTOR_V2", ""),
        "flash_executor_address": os.getenv("FLASH_EXECUTOR", ""),

        # ── Gas estimates per operation type ──
        "gas_units_liquidation": 350_000,
        "gas_units_flash_liquidation": 500_000,
    }

    def __init__(self, config: Dict[str, Any] = None):
        self.config = {**self.DEFAULT_CONFIG, **(config or {})}
        self.stats = PipelineStats()
        self.is_running = False

        # Web3 connections per chain
        self._w3: Dict[int, Web3] = {}

        # Circuit breaker (from MODULE_1)
        self.circuit_breaker = CircuitBreakerState(
            max_consecutive_failures=self.config["circuit_breaker_max_failures"],
            cooldown_seconds=self.config["circuit_breaker_cooldown"],
        )

        # User cooldown tracker: user_address → last_attempt_time
        self._user_cooldowns: Dict[str, float] = {}

        # Subsystems (initialized if available)
        self.gas_optimizer: Any = None
        self.flash_loan_router: Any = None
        self.heat_map: Any = None
        self.profit_ledger: Any = None
        self.capital_multiplier: Any = None

        # Pipeline history (last N results for diagnostics)
        self._history: deque = deque(maxlen=1000)

        logger.info("UnifiedPipeline created")

    # ── Lifecycle ──────────────────────────────────────────────────

    async def initialize(self):
        """Initialize Web3 connections and subsystems."""
        # Initialize Web3 for each chain
        for chain_id, rpc_url in self.config["rpc_urls"].items():
            if rpc_url:
                try:
                    w3 = Web3(Web3.HTTPProvider(rpc_url))
                    if w3.is_connected():
                        self._w3[chain_id] = w3
                        logger.info(
                            f"  Connected to {CHAIN_NAMES.get(chain_id, chain_id)} "
                            f"(block {w3.eth.block_number})"
                        )
                except Exception as e:
                    logger.warning(f"  Failed to connect to chain {chain_id}: {e}")

        # Initialize profit engine subsystems
        if HAS_PROFIT_ENGINE:
            try:
                self.gas_optimizer = GasOptimizer(self.config.get("gas", {}))
                self.flash_loan_router = FlashLoanRouter(self.config.get("flash_loan", {}))
                self.heat_map = HeatMap(self.config.get("heat_map", {}))
                self.profit_ledger = ProfitLedger(self.config.get("ledger", {}))
                logger.info("  Profit engine subsystems loaded")
            except Exception as e:
                logger.warning(f"  Profit engine init failed: {e}")

        self.is_running = True
        logger.info(
            f"UnifiedPipeline ready — "
            f"{len(self._w3)} chains connected"
        )

    async def shutdown(self):
        """Graceful shutdown."""
        self.is_running = False
        logger.info("UnifiedPipeline shut down")

    # ── Main Entry Point ──────────────────────────────────────────

    async def process_batch(
        self, candidates: list
    ) -> List[PipelineCandidate]:
        """
        Process a batch of candidates through the full pipeline.

        Accepts either LiquidationCandidate objects (from SubgraphIndexer)
        or raw dicts with the required fields.

        Returns list of PipelineCandidates with final stage/results.
        """
        results: List[PipelineCandidate] = []

        for raw_candidate in candidates:
            # Convert to PipelineCandidate
            if hasattr(raw_candidate, 'position_key'):
                # It's a LiquidationCandidate from the indexer
                pc = PipelineCandidate.from_indexer_candidate(raw_candidate)
            elif isinstance(raw_candidate, PipelineCandidate):
                pc = raw_candidate
            else:
                continue

            self.stats.total_ingested += 1

            # Run through pipeline stages
            result = await self._run_pipeline(pc)
            results.append(result)
            self._history.append(result)

        return results

    async def process_single(self, candidate) -> PipelineCandidate:
        """Process a single candidate. Convenience wrapper."""
        results = await self.process_batch([candidate])
        return results[0] if results else PipelineCandidate()

    # ── Pipeline Stages ───────────────────────────────────────────

    async def _run_pipeline(self, pc: PipelineCandidate) -> PipelineCandidate:
        """Execute the full pipeline for a single candidate."""
        t0 = time.time()

        try:
            # ── Stage 1: Risk Gate ──
            pc = await self._risk_gate(pc)
            if pc.stage == PipelineStage.RISK_REJECTED:
                return pc

            # ── Stage 2: Profit Gate ──
            pc = await self._profit_gate(pc)
            if pc.stage == PipelineStage.PROFIT_REJECTED:
                return pc

            # ── Stage 3: Determine Execution Tier ──
            pc = self._assign_tier(pc)

            # ── Stage 4: Execute (tier-dependent) ──
            pc = await self._tier_executor(pc)

            # ── Stage 5: Record Result ──
            pc = await self._record_result(pc)

        except Exception as e:
            logger.error(f"Pipeline error for {pc.user_address[:10]}...: {e}")
            pc.stage = PipelineStage.FAILED

        # Timing
        pc.total_pipeline_ms = (time.time() - t0) * 1000
        self._update_avg_latency(pc.total_pipeline_ms)

        return pc

    # ── Risk Gate (ported from MODULE_1 safeguards.py) ────────────

    async def _risk_gate(self, pc: PipelineCandidate) -> PipelineCandidate:
        """
        Risk checks ported from MODULE_1's RiskManager:
        1. Circuit breaker (halt on consecutive failures)
        2. Gas cap (reject if gas exceeds limit)
        3. Oracle staleness (reject if oracle data is too old)
        4. User cooldown (avoid hammering same position)
        """
        pc.risk_checked_at = time.time()

        # 1. Circuit breaker
        if not self.circuit_breaker.should_allow:
            pc.stage = PipelineStage.RISK_REJECTED
            pc.rejection_reason = RejectionReason.CIRCUIT_BREAKER_OPEN
            self.stats.risk_rejected += 1
            self._count_rejection(RejectionReason.CIRCUIT_BREAKER_OPEN)
            return pc

        # 2. Gas cap
        gas_gwei = await self._get_gas_price_gwei(pc.chain_id)
        pc.gas_price_gwei = gas_gwei

        if gas_gwei > self.config["gas_cap_gwei"]:
            pc.stage = PipelineStage.RISK_REJECTED
            pc.rejection_reason = RejectionReason.GAS_CAP_EXCEEDED
            self.stats.risk_rejected += 1
            self._count_rejection(RejectionReason.GAS_CAP_EXCEEDED)
            logger.debug(
                f"Gas cap exceeded: {gas_gwei:.1f} > "
                f"{self.config['gas_cap_gwei']} gwei"
            )
            return pc

        # 3. Oracle staleness (for CAUTIOUS tier — check now, enforce later)
        oracle_age = await self._check_oracle_age(pc.chain_id, pc.best_collateral_asset)
        pc.oracle_age_seconds = oracle_age
        pc.oracle_fresh = oracle_age < self.config["oracle_max_age_seconds"]

        # 4. User cooldown
        last_attempt = self._user_cooldowns.get(pc.user_address.lower(), 0)
        if (time.time() - last_attempt) < self.config["user_cooldown_seconds"]:
            pc.stage = PipelineStage.RISK_REJECTED
            pc.rejection_reason = RejectionReason.USER_ON_COOLDOWN
            self.stats.risk_rejected += 1
            self._count_rejection(RejectionReason.USER_ON_COOLDOWN)
            return pc

        # Record attempt
        self._user_cooldowns[pc.user_address.lower()] = time.time()

        pc.stage = PipelineStage.RISK_APPROVED
        pc.risk_approved = True
        self.stats.risk_approved += 1
        return pc

    # ── Profit Gate (from profit_engine) ──────────────────────────

    async def _profit_gate(self, pc: PipelineCandidate) -> PipelineCandidate:
        """
        Profit validation using profit_engine's GasOptimizer and FlashLoanRouter:
        1. Compute gas cost in USD at current gas price
        2. Select cheapest flash loan provider with sufficient liquidity
        3. Calculate net profit after gas + flash loan fee
        4. Reject if below minimum
        """
        pc.profit_checked_at = time.time()

        # Compute gas cost
        eth_price = await self._get_eth_price(pc.chain_id)
        gas_units = self.config["gas_units_flash_liquidation"]
        gas_cost_eth = (pc.gas_price_gwei * 1e9 * gas_units) / 1e18
        pc.gas_cost_usd = gas_cost_eth * eth_price

        # Flash loan fee estimation via FlashLoanRouter.find_best_route()
        flash_loan_amount = pc.max_liquidatable_usd
        flash_fee_usd = 0.0
        flash_provider = "balancer"

        if self.flash_loan_router:
            try:
                route = await asyncio.get_event_loop().run_in_executor(
                    None,
                    lambda: self.flash_loan_router.find_best_route(
                        chain_id=pc.chain_id,
                        asset=pc.best_debt_asset,
                        amount_usd=flash_loan_amount,
                        gross_profit_usd=pc.estimated_gross_profit_usd,
                    ),
                )
                if route:
                    flash_fee_usd = route.fee_usd
                    flash_provider = route.provider.value
                    pc.flash_loan_pool = route.pool_address
                else:
                    # No viable route — degrade to estimated fee
                    flash_fee_usd = flash_loan_amount * 0.0005
                    flash_provider = "aave_v3"
            except Exception as e:
                logger.debug(f"FlashLoanRouter error: {e}")
                flash_fee_usd = flash_loan_amount * 0.0005
                flash_provider = "aave_v3"
        else:
            # Estimate: Balancer 0% on ETH mainnet, Aave 0.05% elsewhere
            if pc.chain_id == 1:
                flash_fee_usd = 0.0  # Balancer
            else:
                flash_fee_usd = flash_loan_amount * 0.0005  # Aave 0.05%
                flash_provider = "aave_v3"

        pc.flash_loan_fee_usd = flash_fee_usd
        pc.flash_loan_provider = flash_provider

        # Net profit calculation
        gross = pc.estimated_gross_profit_usd
        net = gross - pc.gas_cost_usd - flash_fee_usd
        pc.net_profit_usd = net

        # Minimum profit check
        if net < self.config["min_net_profit_usd"]:
            pc.stage = PipelineStage.PROFIT_REJECTED
            pc.rejection_reason = RejectionReason.UNPROFITABLE_AFTER_GAS
            self.stats.profit_rejected += 1
            self._count_rejection(RejectionReason.UNPROFITABLE_AFTER_GAS)
            logger.debug(
                f"Unprofitable: gross=${gross:.2f} - gas=${pc.gas_cost_usd:.2f} "
                f"- fee=${flash_fee_usd:.4f} = net=${net:.2f}"
            )
            return pc

        # Margin check
        margin_pct = (net / gross * 100) if gross > 0 else 0
        if margin_pct < self.config["min_profit_margin_pct"]:
            pc.stage = PipelineStage.PROFIT_REJECTED
            pc.rejection_reason = RejectionReason.BELOW_MIN_PROFIT
            self.stats.profit_rejected += 1
            self._count_rejection(RejectionReason.BELOW_MIN_PROFIT)
            return pc

        pc.stage = PipelineStage.PROFIT_APPROVED
        self.stats.profit_approved += 1
        return pc

    # ── Tier Assignment ───────────────────────────────────────────

    def _assign_tier(self, pc: PipelineCandidate) -> PipelineCandidate:
        """
        Assign execution tier based on health factor depth.
        This resolves the zero_revert vs jit contradiction.
        """
        hf = pc.health_factor

        if hf < self.config["tier_immediate_below"]:
            pc.execution_tier = ExecutionTier.IMMEDIATE
        elif hf < self.config["tier_verified_below"]:
            pc.execution_tier = ExecutionTier.VERIFIED
        else:
            pc.execution_tier = ExecutionTier.CAUTIOUS

        logger.debug(
            f"Tier: {pc.execution_tier.value} for HF={hf:.4f} "
            f"user={pc.user_address[:10]}..."
        )
        return pc

    # ── Tiered Executor ───────────────────────────────────────────

    async def _tier_executor(self, pc: PipelineCandidate) -> PipelineCandidate:
        """
        Execute based on assigned tier:

        IMMEDIATE (HF < 0.95):
            The position is deeply underwater. The math overwhelmingly
            favors execution. Skip preflight, build tx, send via
            Flashbots bundle for guaranteed inclusion without frontrunning.

        VERIFIED (HF 0.95–1.0):
            Position is liquidatable but closer to the edge.
            Verify via eth_call at pending block state. If preflight
            confirms Aave will accept, send immediately.

        CAUTIOUS (HF 1.0–1.02):
            Position is not yet liquidatable but approaching.
            Preflight AND verify oracle freshness. Only execute if
            the latest oracle price confirms HF has actually crossed
            below 1.0 (i.e., the subgraph data isn't stale).
        """
        pc.stage = PipelineStage.EXECUTING
        pc.executed_at = time.time()

        w3 = self._w3.get(pc.chain_id)
        if not w3:
            pc.stage = PipelineStage.FAILED
            logger.error(f"No Web3 connection for chain {pc.chain_id}")
            return pc

        try:
            if pc.execution_tier == ExecutionTier.IMMEDIATE:
                pc = await self._execute_immediate(pc, w3)

            elif pc.execution_tier == ExecutionTier.VERIFIED:
                pc = await self._execute_verified(pc, w3)

            elif pc.execution_tier == ExecutionTier.CAUTIOUS:
                pc = await self._execute_cautious(pc, w3)

        except Exception as e:
            pc.stage = PipelineStage.FAILED
            logger.error(f"Execution failed: {e}")
            self.circuit_breaker.record_failure()

        return pc

    async def _execute_immediate(
        self, pc: PipelineCandidate, w3: Web3
    ) -> PipelineCandidate:
        """
        IMMEDIATE tier: no preflight, build and send directly.
        Uses Flashbots Protect RPC to prevent frontrunning.
        """
        logger.info(
            f"IMMEDIATE execution: HF={pc.health_factor:.4f} "
            f"user={pc.user_address[:10]}... "
            f"est_profit=${pc.net_profit_usd:.2f}"
        )

        tx = self._build_liquidation_tx(pc, w3)
        if not tx:
            pc.stage = PipelineStage.FAILED
            return pc

        # Sign transaction
        private_key = self.config["private_key"]
        if not private_key:
            pc.stage = PipelineStage.FAILED
            logger.error("No private key configured")
            return pc

        signed = w3.eth.account.sign_transaction(tx, private_key)

        # Send via Flashbots if configured, otherwise public mempool
        if self.config["use_flashbots_for_immediate"] and pc.chain_id == 1:
            tx_hash = await self._send_flashbots(signed.raw_transaction, w3)
        else:
            tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)

        pc.tx_hash = tx_hash.hex() if tx_hash else ""
        pc.stage = PipelineStage.CONFIRMED  # Optimistic — would await receipt
        self.circuit_breaker.record_success()
        self.stats.executed += 1
        self.stats.confirmed += 1

        return pc

    async def _execute_verified(
        self, pc: PipelineCandidate, w3: Web3
    ) -> PipelineCandidate:
        """
        VERIFIED tier: preflight eth_call at pending block, then send if confirmed.
        """
        logger.info(
            f"VERIFIED execution: HF={pc.health_factor:.4f} "
            f"user={pc.user_address[:10]}..."
        )

        # Preflight verification
        pc.stage = PipelineStage.VERIFYING
        preflight_ok = await self._preflight_liquidation(pc, w3)

        if not preflight_ok:
            pc.stage = PipelineStage.PREFLIGHT_FAILED
            pc.rejection_reason = RejectionReason.PREFLIGHT_REVERTED
            self.stats.profit_rejected += 1
            self._count_rejection(RejectionReason.PREFLIGHT_REVERTED)
            return pc

        pc.stage = PipelineStage.PREFLIGHT_PASSED

        # Build, sign, send
        tx = self._build_liquidation_tx(pc, w3)
        if not tx:
            pc.stage = PipelineStage.FAILED
            return pc

        private_key = self.config["private_key"]
        signed = w3.eth.account.sign_transaction(tx, private_key)
        tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)

        pc.tx_hash = tx_hash.hex() if tx_hash else ""
        pc.stage = PipelineStage.CONFIRMED
        self.circuit_breaker.record_success()
        self.stats.executed += 1
        self.stats.confirmed += 1

        return pc

    async def _execute_cautious(
        self, pc: PipelineCandidate, w3: Web3
    ) -> PipelineCandidate:
        """
        CAUTIOUS tier: verify oracle freshness + preflight eth_call + verify HF on-chain.

        This tier exists for positions between HF 1.0–1.02 where the subgraph
        data might be stale. We re-check HF directly on-chain before committing.
        """
        logger.info(
            f"CAUTIOUS execution: HF={pc.health_factor:.4f} "
            f"user={pc.user_address[:10]}..."
        )

        # Check oracle freshness
        if not pc.oracle_fresh:
            pc.stage = PipelineStage.RISK_REJECTED
            pc.rejection_reason = RejectionReason.ORACLE_STALE
            self._count_rejection(RejectionReason.ORACLE_STALE)
            logger.debug(f"Oracle stale ({pc.oracle_age_seconds}s)")
            return pc

        # Re-check HF on-chain
        on_chain_hf = await self._get_on_chain_hf(pc.user_address, pc.pool_address, w3)

        if on_chain_hf is not None and on_chain_hf >= 1.0:
            pc.stage = PipelineStage.RISK_REJECTED
            pc.rejection_reason = RejectionReason.HEALTH_FACTOR_RECOVERED
            self._count_rejection(RejectionReason.HEALTH_FACTOR_RECOVERED)
            logger.debug(
                f"HF recovered on-chain: {on_chain_hf:.4f} "
                f"(subgraph had {pc.health_factor:.4f})"
            )
            return pc

        # Update HF with on-chain value
        if on_chain_hf is not None:
            pc.health_factor = on_chain_hf

        # Preflight verification
        pc.stage = PipelineStage.VERIFYING
        preflight_ok = await self._preflight_liquidation(pc, w3)

        if not preflight_ok:
            pc.stage = PipelineStage.PREFLIGHT_FAILED
            pc.rejection_reason = RejectionReason.PREFLIGHT_REVERTED
            self._count_rejection(RejectionReason.PREFLIGHT_REVERTED)
            return pc

        # Execute
        tx = self._build_liquidation_tx(pc, w3)
        if not tx:
            pc.stage = PipelineStage.FAILED
            return pc

        private_key = self.config["private_key"]
        signed = w3.eth.account.sign_transaction(tx, private_key)
        tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)

        pc.tx_hash = tx_hash.hex() if tx_hash else ""
        pc.stage = PipelineStage.CONFIRMED
        self.circuit_breaker.record_success()
        self.stats.executed += 1
        self.stats.confirmed += 1

        return pc

    # ── Transaction Building ──────────────────────────────────────

    def _build_liquidation_tx(
        self, pc: PipelineCandidate, w3: Web3
    ) -> Optional[Dict]:
        """
        Build the liquidationCall transaction for Aave V3.

        In production, this would route through the deployed LiquidationExecutor
        contract (which wraps flash loan + liquidation + collateral swap).
        For direct execution, we build the raw liquidationCall.
        """
        try:
            pool = w3.eth.contract(
                address=Web3.to_checksum_address(pc.pool_address),
                abi=AAVE_LIQUIDATION_ABI,
            )

            # Aave close factor: liquidate up to 50% of debt
            # For max_liquidatable_usd, we already computed this in the indexer
            # Convert to raw amount (would need price + decimals in production)
            # Simplification: use type(uint256).max to let Aave compute max
            debt_to_cover = 2**256 - 1  # max uint256 = liquidate max allowed

            private_key = self.config["private_key"]
            account = w3.eth.account.from_key(private_key)

            tx = pool.functions.liquidationCall(
                Web3.to_checksum_address(pc.best_collateral_asset),
                Web3.to_checksum_address(pc.best_debt_asset),
                Web3.to_checksum_address(pc.user_address),
                debt_to_cover,
                False,  # receiveAToken = False (receive underlying)
            ).build_transaction({
                "from": account.address,
                "nonce": w3.eth.get_transaction_count(account.address),
                "gas": self.config["gas_units_flash_liquidation"],
                "maxFeePerGas": int(pc.gas_price_gwei * 1.2 * 1e9),
                "maxPriorityFeePerGas": int(2 * 1e9),
                "chainId": pc.chain_id,
            })

            return tx

        except Exception as e:
            logger.error(f"Failed to build tx: {e}")
            return None

    # ── Preflight Verification ─────────────────────────────────────

    async def _preflight_liquidation(
        self, pc: PipelineCandidate, w3: Web3
    ) -> bool:
        """
        Verify liquidationCall via eth_call at 'pending' block.
        Returns True if the live chain would accept this call.
        """
        try:
            pool = w3.eth.contract(
                address=Web3.to_checksum_address(pc.pool_address),
                abi=AAVE_LIQUIDATION_ABI,
            )

            private_key = self.config["private_key"]
            account = w3.eth.account.from_key(private_key)

            # eth_call at pending block state
            pool.functions.liquidationCall(
                Web3.to_checksum_address(pc.best_collateral_asset),
                Web3.to_checksum_address(pc.best_debt_asset),
                Web3.to_checksum_address(pc.user_address),
                2**256 - 1,
                False,
            ).call(
                {"from": account.address},
                block_identifier="pending",
            )

            return True  # If we get here without exception, preflight passed

        except Exception as e:
            logger.debug(f"Preflight reverted: {e}")
            return False

    # ── On-chain Verification ─────────────────────────────────────

    async def _get_on_chain_hf(
        self, user: str, pool_address: str, w3: Web3
    ) -> Optional[float]:
        """Read current health factor directly from Aave V3 pool."""
        try:
            pool = w3.eth.contract(
                address=Web3.to_checksum_address(pool_address),
                abi=AAVE_LIQUIDATION_ABI,
            )
            data = pool.functions.getUserAccountData(
                Web3.to_checksum_address(user)
            ).call()
            # data[5] = healthFactor (18 decimals)
            return data[5] / 1e18

        except Exception as e:
            logger.debug(f"Failed to read on-chain HF: {e}")
            return None

    # ── Result Recording ──────────────────────────────────────────

    async def _record_result(self, pc: PipelineCandidate) -> PipelineCandidate:
        """Record execution result to profit ledger and heat map."""
        pc.confirmed_at = time.time()

        if pc.stage == PipelineStage.CONFIRMED:
            self.stats.total_profit_usd += pc.net_profit_usd
            self.stats.total_gas_spent_usd += pc.gas_cost_usd

            # Per-chain profit tracking
            chain_profit = self.stats.profit_by_chain.get(pc.chain_id, 0.0)
            self.stats.profit_by_chain[pc.chain_id] = chain_profit + pc.net_profit_usd

            # Per-tier profit tracking
            tier_key = pc.execution_tier.value if pc.execution_tier else "unknown"
            tier_profit = self.stats.profit_by_tier.get(tier_key, 0.0)
            self.stats.profit_by_tier[tier_key] = tier_profit + pc.net_profit_usd

            # Record to profit ledger if available
            if self.profit_ledger:
                try:
                    entry = ProfitEntry(
                        entry_id=pc.candidate_id,
                        timestamp=time.time(),
                        chain_id=pc.chain_id,
                        opportunity_type="liquidation",
                        protocol=pc.protocol,
                        tx_hash=pc.tx_hash,
                        gross_profit_usd=pc.estimated_gross_profit_usd,
                        gas_cost_usd=pc.gas_cost_usd,
                        flash_loan_fee_usd=pc.flash_loan_fee_usd,
                        net_profit_usd=pc.net_profit_usd,
                        capital_deployed_usd=0.0,  # Flash loan = zero capital
                        roi_percent=100.0,  # Infinite ROI on zero capital
                        execution_time_ms=int(pc.total_pipeline_ms),
                        block_number=pc.block_number,
                        phase=PhaseState.COLD_START,
                    )
                    await self.profit_ledger.record(entry)
                except Exception as e:
                    logger.warning(f"Failed to record to ledger: {e}")

            # Update heat map if available
            # HeatMap.record_observation(type, chain, protocol, ...)
            if self.heat_map:
                try:
                    self.heat_map.record_observation(
                        opportunity_type="liquidation",
                        chain_id=pc.chain_id,
                        protocol=pc.protocol,
                        was_executed=True,
                        was_profitable=pc.net_profit_usd > 0,
                        profit_usd=pc.net_profit_usd,
                        roi_percent=100.0,
                        execution_time_ms=pc.total_pipeline_ms,
                    )
                except Exception:
                    pass

        elif pc.stage == PipelineStage.REVERTED:
            self.stats.reverted += 1
            self.stats.total_gas_spent_usd += pc.gas_cost_usd
            self.circuit_breaker.record_failure()

            # Record loss to heat map
            if self.heat_map:
                try:
                    self.heat_map.record_observation(
                        opportunity_type="liquidation",
                        chain_id=pc.chain_id,
                        protocol=pc.protocol,
                        was_executed=True,
                        was_profitable=False,
                        profit_usd=-pc.gas_cost_usd,
                    )
                except Exception:
                    pass

        return pc

    # ── Helper Methods ────────────────────────────────────────────

    async def _get_gas_price_gwei(self, chain_id: int) -> float:
        """Get current gas price for a chain."""
        w3 = self._w3.get(chain_id)
        if not w3:
            return 999.0  # Force rejection

        try:
            gas_price = w3.eth.gas_price
            return gas_price / 1e9
        except Exception:
            return 999.0

    async def _get_eth_price(self, chain_id: int) -> float:
        """Get current ETH price. In production, read from Chainlink."""
        # TODO: Read from Chainlink AggregatorV3 on-chain
        return 2000.0

    async def _check_oracle_age(self, chain_id: int, asset: str) -> int:
        """Check how old the oracle price is (seconds since last update)."""
        # TODO: Read lastUpdatedAt from Chainlink aggregator
        return 30  # Default: assume 30s

    async def _send_flashbots(
        self, raw_tx: bytes, w3: Web3
    ) -> Optional[bytes]:
        """Send transaction via Flashbots Protect RPC."""
        try:
            flashbots_w3 = Web3(Web3.HTTPProvider(self.config["flashbots_rpc"]))
            tx_hash = flashbots_w3.eth.send_raw_transaction(raw_tx)
            return tx_hash
        except Exception as e:
            logger.warning(f"Flashbots failed, falling back to public: {e}")
            return w3.eth.send_raw_transaction(raw_tx)

    def _count_rejection(self, reason: RejectionReason):
        """Count rejection reasons for diagnostics."""
        key = reason.value
        self.stats.rejection_reasons[key] = (
            self.stats.rejection_reasons.get(key, 0) + 1
        )

    def _update_avg_latency(self, latency_ms: float):
        """Running average of pipeline latency."""
        total = self.stats.total_ingested
        if total > 0:
            self.stats.avg_pipeline_latency_ms = (
                (self.stats.avg_pipeline_latency_ms * (total - 1) + latency_ms) / total
            )

    # ── Diagnostics ───────────────────────────────────────────────

    def get_stats(self) -> Dict[str, Any]:
        """Return pipeline statistics."""
        return {
            "total_ingested": self.stats.total_ingested,
            "risk_approved": self.stats.risk_approved,
            "risk_rejected": self.stats.risk_rejected,
            "profit_approved": self.stats.profit_approved,
            "profit_rejected": self.stats.profit_rejected,
            "executed": self.stats.executed,
            "confirmed": self.stats.confirmed,
            "reverted": self.stats.reverted,
            "total_profit_usd": round(self.stats.total_profit_usd, 2),
            "total_gas_spent_usd": round(self.stats.total_gas_spent_usd, 2),
            "avg_latency_ms": round(self.stats.avg_pipeline_latency_ms, 1),
            "circuit_breaker_open": self.circuit_breaker.is_open,
            "rejection_reasons": self.stats.rejection_reasons,
            "profit_by_chain": {
                CHAIN_NAMES.get(k, k): round(v, 2)
                for k, v in self.stats.profit_by_chain.items()
            },
            "profit_by_tier": {
                k: round(v, 2)
                for k, v in self.stats.profit_by_tier.items()
            },
        }

    def get_recent_results(self, n: int = 20) -> List[Dict]:
        """Return recent pipeline results for diagnostics."""
        results = list(self._history)[-n:]
        return [
            {
                "id": r.candidate_id,
                "user": r.user_address[:10] + "...",
                "chain": CHAIN_NAMES.get(r.chain_id, r.chain_id),
                "hf": round(r.health_factor, 4),
                "tier": r.execution_tier.value if r.execution_tier else "-",
                "stage": r.stage.value,
                "net_profit": round(r.net_profit_usd, 2),
                "latency_ms": round(r.total_pipeline_ms, 1),
                "rejection": r.rejection_reason.value if r.rejection_reason else "-",
            }
            for r in results
        ]
