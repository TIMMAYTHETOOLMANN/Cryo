# =============================================================================
#  FIRE Engine — Financial Operations Runtime Environment
#
#  Core Python package providing:
#    - Operation Registry: catalog of atomic/composite DeFi operations
#    - FIRE Language Parser: parse FIRE scripts into ASTs
#    - Compiler: compile ASTs into executable Plans
#    - Off-chain Executor: submit plans to on-chain FinancialExecutor
#    - Address Resolver: dynamic per-chain contract address resolution
#    - PreflightVerifier: verify plans against forked chain state
# =============================================================================

from fire_engine.registry import OperationRegistry, Operation, OperationParameter
from fire_engine.parser import FireParser, ASTNode
from fire_engine.compiler import Compiler, Plan, PlanStep
from fire_engine.executor import OffChainExecutor, PreflightVerifier, ExecutionResult
from fire_engine.address_resolver import (
    AddressResolver,
    get_resolver,
    is_valid_address,
    AddressNotFound,
    CANONICAL_PROTOCOLS,
    CANONICAL_TOKENS,
)

__all__ = [
    "OperationRegistry",
    "Operation",
    "OperationParameter",
    "FireParser",
    "ASTNode",
    "Compiler",
    "Plan",
    "PlanStep",
    "OffChainExecutor",
    "PreflightVerifier",
    "ExecutionResult",
    "AddressResolver",
    "get_resolver",
    "is_valid_address",
    "AddressNotFound",
    "CANONICAL_PROTOCOLS",
    "CANONICAL_TOKENS",
]
