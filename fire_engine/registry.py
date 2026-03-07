# =============================================================================
#  Operation Registry — Catalog of atomic and composite DeFi operations
#
#  Each operation is defined by its ID, parameters, effects, constraints,
#  gas cost model, and execution logic (contract address + ABI).
#  The registry is populated by scanning protocol ABIs and mapping public
#  functions to atomic operations based on the FIRE framework axioms.
#
#  Contract addresses are resolved DYNAMICALLY via AddressResolver:
#    - Never stores stale addresses
#    - Resolves at registration time per-chain from env/config/canonical
#    - Re-resolves at compile time for live execution
# =============================================================================

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class ParameterType(str, Enum):
    """Supported parameter types for operation inputs."""
    ADDRESS = "address"
    UINT256 = "uint256"
    INT256 = "int256"
    BYTES32 = "bytes32"
    BYTES = "bytes"
    BOOL = "bool"
    STRING = "string"


@dataclass
class OperationParameter:
    """Definition of an operation input parameter."""
    name: str
    param_type: ParameterType
    optional: bool = False
    description: str = ""


@dataclass
class OperationEffect:
    """State change produced by an operation (asset transfer, liability, etc.)."""
    effect_type: str        # e.g. "transfer", "add_liability", "remove_liability"
    asset: Optional[str] = None
    from_agent: Optional[str] = None
    to_agent: Optional[str] = None
    amount_param: Optional[str] = None  # parameter name holding the amount


@dataclass
class Operation:
    """An atomic or composite financial operation."""
    id: str
    name: str
    description: str = ""
    atomic: bool = True
    parameters: List[OperationParameter] = field(default_factory=list)
    effects: List[OperationEffect] = field(default_factory=list)
    constraints: List[str] = field(default_factory=list)
    gas_estimate: int = 0
    contract_address: Optional[str] = None
    function_signature: Optional[str] = None
    abi: Optional[Dict[str, Any]] = None
    network: str = "ethereum"
    protocol: str = ""
    # Dynamic address resolution key (e.g. "aave_v3_pool") — used by
    # AddressResolver to look up the real contract address per chain at runtime.
    # If set, contract_address is resolved dynamically and never stale.
    resolver_key: Optional[str] = None
    # For composite operations, stores the sub-operation IDs
    sub_operations: List[str] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    def encode(self, params: Dict[str, Any]) -> Optional[bytes]:
        """Encode calldata for this operation via web3 ABI encoding."""
        if not self.function_signature or not self.abi:
            return None
        try:
            from web3 import Web3
            w3 = Web3()
            contract = w3.eth.contract(
                address=Web3.to_checksum_address(self.contract_address or "0x" + "0" * 40),
                abi=[self.abi] if isinstance(self.abi, dict) else self.abi,
            )
            fn_name = self.function_signature.split("(")[0]
            fn = contract.functions[fn_name]
            ordered_args = [params[p.name] for p in self.parameters if p.name in params]
            return fn(*ordered_args).build_transaction({"gas": 0})["data"]
        except Exception as exc:
            logger.debug("ABI encode failed for %s: %s", self.id, exc)
            return None

    def to_dict(self) -> Dict[str, Any]:
        """Serialize operation to dictionary."""
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "atomic": self.atomic,
            "parameters": [
                {"name": p.name, "type": p.param_type.value, "optional": p.optional}
                for p in self.parameters
            ],
            "effects": [
                {
                    "effect_type": e.effect_type,
                    "asset": e.asset,
                    "from_agent": e.from_agent,
                    "to_agent": e.to_agent,
                }
                for e in self.effects
            ],
            "constraints": self.constraints,
            "gas_estimate": self.gas_estimate,
            "contract_address": self.contract_address,
            "function_signature": self.function_signature,
            "network": self.network,
            "protocol": self.protocol,
        }


class OperationRegistry:
    """In-memory registry of all known financial operations.

    Stores atomic operations (direct protocol calls) and composite
    operations (sequences of atomics defined as FIRE scripts).
    """

    def __init__(self) -> None:
        self._operations: Dict[str, Operation] = {}
        self._protocol_index: Dict[str, List[str]] = {}  # protocol -> [op_ids]
        self._network_index: Dict[str, List[str]] = {}   # network  -> [op_ids]

    # ── CRUD ────────────────────────────────────────────────────────────────

    def register(self, operation: Operation) -> None:
        """Register a new operation."""
        self._operations[operation.id] = operation
        # Update indices
        self._protocol_index.setdefault(operation.protocol, []).append(operation.id)
        self._network_index.setdefault(operation.network, []).append(operation.id)

    def get(self, operation_id: str) -> Optional[Operation]:
        """Retrieve an operation by ID."""
        return self._operations.get(operation_id)

    def remove(self, operation_id: str) -> bool:
        """Remove an operation by ID."""
        op = self._operations.pop(operation_id, None)
        if op is None:
            return False
        # Clean up indices
        if op.protocol in self._protocol_index:
            self._protocol_index[op.protocol] = [
                oid for oid in self._protocol_index[op.protocol] if oid != operation_id
            ]
        if op.network in self._network_index:
            self._network_index[op.network] = [
                oid for oid in self._network_index[op.network] if oid != operation_id
            ]
        return True

    def list_all(self) -> List[Operation]:
        """List all registered operations."""
        return list(self._operations.values())

    def list_by_protocol(self, protocol: str) -> List[Operation]:
        """List operations for a specific protocol."""
        ids = self._protocol_index.get(protocol, [])
        return [self._operations[oid] for oid in ids if oid in self._operations]

    def list_by_network(self, network: str) -> List[Operation]:
        """List operations for a specific network."""
        ids = self._network_index.get(network, [])
        return [self._operations[oid] for oid in ids if oid in self._operations]

    @property
    def count(self) -> int:
        return len(self._operations)

    # ── Bulk Registration Helpers ───────────────────────────────────────────

    def register_protocol_operations(self, protocol: str, network: str = "ethereum",
                                      chain_id: int = 1) -> int:
        """Register standard DeFi operations for a known protocol.

        Resolves contract addresses dynamically via AddressResolver.
        Returns the number of operations registered.
        """
        # Lazily import to avoid circular dependency
        from fire_engine.address_resolver import get_resolver, AddressNotFound

        resolver = get_resolver()
        count = 0
        definitions = _PROTOCOL_OPERATIONS.get(protocol, [])
        for op_def in definitions:
            # Dynamic address resolution
            resolver_key = op_def.get("resolver_key")
            contract_addr = None
            if resolver_key:
                try:
                    resolved = resolver.resolve(resolver_key, chain_id)
                    contract_addr = resolved.address
                    logger.debug(
                        "Resolved %s on chain %d → %s (source: %s)",
                        resolver_key, chain_id, contract_addr[:12], resolved.source,
                    )
                except AddressNotFound:
                    logger.debug(
                        "No address for %s on chain %d — operation registered without contract",
                        resolver_key, chain_id,
                    )

            op = Operation(
                id=f"{protocol}_{op_def['name']}",
                name=op_def["name"],
                description=op_def.get("description", ""),
                atomic=op_def.get("atomic", True),
                parameters=[
                    OperationParameter(
                        name=p["name"],
                        param_type=ParameterType(p["type"]),
                    )
                    for p in op_def.get("parameters", [])
                ],
                effects=[
                    OperationEffect(effect_type=e["effect_type"])
                    for e in op_def.get("effects", [])
                ],
                gas_estimate=op_def.get("gas_estimate", 0),
                contract_address=contract_addr,
                function_signature=op_def.get("function_signature"),
                resolver_key=resolver_key,
                network=network,
                protocol=protocol,
            )
            self.register(op)
            count += 1
        return count

    def validate(self, operation_id: str, params: Dict[str, Any]) -> List[str]:
        """Validate parameters for an operation. Returns list of error messages."""
        op = self.get(operation_id)
        if op is None:
            return [f"Unknown operation: {operation_id}"]
        errors = []
        for p in op.parameters:
            if not p.optional and p.name not in params:
                errors.append(f"Missing required parameter: {p.name}")
        return errors


# ── Built-in Protocol Operation Definitions ─────────────────────────────────

_PROTOCOL_OPERATIONS: Dict[str, List[Dict[str, Any]]] = {
    "aave_v3": [
        {
            "name": "flash_loan",
            "description": "Aave V3 flash loan",
            "function_signature": "flashLoanSimple(address,address,uint256,bytes,uint16)",
            "resolver_key": "aave_v3_pool",
            "parameters": [
                {"name": "receiver", "type": "address"},
                {"name": "asset", "type": "address"},
                {"name": "amount", "type": "uint256"},
            ],
            "effects": [{"effect_type": "add_liability"}],
            "gas_estimate": 250_000,
        },
        {
            "name": "supply",
            "description": "Supply asset to Aave V3",
            "function_signature": "supply(address,uint256,address,uint16)",
            "resolver_key": "aave_v3_pool",
            "parameters": [
                {"name": "asset", "type": "address"},
                {"name": "amount", "type": "uint256"},
                {"name": "on_behalf_of", "type": "address"},
            ],
            "effects": [{"effect_type": "transfer"}],
            "gas_estimate": 200_000,
        },
        {
            "name": "borrow",
            "description": "Borrow from Aave V3",
            "function_signature": "borrow(address,uint256,uint256,uint16,address)",
            "resolver_key": "aave_v3_pool",
            "parameters": [
                {"name": "asset", "type": "address"},
                {"name": "amount", "type": "uint256"},
                {"name": "interest_rate_mode", "type": "uint256"},
            ],
            "effects": [{"effect_type": "add_liability"}],
            "gas_estimate": 300_000,
        },
        {
            "name": "liquidation_call",
            "description": "Liquidate undercollateralized position",
            "function_signature": "liquidationCall(address,address,address,uint256,bool)",
            "resolver_key": "aave_v3_pool",
            "parameters": [
                {"name": "collateral_asset", "type": "address"},
                {"name": "debt_asset", "type": "address"},
                {"name": "user", "type": "address"},
                {"name": "debt_to_cover", "type": "uint256"},
            ],
            "effects": [
                {"effect_type": "remove_liability"},
                {"effect_type": "transfer"},
            ],
            "gas_estimate": 400_000,
        },
    ],
    "uniswap_v2": [
        {
            "name": "swap_exact_tokens_for_tokens",
            "description": "Swap exact amount of input tokens for output tokens",
            "function_signature": "swapExactTokensForTokens(uint256,uint256,address[],address,uint256)",
            "resolver_key": "uniswap_v2_router",
            "parameters": [
                {"name": "amount_in", "type": "uint256"},
                {"name": "amount_out_min", "type": "uint256"},
                {"name": "path", "type": "bytes"},
                {"name": "to", "type": "address"},
            ],
            "effects": [{"effect_type": "transfer"}],
            "gas_estimate": 150_000,
        },
        {
            "name": "swap_tokens_for_exact_tokens",
            "description": "Swap tokens for exact output amount",
            "function_signature": "swapTokensForExactTokens(uint256,uint256,address[],address,uint256)",
            "resolver_key": "uniswap_v2_router",
            "parameters": [
                {"name": "amount_out", "type": "uint256"},
                {"name": "amount_in_max", "type": "uint256"},
                {"name": "path", "type": "bytes"},
                {"name": "to", "type": "address"},
            ],
            "effects": [{"effect_type": "transfer"}],
            "gas_estimate": 150_000,
        },
    ],
    "compound_v2": [
        {
            "name": "supply",
            "description": "Supply asset to Compound V2 (mint cTokens)",
            "function_signature": "mint(uint256)",
            "resolver_key": "compound_v2_comptroller",
            "parameters": [
                {"name": "amount", "type": "uint256"},
            ],
            "effects": [{"effect_type": "transfer"}],
            "gas_estimate": 200_000,
        },
        {
            "name": "borrow",
            "description": "Borrow from Compound V2",
            "function_signature": "borrow(uint256)",
            "resolver_key": "compound_v2_comptroller",
            "parameters": [
                {"name": "amount", "type": "uint256"},
            ],
            "effects": [{"effect_type": "add_liability"}],
            "gas_estimate": 250_000,
        },
        {
            "name": "liquidate_borrow",
            "description": "Liquidate undercollateralized Compound V2 position",
            "function_signature": "liquidateBorrow(address,uint256,address)",
            "resolver_key": "compound_v2_comptroller",
            "parameters": [
                {"name": "borrower", "type": "address"},
                {"name": "repay_amount", "type": "uint256"},
                {"name": "collateral_ctoken", "type": "address"},
            ],
            "effects": [
                {"effect_type": "remove_liability"},
                {"effect_type": "transfer"},
            ],
            "gas_estimate": 350_000,
        },
    ],
    "uniswap_v3": [
        {
            "name": "exact_input_single",
            "description": "Swap exact input on Uniswap V3",
            "function_signature": "exactInputSingle((address,address,uint24,address,uint256,uint256,uint256,uint160))",
            "resolver_key": "uniswap_v3_router",
            "parameters": [
                {"name": "token_in", "type": "address"},
                {"name": "token_out", "type": "address"},
                {"name": "fee", "type": "uint256"},
                {"name": "amount_in", "type": "uint256"},
                {"name": "amount_out_min", "type": "uint256"},
            ],
            "effects": [{"effect_type": "transfer"}],
            "gas_estimate": 180_000,
        },
    ],
    "balancer_v2": [
        {
            "name": "flash_loan",
            "description": "Balancer V2 flash loan (0% fee)",
            "function_signature": "flashLoan(address,address[],uint256[],bytes)",
            "resolver_key": "balancer_v2_vault",
            "parameters": [
                {"name": "recipient", "type": "address"},
                {"name": "tokens", "type": "bytes"},
                {"name": "amounts", "type": "bytes"},
            ],
            "effects": [{"effect_type": "add_liability"}],
            "gas_estimate": 200_000,
        },
    ],
    "maker": [
        {
            "name": "flash_mint",
            "description": "MakerDAO DAI flash mint (0% fee)",
            "function_signature": "flashLoan(address,address,uint256,bytes)",
            "resolver_key": "maker_flash",
            "parameters": [
                {"name": "receiver", "type": "address"},
                {"name": "token", "type": "address"},
                {"name": "amount", "type": "uint256"},
            ],
            "effects": [{"effect_type": "add_liability"}],
            "gas_estimate": 200_000,
        },
    ],
}
