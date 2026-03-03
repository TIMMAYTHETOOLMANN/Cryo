# =============================================================================
#  Off-Chain Executor — Construct and submit FIRE plans to the blockchain
#
#  Bridges compiled Plans to on-chain execution via the FinancialExecutor
#  contract. Handles gas estimation, transaction construction, and submission
#  through public mempool or private channels (Flashbots).
# =============================================================================

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from fire_engine.compiler import Plan, PlanStep

logger = logging.getLogger(__name__)


@dataclass
class ExecutionResult:
    """Result of an on-chain plan execution."""
    success: bool
    tx_hash: Optional[str] = None
    gas_used: int = 0
    profit_wei: int = 0
    error: Optional[str] = None
    block_number: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "success": self.success,
            "tx_hash": self.tx_hash,
            "gas_used": self.gas_used,
            "profit_wei": self.profit_wei,
            "error": self.error,
            "block_number": self.block_number,
        }


class OffChainExecutor:
    """Off-chain executor that bridges FIRE Plans to the FinancialExecutor contract.

    Constructs transactions from compiled Plans and submits them to the
    blockchain. Supports both standard mempool and private relay submission.

    Usage::

        executor = OffChainExecutor(
            executor_address="0x...",
            rpc_url="https://eth-mainnet.g.alchemy.com/v2/...",
        )
        result = executor.execute(plan)
    """

    def __init__(
        self,
        executor_address: str,
        rpc_url: str = "",
        private_key: str = "",
        use_flashbots: bool = False,
        gas_price_multiplier: float = 1.1,
        max_gas_price_gwei: float = 100.0,
    ) -> None:
        self.executor_address = executor_address
        self.rpc_url = rpc_url
        self._private_key = private_key
        self.use_flashbots = use_flashbots
        self.gas_price_multiplier = gas_price_multiplier
        self.max_gas_price_gwei = max_gas_price_gwei
        self._w3 = None  # Lazy web3 initialization

    def encode_plan_steps(self, plan: Plan) -> List[Dict[str, Any]]:
        """Convert Plan steps to the format expected by FinancialExecutor.executePlan().

        Returns a list of Step tuples: [(target, data, value, allowFailure), ...]
        """
        encoded_steps = []
        for step in plan.steps:
            encoded_steps.append({
                "target": step.target_contract or "0x" + "0" * 40,
                "data": step.calldata.hex() if step.calldata else "0x",
                "value": step.value,
                "allowFailure": step.allow_failure,
            })
        return encoded_steps

    def estimate_gas(self, plan: Plan) -> int:
        """Estimate total gas for a plan, including base transaction cost."""
        base_gas = 21_000  # base tx cost
        step_overhead = 5_000 * len(plan.steps)  # per-step overhead
        return base_gas + step_overhead + plan.total_gas_estimate

    def validate_plan(self, plan: Plan) -> List[str]:
        """Pre-flight validation of a plan before execution.

        Returns list of warning/error messages.
        """
        issues = []
        if plan.step_count == 0:
            issues.append("Plan has no steps")
        for step in plan.steps:
            if not step.target_contract:
                issues.append(f"Step {step.index} ({step.operation_name}): no target contract")
            if step.gas_estimate == 0:
                issues.append(f"Step {step.index} ({step.operation_name}): zero gas estimate")
        return issues

    def execute(self, plan: Plan, dry_run: bool = True) -> ExecutionResult:
        """Execute a compiled plan.

        Args:
            plan: Compiled Plan from the Compiler.
            dry_run: If True, only simulate without submitting.

        Returns:
            ExecutionResult with transaction details.
        """
        # Pre-flight validation
        issues = self.validate_plan(plan)
        if any("no steps" in i for i in issues):
            return ExecutionResult(success=False, error="Plan has no steps")

        # Estimate gas
        gas_estimate = self.estimate_gas(plan)

        if dry_run:
            logger.info(
                "DRY RUN: Plan with %d steps, estimated gas: %d",
                plan.step_count,
                gas_estimate,
            )
            return ExecutionResult(
                success=True,
                gas_used=gas_estimate,
                profit_wei=plan.expected_profit_wei,
            )

        # Production execution requires web3 connection
        if not self._w3:
            return ExecutionResult(
                success=False,
                error="No web3 connection configured",
            )

        return ExecutionResult(success=False, error="Live execution not yet enabled")


class Simulator:
    """Simulation layer for verifying FIRE plans against forked chain state.

    Runs plans against a forked mainnet to verify behavior and estimate
    profit before live execution.

    Usage::

        sim = Simulator(fork_url="https://eth-mainnet.g.alchemy.com/v2/...")
        result = sim.simulate(plan)
    """

    def __init__(self, fork_url: str = "") -> None:
        self.fork_url = fork_url

    def simulate(self, plan: Plan) -> ExecutionResult:
        """Simulate a plan against forked state.

        Args:
            plan: Compiled Plan to simulate.

        Returns:
            ExecutionResult with simulated outcomes.
        """
        if not self.fork_url:
            # Offline simulation — estimate based on plan data
            gas = sum(s.gas_estimate for s in plan.steps)
            return ExecutionResult(
                success=True,
                gas_used=gas,
                profit_wei=plan.expected_profit_wei,
            )

        # Full fork simulation would use hardhat/anvil fork here
        return ExecutionResult(
            success=False,
            error="Fork simulation requires running Anvil/Hardhat node",
        )
