// SPDX-License-Identifier: MIT
pragma solidity ^0.8.19;

import "forge-std/Test.sol";
import {FinancialExecutor, Step} from "../contracts/fire/FinancialExecutor.sol";

// ---------------------------------------------------------------------------
// Mock contracts for testing
// ---------------------------------------------------------------------------

contract MockTarget {
    uint256 public callCount;
    uint256 public lastValue;
    bool public shouldRevert;

    function doWork(uint256 value) external payable {
        require(!shouldRevert, "MockTarget: forced revert");
        callCount++;
        lastValue = value;
    }

    function setShouldRevert(bool _revert) external {
        shouldRevert = _revert;
    }

    // Accept ETH
    receive() external payable {}
}

contract MockTokenFire {
    string public name;
    mapping(address => uint256) public balanceOf;
    mapping(address => mapping(address => uint256)) public allowance;

    constructor(string memory _name) { name = _name; }

    function mint(address to, uint256 amount) external { balanceOf[to] += amount; }

    function approve(address spender, uint256 amount) external returns (bool) {
        allowance[msg.sender][spender] = amount;
        return true;
    }

    function transfer(address to, uint256 amount) external returns (bool) {
        require(balanceOf[msg.sender] >= amount, "Balance");
        balanceOf[msg.sender] -= amount;
        balanceOf[to] += amount;
        return true;
    }

    function transferFrom(address from, address to, uint256 amount) external returns (bool) {
        require(allowance[from][msg.sender] >= amount, "Allowance");
        allowance[from][msg.sender] -= amount;
        balanceOf[from] -= amount;
        balanceOf[to] += amount;
        return true;
    }
}

/// @dev Contract that sends ETH to the executor during plan execution,
///      simulating profit from a swap or liquidation.
contract ProfitSimulator {
    function sendProfit(address payable to) external payable {
        (bool ok, ) = to.call{value: msg.value}("");
        require(ok, "send failed");
    }
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

contract FinancialExecutorTest is Test {
    FinancialExecutor public executor;
    MockTarget public target1;
    MockTarget public target2;
    MockTokenFire public token;
    ProfitSimulator public profitSim;

    address public owner;
    address public nonOwner;

    function setUp() public {
        owner = address(this);
        nonOwner = address(0xBEEF);

        address[] memory providers = new address[](1);
        providers[0] = address(0xAAAA);

        executor = new FinancialExecutor(providers);
        target1 = new MockTarget();
        target2 = new MockTarget();
        token = new MockTokenFire("TestToken");
        profitSim = new ProfitSimulator();

        // Fund the profit simulator
        vm.deal(address(profitSim), 100 ether);
    }

    // ── Constructor Tests ──────────────────────────────────────────────────

    function testConstructorSetsOwner() public view {
        assertEq(executor.OWNER(), owner);
    }

    function testConstructorRegistersProviders() public view {
        assertEq(executor.providerCount(), 1);
        assertEq(executor.flashLoanProviders(0), address(0xAAAA));
    }

    function testConstructorRejectsZeroProvider() public {
        address[] memory providers = new address[](1);
        providers[0] = address(0);
        vm.expectRevert("Zero provider address");
        new FinancialExecutor(providers);
    }

    // ── Execute Plan Tests ─────────────────────────────────────────────────

    function testExecuteSingleStep() public {
        Step[] memory steps = new Step[](1);
        steps[0] = Step({
            target: address(target1),
            data: abi.encodeCall(MockTarget.doWork, (42)),
            value: 0,
            allowFailure: false
        });

        bool success = executor.executePlan(steps);
        assertTrue(success);
        assertEq(target1.callCount(), 1);
        assertEq(target1.lastValue(), 42);
        assertEq(executor.totalPlansExecuted(), 1);
    }

    function testExecuteMultipleSteps() public {
        Step[] memory steps = new Step[](2);
        steps[0] = Step({
            target: address(target1),
            data: abi.encodeCall(MockTarget.doWork, (100)),
            value: 0,
            allowFailure: false
        });
        steps[1] = Step({
            target: address(target2),
            data: abi.encodeCall(MockTarget.doWork, (200)),
            value: 0,
            allowFailure: false
        });

        executor.executePlan(steps);
        assertEq(target1.callCount(), 1);
        assertEq(target1.lastValue(), 100);
        assertEq(target2.callCount(), 1);
        assertEq(target2.lastValue(), 200);
    }

    function testExecuteWithETHValue() public {
        vm.deal(address(this), 1 ether);
        Step[] memory steps = new Step[](1);
        steps[0] = Step({
            target: address(target1),
            data: abi.encodeCall(MockTarget.doWork, (1)),
            value: 0.5 ether,
            allowFailure: false
        });

        executor.executePlan{value: 0.5 ether}(steps);
        assertEq(address(target1).balance, 0.5 ether);
    }

    function testExecuteEmptyPlanReverts() public {
        Step[] memory steps = new Step[](0);
        vm.expectRevert("Empty plan");
        executor.executePlan(steps);
    }

    function testExecuteZeroTargetReverts() public {
        Step[] memory steps = new Step[](1);
        steps[0] = Step({
            target: address(0),
            data: "",
            value: 0,
            allowFailure: false
        });
        vm.expectRevert("Zero target");
        executor.executePlan(steps);
    }

    function testStepFailureRevertsWhenNotAllowed() public {
        target1.setShouldRevert(true);

        Step[] memory steps = new Step[](1);
        steps[0] = Step({
            target: address(target1),
            data: abi.encodeCall(MockTarget.doWork, (1)),
            value: 0,
            allowFailure: false
        });

        vm.expectRevert();
        executor.executePlan(steps);
    }

    function testStepFailureContinuesWhenAllowed() public {
        target1.setShouldRevert(true);

        Step[] memory steps = new Step[](2);
        steps[0] = Step({
            target: address(target1),
            data: abi.encodeCall(MockTarget.doWork, (1)),
            value: 0,
            allowFailure: true  // Should continue despite failure
        });
        steps[1] = Step({
            target: address(target2),
            data: abi.encodeCall(MockTarget.doWork, (2)),
            value: 0,
            allowFailure: false
        });

        bool success = executor.executePlan(steps);
        assertTrue(success);
        // target1 reverted but target2 succeeded
        assertEq(target1.callCount(), 0);
        assertEq(target2.callCount(), 1);
    }

    function testProfitDistribution() public {
        // Plan: call profitSim to send ETH to executor (simulating profit)
        Step[] memory steps = new Step[](1);
        steps[0] = Step({
            target: address(profitSim),
            data: abi.encodeCall(ProfitSimulator.sendProfit, (payable(address(executor)))),
            value: 0,
            allowFailure: false
        });

        uint256 ownerBalBefore = address(this).balance;
        executor.executePlan(steps);
        uint256 ownerBalAfter = address(this).balance;

        // Profit should have been forwarded to owner
        assertGt(executor.totalProfit(), 0);
        assertGt(ownerBalAfter, ownerBalBefore);
    }

    function testMultiplePlansTrackStats() public {
        Step[] memory steps = new Step[](1);
        steps[0] = Step({
            target: address(target1),
            data: abi.encodeCall(MockTarget.doWork, (1)),
            value: 0,
            allowFailure: false
        });

        executor.executePlan(steps);
        executor.executePlan(steps);
        executor.executePlan(steps);

        assertEq(executor.totalPlansExecuted(), 3);
        assertEq(target1.callCount(), 3);
    }

    // ── Access Control Tests ───────────────────────────────────────────────

    function testOnlyOwnerCanExecute() public {
        Step[] memory steps = new Step[](1);
        steps[0] = Step({
            target: address(target1),
            data: abi.encodeCall(MockTarget.doWork, (1)),
            value: 0,
            allowFailure: false
        });

        vm.prank(nonOwner);
        vm.expectRevert("Not owner");
        executor.executePlan(steps);
    }

    function testOnlyOwnerCanAddProvider() public {
        vm.prank(nonOwner);
        vm.expectRevert("Not owner");
        executor.addProvider(address(0xBBBB));
    }

    function testOnlyOwnerCanRemoveProvider() public {
        vm.prank(nonOwner);
        vm.expectRevert("Not owner");
        executor.removeProvider(0);
    }

    function testOnlyOwnerCanPause() public {
        vm.prank(nonOwner);
        vm.expectRevert("Not owner");
        executor.setPaused(true);
    }

    // ── Pause Tests ────────────────────────────────────────────────────────

    function testPauseBlocksExecution() public {
        executor.setPaused(true);

        Step[] memory steps = new Step[](1);
        steps[0] = Step({
            target: address(target1),
            data: abi.encodeCall(MockTarget.doWork, (1)),
            value: 0,
            allowFailure: false
        });

        vm.expectRevert("Executor paused");
        executor.executePlan(steps);
    }

    function testUnpauseAllowsExecution() public {
        executor.setPaused(true);
        executor.setPaused(false);

        Step[] memory steps = new Step[](1);
        steps[0] = Step({
            target: address(target1),
            data: abi.encodeCall(MockTarget.doWork, (1)),
            value: 0,
            allowFailure: false
        });

        bool success = executor.executePlan(steps);
        assertTrue(success);
    }

    // ── Provider Management Tests ──────────────────────────────────────────

    function testAddProvider() public {
        executor.addProvider(address(0xBBBB));
        assertEq(executor.providerCount(), 2);
        assertEq(executor.flashLoanProviders(1), address(0xBBBB));
    }

    function testAddZeroProviderReverts() public {
        vm.expectRevert("Zero provider");
        executor.addProvider(address(0));
    }

    function testRemoveProvider() public {
        executor.addProvider(address(0xBBBB));
        assertEq(executor.providerCount(), 2);

        executor.removeProvider(0);
        assertEq(executor.providerCount(), 1);
        // The remaining provider is the one that was swapped in
        assertEq(executor.flashLoanProviders(0), address(0xBBBB));
    }

    function testRemoveProviderOutOfBoundsReverts() public {
        vm.expectRevert("Index out of bounds");
        executor.removeProvider(99);
    }

    // ── Token Withdrawal Tests ─────────────────────────────────────────────

    function testWithdrawToken() public {
        token.mint(address(executor), 1000);
        executor.withdrawToken(address(token), owner, 500);
        assertEq(token.balanceOf(owner), 500);
        assertEq(token.balanceOf(address(executor)), 500);
    }

    function testWithdrawTokenZeroRecipientReverts() public {
        vm.expectRevert("Zero recipient");
        executor.withdrawToken(address(token), address(0), 100);
    }

    function testWithdrawETH() public {
        vm.deal(address(executor), 1 ether);
        uint256 balBefore = owner.balance;
        executor.withdrawETH(payable(owner), 0.5 ether);
        assertEq(owner.balance - balBefore, 0.5 ether);
    }

    function testWithdrawETHZeroRecipientReverts() public {
        vm.expectRevert("Zero recipient");
        executor.withdrawETH(payable(address(0)), 1);
    }

    // ── Events Tests ───────────────────────────────────────────────────────

    function testEmitsStepExecutedEvent() public {
        Step[] memory steps = new Step[](1);
        steps[0] = Step({
            target: address(target1),
            data: abi.encodeCall(MockTarget.doWork, (42)),
            value: 0,
            allowFailure: false
        });

        vm.expectEmit(true, false, false, false);
        emit FinancialExecutor.StepExecuted(0, true, "");
        executor.executePlan(steps);
    }

    function testEmitsPlanExecutedEvent() public {
        Step[] memory steps = new Step[](1);
        steps[0] = Step({
            target: address(target1),
            data: abi.encodeCall(MockTarget.doWork, (42)),
            value: 0,
            allowFailure: false
        });

        // Just verify event is emitted (hash varies)
        vm.recordLogs();
        executor.executePlan(steps);
        Vm.Log[] memory logs = vm.getRecordedLogs();
        // Should have StepExecuted + PlanExecuted events
        assertTrue(logs.length >= 2);
    }

    // ── Receive ETH ────────────────────────────────────────────────────────

    function testReceiveETH() public {
        vm.deal(address(this), 1 ether);
        (bool ok, ) = address(executor).call{value: 0.5 ether}("");
        assertTrue(ok);
        assertEq(address(executor).balance, 0.5 ether);
    }

    // Accept ETH for profit distribution
    receive() external payable {}
}
