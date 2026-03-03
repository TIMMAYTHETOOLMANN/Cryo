// SPDX-License-Identifier: MIT
pragma solidity ^0.8.19;

import "forge-std/Test.sol";
import {RiskMitigationExecutor, MitigationParams} from "../contracts/liquidation/RiskMitigationExecutor.sol";

// ---------------------------------------------------------------------------
// Mock contracts
// ---------------------------------------------------------------------------

contract MockTokenRME {
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

// ---------------------------------------------------------------------------

interface IRMEReceiver {
    function executeOperation(
        address asset,
        uint256 amount,
        uint256 premium,
        address initiator,
        bytes calldata params
    ) external returns (bool);
}

contract MockFlashProviderRME {
    MockTokenRME internal _debtToken;
    bool public shouldFail;

    constructor(MockTokenRME debtTok) { _debtToken = debtTok; }

    function setShouldFail(bool v) external { shouldFail = v; }

    function flashLoanSimple(
        address receiverAddress,
        address asset,
        uint256 amount,
        bytes calldata params,
        uint16
    ) external {
        require(!shouldFail, "Provider failed");
        _debtToken.mint(receiverAddress, amount);
        uint256 premium = (amount * 5) / 10_000;
        bool ok = IRMEReceiver(receiverAddress).executeOperation(
            asset, amount, premium, receiverAddress, params
        );
        require(ok, "executeOperation returned false");
    }
}

// ---------------------------------------------------------------------------

contract MockAdapterRME {
    MockTokenRME internal _debtToken;
    MockTokenRME internal _collToken;
    uint256 public collateralReturn;

    constructor(MockTokenRME debt, MockTokenRME coll) {
        _debtToken = debt;
        _collToken = coll;
    }

    function setCollateralReturn(uint256 v) external { collateralReturn = v; }

    function liquidate(
        address,
        address debtAsset,
        address,
        uint256 debtAmount
    ) external returns (uint256) {
        uint256 balBefore = MockTokenRME(debtAsset).balanceOf(address(this));
        require(
            MockTokenRME(debtAsset).transferFrom(msg.sender, address(this), debtAmount),
            "transferFrom failed"
        );
        require(
            MockTokenRME(debtAsset).balanceOf(address(this)) == balBefore + debtAmount,
            "Debt not received"
        );
        _collToken.mint(msg.sender, collateralReturn);
        return collateralReturn;
    }

    function protocolName() external pure returns (string memory) { return "mock-protocol"; }
}

// ---------------------------------------------------------------------------

contract MockOracleRME {
    int256 public price;
    uint256 public updatedAtOverride;

    constructor(int256 _price) {
        price = _price;
    }

    function setUpdatedAt(uint256 ts) external { updatedAtOverride = ts; }
    function setPrice(int256 _price) external { price = _price; }

    function latestRoundData()
        external view
        returns (uint80, int256, uint256, uint256, uint80)
    {
        uint256 ts = updatedAtOverride == 0 ? block.timestamp : updatedAtOverride;
        return (1, price, 0, ts, 1);
    }

    function decimals() external pure returns (uint8) { return 8; }
}

// ---------------------------------------------------------------------------
// Test contract
// ---------------------------------------------------------------------------

contract RiskMitigationExecutorTest is Test {
    RiskMitigationExecutor internal exec;
    MockTokenRME internal debtToken;
    MockTokenRME internal collToken;
    MockFlashProviderRME internal provider;
    MockFlashProviderRME internal fallbackProvider;
    MockAdapterRME internal adapter;
    MockOracleRME internal oracle;

    address internal treasury = address(0xBEEF);
    bytes32 internal ADAPTER_KEY = keccak256("mock-protocol");
    uint256 internal constant MAX_GAS_PRICE = 100 gwei;

    function setUp() public {
        vm.warp(10_000);

        debtToken = new MockTokenRME("DEBT");
        collToken = new MockTokenRME("COLL");
        provider = new MockFlashProviderRME(debtToken);
        fallbackProvider = new MockFlashProviderRME(debtToken);
        adapter = new MockAdapterRME(debtToken, collToken);
        oracle = new MockOracleRME(2000e8);

        exec = new RiskMitigationExecutor(treasury, MAX_GAS_PRICE);
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
        assertEq(exec.maxGasPrice(), MAX_GAS_PRICE);
        assertEq(exec.minNetIncentiveUsd(), 10e18);
        assertEq(exec.totalMitigations(), 0);
        assertEq(exec.totalCollateralSeized(), 0);
    }

    function testConstructor_RejectsZeroTreasury() public {
        vm.expectRevert("Zero treasury");
        new RiskMitigationExecutor(address(0), MAX_GAS_PRICE);
    }

    // -----------------------------------------------------------------------
    // Access control
    // -----------------------------------------------------------------------

    function testOnlyOwner_ExecuteMitigation() public {
        MitigationParams memory params = _defaultParams();
        vm.prank(address(0xBAAD));
        vm.expectRevert("Not owner");
        exec.executeMitigation(params);
    }

    function testOnlyOwner_RegisterAdapter() public {
        vm.prank(address(0xBAAD));
        vm.expectRevert("Not owner");
        exec.registerAdapter(keccak256("x"), address(adapter));
    }

    function testOnlyOwner_SetMaxGasPrice() public {
        vm.prank(address(0xBAAD));
        vm.expectRevert("Not owner");
        exec.setMaxGasPrice(50 gwei);
    }

    // -----------------------------------------------------------------------
    // Input validation
    // -----------------------------------------------------------------------

    function testRevert_UnsupportedDebt() public {
        MitigationParams memory params = _defaultParams();
        params.debtAsset = address(0xDEAD);
        vm.expectRevert("Unsupported debt");
        exec.executeMitigation(params);
    }

    function testRevert_ZeroAmount() public {
        MitigationParams memory params = _defaultParams();
        params.debtAmount = 0;
        vm.expectRevert("Zero amount");
        exec.executeMitigation(params);
    }

    function testRevert_UnknownAdapter() public {
        MitigationParams memory params = _defaultParams();
        params.adapterKey = keccak256("unknown");
        vm.expectRevert("Unknown adapter");
        exec.executeMitigation(params);
    }

    // -----------------------------------------------------------------------
    // Single mitigation happy path
    // -----------------------------------------------------------------------

    function testSingleMitigation_Success() public {
        uint256 bonus = 1050e18;
        adapter.setCollateralReturn(bonus);

        MitigationParams memory params = _defaultParams();
        exec.executeMitigation(params);

        assertEq(collToken.balanceOf(treasury), bonus);
        assertEq(exec.totalMitigations(), 1);
        assertEq(exec.totalCollateralSeized(), bonus);
    }

    function testSingleMitigation_SlippageGuard() public {
        adapter.setCollateralReturn(100e18);

        MitigationParams memory params = _defaultParams();
        params.minCollateral = 200e18; // require 200, only get 100

        vm.expectRevert("Slippage exceeded");
        exec.executeMitigation(params);
    }

    // -----------------------------------------------------------------------
    // Gas price cap (Module 6)
    // -----------------------------------------------------------------------

    function testGasPriceCap_SkipsWhenExceeded() public {
        adapter.setCollateralReturn(1050e18);

        MitigationParams memory params = _defaultParams();

        // Set tx.gasprice above cap
        vm.txGasPrice(200 gwei); // MAX_GAS_PRICE = 100 gwei
        exec.executeMitigation(params);

        // Should NOT have executed — skipped due to gas price
        assertEq(exec.totalMitigations(), 0);
        assertEq(exec.totalSkippedGasPrice(), 1);
        assertEq(collToken.balanceOf(treasury), 0);
    }

    function testGasPriceCap_ProceedsWhenBelow() public {
        adapter.setCollateralReturn(1050e18);

        MitigationParams memory params = _defaultParams();
        vm.txGasPrice(50 gwei); // Below MAX_GAS_PRICE
        exec.executeMitigation(params);

        assertEq(exec.totalMitigations(), 1);
    }

    function testGasPriceCap_ZeroMeansNoLimit() public {
        exec.setMaxGasPrice(0); // Disable gas price cap
        adapter.setCollateralReturn(1050e18);

        MitigationParams memory params = _defaultParams();
        vm.txGasPrice(500 gwei);
        exec.executeMitigation(params);

        assertEq(exec.totalMitigations(), 1);
    }

    // -----------------------------------------------------------------------
    // Oracle validation (Module 6)
    // -----------------------------------------------------------------------

    function testOracleValidation_StalePriceReverts() public {
        adapter.setCollateralReturn(1050e18);

        MitigationParams memory params = _defaultParams();
        params.collateralPriceFeed = address(oracle);
        params.maxOracleStaleness = 3600;

        // Make oracle stale
        oracle.setUpdatedAt(block.timestamp - 7200);

        vm.expectRevert("Oracle: stale price");
        exec.executeMitigation(params);
    }

    function testOracleValidation_ZeroPriceReverts() public {
        adapter.setCollateralReturn(1050e18);

        MitigationParams memory params = _defaultParams();
        params.collateralPriceFeed = address(oracle);
        params.maxOracleStaleness = 3600;

        oracle.setPrice(0);

        vm.expectRevert("Oracle: zero price");
        exec.executeMitigation(params);
    }

    function testOracleValidation_ValidPricePasses() public {
        adapter.setCollateralReturn(1050e18);

        MitigationParams memory params = _defaultParams();
        params.collateralPriceFeed = address(oracle);
        params.maxOracleStaleness = 3600;

        exec.executeMitigation(params);
        assertEq(exec.totalMitigations(), 1);
    }

    function testOracleValidation_NoFeedSkipsCheck() public {
        adapter.setCollateralReturn(1050e18);

        MitigationParams memory params = _defaultParams();
        params.collateralPriceFeed = address(0); // No feed
        params.maxOracleStaleness = 3600;

        exec.executeMitigation(params);
        assertEq(exec.totalMitigations(), 1);
    }

    // -----------------------------------------------------------------------
    // Multi-provider fallback
    // -----------------------------------------------------------------------

    function testFallback_UsesSecondProvider() public {
        provider.setShouldFail(true);
        exec.addFlashLoanProvider(address(fallbackProvider));

        adapter.setCollateralReturn(500e18);
        MitigationParams memory params = _defaultParams();
        exec.executeMitigation(params);

        assertEq(exec.totalMitigations(), 1);
    }

    function testFallback_AllFailReverts() public {
        provider.setShouldFail(true);
        exec.addFlashLoanProvider(address(fallbackProvider));
        fallbackProvider.setShouldFail(true);

        MitigationParams memory params = _defaultParams();
        vm.expectRevert("All providers failed");
        exec.executeMitigation(params);
    }

    // -----------------------------------------------------------------------
    // Batch mitigation
    // -----------------------------------------------------------------------

    function testBatchMitigation_TwoTargets() public {
        uint256 bonus = 600e18;
        adapter.setCollateralReturn(bonus);

        MitigationParams[] memory targets = new MitigationParams[](2);
        targets[0] = _defaultParams();
        targets[0].user = address(0x111);
        targets[0].debtAmount = 500e18;
        targets[1] = _defaultParams();
        targets[1].user = address(0x222);
        targets[1].debtAmount = 300e18;

        exec.executeBatchMitigation(targets);

        assertEq(exec.totalMitigations(), 2);
        assertEq(collToken.balanceOf(treasury), bonus * 2);
    }

    function testBatchMitigation_EmptyReverts() public {
        MitigationParams[] memory empty = new MitigationParams[](0);
        vm.expectRevert("Empty batch");
        exec.executeBatchMitigation(empty);
    }

    function testBatchMitigation_GasCapSkips() public {
        adapter.setCollateralReturn(600e18);

        MitigationParams[] memory targets = new MitigationParams[](1);
        targets[0] = _defaultParams();

        vm.txGasPrice(200 gwei);
        exec.executeBatchMitigation(targets);

        assertEq(exec.totalMitigations(), 0);
        assertEq(exec.totalSkippedGasPrice(), 1);
    }

    // -----------------------------------------------------------------------
    // Reentrancy guard
    // -----------------------------------------------------------------------

    function testReentrancy_BlocksDoubleEntry() public {
        adapter.setCollateralReturn(1050e18);
        MitigationParams memory params = _defaultParams();

        exec.executeMitigation(params);
        exec.executeMitigation(params); // Second call should succeed (guard resets)
        assertEq(exec.totalMitigations(), 2);
    }

    function testExecuteOperation_RejectsDirectCall() public {
        vm.expectRevert("Not in execution");
        exec.executeOperation(address(debtToken), 1e18, 0, address(exec), "");
    }

    // -----------------------------------------------------------------------
    // Admin functions
    // -----------------------------------------------------------------------

    function testSetMaxGasPrice() public {
        exec.setMaxGasPrice(50 gwei);
        assertEq(exec.maxGasPrice(), 50 gwei);
    }

    function testSetMinNetIncentiveUsd() public {
        exec.setMinNetIncentiveUsd(50e18);
        assertEq(exec.minNetIncentiveUsd(), 50e18);
    }

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

    // -----------------------------------------------------------------------
    // Stats accumulation
    // -----------------------------------------------------------------------

    function testStats_Accumulate() public {
        adapter.setCollateralReturn(500e18);

        MitigationParams memory params = _defaultParams();
        exec.executeMitigation(params);
        exec.executeMitigation(params);

        assertEq(exec.totalMitigations(), 2);
        assertEq(exec.totalCollateralSeized(), 1000e18);
    }

    // -----------------------------------------------------------------------
    // Helper
    // -----------------------------------------------------------------------

    function _defaultParams() internal view returns (MitigationParams memory) {
        return MitigationParams({
            user: address(0xABCD),
            debtAsset: address(debtToken),
            debtAmount: 1000e18,
            collateralAsset: address(collToken),
            minCollateral: 0,
            adapterKey: ADAPTER_KEY,
            minNetIncentiveUsd: 10e18,
            collateralPriceFeed: address(0),
            maxOracleStaleness: 3600
        });
    }
}
