// SPDX-License-Identifier: MIT
pragma solidity ^0.8.19;

import "forge-std/Test.sol";
import "../contracts/CollateralHealthMonitor.sol";

// ---------------------------------------------------------------------------
// Mock contracts
// ---------------------------------------------------------------------------

/// @dev Mock Aave pool (shared for v2/v3)
contract MockAavePoolHM {
    uint256 public collateral;
    uint256 public debt;
    uint256 public healthFactor;

    constructor(uint256 _collateral, uint256 _debt, uint256 _hf) {
        collateral = _collateral;
        debt = _debt;
        healthFactor = _hf;
    }

    function setData(uint256 _collateral, uint256 _debt, uint256 _hf) external {
        collateral = _collateral;
        debt = _debt;
        healthFactor = _hf;
    }

    function getUserAccountData(address)
        external view
        returns (uint256, uint256, uint256, uint256, uint256, uint256)
    {
        return (collateral, debt, 0, 0, 0, healthFactor);
    }
}

/// @dev Mock Compound V2 Comptroller
contract MockComptrollerHM {
    uint256 public err;
    uint256 public liquidity;
    uint256 public shortfall;

    constructor(uint256 _err, uint256 _liq, uint256 _short) {
        err = _err;
        liquidity = _liq;
        shortfall = _short;
    }

    function getAccountLiquidity(address)
        external view
        returns (uint256, uint256, uint256)
    {
        return (err, liquidity, shortfall);
    }
}

/// @dev Mock Maker Vat
contract MockVatHM {
    uint256 public inkVal;
    uint256 public artVal;
    uint256 public rateVal;
    uint256 public spotVal;

    constructor(uint256 _ink, uint256 _art, uint256 _rate, uint256 _spot) {
        inkVal = _ink;
        artVal = _art;
        rateVal = _rate;
        spotVal = _spot;
    }

    function urns(bytes32, address) external view returns (uint256, uint256) {
        return (inkVal, artVal);
    }

    function ilks(bytes32) external view returns (uint256, uint256, uint256, uint256, uint256) {
        return (0, rateVal, spotVal, 0, 0);
    }
}

/// @dev Mock Chainlink oracle
contract MockOracleHM {
    int256 public price;
    uint8 public dec;
    uint256 public updatedAtOverride;

    constructor(int256 _price, uint8 _dec) {
        price = _price;
        dec = _dec;
    }

    function setUpdatedAt(uint256 _ts) external { updatedAtOverride = _ts; }
    function setPrice(int256 _price) external { price = _price; }

    function latestRoundData()
        external view
        returns (uint80, int256, uint256, uint256, uint80)
    {
        uint256 ts = updatedAtOverride == 0 ? block.timestamp : updatedAtOverride;
        return (1, price, 0, ts, 1);
    }

    function decimals() external view returns (uint8) { return dec; }
}

// ---------------------------------------------------------------------------
// Test contract
// ---------------------------------------------------------------------------

contract CollateralHealthMonitorTest is Test {
    CollateralHealthMonitor internal monitor;

    function setUp() public {
        monitor = new CollateralHealthMonitor();
        vm.warp(10_000); // Predictable timestamp
    }

    // -----------------------------------------------------------------------
    // Constants
    // -----------------------------------------------------------------------

    function testConstants() public view {
        assertEq(monitor.DEFAULT_HF_THRESHOLD(), 1.05e18);
        assertEq(monitor.MIN_DEBT_USD(), 100e18);
    }

    // -----------------------------------------------------------------------
    // Aave v2 health checks
    // -----------------------------------------------------------------------

    function testCheckAaveV2Health_AtRisk() public {
        // HF = 0.95 < 1.05 → at risk
        MockAavePoolHM pool = new MockAavePoolHM(1.1e18, 1e18, 0.95e18);
        CollateralHealthMonitor.HealthCheckResult memory result =
            monitor.checkAaveV2Health(address(pool), address(this), 1.05e18);

        assertTrue(result.isAtRisk);
        assertEq(result.healthFactor, 0.95e18);
        assertEq(result.collateralUsd, 1.1e18);
        assertEq(result.debtUsd, 1e18);
    }

    function testCheckAaveV2Health_Safe() public {
        // HF = 2.0 > 1.05 → safe
        MockAavePoolHM pool = new MockAavePoolHM(4e18, 2e18, 2e18);
        CollateralHealthMonitor.HealthCheckResult memory result =
            monitor.checkAaveV2Health(address(pool), address(this), 1.05e18);

        assertFalse(result.isAtRisk);
        assertEq(result.healthFactor, 2e18);
    }

    function testCheckAaveV2Health_NoDebt() public {
        // No debt → not at risk (regardless of HF)
        MockAavePoolHM pool = new MockAavePoolHM(5e18, 0, type(uint256).max);
        CollateralHealthMonitor.HealthCheckResult memory result =
            monitor.checkAaveV2Health(address(pool), address(this), 1.05e18);

        assertFalse(result.isAtRisk);
    }

    function testBatchCheckAaveV2() public {
        MockAavePoolHM pool = new MockAavePoolHM(1e18, 1e18, 0.9e18);

        address[] memory users = new address[](3);
        users[0] = address(0x111);
        users[1] = address(0x222);
        users[2] = address(0x333);

        CollateralHealthMonitor.HealthCheckResult[] memory results =
            monitor.batchCheckAaveV2(address(pool), users, 1.05e18);

        assertEq(results.length, 3);
        // All users get the same mock data
        for (uint256 i = 0; i < 3; i++) {
            assertTrue(results[i].isAtRisk);
            assertEq(results[i].healthFactor, 0.9e18);
        }
    }

    // -----------------------------------------------------------------------
    // Aave v3 health checks
    // -----------------------------------------------------------------------

    function testCheckAaveV3Health_AtRisk() public {
        // Aave v3: values in 8-decimal USD
        // HF = 0.98 < 1.05 → at risk
        MockAavePoolHM pool = new MockAavePoolHM(10000e8, 9500e8, 0.98e18);
        CollateralHealthMonitor.HealthCheckResult memory result =
            monitor.checkAaveV3Health(address(pool), address(this), 1.05e18);

        assertTrue(result.isAtRisk);
        // Values scaled to 1e18: 10000e8 * 1e10 = 10000e18
        assertEq(result.collateralUsd, 10000e18);
        assertEq(result.debtUsd, 9500e18);
    }

    function testCheckAaveV3Health_Safe() public {
        MockAavePoolHM pool = new MockAavePoolHM(20000e8, 5000e8, 3e18);
        CollateralHealthMonitor.HealthCheckResult memory result =
            monitor.checkAaveV3Health(address(pool), address(this), 1.05e18);

        assertFalse(result.isAtRisk);
        assertEq(result.healthFactor, 3e18);
    }

    function testBatchCheckAaveV3() public {
        MockAavePoolHM pool = new MockAavePoolHM(5000e8, 4800e8, 1.02e18);

        address[] memory users = new address[](2);
        users[0] = address(0xA);
        users[1] = address(0xB);

        CollateralHealthMonitor.HealthCheckResult[] memory results =
            monitor.batchCheckAaveV3(address(pool), users, 1.05e18);

        assertEq(results.length, 2);
        assertTrue(results[0].isAtRisk);
        assertTrue(results[1].isAtRisk);
        assertEq(results[0].collateralUsd, 5000e18);
    }

    // -----------------------------------------------------------------------
    // Compound v2 health checks
    // -----------------------------------------------------------------------

    function testCheckCompoundV2Health_Liquidatable() public {
        // shortfall > 0 → at risk
        MockComptrollerHM comp = new MockComptrollerHM(0, 0, 500e18);
        CollateralHealthMonitor.HealthCheckResult memory result =
            monitor.checkCompoundV2Health(address(comp), address(this));

        assertTrue(result.isAtRisk);
        assertEq(result.healthFactor, 0);
        assertEq(result.shortfall, 500e18);
    }

    function testCheckCompoundV2Health_Safe() public {
        // liquidity > 0, shortfall = 0 → safe
        MockComptrollerHM comp = new MockComptrollerHM(0, 1000e18, 0);
        CollateralHealthMonitor.HealthCheckResult memory result =
            monitor.checkCompoundV2Health(address(comp), address(this));

        assertFalse(result.isAtRisk);
        assertEq(result.healthFactor, type(uint256).max);
        assertEq(result.collateralUsd, 1000e18);
    }

    function testCheckCompoundV2Health_ErrorReverts() public {
        MockComptrollerHM comp = new MockComptrollerHM(1, 0, 0);
        vm.expectRevert("Comptroller error");
        monitor.checkCompoundV2Health(address(comp), address(this));
    }

    function testBatchCheckCompoundV2() public {
        MockComptrollerHM comp = new MockComptrollerHM(0, 0, 200e18);

        address[] memory accounts = new address[](2);
        accounts[0] = address(0x1);
        accounts[1] = address(0x2);

        CollateralHealthMonitor.HealthCheckResult[] memory results =
            monitor.batchCheckCompoundV2(address(comp), accounts);

        assertEq(results.length, 2);
        assertTrue(results[0].isAtRisk);
        assertEq(results[0].shortfall, 200e18);
    }

    // -----------------------------------------------------------------------
    // MakerDAO health checks
    // -----------------------------------------------------------------------

    function testCheckMakerHealth_AtRisk() public {
        // ink = 1 ETH, art = 1 (normalized), rate = 1e27
        // spot = 0.9e27 (price with safety margin below liquidation)
        // collateralValue = 1e18 * 0.9e27 = 0.9e45
        // debtValue = 1e18 * 1e27 = 1e45
        // HF = 0.9e45 * 1e18 / 1e45 = 0.9e18 → at risk
        MockVatHM vat = new MockVatHM(1e18, 1e18, 1e27, 0.9e27);
        CollateralHealthMonitor.HealthCheckResult memory result =
            monitor.checkMakerHealth(address(vat), bytes32("ETH-A"), address(this));

        assertTrue(result.isAtRisk);
        assertEq(result.healthFactor, 0.9e18);
    }

    function testCheckMakerHealth_Safe() public {
        // ink = 2 ETH, art = 1, rate = 1e27
        // spot = 2e27
        // collateralValue = 2e18 * 2e27 = 4e45
        // debtValue = 1e18 * 1e27 = 1e45
        // HF = 4e45 * 1e18 / 1e45 = 4e18 → safe
        MockVatHM vat = new MockVatHM(2e18, 1e18, 1e27, 2e27);
        CollateralHealthMonitor.HealthCheckResult memory result =
            monitor.checkMakerHealth(address(vat), bytes32("ETH-A"), address(this));

        assertFalse(result.isAtRisk);
        assertEq(result.healthFactor, 4e18);
    }

    function testCheckMakerHealth_NoDebt() public {
        MockVatHM vat = new MockVatHM(5e18, 0, 1e27, 2e27);
        CollateralHealthMonitor.HealthCheckResult memory result =
            monitor.checkMakerHealth(address(vat), bytes32("ETH-A"), address(this));

        assertFalse(result.isAtRisk);
        assertEq(result.healthFactor, type(uint256).max);
    }

    // -----------------------------------------------------------------------
    // Oracle validation
    // -----------------------------------------------------------------------

    function testValidateOracle_Valid() public {
        MockOracleHM oracle = new MockOracleHM(2000e8, 8);
        (int256 price, uint8 decimals, bool isValid) =
            monitor.validateOracle(address(oracle), 3600);

        assertEq(price, 2000e8);
        assertEq(decimals, 8);
        assertTrue(isValid);
    }

    function testValidateOracle_StalePrice() public {
        MockOracleHM oracle = new MockOracleHM(2000e8, 8);
        // Set updatedAt to 2 hours ago (beyond 1 hour staleness)
        oracle.setUpdatedAt(block.timestamp - 7200);

        (, , bool isValid) = monitor.validateOracle(address(oracle), 3600);
        assertFalse(isValid);
    }

    function testValidateOracle_ZeroPrice() public {
        MockOracleHM oracle = new MockOracleHM(0, 8);
        (, , bool isValid) = monitor.validateOracle(address(oracle), 3600);
        assertFalse(isValid);
    }

    function testValidateOracle_NegativePrice() public {
        MockOracleHM oracle = new MockOracleHM(-100, 8);
        (, , bool isValid) = monitor.validateOracle(address(oracle), 3600);
        assertFalse(isValid);
    }

    // -----------------------------------------------------------------------
    // Risk classification
    // -----------------------------------------------------------------------

    function testClassifyRisk_Liquidatable() public view {
        assertEq(monitor.classifyRisk(0.5e18), 0); // HF < 1.0
        assertEq(monitor.classifyRisk(0.99e18), 0);
    }

    function testClassifyRisk_Critical() public view {
        assertEq(monitor.classifyRisk(1e18), 1);    // 1.0 - 1.05
        assertEq(monitor.classifyRisk(1.04e18), 1);
    }

    function testClassifyRisk_AtRisk() public view {
        assertEq(monitor.classifyRisk(1.05e18), 2);  // 1.05 - 1.2
        assertEq(monitor.classifyRisk(1.19e18), 2);
    }

    function testClassifyRisk_Safe() public view {
        assertEq(monitor.classifyRisk(1.2e18), 3);   // HF >= 1.2
        assertEq(monitor.classifyRisk(5e18), 3);
        assertEq(monitor.classifyRisk(type(uint256).max), 3);
    }
}
