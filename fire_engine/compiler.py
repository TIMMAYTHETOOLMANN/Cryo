# =============================================================================
#  Compiler — Compile FIRE ASTs into executable Plans
#
#  Takes parsed AST nodes from the FireParser and resolves them against the
#  OperationRegistry to produce concrete execution Plans. Each Plan contains
#  a sequence of PlanSteps with resolved parameters, calldata, gas estimates,
#  and dependency information.
# =============================================================================

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Dict, List, Optional

from fire_engine.parser import ASTNode, NodeType
from fire_engine.registry import OperationRegistry, Operation


@dataclass
class PlanStep:
    """A single concrete step in an execution plan."""
    index: int
    operation_id: str
    operation_name: str
    resolved_params: Dict[str, Any] = field(default_factory=dict)
    target_contract: Optional[str] = None
    calldata: Optional[bytes] = None
    value: int = 0               # ETH value in wei
    gas_estimate: int = 0
    dependencies: List[int] = field(default_factory=list)
    parallel_group: Optional[str] = None
    allow_failure: bool = False
    # Address resolution provenance
    address_source: str = ""     # "resolver", "operation", "none"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "index": self.index,
            "operation_id": self.operation_id,
            "operation_name": self.operation_name,
            "resolved_params": self.resolved_params,
            "target_contract": self.target_contract,
            "calldata": self.calldata.hex() if self.calldata else None,
            "value": self.value,
            "gas_estimate": self.gas_estimate,
            "dependencies": self.dependencies,
            "parallel_group": self.parallel_group,
            "allow_failure": self.allow_failure,
            "address_source": self.address_source,
        }


@dataclass
class Plan:
    """Complete execution plan compiled from a FIRE script."""
    steps: List[PlanStep] = field(default_factory=list)
    total_gas_estimate: int = 0
    expected_profit_wei: int = 0
    risk_score: float = 0.0
    source_script: str = ""
    variables: Dict[str, Any] = field(default_factory=dict)

    @property
    def step_count(self) -> int:
        return len(self.steps)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "steps": [s.to_dict() for s in self.steps],
            "total_gas_estimate": self.total_gas_estimate,
            "expected_profit_wei": self.expected_profit_wei,
            "risk_score": self.risk_score,
            "step_count": self.step_count,
        }


class CompileError(Exception):
    """Raised when compilation fails."""
    pass


class Compiler:
    """Compiles FIRE ASTs into executable Plans.

    Dynamically resolves contract addresses at compile time via the
    AddressResolver. The context dict should include 'chain_id' to
    enable per-chain address resolution.

    Usage::

        registry = OperationRegistry()
        registry.register_protocol_operations("aave_v3", chain_id=1)

        compiler = Compiler(registry)
        plan = compiler.compile(ast, context={"chain_id": 1, "amountIn": 10})
    """

    def __init__(self, registry: OperationRegistry) -> None:
        self.registry = registry
        self._resolver = None

    def _get_resolver(self):
        """Lazy-load the address resolver."""
        if self._resolver is None:
            try:
                from fire_engine.address_resolver import get_resolver
                self._resolver = get_resolver()
            except ImportError:
                pass
        return self._resolver

    def compile(self, ast: ASTNode, context: Optional[Dict[str, Any]] = None) -> Plan:
        """Compile an AST into an execution Plan.

        Args:
            ast: Root ASTNode from FireParser.
            context: Variable bindings and runtime context.

        Returns:
            Compiled Plan with resolved steps.

        Raises:
            CompileError: If compilation fails.
        """
        ctx = dict(context) if context else {}
        plan = Plan()
        plan.variables = ctx

        self._traverse(ast, ctx, plan)

        # Compute total gas
        plan.total_gas_estimate = sum(s.gas_estimate for s in plan.steps)

        # Compute dependencies from asset flows
        self._compute_dependencies(plan)

        return plan

    def _traverse(self, node: ASTNode, ctx: Dict[str, Any], plan: Plan) -> None:
        """Recursively traverse AST nodes and emit plan steps."""
        if node.node_type == NodeType.ATOMIC:
            self._emit_atomic(node, ctx, plan)
        elif node.node_type == NodeType.SEQUENCE:
            for child in node.children:
                self._traverse(child, ctx, plan)
        elif node.node_type == NodeType.PARALLEL:
            group_id = f"parallel_{len(plan.steps)}"
            for child in node.children:
                start_idx = len(plan.steps)
                self._traverse(child, ctx, plan)
                # Mark all new steps as belonging to this parallel group
                for i in range(start_idx, len(plan.steps)):
                    plan.steps[i].parallel_group = group_id
        elif node.node_type == NodeType.CONDITIONAL:
            self._handle_conditional(node, ctx, plan)
        elif node.node_type == NodeType.REPEAT:
            if node.count > 1000:
                raise CompileError(f"Repeat count too large: {node.count}")
            for _ in range(node.count):
                for child in node.children:
                    self._traverse(child, ctx, plan)
        elif node.node_type == NodeType.COLLECTION:
            self._handle_collection(node, ctx, plan)
        elif node.node_type == NodeType.ASSIGNMENT:
            # Evaluate RHS and bind to variable
            if node.children:
                child = node.children[0]
                if child.node_type == NodeType.ATOMIC:
                    # Store the operation reference
                    ctx[node.identifier] = child
                    self._emit_atomic(child, ctx, plan)
                elif child.node_type == NodeType.LITERAL:
                    ctx[node.identifier] = child.value
                elif child.node_type == NodeType.VARIABLE:
                    ctx[node.identifier] = ctx.get(child.value, child.value)
                else:
                    self._traverse(child, ctx, plan)
        elif node.node_type == NodeType.VARIABLE:
            # Resolve variable — if it maps to an AST node, traverse it
            val = ctx.get(node.value)
            if isinstance(val, ASTNode):
                self._traverse(val, ctx, plan)
            # Otherwise it's a terminal value, nothing to emit
        elif node.node_type == NodeType.LITERAL:
            pass  # Literals are values, not actions

    def _emit_atomic(self, node: ASTNode, ctx: Dict[str, Any], plan: Plan) -> None:
        """Emit a plan step for an atomic operation.

        Resolves contract addresses dynamically:
          1. If operation has a resolver_key, resolve via AddressResolver
          2. Fall back to operation.contract_address (set at registration)
          3. Leave None if unresolvable (validation will catch it)
        """
        op_name = node.value
        if op_name is None:
            raise CompileError("Atomic operation has no name")

        # Resolve parameters from context
        resolved = {}
        for key, val in node.params.items():
            if isinstance(val, str) and val in ctx:
                resolved[key] = ctx[val]
            else:
                resolved[key] = val

        # Look up operation in registry (try exact match, then protocol-prefixed)
        op = self.registry.get(op_name)
        if op is None:
            for prefix in ("aave_v3_", "uniswap_v2_", "uniswap_v3_",
                           "compound_v2_", "balancer_v2_", "maker_"):
                op = self.registry.get(f"{prefix}{op_name}")
                if op:
                    break

        gas = op.gas_estimate if op else 100_000
        op_id = op.id if op else op_name

        # Dynamic address resolution
        contract = None
        address_source = "none"
        chain_id = ctx.get("chain_id", 1)

        if op and op.resolver_key:
            resolver = self._get_resolver()
            if resolver:
                try:
                    resolved_addr = resolver.resolve(op.resolver_key, chain_id)
                    contract = resolved_addr.address
                    address_source = f"resolver:{resolved_addr.source}"
                except Exception:
                    # Fall back to operation's stored address
                    contract = op.contract_address
                    address_source = "operation" if contract else "none"
            else:
                contract = op.contract_address
                address_source = "operation" if contract else "none"
        elif op:
            contract = op.contract_address
            address_source = "operation" if contract else "none"

        step = PlanStep(
            index=len(plan.steps),
            operation_id=op_id,
            operation_name=op_name,
            resolved_params=resolved,
            target_contract=contract,
            gas_estimate=gas,
            address_source=address_source,
        )
        plan.steps.append(step)

    def _handle_conditional(self, node: ASTNode, ctx: Dict[str, Any], plan: Plan) -> None:
        """Handle conditional composition — evaluate at compile time if possible."""
        if node.condition is None:
            raise CompileError("Conditional has no condition")

        # Try to evaluate condition at compile time
        cond_result = self._eval_condition(node.condition, ctx)

        if cond_result is not None:
            branch = node.if_branch if cond_result else node.else_branch
            if branch:
                self._traverse(branch, ctx, plan)
        else:
            # Cannot evaluate at compile time — emit both branches with runtime check
            # For now, default to if_branch
            if node.if_branch:
                self._traverse(node.if_branch, ctx, plan)

    def _handle_collection(self, node: ASTNode, ctx: Dict[str, Any], plan: Plan) -> None:
        """Handle Σ (collection) composition — iterate over collection."""
        coll = ctx.get(node.collection_name)
        if coll is None:
            raise CompileError(f"Unknown collection: {node.collection_name}")
        if not hasattr(coll, "__iter__"):
            raise CompileError(f"Not iterable: {node.collection_name}")
        for item in coll:
            new_ctx = dict(ctx)
            new_ctx[node.var_name] = item
            for child in node.children:
                self._traverse(child, new_ctx, plan)

    def _eval_condition(self, node: ASTNode, ctx: Dict[str, Any]) -> Optional[bool]:
        """Try to evaluate a condition at compile time."""
        if node.node_type != NodeType.COMPARISON:
            return None
        if len(node.children) != 2:
            return None

        left_val = self._resolve_value(node.children[0], ctx)
        right_val = self._resolve_value(node.children[1], ctx)

        if left_val is None or right_val is None:
            return None

        try:
            left_num = Decimal(str(left_val))
            right_num = Decimal(str(right_val))
        except Exception:
            # String comparison
            if node.operator == "==":
                return left_val == right_val
            elif node.operator == "!=":
                return left_val != right_val
            return None

        if node.operator == "==":
            return left_num == right_num
        elif node.operator == "!=":
            return left_num != right_num
        elif node.operator == "<":
            return left_num < right_num
        elif node.operator == ">":
            return left_num > right_num
        elif node.operator == "<=":
            return left_num <= right_num
        elif node.operator == ">=":
            return left_num >= right_num
        return None

    def _resolve_value(self, node: ASTNode, ctx: Dict[str, Any]) -> Any:
        """Resolve an AST node to a concrete value."""
        if node.node_type == NodeType.LITERAL:
            return node.value
        elif node.node_type == NodeType.VARIABLE:
            return ctx.get(node.value)
        return None

    def _compute_dependencies(self, plan: Plan) -> None:
        """Compute step dependencies based on sequential ordering.

        Steps in the same parallel group have no mutual dependencies.
        Sequential steps depend on their predecessor.
        """
        for i, step in enumerate(plan.steps):
            if i == 0:
                continue
            prev = plan.steps[i - 1]
            # If same parallel group, no dependency
            if step.parallel_group and step.parallel_group == prev.parallel_group:
                continue
            step.dependencies.append(i - 1)
