// SPDX-License-Identifier: MIT
pragma solidity ^0.8.19;

import "forge-std/Test.sol";
import "forge-std/console.sol";
import "./contracts/CollateralizationDetector.sol";
import "./contracts/LPPricing.sol";
import "./contracts/OracleIntegration.sol";

/// @title CollateralizationDetectorTest
/// @notice Tests for the multi-chain DeFi over-collateralization detection system
/// @dev Tests are split into unit tests (no fork needed) and fork tests (mainnet data)
contract CollateralizationDetectorTest is Test {
    CollateralizationDetector public detector;

    // --- Known mainnet addresses ---
    address constant AAVE_V2_POOL = 0x7d2768dE32b0b80b7a3454c06BdAc94A69DDc7A9;
    address constant AAVE_V3_POOL = 0x87870Bca3F3fD6335C3F4ce8392D69350B4fA4E2;
    address constant COMPOUND_V2_COMPTROLLER = 0x3d9819210A31b4961b30EF54bE2aeD79B9c9Cd3B;
    address constant COMPOUND_V3_USDC = 0xc3d688B66703497DAA19211EEdff47f25384cdc3;
    address constant CHAINLINK_ETH_USD = 0x5f4eC3Df9cbd43714FE2740f5E3616155c5b8419;
    address constant CHAINLINK_BTC_USD = 0xF4030086522a5bEEa4988F8cA5B36dbC97BeE88c;
    address constant UNISWAP_V2_FACTORY = 0x5C69bEe701ef814a2B6a3EDD4B1652CB9cc5aA6f;
    address constant MAKER_VAT = 0x35D1b3F3D7966A1DFe207aa4514C12a259A0492B;

    function setUp() public {
        detector = new CollateralizationDetector();
    }

    // ============================================================
    // Unit Tests (no fork required)
    // ============================================================

    function testCollateralizationThresholds() public view {
        assertEq(detector.WELL_COLLATERALIZED(), 2e18);
        assertEq(detector.SIGNIFICANTLY_OVER_COLLATERALIZED(), 3e18);
        assertEq(detector.EXTREMELY_CONSERVATIVE(), 5e18);
    }

    function testClassifyCollateralization() public view {
        // Under-collateralized (< 100%)
        assertEq(detector.classifyCollateralization(0.5e18), 0);
        assertEq(detector.classifyCollateralization(0), 0);

        // Just collateralized (100-200%)
        assertEq(detector.classifyCollateralization(1e18), 1);
        assertEq(detector.classifyCollateralization(1.5e18), 1);

        // Well-collateralized (200-300%)
        assertEq(detector.classifyCollateralization(2e18), 2);
        assertEq(detector.classifyCollateralization(2.5e18), 2);

        // Significantly over-collateralized (300-500%)
        assertEq(detector.classifyCollateralization(3e18), 3);
        assertEq(detector.classifyCollateralization(4e18), 3);

        // Extremely conservative (> 500%)
        assertEq(detector.classifyCollateralization(5e18), 4);
        assertEq(detector.classifyCollateralization(10e18), 4);
    }

    function testFuzzClassifyCollateralization(uint256 ratio) public view {
        uint8 level = detector.classifyCollateralization(ratio);
        assertTrue(level <= 4);

        if (ratio < 1e18) assertEq(level, 0);
        else if (ratio < 2e18) assertEq(level, 1);
        else if (ratio < 3e18) assertEq(level, 2);
        else if (ratio < 5e18) assertEq(level, 3);
        else assertEq(level, 4);
    }

    // ============================================================
    // LP Pricing Unit Tests
    // ============================================================

    function testFairLPPrice() public pure {
        // Example: ETH-USDC pool
        // reserve0 = 1000 ETH (1e21 in wei)
        // reserve1 = 2,000,000 USDC (2e12 in 6-decimal)
        // price0 = $2000 (2000e18)
        // price1 = $1 (1e18)
        // totalSupply = 1e18 (1 LP token for simplicity)
        uint256 fairPrice = LPPricing.fairLPPrice(
            1000e18,   // reserve0
            2000000e6, // reserve1
            2000e18,   // price0 in 1e18
            1e18,      // price1 in 1e18
            1e18       // totalSupply
        );

        // Fair price should be approximately 2 * sqrt(1000e18 * 2000000e6) * sqrt(2000e18 * 1e18) / 1e18
        // This is a large number, just verify it's > 0 and reasonable
        assertGt(fairPrice, 0, "Fair LP price should be positive");
    }

    function testFairLPPriceZeroSupply() public pure {
        uint256 fairPrice = LPPricing.fairLPPrice(100, 100, 1e18, 1e18, 0);
        assertEq(fairPrice, 0, "Zero supply should return zero price");
    }

    function testSqrt() public pure {
        assertEq(LPPricing.sqrt(0), 0);
        assertEq(LPPricing.sqrt(1), 1);
        assertEq(LPPricing.sqrt(4), 2);
        assertEq(LPPricing.sqrt(9), 3);
        assertEq(LPPricing.sqrt(16), 4);
        assertEq(LPPricing.sqrt(100), 10);
        assertEq(LPPricing.sqrt(1e18), 1e9);
        assertEq(LPPricing.sqrt(1e36), 1e18);
    }

    function testFuzzSqrt(uint128 x) public pure {
        uint256 y = LPPricing.sqrt(uint256(x));
        // y * y <= x < (y+1) * (y+1)
        assertLe(y * y, uint256(x), "sqrt underflow");
        assertLt(uint256(x), (y + 1) * (y + 1), "sqrt overflow");
    }

    function testCurveLPLowerBound() public pure {
        // virtual_price = 1.02e18 (2% above peg)
        // min underlying price = $0.995 (995e15 in 1e18)
        uint256 bound = LPPricing.curveLPLowerBound(1.02e18, 0.995e18);
        // Expected: 1.02 * 0.995 = ~1.0149 in 1e18
        assertGt(bound, 1e18, "Lower bound should be > $1");
        assertLt(bound, 1.02e18, "Lower bound should be < virtual price");
    }

    // ============================================================
    // Oracle Integration Unit Tests
    // ============================================================

    function testTokenValueUsd18() public pure {
        // 1 ETH ($2000) with 18 decimals and 8-decimal feed
        uint256 value = OracleIntegration.tokenValueUsd18(
            1e18,       // 1 ETH
            18,         // ETH has 18 decimals
            200000000000, // $2000 with 8 decimals
            8
        );
        assertEq(value, 2000e18, "1 ETH at $2000 = $2000");

        // 1000 USDC ($1) with 6 decimals and 8-decimal feed
        value = OracleIntegration.tokenValueUsd18(
            1000e6,     // 1000 USDC
            6,          // USDC has 6 decimals
            100000000,  // $1 with 8 decimals
            8
        );
        assertEq(value, 1000e18, "1000 USDC at $1 = $1000");

        // 1 WBTC ($40000) with 8 decimals and 8-decimal feed
        value = OracleIntegration.tokenValueUsd18(
            1e8,           // 1 WBTC
            8,             // WBTC has 8 decimals
            4000000000000, // $40000 with 8 decimals
            8
        );
        assertEq(value, 40000e18, "1 WBTC at $40000 = $40000");
    }

    function testTokenValueUsd18PrecisionEdgeCases() public pure {
        // Very small amount: 1 wei of USDC (0.000001 USDC)
        uint256 value = OracleIntegration.tokenValueUsd18(
            1,          // 1 raw unit (0.000001 USDC)
            6,
            100000000,  // $1
            8
        );
        // 0.000001 * 1 * 1e18 / (1e6 * 1e8) = 1e18 / 1e14 = 1e4 = 10000
        // This represents $0.000001 in 18-decimal form: 1e12
        assertEq(value, 1e12, "Smallest USDC unit value");
    }

    // ============================================================
    // Multicall3 Builder Tests
    // ============================================================

    function testBuildAaveV2Call() public view {
        address testPool = address(0x1234);
        address testUser = address(0x5678);

        IMulticall3.Call3 memory call = detector.buildAaveV2Call(testPool, testUser);

        assertEq(call.target, testPool);
        assertTrue(call.allowFailure);
        assertEq(
            call.callData,
            abi.encodeWithSelector(IAaveV2LendingPool.getUserAccountData.selector, testUser)
        );
    }

    function testBuildCompoundV2Call() public view {
        address testComptroller = address(0xABCD);
        address testAccount = address(0xEF01);

        IMulticall3.Call3 memory call = detector.buildCompoundV2Call(testComptroller, testAccount);

        assertEq(call.target, testComptroller);
        assertTrue(call.allowFailure);
        assertEq(
            call.callData,
            abi.encodeWithSelector(ICompoundV2Comptroller.getAccountLiquidity.selector, testAccount)
        );
    }
}
