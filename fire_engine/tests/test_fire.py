# =============================================================================
#  Tests for the FIRE Engine — Parser, Registry, Compiler, Executor
# =============================================================================

import pytest
from fire_engine.registry import (
    OperationRegistry,
    Operation,
    OperationParameter,
    OperationEffect,
    ParameterType,
)
from fire_engine.parser import FireParser, ASTNode, NodeType, FireParseError
from fire_engine.compiler import Compiler, Plan, PlanStep, CompileError
from fire_engine.executor import OffChainExecutor, PreflightVerifier, ExecutionResult


# ═══════════════════════════════════════════════════════════════════════════════
#  Registry Tests
# ═══════════════════════════════════════════════════════════════════════════════


class TestOperationRegistry:
    def test_register_and_get(self):
        reg = OperationRegistry()
        op = Operation(id="test_op", name="TestOp", protocol="test")
        reg.register(op)
        assert reg.get("test_op") is op

    def test_get_unknown_returns_none(self):
        reg = OperationRegistry()
        assert reg.get("nonexistent") is None

    def test_remove_operation(self):
        reg = OperationRegistry()
        op = Operation(id="test_op", name="TestOp", protocol="test")
        reg.register(op)
        assert reg.remove("test_op") is True
        assert reg.get("test_op") is None
        assert reg.count == 0

    def test_remove_nonexistent_returns_false(self):
        reg = OperationRegistry()
        assert reg.remove("nonexistent") is False

    def test_list_all(self):
        reg = OperationRegistry()
        op1 = Operation(id="op1", name="Op1", protocol="proto1")
        op2 = Operation(id="op2", name="Op2", protocol="proto2")
        reg.register(op1)
        reg.register(op2)
        assert len(reg.list_all()) == 2

    def test_list_by_protocol(self):
        reg = OperationRegistry()
        op1 = Operation(id="op1", name="Op1", protocol="aave")
        op2 = Operation(id="op2", name="Op2", protocol="uniswap")
        op3 = Operation(id="op3", name="Op3", protocol="aave")
        reg.register(op1)
        reg.register(op2)
        reg.register(op3)
        aave_ops = reg.list_by_protocol("aave")
        assert len(aave_ops) == 2
        assert all(op.protocol == "aave" for op in aave_ops)

    def test_list_by_network(self):
        reg = OperationRegistry()
        op1 = Operation(id="op1", name="Op1", network="ethereum", protocol="test")
        op2 = Operation(id="op2", name="Op2", network="arbitrum", protocol="test")
        reg.register(op1)
        reg.register(op2)
        eth_ops = reg.list_by_network("ethereum")
        assert len(eth_ops) == 1
        assert eth_ops[0].network == "ethereum"

    def test_register_protocol_operations(self):
        reg = OperationRegistry()
        count = reg.register_protocol_operations("aave_v3")
        assert count > 0
        assert reg.count == count
        # Verify specific operations exist
        assert reg.get("aave_v3_flash_loan") is not None
        assert reg.get("aave_v3_liquidation_call") is not None

    def test_register_unknown_protocol(self):
        reg = OperationRegistry()
        count = reg.register_protocol_operations("unknown_protocol")
        assert count == 0

    def test_validate_params_missing_required(self):
        reg = OperationRegistry()
        op = Operation(
            id="test_op",
            name="TestOp",
            protocol="test",
            parameters=[
                OperationParameter(name="amount", param_type=ParameterType.UINT256),
                OperationParameter(name="to", param_type=ParameterType.ADDRESS),
            ],
        )
        reg.register(op)
        errors = reg.validate("test_op", {"amount": 100})
        assert len(errors) == 1
        assert "to" in errors[0]

    def test_validate_params_all_present(self):
        reg = OperationRegistry()
        op = Operation(
            id="test_op",
            name="TestOp",
            protocol="test",
            parameters=[
                OperationParameter(name="amount", param_type=ParameterType.UINT256),
            ],
        )
        reg.register(op)
        errors = reg.validate("test_op", {"amount": 100})
        assert len(errors) == 0

    def test_validate_unknown_operation(self):
        reg = OperationRegistry()
        errors = reg.validate("nonexistent", {})
        assert len(errors) == 1

    def test_operation_to_dict(self):
        op = Operation(
            id="test_op",
            name="TestOp",
            protocol="test",
            parameters=[
                OperationParameter(name="amount", param_type=ParameterType.UINT256),
            ],
            effects=[
                OperationEffect(effect_type="transfer"),
            ],
        )
        d = op.to_dict()
        assert d["id"] == "test_op"
        assert d["name"] == "TestOp"
        assert len(d["parameters"]) == 1
        assert len(d["effects"]) == 1

    def test_count_property(self):
        reg = OperationRegistry()
        assert reg.count == 0
        reg.register(Operation(id="op1", name="Op1", protocol="test"))
        assert reg.count == 1

    def test_multiple_protocol_registrations(self):
        reg = OperationRegistry()
        reg.register_protocol_operations("aave_v3")
        reg.register_protocol_operations("uniswap_v2")
        reg.register_protocol_operations("compound_v2")
        assert reg.count > 5  # All three protocols should have operations


# ═══════════════════════════════════════════════════════════════════════════════
#  Parser Tests
# ═══════════════════════════════════════════════════════════════════════════════


class TestFireParser:
    def setup_method(self):
        self.parser = FireParser()

    def test_parse_single_atomic(self):
        ast = self.parser.parse("Transfer(from, to, amount)")
        assert ast.node_type == NodeType.ATOMIC
        assert ast.value == "Transfer"
        assert len(ast.params) == 3

    def test_parse_atomic_no_args(self):
        ast = self.parser.parse("GetBalance()")
        assert ast.node_type == NodeType.ATOMIC
        assert ast.value == "GetBalance"
        assert len(ast.params) == 0

    def test_parse_sequence(self):
        ast = self.parser.parse("StepA(x) + StepB(y)")
        assert ast.node_type == NodeType.SEQUENCE
        assert len(ast.children) == 2
        assert ast.children[0].value == "StepA"
        assert ast.children[1].value == "StepB"

    def test_parse_multi_sequence(self):
        ast = self.parser.parse("A(x) + B(y) + C(z)")
        assert ast.node_type == NodeType.SEQUENCE
        assert len(ast.children) == 3

    def test_parse_parallel(self):
        ast = self.parser.parse("StepA(x) || StepB(y)")
        assert ast.node_type == NodeType.PARALLEL
        assert len(ast.children) == 2

    def test_parse_repeat(self):
        ast = self.parser.parse("Transfer(x) * 5")
        assert ast.node_type == NodeType.REPEAT
        assert ast.count == 5
        assert ast.children[0].value == "Transfer"

    def test_parse_conditional(self):
        ast = self.parser.parse("if price > 2000 then Buy(x) else Sell(y)")
        assert ast.node_type == NodeType.CONDITIONAL
        assert ast.condition.operator == ">"
        assert ast.if_branch.value == "Buy"
        assert ast.else_branch.value == "Sell"

    def test_parse_let_binding(self):
        ast = self.parser.parse("let amount = 100")
        # Single let produces an assignment
        assert ast.node_type == NodeType.ASSIGNMENT
        assert ast.identifier == "amount"

    def test_parse_grouped_expression(self):
        ast = self.parser.parse("(A(x) + B(y)) || C(z)")
        assert ast.node_type == NodeType.PARALLEL
        assert ast.children[0].node_type == NodeType.SEQUENCE

    def test_parse_comments_ignored(self):
        ast = self.parser.parse("""
            // This is a comment
            Transfer(from, to)
        """)
        assert ast.node_type == NodeType.ATOMIC
        assert ast.value == "Transfer"

    def test_parse_complex_script(self):
        script = """
            let flashLoan = FlashLoan(amount, WETH, Aave)
            let swap1 = SwapExactTokensForTokens(amountIn, WETH, USDC, Uniswap)
            let swap2 = SwapExactTokensForTokens(usdcOut, USDC, WETH, Sushiswap)
            let repay = RepayFlashLoan(amount, WETH, Aave)
            flashLoan + swap1 + swap2 + repay
        """
        ast = self.parser.parse(script)
        assert ast.node_type == NodeType.SEQUENCE
        # 4 let assignments + 1 composed expression
        assert len(ast.children) == 5

    def test_parse_empty_raises(self):
        with pytest.raises(FireParseError):
            self.parser.parse("")

    def test_parse_whitespace_only_raises(self):
        with pytest.raises(FireParseError):
            self.parser.parse("   \n\t  ")

    def test_parse_variable_reference(self):
        ast = self.parser.parse("let x = Transfer(a, b)\nx")
        assert ast.node_type == NodeType.SEQUENCE
        # First child is assignment, second is variable reference
        assert ast.children[1].node_type == NodeType.VARIABLE
        assert ast.children[1].value == "x"

    def test_parse_hex_literal(self):
        ast = self.parser.parse("Transfer(0xDEAD, 0xBEEF)")
        assert ast.node_type == NodeType.ATOMIC
        assert ast.params["arg0"] == "0xDEAD"
        assert ast.params["arg1"] == "0xBEEF"

    def test_parse_numeric_args(self):
        ast = self.parser.parse("Transfer(100, 200)")
        assert ast.node_type == NodeType.ATOMIC
        assert ast.params["arg0"] == "100"
        assert ast.params["arg1"] == "200"

    def test_parse_tilde_reverse(self):
        ast = self.parser.parse("~Transfer(a, b)")
        assert ast.node_type == NodeType.ATOMIC
        assert "reverse_Transfer" == ast.value

    def test_parse_arrow_as_sequence(self):
        ast = self.parser.parse("A(x) -> B(y)")
        assert ast.node_type == NodeType.SEQUENCE
        assert len(ast.children) == 2

    def test_parse_collection(self):
        ast = self.parser.parse("Σ(item in items : Process(item))")
        assert ast.node_type == NodeType.COLLECTION
        assert ast.var_name == "item"
        assert ast.collection_name == "items"

    def test_ast_repr_atomic(self):
        ast = self.parser.parse("Swap(WETH, USDC)")
        r = repr(ast)
        assert "Swap" in r

    def test_ast_repr_sequence(self):
        ast = self.parser.parse("A(x) + B(y)")
        r = repr(ast)
        assert "+" in r


# ═══════════════════════════════════════════════════════════════════════════════
#  Compiler Tests
# ═══════════════════════════════════════════════════════════════════════════════


class TestCompiler:
    def setup_method(self):
        self.registry = OperationRegistry()
        self.registry.register_protocol_operations("aave_v3")
        self.registry.register_protocol_operations("uniswap_v2")
        self.compiler = Compiler(self.registry)
        self.parser = FireParser()

    def test_compile_single_step(self):
        ast = self.parser.parse("Transfer(from, to)")
        plan = self.compiler.compile(ast)
        assert plan.step_count == 1
        assert plan.steps[0].operation_name == "Transfer"

    def test_compile_sequence(self):
        ast = self.parser.parse("A(x) + B(y) + C(z)")
        plan = self.compiler.compile(ast)
        assert plan.step_count == 3
        assert plan.steps[0].operation_name == "A"
        assert plan.steps[1].operation_name == "B"
        assert plan.steps[2].operation_name == "C"

    def test_compile_parallel_groups(self):
        ast = self.parser.parse("A(x) || B(y)")
        plan = self.compiler.compile(ast)
        assert plan.step_count == 2
        assert plan.steps[0].parallel_group is not None
        assert plan.steps[0].parallel_group == plan.steps[1].parallel_group

    def test_compile_repeat(self):
        ast = self.parser.parse("Transfer(x) * 3")
        plan = self.compiler.compile(ast)
        assert plan.step_count == 3

    def test_compile_conditional_true(self):
        ast = self.parser.parse("if 100 > 50 then A(x) else B(y)")
        plan = self.compiler.compile(ast, context={"100": 100, "50": 50})
        assert plan.step_count == 1
        assert plan.steps[0].operation_name == "A"

    def test_compile_conditional_false(self):
        ast = self.parser.parse("if 10 > 50 then A(x) else B(y)")
        plan = self.compiler.compile(ast, context={"10": 10, "50": 50})
        assert plan.step_count == 1
        assert plan.steps[0].operation_name == "B"

    def test_compile_with_context(self):
        ast = self.parser.parse("Transfer(myAmount, myAddr)")
        plan = self.compiler.compile(ast, context={"myAmount": 1000, "myAddr": "0xDEAD"})
        assert plan.steps[0].resolved_params["arg0"] == 1000
        assert plan.steps[0].resolved_params["arg1"] == "0xDEAD"

    def test_compile_gas_estimation(self):
        ast = self.parser.parse("A(x) + B(y)")
        plan = self.compiler.compile(ast)
        assert plan.total_gas_estimate >= 0

    def test_compile_dependencies_sequential(self):
        ast = self.parser.parse("A(x) + B(y) + C(z)")
        plan = self.compiler.compile(ast)
        # Each sequential step depends on the previous
        assert len(plan.steps[0].dependencies) == 0
        assert plan.steps[1].dependencies == [0]
        assert plan.steps[2].dependencies == [1]

    def test_compile_dependencies_parallel(self):
        ast = self.parser.parse("A(x) || B(y)")
        plan = self.compiler.compile(ast)
        # Parallel steps should have no mutual dependencies
        assert len(plan.steps[0].dependencies) == 0
        assert len(plan.steps[1].dependencies) == 0

    def test_compile_let_bindings(self):
        script = """
            let step1 = FlashLoan(100, WETH)
            let step2 = Swap(WETH, USDC)
            step1 + step2
        """
        ast = self.parser.parse(script)
        plan = self.compiler.compile(ast)
        # Let bindings + final composed expression
        assert plan.step_count >= 2

    def test_compile_collection(self):
        ast = self.parser.parse("Σ(token in tokens : Transfer(token))")
        plan = self.compiler.compile(
            ast, context={"tokens": ["WETH", "USDC", "DAI"]}
        )
        assert plan.step_count == 3

    def test_compile_repeat_limit(self):
        ast = self.parser.parse("A(x) * 1001")
        with pytest.raises(CompileError, match="Repeat count too large"):
            self.compiler.compile(ast)

    def test_plan_to_dict(self):
        ast = self.parser.parse("A(x) + B(y)")
        plan = self.compiler.compile(ast)
        d = plan.to_dict()
        assert "steps" in d
        assert "total_gas_estimate" in d
        assert len(d["steps"]) == 2

    def test_plan_step_to_dict(self):
        ast = self.parser.parse("Transfer(from, to)")
        plan = self.compiler.compile(ast)
        d = plan.steps[0].to_dict()
        assert "operation_name" in d
        assert "gas_estimate" in d

    def test_compile_known_registry_operation(self):
        """Test that operations matching the registry get proper gas estimates."""
        ast = self.parser.parse("flash_loan(receiver, asset, amount)")
        plan = self.compiler.compile(ast)
        assert plan.step_count == 1
        # Should match aave_v3_flash_loan from registry
        assert plan.steps[0].gas_estimate == 250_000

    def test_compile_complex_arbitrage(self):
        script = """
            let flashLoan = FlashLoan(amount, WETH, Aave)
            let swap1 = SwapExactTokensForTokens(amountIn, WETH, USDC, Uniswap)
            let swap2 = SwapExactTokensForTokens(usdcOut, USDC, WETH, Sushiswap)
            let repay = RepayFlashLoan(amount, fee, WETH)
            flashLoan + swap1 + swap2 + repay
        """
        ast = self.parser.parse(script)
        plan = self.compiler.compile(ast)
        assert plan.step_count >= 4
        assert plan.total_gas_estimate > 0


# ═══════════════════════════════════════════════════════════════════════════════
#  Executor Tests
# ═══════════════════════════════════════════════════════════════════════════════


class TestOffChainExecutor:
    def test_encode_plan_steps(self):
        executor = OffChainExecutor(executor_address="0x" + "1" * 40)
        plan = Plan(steps=[
            PlanStep(
                index=0,
                operation_id="test",
                operation_name="Test",
                target_contract="0x" + "2" * 40,
                calldata=b"\x01\x02\x03",
                value=0,
            ),
        ])
        encoded = executor.encode_plan_steps(plan)
        assert len(encoded) == 1
        assert encoded[0]["target"] == "0x" + "2" * 40
        assert encoded[0]["data"] == "0x010203"

    def test_estimate_gas(self):
        executor = OffChainExecutor(executor_address="0x" + "1" * 40)
        plan = Plan(
            steps=[
                PlanStep(index=0, operation_id="a", operation_name="A", gas_estimate=100_000),
                PlanStep(index=1, operation_id="b", operation_name="B", gas_estimate=200_000),
            ],
            total_gas_estimate=300_000,
        )
        gas = executor.estimate_gas(plan)
        assert gas > 300_000  # includes base + overhead

    def test_validate_plan_empty(self):
        executor = OffChainExecutor(executor_address="0x" + "1" * 40)
        plan = Plan()
        issues = executor.validate_plan(plan)
        assert any("no steps" in i for i in issues)

    def test_validate_plan_no_target(self):
        executor = OffChainExecutor(executor_address="0x" + "1" * 40)
        plan = Plan(steps=[
            PlanStep(index=0, operation_id="test", operation_name="Test"),
        ])
        issues = executor.validate_plan(plan)
        assert any("no target" in i for i in issues)

    def test_dry_run_execution(self):
        executor = OffChainExecutor(executor_address="0x" + "1" * 40)
        plan = Plan(
            steps=[
                PlanStep(
                    index=0,
                    operation_id="test",
                    operation_name="Test",
                    target_contract="0x" + "2" * 40,
                    gas_estimate=100_000,
                ),
            ],
            total_gas_estimate=100_000,
        )
        result = executor.execute(plan, dry_run=True)
        assert result.success is True
        assert result.gas_used > 0

    def test_execute_empty_plan(self):
        executor = OffChainExecutor(executor_address="0x" + "1" * 40)
        plan = Plan()
        result = executor.execute(plan, dry_run=True)
        assert result.success is False

    def test_execution_result_to_dict(self):
        result = ExecutionResult(success=True, tx_hash="0xabc", gas_used=100_000)
        d = result.to_dict()
        assert d["success"] is True
        assert d["tx_hash"] == "0xabc"


class TestPreflightVerifier:
    def test_requires_fork_url(self):
        with pytest.raises(ValueError, match="fork_url is required"):
            PreflightVerifier(fork_url="")

    def test_verify_with_unreachable_node(self):
        verifier = PreflightVerifier(fork_url="http://localhost:19999")
        plan = Plan(
            steps=[
                PlanStep(index=0, operation_id="test", operation_name="Test", gas_estimate=100_000),
            ],
            total_gas_estimate=100_000,
        )
        result = verifier.verify(plan)
        assert result.success is False

    def test_verify_with_fork_url(self):
        verifier = PreflightVerifier(fork_url="http://localhost:8545")
        plan = Plan(
            steps=[
                PlanStep(index=0, operation_id="test", operation_name="Test", gas_estimate=100_000),
            ],
        )
        result = verifier.verify(plan)
        # Will fail if no local node is running — expected behavior
        assert isinstance(result, ExecutionResult)


# ═══════════════════════════════════════════════════════════════════════════════
#  Integration Tests
# ═══════════════════════════════════════════════════════════════════════════════


class TestFireIntegration:
    """End-to-end tests: parse → compile → verify."""

    def test_simple_arbitrage_flow(self):
        # Set up registry
        registry = OperationRegistry()
        registry.register_protocol_operations("aave_v3")
        registry.register_protocol_operations("uniswap_v2")

        # Parse script
        parser = FireParser()
        script = """
            let flashLoan = FlashLoan(amount, WETH, Aave)
            let swap1 = Swap(WETH, USDC, Uniswap)
            let swap2 = Swap(USDC, WETH, Sushiswap)
            let repay = Repay(amount, fee)
            flashLoan + swap1 + swap2 + repay
        """
        ast = parser.parse(script)

        # Compile
        compiler = Compiler(registry)
        plan = compiler.compile(ast, context={"amount": 10, "fee": 0.05})
        assert plan.step_count >= 4

        # Verify — requires a live fork node
        verifier = PreflightVerifier(fork_url="http://localhost:8545")
        result = verifier.verify(plan)
        assert isinstance(result, ExecutionResult)

    def test_conditional_strategy(self):
        registry = OperationRegistry()
        registry.register_protocol_operations("aave_v3")

        parser = FireParser()
        script = "if 2500 > 2000 then Buy(WETH) else Sell(WETH)"
        ast = parser.parse(script)

        compiler = Compiler(registry)
        plan = compiler.compile(ast, context={"2500": 2500, "2000": 2000})
        assert plan.step_count == 1
        assert plan.steps[0].operation_name == "Buy"

    def test_parallel_swaps(self):
        registry = OperationRegistry()
        registry.register_protocol_operations("uniswap_v2")

        parser = FireParser()
        script = "SwapA(x) || SwapB(y) || SwapC(z)"
        ast = parser.parse(script)

        compiler = Compiler(registry)
        plan = compiler.compile(ast)
        assert plan.step_count == 3
        # All steps should be in the same parallel group
        groups = {s.parallel_group for s in plan.steps}
        assert len(groups) == 1
