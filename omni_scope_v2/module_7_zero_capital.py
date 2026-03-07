#!/usr/bin/env python3
"""
Module 7 — Zero-Capital Scalability Layer (Self-Bootstrapping)
================================================================
Executes opportunities with zero starting capital, using flash loans
and gas reinvestment.

Capabilities:
  7.1  Flash Loan Surplus Aggregation — batch micro-liquidations in one TX
  7.2  Dynamic Capital Allocation from Zero — first TX self-funds gas
  7.3  Gas Reinvestment Loop — convert profits → native tokens → gas

Architecture:
  Every execution bundle includes:
    1. Flash loan for debt amount
    2. Liquidation / Arb / MEV operation
    3. Swap seized collateral to debt asset
    4. Repay flash loan + fee
    5. Swap portion of profit to native gas token
    6. Send gas to executor contract for future operations

The system is truly $0.00 deployable — bootstrapping its own gas from
the first profitable trade and scaling exponentially thereafter.
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Dict, List, Optional

from .config import ZeroCapitalConfig, get_config
from .data_lake import DataLake
from .signal_bus import SignalBus, SignalSource, SignalType, TriangulatedSignal

logger = logging.getLogger(__name__)


# ── Chain Gas Configuration ──────────────────────────────────────

NATIVE_TOKEN_PRICES: Dict[int, float] = {
    1: 2500.0,      # ETH
    42161: 2500.0,   # ETH (Arbitrum)
    10: 2500.0,      # ETH (Optimism)
    8453: 2500.0,    # ETH (Base)
    137: 0.50,       # MATIC
    43114: 25.0,     # AVAX
    56: 300.0,       # BNB
}

FLASH_LOAN_PROVIDERS: Dict[int, List[Dict[str, Any]]] = {
    1: [
        {"name": "aave_v3", "fee_bps": 5, "max_usd": 500_000_000},
        {"name": "balancer_v2", "fee_bps": 0, "max_usd": 100_000_000},
        {"name": "uniswap_v3", "fee_bps": 0, "max_usd": 50_000_000},
        {"name": "maker_dss", "fee_bps": 0, "max_usd": 200_000_000},
        {"name": "dydx", "fee_bps": 0, "max_usd": 100_000_000},
    ],
    42161: [
        {"name": "aave_v3", "fee_bps": 5, "max_usd": 100_000_000},
        {"name": "balancer_v2", "fee_bps": 0, "max_usd": 50_000_000},
    ],
    10: [
        {"name": "aave_v3", "fee_bps": 5, "max_usd": 50_000_000},
    ],
    137: [
        {"name": "aave_v3", "fee_bps": 5, "max_usd": 100_000_000},
        {"name": "balancer_v2", "fee_bps": 0, "max_usd": 50_000_000},
    ],
}


# ── Data Models ──────────────────────────────────────────────────

@dataclass
class GasFund:
    """Gas fund status for a single chain."""
    chain_id: int
    native_balance: float = 0.0
    native_balance_usd: float = 0.0
    target_balance_usd: float = 5.0
    needs_funding: bool = True
    last_funded: float = 0.0
    total_funded_usd: float = 0.0
    fund_count: int = 0


@dataclass
class ExecutionBundle:
    """A zero-capital execution bundle."""
    bundle_id: str
    chain_id: int
    signal_type: str

    # Flash loan
    flash_provider: str
    flash_amount_usd: float
    flash_fee_usd: float

    # Operation steps
    steps: List[Dict[str, Any]] = field(default_factory=list)

    # Profit distribution
    gross_profit_usd: float = 0.0
    flash_repay_usd: float = 0.0
    gas_allocation_usd: float = 0.0
    treasury_allocation_usd: float = 0.0
    net_profit_usd: float = 0.0

    # Gas self-funding
    gas_swap_step: Optional[Dict] = None
    gas_target_chains: List[int] = field(default_factory=list)


@dataclass
class GasFundingPlan:
    """Plan for distributing gas across chains."""
    source_chain: int
    profit_usd: float
    gas_allocation_usd: float
    gas_allocation_pct: float
    target_chains: List[int] = field(default_factory=list)
    per_chain_usd: Dict[int, float] = field(default_factory=dict)
    swap_route: str = ""
    bridge_routes: Dict[int, str] = field(default_factory=dict)
    estimated_cost_usd: float = 0.0


@dataclass
class SurplusAggregation:
    """Flash loan surplus batch — multiple micro-operations in one TX."""
    batch_id: str
    chain_id: int
    flash_provider: str
    total_flash_amount_usd: float
    operations: List[Dict[str, Any]] = field(default_factory=list)
    total_profit_usd: float = 0.0
    single_flash_fee_usd: float = 0.0


# ── Module ───────────────────────────────────────────────────────

class ZeroCapitalLayer:
    """
    Module 7: Zero-Capital Scalability Layer.

    Self-bootstrapping execution framework that uses flash loans for
    capital, converts profits to gas, and maintains multi-chain gas
    balances from $0.00 starting capital.
    """

    def __init__(
        self,
        bus: SignalBus,
        lake: DataLake,
        config: Optional[ZeroCapitalConfig] = None,
    ):
        self.bus = bus
        self.lake = lake
        self._cfg = config or get_config().zero_capital
        self._running = False

        # Gas funds per chain
        self._gas_funds: Dict[int, GasFund] = {}
        for chain_id, target in self._cfg.target_gas_balance_usd.items():
            self._gas_funds[chain_id] = GasFund(
                chain_id=chain_id,
                target_balance_usd=target,
            )

        # Profit accumulator
        self._total_profit_usd = Decimal("0")
        self._total_gas_funded_usd = Decimal("0")
        self._total_treasury_usd = Decimal("0")

        # Surplus aggregation queue
        self._surplus_queue: List[Dict[str, Any]] = []

        # Stats
        self._stats = {
            "bundles_created": 0,
            "bundles_executed": 0,
            "total_profit_usd": 0.0,
            "total_gas_funded_usd": 0.0,
            "total_treasury_usd": 0.0,
            "gas_self_fund_txs": 0,
            "surplus_batches": 0,
            "signals_emitted": 0,
        }

    # ── Lifecycle ─────────────────────────────────────────────

    async def start(self):
        self._running = True
        logger.info("[ZeroCapital] Starting — bootstrap=%s, gas_pct=%.0f%%",
                     self._cfg.bootstrap_enabled, self._cfg.gas_self_fund_pct * 100)

    async def stop(self):
        self._running = False
        logger.info("[ZeroCapital] Stopped — total profit: $%.2f, gas funded: $%.2f",
                     self._total_profit_usd, self._total_gas_funded_usd)

    async def run_cycle(self):
        """Check gas balances and process surplus queue."""
        if not self._running:
            return

        # Check if any chain needs gas funding
        for fund in self._gas_funds.values():
            fund.needs_funding = fund.native_balance_usd < fund.target_balance_usd * 0.5

        # Process surplus aggregation queue
        if len(self._surplus_queue) >= 3:
            await self._execute_surplus_batch()

    # ── 7.1 Flash Loan Surplus Aggregation ───────────────────

    def queue_for_surplus_batch(self, operation: Dict[str, Any]):
        """Queue a micro-operation for surplus aggregation."""
        self._surplus_queue.append(operation)

    async def _execute_surplus_batch(self):
        """Batch multiple micro-operations into a single flash loan."""
        if not self._surplus_queue:
            return

        chain_id = self._surplus_queue[0].get("chain_id", 1)
        operations = self._surplus_queue[:20]
        self._surplus_queue = self._surplus_queue[20:]

        # Find cheapest flash loan provider
        provider = self._select_flash_provider(chain_id)
        if not provider:
            return

        total_amount = sum(op.get("amount_usd", 0) for op in operations)
        fee = total_amount * provider["fee_bps"] / 10000.0

        batch = SurplusAggregation(
            batch_id=f"surplus_{int(time.time() * 1000)}",
            chain_id=chain_id,
            flash_provider=provider["name"],
            total_flash_amount_usd=total_amount,
            operations=operations,
            single_flash_fee_usd=fee,
        )

        # In production: construct and submit on-chain multicall TX
        logger.info(
            "[ZeroCapital] Surplus batch: %d ops, $%.0f flash from %s, fee=$%.2f",
            len(operations), total_amount, provider["name"], fee,
        )

        self._stats["surplus_batches"] += 1

    # ── 7.2 Dynamic Capital Allocation from Zero ─────────────

    def create_execution_bundle(
        self,
        signal: TriangulatedSignal,
        debt_amount_usd: float,
    ) -> Optional[ExecutionBundle]:
        """
        Create a zero-capital execution bundle for a signal.

        The bundle includes:
          1. Flash loan for the debt amount
          2. Main operation (liquidation, arb, etc.)
          3. Repay flash loan
          4. Gas self-funding step (if enabled)
          5. Treasury sweep for excess profit
        """
        chain_id = signal.chain_id
        provider = self._select_flash_provider(chain_id, debt_amount_usd)
        if not provider:
            logger.debug("[ZeroCapital] No flash provider for chain %d / $%.0f",
                          chain_id, debt_amount_usd)
            return None

        flash_fee = debt_amount_usd * provider["fee_bps"] / 10000.0
        estimated_profit = signal.estimated_profit_usd
        net_after_flash = estimated_profit - flash_fee - signal.gas_cost_estimate_usd

        if net_after_flash <= 0:
            return None

        # Compute profit distribution
        gas_allocation = net_after_flash * self._cfg.gas_self_fund_pct
        treasury_allocation = 0.0
        if self._cfg.treasury_address:
            treasury_allocation = net_after_flash * 0.05  # 5% to treasury

        bundle = ExecutionBundle(
            bundle_id=f"bundle_{int(time.time() * 1000)}",
            chain_id=chain_id,
            signal_type=signal.signal_type.value,
            flash_provider=provider["name"],
            flash_amount_usd=debt_amount_usd,
            flash_fee_usd=flash_fee,
            gross_profit_usd=estimated_profit,
            flash_repay_usd=debt_amount_usd + flash_fee,
            gas_allocation_usd=gas_allocation,
            treasury_allocation_usd=treasury_allocation,
            net_profit_usd=net_after_flash - gas_allocation - treasury_allocation,
        )

        # Build steps
        bundle.steps = self._build_bundle_steps(signal, bundle)

        # Add gas self-funding step
        if self._cfg.bootstrap_enabled and gas_allocation > 0:
            bundle.gas_swap_step = self._build_gas_swap_step(
                chain_id, gas_allocation,
            )
            bundle.gas_target_chains = [
                cid for cid, fund in self._gas_funds.items()
                if fund.needs_funding
            ]

        self._stats["bundles_created"] += 1
        return bundle

    def _build_bundle_steps(self, signal: TriangulatedSignal,
                             bundle: ExecutionBundle) -> List[Dict[str, Any]]:
        """Build the ordered steps for an execution bundle."""
        steps = []

        # Step 1: Flash loan
        steps.append({
            "step": 1,
            "action": "flash_loan",
            "provider": bundle.flash_provider,
            "amount_usd": bundle.flash_amount_usd,
            "fee_usd": bundle.flash_fee_usd,
        })

        # Step 2: Main operation
        if signal.signal_type in (
            SignalType.PENDING_LIQUIDATION,
            SignalType.CASCADING_LIQUIDATION,
        ):
            steps.append({
                "step": 2,
                "action": "liquidate",
                "protocol": signal.target_protocol,
                "borrower": signal.target_user,
                "debt_asset": signal.target_asset,
            })
        elif signal.signal_type == SignalType.ARBITRAGE:
            steps.append({
                "step": 2,
                "action": "arb_swap",
                "path": signal.arb_path,
                "hops": signal.hop_count,
            })
        elif signal.signal_type in (SignalType.BACKRUN, SignalType.SANDWICH):
            steps.append({
                "step": 2,
                "action": "mev_bundle",
                "trigger_tx": signal.tx_hash,
            })
        else:
            steps.append({
                "step": 2,
                "action": "generic_execution",
                "signal_type": signal.signal_type.value,
            })

        # Step 3: Swap collateral to debt asset
        steps.append({
            "step": 3,
            "action": "swap_collateral_to_debt",
            "estimated_output_usd": bundle.gross_profit_usd + bundle.flash_amount_usd,
        })

        # Step 4: Repay flash loan
        steps.append({
            "step": 4,
            "action": "repay_flash_loan",
            "amount_usd": bundle.flash_repay_usd,
        })

        # Step 5: Gas self-funding (profit → native token)
        if bundle.gas_allocation_usd > 0:
            steps.append({
                "step": 5,
                "action": "swap_profit_to_gas",
                "amount_usd": bundle.gas_allocation_usd,
                "target_chains": bundle.gas_target_chains,
            })

        # Step 6: Treasury sweep
        if bundle.treasury_allocation_usd > 0:
            steps.append({
                "step": 6,
                "action": "treasury_sweep",
                "amount_usd": bundle.treasury_allocation_usd,
                "treasury": self._cfg.treasury_address,
            })

        return steps

    def _build_gas_swap_step(self, chain_id: int, amount_usd: float) -> Dict:
        """Build the gas self-funding swap step."""
        native_price = NATIVE_TOKEN_PRICES.get(chain_id, 2500.0)
        native_amount = amount_usd / native_price

        return {
            "action": "swap_to_native",
            "chain_id": chain_id,
            "amount_usd": amount_usd,
            "native_amount": native_amount,
            "route": f"USDC → {'WETH' if chain_id in (1, 42161, 10, 8453) else 'native'} via DEX",
        }

    # ── 7.3 Gas Reinvestment Loop ────────────────────────────

    def record_profit(self, chain_id: int, profit_usd: float, gas_funded: bool = False):
        """Record a successful execution profit."""
        self._total_profit_usd += Decimal(str(profit_usd))
        self._stats["total_profit_usd"] = float(self._total_profit_usd)
        self._stats["bundles_executed"] += 1

        if gas_funded:
            gas_amount = profit_usd * self._cfg.gas_self_fund_pct
            fund = self._gas_funds.get(chain_id)
            if fund:
                fund.native_balance_usd += gas_amount
                fund.total_funded_usd += gas_amount
                fund.fund_count += 1
                fund.last_funded = time.time()
                fund.needs_funding = fund.native_balance_usd < fund.target_balance_usd * 0.5

            self._total_gas_funded_usd += Decimal(str(gas_amount))
            self._stats["total_gas_funded_usd"] = float(self._total_gas_funded_usd)
            self._stats["gas_self_fund_txs"] += 1

    def should_self_fund(self) -> bool:
        """Check if any chain needs gas funding."""
        if not self._cfg.bootstrap_enabled:
            return False
        return any(f.needs_funding for f in self._gas_funds.values())

    def create_funding_plan(self) -> Optional[GasFundingPlan]:
        """Create a plan for distributing gas across needy chains."""
        needy = [f for f in self._gas_funds.values() if f.needs_funding]
        if not needy:
            return None

        # Find chain with most gas (source for bridging)
        richest = max(self._gas_funds.values(),
                      key=lambda f: f.native_balance_usd)
        if richest.native_balance_usd < 1.0:
            return None

        available = richest.native_balance_usd * 0.5
        per_chain = available / max(1, len(needy))

        plan = GasFundingPlan(
            source_chain=richest.chain_id,
            profit_usd=available,
            gas_allocation_usd=available,
            gas_allocation_pct=self._cfg.gas_self_fund_pct,
            target_chains=[f.chain_id for f in needy],
            per_chain_usd={f.chain_id: per_chain for f in needy},
        )
        return plan

    def execute_funding(self, plan: GasFundingPlan) -> bool:
        """Execute a gas funding plan (bridge gas to needy chains)."""
        # In production: submit bridge transactions
        logger.info(
            "[ZeroCapital] Funding plan: $%.2f from chain %d → %d chains",
            plan.gas_allocation_usd, plan.source_chain, len(plan.target_chains),
        )
        for chain_id, amount in plan.per_chain_usd.items():
            fund = self._gas_funds.get(chain_id)
            if fund:
                fund.native_balance_usd += amount
                fund.needs_funding = fund.native_balance_usd < fund.target_balance_usd * 0.5
        return True

    # ── Flash Loan Provider Selection ────────────────────────

    @staticmethod
    def _select_flash_provider(
        chain_id: int, min_amount_usd: float = 0.0,
    ) -> Optional[Dict[str, Any]]:
        """Select the cheapest flash loan provider for a chain."""
        providers = FLASH_LOAN_PROVIDERS.get(chain_id, [])
        eligible = [p for p in providers if p["max_usd"] >= min_amount_usd]
        if not eligible:
            return None

        # Prefer zero-fee providers, then lowest fee
        eligible.sort(key=lambda p: p["fee_bps"])
        return eligible[0]

    # ── Signal Emission ──────────────────────────────────────

    def _emit_surplus_signal(self, batch: SurplusAggregation):
        """Emit a signal when a surplus batch is ready."""
        signal = TriangulatedSignal(
            signal_type=SignalType.FLASH_LOAN_SURPLUS,
            source=SignalSource.ZERO_CAPITAL,
            chain_id=batch.chain_id,
            confidence=0.85,
            estimated_profit_usd=batch.total_profit_usd,
            gas_cost_estimate_usd=15.0,
            urgency_seconds=12.0,
            metadata={
                "batch_id": batch.batch_id,
                "operations": len(batch.operations),
                "flash_provider": batch.flash_provider,
                "total_flash_usd": batch.total_flash_amount_usd,
            },
        )
        self.bus.publish(signal)
        self._stats["signals_emitted"] += 1

    # ── Stats ─────────────────────────────────────────────────

    def get_stats(self) -> Dict[str, Any]:
        gas_status = {}
        for cid, fund in self._gas_funds.items():
            gas_status[str(cid)] = {
                "balance_usd": round(fund.native_balance_usd, 2),
                "target_usd": fund.target_balance_usd,
                "needs_funding": fund.needs_funding,
                "fund_count": fund.fund_count,
            }
        return {
            **self._stats,
            "gas_funds": gas_status,
        }
