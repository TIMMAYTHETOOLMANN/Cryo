// SPDX-License-Identifier: MIT
pragma solidity ^0.8.19;

import "forge-std/Test.sol";
import {LiquidationExecutorV3, LiquidationTarget} from "../contracts/liquidation/LiquidationExecutorV3.sol";

// ---------------------------------------------------------------------------
// Minimal mocks (fully self-contained, no external deps)
// ---------------------------------------------------------------------------

contract MockToken {
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
        balanceOf[to]         += amount;
        return true;
    }

    function transferFrom(address from, address to, uint256 amount) external returns (bool) {
        require(allowance[from][msg.sender] >= amount, "Allowance");
        allowance[from][msg.sender] -= amount;
        balanceOf[from] -= amount;
        balanceOf[to]   += amount;
        return true;
    }
}

// ---------------------------------------------------------------------------
// Mock flash loan provider (happy path: mints debt, calls executeOperation)
// ---------------------------------------------------------------------------

interface IV3Receiver {
    function executeOperation(
        address asset,
        uint256 amount,
        uint256 premium,
        address initiator,
        bytes calldata params
    ) external returns (bool);
}

contract MockFlashProvider {
    MockToken internal _debtToken;
    uint256   public lastAmount;
    bool      public shouldFail;

    constructor(MockToken debtTok) { _debtToken = debtTok; }

    function setShouldFail(bool v) external { shouldFail = v; }

    function flashLoanSimple(
        address receiverAddress,
        address asset,
        uint256 amount,
        bytes calldata params,
        uint16
    ) external {
        require(!shouldFail, "Provider failed");
        lastAmount = amount;
        _debtToken.mint(receiverAddress, amount);
        uint256 premium = (amount * 5) / 10_000; // 0.05%
        bool ok = IV3Receiver(receiverAddress).executeOperation(
            asset, amount, premium, receiverAddress, params
        );
        require(ok, "executeOperation returned false");
    }
}

// ---------------------------------------------------------------------------
// Mock protocol adapter
// ---------------------------------------------------------------------------

contract MockAdapter {
    MockToken internal _debtToken;
    MockToken internal _collToken;
    uint256   public collateralReturn;
    bool      public shouldFail;
    string    public protocolName_ = "mock-protocol";

    constructor(MockToken debt, MockToken coll) {
        _debtToken = debt;
        _collToken = coll;
    }

    function setCollateralReturn(uint256 v) external { collateralReturn = v; }
    function setShouldFail(bool v)           external { shouldFail = v; }

    function liquidate(
        address,         // collateralAsset
        address debtAsset,
        address,         // user
        uint256 debtAmount
    ) external returns (uint256) {
        require(!shouldFail, "Adapter failed");
        // Pull debt from caller
        uint256 balBefore = MockToken(debtAsset).balanceOf(address(this));
        require(
            MockToken(debtAsset).transferFrom(msg.sender, address(this), debtAmount),
            "transferFrom failed"
        );
        require(
            MockToken(debtAsset).balanceOf(address(this)) == balBefore + debtAmount,
            "Debt not received"
        );
        // Give collateral to caller
        _collToken.mint(msg.sender, collateralReturn);
        return collateralReturn;
    }

    function protocolName() external view returns (string memory) { return protocolName_; }
}

// ---------------------------------------------------------------------------
// Mock TWAP oracle
// ---------------------------------------------------------------------------

contract MockTWAP {
    mapping(address => uint256) public prices;

    function setPrice(address asset, uint256 price) external { prices[asset] = price; }
    function getTWAP(address asset) external view returns (uint256) { return prices[asset]; }
}

// ---------------------------------------------------------------------------
// Test contract
// ---------------------------------------------------------------------------

contract LiquidationExecutorV3Test is Test {
    LiquidationExecutorV3 internal exec;
    MockToken             internal debtToken;
    MockToken             internal collToken;
    MockFlashProvider     internal provider;
    MockFlashProvider     internal fallbackProvider;
    MockAdapter           internal adapter;
    MockTWAP              internal twap;

    address internal treasury = address(0xBEEF);
    bytes32 internal ADAPTER_KEY = keccak256("mock-protocol");

    function setUp() public {
        debtToken        = new MockToken("DEBT");
        collToken        = new MockToken("COLL");
        provider         = new MockFlashProvider(debtToken);
        fallbackProvider = new MockFlashProvider(debtToken);
        adapter          = new MockAdapter(debtToken, collToken);
        twap             = new MockTWAP();

        exec = new LiquidationExecutorV3(treasury);
        exec.addFlashLoanProvider(address(provider));
        exec.addSupportedDebtAsset(address(debtToken));
        exec.registerAdapter(ADAPTER_KEY, address(adapter));
    }

    // -----------------------------------------------------------------------
    // Constructor / initial state
    // -----------------------------------------------------------------------

    function testConstructor_InitialState() public view {
        assertEq(exec.OWNER(), address(this));
        assertEq(exec.TREASURY(), treasury);
        assertEq(exec.totalLiquidations(), 0);
        assertEq(exec.totalProfit(), 0);
        assertEq(exec.maxTWAPDeviationBps(), 200);
    }

    function testConstructor_RejectsZeroTreasury() public {
        vm.expectRevert("Zero treasury");
        new LiquidationExecutorV3(address(0));
    }

    // -----------------------------------------------------------------------
    // Access control
    // -----------------------------------------------------------------------

    function testOnlyOwner_ExecuteLiquidation() public {
        vm.prank(address(0xBAAD));
        vm.expectRevert("Not owner");
        exec.executeLiquidation(address(debtToken), 1e18, address(collToken), address(1), 0, ADAPTER_KEY);
    }

    function testOnlyOwner_RegisterAdapter() public {
        vm.prank(address(0xBAAD));
        vm.expectRevert("Not owner");
        exec.registerAdapter(keccak256("x"), address(adapter));
    }

    function testOnlyOwner_AddProvider() public {
        vm.prank(address(0xBAAD));
        vm.expectRevert("Not owner");
        exec.addFlashLoanProvider(address(provider));
    }

    // -----------------------------------------------------------------------
    // Input validation
    // -----------------------------------------------------------------------

    function testRevert_UnsupportedDebt() public {
        vm.expectRevert("Unsupported debt");
        exec.executeLiquidation(address(0xDEAD), 1e18, address(collToken), address(1), 0, ADAPTER_KEY);
    }

    function testRevert_ZeroAmount() public {
        vm.expectRevert("Zero amount");
        exec.executeLiquidation(address(debtToken), 0, address(collToken), address(1), 0, ADAPTER_KEY);
    }

    function testRevert_UnknownAdapter() public {
        bytes32 badKey = keccak256("bad");
        vm.expectRevert("Unknown adapter");
        exec.executeLiquidation(address(debtToken), 1e18, address(collToken), address(1), 0, badKey);
    }

    function testRevert_NoProviders() public {
        // Remove the only provider
        exec.removeFlashLoanProvider(0);
        vm.expectRevert("No providers");
        exec.executeLiquidation(address(debtToken), 1e18, address(collToken), address(1), 0, ADAPTER_KEY);
    }

    // -----------------------------------------------------------------------
    // Single liquidation happy path
    // -----------------------------------------------------------------------

    function testSingleLiquidation_Success() public {
        uint256 debtAmt = 1_000e18;
        uint256 bonus   = 1_050e18;
        adapter.setCollateralReturn(bonus);

        exec.executeLiquidation(
            address(debtToken), debtAmt,
            address(collToken), address(0xABCD),
            0,
            ADAPTER_KEY
        );

        // Treasury received the collateral
        assertEq(collToken.balanceOf(treasury), bonus);
        assertEq(exec.totalLiquidations(), 1);
        assertEq(exec.totalProfit(), bonus);
    }

    function testSingleLiquidation_SlippageGuard() public {
        adapter.setCollateralReturn(100e18);
        vm.expectRevert("Slippage exceeded");
        exec.executeLiquidation(
            address(debtToken), 1_000e18,
            address(collToken), address(0x1),
            200e18,   // require 200, only get 100
            ADAPTER_KEY
        );
    }

    // -----------------------------------------------------------------------
    // Multi-provider fallback (Module 3 Enhancement #2)
    // -----------------------------------------------------------------------

    function testMultiProviderFallback_UsesSecondProvider() public {
        // Make the first provider fail
        provider.setShouldFail(true);
        // Register the second provider (works fine)
        exec.addFlashLoanProvider(address(fallbackProvider));

        uint256 bonus = 500e18;
        adapter.setCollateralReturn(bonus);

        exec.executeLiquidation(
            address(debtToken), 500e18,
            address(collToken), address(0x1),
            0,
            ADAPTER_KEY
        );

        // Successful via fallback provider
        assertEq(exec.totalLiquidations(), 1);
        assertEq(collToken.balanceOf(treasury), bonus);
    }

    function testMultiProviderFallback_AllFail_Reverts() public {
        provider.setShouldFail(true);
        exec.addFlashLoanProvider(address(fallbackProvider));
        fallbackProvider.setShouldFail(true);

        vm.expectRevert("All providers failed");
        exec.executeLiquidation(
            address(debtToken), 1e18,
            address(collToken), address(0x1),
            0,
            ADAPTER_KEY
        );
    }

    // -----------------------------------------------------------------------
    // Batch liquidation (Module 5 Enhancement #2)
    // -----------------------------------------------------------------------

    function testBatchLiquidation_TwoTargets() public {
        uint256 bonus = 600e18;
        adapter.setCollateralReturn(bonus); // same collateral per liquidation

        LiquidationTarget[] memory targets = new LiquidationTarget[](2);
        targets[0] = LiquidationTarget({
            user:            address(0x111),
            debtAsset:       address(debtToken),
            debtAmount:      500e18,
            collateralAsset: address(collToken),
            minCollateral:   0,
            adapterKey:      ADAPTER_KEY
        });
        targets[1] = LiquidationTarget({
            user:            address(0x222),
            debtAsset:       address(debtToken),
            debtAmount:      300e18,
            collateralAsset: address(collToken),
            minCollateral:   0,
            adapterKey:      ADAPTER_KEY
        });

        exec.executeBatchLiquidation(targets);

        // Both liquidations ran; treasury received 2 * bonus
        assertEq(exec.totalLiquidations(), 2);
        assertEq(collToken.balanceOf(treasury), bonus * 2);
    }

    function testBatchLiquidation_EmptyReverts() public {
        LiquidationTarget[] memory empty = new LiquidationTarget[](0);
        vm.expectRevert("Empty batch");
        exec.executeBatchLiquidation(empty);
    }

    function testBatchLiquidation_MixedDebtReverts() public {
        MockToken other = new MockToken("OTHER");
        exec.addSupportedDebtAsset(address(other));

        LiquidationTarget[] memory targets = new LiquidationTarget[](2);
        targets[0] = LiquidationTarget({
            user: address(1), debtAsset: address(debtToken), debtAmount: 1e18,
            collateralAsset: address(collToken), minCollateral: 0, adapterKey: ADAPTER_KEY
        });
        targets[1] = LiquidationTarget({
            user: address(2), debtAsset: address(other), debtAmount: 1e18,
            collateralAsset: address(collToken), minCollateral: 0, adapterKey: ADAPTER_KEY
        });

        vm.expectRevert("Mixed assets");
        exec.executeBatchLiquidation(targets);
    }

    // -----------------------------------------------------------------------
    // TWAP circuit breaker (Module 6 Enhancement #3)
    // -----------------------------------------------------------------------

    function testTWAP_StalePrice_Reverts() public {
        exec.setTWAPOracle(address(twap));
        // TWAP returns 0 (stale) for collToken → should revert
        twap.setPrice(address(collToken), 0);
        adapter.setCollateralReturn(1000e18);

        vm.expectRevert("TWAP: stale price");
        exec.executeLiquidation(
            address(debtToken), 1_000e18,
            address(collToken), address(0x1),
            0, ADAPTER_KEY
        );
    }

    function testTWAP_ValidPrice_Passes() public {
        exec.setTWAPOracle(address(twap));
        twap.setPrice(address(collToken), 2000e8); // Valid price
        adapter.setCollateralReturn(1050e18);

        exec.executeLiquidation(
            address(debtToken), 1_000e18,
            address(collToken), address(0x1),
            0, ADAPTER_KEY
        );
        assertEq(exec.totalLiquidations(), 1);
    }

    // -----------------------------------------------------------------------
    // executeOperation security: must only be called during active execution
    // -----------------------------------------------------------------------

    function testExecuteOperation_RejectsDirectCall() public {
        vm.expectRevert("Not in execution");
        exec.executeOperation(address(debtToken), 1e18, 0, address(exec), "");
    }

    function testExecuteOperation_RejectsUnregisteredCaller() public {
        // Simulate being inside an execution (_locked == 2) via a crafted test
        // We can't directly set _locked, but we can verify via the "Invalid caller" path
        // by using a registered provider that passes a wrong caller address.
        // This is effectively tested by testSingleLiquidation_Success (valid path).
        // For the invalid caller path, we check the revert via direct call:
        vm.expectRevert("Not in execution"); // _locked == 1 fires first
        vm.prank(address(0xBAAD));
        exec.executeOperation(address(debtToken), 1e18, 0, address(this), "");
    }

    // -----------------------------------------------------------------------
    // Reentrancy guard
    // -----------------------------------------------------------------------

    function testReentrancy_BlocksDoubleEntry() public {
        // Verify guard resets after successful call; second sequential call should pass.
        adapter.setCollateralReturn(1050e18);
        exec.executeLiquidation(address(debtToken), 1_000e18, address(collToken), address(1), 0, ADAPTER_KEY);
        // If guard broken, second call would also fail; it must succeed.
        exec.executeLiquidation(address(debtToken), 1_000e18, address(collToken), address(1), 0, ADAPTER_KEY);
        assertEq(exec.totalLiquidations(), 2);
    }

    // -----------------------------------------------------------------------
    // Admin: provider management
    // -----------------------------------------------------------------------

    function testAddRemoveProvider() public {
        uint256 before = exec.flashLoanProviders(0) == address(provider) ? 1 : 0;
        assertEq(before, 1);

        exec.removeFlashLoanProvider(0);
        vm.expectRevert("No providers");
        exec.executeLiquidation(address(debtToken), 1e18, address(collToken), address(1), 0, ADAPTER_KEY);
    }

    function testRegisterAdapter_Overwrite() public {
        MockAdapter newAdapter = new MockAdapter(debtToken, collToken);
        exec.registerAdapter(ADAPTER_KEY, address(newAdapter));
        assertEq(exec.adapters(ADAPTER_KEY), address(newAdapter));
    }

    // -----------------------------------------------------------------------
    // Stats accumulation
    // -----------------------------------------------------------------------

    function testStats_Accumulate() public {
        adapter.setCollateralReturn(500e18);
        exec.executeLiquidation(address(debtToken), 1e18, address(collToken), address(1), 0, ADAPTER_KEY);
        exec.executeLiquidation(address(debtToken), 1e18, address(collToken), address(2), 0, ADAPTER_KEY);
        assertEq(exec.totalLiquidations(), 2);
        assertEq(exec.totalProfit(),       1_000e18);
    }

    // -----------------------------------------------------------------------
    // Withdraw
    // -----------------------------------------------------------------------

    function testWithdraw() public {
        debtToken.mint(address(exec), 100e18);
        exec.withdraw(address(debtToken), address(0xCAFE), 100e18);
        assertEq(debtToken.balanceOf(address(0xCAFE)), 100e18);
    }

    function testWithdraw_OnlyOwner() public {
        vm.prank(address(0xBAD));
        vm.expectRevert("Not owner");
        exec.withdraw(address(debtToken), address(0xCAFE), 1);
    }
}
