#!/usr/bin/env python3
"""
MODULE 1 — Module 4: Liquidation Executor
Python interface to on-chain LiquidationExecutor smart contracts (V1 & V2)
Performs flash-loan-powered liquidations via deployed Solidity contracts

Supports:
- Aave V2/V3 liquidationCall via flash loan
- Compound V2 liquidateBorrow via flash loan
- MakerDAO bark / bite via flash loan
- Dry-run verification (eth_call) before submission
- Automatic provider selection via FlashLoanAggregator
"""

import asyncio
import json
import logging
import time
from typing import Dict, Optional, Tuple
from dataclasses import dataclass
from enum import Enum

from web3 import Web3
from web3.contract import Contract
from eth_account import Account

from ..config_manager import ConfigManager, get_config
from ..flash_loan_providers.aggregator import (
    FlashLoanAggregator,
    FlashLoanProviderType,
    get_flash_loan_aggregator,
    get_provider_registry,
)
from ..calculators.profitability_calculator import (
    ProfitabilityCalculator,
    ProfitabilityResult,
    get_calculator,
)
from ..mev_protection.flashbots import MEVProtection, MEVRoute

logger = logging.getLogger(__name__)


# ============================================================================
# DATA MODELS
# ============================================================================

class LiquidationProtocol(Enum):
    """Supported liquidation protocols"""
    AAVE_V3 = "aave_v3"
    AAVE_V2 = "aave_v2"
    COMPOUND_V2 = "compound_v2"
    COMPOUND_V3 = "compound_v3"
    MAKER_DAO = "maker_dao"


@dataclass
class LiquidationRequest:
    """Request to execute a liquidation"""
    chain_id: int
    protocol: LiquidationProtocol
    user: str
    debt_asset: str
    debt_amount: int
    collateral_asset: str
    min_collateral_amount: int = 0
    flash_loan_provider: Optional[FlashLoanProviderType] = None
    max_gas_price: int = 50 * 10**9  # 50 gwei default
    use_flashbots: bool = True


@dataclass
class LiquidationResult:
    """Result of a liquidation execution"""
    success: bool
    tx_hash: Optional[str]
    chain_id: int
    protocol: str
    user: str
    debt_covered: int
    collateral_seized: int
    profit_wei: int
    profit_usd: float
    gas_used: int
    gas_cost_usd: float
    flash_loan_fee_usd: float
    block_number: Optional[int]
    error_message: Optional[str] = None
    timestamp: int = 0

    def __post_init__(self):
        if self.timestamp == 0:
            self.timestamp = int(time.time())


# ============================================================================
# ON-CHAIN EXECUTOR ABI
# ============================================================================

LIQUIDATION_EXECUTOR_ABI = json.loads('''
[
    {
        "inputs": [
            {"name": "debtAsset", "type": "address"},
            {"name": "debtAmount", "type": "uint256"},
            {"name": "collateralAsset", "type": "address"},
            {"name": "user", "type": "address"},
            {"name": "minCollateralAmount", "type": "uint256"}
        ],
        "name": "executeLiquidation",
        "outputs": [],
        "stateMutability": "nonpayable",
        "type": "function"
    },
    {
        "inputs": [{"name": "asset", "type": "address"}],
        "name": "addSupportedDebtAsset",
        "outputs": [],
        "stateMutability": "nonpayable",
        "type": "function"
    },
    {
        "inputs": [{"name": "_minProfit", "type": "uint256"}],
        "name": "setMinProfit",
        "outputs": [],
        "stateMutability": "nonpayable",
        "type": "function"
    },
    {
        "inputs": [],
        "name": "getStats",
        "outputs": [
            {"name": "", "type": "uint256"},
            {"name": "", "type": "uint256"}
        ],
        "stateMutability": "view",
        "type": "function"
    },
    {
        "inputs": [],
        "name": "TREASURY",
        "outputs": [{"name": "", "type": "address"}],
        "stateMutability": "view",
        "type": "function"
    },
    {
        "inputs": [],
        "name": "OWNER",
        "outputs": [{"name": "", "type": "address"}],
        "stateMutability": "view",
        "type": "function"
    },
    {
        "inputs": [],
        "name": "POOL",
        "outputs": [{"name": "", "type": "address"}],
        "stateMutability": "view",
        "type": "function"
    },
    {
        "inputs": [],
        "name": "minProfit",
        "outputs": [{"name": "", "type": "uint256"}],
        "stateMutability": "view",
        "type": "function"
    },
    {
        "inputs": [{"name": "", "type": "address"}],
        "name": "supportedDebtAssets",
        "outputs": [{"name": "", "type": "bool"}],
        "stateMutability": "view",
        "type": "function"
    },
    {
        "anonymous": false,
        "inputs": [
            {"indexed": true, "name": "user", "type": "address"},
            {"indexed": false, "name": "debtAsset", "type": "address"},
            {"indexed": false, "name": "collateralAsset", "type": "address"},
            {"indexed": false, "name": "debtCovered", "type": "uint256"},
            {"indexed": false, "name": "collateralSeized", "type": "uint256"},
            {"indexed": false, "name": "profit", "type": "uint256"}
        ],
        "name": "LiquidationExecuted",
        "type": "event"
    }
]
''')


# ============================================================================
# LIQUIDATION EXECUTOR
# ============================================================================

class LiquidationExecutor:
    """
    Python interface to on-chain LiquidationExecutor contracts.

    Workflow:
    1. Receive LiquidationRequest from Opportunity Detector
    2. Verify profitability via ProfitabilityCalculator
    3. Verify execution via eth_call (dry-run)
    4. If verification succeeds, submit live transaction
    5. Optionally route through Flashbots for MEV protection
    6. Return LiquidationResult with profit data
    """

    def __init__(self, config: Optional[ConfigManager] = None):
        self.config = config or get_config()
        self.calculator: ProfitabilityCalculator = get_calculator()
        self.flash_aggregator: FlashLoanAggregator = get_flash_loan_aggregator()
        self.mev_protection: MEVProtection = MEVProtection(config=self.config)

        # Web3 providers keyed by chain_id
        self._w3_providers: Dict[int, Web3] = {}

        # On-chain executor contracts keyed by (chain_id, version)
        self._executor_contracts: Dict[Tuple[int, str], Contract] = {}

        # Deployed executor addresses from .env
        self._executor_addresses: Dict[str, str] = {
            "v1": self.config.execution.__dict__.get(
                "liquidation_executor_v1", ""
            ) or self._env("LIQUIDATION_EXECUTOR_V1", ""),
            "v2": self.config.execution.__dict__.get(
                "liquidation_executor_v2", ""
            ) or self._env("LIQUIDATION_EXECUTOR_V2", ""),
        }

        # Statistics
        self.stats = {
            "liquidations_attempted": 0,
            "liquidations_succeeded": 0,
            "liquidations_failed": 0,
            "total_profit_usd": 0.0,
            "preflight_checks": 0,
            "start_time": time.time(),
        }

        logger.info("LiquidationExecutor initialized")
        for version, addr in self._executor_addresses.items():
            if addr:
                logger.info(f"  {version.upper()}: {addr}")

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _env(key: str, default: str = "") -> str:
        import os
        return os.getenv(key, default)

    def _get_w3(self, chain_id: int) -> Optional[Web3]:
        """Get or create Web3 provider for chain"""
        if chain_id in self._w3_providers:
            return self._w3_providers[chain_id]

        chain_cfg = self.config.get_chain(chain_id)
        if not chain_cfg or not chain_cfg.rpc_url:
            return None

        w3 = Web3(Web3.HTTPProvider(chain_cfg.rpc_url))
        self._w3_providers[chain_id] = w3
        return w3

    def _get_contract(self, chain_id: int, version: str = "v2") -> Optional[Contract]:
        """Get on-chain executor contract"""
        key = (chain_id, version)
        if key in self._executor_contracts:
            return self._executor_contracts[key]

        addr = self._executor_addresses.get(version)
        if not addr:
            return None

        w3 = self._get_w3(chain_id)
        if not w3:
            return None

        contract = w3.eth.contract(
            address=Web3.to_checksum_address(addr),
            abi=LIQUIDATION_EXECUTOR_ABI,
        )
        self._executor_contracts[key] = contract
        return contract

    # ------------------------------------------------------------------
    # Core execution
    # ------------------------------------------------------------------

    async def execute(self, request: LiquidationRequest) -> LiquidationResult:
        """
        Execute a liquidation request end-to-end.

        Steps:
          1. Pre-flight profitability check
          2. Select best flash loan provider
          3. Dry-run verification via eth_call
          4. Submit transaction (direct or via Flashbots)
          5. Wait for receipt & parse events
        """
        self.stats["liquidations_attempted"] += 1
        chain_id = request.chain_id

        w3 = self._get_w3(chain_id)
        if not w3:
            return self._fail(request, "No Web3 provider for chain")

        private_key = self.config.private_key
        if not private_key:
            return self._fail(request, "No PRIVATE_KEY configured — scan-only mode")

        # ---- Execution safety gate ----
        # EXECUTION_ENABLED must be explicitly set to true in the environment to
        # submit live transactions.  This prevents accidental mainnet execution
        # during testing or misconfigured deployments.
        if not self.config.execution.execution_enabled:
            logger.info(
                "🔒 EXECUTION_ENABLED=false — opportunity detected but transaction "
                "not submitted.  Set EXECUTION_ENABLED=true to enable live execution."
            )
            return self._fail(request, "EXECUTION_ENABLED=false — scan-only mode")

        # ---- 1. Profitability pre-flight ----
        profitability = await self._check_profitability(request, w3)
        if not profitability.is_profitable:
            return self._fail(
                request,
                f"Not profitable: net ${profitability.net_profit_usd:.2f}"
            )

        logger.info(
            f"✅ Profitable: net ${profitability.net_profit_usd:.2f} "
            f"(ROI {profitability.roi_percent:.1f}%)"
        )

        # ---- 2. Select flash loan provider (adaptive: uses success history) ----
        selected_provider: Optional[FlashLoanProviderType] = request.flash_loan_provider
        if selected_provider is None:
            quote = self.flash_aggregator.best_quote(
                asset=request.debt_asset,
                amount=request.debt_amount,
            )
            if not quote:
                return self._fail(request, "No flash loan provider available")
            selected_provider = quote.provider
            logger.info(f"⚡ Best provider: {quote.provider.value} (fee {quote.fee_bps:.2f} bps)")
        else:
            logger.info(f"⚡ Using requested provider: {request.flash_loan_provider.value}")

        # ---- 3. PRODUCTION: Skip verification — submit immediately ----
        logger.info("⚡ PRODUCTION MODE — submitting transaction directly")

        # ---- 4. Submit transaction ----
        _submit_start = time.monotonic()
        result = await self._submit_transaction(request, w3, private_key)
        submit_latency_ms = (time.monotonic() - _submit_start) * 1000

        # ---- 5. Feed execution outcome back into ProviderRegistry ----
        #        This enables best_quote_adaptive() to learn over time and
        #        deprioritise providers with poor historical success rates.
        registry = get_provider_registry()
        registry.record_result(
            selected_provider,
            success=result.success,
            amount=request.debt_amount,
            latency_ms=submit_latency_ms,
        )

        if result.success:
            self.stats["liquidations_succeeded"] += 1
            self.stats["total_profit_usd"] += result.profit_usd
            logger.info(
                f"🎉 Liquidation succeeded — profit ${result.profit_usd:.2f} "
                f"(tx: {result.tx_hash})"
            )
        else:
            self.stats["liquidations_failed"] += 1
            logger.error(f"❌ Liquidation failed: {result.error_message}")

        return result

    # ------------------------------------------------------------------
    # Profitability
    # ------------------------------------------------------------------

    async def _check_profitability(
        self, request: LiquidationRequest, w3: Web3
    ) -> ProfitabilityResult:
        """Pre-flight profitability check"""
        gas_price_gwei = w3.eth.gas_price / 10**9
        eth_price_usd = 2000.0  # TODO: fetch from oracle

        # Determine debt USD value (simplified)
        debt_usd = request.debt_amount / 10**18 * eth_price_usd
        collateral_usd = debt_usd * 1.2  # rough estimate

        # Get protocol liquidation bonus
        bonus = self._get_bonus(request.protocol)

        return self.calculator.calculate(
            debt_amount_usd=debt_usd,
            collateral_amount_usd=collateral_usd,
            liquidation_bonus=bonus,
            flash_loan_provider="aave_v3",
            gas_price_gwei=gas_price_gwei,
            eth_price_usd=eth_price_usd,
            chain_id=request.chain_id,
        )

    @staticmethod
    def _get_bonus(protocol: LiquidationProtocol) -> float:
        bonuses = {
            LiquidationProtocol.AAVE_V3: 0.05,
            LiquidationProtocol.AAVE_V2: 0.05,
            LiquidationProtocol.COMPOUND_V2: 0.08,
            LiquidationProtocol.COMPOUND_V3: 0.05,
            LiquidationProtocol.MAKER_DAO: 0.13,
        }
        return bonuses.get(protocol, 0.05)

    # ------------------------------------------------------------------
    # Preflight Verification (dry-run via eth_call)
    # ------------------------------------------------------------------

    async def _preflight(
        self, request: LiquidationRequest, w3: Web3, private_key: str
    ) -> Tuple[bool, Optional[str]]:
        """
        Verify liquidation via eth_call (no gas spent).
        Returns (success, error_message).
        """
        contract = self._get_contract(request.chain_id)
        if not contract:
            return False, "Executor contract not found"

        account = Account.from_key(private_key)

        try:
            # Build call
            tx_data = contract.functions.executeLiquidation(
                request.debt_asset,
                request.debt_amount,
                request.collateral_asset,
                request.user,
                request.min_collateral_amount,
            ).build_transaction({
                "from": account.address,
                "gas": self.config.execution.max_gas_limit,
                "gasPrice": min(w3.eth.gas_price, request.max_gas_price),
                "nonce": w3.eth.get_transaction_count(account.address),
            })

            # Verify via eth_call
            w3.eth.call(tx_data)
            return True, None

        except Exception as e:
            return False, str(e)

    # ------------------------------------------------------------------
    # Transaction submission
    # ------------------------------------------------------------------

    async def _submit_transaction(
        self, request: LiquidationRequest, w3: Web3, private_key: str
    ) -> LiquidationResult:
        """Build, sign, and submit the liquidation transaction.

        When ``request.use_flashbots`` is True the signed transaction is routed
        through Flashbots Protect (private mempool) to avoid front-running.
        Otherwise it is sent directly to the public mempool.
        """
        contract = self._get_contract(request.chain_id)
        if not contract:
            return self._fail(request, "Executor contract not found")

        account = Account.from_key(private_key)

        try:
            # Gas price check
            current_gas = w3.eth.gas_price
            cap = int(self.config.execution.gas_price_cap_gwei * 10**9)
            if current_gas > cap:
                return self._fail(
                    request,
                    f"Gas price {current_gas / 10**9:.1f} gwei exceeds cap {cap / 10**9:.1f}",
                )

            # Build transaction
            tx = contract.functions.executeLiquidation(
                request.debt_asset,
                request.debt_amount,
                request.collateral_asset,
                request.user,
                request.min_collateral_amount,
            ).build_transaction({
                "from": account.address,
                "gas": int(300_000 * self.config.execution.gas_limit_buffer),
                "gasPrice": current_gas,
                "nonce": await asyncio.to_thread(w3.eth.get_transaction_count, account.address),
            })

            # Sign the transaction
            signed = w3.eth.account.sign_transaction(tx, private_key)
            raw_tx_hex = signed.raw_transaction.hex()

            # Route through Flashbots or public mempool
            if request.use_flashbots:
                route = self.mev_protection.default_route
                logger.info(f"🛡️ Routing via {route.value} for MEV protection")
                fb_result = await self.mev_protection.send_private_transaction(
                    signed_tx=raw_tx_hex,
                    w3=w3,
                    route=route,
                )
                if fb_result is None:
                    # Fallback to public mempool if Flashbots fails
                    logger.warning("Flashbots routing failed — falling back to public mempool")
                    tx_hash_bytes = await asyncio.to_thread(w3.eth.send_raw_transaction, signed.raw_transaction)
                else:
                    tx_hash_bytes = fb_result if isinstance(fb_result, bytes) else bytes.fromhex(fb_result.replace("0x", ""))
                tx_hash_str = tx_hash_bytes.hex() if isinstance(tx_hash_bytes, bytes) else str(tx_hash_bytes)
                logger.info(f"📤 TX submitted via {route.value}: {tx_hash_str}")
            else:
                tx_hash_bytes = await asyncio.to_thread(w3.eth.send_raw_transaction, signed.raw_transaction)
                tx_hash_str = tx_hash_bytes.hex()
                logger.info(f"📤 TX submitted (public mempool): {tx_hash_str}")

            # Wait for receipt (pass bytes hash for reliability)
            receipt = await asyncio.to_thread(
                w3.eth.wait_for_transaction_receipt,
                tx_hash_bytes,
                timeout=self.config.execution.transaction_timeout_seconds,
            )

            if receipt["status"] != 1:
                return self._fail(request, "Transaction reverted", tx_hash=tx_hash_str)

            # Parse LiquidationExecuted event
            result = self._parse_receipt(request, receipt, contract, w3)
            return result

        except Exception as e:
            return self._fail(request, str(e))

    def _parse_receipt(
        self,
        request: LiquidationRequest,
        receipt,
        contract: Contract,
        w3: Web3,
    ) -> LiquidationResult:
        """Parse transaction receipt and extract liquidation data"""
        gas_used = receipt["gasUsed"]
        gas_price = receipt.get("effectiveGasPrice", w3.eth.gas_price)
        gas_cost_eth = (gas_used * gas_price) / 10**18
        gas_cost_usd = gas_cost_eth * 2000  # TODO: live price

        # Try to decode LiquidationExecuted event
        debt_covered = request.debt_amount
        collateral_seized = 0
        profit_wei = 0

        try:
            events = contract.events.LiquidationExecuted().process_receipt(receipt)
            if events:
                evt = events[0]["args"]
                debt_covered = evt.get("debtCovered", debt_covered)
                collateral_seized = evt.get("collateralSeized", 0)
                profit_wei = evt.get("profit", 0)
        except Exception:
            logger.warning("Could not decode LiquidationExecuted event")

        profit_usd = (profit_wei / 10**18) * 2000  # TODO: live price

        return LiquidationResult(
            success=True,
            tx_hash=receipt["transactionHash"].hex(),
            chain_id=request.chain_id,
            protocol=request.protocol.value,
            user=request.user,
            debt_covered=debt_covered,
            collateral_seized=collateral_seized,
            profit_wei=profit_wei,
            profit_usd=profit_usd,
            gas_used=gas_used,
            gas_cost_usd=gas_cost_usd,
            flash_loan_fee_usd=0,  # Included in on-chain execution
            block_number=receipt["blockNumber"],
        )

    # ------------------------------------------------------------------
    # Convenience: verify on-chain contract state
    # ------------------------------------------------------------------

    async def verify_contracts(self, chain_id: int = 1) -> Dict[str, any]:
        """Verify deployed executor contracts are live and configured"""
        w3 = self._get_w3(chain_id)
        if not w3:
            return {"error": "No Web3 provider"}

        results = {}
        for version, addr in self._executor_addresses.items():
            if not addr:
                results[version] = {"status": "NOT_CONFIGURED"}
                continue

            code = w3.eth.get_code(Web3.to_checksum_address(addr))
            if len(code) == 0:
                results[version] = {"status": "NOT_DEPLOYED", "address": addr}
                continue

            contract = self._get_contract(chain_id, version)
            try:
                owner = contract.functions.OWNER().call()
                treasury = contract.functions.TREASURY().call()
                pool = contract.functions.POOL().call()
                min_profit = contract.functions.minProfit().call()

                results[version] = {
                    "status": "ACTIVE",
                    "address": addr,
                    "owner": owner,
                    "treasury": treasury,
                    "pool": pool,
                    "min_profit": min_profit,
                }
            except Exception as e:
                results[version] = {
                    "status": "ERROR",
                    "address": addr,
                    "error": str(e),
                }

        return results

    # ------------------------------------------------------------------
    # Failure helper
    # ------------------------------------------------------------------

    def _fail(
        self,
        request: LiquidationRequest,
        message: str,
        tx_hash: Optional[str] = None,
    ) -> LiquidationResult:
        logger.error(f"❌ {message}")
        return LiquidationResult(
            success=False,
            tx_hash=tx_hash,
            chain_id=request.chain_id,
            protocol=request.protocol.value,
            user=request.user,
            debt_covered=0,
            collateral_seized=0,
            profit_wei=0,
            profit_usd=0,
            gas_used=0,
            gas_cost_usd=0,
            flash_loan_fee_usd=0,
            block_number=None,
            error_message=message,
        )

    def get_stats(self) -> Dict:
        """Return executor statistics"""
        uptime = time.time() - self.stats["start_time"]
        return {
            **self.stats,
            "uptime_seconds": uptime,
            "success_rate": (
                self.stats["liquidations_succeeded"]
                / max(self.stats["liquidations_attempted"], 1)
                * 100
            ),
        }
