# =============================================================================
#  FIRE Engine — Financial Operations Runtime Environment
#
#  Core Python package providing:
#    - Operation Registry: catalog of atomic/composite DeFi operations
#    - FIRE Language Parser: parse FIRE scripts into ASTs
#    - Compiler: compile ASTs into executable Plans
#    - Off-chain Executor: submit plans to on-chain FinancialExecutor
#    - Simulator: verify plans against forked chain state
# =============================================================================

from fire_engine.registry import OperationRegistry, Operation, OperationParameter
from fire_engine.parser import FireParser, ASTNode
from fire_engine.compiler import Compiler, Plan, PlanStep

__all__ = [
    "OperationRegistry",
    "Operation",
    "OperationParameter",
    "FireParser",
    "ASTNode",
    "Compiler",
    "Plan",
    "PlanStep",
]
