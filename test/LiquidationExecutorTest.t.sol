// SPDX-License-Identifier: MIT
pragma solidity ^0.8.19;

import "forge-std/Test.sol";
// Use selective imports so that the locally-defined IERC20/IPool interfaces in each
// contract file do not collide in this compilation unit.
import {LiquidationExecutor}   from "../contracts/liquidation/LiquidationExecutor.sol";
import {LiquidationExecutorV2} from "../contracts/liquidation/LiquidationExecutorV2.sol";

// ---------------------------------------------------------------------------
// Minimal flash-loan-receiver interface (avoids casting to concrete contract types)
// ---------------------------------------------------------------------------

interface IFlashLoanReceiver {
    function executeOperation(
        address asset,
        uint256 amount,
        uint256 premium,
        address initiator,
        bytes calldata params
    ) external returns (bool);
}

// ---------------------------------------------------------------------------
// Minimal ERC-20 mock
// ---------------------------------------------------------------------------

contract MockERC20 {
    mapping(address => uint256) public balanceOf;
    mapping(address => mapping(address => uint256)) public allowance;

    function mint(address to, uint256 amount) external {
        balanceOf[to] += amount;
    }

    function approve(address spender, uint256 amount) external returns (bool) {
        allowance[msg.sender][spender] = amount;
        return true;
    }

    function transfer(address to, uint256 amount) external returns (bool) {
        require(balanceOf[msg.sender] >= amount, "Insufficient");
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

// ---------------------------------------------------------------------------
// Mock Aave V3 Pool that simulates flash loan + liquidation
// ---------------------------------------------------------------------------

contract MockAavePool {
    // Flash loan tracking
    address public lastFlashAsset;
    uint256 public lastFlashAmount;
    bytes   public lastFlashParams;

    // Liquidation tracking
    address public lastCollateral;
    address public lastDebt;
    address public lastUser;
    uint256 public lastDebtCovered;

    // Collateral given back to receiver per liquidation call
    uint256 public collateralToReturn;

    MockERC20 internal _debtToken;
    MockERC20 internal _collToken;

    constructor(MockERC20 debtTok, MockERC20 collTok) {
        _debtToken = debtTok;
        _collTok   = collTok;
    }

    function setCollateralReturn(uint256 amount) external {
        collateralToReturn = amount;
    }

    /// @notice Simulates Aave V3 flashLoanSimple
    function flashLoanSimple(
        address receiverAddress,
        address asset,
        uint256 amount,
        bytes calldata params,
        uint16
    ) external {
        lastFlashAsset  = asset;
        lastFlashAmount = amount;
        lastFlashParams = params;

        // Send funds to receiver
        _debtToken.mint(receiverAddress, amount);

        uint256 premium = (amount * 5) / 10_000; // 0.05%

        // Call executeOperation on receiver
        bool ok = IFlashLoanReceiver(receiverAddress).executeOperation(
            asset, amount, premium, receiverAddress, params
        );
        require(ok, "executeOperation failed");
    }

    /// @notice Simulates Aave V3 liquidationCall
    function liquidationCall(
        address collateralAsset,
        address debtAsset,
        address user,
        uint256 debtToCover,
        bool
    ) external {
        lastCollateral = collateralAsset;
        lastDebt       = debtAsset;
        lastUser       = user;
        lastDebtCovered = debtToCover;

        // Pull debt from caller
        _debtToken.transferFrom(msg.sender, address(this), debtToCover);

        // Send collateral to caller
        _collToken.mint(msg.sender, collateralToReturn);
    }
}

// ---------------------------------------------------------------------------
// Test contract
// ---------------------------------------------------------------------------

contract LiquidationExecutorTest is Test {
    MockERC20          internal debtToken;
    MockERC20          internal collToken;
    MockAavePool       internal pool;
    LiquidationExecutor   internal exec;
    LiquidationExecutorV2 internal execV2;

    address internal treasury = address(0xBEEF);
    address internal owner;

    function setUp() public {
        owner = address(this);

        debtToken = new MockERC20();
        collToken = new MockERC20();
        pool      = new MockAavePool(debtToken, collToken);

        exec   = new LiquidationExecutor(address(pool), treasury);
        execV2 = new LiquidationExecutorV2(address(pool), treasury);

        // Whitelist debt token
        exec.addSupportedDebtAsset(address(debtToken));
        execV2.addSupportedDebtAsset(address(debtToken));
    }

    // -----------------------------------------------------------------------
    // Constructor / initial state
    // -----------------------------------------------------------------------

    function testConstructor_V1() public view {
        assertEq(address(exec.POOL()), address(pool));
        assertEq(exec.TREASURY(), treasury);
        assertEq(exec.OWNER(), address(this));
    }

    function testConstructor_V2() public view {
        assertEq(address(execV2.POOL()), address(pool));
        assertEq(execV2.TREASURY(), treasury);
        assertEq(execV2.OWNER(), address(this));
        assertEq(execV2.totalLiquidations(), 0);
        assertEq(execV2.totalProfit(), 0);
        assertTrue(execV2.surplusUtilizationEnabled());
    }

    function testConstructor_RejectsZeroPool() public {
        vm.expectRevert("Zero pool");
        new LiquidationExecutor(address(0), treasury);
    }

    function testConstructor_RejectsZeroTreasury() public {
        vm.expectRevert("Zero treasury");
        new LiquidationExecutor(address(pool), address(0));
    }

    // -----------------------------------------------------------------------
    // Access control
    // -----------------------------------------------------------------------

    function testOnlyOwner_ExecuteLiquidation() public {
        address notOwner = address(0xBAAD);
        vm.prank(notOwner);
        vm.expectRevert("Not owner");
        exec.executeLiquidation(address(debtToken), 1e18, address(collToken), address(0x1), 0);
    }

    function testOnlyOwner_AddDebtAsset() public {
        address notOwner = address(0xBAAD);
        vm.prank(notOwner);
        vm.expectRevert("Not owner");
        exec.addSupportedDebtAsset(address(0x9));
    }

    function testOnlyOwner_SetMinProfit() public {
        address notOwner = address(0xBAAD);
        vm.prank(notOwner);
        vm.expectRevert("Not owner");
        exec.setMinProfit(1e18);
    }

    // -----------------------------------------------------------------------
    // Debt asset management
    // -----------------------------------------------------------------------

    function testAddRemoveSupportedDebtAsset() public {
        address newAsset = address(0xAABB);
        assertFalse(exec.supportedDebtAssets(newAsset));

        exec.addSupportedDebtAsset(newAsset);
        assertTrue(exec.supportedDebtAssets(newAsset));

        exec.removeSupportedDebtAsset(newAsset);
        assertFalse(exec.supportedDebtAssets(newAsset));
    }

    function testExecuteLiquidation_UnsupportedDebt_Reverts() public {
        address unsupported = address(0xDEAD);
        vm.expectRevert("Unsupported debt");
        exec.executeLiquidation(unsupported, 1e18, address(collToken), address(0x1), 0);
    }

    function testExecuteLiquidation_ZeroAmount_Reverts() public {
        vm.expectRevert("Zero amount");
        exec.executeLiquidation(address(debtToken), 0, address(collToken), address(0x1), 0);
    }

    // -----------------------------------------------------------------------
    // Flash loan liquidation happy path (V1)
    // NOTE: MockERC20 does not enforce decimal precision; amounts below are
    //       intentionally plain integers and represent token units without a
    //       specific decimal assumption.
    // -----------------------------------------------------------------------

    function testExecuteLiquidation_V1_Success() public {
        uint256 debtAmt   = 1000e6;   // 1,000 USDC (6 dec)
        uint256 collBonus = 1050e6;   // 5% bonus collateral returned
        address user      = address(0x1234);

        pool.setCollateralReturn(collBonus);

        exec.executeLiquidation(address(debtToken), debtAmt, address(collToken), user, 0);

        // Pool recorded the liquidation call correctly
        assertEq(pool.lastUser(),        user);
        assertEq(pool.lastDebtCovered(), debtAmt);
        assertEq(pool.lastCollateral(),  address(collToken));

        // Treasury received collateral (profit swept)
        assertEq(collToken.balanceOf(treasury), collBonus);
    }

    function testExecuteLiquidation_V1_SlippageGuard() public {
        uint256 debtAmt        = 1000e6;
        uint256 minCollateral  = 2000e6; // Require 2× but only 1.05× returned
        uint256 collBonus      = 1050e6;

        pool.setCollateralReturn(collBonus);

        vm.expectRevert("Slippage");
        exec.executeLiquidation(address(debtToken), debtAmt, address(collToken), address(0x1), minCollateral);
    }

    // -----------------------------------------------------------------------
    // Flash loan liquidation happy path (V2)
    // -----------------------------------------------------------------------

    function testExecuteLiquidation_V2_Success() public {
        uint256 debtAmt   = 500e18;
        uint256 collBonus = 550e18;
        address user      = address(0x5678);

        pool.setCollateralReturn(collBonus);

        // V2 pool mock uses same interface but cast to V2
        MockAavePool poolV2 = new MockAavePool(debtToken, collToken);
        poolV2.setCollateralReturn(collBonus);
        LiquidationExecutorV2 e2 = new LiquidationExecutorV2(address(poolV2), treasury);
        e2.addSupportedDebtAsset(address(debtToken));

        e2.executeLiquidation(address(debtToken), debtAmt, address(collToken), user, 0);

        (uint256 totalLiqs, uint256 totalProfit) = e2.getStats();
        assertEq(totalLiqs,   1);
        assertEq(totalProfit, collBonus);
    }

    function testV2_Stats_Accumulate() public {
        uint256 collBonus = 1100e18;
        pool.setCollateralReturn(collBonus);

        // Need a separate pool instance per V2 executor to avoid collisions
        MockAavePool p = new MockAavePool(debtToken, collToken);
        p.setCollateralReturn(collBonus);
        LiquidationExecutorV2 e2 = new LiquidationExecutorV2(address(p), treasury);
        e2.addSupportedDebtAsset(address(debtToken));

        e2.executeLiquidation(address(debtToken), 1e18, address(collToken), address(0x1), 0);
        e2.executeLiquidation(address(debtToken), 1e18, address(collToken), address(0x2), 0);

        (uint256 totalLiqs, uint256 totalProfit) = e2.getStats();
        assertEq(totalLiqs, 2);
        assertEq(totalProfit, collBonus * 2);
    }

    // -----------------------------------------------------------------------
    // setMinProfit / setSurplusUtilization
    // -----------------------------------------------------------------------

    function testSetMinProfit() public {
        exec.setMinProfit(1e17);
        assertEq(exec.minProfit(), 1e17);
    }

    function testSetSurplusUtilization() public {
        assertTrue(execV2.surplusUtilizationEnabled());
        execV2.setSurplusUtilization(false);
        assertFalse(execV2.surplusUtilizationEnabled());
    }

    // -----------------------------------------------------------------------
    // withdrawProfit
    // -----------------------------------------------------------------------

    function testWithdrawProfit() public {
        // Fund the executor with some tokens
        debtToken.mint(address(exec), 100e18);
        uint256 before = debtToken.balanceOf(address(0xCAFE));
        exec.withdrawProfit(address(debtToken), 100e18, address(0xCAFE));
        assertEq(debtToken.balanceOf(address(0xCAFE)), before + 100e18);
    }

    // -----------------------------------------------------------------------
    // Reentrancy guard — blocks reentrant calls during active flash loan
    // -----------------------------------------------------------------------

    function testReentrancyGuard_BlocksDoubleEntry() public {
        // After a successful call the guard must be reset so a second sequential
        // call succeeds (guard is released on return, not permanently locked).
        uint256 collBonus = 1050e18;
        pool.setCollateralReturn(collBonus);
        exec.executeLiquidation(address(debtToken), 1e18, address(collToken), address(0xAB), 0);
        // Second sequential call must also succeed — guard was released.
        exec.executeLiquidation(address(debtToken), 1e18, address(collToken), address(0xAB), 0);
    }

    function testReentrancyGuard_BlocksReentrantCall() public {
        // Deploy a malicious pool that, inside flashLoanSimple, tries to call
        // executeLiquidation again on the same executor before executeOperation returns.
        ReentrantPool malPool = new ReentrantPool(debtToken, collToken, address(debtToken));
        LiquidationExecutor malExec = new LiquidationExecutor(address(malPool), treasury);
        malExec.addSupportedDebtAsset(address(debtToken));
        malPool.setTarget(address(malExec));

        // The reentrant pool will attempt a second executeLiquidation during the
        // flash loan callback, which must revert with "REENTRANCY".
        vm.expectRevert("REENTRANCY");
        malExec.executeLiquidation(address(debtToken), 1e18, address(collToken), address(0x1), 0);
    }
}

// ---------------------------------------------------------------------------
// Malicious pool that re-enters executeLiquidation during flashLoanSimple
// ---------------------------------------------------------------------------

contract ReentrantPool {
    MockERC20 internal _debtToken;
    MockERC20 internal _collToken;
    address   internal _debtAsset;
    address   internal _target;   // LiquidationExecutor to attack

    constructor(MockERC20 debt, MockERC20 coll, address debtAsset) {
        _debtToken = debt;
        _collToken = coll;
        _debtAsset = debtAsset;
    }

    function setTarget(address target) external { _target = target; }

    function flashLoanSimple(
        address receiverAddress,
        address asset,
        uint256 amount,
        bytes calldata params,
        uint16
    ) external {
        _debtToken.mint(receiverAddress, amount);

        // Attempt reentrant call — must revert with REENTRANCY
        LiquidationExecutor(_target).executeLiquidation(
            _debtAsset, 1, address(_collToken), address(0x2), 0
        );

        // If we reach here the guard failed; the test should have reverted above.
        IFlashLoanReceiver(receiverAddress).executeOperation(
            asset, amount, 0, receiverAddress, params
        );
    }

    function liquidationCall(address, address, address, uint256, bool) external {
        _collToken.mint(msg.sender, 1e18);
    }
}
