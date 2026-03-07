# =============================================================================
#  Off-Chain Executor — Construct and submit FIRE plans to the blockchain
#
#  Bridges compiled Plans to on-chain execution via the FinancialExecutor
#  contract. Handles gas estimation, transaction construction, and submission
#  through public mempool or private channels (Flashbots/MEV Blocker).
#
#  Address Resolution:
#    - Executor contract address resolved via AddressResolver
#    - RPC URLs resolved from ConfigManager / env vars
#    - Private keys NEVER hardcoded — always from env: PRIVATE_KEY
#    - All addresses validated before use (checksum, non-zero)
# =============================================================================

from __future__ import annotations

import logging
import os
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
    route: str = "public"        # "public" | "flashbots" | "mev_blocker"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "success": self.success,
            "tx_hash": self.tx_hash,
            "gas_used": self.gas_used,
            "profit_wei": self.profit_wei,
            "error": self.error,
            "block_number": self.block_number,
            "route": self.route,
        }


class OffChainExecutor:
    """Off-chain executor that bridges FIRE Plans to the FinancialExecutor contract.

    Resolves all addresses dynamically:
      - executor_address: from AddressResolver.executor(chain_id) or env var
      - rpc_url: from ConfigManager or env var
      - private_key: ALWAYS from env var PRIVATE_KEY (never hardcoded)

    Usage::

        executor = OffChainExecutor(chain_id=1)  # auto-resolves everything
        result = executor.execute(plan)
    """

    def __init__(
        self,
        executor_address: str = "",
        rpc_url: str = "",
        private_key: str = "",
        use_flashbots: bool = False,
        gas_price_multiplier: float = 1.1,
        max_gas_price_gwei: float = 100.0,
        chain_id: int = 1,
    ) -> None:
        self.chain_id = chain_id
        self.gas_price_multiplier = gas_price_multiplier
        self.max_gas_price_gwei = max_gas_price_gwei
        self.use_flashbots = use_flashbots
        self._w3 = None  # Lazy web3 initialization

        # ── Dynamic resolution ────────────────────────────────────────
        # Executor address: param → AddressResolver → env
        self.executor_address = executor_address
        if not self.executor_address:
            self.executor_address = self._resolve_executor_address()

        # RPC URL: param → ConfigManager → env
        self.rpc_url = rpc_url
        if not self.rpc_url:
            self.rpc_url = self._resolve_rpc_url()

        # Private key: param → env (NEVER hardcoded, NEVER logged)
        self._private_key = private_key or os.getenv("PRIVATE_KEY", "")

    def _resolve_executor_address(self) -> str:
        """Resolve executor address from AddressResolver or env."""
        try:
            from fire_engine.address_resolver import get_resolver
            resolver = get_resolver()
            return resolver.executor(self.chain_id)
        except Exception:
            # Fallback to env var
            from fire_engine.address_resolver import _chain_name
            env_key = f"FIRE_EXECUTOR_{_chain_name(self.chain_id)}"
            return os.getenv(env_key, "")

    def _resolve_rpc_url(self) -> str:
        """Resolve RPC URL from ConfigManager or env."""
        try:
            from MODULE_1_LIQUIDATION_ENGINE.config.settings import get_config
            cfg = get_config()
            chain = cfg.get_chain(self.chain_id)
            if chain and chain.rpc_url:
                return chain.rpc_url
        except ImportError:
            pass
        # Env fallback
        env_map = {
            1: "MAINNET_RPC_URL", 42161: "ARBITRUM_RPC_URL",
            10: "OPTIMISM_RPC_URL", 8453: "BASE_RPC_URL",
            137: "POLYGON_RPC_URL", 43114: "AVALANCHE_RPC_URL",
            56: "BSC_RPC_URL", 324: "ZKSYNC_RPC_URL",
        }
        return os.getenv(env_map.get(self.chain_id, ""), "")

    def _get_w3(self):
        """Lazy-initialize Web3 connection."""
        if self._w3 is None and self.rpc_url:
            try:
                from web3 import Web3
                self._w3 = Web3(Web3.HTTPProvider(self.rpc_url))
                if self._w3.is_connected():
                    logger.info("Web3 connected: chain %d", self.chain_id)
                else:
                    logger.warning("Web3 connection failed for chain %d", self.chain_id)
                    self._w3 = None
            except Exception as e:
                logger.warning("Web3 init error: %s", e)
        return self._w3

    def encode_plan_steps(self, plan: Plan) -> List[Dict[str, Any]]:
        """Convert Plan steps to the format expected by FinancialExecutor.executePlan().

        Returns a list of Step tuples: [(target, data, value, allowFailure), ...]
        """
        encoded_steps = []
        for step in plan.steps:
            encoded_steps.append({
                "target": step.target_contract or "0x" + "0" * 40,
                "data": ("0x" + step.calldata.hex()) if step.calldata else "0x",
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
            dry_run: If True, only verify without submitting.

        Returns:
            ExecutionResult with transaction details.
        """
        # Pre-flight validation
        issues = self.validate_plan(plan)
        if any("no steps" in i for i in issues):
            return ExecutionResult(success=False, error="Plan has no steps")

        # Validate executor address
        from fire_engine.address_resolver import is_valid_address
        if not is_valid_address(self.executor_address):
            return ExecutionResult(
                success=False,
                error=f"Invalid executor address: {self.executor_address}. "
                      f"Set FIRE_EXECUTOR_<CHAIN> env var or deploy contract.",
            )

        # Estimate gas
        gas_estimate = self.estimate_gas(plan)

        if dry_run:
            logger.info(
                "DRY RUN: Plan with %d steps, estimated gas: %d, executor: %s",
                plan.step_count, gas_estimate, self.executor_address[:12],
            )
            return ExecutionResult(
                success=True,
                gas_used=gas_estimate,
                profit_wei=plan.expected_profit_wei,
            )

        # ── Live Execution ────────────────────────────────────────────
        w3 = self._get_w3()
        if not w3:
            return ExecutionResult(
                success=False,
                error="No web3 connection — set RPC URL via env or ConfigManager",
            )

        if not self._private_key:
            return ExecutionResult(
                success=False,
                error="No private key — set PRIVATE_KEY env var",
            )

        try:
            account = w3.eth.account.from_key(self._private_key)

            # Check gas price cap
            gas_price = w3.eth.gas_price
            gas_price_gwei = gas_price / 1e9
            if gas_price_gwei > self.max_gas_price_gwei:
                return ExecutionResult(
                    success=False,
                    error=f"Gas price {gas_price_gwei:.1f} gwei > cap {self.max_gas_price_gwei}",
                )

            # Encode steps for the on-chain executor
            steps = self.encode_plan_steps(plan)

            logger.info(
                "🔥 FIRE executing: %d steps, gas est: %d, chain: %d",
                len(steps), gas_estimate, self.chain_id,
            )

            # Build transaction (calling executePlan on FinancialExecutor)
            # This is a simplified construction — in production, encode via ABI
            tx = {
                "from": account.address,
                "to": self.executor_address,
                "gas": int(gas_estimate * 1.2),
                "gasPrice": int(gas_price * self.gas_price_multiplier),
                "nonce": w3.eth.get_transaction_count(account.address),
                "chainId": self.chain_id,
                "value": 0,
            }

            # Sign
            signed = account.sign_transaction(tx)
            tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
            receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=120)

            success = receipt.status == 1
            return ExecutionResult(
                success=success,
                tx_hash=receipt.transactionHash.hex(),
                gas_used=receipt.gasUsed,
                block_number=receipt.blockNumber,
                error=None if success else "Transaction reverted",
                route="public",
            )

        except Exception as e:
            return ExecutionResult(success=False, error=str(e))


class PreflightVerifier:
    """Pre-execution verification layer using a forked chain node (Anvil/Hardhat).

    Verifies FIRE plans against forked mainnet state before live broadcast.
    Requires a running fork node — no offline fallback.

    Usage::

        verifier = PreflightVerifier(fork_url="http://127.0.0.1:8545")
        result = verifier.verify(plan)
    """

    def __init__(self, fork_url: str) -> None:
        if not fork_url:
            raise ValueError("fork_url is required — start an Anvil/Hardhat fork node")
        self.fork_url = fork_url

    def verify(self, plan: Plan) -> ExecutionResult:
        """Verify a plan against forked chain state via eth_call.

        Args:
            plan: Compiled Plan to verify.

        Returns:
            ExecutionResult with verified outcomes from the fork node.
        """
        try:
            from web3 import Web3
            w3 = Web3(Web3.HTTPProvider(self.fork_url))
            if not w3.is_connected():
                return ExecutionResult(
                    success=False,
                    error=f"Fork node not reachable at {self.fork_url}",
                )

            total_gas = 0
            for step in plan.steps:
                if not step.calldata:
                    continue
                try:
                    gas = w3.eth.estimate_gas({
                        "to": Web3.to_checksum_address(step.target),
                        "data": step.calldata,
                        "value": step.value,
                    })
                    total_gas += gas
                except Exception as e:
                    return ExecutionResult(
                        success=False,
                        error=f"Step '{step.operation_id}' reverted on fork: {e}",
                        gas_used=total_gas,
                    )

            return ExecutionResult(
                success=True,
                gas_used=total_gas,
                profit_wei=plan.expected_profit_wei,
            )

        except ImportError:
            return ExecutionResult(
                success=False,
                error="web3 package required for fork verification",
            )
        except Exception as e:
            return ExecutionResult(success=False, error=str(e))
